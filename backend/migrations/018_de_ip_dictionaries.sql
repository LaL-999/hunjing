-- migration 018: de_ip_dictionaries — Sprint 2.E 去 IP 化导出
--
-- 战略锚(doc 5 四层版权防护核心层):
--   用户可选导出原版 / 去 IP 版;去 IP 走字典替换 + 法务证据 trail(violation_logs)
--   同人圈商用必经 — 平台不强制"用户可选"是关键合规姿态
--
-- 设计:per project 0 或 1 个字典(PRIMARY KEY 是 project_id,重新生成 INSERT OR REPLACE)
-- 字典内容:仅人名 / 地名 / 物名(关系类型 / 描述里的非专名不替换)
-- 字典生成:用户主动触发(POST /api/projects/{id}/de_ip_dictionary)— 不预生成省 LLM 钱
-- 配额:不扣(对齐 1.R 自洽守护者 + 2.D 正典守护者;鼓励反 IP 是平台主推)
-- created 2026-05-11 / Sprint 2.E

CREATE TABLE IF NOT EXISTS de_ip_dictionaries (
    project_id    TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    user_id       TEXT NOT NULL REFERENCES users(id),

    -- JSON dict {"原名": "替换名", ...};专有名词(人名/地名/物名),不含泛词
    mapping_json  TEXT NOT NULL,

    -- LLM 给的字典设计说明(例:"整体保持清代古典章回审美;'贾'雅化为'仁'")
    notes         TEXT,

    tokens_input  INTEGER NOT NULL DEFAULT 0,
    tokens_output INTEGER NOT NULL DEFAULT 0,
    cost_yuan     REAL NOT NULL DEFAULT 0,

    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL   -- 重新生成时刷新
);

CREATE INDEX IF NOT EXISTS idx_de_ip_dictionaries_user
    ON de_ip_dictionaries(user_id);
