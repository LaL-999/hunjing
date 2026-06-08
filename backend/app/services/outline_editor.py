"""Sprint 6.A2 M6(2026-05-20)— Outline Editor(用户编辑 + 批准 outline)。

用户在前端 OutlineReviewView 编辑 outline_scene → 后端落库;
用户批准 outline → outline.state → 'approved' → kick_off 启动逐幕生成

API:
  - update_scene_fields(conn, outline_scene_id, **field_updates) → bool
    部分字段更新;校验 user_edited=1
  - update_global(conn, outline_id, **fields)
    更新 outline 主表 global_theme / global_arc
  - approve_outline(conn, sim_id) → outline_id
    校验 state='awaiting_user' → 切到 'approved' + 触发 kick_off

Sprint 6.A2 M8.B(2026-05-21)新增 3 个方法(加幕 / 删幕 / 重排):
  - insert_scene_at(conn, sim_id, position, scene_data) → outline_scene_id
  - delete_scene(conn, outline_scene_id) → bool
  - reorder_scenes(conn, sim_id, ordered_scene_ids) → bool
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from typing import Any

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)


class OutlineNotFound(Exception):
    pass


class OutlineStateMismatch(Exception):
    pass


def update_scene_fields(
    conn: sqlite3.Connection,
    outline_scene_id: str,
    *,
    sim_id_for_auth: str,
    user_id_for_auth: str,
    **fields: Any,
) -> bool:
    """更新某 outline_scene 的字段。

    Raises:
      OutlineNotFound: scene 不存在 / 不属于该用户的 sim
      OutlineStateMismatch: outline 已批准 / 已跑 → 不许改

    Returns:
      True 表示更新成功
    """
    # 1. 验证 scene 存在 + 通过 sim 反查 user_id
    row = fetch_one(
        conn,
        """SELECT os.outline_id, so.simulation_id, s.user_id, so.state AS outline_state
           FROM outline_scenes os
           JOIN simulation_outlines so ON so.id = os.outline_id
           JOIN simulations s ON s.id = so.simulation_id
           WHERE os.id=?""",
        (outline_scene_id,),
    )
    if row is None:
        raise OutlineNotFound(f"outline_scene {outline_scene_id} 不存在")
    if row["user_id"] != user_id_for_auth:
        raise OutlineNotFound(f"outline_scene {outline_scene_id} 不属于当前用户")
    if row["simulation_id"] != sim_id_for_auth:
        raise OutlineNotFound(
            f"outline_scene {outline_scene_id} 不属于 sim {sim_id_for_auth}"
        )

    # 2. outline state 必须 awaiting_user(没批准 / 已跑都不许改)
    if row["outline_state"] != "awaiting_user":
        raise OutlineStateMismatch(
            f"outline 当前 state={row['outline_state']},只有 awaiting_user 允许编辑"
        )

    # 3. 拼 UPDATE
    updates: list[tuple[str, Any]] = []
    if "scene_summary" in fields and fields["scene_summary"] is not None:
        updates.append(("scene_summary", str(fields["scene_summary"]).strip()[:500]))
    if "scene_purpose" in fields and fields["scene_purpose"] is not None:
        updates.append(("scene_purpose", str(fields["scene_purpose"]).strip()[:50]))
    if "location" in fields and fields["location"] is not None:
        updates.append(("location", str(fields["location"]).strip()[:50]))
    if "time_anchor" in fields and fields["time_anchor"] is not None:
        updates.append(("time_anchor", str(fields["time_anchor"]).strip()[:50]))
    if "transition_from_last" in fields and fields["transition_from_last"] is not None:
        updates.append((
            "transition_from_last",
            str(fields["transition_from_last"]).strip()[:300],
        ))
    if "characters_present" in fields and fields["characters_present"] is not None:
        chars = fields["characters_present"]
        if not isinstance(chars, list):
            chars = []
        updates.append((
            "characters_present_json",
            json.dumps([str(c) for c in chars if c][:8], ensure_ascii=False),
        ))
    if "key_events" in fields and fields["key_events"] is not None:
        events = fields["key_events"]
        if not isinstance(events, list):
            events = []
        updates.append((
            "key_events_json",
            json.dumps([str(e)[:300] for e in events if e][:8], ensure_ascii=False),
        ))
    if "key_props" in fields and fields["key_props"] is not None:
        props = fields["key_props"]
        if not isinstance(props, list):
            props = []
        cleaned: list[dict] = []
        for p in props[:6]:
            if isinstance(p, dict) and p.get("name"):
                cleaned.append({
                    "name": str(p["name"])[:50],
                    "action": str(p.get("action") or "referenced")[:30],
                    "properties": {
                        str(k): str(v)[:200]
                        for k, v in (p.get("properties") or {}).items()
                        if isinstance((p.get("properties") or {}), dict)
                    } if isinstance(p.get("properties"), dict) else {},
                })
        updates.append(("key_props_json", json.dumps(cleaned, ensure_ascii=False)))

    if not updates:
        return False

    now = iso_now()
    set_clause = ", ".join(f"{col}=?" for col, _ in updates)
    params = [v for _, v in updates] + [1, now, outline_scene_id]
    execute(
        conn,
        f"UPDATE outline_scenes SET {set_clause}, user_edited=?, updated_at=? "
        f"WHERE id=?",
        tuple(params),
    )
    conn.commit()
    return True


def update_global(
    conn: sqlite3.Connection,
    sim_id: str,
    user_id_for_auth: str,
    *,
    global_theme: str | None = None,
    global_arc: str | None = None,
) -> bool:
    """更新 outline 主表 global_theme / global_arc。"""
    row = fetch_one(
        conn,
        """SELECT so.id, so.state AS outline_state, s.user_id
           FROM simulation_outlines so
           JOIN simulations s ON s.id = so.simulation_id
           WHERE so.simulation_id=?""",
        (sim_id,),
    )
    if row is None:
        raise OutlineNotFound(f"sim {sim_id} 无 outline")
    if row["user_id"] != user_id_for_auth:
        raise OutlineNotFound(f"sim {sim_id} 不属于当前用户")
    if row["outline_state"] != "awaiting_user":
        raise OutlineStateMismatch(
            f"outline state={row['outline_state']},不许改"
        )

    updates: list[tuple[str, Any]] = []
    if global_theme is not None:
        updates.append(("global_theme", str(global_theme).strip()[:200]))
    if global_arc is not None:
        updates.append(("global_arc", str(global_arc).strip()[:1000]))
    if not updates:
        return False

    now = iso_now()
    set_clause = ", ".join(f"{c}=?" for c, _ in updates)
    params = [v for _, v in updates] + [now, row["id"]]
    execute(
        conn,
        f"UPDATE simulation_outlines SET {set_clause}, updated_at=? WHERE id=?",
        tuple(params),
    )
    conn.commit()
    return True


def approve_outline(
    conn: sqlite3.Connection,
    sim_id: str,
    user_id_for_auth: str,
) -> str:
    """批准 outline → state='approved' + 触发 kick_off 启动逐幕生成。

    Returns:
      outline_id

    Raises:
      OutlineNotFound: sim 无 outline / 不属于用户
      OutlineStateMismatch: outline 当前不是 awaiting_user
    """
    row = fetch_one(
        conn,
        """SELECT so.id, so.state, s.user_id
           FROM simulation_outlines so
           JOIN simulations s ON s.id = so.simulation_id
           WHERE so.simulation_id=?""",
        (sim_id,),
    )
    if row is None:
        raise OutlineNotFound(f"sim {sim_id} 无 outline")
    if row["user_id"] != user_id_for_auth:
        raise OutlineNotFound(f"sim {sim_id} 不属于当前用户")
    if row["state"] != "awaiting_user":
        raise OutlineStateMismatch(
            f"outline state={row['state']},只有 awaiting_user 允许批准"
        )

    outline_id = row["id"]
    now = iso_now()
    execute(
        conn,
        """UPDATE simulation_outlines
           SET state='approved', user_approved_at=?, updated_at=?
           WHERE id=?""",
        (now, now, outline_id),
    )
    conn.commit()

    # M8.A(2026-05-20)— outline 批准后,把 outline_scenes 里 LLM 自创的新场景
    # 反向入库 project_scenes,后续推演 / 同项目新 outline 都能看到累积的新场景。
    # 治本"outline 清一色教室"瑕疵 — 不再依赖 LLM 每次都自创,新场景持久化共享。
    # 失败不阻塞批准链路(只是少一次入库,可手动补)
    try:
        from app.models.simulation import Simulation
        sim_row = fetch_one(conn, "SELECT * FROM simulations WHERE id=?", (sim_id,))
        if sim_row is not None:
            sim = Simulation.from_row(sim_row)
            from app.services.sequel_scene_sync import sync_new_scenes_from_outline
            sync_new_scenes_from_outline(conn, sim)
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).warning(
            f"sequel_scene_sync from outline approve failed sim={sim_id}: {e}"
        )

    return outline_id


# ============================================================
# Sprint 6.A2 M8.B(2026-05-21)— 加幕 / 删幕 / 重排
# ============================================================

# 同 outline 最多允许的幕数(防 LLM / 用户加爆)
MAX_SCENES_PER_OUTLINE = 50


def _validate_outline_editable(
    conn: sqlite3.Connection,
    sim_id: str,
    user_id_for_auth: str,
) -> tuple[str, int]:
    """共用鉴权 + 状态校验。返回 (outline_id, current_scene_count)。

    Raises:
      OutlineNotFound / OutlineStateMismatch
    """
    row = fetch_one(
        conn,
        """SELECT so.id, so.state AS outline_state, s.user_id
           FROM simulation_outlines so
           JOIN simulations s ON s.id = so.simulation_id
           WHERE so.simulation_id=?""",
        (sim_id,),
    )
    if row is None:
        raise OutlineNotFound(f"sim {sim_id} 无 outline")
    if row["user_id"] != user_id_for_auth:
        raise OutlineNotFound(f"sim {sim_id} 不属于当前用户")
    if row["outline_state"] != "awaiting_user":
        raise OutlineStateMismatch(
            f"outline state={row['outline_state']},只有 awaiting_user 允许编辑"
        )
    outline_id = row["id"]
    cnt_row = fetch_one(
        conn,
        "SELECT COUNT(*) AS c FROM outline_scenes WHERE outline_id=?",
        (outline_id,),
    )
    return outline_id, int(cnt_row["c"] if cnt_row else 0)


def insert_scene_at(
    conn: sqlite3.Connection,
    sim_id: str,
    user_id_for_auth: str,
    position: int,
    scene_data: dict[str, Any] | None = None,
) -> str:
    """在 position(0-based)插入新幕,后续幕 scene_index 递增。

    position 范围:[0, current_scene_count] — N 表示尾部追加
    scene_data 缺省 → 创建一个空白幕(用户可后续编辑字段)

    Returns:
      new_outline_scene_id

    Raises:
      OutlineNotFound / OutlineStateMismatch
      ValueError: position 越界 / 超 MAX_SCENES_PER_OUTLINE
    """
    outline_id, current_count = _validate_outline_editable(
        conn, sim_id, user_id_for_auth,
    )
    if current_count >= MAX_SCENES_PER_OUTLINE:
        raise ValueError(
            f"outline 已有 {current_count} 幕,超过上限 {MAX_SCENES_PER_OUTLINE}"
        )
    if position < 0 or position > current_count:
        raise ValueError(
            f"position={position} 越界(允许 0..{current_count})"
        )

    data = scene_data or {}
    scene_summary = str(data.get("scene_summary") or "新增幕").strip()[:500]
    scene_purpose = str(data.get("scene_purpose") or "推进主线").strip()[:50]
    location = str(data.get("location") or "").strip()[:50]
    time_anchor = str(data.get("time_anchor") or "").strip()[:50]
    transition = str(data.get("transition_from_last") or "").strip()[:300]

    chars = data.get("characters_present") or []
    if not isinstance(chars, list):
        chars = []
    chars_json = json.dumps([str(c) for c in chars if c][:8], ensure_ascii=False)

    events = data.get("key_events") or []
    if not isinstance(events, list):
        events = []
    events_json = json.dumps([str(e)[:300] for e in events if e][:8], ensure_ascii=False)

    new_scene_id = uuid.uuid4().hex
    now = iso_now()
    OFFSET = 1000  # 避开 UNIQUE(outline_id, scene_index)冲突

    with transaction(conn) as tx:
        # 1. 后续幕 scene_index 先 +OFFSET 避开唯一键,再 +1 - OFFSET
        execute(
            tx,
            "UPDATE outline_scenes SET scene_index=scene_index+? "
            "WHERE outline_id=? AND scene_index >= ?",
            (OFFSET, outline_id, position),
        )
        execute(
            tx,
            "UPDATE outline_scenes SET scene_index=scene_index-?+1, updated_at=? "
            "WHERE outline_id=? AND scene_index >= ?",
            (OFFSET, now, outline_id, position + OFFSET),
        )
        # 2. INSERT 新幕
        execute(
            tx,
            "INSERT INTO outline_scenes "
            "(id, outline_id, scene_index, scene_summary, scene_purpose, "
            " location, time_anchor, characters_present_json, "
            " key_events_json, key_props_json, transition_from_last, "
            " user_edited, state, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, 1, 'pending', ?, ?)",
            (
                new_scene_id, outline_id, position, scene_summary, scene_purpose,
                location, time_anchor, chars_json, events_json, transition,
                now, now,
            ),
        )
        # 3. 更新 outline 主表的 total_scenes_planned(给前端进度显示用)
        execute(
            tx,
            "UPDATE simulation_outlines SET total_scenes_planned=?, updated_at=? "
            "WHERE id=?",
            (current_count + 1, now, outline_id),
        )

    return new_scene_id


def delete_scene(
    conn: sqlite3.Connection,
    outline_scene_id: str,
    sim_id_for_auth: str,
    user_id_for_auth: str,
) -> bool:
    """删某幕,后续幕 scene_index 递减回填。

    Raises:
      OutlineNotFound: scene 不存在 / 不属于该用户的 sim
      OutlineStateMismatch: outline 已批准
      ValueError: outline 只剩 1 幕(不许删空 outline)
    """
    # 1. 验证 scene + outline 可编辑
    row = fetch_one(
        conn,
        """SELECT os.outline_id, os.scene_index, so.simulation_id,
                  s.user_id, so.state AS outline_state
           FROM outline_scenes os
           JOIN simulation_outlines so ON so.id = os.outline_id
           JOIN simulations s ON s.id = so.simulation_id
           WHERE os.id=?""",
        (outline_scene_id,),
    )
    if row is None:
        raise OutlineNotFound(f"outline_scene {outline_scene_id} 不存在")
    if row["user_id"] != user_id_for_auth:
        raise OutlineNotFound("不属于当前用户")
    if row["simulation_id"] != sim_id_for_auth:
        raise OutlineNotFound("scene 不属于 sim")
    if row["outline_state"] != "awaiting_user":
        raise OutlineStateMismatch(
            f"outline state={row['outline_state']},不许删"
        )

    outline_id = row["outline_id"]
    deleted_index = int(row["scene_index"])

    # 2. 当前幕数(防删空)
    cnt_row = fetch_one(
        conn,
        "SELECT COUNT(*) AS c FROM outline_scenes WHERE outline_id=?",
        (outline_id,),
    )
    current_count = int(cnt_row["c"] if cnt_row else 0)
    if current_count <= 1:
        raise ValueError("outline 至少保留 1 幕,不能全删")

    now = iso_now()
    with transaction(conn) as tx:
        # 3. 删 row
        execute(tx, "DELETE FROM outline_scenes WHERE id=?", (outline_scene_id,))
        # 4. 后续 scene_index 整体 -1(被删的位置空出,后续填补)
        #    删除后 deleted_index 位置已空,直接 -1 不会撞唯一键(被删的 row 已无)
        execute(
            tx,
            "UPDATE outline_scenes SET scene_index=scene_index-1, updated_at=? "
            "WHERE outline_id=? AND scene_index > ?",
            (now, outline_id, deleted_index),
        )
        # 5. 更新 outline 主表 total_scenes_planned
        execute(
            tx,
            "UPDATE simulation_outlines SET total_scenes_planned=?, updated_at=? "
            "WHERE id=?",
            (current_count - 1, now, outline_id),
        )
    return True


def reorder_scenes(
    conn: sqlite3.Connection,
    sim_id: str,
    user_id_for_auth: str,
    ordered_scene_ids: list[str],
) -> bool:
    """重排 outline 内所有幕的 scene_index,按 ordered_scene_ids 顺序。

    Args:
      ordered_scene_ids: 新顺序的 outline_scene_id 列表,必须覆盖**所有**当前幕(完备 + 不重复)

    Raises:
      OutlineNotFound / OutlineStateMismatch
      ValueError: 列表长度 / 内容不匹配现有 outline_scenes
    """
    outline_id, current_count = _validate_outline_editable(
        conn, sim_id, user_id_for_auth,
    )
    if len(ordered_scene_ids) != current_count:
        raise ValueError(
            f"ordered_scene_ids 长度 {len(ordered_scene_ids)} 与现有幕数 {current_count} 不匹配"
        )
    if len(set(ordered_scene_ids)) != len(ordered_scene_ids):
        raise ValueError("ordered_scene_ids 含重复 id")

    # 拉所有 outline_scenes,验证 id 完全覆盖
    rows = fetch_all(
        conn,
        "SELECT id FROM outline_scenes WHERE outline_id=?",
        (outline_id,),
    )
    existing_ids = {r["id"] for r in rows}
    given_ids = set(ordered_scene_ids)
    if existing_ids != given_ids:
        missing = existing_ids - given_ids
        extra = given_ids - existing_ids
        raise ValueError(
            f"ordered_scene_ids 与现有不匹配 missing={missing} extra={extra}"
        )

    now = iso_now()
    # SQLite UNIQUE(outline_id, scene_index) → 直接 UPDATE 会撞唯一键
    # 解决:先把所有 scene_index 移到大数(+1000)避开,再赋最终值
    OFFSET = 1000
    with transaction(conn) as tx:
        # 1. 全部 +OFFSET 避开唯一键
        execute(
            tx,
            "UPDATE outline_scenes SET scene_index=scene_index+? WHERE outline_id=?",
            (OFFSET, outline_id),
        )
        # 2. 按目标顺序逐个赋值
        for new_idx, scene_id in enumerate(ordered_scene_ids):
            execute(
                tx,
                "UPDATE outline_scenes SET scene_index=?, updated_at=? WHERE id=?",
                (new_idx, now, scene_id),
            )

    return True
