"""Prop 表的 Python 表示 — Sprint D.9 Sprint 2.A。

ADR v3 §3.2 Agent #5 素材库抽取员产物之三;migration 027 创建。
与 character_visual.signature_props 的区别:这是"剧情中道具",signature_props 是"角色随身物"。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class Prop:
    id: str
    comic_id: str
    name: str
    prop_type: Optional[str]            # 武器 / 服饰 / 书籍 / 家具 / 信物 / 玉器 / 文房 / 其他
    owner_character_id: Optional[str]   # 关联 character.id(可空)
    visual_description: Optional[str]
    story_significance: Optional[str]

    focused_count: int
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Prop":
        return cls(
            id=row["id"],
            comic_id=row["comic_id"],
            name=row["name"],
            prop_type=row["prop_type"],
            owner_character_id=row["owner_character_id"],
            visual_description=row["visual_description"],
            story_significance=row["story_significance"],
            focused_count=row["focused_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_response(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "comic_id": self.comic_id,
            "name": self.name,
            "prop_type": self.prop_type,
            "owner_character_id": self.owner_character_id,
            "visual_description": self.visual_description,
            "story_significance": self.story_significance,
            "focused_count": self.focused_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
