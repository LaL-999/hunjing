"""SP-3(2026-05-28)— 知识边界注入(信息不对称层).

为本幕在场角色构造"已知 / 未知"清单,注入 narrator hard_constraints.
治 AI 写作最大连贯 bug:角色用了他不知道的信息.

输入:project_id + 在场 character_names + current_scene_index
输出:prompt block 字符串(无事实 / 无知识标记 → 返 "")
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from app.db import fetch_all


logger = logging.getLogger(__name__)


def build_knowledge_block(
    conn: sqlite3.Connection,
    project_id: str,
    character_names: list[str],
    *,
    current_scene_index: Optional[int] = None,
) -> str:
    """构造"知识边界"注入段.

    Args:
      character_names: 本幕在场角色名(直接用 name 查 characters 表拿 id)
      current_scene_index: 本幕索引;用于过滤"还没到的幕的信息"

    Returns:
      非空块(无项目级 facts 或无角色 → 返 "")
    """
    if not character_names:
        return ""

    # 1. 拿在场角色 id + name
    placeholders = ",".join(["?"] * len(character_names))
    try:
        char_rows = fetch_all(
            conn,
            f"SELECT id, name FROM characters "
            f"WHERE project_id=? AND name IN ({placeholders})",
            (project_id, *character_names),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"knowledge_util: char fetch failed: {e}")
        return ""

    if not char_rows:
        return ""

    # 2. 拿项目级所有 facts(用于判定 unknown)
    try:
        all_facts = fetch_all(
            conn,
            "SELECT id, description, is_sensitive FROM story_facts "
            "WHERE project_id=?",
            (project_id,),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"knowledge_util: facts fetch failed: {e}")
        return ""

    if not all_facts:
        return ""  # 无事实,无知识边界可注入

    # 3. 为每个在场角色聚合 known + unknown
    from app.services.character_knowledge_service import (
        get_facts_known_by,
        get_facts_unknown_by,
    )

    char_blocks: list[str] = []
    for c in char_rows:
        cid = c["id"]
        cname = c["name"]
        known = get_facts_known_by(conn, cid, up_to_scene=current_scene_index)
        unknown = get_facts_unknown_by(
            conn,
            character_id=cid,
            project_id=project_id,
            up_to_scene=current_scene_index,
        )

        if not (known or unknown):
            continue  # 该角色完全没标过 → 不显示(避免无意义占行)

        lines = [f"· {cname}:"]
        if known:
            known_strs = []
            for k in known:
                desc = k["description"]
                conf = k.get("confidence", "confirmed")
                # confidence != confirmed 时,加注
                conf_suffix = ""
                if conf == "suspected":
                    conf_suffix = "(怀疑)"
                elif conf == "wrong":
                    conf_suffix = "(误信)"
                known_strs.append(f"{desc}{conf_suffix}")
            lines.append(f"    已知:{' / '.join(known_strs)}")
        if unknown:
            unknown_strs = [u["description"] for u in unknown]
            lines.append(f"    不知:{' / '.join(unknown_strs)}")
        char_blocks.append("\n".join(lines))

    if not char_blocks:
        return ""

    out_lines: list[str] = [
        "【知识边界 — 本幕在场角色各自知道什么 / 不知道什么】",
    ]
    out_lines.extend(char_blocks)
    out_lines.append(
        "\n  ⚠ 铁律 — 信息不对称是 AI 写作最大连贯 bug 源:\n"
        "    1. 叙述者描写时,**只能写本场景 POV 视角角色知道的信息** —— \n"
        "       第三人称限知 / 第一人称下,POV 不知道的事实不许被叙述者用全知口吻提及\n"
        "    2. 角色对白 / 内心独白只能基于他「已知」清单 —— \n"
        "       不许让 A 在对话里提及他根本不知道的「不知」清单事实\n"
        "       (典型坑:绿子说「直子已经死了」— 但绿子根本不知道直子的存在)\n"
        "    3. 「怀疑」状态可暗示 / 揣测,但措辞必须含糊;「误信」可坚定但内容是错的\n"
        "    4. 信息不对称 = 戏剧反讽 = 张力源 —— 不许「为对话顺畅」让角色暴露不该知道的\n"
        "    5. 若某角色本幕首次得知某事实,narrator 必须**显式写出他获知的瞬间**\n"
        "       (听到 / 看到 / 被告知 — 不许凭空就知道了)"
    )
    return "\n".join(out_lines)


__all__ = ["build_knowledge_block"]
