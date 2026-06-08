"""WorldFact — Sprint 6.A2 M4.1(2026-05-19)全局事实账本。

每行 = 灵魂续写过程中"已确立的世界事实":
  - fact_type 5 类:LIFE_STATUS / LOCATION / RULE_LOCK / EVENT_DONE / RELATIONSHIP_CHANGE
  - status 3 态:ACTIVE(生效) / SUPERSEDED(被覆盖) / LOCKED(不可覆盖,反派规则用)
  - scene_index 由哪幕产出(对齐 simulation_scenes.scene_index)
  - subject_id / subject_name:事实关联的角色/关系/全局事件

生产侧:world_state_extractor 每幕 narrator 合稿后调用
消费侧:scene_picker / agent_dialogue / narrator / consistency_checker 必读 ACTIVE+LOCKED 列表

设计起源(Gemini 评测反馈):
  - 治瑕疵 1 剧情死循环 + 瑕疵 2 时间线悖论 + 瑕疵 5 反派规则覆写
  - "班长被警察带走" → LIFE_STATUS or LOCATION ACTIVE,后续幕不能让他凭空出场
  - "地府守门人规则:找到 5 张日记残页" → RULE_LOCK LOCKED,后续不许擅自换规则
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, Optional

FactType = Literal[
    "LIFE_STATUS",
    "LOCATION",
    "RULE_LOCK",
    "EVENT_DONE",
    "RELATIONSHIP_CHANGE",
]

FactStatus = Literal["ACTIVE", "SUPERSEDED", "LOCKED"]


@dataclass
class WorldFact:
    id: str
    simulation_id: str
    scene_index: int
    fact_type: FactType
    subject_id: Optional[str]
    subject_name: str
    content: str
    status: FactStatus
    superseded_by_fact_id: Optional[str]
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "WorldFact":
        return cls(
            id=row["id"],
            simulation_id=row["simulation_id"],
            scene_index=int(row["scene_index"]),
            fact_type=row["fact_type"],
            subject_id=row["subject_id"],
            subject_name=row["subject_name"] or "",
            content=row["content"] or "",
            status=row["status"],
            superseded_by_fact_id=row["superseded_by_fact_id"],
            created_at=row["created_at"],
        )

    def to_prompt_line(self) -> str:
        """LLM prompt 友好的单行表示。

        例:[LIFE_STATUS · LOCKED] 班长 — 被警察带走,在警局接受调查
        """
        return f"[{self.fact_type} · {self.status}] {self.subject_name} — {self.content}"
