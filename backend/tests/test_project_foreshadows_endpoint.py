"""阶段 3A(2026-06-02)— 项目级伏笔列表 endpoint + inherited_foreshadow_ids 测试.

覆盖:
  - GET /api/projects/{id}/foreshadows 返 open 状态伏笔
  - 老库无 foreshadow_ledger 表 → 返空
  - CreateSimulationRequest 接受 inherited_foreshadow_ids 字段
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_simulations import _create_project_with_chars


class TestProjectForeshadowsEndpoint:
    def test_empty_project_returns_empty_list(
        self, client: TestClient, make_user,
    ):
        """新项目无伏笔 → 返空列表."""
        user = make_user("u_fs1")
        h = user["headers"]
        project_id, _ = _create_project_with_chars(
            client, h, "测试项目",
            chars=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
        )
        r = client.get(f"/api/projects/{project_id}/foreshadows", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["foreshadows"] == []
        assert body["total"] == 0

    def test_unauthenticated_returns_401(self, client: TestClient):
        r = client.get("/api/projects/some-id/foreshadows")
        assert r.status_code == 401

    def test_cross_user_returns_404(
        self, client: TestClient, make_user,
    ):
        user_a = make_user("u_a")
        project_id, _ = _create_project_with_chars(
            client, user_a["headers"], "A 项目",
            chars=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
        )
        user_b = make_user("u_b")
        r = client.get(
            f"/api/projects/{project_id}/foreshadows",
            headers=user_b["headers"],
        )
        assert r.status_code == 404


class TestCreateSimulationAcceptsInheritedForeshadowIds:
    """CreateSimulationRequest 接受 inherited_foreshadow_ids 字段."""

    def test_accepts_null(
        self, client: TestClient, make_user, patched_simulation_llm,
        sync_simulation_runner,
    ):
        """null 是默认值,不传也 OK."""
        from tests.test_simulations import _preload_minimal_simulation_llm
        user = make_user("u_n")
        h = user["headers"]
        project_id, char_ids = _create_project_with_chars(
            client, h, "测试",
            chars=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
        )
        _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
        r = client.post(
            f"/api/projects/{project_id}/simulations", headers=h,
            json={
                "divergence": "测试 — 用户没传 inherited_foreshadow_ids",
                "reshape_percent": 10,
                # inherited_foreshadow_ids 不传 → null
            },
        )
        assert r.status_code == 201, r.text

    def test_accepts_empty_list(
        self, client: TestClient, make_user, patched_simulation_llm,
        sync_simulation_runner,
    ):
        """[] = 用户主动空选(独立创作),应被接受."""
        from tests.test_simulations import _preload_minimal_simulation_llm
        user = make_user("u_e")
        h = user["headers"]
        project_id, char_ids = _create_project_with_chars(
            client, h, "测试",
            chars=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
        )
        _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
        r = client.post(
            f"/api/projects/{project_id}/simulations", headers=h,
            json={
                "divergence": "测试 — 用户主动空选(独立创作)",
                "reshape_percent": 10,
                "inherited_foreshadow_ids": [],
            },
        )
        assert r.status_code == 201, r.text

    def test_accepts_id_list(
        self, client: TestClient, make_user, patched_simulation_llm,
        sync_simulation_runner,
    ):
        """非空 list 也应被接受(即使 id 不存在,outline_generator 也只是过滤不到)."""
        from tests.test_simulations import _preload_minimal_simulation_llm
        user = make_user("u_l")
        h = user["headers"]
        project_id, char_ids = _create_project_with_chars(
            client, h, "测试",
            chars=[{"name": "A"}, {"name": "B"}, {"name": "C"}],
        )
        _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
        r = client.post(
            f"/api/projects/{project_id}/simulations", headers=h,
            json={
                "divergence": "测试 — 用户主动选择性继承伏笔",
                "reshape_percent": 10,
                "inherited_foreshadow_ids": ["fake-id-1", "fake-id-2"],
            },
        )
        assert r.status_code == 201, r.text
