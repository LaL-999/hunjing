"""SP-3(2026-05-28)— 知识边界服务.

为"谁知道什么"建模,治 AI 写作最大连贯 bug:角色用了他不知道的信息.

API:
  register_fact     录入一条全局事实
  mark_known        标记某角色从某幕起知道某事实
  unmark_known      撤销
  get_facts_known_by   某角色已知的全部事实(按时间排序)
  get_facts_unknown_by 某角色不知道的全部事实(项目级 - 已知)
  list_project_facts   项目下全部事实
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.db import execute, fetch_all, fetch_one


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def register_fact(
    conn: sqlite3.Connection,
    *,
    project_id: str,
    description: str,
    first_revealed_scene: Optional[int] = None,
    is_sensitive: bool = False,
) -> str:
    """录入一条事实.返回 fact_id."""
    fid = str(uuid.uuid4())
    execute(
        conn,
        "INSERT INTO story_facts "
        "(id, project_id, description, first_revealed_scene, is_sensitive, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (fid, project_id, description, first_revealed_scene,
         1 if is_sensitive else 0, _now_iso()),
    )
    conn.commit()
    return fid


def mark_known(
    conn: sqlite3.Connection,
    *,
    character_id: str,
    fact_id: str,
    known_since_scene: Optional[int] = None,
    confidence: str = "confirmed",
) -> str:
    """标记某角色从某幕起知道某事实.

    重复标记同一 (char, fact) → UPDATE(更新 since_scene / confidence).
    返回 knowledge_id.
    """
    if confidence not in ("suspected", "confirmed", "wrong"):
        raise ValueError(f"非法 confidence: {confidence}")

    existing = fetch_one(
        conn,
        "SELECT id FROM character_knowledge "
        "WHERE character_id=? AND fact_id=?",
        (character_id, fact_id),
    )
    if existing:
        kid = existing["id"]
        execute(
            conn,
            "UPDATE character_knowledge "
            "SET known_since_scene=?, confidence=? WHERE id=?",
            (known_since_scene, confidence, kid),
        )
    else:
        kid = str(uuid.uuid4())
        execute(
            conn,
            "INSERT INTO character_knowledge "
            "(id, character_id, fact_id, known_since_scene, confidence, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (kid, character_id, fact_id, known_since_scene, confidence, _now_iso()),
        )
    conn.commit()
    return kid


def unmark_known(
    conn: sqlite3.Connection,
    *,
    character_id: str,
    fact_id: str,
) -> bool:
    """撤销标记.返回是否真的删了一行."""
    cur = conn.execute(
        "DELETE FROM character_knowledge "
        "WHERE character_id=? AND fact_id=?",
        (character_id, fact_id),
    )
    conn.commit()
    return cur.rowcount > 0


def list_project_facts(
    conn: sqlite3.Connection,
    project_id: str,
) -> list[dict[str, Any]]:
    """项目下所有事实."""
    rows = fetch_all(
        conn,
        "SELECT * FROM story_facts WHERE project_id=? "
        "ORDER BY first_revealed_scene IS NULL, first_revealed_scene ASC, created_at ASC",
        (project_id,),
    )
    return [dict(r) for r in rows]


def get_facts_known_by(
    conn: sqlite3.Connection,
    character_id: str,
    *,
    up_to_scene: Optional[int] = None,
) -> list[dict[str, Any]]:
    """某角色已知事实清单.

    Args:
      up_to_scene: 若给,仅返 known_since_scene IS NULL 或 < up_to_scene 的
                   ── 用于查"截至第 N 幕末他知道哪些"
    """
    if up_to_scene is not None:
        rows = fetch_all(
            conn,
            "SELECT f.id, f.description, f.is_sensitive, "
            "       k.known_since_scene, k.confidence "
            "FROM character_knowledge k "
            "JOIN story_facts f ON f.id = k.fact_id "
            "WHERE k.character_id=? "
            "  AND (k.known_since_scene IS NULL OR k.known_since_scene < ?) "
            "ORDER BY k.known_since_scene IS NULL DESC, k.known_since_scene ASC",
            (character_id, up_to_scene),
        )
    else:
        rows = fetch_all(
            conn,
            "SELECT f.id, f.description, f.is_sensitive, "
            "       k.known_since_scene, k.confidence "
            "FROM character_knowledge k "
            "JOIN story_facts f ON f.id = k.fact_id "
            "WHERE k.character_id=? "
            "ORDER BY k.known_since_scene IS NULL DESC, k.known_since_scene ASC",
            (character_id,),
        )
    return [dict(r) for r in rows]


def get_facts_unknown_by(
    conn: sqlite3.Connection,
    *,
    character_id: str,
    project_id: str,
    up_to_scene: Optional[int] = None,
) -> list[dict[str, Any]]:
    """某角色不知道的事实(项目级全部 - 该角色已知).

    Args:
      up_to_scene: 若给,只算"截至此幕末"还不知道的(known_since_scene >= up_to_scene 也算不知道)
    """
    if up_to_scene is not None:
        rows = fetch_all(
            conn,
            "SELECT f.* FROM story_facts f "
            "WHERE f.project_id=? "
            "  AND f.id NOT IN ("
            "    SELECT fact_id FROM character_knowledge "
            "    WHERE character_id=? "
            "      AND (known_since_scene IS NULL OR known_since_scene < ?)"
            "  ) "
            "ORDER BY f.first_revealed_scene IS NULL, f.first_revealed_scene ASC",
            (project_id, character_id, up_to_scene),
        )
    else:
        rows = fetch_all(
            conn,
            "SELECT f.* FROM story_facts f "
            "WHERE f.project_id=? "
            "  AND f.id NOT IN ("
            "    SELECT fact_id FROM character_knowledge WHERE character_id=?"
            "  ) "
            "ORDER BY f.first_revealed_scene IS NULL, f.first_revealed_scene ASC",
            (project_id, character_id),
        )
    return [dict(r) for r in rows]


__all__ = [
    "register_fact",
    "mark_known",
    "unmark_known",
    "list_project_facts",
    "get_facts_known_by",
    "get_facts_unknown_by",
]
