"""Event 表的 Python 表示(初始态可选,菱形节点)。

INIT.7(2026-05-21)新增 time_anchor 字段 — 用户在初始态可指定事件的时间锚。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass
class Event:
    id: str
    project_id: str
    description: str
    participants: list[str]   # character_id 数组
    created_at: str
    # INIT.7(2026-05-21):事件时间锚(如"第 5 章" / "T0+3 天");NULL = 未指定
    time_anchor: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Event":
        # Sprint D.7:participants JSON 兜底(脏数据不 crash)
        participants: list[str] = []
        raw = row["participants"]
        if isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    participants = [str(p) for p in parsed]
            except (json.JSONDecodeError, TypeError):
                pass
        # INIT.7:time_anchor 列兼容老 row(无此列时 None)
        try:
            ta = row["time_anchor"]
            time_anchor = ta if ta else None
        except (IndexError, KeyError):
            time_anchor = None
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            description=row["description"],
            participants=participants,
            created_at=row["created_at"],
            time_anchor=time_anchor,
        )
