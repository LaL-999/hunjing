"""权限检查中心化 — 所有 CRUD 路由"先确认归属"的工具集。

设计原则:
- 单一异常 ResourceNotFoundOrForbidden 由 router 统一转 404
  (404 而非 403 是为了不暴露资源存在性 — 防探测攻击)
- 用 JOIN + project.user_id 一次查询完成"存在 + 归属"双重检查,无 N+1
- service 层不依赖 FastAPI(对齐 ADR §2.1)

YAGNI 决策(2026-05-09):SQL CRUD 直接放在 router 里写,只把权限检查抽到这里。
阶段 2 业务逻辑变复杂时再考虑各 entity 独立 service。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from app.db import fetch_one
from app.models.character import Character
from app.models.event import Event
from app.models.project import Project
from app.models.relationship import Relationship


class ResourceNotFoundOrForbidden(Exception):
    """资源不存在 OR 不属于当前用户。统一返回 404,不暴露存在性。"""

    def __init__(self, resource: str, resource_id: str):
        super().__init__(f"{resource}({resource_id}) 不存在或无权访问")
        self.resource = resource
        self.resource_id = resource_id


def iso_now() -> str:
    """UTC ISO 8601,不带微秒。所有时间字段统一用此函数。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ========== 4 个 entity 的"取回 + 鉴权"工具函数 ==========

def get_project_or_403(
    conn: sqlite3.Connection, project_id: str, user_id: str
) -> Project:
    row = fetch_one(
        conn,
        "SELECT * FROM projects WHERE id=? AND user_id=?",
        (project_id, user_id),
    )
    if not row:
        raise ResourceNotFoundOrForbidden("project", project_id)
    return Project.from_row(row)


def get_character_or_403(
    conn: sqlite3.Connection, character_id: str, user_id: str
) -> Character:
    """JOIN 检查 character → project → user_id。"""
    row = fetch_one(
        conn,
        "SELECT c.* FROM characters c "
        "JOIN projects p ON p.id = c.project_id "
        "WHERE c.id=? AND p.user_id=?",
        (character_id, user_id),
    )
    if not row:
        raise ResourceNotFoundOrForbidden("character", character_id)
    return Character.from_row(row)


def get_relationship_or_403(
    conn: sqlite3.Connection, relationship_id: str, user_id: str
) -> Relationship:
    row = fetch_one(
        conn,
        "SELECT r.* FROM relationships r "
        "JOIN projects p ON p.id = r.project_id "
        "WHERE r.id=? AND p.user_id=?",
        (relationship_id, user_id),
    )
    if not row:
        raise ResourceNotFoundOrForbidden("relationship", relationship_id)
    return Relationship.from_row(row)


def get_event_or_403(
    conn: sqlite3.Connection, event_id: str, user_id: str
) -> Event:
    row = fetch_one(
        conn,
        "SELECT e.* FROM events e "
        "JOIN projects p ON p.id = e.project_id "
        "WHERE e.id=? AND p.user_id=?",
        (event_id, user_id),
    )
    if not row:
        raise ResourceNotFoundOrForbidden("event", event_id)
    return Event.from_row(row)
