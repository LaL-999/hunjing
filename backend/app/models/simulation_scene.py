"""SimulationScene — Sprint 6.A2 M3.A(2026-05-18)灵魂续写每幕元数据。

每行 = 灵魂续写中的一幕场景:
  - scene_index 0-based 严格递增
  - scene_source 标 'project_scenes_pick'(从原作场所选)/ 'llm_created'(LLM 新造)
  - characters_present_json 在场角色 id 列表(summoner 决定)
  - narrative_segment narrator 合稿后的小说段落

跨幕 reflection 时按 scene_index 查"我去过哪儿 / 谁在场"
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

SceneSource = Literal["project_scenes_pick", "llm_created"]


@dataclass
class SimulationScene:
    id: str
    simulation_id: str
    scene_index: int
    scene_name: str
    scene_source: SceneSource
    time_anchor: str
    characters_present: list[str]
    narrative_segment: str
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SimulationScene":
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
            scene_index=int(row["scene_index"]),
            scene_name=row["scene_name"],
            scene_source=row["scene_source"],
            time_anchor=row["time_anchor"] or "",
            characters_present=_safe_list(row["characters_present_json"]),
            narrative_segment=row["narrative_segment"] or "",
            created_at=row["created_at"],
        )
