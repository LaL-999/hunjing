"""Auth 路由 — /api/auth/send_otp + /api/auth/verify + /api/auth/me。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.deps import get_client_ip, get_current_user
from app.models.user import User
from app.schemas.auth import (
    CurrentUserResponse,
    SendOtpRequest,
    SendOtpResponse,
    SseTokenResponse,
    VerifyOtpRequest,
    VerifyOtpResponse,
)
from app.services.auth_service import (
    SSE_TOKEN_TTL_SECONDS,
    OtpInvalid,
    OtpRateLimited,
    issue_jwt,
    issue_sse_token,
    send_otp,
    verify_otp,
)
from app.services.otp_email_service import SmtpNotConfigured, SmtpSendFailed

router = APIRouter()


@router.post("/send_otp", response_model=SendOtpResponse)
def api_send_otp(req: SendOtpRequest, request: Request) -> SendOtpResponse:
    ip = get_client_ip(request)
    try:
        send_otp(str(req.email), sent_ip=ip)
    except OtpRateLimited as e:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "RATE_LIMITED",
                "retry_after_seconds": e.retry_after,
                "message": str(e),
            },
        )
    except SmtpNotConfigured as e:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "SMTP_NOT_CONFIGURED", "message": str(e)},
        )
    except SmtpSendFailed as e:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail={"code": "SMTP_SEND_FAILED", "message": str(e)},
        )
    return SendOtpResponse()


@router.post("/verify", response_model=VerifyOtpResponse)
def api_verify(req: VerifyOtpRequest, request: Request) -> VerifyOtpResponse:
    user_agent = request.headers.get("User-Agent")
    try:
        user = verify_otp(str(req.email), req.code, register_ua=user_agent)
    except OtpInvalid as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "OTP_INVALID", "message": str(e)},
        )

    token, expires_at = issue_jwt(user)
    return VerifyOtpResponse(
        token=token,
        user_id=user.id,
        plan=user.plan,
        expires_at=expires_at.replace(microsecond=0).isoformat(),
    )


@router.get("/me", response_model=CurrentUserResponse)
def api_me(user: User = Depends(get_current_user)) -> CurrentUserResponse:
    """需要 Bearer JWT。Sprint 1.B 自检入口。"""
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        plan=user.plan,
        created_at=user.created_at,
        nickname=user.nickname,
        avatar_url=user.avatar_url,
    )


@router.post("/sse_token", response_model=SseTokenResponse)
def api_sse_token(user: User = Depends(get_current_user)) -> SseTokenResponse:
    """换发短期 SSE 专用 token(Sprint 1.L)。

    why:浏览器 EventSource API 不支持 Authorization header,只能把 token
    塞 URL query。主 JWT 有效期 7 天,放 URL 暴露代价大。这里换发一个
    15 分钟 + 仅 SSE 用 + 带 aud='sse' 的隔离 token,降低暴露风险。

    需要主 JWT(Bearer) 才能换发。前端在创建 EventSource 前调本接口。
    """
    return SseTokenResponse(
        token=issue_sse_token(user),
        expires_in=SSE_TOKEN_TTL_SECONDS,
    )
