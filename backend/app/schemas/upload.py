"""Upload API schema — Sprint 2.A 文件上传。

POST /api/projects/{project_id}/uploads     multipart file 字段
GET  /api/projects/{project_id}/uploads     列出该项目的 uploads
GET  /api/uploads/{id}                       详情
DELETE /api/uploads/{id}                     删 DB 行 + 删磁盘文件
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    """API 单条返回 — 对齐 Upload.to_response()。"""

    id: str
    project_id: str
    filename: str
    mime_type: str
    size_bytes: int
    parsed_text_chars: Optional[int] = None
    state: str
    error_message: Optional[str] = None
    uploaded_at: str


class UploadListItem(BaseModel):
    """列表项 — 同 UploadResponse,留独立类型留扩展空间。"""

    id: str
    filename: str
    mime_type: str
    size_bytes: int
    parsed_text_chars: Optional[int] = None
    state: str
    uploaded_at: str
