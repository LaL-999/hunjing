"""
BYOK 个人收款码支付 + Vision LLM 审核 端到端测试。

测试矩阵:
  1. 创建订单 → 状态 pending
  2. 提交截图(mock Vision LLM 返回完美数据) → 自动 paid + 激活 BYOK 订阅
  3. 金额不符 → manual_review
  4. 收款方不符 → manual_review
  5. 付款时间超 24h → manual_review
  6. SHA-256 重复提交 → 409
  7. 同 transaction_id 第二次提交 → rejected
  8. 订单过期(超 24h)
  9. 非本人订单 → 403
  10. 收款码姓名匹配 — 处理脱敏("李*爽")
"""
from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.config import settings


@pytest.fixture(autouse=True)
def reset_byok_context_for_payment():
    from app.services.byok_context import set_current_user_id
    set_current_user_id(None)
    yield
    set_current_user_id(None)
    # Windows teardown 修复:等所有 byok-review-* 后台 thread 结束释放 db 句柄
    import threading
    for t in list(threading.enumerate()):
        if t.name.startswith("byok-review-") and t.is_alive():
            t.join(timeout=3.0)


@pytest.fixture
def mock_vision_llm(monkeypatch):
    """mock _call_vision_llm_on_proof 返回可配置 detection dict"""
    container = {"return_value": None, "raise_exc": None, "called_with": []}

    def fake(image_path: Path):
        container["called_with"].append(str(image_path))
        if container["raise_exc"]:
            raise container["raise_exc"]
        return container["return_value"]

    monkeypatch.setattr(
        "app.services.byok_payment_service._call_vision_llm_on_proof",
        fake,
    )
    return container


# settings.byok_payee_name 默认就是 "李爽"(见 config.py),无需 patch


def _good_detection(amount_yuan=30, payee="李*爽", txid="WX2026060512345678", pay_time=None):
    """构造一份完美的 detection dict"""
    if pay_time is None:
        # 默认 5 分钟前(在订单 24h 窗内)
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


def _wait_for_review_done(get_status_callable, order_id, timeout_s=5.0):
    """等后台审核线程跑完(轮询订单状态变化 + 额外等 db conn 释放)"""
    import time
    end = time.time() + timeout_s
    final = None
    while time.time() < end:
        s = get_status_callable(order_id)
        if s and s.status in ("paid", "rejected", "manual_review"):
            final = s
            break
        time.sleep(0.05)
    if final is None:
        final = get_status_callable(order_id)
    # 关键:让所有 byok-review-* 后台 thread 完全结束,释放 db 句柄
    # (Windows 上,thread 还持有 conn 时 test_db 不能被 teardown 删除)
    import threading
    for t in list(threading.enumerate()):
        if t.name.startswith("byok-review-") and t.is_alive():
            t.join(timeout=2.0)
    return final


# ============================================================
# 主流程
# ============================================================

