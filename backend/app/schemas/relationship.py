"""Relationship API schema。

Sprint 6.A2 M1(2026-05-18):加 RelationshipPhase schemas + RelationshipResponse
加 current_phase_id 字段。

Sprint 6.A2 M7.G(2026-05-20)关系类型自由化:
  - RelationshipType 由 Literal[7 种] → Annotated[str](trim 后 1-20 字)
  - 加 PRESET_RELATIONSHIP_TYPES 常量(给前端 dropdown 用,约 25 种常用预设)
  - 用户可输入自定义,LLM 抽取也可自创关系名
  - DB CHECK 同步改为长度上限(migration 046)
"""
from __future__ import annotations

from typing import Annotated, Optional

from pydantic import BaseModel, Field, StringConstraints


# ============================================================
# 关系类型:自由字符串(2-20 字,trim 后非空)
# ============================================================

RelationshipType = Annotated[
    str,
    StringConstraints(min_length=1, max_length=20, strip_whitespace=True),
]

# ⭐ 前端 dropdown 预设清单(M7.G 2026-05-20)
# 设计原则:覆盖中文创作里最常用的 PERSON-PERSON 关系 + 4 种结构性关系
# (LOCATION / EVENT 节点连人物用)。用户可绕过此清单输入任意 1-20 字。
# 分类排序:亲属 → 友情 → 爱情 → 师生职场 → 对立 → 结构 → 兜底
PRESET_RELATIONSHIP_TYPES: list[str] = [
    # 亲属类
    "亲属", "兄弟", "兄妹", "姐妹", "姐弟", "父子", "父女", "母子", "母女", "夫妻",
    # 友情类
    "朋友", "挚友", "青梅竹马",
    # 爱情类
    "情侣", "暗恋", "暧昧", "前任",
    # 学习 / 职场
    "师徒", "师生", "同学", "同事", "上下级", "主仆",
    # 对立
    "敌对", "宿敌",
    # 结构性(LLM 抽取 LOCATION / EVENT 节点连人物用)
    "位于", "参与", "提及",
    # 兜底
    "其他",
]


# Sprint 3.A polish — 与 backend/app/models/relationship.py VALID_RELATIONSHIP_STRENGTHS
# 和 build_graph.md prompt v2 输出 5 级 enum 对齐
from typing import Literal

RelationshipStrength = Literal[
    "strong",
    "moderately_strong",
    "moderate",
    "moderately_weak",
    "weak",
]

# SP-7(2026-05-28,migration 072)— 关系正负极性
# positive 喜爱/亲近 / negative 仇恨/对立 / neutral 中性
# 与 strength(紧密度)正交:strong+negative = 强烈仇恨
RelationshipPolarity = Literal["positive", "negative", "neutral"]


class CreateRelationshipRequest(BaseModel):
    source_id: str
    target_id: str
    type: RelationshipType
    description: str = Field(default="", max_length=200)
    color: Optional[str] = Field(None, max_length=20)
    # Sprint 3.A polish:手动创建关系默认 'moderate'(中性,不会被默认阈值过滤)
    strength: RelationshipStrength = "moderate"
    # SP-7(2026-05-28):极性,None = 未标(老数据兼容)
    polarity: Optional[RelationshipPolarity] = None


class UpdateRelationshipRequest(BaseModel):
    type: Optional[RelationshipType] = None
    description: Optional[str] = Field(None, max_length=200)
    color: Optional[str] = Field(None, max_length=20)
    strength: Optional[RelationshipStrength] = None
    # Sprint 6.A2 M1:用户可手动切换 current_phase_id(传 phase_id 字符串或 null=回退 type)
    current_phase_id: Optional[str] = Field(None, max_length=64)
    # SP-7(2026-05-28):用户可手动改极性(None 清空,3 选 1)
    polarity: Optional[RelationshipPolarity] = None


class RelationshipResponse(BaseModel):
    id: str
    project_id: str
    source_id: str
    target_id: str
    type: RelationshipType
    description: str
    color: Optional[str]
    strength: RelationshipStrength
    created_at: str
    # Sprint 6.A2 M1:指向当前生效 phase(NULL = 无 phases / 走 type fallback)
    current_phase_id: Optional[str] = None
    # SP-7(2026-05-28):正负极性(None = 未标,前端显灰 chip)
    polarity: Optional[RelationshipPolarity] = None


# ============================================================
# Sprint 6.A2 M1:RelationshipPhase schemas
# ============================================================

class CreateRelationshipPhaseRequest(BaseModel):
    """加新 phase 到 relationship 末尾(自动 phase_index = max+1)。"""
    type: RelationshipType
    strength: RelationshipStrength = "moderate"
    start_anchor: Optional[str] = Field(None, max_length=80)
    end_anchor: Optional[str] = Field(None, max_length=80)
    trigger_event_id: Optional[str] = Field(None, max_length=64)
    notes: str = Field(default="", max_length=500)
    auto_set_current: bool = True


class UpdateRelationshipPhaseRequest(BaseModel):
    """部分更新 phase 字段(所有可选)。"""
    type: Optional[RelationshipType] = None
    strength: Optional[RelationshipStrength] = None
    start_anchor: Optional[str] = Field(None, max_length=80)
    end_anchor: Optional[str] = Field(None, max_length=80)
    trigger_event_id: Optional[str] = Field(None, max_length=64)
    notes: Optional[str] = Field(None, max_length=500)


class RelationshipPhaseResponse(BaseModel):
    id: str
    relationship_id: str
    phase_index: int
    type: RelationshipType
    strength: RelationshipStrength
    start_anchor: Optional[str]
    end_anchor: Optional[str]
    trigger_event_id: Optional[str]
    notes: str
    created_at: str
    updated_at: str
