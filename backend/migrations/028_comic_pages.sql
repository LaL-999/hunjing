-- migration 028:comic_pages 表(D.9 Sprint 1 — 漫画产物按页持久化)
--
-- 30 页 × 6 格 = 180 格典型漫画。按页存,每页一行 record。
-- 每行 panels_json 含本页 6 格的 {panel_index, image_url, dialogues,
-- narrator, sfx, ...} 数组。
--
-- composed_url:Agent #10 排版嵌字后整页 PNG(对话气泡 + 拟声词 + 字体已贴)。
-- 用户预览时显 composed_url;导出 PDF 也用 composed_url。
--
-- created 2026-05-12 / Sprint D.9 Sprint 1

CREATE TABLE IF NOT EXISTS comic_pages (
    id                  TEXT PRIMARY KEY,
    comic_id            TEXT NOT NULL REFERENCES comic_projects(id) ON DELETE CASCADE,
    page_index          INTEGER NOT NULL,    -- 1-based,1, 2, 3, ...

    -- 单页 panels JSON:[
    --   {
    --     "panel_index": 1,  // 1-based,本页内
    --     "image_url": "https://...",
    --     "prompt_used": "..."  // 导演 agent 生成的完整 prompt(审计/重生成用)
    --     "dialogues": [{speaker, text}, ...],
    --     "narrator": "...",
    --     "sfx": ["…", "！"],
    --     "regenerated_count": 0  // 用户点"重画此格"次数
    --   },
    --   ...
    -- ]
    panels_json         TEXT NOT NULL,

    -- 排版后整页 PNG(对话气泡 + 字体已贴,Agent #10 PIL 本地处理产物)
    composed_url        TEXT,

    -- 状态:queued / generating / composed / failed
    state               TEXT NOT NULL,

    -- 重生成次数(用户点"重画此页"累计;不扣配额仅审计)
    regenerated_count   INTEGER NOT NULL DEFAULT 0,

    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comic_pages_comic ON comic_pages(comic_id, page_index);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_comic_pages_comic_index
    ON comic_pages(comic_id, page_index);
