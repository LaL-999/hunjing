-- migration 026:character_visuals 表(D.9 Sprint 1 — 素材库 Agent #5 产物之一)
--
-- ADR v3 §3.2 Agent #5 素材库抽取员的第 1 类产物 — 角色视觉细节。
-- 1-to-1 关联 comic_projects 内的 character(用户上传的 simulation 拷贝过来的
-- character snapshot,或外部文本时编剧 agent 推断的角色 id)。
--
-- 字段策略(ADR v3 §3.2 铁律):
--   - 字段未明 → 填 "(原文未明)",**绝不凭空创造**
--   - 用户走 character_focus 流程补字段(扩展自 C 阶段对焦机制,见 ADR v3 §4.6)
--
-- 与 character_cards.descriptor 的关系:
--   Agent #5 抽到的 7 类视觉细节 → Agent #4 读这表生成 20-30 句 descriptor
--
-- created 2026-05-12 / Sprint D.9 Sprint 1

CREATE TABLE IF NOT EXISTS character_visuals (
    id                  TEXT PRIMARY KEY,
    comic_id            TEXT NOT NULL REFERENCES comic_projects(id) ON DELETE CASCADE,
    character_id        TEXT NOT NULL,    -- 关联 character(同 comic_id 内 unique)

    -- 面部(JSON)— {eye_shape, eye_color, eyebrow, nose, mouth, face_shape, skin_tone, marks}
    --   全 8 字段,未明填 "(原文未明)"
    face_json           TEXT NOT NULL DEFAULT '{}',

    -- 发型(JSON)— {length, color, texture, hairstyle, bangs}
    hair_json           TEXT NOT NULL DEFAULT '{}',

    -- 体态(JSON)— {height_range, body_type, posture, signature_action}
    body_json           TEXT NOT NULL DEFAULT '{}',

    -- 常服(JSON)— {garment, color, style}
    outfit_json         TEXT NOT NULL DEFAULT '{}',

    -- 标志配饰(JSON 数组)— [{name, position, color}, ...]
    accessories_json    TEXT NOT NULL DEFAULT '[]',

    -- 标志道具(JSON 数组)— [{name, description}, ...]
    --   注:与 props 表的区别 — 这是"角色身上的标志性物件",props 是"剧情中的道具"
    signature_props_json TEXT NOT NULL DEFAULT '[]',

    -- 灵魂特质(1-2 句中文)
    soul_traits         TEXT,

    -- 用户对焦修改次数(走 character_focus 时累计;不扣配额,审计用)
    focused_count       INTEGER NOT NULL DEFAULT 0,

    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_character_visuals_comic ON character_visuals(comic_id);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_character_visuals_comic_char
    ON character_visuals(comic_id, character_id);
