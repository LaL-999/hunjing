"""SimulationOutline — Sprint 6.A2 M6(2026-05-20)整篇 outline 主表。

每行 = 一个 sim 的全篇 outline 元数据:
  - state 6 态机:drafting / awaiting_user / approved / generating / done / failed
  - total_scenes_planned outline 阶段确定的总幕数
  - global_theme 整篇主题(< 60 字)
  - global_arc 整篇起承转合走向(< 300 字)
  - user_approved_at 用户批准时间(NULL = 未批准)

生产侧:outline_generator 每次创建 sim 时调一次,LLM 生成完整 outline
消费侧:outline_orchestrator 按 outline_scenes 逐幕跑;OutlineReviewView 给用户审核
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, Optional

OutlineState = Literal[
    "drafting",       # LLM 正在生成
    "awaiting_user",  # 等待用户审核
    "approved",       # 用户已批准
    "generating",     # 按 outline 跑各幕中
    "done",           # 全部幕完成
    "failed",         # 失败
]


@dataclass
class SimulationOutline:
    id: str
    simulation_id: str
    state: OutlineState
    total_scenes_planned: int
    global_theme: str
    global_arc: str
    user_approved_at: Optional[str]
    error_message: Optional[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "SimulationOutline":
        return cls(
            id=row["id"],
            simulation_id=row["simulation_id"],
            state=row["state"],
            total_scenes_planned=int(row["total_scenes_planned"]),
            global_theme=row["global_theme"] or "",
            global_arc=row["global_arc"] or "",
            user_approved_at=row["user_approved_at"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
