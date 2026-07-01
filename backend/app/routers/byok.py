"""
BYOK 自携密钥 router

Endpoints:
  GET    /api/byok/status                 — 当前用户 BYOK 总览
  POST   /api/byok/purchase               — 购买月卡(MVP 不真扣钱,生成激活码)
  POST   /api/byok/activate               — 输入激活码解锁
  POST   /api/byok/deactivate             — 主动停用(月卡保留)
  GET    /api/byok/configs                — 列出用户所有 provider 配置
  PUT    /api/byok/configs                — 新建 / 更新配置(upsert)
  POST   /api/byok/configs/{id}/default   — 设为默认
  DELETE /api/byok/configs/{id}           — 删除配置
  POST   /api/byok/configs/{id}/test      — 测试连通性(调一个最小 prompt)

鉴权:全部走 get_current_user(必须登录)
"""
from __future__ import annotations

import logging
import sqlite3
import time
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.deps import get_current_user, get_db
from app.models.byok import (
    BYOKActivateRequest,
    BYOKActivateResponse,
    BYOKConfigResponse,
    BYOKConfigsListResponse,
    BYOKConfigUpsertRequest,
    BYOKPurchaseRequest,
    BYOKPurchaseResponse,
    BYOKStatusResponse,
    BYOKTestResponse,
)
from app.models.user import User
from app.services import byok_service
from app.utils.byok_crypto import decrypt_api_key

logger = logging.getLogger(__name__)
router = APIRouter()


# ============================================================
# Status
# ============================================================

@router.get("/byok/status", response_model=BYOKStatusResponse)
def api_byok_status(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> BYOKStatusResponse:
    return byok_service.get_status(conn, user.id)


# ============================================================
# 订阅 + 激活码
# ============================================================

@router.post("/byok/purchase", response_model=BYOKPurchaseResponse)
def api_byok_purchase(
    req: BYOKPurchaseRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> BYOKPurchaseResponse:
    """
    [开发后门] 直接生成激活码 — 仅创始人邮箱可用。

    2026-06-05:用户购买正式走 /api/byok/payment/orders → 上传截图 → Vision LLM 审核流程。
    本 endpoint 保留仅作创始人本地测试入口。
    """
    if user.plan != "founder":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "BYOK_USE_PAYMENT_FLOW",
                "message": "请使用支付流程购买月卡:扫码付款 → 上传截图 → 自动审核",
            },
        )
    sub = byok_service.purchase_subscription(conn, user.id, months=req.months)
    return BYOKPurchaseResponse(
        code=sub.code,
        expires_at=sub.expires_at,
        price_cents=sub.price_cents,
    )


@router.post("/byok/activate", response_model=BYOKActivateResponse)
def api_byok_activate(
    req: BYOKActivateRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> BYOKActivateResponse:
    """输入激活码解锁。错误码透传业务异常 → 友好 HTTP 状态。"""
    try:
        sub = byok_service.activate_code(conn, user.id, req.code)
    except ValueError as exc:
        msg = str(exc)
        if msg == "code_not_found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "BYOK_CODE_NOT_FOUND", "message": "激活码不存在或不属于当前账号"},
            )
        if msg == "code_expired":
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={"code": "BYOK_CODE_EXPIRED", "message": "激活码已过期"},
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "BYOK_ACTIVATE_FAILED", "message": msg},
        )
    return BYOKActivateResponse(is_active=sub.is_active, expires_at=sub.expires_at)


