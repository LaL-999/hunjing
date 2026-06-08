"""Quota 路由 — Sprint C.1 重构(2026-05-13);Sprint 5.B(2026-05-18)加 comics_per_month。

GET /api/quota → 返回当前用户的"配额全景":
  - credit balance(订阅 / 加购 wallet)— Sprint C.1 核心
  - 非 AI 类硬限:characters_per_project / projects_total / reshape_max_percent
  - **comics_per_month**(Sprint 5.B)— 漫画态独立次数池配额(不走 credit)
  - plan 信息 + 月度发放数

URL 保留 /api/quota(前端 useQuota 仍用),实际返回结构改为 credit 模式。
原 *_used_this_month / *_per_month 字段彻底删除;Sprint 5.B 仅恢复 comics_per_month。
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends

from app.deps import get_current_user, get_db
from app.models.user import User
from app.services.credit_service import get_balance
from app.services.quota_service import (
    _count_characters,   # 仍可用于"我有多少角色"
    _count_projects,
    _count_user_comics_this_month,
    get_plan_limits_for_user,
)

router = APIRouter()


@router.get("")
def api_get_quota(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """返回当前用户的 credit balance + 非 AI 硬限状态 + 漫画次数池(5.B)。

    返回结构(对齐 frontend types):
      {
        "plan": "free|pro|max|super_max|founder",
        "limits": {
          "monthly_credits_quota": int,
          "single_credit_price_cents": int,
          "characters_per_project": int,
          "projects_total": int,
          "reshape_max_percent": int,
          "comics_per_month": int            # Sprint 5.B 新增
        },
        "credit_balance": {
          "subscription_credits": int,   月订阅 wallet(月末清零)
          "addon_credits": int,          加购 wallet(1 年有效)
          "total_credits": int,
          "month_start": str
        },
        "usage": {
          "projects_total": int,                  当前项目数
          "comics_used_this_month": int           Sprint 5.B 本月已占用漫画次数
        }
      }
    """
    limits = get_plan_limits_for_user(conn, user.id, user.plan)
    balance = get_balance(conn, user.id)

    return {
        "plan": user.plan,
        "limits": {
            "monthly_credits_quota": limits.monthly_credits_quota,
            "single_credit_price_cents": limits.single_credit_price_cents,
            "characters_per_project": limits.characters_per_project,
            "projects_total": limits.projects_total,
            "reshape_max_percent": limits.reshape_max_percent,
            "comics_per_month": limits.comics_per_month,        # Sprint 5.B
        },
        "credit_balance": balance.to_dict(),
        "usage": {
            "projects_total": _count_projects(conn, user.id),
            "comics_used_this_month": _count_user_comics_this_month(conn, user.id),
        },
    }
