"""SP-9 enhancement(2026-05-29 末)— 因果分析 LLM 测试."""
from __future__ import annotations

import pytest


def _sample_compare_data() -> dict:
    """造一个 SP-9 compare endpoint 返回风格的 data."""
    return {
        "sim_a": {
            "id": "sim-a", "project_id": "p1", "project_name": "测试",
            "state": "done", "divergence": "分支 A 设定",
            "reshape_percent": 50, "rounds_planned": 10, "current_round": 10,
            "target_chars": 5000, "style": "A",
            "created_at": "2026-05-29T00:00:00+00:00", "completed_at": None,
            "cost_yuan": 0.1,
        },
        "sim_b": {
            "id": "sim-b", "project_id": "p1", "project_name": "测试",
            "state": "done", "divergence": "分支 B 设定",
            "reshape_percent": 70, "rounds_planned": 10, "current_round": 10,
            "target_chars": 5000, "style": "A",
            "created_at": "2026-05-29T00:00:00+00:00", "completed_at": None,
            "cost_yuan": 0.1,
        },
        "counterfactual_diff": {
            "common": [],
            "only_in_a": [
                {
                    "id": "cf-1", "project_id": "p1",
                    "target_type": "character", "target_id": "c1",
                    "target_name": "渡边", "field": "personality",
                    "old_value": "内向", "new_value": "外向",
                    "user_intent": "想看外向版本",
                    "created_at": "x", "reverted_at": None, "is_active": True,
                    "applied_in_simulations": ["sim-a"],
                },
            ],
            "only_in_b": [
                {
                    "id": "cf-2", "project_id": "p1",
                    "target_type": "world", "target_id": "_global_",
                    "target_name": "", "field": "tone",
                    "old_value": "抒情", "new_value": "黑暗",
                    "user_intent": "试试黑暗基调",
                    "created_at": "x", "reverted_at": None, "is_active": True,
                    "applied_in_simulations": ["sim-b"],
                },
            ],
        },
        "scene_alignment": [
            {
                "scene_index": 0, "diff_kind": "both", "similar": False,
                "a": {
                    "scene_name": "校园相遇 A", "scene_source": "llm_created",
                    "time_anchor": "第 1 章", "characters_present": ["c1"],
                    "narrative_segment": "渡边主动搭话,对话热烈" * 30,
                },
                "b": {
                    "scene_name": "校园相遇 B", "scene_source": "llm_created",
                    "time_anchor": "第 1 章", "characters_present": ["c1"],
                    "narrative_segment": "渡边沉默地看着" * 30,
                },
            },
            {
                "scene_index": 5, "diff_kind": "only_b", "similar": False,
                "a": None,
                "b": {
                    "scene_name": "悲剧降临", "scene_source": "llm_created",
                    "time_anchor": "第 6 章", "characters_present": ["c2"],
                    "narrative_segment": "直子自杀" * 20,
                },
            },
        ],
        "stats": {
            "scenes_a_count": 1, "scenes_b_count": 2,
            "common_scene_count": 1, "similar_scene_count": 0,
        },
    }


