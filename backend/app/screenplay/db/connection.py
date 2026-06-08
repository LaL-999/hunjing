"""剧创态 SQLite 连接 — 复用父平台 DB(同一个 huimeng.db 文件)。

设计:
  - 父平台 `app.db.get_connection()` 已配 WAL + FK + Row factory
  - 剧创态 sp_* 表与父平台原表共存于同一个 huimeng.db
  - 同库的好处:阶段 5 huimeng_bridge 可直接 JOIN 父平台 characters /
    character_drivers / relationship_polarity 等表
  - 启动时 init_db() 跑剧创态 schema(IF NOT EXISTS 幂等)

注:本模块不再自己 connect — 直接复用 app.db.get_connection()。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import get_connection as _platform_get_connection


# 按顺序加载的 schema 文件(后加的表可引用先建的表)
_SCHEMA_FILES = [
    "schema.sql",                # novels / chapters / paragraphs(已加 sp_ 前缀 + user_id)
    "story_bible_schema.sql",    # 故事圣经 5 张表(已加 sp_ 前缀 + user_id)
    "screenplay_schema.sql",     # 剧本组装持久化表 sp_screenplays
]


def get_connection() -> sqlite3.Connection:
    """返一个新连接 — 直接走父平台 get_connection。"""
    return _platform_get_connection()


def init_db() -> None:
    """剧创态启动初始化 — 在父平台 init_db 后再调用,加载 sp_* schema。

    幂等可重跑(IF NOT EXISTS)+ 跑 in-place migrations(ADD COLUMN if not exists)。
    """
    schemas_dir = Path(__file__).parent
    conn = get_connection()
    try:
        for filename in _SCHEMA_FILES:
            path = schemas_dir / filename
            if not path.exists():
                raise FileNotFoundError(f"剧创态 schema 缺失: {filename} at {path}")
            conn.executescript(path.read_text(encoding="utf-8"))
        _run_in_place_migrations(conn)
        conn.commit()
    finally:
        conn.close()


def _run_in_place_migrations(conn: sqlite3.Connection) -> None:
    """幂等 ALTER TABLE — SQLite 无 ADD COLUMN IF NOT EXISTS,所以查 PRAGMA。"""

    def _has_column(table: str, col: str) -> bool:
        cur = conn.execute(f"PRAGMA table_info({table})")
        return any(row[1] == col for row in cur.fetchall())

    # PR#16 — sp_screenplays 版本树字段(创建时已含,这里兜底)
    if not _has_column("sp_screenplays", "parent_screenplay_id"):
        conn.execute("ALTER TABLE sp_screenplays ADD COLUMN parent_screenplay_id TEXT")
    if not _has_column("sp_screenplays", "optimization_origin"):
        conn.execute("ALTER TABLE sp_screenplays ADD COLUMN optimization_origin TEXT")
    if not _has_column("sp_screenplays", "optimization_log_json"):
        conn.execute("ALTER TABLE sp_screenplays ADD COLUMN optimization_log_json TEXT")
