"""作品广场 · 漫画上架(v5)+ 详情/权限/下载 测试。"""
from __future__ import annotations

import json
import uuid

from app.services import plaza_service
from app.services.plaza_service import PlazaError, PublishComicInput


def _seed_done_comic(conn, user_id: str, pages: int = 3) -> str:
    from app.services.project_service import iso_now
    now = iso_now()
    comic_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO comic_projects
            (id, user_id, name, source_json, state, progress_percent, cost_yuan,
             created_at, updated_at)
           VALUES (?,?,?,?,'done',100,0,?,?)""",
        (comic_id, user_id, "测试漫画", "{}", now, now),
    )
    for i in range(1, pages + 1):
        conn.execute(
            """INSERT INTO comic_pages
                (id, comic_id, page_index, panels_json, state, composed_url,
                 created_at, updated_at)
               VALUES (?,?,?,?,'composed',?,?,?)""",
            (uuid.uuid4().hex, comic_id, i, "[]",
             f"/api/comic-composed/{comic_id}/page_{i}.png", now, now),
        )
    conn.commit()
    return comic_id


def test_list_publishable_comics(make_user):
    from app.db import get_connection
    u = make_user("plaza_comic_list")
    conn = get_connection()
    try:
        cid = _seed_done_comic(conn, u["user_id"], pages=4)
        items = plaza_service.list_publishable_comics(conn, u["user_id"])
        assert len(items) == 1
        assert items[0]["comic_id"] == cid
        assert items[0]["page_count"] == 4
        assert items[0]["cover_url"].endswith("page_1.png")
    finally:
        conn.close()


def test_publish_comic_snapshots_pages(make_user):
    from app.db import get_connection
    u = make_user("plaza_comic_pub")
    conn = get_connection()
    try:
        cid = _seed_done_comic(conn, u["user_id"], pages=3)
        work = plaza_service.publish_comic(
            conn, u["user_id"],
            PublishComicInput(comic_id=cid, title="我的漫画"),
        )
        conn.commit()
        assert work["source_type"] == "comic"
        assert work["mode"] == "cycle"
        assert work["word_count"] == 3   # 复用为页数
        # 详情返回页图数组
        detail = plaza_service.get_work_detail(conn, work["id"], u["user_id"])
        assert len(detail["comic_pages"]) == 3
        assert detail["comic_pages"][0].endswith("page_1.png")
    finally:
        conn.close()


def test_publish_comic_requires_composed_pages(make_user):
    """done 但没有排版整页 → EMPTY_CONTENT。"""
    from app.db import get_connection
    from app.services.project_service import iso_now
    u = make_user("plaza_comic_nopage")
    conn = get_connection()
    try:
        cid = uuid.uuid4().hex
        now = iso_now()
        conn.execute(
            """INSERT INTO comic_projects
                (id, user_id, name, source_json, state, progress_percent, cost_yuan,
                 created_at, updated_at)
               VALUES (?,?,?,?,'done',100,0,?,?)""",
            (cid, u["user_id"], "空漫画", "{}", now, now),
        )
        conn.commit()
        try:
            plaza_service.publish_comic(conn, u["user_id"], PublishComicInput(comic_id=cid, title="空"))
            assert False, "无排版页应拦"
        except PlazaError as e:
            assert e.code == "EMPTY_CONTENT"
    finally:
        conn.close()


def test_private_work_hidden_from_others(make_user):
    """私人作品(is_public=0):他人详情 404,作者自己可见。"""
    from app.db import get_connection
    owner = make_user("plaza_priv_owner")
    other = make_user("plaza_priv_other")
    conn = get_connection()
    try:
        cid = _seed_done_comic(conn, owner["user_id"])
        work = plaza_service.publish_comic(
            conn, owner["user_id"],
            PublishComicInput(comic_id=cid, title="私人漫画", is_public=0),
        )
        conn.commit()
        wid = work["id"]
        # 作者可见
        assert plaza_service.get_work_detail(conn, wid, owner["user_id"])["is_owner"] is True
        # 他人 404
        try:
            plaza_service.get_work_detail(conn, wid, other["user_id"])
            assert False, "私人作品他人应 404"
        except Exception as e:
            assert "published_work" in str(e) or "NotFound" in type(e).__name__
    finally:
        conn.close()


def test_download_permission(make_user):
    """allow_download=0:他人下载被拒;作者本人可下载。"""
    from app.db import get_connection
    owner = make_user("plaza_dl_owner")
    other = make_user("plaza_dl_other")
    conn = get_connection()
    try:
        # 用文本作品测下载(漫画不支持文本下载)
        sim_id = _seed_sim(conn, owner["user_id"])
        work = plaza_service.publish_work(
            conn, owner["user_id"],
            plaza_service.PublishInput(sim_id=sim_id, title="不许下载", allow_download=0),
        )
        conn.commit()
        wid = work["id"]
        # 作者可下载
        assert plaza_service.get_work_for_download(conn, wid, owner["user_id"])["content"]
        # 他人被拒
        try:
            plaza_service.get_work_for_download(conn, wid, other["user_id"])
            assert False, "allow_download=0 他人应被拒"
        except PlazaError as e:
            assert e.code == "DOWNLOAD_FORBIDDEN"
    finally:
        conn.close()


def _seed_sim(conn, user_id: str) -> str:
    from app.services.comic_service import _new_comic_id
    from app.services.project_service import iso_now
    now = iso_now()
    sim_id = _new_comic_id()
    proj_id = _new_comic_id()
    conn.execute(
        """INSERT INTO projects (id, user_id, name, type, custom_type_name, tags, mode,
                                   created_at, updated_at, world_baseline_json, graph_strength_threshold)
           VALUES (?,?,?, 'novel', NULL, '[]', 'initial', ?, ?, '{}', 30)""",
        (proj_id, user_id, "P", now, now),
    )
    conn.execute(
        """INSERT INTO simulations
            (id, project_id, user_id, divergence, reshape_percent, rounds_planned, target_chars,
             style, custom_style_hint, context_simulation_ids, narrative_summary,
             characters_snapshot, state, current_round, timeline_json, narrative,
             tokens_input, tokens_output, cost_yuan, error_message, created_at, started_at, completed_at)
           VALUES (?,?,?, '测试', 50, 10, 4000, 'A', NULL, '[]', NULL, ?, 'done', 10, NULL, ?, 0,0,0.0,NULL,?,?,?)""",
        (sim_id, proj_id, user_id, json.dumps([{"id": "c", "name": "主角"}], ensure_ascii=False),
         "正文内容;" * 30, now, now, now),
    )
    conn.commit()
    return sim_id
