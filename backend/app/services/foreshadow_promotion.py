"""伏笔升级服务(F1.3,2026-06-02)— sim done 后把未解 plot_threads 同步到项目级账本.

背景:
  - plot_threads 表是 sim 内的伏笔账本(单 sim 粒度,推演时 LLM 自动追)
  - foreshadow_ledger 表是项目级的跨代账本(给 chain_context_builder / outline_generator 用)
  - 之前两者**完全没同步**,导致用户在伏笔账本 UI 看到的伏笔,**对"基于本篇续作"的新 sim 没影响**

修法:
  sim done 时(or run_simulation 主循环最后一步),调
  promote_unresolved_threads_to_foreshadow_ledger(conn, sim).
  把所有 open(未解 + 未废弃)plot_threads 转写为 project 级 foreshadow_ledger.

去重策略(关键):
  - 同 project + 相同 content 已存在 open 行 → 跳过(不重复)
  - 同 content 已 resolved → 跳过(老坑已经填了)
  - content 完全不同 → 新建

字段映射:
  plot_threads.description        → foreshadow_ledger.content
  plot_threads.simulation_id       → foreshadow_ledger.introduced_in_simulation_id
  plot_threads.introduced_at_scene_index → foreshadow_ledger.introduced_scene_index
  plot_threads.priority(1/2/3)     → foreshadow_ledger.priority(high/medium/low)
  status                           → 'open'(只有 unresolved 才升级)
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from app.db import execute, fetch_all, fetch_one
from app.models.simulation import Simulation

logger = logging.getLogger(__name__)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


# plot_threads.priority 是 int(1=主线 / 2=支线 / 3=背景)
# foreshadow_ledger.priority 是 str(high / medium / low)
_PRIORITY_MAP = {1: "high", 2: "medium", 3: "low"}


def promote_unresolved_threads_to_foreshadow_ledger(
    conn: sqlite3.Connection,
    sim: Simulation,
) -> tuple[int, int]:
    """sim done 后,把未解 + 未废弃的 plot_threads 升级到 project 级 foreshadow_ledger.

    返回 (inserted_count, skipped_count_due_to_dedup).
    失败 → log + 返 (0, 0)(不阻塞主流程).
    """
    if not sim.id or not sim.project_id:
        return 0, 0

    try:
        # 1. 拉本 sim 的所有未解 + 未废弃 plot_threads(老库无 is_abandoned 列时 COALESCE 兜底)
        try:
            unresolved_rows = fetch_all(
                conn,
                """SELECT id, description, introduced_at_scene_index, priority
                   FROM plot_threads
                   WHERE simulation_id=?
                     AND resolved_at_scene_index IS NULL
                     AND COALESCE(is_abandoned, 0) = 0""",
                (sim.id,),
            )
        except sqlite3.OperationalError:
            # 老库无 is_abandoned 列 → 降级
            unresolved_rows = fetch_all(
                conn,
                """SELECT id, description, introduced_at_scene_index, priority
                   FROM plot_threads
                   WHERE simulation_id=? AND resolved_at_scene_index IS NULL""",
                (sim.id,),
            )

        if not unresolved_rows:
            return 0, 0

        # 2. 拉本 project 已存在的 foreshadow_ledger content 集合(去重用)
        try:
            existing_rows = fetch_all(
                conn,
                "SELECT content, status FROM foreshadow_ledger WHERE project_id=?",
                (sim.project_id,),
            )
            existing_open_contents = {
                (r["content"] or "").strip()
                for r in existing_rows
                if (r["status"] or "open") == "open"
            }
            existing_resolved_contents = {
                (r["content"] or "").strip()
                for r in existing_rows
                if (r["status"] or "") == "resolved"
            }
        except sqlite3.OperationalError:
            # foreshadow_ledger 表不存在 → 默认空集合
            existing_open_contents = set()
            existing_resolved_contents = set()

        # 3. 逐条升级
        inserted = 0
        skipped = 0
        now = _iso_now()

        for row in unresolved_rows:
            content = (row["description"] or "").strip()
            if not content:
                skipped += 1
                continue

            # 去重:已存在(无论 open 还是 resolved)同 content → 跳过
            if content in existing_open_contents:
                skipped += 1
                continue
            if content in existing_resolved_contents:
                skipped += 1
                continue

            # 转优先级
            prio_int = int(row["priority"]) if row["priority"] is not None else 2
            priority_str = _PRIORITY_MAP.get(prio_int, "medium")

            new_id = str(uuid.uuid4())
            try:
                execute(
                    conn,
                    """INSERT INTO foreshadow_ledger
                       (id, project_id, content, introduced_in_simulation_id,
                        introduced_scene_index, status, priority,
                        created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
                    (
                        new_id, sim.project_id, content,
                        sim.id,
                        int(row["introduced_at_scene_index"]),
                        priority_str,
                        now, now,
                    ),
                )
                inserted += 1
                # 更新去重集合,防同 sim 内同 content 重复登记
                existing_open_contents.add(content)
            except sqlite3.OperationalError as e:
                # foreshadow_ledger 表不存在 / 字段缺失 → log + 跳过
                logger.warning(
                    f"foreshadow_promotion: insert failed for content='{content[:30]}...':{e}"
                )
                skipped += 1

        if inserted > 0:
            conn.commit()
            logger.info(
                f"foreshadow_promotion: sim {sim.id} done — "
                f"upgraded {inserted} unresolved plot_threads to project "
                f"foreshadow_ledger (skipped {skipped} dup/empty)"
            )

        return inserted, skipped

    except Exception as e:  # noqa: BLE001
        logger.warning(f"promote_unresolved_threads failed for sim {sim.id}: {e}")
        return 0, 0


__all__ = ["promote_unresolved_threads_to_foreshadow_ledger"]
