"""Sprint 6.A2 M7.C(2026-05-20)— 自洽守护者「采纳」按钮服务。

输入:audit_id + issue_idx + user_id
输出:把该 issue 的 actionable_fix_payload 应用到对应表(character / event / relationship);
      标记 issue.accepted_at;更新 audits.issues_json;返回 ApplySummary

设计原则:
  - **绝不在没有 actionable_fix_payload 时调 LLM 二次抽取** — 简化范围,失败回退到"去修"
  - **白名单严格** — schemas/audit.py 已声明每个 target 允许的 field;运行时再校验一次
  - **append vs replace 语义**:
    - append:string 字段空格拼接(避免重复内容不去重,留给用户后续 character_focus 处理)
    - append:list 字段(quotes / no_go_list)JSON 数组追加(去掉精确重复)
    - replace:只允许特殊字段(identity 仅在原值空时;relationship.type 必须在枚举内)
  - **幂等防呆**:已 accepted 的 issue 第二次调拒绝 422 — 防止用户连点

异常:
  AuditNotFoundOrForbidden    audit 不存在 / 跨用户访问
  IssueNotAcceptable          issue_idx 越界 / kind 非用户可修 / 无 payload / 已 accepted
  SubjectNotFound             payload 关联的 subject_id 在 DB 找不到(角色被删 / 关系被改 等)
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.db import execute, fetch_one, transaction
from app.models.audit import Audit
from app.schemas.audit import (
    ACCEPT_PAYLOAD_FIELDS_BY_TARGET,
    ACCEPT_PAYLOAD_TARGETS,
    ACCEPT_RELATIONSHIP_TYPE_ENUM,
    ALL_KINDS,
    USER_FIXABLE_KINDS,
)
from app.services.project_service import iso_now


# === 异常 ===

class AuditNotFoundOrForbidden(Exception):
    """audit 不存在 / 不属于该用户。"""


class IssueNotAcceptable(Exception):
    """issue_idx 越界 / kind 非用户可修 / 无 payload / 已 accepted。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class SubjectNotFound(Exception):
    """payload 关联的 character / event / relationship 在 DB 找不到。"""


# ======================================================================
# 主入口
# ======================================================================

