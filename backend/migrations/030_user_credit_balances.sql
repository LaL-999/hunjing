-- migration 030: user_credit_balances(每用户 credit 钱包)
-- Sprint D.9 C.1(2026-05-13)— ADR_credit_quota_重构.md 落地
--
-- 设计:每用户 1 行,记录订阅 credit + 加购 credit 两个独立钱包
--   - subscription_credits:月订阅发放,**月末清零**(防囤积)
--   - addon_credits:加购发放,**1 年有效期**(防财务负债无限延展)
--   - 总余额 = subscription_credits + addon_credits
--   - 消耗顺序:优先扣 subscription(反正月末要清,先用)→ 不够时再扣 addon
--
-- 月度重置机制:
--   - month_start 字段记录"本月起点"ISO
--   - cron 任务 month_reset 每天检查:今天 != month_start 的月 → subscription_credits 清零 + 按订阅档发新月度池
--   - free 档每月都发 30 c(不需付费)
--   - pro/max/super_max 看 user_plan_snapshots active 快照的 monthly_credits_quota
--   - 取消订阅后 state='cancelled':本周期内仍发,周期结束后停发
--
-- 新用户首次访问:auth_service.verify_otp 创建 user 后调
--   credit_service.ensure_balance(user_id) 初始化本月 wallet
--   (free 档 30 c subscription_credits / addon_credits 0 / month_start 本月初)
--
-- created 2026-05-13 / Sprint C.1

CREATE TABLE IF NOT EXISTS user_credit_balances (
    user_id              TEXT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,

    -- 月度重置参考点(ISO 8601 UTC,通常是月初 00:00:00)
    -- cron 跑 month_reset 时:if user 当前 month_start < 实际月初 → 清零 + 发新池
    month_start          TEXT NOT NULL,

    -- 订阅 credit(月末清零,防囤积)
    subscription_credits INTEGER NOT NULL DEFAULT 0,

    -- 加购 credit(永久,但每个 lot 1 年有效期,过期由 addon_credit_lots 表跟踪)
    -- 这里冗余存"当前可用总和" — 性能优化(消耗时 O(1) 读,不用 JOIN lots)
    -- 真相源是 addon_credit_lots 表的 remaining_credits sum,本字段定期同步
    addon_credits        INTEGER NOT NULL DEFAULT 0,

    updated_at           TEXT NOT NULL
);

-- 加索引方便 cron 扫描 "今天该重置的用户"
CREATE INDEX IF NOT EXISTS idx_user_credit_balances_month
    ON user_credit_balances(month_start);
