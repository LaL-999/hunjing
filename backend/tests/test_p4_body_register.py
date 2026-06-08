"""P4(2026-05-27)— 身体描写尺度维度单元测试.

测 author_compass_util._build_internal_section 把"身体描写尺度" 4 字段
正确翻译成中文 prompt 行,以及 build_author_compass_block 出口铁律 5。

不依赖 DB / API,直接测纯函数。
"""
from __future__ import annotations

import json
import sqlite3
import uuid

import pytest


# ============================================================
# 1. _build_internal_section 翻译身体描写尺度
# ============================================================

def test_body_register_translates_all_four_fields():
    """4 字段都有 → 输出 1 行 / pill 形式 / 中文友好描述."""
    from app.services.author_compass_util import _build_internal_section

    internal = {
        "身体描写尺度": {
            "频率": "frequent",
            "直白度": "sensual-implicit",
            "态度": "matter-of-fact",
            "功能": "character-development",
            "评注": "隐晦但频繁(类似《挪威的森林》)",
        },
    }
    lines = _build_internal_section(internal)
    # 至少 1 行身体描写尺度
    body_lines = [l for l in lines if "身体描写尺度" in l]
    assert len(body_lines) == 1

    body_line = body_lines[0]
    # 检查中文翻译
    assert "频繁" in body_line
    assert "感官隐晦" in body_line
    assert "平淡如实" in body_line
    assert "推进人物刻画" in body_line
    assert "类似《挪威的森林》" in body_line


def test_body_register_handles_partial_fields():
    """只有 2 字段 → 不报错,输出含 — 占位."""
    from app.services.author_compass_util import _build_internal_section

    internal = {
        "身体描写尺度": {
            "频率": "none",
            "直白度": "absent",
            # 态度、功能、评注 缺
        },
    }
    lines = _build_internal_section(internal)
    body_lines = [l for l in lines if "身体描写尺度" in l]
    assert len(body_lines) == 1
    assert "完全不写" in body_lines[0]
    assert "完全不涉" in body_lines[0]


def test_body_register_absent_when_no_field():
    """无身体描写尺度 → 没有相关行(不影响其他维度)."""
    from app.services.author_compass_util import _build_internal_section

    internal = {
        "句长": {"短句占比": 0.6, "中句占比": 0.3, "长句占比": 0.1},
        # 无身体描写尺度
    }
    lines = _build_internal_section(internal)
    body_lines = [l for l in lines if "身体描写尺度" in l]
    assert len(body_lines) == 0
    # 但句长仍输出
    sj_lines = [l for l in lines if "句长分布" in l]
    assert len(sj_lines) == 1


def test_body_register_unknown_enum_passes_through():
    """LLM 输出非枚举值 → 直接 pass through 不崩."""
    from app.services.author_compass_util import _build_internal_section

    internal = {
        "身体描写尺度": {
            "频率": "very-rare",     # 非枚举
            "直白度": "weird-value",  # 非枚举
            "态度": "matter-of-fact",
            "功能": "atmospheric",
        },
    }
    # 不应抛异常
    lines = _build_internal_section(internal)
    body_lines = [l for l in lines if "身体描写尺度" in l]
    assert len(body_lines) == 1


# ============================================================
# 2. build_author_compass_block 铁律 5 注入
# ============================================================

def test_build_block_includes_body_register_law():
    """有 internal_metrics 含身体描写尺度 → 输出铁律 5."""
    from app.services.author_compass_util import build_author_compass_block

    # 用 in-memory DB 模拟 author_compass 数据
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE author_compass (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL UNIQUE,
        author_name TEXT, work_title TEXT,
        external_profile_json TEXT, external_status TEXT NOT NULL DEFAULT 'pending',
        external_error TEXT, external_at TEXT,
        internal_metrics_json TEXT, internal_status TEXT NOT NULL DEFAULT 'pending',
        internal_error TEXT, internal_at TEXT,
        final_compass_json TEXT, user_locked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    internal = {
        "句长": {"短句占比": 0.5, "中句占比": 0.3, "长句占比": 0.2},
        "身体描写尺度": {
            "频率": "rare",
            "直白度": "sensual-implicit",
            "态度": "atmospheric",
            "功能": "atmospheric",
            "评注": "极少正面写,偶以触觉对照",
        },
    }
    conn.execute(
        "INSERT INTO author_compass VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, "p1", "川端康成", "雪国",
         None, "pending", None, None,
         json.dumps(internal, ensure_ascii=False), "done", None, "2026-05-27",
         None, 0, "2026-05-27", "2026-05-27"),
    )
    conn.commit()

    block = build_author_compass_block(conn, "p1")
    # 铁律 5 关键词
    assert "身体描写尺度对齐" in block
    assert "灵魂错位" in block
    # 数据已翻译
    assert "极少" in block
    assert "感官隐晦" in block


def test_build_block_without_body_register_still_works():
    """无身体描写尺度但有其他维度 → 铁律 5 仍输出(给 LLM 信号但本作家此维度无数据)."""
    from app.services.author_compass_util import build_author_compass_block

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE author_compass (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL UNIQUE,
        author_name TEXT, work_title TEXT,
        external_profile_json TEXT, external_status TEXT NOT NULL DEFAULT 'pending',
        external_error TEXT, external_at TEXT,
        internal_metrics_json TEXT, internal_status TEXT NOT NULL DEFAULT 'pending',
        internal_error TEXT, internal_at TEXT,
        final_compass_json TEXT, user_locked INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    internal = {
        "句长": {"短句占比": 0.5, "中句占比": 0.3, "长句占比": 0.2},
        # 不含身体描写尺度
    }
    conn.execute(
        "INSERT INTO author_compass VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (uuid.uuid4().hex, "p1", "X", "Y",
         None, "pending", None, None,
         json.dumps(internal, ensure_ascii=False), "done", None, "2026-05-27",
         None, 0, "2026-05-27", "2026-05-27"),
    )
    conn.commit()

    block = build_author_compass_block(conn, "p1")
    # 铁律 5 仍在(LLM 看到铁律即便没数据也知道"若有该字段则按此处理")
    assert "身体描写尺度对齐" in block
    # 但数据行不出现
    assert "身体描写尺度:" not in block


# ============================================================
# 3. service keys 接受身体描写尺度作为唯一字段
# ============================================================

def test_service_keys_include_body_register():
    """analyze_internal_for_project 的 keys 校验应含「身体描写尺度」 — 即便 LLM 只返此一字段也合规."""
    # 校验 service 源码包含新字段(无 LLM call 的轻量测试)
    from pathlib import Path
    src = (Path(__file__).parents[1] / "app" / "services"
           / "author_compass_service.py").read_text(encoding="utf-8")
    # 关键 tuple 必含「身体描写尺度」
    assert '"身体描写尺度"' in src, (
        "service.py 的 keys tuple 未含「身体描写尺度」 — P4.1 改动可能丢失"
    )
    # 错误消息也应包含(用户可读的错误反馈)
    assert "身体描写尺度" in src
