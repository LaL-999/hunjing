-- migration 027:scenes + props 表(D.9 Sprint 1 — 素材库 Agent #5 产物之二、三)
--
-- ADR v3 §3.2 Agent #5 素材库抽取员的第 2 + 3 类产物。
--
-- 用途:导演 Agent #6 每格 prompt 头部组装时拉这两表 →
--       [style_detailed_prompt] + [角色 descriptor] + [scenes.xxx] + [props.xxx] + [镜头语言]
--
-- 与 character_visuals.signature_props_json 的区别:
--   - character_visuals.signature_props_json:角色身上的标志物(玉佩、随身配剑)
--   - props 表:剧情中出现的所有道具(包括无主道具,如桌上的茶杯)
--
-- AI 对焦机制扩展(ADR v3 §4.6):用户可对 scenes / props 的任一字段做对焦
-- (赞同/反对/补充/调整),走与 character_focus 类似的流程。
--
-- created 2026-05-12 / Sprint D.9 Sprint 1

-- ==================== scenes 表 ====================
CREATE TABLE IF NOT EXISTS scenes (
    id                  TEXT PRIMARY KEY,
    comic_id            TEXT NOT NULL REFERENCES comic_projects(id) ON DELETE CASCADE,

    name                TEXT NOT NULL,                  -- "林家花园" / "客栈" / "皇宫大殿"
    location_type       TEXT,                            -- "室内" / "室外" / "山区" / "河边"
    era                 TEXT,                            -- "古代" / "现代" / "未来" / "未指明"
    architecture_style  TEXT,                            -- "中式园林" / "江南水乡" / "西式哥特"
    lighting            TEXT,                            -- "白天" / "夜晚" / "黄昏" / "逆光"
    season              TEXT,                            -- "春" / "夏" / "秋" / "冬" / "未指明"

    -- 关键陈设(JSON 数组,3-10 个原文提到的家具/景物中文短词)
    key_props_json      TEXT NOT NULL DEFAULT '[]',

    -- 用户对焦修改次数(审计)
    focused_count       INTEGER NOT NULL DEFAULT 0,

    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scenes_comic ON scenes(comic_id);
CREATE INDEX IF NOT EXISTS idx_scenes_name ON scenes(comic_id, name);

-- ==================== props 表 ====================
CREATE TABLE IF NOT EXISTS props (
    id                  TEXT PRIMARY KEY,
    comic_id            TEXT NOT NULL REFERENCES comic_projects(id) ON DELETE CASCADE,

    name                TEXT NOT NULL,                  -- "青铜剑" / "玉佩" / "信物" / "帕子"
    prop_type           TEXT,                            -- 武器 / 服饰 / 书籍 / 家具 / 信物 /
                                                          --   玉器 / 文房 / 其他
    -- 拥有者(关联到本 comic 的 character_id,可空表示无主)
    owner_character_id  TEXT,
    -- 用于绘图的视觉描述(3-8 个中文词)
    visual_description  TEXT,
    -- 故事意义(可空;是不是关键情节道具)
    story_significance  TEXT,

    focused_count       INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_props_comic ON props(comic_id);
CREATE INDEX IF NOT EXISTS idx_props_owner ON props(comic_id, owner_character_id);
