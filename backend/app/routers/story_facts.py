"""SP-3(2026-05-28)— 知识边界 router.

暴露 story_facts + character_knowledge 给前端 StoryFactsPanel.

端点:
  GET    /projects/{pid}/story_facts                      列项目所有事实
                                                            ?include_knowledge=true
                                                            → 每条 fact 带 known_by_character_ids list
  POST   /projects/{pid}/story_facts                       录入新事实
  DELETE /story_facts/{fid}                                 删事实(级联清 character_knowledge)
  POST   /story_facts/{fid}/known_by/{cid}                 标 character 知道
                                                            body: known_since_scene / confidence
  DELETE /story_facts/{fid}/known_by/{cid}                 撤销知道
  GET    /characters/{cid}/known_facts                     该角色已知事实清单
                                                            ?up_to_scene=N → 截至第 N 幕末

鉴权:project_id / character_id 必须属于当前 user(JOIN projects.user_id).
"""
from __future__ import annotations

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.db import fetch_all, fetch_one
from app.deps import get_current_user, get_db
from app.models.user import User
from app.services import character_knowledge_service as kb
from app.services.project_service import (
    get_character_or_403,
    get_project_or_403,
)


router = APIRouter()


# ============================================================
# Schemas
# ============================================================

class CreateStoryFactRequest(BaseModel):
    description: str = Field(..., min_length=1, max_length=500)
    first_revealed_scene: Optional[int] = Field(None, ge=0, le=9999)
    is_sensitive: bool = False


class MarkKnownRequest(BaseModel):
    known_since_scene: Optional[int] = Field(None, ge=0, le=9999)
    confidence: str = Field("confirmed", pattern="^(suspected|confirmed|wrong)$")


# ============================================================
# 列项目事实 + 可选带 knowledge map
# ============================================================

@router.get("/projects/{project_id}/story_facts")
def api_list_project_facts(
    project_id: str,
    include_knowledge: bool = Query(False, alias="include_knowledge"),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """列项目所有事实.

    include_knowledge=true → 每条 fact 带 known_by[]
    (list of {character_id, known_since_scene, confidence})
    便于前端一次性渲染"谁知道这条事实"矩阵.
    """
    get_project_or_403(conn, project_id, user.id)

    facts = kb.list_project_facts(conn, project_id)

    if include_knowledge:
        # 批量查所有 (fact_id → list of knowledge entries)
        fact_ids = [f["id"] for f in facts]
        knowledge_map: dict[str, list[dict]] = {fid: [] for fid in fact_ids}
        if fact_ids:
            # 用 IN 一次查全部 — 项目级 fact 通常 < 100,避免 N+1
            placeholders = ",".join("?" * len(fact_ids))
            rows = fetch_all(
                conn,
                f"SELECT k.fact_id, k.character_id, k.known_since_scene, k.confidence, "
                f"       c.name AS character_name "
                f"FROM character_knowledge k "
                f"JOIN characters c ON c.id = k.character_id "
                f"WHERE k.fact_id IN ({placeholders})",
                tuple(fact_ids),
            )
            for r in rows:
                knowledge_map[r["fact_id"]].append({
                    "character_id": r["character_id"],
                    "character_name": r["character_name"],
                    "known_since_scene": r["known_since_scene"],
                    "confidence": r["confidence"],
                })

        # 给每条 fact 加 known_by
        for f in facts:
            f["known_by"] = knowledge_map.get(f["id"], [])

    # 一致 — is_sensitive INT → bool
    for f in facts:
        f["is_sensitive"] = bool(f.get("is_sensitive", 0))

    return {"facts": facts}


# ============================================================
# 录入新事实
# ============================================================

@router.post(
    "/projects/{project_id}/story_facts",
    status_code=status.HTTP_201_CREATED,
)
def api_create_story_fact(
    project_id: str,
    req: CreateStoryFactRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """录入项目级事实(后期 LLM 抽事实模块也调本接口)."""
    get_project_or_403(conn, project_id, user.id)
    fid = kb.register_fact(
        conn,
        project_id=project_id,
        description=req.description,
        first_revealed_scene=req.first_revealed_scene,
        is_sensitive=req.is_sensitive,
    )
    # 返回完整 fact 行
    row = fetch_one(conn, "SELECT * FROM story_facts WHERE id=?", (fid,))
    d = dict(row)
    d["is_sensitive"] = bool(d.get("is_sensitive", 0))
    d["known_by"] = []
    return d


# ============================================================
# 删事实
# ============================================================

@router.delete(
    "/story_facts/{fact_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def api_delete_story_fact(
    fact_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    """删事实 + 级联 character_knowledge(SQLite ON DELETE CASCADE).

    鉴权:JOIN projects.user_id.
    """
    row = fetch_one(
        conn,
        "SELECT f.id FROM story_facts f "
        "JOIN projects p ON p.id = f.project_id "
        "WHERE f.id=? AND p.user_id=?",
        (fact_id, user.id),
    )
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "FACT_NOT_FOUND", "message": "事实不存在或无权限"},
        )
    # 显式先删 knowledge(防 migration 069 没加 ON DELETE CASCADE 的兜底)
    conn.execute(
        "DELETE FROM character_knowledge WHERE fact_id=?", (fact_id,)
    )
    conn.execute("DELETE FROM story_facts WHERE id=?", (fact_id,))
    conn.commit()


# ============================================================
# 标 / 撤销 character 知道
# ============================================================

def _check_fact_and_character_same_project(
    conn: sqlite3.Connection, fact_id: str, character_id: str, user_id: str,
) -> None:
    """事实 + 角色必须同属一个 user 拥有的项目."""
    row = fetch_one(
        conn,
        "SELECT f.project_id AS fp, c.project_id AS cp "
        "FROM story_facts f, characters c "
        "JOIN projects p ON p.id = f.project_id AND p.user_id=? "
        "WHERE f.id=? AND c.id=?",
        (user_id, fact_id, character_id),
    )
    if not row or row["fp"] != row["cp"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "FACT_OR_CHARACTER_NOT_FOUND",
                "message": "事实或角色不存在 / 不属同一项目 / 无权限",
            },
        )


