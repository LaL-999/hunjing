-- migration 068: characters 加角色驱动力五件套(SP-2,2026-05-28)
--
-- 灵魂续写北极星·角色驱动层:
-- 当前模型最大的洞 — 角色只有"描述"(identity / personality / behavior_baseline),
-- 没有"驱动"(他想要什么、藏什么、缺什么、要变成什么).
-- LLM 拿到的角色像静态布偶,只会被动反应;给"goal+need+secret"后,他变成主动 agent
-- 推着剧情走 — 这是从被动反应升级到主动驱动的根本性升级.
--
-- 五字段(对照 Claude chat 建议):
--   surface_goal:表层目标(他嘴上追的)— 例:渡边"想跟绿子相处但又放不下直子"
--   deep_need:深层需要(他其实缺却不肯承认的)— 例:渡边"需要承认直子已死、放下自责"
--   fatal_blind_spot:致命盲区(看不见自己的问题)— 例:渡边"以为陪着直子能救她,其实是逃避"
--   arc_from_to:预期弧光("从 X 变到 Y"短句)— 例:"从沉溺自责 → 接受死亡 → 选择活下去"
--   secret_json:秘密列表 JSON [{description, hidden_from?}]
--     例:[{"description":"曾在直子病房答应永远陪她","hidden_from":["绿子"]}]
--
-- 与已有字段的语义分层(防混淆):
--   - identity     = 我是谁(身份)
--   - personality  = 我会怎么做(性格)
--   - quotes       = 我说话什么语气
--   - no_go_list   = 我一般不会做什么(禁忌)
--   - behavior_baseline = 语气/强度/罗盘量化基线(M4.3)
--   ─── 以上是"描述层",静态 ───
--   - surface_goal / deep_need / fatal_blind_spot / arc_from_to / secret_json
--   ─── 以下是"驱动层",动态(SP-2 新加)───
--
-- 默认 NULL — 老角色无影响;用户可手填,后续 SP-2.1 让 build_graph 抽取后回填.

ALTER TABLE characters ADD COLUMN surface_goal TEXT;
ALTER TABLE characters ADD COLUMN deep_need TEXT;
ALTER TABLE characters ADD COLUMN fatal_blind_spot TEXT;
ALTER TABLE characters ADD COLUMN arc_from_to TEXT;
ALTER TABLE characters ADD COLUMN secret_json TEXT;        -- JSON list
