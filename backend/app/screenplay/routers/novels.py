"""小说摄入 API。

Endpoints(全部需要 Bearer JWT,user 通过 Depends 注入 → 透传到 service 层做 SQL 级隔离):
  POST   /novels                          上传 + 解析 + 落库,返摘要
  GET    /novels                          列出当前用户所有已上传作品
  GET    /novels/{novel_id}               单本详情(含章节列表,不含段落)
  DELETE /novels/{novel_id}               删除小说(级联清章节/段落/圣经/剧本)
  PATCH  /novels/{novel_id}/link          阶段 5 — 绑定 / 解绑到父平台 project
  GET    /chapters/{chapter_id}           单章全部段落
"""
from __future__ import annotations

from fastapi import Depends, APIRouter, Body, File, HTTPException, UploadFile, status
from app.deps import get_current_user
from app.models.user import User

from app.screenplay.config import settings
from app.screenplay.db.connection import get_connection
from app.screenplay.parsers import ParserError, parse_novel
from app.screenplay.services import huimeng_bridge, ingest_service

router = APIRouter(tags=["novels"], dependencies=[Depends(get_current_user)])


@router.post("/novels")
async def api_upload_novel(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> dict:
    """上传小说文件 → 解析 → 落库。

    Returns:
        201 Created + 摘要 dict
    """
    # 文件名 / 大小校验
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "MISSING_FILENAME", "message": "缺少文件名"},
        )

    content = await file.read()
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "FILE_TOO_LARGE",
                "message": f"文件超过 {settings.max_upload_size_mb}MB 限制",
            },
        )

    # 解析
    try:
        parsed = parse_novel(content, file.filename)
    except ParserError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PARSE_ERROR", "message": str(e)},
        )

    # 落库(user.id 写入 sp_novels.user_id)
    summary = ingest_service.persist_novel(parsed, file.filename, user_id=user.id)
    return summary


@router.get("/novels")
def api_list_novels(user: User = Depends(get_current_user)) -> dict:
    return {"items": ingest_service.list_novels(user_id=user.id)}


@router.get("/novels/{novel_id}")
def api_get_novel(
    novel_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    novel = ingest_service.get_novel(novel_id, user_id=user.id)
    if novel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOVEL_NOT_FOUND", "message": "小说不存在"},
        )
    return novel


@router.delete("/novels/{novel_id}", status_code=status.HTTP_204_NO_CONTENT)
def api_delete_novel(
    novel_id: str,
    user: User = Depends(get_current_user),
):
    """删除小说(级联清章节 / 段落 / 故事圣经 / screenplays)。"""
    deleted = ingest_service.delete_novel(novel_id, user_id=user.id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOVEL_NOT_FOUND", "message": "小说不存在"},
        )
    return None


@router.patch("/novels/{novel_id}/link")
def api_link_novel(
    novel_id: str,
    payload: dict = Body(...),
    user: User = Depends(get_current_user),
) -> dict:
    """阶段 5 — 把这本小说绑定到当前用户的某个浑晶 project(或解绑)。

    Body:
      { "project_id": "<uuid>" }   绑定
      { "project_id": null }        解绑

    绑定后,本小说的 6 个 LLM agent(scene_splitter / element_extractor /
    dialogue_attributor / adaptation_decision / screenplay_optimizer /
    story_bible_extractor)会通过 huimeng_bridge 读取该 project 的
    SP-2 角色驱动力 / SP-3 知识边界 / SP-4 状态时间线 / SP-7 关系正负极。

    Errors:
      404 NOVEL_NOT_FOUND      novel_id 不存在或不属于该用户
      404 PROJECT_NOT_FOUND    project_id 不存在或不属于该用户
    """
    project_id_raw = payload.get("project_id")
    project_id: str | None = None
    if project_id_raw is not None:
        if not isinstance(project_id_raw, str) or not project_id_raw.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"code": "INVALID_PROJECT_ID", "message": "project_id 必须是字符串"},
            )
        project_id = project_id_raw.strip()

    conn = get_connection()
    try:
        ok = huimeng_bridge.link_novel_to_project(
            conn, user_id=user.id, novel_id=novel_id, project_id=project_id,
        )
    finally:
        conn.close()

    if not ok:
        # 隔离 = 无知:不暴露具体哪个 ID 不存在,统一 404
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "LINK_FAILED",
                "message": "novel 或 project 不存在(或不属于当前用户)",
            },
        )
    return {"novel_id": novel_id, "linked_project_id": project_id}


@router.get("/chapters/{chapter_id}")
def api_get_chapter(
    chapter_id: str,
    user: User = Depends(get_current_user),
) -> dict:
    paragraphs = ingest_service.get_chapter_paragraphs(chapter_id, user_id=user.id)
    if paragraphs is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CHAPTER_NOT_FOUND", "message": "章节不存在"},
        )
    return {
        "chapter_id": chapter_id,
        "paragraphs": paragraphs,
    }
