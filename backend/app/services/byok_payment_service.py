"""
BYOK 个人收款码支付 service。

接口:
  create_order(conn, user_id, months) -> BYOKPaymentOrder
      生成订单 + 计算 expires_at。

  submit_proof(conn, user_id, order_id, image_bytes, mime) -> BYOKPaymentOrder
      保存截图到 backend/data/payment_proofs/{user_id}/{order_id}.ext
      SHA-256 防同图重投
      状态 → 'submitted',启后台 thread 跑 run_auto_review

  run_auto_review(order_id) -> None
      后台线程入口:
        1. 调 Vision LLM 识别截图
        2. 4 项严格校验(金额 / 收款方 / 时间 / 单号未重复)
        3. 全通过:status='paid' + 自动激活 BYOK 订阅
        4. 任一失败:status='manual_review' + 邮件通知管理员

  get_order_status(conn, user_id, order_id) -> PaymentOrderStatusResponse | None
      前端轮询用。

防重放设计:
  - SHA-256(整张图二进制)→ 同图直接拒
  - detected_transaction_id 全局 UNIQUE(同 28 位微信单号只能用一次)
  - PAY_TIME_WINDOW_SECONDS:付款时间必须在订单 created_at 后 24h 内

Vision LLM:
  - 复用 llm_routing.router.get_vision_llm()(Qwen-VL,项目已接)
  - 模型成本:¥0.01/张,完全可忽略
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from app.config import settings
from app.models.byok import BYOK_MONTHLY_PRICE_CENTS
from app.models.byok_payment import (
    AMOUNT_TOLERANCE_CENTS,
    ORDER_VALIDITY_SECONDS,
    PAY_TIME_WINDOW_SECONDS,
    BYOKPaymentOrder,
    PaymentOrderStatusResponse,
)
from app.services import byok_service
from app.services.byok_context import capture_current_context

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _proofs_dir() -> Path:
    """用户上传付款截图的存放目录(私有,不静态服务)"""
    p = settings.uploads_abs_dir.parent / "payment_proofs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _make_order_id() -> str:
    """订单 ID = ORD- + uuid hex 前 12 位(用户用作备注号)"""
    return "ORD-" + uuid.uuid4().hex[:12].upper()


def _format_amount_yuan(cents: int) -> str:
    """3000 → '¥30.00'"""
    yuan = cents / 100
    return f"¥{yuan:.2f}"


# ============================================================
# 订单创建
# ============================================================

def create_order(
    conn: sqlite3.Connection,
    user_id: str,
    months: int = 1,
) -> BYOKPaymentOrder:
    """生成支付订单。状态 'pending',24h 内必须上传截图。"""
    months = max(1, min(months, 12))
    amount = BYOK_MONTHLY_PRICE_CENTS * months
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=ORDER_VALIDITY_SECONDS)).isoformat()
    order_id = _make_order_id()

    conn.execute(
        """
        INSERT INTO byok_payment_orders
          (id, user_id, amount_cents, months, status, created_at, expires_at)
        VALUES (?, ?, ?, ?, 'pending', ?, ?)
        """,
        (order_id, user_id, amount, months, now.isoformat(), expires_at),
    )
    conn.commit()
    logger.info(
        "BYOK 支付订单创建 user=%s order=%s amount=%d months=%d",
        user_id, order_id, amount, months,
    )

    return _fetch_order(conn, order_id)


def _fetch_order(conn: sqlite3.Connection, order_id: str) -> BYOKPaymentOrder:
    row = conn.execute(
        "SELECT * FROM byok_payment_orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"order_not_found:{order_id}")
    return BYOKPaymentOrder.from_row(row)


def _fetch_order_or_none(conn: sqlite3.Connection, order_id: str) -> Optional[BYOKPaymentOrder]:
    row = conn.execute(
        "SELECT * FROM byok_payment_orders WHERE id = ?",
        (order_id,),
    ).fetchone()
    return BYOKPaymentOrder.from_row(row) if row else None


# ============================================================
# 截图提交
# ============================================================

class SubmitProofError(Exception):
    """业务异常 — endpoint 层捕获翻译成 4xx"""
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def submit_proof(
    conn: sqlite3.Connection,
    user_id: str,
    order_id: str,
    image_bytes: bytes,
    file_ext: str = ".png",
) -> BYOKPaymentOrder:
    """
    用户上传付款截图。

    流程:
    1. 校验订单存在 + 归属本人 + 状态 pending(submitted/paid/rejected 等都拒)
    2. 算 SHA-256,查重(同 hash 已被任何订单用过 → 拒)
    3. 落盘到 data/payment_proofs/{user_id}/{order_id}{ext}
    4. 状态 → submitted,启后台 review thread
    """
    order = _fetch_order_or_none(conn, order_id)
    if order is None:
        raise SubmitProofError("ORDER_NOT_FOUND", "订单不存在")
    if order.user_id != user_id:
        raise SubmitProofError("ORDER_FORBIDDEN", "订单不属于当前账号")
    if order.status != "pending":
        raise SubmitProofError(
            "ORDER_NOT_PENDING",
            f"订单状态为 {order.status},不能再次提交截图",
        )

    # 过期校验
    now_iso = _now_iso()
    if order.expires_at <= now_iso:
        # 标记过期再返
        conn.execute(
            "UPDATE byok_payment_orders SET status='expired' WHERE id=?",
            (order_id,),
        )
        conn.commit()
        raise SubmitProofError("ORDER_EXPIRED", "订单已过期,请重新下单")

    if not image_bytes or len(image_bytes) < 100:
        raise SubmitProofError("PROOF_INVALID", "截图文件为空 / 太小")
    if len(image_bytes) > 10 * 1024 * 1024:
        raise SubmitProofError("PROOF_TOO_LARGE", "截图超过 10 MB")

    # SHA-256 查重
    proof_hash = hashlib.sha256(image_bytes).hexdigest()
    dup = conn.execute(
        """
        SELECT id, status FROM byok_payment_orders
         WHERE proof_image_hash = ? AND id != ?
         LIMIT 1
        """,
        (proof_hash, order_id),
    ).fetchone()
    if dup is not None:
        raise SubmitProofError(
            "PROOF_REUSED",
            f"该截图已被订单 {dup['id']} 使用过,不能重复提交",
        )

    # 落盘
    user_dir = _proofs_dir() / user_id
    user_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{order_id}{file_ext}"
    abs_path = user_dir / fname
    abs_path.write_bytes(image_bytes)
    rel_path = f"{user_id}/{fname}"

    # 状态切 submitted
    conn.execute(
        """
        UPDATE byok_payment_orders
           SET status = 'submitted',
               proof_image_path = ?,
               proof_image_hash = ?,
               proof_submitted_at = ?
         WHERE id = ?
        """,
        (rel_path, proof_hash, now_iso, order_id),
    )
    conn.commit()
    logger.info(
        "BYOK 截图已收 order=%s hash=%s, 启动 Vision LLM 审核",
        order_id, proof_hash[:16],
    )

    # 启后台审核 — capture context 把 user_id 带进 thread(虽然审核内部不调 BYOK LLM,
    # 但保持一致性 + 未来若审核也走 LLM-as-judge 可复用 user_id)
    ctx = capture_current_context()
    threading.Thread(
        target=ctx.run,
        args=(run_auto_review, order_id),
        daemon=True,
        name=f"byok-review-{order_id[:8]}",
    ).start()

    return _fetch_order(conn, order_id)


# ============================================================
# 自动审核(Vision LLM)
# ============================================================

REVIEW_SYSTEM_PROMPT = """你是付款截图识别专家。用户会上传一张微信 / 支付宝转账成功的截图,你需要严格识别 4 项信息,并以 JSON 输出。