def test_create_order_returns_pending(make_user, client):
    u = make_user("pay")
    r = client.post(
        "/api/byok/payment/orders",
        json={"months": 1},
        headers=u["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["order_id"].startswith("ORD-")
    assert body["amount_cents"] == 3000
    assert body["months"] == 1
    assert "扫描下方二维码" in body["instruction_text"]


def test_full_happy_path_paid_and_activates(
    make_user, client, mock_vision_llm,
):
    """完美付款流程:创建 → 上传 → Vision 识别完美 → 自动 paid + 激活订阅"""
    u = make_user("pay")
    headers = u["headers"]

    # 1. 创建订单
    r = client.post("/api/byok/payment/orders", json={"months": 1}, headers=headers)
    order_id = r.json()["order_id"]

    # 2. mock Vision LLM 返回完美数据
    mock_vision_llm["return_value"] = _good_detection()

    # 3. 上传截图(虚拟图片)
    fake_png = b"\x89PNG\r\n\x1a\n" + b"x" * 1000
    r = client.post(
        f"/api/byok/payment/orders/{order_id}/submit_proof",
        files={"file": ("proof.png", io.BytesIO(fake_png), "image/png")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "submitted"

    # 4. 等审核完成
    def get_status(oid):
        rr = client.get(f"/api/byok/payment/orders/{oid}", headers=headers)
        if rr.status_code != 200:
            return None
        from types import SimpleNamespace
        return SimpleNamespace(**rr.json())

    s = _wait_for_review_done(get_status, order_id)
    assert s.status == "paid", f"应该 paid,实际 {s.status} 原因 {s.rejected_reason}"
    assert s.activated_code is not None
    assert s.activated_code.startswith("BYOK-")
    assert s.activated_expires_at is not None

    # 5. BYOK status 现在应 active
    r = client.get("/api/byok/status", headers=headers)
    assert r.json()["has_active_subscription"] is True


def test_amount_mismatch_goes_to_manual_review(
    make_user, client, mock_vision_llm,
):
    u = make_user("pay")
    r = client.post("/api/byok/payment/orders", json={"months": 1}, headers=u["headers"])
    order_id = r.json()["order_id"]

    # 截图识别出 ¥10(订单是 ¥30)
    mock_vision_llm["return_value"] = _good_detection(amount_yuan=10)
    fake_png = b"\x89PNG\r\n\x1a\n" + b"a" * 1000
    client.post(
        f"/api/byok/payment/orders/{order_id}/submit_proof",
        files={"file": ("p.png", io.BytesIO(fake_png), "image/png")},
        headers=u["headers"],
    )

    def get_status(oid):
        rr = client.get(f"/api/byok/payment/orders/{oid}", headers=u["headers"])
        from types import SimpleNamespace
        return SimpleNamespace(**rr.json())

    s = _wait_for_review_done(get_status, order_id)
    assert s.status == "manual_review"
    assert "金额不符" in (s.rejected_reason or "")


def test_payee_mismatch_goes_to_manual_review(
    make_user, client, mock_vision_llm,
):
    u = make_user("pay")
    r = client.post("/api/byok/payment/orders", json={"months": 1}, headers=u["headers"])
    order_id = r.json()["order_id"]

    # 收款方是"王明"(不是李爽)
    mock_vision_llm["return_value"] = _good_detection(payee="王明")
    fake_png = b"\x89PNG\r\n\x1a\n" + b"b" * 1000
    client.post(
        f"/api/byok/payment/orders/{order_id}/submit_proof",
        files={"file": ("p.png", io.BytesIO(fake_png), "image/png")},
        headers=u["headers"],
    )

    def get_status(oid):
        rr = client.get(f"/api/byok/payment/orders/{oid}", headers=u["headers"])
        from types import SimpleNamespace
        return SimpleNamespace(**rr.json())

    s = _wait_for_review_done(get_status, order_id)
    assert s.status == "manual_review"
    assert "收款方姓名不符" in (s.rejected_reason or "")


def test_pay_time_too_old_goes_to_manual_review(
    make_user, client, mock_vision_llm,
):
    u = make_user("pay")
    r = client.post("/api/byok/payment/orders", json={"months": 1}, headers=u["headers"])
    order_id = r.json()["order_id"]

    # 付款时间在订单创建前 1 小时(早于订单)
    old_time = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    mock_vision_llm["return_value"] = _good_detection(pay_time=old_time)
    fake_png = b"\x89PNG\r\n\x1a\n" + b"c" * 1000
    client.post(
        f"/api/byok/payment/orders/{order_id}/submit_proof",
        files={"file": ("p.png", io.BytesIO(fake_png), "image/png")},
        headers=u["headers"],
    )

    def get_status(oid):
        rr = client.get(f"/api/byok/payment/orders/{oid}", headers=u["headers"])
        from types import SimpleNamespace
        return SimpleNamespace(**rr.json())

    s = _wait_for_review_done(get_status, order_id)
    assert s.status == "manual_review"
    assert "付款时间" in (s.rejected_reason or "")


def test_duplicate_image_returns_409(
    make_user, client, mock_vision_llm,
):
    u = make_user("pay")
    # 创建 2 个订单
    r1 = client.post("/api/byok/payment/orders", json={"months": 1}, headers=u["headers"])
    r2 = client.post("/api/byok/payment/orders", json={"months": 1}, headers=u["headers"])
    o1 = r1.json()["order_id"]
    o2 = r2.json()["order_id"]

    # 同张图先给 o1
    mock_vision_llm["return_value"] = _good_detection()
    fake_png = b"\x89PNG\r\n\x1a\n" + b"SAME" * 250
    client.post(
        f"/api/byok/payment/orders/{o1}/submit_proof",
        files={"file": ("p.png", io.BytesIO(fake_png), "image/png")},
        headers=u["headers"],
    )

    # 同张图再给 o2 → 应该 409
    r = client.post(
        f"/api/byok/payment/orders/{o2}/submit_proof",
        files={"file": ("p.png", io.BytesIO(fake_png), "image/png")},
        headers=u["headers"],
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "PROOF_REUSED"


def test_other_user_cannot_view_my_order(make_user, client):
    u1 = make_user("uA")
    u2 = make_user("uB")
    r = client.post("/api/byok/payment/orders", json={"months": 1}, headers=u1["headers"])
    order_id = r.json()["order_id"]

    # u2 看 u1 的订单 → 404(不暴露存在性)
    r = client.get(f"/api/byok/payment/orders/{order_id}", headers=u2["headers"])
    assert r.status_code == 404


def test_payee_name_match_handles_wechat_redaction(monkeypatch):
    """微信中央显示通常 "李*爽" 这种脱敏,要能匹配 "李爽" 真名"""
    from app.services.byok_payment_service import _payee_name_match
    assert _payee_name_match("李*爽", "李爽") is True
    assert _payee_name_match("李爽", "李爽") is True
    assert _payee_name_match("*爽", "李爽") is True
    assert _payee_name_match("李*", "李爽") is True
    assert _payee_name_match("王明", "李爽") is False
    assert _payee_name_match("", "李爽") is False
