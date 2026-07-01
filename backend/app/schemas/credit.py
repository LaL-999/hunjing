"""Credit API schema — Sprint C.1 credit 重构(2026-05-13)。

API 端点:
  POST /api/credit/addon/purchase    加购包购买(Sprint C.5 接真支付前 mock 模拟成功)
  GET  /api/credit/transactions      用户 credit 交易明细(Sprint C.2 接通后用户查"我花了哪些 credit")
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


AddonPackageSize = Literal["small", "medium", "large"]


class AddonPurchaseRequest(BaseModel):
    """加购包购买请求(当前 mock,Sprint C.5 接 webhook 后 deprecate 此端点 → 改 Stripe / 微信 / 支付宝)。

    定价见 credit_service.ADDON_PACKAGES(v5 2026-07-02):
      small  → 100 c / ¥10
      medium → 500 c / ¥42
      large  → 2000 c / ¥160
    """
    package_size: AddonPackageSize = Field(
        ...,
        description="加购包规格:small=100c/¥10 / medium=500c/¥42 / large=2000c/¥160",
    )
    notes: Optional[str] = Field(
        default=None, max_length=200,
        description="内部备注(支付通道接入后存交易号)",
    )


class AddonPurchaseResponse(BaseModel):
    """加购成功后返回当前 credit balance + 本次发放数。"""
    purchased_credits: int          # 本次发放的 credit 数
    package_size: AddonPackageSize
    price_cents: int                # 实付金额(分)
    expires_at: str                 # 加购 lot 过期时刻(1 年后)

    # 用户当前余额快照
    credit_balance: dict            # CreditBalance.to_dict() 结构


class CreditTransactionResponse(BaseModel):
    """单条 credit 变更明细(给"我的消费记录"页用)。"""
    id: str
    delta: int
    wallet: str                     # subscription / addon
    kind: str                       # subscribe_grant / addon_purchase / consume / refund / ...
    action: Optional[str]           # refine / continuation / extract / comic_* / ...
    related_id: Optional[str]
    cost_yuan: float
    metadata: Optional[dict]
    created_at: str
