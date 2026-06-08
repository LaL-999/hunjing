"""Relationship 表的 Python 表示。

Sprint 6.A2 M1(2026-05-18):加 current_phase_id 字段指向 relationship_phases 表的
"当前生效"阶段。NULL = 无阶段(走 type fallback,向后兼容老数据)。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


# Sprint 3.A polish — 与 llm_extract.VALID_RELATIONSHIP_STRENGTHS 对齐
VALID_RELATIONSHIP_STRENGTHS = (
    "strong",
    "moderately_strong",
    "moderate",
    "moderately_weak",
    "weak",
)


@dataclass
class Relationship:
    id: str
    project_id: str
    source_id: str
    target_id: str
    type: str          # 亲属 / 敌对 / 朋友 / 情侣 / 师徒 / 同事 / 其他
    description: str
    color: Optional[str]
    # Sprint 3.A:5 级强度 enum,LLM 抽图谱时打;手动创建的关系默认 'moderate'
    strength: str
    created_at: str
    # Sprint 6.A2 M1(2026-05-18):指向 relationship_phases 表的当前生效 phase
    # NULL = 无 phases(走 type / strength fallback,向后兼容)
    current_phase_id: Optional[str] = None
    # SP-7(2026-05-28,migration 072)— 关系正负极性
    # 'positive'(喜爱/亲近)/ 'negative'(仇恨/对立)/ 'neutral'(中性如同事)/ None(未标)
    # 与 strength(紧密度)正交:strong+negative = 强烈仇恨
    polarity: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Relationship":
        # 兼容老库(migration 020 前无 strength;036 前无 current_phase_id):
        # row 字典访问失败 → 兜底默认值
        strength = (
            row["strength"]
            if "strength" in row.keys() and row["strength"]
            else "moderate"
        )

        def _safe_get(key: str, default):
            try:
                return row[key]
            except (KeyError, IndexError):
                return default

        # SP-7:polarity 兜底
        polarity_raw = _safe_get("polarity", None)
        polarity = (
            polarity_raw if polarity_raw in ("positive", "negative", "neutral")
            else None
        )

        return cls(
            id=row["id"],
            project_id=row["project_id"],
            source_id=row["source_id"],
            target_id=row["target_id"],
            type=row["type"],
            description=row["description"],
            color=row["color"],
            strength=strength,
            created_at=row["created_at"],
            current_phase_id=_safe_get("current_phase_id", None),
            polarity=polarity,
        )
