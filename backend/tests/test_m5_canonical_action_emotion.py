"""Sprint 6.A2 M5(2026-05-20)— 治本三件套测试。

覆盖 M5 6 个子模块的核心契约:
  - M5.1 entity_registrar:实体注册 / alias 合并 / 同义不允许造新身份
  - M5.2 action_extractor:原子动作落库 / 不可重复标志
  - M5.3 hard_constraints:prepend 段含实体+动作+情绪+时间
  - M5.4 multi-sample voting:3 候选都跑 + 选 critical 最少
  - M5.5 temporal_lock:fast-path 解析 + 不可倒流
  - M5.6 emotional_state_tracker:情绪向量落库 / 跨幕 baseline
"""
from __future__ import annotations

import json
import uuid


# ============================================================
# helpers
# ============================================================

def _seed_sim_with_chars(n_chars: int = 2) -> tuple[str, str, dict[str, str]]:
    """造 user + project + characters + 1 个 evolution sim。"""
    from app.db import get_connection
    from app.services.project_service import iso_now

    user_id = uuid.uuid4().hex
    pid = uuid.uuid4().hex
    sim_id = uuid.uuid4().hex
    now = iso_now()
    names = ["张凡", "班长", "刘飞", "周梦"][:n_chars]
    name_to_id: dict[str, str] = {}

    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO users (id, email, plan, created_at, updated_at)
               VALUES (?, ?, 'free', ?, ?)""",
            (user_id, f"{user_id[:8]}@test.local", now, now),
        )
        conn.execute(
            """INSERT INTO projects (id, user_id, name, type, tags, mode,
                                       created_at, updated_at,
                                       world_baseline_json, graph_strength_threshold)
               VALUES (?, ?, 'M5 测试', 'novel', '[]', 'initial', ?, ?, '{}', 30)""",
            (pid, user_id, now, now),
        )
        for n in names:
            cid = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO characters
                    (id, project_id, name, identity, personality, quotes, no_go_list,
                     position_x, position_y, position_z, color,
                     created_at, updated_at,
                     is_protagonist, protagonist_score, protagonist_reasons_json,
                     protagonist_user_pinned)
                   VALUES (?, ?, ?, ?, ?, '[]', '[]', 0, 0, 0, NULL, ?, ?,
                           1, 0.9, '[]', 0)""",
                (cid, pid, n, f"{n} 的身份", f"{n} 的性格", now, now),
            )
            name_to_id[n] = cid
        conn.execute(
            """INSERT INTO simulations
                (id, project_id, user_id, divergence, reshape_percent,
                 rounds_planned, target_chars, style, custom_style_hint,
                 context_simulation_ids, narrative_summary, characters_snapshot,
                 state, current_round, timeline_json, narrative,
                 tokens_input, tokens_output, cost_yuan, error_message,
                 created_at, started_at, completed_at, mode)
               VALUES (?, ?, ?, '测试', 50, 5, 4000, 'A', NULL,
                       '[]', NULL, '[]', 'done', 1, NULL, NULL,
                       0, 0, 0.0, NULL, ?, NULL, NULL, 'evolution')""",
            (sim_id, pid, user_id, now),
        )
        conn.commit()
    finally:
        conn.close()
    return sim_id, pid, name_to_id


def _load_sim_and_chars(sim_id: str, name_to_id: dict[str, str]):
    from app.db import get_connection
    from app.models.simulation import Simulation
    from app.models.character import Character
    conn = get_connection()
    try:
        sim_row = conn.execute("SELECT * FROM simulations WHERE id=?", (sim_id,)).fetchone()
        sim = Simulation.from_row(sim_row)
        chars: list[Character] = []
        for n, cid in name_to_id.items():
            char_row = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
            chars.append(Character.from_row(char_row))
        return sim, chars
    finally:
        conn.close()


# ============================================================
# M5.1 entity_registrar 测试
# ============================================================

