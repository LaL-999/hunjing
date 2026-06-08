"""Bug 回归(2026-05-22):agent_profile_enricher._gather_source_context
错把 ParseResult dataclass append 到 list,然后 "\n\n".join() 抛
"TypeError: sequence item 0: expected str instance, ParseResult found"。

修复:取 result.text 后 append;并把 storage_path 拼成绝对 Path。
"""
from __future__ import annotations

import sqlite3
from unittest.mock import patch


def _make_db_with_uploads(rows: list[tuple]) -> sqlite3.Connection:
    """构造一个最小的 db + uploads 表 + 给定行。"""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE uploads (storage_path TEXT, mime_type TEXT, project_id TEXT, "
        "state TEXT, uploaded_at TEXT)"
    )
    for r in rows:
        conn.execute("INSERT INTO uploads VALUES (?, ?, ?, ?, ?)", r)
    return conn


def test_gather_source_context_no_typeerror_after_fix():
    """老 bug 重现 + 修复验证:含 ready upload 时,_gather 不再抛 TypeError。"""
    from app.services.agent_profile_enricher import _gather_source_context
    from app.services.file_parser import ParseResult

    conn = _make_db_with_uploads([
        ("sample.txt", "text/plain", "proj-1", "ready", "2026-05-22T00:00:00Z"),
    ])

    # mock parse_file 返回成功的 ParseResult(test 重点:result 不是 str,
    # 修复前会被错误 append 然后 join 抛 TypeError;修复后取 result.text)
    with patch(
        "app.services.file_parser.parse_file",
        return_value=ParseResult.ok("邻床男子的太太是个温和的人。她每天来探视丈夫。"),
    ):
        result = _gather_source_context(conn, "proj-1", "邻床男子的太太")

    # 修复前:抛 TypeError;修复后:返回 list[str](该名字在 mock 文本里 → 至少 1 个 sample)
    assert isinstance(result, list)
    assert len(result) >= 1
    assert all(isinstance(s, str) for s in result)


def test_gather_source_context_returns_empty_when_no_ready_upload():
    """无 ready upload → 返回 []。"""
    from app.services.agent_profile_enricher import _gather_source_context

    conn = _make_db_with_uploads([])
    result = _gather_source_context(conn, "proj-x", "任何角色")
    assert result == []


def test_gather_source_context_skips_failed_parse():
    """parse_file 返回失败的 ParseResult → 跳过该 upload,不抛(继续看下一个)。"""
    from app.services.agent_profile_enricher import _gather_source_context
    from app.services.file_parser import ParseResult

    conn = _make_db_with_uploads([
        ("bad.txt", "text/plain", "proj-1", "ready", "2026-05-22T00:00:00Z"),
    ])

    with patch(
        "app.services.file_parser.parse_file",
        return_value=ParseResult.fail("mock 解析失败"),
    ):
        result = _gather_source_context(conn, "proj-1", "某角色")
    # 全部 upload 解析失败 → all_text_parts 为空 → 返回 []
    assert result == []


def test_gather_source_context_skips_when_parse_raises():
    """parse_file 抛异常 → 该 upload 跳过,不抛到外层。"""
    from app.services.agent_profile_enricher import _gather_source_context

    conn = _make_db_with_uploads([
        ("crash.txt", "text/plain", "proj-1", "ready", "2026-05-22T00:00:00Z"),
    ])

    with patch(
        "app.services.file_parser.parse_file",
        side_effect=RuntimeError("mock 磁盘 IO 失败"),
    ):
        result = _gather_source_context(conn, "proj-1", "某角色")
    assert result == []
