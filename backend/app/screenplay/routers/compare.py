"""多模型对比 API — 阶段 8.5(2026-06-08)。

Endpoint:
  POST /screenplays/{id}/compare
    body: { scene_id, providers: [{ label, api_key, base_url, model }] }
    → ComparisonResult

跨用户隔离:user_id 经 Depends(get_current_user) 注入,透传 service。
"""
from __future__ import annotations

from fastapi import Depends, APIRouter, HTTPException, status
from app.deps import get_current_user
from app.models.user import User
from pydantic import BaseModel, Field

from app.screenplay.services import model_comparison_service
from app.screenplay.services.model_comparison_service import ProviderConfig

router = APIRouter(tags=["compare"], dependencies=[Depends(get_current_user)])


class ProviderConfigBody(BaseModel):
    label: str = Field(..., min_length=1, max_length=50)
    api_key: str = Field(..., min_length=1)
    base_url: str = Field(..., min_length=1)
    model: str = Field(..., min_length=1, max_length=100)


class CompareRequestBody(BaseModel):
    scene_id: str
    providers: list[ProviderConfigBody] = Field(
        ..., min_length=2, max_length=5,
        description="对比 vendor 数组,2-5 个",
    )


@router.post("/screenplays/{screenplay_id}/compare")
async def api_compare_models(
    screenplay_id: str,
    body: CompareRequestBody,
    user: User = Depends(get_current_user),
) -> dict:
    """对一个场景用多 provider 并行跑 element_extractor + fidelity 打分。

    Errors:
      404 NOT_FOUND     剧本不存在或不属于该用户
      404 SCENE_NOT_FOUND  场景不存在
      400 BAD_REQUEST   providers 不足 / 场景无原文 / 无在场角色
    """
    providers = [
        ProviderConfig(
            label=p.label, api_key=p.api_key,
            base_url=p.base_url, model=p.model,
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
