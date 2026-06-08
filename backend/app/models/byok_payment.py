"""
BYOK 个人收款码支付 — dataclass + Pydantic schemas

订单生命周期:
  pending      创建,等用户付款 + 上传截图
    ↓ submit_proof
  submitted    截图已收,正在 Vision LLM 审核
    ↓
  paid         审核通过 + BYOK 订阅已激活
  rejected     审核拒(截图不符 / 金额不对 / 重复提交等)
  manual_review 自动审核犹豫 / 出错,需人工审
  expired      24h 内未提交截图,自动失效
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field


# 订单有效期(创建后多少秒内必须上传截图)
ORDER_VALIDITY_SECONDS = 24 * 60 * 60  # 24h

# 校验时间窗(付款时间允许在订单创建后多久内,容许少量时钟漂移)
PAY_TIME_WINDOW_SECONDS = 24 * 60 * 60  # 24h

# 金额误差容忍(分)
AMOUNT_TOLERANCE_CENTS = 0  # 严格 0 容忍

# 订单状态字面量
ORDER_STATUS = Literal[
    "pending",
    "submitted",
    "paid",
    "rejected",
    "manual_review",
    "expired",
]


@dataclass
class BYOKPaymentOrder:
    id: str
    user_id: str
    amount_cents: int
    months: int
    status: str

    proof_image_path: Optional[str]
    proof_image_hash: Optional[str]
    proof_submitted_at: Optional[str]

    detected_amount_cents: Optional[int]
    detected_payee_name: Optional[str]
    detected_pay_time: Optional[str]
    detected_transaction_id: Optional[str]
    detection_raw_json: Optional[str]
    detection_run_at: Optional[str]
    detection_pass_reason: Optional[str]

    activated_subscription_id: Optional[str]

    rejected_reason: Optional[str]

    created_at: str
    expires_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "BYOKPaymentOrder":
        def _safe(key: str, default=None):
            try:
                return row[key]
            except (KeyError, IndexError):
                return default

        return cls(
            id=row["id"],
            user_id=row["user_id"],
            amount_cents=int(row["amount_cents"]),
            months=int(_safe("months", 1) or 1),
            status=row["status"],
            proof_image_path=_safe("proof_image_path"),
            proof_image_hash=_safe("proof_image_hash"),
            proof_submitted_at=_safe("proof_submitted_at"),
            detected_amount_cents=(
                int(_safe("detected_amount_cents") or 0) or None
            ),
            detected_payee_name=_safe("detected_payee_name"),
            detected_pay_time=_safe("detected_pay_time"),
            detected_transaction_id=_safe("detected_transaction_id"),
            detection_raw_json=_safe("detection_raw_json"),
            detection_run_at=_safe("detection_run_at"),
            detection_pass_reason=_safe("detection_pass_reason"),
            activated_subscription_id=_safe("activated_subscription_id"),
            rejected_reason=_safe("rejected_reason"),
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )


# ============================================================
# Pydantic — Request / Response
# ============================================================

class CreatePaymentOrderRequest(BaseModel):
    months: int = Field(default=1, ge=1, le=12)


class CreatePaymentOrderResponse(BaseModel):
    order_id: str
    amount_cents: int
    amount_display: str          # "¥30.00"
    months: int
    payee_name: str              # 收款方姓名(展示在指引里)
    wechat_qr_url: str           # 平台微信收款码图片 URL(/api/payment-qrcodes/wechat_qr.png)
    alipay_qr_url: Optional[str] # 支付宝(若有)
    expires_at: str
    instruction_text: str        # 给用户看的步骤说明


class PaymentOrderStatusResponse(BaseModel):
    """订单状态(前端轮询用)"""
    order_id: str
    status: str                          # pending / submitted / paid / ...
    amount_cents: int
    months: int
    created_at: str
    expires_at: str
    proof_submitted_at: Optional[str]

    # 审核详情(若已审核)
    detected_amount_cents: Optional[int] = None
    detected_payee_name: Optional[str] = None
    detected_pay_time: Optional[str] = None
    detection_pass_reason: Optional[str] = None
    rejected_reason: Optional[str] = None

    # 已通过 → 关联的 BYOK 订阅
    activated_code: Optional[str] = None
    activated_expires_at: Optional[str] = None


class PaymentOrdersListResponse(BaseModel):
    orders: list[PaymentOrderStatusResponse]


class SubmitProofResponse(BaseModel):
    order_id: str
    status: str
    message: str           # "已收到截图,正在自动审核..."


# ============================================================
# Admin 后台审核 schemas(2026-06-05)
# ============================================================

class AdminPaymentOrderResponse(BaseModel):
    """Admin 视角 — 比用户多看 user_email + proof_image_url"""
    order_id: str
    user_id: str
    user_email: Optional[str] = None     # 给 admin 看是谁付的款
    status: str
    amount_cents: int
    months: int
    created_at: str
    expires_at: str
    proof_submitted_at: Optional[str] = None
    proof_image_url: Optional[str] = None    # /api/admin/byok/orders/{id}/proof_image

    detected_amount_cents: Optional[int] = None
    detected_payee_name: Optional[str] = None
    detected_pay_time: Optional[str] = None
    detected_transaction_id: Optional[str] = None
    detection_pass_reason: Optional[str] = None
    detection_run_at: Optional[str] = None

    rejected_reason: Optional[str] = None
    activated_subscription_id: Optional[str] = None
    activated_code: Optional[str] = None
    activated_expires_at: Optional[str] = None


class AdminPaymentOrdersListResponse(BaseModel):
    orders: list[AdminPaymentOrderResponse]
    total: int
    pending_review_count: int      # status='manual_review' 的总数(头部 badge 用)


class AdminApproveRequest(BaseModel):
    note: Optional[str] = None             # 可选审计备注(写到 notes)


class AdminRejectRequest(BaseModel):
    reason: str                            # 必填 — 给用户看的拒绝原因
