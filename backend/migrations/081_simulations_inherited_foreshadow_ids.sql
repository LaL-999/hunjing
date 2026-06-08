-- 阶段 3A(2026-06-02):simulations 表加 inherited_foreshadow_ids_json
--
-- 用户在 SimulationDock 创建续作时,主动选择继承哪些项目级伏笔.
-- 字段:JSON array of foreshadow_ledger ids:
--   NULL  = 老 sim / 用户没用新 UI → outline_generator 默认拉所有 open(向后兼容)
--   '[]'  = 用户主动空选 → outline_generator 不读任何伏笔(独立创作)
--   '["id1","id2",...]' = 选择性继承

ALTER TABLE simulations ADD COLUMN inherited_foreshadow_ids_json TEXT DEFAULT NULL;
