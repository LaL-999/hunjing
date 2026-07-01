"""自动图谱抽取路由 — Sprint 2.B + 断点续抽 (2.B+)。

POST /api/uploads/{upload_id}/extract       触发(扣 1 次 continuation 配额)
POST /api/extract_jobs/{job_id}/reset       用户主动放弃 / 取消(可后续 resume)
POST /api/extract_jobs/{job_id}/resume      用户主动从断点继续(不扣新配额)
GET  /api/extract_jobs/{job_id}             状态轮询(2s)
GET  /api/projects/{project_id}/extract_jobs 该项目历史 job 列表
GET  /api/extract_jobs/{job_id}/stream      SSE 实时事件流
"""
from __future__ import annotations

import sqlite3
import traceback

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from jose import JWTError

from app.db import get_connection
from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.extract_job import ExtractJobResponse
from app.services.auth_service import decode_sse_token
from app.services.extract_service import (
    ExtractJobNotInReview,
    ExtractJobNotResettable,
    ExtractJobNotResumable,
    UploadNotExtractable,
    _attach_runtime_fields,
    approve_entities,
    get_extract_job_or_404,
    list_project_extract_jobs,
    reset_extract_job,
    resume_extract_job,
    stream_extract_state,
    trigger_extract,
)
from app.services.credit_service import InsufficientCredits, get_balance
from app.services.project_service import ResourceNotFoundOrForbidden

router = APIRouter()


@router.post(
    "/uploads/{upload_id}/extract",
    response_model=ExtractJobResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_create_extract(
    upload_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        # Sprint C.2:前置 credit 余额粗检(founder 跳过)
        # item7 修(2026-06-26):BYOK 用户走自己的 key,不查平台余额(避免误拦)
        from app.services.byok_service import get_active_llm_config
        if user.plan != "founder" and get_active_llm_config(conn, user.id) is None:
            balance = get_balance(conn, user.id)
            if balance.total <= 0:
                raise InsufficientCredits(
                    needed=1,
                    available=balance.total,
                    action="extract",
                )
        return trigger_extract(conn, upload_id, user.id, user.plan)
    except UploadNotExtractable as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "UPLOAD_NOT_EXTRACTABLE", "message": str(e)},
        )
    except InsufficientCredits as e:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "INSUFFICIENT_CREDITS",
                "needed": e.needed,
                "available": e.available,
                "action": e.action,
                "message": str(e),
            },
        )
    except ResourceNotFoundOrForbidden:
        raise   # 全局 404
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"extract trigger 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )


@router.get("/extract_jobs/{job_id}", response_model=ExtractJobResponse)
def api_get_extract_job(
    job_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        job = get_extract_job_or_404(conn, job_id, user.id)
        return _attach_runtime_fields(conn, job)
    except ResourceNotFoundOrForbidden:
        raise


@router.get(
    "/projects/{project_id}/extract_jobs",
    response_model=list[ExtractJobResponse],
)
def api_list_project_extract_jobs(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    try:
        return list_project_extract_jobs(conn, project_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise


@router.post(
    "/extract_jobs/{job_id}/reset",
    response_model=ExtractJobResponse,
)
def api_reset_extract_job(
    job_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """用户主动放弃当前抽取(取消 / 解除僵尸态)。

    场景:
      - backend 重启后看到"抽图谱中"是僵尸假象 → 点取消 → 改 db state='failed'
      - 抽到一半想换文件 → 点取消 → upload 翻回 'parsed',可重新触发或先 resume
      - **保留** chunk_results,允许后续 POST /resume 继续从断点抽取

    不退配额(LLM 已经为已抽完的块付了费,无法找回)。
    """
    try:
        return reset_extract_job(conn, job_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise
    except ExtractJobNotResettable as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "EXTRACT_NOT_RESETTABLE", "message": str(e)},
        )


@router.post(
    "/extract_jobs/{job_id}/resume",
    response_model=ExtractJobResponse,
)
def api_resume_extract_job(
    job_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """用户主动从断点继续抽取(不扣新配额)。

    前置:
      - job state='failed'(reset / worker 异常 / SIGKILL 后变僵尸再被 reset)
      - chunk_results 至少有 1 块(否则 = 全新抽取,应走 trigger 端点)

    流程:
      1. 校验前置 → 改 job state='queued' + upload state='extracting'
      2. kick_off_extract 起新 worker(同 job_id);worker 启动时 _load_completed_chunks
         → 跳过已完成块,只跑剩余的
      3. SSE 推 chunk_skipped 事件让前端看到"第 N 块已恢复(免抽)"

    异常:
      404  job 不存在或非该用户
      409  job 不满足 resume 条件(state≠failed / 0 已完成块 / 文件丢失)
    """
    try:
        return resume_extract_job(conn, job_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise
    except ExtractJobNotResumable as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "EXTRACT_NOT_RESUMABLE", "message": str(e)},
        )


# Sprint 6.A2 FOCUS.10(2026-05-22):人机协同审核 — 用户批准 entities 列表
from pydantic import BaseModel as _PydBM


class _ApproveEntitiesRequest(_PydBM):
    """审核完毕的 PERSON 增删请求。

    added_persons:用户手动 + 的角色,每个 dict 必须含 name(必),description 可选
    removed_names:用户标记移除的 PERSON name 列表
    """
    added_persons: list[dict] = []
    removed_names: list[str] = []


@router.post(
    "/extract_jobs/{job_id}/approve_entities",
    response_model=ExtractJobResponse,
)
def api_approve_entities(
    job_id: str,
    req: _ApproveEntitiesRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """用户审核完 entities 列表后批准 — 推进 worker 进入档案生成阶段。

    前置:job state='entities_pending_review'
    流程:更新 extracted_graph_json(过滤 removed + 加 added)→ state='generating_characters'
         → kick_off_extract(job_id)续跑,fast-path 跳过 graph 阶段直接进 profile

    Body:{ added_persons: [{name, description?}], removed_names: [str] }

    异常:
      404  job 不存在或非该用户
      409  job 不在 entities_pending_review 状态
    """
    try:
        return approve_entities(
            conn,
            job_id=job_id,
            user_id=user.id,
            added_persons=req.added_persons,
            removed_names=req.removed_names,
        )
    except ResourceNotFoundOrForbidden:
        raise
    except ExtractJobNotInReview as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "EXTRACT_NOT_IN_REVIEW", "message": str(e)},
        )
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"approve_entities 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )


@router.get("/extract_jobs/{job_id}/stream")
def api_stream_extract_job(
    job_id: str,
    token: str = Query(
        ...,
        description="短期 SSE token,通过 POST /api/auth/sse_token 换取(浏览器 EventSource 不支持自定义 header)",
    ),
) -> StreamingResponse:
    """SSE 流(镜像 simulation 1.L 模式)。

    事件 schema(详见 extract_service):
      snapshot / state_change / extract_graph_start / chunk_done / chunk_failed /
      chunk_skipped / extract_graph_done / characters_start / profile_start /
      profile_done / profile_failed / meta_inferred / save_done / done / error /
      heartbeat(`:` 注释行)

    终态(done / error)推完后服务端关闭连接。
    """
    # 1. 验 SSE token
    try:
        user_id = decode_sse_token(token)
    except JWTError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_SSE_TOKEN", "message": "SSE token 无效或已过期"},
        )

    # 2. 鉴权 + 资源验证(用一次性 conn,不放 dependency 因为 stream 长连)
    conn = get_connection()
    try:
        get_extract_job_or_404(conn, job_id, user_id)
    finally:
        conn.close()

    # 3. 开流
    return StreamingResponse(
        stream_extract_state(job_id, user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
