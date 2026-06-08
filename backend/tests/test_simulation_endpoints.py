"""新 endpoint 集成测试(2026-06-02 任务 1):
/api/simulations/{id}/chapters
/api/simulations/{id}/final_work
/api/simulations/{id}/hint_history

复用 test_simulations.py 的 helper(_create_project_with_chars, _preload_minimal_simulation_llm)
跑 happy path → done sim 后调 endpoint 验证.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from tests.test_simulations import (
    _create_project_with_chars,
    _preload_minimal_simulation_llm,
)


# ============================================================
# 公共 helper:跑一个 done sim 出来给后续测试用
# ============================================================

def _create_done_sim(
    client: TestClient,
    headers: dict,
    patched_llm,
    project_name: str = "测试项目",
    reshape: int = 10,
) -> tuple[str, str]:
    """跑一个 happy path → 返 (project_id, sim_id)."""
    project_id, char_ids = _create_project_with_chars(
        client, headers, project_name,
        chars=[
            {"name": "李寻欢", "identity": "江湖浪子", "personality": "豪爽"},
            {"name": "孙小红", "identity": "江湖义士"},
            {"name": "上官金虹", "identity": "神秘剑客"},
        ],
    )
    _preload_minimal_simulation_llm(patched_llm, char_ids, reshape_percent=reshape)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=headers,
        json={"divergence": "如果当晚的相遇没有发生", "reshape_percent": reshape},
    )
    assert r.status_code == 201, r.text
    return project_id, r.json()["simulation_id"]


# ============================================================
# /chapters
# ============================================================

class TestChaptersEndpoint:
    def test_chapters_happy_path(
        self, client: TestClient, make_user, patched_simulation_llm,
        sync_simulation_runner,
    ):
        """done sim 调 /chapters → 返章节列表(可能为 1 章,因测试 narrative 较短)."""
        user = make_user("hero1")
        h = user["headers"]
        _, sim_id = _create_done_sim(client, h, patched_simulation_llm)

        r = client.get(f"/api/simulations/{sim_id}/chapters", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        # 必返字段
        assert "chapters" in body
        assert "start_chapter_no" in body
        assert "chapter_size_min" in body
        assert "chapter_size_max" in body
        assert "narrative_length" in body
        # 独立推演 → start_chapter_no = 1
        assert body["start_chapter_no"] == 1
        # 章节数 ≥ 1(本 sim narrative 至少有内容)
        assert isinstance(body["chapters"], list)
        # 每章字段完整
        for ch in body["chapters"]:
            assert "global_chapter_no" in ch
            assert "char_offset_start" in ch
            assert "char_offset_end" in ch
            assert "char_count" in ch
            assert "first_words" in ch

    def test_chapters_unauthenticated_returns_401(self, client: TestClient):
        r = client.get("/api/simulations/some-id/chapters")
        assert r.status_code == 401

    def test_chapters_cross_user_returns_404(
        self, client: TestClient, make_user, patched_simulation_llm,
        sync_simulation_runner,
    ):
        """A 用户的 sim,B 用户调 /chapters → 404."""
        user_a = make_user("user_a")
        _, sim_id = _create_done_sim(
            client, user_a["headers"], patched_simulation_llm,
        )
        user_b = make_user("user_b")
        r = client.get(
            f"/api/simulations/{sim_id}/chapters",
            headers=user_b["headers"],
        )
        assert r.status_code == 404


# ============================================================
# /final_work
# ============================================================

class TestFinalWorkEndpoint:
    def test_final_work_404_when_no_compilation(
        self, client: TestClient, make_user, patched_simulation_llm,
        sync_simulation_runner,
    ):
        """普通 done sim(无走向终章 + 无 ancestors)→ 404 FINAL_WORK_NOT_AVAILABLE."""
        user = make_user("hero2")
        h = user["headers"]
        _, sim_id = _create_done_sim(client, h, patched_simulation_llm)

        r = client.get(f"/api/simulations/{sim_id}/final_work", headers=h)
        assert r.status_code == 404
        detail = r.json().get("detail") or {}
        assert detail.get("error_code") == "FINAL_WORK_NOT_AVAILABLE"

    def test_final_work_unauthenticated_returns_401(self, client: TestClient):
        r = client.get("/api/simulations/some-id/final_work")
        assert r.status_code == 401


# ============================================================
# /hint_history
# ============================================================

class TestHintHistoryEndpoint:
    def test_hint_history_empty_for_quick_sim(
        self, client: TestClient, make_user, patched_simulation_llm,
        sync_simulation_runner,
    ):
        """快速模式 sim 无 hint(没用户干预)→ hints 为空 list."""
        user = make_user("hero3")
        h = user["headers"]
        _, sim_id = _create_done_sim(client, h, patched_simulation_llm)

        r = client.get(f"/api/simulations/{sim_id}/hint_history", headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["hints"] == []
        assert body["total"] == 0

    def test_hint_history_unauthenticated_returns_401(self, client: TestClient):
        r = client.get("/api/simulations/some-id/hint_history")
        assert r.status_code == 401

    def test_hint_history_cross_user_returns_404(
        self, client: TestClient, make_user, patched_simulation_llm,
        sync_simulation_runner,
    ):
        user_a = make_user("user_x")
        _, sim_id = _create_done_sim(
            client, user_a["headers"], patched_simulation_llm,
        )
        user_b = make_user("user_y")
        r = client.get(
            f"/api/simulations/{sim_id}/hint_history",
            headers=user_b["headers"],
        )
        assert r.status_code == 404
