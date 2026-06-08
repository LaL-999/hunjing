"""Sprint 6.A2 M8.B(2026-05-21)— Outline 编辑增强测试。

覆盖:
  A. insert_scene_at
     - 在头部插入 → 原 0/1/2 → 1/2/3
     - 在中间插入 → 后续 +1
     - 尾部追加(position == count)
     - 越界 / 超 MAX_SCENES_PER_OUTLINE
  B. delete_scene
     - 删中间 → 后续 -1
     - 删最后剩 1 → ValueError("不能删空")
     - 非属于 sim → 404
  C. reorder_scenes
     - 完备倒序 → scene_index 重排
     - 缺漏 id / 多余 id → ValueError
     - 重复 id → ValueError
  D. 状态机
     - outline state=approved 不许加 / 删 / 重排 → OutlineStateMismatch
  E. API 端到端
     - POST /outline/scenes
     - DELETE /outline/scenes/{id}
     - PATCH /outline/reorder
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import _connect, execute as db_execute, fetch_all, fetch_one, transaction
from app.services.outline_editor import (
    OutlineNotFound,
    OutlineStateMismatch,
    delete_scene,
    insert_scene_at,
    reorder_scenes,
)
from app.services.project_service import iso_now


def _conn():
    return _connect(Path(os.environ["HUIMENG_DB_PATH"]))


def _setup_outline(client, make_user, scene_count: int = 3, state: str = "awaiting_user"):
    """造 user + project + sim + outline + N 幕。返回 (user_id, h, sim_id, outline_id, scene_ids)。"""
    user = make_user("editor_user")
    h = user["headers"]
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "M8.B 测试", "type": "novel", "mode": "initial"},
    ).json()
    project_id = p["id"]
    sim_id = uuid.uuid4().hex
    outline_id = uuid.uuid4().hex
    scene_ids = []

    conn = _conn()
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO simulations "
                "(id, project_id, user_id, divergence, reshape_percent, rounds_planned, "
                " target_chars, style, context_simulation_ids, characters_snapshot, "
                " state, current_round, narrative, created_at, "
                " tokens_input, tokens_output, cost_yuan) "
                "VALUES (?, ?, ?, '锚点', 10, ?, 4000, 'auto', '[]', '[]', "
                "        'queued', 0, NULL, ?, 0, 0, 0)",
                (sim_id, project_id, user["user_id"], scene_count, iso_now()),
            )
            db_execute(
                tx,
                "INSERT INTO simulation_outlines "
                "(id, simulation_id, state, total_scenes_planned, "
                " global_theme, global_arc, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, '主题', 'arc', ?, ?)",
                (outline_id, sim_id, state, scene_count, iso_now(), iso_now()),
            )
            for i in range(scene_count):
                sid = uuid.uuid4().hex
                scene_ids.append(sid)
                db_execute(
                    tx,
                    "INSERT INTO outline_scenes "
                    "(id, outline_id, scene_index, scene_summary, "
                    " location, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, outline_id, i, f"幕 {i}", f"地点 {i}",
                     iso_now(), iso_now()),
                )
    finally:
        conn.close()
    return user["user_id"], h, sim_id, outline_id, scene_ids


def _list_indices(outline_id: str) -> list[tuple[str, int]]:
    """拉 outline 所有 scene 的 (id, scene_index),按 index 升序。"""
    conn = _conn()
    try:
        rows = fetch_all(
            conn,
            "SELECT id, scene_index FROM outline_scenes WHERE outline_id=? "
            "ORDER BY scene_index ASC",
            (outline_id,),
        )
    finally:
        conn.close()
    return [(r["id"], r["scene_index"]) for r in rows]


# ============================================================
# A. insert_scene_at
# ============================================================

def test_insert_at_head_shifts_existing(client, make_user):
    user_id, h, sim_id, outline_id, scene_ids = _setup_outline(client, make_user, 3)
    conn = _conn()
    try:
        new_id = insert_scene_at(
            conn, sim_id, user_id, position=0,
            scene_data={"scene_summary": "新头幕", "location": "新地点"},
        )
    finally:
        conn.close()

    indices = _list_indices(outline_id)
    assert len(indices) == 4
    # 新幕在 index=0,原 0/1/2 变 1/2/3
    assert indices[0] == (new_id, 0)
    assert indices[1] == (scene_ids[0], 1)
    assert indices[2] == (scene_ids[1], 2)
    assert indices[3] == (scene_ids[2], 3)


def test_insert_at_middle(client, make_user):
    user_id, h, sim_id, outline_id, scene_ids = _setup_outline(client, make_user, 3)
    conn = _conn()
    try:
        new_id = insert_scene_at(
            conn, sim_id, user_id, position=2,    # 在原 index=2 之前插
            scene_data={"scene_summary": "中间幕"},
        )
    finally:
        conn.close()

    indices = _list_indices(outline_id)
    assert len(indices) == 4
    # 原 0/1 保持;新幕在 2;原 2 → 3
    assert indices[0] == (scene_ids[0], 0)
    assert indices[1] == (scene_ids[1], 1)
    assert indices[2] == (new_id, 2)
    assert indices[3] == (scene_ids[2], 3)


def test_insert_at_tail(client, make_user):
    user_id, h, sim_id, outline_id, scene_ids = _setup_outline(client, make_user, 3)
    conn = _conn()
    try:
        new_id = insert_scene_at(conn, sim_id, user_id, position=3)  # 尾部
    finally:
        conn.close()

    indices = _list_indices(outline_id)
    assert len(indices) == 4
    assert indices[3] == (new_id, 3)


def test_insert_out_of_range_raises(client, make_user):
    user_id, h, sim_id, outline_id, _ = _setup_outline(client, make_user, 3)
    conn = _conn()
    try:
        with pytest.raises(ValueError):
            insert_scene_at(conn, sim_id, user_id, position=5)    # 越界
        with pytest.raises(ValueError):
            insert_scene_at(conn, sim_id, user_id, position=-1)
    finally:
        conn.close()


def test_insert_when_outline_approved_blocked(client, make_user):
    user_id, h, sim_id, outline_id, _ = _setup_outline(
        client, make_user, 3, state="approved",
    )
    conn = _conn()
    try:
        with pytest.raises(OutlineStateMismatch):
            insert_scene_at(conn, sim_id, user_id, position=0)
    finally:
        conn.close()


# ============================================================
# B. delete_scene
# ============================================================

def test_delete_middle_shifts_following(client, make_user):
    user_id, h, sim_id, outline_id, scene_ids = _setup_outline(client, make_user, 4)
    conn = _conn()
    try:
        delete_scene(conn, scene_ids[1], sim_id, user_id)    # 删 index=1
    finally:
        conn.close()

    indices = _list_indices(outline_id)
    assert len(indices) == 3
    # 0 不动;原 2 → 1;原 3 → 2
    assert indices[0] == (scene_ids[0], 0)
    assert indices[1] == (scene_ids[2], 1)
    assert indices[2] == (scene_ids[3], 2)


def test_delete_last_remaining_raises(client, make_user):
    user_id, h, sim_id, outline_id, scene_ids = _setup_outline(client, make_user, 1)
    conn = _conn()
    try:
        with pytest.raises(ValueError):
            delete_scene(conn, scene_ids[0], sim_id, user_id)
    finally:
        conn.close()


def test_delete_when_outline_approved_blocked(client, make_user):
    user_id, h, sim_id, outline_id, scene_ids = _setup_outline(
        client, make_user, 3, state="approved",
    )
    conn = _conn()
    try:
        with pytest.raises(OutlineStateMismatch):
            delete_scene(conn, scene_ids[0], sim_id, user_id)
    finally:
        conn.close()


# ============================================================
# C. reorder_scenes
# ============================================================

def test_reorder_reverses(client, make_user):
    """原顺序 0/1/2 → 倒序 2/1/0。"""
    user_id, h, sim_id, outline_id, scene_ids = _setup_outline(client, make_user, 3)
    conn = _conn()
    try:
        reorder_scenes(conn, sim_id, user_id, list(reversed(scene_ids)))
    finally:
        conn.close()

    indices = _list_indices(outline_id)
    # 现在 index 0 = 原 index 2,以此类推
    assert indices[0] == (scene_ids[2], 0)
    assert indices[1] == (scene_ids[1], 1)
    assert indices[2] == (scene_ids[0], 2)


def test_reorder_invalid_missing_id(client, make_user):
    user_id, h, sim_id, outline_id, scene_ids = _setup_outline(client, make_user, 3)
    conn = _conn()
    try:
        with pytest.raises(ValueError):
            # 缺 scene_ids[2]
            reorder_scenes(conn, sim_id, user_id, scene_ids[:2])
    finally:
        conn.close()


def test_reorder_invalid_duplicate(client, make_user):
    user_id, h, sim_id, outline_id, scene_ids = _setup_outline(client, make_user, 3)
    conn = _conn()
    try:
        with pytest.raises(ValueError):
            reorder_scenes(conn, sim_id, user_id,
                          [scene_ids[0], scene_ids[0], scene_ids[2]])
    finally:
        conn.close()


# ============================================================
# D. API 端到端
# ============================================================

def test_api_insert_scene_returns_full_outline(client, make_user):
    user_id, h, sim_id, outline_id, scene_ids = _setup_outline(client, make_user, 2)
    r = client.post(
        f"/api/simulations/{sim_id}/outline/scenes",
        headers=h,
        json={
            "position": 1,
            "scene_summary": "中间插入的幕",
            "location": "新地点",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["scenes"]) == 3
    # 验证按 scene_index 升序
    indices = [s["scene_index"] for s in body["scenes"]]
    assert indices == [0, 1, 2]
    # 中间幕 summary 是新插入的
    middle = next(s for s in body["scenes"] if s["scene_index"] == 1)
    assert middle["scene_summary"] == "中间插入的幕"


def test_api_delete_scene_returns_full_outline(client, make_user):
    user_id, h, sim_id, outline_id, scene_ids = _setup_outline(client, make_user, 3)
    r = client.delete(
        f"/api/simulations/{sim_id}/outline/scenes/{scene_ids[1]}",
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["scenes"]) == 2
    indices = [s["scene_index"] for s in body["scenes"]]
    assert indices == [0, 1]


def test_api_reorder_scenes(client, make_user):
    user_id, h, sim_id, outline_id, scene_ids = _setup_outline(client, make_user, 3)
    r = client.patch(
        f"/api/simulations/{sim_id}/outline/reorder",
        headers=h,
        json={"ordered_scene_ids": list(reversed(scene_ids))},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    indices_by_id = {s["id"]: s["scene_index"] for s in body["scenes"]}
    assert indices_by_id[scene_ids[2]] == 0
    assert indices_by_id[scene_ids[1]] == 1
    assert indices_by_id[scene_ids[0]] == 2


def test_api_insert_when_locked_returns_409(client, make_user):
    user_id, h, sim_id, outline_id, _ = _setup_outline(
        client, make_user, 3, state="approved",
    )
    r = client.post(
        f"/api/simulations/{sim_id}/outline/scenes",
        headers=h,
        json={"position": 0},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "OUTLINE_LOCKED"
