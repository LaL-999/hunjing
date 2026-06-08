-- migration 048: 长篇心智(滚雪球深化)数据层
-- Sprint 6.A2 M9.A(2026-05-20)
--
-- 起源:用户提"滚雪球深化(长篇心智)" — 多代续作时 LLM 应该:
--   - 看完整继承链(目前 simulation_service 只注入直接前作 narrative_summary)
--   - 记得跨代角色弧光(第 1 代角色变豁达 → 第 2 代延续豁达,不回头)
--   - 追踪伏笔(原作埋的坑 → 续作 1 引用 → 续作 2 收尾,链可追)
--   - 累积世界观规则(续作 1 LLM 新创"次元裂缝"规则 → 续作 2 必须遵守)
--
-- 三张新表:
--   character_arcs       — 每代续作的"角色心境片段"(narrator 完成后 LLM 抽取)
--   foreshadow_ledger    — 跨代伏笔库(open / resolved 状态机)
--   world_rules_ledger   — 跨代世界观新增规则(LLM 创立的设定,后代遵守)
--
-- 与现有机制的关系:
--   - canonical_entities(M5.1)是 sim 内的实体身份注册(本代用)
--   - 本三表是 project 级别的跨代累积(后代续作消费)
--   - world_facts(M4.1)是 sim 内的强约束(本代生效);world_rules_ledger 是
--     project 级"由 LLM 自创的规则"(跨代生效;原作没明写但累积成新设定)
--
-- created 2026-05-20

-- ============================================================
-- 1. character_arcs — 跨代角色弧光片段
-- ============================================================
-- 每幕 narrator 完成后,arc_extractor LLM 抽取该幕"角色心境片段":
--   - 哪些角色被本幕推动?(in_scene_character_ids JSON)
--   - 心境关键词(如"从内向变豁达 / 从信任转怀疑")
--   - 关键事件 / 触发点(简短描述)
-- 后代续作创建时,chain_context_builder 拉本 project 所有 character_arcs
-- (按时间排序),让 LLM 看到"这个角色一路走来的演化轨迹"

CREATE TABLE IF NOT EXISTS character_arcs (
    id                      TEXT PRIMARY KEY,
    project_id              TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    -- 该弧光片段在哪代 sim / 哪一幕抽出
    simulation_id           TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    scene_index             INTEGER NOT NULL,
    -- 关联角色(JSON list of character_id) — 一幕可能影响多个角色
    character_ids_json      TEXT NOT NULL DEFAULT '[]',
    -- 角色心境关键词(LLM 抽,如"从冷漠到挣扎" / "信任开始崩塌" / "新发现自我")
    arc_keyword             TEXT NOT NULL,
    -- 触发点 / 关键事件(简短描述 < 100 字)
    trigger_summary         TEXT NOT NULL DEFAULT '',
    -- 心境演化方向 — gradual(渐变)/ sudden(突变)/ revelation(顿悟)
    arc_kind                TEXT NOT NULL DEFAULT 'gradual' CHECK (
        arc_kind IN ('gradual', 'sudden', 'revelation', 'regression')
    ),
    created_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_character_arcs_project
    ON character_arcs(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_character_arcs_sim
    ON character_arcs(simulation_id, scene_index);


-- ============================================================
-- 2. foreshadow_ledger — 跨代伏笔库
-- ============================================================
-- LLM 在 narrator 后抽取本幕"埋坑 / 解坑":
--   - 埋:新增一条 status='open' 的 row(谁埋的、内容是什么、何时埋)
--   - 解:UPDATE 已有 row 的 status='resolved' + resolved_in_*
-- 后代续作 chain_context 包含所有 open 伏笔 → LLM 看到"还有 N 个未解的坑"
-- → scene_picker / outline 会主动选场景推进 open 坑(治"剧情死循环")

CREATE TABLE IF NOT EXISTS foreshadow_ledger (
    id                          TEXT PRIMARY KEY,
    project_id                  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    -- 伏笔内容(LLM 给的 < 100 字描述,如"红裙照片背面的'对不起'是谁写的")
    content                     TEXT NOT NULL,
    -- 何时引入(代 + 幕)
    introduced_in_simulation_id TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    introduced_scene_index      INTEGER NOT NULL,
    -- 状态 + 解坑信息
    status                      TEXT NOT NULL DEFAULT 'open' CHECK (
        status IN ('open', 'resolved', 'abandoned')
    ),
    -- 解坑(可空)
    resolved_in_simulation_id   TEXT REFERENCES simulations(id) ON DELETE SET NULL,
    resolved_scene_index        INTEGER,
    resolution_summary          TEXT,
    -- 优先级(LLM 给) — high(主线核心)/ medium(支线)/ low(细节)
    priority                    TEXT NOT NULL DEFAULT 'medium' CHECK (
        priority IN ('high', 'medium', 'low')
    ),
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_foreshadow_project_status
    ON foreshadow_ledger(project_id, status, priority);


-- ============================================================
-- 3. world_rules_ledger — 跨代世界观规则
-- ============================================================
-- 续作中 LLM 自创的"新世界规则"(原作没明写,续作建立的设定),
-- 必须被后代续作遵守 — 否则会出现"前作能用魔法,后作突然魔法消失"。
--
-- 与 world_facts(M4.1)的区别:
--   - world_facts: 当前 sim 内的 LIFE_STATUS / LOCATION / RULE_LOCK,sim 结束失效
--   - world_rules_ledger: project 级别累积,影响所有后代续作

CREATE TABLE IF NOT EXISTS world_rules_ledger (
    id                          TEXT PRIMARY KEY,
    project_id                  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    -- 规则描述(< 150 字,如"地府守门人的规则是杀人不偿命,但偿信物")
    rule_text                   TEXT NOT NULL,
    -- 谁创立的(哪代 / 哪一幕)
    introduced_in_simulation_id TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    introduced_scene_index      INTEGER NOT NULL,
    -- 规则范围 — global(全宇宙)/ faction(某派系)/ location(某地)
    scope                       TEXT NOT NULL DEFAULT 'global' CHECK (
        scope IN ('global', 'faction', 'location', 'character')
    ),
    -- 关联实体 id(scope!=global 时填,如 character_id / scene_id / 派系 id)
    scope_target_id             TEXT,
    -- 是否仍生效(用户可手动失效;默认 active)
    active                      INTEGER NOT NULL DEFAULT 1,
    created_at                  TEXT NOT NULL,
    updated_at                  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_world_rules_project_active
    ON world_rules_ledger(project_id, active);
