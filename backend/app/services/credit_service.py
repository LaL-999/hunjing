"""Credit 服务 — Sprint C.1(2026-05-13)credit 重构核心。

替代老 quota_service.py 中"AI 调用次数"配额机制。**统一虚拟单位 credit**
计量所有 AI 消费(文本 token / 视觉 token / 图像张数)。

详细设计:`docs/ADR_credit_quota_重构.md`

═══════════════════════════════════════════════════════════════
核心抽象 — Credit
═══════════════════════════════════════════════════════════════
- 1 credit = ¥0.10 平台成本基线
- 各档单 credit 售价不同(阶梯优惠):Pro ¥0.1423 / Max ¥0.1288 / 超级 ¥0.1157
- 每用户 2 个独立钱包:
  * subscription_credits:订阅月度发放,**月末清零**
  * addon_credits:加购包,**1 年有效期**(per-lot 跟踪)
- 消耗顺序铁律:**优先扣 subscription**(反正要清,先用)→ 不够时扣 addon FIFO

═══════════════════════════════════════════════════════════════
各 LLM 调用 credit 单价表(C.1 锁定,价格涨跌触发重评)
═══════════════════════════════════════════════════════════════
- DeepSeek V3 1K input token:0.013 c(成本 ¥0.001 × 1.3 毛利倍率 / ¥0.10)
- DeepSeek V3 1K output token:0.026 c
- Qwen-VL Max 1K vision token:0.26 c
- Doubao Seedream 4.0 1 张:2.6 c

业务调用辅助:credit_units_for_text_call() / credit_units_for_vision_call() /
              credit_units_for_image_gen()

═══════════════════════════════════════════════════════════════
本文件公开 API(供 router / service 调用)
═══════════════════════════════════════════════════════════════
- ensure_balance(user_id):新用户首次访问 / verify_otp 后调,初始化 wallet
- get_balance(user_id) → CreditBalance
- consume_credits(user_id, action, units, related_id, cost_yuan, metadata) → CreditConsumption
- refund_credits(user_id, action, units, related_id, metadata)
- grant_subscription_credits(user_id, plan, ...) → 月度发放(订阅 / cron)
- purchase_addon(user_id, package_size, price_cents) → 加购入库
- compute_comic_cancel_refund_units(progress_percent) → 漫画取消退款规则
- credit_units_for_text_call / vision_call / image_gen → token → units 换算
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.services.quota_service import (
    PLAN_LIMITS,
    _now_iso,
    get_plan_limits_for_user,
    month_start_iso,
)


# ═══════════════════════════════════════════════════════════════
# 数据结构
# ═══════════════════════════════════════════════════════════════

@dataclass(frozen=True)
class CreditBalance:
    """用户当前 credit 钱包状态(快照)。"""
    user_id:              str
    subscription_credits: int   # 订阅 wallet,月末清零
    addon_credits:        int   # 加购 wallet,1 年有效
    month_start:          str   # 本月重置参考点 ISO

    @property
    def total(self) -> int:
        return self.subscription_credits + self.addon_credits

    def to_dict(self) -> dict:
        return {
            "user_id":              self.user_id,
            "subscription_credits": self.subscription_credits,
            "addon_credits":        self.addon_credits,
            "total_credits":        self.total,
            "month_start":          self.month_start,
        }


@dataclass(frozen=True)
class CreditConsumption:
    """consume_credits 返回 — 告诉调用方扣了哪些 wallet 各多少。"""
    consumed_subscription: int    # 从订阅 wallet 扣的 credit
    consumed_addon:        int    # 从加购 wallet 扣的 credit
    remaining_subscription: int   # 扣后订阅余额
    remaining_addon:       int    # 扣后加购余额
    transaction_ids:       list[str] = field(default_factory=list)

    @property
    def total_consumed(self) -> int:
        return self.consumed_subscription + self.consumed_addon


class InsufficientCredits(Exception):
    """credit 不足以完成本次 AI 调用 — router 转 429 + 弹加购 / 升档 modal。"""

    def __init__(self, needed: int, available: int, action: str):
        super().__init__(
            f"credit 不足:本次 '{action}' 需 {needed} c,可用 {available} c"
        )
        self.needed = needed
        self.available = available
        self.action = action


# ═══════════════════════════════════════════════════════════════
# Wallet 初始化 + 查询
# ═══════════════════════════════════════════════════════════════

def ensure_balance(
    conn: sqlite3.Connection,
    user_id: str,
    plan: str = "free",
) -> CreditBalance:
    """新用户首次访问 / verify_otp 后调,确保 wallet 存在。

    幂等:已存在则不动 + 直接返回当前 balance。
    新建时按 plan 发月度 credit(free 30c,付费档由订阅流程发 — 但这里也兜底
    给一个 free 池防新用户没付费就用)。
    """
    existing = fetch_one(
        conn,
        "SELECT * FROM user_credit_balances WHERE user_id=?",
        (user_id,),
    )
    if existing is not None:
        return _row_to_balance(existing)

    # 新建 wallet:按 plan 发首月 credit(free=30,付费档由订阅流程触发 grant)
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["free"])
    initial_subscription = limits.monthly_credits_quota if plan == "free" else 30
    # 付费档新用户的 wallet 起点暂按 free 走;真订阅由 billing_service 触发 grant
    # (避免"用户点了订阅但还没付钱就先发"的问题)

    now = _now_iso()
    month_iso = month_start_iso()

    execute(
        conn,
        """INSERT INTO user_credit_balances
           (user_id, month_start, subscription_credits, addon_credits, updated_at)
           VALUES (?, ?, ?, 0, ?)""",
        (user_id, month_iso, initial_subscription, now),
    )

    # 写一条 transaction 审计(首发即记账)
    _record_transaction(
        conn,
        user_id=user_id,
        delta=initial_subscription,
        wallet="subscription",
        kind="subscribe_grant",
        action="initial_grant",
        related_id=None,
        cost_yuan=0,
        metadata={"plan": plan, "reason": "ensure_balance_initial"},
    )
    conn.commit()

    return CreditBalance(
        user_id=user_id,
        subscription_credits=initial_subscription,
        addon_credits=0,
        month_start=month_iso,
    )


def get_balance(conn: sqlite3.Connection, user_id: str) -> CreditBalance:
    """读取当前钱包余额(不存在 → ensure_balance 创建后返回)。"""
    row = fetch_one(
        conn,
        "SELECT * FROM user_credit_balances WHERE user_id=?",
        (user_id,),
    )
    if row is None:
        return ensure_balance(conn, user_id)
    return _row_to_balance(row)


def _row_to_balance(row) -> CreditBalance:
    return CreditBalance(
        user_id=row["user_id"],
        subscription_credits=int(row["subscription_credits"]),
        addon_credits=int(row["addon_credits"]),
        month_start=row["month_start"],
    )


# ═══════════════════════════════════════════════════════════════
# 消费 + 退款
# ═══════════════════════════════════════════════════════════════

def consume_credits(
    conn: sqlite3.Connection,
    user_id: str,
    action: str,
    units: int,
    related_id: Optional[str] = None,
    cost_yuan: float = 0,
    metadata: Optional[dict] = None,
) -> CreditConsumption:
    """扣 credit(优先订阅 → 不够再扣加购 FIFO)。

    Args:
        action:业务动作分类(refine / continuation_create / extract /
                comic_create / comic_batch / vote_style / planner / etc.)
        units:消耗 credit 数(整数,credit_units_for_*_call 已四舍五入)
        related_id:关联业务实体 ID(sim_id / comic_id / batch_id)
        cost_yuan:真实 LLM 账单成本(运营 audit)
        metadata:附加 JSON(input_tokens / output_tokens / vendor / model 等)

    Raises:
        InsufficientCredits:余额不足时

    Sprint C.2(2026-05-13)— founder 档短路:
      创始人账号 monthly_credits_quota >= 9999999,**不真扣 credit 但仍写 transaction**
      (运营 audit 仍能看到创始人用了多少,只是不扣余额)。
    """
    if units <= 0:
        # 0 或负数无意义(refund 走 refund_credits)
        bal = get_balance(conn, user_id)
        return CreditConsumption(0, 0, bal.subscription_credits, bal.addon_credits)

    # Sprint C.2:founder 短路 — 仅写 audit transaction,不动 wallet
    # 注意:DB users.plan 永远是 free/pro/max/super_max(CHECK 约束),
    # 'founder' 是 deps.get_current_user 内存改写的运行时态。
    # 因此 founder 判定必须查 settings.founder_emails 白名单(对齐 deps 逻辑)。
    from app.config import settings
    user_row = fetch_one(
        conn, "SELECT email FROM users WHERE id=?", (user_id,),
    )
    is_founder = (
        user_row is not None
        and user_row["email"]
        and user_row["email"].lower() in settings.founder_emails
    )
    if is_founder:
        balance = get_balance(conn, user_id)
        meta = dict(metadata or {})
        meta["founder_short_circuit"] = True
        meta["would_consume"] = units
        tx_id = _record_transaction(
            conn,
            user_id=user_id,
            delta=0,             # 不真扣
            wallet="subscription",
            kind="consume",
            action=action,
            related_id=related_id,
            cost_yuan=cost_yuan,  # 真实成本仍记账
            metadata=meta,
        )
        conn.commit()
        return CreditConsumption(
            consumed_subscription=0,
            consumed_addon=0,
            remaining_subscription=balance.subscription_credits,
            remaining_addon=balance.addon_credits,
            transaction_ids=[tx_id],
        )

    balance = get_balance(conn, user_id)
    if balance.total < units:
        raise InsufficientCredits(
            needed=units,
            available=balance.total,
            action=action,
        )

    # 扣减:先订阅,后加购(订阅 wallet 月末清,先用)
    consumed_sub = min(units, balance.subscription_credits)
    consumed_add = units - consumed_sub

    new_sub = balance.subscription_credits - consumed_sub
    new_add = balance.addon_credits - consumed_add

    _update_balance(conn, user_id, new_sub, new_add)

    tx_ids: list[str] = []
    if consumed_sub > 0:
        tx_id = _record_transaction(
            conn,
            user_id=user_id,
            delta=-consumed_sub,
            wallet="subscription",
            kind="consume",
            action=action,
            related_id=related_id,
            cost_yuan=cost_yuan if consumed_add == 0 else cost_yuan * (consumed_sub / units),
            metadata=metadata,
        )
        tx_ids.append(tx_id)
    if consumed_add > 0:
        tx_id = _record_transaction(
            conn,
            user_id=user_id,
            delta=-consumed_add,
            wallet="addon",
            kind="consume",
            action=action,
            related_id=related_id,
            cost_yuan=cost_yuan * (consumed_add / units) if consumed_sub > 0 else cost_yuan,
            metadata=metadata,
        )
        tx_ids.append(tx_id)
        # 同步 addon_credit_lots(FIFO 扣):由 _consume_from_addon_lots 维护
        _consume_from_addon_lots(conn, user_id, consumed_add)

    conn.commit()

    return CreditConsumption(
        consumed_subscription=consumed_sub,
        consumed_addon=consumed_add,
        remaining_subscription=new_sub,
        remaining_addon=new_add,
        transaction_ids=tx_ids,
    )


def refund_credits(
    conn: sqlite3.Connection,
    user_id: str,
    action: str,
    units: int,
    related_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> CreditBalance:
    """退还 credit(漫画取消按进度退 / LLM 调用失败兜底退)。

    退回到订阅 wallet(简化策略 — 不区分原扣自哪个 wallet,因为
    订阅月末清零,退到加购更"占便宜",但用户已扣了的就当订阅退)。

    units 为正数(退多少);函数内部 delta 取正写 transaction。
    """
    if units <= 0:
        return get_balance(conn, user_id)

    balance = get_balance(conn, user_id)
    new_sub = balance.subscription_credits + units

    _update_balance(conn, user_id, new_sub, balance.addon_credits)

    _record_transaction(
        conn,
        user_id=user_id,
        delta=units,
        wallet="subscription",
        kind="refund",
        action=action,
        related_id=related_id,
        cost_yuan=0,
        metadata=metadata,
    )
    conn.commit()

    return CreditBalance(
        user_id=user_id,
        subscription_credits=new_sub,
        addon_credits=balance.addon_credits,
        month_start=balance.month_start,
    )


# ═══════════════════════════════════════════════════════════════
# 月度发放(订阅 / cron month_reset)
# ═══════════════════════════════════════════════════════════════

def grant_subscription_credits(
    conn: sqlite3.Connection,
    user_id: str,
    plan: str,
    reason: str = "monthly_grant",
) -> CreditBalance:
    """月度发放 — 按用户当前 plan 的 monthly_credits_quota 充值订阅 wallet。

    若用户有 active user_plan_snapshots → 优先按快照的 monthly_credits_quota。
    本函数**覆盖**订阅 wallet 余额(月末清零等于"清空 + 重发"一气呵成)。
    """
    limits = get_plan_limits_for_user(conn, user_id, plan)
    new_quota = limits.monthly_credits_quota

    balance = get_balance(conn, user_id)
    now = _now_iso()
    month_iso = month_start_iso()

    # 1. 清零 subscription wallet → 记账
    if balance.subscription_credits > 0:
        _record_transaction(
            conn,
            user_id=user_id,
            delta=-balance.subscription_credits,
            wallet="subscription",
            kind="month_reset",
            action="cron_month_reset",
            related_id=None,
            cost_yuan=0,
            metadata={"reason": "before_grant"},
        )

    # 2. 发新月度池
    _update_balance(conn, user_id, new_quota, balance.addon_credits, month_start=month_iso)

    _record_transaction(
        conn,
        user_id=user_id,
        delta=new_quota,
        wallet="subscription",
        kind="subscribe_grant",
        action=reason,
        related_id=None,
        cost_yuan=0,
        metadata={"plan": plan, "month_start": month_iso},
    )
    conn.commit()

    return CreditBalance(
        user_id=user_id,
        subscription_credits=new_quota,
        addon_credits=balance.addon_credits,
        month_start=month_iso,
    )


# ═══════════════════════════════════════════════════════════════
# 加购包
# ═══════════════════════════════════════════════════════════════

ADDON_PACKAGES = {
    # package_size: (credits, price_cents) — v5(2026-06-26)全线约减半
    "small":  (100,  1000),    # ¥18 → ¥10  / 100c  → ¥0.10/c
    "medium": (500,  4200),    # ¥85 → ¥42  / 500c  → ¥0.084/c
    "large":  (2000, 16000),   # ¥320 → ¥160 / 2000c → ¥0.08/c(最划算,靠 BYOK 补毛利)
}


def purchase_addon(
    conn: sqlite3.Connection,
    user_id: str,
    package_size: str,
) -> CreditBalance:
    """加购包入库:发 credit 到 addon wallet + INSERT addon_credit_lots(1 年有效期)。

    Sprint C.1 暂不接真支付,本函数模拟"支付成功后调用"。Sprint C.5 接 webhook。
    """
    if package_size not in ADDON_PACKAGES:
        raise ValueError(f"未知加购包 size: {package_size}")
    credits, price_cents = ADDON_PACKAGES[package_size]

    now_dt = datetime.now(timezone.utc).replace(microsecond=0)
    now_iso_str = now_dt.isoformat()
    expires_iso = (now_dt + timedelta(days=365)).isoformat()

    # 1. INSERT lot
    lot_id = str(uuid.uuid4())
    execute(
        conn,
        """INSERT INTO addon_credit_lots
           (id, user_id, initial_credits, price_cents, package_size,
            remaining_credits, is_expired, purchased_at, expires_at, expired_at)
           VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, NULL)""",
        (lot_id, user_id, credits, price_cents, package_size,
         credits, now_iso_str, expires_iso),
    )

    # 2. balance wallet 同步 + transaction 审计
    balance = get_balance(conn, user_id)
    new_addon = balance.addon_credits + credits
    _update_balance(conn, user_id, balance.subscription_credits, new_addon)

    _record_transaction(
        conn,
        user_id=user_id,
        delta=credits,
        wallet="addon",
        kind="addon_purchase",
        action=f"buy_{package_size}",
        related_id=lot_id,
        cost_yuan=price_cents / 100.0,
        metadata={
            "package_size":   package_size,
            "price_cents":    price_cents,
            "expires_at":     expires_iso,
        },
    )
    conn.commit()

    return CreditBalance(
        user_id=user_id,
        subscription_credits=balance.subscription_credits,
        addon_credits=new_addon,
        month_start=balance.month_start,
    )


def _consume_from_addon_lots(
    conn: sqlite3.Connection, user_id: str, units: int,
) -> None:
    """FIFO 从加购 lot 扣余额 — 内部辅助。

    按 purchased_at ASC 扣,每个 lot 扣完前不切下一个。
    """
    remaining_to_consume = units
    lots = fetch_all(
        conn,
        """SELECT id, remaining_credits FROM addon_credit_lots
           WHERE user_id=? AND is_expired=0 AND remaining_credits > 0
           ORDER BY purchased_at ASC""",
        (user_id,),
    )
    for lot in lots:
        if remaining_to_consume <= 0:
            break
        lot_remaining = int(lot["remaining_credits"])
        deduct = min(lot_remaining, remaining_to_consume)
        execute(
            conn,
            "UPDATE addon_credit_lots SET remaining_credits=? WHERE id=?",
            (lot_remaining - deduct, lot["id"]),
        )
        remaining_to_consume -= deduct


# ═══════════════════════════════════════════════════════════════
# 漫画取消退款规则(从老 quota_service 挪过来)
# ═══════════════════════════════════════════════════════════════

# 退款档位阈值(progress_percent)
COMIC_REFUND_FULL_PROGRESS_THRESHOLD = 10    # < 10% 全退
COMIC_REFUND_HALF_PROGRESS_THRESHOLD = 80    # < 80% 半退 / >= 80% 不退


def compute_comic_cancel_refund_units(
    progress_percent: int, original_units: int,
) -> tuple[int, str]:
    """漫画取消按进度退款(配合 C.4 漫画分批承接最终精确化):

      progress <  10:  全退 original_units(净占 0)
      progress < 80:   半退 original_units // 2(净占一半)
      progress >= 80:  不退(净占 1 本)

    返回 (refund_units, refund_phase):
      refund_phase ∈ {"full", "half", "none"}
    """
    if progress_percent < COMIC_REFUND_FULL_PROGRESS_THRESHOLD:
        return (original_units, "full")
    elif progress_percent < COMIC_REFUND_HALF_PROGRESS_THRESHOLD:
        return (original_units // 2, "half")
    else:
        return (0, "none")


# ═══════════════════════════════════════════════════════════════
# Token → Credit 换算(C.2 真接 LLM 时用)
# ═══════════════════════════════════════════════════════════════

def credit_units_for_text_call(input_tokens: int, output_tokens: int) -> int:
    """DeepSeek V3 文本调用 credit 估算(向上取整,最少 1 c)。

    单价(ADR §2.2):input 0.013 c/1K / output 0.026 c/1K
    """
    raw = (input_tokens * 0.013 + output_tokens * 0.026) / 1000
    return max(1, int(raw + 0.999))


def credit_units_for_vision_call(vision_tokens: int) -> int:
    """Qwen-VL Max 多模态调用 credit(input=output 同价 0.26 c/1K)。"""
    raw = vision_tokens * 0.26 / 1000
    return max(1, int(raw + 0.999))


def credit_units_for_image_gen(image_count: int) -> int:
    """Seedream 4.0 图像生成 credit(2.6 c/张)。"""
    return max(1, int(image_count * 2.6 + 0.999))


# ═══════════════════════════════════════════════════════════════
# 内部辅助 — DB 写
# ═══════════════════════════════════════════════════════════════

def _update_balance(
    conn: sqlite3.Connection,
    user_id: str,
    subscription_credits: int,
    addon_credits: int,
    month_start: Optional[str] = None,
) -> None:
    """更新 user_credit_balances 行(单次写)。"""
    if month_start is not None:
        execute(
            conn,
            """UPDATE user_credit_balances
               SET subscription_credits=?, addon_credits=?, month_start=?, updated_at=?
               WHERE user_id=?""",
            (subscription_credits, addon_credits, month_start, _now_iso(), user_id),
        )
    else:
        execute(
            conn,
            """UPDATE user_credit_balances
               SET subscription_credits=?, addon_credits=?, updated_at=?
               WHERE user_id=?""",
            (subscription_credits, addon_credits, _now_iso(), user_id),
        )


def _record_transaction(
    conn: sqlite3.Connection,
    user_id: str,
    delta: int,
    wallet: str,
    kind: str,
    action: Optional[str],
    related_id: Optional[str],
    cost_yuan: float,
    metadata: Optional[dict],
) -> str:
    """写一条 credit_transactions 审计记录。"""
    tx_id = str(uuid.uuid4())
    execute(
        conn,
        """INSERT INTO credit_transactions
           (id, user_id, delta, wallet, kind, action, related_id, cost_yuan, metadata, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            tx_id,
            user_id,
            delta,
            wallet,
            kind,
            action,
            related_id,
            cost_yuan,
            json.dumps(metadata, ensure_ascii=False) if metadata is not None else None,
            _now_iso(),
        ),
    )
    return tx_id
