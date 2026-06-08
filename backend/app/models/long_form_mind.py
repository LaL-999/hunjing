"""Sprint 6.A2 M9.A(2026-05-20)— 长篇心智(滚雪球深化)三表的 Python 表示。

跨代续作的"长篇心智"三个维度,project 级别累积(对标 canonical_entities 是 sim 级别):
  - CharacterArc      角色心境演化片段(每幕抽取)
  - Foreshadow        跨代伏笔(open / resolved 状态机)
  - WorldRule         续作累积的世界规则(后代必须遵守)
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Optional


# ============================================================
# 1. CharacterArc
# ============================================================

@dataclass
class CharacterArc:
    """角色心境演化片段(跨代累积)。"""
    id: str
    project_id: str
    simulation_id: str
    scene_index: int
    character_ids: list[str]            # JSON 解析的角色 id 列表
    arc_keyword: str                    # 心境关键词(LLM 抽)
    trigger_summary: str                # 触发点(< 100 字)
    arc_kind: str                       # 'gradual' / 'sudden' / 'revelation' / 'regression'
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "CharacterArc":
        try:
            char_ids = json.loads(row["character_ids_json"] or "[]")
            if not isinstance(char_ids, list):
                char_ids = []
        except (json.JSONDecodeError, TypeError):
            char_ids = []
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            simulation_id=row["simulation_id"],
            scene_index=int(row["scene_index"]),
            character_ids=[str(x) for x in char_ids if x],
            arc_keyword=row["arc_keyword"] or "",
            trigger_summary=row["trigger_summary"] or "",
            arc_kind=row["arc_kind"] or "gradual",
            created_at=row["created_at"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "simulation_id": self.simulation_id,
            "scene_index": self.scene_index,
            "character_ids": self.character_ids,
            "arc_keyword": self.arc_keyword,
            "trigger_summary": self.trigger_summary,
            "arc_kind": self.arc_kind,
            "created_at": self.created_at,
        }


# ============================================================
# 2. Foreshadow
# ============================================================

@dataclass
class Foreshadow:
    """跨代伏笔(open / resolved 状态机)。"""
    id: str
    project_id: str
    content: str                        # 伏笔内容(< 100 字)
    introduced_in_simulation_id: str
    introduced_scene_index: int
    status: str                         # 'open' / 'resolved' / 'abandoned'
    resolved_in_simulation_id: Optional[str]
    resolved_scene_index: Optional[int]
    resolution_summary: Optional[str]
    priority: str                       # 'high' / 'medium' / 'low'
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Foreshadow":
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            content=row["content"] or "",
            introduced_in_simulation_id=row["introduced_in_simulation_id"],
            introduced_scene_index=int(row["introduced_scene_index"]),
            status=row["status"] or "open",
            resolved_in_simulation_id=row["resolved_in_simulation_id"],
            resolved_scene_index=(
                int(row["resolved_scene_index"])
                if row["resolved_scene_index"] is not None
                else None
            ),
            resolution_summary=row["resolution_summary"],
            priority=row["priority"] or "medium",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "content": self.content,
            "introduced_in_simulation_id": self.introduced_in_simulation_id,
            "introduced_scene_index": self.introduced_scene_index,
            "status": self.status,
            "resolved_in_simulation_id": self.resolved_in_simulation_id,
            "resolved_scene_index": self.resolved_scene_index,
            "resolution_summary": self.resolution_summary,
            "priority": self.priority,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


# ============================================================
# 3. WorldRule
# ============================================================

@dataclass
class WorldRule:
    """续作累积的世界规则(后代必须遵守)。"""
    id: str
    project_id: str
    rule_text: str                      # 规则描述(< 150 字)
    introduced_in_simulation_id: str
    introduced_scene_index: int
    scope: str                          # 'global' / 'faction' / 'location' / 'character'
    scope_target_id: Optional[str]      # scope!=global 时填关联 id
    active: bool
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "WorldRule":
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            rule_text=row["rule_text"] or "",
            introduced_in_simulation_id=row["introduced_in_simulation_id"],
            introduced_scene_index=int(row["introduced_scene_index"]),
            scope=row["scope"] or "global",
            scope_target_id=row["scope_target_id"],
            active=bool(row["active"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "rule_text": self.rule_text,
            "introduced_in_simulation_id": self.introduced_in_simulation_id,
            "introduced_scene_index": self.introduced_scene_index,
            "scope": self.scope,
            "scope_target_id": self.scope_target_id,
            "active": self.active,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


__all__ = ["CharacterArc", "Foreshadow", "WorldRule"]
