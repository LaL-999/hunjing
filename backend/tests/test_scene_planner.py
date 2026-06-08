"""Sprint 6.A2 MP(2026-05-21)— Scene Planner 测试。

覆盖:
  - plan_scene_tension 输出格式 + 字段校验 + 边界值
  - LLM 失败 / 输出无效 → fallback decision
  - write_back_to_outline_scene 持久化 + 幂等
  - PlannerDecision.to_narrator_hint 含张力百分比 + 节奏标签
  - 主循环 hook:outline-first 模式 planner 回写 outline_scene
"""
from __future__ import annotations

import uuid

import pytest


# ============================================================
# helpers
# ============================================================

def _make_outline_scene_row(conn, outline_id: str, scene_index: int = 0) -> str:
    """造一行 outline_scenes,返回 scene_id。"""
    from app.services.project_service import iso_now
    sid = uuid.uuid4().hex
    now = iso_now()
    conn.execute(
        """INSERT INTO outline_scenes
           (id, outline_id, scene_index, scene_summary, scene_purpose,
            location, time_anchor, characters_present_json,
            key_events_json, key_props_json, transition_from_last,
            user_edited, state, created_at, updated_at)
           VALUES (?, ?, ?, '测试幕', '推进主线', '教室', '深夜',
                   '[]', '[]', '[]', '', 0, 'pending', ?, ?)""",
        (sid, outline_id, scene_index, now, now),
    )
    conn.commit()
    return sid


