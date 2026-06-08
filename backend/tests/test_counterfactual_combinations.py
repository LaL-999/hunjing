"""反事实组合树测试 — Sprint 6.A2 CT(2026-05-21)。

覆盖:
  - enumerate_combinations N=1/2/3 笛卡尔积顺序
  - tree_path_to_selected_cf_ids 翻译规则(a=不启用 / b=启用)
  - preview_cost 估算合理性
  - start_combination_run 落库 + 各 sim 关联正确
  - 配额拦截:>3 变量返 400
  - 校验:不存在 ID / 重复 ID / 跨项目 ID 返 422
  - GET /tree:树结构 + 叶 sim 状态
  - 跨用户 404
"""
from __future__ import annotations

from fastapi.testclient import TestClient


# ============================================================
# helpers
# ============================================================

def _create_project_with_chars(client, headers, n_chars=4):
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": "CT 测试", "type": "novel", "mode": "initial"},
    )
    assert p.status_code == 201, p.text
    pid = p.json()["id"]
    char_ids = []
    for i in range(n_chars):
        r = client.post(
            f"/api/projects/{pid}/characters", headers=headers,
            json={
                "name": f"角色{i+1}",
                "identity": f"身份{i+1}",
                "personality": f"性格{i+1}",
                "quotes": [],
                "no_go_list": [],
            },
        )
        assert r.status_code == 201
        char_ids.append(r.json()["id"])
    return pid, char_ids


