-- migration 055: 给 projects 表加 narrative_pov 列
-- Sprint 6.A2 FOCUS.2(2026-05-21):作品叙述视角识别 + 续写一致性
--
-- 用途:
--   ① 抽取阶段:build_graph v5 LLM 从原作识别叙述人称,落到此字段
--   ② 续写阶段:m3_narrator / m6_outline_generator 等 prompt 从此读取,
--      在生成时强制延续原作人称,防止跨幕 / 跨代漂移
--   ③ 自检阶段:consistency_checker 用此字段检测 narrator 输出的人称代词频次,
--      偏离 > 阈值触发重写(复用 M4.3 retry 回路)
--   ④ 前端:ProjectView header 显示"叙述视角"chip,允许用户手动修改
--
-- 枚举值:
--   "first"  = 第一人称("我"叙事)
--   "second" = 第二人称("你"叙事,极罕见,《如果在冬夜》风格)
--   "third"  = 第三人称("他/她"叙事,默认假设)
--   "mixed"  = 混合人称(多视角切换,如《罗生门》)
--   NULL     = 未识别 / 老项目;续写时退回到"模仿原作末段笔法"现有兜底

ALTER TABLE projects ADD COLUMN narrative_pov TEXT;