def test_analyze_success_with_mock_llm(monkeypatch):
    """LLM 返合法分析 → 返结构化报告."""
    from app.services import comparison_analyzer as ca

    captured: dict = {}

    def fake_llm(system_prompt, user_input, **kwargs):
        captured.update(user_input)
        return (
            {
                "summary": "两分支朝完全不同方向演化:A 偏对话热烈版本,B 偏沉默悲剧版本.",
                "verdict_a": "外向渡边推动了主动对话,无悲剧发生",
                "verdict_b": "抒情基调改黑暗后,新增了直子自杀情节",
                "key_turning_points": [
                    {"scene_index": 0, "what_diverged": "对话热烈 vs 沉默观察", "likely_cause_cf_id": "cf-1"},
                    {"scene_index": 5, "what_diverged": "B 独有悲剧情节", "likely_cause_cf_id": "cf-2"},
                ],
                "cf_impact_chains": [
                    {"cf_id": "cf-1", "side": "only_in_a", "narrative_consequence": "scene 0 出现主动对话"},
                    {"cf_id": "cf-2", "side": "only_in_b", "narrative_consequence": "scene 5 新增自杀情节"},
                ],
                "reasoning": "基于 only_in_a/only_in_b 各 1 条 cf 在 scene 0 + 5 各落地",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(ca, "call_llm_json", fake_llm)

    compare_data = _sample_compare_data()
    result = ca.analyze_compare(compare_data)

    assert result["is_fallback"] is False
    assert "两分支" in result["summary"]
    assert result["verdict_a"].startswith("外向")
    assert result["verdict_b"].startswith("抒情")
    assert len(result["key_turning_points"]) == 2
    assert len(result["cf_impact_chains"]) == 2
    assert result["cf_impact_chains"][0]["side"] == "only_in_a"
    assert result["cf_impact_chains"][0]["cf_id"] == "cf-1"

    # LLM 收到的 input 应含 cf_diff 三桶 + scene_alignment trimmed + stats
    assert "counterfactual_diff" in captured
    assert "scene_alignment_trimmed" in captured
    assert "stats" in captured


def test_analyze_llm_failure_returns_program_fallback(monkeypatch):
    """LLM 抛 LlmCallFailed → 返程序级 fallback,is_fallback=True."""
    from app.services import comparison_analyzer as ca
    from app.services.llm_client import LlmCallFailed

    def bad_llm(system_prompt, user_input, **kwargs):
        raise LlmCallFailed("MOCK_FAIL")
    monkeypatch.setattr(ca, "call_llm_json", bad_llm)

    compare_data = _sample_compare_data()
    result = ca.analyze_compare(compare_data)

    assert result["is_fallback"] is True
    assert result["summary"]  # 程序级简短分析
    assert result["key_turning_points"] == []
    assert result["cf_impact_chains"] == []


def test_analyze_endpoint_uses_compare_data(client, make_user, monkeypatch):
    """端到端:endpoint 先组装 compare data,再调 analyzer."""
    import json
    import uuid
    from app.db import execute, get_connection
    from app.services import comparison_analyzer

    u = make_user("ca_e2e")
    pid = str(uuid.uuid4())
    sim_a = str(uuid.uuid4())
    sim_b = str(uuid.uuid4())
    now = "2026-05-29T00:00:00+00:00"
    conn = get_connection()
    try:
        execute(
            conn,
            "INSERT INTO projects "
            "(id, user_id, name, type, custom_type_name, tags, mode, "
            " created_at, updated_at) "
            "VALUES (?, ?, '测试', 'novel', NULL, '[]', 'middle', ?, ?)",
            (pid, u["user_id"], now, now),
        )
        for sid in (sim_a, sim_b):
            execute(
                conn,
                "INSERT INTO simulations "
                "(id, project_id, user_id, divergence, reshape_percent, rounds_planned, "
                " target_chars, style, custom_style_hint, context_simulation_ids, "
                " narrative_summary, characters_snapshot, state, current_round, "
                " timeline_json, narrative, tokens_input, tokens_output, "
                " cost_yuan, error_message, created_at, started_at, completed_at) "
                "VALUES (?, ?, ?, ?, 50, 3, 4000, 'A', NULL, '[]', NULL, "
                " '[]', 'done', 3, NULL, NULL, 0, 0, 0.0, NULL, ?, NULL, ?)",
                (sid, pid, u["user_id"], "test", now, now),
            )
        conn.commit()
    finally:
        conn.close()

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "summary": "测试分析",
                "verdict_a": "A 走向",
                "verdict_b": "B 走向",
                "key_turning_points": [],
                "cf_impact_chains": [],
                "reasoning": "x",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(comparison_analyzer, "call_llm_json", fake_llm)

    r = client.post(
        f"/api/simulations/{sim_a}/compare/{sim_b}/analyze",
        headers=u["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["summary"] == "测试分析"
    assert body["is_fallback"] is False
