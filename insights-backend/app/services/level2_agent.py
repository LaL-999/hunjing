"""Level 2 汇总 agent(INS-A6,2026-05-27).

从一堆 Level 1 单日报告 → 阶段汇总报告.

触发条件(用户拍板):
  - 累积"未被任何 summary 收纳"的 daily_reports ≥ MIN_DAILY_REPORTS_FOR_SUMMARY(30 份)
  - OR 距上次 summary ≥ MIN_DAYS_INTERVAL(30 天)
  - OR force=True

agent 永远不改主平台 — 只读 daily_reports,产报告供人读。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.db import execute, fetch_all, fetch_one
from app.services.llm_client import (
    LlmCallFailed,
    LlmJsonParseFailed,
    call_llm_json,
    estimate_cost_yuan,
)


logger = logging.getLogger(__name__)


# 触发条件(用户拍板)
MIN_DAILY_REPORTS_FOR_SUMMARY = 30
MIN_DAYS_INTERVAL = 30

# prompt 输入截断阈值(防 prompt 爆 context)
MAX_DAILIES_IN_PROMPT = 60   # 阶段最多覆盖 60 份(2 个月)
SUMMARY_MD_TRUNCATE = 500    # 每份 daily summary_md 截断到 500 字

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def _get_uncovered_dailies(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """找还没被任何 summary 收纳的 daily_reports.

    规则:取所有 daily_reports 中,report_date > 最新 summary.period_end 的。
    若无 summary → 取所有 daily_reports。
    """
    last_summary = fetch_one(
        conn,
        "SELECT period_end FROM summary_reports ORDER BY stage_number DESC LIMIT 1",
    )
    if last_summary is None:
        return fetch_all(
            conn,
            "SELECT * FROM daily_reports WHERE skipped = 0 ORDER BY report_date ASC",
        )
    return fetch_all(
        conn,
        "SELECT * FROM daily_reports "
        "WHERE skipped = 0 AND report_date > ? "
        "ORDER BY report_date ASC",
        (last_summary["period_end"],),
    )


def _last_summary_created_days_ago(conn: sqlite3.Connection) -> int:
    """距上次 summary_reports.created_at 多少天.

    无 summary → 返回一个大数(确保不会因"距上次太短"而拒跑)。
    """
    last = fetch_one(
        conn,
        "SELECT created_at FROM summary_reports ORDER BY stage_number DESC LIMIT 1",
    )
    if last is None:
        return 99999
    try:
        # created_at 是 isoformat UTC
        last_dt = datetime.fromisoformat(last["created_at"])
        return (datetime.utcnow() - last_dt).days
    except (ValueError, TypeError):
        return 99999


def check_should_run(conn: sqlite3.Connection) -> tuple[bool, str]:
    """检查是否到触发 Level 2 的条件.

    Returns:
      (should_run, reason)
    """
    uncovered = _get_uncovered_dailies(conn)
    days_ago = _last_summary_created_days_ago(conn)

    if len(uncovered) >= MIN_DAILY_REPORTS_FOR_SUMMARY:
        return True, f"累积 {len(uncovered)} 份未汇总日报(阈值 {MIN_DAILY_REPORTS_FOR_SUMMARY})"
    if days_ago >= MIN_DAYS_INTERVAL and len(uncovered) > 0:
        return True, f"距上次汇总 {days_ago} 天(阈值 {MIN_DAYS_INTERVAL}),且有 {len(uncovered)} 份未汇总"
    return False, (
        f"累积 {len(uncovered)} / {MIN_DAILY_REPORTS_FOR_SUMMARY} 份未汇总日报;"
        f"距上次汇总 {days_ago} / {MIN_DAYS_INTERVAL} 天"
    )


def _build_trend_metrics(dailies: list[sqlite3.Row]) -> dict[str, Any]:
    """从 daily_reports 抽跨日聚合数据(给 LLM 看趋势)."""
    event_series: list[int] = []
    metrics_list: list[dict] = []
    for d in dailies:
        event_series.append(int(d["event_count"]))
        try:
            m = json.loads(d["metrics_json"] or "{}")
            if isinstance(m, dict):
                metrics_list.append(m)
        except (TypeError, ValueError):
            pass

    return {
        "event_count_series": event_series,
        "daily_count_in_series": len(dailies),
        "total_events": sum(event_series),
    }


def run_summary(
    conn: sqlite3.Connection,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """跑 Level 2 汇总 agent.

    Args:
      force: True 时跳过触发条件检查

    Returns:
      {
        "ok": bool,
        "skipped": bool,
        "skip_reason": str | None,
        "summary_id": int | None,
        "stage_number": int | None,
        "daily_count": int,
        "cost_yuan": float,
        "error": str | None,
      }
    """
    # 触发条件检查
    if not force:
        should_run, reason = check_should_run(conn)
        if not should_run:
            return {
                "ok": True,
                "skipped": True,
                "skip_reason": reason,
                "summary_id": None,
                "stage_number": None,
                "daily_count": 0,
                "cost_yuan": 0.0,
                "error": None,
            }

    # 取未覆盖的 dailies
    uncovered = _get_uncovered_dailies(conn)
    if not uncovered:
        return {
            "ok": True,
            "skipped": True,
            "skip_reason": "无未汇总日报",
            "summary_id": None,
            "stage_number": None,
            "daily_count": 0,
            "cost_yuan": 0.0,
            "error": None,
        }

    # 截断 — 防止 prompt 爆 context
    if len(uncovered) > MAX_DAILIES_IN_PROMPT:
        uncovered = uncovered[-MAX_DAILIES_IN_PROMPT:]
        logger.info(f"level2: dailies 截断到 {MAX_DAILIES_IN_PROMPT} 份(原超过)")

    period_start = uncovered[0]["report_date"]
    period_end = uncovered[-1]["report_date"]

    # 阶段号 = 上一个 stage_number + 1
    last = fetch_one(
        conn,
        "SELECT MAX(stage_number) AS m FROM summary_reports",
    )
    stage_number = (last["m"] or 0) + 1 if last else 1

    # 组装 prompt 输入
    dailies_payload = []
    for d in uncovered:
        try:
            metrics = json.loads(d["metrics_json"] or "{}")
        except (TypeError, ValueError):
            metrics = {}
        dailies_payload.append({
            "date": d["report_date"],
            "event_count": int(d["event_count"]),
            "summary_md": (d["summary_md"] or "")[:SUMMARY_MD_TRUNCATE],
            "metrics": metrics,
            "tags": metrics.get("tags", []) if isinstance(metrics, dict) else [],
        })

    payload = {
        "stage_number": stage_number,
        "period_start": period_start,
        "period_end": period_end,
        "daily_count": len(uncovered),
        "daily_reports": dailies_payload,
        "trend_metrics": _build_trend_metrics(uncovered),
    }

    # 跑 LLM
    try:
        system_prompt = _load_prompt("level2_summary.md")
        user_prompt = json.dumps(payload, ensure_ascii=False, indent=2)
        parsed, usage = call_llm_json(
            system_prompt, user_prompt,
            max_tokens=3000, temperature=0.3,
        )
    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"level2: LLM failed: {e}")
        return {
            "ok": False,
            "skipped": False,
            "summary_id": None,
            "stage_number": stage_number,
            "daily_count": len(uncovered),
            "cost_yuan": 0.0,
            "error": f"LLM 调用失败: {e}",
        }

    summary_md = str(parsed.get("summary_md") or "")[:10000]
    insights = parsed.get("insights") or {}
    if not isinstance(insights, dict):
        insights = {}
    tags = parsed.get("tags") or []
    if isinstance(tags, list):
        insights["tags"] = [str(t)[:30] for t in tags[:10]]

    cost = estimate_cost_yuan(usage["input_tokens"], usage["output_tokens"])
    now = datetime.utcnow().isoformat()

    execute(
        conn,
        "INSERT INTO summary_reports "
        "(stage_number, period_start, period_end, daily_count, "
        " summary_md, insights_json, tokens_input, tokens_output, cost_yuan, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            stage_number,
            period_start,
            period_end,
            len(uncovered),
            summary_md,
            json.dumps(insights, ensure_ascii=False),
            usage["input_tokens"],
            usage["output_tokens"],
            cost,
            now,
        ),
    )
    new_id = fetch_one(
        conn,
        "SELECT id FROM summary_reports WHERE stage_number=?",
        (stage_number,),
    )["id"]

    return {
        "ok": True,
        "skipped": False,
        "skip_reason": None,
        "summary_id": new_id,
        "stage_number": stage_number,
        "daily_count": len(uncovered),
        "cost_yuan": cost,
        "error": None,
    }


__all__ = [
    "MIN_DAILY_REPORTS_FOR_SUMMARY",
    "MIN_DAYS_INTERVAL",
    "check_should_run",
    "run_summary",
]
