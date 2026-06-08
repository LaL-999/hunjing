"""Sprint 6.A2 M4.1(2026-05-19)— 全局事实账本 + 主线追踪测试。

覆盖:
  - extract_world_facts:LLM 抽 5 类事实落库 / 字段校验 / supersede 覆盖逻辑
  - LOCKED 不可被覆盖(反派规则铁律)
  - list_active_facts 过滤 + ordering
  - track_plot_threads:resolved / advanced / new_threads + staleness 维护
  - staleness 达阈值时 must_advance 标识
  - 主循环 hook:scene_picker prompt 注入 world_facts + plot_threads
  - LLM 失败时 graceful degrade(主流程不阻塞)

设计起源:Gemini 第三方评测灵魂续写产物,报根因 A 缺全局事实记忆。
"""
from __future__ import annotations

import uuid

import pytest


# ============================================================
# helpers — 造 sim 用
# ============================================================

def _seed_user_project_sim(n_chars: int = 3) -> tuple[str, str, str, dict[str, str]]:
    """造 user + project + characters + 一个 mode='evolution' 的 sim。

    Returns: (user_id, project_id, sim_id, {name: char_id})
    """
    from app.db import get_connection
    from app.services.project_service import iso_now

    user_id = uuid.uuid4().hex
    pid = uuid.uuid4().hex
    sim_id = uuid.uuid4().hex
    now = iso_now()
    names = ["张凡", "班长", "李宇天", "莫晴雨", "刘飞"][:n_chars]
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
               VALUES (?, ?, 'M4 测试', 'novel', '[]', 'initial', ?, ?, '{}', 30)""",
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
                       '[]', NULL, '[]', 'queued', 0, NULL, NULL,
                       0, 0, 0.0, NULL, ?, NULL, NULL, 'evolution')""",
            (sim_id, pid, user_id, now),
        )
        conn.commit()
    finally:
        conn.close()
    return user_id, pid, sim_id, name_to_id


def _load_sim(sim_id: str):
    """拉 sim 对象(给 extract / track 函数用)。"""
    from app.db import get_connection
    from app.models.simulation import Simulation
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM simulations WHERE id=?", (sim_id,)).fetchone()
        return Simulation.from_row(row)
    finally:
        conn.close()


def _load_chars(char_ids: dict[str, str]):
    """name→id 转 Character 列表。"""
    from app.db import get_connection
    from app.models.character import Character
    conn = get_connection()
    try:
        out = []
        for name, cid in char_ids.items():
            row = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
            out.append(Character.from_row(row))
        return out
    finally:
        conn.close()


# ============================================================
# world_state_extractor 测试
# ============================================================

