"""配额闸门(资源容量类硬限) — Sprint C.1(2026-05-13)credit 重构后。

⚠️ **本文件历史背景**:Sprint C.1 之前,本文件含 4 类 AI "次数" 配额(refine / continuation
/ extract / comic),按次记账走 usage_logs。Sprint C.1 用户拍板:**激进重构为 credit 单位制**
彻底删除"次数"模式,AI 调用配额由 `credit_service.py` 接管。

**Sprint 5.B(2026-05-18)漫画态降级** — 漫画态实测产物质量未达 ship 标准,战略性降级:
  - 漫画态从"按 credit 消耗"重新改回"按订阅福利免费次数"
  - 理由:Pro 970c 跑不到 1 本完整漫画(成本 ¥36-58),信号给用户"漫画就是贵",体验灾难
  - (5.B 设计已在 ECON-1 改回:漫创态彻底改为"单买漫画包",不再送)

**Sprint ECON-1(2026-05-27 末⁴)订阅模式重设** — 用户拍板"中期商业模式过时,重新计算":
  - 数据摸底发现:旧模式 Pro 单 credit 真实毛利仅 7%(售价 ¥0.14 / 真实 ¥0.13)
  - 旧模式漫创态每个 Pro 用户每月贴 ¥18-30(图像生成成本不变,平台亏本送)
  - 重设方向(用户全选推荐):50% 毛利 / 600/2000/6500 配额 / 漫画包单买 ¥30 / 年付 -15%
  - 漫创态 comics_per_month **全档清零**(0/0/0/0),改为单买"漫画包"机制
  - 单 credit 售价 ¥0.23/0.22/0.21(月付反算,实际毛利约 44%)

本文件保留以下职责:
  - **资源容量**类配额(reshape_max_percent / characters_per_project / projects_total)
  - **漫画态次数**配额(comics_per_month) — Sprint 5.B 新增,ECON-1 全档清零

订阅模式 v5(ECON-1,2026-05-27 末⁴):
  | 维度                          | Free | Pro(¥138)| Max(¥438)| 超级 Max(¥1388)|
  |-------------------------------|------|-----------|-----------|------------------|
  | monthly_credits_quota         |  20  |    600    |   2000    |       6500       |
  | single_credit_price_cents (分)|   0  |    23     |    22     |        21        |
  | characters_per_project        |  10  |    30     |    50     |       100        |
  | projects_total                |   2  |     5     |    20     |      999999      |
  | reshape_max_percent           |  30  |    80     |    90     |        90        |
  | comics_per_month (ECON-1 清零)|   0  |     0     |     0     |         0        |

  年付定价(ECON-1 -15%):Pro ¥1407.60 / Max ¥4467.60 / 超级 Max ¥14157.60(月付 × 12 × 0.85)

  关键设计(ECON-1 新铁律):
    - 单 credit 售价随档位递减(Pro 基准 / Max -4.3% / 超级 -8.7%)
    - 漫创态不送 — 要玩单买"漫画包"¥30/次,有效期 6 月(配套机制下个 sprint 实现)
    - 单 credit 真实成本 ¥0.13(token×0.013/0.026 反推)→ Pro 毛利 44%(满配额)
    - 实跑 30% 假设下毛利 80%+(因配额过剩,大多数用户用不满)

异常 QuotaExceeded:reshape_percent / projects_total / characters_per_project /
  **comics_per_month**(Sprint 5.B 新增)触发
  AI 类配额超额由 credit_service.InsufficientCredits 处理(漫画态除外)。
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from app.db import fetch_one


@dataclass(frozen=True)
class PlanLimits:
    """订阅档配额(非 AI 类硬限 + credit 池声明 + 漫画次数池)。

    Sprint C.1:绝大多数 AI 调用走 credit_service.consume_credits 走 user_credit_balances 钱包。
    Sprint 5.B(2026-05-18):**漫画态独立次数池**,不走 credit;仅本字段 comics_per_month 控制。
    """
    # ===== Credit 池(C.1 新增,真实分配由 credit_service 管)=====
    monthly_credits_quota:    int    # 月度发放数(订阅 wallet)
    single_credit_price_cents: int   # 单 credit 售价(分,加购包 / 计费快照用)

    # ===== 非 AI 类硬限(纯资源容量,不消耗 credit)=====
    characters_per_project: int
    projects_total:         int
    reshape_max_percent:    int

    # ===== Sprint 5.B 漫画次数池(独立于 credit,2026-05-18 加)=====
    # 漫画态月度免费次数:Free 0(看 chip 解锁)/ Pro 1 / Max 2 / 超级 Max 4
    # 计数策略:created_at 在本月 + state NOT IN (failed, cancelled)= 占用
    # 超额:enforce_comic_count_quota 抛 QuotaExceeded(kind='comics_per_month'),
    #       前端 catch 弹 UpgradeModal 引导升档(不允许加购包补充 — YAGNI)
    comics_per_month:       int


# 4 档配额定义(Sprint 5.B,2026-05-18,加 comics_per_month)— 改动这里影响全平台,改前同步:
#   - frontend/src/api/types.ts QuotaLimits / CreditBalance
#   - frontend/src/constants/legal-docs.ts 服务等级章节
#   - docs/ADR_credit_quota_重构.md §3
PLAN_LIMITS: dict[str, PlanLimits] = {
    "free": PlanLimits(
        monthly_credits_quota=20,        # ECON-1:30 → 20(只够试用,约 3 次中等推演)
        single_credit_price_cents=0,     # free 不卖 credit(只发)
        characters_per_project=10,
        projects_total=2,
        reshape_max_percent=30,
        comics_per_month=0,              # 不送(原本就是 0)
    ),
    "pro": PlanLimits(
        # ¥138/月 ¥1407.60/年 — 基准档,单 credit ¥0.23(成本 ¥0.13 × 1.77)
        # 满配额毛利 44% / 实跑 30% 假设下毛利 80%+
        monthly_credits_quota=600,       # ECON-1:970 → 600(约 90 次中等推演)
        single_credit_price_cents=23,    # ECON-1:14 → 23(¥0.23,月付反算 138/600)
        characters_per_project=30,
        projects_total=5,
        reshape_max_percent=80,
        comics_per_month=0,              # ECON-1:1 → 0(漫画单买,详见 comic_pack)
    ),
    "max": PlanLimits(
        # ¥438/月 ¥4467.60/年 — 单 credit ¥0.22(-4.3% 比 Pro)
        # 满配额毛利 41%
        monthly_credits_quota=2000,      # ECON-1:3400 → 2000(约 300 次中等推演)
        single_credit_price_cents=22,    # ECON-1:13 → 22(¥0.22,月付反算 438/2000)
        characters_per_project=50,
        projects_total=20,
        reshape_max_percent=90,
        comics_per_month=0,              # ECON-1:2 → 0
    ),
    "super_max": PlanLimits(
        # ¥1388/月 ¥14157.60/年 — 单 credit ¥0.21(-8.7% 比 Pro)
        # 满配额毛利 39%
        monthly_credits_quota=6500,      # ECON-1:12000 → 6500(约 1000 次中等推演)
        single_credit_price_cents=21,    # ECON-1:12 → 21(¥0.21,月付反算 1388/6500)
        characters_per_project=100,
        projects_total=999999,
        reshape_max_percent=90,
        comics_per_month=0,              # ECON-1:4 → 0
    ),
    # 创始人专用档 — config.founder_emails 命中的邮箱在 deps.get_current_user
    # 内存改写为此 plan(不写库,DB 仍是 free/pro/max/super_max 之一)。
    "founder": PlanLimits(
        monthly_credits_quota=9999999,
        single_credit_price_cents=12,
        characters_per_project=999999,
        projects_total=999999,
        reshape_max_percent=90,
        comics_per_month=999999,         # 创始人无漫画次数限制
    ),
}


class QuotaExceeded(Exception):
    """资源容量超限。kind ∈ {reshape_percent / projects_total / characters_per_project}。

    AI 调用类配额超额由 credit_service.InsufficientCredits 处理(独立异常)。
    """

    def __init__(self, kind: str, used: int, limit: int, plan: str):
        super().__init__(
            f"{kind} 配额已用尽 ({used}/{limit}),当前 {plan} 档"
        )
        self.kind = kind
        self.used = used
        self.limit = limit
        self.plan = plan


# ============================================================
# 时间工具(credit_service 也会用)
# ============================================================

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def month_start_iso() -> str:
    """本月起点 UTC ISO(月初 00:00:00)。公开给 credit_service.month_reset 用。"""
    now = datetime.now(timezone.utc)
    return (
        now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    )


# ============================================================
# 状态查询(非 AI 类硬限的 used 计算)
# ============================================================

def _count_projects(conn: sqlite3.Connection, user_id: str) -> int:
    row = fetch_one(
        conn,
        "SELECT COUNT(*) AS cnt FROM projects WHERE user_id=?",
        (user_id,),
    )
    return int(row["cnt"]) if row else 0


def _count_characters(conn: sqlite3.Connection, project_id: str) -> int:
    row = fetch_one(
        conn,
        "SELECT COUNT(*) AS cnt FROM characters WHERE project_id=?",
        (project_id,),
    )
    return int(row["cnt"]) if row else 0


# ============================================================
# Plan limits 查询入口
# ============================================================

def get_plan_limits(plan: str) -> PlanLimits:
    return PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])


def get_plan_limits_for_user(
    conn: sqlite3.Connection, user_id: str, plan_fallback: str,
) -> PlanLimits:
    """配额查询入口 — 优先查 user_plan_snapshots active 快照(老用户老规则)。

    无快照(新用户从未付费 / founder)→ fallback 全局 PLAN_LIMITS[plan_fallback]。

    Sprint C.1 注:快照表 schema 已 migration 022 v2 重构为 credit 字段;
    若快照含老的 *_per_month 字段(理论不可能,因 C.1 前无付费用户),取 fallback。

    Sprint 5.B(2026-05-18):**comics_per_month 不冻结进快照** — schema 不加列保 YAGNI;
    snapshot 用户的漫画次数始终读最新 PLAN_LIMITS[snapshot.plan].comics_per_month。
    理由:① 当前无付费用户,无"老规则"包袱;② 漫画次数若涨配额,
    老用户跟着新规则走是产品友好(不会被冻结到 0);③ 未来真要冻结再加 migration。
    """
    from app.services.billing_service import fetch_active_or_pending_for_user
    snapshot = fetch_active_or_pending_for_user(conn, user_id)
    if snapshot is not None:
        # 漫画次数从快照的 plan 字段反查最新 PLAN_LIMITS(不冻结)
        comics_quota = PLAN_LIMITS.get(
            snapshot.plan, PLAN_LIMITS["free"],
        ).comics_per_month
        return PlanLimits(
            monthly_credits_quota=snapshot.monthly_credits_quota,
            single_credit_price_cents=snapshot.single_credit_price_cents,
            characters_per_project=snapshot.characters_per_project,
            projects_total=snapshot.projects_total,
            reshape_max_percent=snapshot.reshape_max_percent,
            comics_per_month=comics_quota,
        )
    return PLAN_LIMITS.get(plan_fallback, PLAN_LIMITS["free"])


# ============================================================
# 资源容量类闸门(非 AI 调用,不消耗 credit)
# ============================================================

def enforce_reshape_quota(
    plan: str, requested_percent: int,
    conn: Optional[sqlite3.Connection] = None,
    user_id: Optional[str] = None,
) -> None:
    """重塑度上限闸门 — Sprint 1.H + E.4 加快照优先。

    超出 → 429 + kind="reshape_percent",前端 catch 弹 UpgradeModal。
    """
    if conn is not None and user_id is not None:
        limits = get_plan_limits_for_user(conn, user_id, plan)
    else:
        limits = get_plan_limits(plan)
    if requested_percent > limits.reshape_max_percent:
        raise QuotaExceeded(
            "reshape_percent",
            requested_percent,
            limits.reshape_max_percent,
            plan,
        )


def enforce_project_create_quota(
    conn: sqlite3.Connection, user_id: str, plan: str
) -> None:
    """E.4 加快照优先。"""
    limits = get_plan_limits_for_user(conn, user_id, plan)
    used = _count_projects(conn, user_id)
    if used >= limits.projects_total:
        raise QuotaExceeded(
            "projects_total", used, limits.projects_total, plan
        )


def enforce_character_create_quota(
    conn: sqlite3.Connection, project_id: str, plan: str,
    user_id: Optional[str] = None,
) -> None:
    """E.4 加快照优先(可选 user_id;不传则用全局 PLAN_LIMITS)。"""
    if user_id is not None:
        limits = get_plan_limits_for_user(conn, user_id, plan)
    else:
        limits = get_plan_limits(plan)
    used = _count_characters(conn, project_id)
    if used >= limits.characters_per_project:
        raise QuotaExceeded(
            "characters_per_project",
            used,
            limits.characters_per_project,
            plan,
        )


# ============================================================
# Sprint 5.B(2026-05-18):漫画次数池闸门 — 独立于 credit
# ============================================================

def _count_user_comics_this_month(
    conn: sqlite3.Connection, user_id: str,
) -> int:
    """统计用户本月已占用的漫画次数。

    计数规则:
      - created_at >= month_start_iso()
      - state NOT IN ('failed', 'cancelled')
        → 失败 / 取消不算占用(用户友好,失败不二次惩罚)
      - 所有进行中状态(queued / scripting / generating / composing 等)+ done 都算占用

    Returns: int,本月占用次数;无任何漫画时返回 0。
    """
    month_start = month_start_iso()
    row = fetch_one(
        conn,
        """SELECT COUNT(*) AS cnt FROM comic_projects
           WHERE user_id=? AND created_at >= ?
             AND state NOT IN ('failed', 'cancelled')""",
        (user_id, month_start),
    )
    return int(row["cnt"]) if row else 0


def enforce_comic_count_quota(
    conn: sqlite3.Connection, user_id: str, plan: str,
) -> None:
    """漫画次数闸门 — 创建漫画前调,超额抛 QuotaExceeded.

    ECON-2(2026-05-27 末⁴⁴)双轨:
      1. **漫画包优先**:user 有 ≥ 1 个有效未用 comic_pack_lot → 放过(不抛错)
      2. **PLAN_LIMITS.comics_per_month 兜底**:仍走老配额(目前 free/pro/max/super 全 0,
         仅 founder=999999 走老路径)

    Sprint 5.B(2026-05-18)初版:订阅福利免费次数(Free 0 / Pro 1 / Max 2 / 超级 4).
    Sprint ECON-1(2026-05-27 末⁴)配额全档清零,改为单买漫画包.
    Sprint ECON-2(2026-05-27 末⁴⁴)本闸门加漫画包检查.

    Raises:
      QuotaExceeded(kind='comics_per_month'):无漫画包 AND 本月配额已用完
    """
    # ECON-2:漫画包检查(在 PLAN_LIMITS 之前)
    from app.services.credit_service import count_available_comic_packs
    if count_available_comic_packs(conn, user_id) > 0:
        return  # 有有效漫画包,放过

    # 旧路径(founder 等保留通道)
    limits = get_plan_limits_for_user(conn, user_id, plan)
    used = _count_user_comics_this_month(conn, user_id)
    if used >= limits.comics_per_month:
        raise QuotaExceeded(
            "comics_per_month",
            used,
            limits.comics_per_month,
            plan,
        )
