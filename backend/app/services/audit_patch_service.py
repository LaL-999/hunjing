"""Sprint 6.A2 路线图 #2.5(2026-05-22)— 自洽守护者 sim_config patch 跨会话持久化。

背景:M7.K(2026-05-20)用户在 SimulationDetailView 采纳 LLM-only 类 audit issue 后,
后端返回 sim_config_patch,前端写 sessionStorage 等用户回项目 dock 时一次性消费。
痛点:sessionStorage 是 use-once + 跨会话丢失 → 用户关浏览器 / 切走再回来,采纳的建议就丢了。

修法:复用 audit.issues_json 已有的 `accepted_at` 机制,**再加 `applied_at` 字段**(JSON 字段
扩展,无 migration):
  - accepted_at != null + applied_at == null → 「已采纳待应用」(pending)
  - accepted_at != null + applied_at != null → 「已采纳已用」(归档)
  - SimulationDock 提交推演成功后批量 mark_applied

API:
  GET  /api/projects/{project_id}/pending_audit_patches      → 列项目下所有 pending patches
  POST /api/projects/{project_id}/audit_patches/mark_applied → body {items: [{audit_id, issue_idx}]}

异常:
  ProjectNotFoundOrForbidden  项目不存在 / 跨用户
  AuditPatchNotFound          某 (audit_id, issue_idx) 找不到 / 跨用户 / 越界 / 未采纳
"""
from __future__ import annotations

import json
import sqlite3

from app.db import execute, fetch_all, fetch_one, transaction
from app.services.project_service import iso_now


class ProjectNotFoundOrForbidden(Exception):
    """项目不存在 / 不属于该用户。"""


