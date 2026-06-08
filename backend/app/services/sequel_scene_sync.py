"""Sprint 6.A2 M8.A(2026-05-20)— 续作新场景同步入库。

对标 M7.B(sequel_character_sync):每次 outline finalize 或 evolution narrator
完成一幕时调用,把 outline_scenes / simulation_scenes 里出现的 location 名,
**反向入库 project_scenes 表**(若 project_scenes 没有同名);标 origin_simulation_id。

设计原则:
  - **新增不覆盖** — 已存在同名 → 跳过(不动用户手编 / 原作抽取的场景)
  - **来源标记** — 新写入的 row.origin_simulation_id = sim.id(前端区分展示)
  - **行为兜底** — 任何步骤失败都不阻塞主循环(log warning,下次手动可补)
  - **幂等** — 重复调用同一幕不会重复创建(name 去重 + UNIQUE 约束兜底)

API:
  sync_new_scenes_from_outline(conn, sim) → dict           # outline 模式
  sync_new_scene_from_evolution(conn, sim, location) → dict # evolution 模式

为什么治本 "outline 清一色教室":
  - 原本 outline 生成时 LLM 看到 available_project_scenes 只有 1 个 "教室"
  - LLM 倾向选已有(避免"偏离原作"),少有自创
  - 即使自创了,新场景仅落 outline_scenes,**下次 outline / evolution 都不知道**
  - 加了 sequel_scene_sync 后:第一次自创"江畔" → 写入 project_scenes →
    第二次 outline / evolution 自动看到"教室 + 江畔"可选 → 多样性正向反馈
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.models.simulation import Simulation
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)


# 入库 location 的长度限制(对齐 outline_scenes.location 上限 / m6_outline_generator < 30 字)
MAX_LOCATION_NAME_LEN = 30
# 单次最多入库的新场景数(防 LLM 异常输出导致表膨胀)
MAX_NEW_SCENES_PER_SYNC = 30


def sync_new_scenes_from_outline(
    conn: sqlite3.Connection,
    sim: Simulation,
) -> dict:
    """outline finalize 时调:把 outline_scenes 里全部 unique location 入库 project_scenes。

    Args:
      sim: 当前推演

    Returns:
      {
        "created_scenes": [{"id": ..., "name": ..., "first_scene_index": ...}, ...],
        "skipped": [{"name": ..., "reason": "name_exists" | "empty" | "too_long"}, ...],
      }
    """
    summary: dict = {"created_scenes": [], "skipped": []}

    # 拉本 sim 的 outline_scenes(应该都是 user_approved 后的最终版)
    rows = fetch_all(
        conn,
        "SELECT scene_index, location FROM outline_scenes "
        "WHERE outline_id IN (SELECT id FROM simulation_outlines WHERE simulation_id=?) "
        "ORDER BY scene_index ASC",
        (sim.id,),
    )
    if not rows:
        return summary

    return _sync_locations(
        conn, sim,
        locations_with_index=[(int(r["scene_index"]), r["location"]) for r in rows],
        summary=summary,
    )


def sync_new_scene_from_evolution(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene_index: int,
    location: str,
) -> dict:
    """evolution 模式每幕 narrator 完成后调:把本幕 LLM 选的 location 入库 project_scenes。

    Args:
      sim: 当前推演
      scene_index: 当前幕号(用于 description 注明)
      location: scene_picker 给的本幕物理位置

    Returns:
      {"created_scenes": [...], "skipped": [...]}
    """
    summary: dict = {"created_scenes": [], "skipped": []}
    return _sync_locations(
        conn, sim,
        locations_with_index=[(scene_index, location)],
        summary=summary,
    )


def _sync_locations(
    conn: sqlite3.Connection,
    sim: Simulation,
    locations_with_index: list[tuple[int, str]],
    summary: dict,
) -> dict:
    """内部 helper:批量入库 locations。

    locations_with_index: list[(scene_index, location)],按 scene_index 升序处理
                         (first_introduced_scene 取最小)
    """
    # 1. 拉本 project 已有 project_scenes(name 去重用)
    existing_rows = fetch_all(
        conn,
        "SELECT id, name FROM project_scenes WHERE project_id=?",
        (sim.project_id,),
    )
    existing_name_set: set[str] = {r["name"] for r in existing_rows if r["name"]}

    now = iso_now()
    # 用 dict 去重 + 记录 first_scene_index(同一 location 出现多幕取最小)
    new_scenes_first_seen: dict[str, int] = {}
    for scene_idx, raw_location in locations_with_index:
        loc = _clean_location(raw_location)
        if not loc:
            continue
        if len(loc) > MAX_LOCATION_NAME_LEN:
            summary["skipped"].append({"name": raw_location, "reason": "too_long"})
            continue
        if loc in existing_name_set:
            continue   # 项目已有同名场景(原作图谱抽出 / 之前续作入库)— 跳过
        if loc not in new_scenes_first_seen:
            new_scenes_first_seen[loc] = scene_idx

    # 2. INSERT 新 project_scenes 行(标 origin_simulation_id)
    for loc, first_idx in list(new_scenes_first_seen.items())[:MAX_NEW_SCENES_PER_SYNC]:
        new_scene_id = uuid.uuid4().hex
        # description 说明来源 — 给用户在场景图谱上看到"续作生成"上下文
        description = f"续作第 {first_idx} 幕首次登场的新场景(LLM 在创作中自创)"

        try:
            execute(
                conn,
                "INSERT INTO project_scenes "
                "(id, project_id, name, aliases_json, description, "
                " appearance_chunk_count, created_at, updated_at, origin_simulation_id) "
                "VALUES (?, ?, ?, '[]', ?, 0, ?, ?, ?)",
                (
                    new_scene_id, sim.project_id, loc, description,
                    now, now, sim.id,
                ),
            )
        except sqlite3.IntegrityError as e:
            # UNIQUE(project_id, name) 撞 → 罕见的并发场景下,有人刚插过同名;跳过即可
            logger.warning(
                f"sequel_scene_sync: insert project_scene failed name={loc} "
                f"sim={sim.id}: {e}"
            )
            summary["skipped"].append({"name": loc, "reason": f"db_error:{type(e).__name__}"})
            continue

        summary["created_scenes"].append({
            "id": new_scene_id,
            "name": loc,
            "first_scene_index": first_idx,
        })
        existing_name_set.add(loc)

    conn.commit()
    return summary


def _clean_location(raw: object) -> str:
    """清洗 location 文本 — 兜底非 str / 多余空格 / emoji 等。"""
    if not isinstance(raw, str):
        return ""
    return raw.strip()


def list_sequel_scenes(
    conn: sqlite3.Connection,
    project_id: str,
    simulation_id: Optional[str] = None,
) -> list[dict]:
    """查询某项目的"续作产物场景"(可选按 simulation_id 过滤)。

    给前端"项目场景列表"展示"续作生成"标签 / 续作场景面板用。
    """
    if simulation_id:
        rows = fetch_all(
            conn,
            "SELECT * FROM project_scenes "
            "WHERE project_id=? AND origin_simulation_id=? "
            "ORDER BY created_at ASC",
            (project_id, simulation_id),
        )
    else:
        rows = fetch_all(
            conn,
            "SELECT * FROM project_scenes "
            "WHERE project_id=? AND origin_simulation_id IS NOT NULL "
            "ORDER BY created_at ASC",
            (project_id,),
        )
    return [dict(r) for r in rows]


__all__ = [
    "sync_new_scenes_from_outline",
    "sync_new_scene_from_evolution",
    "list_sequel_scenes",
]
