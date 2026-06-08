"""CanonicalAudit 表的 Python 表示 — Sprint 2.D 正典守护者。

镜像 Audit(1.R 自洽守护者)结构:
  - state 机:running → done / failed
  - issues_json:list of {dimension, severity, finding, evidence_excerpt,
                          canon_reference, counterfactual_exempt, exempt_reason}
  - 8 维度审计输出由 prompts/canonical_guardian.md 定义
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional


VALID_STATES = ("running", "done", "failed")

VALID_DIMENSIONS = (
    "character_consistency",
    "relationship_network",
    "worldview",
    "era_physics",
    "tone",
    "event_causality",
    "value_orientation",
    "detail_authenticity",
    # B5.3(2026-05-27):身体描写尺度对齐 — 灵魂续写关键(P4 维度,源 author_compass.内部反推.身体描写尺度)
    # 治含蓄原作搞出露骨续作 / 直白原作回避不写 — 两种"灵魂错位"
    "body_register_alignment",
    # P5.2(2026-05-27):outline 执行率 — 治"剧情空心化"
    # 实测挪威森林 28 幕仅覆盖 57%,LLM 用日常琐事替换核心剧情(打电话/问直子/崩溃大哭/做爱)
    # 与 P5.1 程序级关键词检测互补:P5.2 是审计层(LLM 语义判定),P5.1 是 retry 闸门
    "outline_execution",
    # 2026-06-02 SP-3.1 终审:信息边界审计 — LLM-as-judge 判"角色用了不该知道的信息"
    # 数据源:character_knowledge 表(SP-3 落地);LLM 拿到"该角色截止本幕已知 fact ids"
    # 治"AI 写作最大连贯 bug" — 角色用了 narrator 上帝视角的信息(没在场/未告知)
    "information_boundary",
    # 2026-06-02 SP-1 终审:故事内核坚守度 — 是否偏离 core_dramatic_question / theme / ending_direction
    # 治"LLM 提前泄气"/ 主题漂移 / 终点情绪偏离
    "story_core_adherence",
)

# B5.4(2026-05-27)— 9 维度权重表(用户拍板默认值):
# 用于 compute_overall_score 加权扣分,区分"灵魂错位"vs"细节小瑕"。
#
# 设计理由:
#   - 角色崩 / 世界观失守 = 灵魂没了(1.3×)
#   - tone + 身体尺度 = 灵魂续写关键维度(1.2×)
#   - 价值 / 事件因果 / 关系 = 中性(1.0×)
#   - 时代物理(改道具就能修)= 0.9×
#   - 细节真实(用语 / 称谓小瑕)可容忍 = 0.7×
DIMENSION_WEIGHTS: dict[str, float] = {
    "character_consistency":    1.3,
    "worldview":                1.3,
    "outline_execution":        1.3,   # P5.2(2026-05-27):剧情走偏 = 灵魂崩,同级
    "information_boundary":     1.3,   # SP-3.1(2026-06-02):用了不该知道的信息 = 灵魂崩,同级
    "tone":                     1.2,
    "body_register_alignment":  1.2,
    "story_core_adherence":     1.2,   # SP-1 终审(2026-06-02):偏离故事内核 = 灵魂续写关键
    "value_orientation":        1.0,
    "event_causality":          1.0,
    "relationship_network":     1.0,
    "era_physics":              0.9,
    "detail_authenticity":      0.7,
}


VALID_SEVERITIES = (
    "strict_canonical",
    "minor_drift",
    "obvious_drift",
    "severe_breach",
)


@dataclass
class CanonicalAudit:
    id: str
    simulation_id: str
    project_id: str
    user_id: str
    state: str
    issues_json: str
    tokens_input: int
    tokens_output: int
    cost_yuan: float
    error_message: Optional[str]
    created_at: str
    completed_at: Optional[str]

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "CanonicalAudit":
        return cls(
            id=row["id"],
            simulation_id=row["simulation_id"],
            project_id=row["project_id"],
            user_id=row["user_id"],
            state=row["state"],
            issues_json=row["issues_json"] or "[]",
            tokens_input=row["tokens_input"],
            tokens_output=row["tokens_output"],
            cost_yuan=row["cost_yuan"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    @property
    def issues(self) -> list[dict]:
        """parse issues_json → list of dict;损坏返空 list。"""
        try:
            parsed = json.loads(self.issues_json)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    def compute_overall_score(self) -> Optional[int]:
        """Sprint 6.A2 路线图 #7(2026-05-23)— 计算正典审计 0-100 总分。
        B5.4(2026-05-27)— 加维度权重,区分"灵魂错位"vs"细节小瑕"。

        算法:
          - 仅在 state='done' 时返分数,其他状态返 None(前端展示等待)
          - 反事实豁免的 issue 不计入(用户主动重塑的不算违规)
          - 每条 effective issue 按 severity × dimension 加权扣分:
              基础扣分(severity):
                strict_canonical → 0(认证符合,不应出现在 issues 里,但兜底 0 分)
                minor_drift      → 5
                obvious_drift    → 12
                severe_breach    → 25
              维度权重(DIMENSION_WEIGHTS):
                character_consistency / worldview     → 1.3(灵魂崩)
                tone / body_register_alignment        → 1.2(灵魂续写关键)
                value_orientation / event_causality
                  / relationship_network              → 1.0(中性)
                era_physics                           → 0.9(可修)
                detail_authenticity                   → 0.7(可容忍)
          - 最终扣分 = round(severity_deduct × dimension_weight)
          - 起始 100 分,逐条扣;扣到 0 即封底
          - 加权后区分案例:
              · 角色 severe_breach = 25 × 1.3 = 33 分扣(主角崩,严重)
              · 细节 severe_breach = 25 × 0.7 = 18 分扣(用语小瑕,可容忍)
              · 1 个角色 severe + 3 个细节 obvious = 33 + 3×(12×0.7)=33+25=58 分扣 → 42 分
        """
        if self.state != "done":
            return None
        deduct_map = {
            "strict_canonical": 0,
            "minor_drift": 5,
            "obvious_drift": 12,
            "severe_breach": 25,
        }
        score: float = 100.0
        for issue in self.issues:
            if not isinstance(issue, dict):
                continue
            # 反事实豁免不计入
            if issue.get("counterfactual_exempt") is True:
                continue
            severity = issue.get("severity")
            severity_deduct = deduct_map.get(severity, 0)
            if severity_deduct == 0:
                continue
            # B5.4:按维度加权;未知维度默认 1.0(降级容错)
            dimension = issue.get("dimension")
            weight = DIMENSION_WEIGHTS.get(dimension, 1.0) if isinstance(dimension, str) else 1.0
            score -= severity_deduct * weight
        return max(0, round(score))

    def count_effective_issues(self) -> int:
        """非反事实豁免的 issue 数(给前端 chip 显主要问题数用)。"""
        if self.state != "done":
            return 0
        return sum(
            1 for i in self.issues
            if isinstance(i, dict) and not i.get("counterfactual_exempt")
        )

    def to_response(self, is_alive: bool = False) -> dict[str, Any]:
        """API 返回口径。

        Args:
            is_alive: Sprint D.7 zombie 检测派生字段
                state='running' + is_alive=False → 后端重启后的僵尸,前端可显"重新触发"
                调用方决定怎么算:trigger 刚 kick_off 后传 True;GET latest 调
                canonical_guardian_service.is_audit_alive(audit_id) 算实时值;
                history 列表默认 False(历史记录无意义)。
        """
        return {
            "id": self.id,
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "state": self.state,
            "issues": self.issues,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "cost_yuan": round(self.cost_yuan, 4),
            "error_message": self.error_message,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "is_alive": is_alive,
            # #7(2026-05-23):正典 0-100 总分(state='done' 才有值)+ 主要问题数
            "overall_score": self.compute_overall_score(),
            "effective_issues_count": self.count_effective_issues(),
        }
