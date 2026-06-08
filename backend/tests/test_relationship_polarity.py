"""SP-7 关系正负极性 测试(2026-05-28)."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import execute, get_connection


def _setup_project_with_two_chars(user_id: str) -> tuple[str, str, str]:
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
        for cid, name in ((a, "X"), (b, "Y")):
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


def test_create_relationship_with_polarity(client: TestClient, make_user):
    """POST 创建关系带 polarity 字段 → 持久化."""
    u = make_user("pol_create")
    pid, a, b = _setup_project_with_two_chars(u["user_id"])

    r = client.post(
        f"/api/projects/{pid}/relationships",
        headers=u["headers"],
        json={
            "source_id": a,
            "target_id": b,
            "type": "宿敌",
            "polarity": "negative",
            "strength": "strong",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["polarity"] == "negative"
    assert body["strength"] == "strong"


def test_patch_relationship_polarity(client: TestClient, make_user):
    """PATCH 改 polarity → 反映到 GET 列表."""
    u = make_user("pol_patch")
    pid, a, b = _setup_project_with_two_chars(u["user_id"])

    # 创建无 polarity 关系
    r = client.post(
        f"/api/projects/{pid}/relationships",
        headers=u["headers"],
        json={"source_id": a, "target_id": b, "type": "情侣"},
    )
    rid = r.json()["id"]
    assert r.json()["polarity"] is None

    # PATCH 加 positive
    r2 = client.patch(
        f"/api/relationships/{rid}",
        headers=u["headers"],
        json={"polarity": "positive"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["polarity"] == "positive"

    # GET 列表也应反映
    r3 = client.get(
        f"/api/projects/{pid}/relationships",
        headers=u["headers"],
    )
    assert r3.json()[0]["polarity"] == "positive"


def test_invalid_polarity_rejected(client: TestClient, make_user):
    """非法 polarity 字符串 → 422."""
    u = make_user("pol_invalid")
    pid, a, b = _setup_project_with_two_chars(u["user_id"])

    r = client.post(
        f"/api/projects/{pid}/relationships",
        headers=u["headers"],
        json={
            "source_id": a, "target_id": b,
            "type": "X", "polarity": "好坏不分",
        },
    )
    assert r.status_code == 422


def test_polarity_tracked_as_counterfactual(client: TestClient, make_user):
    """改 polarity → 落 counterfactual_changes(SP-7 反事实变量)."""
    u = make_user("pol_cf")
    pid, a, b = _setup_project_with_two_chars(u["user_id"])

    r = client.post(
        f"/api/projects/{pid}/relationships",
        headers=u["headers"],
        json={"source_id": a, "target_id": b, "type": "夫妻", "polarity": "positive"},
    )
    rid = r.json()["id"]

    # 改正负
    client.patch(
        f"/api/relationships/{rid}",
        headers=u["headers"],
        json={"polarity": "negative"},
    )

    # 查 counterfactual_changes(列名是 target_type / target_id)
    conn = get_connection()
    try:
        rows = list(conn.execute(
            "SELECT * FROM counterfactual_changes "
            "WHERE target_type='relationship' AND target_id=? AND field='polarity'",
            (rid,),
        ))
    finally:
        conn.close()
    assert len(rows) == 1
    # old/new value 序列化方式可能是 JSON,查看 raw 字段
    keys = rows[0].keys()
    # 取 value 列,可能叫 old_value / new_value / old_value_json
    old_col = next((k for k in keys if "old" in k.lower()), None)
    new_col = next((k for k in keys if "new" in k.lower()), None)
    assert old_col is not None and new_col is not None
    assert "positive" in str(rows[0][old_col])
    assert "negative" in str(rows[0][new_col])
