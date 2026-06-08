"""Billing / 订阅快照 API schema — Sprint E.4。

API 端点:
  POST /api/billing/subscribe   订阅(创建 active snapshot)— 当前未接支付,仅 mock
  POST /api/billing/cancel      取消(state='cancelled',配额生效到月末)
  POST /api/billing/upgrade     升级 / 改档(老快照 → upgraded,新快照 active)
  GET  /api/billing/snapshot    拿当前 active / cancelled-still-valid 快照
  GET  /api/billing/history     拿用户所有快照历史(运营审计 / 用户"我的订阅"页)
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


PaidPlan = Literal["pro", "max", "super_max"]
BillingCycle = Literal["monthly", "yearly"]
SnapshotState = Literal["active", "cancelled", "expired", "upgraded"]


class SubscribeRequest(BaseModel):
    plan: PaidPlan
    billing_cycle: BillingCycle
    notes: Optional[str] = Field(
        default=None, max_length=200,
        description="内部备注(支付通道接入后存 stripe charge id / 微信 trade_no 等)",
    )


class UpgradeRequest(BaseModel):
    """升级到新档 / 改 billing_cycle — 老快照归档,创建新 active 快照。

    与 SubscribeRequest 字段一样,但语义不同:
      subscribe = 用户首次付费(无 active 时)
      upgrade   = 用户已有 active,主动同意新规则
    """
    plan: PaidPlan
    billing_cycle: BillingCycle
    notes: Optional[str] = Field(default=None, max_length=200)


class PlanSnapshotResponse(BaseModel):
    """订阅快照详情(GET /api/billing/snapshot 返)。"""
    id: str
    plan: str
    billing_cycle: str
    # 冻结的价格(分 + 元格式化)
    price_cents: int
    price_yuan_fmt: str

    # 冻结的 7 项 PlanLimits
    limits: dict[str, int]

    # 时刻轴
    grandfather_at: str
    current_period_start: str
    current_period_end: str

    state: str
    notes: Optional[str] = None
    created_at: str
    updated_at: str


class SnapshotHistoryResponse(BaseModel):
    """订阅历史(GET /api/billing/history 返)。"""
    snapshots: list[PlanSnapshotResponse]
