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
    # 2026-06-25:用户资料(migration 090)
    nickname: Optional[str] = None
    avatar_url: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "User":
        keys = row.keys()
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
            # 防御:个别精简查询 / 迁移前旧库可能未含新列
            nickname=row["nickname"] if "nickname" in keys else None,
            avatar_url=row["avatar_url"] if "avatar_url" in keys else None,
        )
