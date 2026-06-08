-- migration 005: relationships + events
-- 角色间关系网 + 事件菱形节点(初始态可选)。
-- created 2026-05-09 / ADR §3

CREATE TABLE IF NOT EXISTS relationships (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_id       TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    target_id       TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    type            TEXT NOT NULL
                      CHECK (type IN ('亲属','敌对','朋友','情侣','师徒','同事','其他')),
    description     TEXT NOT NULL DEFAULT '',
    color           TEXT,                                   -- 默认按 type 派生,允许覆盖
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_relationships_project ON relationships(project_id);


CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    description     TEXT NOT NULL,
    participants    TEXT NOT NULL DEFAULT '[]',            -- JSON: [character_id, ...]
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id);
