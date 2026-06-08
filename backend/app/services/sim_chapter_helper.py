"""章节体系辅助(2026-06-01).

主要职责:
  - compute_start_chapter_for_sim:沿继承链回溯,算出本 sim 的起始章号
  - 该值在 sim 创建时一次性写入 simulations.start_chapter_locked,之后不变
    → 规避"前篇字数变了 → 本 sim 章号跳"的副作用

算法:
  has_ancestors → 顺继承链回溯,累加每个父辈 chapter_count = ceil(narrative_length / avg_chapter_size)
  no ancestors  → 1

avg_chapter_size 取 projects.chapter_size_min/max 的中点(因为父辈可能跨多个项目设置,
  我们这里用当前 sim 的项目设置作近似 — 实际继承链多在同项目内,差异可忽略).
"""
from __future__ import annotations

import math
import sqlite3
from typing import Optional

from app.db import fetch_one
from app.models.simulation import Simulation


def _get_chapter_size_range_for_project(
    conn: sqlite3.Connection, project_id: str,
) -> tuple[int, int]:
    """从 projects 表取章节字数区间.老库或字段缺失 → 默认 (1500, 2500)."""
    cmin, cmax = 1500, 2500
    # 兼容老库:migration 077 前无 chapter_size_min/max 列 → OperationalError
    try:
        row = fetch_one(
            conn,
            "SELECT chapter_size_min, chapter_size_max FROM projects WHERE id=?",
            (project_id,),
        )
    except sqlite3.OperationalError:
        return cmin, cmax
    if not row:
        return cmin, cmax
    try:
        m = row["chapter_size_min"]
        if isinstance(m, int) and 500 <= m <= 8000:
            cmin = m
    except (KeyError, IndexError, TypeError):
        pass
    try:
        M = row["chapter_size_max"]
        if isinstance(M, int) and 1500 <= M <= 10000:
            cmax = M
    except (KeyError, IndexError, TypeError):
        pass
    if cmax < cmin + 500:
        cmax = cmin + 500
    return cmin, cmax


def _chapter_count_for_narrative(
    narrative_len: int, chapter_size_min: int, chapter_size_max: int,
) -> int:
    """根据字数估算章节数.用区间中点近似(对实际 chapterize 输出几乎一致,差异 ≤ 1 章)."""
    if narrative_len <= 0:
        return 0
    avg = (chapter_size_min + chapter_size_max) // 2
    return max(1, math.ceil(narrative_len / max(1, avg)))


def compute_start_chapter_for_sim(
    conn: sqlite3.Connection,
    sim: Simulation,
) -> int:
    """算本 sim 的起始章号.

    递归追溯 sim.context_simulation_ids 链(滚雪球前篇)— 累加每篇章节数 + 1.
    无前篇 → 返 1.

    注:context_simulation_ids 可能有多条(多 sim 滚雪球),按顺序拼接 → 全部累加.
    """
    ctx_ids = sim.context_simulation_ids or []
    if not ctx_ids:
        return 1

    cmin, cmax = _get_chapter_size_range_for_project(conn, sim.project_id)

    total_prev_chapters = 0
    # 沿 ctx_ids 拉父辈,递归累加(深度优先按顺序)
    for prev_sim_id in ctx_ids:
        prev_row = fetch_one(
            conn,
            "SELECT id, narrative, project_id, context_simulation_ids, "
            "       start_chapter_locked FROM simulations WHERE id=?",
            (prev_sim_id,),
        )
        if not prev_row:
            continue
        prev_narr = prev_row["narrative"] or ""
        prev_chapters = _chapter_count_for_narrative(len(prev_narr), cmin, cmax)
        total_prev_chapters += prev_chapters

    return total_prev_chapters + 1


def get_effective_start_chapter(sim: Simulation) -> int:
    """读 sim.start_chapter_locked 若存在;老 sim(NULL)降级返 1."""
    if sim.start_chapter_locked and sim.start_chapter_locked > 0:
        return sim.start_chapter_locked
    return 1


__all__ = [
    "compute_start_chapter_for_sim",
    "get_effective_start_chapter",
    "_get_chapter_size_range_for_project",
]
