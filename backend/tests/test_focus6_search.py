"""Sprint 6.A2 路线图 #6(2026-05-23)— 全局搜索 endpoint 测试。

覆盖:
  1. 模糊匹配 — 搜"老"命中"老奶奶"角色 + "老奶奶的屋子"场景
  2. 跨项目搜索 — 同用户两个项目里都有"老奶奶"角色,搜出来俩(带各自 project_name)
  3. project_id 范围限制 — 给 project_id 只返该项目内的结果
  4. 跨用户隔离 — alice 的"老奶奶" bob 搜不出来
  5. q 为空 / 全空格 → 422
  6. 6 类实体都能命中(每类各造一条 + 搜出 6 类)
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import _connect, transaction, execute as db_execute
from app.services.project_service import iso_now


# ============================================================
# helpers — 直接 INSERT 多类实体,跳过完整 CRUD 流程
# ============================================================

def _create_project_api(
    client: TestClient, headers: dict, name: str = "测试项目",
) -> str:
    r = client.post(
        "/api/projects", headers=headers,
        json={"name": name, "type": "novel", "mode": "initial"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _insert_character(project_id: str, name: str, identity: str = "") -> str:
    char_id = uuid.uuid4().hex
    now = iso_now()
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                """INSERT INTO characters
                   (id, project_id, name, identity, personality, quotes, no_go_list,
                    position_x, position_y, position_z, color, created_at, updated_at)
                   VALUES (?, ?, ?, ?, '', '[]', '[]', 0, 0, 0, NULL, ?, ?)""",
                (char_id, project_id, name, identity, now, now),
            )
    finally:
        conn.close()
    return char_id


def _insert_event(project_id: str, description: str, time_anchor: str = "") -> str:
    eid = uuid.uuid4().hex
    now = iso_now()
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                """INSERT INTO events
                   (id, project_id, description, participants, time_anchor, created_at)
                   VALUES (?, ?, ?, '[]', ?, ?)""",
                (eid, project_id, description, time_anchor, now),
            )
    finally:
        conn.close()
    return eid


def _insert_scene(project_id: str, name: str, description: str = "") -> str:
    sid = uuid.uuid4().hex
    now = iso_now()
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                """INSERT INTO project_scenes
                   (id, project_id, name, aliases_json, description,
                    appearance_chunk_count, created_at, updated_at)
                   VALUES (?, ?, ?, '[]', ?, 0, ?, ?)""",
                (sid, project_id, name, description, now, now),
            )
    finally:
        conn.close()
    return sid


def _insert_relationship(
    project_id: str, source_id: str, target_id: str,
    rel_type: str = "其他", description: str = "",
) -> str:
    rid = uuid.uuid4().hex
    now = iso_now()
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                """INSERT INTO relationships
                   (id, project_id, source_id, target_id, type, description,
                    color, strength, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, 'weak', ?)""",
                (rid, project_id, source_id, target_id, rel_type, description, now),
            )
    finally:
        conn.close()
    return rid


def _insert_simulation(
    project_id: str, user_id: str, divergence: str = "测试分叉",
) -> str:
    sid = uuid.uuid4().hex
    now = iso_now()
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                """INSERT INTO simulations
                   (id, project_id, user_id, divergence, reshape_percent, rounds_planned,
                    target_chars, style, custom_style_hint, context_simulation_ids,
                    characters_snapshot, state, current_round, timeline_json,
                    narrative, tokens_input, tokens_output, cost_yuan,
                    error_message, created_at, started_at, completed_at)
                   VALUES (?, ?, ?, ?, 10, 5, 4000, 'auto', NULL, '[]', '[]',
                           'done', 5, NULL, NULL, 0, 0, 0.0, NULL, ?, ?, ?)""",
                (sid, project_id, user_id, divergence, now, now, now),
            )
    finally:
        conn.close()
    return sid


# ============================================================
# 1. 模糊匹配 — "老" 命中"老奶奶"角色 + "老奶奶的屋子"场景
# ============================================================

def test_search_partial_match_finds_character_and_scene(
    client: TestClient, make_user,
):
    user = make_user("alice")
    h = user["headers"]
    project_id = _create_project_api(client, h, "童话项目")
    _insert_character(project_id, "老奶奶")
    _insert_scene(project_id, "老奶奶的屋子", "森林深处的木屋")

    r = client.get("/api/search?q=老", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["query"] == "老"
    # 角色命中
    char_names = [c["name"] for c in body["characters"]]
    assert "老奶奶" in char_names
    # 场景命中
    scene_names = [s["name"] for s in body["scenes"]]
    assert "老奶奶的屋子" in scene_names


# ============================================================
# 2. 跨项目搜索 — 两个项目都有"老奶奶",都搜出来 + project_name 区分
# ============================================================

def test_search_cross_projects_returns_both_with_project_breadcrumb(
    client: TestClient, make_user,
):
    user = make_user("alice")
    h = user["headers"]
    p1 = _create_project_api(client, h, "童话 A")
    p2 = _create_project_api(client, h, "童话 B")
    _insert_character(p1, "老奶奶", identity="A 的老奶奶")
    _insert_character(p2, "老奶奶", identity="B 的老奶奶")

    body = client.get("/api/search?q=老奶奶", headers=h).json()
    chars = body["characters"]
    assert len(chars) == 2
    project_names = sorted([c["project_name"] for c in chars])
    assert project_names == ["童话 A", "童话 B"]


# ============================================================
# 3. project_id 范围限制 — 给了只返该项目内的结果
# ============================================================

def test_search_with_project_id_limits_to_that_project(
    client: TestClient, make_user,
):
    user = make_user("alice")
    h = user["headers"]
    p1 = _create_project_api(client, h, "项目 A")
    p2 = _create_project_api(client, h, "项目 B")
    _insert_character(p1, "老奶奶")
    _insert_character(p2, "老奶奶")

    # 限 project A
    body = client.get(
        f"/api/search?q=老奶奶&project_id={p1}", headers=h,
    ).json()
    chars = body["characters"]
    assert len(chars) == 1
    assert chars[0]["project_id"] == p1
    assert chars[0]["project_name"] == "项目 A"


# ============================================================
# 4. 跨用户隔离 — alice 的内容 bob 搜不出来
# ============================================================

def test_search_isolates_cross_user(client: TestClient, make_user):
    alice = make_user("alice")
    bob = make_user("bob")
    p_alice = _create_project_api(client, alice["headers"], "alice 项目")
    _insert_character(p_alice, "老奶奶")

    body = client.get("/api/search?q=老奶奶", headers=bob["headers"]).json()
    assert body["characters"] == []
    assert body["projects"] == []


# ============================================================
# 5. q 为空 / 纯空格 → 422
# ============================================================

def test_search_empty_q_returns_422(client: TestClient, make_user):
    user = make_user("alice")
    h = user["headers"]
    # 完全无 q 参数 → FastAPI 422(Query required)
    r = client.get("/api/search", headers=h)
    assert r.status_code == 422


def test_search_whitespace_q_returns_422(client: TestClient, make_user):
    user = make_user("alice")
    h = user["headers"]
    # 全空格 → strip 后空,router 显式拦
    r = client.get("/api/search?q=%20%20%20", headers=h)
    assert r.status_code == 422


# ============================================================
# 6. 6 类实体都能命中
# ============================================================

def test_search_all_entity_types_hit(client: TestClient, make_user):
    """5 类实体(关系已移除)都能命中"""
    user = make_user("alice")
    h = user["headers"]
    project_id = _create_project_api(client, h, "全维度测试")

    # 5 类实体各造一条,name/desc 都含"星辰"
    _insert_character(project_id, "星辰使者", identity="掌管星河")
    _insert_event(project_id, "星辰大爆发", "公元 3024")
    _insert_scene(project_id, "星辰殿", "古老的星宫")
    _insert_simulation(project_id, user["user_id"], divergence="星辰陨落之后")

    body = client.get("/api/search?q=星辰", headers=h).json()

    # 项目名不含"星辰" → 项目不命中(预期)
    assert body["projects"] == []
    assert any(c["name"] == "星辰使者" for c in body["characters"])
    assert any("星辰大爆发" in e["description"] for e in body["events"])
    assert any(s["name"] == "星辰殿" for s in body["scenes"])
    assert any("星辰陨落" in sim["divergence"] for sim in body["simulations"])
    # 关系搜索已移除 — response 不应含 relationships 字段
    assert "relationships" not in body


def test_search_project_name_hit(client: TestClient, make_user):
    """搜项目名能命中项目实体本身"""
    user = make_user("alice")
    h = user["headers"]
    _create_project_api(client, h, "挪威的森林")

    body = client.get("/api/search?q=挪威", headers=h).json()
    assert any(p["name"] == "挪威的森林" for p in body["projects"])


def test_search_character_only_matches_name_not_identity(
    client: TestClient, make_user,
):
    """精度版:角色只搜 name 字段。identity 含"渡边"不应被命中"""
    user = make_user("alice")
    h = user["headers"]
    project_id = _create_project_api(client, h, "精度测试")
    # 角色名"绿子",但 identity 含"渡边"(渡边的恋人)
    _insert_character(project_id, "绿子", identity="渡边的恋人,经营小林书店")
    # 角色名直接是"渡边"
    _insert_character(project_id, "渡边", identity="主角")

    body = client.get("/api/search?q=渡边", headers=h).json()
    names = [c["name"] for c in body["characters"]]
    # 只命中名字本身是"渡边"的;"绿子"虽然 identity 含"渡边"也不应被命中
    assert "渡边" in names
    assert "绿子" not in names


def test_search_scene_only_matches_name_not_description(
    client: TestClient, make_user,
):
    """精度版:场景只搜 name 字段。description 含搜索词不应被命中"""
    user = make_user("alice")
    h = user["headers"]
    project_id = _create_project_api(client, h, "精度测试")
    # 场景名"东京",description 含"渡边"
    _insert_scene(project_id, "东京", "渡边居住的城市")
    # 场景名直接含"渡边"
    _insert_scene(project_id, "渡边的小屋", "森林深处")

    body = client.get("/api/search?q=渡边", headers=h).json()
    names = [s["name"] for s in body["scenes"]]
    assert "渡边的小屋" in names
    # "东京"虽然 description 含"渡边"也不应被命中
    assert "东京" not in names
