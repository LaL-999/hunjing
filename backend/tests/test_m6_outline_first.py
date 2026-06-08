"""Sprint 6.A2 M6(2026-05-20)— Outline-First 长篇生成测试。

覆盖:
  - outline_generator:LLM 生成完整 outline 落库 + 字段校验
  - outline_editor:编辑 scene / global / approve 状态机
  - 主循环按 outline 跑:scene_picker 走 outline / location 锁死
  - 失败降级:outline LLM 失败 → state='failed'
"""
from __future__ import annotations

import json
import uuid

import pytest


# ============================================================
# helpers
# ============================================================

def _seed_sim(use_outline_first: int = 1, n_chars: int = 3) -> tuple[str, str, dict]:
    """造 user/project/chars/sim — sim 默认 use_outline_first=1 + mode='evolution'。"""
    from app.db import get_connection
    from app.services.project_service import iso_now

    user_id = uuid.uuid4().hex
    pid = uuid.uuid4().hex
    sim_id = uuid.uuid4().hex
    now = iso_now()
    names = ["张凡", "班长", "刘飞", "莫晴雨"][:n_chars]
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
               VALUES (?, ?, 'M6 测试', 'novel', '[]', 'initial', ?, ?, '{}', 30)""",
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
                 created_at, started_at, completed_at, mode, use_outline_first)
               VALUES (?, ?, ?, '三人深夜进入韩紫雨家找线索', 50, 3, 4000, 'A', NULL,
                       '[]', NULL, '[]', 'queued', 0, NULL, NULL,
                       0, 0, 0.0, NULL, ?, NULL, NULL, 'evolution', ?)""",
            (sim_id, pid, user_id, now, use_outline_first),
        )
        conn.commit()
    finally:
        conn.close()
    return user_id, sim_id, name_to_id


# ============================================================
# outline_generator 测试
# ============================================================

def test_outline_generator_creates_outline_and_scenes(monkeypatch):
    """LLM 返完整 outline → 落 simulation_outlines + N 个 outline_scenes。

    M6-fix1:outline_generator 改用 call_llm_text(拿 raw 字符串),
    测试 mock 返 JSON 字符串。
    """
    _, sim_id, char_ids = _seed_sim()
    c1 = list(char_ids.values())[0]
    c2 = list(char_ids.values())[1]

    outline_dict = {
        "global_theme": "校园悬疑 死亡游戏",
        "global_arc": "起:夜探韩紫雨家。承:发现照片揭开秘密。合:解救残魂。",
        "scenes": [
            {
                "scene_index": 0,
                "scene_summary": "张凡和班长深夜进韩紫雨家找到照片",
                "scene_purpose": "引入主任务",
                "location": "韩紫雨家卧室",
                "time_anchor": "深夜",
                "characters_present": [c1, c2],
                "key_events": [
                    "张凡进入主卧",
                    "张凡找到银色相框",
                    "确认照片人物是韩紫雨",
                ],
                "key_props": [{
                    "name": "红裙照片",
                    "action": "introduced",
                    "properties": {
                        "颜色": "银色相框",
                        "背面文字": "对不起,我没能逃出去",
                    },
                }],
                "transition_from_last": "sim 起点状态",
            },
            {
                "scene_index": 1,
                "scene_summary": "三人比对照片刘飞情绪崩溃",
                "scene_purpose": "情绪转折",
                "location": "教室",
                "time_anchor": "次日清晨",
                "characters_present": [c1, c2],
                "key_events": ["照片分享", "刘飞崩溃"],
                "key_props": [],
                "transition_from_last": "三人撤离后次日清晨到教室",
            },
        ],
    }
    raw_json = json.dumps(outline_dict, ensure_ascii=False)

    def fake_llm_text(_sys, _user, **_kw):
        return (raw_json, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.outline_generator.call_llm_text", fake_llm_text,
    )

    from app.services.outline_generator import (
        create_outline_draft, get_outline_with_scenes,
    )
    from app.db import get_connection
    conn = get_connection()
    try:
        outline_id, _ = create_outline_draft(conn, sim_id)
        assert outline_id

        outline, scenes = get_outline_with_scenes(conn, sim_id)
        assert outline.state == "awaiting_user"
        assert outline.global_theme == "校园悬疑 死亡游戏"
        assert "起承转合" in outline.global_arc or "起:" in outline.global_arc
        assert len(scenes) == 2
        assert scenes[0].scene_index == 0
        assert scenes[0].location == "韩紫雨家卧室"
        assert len(scenes[0].key_events) == 3
        assert len(scenes[0].key_props) == 1
        # key_props 属性值锁定:背面文字必须保留
        prop = scenes[0].key_props[0]
        assert prop["name"] == "红裙照片"
        assert prop["properties"]["背面文字"] == "对不起,我没能逃出去"
    finally:
        conn.close()