def accept_audit_issue(
    conn: sqlite3.Connection,
    audit_id: str,
    issue_idx: int,
    user_id: str,
) -> dict:
    """采纳某 audit 的某 issue,自动把 actionable_fix_payload 应用到对应表。

    Returns:
      {
        "audit_id": str,
        "issue_idx": int,
        "target": "character" | "event" | "relationship",
        "subject_id": str,
        "subject_name": str,
        "operations_applied": [{"field", "op", "value", "new_field_value"}, ...],
        "operations_skipped": [{"field", "op", "reason"}, ...],
        "accepted_at": str,
      }
    """
    # 1. 拉 audit + 鉴权
    row = fetch_one(conn, "SELECT * FROM audits WHERE id=?", (audit_id,))
    if not row:
        raise AuditNotFoundOrForbidden(f"audit {audit_id} 不存在")
    audit = Audit.from_row(row)
    if audit.user_id != user_id:
        raise AuditNotFoundOrForbidden(f"audit {audit_id} 不属于你")

    # 2. 取 issue + 校验
    issues: list[dict[str, Any]] = list(audit.issues)
    if issue_idx < 0 or issue_idx >= len(issues):
        raise IssueNotAcceptable(
            "ISSUE_INDEX_OUT_OF_RANGE",
            f"issue_idx={issue_idx} 越界(共 {len(issues)} 条 issue)",
        )
    issue = issues[issue_idx]
    kind = issue.get("kind")
    # M7.K(2026-05-20)8 类 kind 全部允许采纳;旧 USER_FIXABLE 限制移除
    if kind not in ALL_KINDS:
        raise IssueNotAcceptable(
            "ISSUE_KIND_UNKNOWN",
            f"未知 issue 类型:{kind}",
        )
    if issue.get("accepted_at"):
        raise IssueNotAcceptable(
            "ISSUE_ALREADY_ACCEPTED",
            "该建议已经采纳过了,不能重复采纳",
        )

    payload = issue.get("actionable_fix_payload")
    if not payload or not isinstance(payload, dict):
        raise IssueNotAcceptable(
            "ISSUE_NO_PAYLOAD",
            "AI 未给出结构化建议,请点「按建议重生成」让 AI 整体重写",
        )

    target = payload.get("target")
    if target not in ACCEPT_PAYLOAD_TARGETS:
        raise IssueNotAcceptable(
            "ISSUE_INVALID_TARGET",
            f"payload.target 非法:{target}",
        )

    # ===== M7.K(2026-05-20)sim_config target 分支:不动 DB,只返 patch =====
    if target == "sim_config":
        patches = payload.get("patches") or {}
        if not isinstance(patches, dict) or not patches:
            raise IssueNotAcceptable(
                "ISSUE_NO_PATCHES",
                "payload 无可应用的 sim_config patches",
            )
        # audit 鉴权:确认 audit 属于 user(开头已校验)+ sim 也归属 user(防 audit-sim 错配)
        sim_row = fetch_one(
            conn,
            "SELECT s.id, s.project_id FROM simulations s "
            "JOIN projects p ON p.id = s.project_id "
            "WHERE s.id=? AND p.user_id=?",
            (audit.simulation_id, user_id),
        )
        if sim_row is None:
            raise IssueNotAcceptable(
                "ISSUE_SIM_NOT_FOUND",
                "audit 关联的 sim 不属于你或已被删除",
            )

        # 标 accepted_at(避免重复采纳)
        accepted_at = iso_now()
        issues[issue_idx]["accepted_at"] = accepted_at
        with transaction(conn) as tx:
            execute(
                tx,
                "UPDATE audits SET issues_json=? WHERE id=?",
                (json.dumps(issues, ensure_ascii=False), audit_id),
            )

        return {
            "audit_id": audit_id,
            "issue_idx": issue_idx,
            "target": target,
            # sim_config target 用 audit.simulation_id 当 subject(便于前端跳回项目)
            "subject_id": audit.simulation_id,
            "subject_name": issue.get("subject_name", "全篇"),
            "operations_applied": [],
            "operations_skipped": [],
            "sim_config_patch": patches,    # ⭐ 透传给前端,跳回项目 dock 预填
            "accepted_at": accepted_at,
        }

    # ===== 原 M7.C 链路:character / event / relationship target =====
    subject_id = issue.get("subject_id")
    if not subject_id or not isinstance(subject_id, str):
        raise IssueNotAcceptable(
            "ISSUE_NO_SUBJECT",
            "该 issue 缺失主体 ID(subject_id),无法定位修改对象",
        )

    operations = payload.get("operations") or []
    if not isinstance(operations, list) or not operations:
        raise IssueNotAcceptable(
            "ISSUE_NO_OPERATIONS",
            "payload 无可应用的 operation",
        )

    # 3. 拉 subject + 鉴权 project 归属用户
    subject_row, project_id = _load_subject_with_auth(
        conn, target, subject_id, user_id,
    )

    # 4. 逐 operation 应用 + 收集结果
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for op_item in operations:
        if not isinstance(op_item, dict):
            skipped.append({"reason": "op_not_dict"})
            continue
        field = op_item.get("field")
        op = op_item.get("op")
        value = op_item.get("value")
        allowed_fields = ACCEPT_PAYLOAD_FIELDS_BY_TARGET.get(target, ())
        if field not in allowed_fields:
            skipped.append({"field": field, "op": op, "reason": "field_not_whitelisted"})
            continue
        if op not in ("append", "replace"):
            skipped.append({"field": field, "op": op, "reason": "op_invalid"})
            continue

        try:
            new_value = _apply_operation(
                conn, target, subject_row, field, op, value,
            )
        except _OperationSkipped as e:
            skipped.append({"field": field, "op": op, "reason": e.reason})
            continue

        applied.append({
            "field": field,
            "op": op,
            "value": value,
            "new_field_value": new_value,
        })

    if not applied:
        # 全部 op 都被 skip — 不动 issue accepted 状态,提示用户
        raise IssueNotAcceptable(
            "ISSUE_ALL_OPS_SKIPPED",
            "建议中的所有操作都不可应用(可能 identity 已有值 / 类型枚举非法)— 请点「去修」手动编辑",
        )

    # 5. 标记 issue.accepted_at + 更新 audits.issues_json
    accepted_at = iso_now()
    issues[issue_idx]["accepted_at"] = accepted_at
    with transaction(conn) as tx:
        execute(
            tx,
            "UPDATE audits SET issues_json=? WHERE id=?",
            (json.dumps(issues, ensure_ascii=False), audit_id),
        )

    return {
        "audit_id": audit_id,
        "issue_idx": issue_idx,
        "target": target,
        "subject_id": subject_id,
        "subject_name": issue.get("subject_name", ""),
        "operations_applied": applied,
        "operations_skipped": skipped,
        "sim_config_patch": None,    # M7.K:character/event/relationship 路径不返 patch
        "accepted_at": accepted_at,
    }


