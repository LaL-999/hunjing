"""CharacterVisual 表的 Python 表示 — Sprint D.9 Sprint 2.A。

ADR v3 §3.2 Agent #5 素材库抽取员产物;migration 026 创建。
1-to-1 关联 character(同 comic_id 内 unique)。

字段策略(ADR v3 §3.2 铁律):
  - 字段未明 → 填 "(原文未明)",绝不凭空创造
  - 用户走 character_focus 流程补字段(扩展自 C 阶段对焦机制)
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional


def _safe_json_dict(raw: Optional[str]) -> dict:
    if not raw or not isinstance(raw, str):
        return {}
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _safe_json_list(raw: Optional[str]) -> list:
    if not raw or not isinstance(raw, str):
        return []
    try:
        v = json.loads(raw)
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


@dataclass
class CharacterVisual:
    id: str
    comic_id: str
    character_id: str

    face: dict          # {eye_shape, eye_color, eyebrow, nose, mouth, face_shape, skin_tone, marks}
    hair: dict          # {length, color, texture, hairstyle, bangs}
    body: dict          # {height_range, body_type, posture, signature_action}
    outfit: dict        # {garment, color, style}
    accessories: list   # [{name, position, color}, ...]
    signature_props: list  # [{name, description}, ...]
    soul_traits: Optional[str]

    focused_count: int
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "CharacterVisual":
        return cls(
            id=row["id"],
            comic_id=row["comic_id"],
            character_id=row["character_id"],
            face=_safe_json_dict(row["face_json"]),
            hair=_safe_json_dict(row["hair_json"]),
            body=_safe_json_dict(row["body_json"]),
            outfit=_safe_json_dict(row["outfit_json"]),
            accessories=_safe_json_list(row["accessories_json"]),
            signature_props=_safe_json_list(row["signature_props_json"]),
            soul_traits=row["soul_traits"],
            focused_count=row["focused_count"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_response(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "comic_id": self.comic_id,
            "character_id": self.character_id,
            "face": self.face,
            "hair": self.hair,
            "body": self.body,
            "outfit": self.outfit,
            "accessories": self.accessories,
            "signature_props": self.signature_props,
            "soul_traits": self.soul_traits,
            "focused_count": self.focused_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