def test_continue_outline_generation_appends_remaining_scenes(monkeypatch):
    """M6-fix3:partial outline(23/28 幕)→ 续生成补 5 幕 → 28 幕完整。"""
    _, sim_id, char_ids = _seed_sim()
    c1 = list(char_ids.values())[0]
    c2 = list(char_ids.values())[1]

    # 第 1 步:首次生成 — fake LLM 只返 3 幕(模拟被截断)
    initial_dict = {
        "global_theme": "校园悬疑",
        "global_arc": "起承转合走向",
        "scenes": [
            {
                "scene_index": i,
                "scene_summary": f"第 {i+1} 幕概要",
                "scene_purpose": "推进",
                "location": f"场景{i}",
                "time_anchor": "深夜",
                "characters_present": [c1, c2],
                "key_events": [f"事件 {i}"],
                "key_props": [],
                "transition_from_last": f"承接 {i}",
            }
            for i in range(3)
        ],
    }

    def fake_initial(_sys, _user, **_kw):
        return (json.dumps(initial_dict, ensure_ascii=False),
                {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.outline_generator.call_llm_text", fake_initial,
    )

    # 手动把 sim.rounds_planned 改成 5 让 outline 期望 5 幕 (但首次只返 3)
    from app.db import get_connection
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE simulations SET rounds_planned=5 WHERE id=?", (sim_id,),
        )
        conn.commit()
    finally:
        conn.close()

    from app.services.outline_generator import (
        create_outline_draft, get_outline_with_scenes,
        continue_outline_generation,
    )
    conn = get_connection()
    try:
        outline_id, _ = create_outline_draft(conn, sim_id)
        outline, scenes = get_outline_with_scenes(conn, sim_id)
        # 首次:3 幕(LLM 只返 3,但 outline 主表 total_scenes_planned=3 — 因为
        # 当前实现以实际抽出数为 total。但我们要测续生成,所以人工把 total 改回 5)
        assert len(scenes) == 3
        # 人工把 total_scenes_planned 改回 5(模拟被截断 5 -> 3)
        conn.execute(
            "UPDATE simulation_outlines SET total_scenes_planned=5, "
            "error_message=? WHERE id=?",
            ("⚠️ LLM 输出被截断", outline_id),
        )
        conn.commit()
    finally:
        conn.close()

    # 第 2 步:续生成 — fake LLM 返 2 幕(第 3-4 幕)
    continue_dict = {
        "scenes": [
            {
                "scene_index": 3,
                "scene_summary": "第 4 幕概要(续)",
                "scene_purpose": "推进",
                "location": "场景3",
                "time_anchor": "次日",
                "characters_present": [c1, c2],
                "key_events": ["事件 3"],
                "key_props": [],
                "transition_from_last": "承接 3",
            },
            {
                "scene_index": 4,
                "scene_summary": "第 5 幕概要(续完)",
                "scene_purpose": "收束",
                "location": "场景4",
                "time_anchor": "深夜",
                "characters_present": [c1, c2],
                "key_events": ["事件 4"],
                "key_props": [],
                "transition_from_last": "承接 4",
            },
        ],
    }

    def fake_continue(_sys, _user, **_kw):
        return (json.dumps(continue_dict, ensure_ascii=False),
                {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.outline_generator.call_llm_text", fake_continue,
    )

    conn = get_connection()
    try:
        inserted, _, fully_done = continue_outline_generation(conn, sim_id)
        assert inserted == 2
        assert fully_done is True

        # 校验 5 幕完整
        outline, scenes = get_outline_with_scenes(conn, sim_id)
        assert len(scenes) == 5
        assert outline.error_message is None  # 完整后 error_message 清空
        # scene_index 严格 0..4
        for i, s in enumerate(scenes):
            assert s.scene_index == i
    finally:
        conn.close()


def test_continue_outline_rejects_already_complete(monkeypatch):
    """outline 已完整(scenes.length == total)→ 续生成抛 ValueError。"""
    _, sim_id, char_ids = _seed_sim()
    _seed_outline_via_llm(monkeypatch, sim_id, char_ids)

    # outline 现有 2 幕,total=2(完整)
    from app.services.outline_generator import continue_outline_generation
    from app.db import get_connection
    conn = get_connection()
    try:
        with pytest.raises(ValueError, match="已有"):
            continue_outline_generation(conn, sim_id)
    finally:
        conn.close()


def test_outline_generator_partial_extract_on_truncated_llm_output(monkeypatch):
    """M6-fix1:LLM 输出被截断时,partial_extract_outline 应抽出已完成的 scenes。

    模拟场景:LLM 给 4 幕的 outline,但 raw 在第 4 幕中间被截断 → 应只入库前 3 幕。
    """
    _, sim_id, char_ids = _seed_sim()
    c1 = list(char_ids.values())[0]
    c2 = list(char_ids.values())[1]

    # 完整 outline 是 4 幕;故意在第 4 幕中间截断 raw
    full_outline = {
        "global_theme": "主题:校园悬疑",
        "global_arc": "起承转合走向描述",
        "scenes": [
            {
                "scene_index": i,
                "scene_summary": f"第 {i+1} 幕概要内容",
                "scene_purpose": "推进主线",
                "location": f"场景{i}",
                "time_anchor": "深夜",
                "characters_present": [c1, c2],
                "key_events": [f"事件 A {i}", f"事件 B {i}"],
                "key_props": [],
                "transition_from_last": f"连接 {i}",
            }
            for i in range(4)
        ],
    }
    raw_full = json.dumps(full_outline, ensure_ascii=False)
    # 找到第 4 幕的位置,在中间截断
    truncate_at = raw_full.find('"scene_index": 3')
    assert truncate_at > 0
    raw_truncated = raw_full[:truncate_at + 50]  # 截在第 4 幕开头之后

    def fake_text(_sys, _user, **_kw):
        return (raw_truncated, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.outline_generator.call_llm_text", fake_text,
    )

    from app.services.outline_generator import (
        create_outline_draft, get_outline_with_scenes,
    )
    from app.db import get_connection
    conn = get_connection()
    try:
        outline_id, _ = create_outline_draft(conn, sim_id)
        outline, scenes = get_outline_with_scenes(conn, sim_id)
        # 应抽出前 3 幕(第 4 幕被截断未完整)
        assert len(scenes) == 3
        assert outline.state == "awaiting_user"
        # error_message 应附 partial warning
        assert outline.error_message and "截断" in outline.error_message
        # global_theme 和前 3 幕的内容仍完整
        assert outline.global_theme == "主题:校园悬疑"
        for i, s in enumerate(scenes):
            assert s.scene_index == i
            assert f"第 {i+1} 幕" in s.scene_summary
    finally:
        conn.close()


def test_outline_generator_marks_failed_on_llm_error(monkeypatch):
    """LLM 调用抛异常 → outline.state='failed' + error_message 落库。

    M6-fix1:call_llm_text 抛异常时 service 直接 mark_failed。
    """
    _, sim_id, _ = _seed_sim()

    def fake_fail(_sys, _user, **_kw):
        raise RuntimeError("DeepSeek timeout")
    monkeypatch.setattr(
        "app.services.outline_generator.call_llm_text", fake_fail,
    )

    from app.services.outline_generator import create_outline_draft
    from app.db import get_connection
    conn = get_connection()
    try:
        outline_id, _ = create_outline_draft(conn, sim_id)
        # 查 state
        row = conn.execute(
            "SELECT state, error_message FROM simulation_outlines WHERE id=?",
            (outline_id,),
        ).fetchone()
        assert row["state"] == "failed"
        assert "DeepSeek" in (row["error_message"] or "")
    finally:
        conn.close()


# ============================================================
# outline_editor 测试
# ============================================================

def test_outline_editor_update_scene_marks_user_edited(monkeypatch):
    """编辑某 outline_scene 字段 → user_edited=1 + updated_at 更新。"""
    user_id, sim_id, char_ids = _seed_sim()
    _seed_outline_via_llm(monkeypatch, sim_id, char_ids)

    from app.services.outline_editor import update_scene_fields
    from app.services.outline_generator import get_outline_with_scenes
    from app.db import get_connection
    conn = get_connection()
    try:
        _, scenes = get_outline_with_scenes(conn, sim_id)
        first_scene = scenes[0]
        ok = update_scene_fields(
            conn, first_scene.id,
            sim_id_for_auth=sim_id,
            user_id_for_auth=user_id,
            scene_summary="(用户改的概要)张凡和班长进韩紫雨家",
            location="韩紫雨家书房",
        )
        assert ok

        # 重新拉
        _, scenes_after = get_outline_with_scenes(conn, sim_id)
        edited = next(s for s in scenes_after if s.id == first_scene.id)
        assert edited.scene_summary == "(用户改的概要)张凡和班长进韩紫雨家"
        assert edited.location == "韩紫雨家书房"
        assert edited.user_edited is True
    finally:
        conn.close()


def test_outline_editor_blocks_update_after_approved(monkeypatch):
    """outline 批准后再编辑 → OutlineStateMismatch。"""
    user_id, sim_id, char_ids = _seed_sim()
    _seed_outline_via_llm(monkeypatch, sim_id, char_ids)

    from app.services.outline_editor import (
        OutlineStateMismatch, approve_outline, update_scene_fields,
    )
    from app.services.outline_generator import get_outline_with_scenes
    from app.db import get_connection
    conn = get_connection()
    try:
        _, scenes = get_outline_with_scenes(conn, sim_id)
        # 先批准
        approve_outline(conn, sim_id, user_id)

        # 再试编辑 → 应抛 OutlineStateMismatch
        with pytest.raises(OutlineStateMismatch):
            update_scene_fields(
                conn, scenes[0].id,
                sim_id_for_auth=sim_id,
                user_id_for_auth=user_id,
                location="不允许改",
            )
    finally:
        conn.close()


def test_approve_outline_sets_state_and_returns_id(monkeypatch):
    """approve_outline 把 state→'approved' + user_approved_at 非空。"""
    user_id, sim_id, char_ids = _seed_sim()
    _seed_outline_via_llm(monkeypatch, sim_id, char_ids)

    from app.services.outline_editor import approve_outline
    from app.db import get_connection
    conn = get_connection()
    try:
        outline_id = approve_outline(conn, sim_id, user_id)
        assert outline_id

        row = conn.execute(
            "SELECT state, user_approved_at FROM simulation_outlines WHERE id=?",
            (outline_id,),
        ).fetchone()
        assert row["state"] == "approved"
        assert row["user_approved_at"]
    finally:
        conn.close()


# ============================================================
# 主循环按 outline 跑端到端测试
# ============================================================

def test_run_evolution_uses_outline_scenes_when_present(monkeypatch):
    """sim.use_outline_first=1 + outline state=approved →
    主循环每幕从 outline_scenes 读 location/agents,**不调** scene_picker LLM。
    """
    user_id, sim_id, char_ids = _seed_sim(n_chars=3)
    _seed_outline_via_llm(monkeypatch, sim_id, char_ids)

    # 批准 outline
    from app.services.outline_editor import approve_outline
    from app.db import get_connection
    conn = get_connection()
    try:
        approve_outline(conn, sim_id, user_id)
    finally:
        conn.close()

    # 现在 mock 全部 LLM,但 scene_picker 应该**不被调到**
    counts = {"scene_picker": 0, "narrator": 0, "agent_dialogue": 0}

    def fake_llm(system_prompt, user_input, **_kw):
        usage = {"input_tokens": 100, "output_tokens": 50}
        sp = system_prompt.strip()
        if "以下是原 prompt:" in sp:
            real_start = sp.split("以下是原 prompt:", 1)[1][:300]
        else:
            real_start = sp[:1500]

        if "# 场景调度员" in real_start:
            counts["scene_picker"] += 1
            return ({"scene_name": "X", "scene_source": "llm_created",
                     "time_anchor": "", "reasoning": ""}, usage)
        if "# 世界事实账本" in real_start:
            return ({"new_facts": []}, usage)
        if "# 主线追踪员" in real_start:
            return ({"resolved_thread_ids": [], "advanced_thread_ids": [],
                     "new_threads": []}, usage)
        if "# 一致性自检员" in real_start:
            return ({"violations": []}, usage)
        if "# 实体身份注册员" in real_start:
            return ({"new_entities": [], "alias_additions": []}, usage)
        if "# 原子动作提取员" in real_start:
            return ({"actions": []}, usage)
        if "# 角色情绪追踪员" in real_start:
            return ({"emotional_states": []}, usage)
        if "时间锚解析器" in real_start[:200]:
            return ({"new_offset_hours": 0.0}, usage)
        if "# Agent 对话" in real_start or "扮演一个角色" in real_start[:400]:
            counts["agent_dialogue"] += 1
            return ({"reflection": "x", "dialogue": "d", "action": "a"}, usage)
        if "# 叙述者" in real_start:
            counts["narrator"] += 1
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

    # 关键断言:scene_picker 应**为 0**(因为 outline-first 直接用 outline_scenes)
    assert counts["scene_picker"] == 0, (
        f"outline-first 下 scene_picker 不应被调用,实际 {counts['scene_picker']}"
    )
    # 但 narrator / agent_dialogue 仍正常调
    assert counts["narrator"] > 0
    assert counts["agent_dialogue"] > 0

    # outline state 应被推进到 done
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT state FROM simulation_outlines WHERE simulation_id=?",
            (sim_id,),
        ).fetchone()
        assert row["state"] == "done"

        # 每个 outline_scene 应有 generated_simulation_scene_id
        scenes = conn.execute(
            """SELECT state, generated_simulation_scene_id
               FROM outline_scenes
               WHERE outline_id=(SELECT id FROM simulation_outlines WHERE simulation_id=?)
               ORDER BY scene_index""",
            (sim_id,),
        ).fetchall()
        for s in scenes:
            assert s["state"] == "done"
            assert s["generated_simulation_scene_id"]
    finally:
        conn.close()


