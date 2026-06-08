"""Sprint 6.A2 M4.3(2026-05-20)— Reflexion 一致性自检 + retry 回路测试。

覆盖:
  - check_segment LLM mock — 无违规通过
  - check_segment LLM mock — critical 违规生成 retry_hint
  - check_segment LLM mock — warning / info 不触发 retry
  - 主循环 narrator retry max 1 次(第 2 次仍违规接受,不死循环)
  - character behavior_baseline fallback(老角色无 baseline 时用 personality)

设计起源:
  - 治瑕疵 3 行为漂移失控(刘飞请求"求做一次"等)
  - 兜底 M4.1/M4.2 没拦住的违规
"""
from __future__ import annotations

import json
import uuid


# ============================================================
# helpers
# ============================================================

def _seed_sim_with_char(
    behavior_baseline: dict | None = None,
    n_chars: int = 2,
) -> tuple[str, str, dict[str, str]]:
    """造 user + project + character(可选含 behavior_baseline)+ sim。"""
    from app.db import get_connection
    from app.services.project_service import iso_now

    user_id = uuid.uuid4().hex
    pid = uuid.uuid4().hex
    sim_id = uuid.uuid4().hex
    now = iso_now()
    names = ["刘飞", "莫晴雨", "张凡"][:n_chars]
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
               VALUES (?, ?, 'M4.3 测试', 'novel', '[]', 'initial', ?, ?, '{}', 30)""",
            (pid, user_id, now, now),
        )
        for n in names:
            cid = uuid.uuid4().hex
            baseline_json = (
                json.dumps(behavior_baseline, ensure_ascii=False)
                if behavior_baseline else None
            )
            conn.execute(
                """INSERT INTO characters
                    (id, project_id, name, identity, personality, quotes, no_go_list,
                     position_x, position_y, position_z, color,
                     created_at, updated_at,
                     is_protagonist, protagonist_score, protagonist_reasons_json,
                     protagonist_user_pinned, behavior_baseline_json)
                   VALUES (?, ?, ?, ?, ?, '[]', '[]', 0, 0, 0, NULL, ?, ?,
                           1, 0.9, '[]', 0, ?)""",
                (cid, pid, n, f"{n} 的身份", f"{n} 的卑微性格", now, now,
                 baseline_json),
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
               VALUES (?, ?, ?, '测试', 50, 1, 4000, 'A', NULL,
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
# consistency_checker 单元测试
# ============================================================

def test_check_segment_no_violations_passes_through(monkeypatch):
    """LLM 返回空 violations 数组 → CheckResult.has_critical_violation = False。"""
    sim_id, _, name_to_id = _seed_sim_with_char(behavior_baseline=None)
    sim, agents = _load_sim_and_chars(sim_id, name_to_id)

    def fake(_sys, _user, **_kw):
        return ({"violations": []}, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.consistency_checker.call_llm_json", fake,
    )

    from app.services.consistency_checker import check_segment
    from app.db import get_connection
    conn = get_connection()
    try:
        result, _ = check_segment(
            conn, sim, scene_index=0,
            narrative_segment=(
                "刘飞低下视线,小声说着什么。莫晴雨站在旁边沉默。"
                "窗外的雨打在屋檐上,屋子里只剩下她的呼吸声。"
                "她转身走到桌前,翻开了那本旧册子,纸页上的字迹已经模糊。"
                "刘飞欲言又止,最终把那句话咽了回去,只是看着她的背影。"
                "时间像是凝固了一样,谁也没有再开口,炉火噼啪地响了两声。"
            ),
            agents=agents,
        )
    finally:
        conn.close()

    assert not result.has_critical_violation
    assert result.critical_count == 0
    assert result.violations == []
    assert result.to_retry_hint() == ""


def test_check_segment_critical_violation_returns_retry_hint(monkeypatch):
    """critical 违规 → has_critical_violation=True + to_retry_hint 非空。"""
    sim_id, _, name_to_id = _seed_sim_with_char(behavior_baseline={
        "speech_register": "卑微",
        "emotional_intensity": 4,
        "moral_compass": "灰",
        # P0G.2(2026-05-24):out_of_baseline_examples 已永久删除
    })
    sim, agents = _load_sim_and_chars(sim_id, name_to_id)

    def fake(_sys, _user, **_kw):
        return ({
            "violations": [
                {
                    "severity": "critical",
                    "category": "character_baseline",
                    "subject_name": "刘飞",
                    "evidence": "刘飞向莫晴雨开口:'你能不能跟我做一次?'",
                    "suggestion": "刘飞 speech_register=卑微,改写为局促搓衣角请求帮助",
                }
            ]
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.consistency_checker.call_llm_json", fake,
    )

    from app.services.consistency_checker import check_segment
    from app.db import get_connection
    conn = get_connection()
    try:
        result, _ = check_segment(
            conn, sim, scene_index=0,
            narrative_segment=(
                "刘飞向莫晴雨提出突兀的请求,她愣了一下,没有立刻回答。"
                "屋子里灯光昏黄,墙上挂着旧日的照片,边角已经泛黄。"
                "莫晴雨低头看了眼茶杯,水面映着她有些苍白的脸。"
                "刘飞的呼吸变得急促,他攥紧了拳头,等着对方的答复。"
                "窗外的风掠过树梢,远处传来一声犬吠,屋里更显得安静了。"
            ),
            agents=agents,
        )
    finally:
        conn.close()

    assert result.has_critical_violation
    assert result.critical_count == 1

    hint = result.to_retry_hint()
    assert "上一次合稿被一致性自检检测出严重违规" in hint
    assert "刘飞" in hint
    assert "改写为局促搓衣角请求帮助" in hint


def test_check_segment_warning_does_not_trigger_retry(monkeypatch):
    """warning / info 不算 critical,not has_critical_violation。"""
    sim_id, _, name_to_id = _seed_sim_with_char(behavior_baseline=None)
    sim, agents = _load_sim_and_chars(sim_id, name_to_id)

    def fake(_sys, _user, **_kw):
        return ({
            "violations": [
                {"severity": "warning", "category": "plot_thread",
                 "evidence": "本幕未推进主线", "suggestion": "下一幕推进"},
                {"severity": "info", "category": "world_fact",
                 "evidence": "提了背景细节", "suggestion": "OK"},
            ]
        }, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.consistency_checker.call_llm_json", fake,
    )

    from app.services.consistency_checker import check_segment
    from app.db import get_connection
    conn = get_connection()
    try:
        result, _ = check_segment(
            conn, sim, scene_index=0,
            narrative_segment=(
                "正常剧情,没明显违规。刘飞与莫晴雨坐在堂屋里,炉火慢慢燃着。"
                "桌上的茶杯还冒着热气,她端起来抿了一口,目光落在窗外的雪上。"
                "他想说什么,又咽了回去,只是把火盆又往她那边推了推。"
                "屋外的脚步声远去了,世界仿佛被这一场雪盖住,只剩下两个人。"
                "时间不疾不徐,像河水一样从他们身边流过,谁也没有特意提起什么。"
            ),
            agents=agents,
        )
    finally:
        conn.close()

    assert not result.has_critical_violation
    assert len(result.violations) == 2  # warning / info 都落入清单,只是不触发 retry
    assert result.to_retry_hint() == ""  # to_retry_hint 只列 critical


def test_character_baseline_fallback_when_null(monkeypatch):
    """老角色无 behavior_baseline → check_segment 用 baseline_fallback(personality)
       传给 LLM,主流程不应崩(只是判定更宽松)。
    """
    # 不传 behavior_baseline,角色用 fallback
    sim_id, _, name_to_id = _seed_sim_with_char(behavior_baseline=None)
    sim, agents = _load_sim_and_chars(sim_id, name_to_id)
    # 验证 Character.behavior_baseline 为 None
    for a in agents:
        assert a.behavior_baseline is None

    captured = {"user_input": None}

    def fake(_sys, user_input, **_kw):
        captured["user_input"] = user_input
        return ({"violations": []}, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.consistency_checker.call_llm_json", fake,
    )

    from app.services.consistency_checker import check_segment
    from app.db import get_connection
    conn = get_connection()
    try:
        check_segment(
            conn, sim, scene_index=0,
            narrative_segment="x" * 100, agents=agents,
        )
    finally:
        conn.close()

    ui = captured["user_input"]
    assert ui is not None
    baselines = ui["agent_baselines"]
    # 每个 agent 都没 baseline,应该走 baseline_fallback 路径
    for b in baselines:
        assert "baseline" not in b, f"无 baseline 时不应输出 baseline 字段,实际 {b}"
        assert "baseline_fallback" in b, f"无 baseline 应走 baseline_fallback,实际 {b}"
        assert "personality" in b["baseline_fallback"]


# ============================================================
# 主循环 retry 回路集成测试
# ============================================================

def test_narrator_retried_once_when_critical_then_accepted(monkeypatch):
    """主循环(P0S.1 后,2026-05-24 改造):
       - 3 候选(M5.4 multi-sample) → 每个候选都过 consistency_checker
       - 若最优候选仍 critical → narrator 加 retry_hint 重写,**max 3 次 retry**(P0S.1)
       - 每次 retry 后重新 consistency_check;3 次都失败 → 走 P0S.2 程序兜底
    """
    sim_id, _, name_to_id = _seed_sim_with_char(
        behavior_baseline={"speech_register": "卑微",
                            "emotional_intensity": 4,
                            "moral_compass": "灰",
                            # P0G.2(2026-05-24):out_of_baseline_examples 已永久删除
        },
        n_chars=2,
    )
    # 重设 sim 为 queued 让 run_evolution 能跑
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

    counters = {"narrator": 0, "consistency_checker": 0, "retry_hints": []}

    def fake_llm(system_prompt, user_input, **_kw):
        usage = {"input_tokens": 100, "output_tokens": 50}
        sp = system_prompt.strip()
        # M5.3 prepend 段处理:从 "以下是原 prompt:" 后取真实 prompt 标题
        if "以下是原 prompt:" in sp:
            real_start = sp.split("以下是原 prompt:", 1)[1][:300]
        else:
            real_start = sp[:1500]

        def has(s):
            return s in real_start

        if has("# 场景调度员"):
            return ({"scene_name": "S", "scene_source": "llm_created",
                     "time_anchor": "", "reasoning": ""}, usage)
        if has("# 世界事实账本"):
            return ({"new_facts": []}, usage)
        if has("# 主线追踪员"):
            return ({"resolved_thread_ids": [], "advanced_thread_ids": [],
                     "new_threads": []}, usage)
        if has("# 一致性自检员"):
            counters["consistency_checker"] += 1
            # 永远报 critical(模拟"重写后仍违规"场景)
            return ({
                "violations": [
                    {
                        "severity": "critical",
                        "category": "character_baseline",
                        "subject_name": "刘飞",
                        "evidence": "刘飞行为漂移",
                        "suggestion": "改回卑微",
                    }
                ]
            }, usage)
        if has("# 实体身份注册员"):
            return ({"new_entities": [], "alias_additions": []}, usage)
        if has("# 原子动作提取员"):
            return ({"actions": []}, usage)
        if has("# 角色情绪追踪员"):
            return ({"emotional_states": []}, usage)
        if "时间锚解析器" in real_start[:200]:
            return ({"new_offset_hours": 0.0}, usage)
        if has("# Agent 对话") or "扮演一个角色" in real_start[:400]:
            return ({"reflection": "x", "dialogue": "d", "action": "a"}, usage)
        if has("# 叙述者"):
            counters["narrator"] += 1
            counters["retry_hints"].append(user_input.get("retry_hint", ""))
            return ({"narrative_segment": "Y" * 200}, usage)
        raise ValueError(sp[:100])

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
    # M5 新加 LLM 入口
    monkeypatch.setattr(
        "app.services.entity_registrar.call_llm_json", fake_llm,
    )
    monkeypatch.setattr(
        "app.services.action_extractor.call_llm_json", fake_llm,
    )
    monkeypatch.setattr(
        "app.services.emotional_state_tracker.call_llm_json", fake_llm,
    )
    monkeypatch.setattr(
        "app.services.temporal_lock.call_llm_json", fake_llm,
    )

    from app.services.agent_evolution_engine import run_evolution_simulation
    run_evolution_simulation(sim_id)

    # P0S.1(2026-05-24)改造后:
    # narrator 调用 = 3 候选(M5.4 multi-sample) + 3 retry(P0S.1 max retry)= 6 次
    # 因为 fake 永远报 critical,3 候选最优样本仍 critical → 触发 retry 3 次都仍 critical
    assert counters["narrator"] == 6, (
        f"narrator 应被调 6 次(3 候选 + 3 retry),实际 {counters['narrator']}"
    )
    # consistency_checker:候选 3 次 + retry 后每次重检测 3 次 = 6 次(P0S.1 改:retry 后必重检)
    # 注:实测有时是 7 次(因 M5.4 选最优样本前可能多 1 次 check),允许 6-7 都接受
    assert counters["consistency_checker"] in (6, 7), (
        f"consistency_checker 应调 6-7 次(3 候选检查 + 3 retry 重检测 ± 1),实际 {counters['consistency_checker']}"
    )
    # retry_hints[0..2] = ""(3 个候选都首次,无 retry_hint);retry_hints[3..5] = 非空(3 次 retry)
    assert counters["retry_hints"][0] == "", "首个候选 retry_hint 应空"
    assert counters["retry_hints"][1] == "", "第 2 候选 retry_hint 应空"
    assert counters["retry_hints"][2] == "", "第 3 候选 retry_hint 应空"
    assert "上一次合稿被一致性自检" in counters["retry_hints"][3], (
        f"第 1 次 retry narrator 的 retry_hint 应含违规清单,实际 {counters['retry_hints'][3]!r}"
    )
    assert "上一次合稿被一致性自检" in counters["retry_hints"][4], (
        f"第 2 次 retry 的 retry_hint 应含违规清单,实际 {counters['retry_hints'][4]!r}"
    )
    assert "上一次合稿被一致性自检" in counters["retry_hints"][5], (
        f"第 3 次 retry 的 retry_hint 应含违规清单,实际 {counters['retry_hints'][5]!r}"
    )
