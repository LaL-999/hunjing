-- insights-backend 初始 schema(2026-05-27)
-- 设计:events 表记录所有埋点;daily_reports / summary_reports 占位给后期 A5/A6
--
-- 字段考虑:
--   - user_id 是字符串(主平台用 UUID)— 不加 FK,因为 huimeng.users 是 attach 来的只读表
--   - session_id 由前端生成(浏览器一次访问一个),用于推算"session 时长"
--   - event_type 用 TEXT 不用 ENUM(SQLite 没 enum;CHECK 约束保证可控值)
--   - meta_json 兜底任何 event-specific 附加数据
--   - timestamp_ms 用毫秒整数(Unix epoch ms),前端 Date.now() 直发,精度足够

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL CHECK (event_type IN (
        -- 页面级
        'page_view',
        'page_leave',
        'page_refresh',
        -- AI 调用级
        'ai_call_start',
        'ai_call_done',
        'ai_call_failed',
        -- 创作态切换
        'mode_switch',
        -- 业务关键操作
        'project_create',
        'project_delete',
        'simulation_create',
        'simulation_done',
        'simulation_failed',
        'audit_run',
        'canonical_audit_run',
        -- 异常 / 会话
        'error',
        'session_start',
        'session_end'
    )),
    user_id         TEXT,                       -- 未登录用户 NULL(匿名访问)
    session_id      TEXT NOT NULL,              -- 浏览器一次访问一个
    timestamp_ms    INTEGER NOT NULL,           -- 客户端时间(Unix ms)
    server_recv_ms  INTEGER NOT NULL,           -- 服务端收到时间(防客户端伪造)
    project_id      TEXT,                       -- 涉及项目则填
    simulation_id   TEXT,                       -- 涉及推演则填
    mode            TEXT,                       -- initial / middle / tail / comic
    step            TEXT,                       -- character_focus / counterfactual / outline_review / ...
    path            TEXT,                       -- 浏览器路径 /projects/:id
    duration_ms     INTEGER,                    -- page_leave / ai_call_done 用
    meta_json       TEXT NOT NULL DEFAULT '{}'  -- 任意 event-specific 附加数据
);

-- 查询模式:按时间反序拉 + 按 user / session / event_type 过滤
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_events_user_ts ON events(user_id, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(event_type, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id) WHERE project_id IS NOT NULL;


-- ============================================================
-- daily_reports:Level 1 数据 agent 产物(2026-05-27 后期 A5)
-- 字段先占位,A5 实施时填具体逻辑
-- ============================================================
CREATE TABLE IF NOT EXISTS daily_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date     TEXT NOT NULL UNIQUE,       -- YYYY-MM-DD
    event_count     INTEGER NOT NULL,           -- 当日事件总数(< 50 时 agent 跳过)
    skipped         INTEGER NOT NULL DEFAULT 0, -- 是否跳过了 agent(0/1)
    skip_reason     TEXT,                       -- "事件不足" 等
    summary_md      TEXT,                       -- agent 产出的 markdown 报告
    metrics_json    TEXT,                       -- 结构化指标(供 Level 2 聚合用)
    tokens_input    INTEGER NOT NULL DEFAULT 0,
    tokens_output   INTEGER NOT NULL DEFAULT 0,
    cost_yuan       REAL NOT NULL DEFAULT 0.0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_daily_reports_date ON daily_reports(report_date DESC);


-- ============================================================
-- summary_reports:Level 2 汇总 agent 产物(2026-05-27 后期 A6)
-- ============================================================
CREATE TABLE IF NOT EXISTS summary_reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_number    INTEGER NOT NULL UNIQUE,    -- 第 N 阶段(1-base)
    period_start    TEXT NOT NULL,              -- YYYY-MM-DD
    period_end      TEXT NOT NULL,              -- YYYY-MM-DD
    daily_count     INTEGER NOT NULL,           -- 本阶段聚合了多少份 daily_report
    summary_md      TEXT,                       -- agent 产出的"第 N 阶段汇总报告"
    insights_json   TEXT,                       -- 结构化洞察(趋势 / 异常 / 模式)
    tokens_input    INTEGER NOT NULL DEFAULT 0,
    tokens_output   INTEGER NOT NULL DEFAULT 0,
    cost_yuan       REAL NOT NULL DEFAULT 0.0,
    created_at      TEXT NOT NULL,
    read_at         TEXT                        -- 开发者已读时间(标记后不再推醒)
);

CREATE INDEX IF NOT EXISTS idx_summary_reports_stage ON summary_reports(stage_number DESC);