def test_narrator_receives_outline_scene_block_in_system_prompt(monkeypatch):
    """outline-first 跑时,narrator system_prompt 应含'本幕完整图纸'段。"""
    user_id, sim_id, char_ids = _seed_sim(n_chars=2)
    _seed_outline_via_llm(monkeypatch, sim_id, char_ids)
    from app.services.outline_editor import approve_outline
    from app.db import get_connection
    conn = get_connection()
    try:
        approve_outline(conn, sim_id, user_id)
        # 限只跑 1 幕
        conn.execute(
            "UPDATE simulations SET rounds_planned=1 WHERE id=?", (sim_id,),
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
        if "# 世界事实账本" in real_start:
            return ({"new_facts": []}, usage)
        if "# 主线追踪员" in real_start:
            return ({"resolved_thread_ids": [], "advanced_thread_ids": [],
                     "new_threads": []}, usage)
        if "# 一致性自检员" in real_start:
            return ({"violations": []}, usage)
        if "# 实体身份注册员" in real_start:
            return ({"new_entities": [], "alias_additions": []}, usage)
        if "# 原子动作提取员" in real_start:
            return ({"actions": []}, usage)
        if "# 角色情绪追踪员" in real_start:
            return ({"emotional_states": []}, usage)
        if "时间锚解析器" in real_start[:200]:
            return ({"new_offset_hours": 0.0}, usage)
        if "# Agent 对话" in real_start or "扮演一个角色" in real_start[:400]:
            return ({"reflection": "x", "dialogue": "d", "action": "a"}, usage)
        if "# 叙述者" in real_start:
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

    assert len(captured_narrator_prompts) >= 3, "至少 3 个 narrator 候选"

    # 每个 narrator system_prompt 顶部应含"本幕完整图纸"
    for i, p in enumerate(captured_narrator_prompts):
        assert "本幕完整图纸" in p[:2000], (
            f"narrator sample {i} 顶部应含 outline 图纸段"
        )
        assert "outline-first 模式" in p[:2000], (
            f"narrator sample {i} 应标注 outline-first"
        )
        # 验证 outline_scene.key_events 出现在 prompt
        assert "韩紫雨家卧室" in p[:3000] or "教室" in p[:3000], (
            f"narrator sample {i} 应含 outline 中的 location 字段"
        )


def test_outline_orchestrator_uses_outline_characters_not_affinity(monkeypatch):
    """outline 指定的 characters_present 应被严格使用,不调 affinity-based summon。"""
    user_id, sim_id, char_ids = _seed_sim(n_chars=3)
    # 直接 INSERT outline + 1 个 outline_scene,指定只用 1 个角色
    only_c1 = list(char_ids.values())[0]
    from app.db import get_connection
    from app.services.project_service import iso_now
    now = iso_now()
    conn = get_connection()
    try:
        outline_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO simulation_outlines
               (id, simulation_id, state, total_scenes_planned,
                global_theme, global_arc, user_approved_at, error_message,
                created_at, updated_at)
               VALUES (?, ?, 'approved', 1, 'T', 'A', ?, NULL, ?, ?)""",
            (outline_id, sim_id, now, now, now),
        )
        scene_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO outline_scenes
               (id, outline_id, scene_index, scene_summary, scene_purpose,
                location, time_anchor, characters_present_json,
                key_events_json, key_props_json, transition_from_last,
                user_edited, state, generated_simulation_scene_id,
                error_message, created_at, updated_at)
               VALUES (?, ?, 0, '单角色测试', 'X',
                       '某地', '黄昏', ?, ?, '[]', '',
                       0, 'pending', NULL, NULL, ?, ?)""",
            (scene_id, outline_id,
             json.dumps([only_c1], ensure_ascii=False),
             json.dumps(["事件 A"], ensure_ascii=False),
             now, now),
        )
        # sim:1 幕 + use_outline_first=1
        conn.execute(
            "UPDATE simulations SET state='queued', rounds_planned=1, "
            "use_outline_first=1, current_round=0, narrative=NULL WHERE id=?",
            (sim_id,),
        )
        conn.commit()
    finally:
        conn.close()

    captured_agent_names: list[str] = []

    def fake_llm(system_prompt, user_input, **_kw):
        usage = {"input_tokens": 100, "output_tokens": 50}
        sp = system_prompt.strip()
        if "以下是原 prompt:" in sp:
            real_start = sp.split("以下是原 prompt:", 1)[1][:300]
        else:
            real_start = sp[:1500]
        if "# Agent 对话" in real_start or "扮演一个角色" in real_start[:400]:
            captured_agent_names.append(user_input.get("name", ""))
            return ({"reflection": "x", "dialogue": "d", "action": "a"}, usage)
        if "# 世界事实账本" in real_start:
            return ({"new_facts": []}, usage)
        if "# 主线追踪员" in real_start:
            return ({"resolved_thread_ids": [], "advanced_thread_ids": [],
                     "new_threads": []}, usage)
        if "# 一致性自检员" in real_start:
            return ({"violations": []}, usage)
        if "# 实体身份注册员" in real_start:
            return ({"new_entities": [], "alias_additions": []}, usage)
        if "# 原子动作提取员" in real_start:
            return ({"actions": []}, usage)
        if "# 角色情绪追踪员" in real_start:
            return ({"emotional_states": []}, usage)
        if "时间锚解析器" in real_start[:200]:
            return ({"new_offset_hours": 0.0}, usage)
        if "# 叙述者" in real_start:
            return ({"narrative_segment": "x" * 200}, usage)
        if "# 场景调度员" in real_start:
            # 不应被调用(outline-first)— 但若被调返兜底
            return ({"scene_name": "X", "scene_source": "llm_created",
                     "time_anchor": "", "reasoning": ""}, usage)
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

    # 关键断言:只有 1 个角色被召唤(c1),不是全部 3 个 affinity 候选
    unique_names = set(captured_agent_names)
    assert len(unique_names) == 1, (
        f"outline 指定 1 角色,但实际召唤了 {len(unique_names)} 个:{unique_names}"
    )
    # 拉真实 c1 name
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT name FROM characters WHERE id=?", (only_c1,),
        ).fetchone()
        expected_name = row["name"]
        assert expected_name in unique_names, (
            f"应召唤角色 {expected_name},实际 {unique_names}"
        )
    finally:
        conn.close()


