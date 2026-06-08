-- BYOK(自携密钥)— 月度订阅 + 激活码
--
-- 用户花 30 元/月 → 平台生成激活码(BYOK-XXXX-XXXX-XXXX 形式)→ 用户复制到"开挂开关"
-- 1 个月后 expires_at < now → 自动失效,users 自动回到平台默认 LLM key
--
-- 字段说明:
--   code:激活码字符串(独立索引,用户输入时查表 + 校验是否归属本人)
--   purchased_at:购买时间
--   expires_at:有效期到(purchased_at + 30 天)
--   is_active:用户当前是否已"输入码激活"(可购买后不激活,过期就过期)
--   activated_at:用户输入码激活的时间
--   deactivated_at:用户主动停用 / 系统自动失效 的时间
--   notes:审计 / 客服备注
--
-- 业务约束:
--   一个用户可同时持有多张订阅(连续购买叠加月数)— 取 max(expires_at) 作为生效到期
--   is_active 是用户当前是否手动激活了 BYOK,与"有未过期订阅"是 AND 关系
--
-- 2026-06-04 用户拍板:完全独立 30/月,任何用户(含 Free)都能买,纯外挂不扣 credit

CREATE TABLE IF NOT EXISTS byok_subscriptions (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    code            TEXT NOT NULL UNIQUE,
    purchased_at    TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 0,
    activated_at    TEXT,
    deactivated_at  TEXT,
    price_cents     INTEGER NOT NULL DEFAULT 3000,
    notes           TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_byok_subs_user_id ON byok_subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_byok_subs_code ON byok_subscriptions(code);
CREATE INDEX IF NOT EXISTS idx_byok_subs_user_active ON byok_subscriptions(user_id, is_active, expires_at);
