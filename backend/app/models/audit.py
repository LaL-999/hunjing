"""Audit 表的 Python 表示 — Sprint 1.R 自洽守护者。

字段对齐 prompts/self_consistency_guardian.md 输出 schema(JSON 8 维度)。
issues 用 list[dict] 保留原始结构,kind 由 prompt + service 校验,这里不强类型化
(避免与 prompt 演化耦合)。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass


@dataclass
class Audit:
    id: str
    simulation_id: str
    user_id: str
    overall_score: int
    issues: list[dict]                 # JSON deserialized
    regenerate_recommendation: str
    tokens_input: int
    tokens_output: int
    cost_yuan: float
    duration_ms: int
    triggered_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Audit":
        return cls(
            id=row["id"],
            simulation_id=row["simulation_id"],
            user_id=row["user_id"],
            overall_score=row["overall_score"],
            issues=json.loads(row["issues_json"]),
            regenerate_recommendation=row["regenerate_recommendation"],
            tokens_input=row["tokens_input"],
            tokens_output=row["tokens_output"],
            cost_yuan=row["cost_yuan"],
            duration_ms=row["duration_ms"],
            triggered_at=row["triggered_at"],
        )

    def to_response(self) -> dict:
        """前端 API 返回口径(对齐 schemas/audit.py 的 AuditResponse)。"""
        return {
            "id": self.id,
            "simulation_id": self.simulation_id,
            "overall_score": self.overall_score,
            "issues": self.issues,
            "regenerate_recommendation": self.regenerate_recommendation,
            "cost_yuan": round(self.cost_yuan, 4),
            "duration_ms": self.duration_ms,
            "triggered_at": self.triggered_at,
        }
