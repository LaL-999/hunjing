"""Level 1 数据 agent(INS-A5.2,2026-05-27).

每日跑一次:读当日 events → 聚合统计 → 调 LLM → 落 daily_reports.

触发方案 C(用户拍板):
  - 每日 cron 调 run_daily(date)
  - 当日事件 < MIN_EVENTS(50)时跳过 + 记 "事件不足"

agent 不改主平台 — 只读 events + 关联只读 huimeng.users / projects / simulations,产报告。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

from app.db import execute, fetch_all, fetch_one, huimeng_attached
from app.services.llm_client import (
    LlmCallFailed,
    LlmJsonParseFailed,
    call_llm_json,
    estimate_cost_yuan,
)


logger = logging.getLogger(__name__)


# C 方案(用户拍板):当日事件 < 50 时跳过 Level 1 agent
MIN_EVENTS_FOR_AGENT = 50

# 单日产物截断阈值(避免 prompt 爆 context)
MAX_PATHS_IN_PROMPT = 10
MAX_TYPE_DIST_IN_PROMPT = 15

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _date_range_ms(target_date: str) -> tuple[int, int]:
    """把 YYYY-MM-DD 转 [start_ms, end_ms) 区间(UTC 00:00-24:00).

    简化:UTC 时区。生产可加用户时区配置。
    """
    d = datetime.strptime(target_date, "%Y-%m-%d")
    start_ms = int(d.timestamp() * 1000)
    end_ms = start_ms + 24 * 3600 * 1000
    return start_ms, end_ms


def _aggregate_daily_data(
    conn: sqlite3.Connection,
    target_date: str,
) -> dict[str, Any]:
    """聚合某日的事件数据,作为 LLM prompt 的输入."""
    start_ms, end_ms = _date_range_ms(target_date)

    # 事件总数
    event_count = fetch_one(
        conn,
        "SELECT COUNT(*) AS c FROM events WHERE timestamp_ms >= ? AND timestamp_ms < ?",
        (start_ms, end_ms),
    )["c"]

    if event_count == 0:
        return {"event_count": 0}

    # 活跃用户(非匿名)
    active_users = fetch_one(
        conn,
        "SELECT COUNT(DISTINCT user_id) AS c FROM events "
        "WHERE timestamp_ms >= ? AND timestamp_ms < ? AND user_id IS NOT NULL",
        (start_ms, end_ms),
    )["c"]

    # 匿名占比
    anon_count = fetch_one(
        conn,
        "SELECT COUNT(*) AS c FROM events "
        "WHERE timestamp_ms >= ? AND timestamp_ms < ? AND user_id IS NULL",
        (start_ms, end_ms),
    )["c"]
    anonymous_share = round(anon_count / event_count, 3) if event_count else 0.0

    # session 数 + 平均 event/session
    session_count = fetch_one(
        conn,
        "SELECT COUNT(DISTINCT session_id) AS c FROM events "
        "WHERE timestamp_ms >= ? AND timestamp_ms < ?",
        (start_ms, end_ms),
    )["c"]
    avg_session_event_count = (
        round(event_count / session_count, 1) if session_count else 0.0
    )

    # 事件类型分布(top N)
    type_rows = fetch_all(
        conn,
        "SELECT event_type, COUNT(*) AS c FROM events "
        "WHERE timestamp_ms >= ? AND timestamp_ms < ? "
        "GROUP BY event_type ORDER BY c DESC LIMIT ?",
        (start_ms, end_ms, MAX_TYPE_DIST_IN_PROMPT),
    )
    event_type_distribution = {r["event_type"]: r["c"] for r in type_rows}

    # 4 态分布
    mode_rows = fetch_all(
        conn,
        "SELECT mode, COUNT(*) AS c FROM events "
        "WHERE timestamp_ms >= ? AND timestamp_ms < ? AND mode IS NOT NULL "
        "GROUP BY mode",
        (start_ms, end_ms),
    )
    mode_distribution = {r["mode"]: r["c"] for r in mode_rows}

    # top 访问页面
    path_rows = fetch_all(
        conn,
        "SELECT path, COUNT(*) AS c FROM events "
        "WHERE timestamp_ms >= ? AND timestamp_ms < ? "
        "AND event_type = 'page_view' AND path IS NOT NULL "
        "GROUP BY path ORDER BY c DESC LIMIT ?",
        (start_ms, end_ms, MAX_PATHS_IN_PROMPT),
    )
    top_paths = [{"path": r["path"], "count": r["c"]} for r in path_rows]

    # AI 调用统计
    ai_start = fetch_one(
        conn,
        "SELECT COUNT(*) AS c FROM events "
        "WHERE timestamp_ms >= ? AND timestamp_ms < ? AND event_type = 'ai_call_start'",
        (start_ms, end_ms),
    )["c"]
    ai_done = fetch_one(
        conn,
        "SELECT COUNT(*) AS c FROM events "
        "WHERE timestamp_ms >= ? AND timestamp_ms < ? AND event_type = 'ai_call_done'",
        (start_ms, end_ms),
    )["c"]
    ai_failed = fetch_one(
        conn,
        "SELECT COUNT(*) AS c FROM events "
        "WHERE timestamp_ms >= ? AND timestamp_ms < ? AND event_type = 'ai_call_failed'",
        (start_ms, end_ms),
    )["c"]
    ai_success_rate = (
        round(ai_done / (ai_done + ai_failed), 3)
        if (ai_done + ai_failed) > 0
        else 1.0
    )
    avg_duration_row = fetch_one(
        conn,
        "SELECT AVG(duration_ms) AS a FROM events "
        "WHERE timestamp_ms >= ? AND timestamp_ms < ? "
        "AND event_type = 'ai_call_done' AND duration_ms IS NOT NULL",
        (start_ms, end_ms),
    )
    ai_avg_duration = (
        int(avg_duration_row["a"]) if avg_duration_row and avg_duration_row["a"] is not None else 0
    )
    ai_call_stats = {
        "ai_call_start": ai_start,
        "ai_call_done": ai_done,
        "ai_call_failed": ai_failed,
        "success_rate": ai_success_rate,
        "avg_duration_ms": ai_avg_duration,
    }

    # 业务关键操作
    biz_types = [
        "project_create", "project_delete",
        "simulation_create", "simulation_done", "simulation_failed",
        "audit_run", "canonical_audit_run",
    ]
    placeholders = ",".join(["?"] * len(biz_types))
    biz_rows = fetch_all(
        conn,
        f"SELECT event_type, COUNT(*) AS c FROM events "
        f"WHERE timestamp_ms >= ? AND timestamp_ms < ? "
        f"AND event_type IN ({placeholders}) "
        f"GROUP BY event_type",
        (start_ms, end_ms, *biz_types),
    )
    biz_actions = {r["event_type"]: r["c"] for r in biz_rows}

    # 退出路径(page_leave 但 session 终止;近似 — 取 session 中最后一个 page_leave)
    # 简化做法:统计 page_leave + 后续 1 小时内该 session 无新事件 的 path
    # 复杂度高,这里降级:取 page_leave 的 path top 5
    exit_rows = fetch_all(
        conn,
        "SELECT path, COUNT(*) AS c FROM events "
        "WHERE timestamp_ms >= ? AND timestamp_ms < ? "
        "AND event_type = 'page_leave' AND path IS NOT NULL "
        "GROUP BY path ORDER BY c DESC LIMIT 5",
        (start_ms, end_ms),
    )
    exit_paths = [{"path": r["path"], "exit_count": r["c"]} for r in exit_rows]

    # 平均停留最久(top 5)
    long_stay_rows = fetch_all(
        conn,
        "SELECT path, AVG(duration_ms) AS a, COUNT(*) AS c FROM events "
        "WHERE timestamp_ms >= ? AND timestamp_ms < ? "
        "AND event_type = 'page_leave' AND duration_ms IS NOT NULL "
        "AND path IS NOT NULL "
        "GROUP BY path HAVING c >= 3 "
        "ORDER BY a DESC LIMIT 5",
        (start_ms, end_ms),
    )
    long_stay_paths = [
        {"path": r["path"], "avg_duration_ms": int(r["a"])} for r in long_stay_rows
    ]

    # huimeng.* 关联(若 attach 成功)
    huimeng_data: dict[str, Any] = {}
    if huimeng_attached(conn):
        try:
            r = fetch_one(conn, "SELECT COUNT(*) AS c FROM huimeng.users")
            huimeng_data["registered_users_total"] = r["c"] if r else 0
            r = fetch_one(conn, "SELECT COUNT(*) AS c FROM huimeng.projects")
            huimeng_data["projects_total"] = r["c"] if r else 0
            r = fetch_one(conn, "SELECT COUNT(*) AS c FROM huimeng.simulations")
            huimeng_data["simulations_total"] = r["c"] if r else 0
        except Exception as e:  # noqa: BLE001
            logger.warning(f"level1: huimeng query failed: {e}")

    return {
        "report_date": target_date,
        "event_count": event_count,
        "active_users": active_users,
        "anonymous_share": anonymous_share,
        "session_count": session_count,
        "avg_session_event_count": avg_session_event_count,
        "event_type_distribution": event_type_distribution,
        "mode_distribution": mode_distribution,
        "top_paths": top_paths,
        "ai_call_stats": ai_call_stats,
        "biz_actions": biz_actions,
        "exit_paths": exit_paths,
        "long_stay_paths": long_stay_paths,
        "huimeng_data": huimeng_data,
    }


def run_daily(
    conn: sqlite3.Connection,
    target_date: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """跑 Level 1 数据 agent.

    Args:
      target_date: "YYYY-MM-DD"
      force:       True 时忽略 MIN_EVENTS_FOR_AGENT 阈值

    Returns:
      {
        "ok": bool,
        "skipped": bool,
        "skip_reason": str | None,
        "report_id": int | None,
        "event_count": int,
        "cost_yuan": float,
        "error": str | None,
      }

    幂等:同 target_date 重跑会 UPSERT(同日已有报告会覆盖)。
    """
    # 已存在 — 删旧的(允许重跑 / force)
    existing = fetch_one(
        conn,
        "SELECT id FROM daily_reports WHERE report_date=?",
        (target_date,),
    )

    # 聚合数据
    try:
        agg = _aggregate_daily_data(conn, target_date)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"level1: aggregate failed for {target_date}: {e}")
        return {
            "ok": False,
            "skipped": False,
            "report_id": None,
            "event_count": 0,
            "cost_yuan": 0.0,
            "error": f"聚合失败: {e}",
        }

    event_count = agg["event_count"]
    now = datetime.utcnow().isoformat()

    # C 方案触发判定
    if event_count < MIN_EVENTS_FOR_AGENT and not force:
        # 落 skipped 记录(不调 LLM,0 成本)
        if existing:
            execute(
                conn,
                "DELETE FROM daily_reports WHERE id=?",
                (existing["id"],),
            )
        execute(
            conn,
            "INSERT INTO daily_reports (report_date, event_count, skipped, skip_reason, created_at) "
            "VALUES (?, ?, 1, ?, ?)",
            (
                target_date,
                event_count,
                f"事件不足({event_count} < {MIN_EVENTS_FOR_AGENT})",
                now,
            ),
        )
        new_id = fetch_one(
            conn,
            "SELECT id FROM daily_reports WHERE report_date=?",
            (target_date,),
        )["id"]
        return {
            "ok": True,
            "skipped": True,
            "skip_reason": f"事件不足({event_count} < {MIN_EVENTS_FOR_AGENT})",
            "report_id": new_id,
            "event_count": event_count,
            "cost_yuan": 0.0,
            "error": None,
        }

    # 跑 LLM
    try:
        system_prompt = _load_prompt("level1_daily.md")
        user_prompt = json.dumps(agg, ensure_ascii=False, indent=2)
        parsed, usage = call_llm_json(
            system_prompt, user_prompt,
            max_tokens=1500, temperature=0.3,
        )
    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"level1: LLM failed for {target_date}: {e}")
        return {
            "ok": False,
            "skipped": False,
            "report_id": None,
            "event_count": event_count,
            "cost_yuan": 0.0,
            "error": f"LLM 调用失败: {e}",
        }

    summary_md = str(parsed.get("summary_md") or "")[:5000]
    metrics = parsed.get("metrics") or {}
    if not isinstance(metrics, dict):
        metrics = {}
    tags = parsed.get("tags") or []
    if isinstance(tags, list):
        metrics["tags"] = [str(t)[:30] for t in tags[:10]]

    cost = estimate_cost_yuan(usage["input_tokens"], usage["output_tokens"])

    if existing:
        execute(
            conn,
            "DELETE FROM daily_reports WHERE id=?",
            (existing["id"],),
        )
    execute(
        conn,
        "INSERT INTO daily_reports "
        "(report_date, event_count, skipped, summary_md, metrics_json, "
        " tokens_input, tokens_output, cost_yuan, created_at) "
        "VALUES (?, ?, 0, ?, ?, ?, ?, ?, ?)",
        (
            target_date,
            event_count,
            summary_md,
            json.dumps(metrics, ensure_ascii=False),
            usage["input_tokens"],
            usage["output_tokens"],
            cost,
            now,
        ),
    )
    new_id = fetch_one(
        conn,
        "SELECT id FROM daily_reports WHERE report_date=?",
        (target_date,),
    )["id"]
    return {
        "ok": True,
        "skipped": False,
        "skip_reason": None,
        "report_id": new_id,
        "event_count": event_count,
        "cost_yuan": cost,
        "error": None,
    }


def get_pending_daily_dates(conn: sqlite3.Connection) -> list[str]:
    """找需要跑的日期:有 events 但还没 daily_report 的日.

    简化:取距今 30 天内有 events 的日期,扣掉已有报告的。
    """
    rows = fetch_all(
        conn,
        "SELECT DISTINCT DATE(timestamp_ms / 1000, 'unixepoch') AS d FROM events "
        "WHERE timestamp_ms >= ? ORDER BY d",
        (int((datetime.utcnow().timestamp() - 30 * 86400) * 1000),),
    )
    candidate_dates = [r["d"] for r in rows]
    existing_dates = {
        r["report_date"]
        for r in fetch_all(conn, "SELECT report_date FROM daily_reports")
    }
    return [d for d in candidate_dates if d not in existing_dates]


__all__ = [
    "MIN_EVENTS_FOR_AGENT",
    "run_daily",
    "get_pending_daily_dates",
]