def test_outline_first_runs_all_scenes_even_when_target_chars_reached(monkeypatch):
    """M6-fix6 回归测试:outline-first 模式严格按 outline 跑完所有幕,
    即使 narrative 字数已超过 target_chars × 0.95(动态停止机制)。

    用户实测 bug:配置 28 幕 / target=10000 字,跑到第 18 幕已 ~9500 字 → 触发停止 →
    后 10 幕(剧情高潮 + 结局)被砍 → 剧情不闭环。
    """
    user_id, sim_id, char_ids = _seed_sim(n_chars=2)
    c1 = list(char_ids.values())[0]
    c2 = list(char_ids.values())[1]

    # 造 5 幕 outline + 设 target_chars 极小(强制触发动态停止条件)
    from app.db import get_connection
    from app.services.project_service import iso_now
    import uuid
    now = iso_now()
    conn = get_connection()
    try:
        outline_id = uuid.uuid4().hex
        conn.execute(
            """INSERT INTO simulation_outlines
               (id, simulation_id, state, total_scenes_planned,
                global_theme, global_arc, user_approved_at, error_message,
                created_at, updated_at)
               VALUES (?, ?, 'approved', 5, 'T', 'A', ?, NULL, ?, ?)""",
            (outline_id, sim_id, now, now, now),
        )
        for i in range(5):
            conn.execute(
                """INSERT INTO outline_scenes
                   (id, outline_id, scene_index, scene_summary, scene_purpose,
                    location, time_anchor, characters_present_json,
                    key_events_json, key_props_json, transition_from_last,
                    user_edited, state, generated_simulation_scene_id,
                    error_message, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'X', ?, '黄昏', ?, ?, '[]', '',
                           0, 'pending', NULL, NULL, ?, ?)""",
                (
                    uuid.uuid4().hex, outline_id, i,
                    f"第 {i+1} 幕概要", f"场景{i}",
                    json.dumps([c1, c2], ensure_ascii=False),
                    json.dumps([f"事件{i}"], ensure_ascii=False),
                    now, now,
                ),
            )
        # 关键:设极小 target_chars(2000),让前几幕跑完就超 0.95 阈值
        conn.execute(
            """UPDATE simulations SET state='queued', rounds_planned=5,
               use_outline_first=1, target_chars=2000, narrative=NULL
               WHERE id=?""",
            (sim_id,),
        )
        conn.commit()
    finally:
        conn.close()

    # mock LLM:narrator 每幕返 800 字(2 幕就超 target 2000 × 0.95 = 1900)
    def fake_llm(system_prompt, user_input, **_kw):
        usage = {"input_tokens": 100, "output_tokens": 50}
        sp = system_prompt.strip()
        if "以下是原 prompt:" in sp:
            real_start = sp.split("以下是原 prompt:", 1)[1][:300]
        else:
            real_start = sp[:1500]
        if "# 场景调度员" in real_start:
            return ({"scene_name": "X", "scene_source": "llm_created",
                     "time_anchor": "", "reasoning": ""}, usage)
        if "# 世界事实账本" in real_start:
            return ({"new_facts": []}, usage)
        if "# 主线追踪员" in real_start:
            return ({"resolved_thread_ids": [], "advanced_thread_ids": [],
                     "new_threads": []}, usage)
        if "# 一致性自检员" in real_start:
            return ({"violations": []}, usage)
        if "# 实体身份注册员" in real_start:
            return ({"new_entities": [], "alias_additions": []}, usage)
        if "# 原子动作提取员" in real_start:
            return ({"actions": []}, usage)
        if "# 角色情绪追踪员" in real_start:
            return ({"emotional_states": []}, usage)
        if "时间锚解析器" in real_start[:200]:
            return ({"new_offset_hours": 0.0}, usage)
        if "# Agent 对话" in real_start or "扮演一个角色" in real_start[:400]:
            return ({"reflection": "x", "dialogue": "d", "action": "a"}, usage)
        if "# 叙述者" in real_start:
            # 每幕 800 字 → 跑 2 幕就达 1600 字超 0.95 阈值
            return ({"narrative_segment": "x" * 800}, usage)
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

    # 关键断言:outline-first 严格跑完 5 幕(即使字数超 target × 0.95)
    conn = get_connection()
    try:
        scenes = conn.execute(
            """SELECT scene_index FROM simulation_scenes
               WHERE simulation_id=? ORDER BY scene_index""",
            (sim_id,),
        ).fetchall()
        assert len(scenes) == 5, (
            f"outline-first 必须严格跑完所有 5 幕,实际跑了 {len(scenes)} 幕"
        )
        # outline state 应 done
        outline_row = conn.execute(
            "SELECT state FROM simulation_outlines WHERE simulation_id=?",
            (sim_id,),
        ).fetchone()
        assert outline_row["state"] == "done"
    finally:
        conn.close()


