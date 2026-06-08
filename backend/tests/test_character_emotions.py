"""Sprint 6.A2 路线图 #2 二期(2026-05-22)— 跨多次推演的角色情绪总览端到端测试。

测试 GET /api/projects/{project_id}/characters/{character_id}/emotional_states:
  A. 空数据返 [](该角色没参与过任何 emotional_state 落库的 sim)
  B. 跨用户访问 → 404
  C. 角色 ID 跨项目 → 404(防"看到别项目同名角色的数据")
  D. 多 sim 按 created_at DESC + 内部 scene_index ASC + records 字段完整
"""
from __future__ import annotations

import json
import time
import uuid

from fastapi.testclient import TestClient

from app.db import get_connection
from app.services.simulation_service import reshape_to_rounds


# ============================================================
# helpers(对齐 test_simulations.py 的 _create_project_with_chars 等模式)
# ============================================================

def _create_project_with_chars(client, headers, project_name, chars):
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


def _preload_minimal_simulation_llm(ctrl, char_ids, reshape_percent=10):
    first_id = next(iter(char_ids.values()))
    rounds = reshape_to_rounds(reshape_percent)
    for r in range(1, rounds + 1):
        ctrl.json_queue.append({
            "present_agents": list(char_ids.values()),
            "speaking_agents": [first_id],
            "location": f"客栈(第 {r} 轮)",
            "time_advance": "片刻后",
            "round_seed": f"第 {r} 轮契机",
            "narrator_note": "灯火摇曳",
        })
        ctrl.json_queue.append({
            "monologue": "心下一沉",
            "action": "缓缓抬眼",
            "dialogue": f"第 {r} 轮的对白。",
        })
    ctrl.text_queue.append("# 测试 narrative\n\n夜色深沉……")


def _create_sim(client, headers, project_id, divergence="如果情绪总览跨 sim 能正确聚合"):
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=headers,
        json={"divergence": divergence, "reshape_percent": 10},
    )
    assert r.status_code == 201, r.text
    return r.json()["simulation_id"]


def _seed_emotion(sim_id, character_id, scene_index, emotion: dict, rationale="r"):
    """直接 INSERT 一行 character_emotional_states(模拟 evolution mode 已跑过)"""
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO character_emotional_states
               (id, simulation_id, character_id, scene_index,
                emotion_json, rationale, created_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
            (
                f"es-{uuid.uuid4().hex[:8]}",
                sim_id, character_id, scene_index,
                json.dumps(emotion), rationale,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _full_emotion(joy=5, sadness=5, anger=5, fear=5,
                  surprise=5, disgust=5, trust=5, anticipation=5):
    return {
        "joy": joy, "sadness": sadness, "anger": anger, "fear": fear,
        "surprise": surprise, "disgust": disgust, "trust": trust,
        "anticipation": anticipation,
    }


# ============================================================
# A. 空数据返 []
# ============================================================

def test_character_emotions_empty_returns_empty_list(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """该角色没参与过任何 evolution sim → []"""
    user = make_user("emo-overview-empty")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "空情绪项目",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )

    r = client.get(
        f"/api/projects/{project_id}/characters/{char_ids['李寻欢']}/emotional_states",
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json() == []


# ============================================================
# B. 跨用户 404
# ============================================================

def test_character_emotions_cross_user_returns_404(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    alice = make_user("emo-overview-alice")
    bob = make_user("emo-overview-bob")
    project_id, char_ids = _create_project_with_chars(
        client, alice["headers"], "alice 项目",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )

    r = client.get(
        f"/api/projects/{project_id}/characters/{char_ids['李寻欢']}/emotional_states",
        headers=bob["headers"],
    )
    assert r.status_code == 404


# ============================================================
# C. 角色不在该项目 → 404(防泄露)
# ============================================================

def test_character_emotions_character_from_other_project_404(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """alice 有 2 个项目;用 project_A 的 URL + project_B 的 character_id → 404"""
    user = make_user("emo-overview-cross-project")
    h = user["headers"]
    proj_a, _ = _create_project_with_chars(
        client, h, "项目 A",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    proj_b, chars_b = _create_project_with_chars(
        client, h, "项目 B",
        chars=[{"name": "周伯通"}, {"name": "黄药师"}, {"name": "洪七公"}],
    )
    # 用 proj_a 的 url + proj_b 的角色 id → 应该 404
    r = client.get(
        f"/api/projects/{proj_a}/characters/{chars_b['周伯通']}/emotional_states",
        headers=h,
    )
    assert r.status_code == 404


# ============================================================
# D. 多 sim 按 created_at DESC + 内部 scene_index ASC
# ============================================================

def test_character_emotions_multi_sim_sorted(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """种 2 sim × 1 角色 × 各 3 幕情绪 → 返 2 group,顺序正确"""
    user = make_user("emo-overview-multi")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "多 sim 项目",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    li_id = char_ids["李寻欢"]

    # 造 2 个 sim(先后顺序)
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    sim_a = _create_sim(client, h, project_id, "如果这是第一个推演,情绪种 3 幕")
    # 强制 created_at 跨秒(SQLite datetime('now') 精度是秒),让 ORDER BY DESC 行为确定
    time.sleep(1.1)
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    sim_b = _create_sim(client, h, project_id, "如果这是第二个推演,情绪种 2 幕")

    # 给 sim_a 种 李寻欢 3 幕情绪;给 sim_b 种 2 幕
    _seed_emotion(sim_a, li_id, 0, _full_emotion(joy=7), "sim_a 第 1 幕")
    _seed_emotion(sim_a, li_id, 1, _full_emotion(joy=4, sadness=6), "sim_a 第 2 幕")
    _seed_emotion(sim_a, li_id, 2, _full_emotion(joy=2, sadness=8), "sim_a 第 3 幕")
    _seed_emotion(sim_b, li_id, 0, _full_emotion(joy=9, trust=8), "sim_b 第 1 幕")
    _seed_emotion(sim_b, li_id, 1, _full_emotion(joy=6, trust=7), "sim_b 第 2 幕")

    r = client.get(
        f"/api/projects/{project_id}/characters/{li_id}/emotional_states",
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body) == 2

    # sim_b 后创建 → 应排在前(created_at DESC)
    assert body[0]["sim_id"] == sim_b
    assert body[1]["sim_id"] == sim_a

    # 每个 group 的 records 按 scene_index ASC
    assert [r["scene_index"] for r in body[0]["records"]] == [0, 1]
    assert [r["scene_index"] for r in body[1]["records"]] == [0, 1, 2]

    # group 字段完整
    g = body[0]
    assert "sim_id" in g and "sim_state" in g and "sim_created_at" in g
    assert "sim_divergence" in g and "records" in g
    assert g["sim_divergence"] == "如果这是第二个推演,情绪种 2 幕"

    # records 字段完整
    rec = g["records"][0]
    assert set(rec.keys()) == {
        "character_id", "character_name", "scene_index", "emotion", "rationale",
    }
    assert rec["character_id"] == li_id
    assert rec["character_name"] == "李寻欢"
    assert set(rec["emotion"].keys()) == {
        "joy", "sadness", "anger", "fear",
        "surprise", "disgust", "trust", "anticipation",
    }
    assert rec["emotion"]["joy"] == 9
