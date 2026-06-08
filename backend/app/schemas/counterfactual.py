"""CounterfactualChange API schema — Sprint 2.C + 2.C+。

POST /api/projects/{id}/counterfactuals             显式创建(2.C+ 工作台用)— character/event/relationship
POST /api/projects/{id}/counterfactuals/world       显式创建 — world 类型(2.C+ 世界观维度)
GET  /api/projects/{id}/counterfactuals             列出 active
POST /api/counterfactuals/{id}/revert               撤销 → 推演忽略 + 视觉撤标签
GET  /api/projects/{id}/reshape_preview             重塑度三维度预览(给 ReshapeSlider)

注:character/event/relationship 的 PATCH hook 仍保留(隐式 record),与显式创建并存:
  - PATCH 路径:用户在 NodeEditDrawer 改字段保存自动落反事实(无 user_intent)
  - 显式 POST 路径:CounterfactualWorkbench 主动创建,带 user_intent
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class CounterfactualChangeResponse(BaseModel):
    """与 CounterfactualChange.to_response() 对齐。"""

    id: str
    project_id: str
    target_type: str          # 'character' / 'event' / 'relationship' / 'world'(2.C+)
    target_id: str
    field: str
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    user_intent: Optional[str] = None    # 2.C+ 用户自然语言意图
    created_at: str
    reverted_at: Optional[str] = None
    is_active: bool
    applied_in_simulations: list[str] = []


# ============================================================
# 显式创建端点 — Sprint 2.C+ CounterfactualWorkbench 用
# ============================================================

class CreateCounterfactualRequest(BaseModel):
    """POST /api/projects/{id}/counterfactuals — 显式创建 character/event/relationship 反事实。"""

    target_type: Literal["character", "event", "relationship"]
    target_id: str = Field(..., min_length=1)
    field: str = Field(..., min_length=1, max_length=40)
    old_value: Optional[str] = None
    new_value: Optional[str] = None
    user_intent: Optional[str] = Field(
        None, max_length=500,
        description="用户自然语言描述的'想要的效果' — LLM 编排时优先级最高",
    )


class CreateWorldCounterfactualRequest(BaseModel):
    """POST /api/projects/{id}/counterfactuals/world — 显式创建世界观反事实。

    field 必须是 6 个世界观维度之一。
    """

    field: Literal["genre", "setting", "magic_system", "time_axis", "tone", "free_form"]
    old_value: Optional[str] = Field(
        None, max_length=500,
        description="原作的该维度语义(用户填,可空)",
    )
    new_value: str = Field(..., min_length=1, max_length=500)
    user_intent: Optional[str] = Field(
        None, max_length=500,
        description="为什么这么改 — 强烈推荐填",
    )


class ReshapePreviewResponse(BaseModel):
    """重塑度三维度实时预览(给前端 ReshapeSlider 显当前 reshape 值的物理影响)。

    GET /api/projects/{id}/reshape_preview?reshape_percent=50

    结构镜像 simulation_service.derive_reshape_dimensions:
      - max_touched_characters:第 1 维 — 该 reshape % 下允许改的最大角色数
      - rounds_planned:第 2 维 — 派生的 agent 互动轮次(已有,reshape_to_rounds)
      - graph_distance_hops:第 3 维 — BFS 影响半径
      - current_touched_count:当前 active 反事实涉及的角色数(用于"已用 X / 上限 Y")
      - current_affected_node_ids:当前 BFS 触达的节点 id 集合(给 3D 图谱 highlight)
    """

    reshape_percent: int
    max_touched_characters: int
    rounds_planned: int
    graph_distance_hops: int
    current_touched_count: int
    current_affected_node_ids: list[str]
    plan_max_percent: int     # 当前用户 plan 的 reshape 上限(给滑块禁用区显)
    # Sprint 6.A2 M3.D-fix2 v2(2026-05-18):重塑度推断的合理字数区间
    # 用户在 [chars_low, chars_high] 内自由微调 target_chars
    # 前端 SimulationDock 叙事长度滑块应 clamp 到这个区间
    chars_center: int     # 推荐中心字数
    chars_low: int        # 推荐区间下界
    chars_high: int       # 推荐区间上界
    chars_label: str      # 标签:微改 / 短篇 / 中短篇 / 中篇 / 中长篇起点


class CounterfactualOverviewResponse(BaseModel):
    """GET /api/projects/{id}/counterfactuals 的返回 — active 列表 + 总数 + 按 type 分桶。

    分桶让前端反事实面板能 group display(角色 / 事件 / 关系 三栏)。
    """

    total_active: int
    by_type: dict[str, int]   # {'character': 3, 'event': 1, 'relationship': 0}
    items: list[CounterfactualChangeResponse]


class CharacterPatchRequest(BaseModel):
    """character 字段 PATCH 接受全字段可选 — 客户端只传想改的。

    与 CounterfactualChange 配合:service 层比对每个字段 old vs new,差异落 counterfactual。
    复用现有 CharacterUpdate schema 即可,这里只是占位说明 — 实际不新建 schema。
    """
    pass


class RevertCounterfactualResponse(BaseModel):
    """POST /api/counterfactuals/{id}/revert — 返回撤销后的 counterfactual + db 状态变更摘要。"""

    counterfactual: CounterfactualChangeResponse
    target_field_restored: bool   # 是否成功把 db 字段还原到 old_value(失败也 200,前端给提示)
    restore_error: Optional[str] = None
