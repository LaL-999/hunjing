"""SP-5 伏笔账本 router 测试(2026-05-28)."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import execute, get_connection


def _setup_sim_with_threads(user_id: str) -> tuple[str, list[str]]:
    """造一个 sim + 4 条 plot_threads(1 active / 1 resolved / 1 abandoned / 1 overdue)."""
    pid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    now = "2026-05-28T00:00:00+00:00"
    conn = get_connection()
    thread_ids: list[str] = []
    try:
        execute(
            conn,
            "INSERT INTO projects "
            "(id, user_id, name, type, custom_type_name, tags, mode, "
            " created_at, updated_at) "
            "VALUES (?, ?, '测试', 'novel', NULL, '[]', 'middle', ?, ?)",
            (pid, user_id, now, now),
        )
        execute(
            conn,
            "INSERT INTO simulations "
            "(id, project_id, user_id, divergence, reshape_percent, rounds_planned, "
            " target_chars, style, custom_style_hint, context_simulation_ids, "
            " narrative_summary, characters_snapshot, state, current_round, "
            " timeline_json, narrative, tokens_input, tokens_output, "
            " cost_yuan, error_message, created_at, started_at, completed_at) "
            "VALUES (?, ?, ?, 'test', 50, 10, 4000, 'A', NULL, '[]', NULL, "
            " '[]', 'directing', 0, NULL, NULL, 0, 0, 0.0, NULL, ?, NULL, NULL)",
            (sid, pid, user_id, now),
        )
        # 4 条 threads
        # 2026-06-02 cleanup:expected 字段已删(migration 080)— INSERT 不再含
        threads_data = [
            # (intro_scene, description, resolved_scene, is_abandoned, priority)
            (0, "主线悬念 A", None, 0, 1),         # ACTIVE
            (1, "支线 B 已收", 5, 0, 2),             # RESOLVED
            (2, "废弃线 C", None, 1, 3),             # ABANDONED
            (3, "悬空 D", None, 0, 2),                # ACTIVE
        ]
        for intro, desc, res, ab, prio in threads_data:
            tid = str(uuid.uuid4())
            thread_ids.append(tid)
            execute(
                conn,
                "INSERT INTO plot_threads "
                "(id, simulation_id, introduced_at_scene_index, description, "
                " resolved_at_scene_index, priority, staleness, created_at, updated_at, "
                " is_abandoned) "
                "VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (tid, sid, intro, desc, res, prio, now, now, ab),
            )
        conn.commit()
        return sid, thread_ids
    finally:
        conn.close()


def test_list_returns_all_threads_with_counts(client: TestClient, make_user):
    """GET 列表返三类 + counts."""
    u = make_user("pt_list")
    sid, _ = _setup_sim_with_threads(u["user_id"])

    r = client.get(
        f"/api/simulations/{sid}/plot_threads",
        headers=u["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert "threads" in body
    assert "counts" in body
    # counts:2 active(主线 A + 悬空 D)/ 1 resolved(B)/ 1 abandoned(C)
    assert body["counts"]["active"] == 2
    assert body["counts"]["resolved"] == 1
    assert body["counts"]["abandoned"] == 1
    assert len(body["threads"]) == 4


def test_list_filter_active_only(client: TestClient, make_user):
    """status=active 只返 active 2 条."""
    u = make_user("pt_filter")
    sid, _ = _setup_sim_with_threads(u["user_id"])

    r = client.get(
        f"/api/simulations/{sid}/plot_threads?status=active",
        headers=u["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["threads"]) == 2
    for t in body["threads"]:
        assert t["status"] == "active"
    # counts 不变(全集统计)
    assert body["counts"]["active"] == 2


# 2026-06-02 cleanup:test_patch_expected_resolution_scene 已删除
# expected_resolution_scene 字段被移除(用户产品决策:哪幕回收是 LLM 导演的事)


def test_patch_is_abandoned_changes_status(client: TestClient, make_user):
    """PATCH is_abandoned=True → status 变 abandoned."""
    u = make_user("pt_patch_ab")
    sid, tids = _setup_sim_with_threads(u["user_id"])

    # 把 active 主线 A 标废弃
    r = client.patch(
        f"/api/simulations/{sid}/plot_threads/{tids[0]}",
        headers=u["headers"],
        json={"is_abandoned": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["is_abandoned"] is True
    assert body["status"] == "abandoned"


def test_patch_nonexistent_thread_returns_404(client: TestClient, make_user):
    """PATCH 不存在的 thread → 404."""
    u = make_user("pt_patch_404")
    sid, _ = _setup_sim_with_threads(u["user_id"])

    r = client.patch(
        f"/api/simulations/{sid}/plot_threads/fake-id",
        headers=u["headers"],
        json={"is_abandoned": True},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "PLOT_THREAD_NOT_FOUND"


def test_cross_user_sim_returns_404(client: TestClient, make_user):
    """非自己的 sim → 404."""
    alice = make_user("pt_alice")
    bob = make_user("pt_bob")
    sid, _ = _setup_sim_with_threads(alice["user_id"])

    r = client.get(
        f"/api/simulations/{sid}/plot_threads",
        headers=bob["headers"],
    )
    assert r.status_code == 404
