-- migration 014: counterfactual_changes(反事实变量结构化 — Sprint 2.C)
--
-- 用户在 3D 图谱里"动刀"改某个节点(角色属性 / 关系 / 事件)时,**先**记一条
-- counterfactual_change 再 PATCH 实际表。这样:
--   1. 推演时编译这些反事实成 director prompt 的 "变量上下文"区,LLM 显式知道
--      "这是用户的 what-if 假设,不是原作设定"
--   2. 用户可一键还原(reverted_at 打时间戳,推演忽略已撤销)
--   3. 重塑度第 1 维(可改角色数上限)校验直接 SELECT COUNT 这表 + 项目内活跃反事实
--   4. 重塑度第 3 维(图谱距离传播)从这表的 target_id 集合做 BFS 起点
--
-- 设计要点:
--   * old_value / new_value 都用 TEXT(JSON-friendly,方便存数组 / dict 字段)
--   * field 是细粒度(personality / quotes / no_go_list / ...)— 同一角色多次改不同字段
--     是多条反事实,各自独立可撤销
--   * applied_in_simulations_json:trace 哪些推演用过该反事实(后续审计 / 自洽守护者用)
--   * reverted_at IS NULL 表示 active;ON DELETE CASCADE 跟项目走
--
-- 注意 target_id 不加 FK — 跨多张表(characters / events / relationships)无法单一引用,
-- 业务层保证 target_id 有效;FK 由 ON DELETE CASCADE projects 间接维护(项目删则全删)
-- created 2026-05-11 / Sprint 2.C 反事实变量 + 重塑度第 1/3 维

CREATE TABLE IF NOT EXISTS counterfactual_changes (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    target_type   TEXT NOT NULL CHECK (target_type IN ('character','event','relationship')),
    target_id     TEXT NOT NULL,
    field         TEXT NOT NULL,             -- 'name' / 'identity' / 'personality' / 'quotes' / 'no_go_list' / 'description' / ...
    old_value     TEXT,                      -- 改前快照(JSON or plain text);新增节点时 NULL
    new_value     TEXT,                      -- 改后值
    created_at    TEXT NOT NULL,
    reverted_at   TEXT,                      -- 用户撤销时打时间戳,active 反事实 = WHERE reverted_at IS NULL
    applied_in_simulations_json TEXT NOT NULL DEFAULT '[]',  -- JSON array of simulation_id
    user_id       TEXT NOT NULL REFERENCES users(id)         -- 鉴权用(同 project 多用户暂不支持,但留字段)
);

CREATE INDEX IF NOT EXISTS idx_cf_project_active
    ON counterfactual_changes(project_id, reverted_at);
CREATE INDEX IF NOT EXISTS idx_cf_target
    ON counterfactual_changes(target_type, target_id, reverted_at);
CREATE INDEX IF NOT EXISTS idx_cf_user
    ON counterfactual_changes(user_id);
