-- 2026-06-01 hotfix:simulation_scenes 加 user_hint_applied 字段
-- 用于保留用户边写边干预的历史 — 之前 pending_scene_hint 一旦消费就清空,
-- 无法回看"我在第几幕给过哪些指令".此字段在主循环消费 hint 时同步写入对应幕,
-- 前端"提交记录"功能拉这些 hint 按 scene_index 展示.

ALTER TABLE simulation_scenes ADD COLUMN user_hint_applied TEXT DEFAULT NULL;
