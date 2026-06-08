-- migration 003: projects
-- 项目表 — 一个用户的一份"作品"(原创 / 导入),四态共用同一表,mode 字段区分。
-- 阶段 1 只用 mode='initial'。
-- created 2026-05-09 / ADR §3

CREATE TABLE IF NOT EXISTS projects (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name              TEXT NOT NULL,
    type              TEXT NOT NULL                        -- 用户填的作品类型
                        CHECK (type IN ('novel','comic','anime','generic')),
    custom_type_name  TEXT,                                -- type='generic' 时用户写的自定义类型名(如"舞台剧""广播剧"),仅作 LLM 场景描述用
    tags              TEXT NOT NULL DEFAULT '[]',          -- JSON 数组,题材标签
    mode              TEXT NOT NULL DEFAULT 'initial'      -- 四态:阶段 1 只 initial
                        CHECK (mode IN ('initial','middle','end','cycle')),
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);
