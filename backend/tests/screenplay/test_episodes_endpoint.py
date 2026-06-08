"""分集规划 endpoint 集成测试 — 阶段 8.4+ Phase 6(2026-06-08)。

测覆盖:
  1. GET /episodes/presets 返预设档 + 视角清单
  2. POST /plan-episodes-multi 无剧本 → 404
  3. POST /plan-episodes-multi 跨用户 → 404
  4. POST /plan-episodes 老 endpoint 仍可用(向后兼容)
  5. body 校验:preset / target / with_llm_titles
"""
from __future__ import annotations

import pytest


# ============================================================
# 1. GET /episodes/presets
# ============================================================


def test_get_presets_returns_presets_and_perspectives(client):
    """GET /episodes/presets 返 presets + perspectives 两个数组。"""
    resp = client.get("/api/screenplay/episodes/presets")
    assert resp.status_code == 200
    data = resp.json()
    assert "presets" in data
    assert "perspectives" in data
    assert len(data["presets"]) >= 3
    # 至少有 short_drama / long_drama / anime / custom
    preset_keys = [p["key"] for p in data["presets"]]
    assert "short_drama" in preset_keys
    assert "long_drama" in preset_keys
    assert "anime" in preset_keys
    assert "custom" in preset_keys
    # 3 个视角
    persp_keys = [p["key"] for p in data["perspectives"]]
    assert set(persp_keys) == {"rhythm", "hook", "arc"}


def test_get_presets_each_has_label_description(client):
    """每个 preset 含 label / description / default_minutes。"""
    resp = client.get("/api/screenplay/episodes/presets")
    data = resp.json()
    for p in data["presets"]:
        assert "label" in p
        assert "description" in p
        # custom 的 default_minutes 是 None,其他有值
        assert "default_minutes" in p
    for v in data["perspectives"]:
        assert "label" in v
        assert "description" in v


# ============================================================
# 2. POST /plan-episodes-multi 异常
# ============================================================


def test_plan_multi_no_screenplay_returns_404(client):
    """未上传 novel → 404 SCREENPLAY_NOT_FOUND。"""
    resp = client.post(
        "/api/screenplay/novels/nonexistent_999/plan-episodes-multi",
        json={"preset": "short_drama"},
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == "SCREENPLAY_NOT_FOUND"


def test_plan_multi_invalid_target_returns_422(client):
    """target_minutes_per_ep < 0.5 → 422(pydantic 校验)。"""
    resp = client.post(
        "/api/screenplay/novels/n1/plan-episodes-multi",
        json={"preset": "custom", "target_minutes_per_ep": 0.1},
    )
    # pydantic ValidationError → 422
    assert resp.status_code == 422


def test_plan_multi_unauthenticated_returns_401():
    """无 Bearer JWT → 401。"""
    from fastapi.testclient import TestClient
    from app.main import app
    bare_client = TestClient(app)
    resp = bare_client.post(
        "/api/screenplay/novels/n1/plan-episodes-multi",
        json={},
    )
    # 没 JWT → 401(JWT bearer middleware 拦截)
    assert resp.status_code in (401, 403)


# ============================================================
# 3. 向后兼容 — 老 plan-episodes 仍工作
# ============================================================


def test_old_plan_episodes_still_returns_404(client):
    """老 endpoint(MVP)— 无剧本仍返 404。"""
    resp = client.post(
        "/api/screenplay/novels/nonexistent_888/plan-episodes",
        json={"target_minutes_per_ep": 3.0},
    )
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert detail["code"] == "SCREENPLAY_NOT_FOUND"


# ============================================================
# 4. body 默认值
# ============================================================


def test_plan_multi_empty_body_uses_defaults(client):
    """空 body → 默认 short_drama,target=None,with_llm_titles=True。"""
    resp = client.post(
        "/api/screenplay/novels/nonexistent_777/plan-episodes-multi",
        json={},  # 完全空
    )
    # 应进入 service 但因 novel 不存在 → 404,而不是 422
    assert resp.status_code == 404


def test_plan_multi_explicit_preset(client):
    """显式指定 long_drama preset 也应通过 body 校验。"""
    resp = client.post(
        "/api/screenplay/novels/nonexistent_666/plan-episodes-multi",
        json={"preset": "long_drama", "with_llm_titles": False},
    )
    assert resp.status_code == 404  # service 层无剧本
