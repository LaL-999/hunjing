"""Sprint 6.A2 M8.C(2026-05-21)— 项目场景管理面板测试。

覆盖:
  A. GET /projects/{pid}/scenes 透出 origin_simulation_id
  B. PATCH 编辑 name/description/aliases
  C. PATCH name 撞已有 → 409
  D. DELETE 物理删除
  E. POST /scenes/merge:source 数据合并到 target,source 删
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from app.db import _connect, execute as db_execute, fetch_one, transaction
from app.services.project_service import iso_now


def _conn():
    return _connect(Path(os.environ["HUIMENG_DB_PATH"]))


def _setup_project_with_scenes(client, make_user) -> tuple[dict, str, list[str]]:
    """造 project + 3 个场景(2 个原作 / 1 个续作生成)。返回 (h, project_id, scene_ids)。"""
    user = make_user("scene_user")
    h = user["headers"]
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "M8.C 测试", "type": "novel", "mode": "initial"},
    ).json()
    project_id = p["id"]
    # 造 fake sim 给 origin_simulation_id 引用
    fake_sim_id = uuid.uuid4().hex
    conn = _conn()
    scene_ids = []
    try:
        with transaction(conn) as tx:
            # 造 fake sim
            db_execute(
                tx,
                "INSERT INTO simulations (id, project_id, user_id, divergence, "
                " reshape_percent, rounds_planned, target_chars, style, "
                " context_simulation_ids, characters_snapshot, state, current_round, "
                " narrative, created_at, tokens_input, tokens_output, cost_yuan) "
                "VALUES (?, ?, ?, 'x', 10, 5, 4000, 'auto', '[]', '[]', 'done', 5, NULL, ?, 0, 0, 0)",
                (fake_sim_id, project_id, user["user_id"], iso_now()),
            )
            # 2 个原作场景 (origin_simulation_id = NULL)
            for name in ["教室", "天台"]:
                sid = uuid.uuid4().hex
                scene_ids.append(sid)
                db_execute(
                    tx,
                    "INSERT INTO project_scenes "
                    "(id, project_id, name, aliases_json, description, "
                    " appearance_chunk_count, created_at, updated_at, origin_simulation_id) "
                    "VALUES (?, ?, ?, '[]', '', 5, ?, ?, NULL)",
                    (sid, project_id, name, iso_now(), iso_now()),
                )
            # 1 个续作生成场景 (origin_simulation_id = fake_sim_id)
            sid = uuid.uuid4().hex
            scene_ids.append(sid)
            db_execute(
                tx,
                "INSERT INTO project_scenes "
                "(id, project_id, name, aliases_json, description, "
                " appearance_chunk_count, created_at, updated_at, origin_simulation_id) "
                "VALUES (?, ?, '地下室', '[]', '续作首次登场', 0, ?, ?, ?)",
                (sid, project_id, iso_now(), iso_now(), fake_sim_id),
            )
    finally:
        conn.close()
    return h, project_id, scene_ids


# ============================================================
# A. GET 列表透出 origin_simulation_id
# ============================================================

def test_list_scenes_includes_origin_simulation_id(client, make_user):
    h, project_id, scene_ids = _setup_project_with_scenes(client, make_user)
    r = client.get(f"/api/projects/{project_id}/scenes", headers=h)
    assert r.status_code == 200, r.text
    scenes = r.json()
    assert len(scenes) == 3
    # 验证 origin_simulation_id 字段存在
    by_name = {s["name"]: s for s in scenes}
    assert by_name["教室"]["origin_simulation_id"] is None
    assert by_name["天台"]["origin_simulation_id"] is None
    assert by_name["地下室"]["origin_simulation_id"] is not None


# ============================================================
# B. PATCH 编辑
# ============================================================

def test_patch_scene_updates_fields(client, make_user):
    h, project_id, scene_ids = _setup_project_with_scenes(client, make_user)
    r = client.patch(
        f"/api/projects/{project_id}/scenes/{scene_ids[0]}",
        headers=h,
        json={
            "name": "高二三班教室",
            "description": "故事主要场所",
            "aliases": ["教室", "三班"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "高二三班教室"
    assert body["description"] == "故事主要场所"
    assert "教室" in body["aliases"]


def test_patch_scene_name_conflict_409(client, make_user):
    """rename 撞项目内已有的同名场景 → 409。"""
    h, project_id, scene_ids = _setup_project_with_scenes(client, make_user)
    # 把 "天台" 试改成 "教室"(已存在)
    r = client.patch(
        f"/api/projects/{project_id}/scenes/{scene_ids[1]}",
        headers=h,
        json={"name": "教室"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SCENE_NAME_CONFLICT"


# ============================================================
# C. DELETE
# ============================================================

def test_delete_scene_removes_row(client, make_user):
    h, project_id, scene_ids = _setup_project_with_scenes(client, make_user)
    r = client.delete(
        f"/api/projects/{project_id}/scenes/{scene_ids[2]}",
        headers=h,
    )
    assert r.status_code == 204
    # 验证从 list 消失
    r2 = client.get(f"/api/projects/{project_id}/scenes", headers=h)
    names = [s["name"] for s in r2.json()]
    assert "地下室" not in names
    assert len(names) == 2


# ============================================================
# D. POST /scenes/merge
# ============================================================

def test_merge_scenes_combines_data(client, make_user):
    h, project_id, scene_ids = _setup_project_with_scenes(client, make_user)
    # 把"地下室"合并到"教室"(target)
    r = client.post(
        f"/api/projects/{project_id}/scenes/merge",
        headers=h,
        json={
            "source_scene_id": scene_ids[2],     # 地下室
            "target_scene_id": scene_ids[0],     # 教室
        },
    )
    assert r.status_code == 200, r.text
    target = r.json()
    # target name 不变
    assert target["name"] == "教室"
    # source name + source.aliases 应该被并到 target.aliases
    assert "地下室" in target["aliases"]
    # 出现次数应相加(教室原 5 + 地下室 0 = 5)
    assert target["appearance_chunk_count"] == 5
    # source 应该被删
    r_list = client.get(f"/api/projects/{project_id}/scenes", headers=h)
    names = [s["name"] for s in r_list.json()]
    assert "地下室" not in names
    assert len(names) == 2


def test_merge_same_id_rejects(client, make_user):
    h, project_id, scene_ids = _setup_project_with_scenes(client, make_user)
    r = client.post(
        f"/api/projects/{project_id}/scenes/merge",
        headers=h,
        json={"source_scene_id": scene_ids[0], "target_scene_id": scene_ids[0]},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "SCENE_MERGE_INVALID"
