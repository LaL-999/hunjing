"""反事实组合树 Pydantic schemas — Sprint 6.A2 CT(2026-05-21)。

API:
  GET  /api/projects/{pid}/counterfactual-combinations/preview  预估成本
  POST /api/projects/{pid}/counterfactual-combinations          创建批次启动 fanout
  GET  /api/counterfactual-combinations/{id}/tree               拉决策树

设计:
  - 用户最多勾选 3 个反事实变量(每个变量 2 个值 → 2^3=8 个组合 sim)
  - 树根 = 原作,枝 = 变量分叉(a/b 二态),叶 = 单个 sim
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


# ============================================================
# 请求体
# ============================================================

class SelectedVariableRequest(BaseModel):
    """用户勾选的一条反事实变量(2 态)。"""
    counterfactual_id: str = Field(..., min_length=1, max_length=64)
    label_a: str = Field(..., min_length=1, max_length=80, description="原值的可读简短描述")
    label_b: str = Field(..., min_length=1, max_length=80, description="改值的可读简短描述")


class CreateCombinationRunRequest(BaseModel):
    """创建组合批次。

    selected_variables 长度 1-3,后端验证。total = 2^len。
    target_chars / reshape_percent / style / use_outline_first 等创建 sim 时复用。
    """
    selected_variables: list[SelectedVariableRequest] = Field(
        ..., min_length=1, max_length=3,
        description="勾选的反事实变量列表,1-3 个(对应 2/4/8 组合)",
    )
    # 复用 simulation 创建的通用配置
    reshape_percent: int = Field(50, ge=10, le=90)
    target_chars: int = Field(4000, ge=500, le=20000)
    style: str = Field("A", max_length=10)
    custom_style_hint: Optional[str] = Field(default=None, max_length=500)
    use_outline_first: bool = Field(default=True)
    divergence: str = Field(
        ..., min_length=1, max_length=500,
        description="分歧点描述,与单 sim 创建一致",
    )


class PreviewRequest(BaseModel):
    """预估成本(给前端展示用户确认前)。"""
    selected_variable_count: int = Field(..., ge=1, le=3)
    reshape_percent: int = Field(50, ge=10, le=90)
    target_chars: int = Field(4000, ge=500, le=20000)


# ============================================================
# 响应体
# ============================================================

class SelectedVariableResponse(BaseModel):
    counterfactual_id: str
    label_a: str
    label_b: str


class CombinationRunResponse(BaseModel):
    """与 CounterfactualCombinationRun.to_response() 对齐。"""
    id: str
    project_id: str
    user_id: str
    selected_variables: list[SelectedVariableResponse]
    total_combinations: int  # 2 / 4 / 8
    state: str  # pending / generating / partial / done / failed
    error_message: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None


class CombinationLeafSim(BaseModel):
    """决策树叶节点 — 一个 sim 在树中的位置 + 状态。"""
    simulation_id: str
    tree_path: list[Literal["a", "b"]]   # 长度 = 变量数
    sim_state: str  # queued / generating / completed / failed
    sim_narrative_chars: int = 0
    sim_created_at: str
    sim_completed_at: Optional[str] = None
    # 2026-06-05:outline-first 模式 sim 创建后 outline 状态
    #   outline_state='awaiting_user' 时 sim 卡在 queued 等用户审核 outline
    #   前端必须能拿到此字段才能正确显示"等审核"分组 + "前往审核"按钮
    outline_id: Optional[str] = None
    outline_state: Optional[str] = None  # drafting / awaiting_user / done / failed


class CombinationTreeResponse(BaseModel):
    """决策树视图响应。

    前端按 tree_path 自行渲染分叉:
      根 → 变量1.a / 变量1.b → 变量2.a / 变量2.b → ...叶 sim

    selected_variables 给前端显示每层的"原/改"标签。
    """
    combination_run: CombinationRunResponse
    leaves: list[CombinationLeafSim]


class PreviewResponse(BaseModel):
    """成本预估响应(给用户确认前看)。"""
    total_combinations: int
    estimated_token_per_sim: int
    estimated_total_tokens: int
    estimated_minutes_per_sim: int
    estimated_total_minutes: int  # 串行总时长
    estimated_credits: int  # 预估积分消耗
