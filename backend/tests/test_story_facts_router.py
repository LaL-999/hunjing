"""SP-3 知识边界 router 测试(2026-05-28)."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import execute, get_connection


def _setup_project_with_chars(user_id: str) -> tuple[str, str, str]:
    """造 project + 2 个 character(返 project_id, char_a_id, char_b_id)."""
    pid = str(uuid.uuid4())
    a = str(uuid.uuid4())
    b = str(uuid.uuid4())
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
        for cid, name in ((a, "甲"), (b, "乙")):
            execute(
                conn,
                "INSERT INTO characters "
                "(id, project_id, name, identity, personality, quotes, no_go_list, "
                " position_x, position_y, position_z, color, created_at, updated_at) "
                "VALUES (?, ?, ?, '', '', '[]', '[]', 0, 0, 0, NULL, ?, ?)",
                (cid, pid, name, now, now),
            )
        conn.commit()
        return pid, a, b
    finally:
        conn.close()


def test_create_and_list_story_fact(client: TestClient, make_user):
    """POST 录入 + GET 列表."""
    u = make_user("sf_create")
    pid, *_ = _setup_project_with_chars(u["user_id"])

    r = client.post(
        f"/api/projects/{pid}/story_facts",
        headers=u["headers"],
        json={
            "description": "直子已自杀",
            "first_revealed_scene": 8,
            "is_sensitive": True,
        },
    )
    assert r.status_code == 201, r.text
    fid = r.json()["id"]
    assert r.json()["is_sensitive"] is True

    r2 = client.get(
        f"/api/projects/{pid}/story_facts",
        headers=u["headers"],
    )
    assert r2.status_code == 200
    facts = r2.json()["facts"]
    assert len(facts) == 1
    assert facts[0]["id"] == fid
    assert facts[0]["description"] == "直子已自杀"
    # 默认 include_knowledge=false → 不带 known_by 字段
    assert "known_by" not in facts[0]


def test_list_with_include_knowledge_returns_known_by(
    client: TestClient, make_user,
):
    """include_knowledge=true → 每条 fact 带 known_by[] 数组."""
    u = make_user("sf_with_knowledge")
    pid, ca, cb = _setup_project_with_chars(u["user_id"])

    # 录 1 条事实
    r = client.post(
        f"/api/projects/{pid}/story_facts",
        headers=u["headers"],
        json={"description": "X 杀了 Y"},
    )
    fid = r.json()["id"]

    # 标 ca 已知,cb 不标
    r2 = client.post(
        f"/api/story_facts/{fid}/known_by/{ca}",
        headers=u["headers"],
        json={"known_since_scene": 3, "confidence": "confirmed"},
    )
    assert r2.status_code == 200

    # 列 with knowledge
    r3 = client.get(
        f"/api/projects/{pid}/story_facts?include_knowledge=true",
        headers=u["headers"],
    )
    assert r3.status_code == 200
    facts = r3.json()["facts"]
    assert len(facts) == 1
    f0 = facts[0]
    assert len(f0["known_by"]) == 1
    assert f0["known_by"][0]["character_id"] == ca
    assert f0["known_by"][0]["character_name"] == "甲"
    assert f0["known_by"][0]["known_since_scene"] == 3
    assert f0["known_by"][0]["confidence"] == "confirmed"


def test_mark_known_then_unmark(client: TestClient, make_user):
    """POST known_by → DELETE 撤销."""
    u = make_user("sf_mark_unmark")
    pid, ca, _ = _setup_project_with_chars(u["user_id"])

    fr = client.post(
        f"/api/projects/{pid}/story_facts",
        headers=u["headers"],
        json={"description": "F"},
    )
    fid = fr.json()["id"]

    # 标
    r = client.post(
        f"/api/story_facts/{fid}/known_by/{ca}",
        headers=u["headers"],
        json={"confidence": "suspected"},
    )
    assert r.status_code == 200
    assert r.json()["confidence"] == "suspected"

    # 撤销
    r2 = client.delete(
        f"/api/story_facts/{fid}/known_by/{ca}",
        headers=u["headers"],
    )
    assert r2.status_code == 204

    # 再列,应 0 known_by
    r3 = client.get(
        f"/api/projects/{pid}/story_facts?include_knowledge=true",
        headers=u["headers"],
    )
    assert len(r3.json()["facts"][0]["known_by"]) == 0


def test_list_character_known_facts(client: TestClient, make_user):
    """GET /characters/{cid}/known_facts."""
    u = make_user("sf_char_known")
    pid, ca, _ = _setup_project_with_chars(u["user_id"])

    # 录 2 条
    for desc in ("F1", "F2"):
        fr = client.post(
            f"/api/projects/{pid}/story_facts",
            headers=u["headers"],
            json={"description": desc},
        )
        fid = fr.json()["id"]
        client.post(
            f"/api/story_facts/{fid}/known_by/{ca}",
            headers=u["headers"],
            json={},
        )

    r = client.get(
        f"/api/characters/{ca}/known_facts",
        headers=u["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["character_id"] == ca
    assert len(body["facts"]) == 2


def test_delete_fact_cascades_knowledge(client: TestClient, make_user):
    """删事实 → character_knowledge 也清."""
    u = make_user("sf_delete_cascade")
    pid, ca, _ = _setup_project_with_chars(u["user_id"])

    fr = client.post(
        f"/api/projects/{pid}/story_facts",
        headers=u["headers"],
        json={"description": "F"},
    )
    fid = fr.json()["id"]
    client.post(
        f"/api/story_facts/{fid}/known_by/{ca}",
        headers=u["headers"],
        json={},
    )

    r = client.delete(
        f"/api/story_facts/{fid}", headers=u["headers"],
    )
    assert r.status_code == 204

    # 该角色 known_facts 现应空
    r2 = client.get(
        f"/api/characters/{ca}/known_facts",
        headers=u["headers"],
    )
    assert r2.json()["facts"] == []


def test_cross_user_project_returns_404(client: TestClient, make_user):
    """非自己的 project 404."""
    alice = make_user("sf_alice")
    bob = make_user("sf_bob")
    pid, _, _ = _setup_project_with_chars(alice["user_id"])

    r = client.get(
        f"/api/projects/{pid}/story_facts",
        headers=bob["headers"],
    )
    assert r.status_code == 404
