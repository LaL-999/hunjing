"""健康检查 endpoint 测试 — 阶段 7 适配父平台。

比赛仓库原有的 /health endpoint 返
  { status, service: "hunjing-screenplay", version, llm_model, llm_configured }

阶段 3 迁入父平台时,/health 由父平台已存的 endpoint 接管(不在 /api/screenplay/
前缀下,而是父平台根 /health),返不同 schema(无 service 字段等)。原比赛健康
测试不再适用 → 重写为「父平台 /health 仍能跑 + 剧创 router 已挂载」语义。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_parent_health_returns_ok():
    """父平台健康检查 /healthz 或 /api/health → 200。"""
    # 父平台双 endpoint:无前缀 /healthz + 有前缀 /api/health
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    # 父平台健康 schema 至少有 status / ok 类字段
    assert body.get("status") == "ok" or body.get("ok") is True


def test_screenplay_routes_registered():
    """阶段 3 注册的 /api/screenplay/* 18 个 router 在 app 中可见。"""
    paths = {str(r.path) for r in app.routes if str(r.path).startswith("/api/screenplay")}
    # 阶段 5.1 加 /link 后总 19 个独立 path(不含 OPTIONS 副本)
    assert len(paths) >= 18
    # 核心 endpoint 必在
    assert "/api/screenplay/novels" in paths
    assert any("/screenplays/{screenplay_id}" in p for p in paths)
    assert any("/compose-screenplay" in p for p in paths)
    assert any("/link" in p for p in paths)
