"""Refine 路由 — 3 个 endpoints,Sprint 1.D 灵魂。

- POST /api/projects/{project_id}/refine               触发对焦
- POST /api/refinements/{refinement_id}/action         处理一条建议
- POST /api/refine_sessions/{session_id}/skip          跳过整个 session
"""
from __future__ import annotations

import sqlite3
import traceback

from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.refine import (
    ActionRefinementRequest,
    ActionRefinementResponse,
    RefineProjectResponse,
    SkipSessionRequest,
    SkipSessionResponse,
)
from app.services.credit_service import InsufficientCredits
from app.services.llm_client import LlmCallFailed, LlmJsonParseFailed
from app.services.project_service import ResourceNotFoundOrForbidden
from app.services.refine_service import (
    RefinementAlreadyActioned,
    TooFewCharactersError,
    WouldOverwriteNonEmpty,
    action_refinement,
    refine_project,
    skip_session,
)

router = APIRouter()


@router.post(
    "/projects/{project_id}/refine", response_model=RefineProjectResponse
)
def api_refine(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        # Sprint C.1:credit 闸门由 refine_service 内部调 consume_credits 实现
        # 不足时抛 InsufficientCredits → router 转 429 + 弹加购 / 升档 modal
        return refine_project(conn, project_id, user.id)
    except InsufficientCredits as e:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "INSUFFICIENT_CREDITS",
                "needed": e.needed,
                "available": e.available,
                "action": e.action,
                "message": str(e),
            },
        )
    except TooFewCharactersError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "TOO_FEW_CHARACTERS", "message": str(e)},
        )
    except LlmJsonParseFailed as e:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "LLM_OUTPUT_INVALID",
                "message": f"AI 输出不合法:{e}",
            },
        )
    except LlmCallFailed as e:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "LLM_UNAVAILABLE", "message": str(e)},
        )
    except HTTPException:
        raise   # 已分类的 HTTPException 直接外抛
    except ResourceNotFoundOrForbidden:
        raise   # 让 main.py 的全局 handler 转 404
    except Exception as e:
        # 兜底:未分类异常 — 打 traceback 到 stderr 便于定位
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"refine 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )


@router.post(
    "/refinements/{refinement_id}/action",
    response_model=ActionRefinementResponse,
)
def api_action(
    refinement_id: str,
    req: ActionRefinementRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return action_refinement(
            conn, refinement_id, user.id, req.action, req.user_edit
        )
    except RefinementAlreadyActioned as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "ALREADY_ACTIONED", "message": str(e)},
        )
    except WouldOverwriteNonEmpty as e:
        # §16 兜底 ① 硬实施被触发
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "REFINEMENT_WOULD_OVERWRITE",
                "field": e.field,
                "message": str(e),
            },
        )
    except HTTPException:
        raise
    except ResourceNotFoundOrForbidden:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"action 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )


@router.post(
    "/refine_sessions/{session_id}/skip", response_model=SkipSessionResponse
)
def api_skip(
    session_id: str,
    req: SkipSessionRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return skip_session(conn, session_id, user.id, req.reason)
    except RefinementAlreadyActioned as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "SESSION_ALREADY_COMPLETED", "message": str(e)},
        )
    except HTTPException:
        raise
    except ResourceNotFoundOrForbidden:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"skip 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )
