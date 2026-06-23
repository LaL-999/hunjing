"""统一商品目录(SKU catalog)— 商业化重塑第一期(2026-06-09)。

四层解耦的第 ① 层:所有"可付费的东西"在这里统一定义成 SKU。
订单(payment_orders)只存 sku_code + 冻结快照;履约(fulfillment_service)
按 SKU.category 路由到对应权益系统。

价格**单一可信源**:
  - 订阅档 → billing_service.PLAN_PRICE_CENTS(不在此重定义,import 引用)
  - 配额包 → credit_service.ADDON_PACKAGES(同上)
  - BYOK   → 本文件 BYOK_PRICE_PER_MONTH_CENTS(BYOK 价历史就硬编码在
            byok_subscriptions.price_cents DEFAULT 3000,这里集中一处)

加新付费场景 = 往这里加一个 SKU + 在 fulfillment_service 加一条路由分支。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.services.billing_service import PLAN_PRICE_CENTS
from app.services.credit_service import ADDON_PACKAGES


# BYOK 月单价(分)— 与 byok_subscriptions.price_cents DEFAULT 一致
BYOK_PRICE_PER_MONTH_CENTS = 3000

# 三大类目(履约路由 key)
CATEGORY_SUBSCRIPTION = "subscription"
CATEGORY_BYOK = "byok"
CATEGORY_CREDIT = "credit"

# 订阅档中文名(显示用)
_PLAN_LABEL = {"pro": "Pro", "max": "Max", "super_max": "Super Max"}
_CYCLE_LABEL = {"monthly": "月付", "yearly": "年付"}
# 配额包中文名
_PACK_LABEL = {"small": "小包 100", "medium": "中包 500", "large": "大包 2000"}
# BYOK 常用月数预设(自定义月数走 build_byok_sku)
_BYOK_PRESET_MONTHS = (1, 3, 12)


@dataclass(frozen=True)
class SKU:
    """一个可下单的商品。amount_cents 是总价,quantity 是倍率(月数 / 包数)。"""
    code: str
    title: str
    category: str
    amount_cents: int
    quantity: int = 1
    # 冻结给履约用的参数:订阅 {plan, billing_cycle} / BYOK {months} / 配额 {package_size}
    meta: dict = field(default_factory=dict)


# ============================================================
# 构建器(从定价源动态拼 SKU,避免价格漂移)
# ============================================================

def build_subscription_sku(plan: str, billing_cycle: str) -> Optional[SKU]:
    """订阅 SKU:plan ∈ pro/max/super_max,cycle ∈ monthly/yearly"""
    prices = PLAN_PRICE_CENTS.get(plan)
    if not prices or billing_cycle not in prices:
        return None
    return SKU(
        code=f"sub_{plan}_{billing_cycle}",
        title=f"{_PLAN_LABEL.get(plan, plan)} · {_CYCLE_LABEL.get(billing_cycle, billing_cycle)}",
        category=CATEGORY_SUBSCRIPTION,
        amount_cents=int(prices[billing_cycle]),
        quantity=1,
        meta={"plan": plan, "billing_cycle": billing_cycle},
    )


def build_byok_sku(months: int) -> Optional[SKU]:
    """BYOK 月卡 SKU:months 1-12,总价 = 月单价 × 月数"""
    if not isinstance(months, int) or months < 1 or months > 12:
        return None
    return SKU(
        code=f"byok_{months}m",
        title=f"自携密钥 · {months} 个月",
        category=CATEGORY_BYOK,
        amount_cents=BYOK_PRICE_PER_MONTH_CENTS * months,
        quantity=months,
        meta={"months": months},
    )


def build_credit_sku(package_size: str) -> Optional[SKU]:
    """配额加购包 SKU:small / medium / large"""
    pkg = ADDON_PACKAGES.get(package_size)
    if not pkg:
        return None
    credits, price_cents = pkg
    return SKU(
        code=f"credit_{package_size}",
        title=f"配额{_PACK_LABEL.get(package_size, package_size)}",
        category=CATEGORY_CREDIT,
        amount_cents=int(price_cents),
        quantity=1,
        meta={"package_size": package_size, "credits": credits},
    )


# ============================================================
# 查询入口
# ============================================================

def get_sku(code: str) -> Optional[SKU]:
    """按 code 解析 SKU。code 形如 sub_pro_monthly / byok_3m / credit_medium。"""
    if not code:
        return None
    if code.startswith("sub_"):
        # sub_<plan>_<cycle>;plan 可能含下划线(super_max)→ 从尾部切 cycle
        body = code[len("sub_"):]
        if body.endswith("_monthly"):
            return build_subscription_sku(body[: -len("_monthly")], "monthly")
        if body.endswith("_yearly"):
            return build_subscription_sku(body[: -len("_yearly")], "yearly")
        return None
    if code.startswith("byok_") and code.endswith("m"):
        try:
            months = int(code[len("byok_"):-1])
        except ValueError:
            return None
        return build_byok_sku(months)
    if code.startswith("credit_"):
        return build_credit_sku(code[len("credit_"):])
    return None


def list_skus(category: Optional[str] = None) -> list[SKU]:
    """列出所有(或某类目)上架 SKU — 前端定价页 / 购买弹窗用。"""
    skus: list[SKU] = []
    # 订阅:3 档 × 2 周期
    for plan in ("pro", "max", "super_max"):
        for cycle in ("monthly", "yearly"):
            s = build_subscription_sku(plan, cycle)
            if s:
                skus.append(s)
    # BYOK:常用月数预设
    for m in _BYOK_PRESET_MONTHS:
        s = build_byok_sku(m)
        if s:
            skus.append(s)
    # 配额包
    for pkg in ("small", "medium", "large"):
        s = build_credit_sku(pkg)
        if s:
            skus.append(s)
    if category:
        skus = [s for s in skus if s.category == category]
    return skus


def to_dict(sku: SKU) -> dict:
    return {
        "code": sku.code,
        "title": sku.title,
        "category": sku.category,
        "amount_cents": sku.amount_cents,
        "amount_yuan": round(sku.amount_cents / 100, 2),
        "quantity": sku.quantity,
        "meta": sku.meta,
    }


__all__ = [
    "SKU",
    "CATEGORY_SUBSCRIPTION", "CATEGORY_BYOK", "CATEGORY_CREDIT",
    "BYOK_PRICE_PER_MONTH_CENTS",
    "build_subscription_sku", "build_byok_sku", "build_credit_sku",
    "get_sku", "list_skus", "to_dict",
]
