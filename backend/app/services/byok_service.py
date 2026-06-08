"""
BYOK 自携密钥 — service 层 CRUD + 业务逻辑

API:
  purchase_subscription(conn, user_id, months) -> BYOKSubscription
      生成激活码,记账(MVP 简化:暂时不扣 credit,纯生成 — 真支付下次接)

  activate_code(conn, user_id, code) -> BYOKSubscription
      用户输入码激活;校验:必须是本人的码 + 未过期 + 未被 deactivate

  deactivate_current(conn, user_id) -> Optional[BYOKSubscription]
      用户主动停用(后续可再激活,只要月卡没过期)

  get_status(conn, user_id) -> BYOKStatusResponse
      给 sidebar 入口判断:是否已激活 / 是否买了但没激活 / 配置数量

  list_configs(conn, user_id) -> list[BYOKConfig]
      所有配置(明文 key 不出 — service 层只返 mask)

  upsert_config(conn, user_id, req) -> BYOKConfig
      新建 / 更新(user+provider+model_name 三元组唯一,UNIQUE 约束兜底)

  set_default_config(conn, user_id, config_id) -> BYOKConfig
      原 default 清 0,新 default 标 1(单事务)

  delete_config(conn, user_id, config_id) -> None

  get_active_llm_config(conn, user_id) -> Optional[dict]
      LLM router 调用 — 返回当前应用的 {provider, base_url, model_name, api_key 明文}
      返 None = 该用户当前应走平台 default key

  expire_old_subscriptions(conn) -> int
      cron 用 — 把 expires_at < now 的 is_active=1 全部置 0(自动失效)
      返回处理条数

业务约束:
  1. 任何登录用户都能 purchase(Free 也行)
  2. 一次 purchase 累加月数(多次买 → 累计到期日)
  3. 同时只能激活一张订阅;activate 时若有其他 active,自动 deactivate
  4. 配置存活 — 订阅过期后 configs 保留,下次激活立即可用
  5. UNIQUE (user_id, provider, model_name) — 同模型重配自动替换
"""
from __future__ import annotations

import logging
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models.byok import (
    BYOK_MONTHLY_PRICE_CENTS,
    BYOK_VALIDITY_DAYS,
    BYOKConfig,
    BYOKConfigResponse,
    BYOKConfigUpsertRequest,
    BYOKStatusResponse,
    BYOKSubscription,
)
from app.utils.byok_crypto import (
    decrypt_api_key,
    encrypt_api_key,
    mask_api_key,
)

logger = logging.getLogger(__name__)


# ============================================================
# 辅助
# ============================================================

def _now_iso() -> str:
    """UTC ISO8601 字符串(与项目其他地方一致)"""
    return datetime.now(timezone.utc).isoformat()


def _generate_code() -> str:
    """
    生成激活码:BYOK-XXXX-XXXX-XXXX 形式
    - 用 secrets.token_urlsafe 取 12 字符(去掉容易混淆的 0OIl1)
    - 分 3 组用 "-" 拼,4-4-4
    - 加 "BYOK-" 前缀,总长 20 字符
    """
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"  # 去 0OIl1
    parts = []
    for _ in range(3):
        chunk = "".join(secrets.choice(alphabet) for _ in range(4))
        parts.append(chunk)
    return "BYOK-" + "-".join(parts)


def _max_expires_for_user(conn: sqlite3.Connection, user_id: str) -> Optional[str]:
    """
    查该用户当前最大 expires_at(用于累加月数)— 未过期的订阅中取 max。
    若都已过期 / 无订阅,返 None。
    """
    row = conn.execute(
        """
        SELECT MAX(expires_at) AS m
          FROM byok_subscriptions
         WHERE user_id = ?
           AND expires_at > ?
        """,
        (user_id, _now_iso()),
    ).fetchone()
    if row is None:
        return None
    return row["m"]


# ============================================================
# 订阅
# ============================================================