# ======================================================================
# subject 加载 + 鉴权
# ======================================================================

def _load_subject_with_auth(
    conn: sqlite3.Connection,
    target: str,
    subject_id: str,
    user_id: str,
) -> tuple[sqlite3.Row, str]:
    """根据 target 拉 subject row,顺便鉴权该 subject 关联 project 属于该用户。

    Returns: (subject_row, project_id)
    Raises: SubjectNotFound
    """
    if target == "character":
        row = fetch_one(
            conn,
            "SELECT c.*, p.user_id AS _p_user_id "
            "FROM characters c "
            "JOIN projects p ON p.id = c.project_id "
            "WHERE c.id=?",
            (subject_id,),
        )
        if not row:
            raise SubjectNotFound(f"角色 {subject_id} 不存在(可能已被删除)")
        if row["_p_user_id"] != user_id:
            raise SubjectNotFound(f"角色 {subject_id} 不属于你")
        return row, row["project_id"]

    if target == "event":
        row = fetch_one(
            conn,
            "SELECT e.*, p.user_id AS _p_user_id "
            "FROM events e "
            "JOIN projects p ON p.id = e.project_id "
            "WHERE e.id=?",
            (subject_id,),
        )
        if not row:
            raise SubjectNotFound(f"事件 {subject_id} 不存在")
        if row["_p_user_id"] != user_id:
            raise SubjectNotFound(f"事件 {subject_id} 不属于你")
        return row, row["project_id"]

    if target == "relationship":
        row = fetch_one(
            conn,
            "SELECT r.*, p.user_id AS _p_user_id "
            "FROM relationships r "
            "JOIN projects p ON p.id = r.project_id "
            "WHERE r.id=?",
            (subject_id,),
        )
        if not row:
            raise SubjectNotFound(f"关系 {subject_id} 不存在")
        if row["_p_user_id"] != user_id:
            raise SubjectNotFound(f"关系 {subject_id} 不属于你")
        return row, row["project_id"]

    raise SubjectNotFound(f"未知 target:{target}")


# ======================================================================
# 单条 operation 应用
# ======================================================================

class _OperationSkipped(Exception):
    """单条 op 被跳过(白名单 / 兜底);带 reason 给前端展示。"""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


def _apply_operation(
    conn: sqlite3.Connection,
    target: str,
    subject_row: sqlite3.Row,
    field: str,
    op: str,
    value: Any,
) -> Any:
    """应用单条 operation;返回字段应用后最终值(给 API response 用)。

    Raises _OperationSkipped 表示被跳过(白名单 / 兜底),整体不算失败。
    """
    if target == "character":
        return _apply_character_op(conn, subject_row, field, op, value)
    if target == "event":
        return _apply_event_op(conn, subject_row, field, op, value)
    if target == "relationship":
        return _apply_relationship_op(conn, subject_row, field, op, value)
    raise _OperationSkipped("unknown_target")


