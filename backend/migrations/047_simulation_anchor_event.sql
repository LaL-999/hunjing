-- migration 047: simulations 表加 anchor_event_id 字段
-- Sprint 6.A2 M7.J(2026-05-20)中间态起点锚点机制
--
-- 背景:用户反馈"中间态创作边界模糊" — 改了反事实之后是从原作末尾接 / 从被改位置重铸 / 还是从头?
-- 当前实现是"假设改后从独立新场景开始",但用户期待能选「从原作某事件之后开始推演」。
--
-- 本字段让中间态可选一个"起点锚点事件"(必须属于本项目);
-- 创建 sim 时传 anchor_event_id → director_user_prompt 加「## 起点锚点」段,
-- 让 LLM 第 1 幕承接锚点事件刚结束的情绪 / 物理位置 / 关系。
--
-- 兼容:
--   - 已存 sim 该字段为 NULL → 继续按旧"独立新场景"语义跑(无破坏)
--   - end / initial 态 schema 层拒收非空值
--   - 删除 event 时 ON DELETE SET NULL — sim 仍可运行,只是 anchor 丢失(LLM 看不到 anchor block)
-- created 2026-05-20

ALTER TABLE simulations
    ADD COLUMN anchor_event_id TEXT
        REFERENCES events(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_simulations_anchor_event
    ON simulations(anchor_event_id);