def _create_cf(client, headers, project_id, char_id, field, new_val,
               old_val="原值"):
    """显式创建一个 character 反事实,返回 cf_id。"""
    r = client.post(
        f"/api/projects/{project_id}/counterfactuals", headers=headers,
        json={
            "target_type": "character",
            "target_id": char_id,
            "field": field,
            "old_value": old_val,
            "new_value": new_val,
            "user_intent": f"想让角色 {field} 变成 {new_val}",
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ============================================================
# 单元测试 — enumerate / translate / preview
# ============================================================

def test_enumerate_combinations_n1():
    from app.services.counterfactual_combination_service import (
        enumerate_combinations,
    )
    result = enumerate_combinations(1)
    assert result == [["a"], ["b"]]


def test_enumerate_combinations_n2():
    from app.services.counterfactual_combination_service import (
        enumerate_combinations,
    )
    result = enumerate_combinations(2)
    assert result == [
        ["a", "a"], ["a", "b"],
        ["b", "a"], ["b", "b"],
    ]


def test_enumerate_combinations_n3_count_and_order():
    from app.services.counterfactual_combination_service import (
        enumerate_combinations,
    )
    result = enumerate_combinations(3)
    assert len(result) == 8
    # 第一个全 'a',最后一个全 'b'(字典序)
    assert result[0] == ["a", "a", "a"]
    assert result[-1] == ["b", "b", "b"]


def test_enumerate_combinations_invalid_count():
    from app.services.counterfactual_combination_service import (
        CombinationLimitExceeded, enumerate_combinations,
    )
    import pytest
    with pytest.raises(CombinationLimitExceeded):
        enumerate_combinations(0)
    with pytest.raises(CombinationLimitExceeded):
        enumerate_combinations(4)


def test_tree_path_translation_only_b_enables_cf():
    from app.models.counterfactual_combination_run import SelectedVariable
    from app.services.counterfactual_combination_service import (
        tree_path_to_selected_cf_ids,
    )
    v1 = SelectedVariable("cf_A", "原-A", "改-A")
    v2 = SelectedVariable("cf_B", "原-B", "改-B")
    v3 = SelectedVariable("cf_C", "原-C", "改-C")
    # 全 'a' = 空(纯原作)
    assert tree_path_to_selected_cf_ids(["a", "a", "a"], [v1, v2, v3]) == []
    # 全 'b' = 全部启用
    assert tree_path_to_selected_cf_ids(["b", "b", "b"], [v1, v2, v3]) == [
        "cf_A", "cf_B", "cf_C",
    ]
    # 'aba' 只启用第 2 个
    assert tree_path_to_selected_cf_ids(["a", "b", "a"], [v1, v2, v3]) == [
        "cf_B",
    ]


def test_preview_cost_scales_with_count():
    from app.services.counterfactual_combination_service import preview_cost
    p1 = preview_cost(1, 50, 4000)
    p3 = preview_cost(3, 50, 4000)
    assert p1.total_combinations == 2
    assert p3.total_combinations == 8
    # 3 变量比 1 变量 token 多 4 倍(8 个 sim vs 2 个 sim)— token 总量不受并发影响
    assert p3.estimated_total_tokens == p1.estimated_total_tokens * 4
    # CT-OPT.2(2026-05-21):时间不再线性放大;ThreadPoolExecutor 并发 +
    # `concurrency_overhead = 1 + 0.15*(N-1)/N` 公式 → 8 个 sim 实际耗时 ≈ 单 sim × 1.13;
    # 因此 p3 总时间应明显 < p1 × 4(并发节省),但仍略 ≥ p1(更多 sim 有少量调度开销)
    assert p3.estimated_total_minutes >= p1.estimated_total_minutes
    assert p3.estimated_total_minutes < p1.estimated_total_minutes * 2


# ============================================================
# 集成测试 — 通过 router 端点
# ============================================================

def test_preview_endpoint(client: TestClient, make_user):
    user = make_user("alice")
    pid, _ = _create_project_with_chars(client, user["headers"], 4)
    r = client.post(
        f"/api/projects/{pid}/counterfactual-combinations/preview",
        headers=user["headers"],
        json={
            "selected_variable_count": 3,
            "reshape_percent": 50,
            "target_chars": 4000,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_combinations"] == 8
    assert body["estimated_total_tokens"] > 0
    assert body["estimated_credits"] > 0


def test_preview_rejects_more_than_3_variables(client: TestClient, make_user):
    user = make_user("bob")
    pid, _ = _create_project_with_chars(client, user["headers"], 4)
    r = client.post(
        f"/api/projects/{pid}/counterfactual-combinations/preview",
        headers=user["headers"],
        json={
            "selected_variable_count": 4,
            "reshape_percent": 50,
            "target_chars": 4000,
        },
    )
    # Pydantic 422(ge=1, le=3 约束)
    assert r.status_code in (400, 422), r.text


def test_create_combination_run_falls_through_to_sims(
    client: TestClient, make_user, patched_simulation_llm, monkeypatch,
):
    """完整创建链路:3 个反事实变量 → 8 个 sim 落库,各自关联 combination_run_id + tree_path。

    用 monkeypatch 屏蔽 outline_async kickoff 不真跑(只验证 sim 行 + 关联字段)。
    """
    # 屏蔽 outline 异步启动 — 我们只验证 sim 落库,不验证 outline 是否真生成
    monkeypatch.setattr(
        "app.routers.simulations._outline_async_runner",
        lambda sim_id: None,
    )

    user = make_user("carol")
    pid, char_ids = _create_project_with_chars(client, user["headers"], 4)

    # 创建 3 个反事实
    cf1 = _create_cf(client, user["headers"], pid, char_ids[0],
                     "personality", "勇敢")
    cf2 = _create_cf(client, user["headers"], pid, char_ids[1],
                     "personality", "冷静")
    cf3 = _create_cf(client, user["headers"], pid, char_ids[2],
                     "personality", "热情")

    # 创建批次
    r = client.post(
        f"/api/projects/{pid}/counterfactual-combinations",
        headers=user["headers"],
        json={
            "selected_variables": [
                {"counterfactual_id": cf1, "label_a": "原-性格1", "label_b": "改-勇敢"},
                {"counterfactual_id": cf2, "label_a": "原-性格2", "label_b": "改-冷静"},
                {"counterfactual_id": cf3, "label_a": "原-性格3", "label_b": "改-热情"},
            ],
            "reshape_percent": 30,
            "target_chars": 2000,
            "style": "A",
            "use_outline_first": True,
            "divergence": "三人深夜进入韩紫雨家",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["total_combinations"] == 8
    assert body["state"] in ("generating", "partial")  # 至少有 sim 创建成功
    combo_id = body["id"]

    # 拉树验证
    tree_r = client.get(
        f"/api/counterfactual-combinations/{combo_id}/tree",
        headers=user["headers"],
    )
    assert tree_r.status_code == 200, tree_r.text
    tree = tree_r.json()
    assert tree["combination_run"]["id"] == combo_id
    assert tree["combination_run"]["total_combinations"] == 8
    # 8 个叶 sim 全部落库
    assert len(tree["leaves"]) == 8
    # 验证 tree_path 覆盖全 2^3 组合
    paths_seen = {tuple(leaf["tree_path"]) for leaf in tree["leaves"]}
    assert len(paths_seen) == 8  # 8 个不同路径
    assert ("a", "a", "a") in paths_seen
    assert ("b", "b", "b") in paths_seen


def test_create_rejects_nonexistent_counterfactual_id(
    client: TestClient, make_user,
):
    user = make_user("david")
    pid, _ = _create_project_with_chars(client, user["headers"], 4)
    r = client.post(
        f"/api/projects/{pid}/counterfactual-combinations",
        headers=user["headers"],
        json={
            "selected_variables": [
                {"counterfactual_id": "non_existent_id_xyz",
                 "label_a": "原", "label_b": "改"},
            ],
            "reshape_percent": 50,
            "target_chars": 4000,
            "style": "A",
            "use_outline_first": True,
            "divergence": "测试",
        },
    )
    assert r.status_code == 422, r.text


def test_create_rejects_duplicate_counterfactual_ids(
    client: TestClient, make_user,
):
    user = make_user("eve")
    pid, char_ids = _create_project_with_chars(client, user["headers"], 4)
    cf1 = _create_cf(client, user["headers"], pid, char_ids[0],
                     "personality", "勇敢")
    r = client.post(
        f"/api/projects/{pid}/counterfactual-combinations",
        headers=user["headers"],
        json={
            "selected_variables": [
                {"counterfactual_id": cf1, "label_a": "原", "label_b": "改"},
                {"counterfactual_id": cf1, "label_a": "原2", "label_b": "改2"},  # 重复
            ],
            "reshape_percent": 50,
            "target_chars": 4000,
            "style": "A",
            "use_outline_first": True,
            "divergence": "测试",
        },
    )
    assert r.status_code == 422, r.text


def test_get_tree_rejects_cross_user(
    client: TestClient, make_user, patched_simulation_llm, monkeypatch,
):
    """跨用户拉树应 404。"""
    monkeypatch.setattr(
        "app.routers.simulations._outline_async_runner",
        lambda sim_id: None,
    )

    user_a = make_user("alice")
    user_b = make_user("bob")
    pid, char_ids = _create_project_with_chars(client, user_a["headers"], 4)
    cf1 = _create_cf(client, user_a["headers"], pid, char_ids[0],
                     "personality", "勇敢")
    r = client.post(
        f"/api/projects/{pid}/counterfactual-combinations",
        headers=user_a["headers"],
        json={
            "selected_variables": [
                {"counterfactual_id": cf1, "label_a": "原", "label_b": "改"},
            ],
            "reshape_percent": 30,
            "target_chars": 2000,
            "style": "A",
            "use_outline_first": True,
            "divergence": "alice 项目",
        },
    )
    assert r.status_code == 201
    combo_id = r.json()["id"]

    # bob 拉应 404
    bob_r = client.get(
        f"/api/counterfactual-combinations/{combo_id}/tree",
        headers=user_b["headers"],
    )
    assert bob_r.status_code == 404


def test_total_combinations_matches_variable_count(
    client: TestClient, make_user, patched_simulation_llm, monkeypatch,
):
    """1 变量 → 2 组合;2 变量 → 4 组合。"""
    monkeypatch.setattr(
        "app.routers.simulations._outline_async_runner",
        lambda sim_id: None,
    )

    user = make_user("frank")
    pid, char_ids = _create_project_with_chars(client, user["headers"], 4)
    cf1 = _create_cf(client, user["headers"], pid, char_ids[0],
                     "personality", "v1")
    cf2 = _create_cf(client, user["headers"], pid, char_ids[1],
                     "personality", "v2")

    # 1 变量 → 2 sim
    r1 = client.post(
        f"/api/projects/{pid}/counterfactual-combinations",
        headers=user["headers"],
        json={
            "selected_variables": [
                {"counterfactual_id": cf1, "label_a": "a", "label_b": "b"},
            ],
            "reshape_percent": 30,
            "target_chars": 2000,
            "style": "A",
            "use_outline_first": True,
            "divergence": "1 变量",
        },
    )
    assert r1.status_code == 201
    assert r1.json()["total_combinations"] == 2

    # 2 变量 → 4 sim
    r2 = client.post(
        f"/api/projects/{pid}/counterfactual-combinations",
        headers=user["headers"],
        json={
            "selected_variables": [
                {"counterfactual_id": cf1, "label_a": "a", "label_b": "b"},
                {"counterfactual_id": cf2, "label_a": "a", "label_b": "b"},
            ],
            "reshape_percent": 30,
            "target_chars": 2000,
            "style": "A",
            "use_outline_first": True,
            "divergence": "2 变量",
        },
    )
    assert r2.status_code == 201
    assert r2.json()["total_combinations"] == 4
