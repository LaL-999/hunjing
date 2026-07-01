"""UserPlanSnapshot — 用户订阅快照(老用户老规则)。

Sprint E.4(2026-05-12)— 承载协议第三章"价格变更承诺":
    "如未来调价,老用户将以订阅时点的价格快照与配额规则继续服务,
     直至主动取消订阅或明确同意新规则。"

Sprint C.1(2026-05-13)— credit 重构后:
    冻结的字段改为 monthly_credits_quota / single_credit_price_cents +
    非 AI 类硬限(characters / projects / reshape)。
    原 *_per_month 次数字段已**彻底删除**(激进重构 — 无老用户拖累)。

状态机:
  active     正常订阅中 — 配额生效
  cancelled  用户主动取消 — 配额仍生效到 current_period_end
  expired    周期结束未续费 — 配额失效,用户回落到 free
  upgraded   用户主动同意新规则 — 老快照归档,同时创建新 snapshot active
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional


# 快照状态白名单(对齐 migration 022 v2 CHECK + service 校验)
VALID_SNAPSHOT_STATES = ("active", "cancelled", "expired", "upgraded")
VALID_BILLING_CYCLES = ("monthly", "yearly")
VALID_PAID_PLANS = ("pro", "max")   # v5:super_max 下架不可售;free / founder 不写快照


@dataclass
class UserPlanSnapshot:
    id: str
    user_id: str
    plan: str                       # pro / max / super_max
    billing_cycle: str              # monthly / yearly
    price_cents: int                # 订阅时点价格(单位:分)

    # ===== Credit 字段(Sprint C.1 新增,取代次数字段)=====
    monthly_credits_quota:    int   # 月度发放数(订阅 wallet)
    single_credit_price_cents: int  # 单 credit 售价(分,加购包参考用)

    # ===== 非 AI 类硬限(原字段保留)=====
    characters_per_project: int
    projects_total: int
    reshape_max_percent: int

    # 时刻轴
    grandfather_at: str             # 首次受老规则保护起算
    current_period_start: str
    current_period_end: str

    state: str                      # active / cancelled / expired / upgraded
    notes: Optional[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "UserPlanSnapshot":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            plan=row["plan"],
            billing_cycle=row["billing_cycle"],
            price_cents=int(row["price_cents"]),
            monthly_credits_quota=int(row["monthly_credits_quota"]),
            single_credit_price_cents=int(row["single_credit_price_cents"]),
            characters_per_project=int(row["characters_per_project"]),
            projects_total=int(row["projects_total"]),
            reshape_max_percent=int(row["reshape_max_percent"]),
            grandfather_at=row["grandfather_at"],
            current_period_start=row["current_period_start"],
            current_period_end=row["current_period_end"],
            state=row["state"],
            notes=row["notes"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_response(self) -> dict[str, Any]:
        """API 响应形态 — 前端用此显示"我当前的订阅条款 / 价格快照"。"""
        return {
            "id": self.id,
            "plan": self.plan,
            "billing_cycle": self.billing_cycle,
            "price_cents": self.price_cents,
            "price_yuan_fmt": f"{self.price_cents / 100:.2f}",
            "limits": {
                "monthly_credits_quota":    self.monthly_credits_quota,
                "single_credit_price_cents": self.single_credit_price_cents,
                "characters_per_project":   self.characters_per_project,
                "projects_total":           self.projects_total,
                "reshape_max_percent":      self.reshape_max_percent,
            },
            "grandfather_at": self.grandfather_at,
            "current_period_start": self.current_period_start,
            "current_period_end": self.current_period_end,
            "state": self.state,
            "notes": self.notes,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
