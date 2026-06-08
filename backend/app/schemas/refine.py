"""Refine API schema — 3 个 endpoints。"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ========== POST /api/projects/{project_id}/refine ==========

class RefinementResponse(BaseModel):
    id: str
    character_id: str
    character_name: str
    suggestion_kind: str
    suggestion_text: str
    suggestion_payload: dict
    status: str  # pending / accepted / rejected / edited / skipped


class RefineSessionStats(BaseModel):
    characters_count: int
    refinements_count: int
    tokens: dict
    cost_yuan: float
    duration_ms: int
    filtered_out: int = Field(
        default=0,
        description="被 §16 三层兜底过滤掉的建议数(透明化给前端展示)",
    )


class RefineProjectResponse(BaseModel):
    session_id: str
    refinements: list[RefinementResponse]
    stats: RefineSessionStats


# ========== POST /api/refinements/{refinement_id}/action ==========

class ActionRefinementRequest(BaseModel):
    action: Literal["accept", "reject", "edit"]
    user_edit: Optional[dict] = Field(
        default=None,
        description="action=edit 时必填,与 suggestion_payload 同 schema",
    )


class ActionRefinementResponse(BaseModel):
    id: str
    status: str
    applied_to_character: bool


# ========== POST /api/refine_sessions/{session_id}/skip ==========

class SkipSessionRequest(BaseModel):
    reason: Literal["user_skipped", "llm_failed"] = "user_skipped"


class SkipSessionResponse(BaseModel):
    skipped: bool
    remaining_refinements: int
