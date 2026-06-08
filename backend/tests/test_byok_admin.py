"""
BYOK admin 管理后台测试(2026-06-05)。

测试矩阵:
  1. 非 founder 访问 /admin/byok/* → 403 FOUNDER_ONLY
  2. founder 列表订单 → 按 manual_review 优先排序
  3. founder approve manual_review 订单 → status=paid + 自动激活 BYOK 订阅
  4. founder approve 已 paid 订单 → 幂等返回(不重激活)
  5. founder reject 订单 → status=rejected + 写 reason
  6. reject 空 reason → 400
  7. reject 已 paid 订单 → 400
  8. 看截图 endpoint 在订单无截图时 → 404
  9. status_filter='manual_review' → 只返该状态

mock 策略:
  - 跟 test_byok_payment.py 一样 mock Vision LLM,造一些 manual_review 订单
  - settings.founder_emails 测试时改写,把 "founder@test.com" 当 founder
"""
from __future__ import annotations

import io
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture(autouse=True)
def reset_byok_context_admin():
    from app.services.byok_context import set_current_user_id
    set_current_user_id(None)
    yield
    set_current_user_id(None)
    # 等任何后台 byok-review thread 结束(防 Windows teardown 锁库)
    for t in list(threading.enumerate()):
        if t.name.startswith("byok-review-") and t.is_alive():
            t.join(timeout=3.0)


@pytest.fixture
def mock_vision_llm(monkeypatch):
    container = {"return_value": None, "raise_exc": None}

    def fake(image_path):
        if container["raise_exc"]:
            raise container["raise_exc"]
        return container["return_value"]

    monkeypatch.setattr(
        "app.services.byok_payment_service._call_vision_llm_on_proof",
        fake,
    )
    return container


@pytest.fixture
def founder_user(make_user):
    """造一个 founder 用户 — 通过 FastAPI dependency_overrides 让 require_founder 给该用户放行。

    settings.founder_emails 是 frozen dataclass 不能 monkeypatch,所以改用
    dependency_overrides 在 test 期间替换 require_founder 的实现:
    判定改为"凡是 token 解出来的用户 == 该 fixture 创建的用户 → founder",
    其他人继续 403。
    """
    u = make_user("founder")

    from app.main import app
    from app.deps import require_founder, get_current_user
    from app.models.user import User
    from dataclasses import replace
    from fastapi import HTTPException, status as http_status

    def fake_require_founder(user: User = pytest.importorskip("fastapi").Depends(get_current_user)) -> User:
        if user.id == u["user_id"]:
            return replace(user, plan="founder")
        raise HTTPException(
            http_status.HTTP_403_FORBIDDEN,
            detail={"code": "FOUNDER_ONLY", "message": "仅创始人账号可访问"},
        )

    app.dependency_overrides[require_founder] = fake_require_founder
    yield u
    app.dependency_overrides.pop(require_founder, None)


def _good_detection(amount_yuan=30, payee="李*爽", txid="WX-ADMIN-TEST", pay_time=None):
    if pay_time is None:
        pay_time = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    return {
        "amount_yuan": amount_yuan,
        "payee_name": payee,
        "pay_time": pay_time,
        "transaction_id": txid,
        "is_success": True,
        "confidence": 0.95,
        "raw_text_observed": "付款成功 ¥30.00",
    }


def _wait_review_done(client, order_id, headers, timeout_s=5.0):
    import time
    end = time.time() + timeout_s
    final_status = None
    while time.time() < end:
        r = client.get(f"/api/byok/payment/orders/{order_id}", headers=headers)
        if r.status_code == 200:
            s = r.json()["status"]
            if s in ("paid", "rejected", "manual_review"):
                final_status = s
                break
        time.sleep(0.05)
    # 等 thread 结束
    for t in list(threading.enumerate()):
        if t.name.startswith("byok-review-") and t.is_alive():
            t.join(timeout=2.0)
    return final_status