def test_outline_state_marked_done_after_generation(monkeypatch):
    """outline-first 跑完后,simulation_outlines.state → 'done'。"""
    user_id, sim_id, char_ids = _seed_sim(n_chars=2)
    _seed_outline_via_llm(monkeypatch, sim_id, char_ids)
    from app.services.outline_editor import approve_outline
    from app.db import get_connection
    conn = get_connection()
    try:
        approve_outline(conn, sim_id, user_id)
    finally:
        conn.close()

    def fake_llm(system_prompt, user_input, **_kw):
        usage = {"input_tokens": 100, "output_tokens": 50}
        sp = system_prompt.strip()
        if "以下是原 prompt:" in sp:
            real_start = sp.split("以下是原 prompt:", 1)[1][:300]
        else:
            real_start = sp[:1500]
        if "# 世界事实账本" in real_start:
            return ({"new_facts": []}, usage)
        if "# 主线追踪员" in real_start:
            return ({"resolved_thread_ids": [], "advanced_thread_ids": [],
                     "new_threads": []}, usage)
        if "# 一致性自检员" in real_start:
            return ({"violations": []}, usage)
        if "# 实体身份注册员" in real_start:
            return ({"new_entities": [], "alias_additions": []}, usage)
        if "# 原子动作提取员" in real_start:
            return ({"actions": []}, usage)
        if "# 角色情绪追踪员" in real_start:
            return ({"emotional_states": []}, usage)
        if "时间锚解析器" in real_start[:200]:
            return ({"new_offset_hours": 0.0}, usage)
        if "# Agent 对话" in real_start or "扮演一个角色" in real_start[:400]:
            return ({"reflection": "x", "dialogue": "d", "action": "a"}, usage)
        if "# 叙述者" in real_start:
            return ({"narrative_segment": "x" * 200}, usage)
        if "# 场景调度员" in real_start:
            return ({"scene_name": "X", "scene_source": "llm_created",
                     "time_anchor": "", "reasoning": ""}, usage)
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

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT state FROM simulation_outlines WHERE simulation_id=?",
            (sim_id,),
        ).fetchone()
        assert row["state"] == "done"
    finally:
        conn.close()


