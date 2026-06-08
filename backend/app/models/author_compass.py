"""P3 作者指南针 model — Author Compass(2026-05-26).

1:1 与 project,双轨制(外部研究 + 内部反推)+ 用户锁定。

字段语义见 migrations/064_author_compass.sql。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Optional


# 双轨状态枚举(SQL CHECK 等价)
COMPASS_STATUSES = ("pending", "running", "done", "failed")


def _safe_dict(raw: object) -> Optional[dict]:
    """JSON 字段安全解析:非法 / 空 → None。"""
    if not raw or not isinstance(raw, str):
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, TypeError):
        return None


@dataclass
class AuthorCompass:
    id: str
    project_id: str

    # 用户输入(可空)
    author_name: Optional[str] = None
    work_title: Optional[str] = None

    # 外部研究轨
    external_profile: Optional[dict] = None   # 解析后的 JSON dict
    external_status: str = "pending"
    external_error: Optional[str] = None
    external_at: Optional[str] = None

    # 内部反推轨
    internal_metrics: Optional[dict] = None
    internal_status: str = "pending"
    internal_error: Optional[str] = None
    internal_at: Optional[str] = None

    # 用户最终版本 + 锁定
    final_compass: Optional[dict] = None
    user_locked: bool = False

    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "AuthorCompass":
        def _safe_get(key: str, default):
            try:
                return row[key]
            except (KeyError, IndexError):
                return default

        return cls(
            id=row["id"],
            project_id=row["project_id"],
            author_name=_safe_get("author_name", None),
            work_title=_safe_get("work_title", None),
            external_profile=_safe_dict(_safe_get("external_profile_json", None)),
            external_status=_safe_get("external_status", "pending") or "pending",
            external_error=_safe_get("external_error", None),
            external_at=_safe_get("external_at", None),
            internal_metrics=_safe_dict(_safe_get("internal_metrics_json", None)),
            internal_status=_safe_get("internal_status", "pending") or "pending",
            internal_error=_safe_get("internal_error", None),
            internal_at=_safe_get("internal_at", None),
            final_compass=_safe_dict(_safe_get("final_compass_json", None)),
            user_locked=bool(_safe_get("user_locked", 0) or 0),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> dict:
        """API 序列化用 — JSON 字段已解析成 dict,前端直接用。"""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "author_name": self.author_name,
            "work_title": self.work_title,
            "external_profile": self.external_profile,
            "external_status": self.external_status,
            "external_error": self.external_error,
            "external_at": self.external_at,
            "internal_metrics": self.internal_metrics,
            "internal_status": self.internal_status,
            "internal_error": self.internal_error,
            "internal_at": self.internal_at,
            "final_compass": self.final_compass,
            "user_locked": self.user_locked,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    def effective_compass(self) -> Optional[dict]:
        """获取"生效中"的指南针 — 锁定后取 final;否则取 external+internal 合并。

        Returns:
          dict 或 None(双轨都未完成)
        """
        if self.user_locked and self.final_compass:
            return self.final_compass
        if self.external_profile is None and self.internal_metrics is None:
            return None
        return {
            "external_profile": self.external_profile,
            "internal_metrics": self.internal_metrics,
        }
