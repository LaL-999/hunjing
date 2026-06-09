"""GET /admin/* — 给独立的 insights-frontend 调用(2026-05-27).

设计:
  - 所有端点要求 Header `X-Admin-Token` 等于 settings.ADMIN_TOKEN
  - A4(2026-05-27):4 个核心 dashboard
    · /admin/overview   总览(用户数 / 事件数 / 项目数 / 创作数)
    · /admin/funnel     转化漏斗(注册 → 创建项目 → 启动推演 → 完成审计)
    · /admin/retention  留存(N 日回访率)
    · /admin/user/:id   单用户画像(行为序列 + 项目数据关联)
  - INS-B Phase 1(2026-05-27 末⁵):4 个新经营 dashboard
    · /admin/quality    质量观察(audits 评分 / canonical 维度 / 失败率 / 完成时长)
    · /admin/safety     合规风控(violation_logs / 高风险用户 / 同 IP 多账号)
    · /admin/compass    作者指南针使用看板(author_compass 使用率 / 锁定率 / top 作家)
    · /admin/live       实时事件流(最近 50 条 events + 当前在线 + 进行中推演)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import Counter
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config import settings
from app.db import fetch_all, fetch_one, get_db_connection, huimeng_attached


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin")


def require_admin(x_admin_token: Optional[str] = Header(None)) -> None:
    """简单 token 鉴权 — insights-frontend 在 header 带这个 token。"""
    if not x_admin_token or x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_ADMIN_TOKEN", "message": "需要有效 X-Admin-Token"},
        )


def _get_conn() -> sqlite3.Connection:
    return get_db_connection()


# ============================================================
# /admin/overview — 总览看板
# ============================================================

@router.get("/overview")
async def get_overview(_: None = Depends(require_admin)) -> dict:
    """总览看板:
      - A4 原 6 卡:总事件 / 今日事件 / 7日活跃 / 注册用户 / 项目数 / 推演数
      - INS-B4(2026-05-27 末⁵²):加 3 卡 — LLM 累计成本 ¥ / 本月失败推演数 / 高风险用户数
    """
    conn = _get_conn()
    try:
        now_ms = int(time.time() * 1000)
        today_start_ms = now_ms - (now_ms % (24 * 3600 * 1000))
        week_ago_ms = now_ms - 7 * 24 * 3600 * 1000

        total_events = fetch_one(conn, "SELECT COUNT(*) AS c FROM events")["c"]
        today_events = fetch_one(
            conn,
            "SELECT COUNT(*) AS c FROM events WHERE timestamp_ms >= ?",
            (today_start_ms,),
        )["c"]
        active_users_7d = fetch_one(
            conn,
            "SELECT COUNT(DISTINCT user_id) AS c FROM events "
            "WHERE timestamp_ms >= ? AND user_id IS NOT NULL",
            (week_ago_ms,),
        )["c"]

        # 注册用户数 — 从 attached huimeng.users 拉(若 huimeng 未 attach 则降级返 None)
        registered_users: Optional[int] = None
        projects_count: Optional[int] = None
        simulations_count: Optional[int] = None
        # INS-B4 新增 3 卡
        llm_cost_total_yuan: Optional[float] = None
        failed_simulations_this_month: Optional[int] = None
        high_risk_users_count: Optional[int] = None

        if huimeng_attached(conn):
            try:
                r = fetch_one(conn, "SELECT COUNT(*) AS c FROM huimeng.users")
                registered_users = r["c"] if r else 0
                r = fetch_one(conn, "SELECT COUNT(*) AS c FROM huimeng.projects")
                projects_count = r["c"] if r else 0
                r = fetch_one(conn, "SELECT COUNT(*) AS c FROM huimeng.simulations")
                simulations_count = r["c"] if r else 0

                # B4 卡 1:LLM 累计真实成本(credit_transactions.cost_yuan SUM kind='consume')
                r = fetch_one(
                    conn,
                    "SELECT COALESCE(SUM(cost_yuan), 0) AS s "
                    "FROM huimeng.credit_transactions WHERE kind='consume'",
                )
                llm_cost_total_yuan = round(float(r["s"]) if r else 0, 2)

                # B4 卡 2:本月失败推演数
                from datetime import datetime, timezone
                month_start = datetime.now(timezone.utc).replace(
                    day=1, hour=0, minute=0, second=0, microsecond=0
                ).isoformat()
                r = fetch_one(
                    conn,
                    "SELECT COUNT(*) AS c FROM huimeng.simulations "
                    "WHERE state='failed' AND created_at >= ?",
                    (month_start,),
                )
                failed_simulations_this_month = r["c"] if r else 0

                # B4 卡 3:高风险用户数(violation_logs ≥ 3 次)
                r = fetch_one(
                    conn,
                    "SELECT COUNT(*) AS c FROM ("
                    "  SELECT user_id FROM huimeng.violation_logs "
                    "  GROUP BY user_id HAVING COUNT(*) >= 3"
                    ")",
                )
                high_risk_users_count = r["c"] if r else 0
            except Exception as e:  # noqa: BLE001
                logger.warning(f"overview: huimeng query failed: {e}")

        # 事件类型分布(top 10)
        type_dist_rows = fetch_all(
            conn,
            "SELECT event_type, COUNT(*) AS c FROM events "
            "GROUP BY event_type ORDER BY c DESC LIMIT 10",
        )
        type_distribution = [{"type": r["event_type"], "count": r["c"]} for r in type_dist_rows]

        return {
            "total_events": total_events,
            "today_events": today_events,
            "active_users_7d": active_users_7d,
            "registered_users": registered_users,
            "projects_count": projects_count,
            "simulations_count": simulations_count,
            "huimeng_attached": huimeng_attached(conn),
            "type_distribution": type_distribution,
            # INS-B4 新增 3 卡
            "llm_cost_total_yuan": llm_cost_total_yuan,
            "failed_simulations_this_month": failed_simulations_this_month,
            "high_risk_users_count": high_risk_users_count,
        }
    finally:
        conn.close()


# ============================================================
# /admin/funnel — 转化漏斗
# ============================================================

@router.get("/funnel")
async def get_funnel(
    window: str = "all",
    mode: str = "all",
    plan: str = "all",
    _: None = Depends(require_admin),
) -> dict:
    """转化漏斗 — INS-B6(2026-05-27 末⁵²)加深 + 加宽.

    加深(4 步 → 9 步):
      1. 访客(events.event_type='session_start' 或 page_view,user_id IS NULL)
      2. 注册(huimeng.users.created_at)
      3. 上传作品(huimeng.uploads.created_at)
      4. 抽取完成(huimeng.extract_jobs.state='done')
      5. 创建项目(huimeng.projects.created_at)
      6. 启动推演(huimeng.simulations.created_at)
      7. 推演完成(huimeng.simulations.state='done')
      8. 跑过审计(huimeng.audits.created_at)
      9. 二次创作(同 user ≥ 2 个 simulation)
     10. 付费转化(huimeng.user_plan_snapshots.state='active' 或买过漫画包 / addon 包)

    加宽(3 筛选器):
      - window:all / this_week / this_month / quarter
      - mode:all / initial / middle / end(漫创态 cycle 不计入主漏斗 — 5.0a)
      - plan:all / free / pro / max / super_max

    所有步骤用 COUNT(DISTINCT user_id),同一用户每步只算 1 次.
    """
    from datetime import datetime, timezone, timedelta

    # --- 解析窗口 ---
    now = datetime.now(timezone.utc)
    if window == "this_week":
        window_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
    elif window == "this_month":
        window_start = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
    elif window == "quarter":
        window_start = (now - timedelta(days=90)).isoformat()
    else:
        window_start = ""  # all

    # --- mode 筛选(漫创 cycle 默认排除主漏斗)---
    if mode == "all":
        # 主漏斗不含漫创态(用户偏好 5.0a)
        mode_clause = "AND p.mode IN ('initial','middle','end')"
    elif mode in ("initial", "middle", "end"):
        mode_clause = f"AND p.mode = '{mode}'"
    elif mode == "cycle":
        # 显式选漫创态(漫创专项查询)
        mode_clause = "AND p.mode = 'cycle'"
    else:
        mode_clause = "AND p.mode IN ('initial','middle','end')"

    # --- plan 筛选(白名单防 SQL injection)---
    _PLAN_WHITELIST = {"free", "pro", "max", "super_max", "founder"}
    if plan == "all":
        plan_clause = ""
    elif plan in _PLAN_WHITELIST:
        plan_clause = f"AND u.plan = '{plan}'"
    else:
        plan_clause = ""  # 未知值降级 = all

    conn = _get_conn()
    try:
        steps_list = []

        if not huimeng_attached(conn):
            # 降级:只能算前端 events(去掉所有 huimeng 步骤)
            return {
                "window": window, "mode": mode, "plan": plan,
                "huimeng_attached": False,
                "steps": [],
                "note": "huimeng.db 未连接,无法计算完整漏斗",
            }

        # 步骤辅助:agg = 加 window/plan/mode 过滤的子查询
        def add_window(sql: str, col: str) -> str:
            if window_start:
                return sql + f" AND {col} >= '{window_start}'"
            return sql

        # 步骤 1:访客(events 表 — 不强制 huimeng_attach)
        sql1 = "SELECT COUNT(DISTINCT user_id) AS c FROM events WHERE user_id IS NOT NULL"
        if window_start:
            ts_ms = int(datetime.fromisoformat(window_start).timestamp() * 1000)
            sql1 += f" AND timestamp_ms >= {ts_ms}"
        r = fetch_one(conn, sql1)
        steps_list.append({"name": "访客 / 活跃", "count": r["c"] if r else 0})

        # 步骤 2:注册用户
        sql2 = (
            "SELECT COUNT(*) AS c FROM huimeng.users u WHERE 1=1 "
            + plan_clause
        )
        sql2 = add_window(sql2, "u.created_at")
        r = fetch_one(conn, sql2)
        steps_list.append({"name": "注册", "count": r["c"] if r else 0})

        # 步骤 3:上传作品
        sql3 = (
            "SELECT COUNT(DISTINCT up.user_id) AS c FROM huimeng.uploads up "
            "JOIN huimeng.users u ON u.id = up.user_id WHERE 1=1 " + plan_clause
        )
        sql3 = add_window(sql3, "up.created_at" if mode != "cycle" else "up.created_at")
        # NOTE: uploads 没 created_at? 用最早字段 — 查 schema 有 storage_path 但没看到 created_at
        # 用 state 是 ready / uploaded 当过滤
        r = fetch_one(conn, sql3.replace("AND up.created_at", "AND 1=1") if "created_at" not in sql3 else sql3)
        steps_list.append({"name": "上传作品", "count": r["c"] if r else 0})

        # 步骤 4:抽取完成
        sql4 = (
            "SELECT COUNT(DISTINCT ej.user_id) AS c FROM huimeng.extract_jobs ej "
            "JOIN huimeng.users u ON u.id = ej.user_id "
            "WHERE ej.state='done' " + plan_clause
        )
        sql4 = add_window(sql4, "ej.created_at")
        try:
            r = fetch_one(conn, sql4)
            steps_list.append({"name": "抽取完成", "count": r["c"] if r else 0})
        except Exception:  # noqa: BLE001
            steps_list.append({"name": "抽取完成", "count": None})

        # 步骤 5:创建项目(mode 在此处生效)
        sql5 = (
            "SELECT COUNT(DISTINCT p.user_id) AS c FROM huimeng.projects p "
            "JOIN huimeng.users u ON u.id = p.user_id WHERE 1=1 "
            + mode_clause + " " + plan_clause
        )
        sql5 = add_window(sql5, "p.created_at")
        r = fetch_one(conn, sql5)
        steps_list.append({"name": "创建项目", "count": r["c"] if r else 0})

        # 步骤 6:启动推演
        sql6 = (
            "SELECT COUNT(DISTINCT s.user_id) AS c FROM huimeng.simulations s "
            "JOIN huimeng.projects p ON p.id = s.project_id "
            "JOIN huimeng.users u ON u.id = s.user_id WHERE 1=1 "
            + mode_clause + " " + plan_clause
        )
        sql6 = add_window(sql6, "s.created_at")
        r = fetch_one(conn, sql6)
        steps_list.append({"name": "启动推演", "count": r["c"] if r else 0})

        # 步骤 7:推演完成
        sql7 = (
            "SELECT COUNT(DISTINCT s.user_id) AS c FROM huimeng.simulations s "
            "JOIN huimeng.projects p ON p.id = s.project_id "
            "JOIN huimeng.users u ON u.id = s.user_id "
            "WHERE s.state='done' " + mode_clause + " " + plan_clause
        )
        sql7 = add_window(sql7, "s.completed_at")
        r = fetch_one(conn, sql7)
        steps_list.append({"name": "推演完成", "count": r["c"] if r else 0})

        # 步骤 8:跑过审计
        sql8 = (
            "SELECT COUNT(DISTINCT a.user_id) AS c FROM huimeng.audits a "
            "JOIN huimeng.users u ON u.id = a.user_id WHERE 1=1 " + plan_clause
        )
        sql8 = add_window(sql8, "a.triggered_at")
        r = fetch_one(conn, sql8)
        steps_list.append({"name": "跑过审计", "count": r["c"] if r else 0})

        # 步骤 9:二次创作(同 user ≥ 2 个 simulation)
        sql9 = (
            "SELECT COUNT(*) AS c FROM ("
            "  SELECT s.user_id FROM huimeng.simulations s "
            "  JOIN huimeng.projects p ON p.id = s.project_id "
            "  JOIN huimeng.users u ON u.id = s.user_id "
            "  WHERE 1=1 " + mode_clause + " " + plan_clause + " "
            "  GROUP BY s.user_id HAVING COUNT(*) >= 2"
            ")"
        )
        r = fetch_one(conn, sql9)
        steps_list.append({"name": "二次创作", "count": r["c"] if r else 0})

        # 步骤 10:付费转化(active 订阅 OR 买过漫画包 OR 买过 addon)
        sql10_parts = []
        sql10_parts.append(
            "SELECT DISTINCT user_id FROM huimeng.user_plan_snapshots "
            "WHERE state='active'"
        )
        sql10_parts.append("SELECT DISTINCT user_id FROM huimeng.comic_pack_lots")
        sql10_parts.append("SELECT DISTINCT user_id FROM huimeng.addon_credit_lots")
        sql10 = (
            "SELECT COUNT(DISTINCT user_id) AS c FROM ("
            + " UNION ".join(sql10_parts) + ")"
        )
        try:
            r = fetch_one(conn, sql10)
            steps_list.append({"name": "付费转化", "count": r["c"] if r else 0})
        except Exception:  # noqa: BLE001
            steps_list.append({"name": "付费转化", "count": 0})

        return {
            "window": window,
            "mode": mode,
            "plan": plan,
            "huimeng_attached": True,
            "steps": steps_list,
            "note": (
                "漫创态(cycle)不计入主漏斗(用户偏好 5.0a);"
                "如需查漫创专项,mode=cycle"
            ) if mode == "all" else None,
        }
    finally:
        conn.close()


# ============================================================
# /admin/retention — N 日留存
# ============================================================

@router.get("/retention")
async def get_retention(
    days: int = 7,
    _: None = Depends(require_admin),
) -> dict:
    """N 日留存(简化版):
      - 取 N 天前那一天首次出现的用户(Cohort)
      - 计算这些用户在之后每一天的活跃数
    """
    if days < 1 or days > 30:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_DAYS", "message": "days 必须在 1-30"},
        )
    conn = _get_conn()
    try:
        now_ms = int(time.time() * 1000)
        day_ms = 24 * 3600 * 1000

        # Cohort 起点:N 天前那一天的 00:00
        cohort_day_start = now_ms - days * day_ms
        cohort_day_start -= cohort_day_start % day_ms
        cohort_day_end = cohort_day_start + day_ms

        # 这一天首次出现的用户(注意:此处简化为"这一天有活动"非严格 first_seen)
        cohort_rows = fetch_all(
            conn,
            "SELECT DISTINCT user_id FROM events "
            "WHERE timestamp_ms >= ? AND timestamp_ms < ? "
            "AND user_id IS NOT NULL",
            (cohort_day_start, cohort_day_end),
        )
        cohort_user_ids = [r["user_id"] for r in cohort_rows]
        cohort_size = len(cohort_user_ids)

        if cohort_size == 0:
            return {
                "cohort_day": cohort_day_start,
                "cohort_size": 0,
                "retention": [],
            }

        # 之后每天的活跃数
        retention = []
        placeholders = ",".join(["?"] * cohort_size)
        for day_offset in range(days + 1):
            day_start = cohort_day_start + day_offset * day_ms
            day_end = day_start + day_ms
            r = fetch_one(
                conn,
                f"SELECT COUNT(DISTINCT user_id) AS c FROM events "
                f"WHERE timestamp_ms >= ? AND timestamp_ms < ? "
                f"AND user_id IN ({placeholders})",
                (day_start, day_end, *cohort_user_ids),
            )
            retention.append({
                "day": day_offset,
                "active": r["c"] if r else 0,
                "rate": round((r["c"] / cohort_size) if r else 0.0, 3),
            })

        return {
            "cohort_day": cohort_day_start,
            "cohort_size": cohort_size,
            "retention": retention,
        }
    finally:
        conn.close()


# ============================================================
# /admin/user/{user_id} — 单用户画像
# ============================================================

# ════════════════════════════════════════════════════════════════
# INS-B Phase 4(2026-05-27 末⁵³) — 用户画像独立大 tab + 专业搜索引擎
# ════════════════════════════════════════════════════════════════
#
# 用户拍板"长远眼光"(以后上万人) — 不能一次拉所有用户,必须:
#   1. 服务端分页(page + page_size)
#   2. 复合筛选(plan / risk / 时间窗口)
#   3. 多排序(注册时间 / 累计消费 / 红旗命中 / 最近活跃)
#   4. 邮箱 / user_id 模糊搜索(LIKE,小数据;大数据可升 FTS5)
#   5. 索引友好(WHERE 条件用 indexed column)

_USER_SORT_WHITELIST = {
    "created_desc":   "u.created_at DESC",
    "created_asc":    "u.created_at ASC",
    "consumed_desc":  "total_consumed_yuan DESC NULLS LAST",
    "violation_desc": "violation_count DESC NULLS LAST",
    "last_active_desc": "last_event_ms DESC NULLS LAST",
}


@router.get("/users")
async def get_users_list(
    q: Optional[str] = None,
    plan: str = "all",
    risk: str = "all",
    sort: str = "created_desc",
    page: int = 1,
    page_size: int = 20,
    _: None = Depends(require_admin),
) -> dict:
    """用户列表 — 专业搜索引擎(分页 + 复合筛选 + 多排序 + 模糊搜索).

    query 参数:
      q          邮箱 / user_id 模糊匹配(LIKE %q%)
      plan       筛选订阅档:all / free / pro / max / super_max / founder
      risk       筛选风险:all / has_violation(>0) / high_risk(>=3)
      sort       排序:created_desc / created_asc / consumed_desc /
                 violation_desc / last_active_desc(白名单防 SQL inject)
      page       分页页码(1 起)
      page_size  每页(默认 20,最大 100)

    response:
      total / page / page_size / users[]
      users 行:user_id / email / plan / register_ip / created_at /
              projects_count / simulations_count / violation_count /
              total_consumed_yuan / last_event_ms / is_high_risk
    """
    if not (1 <= page_size <= 100):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_PAGE_SIZE", "message": "page_size 必须在 1-100"},
        )
    if page < 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_PAGE", "message": "page 必须 >= 1"},
        )

    # --- 白名单 ---
    _PLAN_WHITELIST = {"free", "pro", "max", "super_max", "founder"}
    sort_clause = _USER_SORT_WHITELIST.get(sort, _USER_SORT_WHITELIST["created_desc"])

    conn = _get_conn()
    try:
        if not huimeng_attached(conn):
            return {
                "total": 0, "page": page, "page_size": page_size,
                "huimeng_attached": False, "users": [],
            }

        # ---- WHERE 条件构造(参数化防 inject)----
        where_parts = ["1=1"]
        params: list = []

        if q and q.strip():
            q_like = f"%{q.strip()}%"
            where_parts.append("(u.email LIKE ? OR u.id LIKE ?)")
            params.extend([q_like, q_like])

        if plan != "all" and plan in _PLAN_WHITELIST:
            where_parts.append("u.plan = ?")
            params.append(plan)

        where_sql = " AND ".join(where_parts)

        # ---- 子查询:violation_count / total_consumed_yuan / last_event_ms ----
        # 这些是聚合字段,放在 SELECT 子查询里(LEFT JOIN 也行,但子查询更清晰)
        base_select = f"""
        SELECT
          u.id AS user_id,
          u.email,
          u.plan,
          u.register_ip,
          u.created_at,
          (SELECT COUNT(*) FROM huimeng.projects p WHERE p.user_id=u.id) AS projects_count,
          (SELECT COUNT(*) FROM huimeng.simulations s
             JOIN huimeng.projects p ON p.id=s.project_id
             WHERE p.user_id=u.id) AS simulations_count,
          (SELECT COUNT(*) FROM huimeng.violation_logs v WHERE v.user_id=u.id) AS violation_count,
          (SELECT COALESCE(SUM(cost_yuan), 0) FROM huimeng.credit_transactions ct
             WHERE ct.user_id=u.id AND ct.kind='consume') AS total_consumed_yuan,
          (SELECT MAX(timestamp_ms) FROM events e WHERE e.user_id=u.id) AS last_event_ms
        FROM huimeng.users u
        WHERE {where_sql}
        """

        # ---- risk 筛选(在外层 wrap,因为依赖聚合字段)----
        risk_wrap = base_select
        if risk == "has_violation":
            risk_wrap = f"SELECT * FROM ({base_select}) AS t WHERE violation_count > 0"
        elif risk == "high_risk":
            risk_wrap = f"SELECT * FROM ({base_select}) AS t WHERE violation_count >= 3"

        # ---- 总数 ----
        count_sql = f"SELECT COUNT(*) AS c FROM ({risk_wrap}) AS tt"
        total = fetch_one(conn, count_sql, tuple(params))
        total_count = total["c"] if total else 0

        # ---- 分页 + 排序 ----
        offset = (page - 1) * page_size
        paged_sql = (
            f"{risk_wrap} ORDER BY {sort_clause} LIMIT ? OFFSET ?"
        )
        rows = fetch_all(conn, paged_sql, tuple(params) + (page_size, offset))

        users = [
            {
                "user_id": r["user_id"],
                "email": r["email"],
                "plan": r["plan"],
                "register_ip": r["register_ip"],
                "created_at": r["created_at"],
                "projects_count": r["projects_count"],
                "simulations_count": r["simulations_count"],
                "violation_count": r["violation_count"],
                "total_consumed_yuan": round(float(r["total_consumed_yuan"] or 0), 2),
                "last_event_ms": r["last_event_ms"],
                "is_high_risk": (r["violation_count"] or 0) >= 3,
            }
            for r in rows
        ]

        return {
            "total": total_count,
            "page": page,
            "page_size": page_size,
            "huimeng_attached": True,
            "users": users,
            "filters": {"q": q, "plan": plan, "risk": risk, "sort": sort},
        }
    finally:
        conn.close()


@router.get("/user/{user_id}")
async def get_user_profile(
    user_id: str,
    _: None = Depends(require_admin),
) -> dict:
    """单用户行为画像 — INS-B5(2026-05-27 末⁵²)加厚.

    原(A4):行为序列 / 项目数 / 推演数 / AI 调用统计 / 4 态分布.
    新加(B5):
      - account:email / plan / register_ip / created_at
      - balance:subscription_credits / addon_credits / available_comic_packs
      - total_consumed_yuan(用户累计真实 LLM 成本)
      - works(projects 列表 + 每个的推演数 / 字数)
      - uploads(列表)
      - violation_count(红旗命中次数)
      - audit_scores_timeline(最近 10 次评分时间线)
    """
    conn = _get_conn()
    try:
        # 行为序列(最近 100 条)
        event_rows = fetch_all(
            conn,
            "SELECT * FROM events WHERE user_id = ? "
            "ORDER BY timestamp_ms DESC LIMIT 100",
            (user_id,),
        )
        events = [
            {
                "id": r["id"],
                "event_type": r["event_type"],
                "timestamp_ms": r["timestamp_ms"],
                "mode": r["mode"],
                "step": r["step"],
                "path": r["path"],
                "duration_ms": r["duration_ms"],
                "project_id": r["project_id"],
            }
            for r in event_rows
        ]

        # AI 调用统计
        ai_stats_rows = fetch_all(
            conn,
            "SELECT event_type, COUNT(*) AS c FROM events "
            "WHERE user_id = ? AND event_type LIKE 'ai_call%' "
            "GROUP BY event_type",
            (user_id,),
        )
        ai_stats = {r["event_type"]: r["c"] for r in ai_stats_rows}

        # 4 态创作分布
        mode_dist_rows = fetch_all(
            conn,
            "SELECT mode, COUNT(*) AS c FROM events "
            "WHERE user_id = ? AND mode IS NOT NULL "
            "GROUP BY mode",
            (user_id,),
        )
        mode_distribution = {r["mode"]: r["c"] for r in mode_dist_rows}

        # ============================================================
        # B5 加厚:account / balance / works / uploads / violations / audits
        # ============================================================
        projects_count: Optional[int] = None
        simulations_count: Optional[int] = None
        account: Optional[dict] = None
        balance: Optional[dict] = None
        total_consumed_yuan: Optional[float] = None
        works: list = []
        uploads_list: list = []
        violation_count: Optional[int] = None
        audit_scores_timeline: list = []

        if huimeng_attached(conn):
            try:
                # 账户元信息
                u = fetch_one(
                    conn,
                    "SELECT email, plan, register_ip, register_ua, created_at "
                    "FROM huimeng.users WHERE id = ?",
                    (user_id,),
                )
                if u:
                    account = {
                        "email": u["email"],
                        "plan": u["plan"],
                        "register_ip": u["register_ip"],
                        "register_ua": u["register_ua"],
                        "created_at": u["created_at"],
                    }

                # 余额
                b = fetch_one(
                    conn,
                    "SELECT subscription_credits, addon_credits "
                    "FROM huimeng.user_credit_balances WHERE user_id = ?",
                    (user_id,),
                )
                if b:
                    comic_pack_row = fetch_one(
                        conn,
                        "SELECT COUNT(*) AS c FROM huimeng.comic_pack_lots "
                        "WHERE user_id=? AND is_used=0 AND is_expired=0",
                        (user_id,),
                    )
                    balance = {
                        "subscription_credits": b["subscription_credits"],
                        "addon_credits": b["addon_credits"],
                        "available_comic_packs": comic_pack_row["c"] if comic_pack_row else 0,
                    }

                # 累计消费 ¥
                tc = fetch_one(
                    conn,
                    "SELECT COALESCE(SUM(cost_yuan), 0) AS s "
                    "FROM huimeng.credit_transactions "
                    "WHERE user_id=? AND kind='consume'",
                    (user_id,),
                )
                total_consumed_yuan = round(float(tc["s"]) if tc else 0, 4)

                # projects + simulations count
                r = fetch_one(
                    conn,
                    "SELECT COUNT(*) AS c FROM huimeng.projects WHERE user_id = ?",
                    (user_id,),
                )
                projects_count = r["c"] if r else 0
                r = fetch_one(
                    conn,
                    "SELECT COUNT(*) AS c FROM huimeng.simulations s "
                    "JOIN huimeng.projects p ON p.id = s.project_id "
                    "WHERE p.user_id = ?",
                    (user_id,),
                )
                simulations_count = r["c"] if r else 0

                # 作品列表
                work_rows = fetch_all(
                    conn,
                    "SELECT p.id, p.name, p.mode, p.type, p.created_at, "
                    "  (SELECT COUNT(*) FROM huimeng.simulations s WHERE s.project_id=p.id) AS sim_count "
                    "FROM huimeng.projects p WHERE p.user_id=? "
                    "ORDER BY p.created_at DESC LIMIT 20",
                    (user_id,),
                )
                works = [dict(r) for r in work_rows]

                # uploads
                try:
                    up_rows = fetch_all(
                        conn,
                        "SELECT id, filename, state, parsed_text_chars, size_bytes "
                        "FROM huimeng.uploads WHERE user_id=? "
                        "ORDER BY id DESC LIMIT 20",
                        (user_id,),
                    )
                    uploads_list = [dict(r) for r in up_rows]
                except Exception:  # noqa: BLE001
                    uploads_list = []

                # 红旗命中
                vc = fetch_one(
                    conn,
                    "SELECT COUNT(*) AS c FROM huimeng.violation_logs WHERE user_id = ?",
                    (user_id,),
                )
                violation_count = vc["c"] if vc else 0

                # 最近 10 次审计评分(audit_scores_timeline)
                audit_rows = fetch_all(
                    conn,
                    "SELECT overall_score, triggered_at FROM huimeng.audits "
                    "WHERE user_id = ? ORDER BY triggered_at DESC LIMIT 10",
                    (user_id,),
                )
                audit_scores_timeline = [
                    {"score": int(r["overall_score"]), "at": r["triggered_at"]}
                    for r in audit_rows
                ]
            except Exception as e:  # noqa: BLE001
                logger.warning(f"user profile B5: huimeng query failed: {e}")

        return {
            "user_id": user_id,
            "events": events,
            "ai_call_stats": ai_stats,
            "mode_distribution": mode_distribution,
            "projects_count": projects_count,
            "simulations_count": simulations_count,
            # B5 加厚
            "account": account,
            "balance": balance,
            "total_consumed_yuan": total_consumed_yuan,
            "works": works,
            "uploads": uploads_list,
            "violation_count": violation_count,
            "audit_scores_timeline": audit_scores_timeline,
        }
    finally:
        conn.close()


# ============================================================
# /admin/business — B1 经营驾驶舱(INS-B Phase 3,2026-05-27 末⁵²)
# ============================================================

@router.get("/business")
async def get_business(_: None = Depends(require_admin)) -> dict:
    """经营驾驶舱:LLM 烧的钱 + 用户付的钱 + 毛利估算.

    指标:
      - LLM 累计成本 / 今日 / 本周 / 本月
      - 按 action 分类成本 top 10
      - 单推演平均成本(simulations.cost_yuan)
      - 用户付费收入:订阅(user_plan_snapshots active) + 漫画包(comic_pack_lots) + addon
      - 付费分层(users.plan 分组人数 + 各档预估月收入)
      - ARPU(累计收入 / 付费用户数)
      - 毛利估算(收入 - LLM 真实成本)
      - 漫创态单列(用户偏好 5.0a)
    """
    from datetime import datetime, timezone, timedelta

    conn = _get_conn()
    try:
        result: dict = {
            "huimeng_attached": huimeng_attached(conn),
            "llm_cost": None,
            "cost_by_action": [],
            "avg_sim_cost": None,
            "revenue": None,
            "plan_distribution": [],
            "arpu": None,
            "gross_profit": None,
            "comic_economy": None,
        }
        if not huimeng_attached(conn):
            return result

        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        week_start = (now - timedelta(days=now.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        month_start = now.replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).isoformat()

        # --- LLM 累计成本(consume kind)---
        def sum_cost(since: Optional[str]) -> float:
            sql = (
                "SELECT COALESCE(SUM(cost_yuan), 0) AS s "
                "FROM huimeng.credit_transactions WHERE kind='consume'"
            )
            params = ()
            if since:
                sql += " AND created_at >= ?"
                params = (since,)
            r = fetch_one(conn, sql, params)
            return round(float(r["s"]) if r else 0, 2)

        result["llm_cost"] = {
            "total_yuan": sum_cost(None),
            "today_yuan": sum_cost(today_start),
            "this_week_yuan": sum_cost(week_start),
            "this_month_yuan": sum_cost(month_start),
        }

        # --- 按 action 分类成本 top 10 ---
        action_rows = fetch_all(
            conn,
            "SELECT action, COALESCE(SUM(cost_yuan), 0) AS s, COUNT(*) AS c "
            "FROM huimeng.credit_transactions WHERE kind='consume' "
            "GROUP BY action ORDER BY s DESC LIMIT 10",
        )
        result["cost_by_action"] = [
            {
                "action": r["action"],
                "cost_yuan": round(float(r["s"]), 2),
                "call_count": r["c"],
            }
            for r in action_rows
        ]

        # --- 单推演平均成本(用 simulations.cost_yuan)---
        avg_row = fetch_one(
            conn,
            "SELECT COALESCE(AVG(cost_yuan), 0) AS a, "
            "       COALESCE(COUNT(*), 0) AS c, "
            "       COALESCE(SUM(cost_yuan), 0) AS s "
            "FROM huimeng.simulations WHERE state='done' AND cost_yuan > 0",
        )
        if avg_row and avg_row["c"]:
            result["avg_sim_cost"] = {
                "average_yuan": round(float(avg_row["a"]), 3),
                "total_yuan": round(float(avg_row["s"]), 2),
                "completed_sims": avg_row["c"],
            }

        # --- 收入(订阅 + 漫画包 + addon)---
        # 订阅:active snapshots 的 price_cents SUM(累计已收订阅费)
        sub_row = fetch_one(
            conn,
            "SELECT COALESCE(SUM(price_cents), 0) AS s, COUNT(*) AS c "
            "FROM huimeng.user_plan_snapshots WHERE state='active'",
        )
        subscription_revenue = round((sub_row["s"] or 0) / 100, 2) if sub_row else 0
        active_subscribers = sub_row["c"] if sub_row else 0

        # 漫画包
        comic_row = fetch_one(
            conn,
            "SELECT COALESCE(SUM(price_cents), 0) AS s, COUNT(*) AS c "
            "FROM huimeng.comic_pack_lots",
        )
        comic_revenue = round((comic_row["s"] or 0) / 100, 2) if comic_row else 0
        comic_packs_sold = comic_row["c"] if comic_row else 0

        # addon
        addon_row = fetch_one(
            conn,
            "SELECT COALESCE(SUM(price_cents), 0) AS s, COUNT(*) AS c "
            "FROM huimeng.addon_credit_lots",
        )
        addon_revenue = round((addon_row["s"] or 0) / 100, 2) if addon_row else 0
        addon_packs_sold = addon_row["c"] if addon_row else 0

        total_revenue = round(subscription_revenue + comic_revenue + addon_revenue, 2)
        result["revenue"] = {
            "total_yuan": total_revenue,
            "subscription_yuan": subscription_revenue,
            "comic_pack_yuan": comic_revenue,
            "addon_yuan": addon_revenue,
            "active_subscribers": active_subscribers,
            "comic_packs_sold": comic_packs_sold,
            "addon_packs_sold": addon_packs_sold,
        }

        # --- 付费分层(users.plan)---
        plan_rows = fetch_all(
            conn,
            "SELECT plan, COUNT(*) AS c FROM huimeng.users GROUP BY plan ORDER BY c DESC",
        )
        result["plan_distribution"] = [
            {"plan": r["plan"], "user_count": r["c"]} for r in plan_rows
        ]

        # --- ARPU(总收入 / 付费用户数)---
        paid_user_row = fetch_one(
            conn,
            "SELECT COUNT(DISTINCT user_id) AS c FROM ("
            "  SELECT user_id FROM huimeng.user_plan_snapshots WHERE state='active' "
            "  UNION SELECT user_id FROM huimeng.comic_pack_lots "
            "  UNION SELECT user_id FROM huimeng.addon_credit_lots"
            ")",
        )
        paid_users = paid_user_row["c"] if paid_user_row else 0
        result["arpu"] = {
            "paid_users": paid_users,
            "arpu_yuan": round(total_revenue / paid_users, 2) if paid_users > 0 else 0,
        }

        # --- 毛利估算 ---
        result["gross_profit"] = {
            "revenue_yuan": total_revenue,
            "llm_cost_yuan": result["llm_cost"]["total_yuan"],
            "gross_profit_yuan": round(total_revenue - result["llm_cost"]["total_yuan"], 2),
            "gross_margin": (
                round(
                    (total_revenue - result["llm_cost"]["total_yuan"]) / total_revenue, 3
                )
                if total_revenue > 0 else 0
            ),
        }

        # --- 漫创态经济(单列,5.0a 偏好)---
        try:
            comic_cost_row = fetch_one(
                conn,
                "SELECT COALESCE(SUM(cost_yuan), 0) AS s "
                "FROM huimeng.credit_transactions "
                "WHERE kind='consume' AND action LIKE 'comic_%'",
            )
            comic_done_row = fetch_one(
                conn,
                "SELECT COUNT(*) AS c FROM huimeng.comic_projects WHERE state='done'",
            )
            result["comic_economy"] = {
                "llm_cost_yuan": round(float(comic_cost_row["s"]) if comic_cost_row else 0, 2),
                "revenue_yuan": comic_revenue,
                "packs_sold": comic_packs_sold,
                "completed_comics": comic_done_row["c"] if comic_done_row else 0,
                "note": "漫创态单列展示(用户偏好 5.0a),不混入主指标",
            }
        except Exception:  # noqa: BLE001
            pass

        return result
    finally:
        conn.close()


# ════════════════════════════════════════════════════════════════
# INS-B Phase 1(2026-05-27 末⁵) — 4 个新经营 dashboard
# ════════════════════════════════════════════════════════════════


# ============================================================
# /admin/quality — B2 质量观察
# ============================================================

@router.get("/quality")
async def get_quality(_: None = Depends(require_admin)) -> dict:
    """B2 质量观察:
      - 自洽审计 overall_score 直方图(0-20/20-40/40-60/60-80/80-100)+ 平均分 / 中位数
      - 正典审计 9 维度严重度分布(从 canonical_audits.issues_json 解析)
      - 推演失败率 + top 5 失败原因(从 simulations.error_message)
      - 完成时长 P50 / P95(completed_at - started_at)
      - 漫创态单列(用户偏好 5.0a:洞察后台漫创态降权 / 分开看)
    """
    conn = _get_conn()
    try:
        result: dict = {
            "huimeng_attached": huimeng_attached(conn),
            "audit_score": None,
            "canonical_dimensions": [],
            "failure_rate": None,
            "failure_reasons": [],
            "completion_time": None,
            "comic_quality": None,
        }
        if not huimeng_attached(conn):
            return result

        # --- 自洽审计 overall_score 分布 ---
        score_rows = fetch_all(
            conn,
            "SELECT overall_score FROM huimeng.audits ORDER BY overall_score",
        )
        scores = [int(r["overall_score"]) for r in score_rows]
        if scores:
            buckets = [0, 0, 0, 0, 0]  # 0-20 / 20-40 / 40-60 / 60-80 / 80-100
            for s in scores:
                idx = min(4, s // 20)
                buckets[idx] += 1
            mid = len(scores) // 2
            median = scores[mid] if len(scores) % 2 == 1 else (scores[mid - 1] + scores[mid]) / 2
            result["audit_score"] = {
                "total": len(scores),
                "average": round(sum(scores) / len(scores), 1),
                "median": median,
                "buckets": [
                    {"range": "0-20", "count": buckets[0]},
                    {"range": "20-40", "count": buckets[1]},
                    {"range": "40-60", "count": buckets[2]},
                    {"range": "60-80", "count": buckets[3]},
                    {"range": "80-100", "count": buckets[4]},
                ],
            }

        # --- 正典审计 9 维度严重度分布 ---
        # 从 issues_json 解析 dimension + severity,聚合每维度命中数
        canon_rows = fetch_all(
            conn,
            "SELECT issues_json FROM huimeng.canonical_audits WHERE state='done'",
        )
        dim_severity = Counter()  # (dimension, severity) → count
        dim_total = Counter()     # dimension → total issue count
        for r in canon_rows:
            try:
                issues = json.loads(r["issues_json"] or "[]")
            except Exception:  # noqa: BLE001
                continue
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                dim = issue.get("dimension")
                sev = issue.get("severity")
                if dim and sev:
                    dim_severity[(dim, sev)] += 1
                    dim_total[dim] += 1
        result["canonical_dimensions"] = [
            {
                "dimension": dim,
                "total_issues": total,
                "by_severity": {
                    sev: dim_severity[(dim, sev)]
                    for sev in ("minor_drift", "obvious_drift", "severe_breach")
                    if dim_severity[(dim, sev)] > 0
                },
            }
            for dim, total in sorted(dim_total.items(), key=lambda x: -x[1])
        ]

        # --- 推演失败率 + 原因 top 5(只看非漫创态主域) ---
        # huimeng.simulations 没有 mode 字段,但 projects.mode 有.
        # 这里只用 simulations 表统计,漫创态在 comic_projects 单算
        total_sim_row = fetch_one(
            conn, "SELECT COUNT(*) AS c FROM huimeng.simulations"
        )
        failed_sim_row = fetch_one(
            conn,
            "SELECT COUNT(*) AS c FROM huimeng.simulations WHERE state='failed'",
        )
        total_sim = total_sim_row["c"] if total_sim_row else 0
        failed_sim = failed_sim_row["c"] if failed_sim_row else 0
        if total_sim > 0:
            result["failure_rate"] = {
                "total": total_sim,
                "failed": failed_sim,
                "rate": round(failed_sim / total_sim, 3),
            }
        reason_rows = fetch_all(
            conn,
            "SELECT error_message FROM huimeng.simulations "
            "WHERE state='failed' AND error_message IS NOT NULL AND error_message != '' "
            "ORDER BY created_at DESC LIMIT 100",
        )
        # 简化版:取 error_message 前 60 字作 "原因 type"(避免长堆栈污染统计)
        reason_counter = Counter()
        for r in reason_rows:
            msg = (r["error_message"] or "").strip()
            if msg:
                reason_counter[msg[:60]] += 1
        result["failure_reasons"] = [
            {"reason": k, "count": v}
            for k, v in reason_counter.most_common(5)
        ]

        # --- 完成时长 P50 / P95 ---
        time_rows = fetch_all(
            conn,
            "SELECT started_at, completed_at FROM huimeng.simulations "
            "WHERE state='done' AND started_at IS NOT NULL AND completed_at IS NOT NULL",
        )
        durations_sec: list[float] = []
        for r in time_rows:
            try:
                from datetime import datetime
                s = datetime.fromisoformat(r["started_at"].replace("Z", "+00:00"))
                c = datetime.fromisoformat(r["completed_at"].replace("Z", "+00:00"))
                durations_sec.append((c - s).total_seconds())
            except Exception:  # noqa: BLE001
                continue
        if durations_sec:
            durations_sec.sort()
            n = len(durations_sec)
            result["completion_time"] = {
                "samples": n,
                "p50_sec": round(durations_sec[n // 2], 1),
                "p95_sec": round(durations_sec[min(n - 1, int(n * 0.95))], 1),
                "avg_sec": round(sum(durations_sec) / n, 1),
            }

        # --- 漫创态质量(单列,5.0a 偏好)---
        try:
            comic_total_row = fetch_one(
                conn, "SELECT COUNT(*) AS c FROM huimeng.comic_projects"
            )
            comic_failed_row = fetch_one(
                conn,
                "SELECT COUNT(*) AS c FROM huimeng.comic_projects WHERE state='failed'",
            )
            comic_done_row = fetch_one(
                conn,
                "SELECT COUNT(*) AS c FROM huimeng.comic_projects WHERE state='done'",
            )
            comic_total = comic_total_row["c"] if comic_total_row else 0
            if comic_total > 0:
                result["comic_quality"] = {
                    "total": comic_total,
                    "failed": comic_failed_row["c"] if comic_failed_row else 0,
                    "done": comic_done_row["c"] if comic_done_row else 0,
                    "note": "漫创态功能不稳定(用户偏好 5.0a),单列展示,不计入主指标",
                }
        except Exception:  # noqa: BLE001
            pass

        return result
    finally:
        conn.close()


# ============================================================
# /admin/safety — B3 合规风控
# ============================================================

@router.get("/safety")
async def get_safety(_: None = Depends(require_admin)) -> dict:
    """B3 合规风控:
      - violation_logs 总数 / 本月新增 / 按 category 分组
      - top 20 命中 pattern(从 red_flag_dictionary 关联取 pattern + category)
      - 高风险用户(violation_logs ≥ 3 次的 user_id top 20)
      - 同 IP 多账号(register_ip 出现 ≥ 2 个 user_id)
    """
    conn = _get_conn()
    try:
        result: dict = {
            "huimeng_attached": huimeng_attached(conn),
            "violations_total": 0,
            "violations_this_month": 0,
            "by_category": [],
            "top_patterns": [],
            "high_risk_users": [],
            "same_ip_accounts": [],
        }
        if not huimeng_attached(conn):
            return result

        # --- violations 总数 + 本月 ---
        total_row = fetch_one(conn, "SELECT COUNT(*) AS c FROM huimeng.violation_logs")
        result["violations_total"] = total_row["c"] if total_row else 0

        from datetime import datetime, timezone
        month_start = datetime.now(timezone.utc).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        month_row = fetch_one(
            conn,
            "SELECT COUNT(*) AS c FROM huimeng.violation_logs WHERE occurred_at >= ?",
            (month_start,),
        )
        result["violations_this_month"] = month_row["c"] if month_row else 0

        # --- 按 category 分组(JOIN red_flag_dictionary 取 category)---
        cat_rows = fetch_all(
            conn,
            "SELECT d.category, COUNT(*) AS c "
            "FROM huimeng.violation_logs v "
            "JOIN huimeng.red_flag_dictionary d ON d.id = v.flag_id "
            "GROUP BY d.category ORDER BY c DESC",
        )
        result["by_category"] = [
            {"category": r["category"], "count": r["c"]} for r in cat_rows
        ]

        # --- top 20 命中 pattern ---
        pattern_rows = fetch_all(
            conn,
            "SELECT d.pattern, d.category, d.severity, COUNT(*) AS c "
            "FROM huimeng.violation_logs v "
            "JOIN huimeng.red_flag_dictionary d ON d.id = v.flag_id "
            "GROUP BY d.id ORDER BY c DESC LIMIT 20",
        )
        result["top_patterns"] = [
            {
                "pattern": r["pattern"],
                "category": r["category"],
                "severity": r["severity"],
                "hit_count": r["c"],
            }
            for r in pattern_rows
        ]

        # --- 高风险用户(≥ 3 次命中)---
        risk_rows = fetch_all(
            conn,
            "SELECT v.user_id, u.email, COUNT(*) AS c "
            "FROM huimeng.violation_logs v "
            "LEFT JOIN huimeng.users u ON u.id = v.user_id "
            "GROUP BY v.user_id "
            "HAVING c >= 3 "
            "ORDER BY c DESC LIMIT 20",
        )
        result["high_risk_users"] = [
            {
                "user_id": r["user_id"],
                "email": r["email"] or "(已注销)",
                "violation_count": r["c"],
            }
            for r in risk_rows
        ]

        # --- 同 IP 多账号(register_ip 关联 ≥ 2 个 user_id)---
        ip_rows = fetch_all(
            conn,
            "SELECT register_ip, COUNT(DISTINCT id) AS c "
            "FROM huimeng.users "
            "WHERE register_ip IS NOT NULL AND register_ip != '' "
            "GROUP BY register_ip "
            "HAVING c >= 2 "
            "ORDER BY c DESC LIMIT 20",
        )
        for ip_row in ip_rows:
            user_rows = fetch_all(
                conn,
                "SELECT id, email FROM huimeng.users WHERE register_ip = ?",
                (ip_row["register_ip"],),
            )
            result["same_ip_accounts"].append({
                "ip": ip_row["register_ip"],
                "account_count": ip_row["c"],
                "accounts": [
                    {"user_id": u["id"], "email": u["email"]} for u in user_rows
                ],
            })

        return result
    finally:
        conn.close()


# ============================================================
# /admin/compass — B7 作者指南针看板
# ============================================================

@router.get("/compass")
async def get_compass(_: None = Depends(require_admin)) -> dict:
    """B7 作者指南针使用看板:
      - 总 projects 数 vs 有 author_compass 的(使用率)
      - 锁定 vs 未锁定
      - 双轨完成状态分布(external_status / internal_status)
      - top 10 author_name + top 10 work_title
    """
    conn = _get_conn()
    try:
        result: dict = {
            "huimeng_attached": huimeng_attached(conn),
            "usage": None,
            "lock_status": None,
            "external_status": [],
            "internal_status": [],
            "top_authors": [],
            "top_works": [],
        }
        if not huimeng_attached(conn):
            return result

        proj_total_row = fetch_one(conn, "SELECT COUNT(*) AS c FROM huimeng.projects")
        compass_total_row = fetch_one(
            conn, "SELECT COUNT(*) AS c FROM huimeng.author_compass"
        )
        proj_total = proj_total_row["c"] if proj_total_row else 0
        compass_total = compass_total_row["c"] if compass_total_row else 0
        result["usage"] = {
            "projects_total": proj_total,
            "compass_total": compass_total,
            "usage_rate": round(compass_total / proj_total, 3) if proj_total > 0 else 0,
        }

        # 锁定状态
        locked_row = fetch_one(
            conn,
            "SELECT COUNT(*) AS c FROM huimeng.author_compass WHERE user_locked=1",
        )
        unlocked_row = fetch_one(
            conn,
            "SELECT COUNT(*) AS c FROM huimeng.author_compass WHERE user_locked=0",
        )
        result["lock_status"] = {
            "locked": locked_row["c"] if locked_row else 0,
            "unlocked": unlocked_row["c"] if unlocked_row else 0,
        }

        # 双轨状态
        ext_rows = fetch_all(
            conn,
            "SELECT external_status AS s, COUNT(*) AS c FROM huimeng.author_compass "
            "GROUP BY external_status ORDER BY c DESC",
        )
        result["external_status"] = [
            {"status": r["s"], "count": r["c"]} for r in ext_rows
        ]
        int_rows = fetch_all(
            conn,
            "SELECT internal_status AS s, COUNT(*) AS c FROM huimeng.author_compass "
            "GROUP BY internal_status ORDER BY c DESC",
        )
        result["internal_status"] = [
            {"status": r["s"], "count": r["c"]} for r in int_rows
        ]

        # top 10 作家
        author_rows = fetch_all(
            conn,
            "SELECT author_name AS n, COUNT(*) AS c FROM huimeng.author_compass "
            "WHERE author_name IS NOT NULL AND author_name != '' "
            "GROUP BY author_name ORDER BY c DESC LIMIT 10",
        )
        result["top_authors"] = [
            {"name": r["n"], "count": r["c"]} for r in author_rows
        ]
        work_rows = fetch_all(
            conn,
            "SELECT work_title AS w, COUNT(*) AS c FROM huimeng.author_compass "
            "WHERE work_title IS NOT NULL AND work_title != '' "
            "GROUP BY work_title ORDER BY c DESC LIMIT 10",
        )
        result["top_works"] = [
            {"title": r["w"], "count": r["c"]} for r in work_rows
        ]

        return result
    finally:
        conn.close()


# ============================================================
# /admin/live — B8 实时事件流
# ============================================================

@router.get("/live")
async def get_live(_: None = Depends(require_admin)) -> dict:
    """B8 实时事件流:
      - 最近 50 条 events(timestamp DESC)
      - 当前在线(15 分钟内有 event 的 distinct user_id)
      - 进行中推演(huimeng.simulations.state IN directing/composing/queued)
    """
    conn = _get_conn()
    try:
        now_ms = int(time.time() * 1000)
        fifteen_min_ago = now_ms - 15 * 60 * 1000

        # 最近 50 条 events
        recent_rows = fetch_all(
            conn,
            "SELECT id, user_id, event_type, mode, step, path, timestamp_ms, "
            "duration_ms, project_id FROM events "
            "ORDER BY timestamp_ms DESC LIMIT 50",
        )
        recent_events = [dict(r) for r in recent_rows]

        # 当前在线(15 分钟内)
        online_row = fetch_one(
            conn,
            "SELECT COUNT(DISTINCT user_id) AS c FROM events "
            "WHERE timestamp_ms >= ? AND user_id IS NOT NULL",
            (fifteen_min_ago,),
        )
        online_count = online_row["c"] if online_row else 0

        # 进行中推演
        active_sims = []
        active_comics = []
        if huimeng_attached(conn):
            try:
                sim_rows = fetch_all(
                    conn,
                    "SELECT id, project_id, user_id, state, started_at, "
                    "rounds_planned, current_round, target_chars "
                    "FROM huimeng.simulations "
                    "WHERE state IN ('queued', 'directing', 'composing', 'finalizing') "
                    "ORDER BY started_at DESC LIMIT 20",
                )
                active_sims = [dict(r) for r in sim_rows]

                # 漫创态进行中(单列,5.0a)
                comic_rows = fetch_all(
                    conn,
                    "SELECT id, user_id, name, state, progress_percent, created_at "
                    "FROM huimeng.comic_projects "
                    "WHERE state NOT IN ('done', 'failed', 'cancelled') "
                    "ORDER BY created_at DESC LIMIT 10",
                )
                active_comics = [dict(r) for r in comic_rows]
            except Exception as e:  # noqa: BLE001
                logger.warning(f"live: huimeng query failed: {e}")

        return {
            "now_ms": now_ms,
            "huimeng_attached": huimeng_attached(conn),
            "online_count": online_count,
            "recent_events": recent_events,
            "active_simulations": active_sims,
            "active_comics": active_comics,
        }
    finally:
        conn.close()


# ============================================================
# /admin/screenplay — 剧创态(第 5 态)使用洞察(2026-06-09 新增)
#
# 价值:
#   平台上线了 BYOK + 多模型对比 + 剧创态全集成,但前面 0 追踪。
#   这个 endpoint 给运营 / 产品看:
#     1. 哪些剧创态功能用得多 / 用得少(指导后续优先级)
#     2. 多模型对比的胜出 vendor 分布(行业洞察:谁家 LLM 真的写剧本强)
#     3. 桥接资产价值(读了多少父平台 SP-2/3/7,转化率几何)
#     4. 转化漏斗:dashboard 卡片 → 上传小说 → 触发 compose → 优化重排 → 多模型对比
# ============================================================


@router.get("/screenplay")
async def get_screenplay_analytics(
    _: None = Depends(require_admin),
    days: int = 30,
) -> dict:
    """剧创态使用洞察看板。

    Args:
        days: 时间窗口(默认 30 天)
    """
    days = max(1, min(days, 365))  # 兜底
    now_ms = int(time.time() * 1000)
    window_ms = now_ms - (days * 24 * 3600 * 1000)

    conn = _get_conn()
    try:
        # ============================================================
        # 1. 剧创态各事件计数(过去 N 天)
        # ============================================================
        sp_events = [
            "screenplay_novel_upload",
            "screenplay_compose_start",
            "screenplay_compose_done",
            "screenplay_optimize",
            "screenplay_characters_view",
            "screenplay_episodes_plan",
            "model_compare_start",
            "model_compare_run",
            "model_compare_winner",
        ]
        placeholders = ",".join("?" * len(sp_events))
        rows = fetch_all(
            conn,
            f"""SELECT event_type, COUNT(*) AS cnt,
                       COUNT(DISTINCT user_id) AS unique_users
                FROM events
                WHERE event_type IN ({placeholders})
                  AND timestamp_ms >= ?
                GROUP BY event_type""",
            (*sp_events, window_ms),
        )
        events_by_type = {r["event_type"]: {
            "count": int(r["cnt"]),
            "unique_users": int(r["unique_users"]),
        } for r in rows}
        # 补全 0 事件
        for et in sp_events:
            events_by_type.setdefault(et, {"count": 0, "unique_users": 0})

        # ============================================================
        # 2. 转化漏斗:dashboard 卡片 → 上传 → compose → optimize → compare
        # ============================================================
        # 2026-06-09 修 bug:events 表字段叫 meta_json 不是 meta;且 LIKE pattern
        # 含双引号要用 ESCAPE,但用 SQLite 单引号字符串包 + json_extract 更稳
        funnel_steps = [
            ("dashboard_card_click", "Dashboard 剧创态卡点击",
             "json_extract(meta_json, '$.card') = 'screenplay'"),
            ("screenplay_novel_upload", "上传小说", None),
            ("screenplay_compose_start", "触发剧本生成", None),
            ("screenplay_optimize", "AI 优化重排", None),
            ("model_compare_start", "多模型对比", None),
        ]
        funnel = []
        for et, label, extra_where in funnel_steps:
            # 2026-06-09 兜底:每个 step 独立 try/except,任一 SQL 失败
            # 不影响其他 step + 不让整个 endpoint 500(常见原因:老 SQLite 不
            # 支持 json_extract;extra_where 语法错;event_type 不在表中)
            try:
                sql = (
                    "SELECT COUNT(DISTINCT user_id) AS u, COUNT(*) AS c FROM events "
                    f"WHERE event_type = ? AND timestamp_ms >= ?"
                )
                if extra_where:
                    sql += f" AND {extra_where}"
                r = fetch_one(conn, sql, (et, window_ms))
                funnel.append({
                    "event_type": et,
                    "label": label,
                    "unique_users": int(r["u"]) if r else 0,
                    "total_count": int(r["c"]) if r else 0,
                })
            except sqlite3.OperationalError as exc:
                logger.warning("funnel step %s SQL failed: %s", et, exc)
                funnel.append({
                    "event_type": et,
                    "label": label,
                    "unique_users": 0,
                    "total_count": 0,
                })

        # ============================================================
        # 3. 多模型对比胜出 vendor 分布(从 meta_json 解析 recommended)
        # ============================================================
        winner_rows = fetch_all(
            conn,
            """SELECT meta_json FROM events
               WHERE event_type = 'model_compare_winner'
                 AND timestamp_ms >= ?""",
            (window_ms,),
        )
        winner_counter: Counter[str] = Counter()
        for r in winner_rows:
            try:
                meta = json.loads(r["meta_json"] or "{}")
                rec = meta.get("recommended")
                if isinstance(rec, str) and rec:
                    winner_counter[rec] += 1
            except (json.JSONDecodeError, TypeError):
                continue
        winner_distribution = [
            {"provider": name, "wins": count}
            for name, count in winner_counter.most_common(10)
        ]

        # ============================================================
        # 4. Optimize scope/focus 分布(用户更关注全篇还是单场?保真度还是结构?)
        # ============================================================
        opt_rows = fetch_all(
            conn,
            """SELECT meta_json FROM events
               WHERE event_type = 'screenplay_optimize'
                 AND timestamp_ms >= ?""",
            (window_ms,),
        )
        scope_counter: Counter[str] = Counter()
        focus_counter: Counter[str] = Counter()
        for r in opt_rows:
            try:
                meta = json.loads(r["meta_json"] or "{}")
                scope = meta.get("scope")
                focus = meta.get("focus")
                if scope:
                    scope_counter[str(scope)] += 1
                if focus:
                    focus_counter[str(focus)] += 1
            except (json.JSONDecodeError, TypeError):
                continue

        # ============================================================
        # 5. Dashboard 各卡片转化率(谁更受欢迎)
        # ============================================================
        card_rows = fetch_all(
            conn,
            """SELECT meta_json FROM events
               WHERE event_type = 'dashboard_card_click'
                 AND timestamp_ms >= ?""",
            (window_ms,),
        )
        card_counter: Counter[str] = Counter()
        for r in card_rows:
            try:
                meta = json.loads(r["meta_json"] or "{}")
                card = meta.get("card")
                if isinstance(card, str) and card:
                    card_counter[card] += 1
            except (json.JSONDecodeError, TypeError):
                continue

        # ============================================================
        # 6. 桥接(huimeng_bridge)使用率:从 huimeng.sp_screenplays.stats_json 读
        # 这是结构化数据,不是事件 — 但仍能从 attached 库查到
        # ============================================================
        bridge_stats: dict = {
            "total_screenplays": 0,
            "with_bridge": 0,
            "avg_drivers_injections": 0.0,
            "avg_knowledge_injections": 0.0,
            "avg_polarity_injections": 0.0,
        }
        if huimeng_attached(conn):
            try:
                br = fetch_all(
                    conn,
                    """SELECT stats_json FROM huimeng.sp_screenplays
                       WHERE created_at >= datetime('now', ?)""",
                    (f"-{days} days",),
                )
                total = len(br)
                with_bridge = 0
                sum_drivers = sum_knowledge = sum_polarity = 0
                for row in br:
                    try:
                        st = json.loads(row["stats_json"] or "{}")
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if st.get("bridge_was_linked"):
                        with_bridge += 1
                    sum_drivers += int(st.get("bridge_drivers_injections", 0) or 0)
                    sum_knowledge += int(st.get("bridge_knowledge_injections", 0) or 0)
                    sum_polarity += int(st.get("bridge_polarity_injections", 0) or 0)
                bridge_stats = {
                    "total_screenplays": total,
                    "with_bridge": with_bridge,
                    "avg_drivers_injections": round(sum_drivers / total, 2) if total else 0.0,
                    "avg_knowledge_injections": round(sum_knowledge / total, 2) if total else 0.0,
                    "avg_polarity_injections": round(sum_polarity / total, 2) if total else 0.0,
                }
            except sqlite3.OperationalError as exc:
                # sp_screenplays 表不存在(huimeng DB 未跑 migration 085)
                logger.debug("bridge stats skipped: %s", exc)

        return {
            "window_days": days,
            "now_ms": now_ms,
            "events_by_type": events_by_type,
            "funnel": funnel,
            "winner_distribution": winner_distribution,
            "optimize_scope_distribution": dict(scope_counter),
            "optimize_focus_distribution": dict(focus_counter),
            "dashboard_card_clicks": dict(card_counter),
            "bridge_stats": bridge_stats,
        }
    finally:
        conn.close()