def _make_outline_row(conn, sim_id: str = "sim_test") -> str:
    """造一行 simulation_outlines + 必要的 user/project/sim,返回 outline_id。"""
    from app.services.project_service import iso_now
    oid = uuid.uuid4().hex
    now = iso_now()
    uid = uuid.uuid4().hex
    pid = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO users (id, email, plan, created_at, updated_at)
           VALUES (?, ?, 'free', ?, ?)""",
        (uid, f"{uid[:8]}@test.local", now, now),
    )
    conn.execute(
        """INSERT INTO projects (id, user_id, name, type, tags, mode,
                                   created_at, updated_at,
                                   world_baseline_json, graph_strength_threshold)
           VALUES (?, ?, 'MP test', 'novel', '[]', 'initial', ?, ?, '{}', 30)""",
        (pid, uid, now, now),
    )
    conn.execute(
        """INSERT INTO simulations
           (id, project_id, user_id, divergence, reshape_percent,
            rounds_planned, target_chars, style, custom_style_hint,
            context_simulation_ids, narrative_summary, characters_snapshot,
            state, current_round, timeline_json, narrative,
            tokens_input, tokens_output, cost_yuan, error_message,
            created_at, started_at, completed_at, mode, use_outline_first)
           VALUES (?, ?, ?, 'x', 50, 3, 4000, 'A', NULL,
                   '[]', NULL, '[]', 'queued', 0, NULL, NULL,
                   0, 0, 0.0, NULL, ?, NULL, NULL, 'evolution', 1)""",
        (sim_id, pid, uid, now),
    )
    conn.execute(
        """INSERT INTO simulation_outlines
           (id, simulation_id, state, total_scenes_planned,
            global_theme, global_arc, created_at, updated_at)
           VALUES (?, ?, 'awaiting_user', 3, '主题', '起承转合', ?, ?)""",
        (oid, sim_id, now, now),
    )
    conn.commit()
    return oid


# ============================================================
# 1. PlannerDecision + to_narrator_hint
# ============================================================

def test_planner_decision_hint_contains_tension_and_tempo():
    """to_narrator_hint 应含张力百分比 + 节奏标签。"""
    from app.services.scene_planner import PlannerDecision
    d = PlannerDecision(tension_percent=75, pacing_tempo="fast", reasoning="测试")
    hint = d.to_narrator_hint()
    assert "75%" in hint, "hint 应含张力百分比"
    assert "快" in hint, "fast 节奏应译为'快'"
    assert "句短促紧凑" in hint or "推进感强" in hint, "hint 应给出笔法约束"


def test_planner_decision_slow_tempo_label():
    from app.services.scene_planner import PlannerDecision
    d = PlannerDecision(tension_percent=20, pacing_tempo="slow", reasoning="x")
    hint = d.to_narrator_hint()
    assert "慢" in hint, "slow 节奏应译为'慢'"
    assert "20%" in hint


# ============================================================
# 2. plan_scene_tension LLM 成功路径
# ============================================================

def test_plan_scene_tension_parses_valid_llm_output(monkeypatch):
    """LLM 返合法 JSON → 正常解析返 PlannerDecision。"""
    def fake_llm(system_prompt, user_input, **_kw):
        return (
            {"tension_percent": 80, "pacing_tempo": "fast", "reasoning": "高潮段"},
            {"input_tokens": 50, "output_tokens": 10},
        )
    monkeypatch.setattr(
        "app.services.scene_planner.call_llm_json", fake_llm,
    )
    from app.services.scene_planner import plan_scene_tension
    decision, usage = plan_scene_tension(
        scene_index=12, total_scenes=28,
        narrative_so_far_tail="前情提要……",
    )
    assert decision.tension_percent == 80
    assert decision.pacing_tempo == "fast"
    assert "高潮" in decision.reasoning
    assert usage["input_tokens"] == 50


def test_plan_scene_tension_clamps_out_of_range(monkeypatch):
    """tension_percent 出界(> 100 或 < 0)应被夹回 [0, 100]。"""
    def fake_llm(system_prompt, user_input, **_kw):
        return ({"tension_percent": 150, "pacing_tempo": "fast",
                 "reasoning": "x"}, {"input_tokens": 10, "output_tokens": 5})
    monkeypatch.setattr(
        "app.services.scene_planner.call_llm_json", fake_llm,
    )
    from app.services.scene_planner import plan_scene_tension
    decision, _ = plan_scene_tension(scene_index=0, total_scenes=10)
    assert decision.tension_percent == 100, "150 应夹到 100"


def test_plan_scene_tension_invalid_pacing_defaults_to_normal(monkeypatch):
    """pacing_tempo 非法值(如 'extreme')→ 默认 normal。"""
    def fake_llm(system_prompt, user_input, **_kw):
        return ({"tension_percent": 50, "pacing_tempo": "extreme",
                 "reasoning": "x"}, {"input_tokens": 10, "output_tokens": 5})
    monkeypatch.setattr(
        "app.services.scene_planner.call_llm_json", fake_llm,
    )
    from app.services.scene_planner import plan_scene_tension
    decision, _ = plan_scene_tension(scene_index=0, total_scenes=10)
    assert decision.pacing_tempo == "normal", "非法 tempo 应回退到 normal"


# ============================================================
# 3. LLM 失败 / 输出无效 → fallback
# ============================================================

def test_plan_scene_tension_llm_failure_returns_fallback(monkeypatch):
    """LLM 调用 raise → 返回 fallback decision (50, normal),不抛异常。"""
    def fake_llm(system_prompt, user_input, **_kw):
        raise RuntimeError("network timeout")
    monkeypatch.setattr(
        "app.services.scene_planner.call_llm_json", fake_llm,
    )
    from app.services.scene_planner import plan_scene_tension
    decision, usage = plan_scene_tension(scene_index=5, total_scenes=20)
    assert decision.tension_percent == 50
    assert decision.pacing_tempo == "normal"
    assert "失败" in decision.reasoning or "中位" in decision.reasoning
    assert usage == {"input_tokens": 0, "output_tokens": 0}


def test_plan_scene_tension_non_dict_output_returns_fallback(monkeypatch):
    """LLM 返非 dict(如 list / 字符串)→ fallback。"""
    def fake_llm(system_prompt, user_input, **_kw):
        return (["not a dict"], {"input_tokens": 5, "output_tokens": 2})
    monkeypatch.setattr(
        "app.services.scene_planner.call_llm_json", fake_llm,
    )
    from app.services.scene_planner import plan_scene_tension
    decision, _ = plan_scene_tension(scene_index=0, total_scenes=10)
    assert decision.tension_percent == 50


def test_plan_scene_tension_missing_tension_field_returns_fallback(monkeypatch):
    """LLM 返 dict 但缺 tension_percent → fallback。"""
    def fake_llm(system_prompt, user_input, **_kw):
        return ({"pacing_tempo": "fast"}, {"input_tokens": 5, "output_tokens": 2})
    monkeypatch.setattr(
        "app.services.scene_planner.call_llm_json", fake_llm,
    )
    from app.services.scene_planner import plan_scene_tension
    decision, _ = plan_scene_tension(scene_index=0, total_scenes=10)
    assert decision.tension_percent == 50, "缺字段应回退"


# ============================================================
# 4. write_back_to_outline_scene 持久化 + 幂等
# ============================================================

def test_write_back_persists_tension_and_pacing():
    """write_back_to_outline_scene 落库后查回应一致。"""
    from app.db import get_connection
    from app.services.scene_planner import (
        PlannerDecision, write_back_to_outline_scene,
    )
    conn = get_connection()
    try:
        oid = _make_outline_row(conn, sim_id="sim_wb_1")
        sid = _make_outline_scene_row(conn, oid, scene_index=0)
        decision = PlannerDecision(
            tension_percent=72, pacing_tempo="fast", reasoning="高张力",
        )
        write_back_to_outline_scene(conn, sid, decision)
        row = conn.execute(
            "SELECT tension_percent, pacing_tempo FROM outline_scenes WHERE id=?",
            (sid,),
        ).fetchone()
        assert row["tension_percent"] == 72
        assert row["pacing_tempo"] == "fast"
    finally:
        conn.close()


def test_write_back_idempotent_overwrites():
    """重复 write_back 应覆盖前值(幂等)。"""
    from app.db import get_connection
    from app.services.scene_planner import (
        PlannerDecision, write_back_to_outline_scene,
    )
    conn = get_connection()
    try:
        oid = _make_outline_row(conn, sim_id="sim_wb_2")
        sid = _make_outline_scene_row(conn, oid, scene_index=0)
        # 第一次写
        d1 = PlannerDecision(tension_percent=30, pacing_tempo="slow", reasoning="x")
        write_back_to_outline_scene(conn, sid, d1)
        # 第二次覆盖
        d2 = PlannerDecision(tension_percent=85, pacing_tempo="fast", reasoning="y")
        write_back_to_outline_scene(conn, sid, d2)
        row = conn.execute(
            "SELECT tension_percent, pacing_tempo FROM outline_scenes WHERE id=?",
            (sid,),
        ).fetchone()
        assert row["tension_percent"] == 85, "第二次值应覆盖第一次"
        assert row["pacing_tempo"] == "fast"
    finally:
        conn.close()


# ============================================================
# 5. OutlineScene 模型 / API 字段往返
# ============================================================

def test_outline_scene_from_row_reads_tension_pacing():
    """OutlineScene.from_row 应正确读取 tension_percent + pacing_tempo。"""
    from app.db import get_connection
    from app.models.outline_scene import OutlineScene
    conn = get_connection()
    try:
        oid = _make_outline_row(conn, sim_id="sim_model_1")
        sid = _make_outline_scene_row(conn, oid)
        # 写值
        conn.execute(
            "UPDATE outline_scenes SET tension_percent=?, pacing_tempo=? WHERE id=?",
            (65, "normal", sid),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM outline_scenes WHERE id=?", (sid,),
        ).fetchone()
        os_obj = OutlineScene.from_row(row)
        assert os_obj.tension_percent == 65
        assert os_obj.pacing_tempo == "normal"
    finally:
        conn.close()


def test_outline_scene_to_prompt_block_includes_tension_when_set():
    """to_prompt_block 在 tension_percent 非空时含张力指令段。"""
    from app.models.outline_scene import OutlineScene
    os_obj = OutlineScene(
        id="x", outline_id="o", scene_index=0,
        scene_summary="测试", scene_purpose="高潮",
        location="教室", time_anchor="深夜",
        characters_present=[], key_events=["事件 A"], key_props=[],
        transition_from_last="", user_edited=False,
        state="pending", generated_simulation_scene_id=None,
        error_message=None, created_at="", updated_at="",
        tension_percent=80, pacing_tempo="fast",
    )
    block = os_obj.to_prompt_block()
    assert "80%" in block, "to_prompt_block 应含张力百分比"
    assert "快" in block, "fast 应译为'快'"


def test_outline_scene_to_prompt_block_omits_tension_when_unset():
    """to_prompt_block 在 tension_percent=None 时不应含张力段。"""
    from app.models.outline_scene import OutlineScene
    os_obj = OutlineScene(
        id="x", outline_id="o", scene_index=0,
        scene_summary="测试", scene_purpose="推进主线",
        location="教室", time_anchor="深夜",
        characters_present=[], key_events=[], key_props=[],
        transition_from_last="", user_edited=False,
        state="pending", generated_simulation_scene_id=None,
        error_message=None, created_at="", updated_at="",
        tension_percent=None, pacing_tempo=None,
    )
    block = os_obj.to_prompt_block()
    assert "本幕张力" not in block, "未规划时不应含张力段"
