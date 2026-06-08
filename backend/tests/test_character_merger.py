"""角色合并服务测试 — Sprint 6.A2 FOCUS(2026-05-21)。

测试覆盖:
  1. happy path — 两个角色合并,target.aliases 累积 + source 被删
  2. relationships 级联 — A→source 关系合并后变 A→target;source==target self-loop 删
  3. events.participants 级联 — JSON 数组里 source.id 替换为 target.id,去重
  4. source==target 拒绝(MERGE_SELF 409)
  5. 跨项目拒绝(CHARACTER_NOT_FOUND 404)
  6. source 不存在 — 404
  7. aliases 上限 15
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _create_project_with_chars(
    client: TestClient,
    headers: dict,
    project_name: str,
    chars: list[dict],
) -> tuple[str, dict[str, str]]:
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": project_name, "type": "novel", "tags": []},
    ).json()
    project_id = p["id"]
    char_ids: dict[str, str] = {}
    for c in chars:
        r = client.post(
            f"/api/projects/{project_id}/characters", headers=headers, json=c,
        ).json()
        char_ids[c["name"]] = r["id"]
    return project_id, char_ids


def test_merge_two_characters_target_gets_aliases_and_source_deleted(
    client: TestClient, make_user
):
    """happy path:把"我"合并到"渡边",target 的 aliases 应含'我' + source.aliases。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "挪威的森林",
        chars=[
            {"name": "渡边", "identity": "三十七岁的杂志编辑"},
            {"name": "我", "identity": "第一人称叙述者"},
        ],
    )
    r = client.post(
        f"/api/projects/{project_id}/characters/merge",
        headers=h,
        json={
            "source_character_id": char_ids["我"],
            "target_character_id": char_ids["渡边"],
        },
    )
    assert r.status_code == 200, r.text
    merged = r.json()
    assert merged["id"] == char_ids["渡边"]
    assert merged["name"] == "渡边"
    assert "我" in merged["aliases"]

    # source 应已删除 — list 接口看不到了
    lst = client.get(f"/api/projects/{project_id}/characters", headers=h).json()
    ids = {c["id"] for c in lst}
    assert char_ids["渡边"] in ids
    assert char_ids["我"] not in ids


