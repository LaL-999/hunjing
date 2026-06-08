"""SP-9 反事实分支并排对比 endpoint 测试(2026-05-29)."""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import execute, get_connection


def _setup_project_with_two_sims(
    user_id: str,
    *,
    scenes_per_sim: int = 3,
    diff_scene_at_a: bool = False,  # 是否在 A 加额外一幕(scenes_a 比 b 多 1)
) -> tuple[str, str, str, list[str]]:
    """造 project + 2 个 sim + 每 sim 几幕 simulation_scenes + 2 角色 + 几条反事实变量.

    返回 (project_id, sim_a_id, sim_b_id, [char_a_id, char_b_id])
    """
    pid = str(uuid.uuid4())
    sim_a = str(uuid.uuid4())
    sim_b = str(uuid.uuid4())
    char_a = str(uuid.uuid4())
    char_b = str(uuid.uuid4())
    now = "2026-05-29T00:00:00+00:00"
    conn = get_connection()
    try:
        execute(
            conn,
            "INSERT INTO projects "
            "(id, user_id, name, type, custom_type_name, tags, mode, "
            " created_at, updated_at) "
            "VALUES (?, ?, '测试', 'novel', NULL, '[]', 'middle', ?, ?)",
            (pid, user_id, now, now),
        )
        # 2 角色
        execute(
            conn,
            "INSERT INTO characters "
            "(id, project_id, name, identity, personality, quotes, no_go_list, "
            " position_x, position_y, position_z, color, created_at, updated_at) "
            "VALUES (?, ?, '渡边', '', '', '[]', '[]', 0, 0, 0, NULL, ?, ?)",
            (char_a, pid, now, now),
        )
        execute(
            conn,
            "INSERT INTO characters "
            "(id, project_id, name, identity, personality, quotes, no_go_list, "
            " position_x, position_y, position_z, color, created_at, updated_at) "
            "VALUES (?, ?, '直子', '', '', '[]', '[]', 0, 0, 0, NULL, ?, ?)",
            (char_b, pid, now, now),
        )
        # 2 个 sim
        for sid, divergence in ((sim_a, "原版分支"), (sim_b, "反事实分支")):
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
                (sid, pid, user_id, divergence, now, now),
            )
        # 反事实变量:
        # 1) 仅 sim_a 应用
        cf1 = str(uuid.uuid4())
        execute(
            conn,
            "INSERT INTO counterfactual_changes "
            "(id, project_id, target_type, target_id, field, old_value, new_value, "
            " user_intent, created_at, reverted_at, applied_in_simulations_json, user_id) "
            "VALUES (?, ?, 'character', ?, 'personality', '内向', '外向', "
            " '想看外向会怎样', ?, NULL, ?, ?)",
            (cf1, pid, char_a, now, json.dumps([sim_a]), user_id),
        )
        # 2) 双方共享
        cf2 = str(uuid.uuid4())
        execute(
            conn,
            "INSERT INTO counterfactual_changes "
            "(id, project_id, target_type, target_id, field, old_value, new_value, "
            " user_intent, created_at, reverted_at, applied_in_simulations_json, user_id) "
            "VALUES (?, ?, 'character', ?, 'identity', '大学生', '上班族', "
            " NULL, ?, NULL, ?, ?)",
            (cf2, pid, char_b, now, json.dumps([sim_a, sim_b]), user_id),
        )
        # 3) 仅 sim_b 应用
        cf3 = str(uuid.uuid4())
        execute(
            conn,
            "INSERT INTO counterfactual_changes "
            "(id, project_id, target_type, target_id, field, old_value, new_value, "
            " user_intent, created_at, reverted_at, applied_in_simulations_json, user_id) "
            "VALUES (?, ?, 'world', '_global_', 'tone', '抒情', '黑暗', "
            " '改基调试试', ?, NULL, ?, ?)",
            (cf3, pid, now, json.dumps([sim_b]), user_id),
        )

        # simulation_scenes — 每 sim 几幕
        def _insert_scene(sim_id: str, idx: int, name: str, narrative: str):
            execute(
                conn,
                "INSERT INTO simulation_scenes "
                "(id, simulation_id, scene_index, scene_name, scene_source, "
                " time_anchor, characters_present_json, narrative_segment, created_at) "
                "VALUES (?, ?, ?, ?, 'llm_created', '', ?, ?, ?)",
                (str(uuid.uuid4()), sim_id, idx, name,
                 json.dumps([char_a]), narrative, now),
            )

        for i in range(scenes_per_sim):
            _insert_scene(sim_a, i, f"第 {i+1} 幕 A", f"分支 A 第 {i+1} 幕的内容。" * 20)
            _insert_scene(sim_b, i, f"第 {i+1} 幕 B", f"分支 B 第 {i+1} 幕的内容。" * 20)

        if diff_scene_at_a:
            _insert_scene(sim_a, scenes_per_sim, "仅 A 加幕", "A 独有此幕")

        conn.commit()
        return pid, sim_a, sim_b, [char_a, char_b]
    finally:
        conn.close()


