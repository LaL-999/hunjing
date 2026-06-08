"""反事实变量路由 — Sprint 2.C + 2.C+。

GET  /api/projects/{project_id}/counterfactuals          列出 active(给反事实工作台 / 面板)
GET  /api/projects/{project_id}/reshape_preview          重塑度三维度实时预览(给 ReshapeSlider)
POST /api/projects/{project_id}/counterfactuals          ⭐ 2.C+ 显式创建(character/event/relationship)
POST /api/projects/{project_id}/counterfactuals/world    ⭐ 2.C+ 显式创建(world)
POST /api/counterfactuals/{change_id}/revert             撤销反事实

设计:
  PATCH hook(隐式 record):用户在 NodeEditDrawer 改字段时自动落,无 user_intent
  显式 POST(2.C+ Workbench):用户主动创建,**强烈推荐**带 user_intent

  两条路径并存,merge 语义统一(同 project+target+field 已 active → 更新 new_value/intent)。
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.deps import get_current_user, get_db
from app.models.counterfactual_change import WORLD_TARGET_ID
from app.models.user import User
from app.schemas.counterfactual import (
    CounterfactualChangeResponse,
    CounterfactualOverviewResponse,
    CreateCounterfactualRequest,
    CreateWorldCounterfactualRequest,
    ReshapePreviewResponse,
    RevertCounterfactualResponse,
)
from app.services.counterfactual_service import (
    CounterfactualNotFoundOrForbidden,
    ReshapeCharacterLimitExceeded,
    derive_reshape_dimensions,
    get_or_404,
    overview,
    record_change,
    record_world_change,
    revert,
)
from app.services.project_service import ResourceNotFoundOrForbidden, get_project_or_403
from app.services.quota_service import PLAN_LIMITS

router = APIRouter()


@router.get(
    "/projects/{project_id}/counterfactuals",
    response_model=CounterfactualOverviewResponse,
)
def api_list_counterfactuals(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """列出该项目所有 active 反事实 + 按 type 分桶 + 总数。"""
    try:
        return overview(conn, project_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise


@router.get(
    "/projects/{project_id}/reshape_preview",
    response_model=ReshapePreviewResponse,
)
def api_reshape_preview(
    project_id: str,
    reshape_percent: int = Query(
        ..., ge=10, le=90,
        description="待预览的重塑度 10-90,前端 ReshapeSlider 拖动时实时调用",
    ),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """三维度预览:max_touched_characters / rounds_planned / graph_distance_hops
    + 当前 touched_count + BFS 影响节点 ids + plan 上限。

    用法:用户拖 ReshapeSlider → debounce 300ms → GET 此端点 → 右侧三维更新
    + 3D 图谱高亮 affected 节点。
    """
    plan_max = PLAN_LIMITS.get(user.plan, PLAN_LIMITS["free"]).reshape_max_percent
    try:
        return derive_reshape_dimensions(
            conn, project_id, user.id, reshape_percent, plan_max,
        )
    except ResourceNotFoundOrForbidden:
        raise


@router.post(
    "/projects/{project_id}/counterfactuals",
    response_model=CounterfactualChangeResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_create_counterfactual(
    project_id: str,
    req: CreateCounterfactualRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """显式创建 character/event/relationship 反事实(2.C+ Workbench 用)。

    与 PATCH hook 区别:
      - 不实际改 db 字段(NodeEditDrawer PATCH 才改);只 record 反事实
      - **强烈推荐**带 user_intent
      - merge 语义:同 (project, target, field) 已 active → 更新 new_value + intent
    """
    try:
        get_project_or_403(conn, project_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise

    try:
        cf = record_change(
            conn=conn,
            project_id=project_id,
            target_type=req.target_type,
            target_id=req.target_id,
            field=req.field,
            old_value=req.old_value,
            new_value=req.new_value,
            user_id=user.id,
            user_intent=req.user_intent,
        )
    except ValueError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_COUNTERFACTUAL", "message": str(e)},
        )
    return cf.to_response()


@router.post(
    "/projects/{project_id}/counterfactuals/world",
    response_model=CounterfactualChangeResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_create_world_counterfactual(
    project_id: str,
    req: CreateWorldCounterfactualRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """显式创建世界观反事实(2.C+ 6 维度入口)— target_type='world',target_id='_global_'。

    field 必须是 6 个维度之一:genre / setting / magic_system / time_axis / tone / free_form
    """
    try:
        get_project_or_403(conn, project_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise

    try:
        cf = record_world_change(
            conn=conn,
            project_id=project_id,
            field=req.field,
            old_value=req.old_value,
            new_value=req.new_value,
            user_intent=req.user_intent,
            user_id=user.id,
        )
    except ValueError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_WORLD_COUNTERFACTUAL", "message": str(e)},
        )
    return cf.to_response()


@router.post(
    "/counterfactuals/{change_id}/revert",
    response_model=RevertCounterfactualResponse,
)
def api_revert_counterfactual(
    change_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """撤销反事实 — 标 reverted_at + 把 db 字段还原到 old_value。

    幂等:重复撤销同一反事实返回 200 + restored=False + error="已经撤销过了"。
    """
    try:
        cf, restored, err = revert(conn, change_id, user.id)
    except CounterfactualNotFoundOrForbidden as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "COUNTERFACTUAL_NOT_FOUND", "message": str(e)},
        )
    return {
        "counterfactual": cf.to_response(),
        "target_field_restored": restored,
        "restore_error": err,
    }
