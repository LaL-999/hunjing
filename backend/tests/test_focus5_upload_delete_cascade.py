"""Sprint 6.A2 FOCUS.5(2026-05-22):
  ① delete_upload 级联清理(角色 / 关系 / 事件 / 场景 / 项目元数据)
  ② _dedupe_similar_quotes 高相似度去重
"""
from __future__ import annotations

from fastapi.testclient import TestClient


# ============================================================
# A. _dedupe_similar_quotes 单元测试
# ============================================================

def test_dedupe_high_jaccard_keeps_first():
    from app.services.extract_service import _dedupe_similar_quotes
    quotes = [
        "我嘛,是学地、地、地图的。",
        "嗯。大学毕业,去国土地理院,绘地、地、地图。",
        "我不明白,我、我嘛,因为喜欢地、地、地图,才地、地、地图的。",
    ]
    result = _dedupe_similar_quotes(quotes, substring_min_len=6)
    # 三句字符集高度重叠("地图""我""嘛"等),理应只剩 1-2 句
    assert len(result) < len(quotes)
    # 第一句必保留
    assert result[0] == quotes[0]


def test_dedupe_distinct_quotes_all_kept():
    from app.services.extract_service import _dedupe_similar_quotes
    quotes = [
        "你回去吧。",
        "我等你。",
        "永远不会忘记。",
    ]
    # 字符集几乎无重叠 → 全部保留
    result = _dedupe_similar_quotes(quotes, substring_min_len=6)
    assert len(result) == 3


def test_dedupe_empty_strings_skipped():
    from app.services.extract_service import _dedupe_similar_quotes
    result = _dedupe_similar_quotes(["", "  ", "实际台词"], substring_min_len=6)
    assert result == ["实际台词"]


def test_dedupe_empty_list():
    from app.services.extract_service import _dedupe_similar_quotes
    assert _dedupe_similar_quotes([], substring_min_len=6) == []


# ============================================================
# B. delete_upload 级联清理 — 集成测试
# ============================================================

def test_delete_upload_clears_characters_and_events_and_scenes(
    client: TestClient, make_user
):
    """删除 upload 后,characters / events / project_scenes 应全清。"""
    user = make_user("hero")
    h = user["headers"]
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "测试", "type": "novel", "tags": []},
    ).json()
    project_id = p["id"]

    # 手动创建角色 / 事件 / 场景(模拟抽取后的状态)
    c1 = client.post(
        f"/api/projects/{project_id}/characters", headers=h,
        json={"name": "甲"},
    ).json()
    c2 = client.post(
        f"/api/projects/{project_id}/characters", headers=h,
        json={"name": "乙"},
    ).json()
    client.post(
        f"/api/projects/{project_id}/events", headers=h,
        json={"description": "三人聚会", "participants": [c1["id"], c2["id"]]},
    )
    client.post(
        f"/api/projects/{project_id}/scenes", headers=h,
        json={"name": "破庙", "description": "三人投宿"},
    )

    # 上传作品文件
    files = {"file": ("test.txt", b"\xe6\xb5\x8b\xe8\xaf\x95\xe6\x96\x87\xe6\x9c\xac" * 50, "text/plain")}
    upload_resp = client.post(
        f"/api/projects/{project_id}/uploads", headers=h, files=files,
    )
    assert upload_resp.status_code == 201, upload_resp.text
    upload = upload_resp.json()
    upload_id = upload["id"]

    # 删除 upload
    del_resp = client.delete(f"/api/uploads/{upload_id}", headers=h)
    assert del_resp.status_code == 204

    # 验证级联清理
    chars = client.get(f"/api/projects/{project_id}/characters", headers=h).json()
    assert len(chars) == 0
    events = client.get(f"/api/projects/{project_id}/events", headers=h).json()
    assert len(events) == 0
    scenes_resp = client.get(f"/api/projects/{project_id}/scenes", headers=h)
    assert scenes_resp.status_code == 200
    assert len(scenes_resp.json()) == 0


def test_delete_upload_resets_project_meta(client: TestClient, make_user):
    """删除 upload 后,project.tags / world_baseline / narrative_pov 应重置;type 保留。"""
    user = make_user("hero")
    h = user["headers"]
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "测试", "type": "novel", "tags": ["原始标签"]},
    ).json()
    project_id = p["id"]

    # 手动 PATCH 给项目填上 narrative_pov + tags(模拟 extract 写回的状态)
    client.patch(
        f"/api/projects/{project_id}", headers=h,
        json={"narrative_pov": "first", "tags": ["LLM 抽出来的标签"]},
    )

    # 上传 + 删除
    files = {"file": ("test.txt", b"\xe6\xb5\x8b\xe8\xaf\x95" * 20, "text/plain")}
    upload = client.post(
        f"/api/projects/{project_id}/uploads", headers=h, files=files,
    ).json()
    client.delete(f"/api/uploads/{upload['id']}", headers=h)

    # 项目元数据应重置
    proj = client.get(f"/api/projects/{project_id}", headers=h).json()
    assert proj["tags"] == []
    assert proj["narrative_pov"] is None
    assert proj["world_baseline"] == {} or proj["world_baseline"] is None
    # type / mode / name 保留
    assert proj["type"] == "novel"
    assert proj["name"] == "测试"


def test_delete_upload_preserves_simulations(client: TestClient, make_user):
    """删除 upload 后,simulations 不动(用户辛苦跑的产物;允许孤儿引用)。"""
    user = make_user("hero")
    h = user["headers"]
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "测试", "type": "novel", "tags": []},
    ).json()
    project_id = p["id"]

    # 上传 + 删除(没跑过 simulations,只验证调用不抛 + simulations 接口仍 OK)
    files = {"file": ("test.txt", b"\xe6\xb5\x8b\xe8\xaf\x95" * 20, "text/plain")}
    upload = client.post(
        f"/api/projects/{project_id}/uploads", headers=h, files=files,
    ).json()
    del_resp = client.delete(f"/api/uploads/{upload['id']}", headers=h)
    assert del_resp.status_code == 204

    # simulations 接口仍可访问(即使是空列表)
    sims_resp = client.get(f"/api/projects/{project_id}/simulations", headers=h)
    assert sims_resp.status_code == 200