def test_extract_world_facts_persists_and_supersedes_old_active(monkeypatch):
    """新 LIFE_STATUS fact 覆盖旧 ACTIVE fact:旧 → SUPERSEDED + superseded_by_fact_id 链接。"""
    _, _, sim_id, char_ids = _seed_user_project_sim(n_chars=2)
    sim = _load_sim(sim_id)
    agents = _load_chars(char_ids)

    # 第一次抽取:班长在警局
    def fake_first(_sys, _user, **_kw):
        return ({
            "new_facts": [
                {
                    "fact_type": "LIFE_STATUS",
                    "subject_name": "班长",
                    "content": "被警察带走,在警局接受调查",
                }
            ]
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.world_state_extractor.call_llm_json", fake_first,
    )
    from app.services.world_state_extractor import (
        extract_world_facts, list_active_facts,
    )

    from app.db import get_connection
    conn = get_connection()
    try:
        inserted, _ = extract_world_facts(conn, sim, scene_index=1,
                                           narrative_segment="班长被带走了…",
                                           agents=agents)
        assert inserted == 1
        active = list_active_facts(conn, sim_id)
        assert len(active) == 1
        assert active[0].subject_name == "班长"
        assert active[0].status == "ACTIVE"
        assert active[0].fact_type == "LIFE_STATUS"
        first_fact_id = active[0].id

        # 第二次抽取:班长被释放回家(覆盖旧 fact)
        def fake_second(_sys, _user, **_kw):
            return ({
                "new_facts": [
                    {
                        "fact_type": "LIFE_STATUS",
                        "subject_name": "班长",
                        "content": "被释放回家,目前可以正常活动",
                    }
                ]
            }, {"input_tokens": 100, "output_tokens": 50})
        monkeypatch.setattr(
            "app.services.world_state_extractor.call_llm_json", fake_second,
        )
        inserted2, _ = extract_world_facts(conn, sim, scene_index=2,
                                            narrative_segment="班长被放回来了…",
                                            agents=agents)
        assert inserted2 == 1

        active_after = list_active_facts(conn, sim_id)
        assert len(active_after) == 1
        assert "被释放" in active_after[0].content

        row = conn.execute(
            "SELECT status, superseded_by_fact_id FROM world_facts WHERE id=?",
            (first_fact_id,),
        ).fetchone()
        assert row["status"] == "SUPERSEDED"
        assert row["superseded_by_fact_id"] == active_after[0].id
    finally:
        conn.close()


def test_rule_lock_facts_are_immutable(monkeypatch):
    """RULE_LOCK 永不可被覆盖 — 即使新事实 fact_type 相同 subject 相同。

    数据契约层守护:_supersede_conflicting_facts 只把 status='ACTIVE' 的标 SUPERSEDED,
    LOCKED 跳过。
    """
    _, _, sim_id, char_ids = _seed_user_project_sim(n_chars=1)
    sim = _load_sim(sim_id)
    agents = _load_chars(char_ids)

    def fake_first(_sys, _user, **_kw):
        return ({
            "new_facts": [
                {
                    "fact_type": "RULE_LOCK",
                    "subject_name": "地府守门人",
                    "content": "找齐 5 张韩紫雨日记残页,缺一不可,零点截止",
                }
            ]
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.world_state_extractor.call_llm_json", fake_first,
    )
    from app.services.world_state_extractor import (
        extract_world_facts, list_active_facts,
    )

    from app.db import get_connection
    conn = get_connection()
    try:
        extract_world_facts(conn, sim, scene_index=1,
                            narrative_segment="规则发布…", agents=agents)
        active = list_active_facts(conn, sim_id)
        assert len(active) == 1
        assert active[0].status == "LOCKED"
        locked_fact_id = active[0].id

        def fake_second(_sys, _user, **_kw):
            return ({
                "new_facts": [
                    {
                        "fact_type": "RULE_LOCK",
                        "subject_name": "地府守门人",
                        "content": "改规则:只需找 3 张,12 点截止(narrator 违反铁律产物)",
                    }
                ]
            }, {"input_tokens": 100, "output_tokens": 50})
        monkeypatch.setattr(
            "app.services.world_state_extractor.call_llm_json", fake_second,
        )
        extract_world_facts(conn, sim, scene_index=2,
                            narrative_segment="假装改规则…", agents=agents)

        # 旧 LOCKED fact 仍 status=LOCKED
        row_old = conn.execute(
            "SELECT status, superseded_by_fact_id FROM world_facts WHERE id=?",
            (locked_fact_id,),
        ).fetchone()
        assert row_old["status"] == "LOCKED", "旧 LOCKED fact 不应被覆盖"
        assert row_old["superseded_by_fact_id"] is None

        all_active = list_active_facts(conn, sim_id)
        assert len(all_active) == 2
    finally:
        conn.close()


def test_list_active_facts_filters_by_type(monkeypatch):
    """list_active_facts(fact_types=[...]) 过滤行为。"""
    _, _, sim_id, char_ids = _seed_user_project_sim(n_chars=1)
    sim = _load_sim(sim_id)
    agents = _load_chars(char_ids)

    def fake(_sys, _user, **_kw):
        return ({
            "new_facts": [
                {"fact_type": "LIFE_STATUS", "subject_name": "张凡",
                 "content": "受了轻伤"},
                {"fact_type": "LOCATION", "subject_name": "张凡",
                 "content": "在韩紫雨家"},
                {"fact_type": "EVENT_DONE", "subject_name": "",
                 "content": "找到第一张日记残页"},
            ]
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.world_state_extractor.call_llm_json", fake,
    )

    from app.services.world_state_extractor import (
        extract_world_facts, list_active_facts,
    )

    from app.db import get_connection
    conn = get_connection()
    try:
        extract_world_facts(conn, sim, scene_index=1,
                            narrative_segment="…", agents=agents)

        only_life = list_active_facts(conn, sim_id, fact_types=["LIFE_STATUS"])
        assert len(only_life) == 1
        assert only_life[0].fact_type == "LIFE_STATUS"

        multi = list_active_facts(
            conn, sim_id, fact_types=["LOCATION", "EVENT_DONE"],
        )
        assert len(multi) == 2
        assert {f.fact_type for f in multi} == {"LOCATION", "EVENT_DONE"}
    finally:
        conn.close()


def test_extract_skips_invalid_fact_types(monkeypatch):
    """LLM 输出非法 fact_type 应被忽略,不污染数据库。"""
    _, _, sim_id, char_ids = _seed_user_project_sim(n_chars=1)
    sim = _load_sim(sim_id)
    agents = _load_chars(char_ids)

    def fake(_sys, _user, **_kw):
        return ({
            "new_facts": [
                {"fact_type": "INVALID_TYPE",
                 "subject_name": "张凡", "content": "应被忽略"},
                {"fact_type": "LIFE_STATUS",
                 "subject_name": "张凡", "content": "受伤"},
                {"fact_type": "LOCATION",
                 "subject_name": "", "content": ""},  # 空 content
            ]
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.world_state_extractor.call_llm_json", fake,
    )

    from app.services.world_state_extractor import (
        extract_world_facts, list_active_facts,
    )

    from app.db import get_connection
    conn = get_connection()
    try:
        inserted, _ = extract_world_facts(conn, sim, scene_index=1,
                                           narrative_segment="…", agents=agents)
        assert inserted == 1
        active = list_active_facts(conn, sim_id)
        assert len(active) == 1
        assert active[0].fact_type == "LIFE_STATUS"
    finally:
        conn.close()


# ============================================================
# plot_tracker 测试
# ============================================================

def test_plot_tracker_introduces_new_threads(monkeypatch):
    """LLM 给出 new_threads → 落库 ACTIVE。"""
    _, _, sim_id, _ = _seed_user_project_sim(n_chars=2)
    sim = _load_sim(sim_id)

    def fake(_sys, _user, **_kw):
        return ({
            "resolved_thread_ids": [],
            "advanced_thread_ids": [],
            "new_threads": [
                {"description": "刘飞母亲被威胁倒计时", "priority": 1},
                {"description": "找到 5 张日记残页", "priority": 1},
                {"description": "调查韩紫雨真实死因", "priority": 2},
            ],
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.plot_tracker.call_llm_json", fake,
    )

    from app.services.plot_tracker import (
        track_plot_threads, list_active_threads,
    )
    from app.db import get_connection
    conn = get_connection()
    try:
        summary, _ = track_plot_threads(conn, sim, scene_index=0,
                                         narrative_segment="…")
        assert len(summary["introduced"]) == 3
        assert summary["resolved"] == []
        assert summary["advanced"] == []

        active = list_active_threads(conn, sim_id)
        assert len(active) == 3
        assert active[0].priority == 1
        assert active[-1].priority == 2
    finally:
        conn.close()


def test_plot_tracker_marks_resolved_and_advanced(monkeypatch):
    """已存在 thread 在本幕:resolved → 标 resolved_at;advanced → staleness=0。"""
    _, _, sim_id, _ = _seed_user_project_sim(n_chars=2)
    sim = _load_sim(sim_id)

    def fake_intro(_sys, _user, **_kw):
        return ({
            "resolved_thread_ids": [],
            "advanced_thread_ids": [],
            "new_threads": [
                {"description": "A 任务", "priority": 1},
                {"description": "B 任务", "priority": 2},
            ],
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.plot_tracker.call_llm_json", fake_intro,
    )
    from app.services.plot_tracker import (
        track_plot_threads, list_active_threads,
    )
    from app.db import get_connection
    conn = get_connection()
    try:
        track_plot_threads(conn, sim, scene_index=0, narrative_segment="…")
        threads = list_active_threads(conn, sim_id)
        a_id = [t.id for t in threads if "A 任务" in t.description][0]
        b_id = [t.id for t in threads if "B 任务" in t.description][0]

        def fake_step(_sys, _user, **_kw):
            return ({
                "resolved_thread_ids": [b_id],
                "advanced_thread_ids": [a_id],
                "new_threads": [],
            }, {"input_tokens": 100, "output_tokens": 50})
        monkeypatch.setattr(
            "app.services.plot_tracker.call_llm_json", fake_step,
        )
        summary, _ = track_plot_threads(conn, sim, scene_index=1,
                                         narrative_segment="…")
        assert a_id in summary["advanced"]
        assert b_id in summary["resolved"]

        active_now = list_active_threads(conn, sim_id)
        assert len(active_now) == 1
        assert active_now[0].id == a_id
        assert active_now[0].staleness == 0
    finally:
        conn.close()


def test_plot_tracker_staleness_increment_and_must_advance(monkeypatch):
    """untouched thread → staleness +1;达 STALENESS_FORCE_THRESHOLD 时 must_advance=True。"""
    _, _, sim_id, _ = _seed_user_project_sim(n_chars=2)
    sim = _load_sim(sim_id)

    def fake_intro(_sys, _user, **_kw):
        return ({
            "resolved_thread_ids": [],
            "advanced_thread_ids": [],
            "new_threads": [{"description": "X 任务", "priority": 1}],
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.plot_tracker.call_llm_json", fake_intro,
    )
    from app.services.plot_tracker import (
        track_plot_threads, list_active_threads,
    )
    from app.models.plot_thread import STALENESS_FORCE_THRESHOLD
    from app.db import get_connection
    conn = get_connection()
    try:
        track_plot_threads(conn, sim, scene_index=0, narrative_segment="…")

        def fake_untouched(_sys, _user, **_kw):
            return ({
                "resolved_thread_ids": [],
                "advanced_thread_ids": [],
                "new_threads": [],
            }, {"input_tokens": 100, "output_tokens": 50})
        monkeypatch.setattr(
            "app.services.plot_tracker.call_llm_json", fake_untouched,
        )
        for scene_idx in range(1, STALENESS_FORCE_THRESHOLD + 1):
            track_plot_threads(conn, sim, scene_index=scene_idx, narrative_segment="…")

        active = list_active_threads(conn, sim_id)
        assert len(active) == 1
        x = active[0]
        assert x.staleness >= STALENESS_FORCE_THRESHOLD
        assert x.must_advance is True
        assert "MUST_ADVANCE" in x.to_prompt_line()
    finally:
        conn.close()


# ============================================================
# 主循环集成 — scene_picker prompt 注入
# ============================================================

def test_scene_picker_prompt_includes_world_facts_and_threads(monkeypatch):
    """主循环 hook 后,scene_picker 收到的 user_input 含 world_facts + plot_threads。"""
    user_id, pid, sim_id, char_ids = _seed_user_project_sim(n_chars=3)

    # 1. 先在 DB 里塞一条 ACTIVE world_fact + 一条 ACTIVE thread
    from app.db import get_connection
    from app.services.project_service import iso_now
    fact_id = uuid.uuid4().hex
    thread_id = uuid.uuid4().hex
    now = iso_now()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO world_facts
               (id, simulation_id, scene_index, fact_type,
                subject_id, subject_name, content, status,
                superseded_by_fact_id, created_at)
               VALUES (?, ?, 0, 'RULE_LOCK', NULL, '地府守门人',
                       '找齐 5 张日记残页', 'LOCKED', NULL, ?)""",
            (fact_id, sim_id, now),
        )
        conn.execute(
            """INSERT INTO plot_threads
               (id, simulation_id, introduced_at_scene_index,
                description, resolved_at_scene_index, priority,
                staleness, created_at, updated_at)
               VALUES (?, ?, 0, '找日记主线', NULL, 1, 0, ?, ?)""",
            (thread_id, sim_id, now, now),
        )
        conn.commit()
    finally:
        conn.close()

    # 2. 截获 scene_picker 的 user_input
    captured = {"scene_picker_user_input": None}

    def fake_llm(system_prompt, user_input, **_kw):
        usage = {"input_tokens": 100, "output_tokens": 50}
        if "场景调度员" in system_prompt:
            captured["scene_picker_user_input"] = user_input
            return ({
                "scene_name": "测试场景",
                "scene_source": "llm_created",
                "time_anchor": "深夜",
                "reasoning": "mock",
            }, usage)
        # 其余 LLM 都不抽 / 不影响
        if "世界事实账本" in system_prompt:
            return ({"new_facts": []}, usage)
        if "主线追踪员" in system_prompt:
            return ({"resolved_thread_ids": [], "advanced_thread_ids": [],
                     "new_threads": []}, usage)
        if "一致性自检员" in system_prompt:
            return ({"violations": []}, usage)
        if "实体身份注册员" in system_prompt:
            return ({"new_entities": [], "alias_additions": []}, usage)
        if "原子动作提取员" in system_prompt:
            return ({"actions": []}, usage)
        if "角色情绪追踪员" in system_prompt:
            return ({"emotional_states": []}, usage)
        if "时间锚解析器" in system_prompt:
            return ({"new_offset_hours": 0.0}, usage)
        if "扮演一个角色" in system_prompt:
            return ({"reflection": "...", "dialogue": "...", "action": ""}, usage)
        if "叙述者" in system_prompt:
            return ({"narrative_segment": "x" * 200}, usage)
        raise ValueError(f"unexpected prompt: {system_prompt[:100]}")

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

    # 3. 只跑 1 幕,验证 captured input
    from app.services.agent_evolution_engine import run_evolution_simulation
    # 改 rounds_planned=1 让只跑 1 幕但触发 scene_picker
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE simulations SET rounds_planned=1 WHERE id=?", (sim_id,),
        )
        conn.commit()
    finally:
        conn.close()

    run_evolution_simulation(sim_id)

    ui = captured["scene_picker_user_input"]
    assert ui is not None, "scene_picker LLM 未被调用"
    assert "world_facts" in ui
    assert "plot_threads" in ui
    # 校验 prompt 行包含我们写入的事实/任务
    assert any("找齐 5 张日记残页" in line for line in ui["world_facts"]), \
        f"world_facts 没注入 LOCKED rule: {ui['world_facts']}"
    assert any("找日记主线" in line for line in ui["plot_threads"]), \
        f"plot_threads 没注入: {ui['plot_threads']}"
    # LOCKED 标识在 prompt 行
    assert any("LOCKED" in line for line in ui["world_facts"])


