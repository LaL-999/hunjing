-- migration 006: refine_sessions + character_refinements
-- 角色对焦组件持久化层 — 对接 prompts/character_focus.md v3 + Sprint 1.D refine 服务。
-- 直接搬自 docs/MVP阶段1_角色对焦组件设计.md §8(无任何修改)。
-- created 2026-05-09 / ADR §3

CREATE TABLE IF NOT EXISTS refine_sessions (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL REFERENCES users(id),
    triggered_at    TEXT NOT NULL,                         -- ISO 8601
    completed_at    TEXT,                                   -- NULL = 未完成
    skip_reason     TEXT,                                   -- NULL / user_skipped / llm_failed
    tokens_input    INTEGER,
    tokens_output   INTEGER,
    cost_yuan       REAL,
    duration_ms     INTEGER
);

CREATE INDEX IF NOT EXISTS idx_refine_session_project ON refine_sessions(project_id);


CREATE TABLE IF NOT EXISTS character_refinements (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES refine_sessions(id) ON DELETE CASCADE,
    character_id        TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    suggestion_kind     TEXT NOT NULL CHECK (suggestion_kind IN
                          ('identity_补全','personality_补充','quote_补充',
                           'no_go_补充','consistency_警告')),
    suggestion_text     TEXT NOT NULL,
    suggestion_payload  TEXT NOT NULL,                      -- JSON
    status              TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','accepted','rejected','edited','skipped')),
    user_edit           TEXT,                                -- JSON,仅 status='edited' 时有
    actioned_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_refine_status ON character_refinements(session_id, status);
