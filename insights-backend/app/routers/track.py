"""POST /track — 接收前端埋点(2026-05-27).

入口:
  - /track       单条事件
  - /track/batch 批量(前端 buffer flush / sendBeacon 场景)

设计:
  - 无鉴权(开放接收,因前端 frontend 还没登录时也要埋点 session_start)
  - rate-limit / 滥用防护留给后期(insights 子系统,流量小)
  - 写入 analytics.db 事务保证
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.db import execute, get_db_connection
from app.models.event import EVENT_TYPES
from app.schemas.track import (
    TrackBatchRequest,
    TrackRequest,
    TrackResponse,
)


logger = logging.getLogger(__name__)
router = APIRouter()


def _get_conn() -> sqlite3.Connection:
    return get_db_connection()


def _insert_event(
    conn: sqlite3.Connection, ev: TrackRequest, server_recv_ms: int,
) -> None:
    """写一条 event 到 events 表。"""
    if ev.event_type not in EVENT_TYPES:
        # Pydantic Literal 已经校验过,这里是双保险
        raise ValueError(f"unknown event_type: {ev.event_type}")
    execute(
        conn,
        """
        INSERT INTO events (
            event_type, user_id, session_id, timestamp_ms, server_recv_ms,
            project_id, simulation_id, mode, step, path, duration_ms, meta_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ev.event_type,
            ev.user_id,
            ev.session_id,
            ev.timestamp_ms,
            server_recv_ms,
            ev.project_id,
            ev.simulation_id,
            ev.mode,
            ev.step,
            ev.path,
            ev.duration_ms,
            json.dumps(ev.meta, ensure_ascii=False),
        ),
    )


@router.post("/track", response_model=TrackResponse)
async def post_track(
    payload: TrackRequest,
    request: Request,
) -> TrackResponse:
    """接收单条埋点。

    永不抛 5xx 给用户 — 埋点失败应静默(不能影响主平台体验)。
    内部异常用 logger.warning 记录。
    """
    server_recv_ms = int(time.time() * 1000)
    conn = _get_conn()
    try:
        _insert_event(conn, payload, server_recv_ms)
        return TrackResponse(ok=True, accepted=1)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"track insert failed: {e}")
        return TrackResponse(ok=False, accepted=0, rejected=1, reason=str(e)[:200])
    finally:
        conn.close()


@router.post("/track/batch", response_model=TrackResponse)
async def post_track_batch(
    payload: TrackBatchRequest,
    request: Request,
) -> TrackResponse:
    """接收批量埋点(sendBeacon 用)。"""
    server_recv_ms = int(time.time() * 1000)
    accepted = 0
    rejected = 0
    conn = _get_conn()
    try:
        for ev in payload.events:
            try:
                _insert_event(conn, ev, server_recv_ms)
                accepted += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"track batch item failed: {e}")
                rejected += 1
        return TrackResponse(
            ok=True if accepted > 0 else False,
            accepted=accepted,
            rejected=rejected,
        )
    finally:
        conn.close()


@router.get("/health")
async def health() -> dict:
    """健康检查 — 验证 analytics.db 可读 + huimeng.db attach 状态."""
    from app.db import huimeng_attached
    conn = _get_conn()
    try:
        events_count = conn.execute("SELECT COUNT(*) AS c FROM events").fetchone()["c"]
        attached = huimeng_attached(conn)
        # 安全:不再回显 huimeng.* 的表名清单(会把主库 schema 送给攻击者)。
        #   只返回 attach 成功与否的布尔位,足够运维诊断"主库连上没"。
        return {
            "ok": True,
            "events_count": events_count,
            "huimeng_attached": attached,
        }
    finally:
        conn.close()
