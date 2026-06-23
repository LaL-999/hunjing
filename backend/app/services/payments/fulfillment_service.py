"""履约路由(fulfillment)— 商业化重塑第一期(2026-06-09)。

四层解耦的第 ④ 层:订单被确认支付(status → paid)后,按 SKU.category
把"发货"分发到对应的权益系统。三个现有"购买"函数(注释都写着"MVP 模拟
支付成功后调用")在这里成为真正的履约动作:

  subscription → billing_service.subscribe(plan, cycle)      → user_plan_snapshots
  byok         → byok_service.purchase_subscription(months)  → byok_subscriptions
  credit       → credit_service.purchase_addon(package_size) → addon_credit_lots

幂等铁律:
  订单已 fulfilled(fulfilled_at 非空)→ 直接返回,绝不二次履约。
  这是 money-handling 最关键的防线 —— 审核员手抖点两次"通过"不能发两次货。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from app.db import fetch_one, execute
from app.services import billing_service, byok_service, credit_service
from app.services.payments import catalog

logger = logging.getLogger(__name__)


class FulfillmentError(Exception):
    """履约失败(SKU 非法 / 权益系统拒绝 / 已履约重复调用等)。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fulfill_order(conn: sqlite3.Connection, order_id: str) -> dict[str, Any]:
    """对一个已支付订单执行履约。

    前置:调用方已把 order.status 置为 'paid'(或本函数在审核通过链路里被调)。
    本函数只认 order 行的真实状态,不信任入参。

    Returns:
        { "fulfilled": bool, "ref": str|None, "skipped": bool, "reason": str }

    Raises:
        FulfillmentError: SKU 解析失败 / 权益系统抛错
    """
    row = fetch_one(
        conn,
        "SELECT id, user_id, sku_code, category, quantity, status, "
        "fulfilled_at, sku_meta_json FROM payment_orders WHERE id = ?",
        (order_id,),
    )
    if row is None:
        raise FulfillmentError(f"订单不存在: {order_id}")

    # ===== 幂等防线 1:已履约 → 跳过 =====
    if row["fulfilled_at"]:
        logger.info("订单 %s 已履约(%s),跳过", order_id, row["fulfilled_at"])
        return {
            "fulfilled": True, "skipped": True,
            "ref": None, "reason": "already_fulfilled",
        }

    # ===== 幂等防线 2:必须是 paid 状态才履约 =====
    if row["status"] != "paid":
        raise FulfillmentError(
            f"订单 {order_id} 状态为 {row['status']},非 paid 不可履约"
        )

    user_id = row["user_id"]
    category = row["category"]
    try:
        meta = json.loads(row["sku_meta_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        meta = {}

    # ===== 按 category 路由到现有权益系统 =====
    ref: str | None = None
    fulfillment_meta: dict[str, Any] = {}

    if category == catalog.CATEGORY_SUBSCRIPTION:
        plan = meta.get("plan")
        cycle = meta.get("billing_cycle")
        if not plan or not cycle:
            raise FulfillmentError(f"订阅订单缺 plan/billing_cycle: {order_id}")
        try:
            snapshot = billing_service.subscribe(
                conn, user_id, plan, cycle,
                notes=f"order:{order_id}",
            )
        except billing_service.AlreadyHasActiveSnapshot as exc:
            # 已有 active 订阅 —— 升级/续费应走 upgrade 链路,这里明确拒绝
            raise FulfillmentError(
                f"用户已有生效订阅,无法直接履约新订阅(应走升级流程): {exc}"
            ) from exc
        ref = snapshot.id
        fulfillment_meta = {"plan": plan, "billing_cycle": cycle}

    elif category == catalog.CATEGORY_BYOK:
        months = int(meta.get("months") or row["quantity"] or 1)
        sub = byok_service.purchase_subscription(conn, user_id, months=months)
        ref = sub.id
        # 把生成的激活码带回(前端可展示给用户)
        fulfillment_meta = {"months": months, "code": getattr(sub, "code", None)}

    elif category == catalog.CATEGORY_CREDIT:
        package_size = meta.get("package_size")
        if not package_size:
            raise FulfillmentError(f"配额订单缺 package_size: {order_id}")
        credit_service.purchase_addon(conn, user_id, package_size)
        ref = f"credit:{package_size}"
        fulfillment_meta = {"package_size": package_size}

    else:
        raise FulfillmentError(f"未知 category: {category}(订单 {order_id})")

    # ===== 回写履约结果(幂等关键:fulfilled_at 一旦写入,再调即跳过)=====
    execute(
        conn,
        "UPDATE payment_orders SET fulfilled_at = ?, fulfillment_ref = ?, "
        "fulfillment_meta_json = ? WHERE id = ? AND fulfilled_at IS NULL",
        (_now_iso(), ref, json.dumps(fulfillment_meta, ensure_ascii=False), order_id),
    )
    conn.commit()
    logger.info("订单 %s 履约成功 → %s(ref=%s)", order_id, category, ref)
    return {"fulfilled": True, "skipped": False, "ref": ref, "reason": "ok"}


__all__ = ["fulfill_order", "FulfillmentError"]
