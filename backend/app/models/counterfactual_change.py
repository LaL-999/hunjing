"""CounterfactualChange — Sprint 2.C 反事实变量。

用户在 3D 图谱里改某个节点字段时记一条;推演时编译成 director prompt 的
"反事实变量"上下文区,LLM 显式知道"这是用户的 what-if 假设,不是原作设定"。

字段对齐 backend/migrations/014_counterfactual_changes.sql。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional


VALID_TARGET_TYPES = ("character", "event", "relationship", "world")
WORLD_TARGET_ID = "_global_"   # world 反事实固定 target_id

# world 类型的合法 field(对齐 prompts/director_system.md 铁律 9.5)
VALID_WORLD_FIELDS = (
    "genre",         # 体裁 — 古典章回 / 玄幻 / 科幻 / 武侠 / 都市 / ...
    "setting",       # 背景设定 — 大观园 / 异世界 / 星际 / 末世 / ...
    "magic_system",  # 超能力体系 — 纯人类 / 有神明 / 有魔法 / 修真 / ...
    "time_axis",     # 时间轴 — 原作时代 / 穿越未来 / 穿越古代 / ...
    "tone",          # 整体基调 — 悲剧 / 大团圆 / 黑暗 / 幽默 / ...
    "free_form",     # 自由描述 — 用户用一段话说想要的世界观改造
)


@dataclass
class CounterfactualChange:
    id: str
    project_id: str
    target_type: str           # 'character' / 'event' / 'relationship' / 'world'
    target_id: str             # world 类型固定 '_global_'
    field: str                 # character/event/relationship 的字段名;world 用 VALID_WORLD_FIELDS
    old_value: Optional[str]   # 改前快照(JSON-serialized for arrays);world 类型可 NULL
    new_value: Optional[str]   # 改后值
    user_intent: Optional[str] # ⭐ 2.C+ 用户自然语言意图(LLM 优先级最高)
    created_at: str
    reverted_at: Optional[str]
    applied_in_simulations_json: str    # JSON array of simulation_id
    user_id: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "CounterfactualChange":
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            field=row["field"],
            old_value=row["old_value"],
            new_value=row["new_value"],
            user_intent=row["user_intent"] if "user_intent" in row.keys() else None,
            created_at=row["created_at"],
            reverted_at=row["reverted_at"],
            applied_in_simulations_json=row["applied_in_simulations_json"] or "[]",
            user_id=row["user_id"],
        )

    @property
    def is_active(self) -> bool:
        return self.reverted_at is None

    @property
    def applied_simulation_ids(self) -> list[str]:
        try:
            parsed = json.loads(self.applied_in_simulations_json)
            return [str(x) for x in parsed] if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []

    def to_response(self) -> dict[str, Any]:
        """API 返回口径。old/new value 不解析(前端按字段类型自行处理 JSON)。"""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "field": self.field,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "user_intent": self.user_intent,
            "created_at": self.created_at,
            "reverted_at": self.reverted_at,
            "is_active": self.is_active,
            "applied_in_simulations": self.applied_simulation_ids,
        }
