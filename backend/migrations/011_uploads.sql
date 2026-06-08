-- migration 011: uploads + red_flag_dictionary + violation_logs
-- C 阶段中间态入口(Sprint 2.A):.txt / .epub / .docx 文件上传 + 红旗词拦截。
-- 接 file_parser_service + red_flag_filter_service + upload_service。
--
-- 设计原则(ADR-2.A):
--   * 文件存储路径在 uploads.storage_path,绝对路径由 settings.uploads_dir 拼出
--   * sha256 同用户 UNIQUE → 重复内容上传第二次返 409 + 旧 upload_id
--   * red_flag_dictionary 可热更(severity/enabled 软控制),seed 由 init_db 灌
--   * violation_logs 是法务审计 trail,不删(用户删项目也不级联)
--   * 上限 100MB(决策 1.0,Python 业务层校验,DB 不约束)
-- created 2026-05-11 / Sprint 2.A

CREATE TABLE IF NOT EXISTS uploads (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id             TEXT NOT NULL REFERENCES users(id),

    filename            TEXT NOT NULL,                 -- 原文件名(用户端展示用,不存绝对路径防泄漏)
    storage_path        TEXT NOT NULL,                 -- 服务器存储相对路径:uploads/{user_id}/{upload_id}.{ext}
    mime_type           TEXT NOT NULL,
    size_bytes          INTEGER NOT NULL,
    sha256              TEXT NOT NULL,                 -- 同用户重复上传去重

    parsed_text_chars   INTEGER,                       -- 解析后纯文本字符数;NULL = 还没解析

    state               TEXT NOT NULL CHECK (state IN
                            ('uploaded','parsed','rejected','extracting','ready','failed')),
    error_message       TEXT,                          -- rejected/failed 时填(向用户透明)
    uploaded_at         TEXT NOT NULL,                 -- ISO 8601

    UNIQUE (user_id, sha256)                            -- 同用户同内容拒第 2 次
);
CREATE INDEX IF NOT EXISTS idx_uploads_project ON uploads(project_id);


CREATE TABLE IF NOT EXISTS red_flag_dictionary (
    id                  TEXT PRIMARY KEY,
    category            TEXT NOT NULL CHECK (category IN
                            ('political','sexual','violence','privacy')),
    pattern             TEXT NOT NULL,
    is_regex            INTEGER NOT NULL DEFAULT 0,    -- 1 = 正则;0 = substring(默认快 + 安全)
    severity            TEXT NOT NULL DEFAULT 'block'
                            CHECK (severity IN ('block','warn')),
    enabled             INTEGER NOT NULL DEFAULT 1,
    note                TEXT,                          -- 解释为什么入选,运营调整时可读
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_red_flag_active
    ON red_flag_dictionary(enabled, category);


CREATE TABLE IF NOT EXISTS violation_logs (
    id                  TEXT PRIMARY KEY,
    user_id             TEXT NOT NULL REFERENCES users(id),
    upload_id           TEXT,                          -- 可空(被拒上传可能尚未建 uploads 行)
    ip_address          TEXT,                          -- 法务审计追溯(get_client_ip 拿)
    flag_id             TEXT NOT NULL REFERENCES red_flag_dictionary(id),
    matched_text        TEXT NOT NULL,                 -- 命中片段,< 80 字脱敏存
    occurred_at         TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_violations_user
    ON violation_logs(user_id, occurred_at DESC);
