"""ViolationLog 表的 Python 表示 — Sprint 2.A 红旗命中审计 trail。

不级联删除(用户 + 项目都被删,违规日志仍保留作法务证据)。
matched_text 限制 80 字内脱敏存。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ViolationLog:
    id: str
    user_id: str
    upload_id: Optional[str]
    ip_address: Optional[str]
    flag_id: str
    matched_text: str
    occurred_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ViolationLog":
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            upload_id=row["upload_id"],
            ip_address=row["ip_address"],
            flag_id=row["flag_id"],
            matched_text=row["matched_text"],
            occurred_at=row["occurred_at"],
        )

    def to_response(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "upload_id": self.upload_id,
            "flag_id": self.flag_id,
            "matched_text": self.matched_text,
            "occurred_at": self.occurred_at,
        }
