"""
BYOK 自携密钥 — dataclass models + Pydantic schemas

数据库表(migration 082/083):
  - byok_subscriptions: 月度订阅 + 激活码
  - byok_configs:      用户配的 provider/key/model(加密存储)

dataclass 用于 service 层 sql row → object 映射
Pydantic 用于 FastAPI request/response 校验
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ============================================================
# 常量
# ============================================================

#: 单次月卡价格(单位:分)— v5(2026-06-26)主打钩子:¥30 → ¥5 = 500 分
#: 与 services/payments/catalog.py BYOK_PRICE_PER_MONTH_CENTS 必须同步
BYOK_MONTHLY_PRICE_CENTS = 500

#: 月卡有效期(天)— 30 天
BYOK_VALIDITY_DAYS = 30

#: 预设 provider 标识(用户也能填 "custom")
PROVIDER_LITERAL = Literal[
    "deepseek",
    "qwen",
    "zhipu",
    "doubao",
    "moonshot",
    "custom",
]


# ============================================================
# byok_subscriptions(月卡 + 激活码)
# ============================================================

@dataclass
class BYOKSubscription:
    id: str
    user_id: str
    code: str
    purchased_at: str
    expires_at: str
    is_active: bool
    activated_at: Optional[str]
    deactivated_at: Optional[str]
    price_cents: int
    notes: Optional[str]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "BYOKSubscription":
        def _safe(key: str, default):
            try:
                return row[key]
            except (KeyError, IndexError):
                return default

        return cls(
            id=row["id"],
            user_id=row["user_id"],
            code=row["code"],
            purchased_at=row["purchased_at"],
            expires_at=row["expires_at"],
            is_active=bool(row["is_active"]),
            activated_at=_safe("activated_at", None),
            deactivated_at=_safe("deactivated_at", None),
            price_cents=int(_safe("price_cents", BYOK_MONTHLY_PRICE_CENTS)),
            notes=_safe("notes", None),
        )


# ============================================================
# byok_configs(用户配置 + 加密 key)
# ============================================================

@dataclass
class BYOKConfig:
    id: str
    user_id: str
    provider: str
    display_name: Optional[str]
    base_url: str
    model_name: str
    api_key_encrypted: str  # service 层用,不出 endpoint
    api_key_mask: str       # endpoint 返回的脱敏展示
    is_default: bool
    last_test_ok: Optional[bool]
    last_test_at: Optional[str]
    last_test_error: Optional[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "BYOKConfig":
        def _safe(key: str, default):
            try:
                return row[key]
            except (KeyError, IndexError):
                return default

        last_test = _safe("last_test_ok", None)
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            provider=row["provider"],
            display_name=_safe("display_name", None),
            base_url=row["base_url"],
            model_name=row["model_name"],
            api_key_encrypted=row["api_key_encrypted"],
            api_key_mask=row["api_key_mask"],
            is_default=bool(row["is_default"]),
            last_test_ok=bool(last_test) if last_test is not None else None,
            last_test_at=_safe("last_test_at", None),
            last_test_error=_safe("last_test_error", None),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


# ============================================================
# Pydantic — Request / Response
# ============================================================

class BYOKStatusResponse(BaseModel):
    """GET /api/byok/status — 当前用户 BYOK 总览(sidebar 入口判断展示用)"""
    has_active_subscription: bool   # 有未过期且 is_active=1 的订阅
    has_unused_subscription: bool   # 有未过期但 is_active=0 (买了但没激活)
    active_subscription_expires_at: Optional[str] = None  # ISO
    active_code: Optional[str] = None                     # 已激活的码,不显未激活
    configs_count: int = 0                                # 用户已配几个 provider
    default_provider: Optional[str] = None                # 当前默认走哪个


class BYOKPurchaseRequest(BaseModel):
    """POST /api/byok/purchase — 购买月卡(MVP:模拟支付,实际接 credit / 支付宝后续)"""
    months: int = Field(default=1, ge=1, le=12)


class BYOKPurchaseResponse(BaseModel):
    code: str                  # BYOK-XXXX-XXXX-XXXX
    expires_at: str            # ISO
    price_cents: int           # 实际付的总价(months × 3000)


class BYOKActivateRequest(BaseModel):
    code: str = Field(..., min_length=4)


class BYOKActivateResponse(BaseModel):
    is_active: bool
    expires_at: str


class BYOKConfigUpsertRequest(BaseModel):
    """新建 / 更新 config — 同 user/provider/model_name 三元组唯一,重复 = 替换"""
    provider: PROVIDER_LITERAL
    display_name: Optional[str] = Field(default=None, max_length=50)
    base_url: str = Field(..., min_length=8, max_length=500)
    model_name: str = Field(..., min_length=1, max_length=100)
    api_key: str = Field(..., min_length=4, max_length=500)
    is_default: bool = False


class BYOKConfigResponse(BaseModel):
    """endpoint 返回 — 不含明文 key,仅 mask"""
    id: str
    provider: str
    display_name: Optional[str]
    base_url: str
    model_name: str
    api_key_mask: str
    is_default: bool
    last_test_ok: Optional[bool]
    last_test_at: Optional[str]
    last_test_error: Optional[str]
    created_at: str
    updated_at: str


class BYOKConfigsListResponse(BaseModel):
    configs: list[BYOKConfigResponse]


class BYOKTestRequest(BaseModel):
    """测试 config 连通性 — 调一个最小 prompt 看是否能连"""
    pass


class BYOKTestResponse(BaseModel):
    ok: bool
    error: Optional[str] = None
    latency_ms: Optional[int] = None
