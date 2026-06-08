"""CanonicalAudit API schema — Sprint 2.D 正典守护者。

POST /api/simulations/{sim_id}/canonical_audit         触发(创建 + kick_off)
GET  /api/simulations/{sim_id}/canonical_audit/latest  最新一次审计
GET  /api/simulations/{sim_id}/canonical_audits        历史列表
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


# 维度 / 严重度 字面量,前端 type narrowing 用
CanonicalDimension = Literal[
    "character_consistency",
    "relationship_network",
    "worldview",
    "era_physics",
    "tone",
    "event_causality",
    "value_orientation",
    "detail_authenticity",
    # B5.3(2026-05-27):身体描写尺度对齐 — 灵魂续写关键(P4)
    "body_register_alignment",
    # P5.2(2026-05-27):outline 执行率 — 治"剧情空心化"
    "outline_execution",
    # SP-3.1(2026-06-02):信息边界审计 — 角色用了不该知道的信息
    "information_boundary",
    # SP-1 终审(2026-06-02):故事内核坚守度
    "story_core_adherence",
]

CanonicalSeverity = Literal[
    "strict_canonical", "minor_drift", "obvious_drift", "severe_breach",
]


class CanonicalIssue(BaseModel):
    dimension: CanonicalDimension
    severity: CanonicalSeverity
    finding: str
    evidence_excerpt: str
    canon_reference: str
    counterfactual_exempt: bool = False
    exempt_reason: Optional[str] = None


class CanonicalAuditResponse(BaseModel):
    id: str
    simulation_id: str
    project_id: str
    state: str
    issues: list[CanonicalIssue] = []
    tokens_input: int
    tokens_output: int
    cost_yuan: float
    error_message: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    # Sprint D.7:zombie 检测派生字段
    # state='running' + is_alive=False → 后端重启后的僵尸 audit,前端可显"重新触发"按钮
    is_alive: bool = False
    # Sprint 6.A2 路线图 #7(2026-05-23)— 0-100 总分(state='done' 才有值)+ 非反事实豁免的问题数
    overall_score: Optional[int] = None
    effective_issues_count: int = 0
