-- migration 024:comic_projects 表(D.9 Sprint 1 — 漫画态主表)
--
-- ADR v3 §6 锁定字段;Sprint 0 验证后,L3 ref image 不阻塞 → schema 不变,
-- 但实施时不强求 L3,L2 单层已达 95/100。
--
-- 状态机:queued → scripting / extracting_visuals(并行) → style_uploading →
--        style_analyzing → style_voting → character_anchoring →
--        designing → generating → composing → done | failed | cancelled
--
-- 与 projects 表的关系:**漫画态走独立表,不复用 projects**(隔离铁律,详 ADR §5)
-- 后端 ProjectMode 'cycle' 字面量保留但前端语义指漫画态;漫画态项目不出现在
-- 「我的剧情线」(独立「我的漫画」列表)
--
-- created 2026-05-12 / Sprint D.9 Sprint 1

CREATE TABLE IF NOT EXISTS comic_projects (
    id                              TEXT PRIMARY KEY,
    user_id                         TEXT NOT NULL REFERENCES users(id),
    name                            TEXT NOT NULL,

    -- 输入源:JSON {"type": "internal" | "external",
    --              "simulation_ids": [...]  -- type=internal 时
    --              "upload_ids": [...]      -- type=external 时}
    source_json                     TEXT NOT NULL,

    -- ============== 画风定调员 v2 产物(Agent #3 v2)==============
    -- 简短中文 tag,展示用(如"国漫工笔半厚涂" / "日系赛璐璐")
    style_tag                       TEXT,
    -- 用户选定那张样张(整本视觉锚点)
    style_anchor_image_url          TEXT,
    -- 5 张候选样张全量(JSON:[{tag, image_url, llm_reasoning}, ...])审计用
    style_candidates_json           TEXT,
    -- v3 新:用户上传的 3 张参考图 URL(JSON 数组)
    style_reference_image_urls_json TEXT,
    -- v3 新:Qwen-VL 提取的视觉 DNA JSON(笔触/上色/光影/比例/线稿/构图/色调)
    style_visual_dna_json           TEXT,
    -- v3 新:DeepSeek 综合后的 200-400 字详细 prompt(整本每格 prompt 头部都贴)
    style_detailed_prompt           TEXT,

    -- ============== 角色一致性 L4 ==============
    -- 项目创建时随机生成,整本所有格调 Seedream 时复用,降低 vendor 随机性漂移
    generation_seed                 INTEGER,

    -- ============== 状态机 ==============
    state                           TEXT NOT NULL,
    -- 当前 phase 进度(% 0-100,前端进度条用,SSE 推送)
    progress_percent                INTEGER NOT NULL DEFAULT 0,

    -- ============== 成本 / 错误 ==============
    cost_yuan                       REAL NOT NULL DEFAULT 0,
    error_message                   TEXT,

    -- ============== 时间戳 ==============
    created_at                      TEXT NOT NULL,
    updated_at                      TEXT NOT NULL,
    completed_at                    TEXT
);

CREATE INDEX IF NOT EXISTS idx_comic_projects_user ON comic_projects(user_id);
CREATE INDEX IF NOT EXISTS idx_comic_projects_state ON comic_projects(state);

-- state 合法值由 service 层 + Pydantic 白名单兜底(SQLite ALTER 不支持 CHECK):
--   queued / scripting / extracting_visuals / style_uploading / style_analyzing /
--   style_voting / character_anchoring / designing / generating / composing /
--   done / failed / cancelled
