-- ============================================================
-- 085 浑晶 · 剧创态(第 5 创作态)— sp_* 全套 schema 迁入
-- ============================================================
-- 2026-06-08 阶段 4:把原阶段 3 的 _init_screenplay_schema 启动钩子
-- 规整进 migration runner 体系。删钩子,所有 schema 走同一条路径(_auto_apply_migrations)。
--
-- 设计:
--   1. 表名加 sp_ 前缀,与父平台原表(characters/events/relationships 等)解耦
--   2. 每张主表 user_id FK → users(id),数据按用户 SQL 级隔离
--   3. 章节 / 段落 / 圣经 / 剧本随 novel 级联清(ON DELETE CASCADE)
--   4. 全部 IF NOT EXISTS — 幂等可重跑(_auto_apply_migrations 启动调,已建库无伤)
--   5. 阶段 3 已用启动钩子建过表的老库:再跑此 migration 等于 noop
-- ============================================================


-- ============================================================
-- 1. 小说 / 章节 / 段落(原 schema.sql)
-- ============================================================

CREATE TABLE IF NOT EXISTS sp_novels (
    id              TEXT PRIMARY KEY,         -- UUID hex
    user_id         TEXT NOT NULL,            -- 父平台 users.id(UUID 字符串)
    title           TEXT NOT NULL,
    source_format   TEXT NOT NULL,             -- 'txt' | 'epub' | 'docx'
    source_filename TEXT NOT NULL,
    total_chars     INTEGER NOT NULL DEFAULT 0,
    total_chapters  INTEGER NOT NULL DEFAULT 0,
    uploaded_at     TEXT NOT NULL,             -- ISO 8601
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_novels_user_time
    ON sp_novels(user_id, uploaded_at DESC);


CREATE TABLE IF NOT EXISTS sp_chapters (
    id              TEXT PRIMARY KEY,
    novel_id        TEXT NOT NULL,
    number          INTEGER NOT NULL,
    title           TEXT,
    paragraph_count INTEGER NOT NULL DEFAULT 0,
    char_count      INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (novel_id) REFERENCES sp_novels(id) ON DELETE CASCADE,
    UNIQUE (novel_id, number)
);

CREATE INDEX IF NOT EXISTS idx_sp_chapters_novel
    ON sp_chapters(novel_id, number);


CREATE TABLE IF NOT EXISTS sp_paragraphs (
    id                TEXT PRIMARY KEY,
    chapter_id        TEXT NOT NULL,
    index_in_chapter  INTEGER NOT NULL,
    text              TEXT NOT NULL,
    FOREIGN KEY (chapter_id) REFERENCES sp_chapters(id) ON DELETE CASCADE,
    UNIQUE (chapter_id, index_in_chapter)
);

CREATE INDEX IF NOT EXISTS idx_sp_paragraphs_chapter
    ON sp_paragraphs(chapter_id, index_in_chapter);


-- ============================================================
-- 2. 故事圣经 5 张表(原 story_bible_schema.sql)
-- ============================================================
-- D2 决策:剧创态故事圣经数据走 A 隔离。
-- 阶段 5 huimeng_bridge 桥接层:角色 agent 调用时 JOIN 父平台
-- character_drivers / character_knowledge / relationship_polarity 表借力。

CREATE TABLE IF NOT EXISTS sp_story_bibles (
    id          TEXT PRIMARY KEY,
    novel_id    TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL,                 -- 'manual' | 'llm_extracted' | 'mixed'
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    FOREIGN KEY (novel_id) REFERENCES sp_novels(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_bibles_novel ON sp_story_bibles(novel_id);


CREATE TABLE IF NOT EXISTS sp_bible_characters (
    id              TEXT PRIMARY KEY,
    bible_id        TEXT NOT NULL,
    name            TEXT NOT NULL,
    aka_json        TEXT,                       -- ["林先生", "老板"]
    description     TEXT,
    is_protagonist  INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (bible_id) REFERENCES sp_story_bibles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_bible_chars ON sp_bible_characters(bible_id);


CREATE TABLE IF NOT EXISTS sp_bible_locations (
    id              TEXT PRIMARY KEY,
    bible_id        TEXT NOT NULL,
    name            TEXT NOT NULL,
    int_ext         TEXT NOT NULL,              -- 'INT' | 'EXT' | 'INT/EXT'
    description     TEXT,
    FOREIGN KEY (bible_id) REFERENCES sp_story_bibles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_bible_locs ON sp_bible_locations(bible_id);


CREATE TABLE IF NOT EXISTS sp_bible_relationships (
    id              TEXT PRIMARY KEY,
    bible_id        TEXT NOT NULL,
    source_char_id  TEXT NOT NULL,
    target_char_id  TEXT NOT NULL,
    type            TEXT NOT NULL,              -- '父子' | '夫妻' | '同事' | ...
    description     TEXT,
    FOREIGN KEY (bible_id) REFERENCES sp_story_bibles(id) ON DELETE CASCADE,
    FOREIGN KEY (source_char_id) REFERENCES sp_bible_characters(id) ON DELETE CASCADE,
    FOREIGN KEY (target_char_id) REFERENCES sp_bible_characters(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_bible_rels ON sp_bible_relationships(bible_id);


CREATE TABLE IF NOT EXISTS sp_bible_events (
    id                      TEXT PRIMARY KEY,
    bible_id                TEXT NOT NULL,
    description             TEXT NOT NULL,
    chapter_number          INTEGER,            -- LLM 给的章节估计
    participant_ids_json    TEXT,               -- ["char_001", "char_002"]
    FOREIGN KEY (bible_id) REFERENCES sp_story_bibles(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_bible_events ON sp_bible_events(bible_id);


-- ============================================================
-- 3. 剧本表(原 screenplay_schema.sql)— 版本树字段直接在 CREATE 内
-- ============================================================
-- PR#10 commit 2 持久化 + PR#16 版本树字段。
-- 一次 compose / optimize 产出一行,parent_screenplay_id 形成版本树。

CREATE TABLE IF NOT EXISTS sp_screenplays (
    id                      TEXT PRIMARY KEY,
    novel_id                TEXT NOT NULL,
    yaml_text               TEXT NOT NULL,
    stats_json              TEXT NOT NULL DEFAULT '{}',
    warnings_json           TEXT NOT NULL DEFAULT '[]',
    failed_chapters_json    TEXT NOT NULL DEFAULT '[]',
    schema_version          TEXT NOT NULL DEFAULT '1.0',
    model_name              TEXT,
    created_at              TEXT NOT NULL,
    -- 版本树字段(PR#16)
    parent_screenplay_id    TEXT,                                -- NULL = 初稿
    optimization_origin     TEXT,                                -- 'initial' | 'single_scene_<id>' | 'full_screenplay'
    optimization_log_json   TEXT,                                -- {change_log, reasoning, fallback_reason}
    FOREIGN KEY (novel_id) REFERENCES sp_novels(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_screenplays_novel_time
    ON sp_screenplays(novel_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_sp_screenplays_parent
    ON sp_screenplays(parent_screenplay_id);


-- ============================================================
-- 4. 关于 in-place ALTER 兜底:本 migration 不需要
-- ============================================================
-- 阶段 3 的 screenplay_schema.sql 已经在 CREATE TABLE sp_screenplays 内联了
-- parent_screenplay_id / optimization_origin / optimization_log_json 三列。
-- 也就是说:任何走过阶段 3 的库都已经有这 3 列;走阶段 4 直接 migration
-- 的新库也是 CREATE TABLE 时就有 — 不需要 ALTER。
--
-- 为什么强调:auto-migration runner 一个 migration 文件走 executescript
-- 包在一个 transaction 里,任何语句失败整脚本回滚。若加 ALTER ADD COLUMN
-- 兜底,fresh DB 上 ALTER 会触发 "duplicate column name",整脚本回滚 →
-- 灾难。所以本 migration 严格只用 IF NOT EXISTS DDL。
