"""ProjectScene API schemas — Sprint 6.A2 M2(2026-05-18)。

Sprint 6.A2 M8.C(2026-05-21):加 origin_simulation_id 字段透出 + 新增编辑 schemas。
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class ProjectSceneResponse(BaseModel):
    id: str
    project_id: str
    name: str
    aliases: list[str]
    description: str
    appearance_chunk_count: int
    created_at: str
    updated_at: str
    # M8.C(2026-05-21):None = 原作图谱抽出;非 None = 续作生成入库(前端 badge 区分)
    origin_simulation_id: Optional[str] = None


# M8.C(2026-05-21)编辑请求
class UpdateProjectSceneRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=30)
    description: Optional[str] = Field(default=None, max_length=500)
    aliases: Optional[list[str]] = Field(default=None, max_length=10)


# INIT.6(2026-05-21)创建请求 — 初始态用户从零定义场景
class CreateProjectSceneRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=30)
    description: str = Field(default="", max_length=500)
    aliases: list[str] = Field(default_factory=list, max_length=10)


# M8.C(2026-05-21)合并请求
class MergeProjectScenesRequest(BaseModel):
    """把 source_scene_id 合并到 target_scene_id。

    合并后:source 的 aliases 被并到 target;source 的 appearance_chunk_count 加到 target;
    source 被删除。outline_scenes / simulation_scenes 中引用 source.name 的 location 字段
    会被批量改为 target.name(因为 location 是文本不是 FK,只能 string replace)。
    """
    source_scene_id: str = Field(..., description="被合并(删除)的场景 id")
    target_scene_id: str = Field(..., description="合并到的主条目场景 id")


class SceneRegularResponse(BaseModel):
    """场景"常客"角色 — character_affinity.compute_scene_regulars 输出。

    在该 scene 出现共场次数排序后的 top-N 角色,M3 续写时 scene_picker 用此
    决定"在场所 X 召唤哪些 agent 上场对话"。
    """
    character_id: str
    character_name: str
    co_occurrence_count: int      # 该角色在该 scene 共出现的 chunk 数
    is_protagonist: bool          # 透传主角标记便于前端展示


class SceneRegularsResponse(BaseModel):
    scene_name: str
    regulars: list[SceneRegularResponse] = Field(default_factory=list)
