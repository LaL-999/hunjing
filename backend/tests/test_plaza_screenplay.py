"""作品广场 · 剧创态发布(v5 item8)测试。

覆盖:
  - list_publishable_screenplays 列出有全局剧本 / 分集方案的 novel
  - publish_screenplay:global / episodes / both 三种 kind
  - both 模式正文含两段落头
  - 跨用户越权发布被拦(SOURCE_NOT_FOUND)
"""
from __future__ import annotations

import json
import uuid

import yaml

from app.services import plaza_service
from app.services.plaza_service import PlazaError, PublishScreenplayInput


_SCREENPLAY_YAML = yaml.safe_dump(
    {
        "meta": {"title": "测试剧本", "source": {"novel_title": "测试原著"}},
        "characters": [{"id": "c1", "name": "阿明", "description": "少年"}],
        "locations": [{"id": "l1", "name": "教室"}],
        "scenes": [
            {
                "scene_id": "S1",
                "heading": "内 教室 日",
                "action": "阿明走进教室。",
                "dialogues": [{"character": "c1", "line": "早上好。"}],
            }
        ],
    },
    allow_unicode=True,
)

_PLAN_JSON = json.dumps(
    {
        "perspectives": [
            {
                "perspective": "rhythm",
                "label": "节奏视角",
                "rationale": "按张力谷切集",
                "episodes": [
                    {"title": "开端", "scene_ids": ["S1"], "est_minutes": 20,
                     "logline": "少年入学", "first_chapter": 1, "last_chapter": 3},
                    {"title": "转折", "scene_ids": ["S1"], "est_minutes": 22,
                     "logline": "冲突爆发", "first_chapter": 4, "last_chapter": 6},
                ],
            }
        ]
    },
    ensure_ascii=False,
)


def _seed_novel(conn, user_id: str) -> str:
    from app.services.project_service import iso_now
    now = iso_now()
    novel_id = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO sp_novels
            (id, user_id, title, source_format, source_filename,
             total_chars, total_chapters, uploaded_at)
           VALUES (?,?,?,?,?,0,0,?)""",
        (novel_id, user_id, "测试小说", "txt", "x.txt", now),
    )
    conn.commit()
    return novel_id


def _seed_screenplay(conn, novel_id: str) -> str:
    from app.services.project_service import iso_now
    sid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sp_screenplays (id, novel_id, yaml_text, created_at) VALUES (?,?,?,?)",
        (sid, novel_id, _SCREENPLAY_YAML, iso_now()),
    )
    conn.commit()
    return sid


def _seed_plan(conn, novel_id: str) -> str:
    from app.services.project_service import iso_now
    now = iso_now()
    pid = uuid.uuid4().hex
    conn.execute(
        """INSERT INTO sp_episode_plans
            (id, novel_id, scheme_name, preset, target_minutes,
             episode_count, scene_count, plan_json, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (pid, novel_id, "默认方案", "custom", 45.0, 2, 1, _PLAN_JSON, now, now),
    )
    conn.commit()
    return pid


def test_list_publishable_screenplays(make_user):
    from app.db import get_connection
    u = make_user("plaza_sp_list")
    conn = get_connection()
    try:
        novel_id = _seed_novel(conn, u["user_id"])
        _seed_screenplay(conn, novel_id)
        _seed_plan(conn, novel_id)

        items = plaza_service.list_publishable_screenplays(conn, u["user_id"])
        assert len(items) == 1
        it = items[0]
        assert it["novel_id"] == novel_id
        assert it["has_global"] is True
        assert len(it["episode_plans"]) == 1
        assert it["episode_plans"][0]["scheme_name"] == "默认方案"
    finally:
        conn.close()


def test_publish_screenplay_global(make_user):
    from app.db import get_connection
    u = make_user("plaza_sp_global")
    conn = get_connection()
    try:
        novel_id = _seed_novel(conn, u["user_id"])
        _seed_screenplay(conn, novel_id)
        work = plaza_service.publish_screenplay(
            conn, u["user_id"],
            PublishScreenplayInput(novel_id=novel_id, kind="global", title="我的剧本"),
        )
        conn.commit()
        full = plaza_service.read_work(conn, work["id"], u["user_id"])
        assert full["content"].strip()
        assert "测试剧本" in full["content"] or "教室" in full["content"]
    finally:
        conn.close()


def test_publish_screenplay_episodes(make_user):
    from app.db import get_connection
    u = make_user("plaza_sp_eps")
    conn = get_connection()
    try:
        novel_id = _seed_novel(conn, u["user_id"])
        plan_id = _seed_plan(conn, novel_id)
        work = plaza_service.publish_screenplay(
            conn, u["user_id"],
            PublishScreenplayInput(novel_id=novel_id, kind="episodes",
                                   plan_id=plan_id, title="我的分集"),
        )
        conn.commit()
        full = plaza_service.read_work(conn, work["id"], u["user_id"])
        assert "第 1 集" in full["content"]
        assert "第 2 集" in full["content"]
    finally:
        conn.close()


def test_publish_screenplay_both_has_two_sections(make_user):
    from app.db import get_connection
    u = make_user("plaza_sp_both")
    conn = get_connection()
    try:
        novel_id = _seed_novel(conn, u["user_id"])
        _seed_screenplay(conn, novel_id)
        plan_id = _seed_plan(conn, novel_id)
        work = plaza_service.publish_screenplay(
            conn, u["user_id"],
            PublishScreenplayInput(novel_id=novel_id, kind="both",
                                   plan_id=plan_id, title="全都要"),
        )
        conn.commit()
        full = plaza_service.read_work(conn, work["id"], u["user_id"])
        assert "# 全局剧本" in full["content"]
        assert "# 分集方案" in full["content"]
        assert full["mode"] == "screenplay"
    finally:
        conn.close()


def test_publish_screenplay_cross_user_blocked(make_user):
    from app.db import get_connection
    owner = make_user("plaza_sp_owner")
    intruder = make_user("plaza_sp_intruder")
    conn = get_connection()
    try:
        novel_id = _seed_novel(conn, owner["user_id"])
        _seed_screenplay(conn, novel_id)
        try:
            plaza_service.publish_screenplay(
                conn, intruder["user_id"],
                PublishScreenplayInput(novel_id=novel_id, kind="global", title="偷发"),
            )
            assert False, "跨用户发布应被拦"
        except PlazaError as e:
            assert e.code == "SOURCE_NOT_FOUND"
    finally:
        conn.close()


def test_publish_screenplay_global_missing_raises(make_user):
    """novel 有但没生成全局剧本 → SOURCE_NOT_DONE。"""
    from app.db import get_connection
    u = make_user("plaza_sp_nodone")
    conn = get_connection()
    try:
        novel_id = _seed_novel(conn, u["user_id"])  # 不 seed screenplay
        try:
            plaza_service.publish_screenplay(
                conn, u["user_id"],
                PublishScreenplayInput(novel_id=novel_id, kind="global", title="空"),
            )
            assert False, "无全局剧本应拦"
        except PlazaError as e:
            assert e.code == "SOURCE_NOT_DONE"
    finally:
        conn.close()
