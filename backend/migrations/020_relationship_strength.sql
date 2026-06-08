-- migration 020: relationships.strength + projects.graph_strength_threshold
-- Sprint 3.A polish — 3D 图谱关系强度阈值机制(用户拍板 2026-05-11)。
--
-- 问题:LLM 抽图谱后,3D 图谱常出现"孤立角色"— 用户怀疑是抽取精度不够,
-- 但其实是这些角色在原作中本就跟其它角色弱相关。如果不加阈值,关系网会爆炸
-- (大长篇角色数 × 角色数 N² 关系组合),完全无法可视化。
--
-- 解法:
--   1. LLM 给每个 relationship 打 5 级强度 enum
--      strong / moderately_strong / moderate / moderately_weak / weak
--   2. 每个项目独立维护一个 graph_strength_threshold(10-80),用户可自定义
--      数值越高显示越严格(只显强关系),越低越宽松(包括弱关系也显)
--   3. 默认 30(过滤掉 'weak',留 top 80%)— 对应"5 等级,只弱不显示"语义
--
-- 5 等级 score 映射(前端用):
--   weak=10  /  moderately_weak=30  /  moderate=50  /  moderately_strong=70  /  strong=90
--   阈值 X% → 显示 score >= X
--   - X=10 → 全显示(包括 weak)
--   - X=30 → 默认,过滤 weak
--   - X=50 → 过滤 weak + moderately_weak
--   - X=70 → 只显 moderately_strong + strong
--   - X=80 → 只显 strong
--
-- 老 relationship 兼容:strength 默认 'moderate'(中性,不会影响默认 threshold=30 的显示);
-- 老 project 兼容:graph_strength_threshold 默认 30。
--
-- created 2026-05-11 / Sprint 3.A polish

ALTER TABLE relationships ADD COLUMN strength TEXT NOT NULL DEFAULT 'moderate';
-- SQLite ALTER ADD COLUMN 不支持 CHECK,服务层 + Pydantic 白名单兜底:
--   合法值 = strong / moderately_strong / moderate / moderately_weak / weak

ALTER TABLE projects ADD COLUMN graph_strength_threshold INTEGER NOT NULL DEFAULT 30;
-- 10-80 范围由 Pydantic 校验;DB 不加 CHECK 避免后续调整范围时还要 schema 迁移
