"""多模型对比 API — 阶段 8.5(2026-06-08)+ BYOK 联动重构(2026-06-09)。

Endpoint:
  POST /screenplays/{id}/compare
    body: {
      scene_id,
      mode: "byok" | "platform",
      providers: [{ label, api_key?, base_url?, model }]
    }
    → ComparisonResult

两种 mode:
  - byok      用户自携密钥 — 必须 has_active_subscription=true
              providers 每条必须包含 api_key + base_url(用户在前端 localStorage 填)
  - platform  平台默认配置 — 任何用户可用,但消耗平台 LLM 配额
              providers 只用 label + model,后端用 settings.llm_api_key/base 填充

鉴权:
  - mode="byok" → 二次校验 has_active_subscription;false → 403 PAYMENT_REQUIRED
  - mode="platform" → 不强制 BYOK(给 free 用户体验);未来可接配额扣减

跨用户隔离:user_id 经 Depends(get_current_user) 注入,透传 service。
"""
from __future__ import annotations

from typing import Literal, Optional

from fastapi import Depends, APIRouter, HTTPException, status
from app.deps import get_current_user, get_db
from app.config import settings
from app.models.user import User
from app.services import byok_service
from pydantic import BaseModel, Field

from app.screenplay.services import model_comparison_service
from app.screenplay.services.model_comparison_service import ProviderConfig

router = APIRouter(tags=["compare"], dependencies=[Depends(get_current_user)])


class ProviderConfigBody(BaseModel):
    """前端提交的 provider 配置。

    mode=byok 时 api_key + base_url 必填;
    mode=platform 时这两字段忽略(后端用 settings 填),只用 label + model。
    """
    label: str = Field(..., min_length=1, max_length=50)
    api_key: Optional[str] = Field(default=None, max_length=200)
    base_url: Optional[str] = Field(default=None, max_length=200)
    model: str = Field(..., min_length=1, max_length=100)


class CompareRequestBody(BaseModel):
    scene_id: str
    # 2026-06-09 新增:运行模式 —— byok 用户自携 / platform 走平台默认配置
    mode: Literal["byok", "platform"] = Field(
        default="byok",
        description="byok=用户自携 API key;platform=走平台默认 LLM 配置",
    )
    providers: list[ProviderConfigBody] = Field(
        ..., min_length=2, max_length=5,
        description="对比 vendor 数组,2-5 个",
    )


@router.post("/screenplays/{screenplay_id}/compare")
async def api_compare_models(
    screenplay_id: str,
    body: CompareRequestBody,
    user: User = Depends(get_current_user),
    conn=Depends(get_db),
) -> dict:
    """对一个场景用多 provider 并行跑 element_extractor + fidelity 打分。

    Errors:
      403 BYOK_REQUIRED    mode=byok 但用户未开通自携密钥
      400 BAD_REQUEST      mode=byok 时 api_key/base_url 缺失
      404 NOT_FOUND        剧本不存在或不属于该用户
      404 SCENE_NOT_FOUND  场景不存在
      400 COMPARE_INPUT_INVALID  providers 不足 / 场景无原文 / 无在场角色
    """
    # ============================================================
    # 鉴权 + 校验(2026-06-09 新增)
    # ============================================================
    if body.mode == "byok":
        # 必须开通自携密钥
        byok_status = byok_service.get_status(conn, user.id)
        if not byok_status.has_active_subscription:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "BYOK_REQUIRED",
                    "message": "自携密钥模式需要开通自携密钥订阅",
                },
            )
        # 必填 api_key / base_url
        for i, p in enumerate(body.providers):
            if not (p.api_key and p.api_key.strip()):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "API_KEY_REQUIRED",
                        "message": f"Provider #{i + 1} ({p.label}) 缺少 API key",
                    },
                )
            if not (p.base_url and p.base_url.strip()):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "code": "BASE_URL_REQUIRED",
                        "message": f"Provider #{i + 1} ({p.label}) 缺少 base_url",
                    },
                )

    # ============================================================
    # 装配 ProviderConfig
    # ============================================================
    if body.mode == "platform":
        # 平台模式:所有 provider 共用 settings 的 LLM 配置,只 model 不同
        # 用户只提供 label + model;api_key/base_url 后端填
        platform_api_key = settings.llm_api_key
        platform_api_base = settings.llm_api_base
        if not (platform_api_key and platform_api_base):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "PLATFORM_LLM_NOT_CONFIGURED",
                    "message": "平台 LLM 暂时不可用,请尝试自携密钥模式",
                },
            )
        providers = [
            ProviderConfig(
                label=p.label,
                api_key=platform_api_key,
                base_url=platform_api_base,
                model=p.model,
            )
            for p in body.providers
        ]
    else:
        # BYOK 模式:用户自填(上面已校验非空)
        providers = [
            ProviderConfig(
                label=p.label,
                api_key=p.api_key or "",       # 上面已校验,此处 fallback 仅为类型安全
                base_url=p.base_url or "",
                model=p.model,
            )
            for p in body.providers
        ]

    try:
        result = await model_comparison_service.compare_scene_extraction(
            screenplay_id, user.id,
            scene_id=body.scene_id,
            providers=providers,
        )
    except model_comparison_service.ComparisonError as e:
        msg = str(e)
        if "剧本不存在" in msg or "场景" in msg and "不存在" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOT_FOUND", "message": msg},
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "COMPARE_INPUT_INVALID", "message": msg},
        )
    return model_comparison_service.to_dict(result)
