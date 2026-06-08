"""SP-3 知识边界系统测试(2026-05-28)."""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import execute, get_connection
from app.services.character_knowledge_service import (
    get_facts_known_by,
    get_facts_unknown_by,
    list_project_facts,
    mark_known,
    register_fact,
    unmark_known,
)
from app.services.character_knowledge_util import build_knowledge_block


def _setup_project_with_chars(
    user_id: str, char_names: list[str],
) -> tuple[str, dict[str, str]]:
    """造一个 project + 多个角色,返回 (project_id, {name: char_id})."""
    pid = str(uuid.uuid4())
    now = "2026-05-28T00:00:00+00:00"
    name_to_id: dict[str, str] = {}
    conn = get_connection()
    try:
        execute(
            conn,
            "INSERT INTO projects "
            "(id, user_id, name, type, custom_type_name, tags, mode, "
            " created_at, updated_at) "
            "VALUES (?, ?, '测试', 'novel', NULL, '[]', 'middle', ?, ?)",
            (pid, user_id, now, now),
        )
        for cn in char_names:
            cid = str(uuid.uuid4())
            name_to_id[cn] = cid
            execute(
                conn,
                "INSERT INTO characters "
                "(id, project_id, name, identity, personality, quotes, no_go_list, "
                " position_x, position_y, position_z, created_at, updated_at) "
                "VALUES (?, ?, ?, '', '', '[]', '[]', 0, 0, 0, ?, ?)",
                (cid, pid, cn, now, now),
            )
        conn.commit()
        return pid, name_to_id
    finally:
        conn.close()


def test_register_fact_and_list(client: TestClient, make_user):
    """录入事实 + 列出项目下所有事实."""
    u = make_user("k_register")
    pid, _ = _setup_project_with_chars(u["user_id"], ["渡边"])
    conn = get_connection()
    try:
        fid1 = register_fact(conn, project_id=pid, description="直子在 1969 年自杀")
        fid2 = register_fact(
            conn, project_id=pid, description="渡边曾在直子病房答应永远陪她",
            is_sensitive=True, first_revealed_scene=3,
        )
        facts = list_project_facts(conn, pid)
    finally:
        conn.close()
    assert len(facts) == 2
    ids = {f["id"] for f in facts}
    assert {fid1, fid2} == ids


def test_mark_known_and_get_known(client: TestClient, make_user):
    """标记某角色知道某事实 + 查回来."""
    u = make_user("k_known")
    pid, chars = _setup_project_with_chars(u["user_id"], ["渡边"])
    conn = get_connection()
    try:
        f1 = register_fact(conn, project_id=pid, description="直子已死")
        f2 = register_fact(conn, project_id=pid, description="玲子还活着")
        mark_known(
            conn, character_id=chars["渡边"], fact_id=f1, known_since_scene=5,
        )
        known = get_facts_known_by(conn, chars["渡边"])
    finally:
        conn.close()
    assert len(known) == 1
    assert known[0]["description"] == "直子已死"
    assert known[0]["known_since_scene"] == 5
    assert known[0]["confidence"] == "confirmed"


def test_unknown_excludes_known(client: TestClient, make_user):
    """get_facts_unknown_by 返"项目级全部 - 已知"."""
    u = make_user("k_unknown")
    pid, chars = _setup_project_with_chars(u["user_id"], ["绿子"])
    conn = get_connection()
    try:
        f1 = register_fact(conn, project_id=pid, description="直子已死")
        f2 = register_fact(conn, project_id=pid, description="渡边总在魂不守舍")
        f3 = register_fact(conn, project_id=pid, description="阿美寮是疗养院")
        # 绿子只知道 f2
        mark_known(conn, character_id=chars["绿子"], fact_id=f2)
        unknown = get_facts_unknown_by(
            conn, character_id=chars["绿子"], project_id=pid,
        )
    finally:
        conn.close()
    assert len(unknown) == 2
    descs = sorted([u["description"] for u in unknown])
    assert descs == sorted(["直子已死", "阿美寮是疗养院"])


