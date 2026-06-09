"""分集方案持久化 store(2026-06-09)。

替换 episode_planner 的 stateless 模式 — 用户跑完一次分集后,可命名保存,
后续从「我的方案」列表加载历史方案,无需重算。

铁律:user 隔离 — 所有读写路径必须 JOIN sp_novels 验证 user_id,
跨用户访问返 None / 抛 PermissionError(跟 screenplay_store 一致)。

Endpoints:
  - save_plan(name, novel_id, user_id, plan_json) → plan_id
  - list_plans(novel_id, user_id) → list[PlanSummary]
  - get_plan(plan_id, user_id) → Plan | None
  - rename_plan(plan_id, user_id, new_name) → bool
  - delete_plan(plan_id, user_id) → bool
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EpisodePlanSummary:
    """列表用 — 不返完整 plan_json,节省带宽"""
    id: str
    novel_id: str
    scheme_name: str
    preset: str
    target_minutes: float
    recommended_perspective: Optional[str]
    episode_count: int
    scene_count: int
    created_at: str
    updated_at: str


@dataclass
class EpisodePlanFull:
    """详情用 — 含完整 plan_json"""
    id: str
    novel_id: str
    scheme_name: str
    preset: str
    target_minutes: float
    recommended_perspective: Optional[str]
    episode_count: int
    scene_count: int
    plan_data: dict[str, Any]  # 解析后的 MultiPerspectivePlan
    created_at: str
    updated_at: str


# ============================================================
# 验证 helper
# ============================================================

def _verify_novel_owner(
    conn: sqlite3.Connection,
    novel_id: str,
    user_id: str,
) -> bool:
    """novel 是否属于该用户(防越权 INSERT)"""
    row = conn.execute(
        "SELECT 1 FROM sp_novels WHERE id = ? AND user_id = ? LIMIT 1",
        (novel_id, user_id),
    ).fetchone()
    return row is not None


def _verify_plan_owner(
    conn: sqlite3.Connection,
    plan_id: str,
    user_id: str,
) -> Optional[str]:
    """plan 是否属于该用户(返 novel_id 给后续 query 用,None=无权访问)"""
    row = conn.execute(
        """SELECT p.novel_id
           FROM sp_episode_plans p
           JOIN sp_novels n ON p.novel_id = n.id
           WHERE p.id = ? AND n.user_id = ?
           LIMIT 1""",
        (plan_id, user_id),
    ).fetchone()
    return row["novel_id"] if row else None


# ============================================================
# Save
# ============================================================

def save_plan(
    conn: sqlite3.Connection,
    *,
    novel_id: str,
    user_id: str,
    scheme_name: str,
    preset: str,
    target_minutes: float,
    plan_data: dict[str, Any],
) -> str:
    """保存一个分集方案。

    Args:
        plan_data: MultiPerspectivePlan 完整序列化(含 3 视角 + 评分等)

    Returns:
        plan_id

    Raises:
        PermissionError: novel 不属于该用户
        ValueError: scheme_name 空 / 字段缺失
    """
    name = (scheme_name or "").strip()
    if not name:
        raise ValueError("方案名不能为空")
    if len(name) > 80:
        raise ValueError("方案名过长(最多 80 字)")
    if not _verify_novel_owner(conn, novel_id, user_id):
        raise PermissionError("novel 不存在或不属于该用户")

    plan_id = uuid.uuid4().hex
    now = _now_iso()

    # 从 plan_data 拿统计信息(用于列表展示)
    recommended_persp = plan_data.get("recommended_perspective")
    # 集数取推荐视角的;若没有,取第一个视角的
    perspectives = plan_data.get("perspectives") or []
    episode_count = 0
    scene_count = 0
    if perspectives:
        first = perspectives[0]
        # 找推荐视角,fallback 第一个
        for p in perspectives:
            if p.get("perspective") == recommended_persp:
                first = p
                break
        episode_count = len(first.get("episodes") or [])
        scene_count = int(first.get("total_scenes") or 0)

    conn.execute(
        """INSERT INTO sp_episode_plans
           (id, novel_id, scheme_name, preset, target_minutes,
            recommended_perspective, episode_count, scene_count,
            plan_json, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            plan_id, novel_id, name, preset, target_minutes,
            recommended_persp, episode_count, scene_count,
            json.dumps(plan_data, ensure_ascii=False),
            now, now,
        ),
    )
    conn.commit()
    return plan_id


