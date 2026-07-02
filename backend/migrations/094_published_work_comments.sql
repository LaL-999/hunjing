-- 作品广场 · 评论区(v5,2026-07-02)—— published_work_comments
--
-- 用户在作品详情页对公开作品发表评论。设计:
--   - 扁平评论(不做楼中楼,YAGNI;需要再迭代)
--   - 作者本人 + 评论人本人 都可删自己相关评论
--   - work 删除级联删评论;user 删除级联删其评论
--
-- 幂等:CREATE TABLE / INDEX IF NOT EXISTS,auto-migration 每次启动重跑无副作用。

CREATE TABLE IF NOT EXISTS published_work_comments (
    id          TEXT PRIMARY KEY,
    work_id     TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    FOREIGN KEY (work_id) REFERENCES published_works(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pwc_work ON published_work_comments(work_id, created_at DESC);
