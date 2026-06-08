"""
续作家族树 endpoint 端到端测试(2026-06-06)。

测试矩阵:
  1. 空项目返 nodes=[] / stats=zero
  2. 1 独立 sim 返 1 节点 + max_depth=0 + roots=1
  3. 滚雪球链 A → B → C:depth(C)=2,parent_ids 正确
  4. 反事实组合批次:combination_runs 列表 + 同 combo_id 兄弟分组
  5. 越权:别的用户拿不到本 user 项目数据(404)
  6. 多 parent(theoretically context_simulation_ids 长度 > 1)— 取最后一个算 depth
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def reset_byok_context_for_family_tree():
    from app.services.byok_context import set_current_user_id
    set_current_user_id(None)
    yield
    set_current_user_id(None)


def _make_project(client, user, name="家族树测试"):
    r = client.post(
        "/api/projects",
        json={"name": name, "type": "novel", "tags": []},
        headers=user["headers"],
    )
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _seed_characters(client, user, pid):
    """造 3 个角色 — build_characters_snapshot 要求 ≥3"""
    for name in ("主角甲", "配角乙", "反派丙"):
        r = client.post(
            f"/api/projects/{pid}/characters",
            json={
                "name": name,
                "identity": f"{name}身份",
                "personality": f"{name}性格",
                "is_protagonist": name == "主角甲",
            },
            headers=user["headers"],
        )
        assert r.status_code in (200, 201), r.text


def _insert_sim_directly(
    pid, user_id, *,
    context_ids=None,
    combo_run_id=None,
    tree_path=None,
    state="done",
    narrative="测试 narrative 内容" * 10,
    divergence="测试分歧点",
):
    """直接 INSERT sim 行,跳过 create_simulation 的复杂校验(配额 / 反事实压力等)。

    项目必须已有 ≥3 角色 — 调用方需先 _seed_characters。
    """
    import uuid
    from app.db import get_connection
    from app.services.simulation_service import build_characters_snapshot

    sid = uuid.uuid4().hex
    conn = get_connection()
    try:
        snapshot = build_characters_snapshot(conn, pid)
        conn.execute(
            """INSERT INTO simulations
               (id, project_id, user_id, divergence, reshape_percent, rounds_planned,
                target_chars, style, custom_style_hint, context_simulation_ids,
                characters_snapshot, state, current_round, narrative,
                tokens_input, tokens_output, cost_yuan, created_at,
                combination_run_id, tree_path_json, mode)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'),
                       ?, ?, ?)""",
            (
                sid, pid, user_id, divergence, 50, 5, 4000, "A", None,
                json.dumps(context_ids or []),
                json.dumps(snapshot),
                state, 0, narrative,
                0, 0, 0.0,
                combo_run_id,
                json.dumps(tree_path) if tree_path else None,
                "evolution",
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return sid


def test_empty_project_returns_zero_stats(make_user, client):
    """空项目家族树:0 节点 / 0 批次(无 sim 也无角色,endpoint 不应崩)"""
    u = make_user("ftempty")
    pid = _make_project(client, u)
    # 注:这条不用 _seed_characters — 空项目本来就允许查询家族树(只是返空数组)

    r = client.get(
        f"/api/projects/{pid}/simulation_family_tree",
        headers=u["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["nodes"] == []
    assert body["combination_runs"] == []
    assert body["stats"]["total_sims"] == 0
    assert body["stats"]["roots"] == 0
    assert body["stats"]["max_depth"] == 0
    assert body["stats"]["combo_batches"] == 0


def test_single_independent_sim(make_user, client):
    """1 个独立 sim:depth=0 / roots=1"""
    u = make_user("ftsingle")
    pid = _make_project(client, u)
    _seed_characters(client, u, pid)
    sid = _insert_sim_directly(pid, u["user_id"])

    r = client.get(
        f"/api/projects/{pid}/simulation_family_tree",
        headers=u["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["nodes"]) == 1
    n = body["nodes"][0]
    assert n["id"] == sid
    assert n["parent_ids"] == []
    assert n["inheritance_depth"] == 0
    assert body["stats"]["roots"] == 1
    assert body["stats"]["max_depth"] == 0


def test_snowball_chain_depth(make_user, client):
    """滚雪球链 A → B → C:depth(A)=0, depth(B)=1, depth(C)=2"""
    u = make_user("ftchain")
    pid = _make_project(client, u)
    _seed_characters(client, u, pid)

    sa = _insert_sim_directly(pid, u["user_id"])
    sb = _insert_sim_directly(pid, u["user_id"], context_ids=[sa])
    sc = _insert_sim_directly(pid, u["user_id"], context_ids=[sa, sb])

    r = client.get(
        f"/api/projects/{pid}/simulation_family_tree",
        headers=u["headers"],
    )
    body = r.json()
    by_id = {n["id"]: n for n in body["nodes"]}
    assert by_id[sa]["inheritance_depth"] == 0
    assert by_id[sb]["inheritance_depth"] == 1
    assert by_id[sc]["inheritance_depth"] == 2
    assert by_id[sc]["parent_ids"] == [sa, sb]
    assert body["stats"]["max_depth"] == 2
    assert body["stats"]["roots"] == 1


def test_combination_runs_listed(make_user, client):
    """有反事实组合批次 → combination_runs 列出 + 同 combo 的 sim 标 combination_run_id"""
    u = make_user("ftcombo")
    pid = _make_project(client, u)
    _seed_characters(client, u, pid)

    # 手插一行 combination_run + 2 个 sim 兄弟
    import uuid
    from app.db import get_connection
    combo_id = uuid.uuid4().hex
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO counterfactual_combination_runs
               (id, project_id, user_id, selected_variables_json,
                total_combinations, state, error_message, created_at, completed_at)
               VALUES (?, ?, ?, ?, ?, 'pending', NULL, datetime('now'), NULL)""",
            (combo_id, pid, u["user_id"],
             json.dumps([{"counterfactual_id": "x", "label_a": "原", "label_b": "改"}]),
             2),
        )
        conn.commit()
    finally:
        conn.close()

    s1 = _insert_sim_directly(pid, u["user_id"], combo_run_id=combo_id, tree_path=["a"])
    s2 = _insert_sim_directly(pid, u["user_id"], combo_run_id=combo_id, tree_path=["b"])

    r = client.get(
        f"/api/projects/{pid}/simulation_family_tree",
        headers=u["headers"],
    )
    body = r.json()
    assert body["stats"]["combo_batches"] == 1
    assert body["combination_runs"][0]["id"] == combo_id
    assert body["combination_runs"][0]["selected_variables_count"] == 1
    by_id = {n["id"]: n for n in body["nodes"]}
    assert by_id[s1]["combination_run_id"] == combo_id
    assert by_id[s1]["tree_path"] == ["a"]
    assert by_id[s2]["tree_path"] == ["b"]