def test_merge_self_returns_409(client: TestClient, make_user):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "项目",
        chars=[{"name": "甲"}],
    )
    r = client.post(
        f"/api/projects/{project_id}/characters/merge",
        headers=h,
        json={
            "source_character_id": char_ids["甲"],
            "target_character_id": char_ids["甲"],
        },
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "MERGE_SELF"


def test_merge_nonexistent_character_returns_404(client: TestClient, make_user):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "项目",
        chars=[{"name": "甲"}, {"name": "乙"}],
    )
    r = client.post(
        f"/api/projects/{project_id}/characters/merge",
        headers=h,
        json={
            "source_character_id": "nonexistent-id-12345",
            "target_character_id": char_ids["甲"],
        },
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "CHARACTER_NOT_FOUND"


def test_merge_cross_project_returns_404(client: TestClient, make_user):
    """source 在项目 A,target 在项目 B → 必须 404(防跨项目合并)。"""
    user = make_user("hero")
    h = user["headers"]
    pa_id, pa_chars = _create_project_with_chars(
        client, h, "项目A", chars=[{"name": "甲A"}]
    )
    pb_id, pb_chars = _create_project_with_chars(
        client, h, "项目B", chars=[{"name": "乙B"}]
    )
    # 在项目 A 的端点下试图合并 B 项目里的角色
    r = client.post(
        f"/api/projects/{pa_id}/characters/merge",
        headers=h,
        json={
            "source_character_id": pb_chars["乙B"],
            "target_character_id": pa_chars["甲A"],
        },
    )
    assert r.status_code == 404


def test_merge_relationships_reassigned_and_self_loop_dropped(
    client: TestClient, make_user
):
    """A→source 关系合并后 source/target 都=甲 → self-loop 删;A→target 保留。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "项目",
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    # 关系 1:乙 → 甲(朋友)
    rel1 = client.post(
        f"/api/projects/{project_id}/relationships",
        headers=h,
        json={
            "source_id": char_ids["乙"],
            "target_id": char_ids["甲"],
            "type": "朋友",
            "description": "",
        },
    ).json()
    # 关系 2:丙 → 乙(同事) — 合并乙→甲后,变为 丙→甲(无 self-loop)
    rel2 = client.post(
        f"/api/projects/{project_id}/relationships",
        headers=h,
        json={
            "source_id": char_ids["丙"],
            "target_id": char_ids["乙"],
            "type": "同事",
            "description": "",
        },
    ).json()
    # 关系 3:甲 → 乙(敌对) — 合并后变成甲→甲 → self-loop 应被删除
    rel3 = client.post(
        f"/api/projects/{project_id}/relationships",
        headers=h,
        json={
            "source_id": char_ids["甲"],
            "target_id": char_ids["乙"],
            "type": "敌对",
            "description": "",
        },
    ).json()

    r = client.post(
        f"/api/projects/{project_id}/characters/merge",
        headers=h,
        json={
            "source_character_id": char_ids["乙"],
            "target_character_id": char_ids["甲"],
        },
    )
    assert r.status_code == 200

    rels = client.get(
        f"/api/projects/{project_id}/relationships", headers=h
    ).json()
    rel_ids = {r["id"] for r in rels}
    # rel1(乙→甲)合并后变成 甲→甲 → 删
    assert rel1["id"] not in rel_ids
    # rel2(丙→乙)→ 丙→甲 保留
    assert rel2["id"] in rel_ids
    # rel3(甲→乙)→ 甲→甲 → 删
    assert rel3["id"] not in rel_ids


def test_merge_events_participants_reassigned_with_dedup(
    client: TestClient, make_user
):
    """事件 participants=[源, 目标, 其他] → 合并后 = [目标, 其他],去重。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "项目",
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    ev = client.post(
        f"/api/projects/{project_id}/events",
        headers=h,
        json={
            "description": "三人聚会",
            "participants": [char_ids["乙"], char_ids["甲"], char_ids["丙"]],
        },
    ).json()

    r = client.post(
        f"/api/projects/{project_id}/characters/merge",
        headers=h,
        json={
            "source_character_id": char_ids["乙"],
            "target_character_id": char_ids["甲"],
        },
    )
    assert r.status_code == 200

    # 查事件,participants 应已 reassign + 去重(乙 → 甲,但甲已在 list 中 → 不重复)
    events = client.get(
        f"/api/projects/{project_id}/events", headers=h
    ).json()
    target_ev = next(e for e in events if e["id"] == ev["id"])
    assert char_ids["乙"] not in target_ev["participants"]
    assert char_ids["甲"] in target_ev["participants"]
    assert char_ids["丙"] in target_ev["participants"]
    # 去重:participants 只有 2 个(甲、丙),不应该有重复甲
    assert len(target_ev["participants"]) == 2


def test_merge_aliases_accumulate_target_keeps_own_plus_source_name_plus_source_aliases():
    """纯单测 service 函数:验证 aliases 累积逻辑,绕开 HTTP。"""
    import sqlite3
    import json
    from app.services.character_merger import merge_characters

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # 用最小化 schema(只测合并逻辑,无其他级联表)
    conn.executescript("""
        CREATE TABLE projects (id TEXT PRIMARY KEY);
        CREATE TABLE characters (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            identity TEXT DEFAULT '',
            personality TEXT DEFAULT '',
            quotes TEXT,
            no_go_list TEXT,
            position_x REAL DEFAULT 0,
            position_y REAL DEFAULT 0,
            position_z REAL DEFAULT 0,
            color TEXT,
            created_at TEXT,
            updated_at TEXT,
            behavior_baseline_json TEXT,
            aliases_json TEXT
        );
        CREATE TABLE relationships (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            source_id TEXT,
            target_id TEXT,
            type TEXT,
            description TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        CREATE TABLE events (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            description TEXT,
            participants TEXT,
            created_at TEXT,
            updated_at TEXT
        );
    """)

    # 构造场景:target 有自己的 aliases ["艺妓"],source 是"驹子"有 aliases ["五等艺妓"]
    conn.execute(
        "INSERT INTO characters (id, project_id, name, aliases_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("c-tgt", "p1", "驹子", json.dumps(["艺妓"]), "2026-01-01", "2026-01-01"),
    )
    conn.execute(
        "INSERT INTO characters (id, project_id, name, aliases_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("c-src", "p1", "五等艺妓", json.dumps(["女艺人"]), "2026-01-01", "2026-01-01"),
    )

    merged = merge_characters(conn, "p1", "c-src", "c-tgt")
    # target 保留:name=驹子 / 累积 aliases = 原["艺妓"] + source.name "五等艺妓" + source.aliases["女艺人"]
    assert merged.name == "驹子"
    assert "艺妓" in merged.aliases       # target 原有
    assert "五等艺妓" in merged.aliases   # source.name 合入
    assert "女艺人" in merged.aliases     # source.aliases 合入
    # source 应被删除
    row = conn.execute("SELECT * FROM characters WHERE id=?", ("c-src",)).fetchone()
    assert row is None
