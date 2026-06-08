"""分集规划 API — 阶段 8.4 MVP(2026-06-08)。

Endpoint:
  POST /novels/{novel_id}/plan-episodes  body: { target_minutes_per_ep? }
    → EpisodePlan

跨用户隔离:user_id 经 Depends(get_current_user) 注入,透传 service。
"""
from __future__ import annotations

from fastapi import Depends, APIRouter, HTTPException, status
from app.deps import get_current_user
from app.models.user import User
from pydantic import BaseModel, Field

from app.screenplay.services import episode_planner

router = APIRouter(tags=["episodes"], dependencies=[Depends(get_current_user)])


class PlanEpisodesBody(BaseModel):
    target_minutes_per_ep: float = Field(
        default=3.0,
        ge=0.5,
        le=30.0,
        description="单集目标时长(分钟);短剧 2-3,长剧 8-12",
    )


@router.post("/novels/{novel_id}/plan-episodes")
def api_plan_episodes(
    novel_id: str,
    body: PlanEpisodesBody = PlanEpisodesBody(),
    user: User = Depends(get_current_user),
) -> dict:
    """对该 novel 的最新剧本做分集规划(MVP 规则版)。

    Errors:
      404 NOVEL_NOT_FOUND     作品未生成剧本
      400 PLAN_FAILED         规划失败(yaml 解析 / 场景缺失)
    """
    try:
        plan = episode_planner.plan_episodes(
            novel_id,
            user_id=user.id,
            target_minutes_per_ep=body.target_minutes_per_ep,
        )
    except episode_planner.EpisodePlanError as e:
        # 区分两类:无剧本 vs 规划本身失败
        msg = str(e)
        if "尚未生成剧本" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "SCREENPLAY_NOT_FOUND", "message": msg},
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PLAN_FAILED", "message": msg},
        )
    return episode_planner.to_dict(plan)
