"""Audit 路由 — 自洽守护者(Sprint 1.R),5 个端点。

POST /api/simulations/{sim_id}/audit                            触发新诊断
GET  /api/simulations/{sim_id}/audit/latest                     取最新一条(用户回 detail 页恢复结果)
POST /api/audits/{audit_id}/issues/{issue_idx}/accept           M7.C(2026-05-20)采纳建议一键应用
GET  /api/projects/{project_id}/pending_audit_patches           Sprint 6.A2 #2.5 列项目下已采纳待应用 patches
POST /api/projects/{project_id}/audit_patches/mark_applied      Sprint 6.A2 #2.5 批量标 applied_at(dock 提交推演后调)
"""
from __future__ import annotations

import sqlite3
import traceback

from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.audit import (
    AcceptAuditIssueResponse,
    AuditResponse,
    MarkAuditPatchesAppliedRequest,
    MarkAuditPatchesAppliedResponse,
    PendingAuditPatch,
)
from app.services.audit_acceptor_service import (
    AuditNotFoundOrForbidden,
    IssueNotAcceptable,
    SubjectNotFound,
    accept_audit_issue,
)
from app.services.audit_patch_service import (
    AuditPatchNotFound,
    ProjectNotFoundOrForbidden,
    list_pending_audit_patches,
    mark_audit_patches_applied,
)
from app.services.audit_service import (
    SimulationNotAuditable,
    audit_simulation,
    get_latest_audit,
)
from app.services.llm_client import LlmCallFailed, LlmJsonParseFailed
from app.services.project_service import ResourceNotFoundOrForbidden

router = APIRouter()


@router.post(
    "/simulations/{sim_id}/audit", response_model=AuditResponse
)
def api_create_audit(
    sim_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return audit_simulation(conn, sim_id, user.id)
    except SimulationNotAuditable as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "SIMULATION_NOT_AUDITABLE", "message": str(e)},
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
        raise
    except ResourceNotFoundOrForbidden:
        raise   # main.py 全局 handler 转 404
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"audit 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )


@router.get(
    "/simulations/{sim_id}/audit/latest", response_model=AuditResponse
)
def api_get_latest_audit(
    sim_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        result = get_latest_audit(conn, sim_id, user.id)
        if result is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail={
                    "code": "NO_AUDIT_YET",
                    "message": "该推演尚未做过自洽诊断",
                },
            )
        return result
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
                "message": f"audit 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )


# ======================================================================
# Sprint 6.A2 M7.C(2026-05-20)采纳建议自动应用
# ======================================================================

@router.post(
    "/audits/{audit_id}/issues/{issue_idx}/accept",
    response_model=AcceptAuditIssueResponse,
)
def api_accept_audit_issue(
    audit_id: str,
    issue_idx: int,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """一键采纳某 audit issue 的结构化修复建议,直接应用到对应字段。

    Errors:
      404 AUDIT_NOT_FOUND        audit 不存在 / 跨用户
      404 SUBJECT_NOT_FOUND      payload 关联的角色 / 事件 / 关系不存在或不属于你
      422 ISSUE_INDEX_OUT_OF_RANGE
      422 ISSUE_NOT_USER_FIXABLE 该 kind 不支持采纳(LLM-only 类)
      422 ISSUE_ALREADY_ACCEPTED 已采纳过
      422 ISSUE_NO_PAYLOAD       LLM 未给结构化建议,引导走「去修」
      422 ISSUE_NO_SUBJECT       payload 缺 subject_id
      422 ISSUE_INVALID_TARGET   payload.target 非法
      422 ISSUE_NO_OPERATIONS    无可应用 operation
      422 ISSUE_ALL_OPS_SKIPPED  全部 op 被白名单 / 兜底过滤
    """
    try:
        return accept_audit_issue(conn, audit_id, issue_idx, user.id)
    except AuditNotFoundOrForbidden as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "AUDIT_NOT_FOUND", "message": str(e)},
        )
    except SubjectNotFound as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "SUBJECT_NOT_FOUND", "message": str(e)},
        )
    except IssueNotAcceptable as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": e.code, "message": str(e)},
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"accept 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )


# ======================================================================
# Sprint 6.A2 路线图 #2.5(2026-05-22)— sim_config patch 跨会话持久化
# 用 audit.issues_json 内字段 applied_at 标记是否已用,无 migration
# ======================================================================

@router.get(
    "/projects/{project_id}/pending_audit_patches",
    response_model=list[PendingAuditPatch],
)
def api_list_pending_audit_patches(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """列项目下所有已采纳未应用的 sim_config patches。

    给 ProjectView mount 时调用,把 patches 透传给 SimulationDock 预填字段。

    Returns:
      list[PendingAuditPatch] — 空数组合法(无 pending);按 accepted_at ASC 排序

    Errors:
      404 NOT_FOUND  项目不存在 / 跨用户
    """
    try:
        return list_pending_audit_patches(conn, project_id, user.id)
    except ProjectNotFoundOrForbidden as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(e)},
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"列 pending patches 失败:{type(e).__name__}: {e}"[:300],
            },
        )


@router.post(
    "/projects/{project_id}/audit_patches/mark_applied",
    response_model=MarkAuditPatchesAppliedResponse,
)
def api_mark_audit_patches_applied(
    project_id: str,
    body: MarkAuditPatchesAppliedRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """批量标记 audit issues 的 applied_at = ISO now。

    dock 提交推演成功后调用,把刚才预填用过的 patches 标记 "已应用",
    下次 GET pending_audit_patches 就不再返这些条目。

    幂等:已 applied 的跳过不算 marked,不报错;允许 client 重复调。

    Errors:
      404 NOT_FOUND       项目不存在 / 跨用户
      422 ITEM_INVALID    item 字段缺失 / 类型非法
      422 AUDIT_NOT_FOUND audit 不存在 / 跨用户
      422 ISSUE_INDEX_OUT_OF_RANGE
      422 ISSUE_NOT_ACCEPTED 未采纳的 issue 不能标 applied
    """
    # 项目鉴权(防 user 把别项目的 audit_id 塞进来)
    try:
        list_pending_audit_patches(conn, project_id, user.id)  # 只为鉴权,丢弃返回
    except ProjectNotFoundOrForbidden as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": str(e)},
        )

    try:
        items = [it.model_dump() for it in body.items]
        # C-4 修复(2026-05-23):传 project_id 让 service 严格校验 audit ∈ project
        marked = mark_audit_patches_applied(conn, items, user.id, project_id)
        return {"marked": marked}
    except AuditPatchNotFound as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": e.code, "message": str(e)},
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"mark applied 失败:{type(e).__name__}: {e}"[:300],
            },
        )
