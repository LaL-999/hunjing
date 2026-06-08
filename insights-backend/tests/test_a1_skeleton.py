"""INS-A1 后端骨架 smoke 测试(2026-05-27).

验证:
  1. migrations 跑通 + events 表创建
  2. POST /track 单条写入
  3. POST /track/batch 批量写入
  4. GET /track/health
  5. /admin/* 鉴权 — 无 token 401
  6. /admin/overview 有 token 通过
  7. EVENT_TYPES 校验
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolate_db(tmp_path, monkeypatch):
    """每个 test 用独立 analytics.db,避免相互污染。"""
    monkeypatch.setenv("INSIGHTS_ANALYTICS_DB", str(tmp_path / "analytics.db"))
    # huimeng.db 用空路径(不 attach),避免依赖主平台 DB
    monkeypatch.setenv("INSIGHTS_HUIMENG_DB", str(tmp_path / "huimeng_not_exist.db"))
    monkeypatch.setenv("INSIGHTS_ADMIN_TOKEN", "test-token")
    # reload settings(它在 import 时读 env)
    import importlib
    import app.config
    importlib.reload(app.config)
    import app.db
    importlib.reload(app.db)
    import app.routers.track
    importlib.reload(app.routers.track)
    import app.routers.admin
    importlib.reload(app.routers.admin)
    import app.main
    importlib.reload(app.main)


@pytest.fixture
def client(isolate_db):
    import app.main
    # FastAPI TestClient 用 with 语法触发 lifespan(跑 migrations)
    # 直接 TestClient(app) 不会跑 lifespan
    with TestClient(app.main.app) as c:
        yield c


# ============================================================
# 1. health
# ============================================================

def test_health_endpoint_returns_events_count(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["events_count"] == 0
    # huimeng.db 不存在 → attach 失败,这里应是 False
    assert body["huimeng_attached"] is False


# ============================================================
# 2. POST /track 单条
# ============================================================

def test_track_single_event_inserts_row(client):
    ev = {
        "event_type": "page_view",
        "user_id": "u-1",
        "session_id": "sess-1",
        "timestamp_ms": int(time.time() * 1000),
        "path": "/projects/foo",
        "mode": "initial",
    }
    r = client.post("/track", json=ev)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "accepted": 1, "rejected": 0, "reason": None}

    # 验证落库
    r = client.get("/health")
    assert r.json()["events_count"] == 1


def test_track_rejects_unknown_event_type(client):
    """Pydantic Literal 拒绝未知 event_type → 422。"""
    ev = {
        "event_type": "fake_event_xxx",
        "session_id": "sess-1",
        "timestamp_ms": 0,
    }
    r = client.post("/track", json=ev)
    assert r.status_code == 422


def test_track_anonymous_user_allowed(client):
    """user_id 可为 None(未登录用户的 page_view)。"""
    ev = {
        "event_type": "page_view",
        "session_id": "sess-anon",
        "timestamp_ms": int(time.time() * 1000),
        "path": "/login",
    }
    r = client.post("/track", json=ev)
    assert r.status_code == 200
    assert r.json()["accepted"] == 1


# ============================================================
# 3. POST /track/batch 批量
# ============================================================

def test_track_batch_inserts_all(client):
    payload = {
        "events": [
            {
                "event_type": "session_start",
                "session_id": "s1",
                "timestamp_ms": 1000,
            },
            {
                "event_type": "page_view",
                "session_id": "s1",
                "timestamp_ms": 2000,
                "path": "/home",
            },
            {
                "event_type": "page_leave",
                "session_id": "s1",
                "timestamp_ms": 3000,
                "duration_ms": 1000,
            },
        ]
    }
    r = client.post("/track/batch", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 3
    assert body["rejected"] == 0


def test_track_batch_rejects_empty(client):
    """min_length=1 → 空数组 422。"""
    r = client.post("/track/batch", json={"events": []})
    assert r.status_code == 422


# ============================================================
# 4. /admin/* 鉴权
# ============================================================

def test_admin_overview_requires_token(client):
    r = client.get("/admin/overview")
    assert r.status_code == 401


def test_admin_overview_with_wrong_token(client):
    r = client.get("/admin/overview", headers={"X-Admin-Token": "wrong"})
    assert r.status_code == 401


def test_admin_overview_with_correct_token(client):
    r = client.get("/admin/overview", headers={"X-Admin-Token": "test-token"})
    assert r.status_code == 200
    body = r.json()
    assert "total_events" in body
    assert "today_events" in body
    assert "active_users_7d" in body
    assert body["huimeng_attached"] is False   # 测试环境无 huimeng.db


def test_admin_funnel_returns_steps(client):
    """INS-B6(2026-05-27 末⁵²):漏斗加深到 10 步 + 3 筛选器(window/mode/plan).
    测试环境无 huimeng.db,降级返 steps=[] + huimeng_attached=False.
    """
    r = client.get("/admin/funnel", headers={"X-Admin-Token": "test-token"})
    assert r.status_code == 200
    body = r.json()
    assert "steps" in body
    assert "huimeng_attached" in body
    assert "window" in body
    assert "mode" in body
    assert "plan" in body
    # 测试环境 huimeng 未挂载 → 降级返空 steps + note
    assert body["huimeng_attached"] is False
    assert body["steps"] == []


def test_admin_retention_validates_days(client):
    r = client.get(
        "/admin/retention?days=999",
        headers={"X-Admin-Token": "test-token"},
    )
    assert r.status_code == 422


def test_admin_retention_empty_cohort(client):
    """无数据时返 cohort_size=0 + retention=[]。"""
    r = client.get(
        "/admin/retention?days=7",
        headers={"X-Admin-Token": "test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["cohort_size"] == 0


def test_admin_user_profile(client):
    # 先插几条事件
    for i in range(3):
        client.post("/track", json={
            "event_type": "ai_call_done",
            "user_id": "u-profile",
            "session_id": f"s-{i}",
            "timestamp_ms": int(time.time() * 1000) + i,
            "duration_ms": 1000 * (i + 1),
            "meta": {"ai_name": "narrator"},
        })
    r = client.get(
        "/admin/user/u-profile",
        headers={"X-Admin-Token": "test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "u-profile"
    assert len(body["events"]) == 3
    assert body["ai_call_stats"]["ai_call_done"] == 3


# ============================================================
# 5. EVENT_TYPES 校验
# ============================================================

def test_event_types_count():
    from app.models.event import EVENT_TYPES
    # 设计上的 17 个 event_type(写在 README / memory)
    # 若加新类型,本断言要一起更新
    assert len(EVENT_TYPES) == 17


def test_event_types_includes_all_categories():
    from app.models.event import EVENT_TYPES
    # 4 大类必须都在
    categories = {
        "page": ["page_view", "page_leave", "page_refresh"],
        "ai": ["ai_call_start", "ai_call_done", "ai_call_failed"],
        "biz": ["project_create", "simulation_create", "audit_run"],
        "session": ["session_start", "session_end"],
    }
    for cat, types in categories.items():
        for t in types:
            assert t in EVENT_TYPES, f"category {cat}: {t} missing"
