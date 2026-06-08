"""SP-4(2026-05-28)— 角色状态时间线快照服务.

提供 CRUD + 查询入口,supports:
  - write_snapshot:每幕 narrator 完成后写在场角色当前状态
  - get_latest_snapshot:查某角色截至某幕末的最新状态
  - get_snapshot_at:查某角色在第 N 幕末的精确快照
  - list_snapshots_for_scene:查第 N 幕末所有角色快照
  - list_snapshots_for_character:查某角色全时间线
  - list_changes:diff 两幕之间的状态变化(给前端可视化用)

字段 JSON 化设计 — 避免列爆炸,emotion_vec / inventory / known_facts 都存 JSON.

写入策略:upsert(同 sim+scene+char 已存在 → UPDATE,否则 INSERT)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.db import execute, fetch_all, fetch_one


logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _json_or_none(value: Any) -> Optional[str]:
    """dict/list → JSON 字符串;None → None."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _parse_json(raw: Any) -> Any:
    if not raw or not isinstance(raw, str):
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def write_snapshot(
    conn: sqlite3.Connection,
    *,
    simulation_id: str,
    scene_index: int,
    character_id: str,
    character_name: str,
    position: Optional[str] = None,
    emotion_vec: Optional[dict[str, float]] = None,
    hp_status: Optional[str] = None,
    status_note: Optional[str] = None,
    inventory: Optional[list[str]] = None,
    known_fact_ids: Optional[list[str]] = None,
) -> str:
    """写入或更新一行快照(upsert).

    返回 snapshot id.
    本函数 commit — 调用方无需手动 commit.
    """
    # 查重:同 sim+scene+char 已存在 → UPDATE
    existing = fetch_one(
        conn,
        "SELECT id FROM character_state_snapshots "
        "WHERE simulation_id=? AND scene_index=? AND character_id=?",
        (simulation_id, scene_index, character_id),
    )

    emotion_json = _json_or_none(emotion_vec)
    inventory_json = _json_or_none(inventory)
    known_facts_json = _json_or_none(known_fact_ids)

    if existing:
        sid = existing["id"]
        execute(
            conn,
            "UPDATE character_state_snapshots SET "
            "character_name=?, position=?, emotion_vec_json=?, "
            "hp_status=?, status_note=?, inventory_json=?, known_fact_ids_json=? "
            "WHERE id=?",
            (
                character_name, position, emotion_json,
                hp_status, status_note, inventory_json, known_facts_json,
                sid,
            ),
        )
    else:
        sid = str(uuid.uuid4())
        execute(
            conn,
            "INSERT INTO character_state_snapshots "
            "(id, simulation_id, scene_index, character_id, character_name, "
            " position, emotion_vec_json, hp_status, status_note, "
            " inventory_json, known_fact_ids_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                sid, simulation_id, scene_index, character_id, character_name,
                position, emotion_json, hp_status, status_note,
                inventory_json, known_facts_json, _now_iso(),
            ),
        )
    conn.commit()
    return sid


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    """把 DB row 解码成 Python dict(JSON 字段反序列化)."""
    return {
        "id":              row["id"],
        "simulation_id":   row["simulation_id"],
        "scene_index":     row["scene_index"],
        "character_id":    row["character_id"],
        "character_name":  row["character_name"],
        "position":        row["position"],
        "emotion_vec":     _parse_json(row["emotion_vec_json"]),
        "hp_status":       row["hp_status"],
        "status_note":     row["status_note"],
        "inventory":       _parse_json(row["inventory_json"]),
        "known_fact_ids":  _parse_json(row["known_fact_ids_json"]),
        "created_at":      row["created_at"],
    }


def get_snapshot_at(
    conn: sqlite3.Connection,
    simulation_id: str,
    character_id: str,
    scene_index: int,
) -> Optional[dict[str, Any]]:
    """精确查某角色在第 N 幕末的快照(不存在则 None)."""
    row = fetch_one(
        conn,
        "SELECT * FROM character_state_snapshots "
        "WHERE simulation_id=? AND character_id=? AND scene_index=?",
        (simulation_id, character_id, scene_index),
    )
    return _row_to_dict(row) if row else None


def get_latest_snapshot(
    conn: sqlite3.Connection,
    simulation_id: str,
    character_id: str,
    *,
    before_scene: Optional[int] = None,
) -> Optional[dict[str, Any]]:
    """查某角色最新一条快照.

    Args:
      before_scene: 若给,只看 < before_scene 的 — 用于"上一幕末状态"查询
    """
    if before_scene is not None:
        row = fetch_one(
            conn,
            "SELECT * FROM character_state_snapshots "
            "WHERE simulation_id=? AND character_id=? AND scene_index<? "
            "ORDER BY scene_index DESC LIMIT 1",
            (simulation_id, character_id, before_scene),
        )
    else:
        row = fetch_one(
            conn,
            "SELECT * FROM character_state_snapshots "
            "WHERE simulation_id=? AND character_id=? "
            "ORDER BY scene_index DESC LIMIT 1",
            (simulation_id, character_id),
        )
    return _row_to_dict(row) if row else None


def list_snapshots_for_scene(
    conn: sqlite3.Connection,
    simulation_id: str,
    scene_index: int,
) -> list[dict[str, Any]]:
    """某 sim 第 N 幕末所有角色快照."""
    rows = fetch_all(
        conn,
        "SELECT * FROM character_state_snapshots "
        "WHERE simulation_id=? AND scene_index=? "
        "ORDER BY character_name",
        (simulation_id, scene_index),
    )
    return [_row_to_dict(r) for r in rows]


def list_snapshots_for_character(
    conn: sqlite3.Connection,
    simulation_id: str,
    character_id: str,
) -> list[dict[str, Any]]:
    """某角色全时间线(按 scene_index 升序)— 给前端时间轴用."""
    rows = fetch_all(
        conn,
        "SELECT * FROM character_state_snapshots "
        "WHERE simulation_id=? AND character_id=? "
        "ORDER BY scene_index ASC",
        (simulation_id, character_id),
    )
    return [_row_to_dict(r) for r in rows]


__all__ = [
    "write_snapshot",
    "get_snapshot_at",
    "get_latest_snapshot",
    "list_snapshots_for_scene",
    "list_snapshots_for_character",
]
