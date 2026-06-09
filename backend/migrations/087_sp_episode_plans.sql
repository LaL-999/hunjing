-- migration 087:sp_episode_plans 表 — 分集方案持久化(2026-06-09)
--
-- 用户报告:分集功能 stateless,退出 modal 数据丢失 → 升级为有状态业务实体
--
-- 设计:
--   一个 novel 可有 N 个分集方案(用户多次跑不同 preset / target 想保留对比)
--   方案 = 当时 LLM 算法跑出来的 3 视角结果完整快照(plan_json)+ 用户命名
--   user 隔离:JOIN sp_novels 通过 user_id 限定(跟 sp_screenplays 一致)
--
-- 字段说明:
--   id              方案 ID(uuid / nanoid 由 app 生成)
--   novel_id        归属小说,跟 sp_novels FK
--   scheme_name     用户命名(如"短剧版 12 集 / 长剧版 24 集"),非空
--   preset          短剧 / 长剧 / 番剧 / 自定义(字符串枚举,app 层校验)
--   target_minutes  目标单集时长(便于列表显示)
--   recommended_perspective  rhythm / hook / arc(LLM 推荐的最佳视角)
--   episode_count   集数(便于列表显示)
--   scene_count     场景总数(便于列表显示)
--   plan_json       完整 MultiPerspectivePlan 序列化(3 视角 cuts + 评分 + 桥接)
--   created_at      创建时间(ISO 8601)
--   updated_at      最近更新(改名 / 重新算)

CREATE TABLE IF NOT EXISTS sp_episode_plans (
    id                       TEXT PRIMARY KEY,
    novel_id                 TEXT NOT NULL,

    -- 用户输入
    scheme_name              TEXT NOT NULL,

    -- 算法配置 snapshot(列表展示用)
    preset                   TEXT NOT NULL DEFAULT 'custom',
    target_minutes           REAL NOT NULL,
    recommended_perspective  TEXT,
    episode_count            INTEGER NOT NULL DEFAULT 0,
    scene_count              INTEGER NOT NULL DEFAULT 0,

    -- 完整 plan 数据
    plan_json                TEXT NOT NULL,

    -- 时间戳
    created_at               TEXT NOT NULL,
    updated_at               TEXT NOT NULL,

    FOREIGN KEY (novel_id) REFERENCES sp_novels(id) ON DELETE CASCADE
);

-- 查询模式:按 novel 倒序拉所有方案(列表用)
CREATE INDEX IF NOT EXISTS idx_sp_episode_plans_novel_created
    ON sp_episode_plans(novel_id, created_at DESC);
