-- migration 001: users
-- 用户表 — 支持邮箱 + (未来)手机号双通道,Sprint 1.B 只走 email 字段。
-- Sprint D.1(2026-05-12)修订:plan CHECK 从 v1 'free/standard/super' 改为 v2
-- 'free/pro/max/super_max'(订阅模式 Anthropic 风重构,见 项目记忆.md)。
-- 老 v1 DB(开发者本人测试库)的迁移见 docs/migrations/v1_to_v2_plan.md(手动 SQL)。
-- created 2026-05-09 / ADR §3 / Sprint D.1 改 CHECK

CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,                     -- uuid
    phone           TEXT UNIQUE,                          -- 国内 11 位,Sprint 1 nullable 不参与登录
    email           TEXT UNIQUE,                          -- Sprint 1 唯一登录通道
    plan            TEXT NOT NULL DEFAULT 'free'
                      CHECK (plan IN ('free', 'pro', 'max', 'super_max')),
    quota_reset_at  TEXT,                                  -- ISO 8601;下次配额重置时间(月初)
    register_ip     TEXT,
    register_ua     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    CHECK (phone IS NOT NULL OR email IS NOT NULL)        -- 至少一个联系方式
);

CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
