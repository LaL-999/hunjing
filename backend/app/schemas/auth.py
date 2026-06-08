"""Auth API schema — /api/auth/send_otp + /api/auth/verify。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class SendOtpRequest(BaseModel):
    email: EmailStr


class SendOtpResponse(BaseModel):
    ok: bool = True
    message: str = "验证码已发送到你的邮箱,5 分钟内有效;请检查收件箱与垃圾邮件"


class VerifyOtpRequest(BaseModel):
    email: EmailStr
    code: str = Field(
        ..., min_length=6, max_length=6, pattern=r"^\d{6}$",
        description="6 位数字验证码"
    )


class VerifyOtpResponse(BaseModel):
    token: str
    user_id: str
    plan: str
    expires_at: str  # ISO 8601


class CurrentUserResponse(BaseModel):
    """GET /api/auth/me 返回。"""
    id: str
    email: Optional[str]
    plan: str
    created_at: str


class SseTokenResponse(BaseModel):
    """POST /api/auth/sse_token — 短期 SSE 专用 token(Sprint 1.L)。"""
    token: str
    expires_in: int  # 秒
