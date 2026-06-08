"""ActionLedgerEntry — Sprint 6.A2 M5.2(2026-05-20)原子动作流水。

每行 = 一次原子动作("张凡 抽出 照片" / "李宇天 踹 门" / "刘飞 击杀 周梦"):
  - actor_name + verb + object_name 三元组
  - is_repeatable 0=原子不可重复(找到关键物件/击杀)/ 1=可重复(说话/走动)
  - 关联到 canonical_entity_id(可空,用于 actor/object 是已注册实体时)

消费侧:narrator/agent_dialogue 调用前,把 is_repeatable=0 的 已发生原子动作
       转成"禁止重复"清单,prepend 到 system_prompt 顶部

设计起源(Gemini 第二轮评测瑕疵 2):
  张凡"从枕芯里抽出照片+翻转"动作跨幕重复 4 次(不同场景下)
  → narrator 看不到"上幕已经抽过照片",每次重新虚构
  → action_ledger 把原子动作锁定,LLM 严格不许重复
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional


@dataclass
class ActionLedgerEntry:
    id: str
    simulation_id: str
    scene_index: int
    actor_name: str
    actor_entity_id: Optional[str]
    verb: str
    object_name: Optional[str]
    object_entity_id: Optional[str]
    description: str
    is_repeatable: bool
    created_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "ActionLedgerEntry":
        return cls(
            id=row["id"],
            simulation_id=row["simulation_id"],
            scene_index=int(row["scene_index"]),
            actor_name=row["actor_name"],
            actor_entity_id=row["actor_entity_id"],
            verb=row["verb"],
            object_name=row["object_name"],
            object_entity_id=row["object_entity_id"],
            description=row["description"] or "",
            is_repeatable=bool(row["is_repeatable"]),
            created_at=row["created_at"],
        )

    def to_prompt_line(self) -> str:
        """LLM prompt 友好的单行表示。

        例:[第 1 幕 · 不可重复] 张凡 抽出 照片 — 从韩紫雨家卧室枕芯里抽出泛黄照片
        """
        rep = "可重复" if self.is_repeatable else "不可重复"
        obj = f" {self.object_name}" if self.object_name else ""
        return (
            f"[第 {self.scene_index + 1} 幕 · {rep}] "
            f"{self.actor_name} {self.verb}{obj} — {self.description[:120]}"
        )
