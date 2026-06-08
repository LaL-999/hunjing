"""自洽守护者服务 — Sprint 1.R。

对接 prompts/self_consistency_guardian.md。给已 done 的 simulation 跑一次诊断,
按 8 维度找问题 + 给可操作 fix,落库到 audits 表。

设计原则:
- 诊断免费(成本 ~0.05 元),不引入 audit_per_month 配额
- 同一 sim 允许多次诊断(用户改完角色再 audit 验证修好了)
- LLM 输出严格 JSON,出格的 kind / 缺字段 → 兜底过滤(不抛异常给用户,只丢非法 issue)
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from app.db import execute, fetch_all, fetch_one, transaction
from app.models.audit import Audit
from app.schemas.audit import (
    ACCEPT_PAYLOAD_FIELDS_BY_TARGET,
    ACCEPT_PAYLOAD_OPS,
    ACCEPT_PAYLOAD_TARGETS,
    ALL_KINDS,
    KIND_TO_PAYLOAD_TARGET,
    SIM_CONFIG_CUSTOM_STYLE_HINT_MAX_LEN,
    SIM_CONFIG_DIVERGENCE_PREFIX_MAX_LEN,
    SIM_CONFIG_RESHAPE_DELTA_RANGE,
    SIM_CONFIG_TARGET_CHARS_DELTA_RANGE,
    USER_FIXABLE_KINDS,
)
from app.services.llm_client import (
    LlmCallFailed,
    LlmJsonParseFailed,
    call_llm_json,
    estimate_cost_yuan,
)
from app.services.project_service import (
    ResourceNotFoundOrForbidden,
    iso_now,
)
from app.services.simulation_service import get_simulation_or_404

# === 路径 ===
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"
PROMPT_FILE = "self_consistency_guardian.md"

# === 字段中文翻译网(prompt 已要求 LLM 输出中文,这里是最后兜底)===
# 按字符长度倒序排,确保 'no_go_list' 在 'list' 之前替换,避免短串先匹配
# value 用「」包裹,与 prompt 内规范一致,且明确字段边界
FIELD_TRANSLATIONS: list[tuple[str, str]] = sorted(
    [
        ("characters_snapshot", "「角色快照」"),
        ("custom_style_hint", "「自定义语体描述」"),
        ("voice_fingerprint", "「角色口吻」"),
        ("reshape_percent", "「重塑度」"),
        ("context_simulation_ids", "「前文推演」"),
        ("narrative_summary", "「前文摘要」"),
        ("relationships", "「关系」"),
        ("participants", "「参与角色」"),
        ("no_go_list", "「禁忌」"),
        ("personality", "「性格」"),
        ("description", "「描述」"),
        ("identity", "「身份」"),
        ("divergence", "「剧情锚点」"),
        ("target_chars", "「目标字数」"),
        ("narrative", "「产物」"),
        ("timeline", "「时间线」"),
        ("quotes", "「台词」"),
        ("style", "「语体」"),
        ("name", "「名字」"),
        ("kind", "「类型」"),
    ],
    key=lambda kv: -len(kv[0]),
)


def _zh_field_names(text: str) -> str:
    """LLM 偶尔不听 prompt 把英文 key 漏出来,这里兜底替换成中文。

    替换边界:用简单 substring 替换。不做严格 token 边界判断,因为我们
    替换目标是英文标识符,中文文本不会撞到这些 ASCII 序列。
    """
    if not text:
        return text
    out = text
    for en, zh in FIELD_TRANSLATIONS:
        out = out.replace(en, zh)
    return out


# ======================================================================
# M7.C(2026-05-20)结构化修复 payload 清洗
# ======================================================================

# M7.K(2026-05-20)kind → target 映射从 schemas.audit 集中导入(避免双源)
# 旧 _KIND_TO_TARGET 弃用,改用 KIND_TO_PAYLOAD_TARGET(已 import)


def _clean_actionable_fix_payload(raw: Any, kind: str) -> Optional[dict[str, Any]]:
    """LLM 输出的 actionable_fix_payload 清洗 + 白名单过滤。

    M7.K(2026-05-20)升级:8 类 kind 全部接受 payload
      - 用户可修类(character/event/relationship)→ 走 "operations" 列表(格式 A)
      - LLM-only 类(dialogue_flat/turn_jarring/pacing_off/opening_weak/whitespace_imbalance)
        → 走 "patches" dict(格式 B,target=sim_config)

    出格 / 类型不对 / 没合法 op-or-patch → 返 None(让 UI 走"按建议重生成"兜底)
    """
    if not isinstance(raw, dict):
        return None
    expected_target = KIND_TO_PAYLOAD_TARGET.get(kind)
    if expected_target is None:
        return None

    # target 与 kind 不一致 → 静默用 kind 推的(防 LLM 写错)
    target = raw.get("target")
    if target not in ACCEPT_PAYLOAD_TARGETS or target != expected_target:
        target = expected_target

    # ===== 格式 B:sim_config target(LLM-only 类)— patches dict =====
    if target == "sim_config":
        raw_patches = raw.get("patches")
        if not isinstance(raw_patches, dict):
            return None
        allowed_fields = ACCEPT_PAYLOAD_FIELDS_BY_TARGET[target]
        cleaned_patches: dict[str, Any] = {}
        for field in allowed_fields:
            if field not in raw_patches:
                continue
            val = raw_patches[field]
            if val is None or val == "":
                continue
            cleaned = _clean_sim_config_patch_value(field, val)
            if cleaned is not None:
                cleaned_patches[field] = cleaned
        if not cleaned_patches:
            return None
        return {"target": target, "patches": cleaned_patches}

    # ===== 格式 A:character / event / relationship — operations list(M7.C 原逻辑)=====
    raw_ops = raw.get("operations")
    if not isinstance(raw_ops, list):
        return None

    allowed_fields = ACCEPT_PAYLOAD_FIELDS_BY_TARGET.get(target, ())
    cleaned_ops: list[dict[str, Any]] = []
    for op_item in raw_ops:
        if not isinstance(op_item, dict):
            continue
        field = op_item.get("field")
        op = op_item.get("op")
        value = op_item.get("value")
        if field not in allowed_fields:
            continue
        if op not in ACCEPT_PAYLOAD_OPS:
            continue
        if value is None or value == "":
            continue
        # value 必须是 string 或 list[str](其它复杂类型丢)
        if isinstance(value, str):
            value_clean = value.strip()
            if not value_clean:
                continue
        elif isinstance(value, list):
            value_clean = [
                str(v).strip() for v in value
                if isinstance(v, (str, int, float)) and str(v).strip()
            ]
            if not value_clean:
                continue
        else:
            continue
        cleaned_ops.append({"field": field, "op": op, "value": value_clean})
        if len(cleaned_ops) >= 3:    # 单条 issue 最多 3 个 operation(prompt 铁律)
            break

    if not cleaned_ops:
        return None
    return {"target": target, "operations": cleaned_ops}


def _clean_sim_config_patch_value(field: str, val: Any) -> Any:
    """M7.K(2026-05-20)sim_config patch 单字段值清洗 + 范围兜底。"""
    if field == "reshape_percent_delta":
        try:
            iv = int(val)
        except (TypeError, ValueError):
            return None
        lo, hi = SIM_CONFIG_RESHAPE_DELTA_RANGE
        return max(lo, min(hi, iv))
    if field == "target_chars_delta":
        try:
            iv = int(val)
        except (TypeError, ValueError):
            return None
        lo, hi = SIM_CONFIG_TARGET_CHARS_DELTA_RANGE
        return max(lo, min(hi, iv))
    if field == "custom_style_hint":
        if not isinstance(val, str):
            return None
        s = val.strip()
        if not s:
            return None
        return s[:SIM_CONFIG_CUSTOM_STYLE_HINT_MAX_LEN]
    if field == "divergence_prefix":
        if not isinstance(val, str):
            return None
        s = val.strip()
        if not s:
            return None
        return s[:SIM_CONFIG_DIVERGENCE_PREFIX_MAX_LEN]
    return None


# === 异常 ===

class SimulationNotAuditable(Exception):
    """sim 状态不是 done — 没产物可诊断。"""


# === 工具 ===

def _load_prompt() -> str:
    return (PROMPTS_DIR / PROMPT_FILE).read_text(encoding="utf-8")


def _validate_audit_response(raw: Any) -> dict[str, Any]:
    """LLM 输出兜底过滤。

    - 顶层必须是 dict
    - overall_score 缺失 / 越界 → 默认 50
    - issues 必须是 list,逐条过滤(kind 非法 / 缺字段直接丢)
    - regenerate_recommendation 缺失 → 默认提示文本
    """
    if not isinstance(raw, dict):
        raise LlmJsonParseFailed(
            f"LLM 输出顶层不是 object,实际:{type(raw).__name__}"
        )

    score = raw.get("overall_score", 50)
    try:
        score_int = int(score)
    except (TypeError, ValueError):
        score_int = 50
    score_int = max(0, min(100, score_int))

    raw_issues = raw.get("issues", [])
    if not isinstance(raw_issues, list):
        raw_issues = []

    cleaned_issues: list[dict[str, Any]] = []
    for item in raw_issues:
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        if kind not in ALL_KINDS:
            continue
        # 用户可修类要求 subject_id;LLM-only 类允许 null
        subject_id = item.get("subject_id")
        if kind in USER_FIXABLE_KINDS and not subject_id:
            # 用户可修类没 subject_id → 仍接收(前端不显跳转按钮);
            # 不丢,因为可能 LLM 偶尔漏字段但内容仍有价值
            subject_id = None
        evidence = item.get("evidence_in_narrative", "")
        if not evidence or not isinstance(evidence, str):
            # evidence 是核心防编造抓手,缺了直接丢
            continue
        # M7.C(2026-05-20):清洗 actionable_fix_payload 结构化建议
        # LLM 给的话即按白名单过滤;LLM-only 类(非 USER_FIXABLE) → 强制 None
        # 出格 / 损坏 / 无法解析 → 设 None(走兜底 "去修" 路径)
        fix_payload = _clean_actionable_fix_payload(
            item.get("actionable_fix_payload"), kind,
        )

        cleaned_issues.append({
            "kind": kind,
            "subject_id": subject_id,
            "subject_name": str(item.get("subject_name", "")),
            # 三段文本走中文翻译网(LLM 漏的英文 key 兜底替换)
            "evidence_in_narrative": _zh_field_names(evidence[:200]),
            "root_cause_in_setup": _zh_field_names(str(item.get("root_cause_in_setup", ""))),
            "actionable_fix": _zh_field_names(str(item.get("actionable_fix", ""))),
            "actionable_fix_payload": fix_payload,
            "accepted_at": None,    # 新审计的 issue 默认未采纳
        })
        if len(cleaned_issues) >= 8:
            break   # prompt 铁律 3:最多 8 条

    rec = raw.get("regenerate_recommendation", "")
    if not isinstance(rec, str) or not rec:
        rec = "可继续编辑设定后重新生成。"
    rec = _zh_field_names(rec)

    return {
        "overall_score": score_int,
        "issues": cleaned_issues,
        "regenerate_recommendation": rec,
    }


# ======================================================================
# 主流程 ① — audit_simulation(LLM + 兜底过滤 + 落库)
# ======================================================================

def audit_simulation(
    conn: sqlite3.Connection, sim_id: str, user_id: str
) -> dict:
    """触发诊断。返回 AuditResponse 形式 dict。

    异常:
      ResourceNotFoundOrForbidden — sim 不属于当前用户
      SimulationNotAuditable      — sim 状态非 done
      LlmCallFailed               — LLM 调用失败
      LlmJsonParseFailed          — LLM 响应非合法 JSON
    """
    sim = get_simulation_or_404(conn, sim_id, user_id)
    if sim.state != "done":
        raise SimulationNotAuditable(
            f"只能诊断已完成的推演,当前状态:{sim.state}"
        )
    if not sim.narrative:
        raise SimulationNotAuditable("产物为空,无法诊断")

    # 拼 LLM 输入
    prompt_input = {
        "divergence": sim.divergence,
        "characters_snapshot": sim.characters_snapshot,
        "narrative": sim.narrative,
        "timeline": sim.timeline,
    }

    started_at = datetime.now(timezone.utc)
    system_prompt = _load_prompt()
    parsed, usage = call_llm_json(system_prompt, prompt_input, retries=2)
    duration_ms = int(
        (datetime.now(timezone.utc) - started_at).total_seconds() * 1000
    )

    cleaned = _validate_audit_response(parsed)

    audit_id = str(uuid.uuid4())
    cost_yuan = estimate_cost_yuan(usage["input_tokens"], usage["output_tokens"])
    triggered_at = iso_now()

    with transaction(conn) as tx:
        execute(
            tx,
            "INSERT INTO audits "
            "(id, simulation_id, user_id, overall_score, issues_json, "
            " regenerate_recommendation, tokens_input, tokens_output, "
            " cost_yuan, duration_ms, triggered_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                audit_id, sim_id, user_id,
                cleaned["overall_score"],
                json.dumps(cleaned["issues"], ensure_ascii=False),
                cleaned["regenerate_recommendation"],
                usage["input_tokens"], usage["output_tokens"],
                cost_yuan, duration_ms, triggered_at,
            ),
        )

    return {
        "id": audit_id,
        "simulation_id": sim_id,
        "overall_score": cleaned["overall_score"],
        "issues": cleaned["issues"],
        "regenerate_recommendation": cleaned["regenerate_recommendation"],
        "cost_yuan": round(cost_yuan, 4),
        "duration_ms": duration_ms,
        "triggered_at": triggered_at,
    }


# ======================================================================
# 主流程 ② — get_latest_audit(detail 页恢复用户上次的诊断结果)
# ======================================================================

def get_latest_audit(
    conn: sqlite3.Connection, sim_id: str, user_id: str
) -> dict | None:
    """取该 sim 的最新一条 audit。无则返回 None(端点转 404)。

    先调 get_simulation_or_404 鉴权 — 跨用户访问 sim 会 raise,与其它端点统一。
    """
    get_simulation_or_404(conn, sim_id, user_id)   # 鉴权,不用返回值
    # ROWID DESC 作 tiebreaker:iso_now() 秒级精度,两次诊断撞秒时按插入顺序兜底
    row = fetch_one(
        conn,
        "SELECT * FROM audits WHERE simulation_id=? "
        "ORDER BY triggered_at DESC, ROWID DESC LIMIT 1",
        (sim_id,),
    )
    if not row:
        return None
    audit = Audit.from_row(row)
    return audit.to_response()


__all__ = [
    "SimulationNotAuditable",
    "audit_simulation",
    "get_latest_audit",
]
