"""Consent 路由 — /api/consent POST(创建)+ GET(列出当前用户的同意记录)。

对接前端 UploadOverlay 的 consent phase:
- 用户首次进入并勾完 4 项 → 前端 POST /api/consent
- 用户切换设备 / 验证 audit:GET /api/consent 看历史
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.db import execute, fetch_all
from app.deps import get_client_ip, get_current_user, get_db
from app.models.user import User
from app.schemas.consent import ConsentRecordResponse, CreateConsentRequest

router = APIRouter()


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@router.post("", response_model=ConsentRecordResponse, status_code=status.HTTP_201_CREATED)
def api_create_consent(
    req: CreateConsentRequest,
    request: Request,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> ConsentRecordResponse:
    # 二次校验:四项必须全勾(前端预校验后,后端再过一遍)
    checks = req.checks
    if not (checks.adult and checks.terms and checks.privacy and checks.pricing):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "CONSENT_INCOMPLETE",
                "message": "四项协议必须全部勾选(成年 + 用户协议 + 隐私政策 + 价格说明)",
            },
        )

    consent_id = str(uuid.uuid4())
    accepted_at = _iso_now()
    execute(
        conn,
        "INSERT INTO consent_records "
        "(id, user_id, version, checks, accepted_at, ip, ua) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            consent_id,
            user.id,
            req.version,
            json.dumps(checks.model_dump(), ensure_ascii=False),
            accepted_at,
            get_client_ip(request),
            request.headers.get("User-Agent"),
        ),
    )
    conn.commit()
    return ConsentRecordResponse(id=consent_id, version=req.version, accepted_at=accepted_at)


@router.get("", response_model=list[ConsentRecordResponse])
def api_list_consent(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[ConsentRecordResponse]:
    rows = fetch_all(
        conn,
        "SELECT id, version, accepted_at FROM consent_records "
        "WHERE user_id=? ORDER BY accepted_at DESC",
        (user.id,),
    )
    return [
        ConsentRecordResponse(id=r["id"], version=r["version"], accepted_at=r["accepted_at"])
        for r in rows
    ]
