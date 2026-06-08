"""Audit API schema — Sprint 1.R 自洽守护者。

POST /api/simulations/{sim_id}/audit                            — 触发新诊断
GET  /api/simulations/{sim_id}/audit/latest                     — 取最新一条(回到 detail 页恢复结果)
POST /api/audits/{audit_id}/issues/{issue_idx}/accept           — M7.C(2026-05-20)采纳建议自动应用

issue.kind 限定 8 个值;subject_id 仅前 3 类(用户可修)有,其余 LLM-only 类留 null。

Sprint 6.A2 M7.C(2026-05-20):AuditIssue 加 2 个新字段:
  - actionable_fix_payload:LLM 输出的结构化建议(可选),供"采纳"按钮一键应用
  - accepted_at:该 issue 被采纳的时间戳(None=未采纳)
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# ============================================================
# 8 维度 kind(对齐 prompts/self_consistency_guardian.md)
# 用户可修类 — UI 显「去修」按钮 + 「采纳」按钮(M7.C 新增)
# LLM-only 类 — UI 仅文本,留给重生成时调
# ============================================================

USER_FIXABLE_KINDS = ("character_thin", "event_inconsistent", "relationship_off")
LLM_ONLY_KINDS = (
    "dialogue_flat",
    "turn_jarring",
    "pacing_off",
    "opening_weak",
    "whitespace_imbalance",
)
ALL_KINDS = USER_FIXABLE_KINDS + LLM_ONLY_KINDS

IssueKind = Literal[
    "character_thin",
    "event_inconsistent",
    "relationship_off",
    "dialogue_flat",
    "turn_jarring",
    "pacing_off",
    "opening_weak",
    "whitespace_imbalance",
]


# ============================================================
# M7.C(2026-05-20)结构化修复 payload 白名单
# audit_acceptor_service 应用建议时严格按此白名单过滤,
# 防 LLM 出格输出非法 field 把 DB 改坏
#
# M7.K(2026-05-20)扩展:加 sim_config target,给 LLM-only 类 issue 用
# (dialogue_flat / turn_jarring / pacing_off / opening_weak / whitespace_imbalance)
# 不改 DB,只把推荐配置返给前端,跳回项目页 dock 预填
# ============================================================

ACCEPT_PAYLOAD_TARGETS = ("character", "event", "relationship", "sim_config")

ACCEPT_PAYLOAD_FIELDS_BY_TARGET: dict[str, tuple[str, ...]] = {
    "character":    ("personality", "quotes", "no_go_list", "identity"),
    "event":        ("description",),
    "relationship": ("description", "type"),
    # M7.K(2026-05-20)sim_config target — 不改 DB,前端跳回项目 dock 预填
    "sim_config":   (
        "reshape_percent_delta",
        "target_chars_delta",
        "custom_style_hint",
        "divergence_prefix",
    ),
}

ACCEPT_PAYLOAD_OPS = ("append", "replace")

# relationship.type 的合法枚举(M7.G 后已自由化,这里保留预设白名单作"合法替换枚举")
ACCEPT_RELATIONSHIP_TYPE_ENUM = (
    "亲属", "敌对", "朋友", "情侣", "师徒", "同事", "其他",
)

# M7.K(2026-05-20)sim_config patch 数值字段范围 — 防 LLM 给出格值
SIM_CONFIG_RESHAPE_DELTA_RANGE = (-30, 30)
SIM_CONFIG_TARGET_CHARS_DELTA_RANGE = (-5000, 10000)
SIM_CONFIG_CUSTOM_STYLE_HINT_MAX_LEN = 100
SIM_CONFIG_DIVERGENCE_PREFIX_MAX_LEN = 30


# M7.K(2026-05-20)kind → 推荐 target 映射(给 audit_service 清洗校验用)
KIND_TO_PAYLOAD_TARGET: dict[str, str] = {
    "character_thin":         "character",
    "event_inconsistent":     "event",
    "relationship_off":       "relationship",
    # LLM-only 5 类统一走 sim_config
    "dialogue_flat":          "sim_config",
    "turn_jarring":           "sim_config",
    "pacing_off":             "sim_config",
    "opening_weak":           "sim_config",
    "whitespace_imbalance":   "sim_config",
}


class AuditIssue(BaseModel):
    kind: IssueKind
    subject_id: Optional[str] = Field(
        default=None,
        description="character_thin/event_inconsistent/relationship_off 时填,其余 LLM-only 类留 null",
    )
    subject_name: str = Field(description="角色名 / 事件描述短句 / '林晚 & 苏宁' 关系对")
    evidence_in_narrative: str = Field(
        description="narrative 中实际短引用(< 80 字),不能编造",
    )
    root_cause_in_setup: str
    actionable_fix: str
    # M7.C(2026-05-20):结构化修复 payload(给"采纳"按钮一键应用)
    # LLM 给出明确建议时含此字段;无法自动应用 / LLM-only 类 → None
    actionable_fix_payload: Optional[dict[str, Any]] = Field(
        default=None,
        description=(
            "结构化修复 payload:{target, operations:[{field, op, value}]};"
            "None 表示无法自动采纳(用户走「去修」手动改)"
        ),
    )
    # M7.C(2026-05-20):该 issue 被采纳的时间戳(ISO 8601),None=未采纳
    accepted_at: Optional[str] = Field(
        default=None,
        description="该建议被「采纳」按钮自动应用的时间;None=尚未采纳",
    )


class AuditResponse(BaseModel):
    id: str
    simulation_id: str
    overall_score: int = Field(ge=0, le=100)
    issues: list[AuditIssue]
    regenerate_recommendation: str
    cost_yuan: float
    duration_ms: int
    triggered_at: str


# ============================================================
# M7.C(2026-05-20)"采纳"端点响应
# ============================================================

class AcceptedOperation(BaseModel):
    """单次应用记录 — 给前端 toast 反馈用。"""
    field: str
    op: Literal["append", "replace"]
    # value 可能是 string 或 list[str](append quotes / no_go_list 等);
    # API 透出原值,前端展示用
    value: Any
    new_field_value: Any = Field(
        description="该字段应用后的最终值(可读形式;quotes / no_go_list 是 list)",
    )


class SimConfigPatch(BaseModel):
    """M7.K(2026-05-20)— LLM-only 类 issue 的 sim_config 推荐配置。

    前端跳回项目 dock 时按此 patch 预填字段:
      - reshape_percent_delta: 调整 reshape 滑块(±范围)
      - target_chars_delta: 字数增量(可正可负)
      - custom_style_hint: 笔法描述(覆盖原 hint;空字符串=不动)
      - divergence_prefix: 续写锚点的前缀引导(prepend 到 divergence)
    所有字段都可选,LLM 给最相关的 1-3 个。
    """
    reshape_percent_delta: Optional[int] = Field(default=None, ge=-30, le=30)
    target_chars_delta: Optional[int] = Field(default=None, ge=-5000, le=10000)
    custom_style_hint: Optional[str] = Field(default=None, max_length=100)
    divergence_prefix: Optional[str] = Field(default=None, max_length=30)


class AcceptAuditIssueResponse(BaseModel):
    audit_id: str
    issue_idx: int
    target: Literal["character", "event", "relationship", "sim_config"]
    # M7.K(2026-05-20)sim_config target 的 subject_id / subject_name 无意义,统一用 sim_id / issue label
    subject_id: str
    subject_name: str
    operations_applied: list[AcceptedOperation] = Field(default_factory=list)
    operations_skipped: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "被白名单 / 兜底过滤掉的 operation(如 identity 已有值时 replace 被跳过)— "
            "前端可显示提示"
        ),
    )
    # M7.K(2026-05-20)— target='sim_config' 时返回该字段;前端跳回项目 dock 预填
    sim_config_patch: Optional[SimConfigPatch] = None
    accepted_at: str


# ============================================================
# Sprint 6.A2 路线图 #2.5(2026-05-22)— audit patch 跨会话持久化
#   GET  /api/projects/{id}/pending_audit_patches
#   POST /api/projects/{id}/audit_patches/mark_applied
# 复用 audit.issues_json 加 applied_at 字段,无 migration
# ============================================================

class PendingAuditPatch(BaseModel):
    """已采纳待应用的 sim_config patch — 给前端 dock 预填用。"""
    audit_id: str
    issue_idx: int
    source_sim_id: str = Field(description="audit 关联的 sim id(用于 dock 默认勾选本篇为前文)")
    source_label: str = Field(description="给前端 banner 显示的人话标签,如 '对白扁平' / '节奏失衡·渡边'")
    patches: dict[str, Any] = Field(description="sim_config patches 字典")
    accepted_at: str


class AuditPatchRef(BaseModel):
    """mark_applied 请求项 — (audit_id, issue_idx) 二元组定位。"""
    audit_id: str
    issue_idx: int = Field(ge=0)


class MarkAuditPatchesAppliedRequest(BaseModel):
    items: list[AuditPatchRef] = Field(
        default_factory=list,
        description="批量待标记的 (audit_id, issue_idx);空数组允许(no-op)",
    )


class MarkAuditPatchesAppliedResponse(BaseModel):
    marked: int = Field(description="本次实际标记 applied_at 的条数(幂等:已 applied 的跳过不算)")
