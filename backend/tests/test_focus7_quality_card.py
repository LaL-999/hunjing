"""Sprint 6.A2 路线图 #7(2026-05-23)— 续写质量审计卡(简版)打分算法测试。

只测 CanonicalAudit.compute_overall_score 纯逻辑:
  1. state != 'done' → 返 None(前端展示等待)
  2. issues 空 / 全是 strict_canonical → 满分 100
  3. severe_breach 重扣 25 + obvious_drift 扣 12 + minor_drift 扣 5 — 算数正确
  4. 反事实豁免的 issue 不计入
  5. 扣到 0 封底
  6. count_effective_issues 同步只数非豁免
"""
from __future__ import annotations

import json

from app.models.canonical_audit import CanonicalAudit


def _make_audit(state: str, issues: list[dict]) -> CanonicalAudit:
    """构造 CanonicalAudit 测试实例(跳过 DB)。"""
    return CanonicalAudit(
        id="test-id",
        simulation_id="sim-1",
        project_id="proj-1",
        user_id="user-1",
        state=state,
        issues_json=json.dumps(issues, ensure_ascii=False),
        tokens_input=0,
        tokens_output=0,
        cost_yuan=0.0,
        error_message=None,
        created_at="2026-05-23T00:00:00+00:00",
        completed_at="2026-05-23T00:00:01+00:00" if state == "done" else None,
    )


# ============================================================
# 1. state != 'done' → None
# ============================================================

def test_score_none_when_running():
    a = _make_audit("running", [])
    assert a.compute_overall_score() is None


def test_score_none_when_failed():
    a = _make_audit("failed", [])
    assert a.compute_overall_score() is None


# ============================================================
# 2. 满分场景
# ============================================================

def test_score_100_when_no_issues():
    a = _make_audit("done", [])
    assert a.compute_overall_score() == 100
    assert a.count_effective_issues() == 0


def test_score_100_when_all_strict_canonical():
    """strict_canonical = 完全符合,实际上 prompt 一般不会把这种放 issues,
    但兜底应该 0 扣分 → 100"""
    a = _make_audit("done", [
        {"dimension": "value_orientation", "severity": "strict_canonical"},
        {"dimension": "worldview", "severity": "strict_canonical"},
    ])
    assert a.compute_overall_score() == 100


# ============================================================
# 3. severity 加权扣分算数
# ============================================================

def test_score_minor_drift_deducts_5():
    a = _make_audit("done", [
        {"dimension": "value_orientation", "severity": "minor_drift"},
    ])
    assert a.compute_overall_score() == 95


def test_score_obvious_drift_deducts_12():
    a = _make_audit("done", [
        {"dimension": "value_orientation", "severity": "obvious_drift"},
    ])
    assert a.compute_overall_score() == 88


def test_score_severe_breach_deducts_25():
    a = _make_audit("done", [
        {"dimension": "value_orientation", "severity": "severe_breach"},
    ])
    assert a.compute_overall_score() == 75


def test_score_combined_deductions():
    """B5.4(2026-05-27)— 加权扣分:
    - value_orientation severe_breach: 25 × 1.0 = 25
    - worldview obvious_drift: 12 × 1.3 = 15.6
    - era_physics obvious_drift: 12 × 0.9 = 10.8
    - character_consistency obvious_drift: 12 × 1.3 = 15.6
    - value_orientation minor_drift × 2: 5 × 1.0 × 2 = 10
    总扣 = 25 + 15.6 + 10.8 + 15.6 + 10 = 77 → 100 - 77 = 23
    """
    a = _make_audit("done", [
        {"dimension": "value_orientation", "severity": "severe_breach"},
        {"dimension": "worldview", "severity": "obvious_drift"},
        {"dimension": "era_physics", "severity": "obvious_drift"},
        {"dimension": "character_consistency", "severity": "obvious_drift"},
        {"dimension": "value_orientation", "severity": "minor_drift"},
        {"dimension": "value_orientation", "severity": "minor_drift"},
    ])
    assert a.compute_overall_score() == 23
    assert a.count_effective_issues() == 6


# ============================================================
# 4. 反事实豁免不计入
# ============================================================

def test_score_counterfactual_exempt_not_counted():
    a = _make_audit("done", [
        {"dimension": "tone", "severity": "severe_breach", "counterfactual_exempt": True, "exempt_reason": "用户主动重塑"},
        {"dimension": "worldview", "severity": "minor_drift"},
    ])
    # B5.4 加权:第 1 条豁免不算,第 2 条 worldview minor_drift = 5 × 1.3 = 6.5
    # 100 - 6.5 = 93.5 → round = 94(Python 3 banker's rounding)
    assert a.compute_overall_score() == 94
    # effective_issues_count 也只数非豁免
    assert a.count_effective_issues() == 1