def test_entity_registrar_creates_new_entities(monkeypatch):
    """LLM 给 new_entities → 落库 canonical_entities 表,locked_at 非空。"""
    sim_id, _, name_to_id = _seed_sim_with_chars(n_chars=2)
    sim, agents = _load_sim_and_chars(sim_id, name_to_id)

    def fake(_sys, _user, **_kw):
        return ({
            "new_entities": [
                {
                    "entity_type": "character",
                    "canonical_name": "林小满",
                    "aliases": ["小满"],
                    "description": "三年前从天台跳楼的女生,被全班遗忘",
                },
                {
                    "entity_type": "object",
                    "canonical_name": "韩紫雨的旧照片",
                    "aliases": ["照片", "泛黄旧照"],
                    "description": "藏在韩紫雨枕芯里的旧照片",
                },
            ],
            "alias_additions": [],
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.entity_registrar.call_llm_json", fake,
    )

    from app.services.entity_registrar import (
        register_entities_from_segment, list_canonical_entities,
    )
    from app.db import get_connection
    conn = get_connection()
    try:
        summary, _ = register_entities_from_segment(
            conn, sim, scene_index=0,
            narrative_segment="x" * 200, agents=agents,
        )
        assert len(summary["created"]) == 2
        entities = list_canonical_entities(conn, sim_id)
        assert len(entities) == 2
        # 校验 locked_at 非空(首次锁定)
        for e in entities:
            assert e.locked_at, f"entity {e.canonical_name} 未锁定"
        # 校验 aliases 含 canonical_name 自己
        for e in entities:
            assert e.canonical_name in e.aliases
    finally:
        conn.close()


def test_entity_registrar_aliases_synonymous_identity(monkeypatch):
    """已有"林小满"+ LLM 抽出"林小禾"语义重合 → 加 alias,不创建新实体(治瑕疵 1 核心)。"""
    sim_id, _, name_to_id = _seed_sim_with_chars(n_chars=2)
    sim, agents = _load_sim_and_chars(sim_id, name_to_id)

    # 第 1 步:先注册"林小满"
    def fake_step1(_sys, _user, **_kw):
        return ({
            "new_entities": [
                {
                    "entity_type": "character",
                    "canonical_name": "林小满",
                    "aliases": ["小满"],
                    "description": "三年前从天台跳楼的女生",
                }
            ],
            "alias_additions": [],
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.entity_registrar.call_llm_json", fake_step1,
    )
    from app.services.entity_registrar import (
        register_entities_from_segment, list_canonical_entities,
    )
    from app.db import get_connection
    conn = get_connection()
    try:
        register_entities_from_segment(
            conn, sim, scene_index=0,
            narrative_segment="x" * 200, agents=agents,
        )
        entities = list_canonical_entities(conn, sim_id)
        lm_id = [e.id for e in entities if e.canonical_name == "林小满"][0]

        # 第 2 步:LLM 判定"林小禾"是林小满别名
        def fake_step2(_sys, _user, **_kw):
            return ({
                "new_entities": [],
                "alias_additions": [
                    {
                        "existing_entity_id": lm_id,
                        "new_alias": "林小禾",
                    }
                ],
            }, {"input_tokens": 100, "output_tokens": 50})
        monkeypatch.setattr(
            "app.services.entity_registrar.call_llm_json", fake_step2,
        )
        register_entities_from_segment(
            conn, sim, scene_index=1,
            narrative_segment="x" * 200, agents=agents,
        )

        # 校验:仍然只有 1 个实体,但 aliases 含"林小禾"
        entities_after = list_canonical_entities(conn, sim_id)
        assert len(entities_after) == 1
        lm = entities_after[0]
        assert "林小禾" in lm.aliases, (
            f"林小禾 应作为别名加入,实际 aliases={lm.aliases}"
        )
    finally:
        conn.close()


# ============================================================
# M5.2 action_extractor 测试
# ============================================================

def test_action_extractor_persists_atomic_actions(monkeypatch):
    """LLM 给 actions → 落库 + is_repeatable 严格判定。"""
    sim_id, _, name_to_id = _seed_sim_with_chars(n_chars=2)
    sim, agents = _load_sim_and_chars(sim_id, name_to_id)

    def fake(_sys, _user, **_kw):
        return ({
            "actions": [
                {
                    "actor_name": "张凡",
                    "verb": "抽出",
                    "object_name": "照片",
                    "description": "张凡从韩紫雨家卧室枕芯里抽出泛黄照片",
                    "is_repeatable": False,
                },
                {
                    "actor_name": "班长",
                    "verb": "找到",
                    "object_name": "日记",
                    "description": "班长在抽屉里找到日记本",
                    "is_repeatable": False,
                },
                {
                    "actor_name": "刘飞",
                    "verb": "走到",
                    "object_name": "窗边",
                    "description": "刘飞走到窗边",
                    "is_repeatable": True,
                },
            ],
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.action_extractor.call_llm_json", fake,
    )

    from app.services.action_extractor import (
        extract_actions_from_segment, list_atomic_actions,
    )
    from app.db import get_connection
    conn = get_connection()
    try:
        inserted, _ = extract_actions_from_segment(
            conn, sim, scene_index=0,
            narrative_segment="x" * 200, agents=agents,
        )
        assert inserted == 3

        # list_atomic_actions 只返 is_repeatable=0 的
        atomic = list_atomic_actions(conn, sim_id)
        assert len(atomic) == 2  # 张凡抽出+班长找到,不含刘飞走到
        verbs = {a.verb for a in atomic}
        assert verbs == {"抽出", "找到"}
    finally:
        conn.close()


# ============================================================
# M5.3 hard_constraints 测试
# ============================================================

def test_hard_constraints_block_contains_entities_actions_emotions():
    """build_hard_constraints_block 整合实体+动作+情绪+时间为完整段。"""
    sim_id, _, name_to_id = _seed_sim_with_chars(n_chars=2)
    sim, agents = _load_sim_and_chars(sim_id, name_to_id)

    # 直接 INSERT 一个 entity + 一个 atomic action + 一个 emotional_state
    from app.db import get_connection
    from app.services.project_service import iso_now
    now = iso_now()
    conn = get_connection()
    try:
        ent_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO canonical_entities
               (id, simulation_id, entity_type, canonical_name,
                aliases_json, description, first_introduced_scene,
                locked_at, created_at)
               VALUES (?, ?, 'character', '林小满', '["林小满","小满"]',
                       '三年前跳楼的女生', 0, ?, ?)""",
            (ent_id, sim_id, now, now),
        )
        act_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO action_ledger
               (id, simulation_id, scene_index, actor_name, actor_entity_id,
                verb, object_name, object_entity_id, description,
                is_repeatable, created_at)
               VALUES (?, ?, 0, '张凡', NULL, '抽出', '照片', NULL,
                       '张凡从枕芯抽出照片', 0, ?)""",
            (act_id, sim_id, now),
        )
        # 也加 1 个 emotional_state(给某 agent)
        first_agent_id = list(name_to_id.values())[0]
        em_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO character_emotional_states
               (id, simulation_id, character_id, scene_index,
                emotion_json, rationale, created_at)
               VALUES (?, ?, ?, 0, ?, '本幕极度恐惧', ?)""",
            (em_id, sim_id, first_agent_id,
             json.dumps({"fear": 9, "joy": 0, "sadness": 5,
                         "anger": 0, "surprise": 2, "disgust": 0,
                         "trust": 0, "anticipation": 1}), now),
        )
        # 也加 1 个 simulation_scene 让 temporal_lock 有数据
        conn.execute(
            """INSERT INTO simulation_scenes
               (id, simulation_id, scene_index, scene_name, scene_source,
                time_anchor, characters_present_json, narrative_segment, created_at)
               VALUES (?, ?, 0, 'X', 'llm_created', '次日下午', '[]', 'y', ?)""",
            (uuid.uuid4().hex, sim_id, now),
        )
        conn.commit()

        from app.services.hard_constraints import build_hard_constraints_block
        block = build_hard_constraints_block(
            conn, sim, scene_index=1, agents=agents,
        )
        assert "本幕硬铁律" in block
        assert "林小满" in block
        assert "抽出" in block
        assert "恐惧" in block  # 中文情绪标签
        assert "次日下午" in block  # 时间锚
    finally:
        conn.close()


def test_hard_constraints_block_empty_when_no_data():
    """无 sim 内容数据时,只返"用户创作偏好"骨架,不应有 world_facts / plot_threads / canonical / 情绪 等内容 section。

    P0V.1(2026-05-24)改造:hard_constraints 始终注入"用户创作偏好"section(reshape_percent / 节奏档位 等 sim 配置),
    即便无内容数据也会有标题骨架。本测试改为验证骨架结构而非完全空。
    """
    sim_id, _, name_to_id = _seed_sim_with_chars(n_chars=2)
    sim, agents = _load_sim_and_chars(sim_id, name_to_id)
    from app.db import get_connection
    conn = get_connection()
    try:
        from app.services.hard_constraints import build_hard_constraints_block
        block = build_hard_constraints_block(
            conn, sim, scene_index=0, agents=agents,
        )
        # 标题永远在(P0V.1)
        assert "本幕硬铁律" in block
        # 用户创作偏好 section 永远拼(配置数据来自 sim 自身)
        assert "用户创作偏好" in block
        # 但内容 section 因无数据应该不在
        assert "世界事实账本" not in block, "无 world_facts 数据时不应拼此段"
        assert "主线追踪" not in block, "无 plot_threads 数据时不应拼此段"
        assert "已注册实体" not in block, "无 canonical_entities 数据时不应拼此段"
        assert "已发生不可重复动作" not in block, "无 action_ledger 数据时不应拼此段"
    finally:
        conn.close()


# ============================================================
# M5.5 temporal_lock 测试
# ============================================================

def test_temporal_lock_fastpath_parses_common_anchors():
    """常见时间锚 fast-path 0 LLM 解析。"""
    from app.services.temporal_lock import parse_time_anchor_fastpath

    # 同时刻
    offset, matched = parse_time_anchor_fastpath("", prev_offset=10.0)
    assert offset == 10.0
    assert matched

    # 次日下午
    offset, matched = parse_time_anchor_fastpath("次日下午", prev_offset=0.0)
    assert matched
    assert offset > 24.0  # 应明显大于"次日"基线

    # 当晚
    offset, matched = parse_time_anchor_fastpath("当晚", prev_offset=0.0)
    assert matched
    assert 0 < offset <= 12.0

    # 数日后
    offset, matched = parse_time_anchor_fastpath("数日后", prev_offset=0.0)
    assert matched
    assert offset >= 24.0

    # 未识别的 → matched=False
    _, matched = parse_time_anchor_fastpath("某个莫名其妙的时间", prev_offset=0.0)
    assert not matched


def test_temporal_constraint_line_format():
    """format_temporal_constraint_line 生成可读硬铁律行。"""
    from app.services.temporal_lock import format_temporal_constraint_line

    line = format_temporal_constraint_line(28.0, "次日下午")
    assert "次日下午" in line
    assert "倒流" in line  # 关键禁令词
    assert "28" in line  # 偏移数值

    # 空 anchor → 空串
    assert format_temporal_constraint_line(0.0, "") == ""


# ============================================================
# M5.6 emotional_state_tracker 测试
# ============================================================

def test_emotional_state_tracker_persists_8_dim_vectors(monkeypatch):
    """LLM 给每个 agent 8 维向量 → 落库 + 8 维都非负 0-10。"""
    sim_id, _, name_to_id = _seed_sim_with_chars(n_chars=2)
    sim, agents = _load_sim_and_chars(sim_id, name_to_id)

    def fake(_sys, _user, **_kw):
        return ({
            "emotional_states": [
                {
                    "character_name": agents[0].name,
                    "emotion": {
                        "joy": 0, "sadness": 7, "anger": 2, "fear": 9,
                        "surprise": 4, "disgust": 1, "trust": 1, "anticipation": 2,
                    },
                    "rationale": "刚收到死亡威胁,极度恐惧",
                },
                {
                    "character_name": agents[1].name,
                    "emotion": {
                        "joy": 3, "sadness": 0, "anger": 5, "fear": 1,
                        "surprise": 2, "disgust": 6, "trust": 1, "anticipation": 2,
                    },
                    "rationale": "对同伴鄙夷",
                },
            ],
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.emotional_state_tracker.call_llm_json", fake,
    )

    from app.services.emotional_state_tracker import (
        track_emotional_states, get_latest_emotional_state,
    )
    from app.db import get_connection
    conn = get_connection()
    try:
        inserted, _ = track_emotional_states(
            conn, sim, scene_index=0,
            narrative_segment="x" * 200, agents=agents,
        )
        assert inserted == 2

        # 拉两个 agent 的最新情绪
        st0 = get_latest_emotional_state(conn, sim_id, agents[0].id)
        assert st0 is not None
        assert st0.emotion["fear"] == 9
        assert st0.emotion["sadness"] == 7

        st1 = get_latest_emotional_state(conn, sim_id, agents[1].id)
        assert st1 is not None
        assert st1.emotion["disgust"] == 6
    finally:
        conn.close()


def test_emotional_state_clamps_out_of_range_values(monkeypatch):
    """LLM 返回 negative 或 > 10 → 自动 clamp 到 [0, 10]。"""
    sim_id, _, name_to_id = _seed_sim_with_chars(n_chars=1)
    sim, agents = _load_sim_and_chars(sim_id, name_to_id)

    def fake(_sys, _user, **_kw):
        return ({
            "emotional_states": [
                {
                    "character_name": agents[0].name,
                    "emotion": {
                        "joy": -3,    # < 0 → clamp 到 0
                        "sadness": 15,  # > 10 → clamp 到 10
                        "anger": 5,    # 正常
                        "fear": 8,
                        "surprise": 0,
                        "disgust": 0,
                        "trust": 0,
                        "anticipation": 0,
                    },
                    "rationale": "极端情绪测试",
                }
            ],
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.emotional_state_tracker.call_llm_json", fake,
    )

    from app.services.emotional_state_tracker import (
        track_emotional_states, get_latest_emotional_state,
    )
    from app.db import get_connection
    conn = get_connection()
    try:
        track_emotional_states(
            conn, sim, scene_index=0,
            narrative_segment="x" * 200, agents=agents,
        )
        st = get_latest_emotional_state(conn, sim_id, agents[0].id)
        assert st is not None
        assert st.emotion["joy"] == 0   # clamp 后
        assert st.emotion["sadness"] == 10  # clamp 后
        assert st.emotion["anger"] == 5
    finally:
        conn.close()


# ============================================================
# M5.4 multi-sample voting + 整体集成测试
# ============================================================

def test_narrator_multi_sample_picks_lowest_critical(monkeypatch):
    """3 个候选,critical_count 不同 → 选 critical 最少的入库。"""
    sim_id, _, name_to_id = _seed_sim_with_chars(n_chars=2)
    from app.db import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE simulations SET state='queued', current_round=0, "
            "narrative=NULL, rounds_planned=1 WHERE id=?",
            (sim_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # 用 sample_idx 给 narrator 不同 segment + 不同 critical 数
    # 顺序:候选 0 critical=2,候选 1 critical=0 (最优),候选 2 critical=1
    state = {
        "narrator_count": 0,
        "checker_count": 0,
    }

    def fake_llm(system_prompt, user_input, **_kw):
        usage = {"input_tokens": 100, "output_tokens": 50}
        sp = system_prompt.strip()
        if "以下是原 prompt:" in sp:
            real_start = sp.split("以下是原 prompt:", 1)[1][:300]
        else:
            real_start = sp[:1500]
        if "# 场景调度员" in real_start:
            return ({"scene_name": "S", "scene_source": "llm_created",
                     "time_anchor": "", "reasoning": ""}, usage)
        if "# 世界事实账本" in real_start:
            return ({"new_facts": []}, usage)
        if "# 主线追踪员" in real_start:
            return ({"resolved_thread_ids": [], "advanced_thread_ids": [],
                     "new_threads": []}, usage)
        if "# 实体身份注册员" in real_start:
            return ({"new_entities": [], "alias_additions": []}, usage)
        if "# 原子动作提取员" in real_start:
            return ({"actions": []}, usage)
        if "# 角色情绪追踪员" in real_start:
            return ({"emotional_states": []}, usage)
        if "时间锚解析器" in real_start[:200]:
            return ({"new_offset_hours": 0.0}, usage)
        if "# 一致性自检员" in real_start:
            # 按 narrator_count 时序返不同 critical 数
            # 顺序:checker 调用 1/2/3 分别对应 narrator 候选 0/1/2
            # narrator_count 此时已经 +1(因为 checker 在 narrator 之后)
            critical_by_sample = {1: 2, 2: 0, 3: 1}
            crit = critical_by_sample.get(state["narrator_count"], 0)
            state["checker_count"] += 1
            return ({
                "violations": [
                    {
                        "severity": "critical", "category": "world_fact",
                        "evidence": f"v{i}", "suggestion": "fix",
                    }
                    for i in range(crit)
                ],
            }, usage)
        if "# Agent 对话" in real_start or "扮演一个角色" in real_start[:400]:
            return ({"reflection": "x", "dialogue": "d", "action": "a"}, usage)
        if "# 叙述者" in real_start:
            state["narrator_count"] += 1
            # 每个候选给不同长度,标记好让我们能验证哪个被选
            sample_id = state["narrator_count"]
            return ({"narrative_segment":
                     f"SAMPLE_{sample_id}_" + "x" * 200}, usage)
        raise ValueError(real_start[:100])

    for tgt in [
        "app.services.agent_evolution_engine.call_llm_json",
        "app.services.world_state_extractor.call_llm_json",
        "app.services.plot_tracker.call_llm_json",
        "app.services.consistency_checker.call_llm_json",
        "app.services.entity_registrar.call_llm_json",
        "app.services.action_extractor.call_llm_json",
        "app.services.emotional_state_tracker.call_llm_json",
        "app.services.temporal_lock.call_llm_json",
    ]:
        monkeypatch.setattr(tgt, fake_llm)

    # P0T/P0U/P0V/P0W/M10(2026-05-24~27):禁用程序级检测器,
    # 让 fake_llm 完全控制 critical_count(否则 SAMPLE_X_xxxx 字符串会被
    # 跨幕 narrative_repetition / phrase_density 等检测器误判)
    for fn_name in (
        "_check_narrative_repetition",
        "_check_dialogue_repetition",
        "_check_narrative_pov_drift",
        "_check_phrase_density",
        "_check_dialogue_phrase_repetition",
        "_check_physical_constraints_violation",
        "_check_style_drift",
        "_check_imagery_violations",
    ):
        monkeypatch.setattr(
            f"app.services.consistency_checker.{fn_name}",
            lambda *a, **kw: [],
        )

    from app.services.agent_evolution_engine import run_evolution_simulation
    run_evolution_simulation(sim_id)

    # 校验:narrator 至少调 3 次(可能有 retry,但此 case 最优 critical=0 不触发)
    assert state["narrator_count"] >= 3, (
        f"narrator 应至少调 3 次(multi-sample),实际 {state['narrator_count']}"
    )
    # 落库的应该是 SAMPLE_2(critical=0 那个)
    from app.db import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT narrative_segment FROM simulation_scenes "
            "WHERE simulation_id=? AND scene_index=0",
            (sim_id,),
        ).fetchone()
        assert row is not None
        seg = row["narrative_segment"]
        assert "SAMPLE_2" in seg, (
            f"应选 critical=0 的 SAMPLE_2,实际入库 {seg[:30]}"
        )
    finally:
        conn.close()


def test_hard_constraints_prepended_to_narrator_prompt(monkeypatch):
    """跑一幕端到端,验证 narrator system_prompt 顶部含硬铁律段。"""
    sim_id, _, name_to_id = _seed_sim_with_chars(n_chars=2)

    # 先 INSERT 一个 canonical_entity,让硬铁律段非空
    from app.db import get_connection
    from app.services.project_service import iso_now
    now = iso_now()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE simulations SET state='queued', current_round=0, "
            "narrative=NULL, rounds_planned=1 WHERE id=?",
            (sim_id,),
        )
        conn.execute(
            """INSERT INTO canonical_entities
               (id, simulation_id, entity_type, canonical_name,
                aliases_json, description, first_introduced_scene,
                locked_at, created_at)
               VALUES (?, ?, 'character', '林小满', '["林小满"]',
                       '三年前跳楼的女生', 0, ?, ?)""",
            (uuid.uuid4().hex, sim_id, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    captured_narrator_prompts: list[str] = []

    def fake_llm(system_prompt, user_input, **_kw):
        usage = {"input_tokens": 100, "output_tokens": 50}
        sp = system_prompt.strip()
        if "以下是原 prompt:" in sp:
            real_start = sp.split("以下是原 prompt:", 1)[1][:300]
        else:
            real_start = sp[:1500]
        if "# 场景调度员" in real_start:
            return ({"scene_name": "S", "scene_source": "llm_created",
                     "time_anchor": "", "reasoning": ""}, usage)
        if "# 世界事实账本" in real_start:
            return ({"new_facts": []}, usage)
        if "# 主线追踪员" in real_start:
            return ({"resolved_thread_ids": [], "advanced_thread_ids": [],
                     "new_threads": []}, usage)
        if "# 实体身份注册员" in real_start:
            return ({"new_entities": [], "alias_additions": []}, usage)
        if "# 原子动作提取员" in real_start:
            return ({"actions": []}, usage)
        if "# 角色情绪追踪员" in real_start:
            return ({"emotional_states": []}, usage)
        if "时间锚解析器" in real_start[:200]:
            return ({"new_offset_hours": 0.0}, usage)
        if "# 一致性自检员" in real_start:
            return ({"violations": []}, usage)
        if "# Agent 对话" in real_start or "扮演一个角色" in real_start[:400]:
            return ({"reflection": "x", "dialogue": "d", "action": "a"}, usage)
        if "# 叙述者" in real_start:
            # 关键:记录 narrator system_prompt
            captured_narrator_prompts.append(system_prompt)
            return ({"narrative_segment": "x" * 200}, usage)
        raise ValueError(real_start[:100])

    for tgt in [
        "app.services.agent_evolution_engine.call_llm_json",
        "app.services.world_state_extractor.call_llm_json",
        "app.services.plot_tracker.call_llm_json",
        "app.services.consistency_checker.call_llm_json",
        "app.services.entity_registrar.call_llm_json",
        "app.services.action_extractor.call_llm_json",
        "app.services.emotional_state_tracker.call_llm_json",
        "app.services.temporal_lock.call_llm_json",
    ]:
        monkeypatch.setattr(tgt, fake_llm)

    from app.services.agent_evolution_engine import run_evolution_simulation
    run_evolution_simulation(sim_id)

    # 至少 3 次 narrator 调用(multi-sample)
    assert len(captured_narrator_prompts) >= 3

    # 每个 narrator system_prompt 顶部都应含硬铁律段
    for i, p in enumerate(captured_narrator_prompts):
        assert "本幕硬铁律" in p[:500], (
            f"narrator sample {i} 顶部应含硬铁律段,实际前 200 字:{p[:200]!r}"
        )
        assert "林小满" in p[:1500], (
            f"narrator sample {i} 硬铁律应含'林小满'实体身份"
        )
        assert "以下是原 prompt:" in p[:2000], (
            f"narrator sample {i} 应有边界标记 '以下是原 prompt:'"
        )
