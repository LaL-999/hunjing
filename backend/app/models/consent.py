"""ConsentRecord 表的 Python 表示。"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass
class ConsentRecord:
    id: str
    user_id: str
    version: str
    checks: dict   # {adult, terms, privacy, pricing}
    accepted_at: str
    ip: Optional[str]
    ua: Optional[str]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ConsentRecord":
        # Sprint D.7:checks JSON 兜底(空 dict 安全 fallback)
        checks: dict = {}
        raw = row["checks"]
        if isinstance(raw, str) and raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    checks = parsed
            except (json.JSONDecodeError, TypeError):
                pass
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            version=row["version"],
            checks=checks,
            accepted_at=row["accepted_at"],
            ip=row["ip"],
            ua=row["ua"],
        )