def _create_manual_review_order(client, user, mock_vision_llm, txid="WX-MR-X"):
    """造一个 manual_review 状态的订单 — 金额不符会触发 manual_review"""
    r = client.post(
        "/api/byok/payment/orders",
        json={"months": 1},
        headers=user["headers"],
    )
    assert r.status_code == 200, r.text
    order_id = r.json()["order_id"]

    # 金额错(20 != 30)→ manual_review
    mock_vision_llm["return_value"] = _good_detection(amount_yuan=20, txid=txid)

    fake_png = b"\x89PNG\r\n\x1a\n" + b"x" * 1000 + txid.encode()
    rp = client.post(
        f"/api/byok/payment/orders/{order_id}/submit_proof",
        files={"file": ("proof.png", io.BytesIO(fake_png), "image/png")},
        headers=user["headers"],
    )
    assert rp.status_code == 200, rp.text

    final = _wait_review_done(client, order_id, user["headers"])
    assert final == "manual_review", f"expected manual_review, got {final}"
    return order_id


# ============================================================
# Tests
# ============================================================

def test_non_founder_gets_403(make_user, client):
    """非 founder 访问 admin endpoint → 403 FOUNDER_ONLY"""
    u = make_user("regular")
    r = client.get("/api/admin/byok/orders", headers=u["headers"])
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "FOUNDER_ONLY"


def test_unauthenticated_gets_401(client):
    r = client.get("/api/admin/byok/orders")
    assert r.status_code == 401


def test_founder_list_orders_prioritizes_manual_review(
    founder_user, make_user, mock_vision_llm, client,
):
    """列表应按 manual_review 优先排序"""
    user1 = make_user("buyer1")
    user2 = make_user("buyer2")

    _create_manual_review_order(client, user1, mock_vision_llm, txid="WX-MR-1")
    _create_manual_review_order(client, user2, mock_vision_llm, txid="WX-MR-2")

    r = client.get("/api/admin/byok/orders", headers=founder_user["headers"])
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 2
    assert body["pending_review_count"] >= 2
    # 头几个应该是 manual_review
    assert body["orders"][0]["status"] == "manual_review"
    # admin 视角能看到 user_email
    assert body["orders"][0]["user_email"] is not None


