-- 2026-06-01:projects 加每章字数"区间"字段
-- 设计:用区间(min, max)而不是单值,让 AI 创作时不用强求精确字数 — 自由发挥,
-- 系统在 [min, max] 范围内贪婪找段落分隔符(\n\n)做章节切分,落点最自然.
-- 默认 1500-2500(中篇章节常用区间);min 不小于 500,max 不大于 10000,max ≥ min + 500.
-- CHECK 约束保证 min < max 且都在合理范围.

ALTER TABLE projects ADD COLUMN chapter_size_min INTEGER DEFAULT 1500
    CHECK (chapter_size_min >= 500 AND chapter_size_min <= 8000);
ALTER TABLE projects ADD COLUMN chapter_size_max INTEGER DEFAULT 2500
    CHECK (chapter_size_max >= 1500 AND chapter_size_max <= 10000);
