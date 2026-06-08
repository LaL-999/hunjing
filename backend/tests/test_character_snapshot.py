"""SP-4 角色状态快照服务测试(2026-05-28)."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import execute, get_connection
from app.services.character_snapshot_service import (
    get_latest_snapshot,
    get_snapshot_at,
    list_snapshots_for_character,
    list_snapshots_for_scene,
    write_snapshot,
)


def _setup_sim_and_chars(user_id: str) -> tuple[str, str, str]:
    """造一个 sim + 2 个 character,返回 (sim_id, char1_id, char2_id)."""
    pid = str(uuid.uuid4())
    sid = str(uuid.uuid4())
    c1 = str(uuid.uuid4())
    c2 = str(uuid.uuid4())
    now = "2026-05-28T00:00:00+00:00"
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
        # simulations 必填字段较多,造个最简版
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
        # 2 角色(quotes / no_go_list 是 NOT NULL,默认 '[]')
        for cid, cname in ((c1, "驹子"), (c2, "岛村")):
            execute(
                conn,
                "INSERT INTO characters "
                "(id, project_id, name, identity, personality, quotes, no_go_list, "
                " position_x, position_y, position_z, created_at, updated_at) "
                "VALUES (?, ?, ?, '角色身份', '性格', '[]', '[]', 0, 0, 0, ?, ?)",
                (cid, pid, cname, now, now),
            )
        conn.commit()
        return sid, c1, c2
    finally:
        conn.close()


def test_write_and_get_snapshot(client: TestClient, make_user):
    """写一行 snapshot → get_snapshot_at 能读回."""
    u = make_user("snap_basic")
    sid, c1, _ = _setup_sim_and_chars(u["user_id"])

    conn = get_connection()
    try:
        write_snapshot(
            conn,
            simulation_id=sid,
            scene_index=0,
            character_id=c1,
            character_name="驹子",
            position="客栈门口",
            emotion_vec={"joy": 0.2, "sadness": 0.6},
            hp_status="alive",
            inventory=["焦布"],
        )
        snap = get_snapshot_at(conn, sid, c1, 0)
    finally:
        conn.close()

    assert snap is not None
    assert snap["character_name"] == "驹子"
    assert snap["position"] == "客栈门口"
    assert snap["emotion_vec"] == {"joy": 0.2, "sadness": 0.6}
    assert snap["hp_status"] == "alive"
    assert snap["inventory"] == ["焦布"]
    assert snap["known_fact_ids"] is None  # SP-3 才填


def test_write_snapshot_upsert(client: TestClient, make_user):
    """同 sim+scene+char 写 2 次 → 第 2 次 UPDATE,不会重复 INSERT."""
    u = make_user("snap_upsert")
    sid, c1, _ = _setup_sim_and_chars(u["user_id"])

    conn = get_connection()
    try:
        # 第 1 次
        write_snapshot(
            conn, simulation_id=sid, scene_index=2, character_id=c1,
            character_name="驹子", position="阁楼", hp_status="alive",
        )
        # 第 2 次同 key 不同 position
        write_snapshot(
            conn, simulation_id=sid, scene_index=2, character_id=c1,
            character_name="驹子", position="客栈门口", hp_status="injured",
        )
        # 该 (sim, scene, char) 只有一行
        all_for_scene = list_snapshots_for_scene(conn, sid, 2)
    finally:
        conn.close()

    assert len(all_for_scene) == 1
    assert all_for_scene[0]["position"] == "客栈门口"
    assert all_for_scene[0]["hp_status"] == "injured"


def test_get_latest_with_before_scene(client: TestClient, make_user):
    """before_scene=5 → 只能看到 < 5 的快照,返最新一条."""
    u = make_user("snap_latest")
    sid, c1, _ = _setup_sim_and_chars(u["user_id"])

    conn = get_connection()
    try:
        for sc, pos in [(0, "门外"), (2, "客栈"), (5, "雪地")]:
            write_snapshot(
                conn, simulation_id=sid, scene_index=sc, character_id=c1,
                character_name="驹子", position=pos,
            )
        # before_scene=5 → 看到 scene 0, 2,最新是 2
        latest = get_latest_snapshot(conn, sid, c1, before_scene=5)
        # 不带 before_scene → 看全部,最新是 5
        latest_all = get_latest_snapshot(conn, sid, c1)
    finally:
        conn.close()

    assert latest is not None
    assert latest["scene_index"] == 2
    assert latest["position"] == "客栈"
    assert latest_all["scene_index"] == 5
    assert latest_all["position"] == "雪地"


def test_list_for_scene_returns_all_chars(client: TestClient, make_user):
    """第 3 幕末 2 角色都写了 → list 返 2 行."""
    u = make_user("snap_list_scene")
    sid, c1, c2 = _setup_sim_and_chars(u["user_id"])

    conn = get_connection()
    try:
        write_snapshot(
            conn, simulation_id=sid, scene_index=3, character_id=c1,
            character_name="驹子", position="客栈",
        )
        write_snapshot(
            conn, simulation_id=sid, scene_index=3, character_id=c2,
            character_name="岛村", position="雪山",
        )
        snaps = list_snapshots_for_scene(conn, sid, 3)
    finally:
        conn.close()

    assert len(snaps) == 2
    names = sorted([s["character_name"] for s in snaps])
    assert names == ["岛村", "驹子"]


def test_list_for_character_timeline(client: TestClient, make_user):
    """某角色全时间线按 scene_index 升序."""
    u = make_user("snap_timeline")
    sid, c1, _ = _setup_sim_and_chars(u["user_id"])

    conn = get_connection()
    try:
        # 乱序写入
        for sc in [3, 0, 5, 1]:
            write_snapshot(
                conn, simulation_id=sid, scene_index=sc, character_id=c1,
                character_name="驹子", position=f"幕{sc}",
            )
        timeline = list_snapshots_for_character(conn, sid, c1)
    finally:
        conn.close()

    assert len(timeline) == 4
    assert [s["scene_index"] for s in timeline] == [0, 1, 3, 5]


def test_nonexistent_returns_none(client: TestClient, make_user):
    """查不存在的快照返 None,不抛错."""
    u = make_user("snap_none")
    sid, c1, _ = _setup_sim_and_chars(u["user_id"])

    conn = get_connection()
    try:
        snap = get_snapshot_at(conn, sid, c1, 99)
        latest = get_latest_snapshot(conn, sid, c1)
    finally:
        conn.close()

    assert snap is None
    assert latest is None