# ============================================================
# List
# ============================================================

def list_plans(
    conn: sqlite3.Connection,
    *,
    novel_id: str,
    user_id: str,
) -> list[EpisodePlanSummary]:
    """列出该 novel 下该用户的所有方案,倒序。

    user 不是 novel 所有者 → 返空列表(不抛错,简化路由层处理)
    """
    if not _verify_novel_owner(conn, novel_id, user_id):
        return []
    rows = conn.execute(
        """SELECT id, novel_id, scheme_name, preset, target_minutes,
                  recommended_perspective, episode_count, scene_count,
                  created_at, updated_at
           FROM sp_episode_plans
           WHERE novel_id = ?
           ORDER BY created_at DESC""",
        (novel_id,),
    ).fetchall()
    return [
        EpisodePlanSummary(
            id=r["id"],
            novel_id=r["novel_id"],
            scheme_name=r["scheme_name"],
            preset=r["preset"],
            target_minutes=float(r["target_minutes"]),
            recommended_perspective=r["recommended_perspective"],
            episode_count=int(r["episode_count"]),
            scene_count=int(r["scene_count"]),
            created_at=r["created_at"],
            updated_at=r["updated_at"],
        )
        for r in rows
    ]


# ============================================================
# Get(详情含 plan_data)
# ============================================================

def get_plan(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    user_id: str,
) -> Optional[EpisodePlanFull]:
    """拿单个方案完整数据。跨用户访问返 None。"""
    if not _verify_plan_owner(conn, plan_id, user_id):
        return None
    row = conn.execute(
        """SELECT id, novel_id, scheme_name, preset, target_minutes,
                  recommended_perspective, episode_count, scene_count,
                  plan_json, created_at, updated_at
           FROM sp_episode_plans
           WHERE id = ?""",
        (plan_id,),
    ).fetchone()
    if not row:
        return None
    try:
        plan_data = json.loads(row["plan_json"])
    except (json.JSONDecodeError, TypeError):
        plan_data = {}
    return EpisodePlanFull(
        id=row["id"],
        novel_id=row["novel_id"],
        scheme_name=row["scheme_name"],
        preset=row["preset"],
        target_minutes=float(row["target_minutes"]),
        recommended_perspective=row["recommended_perspective"],
        episode_count=int(row["episode_count"]),
        scene_count=int(row["scene_count"]),
        plan_data=plan_data,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ============================================================
# Rename
# ============================================================

def rename_plan(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    user_id: str,
    new_name: str,
) -> bool:
    """改名。跨用户返 False。"""
    name = (new_name or "").strip()
    if not name:
        raise ValueError("方案名不能为空")
    if len(name) > 80:
        raise ValueError("方案名过长(最多 80 字)")
    if not _verify_plan_owner(conn, plan_id, user_id):
        return False
    conn.execute(
        "UPDATE sp_episode_plans SET scheme_name = ?, updated_at = ? WHERE id = ?",
        (name, _now_iso(), plan_id),
    )
    conn.commit()
    return True


# ============================================================
# Delete
# ============================================================

def delete_plan(
    conn: sqlite3.Connection,
    *,
    plan_id: str,
    user_id: str,
) -> bool:
    """删方案。跨用户返 False。"""
    if not _verify_plan_owner(conn, plan_id, user_id):
        return False
    conn.execute("DELETE FROM sp_episode_plans WHERE id = ?", (plan_id,))
    conn.commit()
    return True


# ============================================================
# 转 dict(给 router 返 JSON 用)
# ============================================================

def to_summary_dict(s: EpisodePlanSummary) -> dict:
    return asdict(s)


def to_full_dict(f: EpisodePlanFull) -> dict:
    return asdict(f)


__all__ = [
    "EpisodePlanSummary", "EpisodePlanFull",
    "save_plan", "list_plans", "get_plan", "rename_plan", "delete_plan",
    "to_summary_dict", "to_full_dict",
]
