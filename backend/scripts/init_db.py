"""初始化数据库 — 跑 backend/migrations/ 下所有 SQL DDL 文件(按文件名排序)。

用法:
    cd backend
    uv run python scripts/init_db.py

幂等:所有 DDL 用 IF NOT EXISTS,重跑不报错。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 让 scripts/ 下的脚本能 import app(避免 sys.path 问题)
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# Windows 控制台默认 GBK,中文输出强制 utf-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

from app.config import settings
from app.db import execute, fetch_one, get_connection, transaction


def _seed_red_flag_dictionary(conn) -> int:
    """灌 backend/seed/red_flag_seed.json 进 red_flag_dictionary 表(Sprint 2.A)。

    幂等:同 (category, pattern) 已存在则跳过(不覆盖运营手动改的 severity / enabled / note)。
    返回新插入条数。
    """
    import json
    import uuid
    from datetime import datetime, timezone

    seed_file = BACKEND_ROOT / "seed" / "red_flag_seed.json"
    if not seed_file.exists():
        print(f"  ⚠ 未找到 seed 文件 {seed_file},跳过 red_flag seed")
        return 0

    data = json.loads(seed_file.read_text(encoding="utf-8"))
    entries = data.get("entries", [])
    if not entries:
        return 0

    inserted = 0
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with transaction(conn) as tx:
        for e in entries:
            existing = fetch_one(
                tx,
                "SELECT id FROM red_flag_dictionary WHERE category=? AND pattern=?",
                (e["category"], e["pattern"]),
            )
            if existing:
                continue
            execute(
                tx,
                "INSERT INTO red_flag_dictionary "
                "(id, category, pattern, is_regex, severity, enabled, note, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()),
                    e["category"],
                    e["pattern"],
                    1 if e.get("is_regex", False) else 0,
                    e.get("severity", "block"),
                    1 if e.get("enabled", True) else 0,
                    e.get("note"),
                    now,
                ),
            )
            inserted += 1
    return inserted


def main() -> int:
    migrations_dir = BACKEND_ROOT / "migrations"
    sql_files = sorted(migrations_dir.glob("*.sql"))
    if not sql_files:
        print(f"ERROR: 未找到任何 migration 文件:{migrations_dir}", file=sys.stderr)
        return 1

    print("=" * 60)
    print(f"DB:         {settings.db_abs_path}")
    print(f"Migrations: {len(sql_files)} 个文件")
    print("=" * 60)
    print()

    conn = get_connection()
    try:
        # 2.C+ polish:per-file 容忍 "duplicate column"(SQLite ALTER ADD COLUMN 非幂等)
        # CREATE TABLE / INDEX IF NOT EXISTS 本身幂等;只有 ALTER 需要兜底
        import sqlite3 as _sqlite3
        for sql_file in sql_files:
            sql = sql_file.read_text(encoding="utf-8")
            try:
                with transaction(conn) as tx:
                    tx.executescript(sql)
                print(f"  ✓ {sql_file.name}")
            except _sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "duplicate column name" in msg:
                    print(f"  ⊘ {sql_file.name}(列已存在,跳过)")
                else:
                    raise

        # Sprint 2.A:灌红旗词典 seed(幂等,新加的词才插)
        print()
        inserted = _seed_red_flag_dictionary(conn)
        if inserted > 0:
            print(f"  ✓ red_flag_dictionary 新增 {inserted} 条 seed 词")
        else:
            print(f"  · red_flag_dictionary 无新增(已是最新或运营手动维护中)")

        # 验证:列出所有表
        cur = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
        tables = [r["name"] for r in cur.fetchall()]
        print()
        print(f"已创建 {len(tables)} 张业务表:")
        for t in tables:
            print(f"  · {t}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
