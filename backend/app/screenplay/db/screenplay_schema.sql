-- ============================================================
-- 浑晶 · 剧创态 — 剧本组装持久化表(并入父平台,sp_ 前缀)
-- ============================================================
-- 设计原则:
--   1. 一次 compose 调 LLM 几十次,贵且慢 — 必须持久化,GET 直接返
--   2. 同一 novel 允许多次 compose(版本树)→ 每次新建一行
--   3. yaml_text 直接存 TEXT — demo YAML 撑死 50KB,SQLite 单字段几 MB 无忧
--   4. stats / warnings 用 TEXT 存 JSON
--   5. parent_screenplay_id 形成版本树,optimization_log_json 存改动详情
-- ============================================================

-- 剧本表 — 一次 compose / optimize 产出一行
CREATE TABLE IF NOT EXISTS sp_screenplays (
    id              TEXT PRIMARY KEY,                  -- UUID hex
    novel_id        TEXT NOT NULL,                      -- 关联 sp_novels.id
    yaml_text       TEXT NOT NULL,                      -- 完整剧本 YAML
    stats_json      TEXT NOT NULL DEFAULT '{}',         -- {total_scenes, total_pages_estimate, ...}
    warnings_json   TEXT NOT NULL DEFAULT '[]',         -- [{layer, path, message}, ...]
    failed_chapters_json TEXT NOT NULL DEFAULT '[]',
    schema_version  TEXT NOT NULL DEFAULT '1.0',
    model_name      TEXT,
    created_at      TEXT NOT NULL,                       -- ISO 8601
    -- 版本树字段(PR#16)
    parent_screenplay_id  TEXT,                          -- NULL = 初稿
    optimization_origin   TEXT,                          -- 'initial' | 'single_scene_<id>' | 'full_screenplay'
    optimization_log_json TEXT,                          -- {change_log, reasoning, fallback_reason}
    FOREIGN KEY (novel_id) REFERENCES sp_novels(id) ON DELETE CASCADE
);

-- 查询模式:GET /novels/{id}/screenplay 取最新一条 → 按 (novel_id, created_at DESC)
CREATE INDEX IF NOT EXISTS idx_sp_screenplays_novel_time
    ON sp_screenplays(novel_id, created_at DESC);

-- 版本切换:GET /novels/{id}/versions 拉同 novel 全部
CREATE INDEX IF NOT EXISTS idx_sp_screenplays_parent
    ON sp_screenplays(parent_screenplay_id);
