"""Sprint 6.A2 M1(2026-05-18)— 关系时间轴服务。

提供 phase 增删改 + current_phase 切换 + 老数据自动迁移成单 phase。

业务核心:
  - 一条 relationship 可有 N 个 phase(按 phase_index 0,1,2 排序)
  - 任意时刻 relationships.current_phase_id 指向"当前生效" phase
  - 添加 phase 时自动 phase_index = max+1 并将 current_phase_id 指向新 phase
    (新阶段通常是"最新"的,这是大多数用户操作模式)
  - 用户可手动 set_current_phase(切到旧阶段用于反事实推演 / 主线之外的 what-if)

老数据兼容:
  - 老 relationship 无 phase → ensure_phases_initialized() 自动建一个 phase[0]
    (type/strength 沿用 relationships.type/strength;start_anchor=null;end_anchor=null)
  - 调用方:phase API 首次访问某 relationship 时调,自动迁移
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.models.relationship_phase import RelationshipPhase
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)


VALID_PHASE_STRENGTHS = (
    "strong", "moderately_strong", "moderate", "moderately_weak", "weak",
)

# Sprint 6.A2 M7.G(2026-05-20):type 白名单从枚举 7 种 → trim 后 1-20 字软校验,
# 对齐 relationships.type 自由化(用户可自定义 / LLM 可自创)
PHASE_TYPE_MIN_LEN = 1
PHASE_TYPE_MAX_LEN = 20


def _is_valid_phase_type(value: str) -> bool:
    """type 软白名单:trim 后 1-20 字非空字符串。"""
    if not isinstance(value, str):
        return False
    stripped = value.strip()
    return PHASE_TYPE_MIN_LEN <= len(stripped) <= PHASE_TYPE_MAX_LEN


def ensure_phases_initialized(
    conn: sqlite3.Connection, relationship_id: str,
) -> int:
    """老数据自动迁移:若 relationship 无任何 phase,造一个 phase[0] 与 type/strength 同步。

    返回:本次新建的 phase 数(0 或 1)。
    """
    existing_count = fetch_one(
        conn,
        "SELECT COUNT(*) AS cnt FROM relationship_phases WHERE relationship_id=?",
        (relationship_id,),
    )
    if existing_count and int(existing_count["cnt"]) > 0:
        return 0

    rel_row = fetch_one(
        conn,
        "SELECT id, type, strength FROM relationships WHERE id=?",
        (relationship_id,),
    )
    if rel_row is None:
        # relationship 不存在 → 不抛(防外层调用顺序错乱),让调用方判定
        return 0

    phase_id = uuid.uuid4().hex
    now = iso_now()
    rel_type = rel_row["type"]
    rel_strength = rel_row["strength"] if rel_row["strength"] in VALID_PHASE_STRENGTHS else "moderate"

    execute(
        conn,
        """INSERT INTO relationship_phases
            (id, relationship_id, phase_index, type, strength,
             start_anchor, end_anchor, trigger_event_id, notes,
             created_at, updated_at)
           VALUES (?, ?, 0, ?, ?, NULL, NULL, NULL, '', ?, ?)""",
        (phase_id, relationship_id, rel_type, rel_strength, now, now),
    )
    execute(
        conn,
        "UPDATE relationships SET current_phase_id=? WHERE id=?",
        (phase_id, relationship_id),
    )
    conn.commit()
    return 1


def add_phase(
    conn: sqlite3.Connection,
    relationship_id: str,
    *,
    type: str,
    strength: str = "moderate",
    start_anchor: Optional[str] = None,
    end_anchor: Optional[str] = None,
    trigger_event_id: Optional[str] = None,
    notes: str = "",
    auto_set_current: bool = True,
) -> RelationshipPhase:
    """加新 phase 到 relationship 末尾(phase_index = max+1)。

    Args:
      type: 必填(校验在 VALID_PHASE_TYPES)
      strength: 默认 moderate
      auto_set_current: True(默认)→ 自动把 relationships.current_phase_id 指向新 phase
                        (用户操作模式通常是"加新阶段 = 现在变成这状态")

    Raises:
      ValueError: type 不在白名单 / strength 非法 / relationship 不存在
    """
    if not _is_valid_phase_type(type):
        raise ValueError(
            f"type {type!r} 非法(必须 1-20 字 trim 后非空字符串)"
        )
    if strength not in VALID_PHASE_STRENGTHS:
        raise ValueError(
            f"strength {strength!r} 不在白名单 {VALID_PHASE_STRENGTHS}"
        )

    rel_row = fetch_one(
        conn,
        "SELECT id FROM relationships WHERE id=?",
        (relationship_id,),
    )
    if rel_row is None:
        raise ValueError(f"relationship {relationship_id} 不存在")

    # 确保老数据已迁移(若无 phase 则建 phase[0])
    ensure_phases_initialized(conn, relationship_id)

    # 计算新 phase_index = 现有最大 + 1
    max_row = fetch_one(
        conn,
        "SELECT COALESCE(MAX(phase_index), -1) AS max_idx "
        "FROM relationship_phases WHERE relationship_id=?",
        (relationship_id,),
    )
    new_index = int(max_row["max_idx"]) + 1 if max_row else 0

    # 给新 phase 之前的最后一个 phase 自动填 end_anchor(若 user 没显式填 start_anchor)
    # 简化:不自动填,让用户自己控制 anchor(YAGNI)

    phase_id = uuid.uuid4().hex
    now = iso_now()
    execute(
        conn,
        """INSERT INTO relationship_phases
            (id, relationship_id, phase_index, type, strength,
             start_anchor, end_anchor, trigger_event_id, notes,
             created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            phase_id, relationship_id, new_index, type, strength,
            start_anchor, end_anchor, trigger_event_id, notes,
            now, now,
        ),
    )

    if auto_set_current:
        execute(
            conn,
            "UPDATE relationships SET current_phase_id=? WHERE id=?",
            (phase_id, relationship_id),
        )

    conn.commit()
    return _get_phase_or_raise(conn, phase_id)


