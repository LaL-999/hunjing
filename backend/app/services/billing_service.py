"""订阅 / 价格快照服务 — Sprint E.4。

核心职责:管理 user_plan_snapshots 表 — 落地协议第三章"老用户老规则价格保护"承诺。

状态机:
  Free (无快照) ──[subscribe]──→ active(创建新快照)
                                  │
                                  ├─[cancel]──→ cancelled(配额仍到 period_end)
                                  │                 │
                                  │              [周期满]
                                  │                 │
                                  │                 ↓
                                  │              expired
                                  │
                                  └─[upgrade(用户同意新规则)]
                                       老快照 → upgraded(归档)
                                       + 新建 snapshot active

接入支付通道(未来):
  Stripe / 微信支付 / 支付宝 webhook → 调 subscribe(user_id, plan, cycle)
  (当前 sprint 仅落库 service,API 端点也只能 mock 触发,真生效在支付通道接入后)

配额查询联动:
  quota_service.get_plan_limits_for_user 优先查 active snapshot,
  无快照(free / founder / 未付费过)再 fallback PLAN_LIMITS[user.plan]
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.models.user_plan_snapshot import (
    UserPlanSnapshot,
    VALID_BILLING_CYCLES,
    VALID_PAID_PLANS,
)
from app.services.project_service import ResourceNotFoundOrForbidden
from app.services.quota_service import PLAN_LIMITS


# ============================================================
# 价格表(订阅模式 v4,Sprint ECON-1 重新计算,2026-05-27 末⁴)— 单位:分
# ============================================================
# 修改这里的价格 → **必须**同步:
#   - frontend/src/components/UpgradeModal.vue (PLANS price 字段)
#   - frontend/src/constants/legal-docs.ts (协议第三章服务等级)
#   - docs/ADR_credit_quota_重构.md §3
# 老订阅用户不受此处变更影响(由快照机制兜底)
#
# v4 关键变更(ECON-1,2026-05-27 末⁴)— 用户拍板"50% 毛利 + 600c/2000c/6500c 配额":
#   - 月付价不变(¥138 / ¥438 / ¥1388)— 用户对月费数字最敏感
#   - 年付从 -10% 升级到 -15%(月付 × 12 × 0.85)— 锁定现金流 + 拉新福利
#   - 配额从 970/3400/12000 收紧到 600/2000/6500(quota_service.PLAN_LIMITS)
#   - 单 credit 售价从 ¥0.14/0.13/0.12 升到 ¥0.23/0.22/0.21(实际毛利 44%+)
#   - 漫创态从订阅福利改为"单买漫画包 ¥30/次"(comics_per_month 全档清零)
# 详见 memory/session_tech_decisions_20260526.md 第十一章 ECON-1
PLAN_PRICE_CENTS: dict[str, dict[str, int]] = {
    "pro": {
        "monthly": 13800,    # ¥138(v3 不变)
        "yearly":  140760,   # ¥1407.60(月付 × 12 × 0.85,v3 ¥1488 → -15%)
    },
    "max": {
        "monthly": 43800,    # ¥438(v3 不变)
        "yearly":  446760,   # ¥4467.60(月付 × 12 × 0.85,v3 ¥4728 → -15%)
    },
    "super_max": {
        "monthly": 138800,   # ¥1388(v3 不变)
        "yearly":  1415760,  # ¥14157.60(月付 × 12 × 0.85,v3 ¥14988 → -15%)
    },
}


# ============================================================
# 异常
# ============================================================

class InvalidSubscribePlan(Exception):
    """plan 不在 VALID_PAID_PLANS / billing_cycle 不在 VALID_BILLING_CYCLES。"""


class AlreadyHasActiveSnapshot(Exception):
    """用户已有 active snapshot,不能重复 subscribe(必须先 cancel 或 upgrade)。"""


class NoActiveSnapshot(Exception):
    """用户没有 active snapshot,无法 cancel / upgrade。"""


# ============================================================
# 工具
# ============================================================

def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _compute_period_end(
    start_iso: str, billing_cycle: str
) -> str:
    """计算订阅周期止时刻。

    monthly:30 天后(简化 — 月度按 30 日固定,不按自然月。理由:跨月日期处理复杂,
                     业界 SaaS 大多按 30 日;若用户在 1/31 订阅,2/28 续费不困扰)
    yearly:365 天后(简化 — 不闰年)
    """
    start = datetime.fromisoformat(start_iso)
    if billing_cycle == "monthly":
        end = start + timedelta(days=30)
    elif billing_cycle == "yearly":
        end = start + timedelta(days=365)
    else:
        raise InvalidSubscribePlan(f"未知 billing_cycle: {billing_cycle}")
    return end.replace(microsecond=0).isoformat()


# ============================================================
# 查询
# ============================================================

def fetch_active_snapshot(
    conn: sqlite3.Connection, user_id: str
) -> Optional[UserPlanSnapshot]:
    """拿用户当前 active snapshot(per user 最多 1 行 active)。

    配额查询热路径 — quota_service.get_plan_limits_for_user 每次都调,确保走索引
    (idx_user_plan_snapshots_user_state)。
    """
    row = fetch_one(
        conn,
        "SELECT * FROM user_plan_snapshots "
        "WHERE user_id=? AND state='active' "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id,),
    )
    if not row:
        return None
    return UserPlanSnapshot.from_row(row)


# ============================================================
# ECON-1.4 优惠规则(首月 5 折 + 6 月价保,2026-05-27 末⁴)
# ============================================================

# 6 月价保的窗口期(天)— 用户 cancel/expired 后 180 天内重订享老价
GRANDFATHER_GRACE_PERIOD_DAYS = 180


def is_first_subscription(
    conn: sqlite3.Connection, user_id: str
) -> bool:
    """ECON-1.4 首月 5 折判定:用户是否从未订阅过?

    用 snapshot 历史 count 判定 — 任何状态(active/cancelled/upgraded/expired)
    的 snapshot 都算"订阅过",彻底防 abuse(连续注销重注是 anti-pattern 不享 promo).

    Returns:
        True:从未订阅过(适用首月 5 折)
        False:有过任何订阅历史(不适用)
    """
    row = fetch_one(
        conn,
        "SELECT COUNT(*) AS c FROM user_plan_snapshots WHERE user_id=?",
        (user_id,),
    )
    return (row["c"] if row else 0) == 0


def find_grandfather_price_cents(
    conn: sqlite3.Connection,
    user_id: str,
    plan: str,
    billing_cycle: str,
    grace_period_days: int = GRANDFATHER_GRACE_PERIOD_DAYS,
) -> Optional[int]:
    """ECON-1.4 老用户 6 月价保:查最近 180 天内是否有相同 plan+cycle 的老 snapshot.

    规则:
      - 找用户最近 `grace_period_days` 天内 state IN (cancelled/upgraded/expired) 的 snapshot
      - 必须 plan + billing_cycle 完全匹配(不跨档保价 — 用户主动选了新档就用新价)
      - 命中返回那个 snapshot 的 price_cents(沿用老价)
      - 未命中返回 None(用当前 PLAN_PRICE_CENTS)

    设计:
      - 用户从 cancel 到重订 180 天窗口内享老价 — 鼓励"过来看看",降低流失成本
      - 升级到不同档位的用户视为接受新规则,不享老价(避免 abuse "先订 super 再降 pro 享老 super 价")

    Returns:
        价格分数(int),命中老价
        None,未命中(走当前 PLAN_PRICE_CENTS)
    """
    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(days=grace_period_days)
    ).replace(microsecond=0).isoformat()
    row = fetch_one(
        conn,
        "SELECT price_cents FROM user_plan_snapshots "
        "WHERE user_id=? AND plan=? AND billing_cycle=? "
        "  AND state IN ('cancelled', 'upgraded', 'expired') "
        "  AND updated_at >= ? "
        "ORDER BY updated_at DESC LIMIT 1",
        (user_id, plan, billing_cycle, cutoff_iso),
    )
    return row["price_cents"] if row else None


def compute_subscribe_price_cents(
    conn: sqlite3.Connection,
    user_id: str,
    plan: str,
    billing_cycle: str,
) -> tuple[int, str]:
    """ECON-1.4 订阅价格计算(整合 3 套规则,按对用户最有利的应用).

    优先级(取最低价对用户最有利):
      1. 老用户 6 月价保(grandfather)— 沿用 180 天内的老价(可能更低)
      2. 首月 5 折(first_month_promo)— 当前 PLAN_PRICE_CENTS × 0.5
      3. 当前全价(fallback)

    Returns:
        (final_price_cents, applied_rule)
        applied_rule ∈ {'grandfather', 'first_month_promo', 'full_price'}
    """
    full_price = PLAN_PRICE_CENTS[plan][billing_cycle]
    candidates: list[tuple[int, str]] = [(full_price, "full_price")]

    # 首月 5 折(只对从未订阅过的用户)
    if is_first_subscription(conn, user_id):
        candidates.append((full_price // 2, "first_month_promo"))

    # 6 月价保(同 plan+cycle 的老 snapshot)
    grandfather_price = find_grandfather_price_cents(
        conn, user_id, plan, billing_cycle
    )
    if grandfather_price is not None:
        candidates.append((grandfather_price, "grandfather"))

    # 选最低价(对用户最有利)
    return min(candidates, key=lambda x: x[0])


def fetch_snapshot_history(
    conn: sqlite3.Connection, user_id: str
) -> list[UserPlanSnapshot]:
    """拿用户所有快照(active + cancelled + expired + upgraded),用于"我的订阅历史"展示。

    按 created_at DESC,最新在前。
    """
    rows = fetch_all(
        conn,
        "SELECT * FROM user_plan_snapshots "
        "WHERE user_id=? ORDER BY created_at DESC",
        (user_id,),
    )
    return [UserPlanSnapshot.from_row(r) for r in rows]


# ============================================================
# 状态变更
# ============================================================

def subscribe(
    conn: sqlite3.Connection,
    user_id: str,
    plan: str,
    billing_cycle: str,
    notes: Optional[str] = None,
) -> UserPlanSnapshot:
    """用户付费订阅 → 创建 active snapshot(冻结当时 PLAN_LIMITS + 价格)。

    Args:
        plan: 'pro' / 'max' / 'super_max'(free / founder 不能订阅)
        billing_cycle: 'monthly' / 'yearly'
        notes: 内部备注(支付通道接入后存 stripe charge id / 微信 trade_no 等)

    Raises:
        InvalidSubscribePlan       — plan 或 cycle 非法
        AlreadyHasActiveSnapshot   — 用户已有 active,必须先 cancel 或 upgrade
    """
    if plan not in VALID_PAID_PLANS:
        raise InvalidSubscribePlan(
            f"plan 必须为 {VALID_PAID_PLANS} 之一,实际:{plan}"
        )
    if billing_cycle not in VALID_BILLING_CYCLES:
        raise InvalidSubscribePlan(
            f"billing_cycle 必须为 {VALID_BILLING_CYCLES} 之一,实际:{billing_cycle}"
        )

    # 防重复订阅(必须先 cancel / upgrade 旧的)
    existing = fetch_active_snapshot(conn, user_id)
    if existing is not None:
        raise AlreadyHasActiveSnapshot(
            f"用户 {user_id} 已有 active snapshot({existing.plan} / {existing.billing_cycle}),"
            f"必须先 cancel 或 upgrade"
        )

    # 价格 — ECON-1.4 价格计算(整合首月 5 折 + 6 月价保 + 全价,取对用户最有利价)
    price_cents, applied_rule = compute_subscribe_price_cents(
        conn, user_id, plan, billing_cycle
    )
    # 把优惠规则写入 notes,审计可追(支付通道接入后改成 metadata_json 字段)
    if applied_rule != "full_price":
        rule_note = f"[ECON-1.4 {applied_rule}] 优惠价 ¥{price_cents / 100:.2f}"
        notes = f"{notes}\n{rule_note}" if notes else rule_note

    # 配额 — 从全局 PLAN_LIMITS 取(订阅时点的配额,会被冻结)
    limits = PLAN_LIMITS[plan]

    now = _iso_now()
    period_end = _compute_period_end(now, billing_cycle)

    snapshot_id = str(uuid.uuid4())
    execute(
        conn,
        "INSERT INTO user_plan_snapshots "
        "(id, user_id, plan, billing_cycle, price_cents, "
        " monthly_credits_quota, single_credit_price_cents, "
        " characters_per_project, projects_total, reshape_max_percent, "
        " grandfather_at, current_period_start, current_period_end, "
        " state, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "        'active', ?, ?, ?)",
        (
            snapshot_id, user_id, plan, billing_cycle, price_cents,
            limits.monthly_credits_quota, limits.single_credit_price_cents,
            limits.characters_per_project, limits.projects_total,
            limits.reshape_max_percent,
            now, now, period_end,
            notes, now, now,
        ),
    )
    conn.commit()

    # 同步 users.plan(让 deps 等老路径仍能用 user.plan)
    execute(
        conn,
        "UPDATE users SET plan=?, updated_at=? WHERE id=?",
        (plan, now, user_id),
    )
    conn.commit()

    # Sprint C.1:订阅落地后立即发放月度 credit(grant 触发 wallet 充值)
    # 局部 import 避免循环依赖
    from app.services.credit_service import grant_subscription_credits
    grant_subscription_credits(conn, user_id, plan, reason="new_subscription")

    row = fetch_one(
        conn, "SELECT * FROM user_plan_snapshots WHERE id=?", (snapshot_id,)
    )
    return UserPlanSnapshot.from_row(row)


def cancel(conn: sqlite3.Connection, user_id: str) -> UserPlanSnapshot:
    """用户主动取消订阅 — state='cancelled',配额仍生效到 current_period_end。

    Raises:
        NoActiveSnapshot — 用户没有 active snapshot
    """
    snapshot = fetch_active_snapshot(conn, user_id)
    if snapshot is None:
        raise NoActiveSnapshot(f"用户 {user_id} 没有 active snapshot,无法取消")

    now = _iso_now()
    execute(
        conn,
        "UPDATE user_plan_snapshots SET state='cancelled', updated_at=? "
        "WHERE id=?",
        (now, snapshot.id),
    )
    conn.commit()
    # 注意:这里**不**改 users.plan — 用户在 cancelled 期间(到 period_end 之前)
    # 仍享受老 plan 配额。period_end 到了后由后台 cron(E 阶段做)转 expired + 改 users.plan=free

    return fetch_active_or_pending_for_user(conn, user_id) or snapshot


def upgrade(
    conn: sqlite3.Connection,
    user_id: str,
    new_plan: str,
    new_billing_cycle: str,
    notes: Optional[str] = None,
) -> UserPlanSnapshot:
    """用户主动同意新规则 / 改档位 — 老快照 state='upgraded',创建新 active 快照。

    重要:upgrade 用户**明确接受新版 PLAN_LIMITS + 新价格**;否则用户应该走 cancel + 重订(?)。
    设计选择:upgrade 是用户主动行为,意味着同意新规则,无需 30 日公告(老规则保护让位)。
    """
    if new_plan not in VALID_PAID_PLANS:
        raise InvalidSubscribePlan(
            f"plan 必须为 {VALID_PAID_PLANS} 之一,实际:{new_plan}"
        )
    if new_billing_cycle not in VALID_BILLING_CYCLES:
        raise InvalidSubscribePlan(
            f"billing_cycle 必须为 {VALID_BILLING_CYCLES} 之一,实际:{new_billing_cycle}"
        )

    old = fetch_active_snapshot(conn, user_id)
    if old is None:
        raise NoActiveSnapshot(
            f"用户 {user_id} 没有 active snapshot,无法 upgrade(应走 subscribe 创建新订阅)"
        )

    now = _iso_now()

    # 1. 老快照归档(state='upgraded')
    execute(
        conn,
        "UPDATE user_plan_snapshots SET state='upgraded', updated_at=? "
        "WHERE id=?",
        (now, old.id),
    )

    # 2. 创建新 active 快照 — 用最新 PLAN_LIMITS 字段(新规则生效)
    price_cents = PLAN_PRICE_CENTS[new_plan][new_billing_cycle]
    limits = PLAN_LIMITS[new_plan]
    period_end = _compute_period_end(now, new_billing_cycle)

    new_id = str(uuid.uuid4())
    execute(
        conn,
        "INSERT INTO user_plan_snapshots "
        "(id, user_id, plan, billing_cycle, price_cents, "
        " monthly_credits_quota, single_credit_price_cents, "
        " characters_per_project, projects_total, reshape_max_percent, "
        " grandfather_at, current_period_start, current_period_end, "
        " state, notes, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "        'active', ?, ?, ?)",
        (
            new_id, user_id, new_plan, new_billing_cycle, price_cents,
            limits.monthly_credits_quota, limits.single_credit_price_cents,
            limits.characters_per_project, limits.projects_total,
            limits.reshape_max_percent,
            old.grandfather_at,    # **保留**老 grandfather_at
            now, period_end,
            notes, now, now,
        ),
    )

    # 3. 同步 users.plan
    execute(
        conn,
        "UPDATE users SET plan=?, updated_at=? WHERE id=?",
        (new_plan, now, user_id),
    )
    conn.commit()

    # Sprint C.1:upgrade 后立即发新档月度 credit(grant 内部清旧池 + 充新池)
    from app.services.credit_service import grant_subscription_credits
    grant_subscription_credits(conn, user_id, new_plan, reason="upgrade")

    row = fetch_one(
        conn, "SELECT * FROM user_plan_snapshots WHERE id=?", (new_id,)
    )
    return UserPlanSnapshot.from_row(row)


def fetch_active_or_pending_for_user(
    conn: sqlite3.Connection, user_id: str
) -> Optional[UserPlanSnapshot]:
    """拿"对配额仍有效的"快照 — active 或 cancelled(但 period_end 未到)。

    quota_service 用此查可享受的配额(取消订阅后到月末前仍享老配额)。
    """
    now = _iso_now()
    row = fetch_one(
        conn,
        "SELECT * FROM user_plan_snapshots "
        "WHERE user_id=? "
        "AND (state='active' OR (state='cancelled' AND current_period_end > ?)) "
        "ORDER BY created_at DESC LIMIT 1",
        (user_id, now),
    )
    if not row:
        return None
    return UserPlanSnapshot.from_row(row)


__all__ = [
    "PLAN_PRICE_CENTS",
    "InvalidSubscribePlan",
    "AlreadyHasActiveSnapshot",
    "NoActiveSnapshot",
    "subscribe",
    "cancel",
    "upgrade",
    "fetch_active_snapshot",
    "fetch_active_or_pending_for_user",
    "fetch_snapshot_history",
]
