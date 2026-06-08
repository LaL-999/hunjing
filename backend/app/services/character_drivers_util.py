"""SP-2(2026-05-28)— 角色驱动力注入(灵魂续写北极星·驱动层).

设计:让角色从被动反应(描述层 identity/personality)升级为主动 agent(驱动层
goal/need/secret/arc).在 hard_constraints 注入,narrator 能看到每个在场角色
的"想要 vs 需要"张力 + 秘密对谁瞒着 + 预期弧光.

输入:conn + project_id + 在场 character_names
输出:prompt block 字符串(若 5 字段全空则返 "",不污染 prompt)

注入位置(由 caller 决定):hard_constraints 1.5(实体身份锁定之后,因为它是
特定在场角色的驱动力扩展).

与已有字段语义分层:
  - identity / personality / quotes / no_go_list / behavior_baseline:描述层(静态)
  - surface_goal / deep_need / fatal_blind_spot / arc_from_to / secrets:驱动层(动态)
两层正交,不冲突.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Optional

from app.db import fetch_all


logger = logging.getLogger(__name__)


def _safe_secrets(raw: Optional[str]) -> list[dict]:
    if not raw or not isinstance(raw, str):
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [s for s in parsed if isinstance(s, dict)]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def build_character_drivers_block(
    conn: sqlite3.Connection,
    project_id: str,
    character_names: list[str],
) -> str:
    """构造角色驱动力注入段.

    Args:
      project_id: 项目 ID
      character_names: 本幕在场角色名(用 IN 查询)

    Returns:
      非空 markdown 块(若所有在场角色 5 字段都空 → 返 "")
    """
    if not character_names:
        return ""

    placeholders = ",".join(["?"] * len(character_names))
    try:
        rows = fetch_all(
            conn,
            f"SELECT name, surface_goal, deep_need, fatal_blind_spot, "
            f"arc_from_to, secret_json FROM characters "
            f"WHERE project_id=? AND name IN ({placeholders})",
            (project_id, *character_names),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"character_drivers_util: fetch failed: {e}")
        return ""

    # 过滤出至少有一个驱动字段非空的角色
    char_blocks: list[str] = []
    for r in rows:
        sg = (r["surface_goal"] or "").strip() if r["surface_goal"] else ""
        dn = (r["deep_need"] or "").strip() if r["deep_need"] else ""
        fbs = (r["fatal_blind_spot"] or "").strip() if r["fatal_blind_spot"] else ""
        arc = (r["arc_from_to"] or "").strip() if r["arc_from_to"] else ""
        secrets = _safe_secrets(r["secret_json"])

        if not (sg or dn or fbs or arc or secrets):
            continue

        char_lines = [f"· {r['name']}:"]
        if sg:
            char_lines.append(f"    表层想要:{sg}")
        if dn:
            char_lines.append(f"    深层需要:{dn}")
        if fbs:
            char_lines.append(f"    致命盲区:{fbs}")
        if arc:
            char_lines.append(f"    预期弧光:{arc}")
        if secrets:
            for s in secrets:
                desc = s.get("description", "").strip()
                if not desc:
                    continue
                hidden = s.get("hidden_from") or []
                if hidden:
                    char_lines.append(
                        f"    秘密(对 {', '.join(hidden)} 瞒着):{desc}"
                    )
                else:
                    char_lines.append(f"    秘密(对所有人瞒着):{desc}")
        char_blocks.append("\n".join(char_lines))

    if not char_blocks:
        return ""

    lines: list[str] = ["【角色驱动力 — 本幕在场角色的内心张力】"]
    lines.extend(char_blocks)
    lines.append(
        "\n  ⚠ 铁律:\n"
        "    1. 每个角色的「表层想要」和「深层需要」必须有戏剧张力 —— "
        "他嘴上追的 ≠ 他真正缺的;这种撕扯本身就是好戏\n"
        "    2. 「致命盲区」是他自己看不见的 —— 旁人能看见,他自己不能;\n"
        "       不许让角色突然「顿悟」自己的盲区(那是 deus ex machina 假悟道)\n"
        "    3. 「预期弧光」是整本书的轨道 —— 本幕的选择必须指向这条弧,\n"
        "       不许写出与弧光反向的桥段(如弧光是「冷漠→救赎」却在中段更冷漠化)\n"
        "    4. 「秘密 对 X 瞒着」= 该角色在 X 在场时,**绝不能口头/动作暴露**;\n"
        "       秘密暴露是大戏剧转折,必须由 outline 规划,不许 narrator 临时泄露\n"
        "  核心:角色靠「想要 vs 需要」撕扯前进,不是被剧情推着走 — 这是 agent 的根本"
    )
    return "\n".join(lines)


__all__ = ["build_character_drivers_block"]
