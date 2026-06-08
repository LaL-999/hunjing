-- migration 002: consent_records
-- 法务证据链 — 用户同意四协议的快照,对接前端 UploadOverlay consent phase。
-- 律师反馈 P0 改动 ③ + 数据出境合规依据。
-- created 2026-05-09 / ADR §3

CREATE TABLE IF NOT EXISTS consent_records (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    version         TEXT NOT NULL,                         -- "v1" 等;前端 huimeng:consent:vX
    checks          TEXT NOT NULL,                         -- JSON: {adult,terms,privacy,pricing}
    accepted_at     TEXT NOT NULL,
    ip              TEXT,
    ua              TEXT
);

CREATE INDEX IF NOT EXISTS idx_consent_user ON consent_records(user_id);
