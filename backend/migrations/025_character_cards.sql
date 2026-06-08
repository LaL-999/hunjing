-- migration 025:character_cards 表(D.9 Sprint 1 — 角色立绘卡 / 角色一致性 L1)
--
-- ADR v3 §3.2 Agent #4 角色锚定员产物;ADR v3 §4.2 L1 一致性的持久化载体。
--
-- v3 vs v2:descriptor 字段从"8-12 句"扩到 **20-30 句中文**,
--          Agent #4 读 character_visuals(migration 026)填面部/体态/穿搭。
--
-- 一经生成,descriptor 不可改(改 = 换角色,前后页就不一致);
-- card_image_url 可重生成(用户审核时点"重画此立绘"),记 regenerated_count 审计。
--
-- created 2026-05-12 / Sprint D.9 Sprint 1

CREATE TABLE IF NOT EXISTS character_cards (
    id                  TEXT PRIMARY KEY,
    comic_id            TEXT NOT NULL REFERENCES comic_projects(id) ON DELETE CASCADE,

    -- 角色 id 来源:漫画态导入 simulation 时拷贝过来的 character snapshot id;
    -- 或外部文本时由编剧 agent 在本作品内推断的角色 id(本作品内 unique)
    character_id        TEXT NOT NULL,
    character_name      TEXT NOT NULL,

    -- LLM 生成的"身份证级超详细描述符"
    -- v3:20-30 句中文,结构:
    --   [年龄段 + 性别 + 整体气质]
    --   [面部:眼/瞳/眉/鼻/嘴/脸型/肤色/特殊标记]
    --   [发型:长度/颜色/发质/常梳法/刘海]
    --   [体态:身高范围/体型/姿态/标志动作]
    --   [穿搭:常服/标志配饰/常用色调/标志道具]
    --   [性格关键词:1-2 句灵魂特质]
    -- 由 Agent #4 角色锚定员读 character_visuals 表生成,一经生成不可改
    descriptor          TEXT NOT NULL,

    -- 立绘卡 URL(Seedream 用 style_detailed_prompt + descriptor 生成的正面立绘)
    card_image_url      TEXT NOT NULL,

    -- 用户重生成次数(每次 ¥0.20,作为 quota 审计字段)
    regenerated_count   INTEGER NOT NULL DEFAULT 0,

    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_character_cards_comic ON character_cards(comic_id);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_character_cards_comic_char
    ON character_cards(comic_id, character_id);
