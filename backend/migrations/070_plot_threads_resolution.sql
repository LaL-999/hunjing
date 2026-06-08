-- migration 070: plot_threads 加预期回收点 + 废弃标记
-- SP-5(2026-05-28)— 伏笔账本完善 + 前端面板暴露
--
-- 设计:
--   plot_threads 后端(migration 040)已齐全(description / priority / staleness /
--   resolved_at_scene_index),但缺两个语义:
--     1. **预期回收点**:用户/outline 预期"这条伏笔应该在第 N 幕回收"
--        → 系统可"接近预期幕了但还没解决"时给前端高亮警示
--     2. **废弃区分**:resolved_at_scene_index 只能表达"完成",无法区分
--        "圆满回收"vs"故意放弃(LLM 算了不写了)"
--
-- 新字段:
--   expected_resolution_scene  INTEGER NULL — 预期在第几幕回收(NULL = 用户没填)
--   is_abandoned               INTEGER 0/1 — 是否被用户标记废弃(默认 0,与 resolved_at 互斥)
--
-- 状态枚举(派生,非 DB 字段):
--   resolved_at IS NULL AND is_abandoned=0 → ACTIVE(进行中)
--   resolved_at IS NOT NULL                → RESOLVED(已完成)
--   is_abandoned=1                         → ABANDONED(废弃)
--
-- 前端 SP-5 新面板:列出三类伏笔 + 悬空预警(ACTIVE 接近预期幕还没解决)

ALTER TABLE plot_threads ADD COLUMN expected_resolution_scene INTEGER;
ALTER TABLE plot_threads ADD COLUMN is_abandoned INTEGER NOT NULL DEFAULT 0;
