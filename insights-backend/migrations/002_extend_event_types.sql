-- migration 002:删 events.event_type 的 CHECK 约束(2026-06-09)
--
-- 背景:
--   001 把 17 个 event_type 写死在 CHECK 中,新增 event_type 必须重建表。
--   2026-06-09 加剧创态 + 多模型对比 10 个新类型,如果保留 CHECK 每次都要
--   写 migration 重建表 — 工程低效。
--
-- 治理:
--   移除 CHECK 约束 — 信任 Python 层 EVENT_TYPES 白名单(routers/track.py L42)。
--   schema 跟 Python 单一可信源解耦,新增 event_type 只改 model + 前端枚举即可。
--
-- SQLite 限制:不能直接 ALTER 改 CHECK,必须重建表:
--   1. CREATE events_v2(无 CHECK)
--   2. 拷数据(只在 events_v2 为空时拷,保 idempotent)
--   3. DROP events + RENAME events_v2 → events
--   4. 重建 indexes
--
-- 幂等:
--   第一次跑:events 是 v1 → 拷数据完成迁移
--   重复跑:events 已是 v2(无 CHECK)→ events_v2 重建为空 → 拷一次数据
--     → DROP/RENAME → 净状态等价(浪费一次拷贝,无害)

CREATE TABLE IF NOT EXISTS events_v2 (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT NOT NULL,                  -- 移除 CHECK,信 Python 白名单
    user_id         TEXT,
    session_id      TEXT NOT NULL,
    timestamp_ms    INTEGER NOT NULL,
    server_recv_ms  INTEGER NOT NULL,
    project_id      TEXT,
    simulation_id   TEXT,
    mode            TEXT,
    step            TEXT,
    path            TEXT,
    duration_ms     INTEGER,
    meta_json       TEXT NOT NULL DEFAULT '{}'
);

-- 拷数据(events_v2 为空才拷,防重跑数据爆炸)
INSERT INTO events_v2 (id, event_type, user_id, session_id, timestamp_ms, server_recv_ms,
                       project_id, simulation_id, mode, step, path, duration_ms, meta_json)
SELECT id, event_type, user_id, session_id, timestamp_ms, server_recv_ms,
       project_id, simulation_id, mode, step, path, duration_ms, meta_json
FROM events
WHERE (SELECT COUNT(*) FROM events_v2) = 0;

-- 旧表替换为新表
DROP TABLE IF EXISTS events;
ALTER TABLE events_v2 RENAME TO events;

-- 重建 indexes(跟 001 一致)
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_events_user_ts ON events(user_id, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
CREATE INDEX IF NOT EXISTS idx_events_type_ts ON events(event_type, timestamp_ms DESC);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id) WHERE project_id IS NOT NULL;
