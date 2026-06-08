"""CounterfactualCombinationRun — Sprint 6.A2 CT(2026-05-21)反事实组合树批次。

用户在反事实工作台勾选 N(1-3)个反事实变量,每个变量取"原/改"二态,
共 2^N 个组合 → 后端 fanout 启动 2^N 个 sim → 前端决策树视图展示所有产物。

字段对齐 backend/migrations/050_counterfactual_combination_runs.sql。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal, Optional


CombinationRunState = Literal[
    "pending", "generating", "partial", "done", "failed",
]


@dataclass
class SelectedVariable:
    """用户勾选的一条反事实变量,绑定 'a'(原)/ 'b'(改)二态。"""
    counterfactual_id: str
    label_a: str  # 原值的简短可读描述,如 "原-性格懦弱"
    label_b: str  # 改值的简短可读描述,如 "改-性格勇敢"

    def to_dict(self) -> dict[str, str]:
        return {
            "counterfactual_id": self.counterfactual_id,
            "label_a": self.label_a,
            "label_b": self.label_b,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SelectedVariable":
        return cls(
            counterfactual_id=str(d.get("counterfactual_id") or "").strip(),
            label_a=str(d.get("label_a") or "").strip()[:80],
            label_b=str(d.get("label_b") or "").strip()[:80],
        )


@dataclass
class CounterfactualCombinationRun:
    id: str
    project_id: str
    user_id: str
    selected_variables: list[SelectedVariable]
    total_combinations: int  # 2 / 4 / 8
    state: CombinationRunState
    error_message: Optional[str]
    created_at: str
    completed_at: Optional[str]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "CounterfactualCombinationRun":
        raw_vars = row["selected_variables_json"] or "[]"
        try:
            parsed = json.loads(raw_vars)
            if not isinstance(parsed, list):
                parsed = []
        except json.JSONDecodeError:
            parsed = []
        variables = [
            SelectedVariable.from_dict(d)
            for d in parsed
            if isinstance(d, dict)
        ]

        return cls(
            id=row["id"],
            project_id=row["project_id"],
            user_id=row["user_id"],
            selected_variables=variables,
            total_combinations=int(row["total_combinations"]),
            state=row["state"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    def to_response(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "project_id": self.project_id,
            "user_id": self.user_id,
            "selected_variables": [v.to_dict() for v in self.selected_variables],
            "total_combinations": self.total_combinations,
            "state": self.state,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }
