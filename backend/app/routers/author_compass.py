"""作者指南针路由 — P3 Day 1(2026-05-26).

POST /api/projects/{project_id}/author-compass/analyze  触发双轨制 LLM 调研
GET  /api/projects/{project_id}/author-compass          拉当前画像
PUT  /api/projects/{project_id}/author-compass          用户改 / 锁定

设计原则:
  - 所有端点先 get_project_or_403 鉴权(跨用户隔离)
  - POST analyze 同步等待两轨 LLM 完成(~20-40s),前端可加 loading state
  - 已 locked 的 compass 不能 PUT user_locked=False(锁定后只能用户重新跑 analyze 才能改)
"""
from __future__ import annotations

import sqlite3
import traceback

from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.author_compass import (
    AnalyzeRequest,
    AuthorCompassResponse,
    UpdateCompassRequest,
)
from app.services.author_compass_service import (
    analyze_compass_for_project,
    get_compass,
    update_compass_user_fields,
)
from app.services.project_service import (
    ResourceNotFoundOrForbidden,
    get_project_or_403,
)


router = APIRouter()


@router.post(
    "/projects/{project_id}/author-compass/analyze",
    response_model=AuthorCompassResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_analyze_author_compass(
    project_id: str,
    body: AnalyzeRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """触发双轨制 LLM 调研.

    同步等待两轨完成(~20-40s),返回最终 compass。
    已锁定的 compass 会被忽略(返回当前值),用户必须先解锁才能重跑。
    """
    try:
        get_project_or_403(conn, project_id, user.id)
        compass = analyze_compass_for_project(
            conn, project_id,
            author_name=body.author_name,
            work_title=body.work_title,
            force=True,   # 用户主动触发即重跑(除非 locked,service 内部会拦)
        )
        return compass.to_dict()
    except ResourceNotFoundOrForbidden:
        raise
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"作者指南针分析失败:{type(e).__name__}: {e}"[:300],
            },
        )


@router.get(
    "/projects/{project_id}/author-compass",
    response_model=AuthorCompassResponse,
)
def api_get_author_compass(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """拉项目当前 author_compass.

    无记录 → 404 AUTHOR_COMPASS_NOT_FOUND(前端默认显示"尚未分析"占位)
    """
    try:
        get_project_or_403(conn, project_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise

    compass = get_compass(conn, project_id)
    if compass is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={
                "code": "AUTHOR_COMPASS_NOT_FOUND",
                "message": "项目尚未生成作者指南针",
            },
        )
    return compass.to_dict()


@router.put(
    "/projects/{project_id}/author-compass",
    response_model=AuthorCompassResponse,
)
def api_update_author_compass(
    project_id: str,
    body: UpdateCompassRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """用户改 / 锁定 author_compass.

    校验:
      - 锁定时(user_locked=True)final_compass 必须有值(或之前已存在)
      - 项目必须已有 author_compass 记录(没跑过 analyze → 404)
    """
    try:
        get_project_or_403(conn, project_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise

    compass = get_compass(conn, project_id)
    if compass is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={
                "code": "AUTHOR_COMPASS_NOT_FOUND",
                "message": "项目尚未生成作者指南针,请先调用 analyze",
            },
        )

    # 锁定校验:final_compass 必须有(本次给 or 已有)
    will_lock = body.user_locked is True
    final = body.final_compass if body.final_compass is not None else compass.final_compass
    if will_lock and not final:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "FINAL_COMPASS_REQUIRED_BEFORE_LOCK",
                "message": "锁定前必须填写 final_compass(用户最终拍板版本)",
            },
        )

    updated = update_compass_user_fields(
        conn, project_id,
        author_name=body.author_name,
        work_title=body.work_title,
        final_compass=body.final_compass,
        user_locked=body.user_locked,
    )
    return updated.to_dict() if updated else compass.to_dict()