def purchase_subscription(
    conn: sqlite3.Connection,
    user_id: str,
    months: int = 1,
) -> BYOKSubscription:
    """
    购买 BYOK 月卡。MVP 简化:不真正扣钱(后续接支付宝 / 微信);只生成激活码 + 记账。

    累加规则:
    - 该用户有未过期订阅 → 新订阅从该 max(expires_at) 开始算
    - 否则从现在开始算

    返回:新建的 BYOKSubscription(含明文 code)
    """
    months = max(1, min(months, 12))
    code = _generate_code()
    now_iso = _now_iso()

    # 累加 — 如果用户已有未过期订阅,新订阅在那之上叠加月数
    max_expires = _max_expires_for_user(conn, user_id)
    if max_expires:
        base = datetime.fromisoformat(max_expires.replace("Z", "+00:00"))
    else:
        base = datetime.now(timezone.utc)

    expires_at = (base + timedelta(days=BYOK_VALIDITY_DAYS * months)).isoformat()
    sub_id = str(uuid.uuid4())
    price = BYOK_MONTHLY_PRICE_CENTS * months

    conn.execute(
        """
        INSERT INTO byok_subscriptions
            (id, user_id, code, purchased_at, expires_at, is_active, price_cents)
        VALUES (?, ?, ?, ?, ?, 0, ?)
        """,
        (sub_id, user_id, code, now_iso, expires_at, price),
    )
    conn.commit()
    logger.info(
        "BYOK 订阅创建 user=%s code=%s expires=%s price=%d",
        user_id, code[:8] + "...", expires_at, price,
    )

    return BYOKSubscription(
        id=sub_id,
        user_id=user_id,
        code=code,
        purchased_at=now_iso,
        expires_at=expires_at,
        is_active=False,
        activated_at=None,
        deactivated_at=None,
        price_cents=price,
        notes=None,
    )


def activate_code(
    conn: sqlite3.Connection,
    user_id: str,
    code: str,
) -> BYOKSubscription:
    """
    用户输入激活码 → 激活。

    校验:
    - 码必须存在 + 归属本人(避免他人偷码)
    - 未过期(expires_at > now)
    - 未被 deactivate

    若该用户已有其他 active 订阅 → 自动 deactivate(同时只 1 张生效)

    抛:
    - ValueError("code_not_found"):码不存在 / 不归本人
    - ValueError("code_expired"):已过期
    - ValueError("code_already_used"):已被 deactivate(可重新激活,但若新订阅在,先用新订阅)
    """
    row = conn.execute(
        """
        SELECT * FROM byok_subscriptions
         WHERE code = ?
           AND user_id = ?
         LIMIT 1
        """,
        (code.strip(), user_id),
    ).fetchone()
    if row is None:
        raise ValueError("code_not_found")

    sub = BYOKSubscription.from_row(row)
    now_iso = _now_iso()

    if sub.expires_at <= now_iso:
        raise ValueError("code_expired")

    # deactivate 其他 active 订阅(同用户)
    conn.execute(
        """
        UPDATE byok_subscriptions
           SET is_active = 0, deactivated_at = ?
         WHERE user_id = ?
           AND is_active = 1
           AND id != ?
        """,
        (now_iso, user_id, sub.id),
    )

    # 激活本订阅
    conn.execute(
        """
        UPDATE byok_subscriptions
           SET is_active = 1, activated_at = ?, deactivated_at = NULL
         WHERE id = ?
        """,
        (now_iso, sub.id),
    )
    conn.commit()
    logger.info("BYOK 激活 user=%s code=%s", user_id, code[:8] + "...")

    sub.is_active = True
    sub.activated_at = now_iso
    sub.deactivated_at = None
    return sub


