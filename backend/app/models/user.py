"""User 表的 Python 表示。"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass
class User:
    id: str
    phone: Optional[str]
    email: Optional[str]
    plan: str
    quota_reset_at: Optional[str]
    register_ip: Optional[str]
    register_ua: Optional[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "User":
        return cls(
            id=row["id"],
            phone=row["phone"],
            email=row["email"],
            plan=row["plan"],
            quota_reset_at=row["quota_reset_at"],
            register_ip=row["register_ip"],
            register_ua=row["register_ua"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
