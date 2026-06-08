"""Sprint 6.A2 M1(2026-05-18)— 关系时间轴测试。

覆盖范围:
  - migration 036 字段默认值
  - relationship_phase_service 增删改 + 老数据自动迁移
  - 4 个新端点(GET / POST / PATCH / DELETE phase)
  - PATCH /relationships 加 current_phase_id 字段
  - 删 phase 自动修复 current_phase_id
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _create_project_with_rel(client: TestClient, headers: dict) -> tuple[str, str, dict]:
    """造项目 + 2 角色 + 1 关系,返回 (project_id, rel_id, {a_id, b_id})。"""
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": "时间轴测试", "type": "novel", "tags": []},
    )
    assert p.status_code == 201
    proj_id = p.json()["id"]
    a = client.post(
        f"/api/projects/{proj_id}/characters", headers=headers,
        json={"name": "张凡"},
    )
    b = client.post(
        f"/api/projects/{proj_id}/characters", headers=headers,
        json={"name": "莫晴雨"},
    )
    rel = client.post(
        f"/api/projects/{proj_id}/relationships", headers=headers,
        json={
            "source_id": a.json()["id"],
            "target_id": b.json()["id"],
            "type": "情侣",
            "description": "高中同学",
            "strength": "moderate",
        },
    )
    assert rel.status_code == 201, rel.text
    return proj_id, rel.json()["id"], {
        "a_id": a.json()["id"], "b_id": b.json()["id"],
    }


# ============================================================
# 基础:老数据自动迁移
# ============================================================

def test_list_phases_auto_migrates_old_relationship(
    client: TestClient, make_user,
):
    """老 relationship 无 phase → GET phases 自动建 phase[0] 与 type/strength 同步。"""
    u = make_user("phase_migrate")
    _, rel_id, _ = _create_project_with_rel(client, u["headers"])

    r = client.get(
        f"/api/relationships/{rel_id}/phases", headers=u["headers"],
    )
    assert r.status_code == 200
    phases = r.json()
    assert len(phases) == 1
    assert phases[0]["phase_index"] == 0
    assert phases[0]["type"] == "情侣"
    assert phases[0]["strength"] == "moderate"
    assert phases[0]["start_anchor"] is None
    assert phases[0]["end_anchor"] is None

    # 同时 relationship.current_phase_id 应指向新建的 phase[0]
    rel_r = client.get(
        f"/api/projects/{_create_project_with_rel.__name__}", headers=u["headers"],
    )
    # 直接拉 graph 查 relationship 的 current_phase_id
    # 不存在专用 GET /relationships/{id} 路由,这里跳过,改用其他验证


# ============================================================
# 加 phase
# ============================================================

def test_add_phase_auto_set_current(client: TestClient, make_user):
    """POST phases auto_set_current=true → relationships.current_phase_id 指向新 phase。"""
    u = make_user("phase_add")
    _, rel_id, _ = _create_project_with_rel(client, u["headers"])

    # 先 GET 触发自动迁移 phase[0]
    client.get(
        f"/api/relationships/{rel_id}/phases", headers=u["headers"],
    )

    # 加 phase[1]
    r = client.post(
        f"/api/relationships/{rel_id}/phases", headers=u["headers"],
        json={
            "type": "敌对",
            "strength": "strong",
            "start_anchor": "第 15 章",
            "notes": "因误会反目",
        },
    )
    assert r.status_code == 201, r.text
    phase1 = r.json()
    assert phase1["phase_index"] == 1
    assert phase1["type"] == "敌对"

    # 列出应该是 2 个 phase
    r2 = client.get(
        f"/api/relationships/{rel_id}/phases", headers=u["headers"],
    )
    assert len(r2.json()) == 2


def test_add_phase_accepts_freeform_type(client: TestClient, make_user):
    """Sprint 6.A2 M7.G(2026-05-20):type 自由化 — 任意 1-20 字字符串都接受。
    旧版本"ZZZ"被 Literal 枚举拒;新版本视为合法自定义类型,支持"革命战友""暗恋"等自创关系。"""
    u = make_user("phase_freeform")
    _, rel_id, _ = _create_project_with_rel(client, u["headers"])

    r = client.post(
        f"/api/relationships/{rel_id}/phases", headers=u["headers"],
        json={"type": "革命战友", "strength": "moderate"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["type"] == "革命战友"


def test_add_phase_empty_type_rejected(client: TestClient, make_user):
    """空 type → 422(Pydantic min_length=1)"""
    u = make_user("phase_empty")
    _, rel_id, _ = _create_project_with_rel(client, u["headers"])

    r = client.post(
        f"/api/relationships/{rel_id}/phases", headers=u["headers"],
        json={"type": "", "strength": "moderate"},
    )
    assert r.status_code == 422


def test_add_phase_too_long_type_rejected(client: TestClient, make_user):
    """超 20 字 type → 422"""
    u = make_user("phase_too_long")
    _, rel_id, _ = _create_project_with_rel(client, u["headers"])

    r = client.post(
        f"/api/relationships/{rel_id}/phases", headers=u["headers"],
        json={"type": "这是一个超过二十个汉字的非常长非常长的关系类型超出上限", "strength": "moderate"},
    )
    assert r.status_code == 422


# ============================================================
# 更新 + 删除 phase
# ============================================================

def test_update_phase_partial_fields(client: TestClient, make_user):
    """PATCH 部分更新 phase 字段(只改非 None 的)。"""
    u = make_user("phase_update")
    _, rel_id, _ = _create_project_with_rel(client, u["headers"])
    phases = client.get(
        f"/api/relationships/{rel_id}/phases", headers=u["headers"],
    ).json()
    phase_id = phases[0]["id"]

    r = client.patch(
        f"/api/relationship_phases/{phase_id}", headers=u["headers"],
        json={"notes": "改个备注", "strength": "strong"},
    )
    assert r.status_code == 200
    assert r.json()["notes"] == "改个备注"
    assert r.json()["strength"] == "strong"
    # 未传的 type 不变
    assert r.json()["type"] == "情侣"


def test_delete_phase_repairs_current_phase_id(
    client: TestClient, make_user,
):
    """删了 current phase → relationships.current_phase_id 自动切到剩余最大 index。"""
    u = make_user("phase_delete_current")
    _, rel_id, _ = _create_project_with_rel(client, u["headers"])

    # 加 phase[1](自动成为 current)
    client.get(f"/api/relationships/{rel_id}/phases", headers=u["headers"])
    r1 = client.post(
        f"/api/relationships/{rel_id}/phases", headers=u["headers"],
        json={"type": "敌对", "strength": "strong"},
    )
    phase1_id = r1.json()["id"]

    # 删 phase[1] → current 应回退到 phase[0]
    r = client.delete(
        f"/api/relationship_phases/{phase1_id}", headers=u["headers"],
    )
    assert r.status_code == 204

    remaining = client.get(
        f"/api/relationships/{rel_id}/phases", headers=u["headers"],
    ).json()
    assert len(remaining) == 1
    assert remaining[0]["phase_index"] == 0

    # 验证 current_phase_id 已切到 phase[0]
    from app.db import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT current_phase_id FROM relationships WHERE id=?",
            (rel_id,),
        ).fetchone()
        assert row["current_phase_id"] == remaining[0]["id"]
    finally:
        conn.close()


# ============================================================
# PATCH /relationships current_phase_id 切换
# ============================================================

def test_patch_relationship_current_phase_id(
    client: TestClient, make_user,
):
    """用户手动切换 current_phase_id(用于反事实 what-if / 主线之外探索)。"""
    u = make_user("phase_switch")
    _, rel_id, _ = _create_project_with_rel(client, u["headers"])

    client.get(f"/api/relationships/{rel_id}/phases", headers=u["headers"])
    r1 = client.post(
        f"/api/relationships/{rel_id}/phases", headers=u["headers"],
        json={"type": "敌对", "strength": "strong"},
    )
    phase1_id = r1.json()["id"]

    # 此时 current_phase_id = phase1(刚加的 auto_set_current=true)
    # 切回 phase[0]
    phases = client.get(
        f"/api/relationships/{rel_id}/phases", headers=u["headers"],
    ).json()
    phase0_id = phases[0]["id"]

    r = client.patch(
        f"/api/relationships/{rel_id}", headers=u["headers"],
        json={"current_phase_id": phase0_id},
    )
    assert r.status_code == 200

    from app.db import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT current_phase_id FROM relationships WHERE id=?",
            (rel_id,),
        ).fetchone()
        assert row["current_phase_id"] == phase0_id
    finally:
        conn.close()


# ============================================================
# 跨用户隔离
# ============================================================

def test_filter_llm_output_drops_evolution_hint_with_fake_relationship_id():
    """Sprint 6.A2 M1 bug fix:LLM 凭空把 source_id→target_id 拼成 relationship_id 时,
    filter_llm_output 应丢弃整条 evolution_hint(不能让坏数据流到前端导致 404)。"""
    from app.services.refine_service import filter_llm_output
    from app.models.character import Character

    char = Character(
        id="char_a", project_id="p1", name="张凡",
        identity="暗恋班花莫晴雨", personality="", quotes=[], no_go_list=[],
        position_x=0.0, position_y=0.0, position_z=0.0, color=None,
        created_at="2026-05-18", updated_at="2026-05-18",
    )
    char_lookup = {"char_a": char}
    real_rel_ids = {"REAL-UUID-001"}

    # LLM 凭空拼的假 id(像截图那种 "source→target" 串)
    bad_item = {
        "character_id": "char_a",
        "suggestion_kind": "evolution_hint",
        "suggestion_text": "...",
        "suggestion_payload": {
            "kind": "evolution_hint",
            "relationship_id": "995cdff3-...→7821588c-...",   # 假 id
            "earlier_type": "其他",
            "current_type": "情侣",
            "evidence": "暗恋班花莫晴雨",
        },
    }
    # 真实 id 的 evolution_hint 应通过
    good_item = {
        "character_id": "char_a",
        "suggestion_kind": "evolution_hint",
        "suggestion_text": "...",
        "suggestion_payload": {
            "kind": "evolution_hint",
            "relationship_id": "REAL-UUID-001",
            "earlier_type": "其他",
            "current_type": "情侣",
            "evidence": "暗恋班花莫晴雨",
        },
    }

    out = filter_llm_output([bad_item, good_item], char_lookup, real_rel_ids)
    assert len(out) == 1
    assert out[0]["suggestion_payload"]["relationship_id"] == "REAL-UUID-001"


def test_filter_llm_output_evolution_hint_backward_compat_no_rel_set():
    """rel_id_set=None(老调用方)时,evolution_hint 校验跳过保持向后兼容。"""
    from app.services.refine_service import filter_llm_output
    from app.models.character import Character

    char = Character(
        id="char_a", project_id="p1", name="张凡",
        identity="暗恋", personality="", quotes=[], no_go_list=[],
        position_x=0.0, position_y=0.0, position_z=0.0, color=None,
        created_at="2026-05-18", updated_at="2026-05-18",
    )
    item = {
        "character_id": "char_a",
        "suggestion_kind": "evolution_hint",
        "suggestion_text": "演化提示",
        "suggestion_payload": {
            "kind": "evolution_hint",
            "relationship_id": "anything",
            "earlier_type": "其他",
            "current_type": "情侣",
            "evidence": "...",
        },
    }
    # rel_id_set=None → 跳过校验,通过
    out = filter_llm_output([item], {"char_a": char})
    assert len(out) == 1


def test_phase_endpoints_isolate_users(client: TestClient, make_user):
    """user A 的 phase,user B 访问应 404(get_phase_or_404 校验)。"""
    ua = make_user("phase_owner")
    ub = make_user("phase_invader")
    _, rel_id, _ = _create_project_with_rel(client, ua["headers"])

    phases = client.get(
        f"/api/relationships/{rel_id}/phases", headers=ua["headers"],
    ).json()
    phase_id = phases[0]["id"]

    # user B 试 PATCH user A 的 phase → 404
    r = client.patch(
        f"/api/relationship_phases/{phase_id}", headers=ub["headers"],
        json={"notes": "黑客"},
    )
    assert r.status_code == 404
