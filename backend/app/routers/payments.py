"""统一支付 API — 商业化重塑第一期(2026-06-09)。

四层解耦的对外契约。所有付费场景共用:

用户侧(JWT):
  GET  /payments/catalog               上架 SKU 列表(定价页 / 购买弹窗)
  POST /payments/orders                下单(body: sku_code)→ 返回订单 + 收款二维码
  GET  /payments/orders                我的订单列表
  GET  /payments/orders/{id}           单个订单详情
  POST /payments/orders/{id}/proof     上传付款截图(multipart)

管理员侧(X-Admin-Token / 创始人 JWT,给洞察后台审核页):
  GET  /payments/admin/orders          列待审 / 按状态筛
  POST /payments/admin/orders/{id}/approve   审核通过 → 履约发货
  POST /payments/admin/orders/{id}/reject    驳回
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.config import settings
from app.deps import get_current_user, get_db, require_founder_or_admin_token
from app.models.user import User
from app.services.payments import catalog, order_service
from app.services.payments import fulfillment_service

router = APIRouter()


# ============================================================
# 商品目录 + 收款信息
# ============================================================

@router.get("/payments/catalog")
def api_catalog(category: Optional[str] = None) -> dict:
    """上架 SKU 列表(可按 subscription / byok / credit 筛)。"""
    return {"items": [catalog.to_dict(s) for s in catalog.list_skus(category)]}


@router.get("/payments/pay-info")
def api_pay_info() -> dict:
    """收款二维码 + 收款方信息(个人主体扫码阶段;前端下单后展示)。

    复用现有 BYOK 收款配置(byok_payee_name / byok_wechat_qr_filename),
    收款主体是同一个人,无需另配。二维码文件放 backend/data/payment_qrcodes/。
    """
    from pathlib import Path
    qrcodes_dir: Path = settings.uploads_abs_dir.parent / "payment_qrcodes"

    def _qr_url(filename: str) -> str:
        if filename and (qrcodes_dir / filename).exists():
            return f"/api/payment-qrcodes/{filename}"
        return ""

    return {
        "channel": "wechat_qr_manual",
        "payee_name": settings.byok_payee_name,
        "wechat_qr_url": _qr_url(settings.byok_wechat_qr_filename),
        "alipay_qr_url": _qr_url(settings.byok_alipay_qr_filename),
        "note": "扫码支付后,请上传付款截图,我们将尽快人工核验并发放权益。",
    }


# ============================================================
# 用户侧:下单 / 查单 / 传凭证
# ============================================================

class CreateOrderBody(BaseModel):
    sku_code: str = Field(..., min_length=1, max_length=64)


@router.post("/payments/orders")
def api_create_order(
    body: CreateOrderBody,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return order_service.create_order(conn, user.id, body.sku_code)
    except order_service.OrderError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": e.code, "message": e.message},
        )


@router.get("/payments/orders")
def api_list_my_orders(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {"items": order_service.list_my_orders(conn, user.id)}


@router.get("/payments/orders/{order_id}")
def api_get_order(
    order_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return order_service.get_order(conn, order_id, user.id)
    except order_service.OrderError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": e.code, "message": e.message},
        )


@router.post("/payments/orders/{order_id}/proof")
async def api_submit_proof(
    order_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    image_bytes = await file.read()
    # 从文件名 / content-type 推扩展名
    ext = ".png"
    fn = (file.filename or "").lower()
    for cand in (".png", ".jpg", ".jpeg", ".webp"):
        if fn.endswith(cand):
            ext = cand
            break
    try:
        return order_service.submit_proof(conn, user.id, order_id, image_bytes, ext)
    except order_service.OrderError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": e.code, "message": e.message},
        )


# ============================================================
# 管理员侧:审核(给洞察后台,双轨鉴权)
# ============================================================

class RejectOrderBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=200)


@router.get("/payments/admin/orders")
def api_admin_list_orders(
    status_filter: Optional[str] = None,
    admin: User = Depends(require_founder_or_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return {"items": order_service.admin_list_orders(conn, status_filter)}


@router.post("/payments/admin/orders/{order_id}/approve")
def api_admin_approve(
    order_id: str,
    admin: User = Depends(require_founder_or_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return order_service.approve_order(conn, order_id, reviewer=admin.id)
    except order_service.OrderError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": e.code, "message": e.message},
        )
    except fulfillment_service.FulfillmentError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "FULFILLMENT_FAILED", "message": str(e)},
        )


@router.post("/payments/admin/orders/{order_id}/reject")
def api_admin_reject(
    order_id: str,
    body: RejectOrderBody,
    admin: User = Depends(require_founder_or_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        return order_service.reject_order(conn, order_id, reviewer=admin.id, reason=body.reason)
    except order_service.OrderError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": e.code, "message": e.message},
        )


@router.get("/payments/admin/orders/{order_id}/proof-image")
def api_admin_proof_image(
    order_id: str,
    admin: User = Depends(require_founder_or_admin_token),
    conn: sqlite3.Connection = Depends(get_db),
):
    """返付款截图文件给审核员 <img> 加载(私有目录,admin-only)。"""
    from pathlib import Path
    from fastapi.responses import FileResponse
    from app.db import fetch_one

    row = fetch_one(
        conn,
        "SELECT proof_image_path FROM payment_orders WHERE id = ?",
        (order_id,),
    )
    rel = row["proof_image_path"] if row else None
    if not rel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROOF_NOT_FOUND", "message": "截图不存在或未上传"},
        )
    # proof_image_path = 'payment_proofs/{user}/{order}.ext',相对 data/ 根
    abs_path = settings.uploads_abs_dir.parent / rel
    if not abs_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PROOF_NOT_FOUND", "message": "截图文件已丢失"},
        )
    return FileResponse(Path(abs_path))
