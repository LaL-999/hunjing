-- migration 052: events 加 time_anchor 字段
-- INIT.7(2026-05-21)— 初始态事件时间锚
--
-- 用户在初始态创建事件时可指定时间锚(如"第 5 章" / "T0+3 天" / "决战之夜"),
-- 供未来的关系阶段触发(trigger_event_id 绑定)+ LLM 推演时知道事件先后。
--
-- 字段语义:
--   time_anchor TEXT NULL — 自由文本,30 字内;NULL = 未指定
--
-- 老数据兼容:
--   - NULL 表示未指定锚点,前端显示为空,LLM 也不参考
--   - 不影响现有事件渲染 / 推演逻辑
--
-- created 2026-05-21 / Sprint 6.A2 INIT

ALTER TABLE events ADD COLUMN time_anchor TEXT;
