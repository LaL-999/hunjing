-- migration 008: otp_codes
-- OTP 验证码持久化 — 对接 Sprint 1.B auth_service。
-- code 存 SHA-256 hash 不存明文(防数据库泄漏时 OTP 全曝光,即使 5 分钟 TTL)。
-- 60s 同邮箱限频 + 24h 同 IP 限流 都依赖此表的 created_at + sent_ip。
-- created 2026-05-09 / ADR §10.1 修订记录

CREATE TABLE IF NOT EXISTS otp_codes (
    id              TEXT PRIMARY KEY,
    target_email    TEXT NOT NULL,
    code_hash       TEXT NOT NULL,                         -- SHA-256(code) 16 进制小写
    sent_ip         TEXT,                                   -- 发码时的客户端 IP,nullable
    created_at      TEXT NOT NULL,                         -- ISO 8601
    expires_at      TEXT NOT NULL,                         -- ISO 8601,默认 created_at + 5min
    consumed        INTEGER NOT NULL DEFAULT 0             -- 0 未消费,1 已 verify 成功
);

CREATE INDEX IF NOT EXISTS idx_otp_target_time ON otp_codes(target_email, created_at);
CREATE INDEX IF NOT EXISTS idx_otp_ip_time ON otp_codes(sent_ip, created_at);
