"""一键灌满北极星(2026-05-29 末)— 串行编排 4 个 inferer + per-character drivers.

用户痛点:每个项目要点 3-N 次"AI 推断"按钮(脊柱 / 视角 / 知识 / N 个角色驱动),
等待累计 1-3 分钟,UX 摩擦极大.

方案:一键编排 — 后端串行调用所有 inferer,异常隔离,返汇总 report.

异常策略(关键):
- 某个 inferer 抛错 → log warning + record 到 report["failures"],继续下一个
- 整体永不抛 — 用户至少能拿到部分成功

参数:
- include_drivers:是否给每个角色推 drivers(默认 True,但角色多时可能慢)
- force_knowledge:knowledge_boundaries 是否 overwrite(默认 False — 项目已有 facts 时跳过)

返回(对每个 stage):
{
  "story_core": {applied: bool, reasoning: str},
  "narrative_view": {applied: bool, reasoning: str, focus_name: str},
  "knowledge_boundaries": {applied: bool, facts_created: int, knowledge_created: int,
                            skipped_reason: str|None, reasoning: str},
  "character_drivers": [
    {character_id, character_name, applied: bool, reasoning: str}
  ],
  "stats": {
    "stages_attempted": int, "stages_succeeded": int, "failures": [str]
  }
}
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Callable, Optional

from app.db import fetch_all

logger = logging.getLogger(__name__)


# SSE 进度事件类型(2026-05-30²):
# - plan: 推送总 stages 计数(在开始前)
# - stage_start: 某 stage 开始 + 当前 1-based 序号
# - stage_done: 某 stage 完成 + applied 状态 + reasoning 摘要
# - stage_failed: 某 stage 异常隔离 + 错误简介
# - done: 全部完成 + 最终 stats
ProgressEvent = dict[str, Any]
ProgressCallback = Callable[[ProgressEvent], None]


def _emit(cb: Optional[ProgressCallback], event: ProgressEvent) -> None:
    """安全调用 callback,不让 progress 异常中断主流程."""
    if cb is None:
        return
    try:
        cb(event)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"progress_callback raised (ignored): {e}")


def infer_all_boards_for_project(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    include_drivers: bool = True,
    force_knowledge: bool = False,
    include_polarity: bool = True,
    force_polarity: bool = False,
    progress_callback: Optional[ProgressCallback] = None,
) -> dict[str, Any]:
    """编排 5 + N 个 inferer 调用,异常隔离,返汇总 report.

    串行执行(不并发):
      1. story_core_inferer → cache
      2. narrative_view_inferer → cache
      3. knowledge_boundaries_inferer → cache(overwrite=force_knowledge)
      4. relationship_polarity_inferer → cache(overwrite=force_polarity)
      5. (可选)对每个 character 调 character_drivers_inferer → cache(overwrite=False)
    """
    report: dict[str, Any] = {
        "story_core": None,
        "narrative_view": None,
        "knowledge_boundaries": None,
        "relationship_polarity": None,
        "character_drivers": [],
        "stats": {
            "stages_attempted": 0,
            "stages_succeeded": 0,
            "failures": [],
        },
    }

    # 计算总 stages(plan 事件用)
    # 4 项目级(story/view/knowledge/polarity 可关) + N 角色
    total_stages = 1  # story_core
    total_stages += 1  # narrative_view
    total_stages += 1  # knowledge_boundaries
    if include_polarity:
        total_stages += 1
    if include_drivers:
        char_count_row = fetch_all(
            conn,
            "SELECT COUNT(*) AS cnt FROM characters WHERE project_id=?",
            (project_id,),
        )
        char_count = char_count_row[0]["cnt"] if char_count_row else 0
        total_stages += min(char_count, 20)
    _emit(progress_callback, {
        "event": "plan",
        "total_stages": total_stages,
        "include_drivers": include_drivers,
        "include_polarity": include_polarity,
    })

    stage_idx = 0

    # ============================================================
    # Stage 1: story_core
    # ============================================================
    stage_idx += 1
    _emit(progress_callback, {
        "event": "stage_start", "stage": "story_core",
        "stage_index": stage_idx, "total_stages": total_stages,
        "label": "故事脊柱",
    })
    report["stats"]["stages_attempted"] += 1
    try:
        from app.services.story_core_inferer import (
            infer_story_core_for_project,
            cache_story_core_to_project,
        )
        result = infer_story_core_for_project(conn, project_id)
        cache_story_core_to_project(conn, project_id, result)
        applied = bool(
            result.get("core_dramatic_question")
            or result.get("theme")
            or result.get("ending_direction")
        )
        report["story_core"] = {
            "applied": applied,
            "reasoning": result.get("reasoning", "")[:300],
        }
        if applied:
            report["stats"]["stages_succeeded"] += 1
        _emit(progress_callback, {
            "event": "stage_done", "stage": "story_core",
            "stage_index": stage_idx,
            "applied": applied,
            "reasoning": result.get("reasoning", "")[:200],
        })
    except Exception as e:  # noqa: BLE001
        logger.warning(f"infer_all_boards story_core failed for {project_id}: {e}")
        report["story_core"] = {"applied": False, "reasoning": f"失败:{type(e).__name__}"}
        report["stats"]["failures"].append("story_core")
        _emit(progress_callback, {
            "event": "stage_failed", "stage": "story_core",
            "stage_index": stage_idx,
            "error": f"{type(e).__name__}: {str(e)[:120]}",
        })

    # ============================================================
    # Stage 2: narrative_view
    # ============================================================
    stage_idx += 1
    _emit(progress_callback, {
        "event": "stage_start", "stage": "narrative_view",
        "stage_index": stage_idx, "total_stages": total_stages,
        "label": "视角扩展",
    })
    report["stats"]["stages_attempted"] += 1
    try:
        from app.services.narrative_view_inferer import (
            infer_narrative_view_for_project,
            cache_narrative_view_to_project,
        )
        result = infer_narrative_view_for_project(conn, project_id)
        cache_narrative_view_to_project(conn, project_id, result)
        applied = bool(
            result.get("narrative_focus_character_id")
            or result.get("narrator_reliability")
            or result.get("narrative_distance")
        )
        report["narrative_view"] = {
            "applied": applied,
            "reasoning": result.get("reasoning", "")[:300],
            "focus_name": result.get("narrative_focus_character_name", ""),
        }
        if applied:
            report["stats"]["stages_succeeded"] += 1
        _emit(progress_callback, {
            "event": "stage_done", "stage": "narrative_view",
            "stage_index": stage_idx,
            "applied": applied,
            "reasoning": result.get("reasoning", "")[:200],
        })
    except Exception as e:  # noqa: BLE001
        logger.warning(f"infer_all_boards narrative_view failed for {project_id}: {e}")
        report["narrative_view"] = {
            "applied": False, "reasoning": f"失败:{type(e).__name__}", "focus_name": "",
        }
        report["stats"]["failures"].append("narrative_view")
        _emit(progress_callback, {
            "event": "stage_failed", "stage": "narrative_view",
            "stage_index": stage_idx,
            "error": f"{type(e).__name__}: {str(e)[:120]}",
        })

    # ============================================================
    # Stage 3: knowledge_boundaries
    # ============================================================
    stage_idx += 1
    _emit(progress_callback, {
        "event": "stage_start", "stage": "knowledge_boundaries",
        "stage_index": stage_idx, "total_stages": total_stages,
        "label": "知识边界",
    })
    report["stats"]["stages_attempted"] += 1
    try:
        from app.services.knowledge_boundaries_inferer import (
            infer_knowledge_boundaries,
            cache_knowledge_boundaries,
        )
        result = infer_knowledge_boundaries(conn, project_id)
        cache_report = cache_knowledge_boundaries(
            conn, project_id, result, overwrite=force_knowledge,
        )
        report["knowledge_boundaries"] = {
            "applied": cache_report["applied"],
            "facts_created": len(cache_report["fact_ids_created"]),
            "knowledge_created": cache_report["knowledge_created"],
            "skipped_reason": cache_report.get("skipped_reason"),
            "reasoning": result.get("reasoning", "")[:300],
        }
        if cache_report["applied"]:
            report["stats"]["stages_succeeded"] += 1
        _emit(progress_callback, {
            "event": "stage_done", "stage": "knowledge_boundaries",
            "stage_index": stage_idx,
            "applied": cache_report["applied"],
            "reasoning": result.get("reasoning", "")[:200],
            "skipped_reason": cache_report.get("skipped_reason"),
            "facts_created": len(cache_report["fact_ids_created"]),
        })
    except Exception as e:  # noqa: BLE001
        logger.warning(f"infer_all_boards knowledge_boundaries failed for {project_id}: {e}")
        report["knowledge_boundaries"] = {
            "applied": False, "facts_created": 0, "knowledge_created": 0,
            "skipped_reason": f"失败:{type(e).__name__}", "reasoning": "",
        }
        report["stats"]["failures"].append("knowledge_boundaries")
        _emit(progress_callback, {
            "event": "stage_failed", "stage": "knowledge_boundaries",
            "stage_index": stage_idx,
            "error": f"{type(e).__name__}: {str(e)[:120]}",
        })

    # ============================================================
    # Stage 4: relationship_polarity(SP-7.1)
    # ============================================================
    if include_polarity:
        stage_idx += 1
        _emit(progress_callback, {
            "event": "stage_start", "stage": "relationship_polarity",
            "stage_index": stage_idx, "total_stages": total_stages,
            "label": "关系极性",
        })
        report["stats"]["stages_attempted"] += 1
        try:
            from app.services.relationship_polarity_inferer import (
                infer_polarity_for_project,
                cache_polarity_to_project,
            )
            result = infer_polarity_for_project(conn, project_id)
            pol_cache = cache_polarity_to_project(
                conn, project_id, result, overwrite=force_polarity,
            )
            report["relationship_polarity"] = {
                "applied": pol_cache["applied"],
                "updated_count": pol_cache["updated_count"],
                "skipped_count": pol_cache["skipped_count"],
                "no_match_count": pol_cache["no_match_count"],
                "reasoning": result.get("reasoning", "")[:300],
            }
            if pol_cache["applied"]:
                report["stats"]["stages_succeeded"] += 1
            _emit(progress_callback, {
                "event": "stage_done", "stage": "relationship_polarity",
                "stage_index": stage_idx,
                "applied": pol_cache["applied"],
                "reasoning": result.get("reasoning", "")[:200],
                "updated_count": pol_cache["updated_count"],
            })
        except Exception as e:  # noqa: BLE001
            logger.warning(f"infer_all_boards relationship_polarity failed for {project_id}: {e}")
            report["relationship_polarity"] = {
                "applied": False, "updated_count": 0,
                "skipped_count": 0, "no_match_count": 0,
                "reasoning": f"失败:{type(e).__name__}",
            }
            report["stats"]["failures"].append("relationship_polarity")
            _emit(progress_callback, {
                "event": "stage_failed", "stage": "relationship_polarity",
                "stage_index": stage_idx,
                "error": f"{type(e).__name__}: {str(e)[:120]}",
            })

    # ============================================================
    # Stage 5(可选): per-character drivers
    # ============================================================
    if include_drivers:
        char_rows = fetch_all(
            conn,
            "SELECT id, name FROM characters WHERE project_id=? "
            "ORDER BY is_protagonist DESC, name ASC",
            (project_id,),
        )
        for row in char_rows:
            cid = row["id"]
            cname = row["name"]
            stage_idx += 1
            _emit(progress_callback, {
                "event": "stage_start", "stage": f"driver:{cname}",
                "stage_index": stage_idx, "total_stages": total_stages,
                "label": f"角色驱动 · {cname}",
            })
            report["stats"]["stages_attempted"] += 1
            try:
                from app.services.character_drivers_inferer import (
                    infer_drivers_for_character,
                    cache_drivers_to_character,
                )
                result = infer_drivers_for_character(conn, cid)
                # 一键场景默认 overwrite=False — 不破坏用户已填的角色驱动
                cache_drivers_to_character(conn, cid, result, overwrite=False)
                applied = bool(
                    result.get("surface_goal")
                    or result.get("deep_need")
                    or result.get("fatal_blind_spot")
                    or result.get("arc_from_to")
                    or (result.get("secrets") or [])
                )
                report["character_drivers"].append({
                    "character_id": cid,
                    "character_name": cname,
                    "applied": applied,
                    "reasoning": result.get("reasoning", "")[:200],
                })
                if applied:
                    report["stats"]["stages_succeeded"] += 1
                _emit(progress_callback, {
                    "event": "stage_done", "stage": f"driver:{cname}",
                    "stage_index": stage_idx,
                    "applied": applied,
                    "character_name": cname,
                    "reasoning": result.get("reasoning", "")[:200],
                })
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"infer_all_boards drivers failed for char {cid}: {e}"
                )
                report["character_drivers"].append({
                    "character_id": cid,
                    "character_name": cname,
                    "applied": False,
                    "reasoning": f"失败:{type(e).__name__}",
                })
                report["stats"]["failures"].append(f"driver:{cname}")
                _emit(progress_callback, {
                    "event": "stage_failed", "stage": f"driver:{cname}",
                    "stage_index": stage_idx,
                    "character_name": cname,
                    "error": f"{type(e).__name__}: {str(e)[:120]}",
                })

    # 全部完成
    _emit(progress_callback, {
        "event": "done",
        "stats": report["stats"],
    })

    return report


__all__ = ["infer_all_boards_for_project"]