def test_old_sim_without_outline_uses_legacy_scene_picker(monkeypatch):
    """老 sim(use_outline_first=0)继续走原 scene_picker 自由路径(向后兼容)。"""
    _, sim_id, _ = _seed_sim(use_outline_first=0, n_chars=2)

    counts = {"scene_picker": 0}

    def fake_llm(system_prompt, user_input, **_kw):
        usage = {"input_tokens": 100, "output_tokens": 50}
        sp = system_prompt.strip()
        if "以下是原 prompt:" in sp:
            real_start = sp.split("以下是原 prompt:", 1)[1][:300]
        else:
            real_start = sp[:1500]
        if "# 场景调度员" in real_start:
            counts["scene_picker"] += 1
            return ({"scene_name": "X", "scene_source": "llm_created",
                     "time_anchor": "", "reasoning": ""}, usage)
        if "# 世界事实账本" in real_start:
            return ({"new_facts": []}, usage)
        if "# 主线追踪员" in real_start:
            return ({"resolved_thread_ids": [], "advanced_thread_ids": [],
                     "new_threads": []}, usage)
        if "# 一致性自检员" in real_start:
            return ({"violations": []}, usage)
        if "# 实体身份注册员" in real_start:
            return ({"new_entities": [], "alias_additions": []}, usage)
        if "# 原子动作提取员" in real_start:
            return ({"actions": []}, usage)
        if "# 角色情绪追踪员" in real_start:
            return ({"emotional_states": []}, usage)
        if "时间锚解析器" in real_start[:200]:
            return ({"new_offset_hours": 0.0}, usage)
        if "# Agent 对话" in real_start or "扮演一个角色" in real_start[:400]:
            return ({"reflection": "x", "dialogue": "d", "action": "a"}, usage)
        if "# 叙述者" in real_start:
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

    # 关键断言:无 outline 时,scene_picker 应被调用(每幕一次)
    assert counts["scene_picker"] >= 1, (
        f"无 outline 时 scene_picker 必须被调用,实际 {counts['scene_picker']}"
    )


