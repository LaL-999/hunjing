-- migration 033: comic_batches(漫画态分批承接 — 借鉴 1.O 滚雪球机制)
-- Sprint D.9 C.4(2026-05-13)— ADR_credit_quota_重构.md §4
--
-- 解决问题:
--   单次 LLM 调用 max_output_tokens 上限 ≈ 8K,限制单次最多 12 页漫画(Sprint 2.B+ 七修)。
--   用户 5 万字文本想生成 500 张图漫画 → 必须分多批承接。
--
-- 设计:
--   - 全本锚定一次:画风 anchor + 角色立绘卡 + 视觉素材库(在 comic_projects 表 + 子表)
--   - 每批 = 一段文本区间 → 一段 page 范围 → 一组格图
--   - 第 N+1 批承接第 N 批的"末页 panels 摘要"作为编剧上下文(防剧情断裂)
--   - 批次内独立失败 / 重跑(对齐 1.P 断点续推:_RUNNING_COMICS 注册表已就位)
--   - credit 按批结算:每批跑完 INSERT credit_transactions(action='comic_batch')
--   - 中途取消 → 已跑批次扣 credit + 未跑批次不扣
--
-- AI Planner(Sprint C.4)输出建议:
--   recommended_total_pages / recommended_batches / batch_size_pages → 用户拍板 → INSERT batches
--
-- 状态机:queued → running → done / failed
--   - queued:Planner 生成,等启动
--   - running:Orchestrator 调度中(单时刻全 comic 内最多 1 个 running 批)
--   - done:本批完成,可启动下一批
--   - failed:本批失败,可手动重跑(状态机 reset 到 queued)
--
-- 拼接:全部批次 done → comic_pages 表已写入完整 panel,前端阅读器按 page_index 顺序读

CREATE TABLE IF NOT EXISTS comic_batches (
    id              TEXT PRIMARY KEY,
    comic_id        TEXT NOT NULL REFERENCES comic_projects(id) ON DELETE CASCADE,

    -- 顺序索引(1-indexed)
    batch_index     INTEGER NOT NULL,

    -- 文本游标(本批处理原文的哪段)
    source_cursor_start  INTEGER NOT NULL,   -- 起始字符位置(包含)
    source_cursor_end    INTEGER NOT NULL,   -- 结束位置(不包含)

    -- 页码范围(本批生成的 page_index 区间)
    pages_range_start    INTEGER NOT NULL,   -- 1-indexed
    pages_range_end      INTEGER NOT NULL,   -- 1-indexed 包含

    -- 承接上下文(给下一批的编剧用)
    -- 本批末页 panels 简要摘要(JSON),供下一批编剧"承接前文"
    -- 典型字段:last_page_index / last_scene / on_stage_characters / unresolved_threads
    tail_context_json    TEXT,

    -- 状态机
    state           TEXT NOT NULL
                      CHECK (state IN ('queued', 'running', 'done', 'failed')),

    -- credit 消耗(本批跑掉多少)
    cost_credits    INTEGER NOT NULL DEFAULT 0,

    -- LLM 实际成本(¥,运营审计)
    cost_yuan       REAL NOT NULL DEFAULT 0,

    -- 失败时的错误信息(LLM JSON parse / Seedream 限流 / Qwen-VL 超时等)
    error_message   TEXT,

    -- 时刻轴
    created_at      TEXT NOT NULL,
    started_at      TEXT,                    -- running 时设
    completed_at    TEXT,                    -- done / failed 时设

    UNIQUE(comic_id, batch_index)
);

-- 查询入口:某漫画的批次顺序
CREATE INDEX IF NOT EXISTS idx_comic_batches_comic_order
    ON comic_batches(comic_id, batch_index);

-- Orchestrator 入口:扫待启动的批次
CREATE INDEX IF NOT EXISTS idx_comic_batches_state
    ON comic_batches(state, created_at);
