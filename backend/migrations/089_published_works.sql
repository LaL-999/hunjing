-- 089:作品广场(社区发布)— 2026-06-25
-- 用户把自己在浑晶创作的作品上架到广场,免费在线阅读 + 点赞 + 阅读量。
-- 纯 IF NOT EXISTS,幂等安全(不含 ALTER,避免重跑失败拖累整脚本)。

CREATE TABLE IF NOT EXISTS published_works (
    id                TEXT PRIMARY KEY,                 -- uuid
    user_id           TEXT NOT NULL,                    -- 作者
    -- 来源(作品由哪个创作产物上架而来)
    source_type       TEXT NOT NULL DEFAULT 'simulation'
                        CHECK (source_type IN ('simulation', 'comic', 'screenplay')),
    source_id         TEXT,                             -- 对应 simulation/comic/screenplay 的 id
    project_id        TEXT,                             -- 所属项目(可空)
    -- 展示信息
    title             TEXT NOT NULL,                    -- 用户自取的作品名
    summary           TEXT,                             -- 简介 / 摘要(自动截取或用户填)
    mode              TEXT NOT NULL,                    -- 创作态:initial/middle/end/cycle/screenplay
    original_title    TEXT,                             -- 原著名(初始态=原创世界,为 NULL)
    -- 封面:上传图优先,否则用 gradient 默认色(1-9)
    cover_image_path  TEXT,                             -- /api/plaza-covers/{user}/{file},空则用渐变
    cover_gradient    INTEGER NOT NULL DEFAULT 1,       -- 1-9 默认渐变封面编号
    -- 正文快照(上架时冻结,保证可读且不受源改动影响)
    content           TEXT NOT NULL DEFAULT '',
    word_count        INTEGER NOT NULL DEFAULT 0,
    -- 互动计数(去规范化,实时累加)
    like_count        INTEGER NOT NULL DEFAULT 0,
    read_count        INTEGER NOT NULL DEFAULT 0,
    -- 状态
    is_public         INTEGER NOT NULL DEFAULT 1,       -- 1=广场可见,0=下架
    published_at      TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pw_user      ON published_works(user_id);
CREATE INDEX IF NOT EXISTS idx_pw_public    ON published_works(is_public, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_pw_likes     ON published_works(is_public, like_count DESC);
CREATE INDEX IF NOT EXISTS idx_pw_source    ON published_works(source_type, source_id);

-- 点赞记录(每人每作品一次,可取消)— like_count 由它派生但去规范化加速读
CREATE TABLE IF NOT EXISTS published_work_likes (
    work_id     TEXT NOT NULL,
    user_id     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (work_id, user_id),
    FOREIGN KEY (work_id) REFERENCES published_works(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_pwl_user ON published_work_likes(user_id);
