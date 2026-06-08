-- migration 035: characters 表加 3 个主角判定字段(Sprint 6.A1,2026-05-18)
--
-- 背景:Sprint 6.A1 落地"路径 C 多 agent 仿真"的第 1 步 — 把 characters 表升级为
--      统一 agent 档案层(前三态通用)+ 加主角判定机制。
--
-- 既有 4 个 agent 化字段(004 schema 就有,Sprint 0 角色对焦留下):
--   identity     角色身份 (200 字)
--   personality  性格 (500 字)
--   quotes       台词风格示例 JSON 数组
--   no_go_list   禁忌 JSON 数组
-- → 这 4 个字段在 6.A1 语义重定位为 "agent 档案",不改 schema
--
-- 本 migration 新增 3 字段(主角判定结果)+ 1 字段(用户手动覆盖标记):
--   is_protagonist          AI 判定 + 用户可改的主角标(true=主角,false=配角)
--   protagonist_score       0.0-1.0 综合评分(4 维度加权,排序卡片墙用)
--   protagonist_reasons_json  JSON 数组:["戏份持续 ✓", "贾母互动 7 次", ...]
--                              (透明告诉用户为什么 AI 判它是主角,可勾选时参考)
--   protagonist_user_pinned  用户是否手动勾过/取消(true 时 AI 重判不覆盖用户决定)
--
-- 3 态生效路径:
--   初始态:用户手动创角色 → is_protagonist 默认 false → 用户可手动勾选
--   中间/末尾态:extract 完工 → 自动调 protagonist_judger 批量判定 → 落 db
--                  → 用户在主角墙可勾选/取消(置 protagonist_user_pinned=true 后 AI 重判跳过)
--
-- created 2026-05-18 / Sprint 6.A1

ALTER TABLE characters ADD COLUMN is_protagonist           INTEGER NOT NULL DEFAULT 0;
ALTER TABLE characters ADD COLUMN protagonist_score        REAL    NOT NULL DEFAULT 0.0;
ALTER TABLE characters ADD COLUMN protagonist_reasons_json TEXT    NOT NULL DEFAULT '[]';
ALTER TABLE characters ADD COLUMN protagonist_user_pinned  INTEGER NOT NULL DEFAULT 0;

-- 索引:主角墙按 score 倒序拉时用
CREATE INDEX IF NOT EXISTS idx_characters_protagonist
    ON characters(project_id, is_protagonist DESC, protagonist_score DESC);
