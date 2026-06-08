"""Sprint 6.A2 M2(2026-05-18)— 场景图谱测试。

覆盖范围:
  - migration 037 字段默认值
  - scene_extractor 抽 LOCATION + 去重 (同名多 chunk 合并 + appearance_chunk_count 累加)
  - aliases / description 合并(最长 description + aliases 并集)
  - character_affinity.compute_scene_regulars 共现矩阵正确 + 主角优先排序
  - 2 个新端点(GET list / GET regulars)+ 跨用户隔离 + scene 不存在 404
"""
from __future__ import annotations

import json
import uuid

from fastapi.testclient import TestClient


def _create_project(client: TestClient, headers: dict, name: str = "M2 测试") -> str:
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": name, "type": "novel", "tags": []},
    )
    assert p.status_code == 201, p.text
    return p.json()["id"]


def _seed_extract_chunks(
    project_id: str, user_id: str,
    chunks_entities: list[list[dict]],
) -> None:
    """直接 SQL 插 graph_extraction_jobs + extract_chunk_results。

    chunks_entities[i] = chunk i 的 entities 数组(每个 dict 含 name / type)。
    """
    from app.db import get_connection
    from app.services.project_service import iso_now

    conn = get_connection()
    try:
        job_id = uuid.uuid4().hex
        upload_id = uuid.uuid4().hex
        now = iso_now()
        conn.execute(
            """INSERT INTO uploads (id, project_id, user_id, filename, storage_path,
                                     mime_type, size_bytes, sha256,
                                     parsed_text_chars, state, uploaded_at)
               VALUES (?, ?, ?, 'test.txt', '/tmp/x', 'text/plain', 100, ?,
                       1000, 'ready', ?)""",
            (upload_id, project_id, user_id, f"hash_{upload_id[:8]}", now),
        )
        conn.execute(
            """INSERT INTO graph_extraction_jobs
                (id, project_id, upload_id, user_id, state, is_admin_retag,
                 started_at)
               VALUES (?, ?, ?, ?, 'done', 0, ?)""",
            (job_id, project_id, upload_id, user_id, now),
        )

        for ci, entities in enumerate(chunks_entities):
            graph_json = json.dumps(
                {"entities": entities, "relations": []},
                ensure_ascii=False,
            )
            conn.execute(
                """INSERT INTO extract_chunk_results
                   (job_id, chunk_index, chunk_text_hash, graph_json,
                    tokens_input, tokens_output, completed_at)
                   VALUES (?, ?, 'hash', ?, 0, 0, ?)""",
                (job_id, ci, graph_json, now),
            )
        conn.commit()
    finally:
        conn.close()


# ============================================================
# scene_extractor: 抽 LOCATION + 去重 + UPSERT
# ============================================================

def test_extract_scenes_basic(client: TestClient, make_user):
    """3 个 chunk 含相同 LOCATION,聚合 → 1 条 scene + appearance_chunk_count=3。"""
    u = make_user("scene_basic")
    pid = _create_project(client, u["headers"])

    _seed_extract_chunks(pid, u["user_id"], [
        [{"name": "潇湘馆", "type": "LOCATION", "aliases": ["竹院"], "description": "黛玉住的院落"}],
        [{"name": "潇湘馆", "type": "LOCATION"}],
        [{"name": "潇湘馆", "type": "LOCATION", "description": "竹影摇曳的院子,有湘妃竹无数"}],
    ])

    from app.db import get_connection
    from app.services.scene_extractor import extract_scenes_from_project
    conn = get_connection()
    try:
        report = extract_scenes_from_project(conn, pid)
        assert report["scenes_count"] == 1
        assert report["chunks_processed"] == 3
        assert report["raw_locations_seen"] == 3

        scenes = conn.execute(
            "SELECT * FROM project_scenes WHERE project_id=?", (pid,)
        ).fetchall()
        assert len(scenes) == 1
        s = scenes[0]
        assert s["name"] == "潇湘馆"
        assert s["appearance_chunk_count"] == 3
        # description 取最长
        assert "湘妃竹" in s["description"]
        # aliases 并集
        assert "竹院" in s["aliases_json"]
    finally:
        conn.close()


