-- ============================================================
-- 086 sp_novels 加 linked_project_id —— 剧创态对接父平台项目
-- ============================================================
-- 2026-06-08 阶段 5(huimeng_bridge 桥接层前置 migration):
--
-- 剧创态从小说抽出来的角色没有 project_id,而父平台的 SP-2/3/4/7 资产
-- (character_drivers / character_knowledge / character_state_snapshots /
--  relationship polarity / story_facts)都按 project_id 组织。
--
-- 桥接方案:用户可以把一本剧创态小说"绑定"到自己的某个浑晶项目 —
-- 同名角色直接复用父平台已建的驱动力 / 知识边界 / 关系正负极。
--
-- 字段语义:
--   linked_project_id NULL → 未绑定,bridge 退化为"全 project 同名匹配"启发式
--   linked_project_id 非空 → bridge 严格用该项目的角色 + 关系 + 事实
--
-- 用户绑定流程(后续 endpoint,本 migration 只加字段):
--   PATCH /api/screenplay/novels/{id}/link  body: { project_id }
--   → 校验该 project 属于当前 user → 写入
--
-- 为什么不强制必填:
--   1. 用户可能直接上传小说,从未在父平台建过相关项目 → 仍能用基础剧创态
--   2. 启发式匹配是兜底,即使不绑定也能 60% 覆盖(同名角色)
--   3. 阶段 7 测试要覆盖 NULL + 已绑定 两条路径
-- ============================================================

ALTER TABLE sp_novels ADD COLUMN linked_project_id TEXT
    REFERENCES projects(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_sp_novels_linked_project
    ON sp_novels(linked_project_id);
