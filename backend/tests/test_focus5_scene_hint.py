"""Sprint 6.A2 路线图 #5(2026-05-23)— 边写边干预 hint 测试。

覆盖:
  A. POST /api/simulations/:id/inject_scene_hint
     - happy path:running 状态 → 写入字段 + 200 accepted=True
     - 终态 sim → accepted=False(不报错,前端可 toast)
     - 别人的 sim → 404
     - hint 过长(>300 字) → 422 schema 拒绝
     - hint 为空 → 422 schema 拒绝
  B. helper 函数单元测试
     - _consume_pending_hint:读 + 立即清空(消耗式)
     - _build_user_hint_block:空 hint 返空串;非空 hint 含"用户即时干预"

设计:
  - 不跑完整 sim runner(慢),直接 SQL INSERT 模拟不同状态的 sim
  - helper 函数测试用真实 conn 验证 SQL 行为(读写字段)
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import _connect, fetch_one, transaction, execute as db_execute
from app.services.agent_evolution_engine import (
    _build_user_hint_block,
    _consume_pending_hint,
)
from app.services.project_service import iso_now


# ============================================================
# helpers
# ============================================================

def _create_project(client: TestClient, headers: dict, name: str = "测试") -> str:
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": name, "type": "novel", "mode": "initial"},
    ).json()
    return p["id"]


def _insert_sim_directly(
    project_id: str, user_id: str,
    state: str = "directing",
    pending_scene_hint: str | None = None,
    current_round: int = 2,
) -> str:
    """直接 SQL INSERT 一条 sim(跳过完整 runner,适合测 API)。"""
    sim_id = uuid.uuid4().hex
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
                    error_message, created_at, started_at, completed_at,
                    mode, use_outline_first, pending_scene_hint)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    sim_id, project_id, user_id,
                    "测试分叉点", 10, 5,
                    4000, "auto", None, "[]",
                    "[]",  # characters_snapshot 空
                    state, current_round, None,
                    None, 0, 0, 0.0,
                    None, now, now, None,
                    "evolution", 0, pending_scene_hint,
                ),
            )
    finally:
        conn.close()
    return sim_id


def _read_pending_hint_from_db(sim_id: str) -> str | None:
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        row = fetch_one(
            conn, "SELECT pending_scene_hint FROM simulations WHERE id=?", (sim_id,),
        )
        return row["pending_scene_hint"] if row else None
    finally:
        conn.close()


# ============================================================
# A1. happy path:POST hint 写入字段 + 200 accepted=True
# ============================================================

def test_inject_hint_happy_path(client: TestClient, make_user):
    user = make_user("alice")
    h = user["headers"]
    project_id = _create_project(client, h)
    sim_id = _insert_sim_directly(
        project_id, user["user_id"],
        state="directing", current_round=2,
    )

    r = client.post(
        f"/api/simulations/{sim_id}/inject_scene_hint",
        headers=h,
        json={"hint": "让主角这里要爆发"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is True
    # hotfix(2026-06-01):current_round=2 时,前端 UI 显示"AI 正在演第 3 幕"已 consume hint,
    # 用户提交时 hint 只能影响第 4 幕(current_round + 2)
    assert body["will_apply_to_scene"] == 4
    assert body["hint_preview"].startswith("让主角")

    # DB 字段已写入
    saved = _read_pending_hint_from_db(sim_id)
    assert saved == "让主角这里要爆发"


# ============================================================
# A2. 终态 sim 拒绝(返 accepted=False,不报错)
# ============================================================

def test_inject_hint_rejected_when_done(client: TestClient, make_user):
    user = make_user("alice")
    h = user["headers"]
    project_id = _create_project(client, h)
    sim_id = _insert_sim_directly(project_id, user["user_id"], state="done")

    r = client.post(
        f"/api/simulations/{sim_id}/inject_scene_hint",
        headers=h,
        json={"hint": "想干预"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["accepted"] is False
    assert body["will_apply_to_scene"] is None
    assert body["hint_preview"] is None

    # DB 字段没被改(sim 已结束,前端可 toast 提示)
    saved = _read_pending_hint_from_db(sim_id)
    assert saved is None


def test_inject_hint_rejected_when_failed(client: TestClient, make_user):
    user = make_user("alice")
    h = user["headers"]
    project_id = _create_project(client, h)
    sim_id = _insert_sim_directly(project_id, user["user_id"], state="failed")

    r = client.post(
        f"/api/simulations/{sim_id}/inject_scene_hint",
        headers=h, json={"hint": "想干预"},
    )
    assert r.status_code == 200
    assert r.json()["accepted"] is False


# ============================================================
# A3. 别人的 sim → 404
# ============================================================

def test_inject_hint_other_user_returns_404(client: TestClient, make_user):
    alice = make_user("alice")
    bob = make_user("bob")
    project_id = _create_project(client, alice["headers"])
    sim_id = _insert_sim_directly(project_id, alice["user_id"], state="directing")

    r = client.post(
        f"/api/simulations/{sim_id}/inject_scene_hint",
        headers=bob["headers"],
        json={"hint": "我能跨用户干预吗"},
    )
    assert r.status_code == 404


# ============================================================
# A4. hint 过长(>300 字) → 422
# ============================================================

def test_inject_hint_too_long_returns_422(client: TestClient, make_user):
    user = make_user("alice")
    h = user["headers"]
    project_id = _create_project(client, h)
    sim_id = _insert_sim_directly(project_id, user["user_id"], state="directing")

    long_hint = "重复" * 200  # 400 字
    r = client.post(
        f"/api/simulations/{sim_id}/inject_scene_hint",
        headers=h, json={"hint": long_hint},
    )
    assert r.status_code == 422


# ============================================================
# A5. hint 为空 → 422
# ============================================================

def test_inject_hint_empty_returns_422(client: TestClient, make_user):
    user = make_user("alice")
    h = user["headers"]
    project_id = _create_project(client, h)
    sim_id = _insert_sim_directly(project_id, user["user_id"], state="directing")

    r = client.post(
        f"/api/simulations/{sim_id}/inject_scene_hint",
        headers=h, json={"hint": ""},
    )
    assert r.status_code == 422


# ============================================================
# B1. helper:_consume_pending_hint 读 + 立即清空
# ============================================================

def test_consume_pending_hint_reads_and_clears(client: TestClient, make_user):
    """字段非空 → 返回 hint 文本 + 字段被立即设为 NULL(消耗式)。"""
    user = make_user("alice")
    project_id = _create_project(client, user["headers"])
    sim_id = _insert_sim_directly(
        project_id, user["user_id"], state="directing",
        pending_scene_hint="让主角爆发",
    )

    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        hint = _consume_pending_hint(conn, sim_id)
    finally:
        conn.close()

    assert hint == "让主角爆发"
    # 字段已清空
    saved = _read_pending_hint_from_db(sim_id)
    assert saved is None


def test_consume_pending_hint_returns_none_when_empty(
    client: TestClient, make_user,
):
    """字段 NULL → 返回 None,不抛。"""
    user = make_user("alice")
    project_id = _create_project(client, user["headers"])
    sim_id = _insert_sim_directly(
        project_id, user["user_id"], state="directing",
        pending_scene_hint=None,
    )

    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        hint = _consume_pending_hint(conn, sim_id)
    finally:
        conn.close()

    assert hint is None


def test_consume_pending_hint_returns_none_when_whitespace(
    client: TestClient, make_user,
):
    """字段全空白(用户手贱提交 ' ') → 视同 None。"""
    user = make_user("alice")
    project_id = _create_project(client, user["headers"])
    sim_id = _insert_sim_directly(
        project_id, user["user_id"], state="directing",
        pending_scene_hint="   ",
    )

    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        hint = _consume_pending_hint(conn, sim_id)
    finally:
        conn.close()

    assert hint is None


# ============================================================
# B2. helper:_build_user_hint_block 输出
# ============================================================

def test_build_user_hint_block_empty_returns_empty_string():
    """空 hint(None / '')→ 空字符串(不动 prompt)。"""
    assert _build_user_hint_block(None) == ""
    assert _build_user_hint_block("") == ""


def test_build_user_hint_block_contains_marker_and_text():
    """非空 hint → 包含"用户即时干预"标记 + 原文。"""
    block = _build_user_hint_block("让主角爆发")
    assert "用户即时干预" in block
    assert "让主角爆发" in block
    assert "最高优先级" in block or "必须" in block
    # 提示是单次消耗,后续幕不再生效 — 让 LLM 不要"记住"
    assert "当前这一幕" in block or "后续幕不再" in block


# ============================================================
# B3. 同 sim 二次提交 — 后写覆盖前写(用户改主意)
# ============================================================

def test_inject_hint_second_post_overrides_first(client: TestClient, make_user):
    user = make_user("alice")
    h = user["headers"]
    project_id = _create_project(client, h)
    sim_id = _insert_sim_directly(project_id, user["user_id"], state="directing")

    # 第 1 次提交
    r1 = client.post(
        f"/api/simulations/{sim_id}/inject_scene_hint",
        headers=h, json={"hint": "第一条 hint"},
    )
    assert r1.status_code == 200
    # 第 2 次提交(用户改主意)
    r2 = client.post(
        f"/api/simulations/{sim_id}/inject_scene_hint",
        headers=h, json={"hint": "第二条 hint - 覆盖前一条"},
    )
    assert r2.status_code == 200

    # DB 只有最新的(后写覆盖)
    saved = _read_pending_hint_from_db(sim_id)
    assert saved == "第二条 hint - 覆盖前一条"
