"""Event model — 对应 events 表(2026-05-27).

枚举值与 migrations/001_events.sql 的 CHECK 约束保持一致。
若新加 event_type:必须同步 3 处(本 model EVENT_TYPES / migration CHECK / Pydantic Literal)。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Literal, Optional


EVENT_TYPES: tuple[str, ...] = (
    # 页面级
    "page_view",
    "page_leave",
    "page_refresh",
    # AI 调用级
    "ai_call_start",
    "ai_call_done",
    "ai_call_failed",
    # 创作态切换
    "mode_switch",
    # 业务关键操作(传统 4 态)
    "project_create",
    "project_delete",
    "simulation_create",
    "simulation_done",
    "simulation_failed",
    "audit_run",
    "canonical_audit_run",
    # 异常 / 会话
    "error",
    "session_start",
    "session_end",
    # 2026-06-09 新增 — 剧创态(第 5 态)6 类
    "screenplay_novel_upload",
    "screenplay_compose_start",
    "screenplay_compose_done",
    "screenplay_optimize",
    "screenplay_characters_view",
    "screenplay_episodes_plan",
    # 2026-06-09 新增 — 多模型对比 3 类
    "model_compare_start",
    "model_compare_run",
    "model_compare_winner",
    # 2026-06-09 新增 — Dashboard 入口转化 1 类
    "dashboard_card_click",
)

EventType = Literal[
    "page_view", "page_leave", "page_refresh",
    "ai_call_start", "ai_call_done", "ai_call_failed",
    "mode_switch",
    "project_create", "project_delete",
    "simulation_create", "simulation_done", "simulation_failed",
    "audit_run", "canonical_audit_run",
    "error", "session_start", "session_end",
    # 2026-06-09 新增:
    "screenplay_novel_upload", "screenplay_compose_start", "screenplay_compose_done",
    "screenplay_optimize", "screenplay_characters_view", "screenplay_episodes_plan",
    "model_compare_start", "model_compare_run", "model_compare_winner",
    "dashboard_card_click",
]

# 5 态枚举(对齐主平台 + 剧创态)
ModeType = Literal["initial", "middle", "tail", "comic", "screenplay"]


@dataclass
class Event:
    """events 表的行表示."""
    id: Optional[int] = None
    event_type: str = ""
    user_id: Optional[str] = None
    session_id: str = ""
    timestamp_ms: int = 0
    server_recv_ms: int = 0
    project_id: Optional[str] = None
    simulation_id: Optional[str] = None
    mode: Optional[str] = None
    step: Optional[str] = None
    path: Optional[str] = None
    duration_ms: Optional[int] = None
    meta: dict = field(default_factory=dict)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Event":
        try:
            meta = json.loads(row["meta_json"] or "{}")
            if not isinstance(meta, dict):
                meta = {}
        except (TypeError, ValueError, json.JSONDecodeError):
            meta = {}
        return cls(
            id=row["id"],
            event_type=row["event_type"],
            user_id=row["user_id"],
            session_id=row["session_id"],
            timestamp_ms=int(row["timestamp_ms"]),
            server_recv_ms=int(row["server_recv_ms"]),
            project_id=row["project_id"],
            simulation_id=row["simulation_id"],
            mode=row["mode"],
            step=row["step"],
            path=row["path"],
            duration_ms=(int(row["duration_ms"]) if row["duration_ms"] is not None else None),
            meta=meta,
        )

    def to_response(self) -> dict:
        return {
            "id": self.id,
            "event_type": self.event_type,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "timestamp_ms": self.timestamp_ms,
            "server_recv_ms": self.server_recv_ms,
            "project_id": self.project_id,
            "simulation_id": self.simulation_id,
            "mode": self.mode,
            "step": self.step,
            "path": self.path,
            "duration_ms": self.duration_ms,
            "meta": self.meta,
        }


__all__ = ["EVENT_TYPES", "EventType", "ModeType", "Event"]
