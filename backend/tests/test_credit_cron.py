"""test_credit_cron.py — Sprint C.5 cron 任务测试。

覆盖:
  - month_reset:跨月用户被重置 / 同月用户跳过 / 报告字段
  - addon_expire:已到期 lot 被处理 + addon_credits 减少 / 未到期跳过 / 已 expired 不重处理
  - run_daily_credit_jobs 集成
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone


def _setup_user_with_balance(
    make_user,
    plan: str = "max",
    subscription_credits: int = 100,
    addon_credits: int = 0,
    month_start_override: str | None = None,
) -> dict:
    """造一个 user + wallet 已初始化 + 余额按需。

    Args:
        month_start_override:为 None 用真实本月初;否则用 override(用来模拟"上月用户")
    """
    from app.db import get_connection
    from app.services.credit_service import ensure_balance
    from app.services.quota_service import month_start_iso

    u = make_user("cron_user")

    conn = get_connection()
    try:
        # 改 plan
        if plan != "free":
            conn.execute("UPDATE users SET plan=? WHERE id=?", (plan, u["user_id"]))
            conn.commit()

        # 初始化 wallet
        ensure_balance(conn, u["user_id"], plan)

        # 覆盖余额 + month_start
        ms = month_start_override or month_start_iso()
        conn.execute(
            """UPDATE user_credit_balances
               SET subscription_credits=?, addon_credits=?, month_start=?
               WHERE user_id=?""",
            (subscription_credits, addon_credits, ms, u["user_id"]),
        )
        conn.commit()
    finally:
        conn.close()
    return u


def _get_wallet(user_id):
    from app.db import get_connection
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM user_credit_balances WHERE user_id=?",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ============================================================
# month_reset 测试
# ============================================================

def test_month_reset_resets_old_month_user(client, make_user):
    """用户 month_start 在上个月 → cron 应重置 + 发新月度池。"""
    from app.services.credit_cron import month_reset_all_users

    # 模拟"上个月"起点
    last_month = (
        (datetime.now(timezone.utc).replace(day=1) - timedelta(days=1))
        .replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        .isoformat()
    )
    u = _setup_user_with_balance(
        make_user, plan="max",
        subscription_credits=50,    # 还剩 50,应被清掉
        month_start_override=last_month,
    )

    report = month_reset_all_users()

    assert report["users_reset"] >= 1
    # 验证 wallet 被重置
    wallet = _get_wallet(u["user_id"])
    assert wallet is not None
    # max 档月度配额 2000(ECON-1 v5,3400 → 2000 收紧)
    assert wallet["subscription_credits"] == 2000
    # month_start 已更新为本月
    from app.services.quota_service import month_start_iso
    assert wallet["month_start"] == month_start_iso()


def test_month_reset_skips_same_month_user(client, make_user):
    """用户 month_start 已是本月 → cron 应跳过。"""
    from app.services.credit_cron import month_reset_all_users

    u = _setup_user_with_balance(
        make_user, plan="pro", subscription_credits=500,
        # 默认 month_start_override=None → 用真实本月初
    )

    report = month_reset_all_users()

    # 跳过统计 ≥ 1(本测试这个用户)
    assert report["users_skipped_same_month"] >= 1
    # wallet 未被改
    wallet = _get_wallet(u["user_id"])
    assert wallet["subscription_credits"] == 500


# ============================================================
# addon_expire 测试
# ============================================================

def test_addon_expire_processes_expired_lots(client, make_user):
    """到期 + 未处理的 lot 应被标记 + addon_credits 减少。"""
    from app.db import get_connection
    from app.services.credit_cron import expire_addon_lots
    from app.services.credit_service import ensure_balance, purchase_addon

    u = make_user("addon_expire_user")
    conn = get_connection()
    try:
        ensure_balance(conn, u["user_id"], "max")
        # 把 max 设上(让 grant_subscription_credits 用 max 配额)
        conn.execute("UPDATE users SET plan='max' WHERE id=?", (u["user_id"],))
        conn.commit()

        # 加购小包
        purchase_addon(conn, u["user_id"], "small")    # +100 addon

        # 改 lot 的 expires_at 为昨天(模拟到期)
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).replace(microsecond=0).isoformat()
        conn.execute(
            "UPDATE addon_credit_lots SET expires_at=? WHERE user_id=?",
            (yesterday, u["user_id"]),
        )
        conn.commit()

        # wallet 应仍有 100 addon(到期还没处理)
        wallet_before = _get_wallet(u["user_id"])
        assert wallet_before["addon_credits"] == 100

        # 跑 cron
        report = expire_addon_lots()
        assert report["lots_expired"] >= 1
        assert report["credits_expired_total"] >= 100

        # wallet addon 减为 0
        wallet_after = _get_wallet(u["user_id"])
        assert wallet_after["addon_credits"] == 0

        # lot 状态 is_expired=1 + expired_at 有值
        lot_row = conn.execute(
            "SELECT is_expired, expired_at, remaining_credits FROM addon_credit_lots WHERE user_id=?",
            (u["user_id"],),
        ).fetchone()
        assert int(lot_row["is_expired"]) == 1
        assert lot_row["expired_at"] is not None
        assert int(lot_row["remaining_credits"]) == 0
    finally:
        conn.close()


def test_addon_expire_skips_unexpired_lots(client, make_user):
    """未到期的 lot 不被处理。"""
    from app.db import get_connection
    from app.services.credit_cron import expire_addon_lots
    from app.services.credit_service import ensure_balance, purchase_addon

    u = make_user("addon_unexpired")
    conn = get_connection()
    try:
        ensure_balance(conn, u["user_id"], "max")
        conn.execute("UPDATE users SET plan='max' WHERE id=?", (u["user_id"],))
        conn.commit()
        purchase_addon(conn, u["user_id"], "medium")   # 1 年有效(默认)
    finally:
        conn.close()

    report = expire_addon_lots()
    # 不影响 wallet
    wallet = _get_wallet(u["user_id"])
    assert wallet["addon_credits"] == 500
    # 验证此 lot 没被 expired 标记
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT is_expired FROM addon_credit_lots WHERE user_id=?",
            (u["user_id"],),
        ).fetchone()
        assert int(row["is_expired"]) == 0
    finally:
        conn.close()


def test_addon_expire_idempotent(client, make_user):
    """已 expired 的 lot 不会被重复处理。"""
    from app.db import get_connection
    from app.services.credit_cron import expire_addon_lots
    from app.services.credit_service import ensure_balance, purchase_addon

    u = make_user("addon_idempotent")
    conn = get_connection()
    try:
        ensure_balance(conn, u["user_id"], "max")
        conn.execute("UPDATE users SET plan='max' WHERE id=?", (u["user_id"],))
        conn.commit()
        purchase_addon(conn, u["user_id"], "small")

        # 改 expires_at 过去
        past = (datetime.now(timezone.utc) - timedelta(days=2)).replace(microsecond=0).isoformat()
        conn.execute(
            "UPDATE addon_credit_lots SET expires_at=? WHERE user_id=?",
            (past, u["user_id"]),
        )
        conn.commit()
    finally:
        conn.close()

    # 第 1 次跑:处理
    report1 = expire_addon_lots()
    assert report1["lots_expired"] >= 1

    # 第 2 次跑:不再处理(已 is_expired=1)
    report2 = expire_addon_lots()
    assert report2["lots_expired"] == 0


# ============================================================
# 集成测试
# ============================================================

def test_run_daily_credit_jobs_returns_report(client, make_user):
    """run_daily_credit_jobs 返回完整报告结构(用于 cron 监控)。"""
    from app.services.credit_cron import run_daily_credit_jobs

    report = run_daily_credit_jobs()

    assert "started_at" in report
    assert "completed_at" in report
    assert "month_reset" in report
    assert "addon_expire" in report
    assert isinstance(report["month_reset"]["users_scanned"], int)
    assert isinstance(report["addon_expire"]["lots_scanned"], int)
