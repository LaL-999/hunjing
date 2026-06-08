"""POST /track 的 Pydantic schema(2026-05-27)."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field

from app.models.event import EventType, ModeType


class TrackRequest(BaseModel):
    """前端 useAnalytics composable 发送的埋点。

    设计:
      - 字段尽量平铺,common 字段单独抽出
      - meta 兜底任何 event-specific 附加数据(如 ai_call 的 ai_name / tokens)
      - timestamp_ms 由客户端发送(Date.now()),server_recv_ms 服务端填
    """
    event_type: EventType
    user_id: Optional[str] = None
    session_id: str = Field(..., min_length=1, max_length=64)
    timestamp_ms: int = Field(..., ge=0)
    project_id: Optional[str] = Field(None, max_length=64)
    simulation_id: Optional[str] = Field(None, max_length=64)
    mode: Optional[ModeType] = None
    step: Optional[str] = Field(None, max_length=64)
    path: Optional[str] = Field(None, max_length=255)
    duration_ms: Optional[int] = Field(None, ge=0)
    meta: dict[str, Any] = Field(default_factory=dict)


class TrackBatchRequest(BaseModel):
    """批量埋点(前端 buffer 一段时间后 flush 一批).

    用于 sendBeacon 场景(页面 unload 时一次性把 buffer 发出去)。
    """
    events: list[TrackRequest] = Field(..., min_length=1, max_length=100)


class TrackResponse(BaseModel):
    ok: bool = True
    accepted: int = 0
    rejected: int = 0
    reason: Optional[str] = None
