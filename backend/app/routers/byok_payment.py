"""
BYOK 个人收款码支付 router。

Endpoints:
  POST  /api/byok/payment/orders                   — 创建订单(返二维码 URL + 提示)
  POST  /api/byok/payment/orders/{id}/submit_proof — 用户上传付款截图(multipart)
  GET   /api/byok/payment/orders/{id}              — 订单状态(前端轮询)
  GET   /api/byok/payment/orders                   — 用户全部订单列表
  GET   /api/byok/payment/config                   — 收款码 URL + 收款方姓名(展示用)

注:旧的 /api/byok/purchase 在 byok.py 内被废弃 — 走真支付流后,激活码不再直接给,
   必须经过订单 → 上传截图 → 自动审核流程。byok.py 的 purchase endpoint 暂保留
   作为"开发后门"(只对 founder 邮箱开放),便于本地测试。
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import JSONResponse

from app.config import settings
from app.deps import get_current_user, get_db
from app.models.byok import BYOK_MONTHLY_PRICE_CENTS
from app.models.byok_payment import (
    CreatePaymentOrderRequest,
    CreatePaymentOrderResponse,
    PaymentOrdersListResponse,
    PaymentOrderStatusResponse,
    SubmitProofResponse,
)
from app.models.user import User
from app.services import byok_payment_service

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# 收款码配置展示
# ============================================================

@router.get("/byok/payment/config")
def api_payment_config():
    """返回平台收款码 URL + 收款方姓名(前端显示在支付指引里)。

    不要求鉴权 — 收款码本身公开。
    """
    qrcodes_dir = settings.uploads_abs_dir.parent / "payment_qrcodes"

    def _exists_url(filename: str) -> str | None:
        if not filename:
            return None
        p = qrcodes_dir / filename
        if p.exists():
            return f"/api/payment-qrcodes/{filename}"
        return None

    return {
        "payee_name": settings.byok_payee_display_name,   # 展示名(对外只露品牌名)
        "wechat_qr_url": _exists_url(settings.byok_wechat_qr_filename),
        "alipay_qr_url": _exists_url(settings.byok_alipay_qr_filename),
        "monthly_price_yuan": BYOK_MONTHLY_PRICE_CENTS // 100,   # v5:¥5(从常量派生,勿写死)
    }


# ============================================================
# 创建订单
# ============================================================

@router.post("/byok/payment/orders", response_model=CreatePaymentOrderResponse)
def api_create_payment_order(
    req: CreatePaymentOrderRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> CreatePaymentOrderResponse:
    order = byok_payment_service.create_order(conn, user.id, months=req.months)

    qrcodes_dir = settings.uploads_abs_dir.parent / "payment_qrcodes"
    wechat_qr_url = (
        f"/api/payment-qrcodes/{settings.byok_wechat_qr_filename}"
        if (qrcodes_dir / settings.byok_wechat_qr_filename).exists()
        else ""
    )
    alipay_qr_url = (
        f"/api/payment-qrcodes/{settings.byok_alipay_qr_filename}"
        if settings.byok_alipay_qr_filename
        and (qrcodes_dir / settings.byok_alipay_qr_filename).exists()
        else None
    )

    amount_display = f"¥{order.amount_cents / 100:.2f}"
    instruction = (
        f"1. 扫描下方二维码,使用微信向【{settings.byok_payee_display_name}】转账 {amount_display}\n"
        f"2. 转账完成后,截图付款成功页(含金额 / 收款方 / 时间 / 单号)\n"
        f"3. 在下方上传截图,系统自动识别并审核\n"
        f"4. 审核通常 30 秒内完成,通过后激活码自动填入解锁框\n"
        f"⚠ 请在 24 小时内完成付款 + 上传截图,否则订单自动失效。"
    )

    return CreatePaymentOrderResponse(
        order_id=order.id,
        amount_cents=order.amount_cents,
        amount_display=amount_display,
        months=order.months,
        payee_name=settings.byok_payee_display_name,   # 展示名(对外只露品牌名)
        wechat_qr_url=wechat_qr_url,
        alipay_qr_url=alipay_qr_url,
        expires_at=order.expires_at,
        instruction_text=instruction,
    )


# ============================================================
# 上传付款截图
# ============================================================

@router.post(
    "/byok/payment/orders/{order_id}/submit_proof",
    response_model=SubmitProofResponse,
)
async def api_submit_proof(
    order_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> SubmitProofResponse:
    """用户上传付款截图(multipart/form-data)。

    后端:
    1. 保存文件 + 算 SHA-256 防重放
    2. 状态切 'submitted'
    3. 启后台 Vision LLM 审核线程
    4. 立即返回("已收到,审核中,请等待")
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "FILE_REQUIRED", "message": "请选择付款截图"},
        )

    # 限制 MIME
    allowed = {"image/png", "image/jpeg", "image/jpg", "image/webp"}
    if file.content_type and file.content_type.lower() not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "FILE_TYPE_INVALID", "message": "只支持 png / jpg / webp"},
        )

    image_bytes = await file.read()
    # 推断扩展名
    ext_map = {
        "image/png": ".png", "image/jpeg": ".jpg",
        "image/jpg": ".jpg", "image/webp": ".webp",
    }
    file_ext = ext_map.get(
        (file.content_type or "image/png").lower(),
        Path(file.filename).suffix or ".png",
    )

    try:
        order = byok_payment_service.submit_proof(
            conn, user.id, order_id, image_bytes, file_ext=file_ext,
        )
    except byok_payment_service.SubmitProofError as exc:
        # 翻译业务异常 → HTTP
        if exc.code == "ORDER_NOT_FOUND":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": exc.code, "message": exc.message},
            )
        if exc.code == "ORDER_FORBIDDEN":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": exc.code, "message": exc.message},
            )
        if exc.code == "PROOF_REUSED":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": exc.code, "message": exc.message},
            )
        if exc.code == "ORDER_EXPIRED":
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={"code": exc.code, "message": exc.message},
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message},
        )

    return SubmitProofResponse(
        order_id=order.id,
        status=order.status,
        message="已收到截图,系统正在自动识别 + 审核,通常 30 秒内完成",
    )


# ============================================================
# 订单状态查询
# ============================================================

@router.get(
    "/byok/payment/orders/{order_id}",
    response_model=PaymentOrderStatusResponse,
)
def api_get_order_status(
    order_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> PaymentOrderStatusResponse:
    resp = byok_payment_service.get_order_status_response(conn, user.id, order_id)
    if resp is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "ORDER_NOT_FOUND", "message": "订单不存在或不属于当前账号"},
        )
    return resp


@router.get(
    "/byok/payment/orders",
    response_model=PaymentOrdersListResponse,
)
def api_list_orders(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> PaymentOrdersListResponse:
    orders = byok_payment_service.list_orders(conn, user.id)
    return PaymentOrdersListResponse(orders=orders)
