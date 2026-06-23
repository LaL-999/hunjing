"""统一订单服务(order lifecycle)— 商业化重塑第一期(2026-06-09)。

四层解耦的第 ② 层。所有付费场景(订阅 / BYOK / 配额)统一走这套订单生命周期:

  create_order(user, sku_code)         → pending(显示扫码二维码)
  submit_proof(user, order, image)     → submitted(用户传付款截图)
  [管理员在洞察后台]
  approve_order(order, reviewer)       → paid → 调 fulfillment 发货
  reject_order(order, reviewer, why)   → rejected

用户拍板(2026-06-09):个人主体扫码 + 截图 + 人工复核(Vision LLM 仅辅助预填,
终审在人)。所以本服务默认 submit_proof 后落 'submitted'(待人工审),不自动放行。

user 隔离:所有用户侧读写校验 user_id;管理员侧函数标注 admin-only,由 router
的 admin 鉴权把关。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from app.config import settings
from app.db import fetch_one, fetch_all, execute
from app.services.payments import catalog
from app.services.payments import fulfillment_service

logger = logging.getLogger(__name__)

# 待付订单有效期(小时)
ORDER_TTL_HOURS = 24
# 截图大小限制
_MAX_PROOF_BYTES = 10 * 1024 * 1024
_MIN_PROOF_BYTES = 1024


class OrderError(Exception):
    """订单操作失败(SKU 非法 / 越权 / 状态非法 / 截图问题等)。"""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _make_order_id() -> str:
    return "ORD-" + uuid.uuid4().hex[:12].upper()


def _proofs_dir() -> Path:
    p = settings.uploads_abs_dir.parent / "payment_proofs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    # 解析 JSON 字段
    for k in ("sku_meta_json", "fulfillment_meta_json"):
        if d.get(k):
            try:
                d[k.replace("_json", "")] = json.loads(d[k])
            except (json.JSONDecodeError, TypeError):
                d[k.replace("_json", "")] = {}
        d.pop(k, None)
    d["amount_yuan"] = round((d.get("amount_cents") or 0) / 100, 2)
    return d


# ============================================================
# 创建订单
# ============================================================

def create_order(
    conn: sqlite3.Connection,
    user_id: str,
    sku_code: str,
) -> dict[str, Any]:
    """下单 — 解析 SKU 冻结快照,生成 pending 订单。

    Raises:
        OrderError(INVALID_SKU): sku_code 无法解析
    """
    sku = catalog.get_sku(sku_code)
    if sku is None:
        raise OrderError("INVALID_SKU", f"未知商品: {sku_code}")

    order_id = _make_order_id()
    now = _now()
    expires = now + timedelta(hours=ORDER_TTL_HOURS)

    execute(
        conn,
        """INSERT INTO payment_orders
           (id, user_id, sku_code, sku_title, category, amount_cents, quantity,
            sku_meta_json, channel, status, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'wechat_qr_manual', 'pending', ?, ?)""",
        (
            order_id, user_id, sku.code, sku.title, sku.category,
            sku.amount_cents, sku.quantity,
            json.dumps(sku.meta, ensure_ascii=False),
            _iso(now), _iso(expires),
        ),
    )
    conn.commit()
    logger.info("订单创建 %s user=%s sku=%s ¥%.2f",
                order_id, user_id, sku.code, sku.amount_cents / 100)
    return get_order(conn, order_id, user_id)


# ============================================================
# 查询
# ============================================================

def get_order(
    conn: sqlite3.Connection,
    order_id: str,
    user_id: str,
) -> dict[str, Any]:
    """拿单个订单(校验归属当前用户)。"""
    row = fetch_one(
        conn,
        "SELECT * FROM payment_orders WHERE id = ? AND user_id = ?",
        (order_id, user_id),
    )
    if row is None:
        raise OrderError("NOT_FOUND", "订单不存在或无权访问")
    return _row_to_dict(row)


def list_my_orders(
    conn: sqlite3.Connection,
    user_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """当前用户的订单列表,倒序。"""
    rows = fetch_all(
        conn,
        "SELECT * FROM payment_orders WHERE user_id = ? "
        "ORDER BY created_at DESC LIMIT ?",
        (user_id, max(1, min(limit, 200))),
    )
    return [_row_to_dict(r) for r in rows]


# ============================================================
# 提交付款凭证
# ============================================================

def submit_proof(
    conn: sqlite3.Connection,
    user_id: str,
    order_id: str,
    image_bytes: bytes,
    ext: str = ".png",
) -> dict[str, Any]:
    """用户上传付款截图 → 落盘 + 算 hash 去重 → status='submitted' 待人工审。

    Raises:
        OrderError: 越权 / 状态非法 / 截图问题 / 重复截图
    """
    # 校验订单归属 + 状态
    row = fetch_one(
        conn,
        "SELECT id, status FROM payment_orders WHERE id = ? AND user_id = ?",
        (order_id, user_id),
    )
    if row is None:
        raise OrderError("NOT_FOUND", "订单不存在或无权访问")
    if row["status"] not in ("pending", "submitted", "rejected", "manual_review"):
        raise OrderError("INVALID_STATE", f"订单状态 {row['status']} 不可再提交凭证")

    # 截图校验
    if not image_bytes or len(image_bytes) < _MIN_PROOF_BYTES:
        raise OrderError("PROOF_INVALID", "截图文件为空 / 太小")
    if len(image_bytes) > _MAX_PROOF_BYTES:
        raise OrderError("PROOF_TOO_LARGE", "截图超过 10 MB")

    proof_hash = hashlib.sha256(image_bytes).hexdigest()
    # 防同图重复提交(别的订单已用过)
    dup = fetch_one(
        conn,
        "SELECT id FROM payment_orders WHERE proof_image_hash = ? AND id != ?",
        (proof_hash, order_id),
    )
    if dup is not None:
        raise OrderError("PROOF_REUSED", "这张截图已被用于其它订单,请勿重复使用")

    # 落盘
    safe_ext = ext if ext.startswith(".") else f".{ext}"
    user_dir = _proofs_dir() / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    abs_path = user_dir / f"{order_id}{safe_ext}"
    abs_path.write_bytes(image_bytes)
    rel_path = f"payment_proofs/{user_id}/{order_id}{safe_ext}"

    now_iso = _iso(_now())
    execute(
        conn,
        "UPDATE payment_orders SET status='submitted', proof_image_path=?, "
        "proof_image_hash=?, proof_submitted_at=? WHERE id=?",
        (rel_path, proof_hash, now_iso, order_id),
    )
    conn.commit()
    logger.info("订单 %s 凭证已提交(hash %s),待人工审核", order_id, proof_hash[:16])
    return get_order(conn, order_id, user_id)


# ============================================================
# 管理员审核(admin-only — 由 router 的 admin 鉴权把关)
# ============================================================

def admin_list_orders(
    conn: sqlite3.Connection,
    status: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """后台列订单。status 缺省列待审(submitted + manual_review)。"""
    if status:
        rows = fetch_all(
            conn,
            "SELECT * FROM payment_orders WHERE status = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (status, max(1, min(limit, 500))),
        )
    else:
        rows = fetch_all(
            conn,
            "SELECT * FROM payment_orders "
            "WHERE status IN ('submitted', 'manual_review') "
            "ORDER BY created_at ASC LIMIT ?",
            (max(1, min(limit, 500)),),
        )
    return [_row_to_dict(r) for r in rows]


def approve_order(
    conn: sqlite3.Connection,
    order_id: str,
    reviewer: str,
) -> dict[str, Any]:
    """审核通过 → status=paid → 调履约发货。幂等:已 paid/已履约的重复审不重复发货。

    Raises:
        OrderError / fulfillment_service.FulfillmentError
    """
    row = fetch_one(
        conn,
        "SELECT id, status, user_id, fulfilled_at FROM payment_orders WHERE id = ?",
        (order_id,),
    )
    if row is None:
        raise OrderError("NOT_FOUND", "订单不存在")
    if row["status"] not in ("submitted", "manual_review", "pending", "paid"):
        raise OrderError("INVALID_STATE", f"订单状态 {row['status']} 不可审核通过")

    # 置 paid(若还不是)
    if row["status"] != "paid":
        execute(
            conn,
            "UPDATE payment_orders SET status='paid', reviewed_by=?, reviewed_at=? "
            "WHERE id=?",
            (reviewer, _iso(_now()), order_id),
        )
        conn.commit()

    # 履约(内部幂等:fulfilled_at 非空直接跳过)
    result = fulfillment_service.fulfill_order(conn, order_id)
    user_id = row["user_id"]
    out = get_order(conn, order_id, user_id)
    out["fulfillment"] = result
    return out


def reject_order(
    conn: sqlite3.Connection,
    order_id: str,
    reviewer: str,
    reason: str,
) -> dict[str, Any]:
    """审核驳回。已履约订单不可驳回(防发货后反悔造成权益不一致)。"""
    row = fetch_one(
        conn,
        "SELECT id, status, user_id, fulfilled_at FROM payment_orders WHERE id = ?",
        (order_id,),
    )
    if row is None:
        raise OrderError("NOT_FOUND", "订单不存在")
    if row["fulfilled_at"]:
        raise OrderError("ALREADY_FULFILLED", "订单已发货,不可驳回(如需退款另走退款流程)")
    execute(
        conn,
        "UPDATE payment_orders SET status='rejected', reviewed_by=?, reviewed_at=?, "
        "rejected_reason=? WHERE id=?",
        (reviewer, _iso(_now()), (reason or "").strip()[:200], order_id),
    )
    conn.commit()
    return get_order(conn, order_id, row["user_id"])


__all__ = [
    "OrderError",
    "create_order", "get_order", "list_my_orders", "submit_proof",
    "admin_list_orders", "approve_order", "reject_order",
    "ORDER_TTL_HOURS",
]
