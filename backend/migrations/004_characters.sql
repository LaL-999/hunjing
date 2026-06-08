-- migration 004: characters
-- 角色表 — 用户从零创建的角色档案。
-- 字段对齐 character_focus.md v3 prompt 输入 schema(name 必填,其他可空)。
-- position_x/y/z + color 用于前端 3D 图谱编辑器持久化布局。
-- created 2026-05-09 / ADR §3

CREATE TABLE IF NOT EXISTS characters (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,                         -- 唯一必填字段
    identity        TEXT NOT NULL DEFAULT '',
    personality     TEXT NOT NULL DEFAULT '',
    quotes          TEXT NOT NULL DEFAULT '[]',            -- JSON 数组
    no_go_list      TEXT NOT NULL DEFAULT '[]',            -- JSON 数组
    position_x      REAL NOT NULL DEFAULT 0,
    position_y      REAL NOT NULL DEFAULT 0,
    position_z      REAL NOT NULL DEFAULT 0,
    color           TEXT,                                   -- hex 字符串,可空
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_characters_project ON characters(project_id);
