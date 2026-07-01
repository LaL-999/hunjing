"""Billing / 价格快照测试 — Sprint E.4。

测试分组:
  A. subscribe:happy + 非法 plan / cycle + 重复订阅 409
  B. cancel:happy + 无 active 404
  C. upgrade:happy + 老快照归档 + grandfather_at 保留
  D. snapshot 查询:active / cancelled-still-valid / 204 无快照
  E. 老用户老规则保护:平台改 PLAN_LIMITS 不影响 active 快照配额
  F. 鉴权:跨用户 / 401
"""
from __future__ import annotations

from fastapi.testclient import TestClient


# ============================================================
# A. subscribe
# ============================================================

def test_subscribe_happy_creates_active_snapshot(
    client: TestClient, make_user, monkeypatch,
):
    # ECON-1.4:本测试关注非 promo 路径(全价订阅),关掉首月 5 折
    from app.services import billing_service
    monkeypatch.setattr(billing_service, "is_first_subscription", lambda *a, **k: False)

    user = make_user("alice")
    r = client.post(
        "/api/billing/subscribe", headers=user["headers"],
        json={"plan": "pro", "billing_cycle": "monthly", "notes": "测试"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["plan"] == "pro"
    assert body["billing_cycle"] == "monthly"
    assert body["price_cents"] == 6800   # v5:¥68(¥138 → ¥68 约减半)
    assert body["price_yuan_fmt"] == "68.00"
    assert body["state"] == "active"
    assert body["notes"] == "测试"
    # 快照里的 limits 跟当前 PLAN_LIMITS["pro"] 一致(v5:Pro 配额 600/¥0.11)
    assert body["limits"]["monthly_credits_quota"] == 600
    assert body["limits"]["single_credit_price_cents"] == 11
    assert body["limits"]["characters_per_project"] == 30
    assert body["limits"]["projects_total"] == 5
    assert body["limits"]["reshape_max_percent"] == 80


def test_subscribe_max_yearly_price_correct(
    client: TestClient, make_user, monkeypatch,
):
    # ECON-1.4:本测试关注非 promo 路径
    from app.services import billing_service
    monkeypatch.setattr(billing_service, "is_first_subscription", lambda *a, **k: False)

    user = make_user("alice")
    r = client.post(
        "/api/billing/subscribe", headers=user["headers"],
        json={"plan": "max", "billing_cycle": "yearly"},
    )
    assert r.status_code == 201
    # v5:Max yearly ¥218 × 12 × 0.85 = ¥2223.60
    assert r.json()["price_cents"] == 222360
    assert r.json()["plan"] == "max"
    assert r.json()["billing_cycle"] == "yearly"


def test_subscribe_super_max_correct(client: TestClient, make_user, monkeypatch):
    """v5(2026-07-02):超级 Max 已下架不可售 —— 订阅请求被拒(非 201)。"""
    from app.services import billing_service
    monkeypatch.setattr(billing_service, "is_first_subscription", lambda *a, **k: False)

    user = make_user("alice")
    r = client.post(
        "/api/billing/subscribe", headers=user["headers"],
        json={"plan": "super_max", "billing_cycle": "monthly"},
    )
    # 已下架:PaidPlan Literal 或 PLAN_PRICE_CENTS 缺失 → 400/422,绝不 201
    assert r.status_code in (400, 422), r.text


def test_subscribe_invalid_plan_returns_422(client: TestClient, make_user):
    """Pydantic Literal 校验 — 'free' 不在 PaidPlan,422 而非 400。"""
    user = make_user("alice")
    r = client.post(
        "/api/billing/subscribe", headers=user["headers"],
        json={"plan": "free", "billing_cycle": "monthly"},
    )
    assert r.status_code == 422


def test_subscribe_invalid_cycle_returns_422(client: TestClient, make_user):
    user = make_user("alice")
    r = client.post(
        "/api/billing/subscribe", headers=user["headers"],
        json={"plan": "pro", "billing_cycle": "daily"},
    )
    assert r.status_code == 422


def test_subscribe_duplicate_returns_409(client: TestClient, make_user):
    user = make_user("alice")
    h = user["headers"]
    client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    r = client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "max", "billing_cycle": "monthly"},
    )
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "ALREADY_HAS_ACTIVE"


