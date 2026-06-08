"""Credit 提醒邮件 — Sprint C.5(2026-05-13)。

每天 cron 跑一次,扫"即将发生事件的用户",发邮件提醒:
  1. 月末 3 天:订阅 credit 即将清零 → 鼓励加购永久保
  2. 加购 lot 到期前 30/7/1 天:credit 即将作废 → 鼓励先用

走现有 SMTP 通道(对齐 otp_email_service)。
SMTP 未配置时(开发期)→ 仅 log 不发,不阻塞 cron。
"""
from __future__ import annotations

import logging
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

from app.config import settings
from app.db import fetch_all, get_connection
from app.services.quota_service import month_start_iso


logger = logging.getLogger("credit_reminders")


# ============================================================
# 内部 — SMTP 发送(失败不抛,只 log)
# ============================================================

def _send_credit_email(target_email: str, subject: str, body: str) -> bool:
    """发邮件 — 失败仅 log 返 False,不抛(cron 不能因一封邮件失败炸整批)。"""
    if not settings.smtp_configured():
        logger.info(f"SMTP 未配置,跳过邮件:{subject} → {target_email}")
        return False

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = settings.smtp_from
    msg["To"] = target_email
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15) as s:
            s.login(settings.smtp_user, settings.smtp_pass)
            s.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as e:
        logger.warning(f"send credit email failed for {target_email}: {e}")
        return False


# ============================================================
# 月末 3 天提醒
# ============================================================

def send_month_end_reminders() -> dict[str, Any]:
    """扫订阅 credit > 0 且 month_start + 27 天 <= now <= month_start + 30 天的用户,
    发邮件提醒"3 天内清零"。

    简化规则:cron 每天跑,只在月末 3 天发(避免重复刷邮件)。
    幂等:加 user_credit_reminders 表更严谨,本 Sprint C.5 简化为"扫到就发"
    (重复邮件每天 1 封,用户不会被骚扰因为只有 3 天窗口)。

    返回:{"users_scanned": int, "emails_sent": int, "errors": [...]}
    """
    report = {"users_scanned": 0, "emails_sent": 0, "errors": []}

    now = datetime.now(timezone.utc)
    # 月末 3 天 = month_start + 28-30 天之间(简化按 30 天 month_start 计算)
    # 即 (now - 28 天) > month_start AND (now - 30 天) <= month_start
    threshold_30d_ago = (now - timedelta(days=30)).replace(microsecond=0).isoformat()
    threshold_27d_ago = (now - timedelta(days=27)).replace(microsecond=0).isoformat()

    conn = get_connection()
    try:
        rows = fetch_all(
            conn,
            """SELECT b.user_id, b.month_start, b.subscription_credits, u.email
               FROM user_credit_balances b
               JOIN users u ON u.id = b.user_id
               WHERE b.subscription_credits > 0
                 AND b.month_start <= ?
                 AND b.month_start > ?""",
            (threshold_27d_ago, threshold_30d_ago),
        )
        report["users_scanned"] = len(rows)

        for row in rows:
            email = row["email"]
            if not email:
                continue
            sub = int(row["subscription_credits"])
            subject = "浑晶 credit 即将清零提醒"
            body = (
                f"你好,\n\n"
                f"你的订阅 credit 还剩 {sub} c,将在本月末清零。\n"
                f"如果不希望浪费,可以:\n"
                f"  · 立即跑一次 AI 推演 / 抽图谱(消耗本月订阅 credit)\n"
                f"  · 加购 credit 包(1 年有效,永不清零)\n\n"
                f"打开浑晶 → 右下角 ⓘ → 加购 credit\n\n"
                f"--\n浑晶 · 让每一个意难平,都有一个版本"
            )
            sent = _send_credit_email(email, subject, body)
            if sent:
                report["emails_sent"] += 1
                logger.info(f"month_end reminder sent: {email} (sub={sub} c)")
            else:
                report["errors"].append({"user_id": row["user_id"], "error": "SMTP send failed"})
    finally:
        conn.close()

    return report


# ============================================================
# 加购到期提醒(30/7/1 天前各发一次)
# ============================================================

def send_addon_expiry_reminders() -> dict[str, Any]:
    """扫 30/7/1 天后到期 + remaining_credits > 0 + is_expired=0 的 lot,发邮件提醒。

    简化:cron 每天跑,精确匹配"今天 + N 天后到期"(N ∈ {30, 7, 1}),
    每个 lot 在这 3 个时间点各发 1 次。
    幂等:同一天重复跑会重发(用户每天最多 1 封 / lot;3 个点共 3 封 / lot,可接受)。
    Sprint C.5 简化不加 reminders_sent 表,真正生产需加。

    返回:{"lots_scanned": int, "emails_sent": int, "errors": [...]}
    """
    report = {"lots_scanned": 0, "emails_sent": 0, "errors": []}

    now = datetime.now(timezone.utc)
    # 计算今天 + 30/7/1 天的 ISO 时间窗(±12 小时容忍 cron 跑时刻浮动)
    targets = [30, 7, 1]
    window_ranges = []
    for n in targets:
        target_day = now + timedelta(days=n)
        start = (target_day - timedelta(hours=12)).replace(microsecond=0).isoformat()
        end = (target_day + timedelta(hours=12)).replace(microsecond=0).isoformat()
        window_ranges.append((n, start, end))

    conn = get_connection()
    try:
        total_lots_seen = 0
        for days_left, start, end in window_ranges:
            rows = fetch_all(
                conn,
                """SELECT l.id, l.user_id, l.remaining_credits, l.expires_at, u.email
                   FROM addon_credit_lots l
                   JOIN users u ON u.id = l.user_id
                   WHERE l.is_expired = 0
                     AND l.remaining_credits > 0
                     AND l.expires_at >= ?
                     AND l.expires_at <= ?""",
                (start, end),
            )
            total_lots_seen += len(rows)

            for row in rows:
                email = row["email"]
                if not email:
                    continue
                remaining = int(row["remaining_credits"])
                subject = f"浑晶加购 credit {days_left} 天后过期"
                body = (
                    f"你好,\n\n"
                    f"你的一个加购 credit 包(剩 {remaining} c)将在 {days_left} 天后过期,"
                    f"过期 credit 将无法使用且不退款。\n\n"
                    f"建议在本周内跑一次 AI 推演 / 漫画生成消耗掉。\n\n"
                    f"打开浑晶 → 右下角 ⓘ → 看 Credit 消费记录\n\n"
                    f"--\n浑晶"
                )
                sent = _send_credit_email(email, subject, body)
                if sent:
                    report["emails_sent"] += 1
                    logger.info(
                        f"addon_expiry reminder sent: {email} lot={row['id']} "
                        f"days_left={days_left} remaining={remaining} c"
                    )
                else:
                    report["errors"].append(
                        {"lot_id": row["id"], "error": "SMTP send failed"}
                    )

        report["lots_scanned"] = total_lots_seen
    finally:
        conn.close()

    return report
