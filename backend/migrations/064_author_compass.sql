-- migration 064: 新表 author_compass(1:1 project)— P3 作者指南针 Agent
--
-- 起源:让续作真正"像川端 / 像村上",从抽象"风格 = literary"升级到作者级元信息。
--
-- 双轨制(LLM-first):
--   外部研究轨:LLM 用世界知识吐作家结构化画像(流派/主题/风格标签/雷区)
--   内部反推轨:LLM 读原作文本分布采样(~6000 字),反推"虚拟作者"
--                量化参数(句长/对白率/感官比例/段落节奏/意象 top10/视角/基调)
--
-- 用户介入:可逐字段改 + 整体锁定;锁定后续作生成读 final_compass_json
--
-- 注入点:
--   evolution mode → hard_constraints.py section 6
--   quick mode     → simulation_service.py composer prompt prepend
--   (复用 P0X.2 architecture — 与 physical_constraints_util 同构)
--
-- 普适性:任何作品都需要 — 作家画像与作品类型 / 节奏档位正交
--
-- created 2026-05-26 / P3 D1

CREATE TABLE IF NOT EXISTS author_compass (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL UNIQUE,            -- 1:1 与 project

    -- 用户输入(可空,LLM 也能从原作猜)
    author_name TEXT,                            -- "川端康成"
    work_title TEXT,                             -- "雪国"

    -- 外部研究轨(LLM 用世界知识)
    external_profile_json TEXT,                  -- JSON: 流派/年代/主题/风格标签/雷区
    external_status TEXT NOT NULL DEFAULT 'pending',   -- pending/running/done/failed
    external_error TEXT,                         -- 失败时的错误信息(给用户看)
    external_at TEXT,                            -- 完成时间

    -- 内部反推轨(LLM 读原作文本)
    internal_metrics_json TEXT,                  -- JSON: 句长/对白率/感官/段落/意象/视角/基调
    internal_status TEXT NOT NULL DEFAULT 'pending',
    internal_error TEXT,
    internal_at TEXT,

    -- 用户审阅后的最终版本(可改+锁;锁定后续作读这版)
    final_compass_json TEXT,                     -- 用户合并 + 修改后的完整指南针
    user_locked INTEGER NOT NULL DEFAULT 0,      -- 0=未锁(读 external+internal 合并); 1=锁定(读 final)

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_author_compass_project ON author_compass(project_id);