def test_founder_approve_activates_byok_subscription(
    founder_user, make_user, mock_vision_llm, client,
):
    """approve manual_review 订单 → status=paid + sub 激活"""
    buyer = make_user("approval-test")
    order_id = _create_manual_review_order(client, buyer, mock_vision_llm, txid="WX-APP-1")

    # founder 审批通过
    r = client.post(
        f"/api/admin/byok/orders/{order_id}/approve",
        json={"note": "微信里我已确认收款"},
        headers=founder_user["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "paid"
    assert body["activated_code"] is not None
    assert body["activated_expires_at"] is not None
    assert body["activated_subscription_id"] is not None

    # buyer 视角:也应看到激活码
    rb = client.get(f"/api/byok/payment/orders/{order_id}", headers=buyer["headers"])
    assert rb.status_code == 200
    assert rb.json()["status"] == "paid"
    assert rb.json()["activated_code"] == body["activated_code"]


def test_founder_approve_paid_order_is_idempotent(
    founder_user, make_user, mock_vision_llm, client,
):
    """重复审批已 paid 订单不重复激活"""
    buyer = make_user("idempotent")
    order_id = _create_manual_review_order(client, buyer, mock_vision_llm, txid="WX-IDP-1")

    r1 = client.post(
        f"/api/admin/byok/orders/{order_id}/approve",
        json={"note": "first"},
        headers=founder_user["headers"],
    )
    assert r1.status_code == 200
    code1 = r1.json()["activated_code"]

    r2 = client.post(
        f"/api/admin/byok/orders/{order_id}/approve",
        json={"note": "second"},
        headers=founder_user["headers"],
    )
    assert r2.status_code == 200
    # 同一订单不应产生新激活码
    assert r2.json()["activated_code"] == code1


def test_founder_reject_marks_order_rejected(
    founder_user, make_user, mock_vision_llm, client,
):
    """reject 订单 → status=rejected + reason 落库"""
    buyer = make_user("rejectee")
    order_id = _create_manual_review_order(client, buyer, mock_vision_llm, txid="WX-REJ-1")

    r = client.post(
        f"/api/admin/byok/orders/{order_id}/reject",
        json={"reason": "截图来自另一个交易,与本订单时间不符"},
        headers=founder_user["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "rejected"
    assert "截图来自另一个交易" in (body["rejected_reason"] or "")


def test_reject_empty_reason_400(founder_user, make_user, mock_vision_llm, client):
    buyer = make_user("empty-reason")
    order_id = _create_manual_review_order(client, buyer, mock_vision_llm, txid="WX-ER-1")

    r = client.post(
        f"/api/admin/byok/orders/{order_id}/reject",
        json={"reason": "   "},
        headers=founder_user["headers"],
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "REJECT_FAILED"


def test_reject_paid_order_400(founder_user, make_user, mock_vision_llm, client):
    buyer = make_user("paid-to-reject")
    order_id = _create_manual_review_order(client, buyer, mock_vision_llm, txid="WX-PR-1")

    # 先通过
    client.post(
        f"/api/admin/byok/orders/{order_id}/approve",
        json={},
        headers=founder_user["headers"],
    )
    # 再尝试拒绝
    r = client.post(
        f"/api/admin/byok/orders/{order_id}/reject",
        json={"reason": "想反悔"},
        headers=founder_user["headers"],
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "REJECT_FAILED"


def test_approve_nonexistent_order_400(founder_user, client):
    r = client.post(
        "/api/admin/byok/orders/nonexistent-id-xxx/approve",
        json={},
        headers=founder_user["headers"],
    )
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "APPROVE_FAILED"


def test_proof_image_404_when_missing(founder_user, make_user, client):
    """订单存在但无截图(pending 状态)→ 404"""
    buyer = make_user("noproof")
    r = client.post(
        "/api/byok/payment/orders",
        json={"months": 1},
        headers=buyer["headers"],
    )
    order_id = r.json()["order_id"]

    rp = client.get(
        f"/api/admin/byok/orders/{order_id}/proof_image",
        headers=founder_user["headers"],
    )
    assert rp.status_code == 404
    assert rp.json()["detail"]["code"] == "PROOF_NOT_FOUND"


def test_x_admin_token_path_works(make_user, mock_vision_llm, client, monkeypatch):
    """洞察后台用 X-Admin-Token 调主平台 admin endpoint 也能跑通(2026-06-05 改造)"""
    from app.config import settings

    # 用 settings 当前的 token(测试默认值)做 header
    admin_token = settings.insights_admin_token

    # 任何人下个单(走 manual_review)
    buyer = make_user("byok-buyer-via-admin-token")
    order_id = _create_manual_review_order(client, buyer, mock_vision_llm, txid="WX-XAT-1")

    # X-Admin-Token 调列表
    r = client.get(
        "/api/admin/byok/orders?status_filter=manual_review",
        headers={"X-Admin-Token": admin_token},
    )
    assert r.status_code == 200, r.text
    assert any(o["order_id"] == order_id for o in r.json()["orders"])

    # X-Admin-Token approve
    rapp = client.post(
        f"/api/admin/byok/orders/{order_id}/approve",
        json={"note": "via insights backend"},
        headers={"X-Admin-Token": admin_token},
    )
    assert rapp.status_code == 200, rapp.text
    assert rapp.json()["status"] == "paid"
    assert rapp.json()["activated_code"]

    # 错的 token → 403
    r403 = client.get(
        "/api/admin/byok/orders",
        headers={"X-Admin-Token": "wrong-token-xxx"},
    )
    assert r403.status_code == 403
    assert r403.json()["detail"]["code"] == "INVALID_ADMIN_TOKEN"


def test_status_filter_works(founder_user, make_user, mock_vision_llm, client):
    """status_filter=manual_review 只返 manual_review"""
    buyer = make_user("filter")
    _create_manual_review_order(client, buyer, mock_vision_llm, txid="WX-FILTER-1")

    r = client.get(
        "/api/admin/byok/orders?status_filter=manual_review",
        headers=founder_user["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    assert all(o["status"] == "manual_review" for o in body["orders"])