@router.post("/story_facts/{fact_id}/known_by/{character_id}")
def api_mark_known(
    fact_id: str,
    character_id: str,
    req: MarkKnownRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """标 character 知道此事实(可重复调用 → UPDATE 现有行)."""
    _check_fact_and_character_same_project(conn, fact_id, character_id, user.id)
    kid = kb.mark_known(
        conn,
        character_id=character_id,
        fact_id=fact_id,
        known_since_scene=req.known_since_scene,
        confidence=req.confidence,
    )
    return {
        "id": kid,
        "fact_id": fact_id,
        "character_id": character_id,
        "known_since_scene": req.known_since_scene,
        "confidence": req.confidence,
    }


@router.delete(
    "/story_facts/{fact_id}/known_by/{character_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
)
def api_unmark_known(
    fact_id: str,
    character_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    """撤销 character 知道此事实."""
    _check_fact_and_character_same_project(conn, fact_id, character_id, user.id)
    kb.unmark_known(conn, character_id=character_id, fact_id=fact_id)


# ============================================================
# 某 character 已知事实清单
# ============================================================

@router.get("/characters/{character_id}/known_facts")
def api_list_known_facts(
    character_id: str,
    up_to_scene: Optional[int] = Query(None, ge=0, le=9999),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """该 character 已知事实清单(可 ?up_to_scene=N 截止某幕末)."""
    get_character_or_403(conn, character_id, user.id)
    known = kb.get_facts_known_by(conn, character_id, up_to_scene=up_to_scene)
    for k in known:
        k["is_sensitive"] = bool(k.get("is_sensitive", 0))
    return {"character_id": character_id, "facts": known}


# ============================================================
# SP-3.1(2026-05-29):AI 推断知识边界(事实清单 + 角色 known 矩阵)
# ============================================================

@router.post("/projects/{project_id}/infer/knowledge_boundaries")
def api_infer_knowledge_boundaries(
    project_id: str,
    overwrite: bool = False,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """LLM 推断知识边界:facts + character_knowledge matrix.

    overwrite=False(默认):项目已有任何 fact → 拒绝写,提示用户勾覆盖
    overwrite=true:先删项目所有 facts + knowledge,全替换为 LLM 推断结果

    失败任何阶段都内部兜底,绝不 5xx.
    """
    get_project_or_403(conn, project_id, user.id)
    from app.services.knowledge_boundaries_inferer import (
        infer_knowledge_boundaries,
        cache_knowledge_boundaries,
    )
    result = infer_knowledge_boundaries(conn, project_id)
    cache_report = cache_knowledge_boundaries(
        conn, project_id, result, overwrite=overwrite,
    )
    return {
        "applied": cache_report["applied"],
        "facts_created": len(cache_report["fact_ids_created"]),
        "knowledge_created": cache_report["knowledge_created"],
        "skipped_reason": cache_report.get("skipped_reason"),
        "reasoning": result.get("reasoning", ""),
        "raw_inferred_facts_count": len(result.get("facts") or []),
        "raw_inferred_knowledge_count": len(result.get("character_knowledge") or []),
    }