def _apply_character_op(
    conn: sqlite3.Connection,
    char_row: sqlite3.Row,
    field: str,
    op: str,
    value: Any,
) -> Any:
    """character 表的字段应用。"""
    char_id = char_row["id"]
    now = iso_now()

    if field == "personality":
        # append: 空格拼接;若原已有,加换行 + value
        existing = (char_row["personality"] or "").strip()
        if op == "append":
            new_value = f"{existing}\n{value}".strip() if existing else str(value).strip()
        elif op == "replace":
            new_value = str(value).strip()
        else:
            raise _OperationSkipped("op_invalid")
        with transaction(conn) as tx:
            execute(
                tx,
                "UPDATE characters SET personality=?, updated_at=? WHERE id=?",
                (new_value, now, char_id),
            )
        return new_value

    if field == "quotes":
        # 总是数组化处理
        existing_list = _parse_json_list(char_row["quotes"])
        if op == "append":
            new_items = _ensure_str_list(value)
            # 去精确重复
            merged = list(existing_list)
            for it in new_items:
                if it not in merged:
                    merged.append(it)
            new_list = merged
        elif op == "replace":
            new_list = _ensure_str_list(value)
        else:
            raise _OperationSkipped("op_invalid")
        with transaction(conn) as tx:
            execute(
                tx,
                "UPDATE characters SET quotes=?, updated_at=? WHERE id=?",
                (json.dumps(new_list, ensure_ascii=False), now, char_id),
            )
        return new_list

    if field == "no_go_list":
        existing_list = _parse_json_list(char_row["no_go_list"])
        if op == "append":
            new_items = _ensure_str_list(value)
            merged = list(existing_list)
            for it in new_items:
                if it not in merged:
                    merged.append(it)
            new_list = merged
        elif op == "replace":
            new_list = _ensure_str_list(value)
        else:
            raise _OperationSkipped("op_invalid")
        with transaction(conn) as tx:
            execute(
                tx,
                "UPDATE characters SET no_go_list=?, updated_at=? WHERE id=?",
                (json.dumps(new_list, ensure_ascii=False), now, char_id),
            )
        return new_list

    if field == "identity":
        # identity 仅在原值为空时 replace(对齐 refine_service 的保护逻辑,
        # 防 LLM 覆盖用户已填的角色定位)
        existing = (char_row["identity"] or "").strip()
        if existing:
            raise _OperationSkipped("identity_already_filled")
        new_value = str(value).strip()
        if not new_value:
            raise _OperationSkipped("value_empty")
        with transaction(conn) as tx:
            execute(
                tx,
                "UPDATE characters SET identity=?, updated_at=? WHERE id=?",
                (new_value, now, char_id),
            )
        return new_value

    raise _OperationSkipped("field_not_whitelisted")


def _apply_event_op(
    conn: sqlite3.Connection,
    event_row: sqlite3.Row,
    field: str,
    op: str,
    value: Any,
) -> Any:
    """event 表的字段应用。"""
    event_id = event_row["id"]

    if field == "description":
        existing = (event_row["description"] or "").strip()
        if op == "append":
            new_value = f"{existing}\n{value}".strip() if existing else str(value).strip()
        elif op == "replace":
            new_value = str(value).strip()
        else:
            raise _OperationSkipped("op_invalid")
        with transaction(conn) as tx:
            execute(
                tx,
                "UPDATE events SET description=? WHERE id=?",
                (new_value, event_id),
            )
        return new_value

    raise _OperationSkipped("field_not_whitelisted")


def _apply_relationship_op(
    conn: sqlite3.Connection,
    rel_row: sqlite3.Row,
    field: str,
    op: str,
    value: Any,
) -> Any:
    """relationship 表的字段应用。"""
    rel_id = rel_row["id"]

    if field == "description":
        existing = (rel_row["description"] or "").strip()
        if op == "append":
            new_value = f"{existing}\n{value}".strip() if existing else str(value).strip()
        elif op == "replace":
            new_value = str(value).strip()
        else:
            raise _OperationSkipped("op_invalid")
        with transaction(conn) as tx:
            execute(
                tx,
                "UPDATE relationships SET description=? WHERE id=?",
                (new_value, rel_id),
            )
        return new_value

    if field == "type":
        # 必须在枚举内
        new_value = str(value).strip()
        if new_value not in ACCEPT_RELATIONSHIP_TYPE_ENUM:
            raise _OperationSkipped(
                f"relationship_type_not_in_enum:{new_value}"
            )
        if op != "replace":
            raise _OperationSkipped("relationship_type_must_replace")
        with transaction(conn) as tx:
            execute(
                tx,
                "UPDATE relationships SET type=? WHERE id=?",
                (new_value, rel_id),
            )
        return new_value

    raise _OperationSkipped("field_not_whitelisted")


# ======================================================================
# helpers
# ======================================================================

def _parse_json_list(raw: Any) -> list[str]:
    """quotes / no_go_list 字段的 JSON 解析兜底。"""
    if not raw or not isinstance(raw, str):
        return []
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, list):
            return []
        return [str(x) for x in parsed if x is not None]
    except (json.JSONDecodeError, TypeError):
        return []


def _ensure_str_list(value: Any) -> list[str]:
    """value 是 string → [value];是 list → 字符串化每项;其它 → []。"""
    if isinstance(value, str):
        v = value.strip()
        return [v] if v else []
    if isinstance(value, list):
        out: list[str] = []
        for x in value:
            if isinstance(x, (str, int, float)):
                s = str(x).strip()
                if s:
                    out.append(s)
        return out
    return []


__all__ = [
    "AuditNotFoundOrForbidden",
    "IssueNotAcceptable",
    "SubjectNotFound",
    "accept_audit_issue",
]