def test_compare_returns_two_sim_metadata(client: TestClient, make_user):
    """GET /compare/ 返两 sim metadata."""
    u = make_user("cmp_meta")
    pid, sim_a, sim_b, _ = _setup_project_with_two_sims(u["user_id"])

    r = client.get(
        f"/api/simulations/{sim_a}/compare/{sim_b}",
        headers=u["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sim_a"]["id"] == sim_a
    assert body["sim_b"]["id"] == sim_b
    assert body["sim_a"]["divergence"] == "原版分支"
    assert body["sim_b"]["divergence"] == "反事实分支"


def test_compare_counterfactual_diff_buckets(client: TestClient, make_user):
    """反事实变量正确分桶 common / only_in_a / only_in_b."""
    u = make_user("cmp_cf")
    pid, sim_a, sim_b, _ = _setup_project_with_two_sims(u["user_id"])

    r = client.get(
        f"/api/simulations/{sim_a}/compare/{sim_b}",
        headers=u["headers"],
    )
    assert r.status_code == 200
    diff = r.json()["counterfactual_diff"]
    assert len(diff["common"]) == 1
    assert len(diff["only_in_a"]) == 1
    assert len(diff["only_in_b"]) == 1
    # common 那条改的是 identity
    assert diff["common"][0]["field"] == "identity"
    # only_a 改的是 personality
    assert diff["only_in_a"][0]["field"] == "personality"
    # only_b 改的是 world tone
    assert diff["only_in_b"][0]["field"] == "tone"
    assert diff["only_in_b"][0]["target_type"] == "world"


def test_compare_scene_alignment_both_and_only_a(client: TestClient, make_user):
    """scene_alignment 含 both + only_a(若 A 多一幕)."""
    u = make_user("cmp_scene")
    pid, sim_a, sim_b, _ = _setup_project_with_two_sims(
        u["user_id"], scenes_per_sim=2, diff_scene_at_a=True,
    )

    r = client.get(
        f"/api/simulations/{sim_a}/compare/{sim_b}",
        headers=u["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    alignment = body["scene_alignment"]
    # 0, 1, 2 — 前两个 both,第 3 个 only_a
    assert len(alignment) == 3
    assert alignment[0]["diff_kind"] == "both"
    assert alignment[1]["diff_kind"] == "both"
    assert alignment[2]["diff_kind"] == "only_a"
    assert alignment[2]["a"] is not None
    assert alignment[2]["b"] is None

    # stats
    stats = body["stats"]
    assert stats["scenes_a_count"] == 3
    assert stats["scenes_b_count"] == 2
    assert stats["common_scene_count"] == 2


def test_compare_cross_project_400(client: TestClient, make_user):
    """跨项目 sim → 400 COMPARE_CROSS_PROJECT."""
    u = make_user("cmp_xp")
    pid1, sim_a, _, _ = _setup_project_with_two_sims(u["user_id"])
    pid2, _, sim_b2, _ = _setup_project_with_two_sims(u["user_id"])

    r = client.get(
        f"/api/simulations/{sim_a}/compare/{sim_b2}",
        headers=u["headers"],
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "COMPARE_CROSS_PROJECT"


def test_compare_self_400(client: TestClient, make_user):
    """自比 → 400 COMPARE_SELF."""
    u = make_user("cmp_self")
    pid, sim_a, _, _ = _setup_project_with_two_sims(u["user_id"])

    r = client.get(
        f"/api/simulations/{sim_a}/compare/{sim_a}",
        headers=u["headers"],
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "COMPARE_SELF"


def test_compare_cross_user_404(client: TestClient, make_user):
    """非自己的 sim → 404."""
    alice = make_user("cmp_alice")
    bob = make_user("cmp_bob")
    _, sim_a, sim_b, _ = _setup_project_with_two_sims(alice["user_id"])

    r = client.get(
        f"/api/simulations/{sim_a}/compare/{sim_b}",
        headers=bob["headers"],
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SIMULATION_NOT_FOUND"


def test_compare_resolves_target_name(client: TestClient, make_user):
    """反事实条目应有 target_name(character.name / world ''=空)."""
    u = make_user("cmp_name")
    pid, sim_a, sim_b, _ = _setup_project_with_two_sims(u["user_id"])

    r = client.get(
        f"/api/simulations/{sim_a}/compare/{sim_b}",
        headers=u["headers"],
    )
    body = r.json()
    only_a = body["counterfactual_diff"]["only_in_a"][0]
    # only_a 改 character.personality → target_name 应为 "渡边"
    assert only_a["target_name"] == "渡边"
    common = body["counterfactual_diff"]["common"][0]
    assert common["target_name"] == "直子"