def list_phases(
    conn: sqlite3.Connection, relationship_id: str,
) -> list[RelationshipPhase]:
    """按 phase_index 升序列出某 relationship 的所有 phase。

    老数据(无 phase)→ 自动迁移成单 phase 后返回。
    """
    ensure_phases_initialized(conn, relationship_id)
    rows = fetch_all(
        conn,
        """SELECT * FROM relationship_phases
           WHERE relationship_id=? ORDER BY phase_index ASC""",
        (relationship_id,),
    )
    return [RelationshipPhase.from_row(r) for r in rows]


def delete_phase(
    conn: sqlite3.Connection, phase_id: str,
) -> None:
    """删 phase。若 relationships.current_phase_id 指向此 phase → 自动切到剩余最大 index 的。

    若删完所有 phase → relationships.current_phase_id 置 NULL(走 type fallback)。

    注意:relationships.current_phase_id 有 SQL `ON DELETE SET NULL` 外键约束 — 删除
    phase 时若是 current,DB 会自动 NULL it。所以我们必须**在 DELETE 之前**记录
    是否是 current,然后 DELETE 之后再手动 UPDATE 成剩余 phase(否则就永远 NULL)。
    """
    phase_row = fetch_one(
        conn,
        "SELECT relationship_id FROM relationship_phases WHERE id=?",
        (phase_id,),
    )
    if phase_row is None:
        raise ValueError(f"phase {phase_id} 不存在")
    rel_id = phase_row["relationship_id"]

    # 先记 — DELETE 触发外键 SET NULL 前的 current_phase_id
    current_row = fetch_one(
        conn,
        "SELECT current_phase_id FROM relationships WHERE id=?",
        (rel_id,),
    )
    was_current = bool(
        current_row and current_row["current_phase_id"] == phase_id
    )

    execute(conn, "DELETE FROM relationship_phases WHERE id=?", (phase_id,))

    # 删后修复 current_phase_id(若被删的就是 current)
    if was_current:
        remain = fetch_one(
            conn,
            """SELECT id FROM relationship_phases
               WHERE relationship_id=?
               ORDER BY phase_index DESC LIMIT 1""",
            (rel_id,),
        )
        new_current = remain["id"] if remain else None
        execute(
            conn,
            "UPDATE relationships SET current_phase_id=? WHERE id=?",
            (new_current, rel_id),
        )

    conn.commit()


