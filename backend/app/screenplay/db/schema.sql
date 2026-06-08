-- ============================================================
-- 浑晶 · 剧创态 SQLite schema(并入父平台 huimeng.db)
-- ============================================================
-- 设计原则:
--   1. 表名加 sp_ 前缀,防与父平台原表冲突
--   2. 每张主表加 user_id INTEGER NOT NULL FK → users(id),数据按用户隔离
--   3. 章节 / 段落随 novel 级联清(ON DELETE CASCADE)
--   4. user_id 不级联(用户删除走父平台流程,sp_* 表暂留作审计)
--   5. 每段(paragraph)有稳定 index_in_chapter,后续场景切分能溯源回原文位置
-- ============================================================

-- 小说主表 — 用户上传的原始作品
CREATE TABLE IF NOT EXISTS sp_novels (
    id              TEXT PRIMARY KEY,         -- UUID hex
    user_id         INTEGER NOT NULL,          -- 父平台 users.id
    title           TEXT NOT NULL,             -- 作品标题(从文件名或元数据推断)
    source_format   TEXT NOT NULL,             -- 'txt' | 'epub' | 'docx'
    source_filename TEXT NOT NULL,             -- 原始上传文件名
    total_chars     INTEGER NOT NULL DEFAULT 0,
    total_chapters  INTEGER NOT NULL DEFAULT 0,
    uploaded_at     TEXT NOT NULL,             -- ISO 8601
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sp_novels_user_time ON sp_novels(user_id, uploaded_at DESC);


-- 章节表 — 自动分章后的结果
CREATE TABLE IF NOT EXISTS sp_chapters (
    id                TEXT PRIMARY KEY,        -- UUID hex
    novel_id          TEXT NOT NULL,
    number            INTEGER NOT NULL,         -- 1-based 章节号
    title             TEXT,                     -- 章节标题(若提取到)
    paragraph_count   INTEGER NOT NULL DEFAULT 0,
    char_count        INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (novel_id) REFERENCES sp_novels(id) ON DELETE CASCADE,
    UNIQUE (novel_id, number)
);

CREATE INDEX IF NOT EXISTS idx_sp_chapters_novel ON sp_chapters(novel_id, number);


-- 段落表 — 每段独立行,带稳定 index 用于溯源
CREATE TABLE IF NOT EXISTS sp_paragraphs (
    id                  TEXT PRIMARY KEY,      -- UUID hex
    chapter_id          TEXT NOT NULL,
    index_in_chapter    INTEGER NOT NULL,       -- 1-based 段落号(章节内)
    text                TEXT NOT NULL,
    FOREIGN KEY (chapter_id) REFERENCES sp_chapters(id) ON DELETE CASCADE,
    UNIQUE (chapter_id, index_in_chapter)
);

CREATE INDEX IF NOT EXISTS idx_sp_paragraphs_chapter ON sp_paragraphs(chapter_id, index_in_chapter);
