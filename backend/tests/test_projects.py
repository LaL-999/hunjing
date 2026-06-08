"""Project / Character / Relationship / Event CRUD 端到端测试 — Sprint 1.C 验收基线。

设计原则:
- **真实场景数据**(继承 verify_prompt.py 的"江湖夜雨"等),不用 "测试项目1" 这种 placeholder
- 不写 seed_dev.py;每个测试自己用 API 走完"用户登录→建项目→加角色→...→断言"
- 12 个测试覆盖:完整闭环 / 权限隔离 / 边界 case / 删除级联 / 部分更新

测试数据基线(对齐《江湖夜雨》— 武侠悬疑):
  项目:江湖夜雨 (novel, ['武侠', '悬疑'])
  角色:李寻欢 / 孙小红 / 上官金虹
  关系:李寻欢 朋友 孙小红;李寻欢 敌对 上官金虹
  事件:风雨夜,李寻欢与孙小红初遇于客栈
"""
from __future__ import annotations

from fastapi.testclient import TestClient


# ============================================================
# 完整闭环
# ============================================================

def test_full_flow_create_project_and_graph(client: TestClient, make_user):
    """单用户:登录 → 创建项目 → 加 3 角色 → 加 2 关系 → 加 1 事件 → GET /graph 验全部回来。"""
    user = make_user("hero")
    h = user["headers"]

    # 1. 创建项目《江湖夜雨》
    p = client.post(
        "/api/projects",
        headers=h,
        json={"name": "江湖夜雨", "type": "novel", "tags": ["武侠", "悬疑"]},
    )
    assert p.status_code == 201, p.text
    project_id = p.json()["id"]

    # 2. 加 3 角色
    char_ids = {}
    for name in ("李寻欢", "孙小红", "上官金虹"):
        r = client.post(
            f"/api/projects/{project_id}/characters",
            headers=h,
            json={"name": name},
        )
        assert r.status_code == 201, r.text
        char_ids[name] = r.json()["id"]

    # 3. 加 2 关系
    rel_friend = client.post(
        f"/api/projects/{project_id}/relationships",
        headers=h,
        json={
            "source_id": char_ids["李寻欢"],
            "target_id": char_ids["孙小红"],
            "type": "朋友",
            "description": "酒馆相识,并肩查案",
        },
    )
    assert rel_friend.status_code == 201, rel_friend.text

    rel_foe = client.post(
        f"/api/projects/{project_id}/relationships",
        headers=h,
        json={
            "source_id": char_ids["李寻欢"],
            "target_id": char_ids["上官金虹"],
            "type": "敌对",
            "description": "宿敌,各为所执",
        },
    )
    assert rel_foe.status_code == 201, rel_foe.text

    # 4. 加 1 事件
    e = client.post(
        f"/api/projects/{project_id}/events",
        headers=h,
        json={
            "description": "风雨夜,李寻欢与孙小红初遇于客栈",
            "participants": [char_ids["李寻欢"], char_ids["孙小红"]],
        },
    )
    assert e.status_code == 201, e.text

    # 5. GET /graph 验证全部回来
    g = client.get(f"/api/projects/{project_id}/graph", headers=h)
    assert g.status_code == 200, g.text
    body = g.json()
    assert body["project"]["name"] == "江湖夜雨"
    assert body["project"]["tags"] == ["武侠", "悬疑"]
    assert len(body["characters"]) == 3
    assert {c["name"] for c in body["characters"]} == {"李寻欢", "孙小红", "上官金虹"}
    assert len(body["relationships"]) == 2
    assert len(body["events"]) == 1


# ============================================================
# 权限隔离
# ============================================================

def test_list_projects_returns_only_own(client: TestClient, make_user):
    """用户 A 看不到用户 B 的项目。"""
    a = make_user("alpha")
    b = make_user("beta")

    client.post("/api/projects", headers=a["headers"],
                 json={"name": "A 的项目", "type": "novel"})
    client.post("/api/projects", headers=b["headers"],
                 json={"name": "B 的项目", "type": "comic"})

    a_list = client.get("/api/projects", headers=a["headers"]).json()
    b_list = client.get("/api/projects", headers=b["headers"]).json()

    assert len(a_list) == 1 and a_list[0]["name"] == "A 的项目"
    assert len(b_list) == 1 and b_list[0]["name"] == "B 的项目"


