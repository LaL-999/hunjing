"""Sprint 6.A2 M4.2(2026-05-19)— round 内同步 + 套语去重测试。

覆盖:
  - phrase_blacklist 算法:3-4 字 n-gram 统计 + freq ≥ 5 + dedup 子串 + 截断 top
  - _agent_dialogue_one_round round_so_far_witnessed 传递
  - _run_scene_dialogue round 内 visible_actions 累计
  - 主循环 narrator 调用前 forbidden_phrases 注入

设计起源:
  - 治 Gemini 评测瑕疵 4(套语复读机 — 刘飞专属套装)+ 续作"灭灯"撕裂
"""
from __future__ import annotations

import uuid

import pytest


# ============================================================
# helpers
# ============================================================

def _seed_user_project_sim_with_scenes(
    scene_narratives: list[str],
    n_chars: int = 3,
) -> tuple[str, str, dict[str, str]]:
    """造 user + project + characters + sim + N 幕已落库的 narrative。

    Returns: (sim_id, project_id, {name: char_id})
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
               VALUES (?, ?, 'M4.2 测试', 'novel', '[]', 'initial', ?, ?, '{}', 30)""",
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
                       '[]', NULL, '[]', 'done', ?, NULL, NULL,
                       0, 0, 0.0, NULL, ?, NULL, NULL, 'evolution')""",
            (sim_id, pid, user_id, len(scene_narratives), now),
        )
        # 落 simulation_scenes
        for idx, narrative in enumerate(scene_narratives):
            conn.execute(
                """INSERT INTO simulation_scenes
                   (id, simulation_id, scene_index, scene_name, scene_source,
                    time_anchor, characters_present_json, narrative_segment, created_at)
                   VALUES (?, ?, ?, ?, 'llm_created', '', '[]', ?, ?)""",
                (uuid.uuid4().hex, sim_id, idx, f"场景{idx}", narrative, now),
            )
        conn.commit()
    finally:
        conn.close()
    return sim_id, pid, name_to_id


# ============================================================
# phrase_blacklist 测试
# ============================================================

def test_phrase_blacklist_picks_high_freq_3_and_4_grams():
    """3-4 字短语 freq ≥ 5 应入清单;freq < 5 应被过滤。"""
    # 构造 6 幕 narrative,故意复读"低着头"(7 次)+"指节发白"(6 次)
    # 同时"突然之间"(只 2 次)应被过滤
    narratives = [
        "刘飞低着头,他指节发白地攥着衣角。突然之间外面起风了。",
        "刘飞又低着头,这一次他指节发白得更厉害。",
        "在角落里,刘飞低着头不敢看。",
        "莫晴雨发现刘飞低着头,他指节发白。",
        "刘飞低着头沉默良久,指节发白如纸。",
        "最后刘飞低着头说话,他指节发白,声音颤抖。突然之间天黑了。",
    ]
    sim_id, _, _ = _seed_user_project_sim_with_scenes(narratives)

    from app.services.phrase_blacklist import build_phrase_blacklist
    from app.db import get_connection
    conn = get_connection()
    try:
        blacklist = build_phrase_blacklist(conn, sim_id)
    finally:
        conn.close()

    # 提取 phrase 列表方便断言
    phrases = [p for p, _ in blacklist]
    # 校验:含"低着头"的 phrase 至少 1 个(可能是 3-gram 本身 或 4-gram 含子串
    # 如"飞低着头" / "他低着头",都算)
    assert any("低着头" in p for p in phrases), (
        f"高频套语 '低着头' 至少应被某个 phrase 代表,但 phrases={phrases}"
    )
    assert any("指节发白" in p for p in phrases), (
        f"高频套语 '指节发白' 至少应被某个 phrase 代表,但 phrases={phrases}"
    )
    # 突然之间 只出现 2 次 < MIN_FREQ_FOR_BLACKLIST(5)
    assert not any("突然之间" in p for p in phrases), (
        f"低频短语 '突然之间' 不应入清单(只 2 次),但 phrases={phrases}"
    )
    # freq 倒序
    freqs = [f for _, f in blacklist]
    assert freqs == sorted(freqs, reverse=True), "应按 freq 倒序"


def test_phrase_blacklist_empty_when_segments_too_short():
    """字数太少(< 100)→ 数据不足,返回空清单。"""
    narratives = ["短句一", "短句二"]
    sim_id, _, _ = _seed_user_project_sim_with_scenes(narratives)

    from app.services.phrase_blacklist import build_phrase_blacklist
    from app.db import get_connection
    conn = get_connection()
    try:
        blacklist = build_phrase_blacklist(conn, sim_id)
    finally:
        conn.close()
    assert blacklist == []


def test_phrase_blacklist_dedup_substrings():
    """若"低着头"和"他低着头"都高频,只保留更具体的(更长的)以代表。"""
    # 构造 9 个 segment 让"他低着头"出现 6 次("低着头"自然也至少 6 次)
    narratives = []
    for i in range(9):
        narratives.append(
            f"第{i}场。他低着头看了一会儿。后来他低着头走出去。"
            f"刘飞他低着头在旁边等。"
        )
    sim_id, _, _ = _seed_user_project_sim_with_scenes(narratives)

    from app.services.phrase_blacklist import build_phrase_blacklist
    from app.db import get_connection
    conn = get_connection()
    try:
        blacklist = build_phrase_blacklist(conn, sim_id)
    finally:
        conn.close()
    phrases = [p for p, _ in blacklist]
    # 更长的应保留 "他低着头"(可能);更短的 "低着头" 应被 dedup 掉(被更长包含)
    # 但只有当更长 phrase 也满足 freq ≥ 5 时才会触发 dedup
    if "他低着头" in phrases:
        # 若同时含子串就是 dedup 失败
        # 注:dedup 算法对"他低着头" 的子串("低着","着头","他低")会跳过
        # 但 "低着头"(3 gram,4 gram 不含)如果在 phrases 中且 "他低着头" 也在,
        # 应该被 dedup
        assert "低着头" not in phrases, (
            "dedup 失败:'低着头' 被 '他低着头' 包含,应保留更长的"
        )


# ============================================================
# round 内 visible_actions 测试
# ============================================================

def test_run_scene_dialogue_passes_round_visible_to_each_agent(monkeypatch):
    """主循环跑一幕 3 agent 顺序对话时,
       第 2 个 agent 应能在 user_input 看到第 1 个 agent 的动作;
       第 3 个能看到前 2 个的累计。
    """
    # 造 sim,记录每次 LLM 调用的 user_input
    sim_id, _, char_ids = _seed_user_project_sim_with_scenes(
        scene_narratives=[], n_chars=3,
    )
    # 修 sim 为 queued 让 run_evolution 可跑
    from app.db import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE simulations SET state='queued', current_round=0, narrative=NULL, "
            "rounds_planned=1 WHERE id=?",
            (sim_id,),
        )
        # 清空老的 simulation_scenes(我们要让 evolution 跑一幕新的)
        conn.execute("DELETE FROM simulation_scenes WHERE simulation_id=?", (sim_id,))
        conn.commit()
    finally:
        conn.close()

    captured_agent_inputs: list[dict] = []

    def fake_llm(system_prompt, user_input, **_kw):
        usage = {"input_tokens": 100, "output_tokens": 50}
        if "场景调度员" in system_prompt:
            return ({
                "scene_name": "测试场景",
                "scene_source": "llm_created",
                "time_anchor": "深夜",
                "reasoning": "mock",
            }, usage)
        if "扮演一个角色" in system_prompt:
            captured_agent_inputs.append(user_input)
            speaker = user_input["name"]
            # 让每个 agent 都输出非空 action(让 round_visible_actions 累积)
            return ({
                "reflection": f"{speaker} 想点什么",
                "dialogue": f"{speaker} 说话",
                "action": f"{speaker} 做了某事",
            }, usage)
        if "叙述者" in system_prompt:
            return ({"narrative_segment": "x" * 200}, usage)
        if "世界事实账本" in system_prompt:
            return ({"new_facts": []}, usage)
        if "主线追踪员" in system_prompt:
            return ({
                "resolved_thread_ids": [], "advanced_thread_ids": [], "new_threads": [],
            }, usage)
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

    from app.services.agent_evolution_engine import run_evolution_simulation
    run_evolution_simulation(sim_id)

    # 验证:agent_inputs 不为空(至少 3 agent × 3 round = 9 调用)
    assert len(captured_agent_inputs) >= 3, \
        f"应至少 3 个 agent 调用,实际 {len(captured_agent_inputs)}"

    # 找第 1 个 round 的 3 次 agent 调用(scene_index=0, 前 3 个)
    # 注:scene_index 字段在 user_input 中
    round0_first_round = [
        inp for inp in captured_agent_inputs[:3]
    ]
    # 第 1 个 agent:round_so_far_witnessed 应为空
    assert "round_so_far_witnessed" in round0_first_round[0], \
        "user_input 必须含 round_so_far_witnessed 字段"
    assert round0_first_round[0]["round_so_far_witnessed"] == [], \
        f"第 1 个 agent 应见空 round_so_far,实际 {round0_first_round[0]['round_so_far_witnessed']}"

    # 第 2 个 agent:应能看到第 1 个的动作
    second = round0_first_round[1]["round_so_far_witnessed"]
    assert len(second) == 1, f"第 2 个 agent 应见 1 条 round_so_far,实际 {len(second)}"
    assert second[0]["speaker"] == round0_first_round[0]["name"], (
        "第 2 个 agent 看到的 speaker 应等于第 1 个 agent 的 name"
    )

    # 第 3 个 agent:应能看到前 2 个的动作
    third = round0_first_round[2]["round_so_far_witnessed"]
    assert len(third) == 2, f"第 3 个 agent 应见 2 条 round_so_far,实际 {len(third)}"


def test_round_visible_resets_between_rounds(monkeypatch):
    """新 round 开始时,round_visible_actions 应重置(不携带上 round 的)。"""
    sim_id, _, _ = _seed_user_project_sim_with_scenes(
        scene_narratives=[], n_chars=3,
    )
    from app.db import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE simulations SET state='queued', current_round=0, narrative=NULL, "
            "rounds_planned=1 WHERE id=?",
            (sim_id,),
        )
        conn.execute("DELETE FROM simulation_scenes WHERE simulation_id=?", (sim_id,))
        conn.commit()
    finally:
        conn.close()

    captured: list[dict] = []

    def fake_llm(system_prompt, user_input, **_kw):
        usage = {"input_tokens": 100, "output_tokens": 50}
        if "场景调度员" in system_prompt:
            return ({"scene_name": "S", "scene_source": "llm_created",
                     "time_anchor": "", "reasoning": ""}, usage)
        if "扮演一个角色" in system_prompt:
            captured.append({
                "name": user_input["name"],
                "round_so_far_len": len(user_input["round_so_far_witnessed"]),
            })
            return ({"reflection": "x", "dialogue": "d", "action": "a"}, usage)
        if "叙述者" in system_prompt:
            return ({"narrative_segment": "x" * 200}, usage)
        if "世界事实账本" in system_prompt:
            return ({"new_facts": []}, usage)
        if "主线追踪员" in system_prompt:
            return ({"resolved_thread_ids": [], "advanced_thread_ids": [],
                     "new_threads": []}, usage)
        if "一致性自检员" in system_prompt:
            return ({"violations": []}, usage)
        raise ValueError(system_prompt[:50])

    monkeypatch.setattr(
        "app.services.agent_evolution_engine.call_llm_json", fake_llm,
    )
    monkeypatch.setattr(
        "app.services.world_state_extractor.call_llm_json", fake_llm,
    )
    monkeypatch.setattr(
        "app.services.plot_tracker.call_llm_json", fake_llm,
    )
    monkeypatch.setattr(
        "app.services.consistency_checker.call_llm_json", fake_llm,
    )

    from app.services.agent_evolution_engine import run_evolution_simulation
    run_evolution_simulation(sim_id)

    # 3 agents × DIALOGUE_ROUNDS_PER_SCENE = 9 总调用(1 幕)
    from app.services.agent_evolution_engine import DIALOGUE_ROUNDS_PER_SCENE
    assert len(captured) == 3 * DIALOGUE_ROUNDS_PER_SCENE

    # 每个 round 的第 1 个 agent(captured[0], captured[3], captured[6])应 round_so_far 长度=0
    for round_idx in range(DIALOGUE_ROUNDS_PER_SCENE):
        first_in_round = captured[round_idx * 3]
        assert first_in_round["round_so_far_len"] == 0, (
            f"round {round_idx} 第 1 个 agent({first_in_round['name']})"
            f"应见空 round_so_far,实际长度 {first_in_round['round_so_far_len']}"
        )


# ============================================================
# narrator forbidden_phrases 注入测试
# ============================================================

def test_narrator_receives_forbidden_phrases_after_repetitive_scenes(monkeypatch):
    """跑 N 幕复读后,narrator 第 N+1 幕的 user_input 应含非空 forbidden_phrases。"""
    # 让前几幕的 narrative 复读 "低着头" 和 "指节发白"
    # 用 narrator mock 输出相同套语,phrase_blacklist 会统计到
    sim_id, _, _ = _seed_user_project_sim_with_scenes(
        scene_narratives=[], n_chars=3,
    )
    from app.db import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE simulations SET state='queued', current_round=0, narrative=NULL, "
            "rounds_planned=6 WHERE id=?",  # 6 幕,让 phrase 统计累积
            (sim_id,),
        )
        conn.execute("DELETE FROM simulation_scenes WHERE simulation_id=?", (sim_id,))
        conn.commit()
    finally:
        conn.close()

    narrator_inputs: list[dict] = []
    narrator_call_count = [0]

    def fake_llm(system_prompt, user_input, **_kw):
        usage = {"input_tokens": 100, "output_tokens": 50}
        if "场景调度员" in system_prompt:
            return ({"scene_name": f"场景{user_input['scene_index']}",
                     "scene_source": "llm_created",
                     "time_anchor": "", "reasoning": ""}, usage)
        if "扮演一个角色" in system_prompt:
            return ({"reflection": "x", "dialogue": "d", "action": "a"}, usage)
        if "叙述者" in system_prompt:
            narrator_call_count[0] += 1
            narrator_inputs.append(user_input)
            # narrator 故意复读"低着头""指节发白"(填到 freq>=5)
            return ({"narrative_segment":
                     "刘飞低着头,指节发白。他低着头沉默,指节发白如纸。"
                     "他又低着头,指节发白得厉害。低着头沉默指节发白。" * 2}, usage)
        if "世界事实账本" in system_prompt:
            return ({"new_facts": []}, usage)
        if "主线追踪员" in system_prompt:
            return ({"resolved_thread_ids": [], "advanced_thread_ids": [],
                     "new_threads": []}, usage)
        if "一致性自检员" in system_prompt:
            return ({"violations": []}, usage)
        raise ValueError(system_prompt[:50])

    monkeypatch.setattr(
        "app.services.agent_evolution_engine.call_llm_json", fake_llm,
    )
    monkeypatch.setattr(
        "app.services.world_state_extractor.call_llm_json", fake_llm,
    )
    monkeypatch.setattr(
        "app.services.plot_tracker.call_llm_json", fake_llm,
    )
    monkeypatch.setattr(
        "app.services.consistency_checker.call_llm_json", fake_llm,
    )

    from app.services.agent_evolution_engine import run_evolution_simulation
    run_evolution_simulation(sim_id)

    # 至少跑 3 幕,后面的幕 forbidden_phrases 应非空(高频套语累积)
    assert narrator_call_count[0] >= 2, "至少跑 2 幕 narrator"

    # 第 1 幕的 forbidden_phrases 应为空(没历史)
    assert "forbidden_phrases" in narrator_inputs[0], (
        "narrator user_input 必须含 forbidden_phrases 字段"
    )
    assert narrator_inputs[0]["forbidden_phrases"] == [], (
        "第 1 幕没历史,forbidden_phrases 应空"
    )

    # 后面的幕应有非空 forbidden_phrases(套语已累积)
    later_phrases = narrator_inputs[-1]["forbidden_phrases"]
    assert len(later_phrases) > 0, (
        f"后续幕 forbidden_phrases 应非空(历史复读),实际 {later_phrases}"
    )
    # 应含"低着头"或"指节发白"
    phrase_strings = [p["phrase"] for p in later_phrases]
    has_target = (
        any("低着头" in p for p in phrase_strings)
        or any("指节发白" in p for p in phrase_strings)
    )
    assert has_target, (
        f"forbidden_phrases 应含'低着头'或'指节发白',实际 {phrase_strings}"
    )
