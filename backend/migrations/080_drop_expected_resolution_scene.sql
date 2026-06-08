-- 2026-06-02:删除 plot_threads.expected_resolution_scene 列
--
-- 用户产品决策:"伏笔在哪幕闭环是导演/LLM 的工作,不是用户的工作"
-- 之前(migration 070)加的这个字段让用户填"预期回收幕",但:
--   1. 在 outline 模式下用户根本看不出哪些伏笔放在第几幕合理
--   2. 用户填的值会**干扰 LLM 编导的自由发挥**
--   3. F1.2 加的"按距离排序"反而成了 LLM 走向的束缚
--
-- 删此列后:
--   - UI 不再显示"预期回收幕"输入框
--   - LLM 按 priority + staleness 自然排序(主线优先 + 停滞越久越优先推)
--   - 用户只需要负责"标废弃"(主动剪枝) → 保留
--
-- SQLite 3.35+ 支持 ALTER TABLE DROP COLUMN(Python 3.11 自带 3.40+,安全)
-- 老数据(若曾填过预期幕)的值会被丢弃 — 没有备份必要(本来就是非业务关键字段)

ALTER TABLE plot_threads DROP COLUMN expected_resolution_scene;
