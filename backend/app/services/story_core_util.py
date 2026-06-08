"""SP-1(2026-05-28)— 故事内核三件套注入(灵魂续写北极星·目的层).

设计源:用户清单 + Claude chat 建议合并:LLM 受训成爱"尽早闭合悬念",
没有"故事终点"会把好不容易攒的张力提前泄光.给推演一个目标弧 → 它才敢憋着不解决.

三字段(projects 表 migration 066):
  - core_dramatic_question: 一句话脊柱(整本书围着它转)
  - theme: 主题(独立于基调)
  - ending_direction: 终点情绪 / 走向(粗略落点)

注入策略:
  - 比物理 / 时间 / 实体身份 更高优先级 — 它是"目的层"
  - 在 hard_constraints section 0.0 注入(全段最顶)
  - 续作正在第 X / Y 幕时,提示"距离终点还差 Y-X 幕,该攒还是该释放"

与已有字段正交:
  - tone(world_baseline)= 情绪色温(本书是哀是喜)
  - with_grand_finale(simulation)= 结构开关(本次推演要不要走向终章)
  - ending_direction = 情绪终点(故事最后落在什么状态)
  - inferred_pacing = 节奏档位

普适性:任何作品 + 任何 mode(initial/middle/end)只要这 3 字段填了就生效;
都为空则返空串(无副作用,跟其他 build_X_block 一致).
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from app.db import fetch_one


logger = logging.getLogger(__name__)


def build_story_core_block(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    current_scene_index: Optional[int] = None,
    total_scenes_planned: Optional[int] = None,
) -> str:
    """构造"故事内核"注入段.

    Args:
      project_id: 当前推演所属项目
      current_scene_index: 本幕索引(0-based),None 时不显示进度提示
      total_scenes_planned: 整篇预计幕数,None 时不显示进度

    Returns:
      非空 markdown 块(若 3 字段全空则返 "")
    """
    try:
        row = fetch_one(
            conn,
            "SELECT core_dramatic_question, theme, ending_direction "
            "FROM projects WHERE id=?",
            (project_id,),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"story_core_util: fetch project failed: {e}")
        return ""

    if not row:
        return ""

    # SQLite Row 对老库(migration 066 前)无此列 → KeyError;一律兜底空
    def _safe(key: str) -> str:
        try:
            v = row[key]
            return v.strip() if isinstance(v, str) else ""
        except (KeyError, IndexError):
            return ""

    cdq = _safe("core_dramatic_question")
    theme = _safe("theme")
    ending = _safe("ending_direction")

    # 三字段全空 → 无效,不输出块(避免污染 prompt)
    if not (cdq or theme or ending):
        return ""

    lines: list[str] = ["【故事内核 — 本作脊柱(最高优先级)】"]
    if cdq:
        lines.append(f"  · 核心戏剧问题:{cdq}")
    if theme:
        lines.append(f"  · 主题:{theme}")
    if ending:
        lines.append(f"  · 终点方向:{ending}")

    # 进度提示(可选):当前在哪、还差多少幕到终点
    if (
        current_scene_index is not None
        and total_scenes_planned is not None
        and total_scenes_planned > 0
    ):
        remaining = max(0, total_scenes_planned - current_scene_index - 1)
        progress_pct = round(
            (current_scene_index + 1) / total_scenes_planned * 100
        )
        lines.append(
            f"  · 本幕进度:第 {current_scene_index + 1} / {total_scenes_planned} 幕"
            f"(已走 {progress_pct}%,距终点还有 {remaining} 幕)"
        )

    lines.append(
        "\n  ⚠ 铁律(LLM 写作易犯的 3 种内伤,本作必须杜绝):\n"
        "    1. **不许提前泄气**:若距终点还远(< 70% 进度),核心戏剧问题**不能解决**;\n"
        "       张力该攒就攒,不许图省事让矛盾「一笔勾销」\n"
        "    2. **不许偏离主题**:本幕剧情走向必须服务于上方主题;\n"
        "       若你写出「事件 A 但跟主题无关」,说明这一笔是闲笔,删了重写\n"
        "    3. **不许漂移终点**:本幕的情绪 / 走向 / 角色变化必须**指向终点方向**,\n"
        "       不许写出与终点情绪冲突的桥段(如终点是「悲剧收束」却在 80% 处给大团圆)\n"
        "  核心理念:LLM 没目标弧时会提前泄气;有目标弧时才敢「憋着不解决」,这正是灵魂续写的精髓."
    )
    return "\n".join(lines)


__all__ = ["build_story_core_block"]
