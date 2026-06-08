"""Upload 路由 — Sprint 2.A 中间态文件上传。

POST /api/projects/{project_id}/uploads     multipart/form-data,file 字段
GET  /api/projects/{project_id}/uploads     列出项目所有 uploads
GET  /api/uploads/{id}                       单条详情
DELETE /api/uploads/{id}                     删 DB 行 + 删磁盘文件
"""
from __future__ import annotations

import sqlite3
import traceback

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from app.deps import get_client_ip, get_current_user, get_db
from app.models.user import User
from app.schemas.upload import UploadResponse
from app.services.project_service import ResourceNotFoundOrForbidden
from app.services.upload_service import (
    ProjectAlreadyHasUpload,
    UploadDuplicate,
    UploadFormatRejected,
    UploadParseFailed,
    UploadRejectedRedFlag,
    UploadRequest,
    delete_upload,
    get_upload_or_404,
    list_project_uploads,
    list_ready_uploads_for_user,
    receive_upload,
)

router = APIRouter()


@router.post(
    "/projects/{project_id}/uploads",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def api_create_upload(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    raw = await file.read()
    req = UploadRequest(
        project_id=project_id,
        user_id=user.id,
        filename=file.filename or "unnamed",
        declared_mime=file.content_type or "application/octet-stream",
        raw_bytes=raw,
    )
    ip = get_client_ip(request)
    try:
        return receive_upload(conn, req, ip_address=ip)
    except UploadFormatRejected as e:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "UPLOAD_FORMAT_REJECTED", "message": str(e)},
        )
    except UploadDuplicate as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "UPLOAD_DUPLICATE",
                "message": str(e),
                "existing_upload_id": e.existing_upload_id,
            },
        )
    except ProjectAlreadyHasUpload as e:
        # Sprint 6.A2 M7.E(2026-05-20)— 一个项目只能有一个作品文件
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "PROJECT_ALREADY_HAS_UPLOAD",
                "message": str(e),
                "existing_upload_id": e.existing_upload_id,
                "existing_filename": e.existing_filename,
            },
        )
    except UploadParseFailed as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "UPLOAD_PARSE_FAILED", "message": str(e)},
        )
    except UploadRejectedRedFlag as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "UPLOAD_RED_FLAG",
                "category": e.category,
                "matched_text": e.matched_text,
                "message": "内容含红旗词,无法上传(详情见 category)",
            },
        )
    except ResourceNotFoundOrForbidden:
        raise   # main.py 全局 handler 转 404
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"upload 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )


@router.get(
    "/projects/{project_id}/uploads", response_model=list[UploadResponse]
)
def api_list_uploads(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    try:
        return list_project_uploads(conn, project_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise


@router.get("/uploads/ready")
def api_list_ready_uploads(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """Sprint 4.D(2026-05-13):列出当前用户所有 state='ready' 的 uploads。

    用途:漫画 CreateComicModal 的 external tab 让用户选已上传的文本作为漫画原文。
    返回带 project_name(LEFT JOIN projects)— 用户视角知道"这个文件来自哪个项目"。

    无 path param:跨 project 全局查询(漫画态用户视角不感知"项目"概念)。
    必须在 /uploads/{upload_id} 之前注册(否则 'ready' 会匹配到 {upload_id})。
    """
    return list_ready_uploads_for_user(conn, user.id)


@router.get("/uploads/{upload_id}", response_model=UploadResponse)
def api_get_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        upload = get_upload_or_404(conn, upload_id, user.id)
        return upload.to_response()
    except ResourceNotFoundOrForbidden:
        raise


@router.delete(
    "/uploads/{upload_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def api_delete_upload(
    upload_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    try:
        delete_upload(conn, upload_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"upload delete 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )
