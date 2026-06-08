"""CanonicalEntity — Sprint 6.A2 M5.1(2026-05-20)实体唯一身份注册。

每行 = 灵魂续写过程中"已确立的核心实体":
  - entity_type 4 类:character / object / location / event
  - canonical_name 规范名(首次锁定,后续严禁覆盖)
  - aliases 同实体别名列表
  - description 用于 LLM 语义匹配(判定新身份是否与已有实体同义)

生产侧:entity_registrar 每幕 narrator 合稿后调用
消费侧:scene_picker / narrator / agent_dialogue 必读 canonical list 作"身份锁定"硬约束

设计起源(Gemini 第二轮评测瑕疵 1):
  - 照片主人 在同一篇内变 3 次(林小满→柳英→林小禾)
  - world_facts flat 清单允许"同一概念多身份共存"
  - canonical_entities 强制"首次锁定,语义重合 → 用旧 entity_id"
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Literal

EntityType = Literal["character", "object", "location", "event"]


@dataclass
class CanonicalEntity:
    id: str
    simulation_id: str
    entity_type: EntityType
    canonical_name: str
    aliases: list[str]
    description: str
    first_introduced_scene: int
    locked_at: str
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "CanonicalEntity":
        def _safe_list(raw: object) -> list[str]:
            if not raw or not isinstance(raw, str):
                return []
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []

        return cls(
            id=row["id"],
            simulation_id=row["simulation_id"],
            entity_type=row["entity_type"],
            canonical_name=row["canonical_name"],
            aliases=_safe_list(row["aliases_json"]),
            description=row["description"] or "",
            first_introduced_scene=int(row["first_introduced_scene"]),
            locked_at=row["locked_at"],
            created_at=row["created_at"],
        )

    def to_prompt_line(self) -> str:
        """LLM prompt 友好的单行表示。

        例:[character#ent_001] 林小满(别名:小满 / 那个跳楼的女生)— 三年前从天台跳下的女生
        """
        alias_str = (
            f"(别名:{' / '.join(a for a in self.aliases if a != self.canonical_name)})"
            if len([a for a in self.aliases if a != self.canonical_name]) > 0
            else ""
        )
        return (
            f"[{self.entity_type}#{self.id[:8]}] {self.canonical_name}"
            f"{alias_str} — {self.description[:120]}"
        )
