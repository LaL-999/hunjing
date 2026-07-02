"""Credit cron 任务 — Sprint C.5(2026-05-13)。

每天跑一次的定时任务,处理 credit 系统的"时间敏感"逻辑:
  1. month_reset_all_users:扫 user_credit_balances.month_start < 实际本月初的用户
     → 重置订阅 wallet(清零旧的 + 按当前 plan 发新月度池)
  2. expire_addon_lots:扫 addon_credit_lots.expires_at < now AND is_expired=0 的 lot
     → 设 is_expired=1 + 减 user_credit_balances.addon_credits + 写 credit_transactions

调用方式:
  - Linux crontab:`0 0 * * * python backend/scripts/run_credit_cron.py`(每天 UTC 0:00)
  - Windows 任务计划:同 script,触发器"每天 0:00"
  - 手动 / 测试:POST /api/credit/admin/run_cron(仅 founder),Sprint C.5 测试用
  - 集成:run_daily_credit_jobs() 一次性跑两个任务 + 返回执行报告

幂等:
  - month_reset:已重置的(month_start = 实际本月初)跳过
  - expire_addon_lots:is_expired=1 的跳过
  - 重复跑同一天无副作用
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db import execute, fetch_all, fetch_one, get_connection
from app.services.credit_service import (
    _record_transaction,
    grant_subscription_credits,
)
from app.services.quota_service import month_start_iso


logger = logging.getLogger("credit_cron")


# ============================================================
# 月度重置
# ============================================================

def month_reset_all_users(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """月度重置 — 扫所有 user_credit_balances.month_start < 实际本月初的用户。

    对每个匹配的 user:
      1. 拉 user.plan(快照优先,fallback PLAN_LIMITS)
      2. 调 grant_subscription_credits(清旧 + 发新)
      3. 累计报告字段

    返回:
      {"users_scanned": int, "users_reset": int, "users_skipped_same_month": int,
       "errors": [{"user_id": str, "error": str}]}

    幂等:同一天重复跑,users_reset=0(都已重置)。
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    report = {
        "users_scanned": 0,
        "users_reset": 0,
        "users_skipped_same_month": 0,
        "errors": [],
    }
    try:
        current_month = month_start_iso()
        # 扫所有 wallet
        rows = fetch_all(
            conn,
            "SELECT b.user_id, b.month_start, u.plan, u.email "
            "FROM user_credit_balances b "
            "JOIN users u ON u.id = b.user_id",
        )
        report["users_scanned"] = len(rows)

        for row in rows:
            user_id = row["user_id"]
            row_month_start = row["month_start"]
            user_plan = row["plan"]

            if row_month_start >= current_month:
                report["users_skipped_same_month"] += 1
                continue

            try:
                grant_subscription_credits(
                    conn, user_id, user_plan, reason="cron_month_reset",
                )
                report["users_reset"] += 1
                logger.info(
                    f"month_reset: user={user_id} plan={user_plan} "
                    f"old_month={row_month_start} → new={current_month}"
                )
            except Exception as e:  # noqa: BLE001
                report["errors"].append({"user_id": user_id, "error": f"{type(e).__name__}: {e}"})
                logger.warning(f"month_reset failed for user={user_id}: {e}")
    finally:
        if own_conn:
            conn.close()

    return report


# ============================================================
# 加购 lot 过期处理
# ============================================================

def expire_addon_lots(conn: sqlite3.Connection | None = None) -> dict[str, Any]:
    """加购 lot 到期处理 — 扫 expires_at < now AND is_expired=0 的 lot。

    对每个匹配的 lot:
      1. UPDATE is_expired=1 + expired_at=now
      2. UPDATE user_credit_balances.addon_credits -= remaining_credits
      3. INSERT credit_transactions(kind='addon_expire', delta=-remaining_credits, wallet='addon')

    返回:
      {"lots_scanned": int, "lots_expired": int, "credits_expired_total": int,
       "errors": [{"lot_id": str, "error": str}]}

    幂等:已 is_expired=1 的 lot 不会被再处理。
    """
    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    report = {
        "lots_scanned": 0,
        "lots_expired": 0,
        "credits_expired_total": 0,
        "errors": [],
    }
    try:
        now_iso = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        # 扫到期未处理 lot
        rows = fetch_all(
            conn,
            """SELECT id, user_id, remaining_credits, expires_at
               FROM addon_credit_lots
               WHERE expires_at < ? AND is_expired = 0""",
            (now_iso,),
        )
        report["lots_scanned"] = len(rows)

        for row in rows:
            lot_id = row["id"]
            user_id = row["user_id"]
            remaining = int(row["remaining_credits"])

            try:
                # 1. 标记 lot 过期
                execute(
                    conn,
                    """UPDATE addon_credit_lots
                       SET is_expired=1, remaining_credits=0, expired_at=?
                       WHERE id=? AND is_expired=0""",
                    (now_iso, lot_id),
                )

                # 2. 从 user_credit_balances.addon_credits 减
                if remaining > 0:
                    execute(
                        conn,
                        """UPDATE user_credit_balances
                           SET addon_credits = MAX(0, addon_credits - ?), updated_at=?
                           WHERE user_id=?""",
                        (remaining, now_iso, user_id),
                    )

                    # 3. 写 audit transaction
                    _record_transaction(
                        conn,
                        user_id=user_id,
                        delta=-remaining,
                        wallet="addon",
                        kind="addon_expire",
                        action="cron_addon_expire",
                        related_id=lot_id,
                        cost_yuan=0,
                        metadata={
                            "lot_id": lot_id,
                            "remaining_at_expire": remaining,
                            "expires_at": row["expires_at"],
                        },
                    )

                conn.commit()
                report["lots_expired"] += 1
                report["credits_expired_total"] += remaining
                logger.info(
                    f"addon_expire: lot={lot_id} user={user_id} "
                    f"remaining={remaining} c"
                )
            except Exception as e:  # noqa: BLE001
                report["errors"].append({"lot_id": lot_id, "error": f"{type(e).__name__}: {e}"})
                logger.warning(f"addon_expire failed for lot={lot_id}: {e}")
    finally:
        if own_conn:
            conn.close()

    return report


# ============================================================
# 统一入口 — 每日 cron 调用
# ============================================================

def run_daily_credit_jobs() -> dict[str, Any]:
    """每日 cron 入口 — 跑全套 credit 时间敏感任务,返回执行报告。

    调用方:
      - scripts/run_credit_cron.py(Linux crontab / Windows 任务计划)
      - POST /api/credit/admin/run_cron(运维手动 / 测试)
    """
    started_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    logger.info(f"=== run_daily_credit_jobs started at {started_at} ===")

    report = {
        "started_at": started_at,
        "month_reset": month_reset_all_users(),
        "addon_expire": expire_addon_lots(),
    }

    # 邮件提醒(Sprint C.5 同步落地,失败不阻塞 cron)
    try:
        from app.services.credit_reminders import (
            send_month_end_reminders,
            send_addon_expiry_reminders,
        )
        report["reminders"] = {
            "month_end": send_month_end_reminders(),
            "addon_expiry": send_addon_expiry_reminders(),
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f"reminders failed: {e}")
        report["reminders"] = {"error": str(e)}

    report["completed_at"] = (
        datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    )
    logger.info(
        f"=== run_daily_credit_jobs completed: "
        f"reset={report['month_reset']['users_reset']} / "
        f"addon_expired={report['addon_expire']['lots_expired']} lots ==="
    )
    return report
