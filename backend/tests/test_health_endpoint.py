"""探活端点测试 — /healthz + /api/health.

2026-06-02 加 /api/health 作为 nginx 默认转发的探活路径.
"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_returns_ok(client: TestClient):
    """/healthz 返 ok=true + version + db ok."""
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "ok"
    assert body["db"] == "ok"
    assert "version" in body
    assert isinstance(body["version"], str)


def test_api_health_returns_ok(client: TestClient):
    """/api/health 跟 /healthz 等价(给 nginx /api/* 转发用)."""
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "ok"
    assert body["db"] == "ok"


def test_healthz_no_auth_required(client: TestClient):
    """探活端点不需鉴权 — UptimeRobot 直接访问."""
    r = client.get("/api/health")  # 无 headers
    assert r.status_code == 200
