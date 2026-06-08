-- migration 060: simulations 加 with_grand_finale + narrative_pacing(P2.A + P2.B)
--
-- 用户痛点:
--   P2.A:AI 续写时倾向于"在最后几幕安排大结局"(主角告别过去/接受未来/做出人生重大决定),
--         用户想超长篇续作时,第一篇就被"完结",后续无法继续。
--   P2.B:续作节奏太平稳 — 总几个角色在几个场景转悠,缺乏波折。希望用户能掌控节奏档。
--
-- 字段设计:
--   with_grand_finale BOOLEAN
--     0 = 默认(不锁结局)— scene_picker / narrator prompt 注入"禁止大结局动作"铁律
--     1 = 鼓励大结局   — 鼓励主线收束 / 情绪闭环 / 角色弧光完成
--
--   narrative_pacing TEXT
--     'slow'     允许多幕同场景 + 鼓励氛围铺陈(适合村上 / 川端类)
--     'standard' 默认 — 当前行为
--     'fast'     紧凑模式:每 2-3 幕必须切场景 + 必须有外部刺激(适合三体类)
--
-- 普适性:任何作品 / 任何作者都能用 — 与具体文风无关
--
-- created 2026-05-24 / P2.A + P2.B

ALTER TABLE simulations ADD COLUMN with_grand_finale INTEGER NOT NULL DEFAULT 0;
ALTER TABLE simulations ADD COLUMN narrative_pacing TEXT NOT NULL DEFAULT 'standard';
