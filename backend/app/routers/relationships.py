"""Relationship 路由 — 角色间关系网。

- POST   /api/projects/{project_id}/relationships    嵌套创建(校验 source/target 同项目)
- GET    /api/projects/{project_id}/relationships    嵌套列出
- PATCH  /api/relationships/{relationship_id}        顶层更新
- DELETE /api/relationships/{relationship_id}        顶层删除
"""
from __future__ import annotations

import sqlite3
import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status

from app.db import execute, fetch_all, fetch_one
from app.deps import get_current_user, get_db
from app.models.relationship import Relationship
from app.models.user import User
from app.schemas.relationship import (
    CreateRelationshipPhaseRequest,
    CreateRelationshipRequest,
    RelationshipPhaseResponse,
    RelationshipResponse,
    UpdateRelationshipPhaseRequest,
    UpdateRelationshipRequest,
)
from app.services.project_service import (
    get_project_or_403,
    get_relationship_or_403,
    iso_now,
)

router = APIRouter()


@router.post(
    "/projects/{project_id}/relationships",
    response_model=RelationshipResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_create_relationship(
    project_id: str,
    req: CreateRelationshipRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    get_project_or_403(conn, project_id, user.id)

    # 校验顺序:① 自环(用户错误,语义最直接) ② endpoints 是否在本项目
    #
    # 注意:② 用 SELECT COUNT(*) WHERE id IN (?, ?) 时,如果 source_id == target_id,
    # SQL 的 IN 集合 dedupe 后只剩 1 个 ID,COUNT 也只能拿到 1,会误报为
    # INVALID_RELATIONSHIP_ENDPOINTS。所以 ① 必须在 ② 之前。
    if req.source_id == req.target_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "SELF_RELATIONSHIP",
                "message": "角色不能与自己建立关系",
            },
        )

    cnt_row = fetch_one(
        conn,
        "SELECT COUNT(*) AS cnt FROM characters "
        "WHERE id IN (?, ?) AND project_id=?",
        (req.source_id, req.target_id, project_id),
    )
    if not cnt_row or cnt_row["cnt"] != 2:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "INVALID_RELATIONSHIP_ENDPOINTS",
                "message": "source_id 和 target_id 必须都属于该项目下的角色",
            },
        )

    rel_id = str(uuid.uuid4())
    now = iso_now()
    execute(
        conn,
        "INSERT INTO relationships "
        "(id, project_id, source_id, target_id, type, description, color, strength, created_at, polarity) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            rel_id, project_id, req.source_id, req.target_id,
            req.type, req.description, req.color, req.strength, now,
            req.polarity,  # SP-7
        ),
    )
    conn.commit()
    return asdict(Relationship(
        id=rel_id, project_id=project_id,
        source_id=req.source_id, target_id=req.target_id,
        type=req.type, description=req.description, color=req.color,
        strength=req.strength,
        created_at=now,
        polarity=req.polarity,  # SP-7
    ))


