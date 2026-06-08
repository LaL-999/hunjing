"""AuthorCompass API schema — P3 作者指南针(2026-05-26).

POST /api/projects/{project_id}/author-compass/analyze  触发双轨制 LLM 调研
GET  /api/projects/{project_id}/author-compass          拉当前画像
PUT  /api/projects/{project_id}/author-compass          用户改 / 锁定
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


CompassStatus = Literal["pending", "running", "done", "failed"]


class AnalyzeRequest(BaseModel):
    """用户触发分析时填的基础信息(都可空,LLM 也能从原作猜)。"""
    author_name: Optional[str] = Field(default=None, max_length=100)
    work_title: Optional[str] = Field(default=None, max_length=200)


class AuthorCompassResponse(BaseModel):
    id: str
    project_id: str

    author_name: Optional[str] = None
    work_title: Optional[str] = None

    external_profile: Optional[dict[str, Any]] = None
    external_status: CompassStatus = "pending"
    external_error: Optional[str] = None
    external_at: Optional[str] = None

    internal_metrics: Optional[dict[str, Any]] = None
    internal_status: CompassStatus = "pending"
    internal_error: Optional[str] = None
    internal_at: Optional[str] = None

    final_compass: Optional[dict[str, Any]] = None
    user_locked: bool = False

    created_at: str
    updated_at: str


class UpdateCompassRequest(BaseModel):
    """用户改的字段 — 全部可选,只更新提供的字段。

    final_compass: 用户合并 / 修改后的完整指南针(锁定后续作读这版)
    user_locked: 是否锁定;锁定时 final_compass 必须有值
    """
    author_name: Optional[str] = Field(default=None, max_length=100)
    work_title: Optional[str] = Field(default=None, max_length=200)
    final_compass: Optional[dict[str, Any]] = None
    user_locked: Optional[bool] = None