def test_subscribe_syncs_users_plan(client: TestClient, make_user):
    """subscribe 应同步更新 users.plan,让 quota_service / deps 老路径仍能用 user.plan。"""
    import sqlite3
    from app.config import settings

    user = make_user("alice")
    h = user["headers"]
    client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "monthly"},
    )

    conn = sqlite3.connect(str(settings.db_abs_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT plan FROM users WHERE id=?", (user["user_id"],),
    ).fetchone()
    conn.close()
    assert row["plan"] == "pro"


# ============================================================
# B. cancel
# ============================================================

def test_cancel_happy_active_to_cancelled(client: TestClient, make_user):
    user = make_user("alice")
    h = user["headers"]
    client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    r = client.post("/api/billing/cancel", headers=h)
    assert r.status_code == 200
    assert r.json()["state"] == "cancelled"


def test_cancel_without_active_returns_404(client: TestClient, make_user):
    user = make_user("alice")
    r = client.post("/api/billing/cancel", headers=user["headers"])
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NO_ACTIVE_SNAPSHOT"


def test_cancelled_still_visible_via_get_snapshot(
    client: TestClient, make_user,
):
    """cancelled 但未到 period_end → 仍能通过 /snapshot 拿到(配额仍生效)。"""
    user = make_user("alice")
    h = user["headers"]
    client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    client.post("/api/billing/cancel", headers=h)
    r = client.get("/api/billing/snapshot", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "cancelled"
    assert body["plan"] == "pro"


# ============================================================
# C. upgrade
# ============================================================

def test_upgrade_archives_old_creates_new_active(
    client: TestClient, make_user,
):
    user = make_user("alice")
    h = user["headers"]
    r1 = client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    old_id = r1.json()["id"]
    r2 = client.post(
        "/api/billing/upgrade", headers=h,
        json={"plan": "max", "billing_cycle": "yearly"},
    )
    assert r2.status_code == 200
    new = r2.json()
    assert new["plan"] == "max"
    assert new["billing_cycle"] == "yearly"
    # v5:Max yearly ¥2223.60(¥218 × 12 × 0.85)
    assert new["price_cents"] == 222360
    assert new["id"] != old_id   # 新 snapshot
    assert new["state"] == "active"

    # 老快照状态变 upgraded
    history = client.get("/api/billing/history", headers=h).json()
    assert len(history["snapshots"]) == 2
    old_snap = next((s for s in history["snapshots"] if s["id"] == old_id), None)
    assert old_snap is not None
    assert old_snap["state"] == "upgraded"


def test_upgrade_preserves_grandfather_at(client: TestClient, make_user):
    """grandfather_at 保留(用户连续受老规则保护起算时间)。"""
    import time
    user = make_user("alice")
    h = user["headers"]
    r1 = client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    original_grandfather = r1.json()["grandfather_at"]

    time.sleep(1.1)   # 让时间戳不同

    r2 = client.post(
        "/api/billing/upgrade", headers=h,
        json={"plan": "max", "billing_cycle": "monthly"},
    )
    new_grandfather = r2.json()["grandfather_at"]
    assert new_grandfather == original_grandfather
    # 但 created_at 是新的(因为是新 snapshot)
    assert r2.json()["created_at"] != original_grandfather


def test_upgrade_without_active_returns_404(client: TestClient, make_user):
    user = make_user("alice")
    r = client.post(
        "/api/billing/upgrade", headers=user["headers"],
        json={"plan": "max", "billing_cycle": "monthly"},
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NO_ACTIVE_SNAPSHOT"


# ============================================================
# D. snapshot 查询
# ============================================================

def test_get_snapshot_no_subscription_returns_204(
    client: TestClient, make_user,
):
    user = make_user("alice")
    r = client.get("/api/billing/snapshot", headers=user["headers"])
    assert r.status_code == 204


def test_get_snapshot_after_subscribe(client: TestClient, make_user):
    user = make_user("alice")
    h = user["headers"]
    client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    r = client.get("/api/billing/snapshot", headers=h)
    assert r.status_code == 200
    assert r.json()["plan"] == "pro"
    assert r.json()["state"] == "active"


def test_history_returns_all_snapshots(client: TestClient, make_user):
    user = make_user("alice")
    h = user["headers"]
    client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    client.post(
        "/api/billing/upgrade", headers=h,
        json={"plan": "max", "billing_cycle": "yearly"},
    )
    r = client.get("/api/billing/history", headers=h)
    assert r.status_code == 200
    snaps = r.json()["snapshots"]
    assert len(snaps) == 2
    # 最新在前(created_at DESC)
    assert snaps[0]["plan"] == "max"
    assert snaps[0]["state"] == "active"
    assert snaps[1]["plan"] == "pro"
    assert snaps[1]["state"] == "upgraded"


# ============================================================
# E. 老用户老规则保护 — 平台改 PLAN_LIMITS 不影响 active 快照
# ============================================================

def test_grandfather_protection_quota_uses_snapshot(
    client: TestClient, make_user, monkeypatch,
):
    """订阅后 → 平台改 PLAN_LIMITS["pro"].projects_total = 999 →
       quota /api/quota 返回的 limits.projects_total 仍是订阅时的 5(快照保护)。"""
    user = make_user("alice")
    h = user["headers"]
    client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "monthly"},
    )

    # Sprint C.1:模拟平台调整 PLAN_LIMITS(扩大 pro 配额到 999)
    # Sprint 5.B(2026-05-18):PlanLimits 加 comics_per_month 字段;此测试 mock 升至 999
    from app.services.quota_service import PLAN_LIMITS, PlanLimits
    new_pro = PlanLimits(
        monthly_credits_quota=999999,
        single_credit_price_cents=99,
        characters_per_project=999,
        projects_total=999,
        reshape_max_percent=90,
        comics_per_month=999,            # Sprint 5.B 加
    )
    monkeypatch.setitem(PLAN_LIMITS, "pro", new_pro)

    # 此时查 /api/quota — 老用户应该仍享受订阅时的快照,不是新的 999
    r = client.get("/api/quota", headers=h)
    assert r.status_code == 200
    assert r.json()["limits"]["projects_total"] == 5         # 订阅时冻结值,**非** 999
    assert r.json()["limits"]["monthly_credits_quota"] == 600  # ECON-1:Pro 600c 冻结
    assert r.json()["limits"]["reshape_max_percent"] == 80
    # Sprint 5.B 设计决策:comics_per_month **不冻结进 snapshot**,跟最新规则走;
    # mock 把 PLAN_LIMITS['pro'].comics_per_month 改成 999 → snapshot 用户也是 999
    assert r.json()["limits"]["comics_per_month"] == 999


def test_no_snapshot_uses_global_plan_limits(
    client: TestClient, make_user,
):
    """未订阅(free 用户)→ 走全局 PLAN_LIMITS["free"]。"""
    user = make_user("alice")
    r = client.get("/api/quota", headers=user["headers"])
    assert r.status_code == 200
    assert r.json()["plan"] == "free"
    assert r.json()["limits"]["projects_total"] == 2     # free


# ============================================================
# F. 鉴权
# ============================================================

def test_subscribe_unauthenticated_returns_401(client: TestClient):
    r = client.post(
        "/api/billing/subscribe",
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    assert r.status_code == 401


def test_snapshot_cross_user_isolation(client: TestClient, make_user):
    """alice 订阅,bob 查不到 alice 的快照。"""
    alice = make_user("alice")
    bob = make_user("bob")
    client.post(
        "/api/billing/subscribe", headers=alice["headers"],
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    r = client.get("/api/billing/snapshot", headers=bob["headers"])
    assert r.status_code == 204   # bob 自己没订阅


# ============================================================
# G. ECON-1.4 优惠规则(首月 5 折 + 6 月价保,2026-05-27 末⁴)
# ============================================================

def test_subscribe_first_month_promo_applied(
    client: TestClient, make_user,
):
    """ECON-1.4 首月 5 折:从未订阅过的用户首次订阅自动享 50% 折扣."""
    user = make_user("new_user")
    r = client.post(
        "/api/billing/subscribe", headers=user["headers"],
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    # v5:Pro 月付原价 ¥68 = 6800 分 → 5 折 3400 分
    assert body["price_cents"] == 3400, f"首月 5 折应是 3400,实际 {body['price_cents']}"
    # notes 应含 ECON-1.4 promo 标记(审计可追)
    assert body["notes"] is not None
    assert "first_month_promo" in body["notes"]


def test_subscribe_second_time_no_first_month_promo(
    client: TestClient, make_user,
):
    """ECON-1.4 反 abuse:订阅后取消,再次订阅不再享首月 5 折(走全价或 grandfather)."""
    user = make_user("two_time")
    h = user["headers"]
    # 第 1 次订阅 → 享 5 折
    r1 = client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    assert r1.status_code == 201
    first_price = r1.json()["price_cents"]
    assert first_price == 3400   # v5:5 折确认(6800 × 0.5)

    # 取消订阅
    rc = client.post("/api/billing/cancel", headers=h)
    assert rc.status_code == 200

    # 第 2 次订阅(同 plan + cycle)→ 应享 6 月价保(沿用第 1 次老价 ¥69),不享首月 5 折
    r2 = client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    assert r2.status_code == 201
    # 第 2 次走 grandfather(沿用第 1 次 3400),而非全价 6800,也不是再次 5 折
    assert r2.json()["price_cents"] == 3400, (
        f"6 月价保应沿用老价 3400,实际 {r2.json()['price_cents']}"
    )
    assert "grandfather" in r2.json()["notes"]


def test_subscribe_grandfather_only_same_plan_cycle(
    client: TestClient, make_user, monkeypatch,
):
    """ECON-1.4:6 月价保只对相同 plan+cycle 生效,跨档/换 cycle 走新价."""
    user = make_user("upgrade_user")
    h = user["headers"]

    # 关掉首月 5 折以便清晰测试 grandfather 边界
    from app.services import billing_service
    monkeypatch.setattr(billing_service, "is_first_subscription", lambda *a, **k: False)

    # 第 1 次订阅 Pro monthly 全价
    r1 = client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    assert r1.status_code == 201
    assert r1.json()["price_cents"] == 6800   # v5 全价

    # 取消
    client.post("/api/billing/cancel", headers=h)

    # 重订相同 Pro monthly → grandfather 命中(沿用 6800,虽然就是当前价)
    r2 = client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "monthly"},
    )
    assert r2.status_code == 201
    assert r2.json()["price_cents"] == 6800

    # cancel,再订**不同 cycle**(Pro yearly)→ grandfather 不命中,走当前全价
    client.post("/api/billing/cancel", headers=h)
    r3 = client.post(
        "/api/billing/subscribe", headers=h,
        json={"plan": "pro", "billing_cycle": "yearly"},
    )
    assert r3.status_code == 201
    # v5:Pro yearly 当前全价 69360(¥693.60)
    assert r3.json()["price_cents"] == 69360, (
        f"跨 cycle 不享 grandfather,应走全价 69360,实际 {r3.json()['price_cents']}"
    )


def test_compute_subscribe_price_helpers(client: TestClient, make_user):
    """ECON-1.4 helper 函数直接验证(无 API,只跑 service)."""
    from app.db import get_connection
    from app.services.billing_service import (
        compute_subscribe_price_cents,
        is_first_subscription,
        find_grandfather_price_cents,
    )

    user = make_user("helper_test")
    conn = get_connection()
    try:
        # 1. 全新用户 → first_subscription=True / grandfather=None
        assert is_first_subscription(conn, user["user_id"]) is True
        assert find_grandfather_price_cents(conn, user["user_id"], "pro", "monthly") is None

        # 2. compute 应返 5 折(因为是 first sub;v5:6800 × 0.5 = 3400)
        price, rule = compute_subscribe_price_cents(conn, user["user_id"], "pro", "monthly")
        assert price == 3400
        assert rule == "first_month_promo"
    finally:
        conn.close()
