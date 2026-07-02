"""Credit 路由 — Sprint C.1(2026-05-13)credit 重构。

端点:
  POST /api/credit/addon/purchase    加购包购买(Sprint C.5 接真支付前 mock)
  GET  /api/credit/transactions      用户 credit 交易明细(分页可选,默认本月)

历史:加购此前由前端 QuotaIndicator 直接弹 UpgradeModal 占位,Sprint C.1 用户报告
"点加购弹的是订阅列表,不对"。本路由 + AddonPurchaseModal 是正确的加购入口。
"""
from __future__ import annotations

import json
import sqlite3
import traceback

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.db import fetch_all
from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.credit import (
    AddonPurchaseRequest,
    AddonPurchaseResponse,
    CreditTransactionResponse,
)
from app.services.credit_service import (
    ADDON_PACKAGES,
    get_balance,
    purchase_addon,
)
from app.services.quota_service import month_start_iso

router = APIRouter()


# ============================================================
# 加购包购买
# ============================================================

@router.post(
    "/credit/addon/purchase",
    response_model=AddonPurchaseResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_purchase_addon(
    req: AddonPurchaseRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """加购包购买。

    Sprint C.1 占位:**模拟支付成功**,直接发 credit + 写 transaction + 落 addon_credit_lots。
    Sprint C.5 接通真支付通道(Stripe / 微信 / 支付宝)后,本端点改为只接 webhook 调用,
    用户前端 → 跳支付页 → 支付完 webhook → 调本端点发 credit。
    """
    try:
        balance = purchase_addon(conn, user.id, req.package_size)
    except ValueError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_ADDON_PACKAGE", "message": str(e)},
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"加购失败:{type(e).__name__}: {e}"[:300],
            },
        )

    credits, price_cents = ADDON_PACKAGES[req.package_size]

    # 拉刚落库的 lot 拿 expires_at(简单做法:purchase_addon 内部已 insert,这里 fetch_one 拿最新)
    from app.db import fetch_one
    lot_row = fetch_one(
        conn,
        """SELECT expires_at FROM addon_credit_lots
           WHERE user_id=? AND package_size=?
           ORDER BY purchased_at DESC LIMIT 1""",
        (user.id, req.package_size),
    )
    expires_at = lot_row["expires_at"] if lot_row else ""

    return {
        "purchased_credits": credits,
        "package_size": req.package_size,
        "price_cents": price_cents,
        "expires_at": expires_at,
        "credit_balance": balance.to_dict(),
    }


# ============================================================
# Credit 交易明细
# ============================================================

@router.get(
    "/credit/transactions",
    response_model=list[CreditTransactionResponse],
)
def api_list_transactions(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
    this_month_only: bool = Query(
        True, description="仅查本月(默认 true);false 查最近 50 条",
    ),
    limit: int = Query(50, ge=1, le=200),
) -> list[dict]:
    """查用户 credit 交易明细 — 给「我的消费记录」页用。

    默认本月所有 transaction;若 this_month_only=false,返回最近 50 条。
    """
    if this_month_only:
        rows = fetch_all(
            conn,
            """SELECT * FROM credit_transactions
               WHERE user_id=? AND created_at > ?
               ORDER BY created_at DESC LIMIT ?""",
            (user.id, month_start_iso(), limit),
        )
    else:
        rows = fetch_all(
            conn,
            """SELECT * FROM credit_transactions
               WHERE user_id=?
               ORDER BY created_at DESC LIMIT ?""",
            (user.id, limit),
        )

    result: list[dict] = []
    for r in rows:
        metadata = None
        if r["metadata"]:
            try:
                metadata = json.loads(r["metadata"])
            except json.JSONDecodeError:
                metadata = None
        result.append({
            "id": r["id"],
            "delta": int(r["delta"]),
            "wallet": r["wallet"],
            "kind": r["kind"],
            "action": r["action"],
            "related_id": r["related_id"],
            "cost_yuan": float(r["cost_yuan"]),
            "metadata": metadata,
            "created_at": r["created_at"],
        })
    return result


# ============================================================
# 当前余额(可选 — 已通过 /api/quota 拿,这里作 alias 方便前端单独查)
# ============================================================

@router.get("/credit/balance")
def api_get_balance(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """单独查 credit balance(不查 plan limits)— 加购成功后前端可单独刷新。"""
    return get_balance(conn, user.id).to_dict()


# ============================================================
# Sprint C.5:Admin — 手动触发 cron(仅 founder)
# ============================================================

@router.post("/credit/admin/run_cron")
def api_admin_run_cron(
    user: User = Depends(get_current_user),
) -> dict:
    """手动触发每日 credit cron 任务 — 仅 founder 可用。

    用途:
      - 运维 / 排障:线上 cron 漏跑时手动补发
      - 测试:验证 cron 逻辑不用等明天

    检测 founder:deps.get_current_user 已 runtime promote 命中 founder_emails
    的用户为 plan='founder'。
    """
    if user.plan != "founder":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "FORBIDDEN", "message": "仅 founder 可手动触发 cron"},
        )

    from app.services.credit_cron import run_daily_credit_jobs
    try:
        report = run_daily_credit_jobs()
        return {"ok": True, "report": report}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "CRON_FAILED",
                "message": f"{type(e).__name__}: {e}"[:300],
            },
        )
