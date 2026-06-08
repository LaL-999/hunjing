"""SQLite 连接 + 事务上下文管理器。

ADR §3.1 配置:
- WAL 模式(并发友好)
- 强制外键(SQLite 默认关)
- 每个请求一个 connection(走 FastAPI Depends),无连接池
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import settings


def _connect(db_path: Path) -> sqlite3.Connection:
    """打开 connection 并应用 PRAGMA。

    db_path 父目录不存在时自动创建(Sprint 1.A 首次启动时 backend/data/ 不存在)。
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        detect_types=sqlite3.PARSE_DECLTYPES,
        check_same_thread=False,  # FastAPI 走线程池,需要允许跨线程
    )
    conn.row_factory = sqlite3.Row  # row 像 dict 一样取
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")  # WAL 下的合理选择
    return conn


def get_connection() -> sqlite3.Connection:
    """获取一个新 connection。FastAPI Depends 用 get_db 包装它。"""
    return _connect(settings.db_abs_path)


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """事务上下文。退出时 commit;异常时 rollback。

    用法:
        with transaction(conn) as tx:
            tx.execute("INSERT ...")
            tx.execute("UPDATE ...")
        # commit 在 with 退出时自动
    """
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def fetch_one(
    conn: sqlite3.Connection, sql: str, params: tuple = ()
) -> sqlite3.Row | None:
    cur = conn.execute(sql, params)
    return cur.fetchone()


def fetch_all(
    conn: sqlite3.Connection, sql: str, params: tuple = ()
) -> list[sqlite3.Row]:
    cur = conn.execute(sql, params)
    return cur.fetchall()


def execute(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    """执行写操作,返回 lastrowid(INSERT)或 rowcount(UPDATE/DELETE)。"""
    cur = conn.execute(sql, params)
    return cur.lastrowid if cur.lastrowid else cur.rowcount


def healthcheck() -> bool:
    """启动时 / /healthz 接口可调,验证 SQLite 可读写。"""
    try:
        conn = get_connection()
        try:
            conn.execute("SELECT 1").fetchone()
            return True
        finally:
            # 即使 SELECT 抛错也要 close,避免连接泄漏
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        # 2026-06-02:不再静默吞 — 至少 log 出来,便于运维诊断"为什么 /healthz 返 fail"
        import logging
        logging.getLogger(__name__).error(
            f"db healthcheck failed: {type(e).__name__}: {e}"
        )
        return False
