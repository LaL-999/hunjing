"""CharacterCard 表的 Python 表示 — Sprint D.9 Sprint 2.A。

ADR v3 §3.2 Agent #4 角色锚定员产物;migration 025 创建。
角色一致性 L1 层的持久化(身份证级 descriptor + 立绘卡 URL)。

descriptor v3:20-30 句中文 6 段式结构(气质/面部/发型/体态/穿搭/灵魂),
由 Agent #4 读 character_visuals 表 + style_detailed_prompt 综合生成,**一经生成不可改**。
card_image_url 可重生成,记 regenerated_count 审计。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any


@dataclass
class CharacterCard:
    id: str
    comic_id: str
    character_id: str
    character_name: str
    descriptor: str        # 20-30 句"身份证级"描述符,LLM 生成后不可改
    card_image_url: str    # Seedream 出的正面立绘
    regenerated_count: int

    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "CharacterCard":
        return cls(
            id=row["id"],
            comic_id=row["comic_id"],
            character_id=row["character_id"],
            character_name=row["character_name"],
            descriptor=row["descriptor"],
            card_image_url=row["card_image_url"],
            regenerated_count=row["regenerated_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_response(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "comic_id": self.comic_id,
            "character_id": self.character_id,
            "character_name": self.character_name,
            "descriptor": self.descriptor,
            "card_image_url": self.card_image_url,
            "regenerated_count": self.regenerated_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
