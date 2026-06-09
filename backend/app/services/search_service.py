"""Sprint 6.A2 路线图 #6(2026-05-23)— 全局搜索 service。
2026-06-09 扩展:加 sp_novels / sp_screenplays / comic_projects(覆盖新增创作态)。

跨用户所有项目搜 8 类实体(模糊匹配):
  原 5 类(精度版 2026-05-23):
  - projects(name)
  - characters(只 name)
  - events(只 description)
  - scenes / project_scenes(只 name)
  - simulations(divergence)

  新增 3 类(2026-06-09):
  - sp_novels(title)— 剧创态小说
  - sp_screenplays(JOIN sp_novels.title)— 剧本(无独立标题,继承小说)
  - comic_projects(name)— 漫创态作品

  ⚠️ 关系(relationships)**不搜** — 跳不到具体编辑位置,待定位 feature 完善后加回来。

  ⚠️ 反事实组合(counterfactual_combination_runs)**不搜** — 内部技术构造,无用户可
  搜索的语义标签;用户搜推演分支已经通过 simulations.divergence 覆盖。

设计:
  - SQLite LIKE '%q%' — 数据量毫秒级,无需 FTS
  - 每条返回 project_id + project_name(剧创态特殊:novel_id + novel_title)
  - 跨用户隔离:projects/characters/events/scenes/simulations JOIN projects ON user_id = ?
  - 剧创态隔离:sp_novels.user_id / comic_projects.user_id 直接过滤
  - 可选 project_id:给了限当前项目(剧创态不受影响,因为它跟 projects 是平行域)
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
          # 2026-06-09 新增:
          "novels": [{id, title, total_chapters, total_chars, source_format}, ...],
          "screenplays": [{id, novel_id, novel_title, scene_count, ready_state}, ...],
          "comics": [{id, name, state, progress_percent, style_tag}, ...],
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

    # ============================================================
    # 7. 剧创态小说(2026-06-09 新增)
    # 跟 projects 是平行域 — 不受 project_id 过滤影响(用户在某项目搜框里
    # 输入小说名也能搜到,因为剧创态跟传统项目没关联)
    #
    # 表不存在时降级(老库未运行 migration 085 的兜底):try/except 兜
    # ============================================================
    novels: list[dict[str, Any]] = []
    try:
        novel_rows = fetch_all(
            conn,
            """SELECT id, title, total_chapters, total_chars, source_format
               FROM sp_novels
               WHERE user_id = ? AND title LIKE ?
               ORDER BY uploaded_at DESC
               LIMIT ?""",
            (user_id, pattern, limit),
        )
        novels = [
            {
                "id": r["id"],
                "title": r["title"],
                "total_chapters": r["total_chapters"],
                "total_chars": r["total_chars"],
                "source_format": r["source_format"],
            }
            for r in novel_rows
        ]
    except sqlite3.OperationalError as exc:
        # 表不存在 → 该用户的 DB 未跑 sp_ migrations,降级返空
        logger.debug("sp_novels search skipped: %s", exc)

    # ============================================================
    # 8. 剧创态剧本(2026-06-09 新增)
    # 剧本没独立标题 — 通过 JOIN sp_novels 拿继承的 title 搜
    # 这样用户搜书名能定位到剧本(剧本在 editor 视图打开)
    # ============================================================
    screenplays: list[dict[str, Any]] = []
    try:
        # sp_screenplays 表只有 created_at,没 composed_at;scene_count 也没,要 JSON 解析 stats_json
        screenplay_rows = fetch_all(
            conn,
            """SELECT s.id, s.novel_id, s.stats_json, s.created_at, n.title AS novel_title
               FROM sp_screenplays s
               JOIN sp_novels n ON s.novel_id = n.id
               WHERE n.user_id = ? AND n.title LIKE ?
               ORDER BY s.created_at DESC
               LIMIT ?""",
            (user_id, pattern, limit),
        )
        screenplays = [
            {
                "id": r["id"],
                "novel_id": r["novel_id"],
                "novel_title": r["novel_title"],
                "scene_count": _extract_scene_count(r["stats_json"]),
                "created_at": r["created_at"],
            }
            for r in screenplay_rows
        ]
    except sqlite3.OperationalError as exc:
        logger.debug("sp_screenplays search skipped: %s", exc)

    # ============================================================
    # 9. 漫创态作品(2026-06-09 新增)
    # 表不存在时降级(老库未运行 migration 024 的兜底)
    # ============================================================
    comics: list[dict[str, Any]] = []
    try:
        comic_rows = fetch_all(
            conn,
            """SELECT id, name, state, progress_percent, style_tag
               FROM comic_projects
               WHERE user_id = ? AND name LIKE ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (user_id, pattern, limit),
        )
        comics = [
            {
                "id": r["id"],
                "name": r["name"],
                "state": r["state"],
                "progress_percent": r["progress_percent"],
                "style_tag": r["style_tag"],
            }
            for r in comic_rows
        ]
    except sqlite3.OperationalError as exc:
        logger.debug("comic_projects search skipped: %s", exc)

    return {
        "query": q,
        "projects": projects,
        "characters": characters,
        "events": events,
        "scenes": scenes,
        "simulations": simulations,
        # 2026-06-09 新增:
        "novels": novels,
        "screenplays": screenplays,
        "comics": comics,
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


def _extract_scene_count(stats_json: Optional[str]) -> int:
    """sp_screenplays.stats_json 是 compose 阶段生成的统计,有 scene_count 字段。
    脏数据 fallback 0。
    """
    if not stats_json:
        return 0
    try:
        parsed = json.loads(stats_json)
        if isinstance(parsed, dict):
            return int(parsed.get("scene_count", 0))
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return 0


__all__ = ["search", "DEFAULT_LIMIT_PER_TYPE"]
