-- migration 049: outline_scenes 加 tension_percent + pacing_tempo
-- Sprint 6.A2 MP(M-planner,2026-05-21)— Planner 介入 + 张力调节
--
-- 起因:
--   LLM 在写长篇时容易"前 5 幕铺垫到 25 幕才打高潮"或"全程平淡无起伏",
--   outline 阶段虽有 scene_purpose(6 个标签),但缺少:
--     - "本幕相对于全篇的张力百分比"(0-100 连续值)
--     - "节奏速度"(fast 快速推进 / normal 常态 / slow 缓慢铺垫)
--
-- 方案:
--   每幕生成前加一步 LLM planner,根据 narrative_so_far + 剩余幕数动态规划张力
--   outline-first 模式:planner 输出回写 outline_scene 表(用户可见 + 持久化)
--   灵魂续写模式:planner 输出仅注入本幕 narrator system_prompt(transient)
--
-- 取值范围:
--   tension_percent  INTEGER 0-100,NULL = 未规划(老数据 / 灵魂续写 transient)
--   pacing_tempo     TEXT 'fast' | 'normal' | 'slow' | NULL
--
-- created 2026-05-21 / Sprint 6.A2 MP

ALTER TABLE outline_scenes ADD COLUMN tension_percent INTEGER;
ALTER TABLE outline_scenes ADD COLUMN pacing_tempo TEXT;