def deactivate_current(
    conn: sqlite3.Connection,
    user_id: str,
) -> Optional[BYOKSubscription]:
    """用户主动停用 — 不删码,只置 is_active=0。月卡还在,下次可再激活。"""
    row = conn.execute(
        """
        SELECT * FROM byok_subscriptions
         WHERE user_id = ? AND is_active = 1
         LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if row is None:
        return None

    sub = BYOKSubscription.from_row(row)
    now_iso = _now_iso()
    conn.execute(
        """
        UPDATE byok_subscriptions
           SET is_active = 0, deactivated_at = ?
         WHERE id = ?
        """,
        (now_iso, sub.id),
    )
    conn.commit()
    logger.info("BYOK 主动停用 user=%s", user_id)

    sub.is_active = False
    sub.deactivated_at = now_iso
    return sub


def get_status(
    conn: sqlite3.Connection,
    user_id: str,
) -> BYOKStatusResponse:
    """给 sidebar 入口判断 — 是否已激活 / 是否有未激活的码 / configs 数量"""
    now_iso = _now_iso()

    # 当前 active 订阅
    active_row = conn.execute(
        """
        SELECT * FROM byok_subscriptions
         WHERE user_id = ? AND is_active = 1 AND expires_at > ?
         LIMIT 1
        """,
        (user_id, now_iso),
    ).fetchone()

    # 未激活但有效的订阅(用户买了没输入码)
    unused_row = conn.execute(
        """
        SELECT 1 FROM byok_subscriptions
         WHERE user_id = ? AND is_active = 0 AND expires_at > ?
         LIMIT 1
        """,
        (user_id, now_iso),
    ).fetchone()

    # configs 总数 + default
    cfg_rows = conn.execute(
        """
        SELECT COUNT(*) AS c,
               MAX(CASE WHEN is_default = 1 THEN provider ELSE NULL END) AS dp
          FROM byok_configs
         WHERE user_id = ?
        """,
        (user_id,),
    ).fetchone()

    return BYOKStatusResponse(
        has_active_subscription=active_row is not None,
        has_unused_subscription=unused_row is not None,
        active_subscription_expires_at=active_row["expires_at"] if active_row else None,
        active_code=active_row["code"] if active_row else None,
        configs_count=int(cfg_rows["c"] or 0) if cfg_rows else 0,
        default_provider=cfg_rows["dp"] if cfg_rows else None,
    )


# ============================================================
# 配置 CRUD
# ============================================================

def list_configs(
    conn: sqlite3.Connection,
    user_id: str,
) -> list[BYOKConfig]:
    """所有配置(default 优先,然后按更新时间倒序)"""
    rows = conn.execute(
        """
        SELECT * FROM byok_configs
         WHERE user_id = ?
         ORDER BY is_default DESC, updated_at DESC
        """,
        (user_id,),
    ).fetchall()
    return [BYOKConfig.from_row(r) for r in rows]


def upsert_config(
    conn: sqlite3.Connection,
    user_id: str,
    req: BYOKConfigUpsertRequest,
) -> BYOKConfig:
    """
    新建 / 更新配置。同 (user_id, provider, model_name) 三元组覆盖。

    流程:
    1. 加密 api_key + 生成 mask
    2. 若指定 is_default=True,先把该用户所有 configs is_default=0
    3. INSERT OR REPLACE(三元组 UNIQUE 自动触发 REPLACE)
    """
    now_iso = _now_iso()
    encrypted = encrypt_api_key(req.api_key)
    mask = mask_api_key(req.api_key)

    # 看是否已有同 三元组
    existing = conn.execute(
        """
        SELECT id, created_at FROM byok_configs
         WHERE user_id = ? AND provider = ? AND model_name = ?
        """,
        (user_id, req.provider, req.model_name),
    ).fetchone()

    if req.is_default:
        # 清掉该用户其他所有 default
        conn.execute(
            """
            UPDATE byok_configs SET is_default = 0, updated_at = ?
             WHERE user_id = ? AND is_default = 1
            """,
            (now_iso, user_id),
        )

    if existing:
        config_id = existing["id"]
        created_at = existing["created_at"]
        conn.execute(
            """
            UPDATE byok_configs
               SET display_name = ?,
                   base_url = ?,
                   api_key_encrypted = ?,
                   api_key_mask = ?,
                   is_default = ?,
                   updated_at = ?
             WHERE id = ?
            """,
            (
                req.display_name,
                req.base_url,
                encrypted,
                mask,
                int(req.is_default),
                now_iso,
                config_id,
            ),
        )
    else:
        config_id = str(uuid.uuid4())
        created_at = now_iso
        conn.execute(
            """
            INSERT INTO byok_configs
                (id, user_id, provider, display_name, base_url, model_name,
                 api_key_encrypted, api_key_mask, is_default,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                config_id,
                user_id,
                req.provider,
                req.display_name,
                req.base_url,
                req.model_name,
                encrypted,
                mask,
                int(req.is_default),
                created_at,
                now_iso,
            ),
        )

    conn.commit()
    logger.info(
        "BYOK config upsert user=%s provider=%s model=%s default=%s",
        user_id, req.provider, req.model_name, req.is_default,
    )

    return BYOKConfig(
        id=config_id,
        user_id=user_id,
        provider=req.provider,
        display_name=req.display_name,
        base_url=req.base_url,
        model_name=req.model_name,
        api_key_encrypted=encrypted,
        api_key_mask=mask,
        is_default=req.is_default,
        last_test_ok=None,
        last_test_at=None,
        last_test_error=None,
        created_at=created_at,
        updated_at=now_iso,
    )


