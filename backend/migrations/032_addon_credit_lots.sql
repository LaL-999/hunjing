-- migration 032: addon_credit_lots(加购批次 + 1 年有效期跟踪)
-- Sprint D.9 C.1(2026-05-13)— ADR_credit_quota_重构.md §6.1
--
-- 设计:
--   每次用户加购 → INSERT 1 行 addon_credit_lots(initial / remaining / expires_at)
--   消耗加购 credit 时:FIFO(先购买的先用),按 lot 顺序扣 remaining_credits
--   cron addon_expire 每天扫 expires_at < now 的 lot → 设 is_expired=1 + 写 credit_transactions
--
-- 为什么要 lot 表(而非简单 user_credit_balances.addon_credits 单字段):
--   - 加购 1 年有效期 — 不同批次到期日不同,必须独立跟踪
--   - 财务 audit:某季度卖了多少加购包 / 多少已过期未使用
--   - 用户透明:UI 显"100 c 将在 30 天后过期"
--
-- user_credit_balances.addon_credits 是 lot 的 SUM(remaining) 缓存(O(1) 读优化)。
-- 真相源 = lot 表;balance 字段消耗 / 加购时同步更新。

CREATE TABLE IF NOT EXISTS addon_credit_lots (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- 购买信息(冻结)
    initial_credits   INTEGER NOT NULL,    -- 购买时 credit 数(100/500/2000)
    price_cents       INTEGER NOT NULL,    -- 购买时实付金额(分,避浮点)
    package_size      TEXT NOT NULL        -- 包装规格(small=100c / medium=500c / large=2000c / custom)
                        CHECK (package_size IN ('small', 'medium', 'large', 'custom')),

    -- 剩余 + 过期跟踪
    remaining_credits INTEGER NOT NULL,    -- 当前剩余(消费时减,跟 user_credit_balances.addon_credits 同步)
    is_expired        INTEGER NOT NULL DEFAULT 0,   -- 0=有效 / 1=已过期(cron 设)

    -- 时刻轴
    purchased_at      TEXT NOT NULL,
    expires_at        TEXT NOT NULL,        -- purchased_at + 1 年
    expired_at        TEXT                  -- 实际过期时间(cron 设)
);

-- cron 扫描入口:今天该过期的 lot
CREATE INDEX IF NOT EXISTS idx_addon_lots_expire
    ON addon_credit_lots(expires_at, is_expired);

-- 用户视角:某用户的全部加购批次(FIFO 消耗顺序)
CREATE INDEX IF NOT EXISTS idx_addon_lots_user_purchased
    ON addon_credit_lots(user_id, purchased_at);