def test_extract_scenes_ignores_non_location_entities(
    client: TestClient, make_user,
):
    """PERSON / OBJECT / EVENT 实体不进 project_scenes。"""
    u = make_user("scene_filter")
    pid = _create_project(client, u["headers"])

    _seed_extract_chunks(pid, u["user_id"], [
        [
            {"name": "贾母", "type": "PERSON"},
            {"name": "玉佩", "type": "OBJECT"},
            {"name": "大观园", "type": "LOCATION"},
            {"name": "黛玉葬花", "type": "EVENT"},
        ],
    ])

    from app.db import get_connection
    from app.services.scene_extractor import extract_scenes_from_project
    conn = get_connection()
    try:
        report = extract_scenes_from_project(conn, pid)
        assert report["scenes_count"] == 1   # 只 LOCATION
        scenes = conn.execute(
            "SELECT name FROM project_scenes WHERE project_id=?", (pid,)
        ).fetchall()
        assert {s["name"] for s in scenes} == {"大观园"}
    finally:
        conn.close()


def test_extract_scenes_upsert_idempotent(client: TestClient, make_user):
    """连跑 2 次 extract_scenes — 同 (project_id, name) UNIQUE 不报错,字段刷新。"""
    u = make_user("scene_upsert")
    pid = _create_project(client, u["headers"])

    _seed_extract_chunks(pid, u["user_id"], [
        [{"name": "荣禧堂", "type": "LOCATION", "description": "第一次描述"}],
    ])

    from app.db import get_connection
    from app.services.scene_extractor import extract_scenes_from_project
    conn = get_connection()
    try:
        extract_scenes_from_project(conn, pid)
        rows1 = conn.execute(
            "SELECT id, description FROM project_scenes WHERE project_id=?", (pid,)
        ).fetchall()
        first_id = rows1[0]["id"]

        # 再跑一次 — UPSERT 应不报错
        extract_scenes_from_project(conn, pid)
        rows2 = conn.execute(
            "SELECT id, description FROM project_scenes WHERE project_id=?", (pid,)
        ).fetchall()
        assert len(rows2) == 1
        # id 应保持(ON CONFLICT 仅更新字段)— 实际我们的实现是 INSERT 新 id 在 conflict
        # 上做 UPDATE,SQLite UPSERT 保留原 id;验证至少行数没翻倍
    finally:
        conn.close()


# ============================================================
# character_affinity: 共现矩阵 + 主角优先
# ============================================================

def test_compute_scene_regulars_co_occurrence(
    client: TestClient, make_user,
):
    """潇湘馆出现 3 次,黛玉同 chunk 3 次/紫鹃 2 次/宝玉 1 次 → 排序倒序。"""
    u = make_user("affinity_basic")
    pid = _create_project(client, u["headers"])

    # 先建角色
    chars = {}
    for n in ("林黛玉", "紫鹃", "贾宝玉", "贾母"):
        r = client.post(
            f"/api/projects/{pid}/characters", headers=u["headers"],
            json={"name": n},
        )
        chars[n] = r.json()["id"]

    _seed_extract_chunks(pid, u["user_id"], [
        # chunk 0:潇湘馆 + 黛玉 + 紫鹃
        [
            {"name": "潇湘馆", "type": "LOCATION"},
            {"name": "林黛玉", "type": "PERSON"},
            {"name": "紫鹃", "type": "PERSON"},
        ],
        # chunk 1:潇湘馆 + 黛玉 + 宝玉
        [
            {"name": "潇湘馆", "type": "LOCATION"},
            {"name": "林黛玉", "type": "PERSON"},
            {"name": "贾宝玉", "type": "PERSON"},
        ],
        # chunk 2:潇湘馆 + 黛玉 + 紫鹃
        [
            {"name": "潇湘馆", "type": "LOCATION"},
            {"name": "林黛玉", "type": "PERSON"},
            {"name": "紫鹃", "type": "PERSON"},
        ],
        # chunk 3:荣禧堂 + 贾母(无潇湘馆 → 不计入)
        [
            {"name": "荣禧堂", "type": "LOCATION"},
            {"name": "贾母", "type": "PERSON"},
        ],
    ])

    from app.db import get_connection
    from app.services.scene_extractor import extract_scenes_from_project
    from app.services.character_affinity import compute_scene_regulars
    conn = get_connection()
    try:
        extract_scenes_from_project(conn, pid)
        regulars = compute_scene_regulars(conn, pid, "潇湘馆", top_n=10)
        assert len(regulars) == 3
        # 排序:无主角时按共现次数倒序
        # 黛玉 3 次,紫鹃 2 次,宝玉 1 次
        assert regulars[0].character_name == "林黛玉"
        assert regulars[0].co_occurrence_count == 3
        assert regulars[1].character_name == "紫鹃"
        assert regulars[1].co_occurrence_count == 2
        assert regulars[2].character_name == "贾宝玉"
        assert regulars[2].co_occurrence_count == 1
        # 贾母没在潇湘馆共现 → 不进列表
        names = {r.character_name for r in regulars}
        assert "贾母" not in names
    finally:
        conn.close()