def set_default_config(
    conn: sqlite3.Connection,
    user_id: str,
    config_id: str,
) -> Optional[BYOKConfig]:
    """单独切默认 config — 其他 configs is_default 清 0,目标 config 置 1"""
    # 校验 config 归属
    row = conn.execute(
        """
        SELECT * FROM byok_configs WHERE id = ? AND user_id = ?
        """,
        (config_id, user_id),
    ).fetchone()
    if row is None:
        return None

    now_iso = _now_iso()
    conn.execute(
        """
        UPDATE byok_configs SET is_default = 0, updated_at = ?
         WHERE user_id = ? AND is_default = 1
        """,
        (now_iso, user_id),
    )
    conn.execute(
        """
        UPDATE byok_configs SET is_default = 1, updated_at = ?
         WHERE id = ?
        """,
        (now_iso, config_id),
    )
    conn.commit()

    # 重读
    new_row = conn.execute(
        "SELECT * FROM byok_configs WHERE id = ?", (config_id,),
    ).fetchone()
    return BYOKConfig.from_row(new_row) if new_row else None


def delete_config(
    conn: sqlite3.Connection,
    user_id: str,
    config_id: str,
) -> bool:
    """删配置 — 必须归属本人。返 True = 删了一行,False = 不存在 / 不归本人"""
    cursor = conn.execute(
        "DELETE FROM byok_configs WHERE id = ? AND user_id = ?",
        (config_id, user_id),
    )
    conn.commit()
    return cursor.rowcount > 0


def update_test_result(
    conn: sqlite3.Connection,
    config_id: str,
    ok: bool,
    error: Optional[str] = None,
) -> None:
    """连通性测试后更新状态"""
    now_iso = _now_iso()
    conn.execute(
        """
        UPDATE byok_configs
           SET last_test_ok = ?, last_test_at = ?, last_test_error = ?
         WHERE id = ?
        """,
        (int(ok), now_iso, error, config_id),
    )
    conn.commit()


# ============================================================
# LLM router 接口 — 决定走用户 key 还是平台 default key
# ============================================================

def get_active_llm_config(
    conn: sqlite3.Connection,
    user_id: str,
) -> Optional[dict]:
    """
    给 LLM router 调用 — 返回当前用户应该用的 LLM 配置(含明文 key)。

    返回 None = 该用户当前应该走平台 default key
    返回 dict = 该用户当前应该走自己的 key,字段:
        - provider: str
        - base_url: str
        - model_name: str
        - api_key: str(明文,临时使用,调用完即丢)
    """
    now_iso = _now_iso()

    # 必须:有 active 订阅 + 未过期
    sub_row = conn.execute(
        """
        SELECT 1 FROM byok_subscriptions
         WHERE user_id = ? AND is_active = 1 AND expires_at > ?
         LIMIT 1
        """,
        (user_id, now_iso),
    ).fetchone()
    if sub_row is None:
        return None

    # 必须:有 default config
    cfg_row = conn.execute(
        """
        SELECT * FROM byok_configs
         WHERE user_id = ? AND is_default = 1
         LIMIT 1
        """,
        (user_id,),
    ).fetchone()
    if cfg_row is None:
        return None

    try:
        api_key = decrypt_api_key(cfg_row["api_key_encrypted"])
    except Exception:
        logger.exception("BYOK config 解密失败 — user_id=%s config_id=%s", user_id, cfg_row["id"])
        return None

    return {
        "provider": cfg_row["provider"],
        "base_url": cfg_row["base_url"],
        "model_name": cfg_row["model_name"],
        "api_key": api_key,
    }


# ============================================================
# Cron / 后台任务
# ============================================================

def expire_old_subscriptions(conn: sqlite3.Connection) -> int:
    """
    cron 用 — 把 expires_at < now 且 is_active=1 的全部 deactivate。
    返回:处理的条数(0 = 没有需要失效的)
    """
    now_iso = _now_iso()
    cursor = conn.execute(
        """
        UPDATE byok_subscriptions
           SET is_active = 0, deactivated_at = ?
         WHERE is_active = 1 AND expires_at <= ?
        """,
        (now_iso, now_iso),
    )
    affected = cursor.rowcount
    conn.commit()
    if affected:
        logger.info("BYOK 过期失效 %d 条订阅", affected)
    return affected


# ============================================================
# Response 转换
# ============================================================

def to_response(config: BYOKConfig) -> BYOKConfigResponse:
    """dataclass → Pydantic(只露 mask,不露明文 / 密文)"""
    return BYOKConfigResponse(
        id=config.id,
        provider=config.provider,
        display_name=config.display_name,
        base_url=config.base_url,
        model_name=config.model_name,
        api_key_mask=config.api_key_mask,
        is_default=config.is_default,
        last_test_ok=config.last_test_ok,
        last_test_at=config.last_test_at,
        last_test_error=config.last_test_error,
        created_at=config.created_at,
        updated_at=config.updated_at,
    )
