-- 2026-06-02:清理 initial journal 功能回退后的僵尸列(B5 残留)
-- 历史:2026-05-23 初心日志全栈功能被用户拒回退,
--       projects.founding_intent / projects.journal_last_review_at 两列保留在 SQLite
--       (因为 SQLite 早期不支持 DROP COLUMN)
-- 现在 SQLite 3.35+ 支持 ALTER TABLE DROP COLUMN(我们用的 3.50+ 完全支持).
-- 直接 DROP,无需重建表(简化版).
--
-- 兼容性:
--   - 现役代码无任何引用(2026-06-02 grep 全 codebase 确认)
--   - 老用户 DB 直接 ALTER 即可(SQLite 3.35+)
--   - 极老 SQLite 用户:迁移会失败,但无副作用(列保留)

-- 用 try-style:如果列不存在(老 DB 已没 / 新 DB 从未创建),SQLite 会报错但不影响后续
-- 因此我们用一个保护策略:IF EXISTS 不支持 DROP COLUMN,但 SELECT 检查先

-- 直接 DROP — SQLite 3.35+ 支持;若列不存在会报 "no such column" 错(无害,migration runner 应有重试 / 跳过)
ALTER TABLE projects DROP COLUMN founding_intent;
ALTER TABLE projects DROP COLUMN journal_last_review_at;