@router.post("/byok/deactivate")
def api_byok_deactivate(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """主动停用 — 月卡仍在,下次可再激活。"""
    sub = byok_service.deactivate_current(conn, user.id)
    return {
        "deactivated": sub is not None,
        "expires_at": sub.expires_at if sub else None,
    }


# ============================================================
# 配置 CRUD
# ============================================================

@router.get("/byok/configs", response_model=BYOKConfigsListResponse)
def api_byok_list_configs(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> BYOKConfigsListResponse:
    cfgs = byok_service.list_configs(conn, user.id)
    return BYOKConfigsListResponse(
        configs=[byok_service.to_response(c) for c in cfgs],
    )


@router.put("/byok/configs", response_model=BYOKConfigResponse)
def api_byok_upsert_config(
    req: BYOKConfigUpsertRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> BYOKConfigResponse:
    cfg = byok_service.upsert_config(conn, user.id, req)
    return byok_service.to_response(cfg)


@router.post("/byok/configs/{config_id}/default", response_model=BYOKConfigResponse)
def api_byok_set_default(
    config_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> BYOKConfigResponse:
    cfg = byok_service.set_default_config(conn, user.id, config_id)
    if cfg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "BYOK_CONFIG_NOT_FOUND", "message": "配置不存在或不属于当前账号"},
        )
    return byok_service.to_response(cfg)


@router.delete("/byok/configs/{config_id}")
def api_byok_delete_config(
    config_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    ok = byok_service.delete_config(conn, user.id, config_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "BYOK_CONFIG_NOT_FOUND", "message": "配置不存在或不属于当前账号"},
        )
    return {"deleted": True}


@router.post("/byok/configs/{config_id}/test", response_model=BYOKTestResponse)
def api_byok_test_config(
    config_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> BYOKTestResponse:
    """
    测试 config 连通性 — 调一个最小 prompt 看是否能连。

    走 OpenAI compatible /chat/completions 协议。所有预设 provider 都兼容。
    """
    # 取明文 key
    row = conn.execute(
        "SELECT * FROM byok_configs WHERE id = ? AND user_id = ?",
        (config_id, user.id),
    ).fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "BYOK_CONFIG_NOT_FOUND", "message": "配置不存在或不属于当前账号"},
        )

    try:
        api_key = decrypt_api_key(row["api_key_encrypted"])
    except Exception:
        byok_service.update_test_result(conn, config_id, ok=False, error="key 解密失败")
        return BYOKTestResponse(ok=False, error="key 解密失败,请重新填写")

    base_url = row["base_url"].rstrip("/")
    model_name = row["model_name"]

    # v5 item2:图像模型走 /images/generations(chat/completions 测不了图像端点)。
    # 复用 JimengImageAdapter —— 自动按 base_url 域名适配 ark / siliconflow / 智谱字段差异。
    modality = (row["modality"] if "modality" in row.keys() else "text") or "text"
    if modality == "image":
        from app.services.llm_routing.adapters.jimeng_image import JimengImageAdapter
        start = time.time()
        try:
            res = JimengImageAdapter().generate(
                "一个白底居中的小红圆点,简笔",
                aspect_ratio="1:1",
                override_api_key=api_key,
                override_base_url=base_url,
                override_model=model_name,
            )
            latency = int((time.time() - start) * 1000)
            ok = bool(res.url)
            err = None if ok else "图像模型返回空(可能限流 / 内容审核)"
            byok_service.update_test_result(conn, config_id, ok=ok, error=err)
            return BYOKTestResponse(ok=ok, error=err, latency_ms=latency)
        except Exception as exc:  # noqa: BLE001
            latency = int((time.time() - start) * 1000)
            err = f"图像模型测试失败:{str(exc)[:200]}"
            byok_service.update_test_result(conn, config_id, ok=False, error=err)
            return BYOKTestResponse(ok=False, error=err, latency_ms=latency)

    # 文本模型:调最小 prompt
    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": "你好"}],
        "max_tokens": 10,
        "temperature": 0.1,
    }

    start = time.time()
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
    except requests.RequestException as exc:
        err = f"网络错误:{exc.__class__.__name__}"
        byok_service.update_test_result(conn, config_id, ok=False, error=err)
        return BYOKTestResponse(ok=False, error=err)

    latency = int((time.time() - start) * 1000)

    if resp.status_code != 200:
        # 提取人话错误
        try:
            j = resp.json()
            api_msg = j.get("error", {}).get("message") or j.get("message") or resp.text[:200]
        except Exception:
            api_msg = resp.text[:200]
        err = f"HTTP {resp.status_code}: {api_msg}"
        byok_service.update_test_result(conn, config_id, ok=False, error=err)
        return BYOKTestResponse(ok=False, error=err, latency_ms=latency)

    byok_service.update_test_result(conn, config_id, ok=True, error=None)
    return BYOKTestResponse(ok=True, error=None, latency_ms=latency)
