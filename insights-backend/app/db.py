"""insights-backend SQLite 连接 + huimeng.db read-only attach.

设计:
  - 主连接打开 analytics.db(写)
  - ATTACH huimeng.db 为 read-only(用于关联用户画像 / 项目数据)
  - 永不写 huimeng.* — 数据库引擎层保证(ATTACH 用 mode=ro)
  - WAL 模式 + 行字典化(同主平台 backend/app/db.py 风格)
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.config import settings


def _connect(db_path: Path) -> sqlite3.Connection:
    """打开 analytics.db connection + attach huimeng.db read-only。

    关键:主连接必须 uri=True 才能让 ATTACH 的 URI 字符串(file:...?mode=ro)
    被 SQLite 识别为 URI。否则 SQLite 把整个 'file:...?mode=ro' 当文件名,
    报 "unable to open database file"。

    Windows 路径转 URI 用 Path.as_uri():
      C:/Users/foo/db.sqlite → file:///C:/Users/foo/db.sqlite(标准三斜杠形式)
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    main_uri = db_path.as_uri()   # 标准 file:/// URI
    conn = sqlite3.connect(
        main_uri,
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,
        uri=True,                  # ★ 关键:让 SQLite 解析 URI(主连接 + ATTACH 都靠这个)
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    # ATTACH huimeng.db read-only — 本服务"洞察 → 平台单向读"的实现方式
    # mode=ro 由 SQLite 引擎强制保证只读,即使代码写错也写不进去
    huimeng_path = settings.HUIMENG_DB_PATH
    if huimeng_path.exists():
        attach_uri = f"{huimeng_path.as_uri()}?mode=ro"
        conn.execute(f"ATTACH DATABASE '{attach_uri}' AS huimeng")
    # 若 huimeng.db 不存在(测试环境 / 主平台未启动)→ 不 attach,
    # 后续查 huimeng.* 表会报错,本服务做降级处理(返空 / 跳过画像聚合)
    return conn


def get_db_connection() -> sqlite3.Connection:
    """工厂:每次调用产生独立 connection。

    FastAPI Depends 用,生命周期 = 1 request。
    """
    return _connect(settings.ANALYTICS_DB_PATH)


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """上下文管理器:用 with 自动 close。"""
    conn = _connect(settings.ANALYTICS_DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """事务上下文:成功 commit / 失败 rollback。"""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def execute(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple = (),
) -> sqlite3.Cursor:
    """执行 SQL(写),自动 commit。"""
    cursor = conn.execute(sql, params)
    conn.commit()
    return cursor


def fetch_one(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple = (),
) -> sqlite3.Row | None:
    """读单行。"""
    return conn.execute(sql, params).fetchone()


def fetch_all(
    conn: sqlite3.Connection,
    sql: str,
    params: tuple = (),
) -> list[sqlite3.Row]:
    """读多行。"""
    return conn.execute(sql, params).fetchall()


def huimeng_attached(conn: sqlite3.Connection) -> bool:
    """检查 huimeng.db 是否成功 attach **且核心表存在**.

    用法:洞察聚合查询前先判,没 attach / 表不存在就降级到只用 analytics 数据.

    设计:
      - ATTACH 成功只代表文件能打开;主平台未初始化时 huimeng.db 是空 SQLite 文件
      - 必须实质探针某个核心表(如 sqlite_master 里有 users / projects 任一)
      - 任一存在 = "数据可用",返 True
      - 全不存在 = "主平台 db 是空的",返 False(让上层降级)
    """
    rows = conn.execute("PRAGMA database_list").fetchall()
    if not any(r["name"] == "huimeng" for r in rows):
        return False
    # 探针核心表 — 主平台至少有 users / projects 其中之一(实际两者都有)
    try:
        probe = conn.execute(
            "SELECT name FROM huimeng.sqlite_master "
            "WHERE type='table' AND name IN ('users', 'projects', 'simulations') "
            "LIMIT 1"
        ).fetchone()
        return probe is not None
    except sqlite3.OperationalError:
        return False


def run_migrations(conn: sqlite3.Connection, migrations_dir: Path) -> None:
    """跑 migrations 目录下所有 .sql 文件(按文件名升序)。

    简化实现:不维护 migration version 表,每次都跑所有(SQL 用 IF NOT EXISTS 保证幂等)。
    """
    if not migrations_dir.exists():
        return
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        sql_text = sql_file.read_text(encoding="utf-8")
        conn.executescript(sql_text)
    conn.commit()


__all__ = [
    "get_db_connection",
    "get_db",
    "transaction",
    "execute",
    "fetch_one",
    "fetch_all",
    "huimeng_attached",
    "run_migrations",
]
