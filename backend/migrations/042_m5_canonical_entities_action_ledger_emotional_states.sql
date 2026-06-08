-- migration 042: M5 治本三件套(Sprint 6.A2 M5,2026-05-20)
--
-- 用户拍板铁律延续(2026-05-18):
--   "以最高标准开发项目,做最好的产品 — 成本不重要"
--
-- M5 起源 — Gemini 第二轮评测 M4 产物,4 个根因性瑕疵 M4 没拦住:
--   1. 照片主人在同一篇内变 3 次(林小满→柳英→林小禾)
--      → world_facts 是 flat 清单,无"实体唯一身份"语义
--   2. 张凡"抽出+翻转照片"动作跨幕重复 4 次
--      → 没有"已发生原子动作流水"
--   3. 卧室→教室空间瞬移 / 同幕"昨天那歌"时间倒流
--      → LOCATION/time fact 淹没在 active_facts 列表,LLM 不读
--   4. 刘飞收到死亡威胁后立即讨好周梦买薯片"平安无事"
--      → 角色情绪无跨幕连续性
--
-- M5 治本策略:
--   - 5.1 canonical_entities — 实体唯一身份注册(首次锁定,后续严禁新造同义身份)
--   - 5.2 action_ledger — 原子动作流水(narrator 调用前注入"禁止重复")
--   - 5.6 character_emotional_states — 每幕末角色情绪向量(跨幕连续性)
--
-- 注:5.3 pre-gen 硬铁律 prepend 是 prompt 改造,不需要 schema
--     5.4 multi-sample voting 是主循环改造,不需要 schema
--     5.5 temporal_lock 复用现有 simulation_scenes.time_anchor + 业务层 numeric 解析
--
-- created 2026-05-20 / Sprint 6.A2 M5


