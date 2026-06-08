"""PlotThread — Sprint 6.A2 M4.1(2026-05-19)主线/支线任务追踪。

每行 = 剧情线索:已引入 / 未解决 / 已解决:
  - description LLM 给的短句描述
  - priority 1 主线 / 2 次要 / 3 背景(数字越小越优先)
  - staleness 多少幕没推进;>= 阈值 → scene_picker 必须本幕推进
  - resolved_at_scene_index NULL = 仍 ACTIVE,非空 = 已完成幕索引

生产侧:plot_tracker 每幕 narrator 合稿后调用:
  - 抽出本幕新引入的 thread
  - 标记本幕已解决的旧 thread
  - 维护所有 ACTIVE thread 的 staleness

消费侧:
  - scene_picker 必读 ACTIVE threads(优先未推进 + staleness 高的强推)
  - narrator 可读 ACTIVE threads(承接合理的,不引入太多新)

设计起源(Gemini 评测瑕疵 1):
  剧情死循环 = 主线没追踪 = LLM 反复"接新任务无结果"。
  plot_threads 给主循环一个"任务清单",强制完成推进而非无限引入。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

# 老化阈值 — staleness >= 此值时 scene_picker 必须推进该 thread
STALENESS_FORCE_THRESHOLD = 3


@dataclass
class PlotThread:
    id: str
    simulation_id: str
    introduced_at_scene_index: int
    description: str
    resolved_at_scene_index: Optional[int]
    priority: int
    staleness: int
    created_at: str
    updated_at: str
    # SP-5(2026-05-28,migration 070)— 伏笔账本完善
    # 2026-06-02 cleanup:expected_resolution_scene 删除(migration 080)
    # 理由:用户产品决策 — 哪幕回收伏笔是 LLM 导演的工作,不应该用户填.
    is_abandoned: bool = False                        # 是否废弃(与 resolved 互斥)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "PlotThread":
        resolved = row["resolved_at_scene_index"]
        # SP-5 兜底:老库无 is_abandoned 列
        def _safe(key: str, default):
            try:
                return row[key]
            except (KeyError, IndexError):
                return default
        return cls(
            id=row["id"],
            simulation_id=row["simulation_id"],
            introduced_at_scene_index=int(row["introduced_at_scene_index"]),
            description=row["description"] or "",
            resolved_at_scene_index=int(resolved) if resolved is not None else None,
            priority=int(row["priority"]),
            staleness=int(row["staleness"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            is_abandoned=bool(_safe("is_abandoned", 0)),
        )

    @property
    def is_active(self) -> bool:
        """SP-5:废弃也不算 active."""
        return self.resolved_at_scene_index is None and not self.is_abandoned

    @property
    def status(self) -> str:
        """SP-5 派生:active / resolved / abandoned 三态."""
        if self.is_abandoned:
            return "abandoned"
        if self.resolved_at_scene_index is not None:
            return "resolved"
        return "active"

    @property
    def must_advance(self) -> bool:
        """staleness 达阈值 → 下幕必须推进."""
        return self.is_active and self.staleness >= STALENESS_FORCE_THRESHOLD

    # 2026-06-02 cleanup:删除 is_overdue() 方法 — 依赖已删的 expected_resolution_scene

    def to_prompt_line(self) -> str:
        """LLM prompt 友好的单行表示。

        例:[P1 · staleness=3 · MUST_ADVANCE] 刘飞母亲被威胁,5h47m 倒计时
        """
        force = " · MUST_ADVANCE" if self.must_advance else ""
        return (
            f"[P{self.priority} · staleness={self.staleness}{force}] "
            f"{self.description}"
        )
