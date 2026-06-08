"""FastAPI Depends 工厂 — 路由层用 Depends() 注入。"""
from __future__ import annotations

import sqlite3
from dataclasses import replace
from typing import Generator, Optional

from fastapi import Depends, Header, HTTPException, Request, status
from jose import ExpiredSignatureError, JWTError

from app.config import settings
from app.db import fetch_one, get_connection
from app.models.user import User
from app.services.auth_service import decode_jwt


def get_db() -> Generator[sqlite3.Connection, None, None]:
    """每个请求一个 connection,请求结束自动 close。"""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def get_client_ip(request: Request) -> Optional[str]:
    """优先 X-Forwarded-For 第一段(走 nginx 反代时);降级到 request.client.host。"""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    conn: sqlite3.Connection = Depends(get_db),
) -> User:
    """Bearer JWT → User 对象。失败抛 401。"""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "MISSING_TOKEN", "message": "缺少 Authorization Bearer token"},
        )
    token = authorization[len("Bearer "):]

    try:
        payload = decode_jwt(token)
    except ExpiredSignatureError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "TOKEN_EXPIRED", "message": "token 已过期,请重新登录"},
        )
    except JWTError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "TOKEN_INVALID", "message": "无效 token"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "TOKEN_MALFORMED", "message": "token 缺少 sub 字段"},
        )

    row = fetch_one(conn, "SELECT * FROM users WHERE id=?", (user_id,))
    if not row:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "USER_NOT_FOUND", "message": "用户不存在(可能已删除)"},
        )
    user = User.from_row(row)

    # 创始人邮箱 → 内存改写 plan='founder'(不写库,DB 仍是原值)
    # PLAN_LIMITS['founder'] 各项 999999,所有 enforce_*_quota 自动放行
    if user.email and user.email.lower() in settings.founder_emails:
        user = replace(user, plan="founder")

    # 2026-06-05:BYOK 自携密钥 — 设 ContextVar,让 LLM client 最深处也能拿到 user_id
    # 用于查 BYOK 配置:有则用用户 key,无则平台默认
    # contextvars 在同步 / async 调用链自动跟随,thread 中需 copy_context()(simulation/outline async runner 已改造)
    from app.services.byok_context import set_current_user_id
    set_current_user_id(user.id)

    return user


def require_founder_or_admin_token(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token"),
    conn: sqlite3.Connection = Depends(get_db),
) -> User:
    """admin-only endpoint 双轨鉴权(2026-06-05):

    - 优先 X-Admin-Token 路径 — 用于洞察后台(独立服务,不持有用户 JWT)
    - 回退 Bearer JWT 路径 — 用于主平台同源调用(founder 邮箱)

    任一通过即放行;都不通过则 401/403。

    为什么 X-Admin-Token 路径返回的 User 是合成的:
      - 洞察后台是 service-to-service 调用,不绑定具体用户
      - 但下游 service 函数签名要 user.id 写审计(admin_user_id)
      - 合成一个 id='__insights_admin__' 的伪 User,便于追溯日志
    """
    # 路径 1:X-Admin-Token(洞察后台)
    if x_admin_token:
        if x_admin_token != settings.insights_admin_token:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={"code": "INVALID_ADMIN_TOKEN", "message": "X-Admin-Token 无效"},
            )
        # 合成 founder 身份(用作审计标识)
        return User(
            id="__insights_admin__",
            phone=None,
            email="admin@insights",
            plan="founder",
            quota_reset_at=None,
            register_ip=None,
            register_ua=None,
            created_at="",
            updated_at="",
        )

    # 路径 2:Bearer JWT + founder 邮箱
    user = get_current_user(authorization=authorization, conn=conn)
    if user.plan != "founder":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "FOUNDER_ONLY", "message": "仅创始人账号或洞察后台 token 可访问"},
        )
    return user


# 别名:保持原命名,旧 import 不破
require_founder = require_founder_or_admin_token