def test_score_all_exempt_returns_full_score():
    a = _make_audit("done", [
        {"dimension": "tone", "severity": "severe_breach", "counterfactual_exempt": True},
        {"dimension": "worldview", "severity": "obvious_drift", "counterfactual_exempt": True},
    ])
    assert a.compute_overall_score() == 100
    assert a.count_effective_issues() == 0


# ============================================================
# 5. 扣到 0 封底(不出负数)
# ============================================================

def test_score_clamped_at_zero():
    # 10 条 severe_breach = -250 → 应封底到 0
    issues = [
        {"dimension": "value_orientation", "severity": "severe_breach"}
        for _ in range(10)
    ]
    a = _make_audit("done", issues)
    assert a.compute_overall_score() == 0


# ============================================================
# 6. 兜底:脏 issue 结构不抛
# ============================================================

def test_score_robust_against_malformed_issues():
    """LLM 可能返奇怪结构 — 加固 model 兜底"""
    a = _make_audit("done", [
        {"severity": "minor_drift"},          # 缺 dimension OK
        {"dimension": "tone"},                # 缺 severity → 0 扣
        "not a dict",                         # 非 dict
        None,                                 # None
        {"severity": "unknown_severity"},     # 非枚举 severity
        {"dimension": "value_orientation", "severity": "minor_drift"},  # 正常
    ])
    # 第 1 条 -5,第 6 条 -5,其他 0 扣 = 90
    assert a.compute_overall_score() == 90


# ============================================================
# 7. to_response 暴露新字段
# ============================================================

def test_to_response_includes_score_and_count():
    a = _make_audit("done", [
        {"dimension": "value_orientation", "severity": "obvious_drift"},
        {"dimension": "value_orientation", "severity": "minor_drift"},
    ])
    resp = a.to_response()
    assert resp["overall_score"] == 83  # 100 - 12 - 5
    assert resp["effective_issues_count"] == 2


def test_to_response_running_returns_null_score():
    a = _make_audit("running", [])
    resp = a.to_response()
    assert resp["overall_score"] is None
    assert resp["effective_issues_count"] == 0


# ============================================================
# 8. B5.4(2026-05-27)— 维度权重加权扣分
# ============================================================

def test_score_weighted_character_consistency_severe():
    """B5.4:角色 severe_breach = 25 × 1.3 = 32.5 → round = 32 → 100-32 = 68"""
    a = _make_audit("done", [
        {"dimension": "character_consistency", "severity": "severe_breach"},
    ])
    # 100 - 32.5 = 67.5 → round(67.5) = 68(Python banker's,5.5→6)
    assert a.compute_overall_score() == 68


def test_score_weighted_detail_authenticity_severe():
    """B5.4:细节 severe_breach = 25 × 0.7 = 17.5 → round = 18 → 100-18 = 82"""
    a = _make_audit("done", [
        {"dimension": "detail_authenticity", "severity": "severe_breach"},
    ])
    # 100 - 17.5 = 82.5 → round(82.5) = 82(Python banker's,2.5→2)
    assert a.compute_overall_score() == 82


def test_score_weighted_body_register_alignment():
    """B5.4:身体描写尺度对齐 = 1.2× 权重(灵魂续写关键)"""
    a = _make_audit("done", [
        {"dimension": "body_register_alignment", "severity": "obvious_drift"},
    ])
    # 12 × 1.2 = 14.4 → 100 - 14.4 = 85.6 → round = 86
    assert a.compute_overall_score() == 86


def test_score_weighted_unknown_dimension_falls_back_to_1():
    """B5.4:未知维度(LLM 偶发自创)默认 1.0× 不崩"""
    a = _make_audit("done", [
        {"dimension": "fake_unknown_dim", "severity": "obvious_drift"},
    ])
    # 12 × 1.0 = 12 → 100 - 12 = 88
    assert a.compute_overall_score() == 88


def test_score_weighted_outline_execution_severe():
    """P5.2(2026-05-27):outline_execution = 1.3× 权重(剧情走偏 = 灵魂崩)"""
    a = _make_audit("done", [
        {"dimension": "outline_execution", "severity": "severe_breach"},
    ])
    # 25 × 1.3 = 32.5 → 100 - 32.5 = 67.5 → round = 68
    assert a.compute_overall_score() == 68


def test_score_weighted_outline_execution_obvious():
    """P5.2:outline_execution 1.3× obvious_drift 扣分"""
    a = _make_audit("done", [
        {"dimension": "outline_execution", "severity": "obvious_drift"},
    ])
    # 12 × 1.3 = 15.6 → 100 - 15.6 = 84.4 → round = 84
    assert a.compute_overall_score() == 84
