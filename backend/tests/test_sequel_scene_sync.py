"""Sprint 6.A2 M8.A(2026-05-20)— 续作场景同步入库测试。

覆盖:
  A. evolution 模式新场景入库(scene_picker 选了不在 project_scenes 的 location)
  B. 已存在同名场景 → 跳过(name 去重)
  C. 同一幕重复调 → 幂等(不会重复创建)
  D. 多 location 入库(outline 链路批量)
  E. origin_simulation_id 标记正确
  F. list_sequel_scenes 过滤
"""
from __future__ import annotations

import os
import sqlite3
import uuid
from pathlib import Path

import pytest

from app.db import _connect, fetch_all, transaction, execute as db_execute
from app.models.simulation import Simulation
from app.services.project_service import iso_now
from app.services.sequel_scene_sync import (
    list_sequel_scenes,
    sync_new_scene_from_evolution,
    sync_new_scenes_from_outline,
)


# ============================================================
# helpers
# ============================================================

def _conn() -> sqlite3.Connection:
    return _connect(Path(os.environ["HUIMENG_DB_PATH"]))


def _setup_minimal(client, make_user) -> tuple[str, str, str]:
    """造 user + project + sim,返回 (user_id, project_id, sim_id)。"""
    user = make_user("scene_user")
    h = user["headers"]
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "M8.A 测试", "type": "novel", "mode": "initial"},
    ).json()
    project_id = p["id"]

    sim_id = uuid.uuid4().hex
    conn = _conn()
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO simulations "
                "(id, project_id, user_id, divergence, reshape_percent, "
                " rounds_planned, target_chars, style, "
                " context_simulation_ids, characters_snapshot, "
                " state, current_round, narrative, created_at, "
                " tokens_input, tokens_output, cost_yuan) "
                "VALUES (?, ?, ?, '锚点', 10, 5, 4000, 'auto', '[]', '[]', "
                "        'done', 5, '...', ?, 0, 0, 0)",
                (sim_id, project_id, user["user_id"], iso_now()),
            )
    finally:
        conn.close()
    return user["user_id"], project_id, sim_id


def _get_sim(sim_id: str) -> Simulation:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM simulations WHERE id=?", (sim_id,),
        ).fetchone()
        return Simulation.from_row(row)
    finally:
        conn.close()


def _seed_project_scene(project_id: str, name: str) -> str:
    """造一个原作图谱抽出的场景(origin_simulation_id=NULL)。"""
    conn = _conn()
    sid = uuid.uuid4().hex
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO project_scenes "
                "(id, project_id, name, aliases_json, description, "
                " appearance_chunk_count, created_at, updated_at, origin_simulation_id) "
                "VALUES (?, ?, ?, '[]', '', 5, ?, ?, NULL)",
                (sid, project_id, name, iso_now(), iso_now()),
            )
    finally:
        conn.close()
    return sid


def _insert_outline_with_scenes(sim_id: str, scene_locations: list[str]):
    """造 simulation_outlines + outline_scenes 行。"""
    conn = _conn()
    outline_id = uuid.uuid4().hex
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO simulation_outlines "
                "(id, simulation_id, state, total_scenes_planned, "
                " global_theme, global_arc, created_at, updated_at) "
                "VALUES (?, ?, 'approved', ?, '', '', ?, ?)",
                (outline_id, sim_id, len(scene_locations), iso_now(), iso_now()),
            )
            for idx, loc in enumerate(scene_locations):
                db_execute(
                    tx,
                    "INSERT INTO outline_scenes "
                    "(id, outline_id, scene_index, scene_summary, "
                    " location, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (uuid.uuid4().hex, outline_id, idx, f"幕 {idx}",
                     loc, iso_now(), iso_now()),
                )
    finally:
        conn.close()


# ============================================================
# A. evolution 新场景入库
# ============================================================

def test_evolution_sync_creates_new_scene(client, make_user):
    user_id, project_id, sim_id = _setup_minimal(client, make_user)
    _seed_project_scene(project_id, "教室")    # 原作场景

    sim = _get_sim(sim_id)
    conn = _conn()
    try:
        summary = sync_new_scene_from_evolution(conn, sim, scene_index=3, location="天台")
    finally:
        conn.close()

    assert len(summary["created_scenes"]) == 1
    created = summary["created_scenes"][0]
    assert created["name"] == "天台"
    assert created["first_scene_index"] == 3

    # 验证 DB 里多了一个 origin_simulation_id 标记的 scene
    conn = _conn()
    try:
        rows = fetch_all(
            conn,
            "SELECT name, origin_simulation_id FROM project_scenes "
            "WHERE project_id=? ORDER BY name",
            (project_id,),
        )
    finally:
        conn.close()
    names = [r["name"] for r in rows]
    assert "教室" in names
    assert "天台" in names
    origin_map = {r["name"]: r["origin_simulation_id"] for r in rows}
    assert origin_map["教室"] is None        # 原作抽出
    assert origin_map["天台"] == sim_id      # 续作生成