class AuditPatchNotFound(Exception):
    """批量标记中某条 (audit_id, issue_idx) 找不到 / 跨用户 / 越界 / 未采纳。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def list_pending_audit_patches(
    conn: sqlite3.Connection,
    project_id: str,
    user_id: str,
) -> list[dict]:
    """列项目下所有 sim_config target 已采纳未应用的 patches。

    顺序:按 accepted_at ASC(最早采纳的先应用,符合用户预期"先采纳先用")

    Returns:
      [
        {
          "audit_id": str,
          "issue_idx": int,
          "source_sim_id": str,        # audit 关联的 sim id
          "source_label": str,          # 给前端 banner 显示的人话标签(如 "对白扁平")
          "patches": dict,              # sim_config patches: {field: value}
          "accepted_at": str,           # ISO 时间
        },
        ...
      ]
    """
    proj_row = fetch_one(
        conn,
        "SELECT id FROM projects WHERE id=? AND user_id=?",
        (project_id, user_id),
    )
    if proj_row is None:
        raise ProjectNotFoundOrForbidden(f"项目 {project_id} 不存在或不属于你")

    # 拉该项目下所有 audit(JOIN simulations 限定项目)
    rows = fetch_all(
        conn,
        "SELECT a.id, a.simulation_id, a.issues_json "
        "FROM audits a "
        "JOIN simulations s ON s.id = a.simulation_id "
        "WHERE s.project_id=? AND a.user_id=? "
        "ORDER BY a.triggered_at ASC",
        (project_id, user_id),
    )

    pending: list[dict] = []
    for row in rows:
        audit_id = row["id"]
        sim_id = row["simulation_id"]
        try:
            issues = json.loads(row["issues_json"])
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(issues, list):
            continue
        for idx, issue in enumerate(issues):
            if not isinstance(issue, dict):
                continue
            if not issue.get("accepted_at"):
                continue
            if issue.get("applied_at"):
                continue
            payload = issue.get("actionable_fix_payload")
            if not isinstance(payload, dict):
                continue
            if payload.get("target") != "sim_config":
                continue
            patches = payload.get("patches")
            if not isinstance(patches, dict) or not patches:
                continue
            pending.append({
                "audit_id": audit_id,
                "issue_idx": idx,
                "source_sim_id": sim_id,
                "source_label": _kind_to_label(
                    issue.get("kind", ""),
                    issue.get("subject_name"),
                ),
                "patches": patches,
                "accepted_at": issue["accepted_at"],
            })
    pending.sort(key=lambda p: p["accepted_at"])
    return pending


def mark_audit_patches_applied(
    conn: sqlite3.Connection,
    items: list[dict],
    user_id: str,
    project_id: str,
) -> int:
    """批量把指定 (audit_id, issue_idx) 的 issues 标 applied_at = ISO now。

    items: [{"audit_id": str, "issue_idx": int}, ...]
    project_id: C-4 修复(2026-05-23)— 严格校验 audit 必须属于 router URL 的 project_id,
                防同用户跨项目 audit_id 提交(schema 严密性,不是跨用户漏洞)
    Returns: 成功标记的条数(已 applied 的跳过不算)
    Raises: AuditPatchNotFound 任一 item 无效则事务回滚,**全部不写**
    """
    if not items:
        return 0

    now = iso_now()
    # 按 audit_id 分组,减少 UPDATE 次数;同时排查重复 idx
    by_audit: dict[str, list[int]] = {}
    for it in items:
        aid = it.get("audit_id")
        idx = it.get("issue_idx")
        if not isinstance(aid, str) or not isinstance(idx, int):
            raise AuditPatchNotFound("ITEM_INVALID", f"非法 item: {it}")
        by_audit.setdefault(aid, []).append(idx)

    marked = 0
    with transaction(conn) as tx:
        for audit_id, idxs in by_audit.items():
            # C-4 修复(2026-05-23):JOIN simulations 校验 audit 真属于 URL 的 project_id
            # (原代码只校验 user_id,允许同用户跨项目 audit_id 提交)
            row = fetch_one(
                tx,
                "SELECT a.id, a.user_id, a.issues_json "
                "FROM audits a JOIN simulations s ON a.simulation_id = s.id "
                "WHERE a.id = ? AND s.project_id = ?",
                (audit_id, project_id),
            )
            if row is None:
                raise AuditPatchNotFound(
                    "AUDIT_NOT_FOUND",
                    f"audit {audit_id} 不存在 / 不属于该项目",
                )
            if row["user_id"] != user_id:
                raise AuditPatchNotFound(
                    "AUDIT_NOT_FOUND",
                    f"audit {audit_id} 不属于你",
                )
            try:
                issues = json.loads(row["issues_json"])
            except (json.JSONDecodeError, TypeError) as exc:
                raise AuditPatchNotFound(
                    "AUDIT_CORRUPT",
                    f"audit {audit_id} 的 issues_json 损坏",
                ) from exc
            if not isinstance(issues, list):
                raise AuditPatchNotFound(
                    "AUDIT_CORRUPT",
                    f"audit {audit_id} issues 非 list",
                )

            mutated = False
            for idx in idxs:
                if idx < 0 or idx >= len(issues):
                    raise AuditPatchNotFound(
                        "ISSUE_INDEX_OUT_OF_RANGE",
                        f"audit {audit_id} issue_idx={idx} 越界",
                    )
                issue = issues[idx]
                if not isinstance(issue, dict):
                    raise AuditPatchNotFound(
                        "ISSUE_CORRUPT",
                        f"audit {audit_id}.issues[{idx}] 非 dict",
                    )
                # 幂等:已 applied 则跳过(允许 client 重复 mark,不报错)
                if issue.get("applied_at"):
                    continue
                # 业务约束:未采纳的 issue 不允许 mark applied
                if not issue.get("accepted_at"):
                    raise AuditPatchNotFound(
                        "ISSUE_NOT_ACCEPTED",
                        f"audit {audit_id}.issues[{idx}] 未采纳,不能标 applied",
                    )
                issue["applied_at"] = now
                mutated = True
                marked += 1

            if mutated:
                execute(
                    tx,
                    "UPDATE audits SET issues_json=? WHERE id=?",
                    (json.dumps(issues, ensure_ascii=False), audit_id),
                )
    return marked


# ============================================================
# 内部:kind + subject_name → 中文 label(给前端 banner 显示)
# 跟 prompts/self_consistency_guardian.md + frontend KIND_LABEL 对齐
# ============================================================

_KIND_TO_CN = {
    "character_thin": "角色单薄",
    "event_inconsistent": "事件矛盾",
    "relationship_off": "关系失真",
    "dialogue_flat": "对白扁平",
    "turn_jarring": "节奏失衡",
    "pacing_off": "节奏失衡",
    "opening_weak": "开篇乏力",
    "whitespace_imbalance": "留白失衡",
}


def _kind_to_label(kind: str, subject_name: str | None) -> str:
    base = _KIND_TO_CN.get(kind, kind or "建议")
    if subject_name and isinstance(subject_name, str) and subject_name.strip():
        return f"{base}·{subject_name.strip()}"
    return base
