-- migration 067: character_state_snapshots(角色状态时间线快照)
-- SP-4(2026-05-28)— 灵魂续写北极星·基础设施层
--
-- 为什么单建表?
--   - `character_emotional_states`(migration 043)只存"情绪事件"单点,不是状态快照
--   - `action_ledger`(migration 044)存原子动作流水,需要聚合才能算"当前状态"
--   - `canonical_entities.locked_status` 是"实体物理存在"全局态,无时间维
--   → 没有一张表能直接回答"第 5 幕末此角色处于什么状态"
--
-- 用途:
--   1. SP-4 本身:hard_constraints / narrator 可直接拉上幕快照作铁律
--   2. SP-2 角色想要 vs 需要:弧光在时间上展开,挂在此表的状态变化序列上
--   3. SP-3 知识边界:known_fact_ids_json 字段是知识时间线的载体
--   4. 前端时间轴可视化(SP-5 / SP-6 顺手用)
--
-- 数据量:典型 28 幕 × 5 角色 = 140 行 / sim,SQLite 完全可承受
-- 写入:agent_evolution_engine 每幕 narrator 完成后扫 in_scene_agents 各写一行
-- 读取:hard_constraints / 前端时间轴
--
-- 字段设计原则:
--   - JSON 化 多维状态(emotion_vec / inventory / known_facts)— 字段不爆炸
--   - position / scene_name 文本即可
--   - 必填:sim+scene+char 三元组主键;其它全 NULL 可缺(SP-2/3 才填齐)

CREATE TABLE IF NOT EXISTS character_state_snapshots (
    id                  TEXT PRIMARY KEY,
    simulation_id       TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    scene_index         INTEGER NOT NULL,                   -- 0-based 幕号
    character_id        TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    character_name      TEXT NOT NULL,                      -- 冗余 canonical_name 便利前端
    -- 状态字段(都允许 NULL,本 sprint 主要写 emotion / position / hp;
    -- inventory / known_facts 给 SP-2/SP-3 接入)
    position            TEXT,                                -- 文字描述:"驹子在客栈门口"
    emotion_vec_json    TEXT,                                -- 8 维情绪向量 JSON:{"joy":0.3, "sadness":0.7, ...}
    hp_status           TEXT,                                -- alive/deceased/in_facility/absent/injured(全局或局部)
    status_note         TEXT,                                -- 自由备注(伤势 / 状况描述)
    inventory_json      TEXT,                                -- 持有物 JSON 列表:["照片","信件"]
    known_fact_ids_json TEXT,                                -- 已知 fact_id JSON 列表(SP-3 知识边界用)
    created_at          TEXT NOT NULL,
    UNIQUE(simulation_id, scene_index, character_id)
);

-- 时间线主用法:某 sim 某角色按 scene_index 排序
CREATE INDEX IF NOT EXISTS idx_snapshots_sim_char_scene
    ON character_state_snapshots(simulation_id, character_id, scene_index);

-- 单幕主用法:某 sim 第 N 幕所有角色快照
CREATE INDEX IF NOT EXISTS idx_snapshots_sim_scene
    ON character_state_snapshots(simulation_id, scene_index);
