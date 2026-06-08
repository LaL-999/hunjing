"""Outline 路由 — Sprint 6.A2 M6(2026-05-20)。

提供用户审核 / 编辑 / 批准 outline 的 5 个端点:

- GET    /api/simulations/{sim_id}/outline             拉 outline + 全部 scenes
- POST   /api/simulations/{sim_id}/outline/regenerate  重新生成 outline 草稿(失败后兜底)
- PATCH  /api/simulations/{sim_id}/outline/global      编辑全局字段(theme / arc)
- PATCH  /api/simulations/{sim_id}/outline/scenes/{scene_id}  编辑某幕
- POST   /api/simulations/{sim_id}/outline/approve     批准 outline → kick_off
"""
from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, Body, Depends, HTTPException, status

from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.outline import (
    ApproveOutlineRequest,
    InsertOutlineSceneRequest,
    OutlineSceneResponse,
    ReorderOutlineScenesRequest,
    SimulationOutlineResponse,
    UpdateOutlineGlobalRequest,
    UpdateOutlineSceneRequest,
)
from app.services.outline_editor import (
    OutlineNotFound,
    OutlineStateMismatch,
    approve_outline,
    delete_scene,
    insert_scene_at,
    reorder_scenes,
    update_global,
    update_scene_fields,
)
from app.services.outline_generator import (
    continue_outline_generation,
    create_outline_draft,
    get_outline_with_scenes,
)
from app.services.project_service import get_project_or_403

logger = logging.getLogger(__name__)
router = APIRouter()


def _serialize_outline(outline, scenes) -> SimulationOutlineResponse:
    return SimulationOutlineResponse(
        id=outline.id,
        simulation_id=outline.simulation_id,
        state=outline.state,
        total_scenes_planned=outline.total_scenes_planned,
        global_theme=outline.global_theme,
        global_arc=outline.global_arc,
        user_approved_at=outline.user_approved_at,
        error_message=outline.error_message,
        created_at=outline.created_at,
        updated_at=outline.updated_at,
        scenes=[
            OutlineSceneResponse(
                id=s.id,
                outline_id=s.outline_id,
                scene_index=s.scene_index,
                scene_summary=s.scene_summary,
                scene_purpose=s.scene_purpose,
                location=s.location,
                time_anchor=s.time_anchor,
                characters_present=s.characters_present,
                key_events=s.key_events,
                key_props=s.key_props,
                transition_from_last=s.transition_from_last,
                user_edited=s.user_edited,
                state=s.state,
                generated_simulation_scene_id=s.generated_simulation_scene_id,
                error_message=s.error_message,
                created_at=s.created_at,
                updated_at=s.updated_at,
                tension_percent=s.tension_percent,
                pacing_tempo=s.pacing_tempo,
            )
            for s in scenes
        ],
    )


def _verify_sim_belongs_to_user(
    conn: sqlite3.Connection, sim_id: str, user_id: str,
) -> str:
    """验证 sim 属于该用户。返回 project_id 用于后续鉴权链路。"""
    from app.db import fetch_one
    row = fetch_one(
        conn,
        "SELECT project_id, user_id FROM simulations WHERE id=?",
        (sim_id,),
    )
    if row is None or row["user_id"] != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SIM_NOT_FOUND", "message": "sim 不存在或不属于当前用户"},
        )
    return row["project_id"]


@router.get(
    "/api/simulations/{sim_id}/outline",
    response_model=SimulationOutlineResponse,
)
def get_outline(
    sim_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """拉 outline + 全部 scenes(给前端 OutlineReviewView)。"""
    _verify_sim_belongs_to_user(conn, sim_id, user.id)
    try:
        outline, scenes = get_outline_with_scenes(conn, sim_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "OUTLINE_NOT_FOUND", "message": str(e)},
        )
    return _serialize_outline(outline, scenes)


@router.post(
    "/api/simulations/{sim_id}/outline/continue",
    response_model=SimulationOutlineResponse,
)
def continue_outline(
    sim_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """M6-fix3:从断点续生成 outline。

    场景:outline LLM 输出被截断,只生成 N/total 幕 → state='awaiting_user' +
    error_message='截断';用户点"继续生成"接力补完(每次最多 15 幕,可多次调用)。
    """
    _verify_sim_belongs_to_user(conn, sim_id, user.id)
    try:
        continue_outline_generation(conn, sim_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "OUTLINE_CONTINUE_INVALID", "message": str(e)},
        )

    outline, scenes = get_outline_with_scenes(conn, sim_id)
    return _serialize_outline(outline, scenes)


