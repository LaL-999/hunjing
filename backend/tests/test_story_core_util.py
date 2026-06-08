"""SP-1 故事内核注入测试(2026-05-28)."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import execute, get_connection


def _create_project_with_core(
    user_id: str,
    *,
    cdq: str | None = None,
    theme: str | None = None,
    ending: str | None = None,
) -> str:
    """用已有 user_id 直接 SQL 建 project + 设故事内核三字段."""
    pid = str(uuid.uuid4())
    now = "2026-05-28T00:00:00+00:00"
    conn = get_connection()
    try:
        execute(
            conn,
            "INSERT INTO projects "
            "(id, user_id, name, type, custom_type_name, tags, mode, created_at, updated_at, "
            " core_dramatic_question, theme, ending_direction) "
            "VALUES (?, ?, '测试项目', 'novel', NULL, '[]', 'initial', ?, ?, ?, ?, ?)",
            (pid, user_id, now, now, cdq, theme, ending),
        )
        conn.commit()
        return pid
    finally:
        conn.close()


def test_story_core_block_empty_when_all_none(client: TestClient, make_user):
    """三字段全空 → 返空串(无副作用)."""
    from app.services.story_core_util import build_story_core_block

    u = make_user("sc_empty")
    pid = _create_project_with_core(u["user_id"])
    conn = get_connection()
    try:
        block = build_story_core_block(conn, pid)
    finally:
        conn.close()
    assert block == ""


def test_story_core_block_partial_only_cdq(client: TestClient, make_user):
    """仅 core_dramatic_question 填了 → 块含它,无其他两项."""
    from app.services.story_core_util import build_story_core_block

    u = make_user("sc_partial")
    pid = _create_project_with_core(
        u["user_id"], cdq="线上交付的真心扛不扛得住线下的真相",
    )
    conn = get_connection()
    try:
        block = build_story_core_block(conn, pid)
    finally:
        conn.close()
    assert "核心戏剧问题:线上交付的真心扛不扛得住线下的真相" in block
    assert "主题:" not in block
    assert "终点方向:" not in block
    assert "铁律" in block
    assert "不许提前泄气" in block


def test_story_core_block_full_with_progress(client: TestClient, make_user):
    """三字段全填 + 带进度参数 → 块含进度提示."""
    from app.services.story_core_util import build_story_core_block

    u = make_user("sc_full")
    pid = _create_project_with_core(
        u["user_id"],
        cdq="为爱救赎之路能不能熬过自我毁灭",
        theme="孤独中相互取暖",
        ending="哀而不伤,留一抹希望",
    )
    conn = get_connection()
    try:
        block = build_story_core_block(
            conn, pid,
            current_scene_index=10,
            total_scenes_planned=28,
        )
    finally:
        conn.close()
    assert "核心戏剧问题:为爱救赎之路能不能熬过自我毁灭" in block
    assert "主题:孤独中相互取暖" in block
    assert "终点方向:哀而不伤,留一抹希望" in block
    # 进度:第 11 / 28 幕(0-based 10 → 显示 11)
    assert "第 11 / 28 幕" in block
    assert "距终点还有 17 幕" in block


def test_story_core_block_nonexistent_project(client: TestClient):
    """project_id 不存在 → 返空串(防御性,不抛错)."""
    from app.services.story_core_util import build_story_core_block

    conn = get_connection()
    try:
        block = build_story_core_block(conn, "fake-uuid-not-exists")
    finally:
        conn.close()
    assert block == ""


def test_story_core_progress_omitted_when_no_total(client: TestClient, make_user):
    """有 scene_index 但无 total_scenes_planned → 不显示进度."""
    from app.services.story_core_util import build_story_core_block

    u = make_user("sc_no_total")
    pid = _create_project_with_core(u["user_id"], cdq="脊柱")
    conn = get_connection()
    try:
        block = build_story_core_block(conn, pid, current_scene_index=5)
    finally:
        conn.close()
    assert "本幕进度" not in block
