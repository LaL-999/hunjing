-- migration 054: 给 characters 表加 aliases_json 列
-- 用于:
--   ① build_graph LLM 抽到的 entities[].aliases 落库(prompt v4 起强化称谓归一)
--   ② 用户在主角面板手动合并卡片时,把 source.aliases + source.name 累积到 target.aliases
--      (对齐 project_scenes 的合并语义)
-- 默认 NULL = 老角色无别名记录;非 NULL 必须是 JSON 数组字符串
-- 新建项目的 character 默认值由应用层负责([]) → JSON `[]`

ALTER TABLE characters ADD COLUMN aliases_json TEXT;
