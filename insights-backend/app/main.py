"""insights-backend FastAPI 入口(2026-05-27).

启动:
  uvicorn app.main:app --port 8001 --reload

健康检查:
  GET http://localhost:8001/track/health
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db import get_db_connection, run_migrations
from app.routers import admin, agent, track


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动 → 跑 migrations / 检查 huimeng.db 可达."""
    logger.info("insights-backend 启动")
    logger.info(f"  analytics.db: {settings.ANALYTICS_DB_PATH}")
    logger.info(f"  huimeng.db: {settings.HUIMENG_DB_PATH}(read-only attach)")
    logger.info(f"  port: {settings.PORT}")

    # 跑 migrations(幂等)
    migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
    conn = get_db_connection()
    try:
        run_migrations(conn, migrations_dir)
        logger.info(f"  migrations: {migrations_dir} 已应用")
    finally:
        conn.close()

    yield

    logger.info("insights-backend 关闭")


app = FastAPI(
    title="huimeng-insights",
    description=(
        "浑晶洞察子系统 — 用户行为追踪 + 数据 agent + 知识库。"
        "零污染主平台:本服务独立端口 / 独立 DB,只读 attach 主平台 huimeng.db。"
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — 允许主平台 frontend 发埋点 + insights-frontend 调 admin
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-Admin-Token"],
)

app.include_router(track.router, tags=["track"])
app.include_router(admin.router, tags=["admin"])
app.include_router(agent.router, tags=["agent"])   # INS-A5/A6 二级数据 agent


@app.get("/")
async def root() -> dict:
    """根端点 — 返回基本信息(用户不会调,只为方便排查)."""
    return {
        "service": "huimeng-insights",
        "version": "0.1.0",
        "docs": "/docs",
    }