输出 schema(JSON,**不要任何其他文字 / markdown 围栏**):
{
  "amount_yuan": <number, 付款金额,单位 元,2 位小数;识别不到填 null>,
  "payee_name": <string, 收款方姓名;识别不到填 null>,
  "pay_time": <string, ISO8601 时间(yyyy-MM-ddTHH:mm:ss),北京时间;识别不到填 null>,
  "transaction_id": <string, 微信交易单号 / 支付宝交易号(长串数字 / 字母数字混合);识别不到填 null>,
  "is_success": <boolean, 截图是否显示"付款成功"/"转账成功"/"已支付"等成功状态>,
  "confidence": <number, 0.0-1.0,整体识别置信度>,
  "raw_text_observed": <string, 你在截图中看到的关键文字片段(原文,用于 audit)>
}

注意:
- 金额识别要小心 — 如 "¥30.00" → 30.00,"30元" → 30.00
- 收款方姓名:微信中央显示的姓名(通常加*号脱敏,如"李*爽" → 输出 "李*爽")
- 付款时间:微信详情页"支付时间"字段,北京时间;若无年份,默认当前年
- 交易单号:微信"商家订单号" / "交易单号" / 支付宝"商家流水号"(28+ 位)
- 若是截图无法识别(图模糊 / 不是付款截图 / 是收款方截图)→ 所有字段填 null + is_success=false + confidence<0.3"""


def _call_vision_llm_on_proof(image_path: Path) -> dict:
    """调 Vision LLM 识别截图,返回 dict(LLM 原始 JSON 解码结果)。

    异常上抛 — 调用方决定 rejected_reason。
    """
    from app.services.llm_routing.router import get_vision_llm

    # Qwen-VL 接受 base64 编码或 URL;走 base64 最直接
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")

    vision = get_vision_llm()

    # VisionLlm 协议 .call_json(system, image_b64) — 看 protocols.py 真实签名
    # 兼容多种 adapter:有些用 base64,有些用 URL,有些用 data URI
    # 这里走兼容 dictpayload 形式
    result, _usage = vision.call_json(
        system_prompt=REVIEW_SYSTEM_PROMPT,
        user_input={
            "instruction": "识别这张付款截图的 4 项信息",
            "image_base64": image_b64,
        },
        max_tokens=1000,
        temperature=0.0,
    )
    return result


def _parse_yuan_to_cents(yuan_value) -> Optional[int]:
    """支持 30 / 30.00 / "30.00" / "¥30" / None"""
    if yuan_value is None:
        return None
    if isinstance(yuan_value, (int, float)):
        return int(round(float(yuan_value) * 100))
    if isinstance(yuan_value, str):
        m = re.search(r"(\d+(?:\.\d+)?)", yuan_value.replace(",", ""))
        if m:
            return int(round(float(m.group(1)) * 100))
    return None


def _normalize_time_to_iso(time_str) -> Optional[str]:
    """识别的时间字符串归一为 ISO。LLM 可能输出 "2026-06-05 10:30:00" / 已是 ISO"""
    if not time_str or not isinstance(time_str, str):
        return None
    # 替换中文 / 空格 → T
    s = time_str.replace("年", "-").replace("月", "-").replace("日", " ")
    s = s.replace(" ", "T")
    # 兼容时区缺失 — 当作北京时间(UTC+8)
    try:
        # 已含时区
        if "+" in s or s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat()
        # 纯本地时间 → 当作北京时间
        dt = datetime.fromisoformat(s)
        dt = dt.replace(tzinfo=timezone(timedelta(hours=8)))
        return dt.isoformat()
    except (ValueError, TypeError):
        return None


def _validate_detection(
    order: BYOKPaymentOrder,
    detection: dict,
) -> tuple[bool, str]:
    """
    严格 4 校验。

    返回 (passed, reason)。
    全过 → (True, "通过原因摘要")
    任一不过 → (False, "失败原因")
    """
    # 1. 截图必须显示"付款成功"
    if not detection.get("is_success"):
        return False, "截图未显示'付款成功'状态"

    # 2. 金额校验
    detected_yuan = detection.get("amount_yuan")
    detected_cents = _parse_yuan_to_cents(detected_yuan)
    if detected_cents is None:
        return False, "未识别到付款金额"
    if abs(detected_cents - order.amount_cents) > AMOUNT_TOLERANCE_CENTS:
        return False, (
            f"金额不符:截图 {_format_amount_yuan(detected_cents)} "
            f"vs 订单 {_format_amount_yuan(order.amount_cents)}"
        )

    # 3. 收款方姓名校验
    payee = detection.get("payee_name") or ""
    expected = settings.byok_payee_name          # 真实姓名,仅用于比对,绝不外泄
    # 微信会脱敏中间字(如"李*爽"),只要首字 + 尾字一致即过
    if not payee:
        return False, "未识别到收款方姓名"
    if not _payee_name_match(payee, expected):
        # 用户可见文案只提展示名(品牌名),不回显真实姓名
        return False, (
            f"收款方姓名不符:截图识别为 '{payee}',"
            f"请确认扫的是平台【{settings.byok_payee_display_name}】的收款码"
        )

    # 4. 付款时间窗
    pay_time_iso = _normalize_time_to_iso(detection.get("pay_time"))
    if pay_time_iso is None:
        return False, "未识别到付款时间"
    try:
        pay_time = datetime.fromisoformat(pay_time_iso)
        if pay_time.tzinfo is None:
            pay_time = pay_time.replace(tzinfo=timezone(timedelta(hours=8)))
        order_created = datetime.fromisoformat(order.created_at)
        if order_created.tzinfo is None:
            order_created = order_created.replace(tzinfo=timezone.utc)
        # 允许付款时间在订单创建前最多 5 分钟(用户先付款后下单的情况)
        # 允许付款时间在订单创建后 24h 内
        delta = (pay_time - order_created).total_seconds()
        if delta < -5 * 60:
            return False, "付款时间早于订单创建,可能是旧截图"
        if delta > PAY_TIME_WINDOW_SECONDS:
            return False, "付款时间超过订单 24h 时限"
    except (ValueError, TypeError) as exc:
        return False, f"付款时间解析失败:{exc}"

    # 5. 交易单号必须存在(同步 transaction_id 写入会触发 UNIQUE 约束,防一图多用)
    if not detection.get("transaction_id"):
        return False, "未识别到交易单号"

    confidence = detection.get("confidence", 0.0) or 0.0
    # 不回显真实姓名(pass_reason 会随订单状态返给用户端)
    pass_reason = (
        f"金额 ✓ 收款方 ✓ 时间 ✓ 单号 ✓ 置信度 {confidence:.2f}"
    )
    return True, pass_reason


def _payee_name_match(detected: str, expected: str) -> bool:
    """
    收款方姓名匹配(容许微信脱敏 * 号)。

    "李*爽" vs "李爽" → True(首末字一致 + 中间是 *)
    "李*" vs "李爽"   → True(首字 + * 兜底)
    "*爽" vs "李爽"   → True(末字 + * 兜底)
    "王*强" vs "李爽" → False
    """
    if detected == expected:
        return True
    # 简单规则:首字一致 OR 末字一致(都得对至少一个有效字符)
    if not detected or not expected:
        return False
    # 去掉 * 后比较关键字符
    detected_chars = [c for c in detected if c != "*"]
    if not detected_chars:
        return False
    # 至少首字 OR 末字必须命中 expected 中的对应位置
    head_match = detected[0] == expected[0] if expected else False
    tail_match = detected[-1] == expected[-1] if expected else False
    if head_match and tail_match:
        return True
    # 容忍场景:detected 全字都在 expected 中(脱敏过)
    if all(c in expected for c in detected_chars):
        return True
    return False


def run_auto_review(order_id: str) -> None:
    """
    后台 thread 入口 — Vision LLM 审核 + 校验 + 激活 / reject。

    所有异常自捕获 — 任何阶段挂掉 → status='manual_review'。
    """
    from app.db import get_connection
    conn = get_connection()
    try:
        order = _fetch_order_or_none(conn, order_id)
        if order is None or order.status != "submitted":
            logger.warning("review skipped: order=%s status=%s", order_id, order and order.status)
            return

        proof_abs = _proofs_dir() / order.proof_image_path
        if not proof_abs.exists():
            _mark_order_status(
                conn, order_id, "manual_review",
                reason="截图文件丢失,需人工核查",
            )
            _notify_admin(order_id, "截图文件丢失")
            return

        # 调 Vision LLM
        try:
            detection = _call_vision_llm_on_proof(proof_abs)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Vision LLM 调用失败 order=%s", order_id)
            _mark_order_status(
                conn, order_id, "manual_review",
                reason=f"Vision LLM 调用失败:{type(exc).__name__}",
                detection_raw_json=json.dumps({"error": str(exc)[:500]}, ensure_ascii=False),
            )
            _notify_admin(order_id, f"Vision LLM 失败:{exc}")
            return

        # 校验 4 项
        passed, reason = _validate_detection(order, detection)
        transaction_id = detection.get("transaction_id")
        amount_cents = _parse_yuan_to_cents(detection.get("amount_yuan"))
        pay_time_iso = _normalize_time_to_iso(detection.get("pay_time"))

        # 写识别结果(无论通过与否都留底)
        try:
            conn.execute(
                """
                UPDATE byok_payment_orders SET
                    detected_amount_cents = ?,
                    detected_payee_name = ?,
                    detected_pay_time = ?,
                    detected_transaction_id = ?,
                    detection_raw_json = ?,
                    detection_run_at = ?,
                    detection_pass_reason = ?
                 WHERE id = ?
                """,
                (
                    amount_cents,
                    detection.get("payee_name"),
                    pay_time_iso,
                    transaction_id,
                    json.dumps(detection, ensure_ascii=False)[:5000],
                    _now_iso(),
                    reason,
                    order_id,
                ),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            # detected_transaction_id UNIQUE 约束被触发:同一笔交易已被别的订单激活过
            logger.warning("transaction_id 重复 order=%s: %s", order_id, exc)
            _mark_order_status(
                conn, order_id, "rejected",
                reason=f"交易单号 {transaction_id} 已被其他订单使用",
            )
            return

        if not passed:
            # 校验失败 → 人工 review(不直接 reject,因为可能是 LLM 误识别 / 截图角度问题)
            _mark_order_status(
                conn, order_id, "manual_review",
                reason=reason,
            )
            _notify_admin(order_id, f"自动校验未过:{reason}")
            return

        # 全通过 → 激活 BYOK 订阅
        try:
            sub = byok_service.purchase_subscription(conn, order.user_id, months=order.months)
            byok_service.activate_code(conn, order.user_id, sub.code)
            conn.execute(
                """
                UPDATE byok_payment_orders SET
                    status = 'paid',
                    activated_subscription_id = ?
                 WHERE id = ?
                """,
                (sub.id, order_id),
            )
            conn.commit()
            logger.info(
                "BYOK 订单 %s 审核通过,自动激活订阅 %s (user=%s)",
                order_id, sub.id, order.user_id,
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("审核通过但激活失败 order=%s", order_id)
            _mark_order_status(
                conn, order_id, "manual_review",
                reason=f"审核通过但激活时出错:{exc}",
            )
            _notify_admin(order_id, f"激活失败:{exc}")

    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def _mark_order_status(
    conn: sqlite3.Connection,
    order_id: str,
    status: str,
    *,
    reason: Optional[str] = None,
    detection_raw_json: Optional[str] = None,
) -> None:
    if detection_raw_json:
        conn.execute(
            """
            UPDATE byok_payment_orders
               SET status = ?, rejected_reason = ?,
                   detection_raw_json = ?, detection_run_at = ?
             WHERE id = ?
            """,
            (status, reason, detection_raw_json, _now_iso(), order_id),
        )
    else:
        conn.execute(
            """
            UPDATE byok_payment_orders
               SET status = ?, rejected_reason = ?
             WHERE id = ?
            """,
            (status, reason, order_id),
        )
    conn.commit()


def _notify_admin(order_id: str, message: str) -> None:
    """向管理员发邮件通知(manual_review 时触发)。

    复用 auth_service.send_email_otp 的 SMTP 链路 — 但这里我们写自定义内容。
    SMTP 未配置时静默 log。
    """
    if not settings.smtp_configured():
        logger.warning(
            "SMTP 未配置,跳过 BYOK 订单 %s 的管理员通知:%s",
            order_id, message,
        )
        return
    try:
        import smtplib
        from email.mime.text import MIMEText
        from email.utils import formataddr

        msg_body = (
            f"浑晶平台 — BYOK 订单需人工审核\n\n"
            f"订单 ID:{order_id}\n"
            f"原因:{message}\n\n"
            f"请到管理后台查看截图 + 决定通过 / 拒绝。"
        )
        msg = MIMEText(msg_body, "plain", "utf-8")
        msg["Subject"] = f"[浑晶][BYOK] 订单 {order_id} 需人工审核"
        msg["From"] = formataddr(("浑晶系统通知", settings.smtp_from))
        msg["To"] = settings.byok_review_notify_email

        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=15) as s:
            s.login(settings.smtp_user, settings.smtp_pass)
            s.send_message(msg)
        logger.info("已向 %s 发送审核通知 order=%s", settings.byok_review_notify_email, order_id)
    except Exception:  # noqa: BLE001
        logger.exception("发送审核邮件失败 order=%s", order_id)


# ============================================================
# 状态查询
# ============================================================

def get_order_status_response(
    conn: sqlite3.Connection,
    user_id: str,
    order_id: str,
) -> Optional[PaymentOrderStatusResponse]:
    order = _fetch_order_or_none(conn, order_id)
    if order is None or order.user_id != user_id:
        return None

    activated_code = None
    activated_expires = None
    if order.activated_subscription_id:
        sub_row = conn.execute(
            "SELECT code, expires_at FROM byok_subscriptions WHERE id = ?",
            (order.activated_subscription_id,),
        ).fetchone()
        if sub_row:
            activated_code = sub_row["code"]
            activated_expires = sub_row["expires_at"]

    return PaymentOrderStatusResponse(
        order_id=order.id,
        status=order.status,
        amount_cents=order.amount_cents,
        months=order.months,
        created_at=order.created_at,
        expires_at=order.expires_at,
        proof_submitted_at=order.proof_submitted_at,
        detected_amount_cents=order.detected_amount_cents,
        detected_payee_name=order.detected_payee_name,
        detected_pay_time=order.detected_pay_time,
        detection_pass_reason=order.detection_pass_reason,
        rejected_reason=order.rejected_reason,
        activated_code=activated_code,
        activated_expires_at=activated_expires,
    )


def list_orders(
    conn: sqlite3.Connection,
    user_id: str,
    limit: int = 20,
) -> list[PaymentOrderStatusResponse]:
    rows = conn.execute(
        """
        SELECT id FROM byok_payment_orders
         WHERE user_id = ?
         ORDER BY created_at DESC
         LIMIT ?
        """,
        (user_id, limit),
    ).fetchall()
    out = []
    for r in rows:
        resp = get_order_status_response(conn, user_id, r["id"])
        if resp:
            out.append(resp)
    return out


# ============================================================
# Cron — 过期未提交订单清理
# ============================================================

# ============================================================
# Admin 后台 — 审核 manual_review 订单(2026-06-05)
# ============================================================

def admin_list_orders(
    conn: sqlite3.Connection,
    *,
    status_filter: Optional[str] = None,
    limit: int = 100,
) -> tuple[list[dict], int, int]:
    """Admin 列单 — 返 (orders 字典列表, 总数, manual_review 计数)。

    status_filter:
      - 'manual_review' / 'pending' / 'submitted' / 'paid' / 'rejected' / 'expired'
      - None / 'all' → 全部
    """
    where_sql = ""
    params: list = []
    if status_filter and status_filter != "all":
        where_sql = "WHERE o.status = ?"
        params.append(status_filter)

    # JOIN users 拿 email 给 admin 看
    rows = conn.execute(
        f"""
        SELECT o.*, u.email AS user_email
          FROM byok_payment_orders o
     LEFT JOIN users u ON u.id = o.user_id
          {where_sql}
      ORDER BY
          CASE o.status
            WHEN 'manual_review' THEN 0
            WHEN 'submitted'     THEN 1
            WHEN 'pending'       THEN 2
            WHEN 'paid'          THEN 3
            ELSE 4
          END,
          o.created_at DESC
         LIMIT ?
        """,
        (*params, limit),
    ).fetchall()

    total_row = conn.execute(
        "SELECT COUNT(*) AS c FROM byok_payment_orders"
    ).fetchone()
    pending_row = conn.execute(
        "SELECT COUNT(*) AS c FROM byok_payment_orders WHERE status='manual_review'"
    ).fetchone()

    out: list[dict] = []
    for r in rows:
        # 取激活码(若已 paid 关联到 sub)
        activated_code = None
        activated_expires = None
        if r["activated_subscription_id"]:
            sub_row = conn.execute(
                "SELECT code, expires_at FROM byok_subscriptions WHERE id = ?",
                (r["activated_subscription_id"],),
            ).fetchone()
            if sub_row:
                activated_code = sub_row["code"]
                activated_expires = sub_row["expires_at"]

        out.append({
            "order_id": r["id"],
            "user_id": r["user_id"],
            "user_email": r["user_email"],
            "status": r["status"],
            "amount_cents": r["amount_cents"],
            "months": r["months"],
            "created_at": r["created_at"],
            "expires_at": r["expires_at"],
            "proof_submitted_at": r["proof_submitted_at"],
            "proof_image_url": (
                f"/api/admin/byok/orders/{r['id']}/proof_image"
                if r["proof_image_path"] else None
            ),
            "detected_amount_cents": r["detected_amount_cents"],
            "detected_payee_name": r["detected_payee_name"],
            "detected_pay_time": r["detected_pay_time"],
            "detected_transaction_id": r["detected_transaction_id"],
            "detection_pass_reason": r["detection_pass_reason"],
            "detection_run_at": r["detection_run_at"],
            "rejected_reason": r["rejected_reason"],
            "activated_subscription_id": r["activated_subscription_id"],
            "activated_code": activated_code,
            "activated_expires_at": activated_expires,
        })

    return out, total_row["c"], pending_row["c"]


def admin_approve_order(
    conn: sqlite3.Connection,
    order_id: str,
    *,
    admin_user_id: str,
    note: Optional[str] = None,
) -> dict:
    """管理员审核通过 — paid + 激活 BYOK 订阅。

    幂等:已 paid 的订单不重复处理(返当前订单详情)。
    抛 ValueError:订单不存在 / 状态不允许审批(已 expired / rejected)。
    """
    order = _fetch_order_or_none(conn, order_id)
    if order is None:
        raise ValueError("订单不存在")

    if order.status == "paid":
        # 幂等返回
        out, _, _ = admin_list_orders(conn, status_filter="all", limit=1000)
        match = next((x for x in out if x["order_id"] == order_id), None)
        return match or {}

    if order.status not in ("submitted", "manual_review", "pending"):
        raise ValueError(f"订单当前状态 {order.status},不能审批通过")

    # 激活 BYOK 订阅
    sub = byok_service.purchase_subscription(conn, order.user_id, months=order.months)
    byok_service.activate_code(conn, order.user_id, sub.code)

    # 写订单 → paid + 关联 sub + 审计备注
    audit_note = (
        f"[admin={admin_user_id}@{_now_iso()}] 手动审核通过"
        + (f"; note={note}" if note else "")
    )
    conn.execute(
        """
        UPDATE byok_payment_orders
           SET status='paid',
               activated_subscription_id=?,
               detection_pass_reason=COALESCE(detection_pass_reason || ' | ', '') || ?
         WHERE id=?
        """,
        (sub.id, audit_note, order_id),
    )
    conn.commit()
    logger.info("admin %s 审批通过订单 %s → 激活 sub=%s", admin_user_id, order_id, sub.id)

    out, _, _ = admin_list_orders(conn, status_filter="all", limit=1000)
    match = next((x for x in out if x["order_id"] == order_id), None)
    return match or {}


def admin_reject_order(
    conn: sqlite3.Connection,
    order_id: str,
    *,
    admin_user_id: str,
    reason: str,
) -> dict:
    """管理员拒绝 — status=rejected + 写明拒绝原因。

    抛 ValueError:订单不存在 / 已 paid(不能拒绝已通过的)。
    """
    if not reason or not reason.strip():
        raise ValueError("拒绝原因不能为空")

    order = _fetch_order_or_none(conn, order_id)
    if order is None:
        raise ValueError("订单不存在")
    if order.status == "paid":
        raise ValueError("已通过审核的订单不能拒绝(如需退款请联系客服)")
    if order.status == "rejected":
        # 幂等
        out, _, _ = admin_list_orders(conn, status_filter="all", limit=1000)
        match = next((x for x in out if x["order_id"] == order_id), None)
        return match or {}

    audit_note = f"[admin={admin_user_id}@{_now_iso()}] 手动拒绝: {reason.strip()}"
    conn.execute(
        """
        UPDATE byok_payment_orders
           SET status='rejected',
               rejected_reason=?
         WHERE id=?
        """,
        (audit_note, order_id),
    )
    conn.commit()
    logger.info("admin %s 拒绝订单 %s: %s", admin_user_id, order_id, reason)

    out, _, _ = admin_list_orders(conn, status_filter="all", limit=1000)
    match = next((x for x in out if x["order_id"] == order_id), None)
    return match or {}


def admin_get_proof_image_path(
    conn: sqlite3.Connection,
    order_id: str,
) -> Optional[Path]:
    """返截图文件绝对路径(给 admin 看)。文件不存在或订单无截图返 None。"""
    order = _fetch_order_or_none(conn, order_id)
    if order is None or not order.proof_image_path:
        return None
    proof_abs = _proofs_dir() / order.proof_image_path
    if not proof_abs.exists():
        return None
    return proof_abs


# ============================================================
# Cron — 过期未提交订单清理
# ============================================================

def expire_old_orders(conn: sqlite3.Connection) -> int:
    """把超过 expires_at 的 pending 订单标 expired。返回处理条数。"""
    now_iso = _now_iso()
    cursor = conn.execute(
        """
        UPDATE byok_payment_orders
           SET status = 'expired'
         WHERE status = 'pending' AND expires_at <= ?
        """,
        (now_iso,),
    )
    affected = cursor.rowcount
    conn.commit()
    if affected:
        logger.info("BYOK 支付订单过期 %d 条", affected)
    return affected
