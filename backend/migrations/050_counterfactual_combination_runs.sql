-- migration 050: counterfactual_combination_runs(反事实组合树批次记录)
-- Sprint 6.A2 CT(Counterfactual Tree,2026-05-21)
--
-- 起因:
--   用户在反事实工作台只能改单变量,看一个推演产物 → 无法对比"如果同时改 A+B+C 各自的'原/改'8 种组合,哪种最好玩"
--   组合树视图让用户一次配置 N 个反事实变量(每个 2 个值,共 2^N 个组合)→ 后端 fanout 启动 N 个 sim
--   → 前端决策树视图展示所有产物,叶节点 click 跳推演详情
--
-- 限制:
--   - 最多 3 个变量(2^3 = 8 个 sim),防止 token 爆炸
--   - 每个变量必须是已存在的 CounterfactualChange 行(用户先在 Workbench 创建)
--   - 同 project 同 user 允许多个 combination_run 并行,无去重约束
--
-- 关联:
--   - simulations 加 combination_run_id + tree_path_json 字段,inline 关联(避免 join)
--
-- created 2026-05-21 / Sprint 6.A2 CT

CREATE TABLE IF NOT EXISTS counterfactual_combination_runs (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- 用户勾选的反事实变量 + 每个的"原/改"二态(JSON)
    -- 格式:
    --   [
    --     {"counterfactual_id": "...", "label_a": "原-性格懦弱", "label_b": "改-性格勇敢"},
    --     {"counterfactual_id": "...", "label_a": "原-与父亲对立", "label_b": "改-与父亲和解"},
    --     ...
    --   ]
    -- 长度 1-3(2^1=2 / 2^2=4 / 2^3=8 个组合)
    selected_variables_json TEXT NOT NULL,

    -- 总组合数 = 2^len(selected_variables)
    total_combinations INTEGER NOT NULL CHECK (total_combinations IN (2, 4, 8)),

    -- 批次整体状态
    --   pending     — 已创建批次,sim 尚未启动
    --   generating  — 部分 / 全部 sim 在跑
    --   partial     — 部分 sim 完成 / 部分失败 / 部分仍在跑
    --   done        — 所有 sim 都已 done(无论失败 / 成功)
    --   failed      — 整批启动失败(配额不足 / 其他)
    state           TEXT NOT NULL DEFAULT 'pending' CHECK (
        state IN ('pending', 'generating', 'partial', 'done', 'failed')
    ),

    -- 失败原因(state=failed 时填)
    error_message   TEXT,

    created_at      TEXT NOT NULL,
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_combo_runs_project_user
    ON counterfactual_combination_runs(project_id, user_id, created_at DESC);


-- ============================================================
-- simulations 表加 combination_run_id + tree_path_json
-- ============================================================
-- combination_run_id NULL = 普通 sim;非 NULL = 该 sim 属于一个组合批次
-- tree_path_json    = 该 sim 在树中的路径,如 ["a", "b", "a"](长度 = 变量数)
--                     'a' 表示该变量取 label_a(原)/ 'b' 表示取 label_b(改)
ALTER TABLE simulations ADD COLUMN combination_run_id TEXT
    REFERENCES counterfactual_combination_runs(id) ON DELETE SET NULL;

ALTER TABLE simulations ADD COLUMN tree_path_json TEXT;

CREATE INDEX IF NOT EXISTS idx_sims_combination_run
    ON simulations(combination_run_id) WHERE combination_run_id IS NOT NULL;
