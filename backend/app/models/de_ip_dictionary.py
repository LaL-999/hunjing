"""DeIpDictionary 表的 Python 表示 — Sprint 2.E 去 IP 化导出。

per project 0 或 1 条;重新生成走 INSERT OR REPLACE 覆盖。
mapping_json:{"林黛玉": "黛影", ...} dict
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class DeIpDictionary:
    project_id: str
    user_id: str
    mapping_json: str
    notes: Optional[str]
    tokens_input: int
    tokens_output: int
    cost_yuan: float
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "DeIpDictionary":
        return cls(
            project_id=row["project_id"],
            user_id=row["user_id"],
            mapping_json=row["mapping_json"] or "{}",
            notes=row["notes"],
            tokens_input=row["tokens_input"],
            tokens_output=row["tokens_output"],
            cost_yuan=row["cost_yuan"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @property
    def mapping(self) -> dict[str, str]:
        """parse mapping_json → dict;损坏返空 dict。"""
        try:
            parsed = json.loads(self.mapping_json)
            if not isinstance(parsed, dict):
                return {}
            return {str(k): str(v) for k, v in parsed.items() if k and v}
        except (json.JSONDecodeError, TypeError):
            return {}

    def to_response(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "mapping": self.mapping,
            "notes": self.notes,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "cost_yuan": round(self.cost_yuan, 4),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
