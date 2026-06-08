"""订阅 / 价格快照路由 — Sprint E.4。

POST /api/billing/subscribe   订阅(创建 active snapshot)
POST /api/billing/cancel      取消(state='cancelled')
POST /api/billing/upgrade     升级 / 改档(老 snapshot 'upgraded' + 新 active)
GET  /api/billing/snapshot    拿当前 active / cancelled-still-valid 快照(无 → 204)
GET  /api/billing/history     拿用户所有快照历史

**支付通道未接入**:当前 subscribe / upgrade 是 mock 触发(测试 / 内部联调),
真支付通道接入后(Stripe / 微信 / 支付宝 webhook),由 webhook handler 调
billing_service.subscribe(user_id, plan, cycle)。
"""
from __future__ import annotations

import sqlite3
import traceback

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.billing import (
    PlanSnapshotResponse,
    SnapshotHistoryResponse,
    SubscribeRequest,
    UpgradeRequest,
)
from app.services.billing_service import (
    AlreadyHasActiveSnapshot,
    InvalidSubscribePlan,
    NoActiveSnapshot,
    cancel,
    fetch_active_or_pending_for_user,
    fetch_snapshot_history,
    subscribe,
    upgrade,
)


router = APIRouter()


@router.post(
    "/billing/subscribe",
    response_model=PlanSnapshotResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_subscribe(
    req: SubscribeRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """订阅(创建 active snapshot)。

    异常:
      400 INVALID_PLAN          plan / cycle 非法
      409 ALREADY_HAS_ACTIVE    已有 active(必须先 cancel 或 upgrade)
      500 INTERNAL_ERROR
    """
    try:
        snapshot = subscribe(conn, user.id, req.plan, req.billing_cycle, req.notes)
        return snapshot.to_response()
    except InvalidSubscribePlan as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_PLAN", "message": str(e)},
        )
    except AlreadyHasActiveSnapshot as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "ALREADY_HAS_ACTIVE", "message": str(e)},
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"subscribe 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )


@router.post(
    "/billing/cancel",
    response_model=PlanSnapshotResponse,
)
def api_cancel(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """取消订阅(state='cancelled')。配额仍生效到 current_period_end。

    异常:
      404 NO_ACTIVE_SNAPSHOT    用户没有 active snapshot
    """
    try:
        snapshot = cancel(conn, user.id)
        return snapshot.to_response()
    except NoActiveSnapshot as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "NO_ACTIVE_SNAPSHOT", "message": str(e)},
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"cancel 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )


@router.post(
    "/billing/upgrade",
    response_model=PlanSnapshotResponse,
)
def api_upgrade(
    req: UpgradeRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """升级 / 改档(老 snapshot 'upgraded' + 新 active)。

    用户**明确同意新规则 + 新价格**;否则应走 cancel。

    异常:
      400 INVALID_PLAN
      404 NO_ACTIVE_SNAPSHOT
    """
    try:
        snapshot = upgrade(
            conn, user.id, req.plan, req.billing_cycle, req.notes,
        )
        return snapshot.to_response()
    except InvalidSubscribePlan as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_PLAN", "message": str(e)},
        )
    except NoActiveSnapshot as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "NO_ACTIVE_SNAPSHOT", "message": str(e)},
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"upgrade 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )


@router.get(
    "/billing/snapshot",
    response_model=PlanSnapshotResponse,
    responses={204: {"description": "用户无 active / cancelled-still-valid 快照"}},
)
def api_get_snapshot(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """拿用户当前 active 或 cancelled-still-valid 快照(无 → 204 No Content)。

    前端 QuotaIndicator / UpgradeModal 用此显示"我的订阅条款"+ 价格锁定时刻。
    """
    snapshot = fetch_active_or_pending_for_user(conn, user.id)
    if snapshot is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return snapshot.to_response()


@router.get(
    "/billing/history",
    response_model=SnapshotHistoryResponse,
)
def api_get_history(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """拿用户所有订阅快照(active + cancelled + expired + upgraded),最新在前。

    用户"我的订阅"页展示订阅历史 / 运营审计。
    """
    history = fetch_snapshot_history(conn, user.id)
    return {"snapshots": [s.to_response() for s in history]}