def test_up_to_scene_filter(client: TestClient, make_user):
    """up_to_scene 过滤:第 5 幕末时,since_scene=7 的事实视为"还不知道"."""
    u = make_user("k_scene_filter")
    pid, chars = _setup_project_with_chars(u["user_id"], ["渡边"])
    conn = get_connection()
    try:
        f1 = register_fact(conn, project_id=pid, description="自己的过去")
        f2 = register_fact(conn, project_id=pid, description="第 7 幕才知道的事")
        # f1 始终知道(since_scene=NULL),f2 第 7 幕才知道
        mark_known(conn, character_id=chars["渡边"], fact_id=f1)
        mark_known(
            conn, character_id=chars["渡边"], fact_id=f2, known_since_scene=7,
        )
        # 第 5 幕末:f2 还不知道
        known_at_5 = get_facts_known_by(conn, chars["渡边"], up_to_scene=5)
        unknown_at_5 = get_facts_unknown_by(
            conn, character_id=chars["渡边"], project_id=pid, up_to_scene=5,
        )
    finally:
        conn.close()
    assert len(known_at_5) == 1
    assert known_at_5[0]["description"] == "自己的过去"
    assert len(unknown_at_5) == 1
    assert unknown_at_5[0]["description"] == "第 7 幕才知道的事"


def test_unmark_known(client: TestClient, make_user):
    """撤销标记."""
    u = make_user("k_unmark")
    pid, chars = _setup_project_with_chars(u["user_id"], ["渡边"])
    conn = get_connection()
    try:
        f1 = register_fact(conn, project_id=pid, description="测试事实")
        mark_known(conn, character_id=chars["渡边"], fact_id=f1)
        before = get_facts_known_by(conn, chars["渡边"])
        ok = unmark_known(conn, character_id=chars["渡边"], fact_id=f1)
        after = get_facts_known_by(conn, chars["渡边"])
    finally:
        conn.close()
    assert len(before) == 1
    assert ok is True
    assert len(after) == 0


def test_block_with_known_and_unknown(client: TestClient, make_user):
    """build_knowledge_block:渡边知道 A、不知道 B → 块含两行."""
    u = make_user("k_block")
    pid, chars = _setup_project_with_chars(u["user_id"], ["渡边", "绿子"])
    conn = get_connection()
    try:
        f1 = register_fact(conn, project_id=pid, description="直子已死")
        f2 = register_fact(conn, project_id=pid, description="玲子还活着")
        # 渡边知道 f1
        mark_known(conn, character_id=chars["渡边"], fact_id=f1)
        block = build_knowledge_block(conn, pid, ["渡边"])
    finally:
        conn.close()
    assert "· 渡边:" in block
    assert "已知:直子已死" in block
    assert "不知:玲子还活着" in block
    assert "信息不对称" in block  # 铁律段


def test_block_empty_when_no_facts(client: TestClient, make_user):
    """项目无任何事实 → 返空(不污染 prompt)."""
    u = make_user("k_block_empty")
    pid, _ = _setup_project_with_chars(u["user_id"], ["渡边"])
    conn = get_connection()
    try:
        block = build_knowledge_block(conn, pid, ["渡边"])
    finally:
        conn.close()
    assert block == ""


def test_block_suspected_confidence(client: TestClient, make_user):
    """confidence=suspected → 标"(怀疑)"."""
    u = make_user("k_susp")
    pid, chars = _setup_project_with_chars(u["user_id"], ["渡边"])
    conn = get_connection()
    try:
        f1 = register_fact(conn, project_id=pid, description="直子可能自杀了")
        mark_known(
            conn, character_id=chars["渡边"], fact_id=f1,
            confidence="suspected",
        )
        block = build_knowledge_block(conn, pid, ["渡边"])
    finally:
        conn.close()
    assert "直子可能自杀了(怀疑)" in block
