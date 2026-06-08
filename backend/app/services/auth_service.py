"""Auth 业务逻辑核心 — OTP 生成 / 限频 / 验证 + JWT 签发 / 解码。

设计原则:
- 不依赖 FastAPI(routers/auth.py 才是 HTTP 转译层)
- 时间统一 UTC ISO 8601
- OTP code 永不进数据库,只存 SHA-256 hash
- 异常分类清楚:OtpRateLimited(限频) / OtpInvalid(无效) / SmtpXxx(发送失败)
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

from app.config import settings
from app.db import execute, fetch_one, get_connection, transaction
from app.models.user import User
from app.services.otp_email_service import send_email_otp

# === 常量 ===
RESEND_COOLDOWN_SECONDS = 60          # 同邮箱重发间隔
DAILY_IP_LIMIT = 10                    # 同 IP 24h 内发码上限
OTP_TTL_SECONDS = 300                  # 5 分钟
OTP_LENGTH = 6                         # 6 位数字
JWT_ALGO = "HS256"
SSE_TOKEN_TTL_SECONDS = 900           # SSE 短期 token 15 分钟(Sprint 1.L)
SSE_TOKEN_AUDIENCE = "sse"            # JWT aud 字段,与主 JWT 隔离防误用


# === 异常 ===

class OtpRateLimited(Exception):
    """同邮箱 60s 内重发,或同 IP 24h 内 > 10 次。"""

    def __init__(self, retry_after: int):
        super().__init__(f"操作过于频繁,请 {retry_after} 秒后再试")
        self.retry_after = retry_after


class OtpInvalid(Exception):
    """验证码错误 / 已过期 / 已使用。"""


# === 时间工具 ===

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    """UTC ISO 8601,不带微秒。"""
    return dt.replace(microsecond=0).isoformat()


# === OTP 工具 ===

def _gen_code() -> str:
    """6 位数字,前导零保留(如 '003715')。"""
    return f"{secrets.randbelow(10 ** OTP_LENGTH):0{OTP_LENGTH}d}"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


# === 限频检查 ===

def _check_rate_limits(
    conn: sqlite3.Connection, target_email: str, sent_ip: Optional[str]
) -> None:
    """两层限频:① 同邮箱 60s ② 同 IP 24h 10 次。失败抛 OtpRateLimited。"""
    now = _now()
    cooldown_threshold = _iso(now - timedelta(seconds=RESEND_COOLDOWN_SECONDS))

    # 1. 同邮箱 60s 内不能重发
    last = fetch_one(
        conn,
        "SELECT created_at FROM otp_codes "
        "WHERE target_email=? AND created_at > ? "
        "ORDER BY created_at DESC LIMIT 1",
        (target_email, cooldown_threshold),
    )
    if last:
        sent_at = datetime.fromisoformat(last["created_at"])
        elapsed = (now - sent_at).total_seconds()
        retry_after = max(1, int(RESEND_COOLDOWN_SECONDS - elapsed))
        raise OtpRateLimited(retry_after)

    # 2. 同 IP 24h 内最多 10 次(IP 缺失则跳过此检查)
    if sent_ip:
        daily_threshold = _iso(now - timedelta(hours=24))
        cnt_row = fetch_one(
            conn,
            "SELECT COUNT(*) AS cnt FROM otp_codes "
            "WHERE sent_ip=? AND created_at > ?",
            (sent_ip, daily_threshold),
        )
        if cnt_row and cnt_row["cnt"] >= DAILY_IP_LIMIT:
            # 笼统提示 1 小时,避免精确暴露 24h 窗口
            raise OtpRateLimited(retry_after=3600)


# === 主流程:发送 OTP ===

def send_otp(target_email: str, sent_ip: Optional[str] = None) -> None:
    """生成 OTP + 发邮件 + 写库。

    顺序:① 限频检查 → ② 生成 code → ③ 发邮件(失败抛异常,不写库) → ④ 写库
    "先发邮件再写库"避免:邮件发送失败时数据库留无效记录,影响后续限频判断
    """
    conn = get_connection()
    try:
        _check_rate_limits(conn, target_email, sent_ip)
        code = _gen_code()
        send_email_otp(target_email, code)  # 失败抛 SmtpXxx,不进 with transaction

        with transaction(conn) as tx:
            now = _now()
            execute(
                tx,
                "INSERT INTO otp_codes "
                "(id, target_email, code_hash, sent_ip, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    target_email,
                    _hash_code(code),
                    sent_ip,
                    _iso(now),
                    _iso(now + timedelta(seconds=OTP_TTL_SECONDS)),
                ),
            )
    finally:
        conn.close()


# === 主流程:验证 OTP + 创建/取回用户 ===

def verify_otp(target_email: str, input_code: str, register_ua: Optional[str] = None) -> User:
    """验证 OTP + 创建 / 取回用户。失败抛 OtpInvalid。"""
    code_hash = _hash_code(input_code)
    conn = get_connection()
    try:
        with transaction(conn) as tx:
            now_iso = _iso(_now())

            # 1. 找未消费且未过期的匹配 OTP
            row = fetch_one(
                tx,
                "SELECT id, sent_ip FROM otp_codes "
                "WHERE target_email=? AND code_hash=? AND consumed=0 AND expires_at > ? "
                "ORDER BY created_at DESC LIMIT 1",
                (target_email, code_hash, now_iso),
            )
            if not row:
                raise OtpInvalid("验证码错误或已过期")

            otp_id = row["id"]
            register_ip = row["sent_ip"]

            # 2. 标记已消费(单次有效)
            execute(tx, "UPDATE otp_codes SET consumed=1 WHERE id=?", (otp_id,))

            # 3. 取回 / 创建用户
            user_row = fetch_one(tx, "SELECT * FROM users WHERE email=?", (target_email,))
            if user_row:
                user = User.from_row(user_row)
            else:
                user_id = str(uuid.uuid4())
                execute(
                    tx,
                    "INSERT INTO users "
                    "(id, email, plan, register_ip, register_ua, created_at, updated_at) "
                    "VALUES (?, ?, 'free', ?, ?, ?, ?)",
                    (user_id, target_email, register_ip, register_ua, now_iso, now_iso),
                )
                user = User(
                    id=user_id, phone=None, email=target_email, plan="free",
                    quota_reset_at=None, register_ip=register_ip, register_ua=register_ua,
                    created_at=now_iso, updated_at=now_iso,
                )

        # Sprint C.1:新用户首次访问 / 老用户重新登录 → 确保 wallet 已初始化
        # 局部 import 避免循环依赖(auth_service ← credit_service ← quota_service)
        # 注意:出事务后再调,因为 ensure_balance 内部有自己的 commit
        from app.services.credit_service import ensure_balance
        try:
            ensure_balance(conn, user.id, user.plan)
        except Exception as e:
            # 初始化 wallet 失败不阻塞登录(用户进系统后会被 lazy 兜底 ensure)
            import logging
            logging.warning(f"ensure_balance for {user.id} failed: {e}")

        return user
    finally:
        conn.close()


# === JWT ===

def issue_jwt(user: User) -> tuple[str, datetime]:
    """签发 JWT,返回 (token, expires_at)。"""
    expires_at = _now() + timedelta(seconds=settings.jwt_ttl_seconds)
    payload = {
        "sub": user.id,
        "plan": user.plan,
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGO)
    return token, expires_at


def decode_jwt(token: str) -> dict:
    """返回 payload。失败抛 jose.JWTError / ExpiredSignatureError(由 caller 处理)。"""
    return jwt.decode(token, settings.jwt_secret, algorithms=[JWT_ALGO])


# === 短期 SSE token(Sprint 1.L)===
#
# why 单独 token:
#   浏览器 EventSource API 不支持 Authorization header,只能把 token 写在
#   URL query param。主 JWT 7 天有效,暴露在 URL / nginx access log 风险大。
#   发一个 15 分钟 + 仅 SSE 用 + 带 aud='sse' 的隔离 token,降低暴露代价。
#
# why aud 隔离:
#   主 JWT 不带 aud,decode_jwt 验证时不传 audience 参数(jose 默认不校验);
#   SSE token 带 aud='sse',decode 时强制传 audience='sse',aud 不匹配的
#   token(包括主 JWT)会被 JWTClaimsError 拒。物理隔离防误用。

def issue_sse_token(user: User) -> str:
    """签发短期 SSE token,15 分钟内有效。"""
    payload = {
        "sub": user.id,
        "aud": SSE_TOKEN_AUDIENCE,
        "exp": int((_now() + timedelta(seconds=SSE_TOKEN_TTL_SECONDS)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=JWT_ALGO)


def decode_sse_token(token: str) -> str:
    """验证 SSE token + 返回 user_id。失败抛 JWTError 子类(由 caller 处理)。

    aud 隔离用**手动**验:
      - python-jose 在 token 含 aud 但 decode 没传 audience 时抛 JWTClaimsError
        (拦不下主 JWT 的"无 aud" 情况)
      - 在 token 不含 aud 但 decode 传了 audience 时**不校验**(放过主 JWT)
      - 两边行为不对称,正确做法是 options={"verify_aud": False} 关掉 jose 的
        audience 校验,自己显式比对 payload['aud'] == 'sse'
    """
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[JWT_ALGO],
        options={"verify_aud": False},
    )
    if payload.get("aud") != SSE_TOKEN_AUDIENCE:
        raise JWTError("非 SSE token(aud 不匹配)")
    user_id = payload.get("sub")
    if not user_id:
        raise JWTError("SSE token 缺 sub")
    return user_id
