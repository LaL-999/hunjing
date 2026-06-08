-- migration 010: audits
-- 自洽守护者诊断结果落库(Sprint 1.R)。
-- 对接 prompts/self_consistency_guardian.md + audit_service.audit_simulation()。
--
-- 设计:
--   * 同一 sim 允许多次诊断(用户改了角色再 audit 一次验证修好了)— 无 UNIQUE
--   * issues 内嵌 JSON 数组,不拆子表(读取永远是整组,无单条 query 需求)
--   * cost_yuan 落 audit 行,不滚进 simulations.cost_yuan(语义不同 — 推演成本 vs 诊断成本)
--   * 不与 refine_sessions 复用(refine 看角色设定,audit 看产物;字段口径完全不同)
-- created 2026-05-10 / Sprint 1.R

CREATE TABLE IF NOT EXISTS audits (
    id                          TEXT PRIMARY KEY,
    simulation_id               TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    user_id                     TEXT NOT NULL REFERENCES users(id),

    overall_score               INTEGER NOT NULL                 -- 0-100 LLM 自评
                                  CHECK (overall_score BETWEEN 0 AND 100),
    issues_json                 TEXT NOT NULL,                    -- JSON array of IssueItem
    regenerate_recommendation   TEXT NOT NULL,                    -- 一段建议文本

    tokens_input                INTEGER NOT NULL DEFAULT 0,
    tokens_output               INTEGER NOT NULL DEFAULT 0,
    cost_yuan                   REAL NOT NULL DEFAULT 0,
    duration_ms                 INTEGER NOT NULL DEFAULT 0,

    triggered_at                TEXT NOT NULL                     -- ISO 8601
);

-- 取最新诊断(同一 sim 多条,按时间倒序)
CREATE INDEX IF NOT EXISTS idx_audits_sim_time
    ON audits(simulation_id, triggered_at DESC);
