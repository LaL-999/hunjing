"""Sprint 6.A2 路线图 #6(2026-05-23)— 全局搜索 service。

跨用户所有项目搜 5 类实体(模糊匹配,精度版 2026-05-23):
  - projects(name)
  - characters(只 name — 不搜 identity / aliases,避免角色描述含某词就被命中)
  - events(只 description — 事件没独立 name 字段;不搜 time_anchor)
  - scenes / project_scenes(只 name — 不搜 description / aliases)
  - simulations(divergence)

  ⚠️ 关系(relationships)**不搜** — 用户决策:搜出来一条关系但跳不到具体编辑位置
  意义不大;待"精确定位到实体"feature 做完后再加回来。

设计:
  - SQLite LIKE '%q%' — 数据量 (中型项目 20+ 角色 / 50+ 事件) 毫秒级,无需 FTS
  - 每条返回 project_id + project_name(给前端面包屑显"老奶奶 · 《项目 A》")
  - 跨用户隔离:所有查询 JOIN projects ON projects.user_id = ?
  - 可选 project_id 参数:给了就限当前项目(PyCharm 风范围 tab"当前项目")
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any, Optional

from app.db import fetch_all

logger = logging.getLogger(__name__)


# 每类实体最多返回条数(防一次拉爆;前端列表也容纳不下太多)
DEFAULT_LIMIT_PER_TYPE = 10


def search(
    conn: sqlite3.Connection,
    user_id: str,
    q: str,
    project_id: Optional[str] = None,
    limit: int = DEFAULT_LIMIT_PER_TYPE,
) -> dict[str, Any]:
    """跨用户搜 6 类实体。

    Args:
        user_id: 当前用户 id(鉴权 — 只返该用户名下项目的内容)
        q: 搜索关键字(至少 1 字符,router 层已校验)
        project_id: 可选;给了限该项目内(范围 tab "当前项目")
        limit: 每类最多返回条数

    Returns:
        {
          "query": str,
          "projects": [{id, name, mode, tags}, ...],
          "characters": [{id, name, project_id, project_name, identity_excerpt}, ...],
          "events": [{id, project_id, project_name, description, time_anchor}, ...],
          "scenes": [{id, project_id, project_name, name, description}, ...],
          "simulations": [{id, project_id, project_name, divergence, state, created_at}, ...],
        }
    """
    pattern = f"%{q}%"
    limit = max(1, min(limit, 50))  # 兜底范围

    # 共享 SQL 片段:project_id 可选限制
    proj_filter = "AND p.id = ?" if project_id else ""
    extra_args: list = [project_id] if project_id else []

    # ============================================================
    # 1. 项目(只搜 name;tags JSON 数组用 LIKE 易脏不搜)
    # ============================================================
    # 项目自身不需要 JOIN(它就是顶层),直接用 user_id 鉴权
    if project_id:
        # 范围限当前项目时,projects 类别只可能返回这一条(若名字命中)
        proj_rows = fetch_all(
            conn,
            "SELECT id, name, mode, tags FROM projects "
            "WHERE user_id=? AND id=? AND name LIKE ? "
            "LIMIT ?",
            (user_id, project_id, pattern, limit),
        )
    else:
        proj_rows = fetch_all(
            conn,
            "SELECT id, name, mode, tags FROM projects "
            "WHERE user_id=? AND name LIKE ? "
            "ORDER BY created_at DESC LIMIT ?",
            (user_id, pattern, limit),
        )

    projects = [
        {
            "id": r["id"],
            "name": r["name"],
            "mode": r["mode"],
            "tags": _parse_tags(r["tags"]),
        }
        for r in proj_rows
    ]

    # ============================================================
    # 2. 角色(精度版:只搜 name — 不搜 identity/aliases 避免描述含词被命中)
    # ============================================================
    char_rows = fetch_all(
        conn,
        f"""SELECT c.id, c.name, c.identity, c.project_id, p.name AS project_name
            FROM characters c
            JOIN projects p ON c.project_id = p.id
            WHERE p.user_id = ?
              {proj_filter}
              AND c.name LIKE ?
            ORDER BY c.created_at DESC
            LIMIT ?""",
        (user_id, *extra_args, pattern, limit),
    )
    characters = [
        {
            "id": r["id"],
            "name": r["name"],
            "project_id": r["project_id"],
            "project_name": r["project_name"],
            "identity_excerpt": (r["identity"] or "")[:80],
        }
        for r in char_rows
    ]

    # ============================================================
    # 3. 事件(精度版:只搜 description — 事件没独立 name 字段,description 即事件名;
    #         不搜 time_anchor 避免"1969 年"含"渡边"撞)
    # ============================================================
    event_rows = fetch_all(
        conn,
        f"""SELECT e.id, e.description, e.time_anchor, e.project_id, p.name AS project_name
            FROM events e
            JOIN projects p ON e.project_id = p.id
            WHERE p.user_id = ?
              {proj_filter}
              AND e.description LIKE ?
            ORDER BY e.created_at DESC
            LIMIT ?""",
        (user_id, *extra_args, pattern, limit),
    )
    events = [
        {
            "id": r["id"],
            "description": (r["description"] or "")[:120],
            "time_anchor": r["time_anchor"],
            "project_id": r["project_id"],
            "project_name": r["project_name"],
        }
        for r in event_rows
    ]

    # ============================================================
    # 4. 场景(project_scenes,精度版:只搜 name — 不搜 description/aliases)
    # ============================================================
    scene_rows = fetch_all(
        conn,
        f"""SELECT s.id, s.name, s.description, s.aliases_json,
                   s.project_id, p.name AS project_name
            FROM project_scenes s
            JOIN projects p ON s.project_id = p.id
            WHERE p.user_id = ?
              {proj_filter}
              AND s.name LIKE ?
            ORDER BY s.created_at DESC
            LIMIT ?""",
        (user_id, *extra_args, pattern, limit),
    )
    scenes = [
        {
            "id": r["id"],
            "name": r["name"],
            "description": (r["description"] or "")[:80],
            "project_id": r["project_id"],
            "project_name": r["project_name"],
        }
        for r in scene_rows
    ]

    # ============================================================
    # 6. 推演产物(搜 divergence — 短描述;不搜 narrative 全文,太大)
    # ============================================================
    sim_rows = fetch_all(
        conn,
        f"""SELECT s.id, s.divergence, s.state, s.created_at,
                   s.project_id, p.name AS project_name
            FROM simulations s
            JOIN projects p ON s.project_id = p.id
            WHERE p.user_id = ?
              {proj_filter}
              AND s.divergence LIKE ?
            ORDER BY s.created_at DESC
            LIMIT ?""",
        (user_id, *extra_args, pattern, limit),
    )
    simulations = [
        {
            "id": r["id"],
            "divergence": (r["divergence"] or "")[:120],
            "state": r["state"],
            "created_at": r["created_at"],
            "project_id": r["project_id"],
            "project_name": r["project_name"],
        }
        for r in sim_rows
    ]

    return {
        "query": q,
        "projects": projects,
        "characters": characters,
        "events": events,
        "scenes": scenes,
        "simulations": simulations,
    }


def _parse_tags(raw: Optional[str]) -> list[str]:
    """projects.tags 是 JSON 字符串数组,容忍脏数据 fallback []。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(t) for t in parsed]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


__all__ = ["search", "DEFAULT_LIMIT_PER_TYPE"]
