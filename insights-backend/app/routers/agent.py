"""POST /agent/* — 二级 agent 触发端点(INS-A5/A6,2026-05-27).

端点:
  POST /agent/run-daily    手动触发某日 Level 1 agent(或自动 pending 日期)
  POST /agent/run-summary  手动触发 Level 2 汇总 agent
  GET  /agent/status       看当前触发条件 / 已有报告统计
  GET  /agent/daily        最近 N 份 daily_reports
  GET  /agent/summaries    所有 summary_reports
  GET  /agent/summaries/{id}  单份阶段汇总详情

鉴权:同 /admin/* — 要求 X-Admin-Token header。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.db import fetch_all, fetch_one, get_db_connection
from app.routers.admin import require_admin
from app.services import level1_agent, level2_agent


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/agent")


def _get_conn() -> sqlite3.Connection:
    return get_db_connection()


# ============================================================
# Schemas
# ============================================================

class RunDailyRequest(BaseModel):
    target_date: Optional[str] = Field(
        None,
        description="YYYY-MM-DD;不传则自动跑所有 pending 日期",
    )
    force: bool = False


class RunDailyResult(BaseModel):
    ok: bool
    skipped: bool
    skip_reason: Optional[str] = None
    report_id: Optional[int] = None
    event_count: int
    cost_yuan: float
    error: Optional[str] = None
    target_date: str


class RunSummaryRequest(BaseModel):
    force: bool = False


# ============================================================
# /agent/run-daily
# ============================================================

@router.post("/run-daily")
async def post_run_daily(
    payload: RunDailyRequest,
    _: None = Depends(require_admin),
) -> dict:
    """触发 Level 1 数据 agent.

    若 target_date 未传 → 自动找 pending 日期(过去 30 天有 events 但无 daily_report 的日)
    并逐日跑.
    """
    conn = _get_conn()
    try:
        if payload.target_date:
            # 单日模式
            result = level1_agent.run_daily(
                conn, payload.target_date, force=payload.force,
            )
            return {
                "mode": "single",
                "target_date": payload.target_date,
                "result": result,
            }

        # 批量模式 — 跑所有 pending
        pending = level1_agent.get_pending_daily_dates(conn)
        results: list[dict] = []
        for d in pending:
            r = level1_agent.run_daily(conn, d, force=payload.force)
            results.append({**r, "target_date": d})
        return {
            "mode": "batch",
            "pending_count": len(pending),
            "results": results,
        }
    finally:
        conn.close()


# ============================================================
# /agent/run-summary
# ============================================================

@router.post("/run-summary")
async def post_run_summary(
    payload: RunSummaryRequest,
    _: None = Depends(require_admin),
) -> dict:
    """触发 Level 2 汇总 agent."""
    conn = _get_conn()
    try:
        result = level2_agent.run_summary(conn, force=payload.force)
        return result
    finally:
        conn.close()


# ============================================================
# /agent/status
# ============================================================

@router.get("/status")
async def get_status(_: None = Depends(require_admin)) -> dict:
    """看当前触发条件 + 报告统计."""
    conn = _get_conn()
    try:
        # Level 1 待跑日期
        pending_dailies = level1_agent.get_pending_daily_dates(conn)

        # Level 2 触发判断
        should_run_summary, reason = level2_agent.check_should_run(conn)

        # 报告统计
        daily_total = fetch_one(
            conn, "SELECT COUNT(*) AS c FROM daily_reports"
        )["c"]
        daily_skipped = fetch_one(
            conn, "SELECT COUNT(*) AS c FROM daily_reports WHERE skipped = 1"
        )["c"]
        summary_total = fetch_one(
            conn, "SELECT COUNT(*) AS c FROM summary_reports"
        )["c"]

        # 累计成本
        cost_row = fetch_one(
            conn,
            "SELECT COALESCE(SUM(cost_yuan), 0) AS c FROM daily_reports",
        )
        daily_cost = float(cost_row["c"] or 0)
        cost_row = fetch_one(
            conn,
            "SELECT COALESCE(SUM(cost_yuan), 0) AS c FROM summary_reports",
        )
        summary_cost = float(cost_row["c"] or 0)

        return {
            "level1": {
                "pending_dates": pending_dailies,
                "pending_count": len(pending_dailies),
                "total_reports": daily_total,
                "skipped_reports": daily_skipped,
                "valid_reports": daily_total - daily_skipped,
                "min_events_threshold": level1_agent.MIN_EVENTS_FOR_AGENT,
                "total_cost_yuan": round(daily_cost, 4),
            },
            "level2": {
                "should_run": should_run_summary,
                "reason": reason,
                "total_summaries": summary_total,
                "min_reports_threshold": level2_agent.MIN_DAILY_REPORTS_FOR_SUMMARY,
                "min_days_interval": level2_agent.MIN_DAYS_INTERVAL,
                "total_cost_yuan": round(summary_cost, 4),
            },
        }
    finally:
        conn.close()


# ============================================================
# /agent/daily — 最近 N 份日报
# ============================================================

@router.get("/daily")
async def get_recent_dailies(
    limit: int = 30,
    _: None = Depends(require_admin),
) -> dict:
    if limit < 1 or limit > 365:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_LIMIT", "message": "limit 必须在 1-365"},
        )
    conn = _get_conn()
    try:
        rows = fetch_all(
            conn,
            "SELECT * FROM daily_reports ORDER BY report_date DESC LIMIT ?",
            (limit,),
        )
        return {
            "reports": [
                {
                    "id": r["id"],
                    "report_date": r["report_date"],
                    "event_count": int(r["event_count"]),
                    "skipped": bool(r["skipped"]),
                    "skip_reason": r["skip_reason"],
                    "summary_md": r["summary_md"],
                    "metrics": json.loads(r["metrics_json"] or "{}") if r["metrics_json"] else {},
                    "cost_yuan": float(r["cost_yuan"] or 0),
                    "created_at": r["created_at"],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


# ============================================================
# /agent/summaries — 所有阶段汇总
# ============================================================

@router.get("/summaries")
async def get_summaries(_: None = Depends(require_admin)) -> dict:
    conn = _get_conn()
    try:
        rows = fetch_all(
            conn,
            "SELECT id, stage_number, period_start, period_end, daily_count, "
            "       cost_yuan, created_at, read_at "
            "FROM summary_reports ORDER BY stage_number DESC",
        )
        return {
            "summaries": [
                {
                    "id": r["id"],
                    "stage_number": int(r["stage_number"]),
                    "period_start": r["period_start"],
                    "period_end": r["period_end"],
                    "daily_count": int(r["daily_count"]),
                    "cost_yuan": float(r["cost_yuan"] or 0),
                    "created_at": r["created_at"],
                    "read_at": r["read_at"],
                }
                for r in rows
            ],
        }
    finally:
        conn.close()


# ============================================================
# /agent/summaries/{id} — 单份阶段汇总详情
# ============================================================

@router.get("/summaries/{summary_id}")
async def get_summary_detail(
    summary_id: int,
    _: None = Depends(require_admin),
) -> dict:
    conn = _get_conn()
    try:
        r = fetch_one(
            conn,
            "SELECT * FROM summary_reports WHERE id = ?",
            (summary_id,),
        )
        if r is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "SUMMARY_NOT_FOUND", "message": "未找到该阶段汇总"},
            )
        return {
            "id": r["id"],
            "stage_number": int(r["stage_number"]),
            "period_start": r["period_start"],
            "period_end": r["period_end"],
            "daily_count": int(r["daily_count"]),
            "summary_md": r["summary_md"],
            "insights": json.loads(r["insights_json"] or "{}") if r["insights_json"] else {},
            "cost_yuan": float(r["cost_yuan"] or 0),
            "tokens_input": int(r["tokens_input"] or 0),
            "tokens_output": int(r["tokens_output"] or 0),
            "created_at": r["created_at"],
            "read_at": r["read_at"],
        }
    finally:
        conn.close()


# ============================================================
# /agent/summaries/{id}/mark-read — 标已读
# ============================================================

@router.post("/summaries/{summary_id}/mark-read")
async def post_mark_read(
    summary_id: int,
    _: None = Depends(require_admin),
) -> dict:
    """标某份阶段汇总已读,后续不再提醒(insights-frontend 用)."""
    from app.db import execute
    conn = _get_conn()
    try:
        now = datetime.utcnow().isoformat()
        cursor = conn.execute(
            "UPDATE summary_reports SET read_at = ? WHERE id = ?",
            (now, summary_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "SUMMARY_NOT_FOUND", "message": "未找到该阶段汇总"},
            )
        conn.commit()
        return {"ok": True, "summary_id": summary_id, "read_at": now}
    finally:
        conn.close()
