"""Sprint 6.A2 路线图 #6(2026-05-23)— 全局搜索 endpoint。

GET /api/search?q=xxx&project_id=xxx(可选)&limit=10(每类)
跨用户所有项目模糊搜 6 类实体(projects / characters / relationships / events /
scenes / simulations)。
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.deps import get_current_user, get_db
from app.models.user import User
from app.services import search_service

router = APIRouter()


# ============================================================
# Response Schemas
# ============================================================

class SearchProjectItem(BaseModel):
    id: str
    name: str
    mode: str
    tags: list[str]


class SearchCharacterItem(BaseModel):
    id: str
    name: str
    project_id: str
    project_name: str
    identity_excerpt: str


class SearchEventItem(BaseModel):
    id: str
    description: str
    time_anchor: Optional[str] = None
    project_id: str
    project_name: str


class SearchSceneItem(BaseModel):
    id: str
    name: str
    description: str
    project_id: str
    project_name: str


class SearchSimulationItem(BaseModel):
    id: str
    divergence: str
    state: str
    created_at: str
    project_id: str
    project_name: str


class GlobalSearchResponse(BaseModel):
    query: str
    projects: list[SearchProjectItem]
    characters: list[SearchCharacterItem]
    events: list[SearchEventItem]
    scenes: list[SearchSceneItem]
    simulations: list[SearchSimulationItem]


# ============================================================
# Endpoint
# ============================================================

@router.get("/search", response_model=GlobalSearchResponse)
def api_search(
    q: str = Query(..., min_length=1, max_length=100, description="搜索关键字"),
    project_id: Optional[str] = Query(
        default=None,
        description="可选 — 限当前项目内搜(范围 tab '当前项目');不给跨所有项目",
    ),
    limit: int = Query(default=10, ge=1, le=50, description="每类实体最多返回条数"),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """全局搜索 6 类实体,模糊匹配,跨用户所有项目。

    错误:
      422 VALIDATION q 为空 / 过长(schema 拦)
    """
    q_clean = q.strip()
    if not q_clean:
        # min_length=1 不拦纯空格 — service 层兜底
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "VALIDATION_ERROR", "message": "搜索关键字不能为空"},
        )
    return search_service.search(
        conn, user_id=user.id, q=q_clean, project_id=project_id, limit=limit,
    )
