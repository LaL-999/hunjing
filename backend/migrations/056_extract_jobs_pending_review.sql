-- migration 056: graph_extraction_jobs.state 加 'entities_pending_review'
-- Sprint 6.A2 FOCUS.10(2026-05-22):人机协同审核阶段
--
-- 新流水线:
--   queued → extracting_graph → entities_pending_review (用户审 entities) →
--     generating_characters → inferring_meta → saving → done
--                                                  ↘ failed
--
-- entities_pending_review:
--   * extracted_graph_json 已落库,worker 早退等待用户审核
--   * 用户在前端 modal 列表里增删 PERSON entity,POST /jobs/:id/approve_entities
--   * 端点更新 extracted_graph_json + state='generating_characters' + kick_off_extract 恢复
--
-- SQLite 不支持 ALTER CHECK 约束 → 重建表(参照 053):

BEGIN;

CREATE TABLE graph_extraction_jobs_new (
    id                          TEXT PRIMARY KEY,
    upload_id                   TEXT NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    user_id                     TEXT NOT NULL REFERENCES users(id),
    project_id                  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    state                       TEXT NOT NULL CHECK (state IN
                                    ('queued','extracting_graph',
                                     'entities_pending_review',
                                     'generating_characters',
                                     'inferring_meta','saving','done','failed')),
    is_admin_retag              INTEGER NOT NULL DEFAULT 0,
    extracted_graph_json        TEXT,
    inferred_type               TEXT,
    inferred_custom_type_name   TEXT,
    inferred_tags_json          TEXT,
    characters_count            INTEGER NOT NULL DEFAULT 0,
    relationships_count         INTEGER NOT NULL DEFAULT 0,
    events_count                INTEGER NOT NULL DEFAULT 0,
    skipped_count               INTEGER NOT NULL DEFAULT 0,
    tokens_input                INTEGER NOT NULL DEFAULT 0,
    tokens_output               INTEGER NOT NULL DEFAULT 0,
    cost_yuan                   REAL NOT NULL DEFAULT 0,
    error_message               TEXT,
    started_at                  TEXT NOT NULL,
    completed_at                TEXT
);

INSERT INTO graph_extraction_jobs_new
SELECT * FROM graph_extraction_jobs;

DROP TABLE graph_extraction_jobs;

ALTER TABLE graph_extraction_jobs_new RENAME TO graph_extraction_jobs;

CREATE INDEX IF NOT EXISTS idx_extract_jobs_upload   ON graph_extraction_jobs(upload_id);
CREATE INDEX IF NOT EXISTS idx_extract_jobs_user     ON graph_extraction_jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_extract_jobs_project  ON graph_extraction_jobs(project_id);

COMMIT;
