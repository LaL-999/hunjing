-- migration 038: 灵魂续写模式 — simulations.mode + 2 张新表(Sprint 6.A2 M3.A,2026-05-18)
--
-- 用户拍板铁律(2026-05-18):
--   "以最高标准开发项目,不要遇难则退,不管多复杂都要做"
--   → M3 走 B 路径(真多 agent 独立 LLM 进程 + 私有记忆 + reflection + RAG)
--
-- 模式开关(对齐用户拍板"模式开关 B"):
--   每次推演让用户选 quick(快速 ¥0.78)or evolution(灵魂 ¥4-8)
--   - quick     原 simulation_service 主路径(向后兼容,老 sim 默认走此)
--   - evolution agent_evolution_engine 新路径(M3.B 落地)
--
-- 新表 agent_private_memories:每 agent 每幕的"亲历记忆"片段
--   - 用途:agent reflection 时只看自己亲历过的 memory(不是上帝视角共享 history)
--   - 信息不对称:黛玉不知道宝钗背后说她什么(若黛玉没在场)
--
-- 新表 simulation_scenes:每幕的元数据
--   - 用途:跨幕 reflection 时按 scene_index 查"我去过哪儿 / 谁在场"
--   - 也给前端 SSE 展示用("📍 场景 X → 在场 A, B")
--
-- created 2026-05-18 / Sprint 6.A2 M3.A

-- ========== 1. simulations 加 mode 字段 ==========
ALTER TABLE simulations ADD COLUMN mode TEXT NOT NULL DEFAULT 'quick';
-- 注:SQLite ALTER ADD COLUMN 不支持 CHECK 约束(只在 CREATE TABLE 支持)
-- 业务层 schemas/simulation.py 用 Literal['quick','evolution'] Pydantic 校验


-- ========== 2. agent_private_memories ==========
-- 每行 = 某 agent 在某幕产出 / 见证的一条记忆片段
CREATE TABLE IF NOT EXISTS agent_private_memories (
    id              TEXT PRIMARY KEY,
    simulation_id   TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    character_id    TEXT NOT NULL,    -- 不加 FK 外键(允许 character_id='script:hash' 之类的 inferred 角色)

    -- 该记忆属于第几幕(对齐 simulation_scenes.scene_index)
    scene_index     INTEGER NOT NULL,

    -- 记忆类型:
    --   reflection  — agent 心理活动(看到场景 + 上一轮 → 内心独白)
    --   dialogue    — agent 自己说出的对白
    --   action      — agent 自己做出的行动
    --   witnessed   — agent 在场目击的他人对白 / 行动
    memory_type     TEXT NOT NULL CHECK (
        memory_type IN ('reflection', 'dialogue', 'action', 'witnessed')
    ),

    -- 记忆内容(50-300 字 LLM 输出片段)
    content         TEXT NOT NULL,

    -- 该记忆涉及的其他角色(JSON 数组,如 ["char_id_紫鹃"]):
    -- witnessed 时常用,reflection 时可空
    other_chars_json  TEXT NOT NULL DEFAULT '[]',

    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_memories_sim_char
    ON agent_private_memories(simulation_id, character_id, scene_index);


-- ========== 3. simulation_scenes ==========
-- 每行 = 灵魂续写中的一幕场景元数据
CREATE TABLE IF NOT EXISTS simulation_scenes (
    id              TEXT PRIMARY KEY,
    simulation_id   TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,

    -- 第几幕(0-based,严格递增)
    scene_index     INTEGER NOT NULL,

    -- 场景名(可能来自 project_scenes 或 LLM 新造)
    scene_name      TEXT NOT NULL,
    -- 场景来源标记:
    --   project_scenes_pick — 从 M2 project_scenes 选的原作场所
    --   llm_created        — LLM 根据剧情合理性新造的场景
    scene_source    TEXT NOT NULL CHECK (
        scene_source IN ('project_scenes_pick', 'llm_created')
    ),

    -- 时间锚点(LLM 给的"第 81 章夜深" / "T+1 小时"等自由文本)
    time_anchor     TEXT NOT NULL DEFAULT '',

    -- 在场角色 id 列表(JSON 数组)— summoner 决定
    characters_present_json  TEXT NOT NULL DEFAULT '[]',

    -- 该幕产出的小说段落(narrator 合稿后写入;失败留空)
    narrative_segment    TEXT NOT NULL DEFAULT '',

    created_at      TEXT NOT NULL,

    -- 同推演内 scene_index 唯一
    UNIQUE (simulation_id, scene_index)
);

CREATE INDEX IF NOT EXISTS idx_sim_scenes_sim
    ON simulation_scenes(simulation_id, scene_index);
