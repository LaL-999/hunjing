"""Outline 相关 Pydantic schemas — Sprint 6.A2 M6(2026-05-20)。"""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ========== Responses ==========

class OutlineSceneResponse(BaseModel):
    id: str
    outline_id: str
    scene_index: int
    scene_summary: str
    scene_purpose: str
    location: str
    time_anchor: str
    characters_present: list[str]
    key_events: list[str]
    key_props: list[dict[str, Any]]
    transition_from_last: str
    user_edited: bool
    state: str  # pending / running / done / failed
    generated_simulation_scene_id: Optional[str]
    error_message: Optional[str]
    created_at: str
    updated_at: str
    # Sprint 6.A2 MP(2026-05-21)— planner 张力 / 节奏(可空)
    tension_percent: Optional[int] = None
    pacing_tempo: Optional[str] = None  # fast / normal / slow


class SimulationOutlineResponse(BaseModel):
    id: str
    simulation_id: str
    state: str  # drafting / awaiting_user / approved / generating / done / failed
    total_scenes_planned: int
    global_theme: str
    global_arc: str
    user_approved_at: Optional[str]
    error_message: Optional[str]
    created_at: str
    updated_at: str
    scenes: list[OutlineSceneResponse]


# ========== Requests ==========

class UpdateOutlineSceneRequest(BaseModel):
    """编辑某 outline_scene 的字段(所有字段可选,只更新非 None 的)。"""
    scene_summary: Optional[str] = Field(default=None, max_length=500)
    scene_purpose: Optional[str] = Field(default=None, max_length=50)
    location: Optional[str] = Field(default=None, max_length=50)
    time_anchor: Optional[str] = Field(default=None, max_length=50)
    characters_present: Optional[list[str]] = Field(default=None)
    key_events: Optional[list[str]] = Field(default=None)
    key_props: Optional[list[dict[str, Any]]] = Field(default=None)
    transition_from_last: Optional[str] = Field(default=None, max_length=300)


class UpdateOutlineGlobalRequest(BaseModel):
    """编辑 outline 主表的全局字段(global_theme / global_arc)。"""
    global_theme: Optional[str] = Field(default=None, max_length=200)
    global_arc: Optional[str] = Field(default=None, max_length=1000)


class ApproveOutlineRequest(BaseModel):
    """批准 outline 后即启动逐幕生成。"""
    confirm: bool = Field(..., description="必须显式 true 才执行")


# ========== Sprint 6.A2 M8.B(2026-05-21)加幕 / 删幕 / 重排 ==========

class InsertOutlineSceneRequest(BaseModel):
    """在 outline 的指定 position 插入新幕,后续幕 scene_index 整体 +1。"""
    position: int = Field(
        ..., ge=0,
        description="插入位置 0-based;N 表示尾部追加(N=当前幕数)",
    )
    # 可选 scene 字段(全可空 → 创建空白幕由用户后续编辑)
    scene_summary: Optional[str] = Field(default=None, max_length=500)
    scene_purpose: Optional[str] = Field(default=None, max_length=50)
    location: Optional[str] = Field(default=None, max_length=50)
    time_anchor: Optional[str] = Field(default=None, max_length=50)
    characters_present: Optional[list[str]] = Field(default=None)
    key_events: Optional[list[str]] = Field(default=None)
    transition_from_last: Optional[str] = Field(default=None, max_length=300)


class ReorderOutlineScenesRequest(BaseModel):
    """重排 outline 内所有幕,按 ordered_scene_ids 顺序。

    必须**完备 + 不重复**覆盖所有现有 outline_scenes。
    """
    ordered_scene_ids: list[str] = Field(
        ..., min_length=1,
        description="新顺序的 outline_scene_id 列表,完备覆盖所有现有幕,无重复",
    )
