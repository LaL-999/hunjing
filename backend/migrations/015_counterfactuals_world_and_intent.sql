-- migration 015: counterfactual_changes 扩展 — Sprint 2.C+
--
-- 改动 vs 014:
--   1. CHECK 加 'world' target_type(全局世界观反事实,target_id 固定 '_global_')
--   2. 新加 user_intent TEXT(用户自然语言描述的"想要的效果",**比字段 diff 优先级高**)
--   3. 新表 simulation_counterfactual_links — 推演与反事实的多对多关联
--      (用户可在 SimulationDock 勾选本次推演用哪些反事实;默认 = 全部 active)
--
-- ⚠ destructive:此 migration 会清空 counterfactual_changes 表数据
--    背景:014 是同一 sprint 内加,生产数据应为空(用户截图证实未用过反事实)
--    幂等:多次跑 init_db 安全(DROP IF EXISTS / CREATE IF NOT EXISTS)
-- created 2026-05-11 / Sprint 2.C+ 反事实工作台 + 世界观维度

DROP INDEX IF EXISTS idx_cf_project_active;
DROP INDEX IF EXISTS idx_cf_target;
DROP INDEX IF EXISTS idx_cf_user;
DROP TABLE IF EXISTS counterfactual_changes;

CREATE TABLE IF NOT EXISTS counterfactual_changes (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    -- 'world' 是 2.C+ 新加 — 全局世界观反事实(覆盖所有原作设定)
    -- target_id 固定 '_global_';field 是世界观维度名(genre / setting / magic_system / time_axis / tone / free_form)
    target_type   TEXT NOT NULL CHECK (target_type IN ('character','event','relationship','world')),
    target_id     TEXT NOT NULL,

    field         TEXT NOT NULL,             -- character/event/relationship 的字段名;world 类型用维度名
    old_value     TEXT,                      -- 改前快照(JSON or plain);world 类型可为 NULL(原作语义自由文本)
    new_value     TEXT,                      -- 改后值

    -- ⭐ 2.C+ 核心新增:用户的自然语言意图("我想让小红勇敢起来 / 把都市改成星际")
    -- LLM 比 old/new diff 更看重这个 — 它告诉 director 用户的"为什么改",不只是"改成什么"
    user_intent   TEXT,

    created_at    TEXT NOT NULL,
    reverted_at   TEXT,                      -- active = WHERE reverted_at IS NULL
    applied_in_simulations_json TEXT NOT NULL DEFAULT '[]',  -- trace 用过哪些推演
    user_id       TEXT NOT NULL REFERENCES users(id)
);

CREATE INDEX IF NOT EXISTS idx_cf_project_active
    ON counterfactual_changes(project_id, reverted_at);
CREATE INDEX IF NOT EXISTS idx_cf_target
    ON counterfactual_changes(target_type, target_id, reverted_at);
CREATE INDEX IF NOT EXISTS idx_cf_user
    ON counterfactual_changes(user_id);


-- 2.C+ 新表:推演与反事实的多对多关联
-- 用户在 SimulationDock 勾选本次推演用哪些反事实;
-- 表为空 = 用全部 active 反事实(向下兼容现状);
-- 表非空 = 只用关联的子集
CREATE TABLE IF NOT EXISTS simulation_counterfactual_links (
    simulation_id     TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    counterfactual_id TEXT NOT NULL REFERENCES counterfactual_changes(id) ON DELETE CASCADE,
    PRIMARY KEY (simulation_id, counterfactual_id)
);

CREATE INDEX IF NOT EXISTS idx_sim_cf_links
    ON simulation_counterfactual_links(simulation_id);
