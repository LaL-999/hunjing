-- migration 009: simulations
-- 续写引擎产物 — Sprint 1.G simulate.py 服务化。
-- ADR §2:单表 + JSON 列(timeline_json / narrative / characters_snapshot),
-- 不拆 simulation_rounds 子表(初版无按轮过滤需求,周期态再重构)。
-- created 2026-05-10 / Sprint 1.G

CREATE TABLE IF NOT EXISTS simulations (
    id                   TEXT PRIMARY KEY,
    project_id           TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id              TEXT NOT NULL REFERENCES users(id),

    -- 创建时冻结的参数快照(后续改 plan / 项目角色不影响已存推演)
    divergence           TEXT NOT NULL,
    reshape_percent      INTEGER NOT NULL DEFAULT 50          -- 用户面向滑块 10-90
                            CHECK (reshape_percent BETWEEN 10 AND 90),
    rounds_planned       INTEGER NOT NULL DEFAULT 10,         -- 由 reshape_percent 在 service 派生
    target_chars         INTEGER NOT NULL DEFAULT 4000,
    style                TEXT NOT NULL DEFAULT 'auto',        -- 1.Q 起统一为 'auto' / 'custom';
                                                              -- 旧数据兼容:'A' 'C' 视同 'auto'(LLM 自适应)
                                                              -- DB 不加 CHECK — 后端 Pydantic Literal 已强制,留空间方便后续扩展(如 'preset_horror')
    custom_style_hint    TEXT,                                -- style='custom' 时用户写的语体描述;非空时 composer 强制覆盖
    characters_snapshot  TEXT NOT NULL,                       -- JSON

    -- 状态机
    state                TEXT NOT NULL CHECK (state IN
                            ('queued','directing','composing','done','failed','cancelled')),
    current_round        INTEGER NOT NULL DEFAULT 0,

    -- 产物
    timeline_json        TEXT,                                -- JSON {rounds: [...]}
    narrative            TEXT,                                -- Composer 产物 markdown

    -- 滚雪球续写(Sprint 1.O)
    context_simulation_ids  TEXT,    -- JSON array,前文 simulation ids;空 = 独立推演
    narrative_summary       TEXT,    -- ~800 字摘要,首次被引用时由 _generate_summary 生成并缓存

    -- 计费 + 错误
    tokens_input         INTEGER NOT NULL DEFAULT 0,
    tokens_output        INTEGER NOT NULL DEFAULT 0,
    cost_yuan            REAL NOT NULL DEFAULT 0,
    error_message        TEXT,

    created_at           TEXT NOT NULL,
    started_at           TEXT,
    completed_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_sim_project    ON simulations(project_id);
CREATE INDEX IF NOT EXISTS idx_sim_user_state ON simulations(user_id, state);