def test_other_user_cannot_access(make_user, client):
    """别人的项目 → 403"""
    owner = make_user("ftowner")
    intruder = make_user("ftintruder")
    pid = _make_project(client, owner)

    r = client.get(
        f"/api/projects/{pid}/simulation_family_tree",
        headers=intruder["headers"],
    )
    # get_project_or_403 在不属于 user 时实际返 404(项目对该 user 不可见)
    assert r.status_code in (403, 404)


def test_depth_uses_last_parent(make_user, client):
    """多 parent 取 ctx_ids[-1](经验值 35:直接父辈在尾部)"""
    u = make_user("ftmulti")
    pid = _make_project(client, u)
    _seed_characters(client, u, pid)

    sa = _insert_sim_directly(pid, u["user_id"])                 # depth=0
    sb = _insert_sim_directly(pid, u["user_id"], context_ids=[sa])   # depth=1
    # sc 的 ctx = [sa, sb] — 直接父是 sb,depth = depth(sb)+1 = 2
    sc = _insert_sim_directly(pid, u["user_id"], context_ids=[sa, sb])

    r = client.get(
        f"/api/projects/{pid}/simulation_family_tree",
        headers=u["headers"],
    )
    body = r.json()
    by_id = {n["id"]: n for n in body["nodes"]}
    assert by_id[sc]["inheritance_depth"] == 2  # 用 ctx[-1]=sb 算
