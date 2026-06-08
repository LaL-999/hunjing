"""Sprint 6.A2 M4.1(2026-05-19)— Plot Tracker。

主线/支线任务追踪 — 治瑕疵 1 "剧情死循环 / 永远接新任务无结果"。

每幕 narrator 合稿后调用,LLM 从 narrative_segment 抽取:
  - 新引入的 plot_thread(任务/悬念/承诺)
  - 已解决的现有 thread(标 resolved_at_scene_index)
  - 每个 ACTIVE thread 在本幕是否被推进(用于 staleness 维护)

staleness 维护规则:
  - 推进了 → staleness = 0
  - 没推进 → staleness += 1
  - staleness >= STALENESS_FORCE_THRESHOLD(3) → scene_picker 必须本幕推进

设计原则:
  - LLM 用现有 thread 列表作 baseline → LLM 决定哪些 resolved / 哪些 advanced
  - 引入新 thread 默认 priority=2(次要),除非 LLM 明确给 1 主线 / 3 背景
  - 推进 = 任何与该 thread 有关的剧情动作(对话/进展/部分完成),非完整解决

API:
  - track_plot_threads(conn, sim, scene_index, narrative_segment) → tuple[dict, dict]
    抽取 + 落库;返回 ({introduced, resolved, advanced, stale}, llm_usage)
  - list_active_threads(conn, simulation_id) → list[PlotThread]
    拉所有 ACTIVE thread(scene_picker / narrator 消费)
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path

from app.db import execute, fetch_all
from app.models.plot_thread import PlotThread
from app.models.simulation import Simulation
from app.services.llm_client import call_llm_json
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


# ============================================================
# 抽取入口
# ============================================================

def track_plot_threads(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene_index: int,
    narrative_segment: str,
) -> tuple[dict, dict]:
    """从一幕 narrative_segment 抽取 thread 变化,落库 + 维护 staleness。

    Args:
      sim: 当前推演(必须 mode='evolution')
      scene_index: 本幕索引
      narrative_segment: narrator 合稿产物

    Returns:
      ({
        "introduced": [thread_id, ...],       # 本幕新引入
        "resolved": [thread_id, ...],         # 本幕标完成
        "advanced": [thread_id, ...],         # 本幕推进(staleness 归零)
        "stale_count": int,                   # 本幕后仍未推进的 ACTIVE thread 数
      }, llm_usage)
    """
    summary = {
        "introduced": [],
        "resolved": [],
        "advanced": [],
        "stale_count": 0,
    }
    if not narrative_segment or not narrative_segment.strip():
        return summary, {"input_tokens": 0, "output_tokens": 0}

    # 拉当前所有 ACTIVE thread → 喂给 LLM 让它判定 resolved/advanced
    active = list_active_threads(conn, sim.id)

    user_input = {
        "scene_index": scene_index,
        "narrative_segment": narrative_segment,
        "active_threads": [
            {
                "id": t.id,
                "description": t.description,
                "priority": t.priority,
                "staleness": t.staleness,
                "introduced_at_scene_index": t.introduced_at_scene_index,
            }
            for t in active
        ],
    }

    system_prompt = _load_prompt("m4_plot_tracker.md")
    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input,
            max_tokens=1000, temperature=0.2,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"plot_tracker LLM failed sim={sim.id} scene={scene_index}: {e}"
        )
        # LLM 失败也要维护 staleness:所有 ACTIVE 都 +1(保守降级)
        _bump_staleness(conn, sim.id, exclude_ids=[])
        return summary, {"input_tokens": 0, "output_tokens": 0}

    if not isinstance(parsed, dict):
        _bump_staleness(conn, sim.id, exclude_ids=[])
        return summary, usage

    now = iso_now()

    # 1. 落 resolved
    resolved_ids = parsed.get("resolved_thread_ids") or []
    if isinstance(resolved_ids, list):
        for tid in resolved_ids:
            if not isinstance(tid, str):
                continue
            execute(
                conn,
                """UPDATE plot_threads
                   SET resolved_at_scene_index=?, updated_at=?, staleness=0
                   WHERE id=? AND simulation_id=? AND resolved_at_scene_index IS NULL""",
                (scene_index, now, tid, sim.id),
            )
            summary["resolved"].append(tid)

    # 2. advanced threads(本幕推进但未解决)→ staleness=0
    advanced_ids = parsed.get("advanced_thread_ids") or []
    if isinstance(advanced_ids, list):
        for tid in advanced_ids:
            if not isinstance(tid, str) or tid in summary["resolved"]:
                continue
            execute(
                conn,
                """UPDATE plot_threads
                   SET staleness=0, updated_at=?
                   WHERE id=? AND simulation_id=? AND resolved_at_scene_index IS NULL""",
                (now, tid, sim.id),
            )
            summary["advanced"].append(tid)

    # 3. 其他 ACTIVE thread(未 resolved 也未 advanced)→ staleness += 1
    untouched_ids = [
        t.id for t in active
        if t.id not in summary["resolved"] and t.id not in summary["advanced"]
    ]
    _bump_staleness(conn, sim.id, exclude_ids=untouched_ids, include_only=True)

    # 4. 新引入 threads
    new_threads = parsed.get("new_threads") or []
    if isinstance(new_threads, list):
        for nt in new_threads:
            if not isinstance(nt, dict):
                continue
            desc = str(nt.get("description") or "").strip()[:500]
            if not desc or len(desc) < 3:
                # 太短(< 3 字)= 噪声,跳过;但允许"找日记" / "A 任务" 等短描述
                continue
            priority = nt.get("priority")
            if priority not in (1, 2, 3):
                priority = 2  # 默认次要
            tid = uuid.uuid4().hex
            execute(
                conn,
                """INSERT INTO plot_threads
                   (id, simulation_id, introduced_at_scene_index,
                    description, resolved_at_scene_index, priority,
                    staleness, created_at, updated_at)
                   VALUES (?, ?, ?, ?, NULL, ?, 0, ?, ?)""",
                (tid, sim.id, scene_index, desc, priority, now, now),
            )
            summary["introduced"].append(tid)

    conn.commit()

    # 5. 重算 stale_count(本幕处理后的 ACTIVE 且未推进的)
    summary["stale_count"] = len([
        t for t in list_active_threads(conn, sim.id)
        if t.staleness > 0
    ])
    return summary, usage


def _bump_staleness(
    conn: sqlite3.Connection,
    simulation_id: str,
    exclude_ids: list[str],
    *,
    include_only: bool = False,
) -> None:
    """ACTIVE thread 的 staleness +1。

    Args:
      exclude_ids:
        - include_only=False: 排除这些 id(LLM 失败时,全部 +1 / exclude=[])
        - include_only=True: **只对**这些 id 操作(本幕已识别为 untouched 的)
    """
    now = iso_now()
    if include_only:
        if not exclude_ids:
            return
        placeholders = ",".join("?" for _ in exclude_ids)
        execute(
            conn,
            f"""UPDATE plot_threads
                SET staleness = staleness + 1, updated_at=?
                WHERE simulation_id=? AND resolved_at_scene_index IS NULL
                  AND id IN ({placeholders})""",
            (now, simulation_id, *exclude_ids),
        )
    else:
        if not exclude_ids:
            execute(
                conn,
                """UPDATE plot_threads
                   SET staleness = staleness + 1, updated_at=?
                   WHERE simulation_id=? AND resolved_at_scene_index IS NULL""",
                (now, simulation_id),
            )
        else:
            placeholders = ",".join("?" for _ in exclude_ids)
            execute(
                conn,
                f"""UPDATE plot_threads
                    SET staleness = staleness + 1, updated_at=?
                    WHERE simulation_id=? AND resolved_at_scene_index IS NULL
                      AND id NOT IN ({placeholders})""",
                (now, simulation_id, *exclude_ids),
            )


# ============================================================
# 查询接口(scene_picker / narrator 消费)
# ============================================================

def list_active_threads(
    conn: sqlite3.Connection,
    simulation_id: str,
) -> list[PlotThread]:
    """拉某 sim 所有"真正还在追的" thread.

    2026-06-02 cleanup:
      - 保留 F1.1:`COALESCE(is_abandoned, 0) = 0` 过滤,让标"废弃"真生效
        (老库 migration 070 前无此列,COALESCE 兜底返 0)
      - 删 F1.2:不再考虑 expected_resolution_scene 距离(列已删 + 用户产品决策:
        哪幕回收是 LLM 导演的事,不需要用户填)

    排序:
      a) priority ASC(主线 1 > 支线 2 > 背景 3)
      b) staleness DESC(停滞越久越优先)
      c) created_at ASC(同等条件下老的先)
    """
    rows = fetch_all(
        conn,
        """SELECT * FROM plot_threads
           WHERE simulation_id=?
             AND resolved_at_scene_index IS NULL
             AND COALESCE(is_abandoned, 0) = 0
           ORDER BY priority ASC, staleness DESC, created_at ASC""",
        (simulation_id,),
    )
    return [PlotThread.from_row(r) for r in rows]
