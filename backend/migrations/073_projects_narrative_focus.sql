-- migration 073: projects 加视角扩展三件套
-- SP-8(2026-05-28)— 视角细化:焦点人物 / 可靠性 / 距离
--
-- 起因:
--   narrative_pov(migration 055)只记人称(first/second/third/mixed),
--   但作家视角是 3 个独立维度:
--     1. 人称       :第一/第二/第三(已有 narrative_pov)
--     2. 焦点人物   :第三人称限知 / 多视角时,以哪个角色眼中看世界
--     3. 叙述者可靠性:可信 / 不可信(unreliable narrator 是文学手法)
--     4. 叙述距离   :全知(远) / 限知(近) / 内心(更近)
--
-- 设计:
--   narrative_focus_character_id  TEXT  REF characters.id NULL — 焦点角色 id
--   narrator_reliability          TEXT  'reliable'/'unreliable'/'uncertain' NULL
--   narrative_distance            TEXT  'omniscient'/'limited'/'close'/'intimate' NULL
--
-- 用户/Planner 填法:
--   - 中间态 build_graph 抽取时 LLM 推断填(SP-8.1 升级)
--   - 用户在 ProjectView 顶部"视角"section 可改(SP-8.1 前端)
--
-- 与已有 narrative_pov 正交 — 4 维度独立维度.

ALTER TABLE projects ADD COLUMN narrative_focus_character_id TEXT
    REFERENCES characters(id) ON DELETE SET NULL;
ALTER TABLE projects ADD COLUMN narrator_reliability TEXT
    CHECK (narrator_reliability IN ('reliable', 'unreliable', 'uncertain'));
ALTER TABLE projects ADD COLUMN narrative_distance TEXT
    CHECK (narrative_distance IN ('omniscient', 'limited', 'close', 'intimate'));
