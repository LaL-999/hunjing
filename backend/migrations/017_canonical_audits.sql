-- migration 017: canonical_audits — Sprint 2.D 正典守护者(差异化王牌)
--
-- 设计哲学(doc 3 评"竞品短期内难抄走"):
--   自洽守护者 1.R:产物 vs 用户设定(应然)
--   正典守护者 2.D:产物 vs 原作 canon(已然)— 这表
--
-- 仅 middle / end / cycle mode 启用(initial 态没原作可守,UI 自动 N/A)
-- 不扣配额(对齐 1.R)— 创作辅助工具
--
-- ⚠ 关键设计:必须识别"用户反事实重塑的部分"→ 反事实 cover 的字段在
-- 审计中标"用户主动重塑,豁免"而非"违规"(否则用产品 = 天天被告状)
--
-- 8 维度审计(prompts/canonical_guardian.md LOCKED v1):
--   ① 角色性格连贯(原作底色)
--   ② 关系网络
--   ③ 世界观一致(体裁/超能力/时间轴)
--   ④ 时代物理可行性
--   ⑤ 叙事语调底色
--   ⑥ 关键事件因果(伏笔呼应)
--   ⑦ 道德/价值取向
--   ⑧ 细节真实(称谓/礼仪/风俗)
--
-- 表结构镜像 audits(1.R):同 state 机 + 同 issues_json schema(各维度等级 + 证据 + 引用)
-- created 2026-05-11 / Sprint 2.D

CREATE TABLE IF NOT EXISTS canonical_audits (
    id              TEXT PRIMARY KEY,
    simulation_id   TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL REFERENCES users(id),

    state           TEXT NOT NULL CHECK (state IN ('running', 'done', 'failed')),

    -- JSON: list of {dimension, severity, finding, evidence_excerpt, canon_reference, counterfactual_exempt}
    issues_json     TEXT NOT NULL DEFAULT '[]',

    tokens_input    INTEGER NOT NULL DEFAULT 0,
    tokens_output   INTEGER NOT NULL DEFAULT 0,
    cost_yuan       REAL NOT NULL DEFAULT 0,
    error_message   TEXT,

    created_at      TEXT NOT NULL,
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_canonical_audits_simulation
    ON canonical_audits(simulation_id);
CREATE INDEX IF NOT EXISTS idx_canonical_audits_user
    ON canonical_audits(user_id);
