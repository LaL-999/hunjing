-- migration 029:comic_projects 加 script_json 字段(D.9 Sprint 2.A)
--
-- ADR v3 §3.2 Agent #2 编剧产物 — 整本剧本 JSON(pages → panels 二级结构)
--
-- 为什么用 JSON 字段而不是新表:
--   - 剧本一旦生成基本不改(用户重生成 = 整本重跑)
--   - 单本剧本 ~30 页 × 6 格 = 180 panel records,JSON 字符串 ~20-50 KB,SQLite 容易扛
--   - 单字段读 / 写,简单;新表会让 service 多一层 join 复杂度
--
-- 字段结构:见 prompts/screenwriter.md "分镜结构" 章节
--
-- created 2026-05-12 / Sprint D.9 Sprint 2.A

ALTER TABLE comic_projects ADD COLUMN script_json TEXT;
