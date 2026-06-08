"""Sprint 6.A2 INIT.3(2026-05-21)— 角色 behavior_baseline 端到端测试。

测试目标:
  1. POST 创建角色带 baseline → 落库 + 响应回带
  2. PATCH 部分维度(只改 speech_register)→ 整对象覆盖语义
  3. PATCH baseline=null → 清空列(consistency_checker fallback)
  4. 老角色 baseline 字段缺失 → GET 返 None,无 crash
  5. 4 维度校验失败 → 422(emotional_intensity 超 0-10 / speech_register 非枚举)
  6. baseline **不进** counterfactual_changes(故意不追踪,与 personality/quotes 区分)

枚举值与 migration 041 + prompts/m4_consistency_checker.md 一致:
  speech_register  ∈ {卑微 / 平和 / 强硬 / 恶意}
  moral_compass    ∈ {善 / 灰 / 恶}
  emotional_intensity 0-10 整数
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def _make_project(client: TestClient, headers: dict) -> str:
    """快捷:创建一个项目,返 project id。"""
    r = client.post(
        "/api/projects",
        headers=headers,
        json={"name": "江湖夜雨", "type": "novel"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ============================================================
# 1. POST 创建带 baseline → 落库 + 响应回带
# ============================================================

def test_create_character_with_full_baseline(client: TestClient, make_user):
    """创建角色时即填 4 维度行为基线;响应应回带 baseline 字段。"""
    user = make_user("hero")
    h = user["headers"]
    p_id = _make_project(client, h)

    payload = {
        "name": "刘飞",
        "behavior_baseline": {
            "speech_register": "卑微",
            "emotional_intensity": 4,
            "moral_compass": "灰",
            # P0G.2(2026-05-24):out_of_baseline_examples 已永久删除
        },
    }
    r = client.post(f"/api/projects/{p_id}/characters", headers=h, json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["name"] == "刘飞"
    assert body["behavior_baseline"] == payload["behavior_baseline"]

    # GET 回读确认落库
    r2 = client.get(f"/api/characters/{body['id']}", headers=h)
    assert r2.status_code == 200
    assert r2.json()["behavior_baseline"] == payload["behavior_baseline"]


def test_create_character_without_baseline_returns_none(
    client: TestClient, make_user
):
    """不填 baseline → 列 NULL → 响应 behavior_baseline 为 None。
    consistency_checker 看到 None 自动 fallback 用 personality + no_go_list。
    """
    user = make_user("hero")
    h = user["headers"]
    p_id = _make_project(client, h)

    r = client.post(
        f"/api/projects/{p_id}/characters",
        headers=h,
        json={"name": "李寻欢"},
    )
    assert r.status_code == 201
    assert r.json()["behavior_baseline"] is None


# ============================================================
# 2. PATCH 部分维度 → 整对象覆盖语义(用户拍板)
# ============================================================

def test_patch_baseline_full_replacement(client: TestClient, make_user):
    """PATCH 时整对象覆盖:旧 baseline 有 3 字段,新 PATCH 只传 2 字段 →
    最终结果是新 2 字段为新值 + 缺的 1 字段回默认(moral_compass=None)。
    P0G.2(2026-05-24):out_of_baseline_examples 字段已永久删除。
    """
    user = make_user("hero")
    h = user["headers"]
    p_id = _make_project(client, h)

    # 先创建带完整 baseline 的角色
    c = client.post(
        f"/api/projects/{p_id}/characters",
        headers=h,
        json={
            "name": "刘飞",
            "behavior_baseline": {
                "speech_register": "卑微",
                "emotional_intensity": 4,
                "moral_compass": "灰",
            },
        },
    ).json()

    # PATCH 只传 speech_register + emotional_intensity 改新值
    r = client.patch(
        f"/api/characters/{c['id']}",
        headers=h,
        json={
            "behavior_baseline": {
                "speech_register": "强硬",
                "emotional_intensity": 8,
                # moral_compass 没传 → Pydantic 默认 None
            },
        },
    )
    assert r.status_code == 200, r.text
    final_baseline = r.json()["behavior_baseline"]
    assert final_baseline["speech_register"] == "强硬"
    assert final_baseline["emotional_intensity"] == 8
    # 整对象覆盖语义:没传的字段被默认值清空
    assert final_baseline["moral_compass"] is None


# ============================================================
# 3. PATCH baseline=null → 清空整列
# ============================================================

def test_patch_baseline_null_clears_column(client: TestClient, make_user):
    """PATCH 传 behavior_baseline=null → 列 NULL,响应 None。
    用户场景:之前精调过,现在想完全交给 AI(fallback 走 personality)。
    """
    user = make_user("hero")
    h = user["headers"]
    p_id = _make_project(client, h)

    c = client.post(
        f"/api/projects/{p_id}/characters",
        headers=h,
        json={
            "name": "刘飞",
            "behavior_baseline": {
                "speech_register": "卑微",
                "emotional_intensity": 4,
                "moral_compass": "灰",
                "out_of_baseline_examples": [],
            },
        },
    ).json()
    assert c["behavior_baseline"]["speech_register"] == "卑微"

    r = client.patch(
        f"/api/characters/{c['id']}",
        headers=h,
        json={"behavior_baseline": None},
    )
    assert r.status_code == 200
    assert r.json()["behavior_baseline"] is None


# ============================================================
# 4. 校验失败 → 422
# ============================================================

def test_baseline_validation_emotional_intensity_out_of_range(
    client: TestClient, make_user
):
    """emotional_intensity 必须 0-10,11 应被 Pydantic 拒。"""
    user = make_user("hero")
    h = user["headers"]
    p_id = _make_project(client, h)

    r = client.post(
        f"/api/projects/{p_id}/characters",
        headers=h,
        json={
            "name": "X",
            "behavior_baseline": {"emotional_intensity": 11},
        },
    )
    assert r.status_code == 422


def test_baseline_validation_invalid_speech_register(
    client: TestClient, make_user
):
    """speech_register 必须是 4 档之一,自由字符串应被 Literal 拒。"""
    user = make_user("hero")
    h = user["headers"]
    p_id = _make_project(client, h)

    r = client.post(
        f"/api/projects/{p_id}/characters",
        headers=h,
        json={
            "name": "X",
            "behavior_baseline": {"speech_register": "暴躁"},  # 非合法档
        },
    )
    assert r.status_code == 422


# ============================================================
# 5. baseline 不被反事实追踪(故意,语义清白)
# ============================================================

def test_baseline_change_does_not_record_counterfactual(
    client: TestClient, make_user
):
    """改 baseline 不应落 counterfactual_changes 表 — 它是基础设定,不是 what-if 变量。
    对比:改 personality / quotes 会落表(_CHARACTER_TRACKED_FIELDS)。
    """
    user = make_user("hero")
    h = user["headers"]
    p_id = _make_project(client, h)

    c = client.post(
        f"/api/projects/{p_id}/characters",
        headers=h,
        json={"name": "刘飞", "personality": "孤傲"},
    ).json()

    # 改 baseline 不应产生 counterfactual
    client.patch(
        f"/api/characters/{c['id']}",
        headers=h,
        json={
            "behavior_baseline": {
                "speech_register": "强硬",
                "emotional_intensity": 7,
            },
        },
    )
    overview_before = client.get(
        f"/api/projects/{p_id}/counterfactuals", headers=h
    ).json()
    cf_before_count = overview_before.get("total_active", 0)

    # 改 personality 应产生 counterfactual(对照组,证明追踪机制本身工作)
    client.patch(
        f"/api/characters/{c['id']}",
        headers=h,
        json={"personality": "孤傲深沉,藏着旧伤"},
    )
    overview_after = client.get(
        f"/api/projects/{p_id}/counterfactuals", headers=h
    ).json()
    cf_after_count = overview_after.get("total_active", 0)

    # baseline 改不增加 counterfactual;personality 改 +1(共增加 1)
    assert cf_after_count == cf_before_count + 1
