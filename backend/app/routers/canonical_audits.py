"""正典守护者路由 — Sprint 2.D。

POST /api/simulations/{sim_id}/canonical_audit         触发审计(异步,返 state='running' 初状态)
GET  /api/simulations/{sim_id}/canonical_audit/latest  最新一条(前端 polling)
GET  /api/simulations/{sim_id}/canonical_audits        历史列表

设计:对齐 1.R audit_service 但加 async state 机(2.D 输出长,LLM 耗时 30-60s,异步 UX 更好)
"""
from __future__ import annotations

import sqlite3
import traceback

from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.canonical_audit import CanonicalAuditResponse
from app.services.canonical_guardian_service import (
    CanonicalAuditNotApplicable,
    CanonicalAuditNotFoundOrForbidden,
    SimulationNotAuditable,
    get_latest_for_simulation,
    list_for_simulation,
    trigger_canonical_audit,
)
from app.services.project_service import ResourceNotFoundOrForbidden


router = APIRouter()


@router.post(
    "/simulations/{simulation_id}/canonical_audit",
    response_model=CanonicalAuditResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_trigger_canonical_audit(
    simulation_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """触发正典审计。

    异常:
      404 — sim 不存在 / 不属于用户
      422 CANONICAL_AUDIT_NOT_APPLICABLE — initial 态(无原作可守)
      422 SIMULATION_NOT_AUDITABLE       — sim 非 done / narrative 空
    """
    try:
        return trigger_canonical_audit(conn, simulation_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise
    except CanonicalAuditNotApplicable as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "CANONICAL_AUDIT_NOT_APPLICABLE", "message": str(e)},
        )
    except SimulationNotAuditable as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "SIMULATION_NOT_AUDITABLE", "message": str(e)},
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"触发正典审计失败:{type(e).__name__}: {e}"[:300],
            },
        )


@router.get(
    "/simulations/{simulation_id}/canonical_audit/latest",
    response_model=CanonicalAuditResponse,
)
def api_get_latest_canonical_audit(
    simulation_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """该 sim 最新一条 audit(前端 polling state 用)。

    无 audit → 404 CANONICAL_AUDIT_NOT_FOUND(前端默认无审计 UI)
    """
    try:
        audit = get_latest_for_simulation(conn, simulation_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise
    if audit is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "CANONICAL_AUDIT_NOT_FOUND", "message": "该推演还未跑过正典审计"},
        )
    # Sprint D.7:GET latest 实时查 _RUNNING_AUDITS 算 is_alive,前端识别僵尸态
    from app.services.canonical_guardian_service import is_audit_alive
    return audit.to_response(is_alive=is_audit_alive(audit.id))


@router.get(
    "/simulations/{simulation_id}/canonical_audits",
    response_model=list[CanonicalAuditResponse],
)
def api_list_canonical_audits(
    simulation_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """历史 audits(按时间倒序)。"""
    try:
        items = list_for_simulation(conn, simulation_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise
    return [a.to_response() for a in items]
