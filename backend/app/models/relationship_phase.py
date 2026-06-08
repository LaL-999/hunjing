"""RelationshipPhase 表的 Python 表示(Sprint 6.A2 M1,2026-05-18)。

关系时间轴的单个阶段。一条 relationship 可有 N 个 phase,按 phase_index 排序。

业务意义:
  - 张凡↔莫晴雨:phase[0]=暗恋(第1-7章) / phase[1]=情侣(第8-14章) / phase[2]=仇敌(第15章-)
  - relationships.current_phase_id 指向当前生效 phase(通常 = 最后一个 phase)
  - 续写时 director 按 panel/章节时间锚点选对应 phase 的 type / strength
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass
class RelationshipPhase:
    id: str
    relationship_id: str
    phase_index: int                # 0-based,按时间顺序
    type: str                       # 亲属 / 敌对 / 朋友 / 情侣 / 师徒 / 同事 / 其他
    strength: str                   # strong / moderately_strong / moderate / moderately_weak / weak
    start_anchor: Optional[str]     # "第 1 章" / "T1" / null=未知
    end_anchor: Optional[str]       # "第 7 章" / null=持续到现在(最新 phase)
    trigger_event_id: Optional[str] # 触发该 phase 转变的事件 id(可选)
    notes: str                      # 用户备注 / 阶段描述
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "RelationshipPhase":
        return cls(
            id=row["id"],
            relationship_id=row["relationship_id"],
            phase_index=int(row["phase_index"]),
            type=row["type"],
            strength=row["strength"] or "moderate",
            start_anchor=row["start_anchor"],
            end_anchor=row["end_anchor"],
            trigger_event_id=row["trigger_event_id"],
            notes=row["notes"] or "",
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