@router.get(
    "/projects/{project_id}/relationships",
    response_model=list[RelationshipResponse],
)
def api_list_relationships(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    get_project_or_403(conn, project_id, user.id)
    rows = fetch_all(
        conn,
        "SELECT * FROM relationships WHERE project_id=? ORDER BY created_at ASC",
        (project_id,),
    )
    return [asdict(Relationship.from_row(r)) for r in rows]


# Sprint 2.C 反事实变量:relationship PATCH 跟踪 type / description
# Sprint 6.A2 M1:current_phase_id 是用户切换"当前生效阶段"的操作,**不算反事实**
#   (反事实变量是改 phase 内字段如 type,与切 phase 是两个不同动作)
# SP-7(2026-05-28):polarity 加进反事实跟踪 — 改"正负"= 重塑关系底色,影响推演
_RELATIONSHIP_TRACKED_FIELDS = {"type", "description", "polarity"}


@router.patch(
    "/relationships/{relationship_id}",
    response_model=RelationshipResponse,
)
def api_update_relationship(
    relationship_id: str,
    req: UpdateRelationshipRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    rel = get_relationship_or_403(conn, relationship_id, user.id)
    updates = req.model_dump(exclude_unset=True)
    if not updates:
        return asdict(rel)

    # === Sprint 2.C:先 record 反事实 ===
    from app.services.counterfactual_service import record_change
    rel_dict = asdict(rel)
    for field, new_val in updates.items():
        if field not in _RELATIONSHIP_TRACKED_FIELDS:
            continue
        old_val = rel_dict.get(field)
        try:
            record_change(
                conn, rel.project_id, "relationship", relationship_id,
                field, old_val, new_val, user.id,
            )
        except ValueError:
            pass

    set_parts = []
    values: list = []
    for field, value in updates.items():
        set_parts.append(f"{field}=?")
        values.append(value)
    values.append(relationship_id)

    execute(
        conn,
        f"UPDATE relationships SET {', '.join(set_parts)} WHERE id=?",
        tuple(values),
    )
    conn.commit()
    return asdict(get_relationship_or_403(conn, relationship_id, user.id))


@router.delete(
    "/relationships/{relationship_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,  # FastAPI 0.110 + Py3.13 把 `-> None` 推成 NoneType,触发 204 无 body 断言
)
def api_delete_relationship(
    relationship_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    get_relationship_or_403(conn, relationship_id, user.id)
    execute(conn, "DELETE FROM relationships WHERE id=?", (relationship_id,))
    conn.commit()


# ============================================================
# Sprint 6.A2 M1(2026-05-18):关系时间轴 phase 端点
# ============================================================
#
# 4 个端点:
#   GET    /relationships/{rid}/phases       列出该 relationship 的所有 phase(按 index 升序)
#   POST   /relationships/{rid}/phases       加新 phase 到末尾(auto phase_index = max+1)
#   PATCH  /relationship_phases/{pid}        更新 phase 字段(只改非 None 的)
#   DELETE /relationship_phases/{pid}        删 phase(自动修复 current_phase_id)
#
# 老数据自动迁移:首次 GET phases 时自动建 phase[0](type/strength 同步 relationship 字段)。

@router.get("/projects/{project_id}/relationship_phase_counts")
def api_relationship_phase_counts(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict[str, int]:
    """批量返回项目内每条 relationship 的 phase 数({relId: count}),替代前端 N+1。

    无 phase 行的关系不在返回里;前端按隐式 1 阶段补(与单条 GET 自动迁移语义一致)。
    """
    get_project_or_403(conn, project_id, user.id)
    from app.services.relationship_phase_service import count_phases_by_project
    return count_phases_by_project(conn, project_id)


@router.get(
    "/relationships/{relationship_id}/phases",
    response_model=list[RelationshipPhaseResponse],
)
def api_list_relationship_phases(
    relationship_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """列出关系的所有 phase(按 phase_index 升序)。

    老数据自动迁移:无 phase 时建 phase[0] 与 relationship.type/strength 同步。
    """
    get_relationship_or_403(conn, relationship_id, user.id)
    from app.services.relationship_phase_service import list_phases
    phases = list_phases(conn, relationship_id)
    return [asdict(p) for p in phases]


@router.post(
    "/relationships/{relationship_id}/phases",
    response_model=RelationshipPhaseResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_create_relationship_phase(
    relationship_id: str,
    req: CreateRelationshipPhaseRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """加新 phase 到关系末尾(自动 phase_index = max+1;auto_set_current 默认 true)。"""
    get_relationship_or_403(conn, relationship_id, user.id)
    from app.services.relationship_phase_service import add_phase
    try:
        phase = add_phase(
            conn, relationship_id,
            type=req.type,
            strength=req.strength,
            start_anchor=req.start_anchor,
            end_anchor=req.end_anchor,
            trigger_event_id=req.trigger_event_id,
            notes=req.notes,
            auto_set_current=req.auto_set_current,
        )
    except ValueError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_PHASE", "message": str(e)},
        )
    return asdict(phase)


@router.patch(
    "/relationship_phases/{phase_id}",
    response_model=RelationshipPhaseResponse,
)
def api_update_relationship_phase(
    phase_id: str,
    req: UpdateRelationshipPhaseRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """部分更新 phase 字段(只改非 None 的)。"""
    from app.services.relationship_phase_service import (
        get_phase_or_404,
        update_phase,
    )
    # 鉴权 + 校验属于该 user
    get_phase_or_404(conn, phase_id, user.id)
    updates = req.model_dump(exclude_unset=True)
    if not updates:
        return asdict(get_phase_or_404(conn, phase_id, user.id))

    try:
        phase = update_phase(conn, phase_id, **updates)
    except ValueError as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_PHASE", "message": str(e)},
        )
    return asdict(phase)


@router.delete(
    "/relationship_phases/{phase_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def api_delete_relationship_phase(
    phase_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    """删 phase(自动修复 current_phase_id 指向剩余最大 index 的 phase)。"""
    from app.services.relationship_phase_service import (
        delete_phase,
        get_phase_or_404,
    )
    # 鉴权
    get_phase_or_404(conn, phase_id, user.id)
    try:
        delete_phase(conn, phase_id)
    except ValueError as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "PHASE_NOT_FOUND", "message": str(e)},
        )


# ============================================================
# SP-7.1(2026-05-30):AI 一次推断项目所有关系 polarity
# ============================================================

@router.post("/projects/{project_id}/infer/relationship_polarity")
def api_infer_relationship_polarity(
    project_id: str,
    overwrite: bool = False,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """LLM 推断项目所有关系的 polarity.

    overwrite=False(默认):仅填 polarity 为 NULL 的关系(保留用户手动 chip 切过的)
    overwrite=true:全部更新(用户选"重置极性")

    失败任何阶段都内部兜底,绝不 5xx.
    """
    get_project_or_403(conn, project_id, user.id)
    from app.services.relationship_polarity_inferer import (
        infer_polarity_for_project,
        cache_polarity_to_project,
    )
    result = infer_polarity_for_project(conn, project_id)
    cache_report = cache_polarity_to_project(
        conn, project_id, result, overwrite=overwrite,
    )
    return {
        "applied": cache_report["applied"],
        "updated_count": cache_report["updated_count"],
        "skipped_count": cache_report["skipped_count"],
        "no_match_count": cache_report["no_match_count"],
        "reasoning": result.get("reasoning", ""),
        "raw_decisions_count": len(result.get("polarity_decisions") or []),
    }
