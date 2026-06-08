"""Scene 表的 Python 表示 — Sprint D.9 Sprint 2.A。

ADR v3 §3.2 Agent #5 素材库抽取员产物之二;migration 027 创建。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Scene:
    id: str
    comic_id: str
    name: str
    location_type: Optional[str]
    era: Optional[str]
    architecture_style: Optional[str]
    lighting: Optional[str]
    season: Optional[str]
    key_props: list[str]   # 关键陈设短词列表

    focused_count: int
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Scene":
        key_props_raw = row["key_props_json"]
        key_props: list[str] = []
        if key_props_raw and isinstance(key_props_raw, str):
            try:
                parsed = json.loads(key_props_raw)
                if isinstance(parsed, list):
                    key_props = [str(p) for p in parsed]
            except (json.JSONDecodeError, TypeError):
                pass
        return cls(
            id=row["id"],
            comic_id=row["comic_id"],
            name=row["name"],
            location_type=row["location_type"],
            era=row["era"],
            architecture_style=row["architecture_style"],
            lighting=row["lighting"],
            season=row["season"],
            key_props=key_props,
            focused_count=row["focused_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_response(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "comic_id": self.comic_id,
            "name": self.name,
            "location_type": self.location_type,
            "era": self.era,
            "architecture_style": self.architecture_style,
            "lighting": self.lighting,
            "season": self.season,
            "key_props": self.key_props,
            "focused_count": self.focused_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