@router.post(
    "/api/simulations/{sim_id}/outline/regenerate",
    response_model=SimulationOutlineResponse,
    status_code=status.HTTP_201_CREATED,
)
def regenerate_outline(
    sim_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
    strengthen_diversity: bool = Body(default=False, embed=True),
):
    """outline 失败 / 用户不满意时手动重新生成。

    只允许 outline 在 'failed' 或不存在时调用。

    M8.D(2026-05-21):strengthen_diversity 参数 — 前端 OutlineReviewView 在
    diversity chip 显警告时(场景过于单一)自动传 True,让 LLM 看到额外多样性指令。
    """
    _verify_sim_belongs_to_user(conn, sim_id, user.id)

    # 拉旧 outline(若有)
    from app.db import fetch_one, execute
    existing = fetch_one(
        conn,
        "SELECT id, state FROM simulation_outlines WHERE simulation_id=?",
        (sim_id,),
    )
    if existing is not None:
        if existing["state"] in ("approved", "generating", "done"):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "OUTLINE_LOCKED",
                    "message": f"outline 当前 state={existing['state']},不许重生成",
                },
            )
        # awaiting_user / failed / drafting → 删除老的允许重生
        execute(
            conn,
            "DELETE FROM simulation_outlines WHERE id=?",
            (existing["id"],),
        )
        conn.commit()

    try:
        create_outline_draft(conn, sim_id, strengthen_diversity=strengthen_diversity)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "OUTLINE_GEN_FAILED", "message": str(e)},
        )

    outline, scenes = get_outline_with_scenes(conn, sim_id)
    return _serialize_outline(outline, scenes)


@router.patch(
    "/api/simulations/{sim_id}/outline/global",
    response_model=SimulationOutlineResponse,
)
def patch_outline_global(
    sim_id: str,
    body: UpdateOutlineGlobalRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """编辑 outline 主表 global_theme / global_arc。"""
    _verify_sim_belongs_to_user(conn, sim_id, user.id)
    try:
        update_global(
            conn, sim_id, user.id,
            global_theme=body.global_theme,
            global_arc=body.global_arc,
        )
    except OutlineNotFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "OUTLINE_NOT_FOUND", "message": str(e)},
        )
    except OutlineStateMismatch as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "OUTLINE_LOCKED", "message": str(e)},
        )
    outline, scenes = get_outline_with_scenes(conn, sim_id)
    return _serialize_outline(outline, scenes)


@router.patch(
    "/api/simulations/{sim_id}/outline/scenes/{scene_id}",
    response_model=OutlineSceneResponse,
)
def patch_outline_scene(
    sim_id: str,
    scene_id: str,
    body: UpdateOutlineSceneRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """编辑某 outline_scene 的字段。"""
    _verify_sim_belongs_to_user(conn, sim_id, user.id)
    try:
        update_scene_fields(
            conn, scene_id,
            sim_id_for_auth=sim_id,
            user_id_for_auth=user.id,
            scene_summary=body.scene_summary,
            scene_purpose=body.scene_purpose,
            location=body.location,
            time_anchor=body.time_anchor,
            characters_present=body.characters_present,
            key_events=body.key_events,
            key_props=body.key_props,
            transition_from_last=body.transition_from_last,
        )
    except OutlineNotFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "OUTLINE_SCENE_NOT_FOUND", "message": str(e)},
        )
    except OutlineStateMismatch as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "OUTLINE_LOCKED", "message": str(e)},
        )

    # 返回更新后的 scene
    from app.db import fetch_one
    from app.models.outline_scene import OutlineScene
    row = fetch_one(conn, "SELECT * FROM outline_scenes WHERE id=?", (scene_id,))
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "SCENE_VANISHED"})
    s = OutlineScene.from_row(row)
    return OutlineSceneResponse(
        id=s.id, outline_id=s.outline_id, scene_index=s.scene_index,
        scene_summary=s.scene_summary, scene_purpose=s.scene_purpose,
        location=s.location, time_anchor=s.time_anchor,
        characters_present=s.characters_present,
        key_events=s.key_events, key_props=s.key_props,
        transition_from_last=s.transition_from_last,
        user_edited=s.user_edited, state=s.state,
        generated_simulation_scene_id=s.generated_simulation_scene_id,
        error_message=s.error_message,
        created_at=s.created_at, updated_at=s.updated_at,
        tension_percent=s.tension_percent,
        pacing_tempo=s.pacing_tempo,
    )


