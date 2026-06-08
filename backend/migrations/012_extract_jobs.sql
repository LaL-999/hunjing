-- migration 012: graph_extraction_jobs
-- 中间态 / 末尾态 / 周期态项目从上传文件自动抽图谱(Sprint 2.B)。
-- 流水线:
--   queued → extracting_graph → generating_characters → inferring_meta → saving → done
--                                                                              ↘ failed
--
-- 设计原则(用户拍板):
--   * 允许重抽(无唯一索引限同 upload 只 1 个 active job)— 重抽走 is_admin_retag=1 标记;
--     batch save 时按 character.name 去重(项目内同名 skip,保留用户已手改的)
--   * type 不限定 schema 4 类(novel/comic/anime/generic):AI 自由识别 → custom_type_name 兜底
--   * tags 不预定义类目:AI 自由输出 → 软上限 10
--   * 配额:每次抽取扣 1 次 continuation(成本对齐推演)
-- created 2026-05-11 / Sprint 2.B

CREATE TABLE IF NOT EXISTS graph_extraction_jobs (
    id                          TEXT PRIMARY KEY,
    upload_id                   TEXT NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    user_id                     TEXT NOT NULL REFERENCES users(id),
    project_id                  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    state                       TEXT NOT NULL CHECK (state IN
                                    ('queued','extracting_graph','generating_characters',
                                     'inferring_meta','saving','done','failed')),

    is_admin_retag              INTEGER NOT NULL DEFAULT 0,    -- 1 = 重抽(同 upload 之前已有 done job)

    -- 中间产物(debug + 重试用)
    extracted_graph_json        TEXT,                          -- {entities, relations} 单次 LLM 输出
    inferred_type               TEXT,                          -- AI 识别的 type(novel/comic/anime/generic)
    inferred_custom_type_name   TEXT,                          -- AI 给的精准体裁(如"剧本杀")
    inferred_tags_json          TEXT,                          -- JSON array of tags

    -- 产物统计(save 阶段填)
    characters_count            INTEGER NOT NULL DEFAULT 0,
    relationships_count         INTEGER NOT NULL DEFAULT 0,
    events_count                INTEGER NOT NULL DEFAULT 0,
    skipped_count               INTEGER NOT NULL DEFAULT 0,    -- 重抽时按名字去重 skip 的数量

    -- 计费 + 错误
    tokens_input                INTEGER NOT NULL DEFAULT 0,
    tokens_output               INTEGER NOT NULL DEFAULT 0,
    cost_yuan                   REAL NOT NULL DEFAULT 0,
    error_message               TEXT,

    started_at                  TEXT NOT NULL,
    completed_at                TEXT
);

CREATE INDEX IF NOT EXISTS idx_extract_jobs_upload   ON graph_extraction_jobs(upload_id);
CREATE INDEX IF NOT EXISTS idx_extract_jobs_user     ON graph_extraction_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_extract_jobs_project  ON graph_extraction_jobs(project_id);