# ============================================================
# 辅助:用 LLM mock 快速 seed outline + scenes
# ============================================================

def _seed_outline_via_llm(monkeypatch, sim_id: str, char_ids: dict):
    """复用 outline_generator + fake LLM 生成最小 outline。

    M6-fix1:outline_generator 用 call_llm_text → mock 返 JSON 字符串。
    """
    char_ids_list = list(char_ids.values())
    c1 = char_ids_list[0]
    c2 = char_ids_list[1] if len(char_ids_list) > 1 else c1

    outline_dict = {
        "global_theme": "测试主题",
        "global_arc": "测试起承转合",
        "scenes": [
            {
                "scene_index": 0,
                "scene_summary": "第一幕概要",
                "scene_purpose": "引入",
                "location": "韩紫雨家卧室",
                "time_anchor": "深夜",
                "characters_present": [c1, c2],
                "key_events": ["事件 1", "事件 2"],
                "key_props": [],
                "transition_from_last": "起点",
            },
            {
                "scene_index": 1,
                "scene_summary": "第二幕概要",
                "scene_purpose": "推进",
                "location": "教室",
                "time_anchor": "次日清晨",
                "characters_present": [c1, c2],
                "key_events": ["事件 3"],
                "key_props": [],
                "transition_from_last": "次日清晨到教室",
            },
        ],
    }
    raw_json = json.dumps(outline_dict, ensure_ascii=False)

    def fake_text(_sys, _user, **_kw):
        return (raw_json, {"input_tokens": 100, "output_tokens": 50})
    monkeypatch.setattr(
        "app.services.outline_generator.call_llm_text", fake_text,
    )

    from app.services.outline_generator import create_outline_draft
    from app.db import get_connection
    conn = get_connection()
    try:
        create_outline_draft(conn, sim_id)
    finally:
        conn.close()
