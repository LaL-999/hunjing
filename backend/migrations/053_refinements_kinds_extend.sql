-- migration 053: 扩展 character_refinements.suggestion_kind CHECK 约束
-- 加入 Sprint 6.A2 M1 后续未补 migration 的 evolution_hint
--   + Sprint 6.A2 FOCUS(2026-05-21)新增的 behavior_baseline_补充
--
-- SQLite 不支持 ALTER TABLE 改 CHECK 约束 → 用经典"重建表"模式:
--   1. 创建新表 character_refinements_new(带新 CHECK)
--   2. INSERT INTO ... SELECT 拷数据
--   3. DROP 老表
--   4. RENAME 新表
--   5. 重建 idx_refine_status 索引
-- 全程在事务内,失败可回滚。

BEGIN;

CREATE TABLE character_refinements_new (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES refine_sessions(id) ON DELETE CASCADE,
    character_id        TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    suggestion_kind     TEXT NOT NULL CHECK (suggestion_kind IN
                          ('identity_补全','personality_补充','quote_补充',
                           'no_go_补充','consistency_警告',
                           'evolution_hint',
                           'behavior_baseline_补充')),
    suggestion_text     TEXT NOT NULL,
    suggestion_payload  TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','accepted','rejected','edited','skipped')),
    user_edit           TEXT,
    actioned_at         TEXT
);

INSERT INTO character_refinements_new
SELECT * FROM character_refinements;

DROP TABLE character_refinements;

ALTER TABLE character_refinements_new RENAME TO character_refinements;

CREATE INDEX IF NOT EXISTS idx_refine_status
    ON character_refinements(session_id, status);

COMMIT;
