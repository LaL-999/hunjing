-- migration 045: characters.origin_simulation_id(Sprint 6.A2 M7.B,2026-05-20)
--
-- 背景:M7.A 修复"正典守护者误判新角色"后,用户提出:
--   "续作新增的角色也要实时更新到角色表中(如果有的话),也需要添加到关系图谱"
--
-- 设计:
--   - characters 表加 origin_simulation_id(可空)— 标记该角色来自哪次推演
--     · NULL  = 原作角色(手动创建 / 原作 extract 抽出)
--     · 非 NULL = 续作产物中新出现 → 自动入库的"续作血液"
--   - FK ON DELETE SET NULL — 推演被删除时,新角色仍保留(用户可能已编辑使用)
--   - 索引 (project_id, origin_simulation_id) 给前端查询"这个推演产生了哪些新角色"用
--
-- 关联服务:
--   - sequel_character_sync.py(新增):每幕 entity_registrar 后调用,
--     character 类型 canonical_entities 同步到 characters(name 不冲突时新增,冲突时跳过)
--   - 同时为新角色自动建一条与主角的关系("其他"类型,description 提示来自续作)

ALTER TABLE characters ADD COLUMN origin_simulation_id TEXT REFERENCES simulations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_characters_origin_sim
    ON characters(project_id, origin_simulation_id);