# ============================================================
# Sprint 6.A2 M8.B(2026-05-21)— 加幕 / 删幕 / 重排
# ============================================================

@router.post(
    "/api/simulations/{sim_id}/outline/scenes",
    response_model=SimulationOutlineResponse,
)
def post_insert_outline_scene(
    sim_id: str,
    body: InsertOutlineSceneRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """在 outline 的 position 插入新幕(0-based;N 表示尾部追加)。

    Returns:
      整个 outline + scenes(前端用响应内更新 store,避免再 GET 一次)
    """
    _verify_sim_belongs_to_user(conn, sim_id, user.id)
    try:
        insert_scene_at(
            conn, sim_id, user.id, body.position,
            scene_data={
                "scene_summary": body.scene_summary,
                "scene_purpose": body.scene_purpose,
                "location": body.location,
                "time_anchor": body.time_anchor,
                "characters_present": body.characters_present,
                "key_events": body.key_events,
                "transition_from_last": body.transition_from_last,
            },
        )
    except OutlineNotFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "OUTLINE_NOT_FOUND", "message": str(e)},
        )
    except OutlineStateMismatch as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "OUTLINE_LOCKED", "message": str(e)},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "OUTLINE_INSERT_INVALID", "message": str(e)},
        )

    outline, scenes = get_outline_with_scenes(conn, sim_id)
    return _serialize_outline(outline, scenes)


@router.delete(
    "/api/simulations/{sim_id}/outline/scenes/{scene_id}",
    response_model=SimulationOutlineResponse,
)
def delete_outline_scene(
    sim_id: str,
    scene_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """删 outline 中某幕,后续幕 scene_index 整体 -1。"""
    _verify_sim_belongs_to_user(conn, sim_id, user.id)
    try:
        delete_scene(
            conn, scene_id,
            sim_id_for_auth=sim_id,
            user_id_for_auth=user.id,
        )
    except OutlineNotFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "OUTLINE_SCENE_NOT_FOUND", "message": str(e)},
        )
    except OutlineStateMismatch as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "OUTLINE_LOCKED", "message": str(e)},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "OUTLINE_CANNOT_DELETE", "message": str(e)},
        )

    outline, scenes = get_outline_with_scenes(conn, sim_id)
    return _serialize_outline(outline, scenes)


@router.patch(
    "/api/simulations/{sim_id}/outline/reorder",
    response_model=SimulationOutlineResponse,
)
def patch_reorder_outline_scenes(
    sim_id: str,
    body: ReorderOutlineScenesRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """按 ordered_scene_ids 重排 outline 全部幕的 scene_index。"""
    _verify_sim_belongs_to_user(conn, sim_id, user.id)
    try:
        reorder_scenes(conn, sim_id, user.id, body.ordered_scene_ids)
    except OutlineNotFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "OUTLINE_NOT_FOUND", "message": str(e)},
        )
    except OutlineStateMismatch as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "OUTLINE_LOCKED", "message": str(e)},
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "OUTLINE_REORDER_INVALID", "message": str(e)},
        )

    outline, scenes = get_outline_with_scenes(conn, sim_id)
    return _serialize_outline(outline, scenes)


@router.post(
    "/api/simulations/{sim_id}/outline/approve",
    response_model=SimulationOutlineResponse,
)
def post_approve_outline(
    sim_id: str,
    body: ApproveOutlineRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """批准 outline → outline.state='approved' → 触发 sim kick_off 启动逐幕生成。"""
    _verify_sim_belongs_to_user(conn, sim_id, user.id)
    if not body.confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "CONFIRM_REQUIRED", "message": "必须 confirm=true 才批准"},
        )

    try:
        approve_outline(conn, sim_id, user.id)
    except OutlineNotFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "OUTLINE_NOT_FOUND", "message": str(e)},
        )
    except OutlineStateMismatch as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "OUTLINE_NOT_AWAITING", "message": str(e)},
        )

    # 触发 sim kick_off(异步)
    from app.services import simulation_service
    try:
        simulation_service.kick_off(sim_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"kick_off after approve outline failed sim={sim_id}: {e}")
        # 不阻塞 — 用户可在前端重试或走 resume 路径

    outline, scenes = get_outline_with_scenes(conn, sim_id)
    return _serialize_outline(outline, scenes)
