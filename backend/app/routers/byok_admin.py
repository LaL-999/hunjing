"""
BYOK admin 管理后台 router(2026-06-05)。

仅创始人邮箱(plan='founder')可访问 — 通过 require_founder dependency 守门。

Endpoints:
  GET   /api/admin/byok/orders                      — 订单列表(status 筛选 + manual_review 优先)
  POST  /api/admin/byok/orders/{id}/approve         — 审批通过(自动激活 BYOK 订阅)
  POST  /api/admin/byok/orders/{id}/reject          — 拒绝(必填 reason)
  GET   /api/admin/byok/orders/{id}/proof_image     — 看付款截图(file response)

为什么单独建一个 router 而不挂到 byok_payment.py:
  - 权限不同(用户 vs founder)
  - 路径 prefix 不同(/byok/payment vs /admin/byok)
  - 后续 admin 模块会扩展(用户管理 / 审计日志 / 系统配置等),独立 router 更清晰
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse

from app.deps import get_db, require_founder
from app.models.byok_payment import (
    AdminApproveRequest,
    AdminPaymentOrderResponse,
    AdminPaymentOrdersListResponse,
    AdminRejectRequest,
)
from app.models.user import User
from app.services import byok_payment_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# 订单列表
# ============================================================

@router.get(
    "/admin/byok/orders",
    response_model=AdminPaymentOrdersListResponse,
)
def api_admin_list_orders(
    status_filter: Optional[str] = Query(
        None,
        description="筛选状态:pending / submitted / manual_review / paid / rejected / expired / all",
    ),
    limit: int = Query(100, ge=1, le=500),
    admin: User = Depends(require_founder),
    conn: sqlite3.Connection = Depends(get_db),
) -> AdminPaymentOrdersListResponse:
    orders, total, pending_count = byok_payment_service.admin_list_orders(
        conn,
        status_filter=status_filter,
        limit=limit,
    )
    return AdminPaymentOrdersListResponse(
        orders=[AdminPaymentOrderResponse(**o) for o in orders],
        total=total,
        pending_review_count=pending_count,
    )


# ============================================================
# 审批通过
# ============================================================

@router.post(
    "/admin/byok/orders/{order_id}/approve",
    response_model=AdminPaymentOrderResponse,
)
def api_admin_approve_order(
    order_id: str,
    payload: AdminApproveRequest = AdminApproveRequest(),
    admin: User = Depends(require_founder),
    conn: sqlite3.Connection = Depends(get_db),
) -> AdminPaymentOrderResponse:
    try:
        result = byok_payment_service.admin_approve_order(
            conn,
            order_id,
            admin_user_id=admin.id,
            note=payload.note,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "APPROVE_FAILED", "message": str(e)},
        )

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORDER_NOT_FOUND", "message": "订单不存在"},
        )

    return AdminPaymentOrderResponse(**result)


# ============================================================
# 拒绝
# ============================================================

@router.post(
    "/admin/byok/orders/{order_id}/reject",
    response_model=AdminPaymentOrderResponse,
)
def api_admin_reject_order(
    order_id: str,
    payload: AdminRejectRequest,
    admin: User = Depends(require_founder),
    conn: sqlite3.Connection = Depends(get_db),
) -> AdminPaymentOrderResponse:
    try:
        result = byok_payment_service.admin_reject_order(
            conn,
            order_id,
            admin_user_id=admin.id,
            reason=payload.reason,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "REJECT_FAILED", "message": str(e)},
        )

    if not result:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORDER_NOT_FOUND", "message": "订单不存在"},
        )

    return AdminPaymentOrderResponse(**result)


# ============================================================
# 截图查看
# ============================================================

@router.get("/admin/byok/orders/{order_id}/proof_image")
def api_admin_get_proof_image(
    order_id: str,
    admin: User = Depends(require_founder),
    conn: sqlite3.Connection = Depends(get_db),
):
    """返截图文件(给前端 <img> 加载)— 仅 founder 可看。"""
    path: Optional[Path] = byok_payment_service.admin_get_proof_image_path(conn, order_id)
    if path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROOF_NOT_FOUND", "message": "截图不存在或未上传"},
        )
    # 让浏览器内嵌显示(不下载)— inline,自动识别 MIME
    return FileResponse(path)