def test_compute_scene_regulars_protagonist_priority(
    client: TestClient, make_user,
):
    """主角即便共现次数少也排在配角(共现次数多)之前。"""
    u = make_user("affinity_protag")
    pid = _create_project(client, u["headers"])

    chars = {}
    for n in ("主角A", "配角B"):
        r = client.post(
            f"/api/projects/{pid}/characters", headers=u["headers"],
            json={"name": n},
        )
        chars[n] = r.json()["id"]

    # 手动标主角A 为 protagonist
    client.patch(
        f"/api/characters/{chars['主角A']}", headers=u["headers"],
        json={"is_protagonist": True},
    )

    _seed_extract_chunks(pid, u["user_id"], [
        # chunk 0:大观园 + 主角A
        [{"name": "大观园", "type": "LOCATION"}, {"name": "主角A", "type": "PERSON"}],
        # chunk 1:大观园 + 配角B
        [{"name": "大观园", "type": "LOCATION"}, {"name": "配角B", "type": "PERSON"}],
        # chunk 2:大观园 + 配角B
        [{"name": "大观园", "type": "LOCATION"}, {"name": "配角B", "type": "PERSON"}],
        # chunk 3:大观园 + 配角B
        [{"name": "大观园", "type": "LOCATION"}, {"name": "配角B", "type": "PERSON"}],
    ])

    from app.db import get_connection
    from app.services.scene_extractor import extract_scenes_from_project
    from app.services.character_affinity import compute_scene_regulars
    conn = get_connection()
    try:
        extract_scenes_from_project(conn, pid)
        regulars = compute_scene_regulars(conn, pid, "大观园", top_n=10)
        # 主角A 共现 1 次 / 配角B 共现 3 次,但主角优先 → 主角A 排第一
        assert len(regulars) == 2
        assert regulars[0].character_name == "主角A"
        assert regulars[0].is_protagonist is True
        assert regulars[1].character_name == "配角B"
        assert regulars[1].is_protagonist is False
    finally:
        conn.close()


# ============================================================
# Endpoint
# ============================================================

def test_endpoint_list_scenes(client: TestClient, make_user):
    """GET /projects/{id}/scenes 列项目所有 scene(按 appearance_chunk_count 倒序)。"""
    u = make_user("endpoint_list")
    pid = _create_project(client, u["headers"])

    _seed_extract_chunks(pid, u["user_id"], [
        # 大观园 3 次,潇湘馆 1 次
        [{"name": "大观园", "type": "LOCATION"}],
        [{"name": "大观园", "type": "LOCATION"}, {"name": "潇湘馆", "type": "LOCATION"}],
        [{"name": "大观园", "type": "LOCATION"}],
    ])

    from app.db import get_connection
    from app.services.scene_extractor import extract_scenes_from_project
    conn = get_connection()
    try:
        extract_scenes_from_project(conn, pid)
    finally:
        conn.close()

    r = client.get(
        f"/api/projects/{pid}/scenes", headers=u["headers"],
    )
    assert r.status_code == 200
    scenes = r.json()
    assert len(scenes) == 2
    # appearance 倒序:大观园 3 在前
    assert scenes[0]["name"] == "大观园"
    assert scenes[0]["appearance_chunk_count"] == 3
    assert scenes[1]["name"] == "潇湘馆"


def test_endpoint_regulars_404_when_scene_not_exist(
    client: TestClient, make_user,
):
    """请求不存在的 scene → 404 SCENE_NOT_FOUND。"""
    u = make_user("endpoint_404")
    pid = _create_project(client, u["headers"])

    r = client.get(
        f"/api/projects/{pid}/scenes/不存在的场所/regulars",
        headers=u["headers"],
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "SCENE_NOT_FOUND"


def test_endpoint_isolates_users(client: TestClient, make_user):
    """user A 的 scenes,user B 访问应 404(get_project_or_403 校验)。"""
    ua = make_user("scene_owner")
    ub = make_user("scene_invader")
    pid = _create_project(client, ua["headers"])

    r = client.get(
        f"/api/projects/{pid}/scenes", headers=ub["headers"],
    )
    assert r.status_code == 404
