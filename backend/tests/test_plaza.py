"""作品广场端到端冒烟测试 — 2026-06-25。

直接插一个 done 的 simulation(免去跑真推演),走真 HTTP 全链路:
  publishable → publish → list(hot/new/classic) → read(+阅读) → like 幂等 →
  跨用户 liked 隔离 → 下架后不可见 → 资料改昵称。
"""
from __future__ import annotations

import uuid

from app.db import get_connection
from app.services.project_service import iso_now


def _seed_done_sim(user_id: str, mode: str = "middle", name: str = "原著·测试卷") -> str:
    """直接插一个 project + 一个 done simulation(带正文),返回 sim_id。"""
    conn = get_connection()
    try:
        now = iso_now()
        project_id = str(uuid.uuid4())
        sim_id = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO projects (id, user_id, name, type, tags, mode, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (project_id, user_id, name, "novel", "[]", mode, now, now),
        )
        conn.execute(
            """INSERT INTO simulations
                 (id, project_id, user_id, divergence, characters_snapshot,
                  state, narrative, narrative_summary, created_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                sim_id, project_id, user_id, "如果主角没有离开",
                "[]", "done",
                "# 第一章\n\n这是一段被推演出来的精彩正文,长度足够上架。" * 5,
                "一段摘要。", now,
            ),
        )
        conn.commit()
        return sim_id
    finally:
        conn.close()


def test_plaza_full_flow(client, make_user):
    author = make_user("author")
    sim_id = _seed_done_sim(author["user_id"])

    # 1. 可上架素材里能看到这个 sim
    r = client.get("/api/plaza/publishable", headers=author["headers"])
    assert r.status_code == 200, r.text
    pub_ids = [it["sim_id"] for it in r.json()["items"]]
    assert sim_id in pub_ids
    # 非初始态派生原著名
    item = next(it for it in r.json()["items"] if it["sim_id"] == sim_id)
    assert item["original_title"] == "原著·测试卷"

    # 2. 发布
    r = client.post(
        "/api/plaza/publish",
        headers=author["headers"],
        json={"title": "我的第一部上架作品", "sim_id": sim_id, "cover_gradient": 3},
    )
    assert r.status_code == 201, r.text
    work = r.json()
    work_id = work["id"]
    assert work["title"] == "我的第一部上架作品"
    assert work["original_title"] == "原著·测试卷"
    assert work["mode"] == "middle"
    assert work["cover_gradient"] == 3
    assert work["like_count"] == 0 and work["read_count"] == 0

    # 3. 发布后不再出现在 publishable
    r = client.get("/api/plaza/publishable", headers=author["headers"])
    assert sim_id not in [it["sim_id"] for it in r.json()["items"]]

    # 4. 广场三种排序都能列出
    for sort in ("hot", "new", "classic"):
        r = client.get(f"/api/plaza/works?sort={sort}", headers=author["headers"])
        assert r.status_code == 200, r.text
        ids = [c["id"] for c in r.json()["items"]]
        assert work_id in ids, f"{sort} 排序未列出作品"

    # 5a. 详情落地页:元信息 + 预览,不 +阅读量、不返全文(v5)
    r = client.get(f"/api/plaza/works/{work_id}", headers=author["headers"])
    assert r.status_code == 200, r.text
    detail = r.json()
    assert "content" not in detail          # 详情不返全文
    assert detail["read_count"] == 0        # 详情不 +阅读量
    assert detail["is_owner"] is True
    assert detail.get("preview")            # 有预览节选

    # 5b. 在线阅读(/content):返回正文 + 阅读量 +1
    r = client.get(f"/api/plaza/works/{work_id}/content", headers=author["headers"])
    assert r.status_code == 200, r.text
    assert "第一章" in r.json()["content"]
    assert r.json()["read_count"] == 1

    # 6. 点赞幂等
    r = client.post(f"/api/plaza/works/{work_id}/like", headers=author["headers"], json={"liked": True})
    assert r.json() == {"liked": True, "like_count": 1}
    # 再点一次仍为 1(幂等)
    r = client.post(f"/api/plaza/works/{work_id}/like", headers=author["headers"], json={"liked": True})
    assert r.json()["like_count"] == 1
    # 取消
    r = client.post(f"/api/plaza/works/{work_id}/like", headers=author["headers"], json={"liked": False})
    assert r.json() == {"liked": False, "like_count": 0}

    # 7. 另一个用户看不到自己的 liked 标记 + 我的作品列表隔离
    reader = make_user("reader")
    r = client.get("/api/plaza/works?sort=new", headers=reader["headers"])
    card = next(c for c in r.json()["items"] if c["id"] == work_id)
    assert card["liked"] is False
    assert client.get("/api/plaza/my-works", headers=reader["headers"]).json()["items"] == []

    # 8. 下架后广场不可见 + 阅读 404
    r = client.delete(f"/api/plaza/works/{work_id}", headers=author["headers"])
    assert r.status_code == 200, r.text
    r = client.get("/api/plaza/works?sort=new", headers=reader["headers"])
    assert work_id not in [c["id"] for c in r.json()["items"]]
    r = client.get(f"/api/plaza/works/{work_id}", headers=reader["headers"])
    assert r.status_code == 404


def test_publish_rejects_foreign_sim(client, make_user):
    """不能上架别人的 simulation。"""
    owner = make_user("owner")
    sim_id = _seed_done_sim(owner["user_id"])
    attacker = make_user("attacker")
    r = client.post(
        "/api/plaza/publish",
        headers=attacker["headers"],
        json={"title": "盗发", "sim_id": sim_id},
    )
    assert r.status_code == 404, r.text


def test_profile_update(client, make_user):
    u = make_user("profileuser")
    r = client.get("/api/me/profile", headers=u["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["nickname"] is None

    r = client.patch("/api/me/profile", headers=u["headers"], json={"nickname": "晨星"})
    assert r.status_code == 200, r.text
    assert r.json()["nickname"] == "晨星"

    # 再 GET 持久化
    assert client.get("/api/me/profile", headers=u["headers"]).json()["nickname"] == "晨星"


def test_comments_flow(client, make_user):
    """评论区:发 → 列 → 他人可见 → 评论人删自己 / 作品作者删他人评论。"""
    author = make_user("cmt_author")
    reader = make_user("cmt_reader")
    sim_id = _seed_done_sim(author["user_id"], name="评论测试卷")
    work_id = client.post(
        "/api/plaza/publish", headers=author["headers"],
        json={"title": "评论测试", "sim_id": sim_id},
    ).json()["id"]

    # reader 发评论
    r = client.post(f"/api/plaza/works/{work_id}/comments",
                    headers=reader["headers"], json={"content": "写得真好!"})
    assert r.status_code == 201, r.text
    cid_reader = r.json()["id"]
    assert r.json()["is_mine"] is True
    assert r.json()["content"] == "写得真好!"

    # 空评论被拒
    assert client.post(f"/api/plaza/works/{work_id}/comments",
                       headers=reader["headers"], json={"content": "   "}).status_code == 400

    # author 也发一条
    cid_author = client.post(f"/api/plaza/works/{work_id}/comments",
                             headers=author["headers"], json={"content": "谢谢支持"}).json()["id"]

    # 列表:2 条,新→旧,author 视角 is_mine 正确
    lst = client.get(f"/api/plaza/works/{work_id}/comments", headers=author["headers"]).json()["items"]
    assert len(lst) == 2
    mine = {c["id"]: c["is_mine"] for c in lst}
    assert mine[cid_author] is True and mine[cid_reader] is False

    # reader 删不了别人的(author 的)评论
    assert client.delete(f"/api/plaza/comments/{cid_author}", headers=reader["headers"]).status_code == 404
    # 但作品作者能删他人(reader 的)评论
    assert client.delete(f"/api/plaza/comments/{cid_reader}", headers=author["headers"]).status_code == 200
    # 评论人删自己的
    assert client.delete(f"/api/plaza/comments/{cid_author}", headers=author["headers"]).status_code == 200

    assert len(client.get(f"/api/plaza/works/{work_id}/comments", headers=author["headers"]).json()["items"]) == 0