# ============================================================
# B. 已存在同名场景 → 跳过
# ============================================================

def test_evolution_sync_skips_existing_name(client, make_user):
    user_id, project_id, sim_id = _setup_minimal(client, make_user)
    _seed_project_scene(project_id, "教室")

    sim = _get_sim(sim_id)
    conn = _conn()
    try:
        summary = sync_new_scene_from_evolution(conn, sim, scene_index=1, location="教室")
    finally:
        conn.close()

    assert summary["created_scenes"] == []
    # project_scenes 还是只有 1 条
    conn = _conn()
    try:
        rows = fetch_all(
            conn, "SELECT name FROM project_scenes WHERE project_id=?", (project_id,),
        )
    finally:
        conn.close()
    assert len(rows) == 1


# ============================================================
# C. 同一幕重复调 → 幂等
# ============================================================

def test_evolution_sync_idempotent(client, make_user):
    user_id, project_id, sim_id = _setup_minimal(client, make_user)
    sim = _get_sim(sim_id)

    conn = _conn()
    try:
        s1 = sync_new_scene_from_evolution(conn, sim, 0, "江边渔船")
        s2 = sync_new_scene_from_evolution(conn, sim, 0, "江边渔船")
    finally:
        conn.close()

    assert len(s1["created_scenes"]) == 1
    assert len(s2["created_scenes"]) == 0    # 第二次跳过

    conn = _conn()
    try:
        rows = fetch_all(
            conn, "SELECT name FROM project_scenes WHERE project_id=?", (project_id,),
        )
    finally:
        conn.close()
    assert len(rows) == 1


# ============================================================
# D. outline 批量入库
# ============================================================

def test_outline_sync_creates_multiple_unique_scenes(client, make_user):
    user_id, project_id, sim_id = _setup_minimal(client, make_user)
    _seed_project_scene(project_id, "教室")

    # outline 包含 5 幕,4 个 unique location("教室"是已有 + "天台 / 操场 / 地下室 / 教室"
    # 重复出现但 unique 入库 2 个新场景)
    _insert_outline_with_scenes(sim_id, ["教室", "天台", "操场", "教室", "地下室"])

    sim = _get_sim(sim_id)
    conn = _conn()
    try:
        summary = sync_new_scenes_from_outline(conn, sim)
    finally:
        conn.close()

    assert len(summary["created_scenes"]) == 3
    names = sorted(s["name"] for s in summary["created_scenes"])
    assert names == ["地下室", "天台", "操场"]

    # 验证 first_scene_index 取最小幕号
    first_idx_map = {s["name"]: s["first_scene_index"] for s in summary["created_scenes"]}
    assert first_idx_map["天台"] == 1       # 第 1 幕首次出现
    assert first_idx_map["操场"] == 2       # 第 2 幕首次出现
    assert first_idx_map["地下室"] == 4     # 第 4 幕首次出现


# ============================================================
# E. 空 location / 超长 location 不入库
# ============================================================

def test_evolution_sync_skips_empty_or_too_long(client, make_user):
    user_id, project_id, sim_id = _setup_minimal(client, make_user)
    sim = _get_sim(sim_id)

    conn = _conn()
    try:
        s1 = sync_new_scene_from_evolution(conn, sim, 0, "")
        s2 = sync_new_scene_from_evolution(conn, sim, 0, "   ")
        long_name = "x" * 50
        s3 = sync_new_scene_from_evolution(conn, sim, 0, long_name)
    finally:
        conn.close()

    assert s1["created_scenes"] == []
    assert s2["created_scenes"] == []
    assert s3["created_scenes"] == []        # too_long
    assert any(sk["reason"] == "too_long" for sk in s3["skipped"])


# ============================================================
# F. list_sequel_scenes 只返续作场景(过滤 origin IS NOT NULL)
# ============================================================

def test_list_sequel_scenes_filters_origin(client, make_user):
    user_id, project_id, sim_id = _setup_minimal(client, make_user)
    _seed_project_scene(project_id, "教室")    # 原作

    sim = _get_sim(sim_id)
    conn = _conn()
    try:
        sync_new_scene_from_evolution(conn, sim, 0, "天台")
        sync_new_scene_from_evolution(conn, sim, 1, "地下室")
    finally:
        conn.close()

    conn = _conn()
    try:
        sequel_only = list_sequel_scenes(conn, project_id)
    finally:
        conn.close()

    names = sorted(s["name"] for s in sequel_only)
    assert names == ["地下室", "天台"]    # 不含"教室"(原作抽出)
