"""AgentPrivateMemory — Sprint 6.A2 M3.A(2026-05-18)灵魂续写每 agent 私有记忆。

每行 = 某 agent 在某幕产出 / 见证的一条记忆片段。

业务意义:
  - reflection:agent 看到场景 + 上一轮 → 内心独白(只有自己看得到)
  - dialogue:agent 自己说出的对白(自己 + witnesses 都能看到)
  - action:agent 自己做出的行动(同上)
  - witnessed:agent 在场目击的他人对白 / 行动(从他人 dialogue/action 复制一份)

为什么不共享 history:
  - 上帝视角共享 history → 黛玉知道宝钗背后说她什么(若没在场)→ 违反"agent 像真人"
  - 私有 memory → 真信息不对称,符合产品愿景
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

MemoryType = Literal["reflection", "dialogue", "action", "witnessed"]


@dataclass
class AgentPrivateMemory:
    id: str
    simulation_id: str
    character_id: str
    scene_index: int
    memory_type: MemoryType
    content: str
    other_chars: list[str]
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "AgentPrivateMemory":
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
            character_id=row["character_id"],
            scene_index=int(row["scene_index"]),
            memory_type=row["memory_type"],
            content=row["content"],
            other_chars=_safe_list(row["other_chars_json"]),
            created_at=row["created_at"],
        )
