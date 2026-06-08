-- ============================================================
-- 故事圣经表 — 剧创态独立(D2 决策:数据走 A 隔离)
-- ============================================================
-- 一本小说对应一份故事圣经(1:1)。圣经里有 N 个角色/地点/关系/事件。
-- 命名 sp_bible_* 与父平台 characters / events / relationships 解耦。
-- 阶段 5 huimeng_bridge 桥接层:角色 agent 调用时 JOIN 父平台
-- character_drivers / character_knowledge / relationship_polarity 表借力。
-- ============================================================

-- 故事圣经主表(每本小说 1 行,即使没填也存)
CREATE TABLE IF NOT EXISTS sp_story_bibles (
    id          TEXT PRIMARY KEY,         -- UUID hex
    novel_id    TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL,             -- 'manual' (JSON import) | 'llm_extracted' | 'mixed'
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (novel_id) REFERENCES sp_novels(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_bibles_novel ON sp_story_bibles(novel_id);


-- 角色表
CREATE TABLE IF NOT EXISTS sp_bible_characters (
    id              TEXT PRIMARY KEY,    -- char_NNN 格式(对齐 SCHEMA_DESIGN)
    bible_id        TEXT NOT NULL,
    name            TEXT NOT NULL,
    aka_json        TEXT,                 -- ["林先生", "老板"]
    description     TEXT,
    is_protagonist  INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (bible_id) REFERENCES sp_story_bibles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_bible_chars ON sp_bible_characters(bible_id);


-- 地点表
CREATE TABLE IF NOT EXISTS sp_bible_locations (
    id              TEXT PRIMARY KEY,    -- loc_NNN
    bible_id        TEXT NOT NULL,
    name            TEXT NOT NULL,
    int_ext         TEXT NOT NULL,        -- 'INT' | 'EXT' | 'INT/EXT'
    description     TEXT,
    FOREIGN KEY (bible_id) REFERENCES sp_story_bibles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_bible_locs ON sp_bible_locations(bible_id);


-- 关系表(可选,场景切分时辅助)
CREATE TABLE IF NOT EXISTS sp_bible_relationships (
    id              TEXT PRIMARY KEY,    -- rel_NNN
    bible_id        TEXT NOT NULL,
    source_char_id  TEXT NOT NULL,
    target_char_id  TEXT NOT NULL,
    type            TEXT NOT NULL,        -- '父子' | '夫妻' | '同事' | ...
    description     TEXT,
    FOREIGN KEY (bible_id) REFERENCES sp_story_bibles(id) ON DELETE CASCADE,
    FOREIGN KEY (source_char_id) REFERENCES sp_bible_characters(id) ON DELETE CASCADE,
    FOREIGN KEY (target_char_id) REFERENCES sp_bible_characters(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_bible_rels ON sp_bible_relationships(bible_id);


-- 事件表(关键剧情节点,后续场景切分时作锚点)
CREATE TABLE IF NOT EXISTS sp_bible_events (
    id                  TEXT PRIMARY KEY,    -- evt_NNN
    bible_id            TEXT NOT NULL,
    description         TEXT NOT NULL,
    chapter_number      INTEGER,             -- 大致发生在哪一章(LLM 给的估计)
    participant_ids_json TEXT,                -- ["char_001", "char_002"]
    FOREIGN KEY (bible_id) REFERENCES sp_story_bibles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_bible_events ON sp_bible_events(bible_id);