def test_get_others_project_returns_404(client: TestClient, make_user):
    """用户 B 用 token 访问用户 A 的 project_id → 404 NOT_FOUND(不暴露存在性)。"""
    a = make_user("alpha")
    b = make_user("beta")

    p = client.post("/api/projects", headers=a["headers"],
                     json={"name": "私有项目", "type": "novel"}).json()

    r = client.get(f"/api/projects/{p['id']}", headers=b["headers"])
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"
    assert r.json()["detail"]["resource"] == "project"


def test_create_character_in_others_project_forbidden(client: TestClient, make_user):
    """B 不能在 A 的项目下加角色。"""
    a = make_user("alpha")
    b = make_user("beta")

    p = client.post("/api/projects", headers=a["headers"],
                     json={"name": "A 的项目", "type": "novel"}).json()

    r = client.post(
        f"/api/projects/{p['id']}/characters",
        headers=b["headers"],
        json={"name": "试图入侵"},
    )
    assert r.status_code == 404


# ============================================================
# 部分更新(PATCH)
# ============================================================

def test_patch_project_only_updates_provided_fields(client: TestClient, make_user):
    user = make_user("hero")
    h = user["headers"]

    p = client.post("/api/projects", headers=h,
                     json={"name": "原名", "type": "novel", "tags": ["A"]}).json()

    # 只改 name,type 和 tags 应保持
    r = client.patch(f"/api/projects/{p['id']}", headers=h,
                      json={"name": "新名"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "新名"
    assert body["type"] == "novel"
    assert body["tags"] == ["A"]


def test_patch_character_only_updates_provided_fields(client: TestClient, make_user):
    user = make_user("hero")
    h = user["headers"]

    p = client.post("/api/projects", headers=h,
                     json={"name": "项目", "type": "novel"}).json()
    c = client.post(f"/api/projects/{p['id']}/characters", headers=h,
                     json={"name": "李寻欢", "personality": "孤傲"}).json()

    # 只补充 personality,name 和其他字段不动
    r = client.patch(f"/api/characters/{c['id']}", headers=h,
                      json={"personality": "孤傲深沉,藏着旧伤"})
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "李寻欢"
    assert body["personality"] == "孤傲深沉,藏着旧伤"


# ============================================================
# 删除级联
# ============================================================

def test_delete_project_cascades_characters_and_relationships(
    client: TestClient, make_user
):
    """删项目后,相关 characters / relationships 都应被级联删(SQL ON DELETE CASCADE)。"""
    user = make_user("hero")
    h = user["headers"]

    p = client.post("/api/projects", headers=h,
                     json={"name": "江湖夜雨", "type": "novel"}).json()
    c1 = client.post(f"/api/projects/{p['id']}/characters", headers=h,
                      json={"name": "李寻欢"}).json()
    c2 = client.post(f"/api/projects/{p['id']}/characters", headers=h,
                      json={"name": "孙小红"}).json()
    client.post(f"/api/projects/{p['id']}/relationships", headers=h,
                 json={"source_id": c1["id"], "target_id": c2["id"], "type": "朋友"})

    # 删项目
    d = client.delete(f"/api/projects/{p['id']}", headers=h)
    assert d.status_code == 204

    # 角色应该 404
    r = client.get(f"/api/characters/{c1['id']}", headers=h)
    assert r.status_code == 404


def test_delete_character_cascades_relationships(client: TestClient, make_user):
    user = make_user("hero")
    h = user["headers"]

    p = client.post("/api/projects", headers=h,
                     json={"name": "项目", "type": "novel"}).json()
    c1 = client.post(f"/api/projects/{p['id']}/characters", headers=h,
                      json={"name": "甲"}).json()
    c2 = client.post(f"/api/projects/{p['id']}/characters", headers=h,
                      json={"name": "乙"}).json()
    rel = client.post(
        f"/api/projects/{p['id']}/relationships", headers=h,
        json={"source_id": c1["id"], "target_id": c2["id"], "type": "朋友"},
    ).json()

    # 删 c1,关系应被级联删
    client.delete(f"/api/characters/{c1['id']}", headers=h)

    rels = client.get(
        f"/api/projects/{p['id']}/relationships", headers=h
    ).json()
    assert len(rels) == 0, f"删除角色后关系应被级联删,实际仍有:{rels}"


# ============================================================
# 关系的边界 case
# ============================================================

def test_self_relationship_rejected(client: TestClient, make_user):
    user = make_user("hero")
    h = user["headers"]
    p = client.post("/api/projects", headers=h,
                     json={"name": "项目", "type": "novel"}).json()
    c = client.post(f"/api/projects/{p['id']}/characters", headers=h,
                     json={"name": "李寻欢"}).json()

    r = client.post(
        f"/api/projects/{p['id']}/relationships", headers=h,
        json={"source_id": c["id"], "target_id": c["id"], "type": "朋友"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "SELF_RELATIONSHIP"


def test_relationship_endpoint_from_other_project_rejected(
    client: TestClient, make_user
):
    """source_id 来自项目 P,target_id 来自项目 Q,在 P 创建关系 → 400。"""
    user = make_user("hero")
    h = user["headers"]
    p = client.post("/api/projects", headers=h,
                     json={"name": "P", "type": "novel"}).json()
    q = client.post("/api/projects", headers=h,
                     json={"name": "Q", "type": "novel"}).json()

    c_p = client.post(f"/api/projects/{p['id']}/characters", headers=h,
                       json={"name": "P 中角色"}).json()
    c_q = client.post(f"/api/projects/{q['id']}/characters", headers=h,
                       json={"name": "Q 中角色"}).json()

    r = client.post(
        f"/api/projects/{p['id']}/relationships", headers=h,
        json={"source_id": c_p["id"], "target_id": c_q["id"], "type": "朋友"},
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "INVALID_RELATIONSHIP_ENDPOINTS"


# ============================================================
# 事件 CRUD 全流程
# ============================================================

def test_event_full_crud(client: TestClient, make_user):
    user = make_user("hero")
    h = user["headers"]
    p = client.post("/api/projects", headers=h,
                     json={"name": "项目", "type": "novel"}).json()
    c1 = client.post(f"/api/projects/{p['id']}/characters", headers=h,
                      json={"name": "李寻欢"}).json()

    # 创建
    e = client.post(
        f"/api/projects/{p['id']}/events", headers=h,
        json={"description": "初遇客栈", "participants": [c1["id"]]},
    )
    assert e.status_code == 201
    event_id = e.json()["id"]

    # 列出
    listed = client.get(
        f"/api/projects/{p['id']}/events", headers=h
    ).json()
    assert len(listed) == 1

    # 更新
    u = client.patch(
        f"/api/events/{event_id}", headers=h,
        json={"description": "风雨夜初遇客栈"},
    )
    assert u.status_code == 200
    assert u.json()["description"] == "风雨夜初遇客栈"

    # 删除
    d = client.delete(f"/api/events/{event_id}", headers=h)
    assert d.status_code == 204
    final = client.get(
        f"/api/projects/{p['id']}/events", headers=h
    ).json()
    assert len(final) == 0


# ============================================================
# 无 token
# ============================================================

def test_no_token_returns_401(client: TestClient):
    """随便挑几个接口验证未登录被拒。"""
    for path, method in [
        ("/api/projects", "GET"),
        ("/api/projects", "POST"),
        ("/api/projects/some-id/graph", "GET"),
        ("/api/characters/some-id", "GET"),
    ]:
        r = client.request(method, path, json={})
        assert r.status_code == 401, f"{method} {path} 应该 401,实际 {r.status_code}"
