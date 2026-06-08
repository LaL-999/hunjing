"""ProjectScene 表的 Python 表示(Sprint 6.A2 M2,2026-05-18)。

前三态项目的"场所图谱"。从 extract_chunk_results.graph_json.entities 中
type='LOCATION' 实体聚合而来,M3 续写时 scene_picker 消费。

与漫画态 027 scenes 表的区别:
  - 漫画态 scenes: comic_id 绑定,给导演 Agent #6 拼 panel prompt
  - 本表 project_scenes: project_id 绑定,给多 agent 仿真 scene_picker
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProjectScene:
    id: str
    project_id: str
    name: str
    aliases: list[str]
    description: str
    appearance_chunk_count: int
    created_at: str
    updated_at: str
    # Sprint 6.A2 M8.A(2026-05-20):标记"续作自创入库"vs"原作图谱抽出"
    # None = 原作图谱抽出(M2 链路);非 None = 该续作 sim LLM 自创的场景
    origin_simulation_id: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ProjectScene":
        def _safe_list(raw: object) -> list[str]:
            if not raw or not isinstance(raw, str):
                return []
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []

        # 兼容旧 row 没有 origin_simulation_id 列的 fallback
        origin_sid: Optional[str] = None
        try:
            origin_sid = row["origin_simulation_id"]
        except (IndexError, KeyError):
            origin_sid = None

        return cls(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            aliases=_safe_list(row["aliases_json"]),
            description=row["description"] or "",
            appearance_chunk_count=int(row["appearance_chunk_count"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            origin_simulation_id=origin_sid,
        )
