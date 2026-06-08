"""Sprint 6.A2 M2(2026-05-18)— project_scenes 路由。

只读端点(M2):
  GET /api/projects/{project_id}/scenes              列项目所有 scene
  GET /api/projects/{project_id}/scenes/{name}/regulars   常客角色

M8.C(2026-05-21)加 3 个编辑端点:
  PATCH  /api/projects/{project_id}/scenes/{scene_id}     编辑 name/desc/aliases
  DELETE /api/projects/{project_id}/scenes/{scene_id}     删除场景
  POST   /api/projects/{project_id}/scenes/merge         合并场景(source→target)
"""
from __future__ import annotations

import sqlite3
import urllib.parse
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.project_scene import (
    CreateProjectSceneRequest,
    MergeProjectScenesRequest,
    ProjectSceneResponse,
    SceneRegularResponse,
    SceneRegularsResponse,
    UpdateProjectSceneRequest,
)
from app.services.character_affinity import compute_scene_regulars
from app.services.project_service import get_project_or_403
from app.services.scene_extractor import (
    SceneConflict,
    SceneNotFound,
    create_scene,
    delete_scene,
    get_scene_by_name,
    list_scenes_for_project,
    merge_scenes,
    update_scene_fields,
)

router = APIRouter()


@router.get(
    "/projects/{project_id}/scenes",
    response_model=list[ProjectSceneResponse],
)
def api_list_project_scenes(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """列项目所有 scene(按 appearance_chunk_count 倒序;空 = 未抽过 / 没 LOCATION 实体)。"""
    get_project_or_403(conn, project_id, user.id)
    scenes = list_scenes_for_project(conn, project_id)
    return [asdict(s) for s in scenes]


@router.post(
    "/projects/{project_id}/scenes",
    response_model=ProjectSceneResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_create_project_scene(
    project_id: str,
    req: CreateProjectSceneRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """INIT.6(2026-05-21):初始态用户手动创建场景。

    场景名同项目内唯一,冲突返 409。appearance_chunk_count 初始为 0。
    """
    get_project_or_403(conn, project_id, user.id)
    try:
        scene = create_scene(
            conn,
            project_id,
            req.name,
            req.description,
            req.aliases,
        )
    except SceneConflict as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "SCENE_CONFLICT", "message": str(e)},
        ) from e
    return asdict(scene)


@router.get(
    "/projects/{project_id}/scenes/{scene_name}/regulars",
    response_model=SceneRegularsResponse,
)
def api_get_scene_regulars(
    project_id: str,
    scene_name: str,
    top_n: int = Query(10, ge=1, le=50),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """拉该 scene 的"常客"角色(共现次数 top-N,主角优先排前)。

    scene_name 是 URL path 段:需 URL-decode(中文名场景);FastAPI 默认已解码,
    但若调用方传 URL-encoded 串作 fallback 再 decode 一次保险。
    """
    get_project_or_403(conn, project_id, user.id)

    # 双层 URL decode 兜底(浏览器 / 客户端可能 encode 中文)
    try:
        decoded = urllib.parse.unquote(scene_name)
    except Exception:  # noqa: BLE001
        decoded = scene_name

    # 校验 scene 存在(避免任意 scene_name 查询拖空数据)
    scene = get_scene_by_name(conn, project_id, decoded)
    if scene is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={
                "code": "SCENE_NOT_FOUND",
                "message": f"项目 {project_id} 下没有名为「{decoded}」的场所",
            },
        )

    regulars = compute_scene_regulars(conn, project_id, decoded, top_n=top_n)
    return {
        "scene_name": decoded,
        "regulars": [asdict(r) for r in regulars],
    }


# ============================================================
# Sprint 6.A2 M8.C(2026-05-21)场景编辑 / 删除 / 合并
# ============================================================

@router.patch(
    "/projects/{project_id}/scenes/{scene_id}",
    response_model=ProjectSceneResponse,
)
def api_update_project_scene(
    project_id: str,
    scene_id: str,
    body: UpdateProjectSceneRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """编辑场景的 name / description / aliases。"""
    get_project_or_403(conn, project_id, user.id)
    try:
        scene = update_scene_fields(
            conn, scene_id, project_id,
            name=body.name,
            description=body.description,
            aliases=body.aliases,
        )
    except SceneNotFound as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "SCENE_NOT_FOUND", "message": str(e)},
        )
    except SceneConflict as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "SCENE_NAME_CONFLICT", "message": str(e)},
        )
    return asdict(scene)


@router.delete(
    "/projects/{project_id}/scenes/{scene_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def api_delete_project_scene(
    project_id: str,
    scene_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    """物理删除场景。"""
    get_project_or_403(conn, project_id, user.id)
    try:
        delete_scene(conn, scene_id, project_id)
    except SceneNotFound as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "SCENE_NOT_FOUND", "message": str(e)},
        )


@router.post(
    "/projects/{project_id}/scenes/merge",
    response_model=ProjectSceneResponse,
)
def api_merge_project_scenes(
    project_id: str,
    body: MergeProjectScenesRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """合并:source 加到 target,source 被删,返回 target。"""
    get_project_or_403(conn, project_id, user.id)
    try:
        target = merge_scenes(
            conn, project_id,
            source_scene_id=body.source_scene_id,
            target_scene_id=body.target_scene_id,
        )
    except SceneNotFound as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "SCENE_NOT_FOUND", "message": str(e)},
        )
    except SceneConflict as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "SCENE_MERGE_INVALID", "message": str(e)},
        )
    return asdict(target)
