-- migration 040: 全局事实账本 + 主线追踪(Sprint 6.A2 M4.1,2026-05-19)
--
-- 用户拍板铁律(2026-05-18 沉淀):
--   "以最高标准开发项目,不要遇难则退,不管多复杂都要做"
--   "成本不重要,做最好的产品"
--
-- M4 质量纵深起源 — Gemini 第三方评测灵魂续写产物,报 7 个核心瑕疵:
--   1. 剧情死循环(永远接新任务无结果)— 根因 A 缺全局事实
--   2. 时间线悖论(班长被捕又上街)— 根因 A
--   3. 行为漂移失控(刘飞请求无序升级)— 根因 B
--   4. 套语复读机(刘飞专属低头攥袋发抖)— 根因 C
--   5. 反派规则覆写(地府守门人每幕新游戏)— 根因 A
--   6. 续作"灭灯"撕裂(张凡推门→李宇天又踹门)— 根因 D
--
-- 本 migration 治根因 A:**全局事实账本 + 主线推进追踪**。
--
-- ============================================================
-- 1. world_facts — 灵魂续写过程中"已确立的世界事实"账本
-- ============================================================
-- 每行 = 一条客观事实(角色生死 / 物理位置 / 反派规则 / 关键事件已发生 / 关系变化)
--
-- 产生:每幕 narrator 合稿后,world_state_extractor LLM 从 narrative_segment 抽取
-- 消费:
--   - scene_picker 必读 ACTIVE + LOCKED 事实(选场景 / 选时间不能与事实冲突)
--   - agent_dialogue 必读相关事实(自己 / 同场角色的生死 / 位置)
--   - narrator 必读 LOCKED 事实(反派规则要前后一致)
--   - consistency_checker(M4.3)必读全部 ACTIVE/LOCKED 做违规检测
--
-- 状态机:
--   ACTIVE     新事实,当前生效
--   SUPERSEDED 被后续事实覆盖(如班长从警局回家:旧 LOCATION ACTIVE → SUPERSEDED)
--   LOCKED     不可覆盖(反派游戏规则一旦发布,LLM 不许擅自改)
--
-- 为什么把 RULE_LOCK 独立 status 而非 fact_type?
--   - fact_type 是"事实分类"(语义维度);LOCKED 是"可变性约束"(强度维度)
--   - 一条 RULE_LOCK fact_type 默认 status=LOCKED;但理论上其他事实也可被人工锁
--   - 解耦让后续 M4.3 一致性检测可灵活策略(看 status 而非 fact_type)
CREATE TABLE IF NOT EXISTS world_facts (
    id              TEXT PRIMARY KEY,
    simulation_id   TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,

    -- 由哪幕产出(对齐 simulation_scenes.scene_index)
    scene_index     INTEGER NOT NULL,

    -- 事实类型:
    --   LIFE_STATUS         角色生死/重伤等生命状态变化
    --   LOCATION            角色物理位置(在警局 / 在韩紫雨家)
    --   RULE_LOCK           反派/规则系统发布的游戏规则(默认 LOCKED)
    --   EVENT_DONE          关键事件已发生(找到日记 / 解开机关)
    --   RELATIONSHIP_CHANGE 关系状态切换(从朋友变敌人,与 M1 phases 互补)
    fact_type       TEXT NOT NULL CHECK (
        fact_type IN (
            'LIFE_STATUS', 'LOCATION', 'RULE_LOCK',
            'EVENT_DONE', 'RELATIONSHIP_CHANGE'
        )
    ),

    -- 事实关联主体 — 不加 FK(允许 NULL / inferred 角色 / 全局事件)
    subject_id      TEXT,           -- 角色 id / 关系 id / NULL(全局事件)
    subject_name    TEXT NOT NULL,  -- 冗余字段,prompt 可直接读不用 JOIN

    -- 事实内容(LLM 给的短句描述,< 200 字)
    -- 例:"班长被警察带走,在警局接受调查,本幕及后续不能在场,除非另有事件解释"
    content         TEXT NOT NULL,

    -- 状态机
    status          TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (
        status IN ('ACTIVE', 'SUPERSEDED', 'LOCKED')
    ),

    -- 被哪条 fact 覆盖(SUPERSEDED 时填写;ACTIVE/LOCKED 为 NULL)
    superseded_by_fact_id TEXT REFERENCES world_facts(id) ON DELETE SET NULL,

    created_at      TEXT NOT NULL
);

