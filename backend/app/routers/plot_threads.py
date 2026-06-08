"""SP-5(2026-05-28)— 伏笔账本 router.

暴露 plot_threads 给前端面板(plot_threads 后端齐全但此前 0 endpoint).
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.db import execute, fetch_all, fetch_one
from app.deps import get_current_user, get_db
from app.models.plot_thread import PlotThread
from app.models.user import User
from app.services.project_service import ResourceNotFoundOrForbidden


router = APIRouter()


def _thread_to_dict(t: PlotThread) -> dict:
    """asdict + 手动加 @property 派生字段(status / is_active / must_advance)."""
    d = asdict(t)
    d["status"] = t.status
    d["is_active"] = t.is_active
    d["must_advance"] = t.must_advance
    return d


def _check_sim_access(
    conn: sqlite3.Connection, sim_id: str, user_id: str,
) -> None:
    """简单鉴权:sim 必须属于该 user 的项目."""
    row = fetch_one(
        conn,
        "SELECT s.id FROM simulations s "
        "JOIN projects p ON p.id = s.project_id "
        "WHERE s.id=? AND p.user_id=?",
        (sim_id, user_id),
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SIMULATION_NOT_FOUND", "message": "推演不存在或无权限"},
        )


@router.get("/simulations/{simulation_id}/plot_threads")
def api_list_plot_threads(
    simulation_id: str,
    status_filter: Optional[Literal["active", "resolved", "abandoned", "all"]] = Query(
        default="all", alias="status",
    ),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """列出某 sim 下所有伏笔(可按状态筛选).

    返回 {threads: [...], counts: {active, resolved, abandoned}}
    """
    _check_sim_access(conn, simulation_id, user.id)

    rows = fetch_all(
        conn,
        "SELECT * FROM plot_threads WHERE simulation_id=? "
        "ORDER BY priority ASC, introduced_at_scene_index ASC",
        (simulation_id,),
    )
    all_threads = [PlotThread.from_row(r) for r in rows]

    # 统计 3 类
    counts = {"active": 0, "resolved": 0, "abandoned": 0}
    for t in all_threads:
        counts[t.status] += 1

    # 应用 status filter
    if status_filter and status_filter != "all":
        filtered = [t for t in all_threads if t.status == status_filter]
    else:
        filtered = all_threads

    return {
        "threads": [_thread_to_dict(t) for t in filtered],
        "counts": counts,
    }


class UpdatePlotThreadRequest(BaseModel):
    """SP-5:用户可编辑字段.

    2026-06-02 cleanup:expected_resolution_scene 字段删除 — 用户产品决策.
    现在只剩 is_abandoned(用户主动剪枝).
    """
    is_abandoned: Optional[bool] = None


@router.patch("/simulations/{simulation_id}/plot_threads/{thread_id}")
def api_update_plot_thread(
    simulation_id: str,
    thread_id: str,
    req: UpdatePlotThreadRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """部分更新某条伏笔的 is_abandoned."""
    _check_sim_access(conn, simulation_id, user.id)

    # 验 thread 存在且属于该 sim
    row = fetch_one(
        conn,
        "SELECT id FROM plot_threads WHERE id=? AND simulation_id=?",
        (thread_id, simulation_id),
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "PLOT_THREAD_NOT_FOUND", "message": "伏笔不存在"},
        )

    updates = req.model_dump(exclude_unset=True)
    if not updates:
        # nothing to do — 直接返当前
        fresh = fetch_one(
            conn, "SELECT * FROM plot_threads WHERE id=?", (thread_id,),
        )
        return _thread_to_dict(PlotThread.from_row(fresh))

    set_parts = []
    values: list = []
    for field, value in updates.items():
        if field == "is_abandoned":
            set_parts.append("is_abandoned=?")
            values.append(1 if value else 0)
        else:
            set_parts.append(f"{field}=?")
            values.append(value)

    # 更新 updated_at
    from app.services.project_service import iso_now
    set_parts.append("updated_at=?")
    values.append(iso_now())
    values.append(thread_id)

    execute(
        conn,
        f"UPDATE plot_threads SET {', '.join(set_parts)} WHERE id=?",
        tuple(values),
    )
    conn.commit()

    fresh = fetch_one(
        conn, "SELECT * FROM plot_threads WHERE id=?", (thread_id,),
    )
    return _thread_to_dict(PlotThread.from_row(fresh))
