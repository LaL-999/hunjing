"""反事实变量 + 重塑度三维度服务端测试 — Sprint 2.C 验收基线。

测试分组:
  A. 公式:reshape_to_max_touched_characters / reshape_to_graph_distance_hops
  B. record_change:merge 语义 / 字段类型清洗 / noop 跳过
  C. PATCH hook:character/event/relationship 改字段后自动 record + 不 track 视觉字段
  D. revert:还原 db 字段 + 撤销幂等 + 跨用户 404
  E. list / overview:active 过滤 + 按 type 分桶
  F. BFS affected_node_ids:hops=0 只 seed / hops 充足 全图覆盖 / 跨 character-event 边
  G. derive_reshape_dimensions:三维同步
  H. simulation 触发:create 时 mark_applied + ReshapeCharacterLimitExceeded 转 422
  I. director prompt 注入:run_simulation 拼接含反事实文本
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ============================================================
# helpers
# ============================================================

def _create_project_with_n_characters(client, headers, n: int = 4) -> tuple[str, list[str]]:
    """创建 1 项目 + n 个 character,返回 (project_id, [character_id...])。"""
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": "测试项目", "type": "novel", "mode": "initial"},
    )
    assert p.status_code == 201, p.text
    project_id = p.json()["id"]
    char_ids: list[str] = []
    for i in range(n):
        r = client.post(
            f"/api/projects/{project_id}/characters",
            headers=headers,
            json={
                "name": f"角色{i+1:02d}",
                "identity": f"身份{i+1}",
                "personality": f"性格{i+1}",
                "quotes": [f"台词{i+1}-1", f"台词{i+1}-2"],
                "no_go_list": [f"禁忌{i+1}"],
            },
        )
        assert r.status_code == 201, r.text
        char_ids.append(r.json()["id"])
    return project_id, char_ids


def _db_conn():
    import os
    from app.db import _connect
    return _connect(Path(os.environ["HUIMENG_DB_PATH"]))


# ============================================================
# A. 公式
# ============================================================

@pytest.mark.parametrize("percent,expected", [
    (10, 2),
    (25, 5),
    (50, 10),
    (90, 18),
])
def test_reshape_to_max_touched_characters(percent, expected):
    from app.services.counterfactual_service import reshape_to_max_touched_characters
    assert reshape_to_max_touched_characters(percent) == expected


@pytest.mark.parametrize("percent,expected_hops", [
    (10, 0),    # 只本节点
    (20, 1),
    (30, 2),
    (50, 4),
    (90, 8),    # 基本全图
])
def test_reshape_to_graph_distance_hops(percent, expected_hops):
    from app.services.counterfactual_service import reshape_to_graph_distance_hops
    assert reshape_to_graph_distance_hops(percent) == expected_hops


# ============================================================
# B. record_change merge 语义
# ============================================================

def test_record_change_merge_same_field_keeps_one_active(client: TestClient, make_user):
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 1)
    cid = char_ids[0]

    # 第 1 次改 personality 触发 record(走 PATCH hook)
    r1 = client.patch(
        f"/api/characters/{cid}", headers=user["headers"],
        json={"personality": "改 1"},
    )
    assert r1.status_code == 200
    # 第 2 次再改 personality(merge 语义:仍只 1 条 active)
    r2 = client.patch(
        f"/api/characters/{cid}", headers=user["headers"],
        json={"personality": "改 2"},
    )
    assert r2.status_code == 200

    # 项目 active 反事实 = 1 条(merge,新 value 是"改 2",old 仍是最初的"性格1")
    overview = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()
    assert overview["total_active"] == 1
    assert overview["by_type"]["character"] == 1
    cf = overview["items"][0]
    assert cf["field"] == "personality"
    assert cf["old_value"] == "性格1"
    assert cf["new_value"] == "改 2"


def test_record_change_noop_skipped(client: TestClient, make_user):
    """PATCH 同字段同值(无变化)→ record_change 抛 ValueError 静默跳过,不留反事实。"""
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 1)
    cid = char_ids[0]

    # 把 personality 改成一样的值
    r = client.patch(
        f"/api/characters/{cid}", headers=user["headers"],
        json={"personality": "性格1"},   # 和原值一样
    )
    assert r.status_code == 200

    overview = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()
    assert overview["total_active"] == 0


def test_record_change_different_fields_count_separately(client: TestClient, make_user):
    """同角色改 2 个字段 = 2 个反事实(name + identity 各一)。"""
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 1)
    cid = char_ids[0]

    client.patch(
        f"/api/characters/{cid}", headers=user["headers"],
        json={"name": "新名", "identity": "新身份"},
    )
    overview = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()
    assert overview["total_active"] == 2


def test_position_color_changes_not_tracked(client: TestClient, make_user):
    """改 position_x / color → 不该 record 反事实(纯视觉,不影响推演)。"""
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 1)
    cid = char_ids[0]

    client.patch(
        f"/api/characters/{cid}", headers=user["headers"],
        json={"position_x": 99.0, "position_y": 88.0, "color": "#abcdef"},
    )
    overview = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()
    assert overview["total_active"] == 0


# ============================================================
# C. event / relationship PATCH 也跟踪
# ============================================================

def test_event_patch_records_counterfactual(client: TestClient, make_user):
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 2)
    # 创个 event
    r = client.post(
        f"/api/projects/{project_id}/events", headers=user["headers"],
        json={"description": "原描述", "participants": [char_ids[0]]},
    )
    assert r.status_code == 201
    eid = r.json()["id"]

    # 改 description
    client.patch(
        f"/api/events/{eid}", headers=user["headers"],
        json={"description": "改描述"},
    )
    overview = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()
    assert overview["total_active"] == 1
    assert overview["by_type"]["event"] == 1


def test_relationship_patch_records_counterfactual(client: TestClient, make_user):
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 2)
    r = client.post(
        f"/api/projects/{project_id}/relationships", headers=user["headers"],
        json={
            "source_id": char_ids[0],
            "target_id": char_ids[1],
            "type": "朋友",
            "description": "原描述",
        },
    )
    assert r.status_code == 201
    rid = r.json()["id"]

    client.patch(
        f"/api/relationships/{rid}", headers=user["headers"],
        json={"type": "敌对"},
    )
    overview = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()
    assert overview["total_active"] == 1
    assert overview["by_type"]["relationship"] == 1


# ============================================================
# D. revert
# ============================================================

def test_revert_restores_db_field_and_marks_reverted(client: TestClient, make_user):
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 1)
    cid = char_ids[0]

    # 改 personality
    client.patch(
        f"/api/characters/{cid}", headers=user["headers"],
        json={"personality": "改后的性格"},
    )
    cfs = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()["items"]
    cf_id = cfs[0]["id"]

    # 撤销
    r_revert = client.post(
        f"/api/counterfactuals/{cf_id}/revert", headers=user["headers"],
    )
    assert r_revert.status_code == 200
    body = r_revert.json()
    assert body["target_field_restored"] is True
    assert body["counterfactual"]["is_active"] is False
    assert body["counterfactual"]["reverted_at"] is not None

    # 验证 db 字段已还原
    char_after = client.get(
        f"/api/characters/{cid}", headers=user["headers"],
    ).json()
    assert char_after["personality"] == "性格1"   # 原值

    # 反事实总数现在 = 0(active 列表过滤了 reverted)
    overview = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()
    assert overview["total_active"] == 0


def test_revert_idempotent(client: TestClient, make_user):
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 1)
    cid = char_ids[0]
    client.patch(
        f"/api/characters/{cid}", headers=user["headers"],
        json={"personality": "x"},
    )
    cf_id = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()["items"][0]["id"]
    r1 = client.post(f"/api/counterfactuals/{cf_id}/revert", headers=user["headers"])
    assert r1.status_code == 200
    r2 = client.post(f"/api/counterfactuals/{cf_id}/revert", headers=user["headers"])
    assert r2.status_code == 200
    assert r2.json()["target_field_restored"] is False
    assert "已经撤销" in (r2.json()["restore_error"] or "")


def test_revert_cross_user_404(client: TestClient, make_user):
    alice = make_user("alice")
    bob = make_user("bob")
    project_id, char_ids = _create_project_with_n_characters(client, alice["headers"], 1)
    client.patch(
        f"/api/characters/{char_ids[0]}", headers=alice["headers"],
        json={"personality": "x"},
    )
    cf_id = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=alice["headers"],
    ).json()["items"][0]["id"]
    r = client.post(f"/api/counterfactuals/{cf_id}/revert", headers=bob["headers"])
    assert r.status_code == 404


# ============================================================
# E. list / overview
# ============================================================

def test_overview_buckets_by_type(client: TestClient, make_user):
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 3)

    # 1 character + 1 event + 1 relationship 各一改
    client.patch(f"/api/characters/{char_ids[0]}", headers=user["headers"], json={"personality": "x"})

    e = client.post(
        f"/api/projects/{project_id}/events", headers=user["headers"],
        json={"description": "原", "participants": [char_ids[0]]},
    ).json()
    client.patch(f"/api/events/{e['id']}", headers=user["headers"], json={"description": "改"})

    rel = client.post(
        f"/api/projects/{project_id}/relationships", headers=user["headers"],
        json={"source_id": char_ids[0], "target_id": char_ids[1], "type": "朋友", "description": ""},
    ).json()
    client.patch(f"/api/relationships/{rel['id']}", headers=user["headers"], json={"type": "敌对"})

    overview = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()
    assert overview["total_active"] == 3
    assert overview["by_type"] == {"character": 1, "event": 1, "relationship": 1}


# ============================================================
# F. BFS affected_node_ids
# ============================================================

def test_bfs_hops_zero_returns_only_seeds(client: TestClient, make_user):
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 4)
    # 建 1 个关系让图有边
    client.post(
        f"/api/projects/{project_id}/relationships", headers=user["headers"],
        json={"source_id": char_ids[0], "target_id": char_ids[1], "type": "朋友", "description": ""},
    )

    from app.services.counterfactual_service import compute_affected_node_ids
    conn = _db_conn()
    try:
        affected = compute_affected_node_ids(conn, project_id, [char_ids[0]], hops=0)
    finally:
        conn.close()
    assert affected == {char_ids[0]}


def test_bfs_hops_one_includes_direct_neighbors(client: TestClient, make_user):
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 4)
    # 边:0-1, 1-2(不连 3)
    client.post(
        f"/api/projects/{project_id}/relationships", headers=user["headers"],
        json={"source_id": char_ids[0], "target_id": char_ids[1], "type": "朋友", "description": ""},
    )
    client.post(
        f"/api/projects/{project_id}/relationships", headers=user["headers"],
        json={"source_id": char_ids[1], "target_id": char_ids[2], "type": "朋友", "description": ""},
    )

    from app.services.counterfactual_service import compute_affected_node_ids
    conn = _db_conn()
    try:
        affected_1 = compute_affected_node_ids(conn, project_id, [char_ids[0]], hops=1)
        affected_2 = compute_affected_node_ids(conn, project_id, [char_ids[0]], hops=2)
    finally:
        conn.close()
    assert affected_1 == {char_ids[0], char_ids[1]}
    assert affected_2 == {char_ids[0], char_ids[1], char_ids[2]}
    # char_ids[3] 不在任何一跳内


def test_bfs_event_participants_count_as_edges(client: TestClient, make_user):
    """events.participants 应当 count 为图的边(character ↔ event)。"""
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 3)
    e = client.post(
        f"/api/projects/{project_id}/events", headers=user["headers"],
        json={"description": "聚会", "participants": [char_ids[0], char_ids[1]]},
    ).json()
    eid = e["id"]

    from app.services.counterfactual_service import compute_affected_node_ids
    conn = _db_conn()
    try:
        # 从 char[0] 出发 1 跳:应触达 event(直接邻居)
        affected = compute_affected_node_ids(conn, project_id, [char_ids[0]], hops=1)
    finally:
        conn.close()
    assert eid in affected
    assert char_ids[0] in affected
    # 2 跳应触达 char[1](通过 event)
    conn = _db_conn()
    try:
        affected_2 = compute_affected_node_ids(conn, project_id, [char_ids[0]], hops=2)
    finally:
        conn.close()
    assert char_ids[1] in affected_2


# ============================================================
# G. derive_reshape_dimensions
# ============================================================

def test_reshape_preview_three_dimensions(client: TestClient, make_user):
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 3)
    # 改 1 个角色 → touched=1
    client.patch(
        f"/api/characters/{char_ids[0]}", headers=user["headers"],
        json={"personality": "改"},
    )

    r = client.get(
        f"/api/projects/{project_id}/reshape_preview?reshape_percent=50",
        headers=user["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["reshape_percent"] == 50
    assert body["max_touched_characters"] == 10   # 50% → ceil(50/5) = 10
    assert body["graph_distance_hops"] == 4       # (50-10)//10 = 4
    assert body["current_touched_count"] == 1
    assert char_ids[0] in body["current_affected_node_ids"]
    assert body["plan_max_percent"] in (30, 60, 90)   # 取决于 plan(free=30 默认)


def test_reshape_preview_rejects_out_of_range(client: TestClient, make_user):
    user = make_user("alice")
    project_id, _ = _create_project_with_n_characters(client, user["headers"], 1)
    r = client.get(
        f"/api/projects/{project_id}/reshape_preview?reshape_percent=5",
        headers=user["headers"],
    )
    assert r.status_code == 422   # Pydantic ge=10 校验


# ============================================================
# H. simulation 触发时校验角色数 + mark_applied
# ============================================================

def test_simulation_create_blocks_when_touched_exceeds_reshape_limit(
    client: TestClient, make_user, monkeypatch,
):
    """改 5 个角色但 reshape=10%(上限 2)→ 触发推演返 422 RESHAPE_CHARACTER_LIMIT_EXCEEDED。"""
    import dataclasses
    user = make_user("alice")
    # 用 founder 邮箱绕开 continuation 配额
    from app import deps
    monkeypatch.setattr(
        deps, "settings",
        dataclasses.replace(deps.settings, founder_emails=frozenset({user["email"].lower()})),
    )
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 5)

    # 改 5 个角色的 personality
    for cid in char_ids:
        client.patch(
            f"/api/characters/{cid}", headers=user["headers"],
            json={"personality": "改"},
        )

    # 触发推演 reshape=10%(上限 max(1,ceil(10/5))=2)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=user["headers"],
        json={
            "divergence": "x" * 30,
            "reshape_percent": 10,
        },
    )
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == "RESHAPE_CHARACTER_LIMIT_EXCEEDED"
    assert r.json()["detail"]["current"] == 5
    assert r.json()["detail"]["limit"] == 2


def test_simulation_create_marks_active_counterfactuals_applied(
    client: TestClient, make_user, sync_simulation_runner, patched_simulation_llm, monkeypatch,
):
    """create_simulation 后 active 反事实应记录该 simulation_id 到 applied_in_simulations。"""
    import dataclasses
    user = make_user("alice")
    from app import deps
    monkeypatch.setattr(
        deps, "settings",
        dataclasses.replace(deps.settings, founder_emails=frozenset({user["email"].lower()})),
    )
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 4)

    # 改 1 个角色
    client.patch(
        f"/api/characters/{char_ids[0]}", headers=user["headers"],
        json={"personality": "豁达开朗"},
    )

    # 触发推演 reshape=50%(上限 10,1 个角色 OK)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=user["headers"],
        json={
            "divergence": "x" * 30,
            "reshape_percent": 50,
        },
    )
    assert r.status_code == 201, r.text
    sim_id = r.json()["simulation_id"]

    # 验证反事实 applied_in_simulations 含该 sim_id
    cfs = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()["items"]
    assert len(cfs) == 1
    assert sim_id in cfs[0]["applied_in_simulations"]


# ============================================================
# I. director prompt 注入(白盒测试 — 直接调函数检查文本)
# ============================================================

def test_render_counterfactual_section_text_includes_field_label_and_values(
    client: TestClient, make_user,
):
    """build_director_context + render_section 应输出含中文字段名 / 原作 / 用户重塑的文本块。"""
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 1)
    client.patch(
        f"/api/characters/{char_ids[0]}", headers=user["headers"],
        json={"personality": "豁达开朗"},
    )

    from app.services.counterfactual_service import (
        build_director_context, render_counterfactual_section_text,
    )
    conn = _db_conn()
    try:
        ctx = build_director_context(conn, project_id)
        text = render_counterfactual_section_text(ctx)
    finally:
        conn.close()
    assert ctx["active_count"] == 1
    assert "反事实变量上下文" in text
    assert "「性格」" in text
    assert "豁达开朗" in text
    assert "性格1" in text   # 原值
    assert "角色01" in text


# ============================================================
# J. Sprint 2.C+:显式创建端点 + user_intent + world type + selected_ids 子集
# ============================================================


def test_explicit_create_counterfactual_with_user_intent(
    client: TestClient, make_user,
):
    """显式 POST /api/projects/{id}/counterfactuals 创建反事实带 user_intent。"""
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 1)

    r = client.post(
        f"/api/projects/{project_id}/counterfactuals",
        headers=user["headers"],
        json={
            "target_type": "character",
            "target_id": char_ids[0],
            "field": "personality",
            "old_value": "性格1",
            "new_value": "勇敢果断",
            "user_intent": "我想让小红面对魔兽时不再逃跑而是迎敌",
        },
    )
    assert r.status_code == 201, r.text
    cf = r.json()
    assert cf["target_type"] == "character"
    assert cf["field"] == "personality"
    assert cf["new_value"] == "勇敢果断"
    assert cf["user_intent"] == "我想让小红面对魔兽时不再逃跑而是迎敌"
    assert cf["is_active"] is True


def test_explicit_create_world_counterfactual_six_dimensions(
    client: TestClient, make_user,
):
    """POST /counterfactuals/world 创建世界观反事实 — 6 个维度全可用。"""
    user = make_user("alice")
    project_id, _ = _create_project_with_n_characters(client, user["headers"], 1)

    fields = ["genre", "setting", "magic_system", "time_axis", "tone", "free_form"]
    for field in fields:
        r = client.post(
            f"/api/projects/{project_id}/counterfactuals/world",
            headers=user["headers"],
            json={
                "field": field,
                "old_value": f"原作{field}值",
                "new_value": f"改后{field}值",
                "user_intent": f"想要{field}方向的改造",
            },
        )
        assert r.status_code == 201, f"{field} 创建失败:{r.text}"
        cf = r.json()
        assert cf["target_type"] == "world"
        assert cf["target_id"] == "_global_"
        assert cf["field"] == field

    overview = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()
    assert overview["total_active"] == 6


def test_world_field_invalid_rejected(client: TestClient, make_user):
    """world field 非 6 维之一 → 422。"""
    user = make_user("alice")
    project_id, _ = _create_project_with_n_characters(client, user["headers"], 1)

    r = client.post(
        f"/api/projects/{project_id}/counterfactuals/world",
        headers=user["headers"],
        json={"field": "invalid_field", "new_value": "x"},
    )
    assert r.status_code == 422   # Pydantic Literal 校验失败


def test_world_counterfactual_does_not_count_against_character_limit(
    client: TestClient, make_user, sync_simulation_runner, patched_simulation_llm, monkeypatch,
):
    """世界观反事实不算 max_touched_characters 限制(reshape 10% 上限 2 时,可加 N 个 world)。"""
    import dataclasses
    user = make_user("alice")
    from app import deps
    monkeypatch.setattr(
        deps, "settings",
        dataclasses.replace(deps.settings, founder_emails=frozenset({user["email"].lower()})),
    )
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 5)

    # 加 6 个世界观反事实(超过任何角色数限制都没事)
    for f in ["genre", "setting", "magic_system", "time_axis", "tone", "free_form"]:
        client.post(
            f"/api/projects/{project_id}/counterfactuals/world",
            headers=user["headers"],
            json={"field": f, "new_value": f"new_{f}"},
        )

    # 触发推演 reshape=10%(角色上限 2,但 0 个角色反事实 + 6 个世界观 → 应通过)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=user["headers"],
        json={"divergence": "x" * 30, "reshape_percent": 10},
    )
    assert r.status_code == 201, r.text


def test_simulation_with_selected_counterfactual_ids_subset(
    client: TestClient, make_user, sync_simulation_runner, patched_simulation_llm, monkeypatch,
):
    """推演带 selected_counterfactual_ids 只用子集,link 表落子集 + applied_in_simulations 也是子集。"""
    import dataclasses
    user = make_user("alice")
    from app import deps
    monkeypatch.setattr(
        deps, "settings",
        dataclasses.replace(deps.settings, founder_emails=frozenset({user["email"].lower()})),
    )
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 4)

    # 改 3 个角色,产生 3 个反事实
    for cid in char_ids[:3]:
        client.patch(
            f"/api/characters/{cid}", headers=user["headers"],
            json={"personality": f"changed_{cid[:4]}"},
        )
    cfs = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()["items"]
    assert len(cfs) == 3
    selected = [cfs[0]["id"], cfs[1]["id"]]   # 只选前 2 个

    # 触发推演 reshape 50%(角色上限 10,2 个角色绝对够)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=user["headers"],
        json={
            "divergence": "x" * 30,
            "reshape_percent": 50,
            "selected_counterfactual_ids": selected,
        },
    )
    assert r.status_code == 201, r.text
    sim_id = r.json()["simulation_id"]

    # 验证只 2 个反事实 applied(第 3 个未选)
    cfs_after = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()["items"]
    applied_count = sum(1 for c in cfs_after if sim_id in c["applied_in_simulations"])
    assert applied_count == 2

    # 验证 link 表
    from app.services.counterfactual_service import get_linked_counterfactual_ids
    conn = _db_conn()
    try:
        linked = get_linked_counterfactual_ids(conn, sim_id)
    finally:
        conn.close()
    assert linked is not None
    assert set(linked) == set(selected)


def test_simulation_with_empty_selected_ids_means_no_counterfactuals(
    client: TestClient, make_user, sync_simulation_runner, patched_simulation_llm, monkeypatch,
):
    """selected_counterfactual_ids=[] → 显式纯按原作演,不用任何反事实。"""
    import dataclasses
    user = make_user("alice")
    from app import deps
    monkeypatch.setattr(
        deps, "settings",
        dataclasses.replace(deps.settings, founder_emails=frozenset({user["email"].lower()})),
    )
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 4)

    # 改 5 个角色 → 5 反事实 active
    for cid in char_ids[:3]:
        client.patch(
            f"/api/characters/{cid}", headers=user["headers"],
            json={"personality": f"x_{cid[:4]}"},
        )
    # 5 个角色 reshape 10%(上限 2)— 但 selected=[] 算 0 角色 → 应通过
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=user["headers"],
        json={
            "divergence": "x" * 30,
            "reshape_percent": 10,
            "selected_counterfactual_ids": [],
        },
    )
    assert r.status_code == 201, r.text
    sim_id = r.json()["simulation_id"]

    # 验证没有反事实 applied
    cfs_after = client.get(
        f"/api/projects/{project_id}/counterfactuals", headers=user["headers"],
    ).json()["items"]
    applied_count = sum(1 for c in cfs_after if sim_id in c["applied_in_simulations"])
    assert applied_count == 0


def test_user_intent_appears_in_director_context_text(client: TestClient, make_user):
    """user_intent 应在 render_counterfactual_section_text 输出里以 '★ 用户意图' 出现。"""
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 1)

    client.post(
        f"/api/projects/{project_id}/counterfactuals",
        headers=user["headers"],
        json={
            "target_type": "character",
            "target_id": char_ids[0],
            "field": "personality",
            "old_value": "懦弱怕事",
            "new_value": "勇敢果断",
            "user_intent": "我想让小红面对魔兽时不再逃跑而是迎敌",
        },
    )
    from app.services.counterfactual_service import (
        build_director_context, render_counterfactual_section_text,
    )
    conn = _db_conn()
    try:
        ctx = build_director_context(conn, project_id)
        text = render_counterfactual_section_text(ctx)
    finally:
        conn.close()
    assert "★ 用户意图" in text
    assert "面对魔兽" in text


def test_world_counterfactual_appears_in_director_context_first(
    client: TestClient, make_user,
):
    """世界观反事实在 director context 里应排在角色 / 事件反事实之前(优先级最高)。"""
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 1)

    # 先加角色反事实
    client.patch(
        f"/api/characters/{char_ids[0]}", headers=user["headers"],
        json={"personality": "x"},
    )
    # 后加世界观反事实
    client.post(
        f"/api/projects/{project_id}/counterfactuals/world",
        headers=user["headers"],
        json={"field": "setting", "old_value": "都市", "new_value": "星际"},
    )
    from app.services.counterfactual_service import (
        build_director_context, render_counterfactual_section_text,
    )
    conn = _db_conn()
    try:
        ctx = build_director_context(conn, project_id)
        text = render_counterfactual_section_text(ctx)
    finally:
        conn.close()
    # 世界观区出现在角色区之前
    world_idx = text.find("### 世界观反事实")
    char_idx = text.find("### 角色反事实")
    assert world_idx >= 0 and char_idx >= 0
    assert world_idx < char_idx, "世界观应排在角色之前(最高优先级)"


def test_subset_excludes_world_when_selected_only_character(
    client: TestClient, make_user,
):
    """selected_ids 只选角色反事实 → director context 不出现世界观区。"""
    user = make_user("alice")
    project_id, char_ids = _create_project_with_n_characters(client, user["headers"], 1)

    # 加 1 角色 + 1 世界观
    char_cf = client.post(
        f"/api/projects/{project_id}/counterfactuals",
        headers=user["headers"],
        json={
            "target_type": "character", "target_id": char_ids[0],
            "field": "personality", "old_value": "性格1", "new_value": "x",
        },
    ).json()
    client.post(
        f"/api/projects/{project_id}/counterfactuals/world",
        headers=user["headers"],
        json={"field": "setting", "new_value": "星际"},
    )

    # 只选角色反事实
    from app.services.counterfactual_service import (
        build_director_context, render_counterfactual_section_text,
    )
    conn = _db_conn()
    try:
        ctx = build_director_context(conn, project_id, [char_cf["id"]])
        text = render_counterfactual_section_text(ctx)
    finally:
        conn.close()
    assert ctx["active_count"] == 1
    assert "### 世界观反事实" not in text
    assert "### 角色反事实" in text