-- ============================================================
-- 1. canonical_entities — 实体唯一身份注册表
-- ============================================================
-- 每行 = 灵魂续写过程中"已确立的核心实体"(角色/物件/地点/事件)
--
-- 设计核心:
--   - 首次出现即"锁定"(locked_at 非空)
--   - 后续抽取若发现新身份与已有实体语义重合 → **强制用旧 entity_id**,
--     不允许造新身份(治瑕疵 1)
--   - canonical_name 是规范名;aliases_json 是同实体的别名列表
--   - description 用于 LLM 语义匹配(判定"林小禾"与"林小满"是否同一人)
CREATE TABLE IF NOT EXISTS canonical_entities (
    id              TEXT PRIMARY KEY,
    simulation_id   TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,

    -- 实体类型
    --   character — 角色(主角/配角/inferred)
    --   object    — 关键物件(照片/日记/钥匙/凶器)
    --   location  — 地点(具体的"潇湘馆""韩紫雨家卧室")
    --   event     — 关键事件("林小满跳楼""刘飞母亲被威胁")
    entity_type     TEXT NOT NULL CHECK (
        entity_type IN ('character', 'object', 'location', 'event')
    ),

    -- 规范名(< 30 字)
    canonical_name  TEXT NOT NULL,

    -- 别名列表(JSON 数组;首次锁定时只含 canonical_name 自己)
    -- 例:["林小满", "小满", "那个跳楼的女生"]
    aliases_json    TEXT NOT NULL DEFAULT '[]',

    -- 实体描述(< 300 字;用于 LLM 语义匹配判定同义)
    description     TEXT NOT NULL,

    -- 首次引入幕(NULL = 创建时还没确定;实际由 narrator 调用)
    first_introduced_scene INTEGER NOT NULL,

    -- 锁定时间(非空即锁;一旦锁定后续不允许造同义新身份)
    locked_at       TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_canonical_entities_sim_type
    ON canonical_entities(simulation_id, entity_type);

CREATE INDEX IF NOT EXISTS idx_canonical_entities_sim_scene
    ON canonical_entities(simulation_id, first_introduced_scene);


-- ============================================================
-- 2. action_ledger — 原子动作流水(治瑕疵 2 跨幕动作重复)
-- ============================================================
-- 每行 = 一次原子动作("张凡 抽出 照片" / "李宇天 踹 门")
--
-- 设计核心:
--   - 抽取每幕 narrative 中的关键原子动作(actor + verb + object)
--   - narrator/agent_dialogue 调用前,把已发生的 atomic actions 转成
--     "禁止重复"清单,prepend 到 system_prompt 顶部
--   - is_repeatable=0 的动作(如"抽出照片""击杀")严格不许跨幕重复
CREATE TABLE IF NOT EXISTS action_ledger (
    id              TEXT PRIMARY KEY,
    simulation_id   TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,

    -- 哪幕发生(对齐 simulation_scenes.scene_index)
    scene_index     INTEGER NOT NULL,

    -- 谁做的(角色名;若关联到 canonical_entity 走 actor_entity_id)
    actor_name      TEXT NOT NULL,
    actor_entity_id TEXT REFERENCES canonical_entities(id) ON DELETE SET NULL,

    -- 做什么(动词 / 短动作描述)
    -- 例:"抽出" / "翻转" / "拨打" / "射击" / "推门" / "击杀"
    verb            TEXT NOT NULL,

    -- 对什么(对象;可空 — 不及物动词如"逃跑")
    object_name     TEXT,
    object_entity_id TEXT REFERENCES canonical_entities(id) ON DELETE SET NULL,

    -- 完整描述(< 200 字)
    description     TEXT NOT NULL,

    -- 是否可重复:
    --   0 = 原子不可重复(找到关键物件 / 击杀 / 拆开机关)
    --   1 = 可重复的常规动作(说话 / 走动 / 看一眼)
    -- 默认 0(extractor LLM 判定;有疑议时默认严格)
    is_repeatable   INTEGER NOT NULL DEFAULT 0,

    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_action_ledger_sim_scene
    ON action_ledger(simulation_id, scene_index);

-- 查询模式:某 sim 全部不可重复的已发生动作(narrator 注入"禁止重复"用)
CREATE INDEX IF NOT EXISTS idx_action_ledger_sim_atomic
    ON action_ledger(simulation_id, is_repeatable)
    WHERE is_repeatable = 0;


-- ============================================================
-- 3. character_emotional_states — 角色情绪链(治瑕疵 4 情绪断裂)
-- ============================================================
-- 每行 = 某角色在某幕末尾的情绪向量
--
-- 设计核心:
--   - 8 维情绪向量(对齐 Plutchik 情绪轮简化版,中文友好)
--   - 每幕 narrator 后,extractor 抽每个在场角色的本幕末情绪
--   - 下幕 narrator/agent_dialogue 调用前,把上幕情绪向量塞 prompt
--   - consistency_checker 增加 EMOTIONAL_DISCONTINUITY 违规类型
--     (单维度跨幕变化 ≥ 5 档无剧情铺垫 → critical)
--
-- 8 维:joy / sadness / anger / fear / surprise / disgust / trust / anticipation
-- 每维 0-10 整数(0 = 完全无,10 = 极端强烈)
CREATE TABLE IF NOT EXISTS character_emotional_states (
    id              TEXT PRIMARY KEY,
    simulation_id   TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,
    character_id    TEXT NOT NULL,  -- 不加 FK(允许 inferred 角色)

    -- 哪幕(每幕末该角色的情绪快照)
    scene_index     INTEGER NOT NULL,

    -- 8 维情绪向量(0-10 整数)
    emotion_json    TEXT NOT NULL,
    -- 示例 {"joy":2,"sadness":7,"anger":3,"fear":9,"surprise":4,
    --        "disgust":2,"trust":3,"anticipation":5}

    -- LLM 给的简短解释(< 150 字;说明为什么是这个向量)
    rationale       TEXT NOT NULL DEFAULT '',

    created_at      TEXT NOT NULL,

    -- 同角色同幕只一行
    UNIQUE (simulation_id, character_id, scene_index)
);

CREATE INDEX IF NOT EXISTS idx_emotional_states_sim_char_scene
    ON character_emotional_states(simulation_id, character_id, scene_index);
