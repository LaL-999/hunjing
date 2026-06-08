"""故事圣经 API(阶段 3.5:全部 user_id 隔离)。

Endpoints:
  POST   /novels/{novel_id}/story-bible           手动导入(JSON body)
  POST   /novels/{novel_id}/story-bible/auto      LLM 自动抽取
  GET    /novels/{novel_id}/story-bible           查看圣经

所有 endpoint 都注入 user(JWT)→ 透传到 service 层校验 novel 归属。
跨用户访问 novel 统一返 404 NOVEL_NOT_FOUND(不暴露"存在但是别人的")。
"""
from __future__ import annotations

from fastapi import Depends, APIRouter, Body, HTTPException, status
from app.deps import get_current_user
from app.models.user import User

from app.screenplay.services import story_bible_service

router = APIRouter(tags=["story-bible"], dependencies=[Depends(get_current_user)])


@router.post("/novels/{novel_id}/story-bible")
def api_import_bible(
    novel_id: str,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
) -> dict:
    """手动导入 JSON 故事圣经(覆盖既有)。"""
    try:
        return story_bible_service.import_bible_from_json(
            novel_id, payload, user_id=user.id,
        )
    except ValueError as e:
        msg = str(e)
        if "不存在" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOVEL_NOT_FOUND", "message": msg},
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "BIBLE_INVALID", "message": msg},
        )


@router.post("/novels/{novel_id}/story-bible/auto")
def api_extract_bible(
    novel_id: str,
    max_chapters: int = 3,
    user: User = Depends(get_current_user),
) -> dict:
    """LLM 自动从前 N 章抽取(覆盖既有)。

    Query 参数:
        max_chapters: 喂给 LLM 的章数(默认 3,题目要求 ≥ 3)
    """
    if max_chapters < 1 or max_chapters > 10:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_MAX_CHAPTERS", "message": "max_chapters 必须在 1-10"},
        )
    try:
        return story_bible_service.extract_bible_with_llm(
            novel_id, user_id=user.id, max_chapters=max_chapters,
        )
    except ValueError as e:
        msg = str(e)
        if "不存在" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "NOVEL_NOT_FOUND", "message": msg},
            )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "LLM_EXTRACT_FAILED", "message": msg},
        )


@router.get("/novels/{novel_id}/story-bible")
def api_get_bible(
    novel_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    bible = story_bible_service.get_bible(novel_id, user_id=user.id)
    if bible is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "BIBLE_NOT_FOUND", "message": "该作品还没创建故事圣经"},
        )
    return bible
