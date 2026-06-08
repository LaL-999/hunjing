"""Event 路由 — 菱形事件节点(初始态可选)。

- POST   /api/projects/{project_id}/events   嵌套创建
- GET    /api/projects/{project_id}/events   嵌套列出
- PATCH  /api/events/{event_id}              顶层更新
- DELETE /api/events/{event_id}              顶层删除

YAGNI:participants 字段是 character_id 数组,删除某 character 不会自动从此处剥离
(events 在初始态可选,数据冗余可接受;阶段 2 如需要再加 cascade)。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, status

from app.db import execute, fetch_all
from app.deps import get_current_user, get_db
from app.models.event import Event
from app.models.user import User
from app.schemas.event import (
    CreateEventRequest,
    EventResponse,
    UpdateEventRequest,
)
from app.services.project_service import (
    get_event_or_403,
    get_project_or_403,
    iso_now,
)

router = APIRouter()


@router.post(
    "/projects/{project_id}/events",
    response_model=EventResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_create_event(
    project_id: str,
    req: CreateEventRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    get_project_or_403(conn, project_id, user.id)
    event_id = str(uuid.uuid4())
    now = iso_now()
    # INIT.7(2026-05-21):INSERT 加 time_anchor 字段(可为 NULL)
    time_anchor_val = (req.time_anchor or "").strip()[:30] or None
    execute(
        conn,
        "INSERT INTO events (id, project_id, description, participants, "
        "                    created_at, time_anchor) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            event_id, project_id, req.description,
            json.dumps(req.participants, ensure_ascii=False),
            now, time_anchor_val,
        ),
    )
    conn.commit()
    return asdict(Event(
        id=event_id, project_id=project_id,
        description=req.description, participants=req.participants,
        created_at=now,
        time_anchor=time_anchor_val,
    ))


@router.get(
    "/projects/{project_id}/events",
    response_model=list[EventResponse],
)
def api_list_events(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    get_project_or_403(conn, project_id, user.id)
    rows = fetch_all(
        conn,
        "SELECT * FROM events WHERE project_id=? ORDER BY created_at ASC",
        (project_id,),
    )
    return [asdict(Event.from_row(r)) for r in rows]


@router.get("/events/{event_id}", response_model=EventResponse)
def api_get_event(
    event_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """单条事件详情(Sprint 1.M.1.D)— 3D 图谱 NodeEditDrawer 拉取用。"""
    return asdict(get_event_or_403(conn, event_id, user.id))


# Sprint 2.C 反事实变量:event PATCH 跟踪 description / participants
_EVENT_TRACKED_FIELDS = {"description", "participants"}


@router.patch("/events/{event_id}", response_model=EventResponse)
def api_update_event(
    event_id: str,
    req: UpdateEventRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    event = get_event_or_403(conn, event_id, user.id)
    updates = req.model_dump(exclude_unset=True)
    if not updates:
        return asdict(event)

    # === Sprint 2.C:先 record 反事实 ===
    from app.services.counterfactual_service import record_change
    event_dict = asdict(event)
    for field, new_val in updates.items():
        if field not in _EVENT_TRACKED_FIELDS:
            continue
        old_val = event_dict.get(field)
        try:
            record_change(
                conn, event.project_id, "event", event_id,
                field, old_val, new_val, user.id,
            )
        except ValueError:
            pass

    set_parts = []
    values: list = []
    for field, value in updates.items():
        if field == "participants":
            value = json.dumps(value, ensure_ascii=False)
        set_parts.append(f"{field}=?")
        values.append(value)
    values.append(event_id)

    execute(
        conn,
        f"UPDATE events SET {', '.join(set_parts)} WHERE id=?",
        tuple(values),
    )
    conn.commit()
    return asdict(get_event_or_403(conn, event_id, user.id))


@router.delete(
    "/events/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,  # FastAPI 0.110 + Py3.13 把 `-> None` 推成 NoneType,触发 204 无 body 断言
)
def api_delete_event(
    event_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    get_event_or_403(conn, event_id, user.id)
    execute(conn, "DELETE FROM events WHERE id=?", (event_id,))
    conn.commit()