def set_current_phase(
    conn: sqlite3.Connection, relationship_id: str, phase_id: Optional[str],
) -> None:
    """手动切换 current_phase_id(用于反事实 what-if / 主线之外的探索)。

    phase_id=None → 走 type fallback(老数据模式)。
    """
    if phase_id is not None:
        check = fetch_one(
            conn,
            "SELECT id FROM relationship_phases "
            "WHERE id=? AND relationship_id=?",
            (phase_id, relationship_id),
        )
        if check is None:
            raise ValueError(
                f"phase {phase_id} 不属于 relationship {relationship_id}"
            )
    execute(
        conn,
        "UPDATE relationships SET current_phase_id=? WHERE id=?",
        (phase_id, relationship_id),
    )
    conn.commit()


def update_phase(
    conn: sqlite3.Connection, phase_id: str,
    *,
    type: Optional[str] = None,
    strength: Optional[str] = None,
    start_anchor: Optional[str] = None,
    end_anchor: Optional[str] = None,
    trigger_event_id: Optional[str] = None,
    notes: Optional[str] = None,
) -> RelationshipPhase:
    """更新 phase 字段(只改非 None 的)。"""
    phase_row = fetch_one(
        conn,
        "SELECT id FROM relationship_phases WHERE id=?",
        (phase_id,),
    )
    if phase_row is None:
        raise ValueError(f"phase {phase_id} 不存在")

    if type is not None and not _is_valid_phase_type(type):
        raise ValueError(f"type {type!r} 非法(必须 1-20 字 trim 后非空字符串)")
    if strength is not None and strength not in VALID_PHASE_STRENGTHS:
        raise ValueError(f"strength {strength!r} 不在白名单")

    updates: dict = {}
    if type is not None: updates["type"] = type
    if strength is not None: updates["strength"] = strength
    if start_anchor is not None: updates["start_anchor"] = start_anchor or None
    if end_anchor is not None: updates["end_anchor"] = end_anchor or None
    if trigger_event_id is not None: updates["trigger_event_id"] = trigger_event_id or None
    if notes is not None: updates["notes"] = notes

    if not updates:
        return _get_phase_or_raise(conn, phase_id)

    set_parts = [f"{k}=?" for k in updates.keys()]
    set_parts.append("updated_at=?")
    values = list(updates.values()) + [iso_now(), phase_id]

    execute(
        conn,
        f"UPDATE relationship_phases SET {', '.join(set_parts)} WHERE id=?",
        tuple(values),
    )
    conn.commit()
    return _get_phase_or_raise(conn, phase_id)


def _get_phase_or_raise(
    conn: sqlite3.Connection, phase_id: str,
) -> RelationshipPhase:
    row = fetch_one(
        conn,
        "SELECT * FROM relationship_phases WHERE id=?",
        (phase_id,),
    )
    if row is None:
        raise ValueError(f"phase {phase_id} 不存在")
    return RelationshipPhase.from_row(row)


def get_phase_or_404(
    conn: sqlite3.Connection, phase_id: str, user_id: str,
) -> RelationshipPhase:
    """拉 phase + 校验属于该 user 的项目(防跨用户访问)。"""
    row = fetch_one(
        conn,
        """SELECT rp.*, r.project_id, p.user_id AS owner_id
           FROM relationship_phases rp
           JOIN relationships r ON r.id = rp.relationship_id
           JOIN projects p ON p.id = r.project_id
           WHERE rp.id=?""",
        (phase_id,),
    )
    if row is None or row["owner_id"] != user_id:
        from app.services.project_service import ResourceNotFoundOrForbidden
        raise ResourceNotFoundOrForbidden("relationship_phase", phase_id)
    return RelationshipPhase.from_row(row)
