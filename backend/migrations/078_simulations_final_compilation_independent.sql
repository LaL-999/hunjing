-- 2026-06-01:把"走向终章 + 滚雪球合并最终作品"从源 sim 字段抽出为独立 sim 行
-- 治"用户在源 sim 详情页只能导出本 sim narrative,不能导出合并后的完整最终作品"
--
-- 设计:
--   - sim 完成 + with_grand_finale + 有 ancestors → 自动新建一个 sim 行,
--     标记 is_final_compilation=1,narrative = 合并文本
--   - 来源 sim 的 final_compiled_narrative 字段不再写(保留旧 schema 向下兼容,
--     但前端 has_final_work 改为查 is_final_compilation=1 + compiled_from_sim_id 反查)
--   - 合并 sim 不算创作 cost / round / target_chars 配额(后端 cost=0)
--
-- 字段:
--   is_final_compilation:0=普通 sim;1=自动合并产物
--   compiled_from_sim_id:触发合并的源 sim id;FK 软引用(源 sim 删了不级联,合并产物保留)

ALTER TABLE simulations ADD COLUMN is_final_compilation INTEGER DEFAULT 0
    CHECK (is_final_compilation IN (0, 1));

ALTER TABLE simulations ADD COLUMN compiled_from_sim_id TEXT DEFAULT NULL;

-- 索引:列表查询时按 is_final_compilation 区分主推演 vs 合并产物
CREATE INDEX IF NOT EXISTS idx_simulations_is_final_compilation
    ON simulations(project_id, is_final_compilation);

-- 索引:从合并产物快速反查源 sim
CREATE INDEX IF NOT EXISTS idx_simulations_compiled_from
    ON simulations(compiled_from_sim_id)
    WHERE compiled_from_sim_id IS NOT NULL;