-- 查询模式 1:某 sim 当前所有生效事实(scene_picker / agent_dialogue 必读)
CREATE INDEX IF NOT EXISTS idx_world_facts_sim_active
    ON world_facts(simulation_id, status, fact_type)
    WHERE status IN ('ACTIVE', 'LOCKED');

-- 查询模式 2:某 sim 某幕产出的事实(主循环增量落库时查)
CREATE INDEX IF NOT EXISTS idx_world_facts_sim_scene
    ON world_facts(simulation_id, scene_index);

-- 查询模式 3:覆盖一条事实时,按 subject + type 找候选(extractor 覆盖判定用)
CREATE INDEX IF NOT EXISTS idx_world_facts_subject_type
    ON world_facts(simulation_id, subject_id, fact_type, status)
    WHERE status = 'ACTIVE';


-- ============================================================
-- 2. plot_threads — 主线/支线任务追踪(治剧情死循环)
-- ============================================================
-- 每行 = 一条剧情线索(已引入 / 未解决 / 已解决)
--
-- 产生:每幕 narrator 后,plot_tracker LLM 从 narrative_segment 抽取:
--   - 新引入的任务/悬念/承诺(introduced)
--   - 已解决的现有 thread(resolved)
-- 维护:每幕开始时,扫所有 ACTIVE thread:
--   - 若上幕未推进 → staleness +1
--   - 若上幕推进了 → staleness = 0
--   - staleness >= STALENESS_FORCE_THRESHOLD(默认 3)→ 下幕 scene_picker 必须推进
--
-- 消费:
--   - scene_picker 必读 ACTIVE threads(优先选未推进的;staleness 高的强推)
--   - narrator 可读 ACTIVE threads(本幕承接合理的 thread,不引入太多新)
--
-- 为什么 priority 1/2/3 而非自由整数?
--   - 1 = 主线(用户 divergence 直接驱动的)
--   - 2 = 次要(单角色弧光 / 子任务)
--   - 3 = 背景(世界观铺垫 / 伏笔)
--   - LLM 给出 priority 时按这 3 档判定;数字越小越优先
CREATE TABLE IF NOT EXISTS plot_threads (
    id              TEXT PRIMARY KEY,
    simulation_id   TEXT NOT NULL REFERENCES simulations(id) ON DELETE CASCADE,

    -- 由哪幕引入(0-based,对齐 scene_index)
    introduced_at_scene_index    INTEGER NOT NULL,

    -- thread 描述(< 200 字 LLM 输出)
    -- 例:"刘飞母亲被地府守门人威胁,5h47m 倒计时,主角小队需赶到刘飞家解救"
    description     TEXT NOT NULL,

    -- 已解决幕索引(NULL = 仍 ACTIVE)
    resolved_at_scene_index      INTEGER,

    -- 优先级(1 主线 / 2 次要 / 3 背景)
    priority        INTEGER NOT NULL DEFAULT 2 CHECK (priority IN (1, 2, 3)),

    -- 老化计数(每幕扫描时更新):
    -- 多少幕没推进了。达到阈值 → scene_picker 必须推进
    staleness       INTEGER NOT NULL DEFAULT 0,

    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

-- 查询模式 1:某 sim 所有未解决 thread(scene_picker / narrator 必读)
CREATE INDEX IF NOT EXISTS idx_plot_threads_sim_active
    ON plot_threads(simulation_id, resolved_at_scene_index, priority, staleness DESC)
    WHERE resolved_at_scene_index IS NULL;

-- 查询模式 2:某 sim 某幕引入的 thread(主循环增量落库后查)
CREATE INDEX IF NOT EXISTS idx_plot_threads_sim_scene
    ON plot_threads(simulation_id, introduced_at_scene_index);
