"""RedFlag 表的 Python 表示 — Sprint 2.A 红旗词词典。

设计原则(ADR-2.A):宽松文学优先 — 默认只拦极端三类(政治敏感 / 恐怖暴力 / 猎奇极端色情)
+ 隐私模式(身份证 / 银行卡号正则,防意外泄露)。一般文学正常元素(武侠流血、言情亲密、
悬疑死亡、战争场景、历史事件提及、普通脏话)绝不拦。

字段对齐 backend/migrations/011_uploads.sql。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


RED_FLAG_CATEGORIES = ("political", "sexual", "violence", "privacy")
RED_FLAG_SEVERITIES = ("block", "warn")


@dataclass
class RedFlag:
    id: str
    category: str
    pattern: str
    is_regex: bool
    severity: str
    enabled: bool
    note: Optional[str]
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "RedFlag":
        return cls(
            id=row["id"],
            category=row["category"],
            pattern=row["pattern"],
            is_regex=bool(row["is_regex"]),
            severity=row["severity"],
            enabled=bool(row["enabled"]),
            note=row["note"],
            created_at=row["created_at"],
        )
