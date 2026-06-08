-- migration 047: project_scenes 加 origin_simulation_id 字段
-- Sprint 6.A2 M8.A(2026-05-20)— 场景动态扩展 + 入库
--
-- 起源:LLM 在 outline 生成 / evolution 运行时**已被 prompt 允许自创新场景**
-- (m6_outline_generator.md 行 69 / m3_scene_picker.md 铁律 95-99),但自创的场景
-- 仅写入 outline_scenes / simulation_scenes 等运行时表,**从未反向入库 project_scenes**。
--
-- 后果(用户实测反馈):若原作只抽出 1 个场景"教室",后续 outline 清一色"教室",
-- LLM 不知道自己之前自创过新场景,只能撞已有的。
--
-- 修复:让 sequel_scene_sync(M7.B 角色入库的对标版)在 outline finalize /
-- evolution narrator 完成后,把所有 location 写入 project_scenes 表 → 后续推演的
-- scene_picker / outline 看到日益丰富的场景集,内容质量上升。
--
-- 字段:origin_simulation_id TEXT NULL
--   - NULL: 原作图谱抽取的场景(M2 链路;现有数据无 origin)
--   - 非 NULL: 续作 sim 自创入库的(前端"续作生成"badge / 项目场景列表区分展示)
--
-- 幂等:ALTER ADD COLUMN — SQLite 不允许多次 ADD 同名列,但 init_db.py 有"duplicate
-- column name 跳过"兜底(scripts/init_db.py L107)
-- created 2026-05-20

ALTER TABLE project_scenes
ADD COLUMN origin_simulation_id TEXT REFERENCES simulations(id) ON DELETE SET NULL;

-- 给 origin_simulation_id 加索引(给"按 sim 列续作场景"查询用)
CREATE INDEX IF NOT EXISTS idx_project_scenes_origin
    ON project_scenes(origin_simulation_id) WHERE origin_simulation_id IS NOT NULL;
