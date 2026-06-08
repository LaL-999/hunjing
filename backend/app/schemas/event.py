"""Event API schema(初始态可选,菱形节点)。"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class CreateEventRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=300)
    participants: list[str] = Field(default_factory=list, max_length=20)
    # INIT.7(2026-05-21):时间锚(可选,如"第 5 章" / "T0+3 天")
    time_anchor: Optional[str] = Field(None, max_length=30)


class UpdateEventRequest(BaseModel):
    description: Optional[str] = Field(None, min_length=1, max_length=300)
    participants: Optional[list[str]] = Field(None, max_length=20)
    # INIT.7(2026-05-21):时间锚可更新
    time_anchor: Optional[str] = Field(None, max_length=30)


class EventResponse(BaseModel):
    id: str
    project_id: str
    description: str
    participants: list[str]
    created_at: str
    # INIT.7(2026-05-21):时间锚透出
    time_anchor: Optional[str] = None


class ProjectGraphResponse(BaseModel):
    """GET /api/projects/{id}/graph — 一次返回全图,前端编辑器主入口。"""
    project: dict          # ProjectResponse 字段(避免循环 import,用 dict)
    characters: list[dict]
    relationships: list[dict]
    events: list[dict]
