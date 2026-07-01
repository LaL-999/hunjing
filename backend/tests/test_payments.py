"""统一支付内核测试(商业化重塑 P1,2026-06-09)。

聚焦 money-handling 关键路径:
  1. SKU 目录解析(3 类 + 未知)
  2. 订单生命周期 create → submit_proof → approve(履约)
  3. 履约幂等(approve 两次只发一次货)— 最关键防线
  4. 跨用户隔离(看不到别人订单)
  5. 已履约订单不可驳回
  6. 未知 SKU 拒绝下单
"""
from __future__ import annotations

import pytest

from app.db import get_connection
from app.services import credit_service
from app.services.payments import catalog, order_service, fulfillment_service


# ============================================================
# 1. SKU 目录
# ============================================================

def test_catalog_resolves_all_categories():
    # v5(2026-07-02):Pro¥68 / Max¥218 / 删超级Max / BYOK¥5·月 / 加购包 ¥10/42/160
    assert catalog.get_sku("sub_pro_monthly").amount_cents == 6800
    assert catalog.get_sku("sub_max_yearly").amount_cents == 222360
    assert catalog.get_sku("sub_super_max_yearly") is None       # 超级Max 已下架不可售
    assert catalog.get_sku("byok_3m").amount_cents == 1500        # 3 × 500
    assert catalog.get_sku("byok_3m").quantity == 3
    assert catalog.get_sku("credit_medium").amount_cents == 4200
    assert catalog.get_sku("credit_medium").meta["credits"] == 500


def test_catalog_unknown_sku_returns_none():
    assert catalog.get_sku("nonexistent") is None
    assert catalog.get_sku("sub_pro_weekly") is None       # 非法周期
    assert catalog.get_sku("byok_99m") is None              # 超 12 月
    assert catalog.get_sku("") is None


def test_catalog_list_has_12_skus():
    # v5:删超级Max 2 个订阅 SKU → 12 变 10(4 订阅 + 3 BYOK + 3 加购)
    assert len(catalog.list_skus()) == 10
    assert len(catalog.list_skus("credit")) == 3
    assert len(catalog.list_skus("subscription")) == 4   # pro/max × 月/年
    assert len(catalog.list_skus("byok")) == 3


# ============================================================
# 2-5. 订单生命周期 + 履约幂等 + 隔离(走 credit 类,履约最易验证)
# ============================================================

_DUMMY_IMG = b"\x89PNG\r\n\x1a\n" + b"0" * 2048  # ≥1024,过 submit_proof 大小校验


def test_order_lifecycle_and_fulfillment(make_user):
    """credit 订单:create → submit_proof → approve → 配额到账"""
    u = make_user("payer")
    uid = u["user_id"]
    conn = get_connection()
    try:
        before = credit_service.get_balance(conn, uid).addon_credits

        order = order_service.create_order(conn, uid, "credit_small")
        assert order["status"] == "pending"
        assert order["amount_cents"] == 1000       # v5:credit_small 100c ¥10

        order_service.submit_proof(conn, uid, order["id"], _DUMMY_IMG, ".png")
        got = order_service.get_order(conn, order["id"], uid)
        assert got["status"] == "submitted"

        result = order_service.approve_order(conn, order["id"], reviewer="admin1")
        assert result["status"] == "paid"
        assert result["fulfilled_at"] is not None

        # 配额真的到账(credit_small = 100c)
        after = credit_service.get_balance(conn, uid).addon_credits
        assert after == before + 100
    finally:
        conn.close()


def test_fulfillment_idempotent_no_double_grant(make_user):
    """approve 两次 → 只发一次货(money-handling 最关键防线)"""
    u = make_user("payer")
    uid = u["user_id"]
    conn = get_connection()
    try:
        before = credit_service.get_balance(conn, uid).addon_credits
        order = order_service.create_order(conn, uid, "credit_small")
        order_service.submit_proof(conn, uid, order["id"], _DUMMY_IMG, ".png")

        order_service.approve_order(conn, order["id"], reviewer="admin1")
        # 第二次审核通过(模拟审核员手抖双击)
        second = order_service.approve_order(conn, order["id"], reviewer="admin1")
        assert second["fulfillment"]["skipped"] is True       # 履约被跳过

        after = credit_service.get_balance(conn, uid).addon_credits
        assert after == before + 100                          # 只发一次 100c
    finally:
        conn.close()


def test_cross_user_cannot_see_order(make_user):
    u1 = make_user("alice")
    u2 = make_user("bob")
    conn = get_connection()
    try:
        order = order_service.create_order(conn, u1["user_id"], "credit_small")
        # bob 拿 alice 的订单 → NOT_FOUND
        with pytest.raises(order_service.OrderError) as ei:
            order_service.get_order(conn, order["id"], u2["user_id"])
        assert ei.value.code == "NOT_FOUND"
        # bob 列表里也没有
        assert order_service.list_my_orders(conn, u2["user_id"]) == []
    finally:
        conn.close()


def test_cannot_reject_fulfilled_order(make_user):
    u = make_user("payer")
    uid = u["user_id"]
    conn = get_connection()
    try:
        order = order_service.create_order(conn, uid, "credit_small")
        order_service.submit_proof(conn, uid, order["id"], _DUMMY_IMG, ".png")
        order_service.approve_order(conn, order["id"], reviewer="admin1")
        # 已发货后驳回 → 拒绝
        with pytest.raises(order_service.OrderError) as ei:
            order_service.reject_order(conn, order["id"], reviewer="admin1", reason="反悔")
        assert ei.value.code == "ALREADY_FULFILLED"
    finally:
        conn.close()


# ============================================================
# 6. 未知 SKU 拒绝
# ============================================================

def test_create_order_rejects_unknown_sku(make_user):
    u = make_user("payer")
    conn = get_connection()
    try:
        with pytest.raises(order_service.OrderError) as ei:
            order_service.create_order(conn, u["user_id"], "bogus_sku")
        assert ei.value.code == "INVALID_SKU"
    finally:
        conn.close()


def test_proof_dedup_rejects_same_image(make_user):
    """同一张截图不能用于两个订单"""
    u = make_user("payer")
    uid = u["user_id"]
    conn = get_connection()
    try:
        o1 = order_service.create_order(conn, uid, "credit_small")
        o2 = order_service.create_order(conn, uid, "credit_medium")
        order_service.submit_proof(conn, uid, o1["id"], _DUMMY_IMG, ".png")
        with pytest.raises(order_service.OrderError) as ei:
            order_service.submit_proof(conn, uid, o2["id"], _DUMMY_IMG, ".png")
        assert ei.value.code == "PROOF_REUSED"
    finally:
        conn.close()
