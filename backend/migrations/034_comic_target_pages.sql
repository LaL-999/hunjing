-- migration 034: comic_projects 加 target_pages 字段
-- Sprint C.4(2026-05-13)— AI Planner 推荐 + 用户拍板的目标页数
--
-- 默认 12(Sprint 2.B+ 七修后的"短篇"默认);AI Planner 扫源文本后可建议更高
-- (短文本可降到 6,长文本可升到 18,上限对齐 prompts/screenwriter.md 铁律 1)。
--
-- 老 _agent_scripter 硬编码 12;C.4 改成从 comic.target_pages 拿,允许用户在创建时调。

ALTER TABLE comic_projects ADD COLUMN target_pages INTEGER NOT NULL DEFAULT 12;
