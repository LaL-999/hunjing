"""剧创态 SQLite 连接 — 复用父平台 DB(同一个 huimeng.db 文件)。

设计:
  - 父平台 `app.db.get_connection()` 已配 WAL + FK + Row factory
  - 剧创态 sp_* 表与父平台原表共存于同一个 huimeng.db
  - 同库的好处:阶段 5 huimeng_bridge 可直接 JOIN 父平台 characters /
    character_drivers / relationship_polarity 等表

阶段 4(2026-06-08):
  - sp_* schema 走父平台 migration runner(migration 085_screenplay_sp_tables.sql)
  - 不再有 `init_db()` 函数 — 启动初始化由 `_auto_apply_migrations` 统一接管
  - 本模块只剩 `get_connection` 代理
"""
from __future__ import annotations

import sqlite3

from app.db import get_connection as _platform_get_connection


def get_connection() -> sqlite3.Connection:
    """返一个新连接 — 直接走父平台 get_connection。"""
    return _platform_get_connection()
