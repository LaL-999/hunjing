"""角色档案 + 关系图 API — 阶段 8.2(2026-06-08)。

Endpoint:
  GET /novels/{novel_id}/characters  → 角色卡 + 关系图 + 桥接资产

跨用户隔离:user_id 通过 Depends(get_current_user) 注入,透传 service 层。
"""
from __future__ import annotations

from dataclasses import asdict

from fastapi import Depends, APIRouter, HTTPException, status
from app.deps import get_current_user
from app.models.user import User

from app.screenplay.services import character_profile_service

router = APIRouter(tags=["character-profiles"], dependencies=[Depends(get_current_user)])


@router.get("/novels/{novel_id}/characters")
def api_get_character_profiles(
    novel_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    """角色页 + 关系图数据。

    Returns:
      {
        "novel_id": "...",
        "screenplay_id": "..." | "",  // 空 = 未 compose,仅 bible 数据
        "linked_project_id": "..." | null,
        "characters": [CharacterProfile],
        "graph": { nodes: [GraphNode], edges: [GraphEdge] }
      }

    Errors:
      404 NOVEL_NOT_FOUND  novel 不存在或不属于该用户
    """
    result = character_profile_service.get_character_profiles(
        novel_id, user_id=user.id,
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOVEL_NOT_FOUND", "message": "小说不存在"},
        )
    return asdict(result)
