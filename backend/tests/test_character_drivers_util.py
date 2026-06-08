"""SP-2 角色驱动力注入测试(2026-05-28)."""
from __future__ import annotations

import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import execute, get_connection
from app.services.character_drivers_util import build_character_drivers_block


def _setup_project_with_chars(
    user_id: str,
    chars_data: list[dict],
) -> tuple[str, list[str]]:
    """造一个 project + N 个角色,返回 (project_id, character_ids).

    chars_data 每项可有:
      name(必),surface_goal/deep_need/fatal_blind_spot/arc_from_to,
      secrets(list[dict {description, hidden_from?}])
    """
    pid = str(uuid.uuid4())
    now = "2026-05-28T00:00:00+00:00"
    conn = get_connection()
    char_ids: list[str] = []
    try:
        execute(
            conn,
            "INSERT INTO projects "
            "(id, user_id, name, type, custom_type_name, tags, mode, "
            " created_at, updated_at) "
            "VALUES (?, ?, '测试', 'novel', NULL, '[]', 'middle', ?, ?)",
            (pid, user_id, now, now),
        )
        for c in chars_data:
            cid = str(uuid.uuid4())
            char_ids.append(cid)
            secrets_json = (
                json.dumps(c["secrets"], ensure_ascii=False)
                if c.get("secrets")
                else None
            )
            execute(
                conn,
                "INSERT INTO characters "
                "(id, project_id, name, identity, personality, quotes, no_go_list, "
                " position_x, position_y, position_z, created_at, updated_at, "
                " surface_goal, deep_need, fatal_blind_spot, arc_from_to, secret_json) "
                "VALUES (?, ?, ?, '', '', '[]', '[]', 0, 0, 0, ?, ?, "
                "        ?, ?, ?, ?, ?)",
                (
                    cid, pid, c["name"], now, now,
                    c.get("surface_goal"), c.get("deep_need"),
                    c.get("fatal_blind_spot"), c.get("arc_from_to"),
                    secrets_json,
                ),
            )
        conn.commit()
        return pid, char_ids
    finally:
        conn.close()


def test_empty_when_all_chars_have_no_drivers(client: TestClient, make_user):
    """5 字段全空 → 返空串."""
    u = make_user("drv_empty")
    pid, _ = _setup_project_with_chars(u["user_id"], [
        {"name": "渡边"},
        {"name": "绿子"},
    ])
    conn = get_connection()
    try:
        block = build_character_drivers_block(conn, pid, ["渡边", "绿子"])
    finally:
        conn.close()
    assert block == ""


def test_partial_fields_one_char(client: TestClient, make_user):
    """单角色仅填部分字段 → 块只显示填了的字段."""
    u = make_user("drv_partial")
    pid, _ = _setup_project_with_chars(u["user_id"], [
        {
            "name": "渡边",
            "surface_goal": "跟绿子相处,但放不下直子",
            "arc_from_to": "从沉溺自责 → 接受死亡 → 选择活下去",
        },
    ])
    conn = get_connection()
    try:
        block = build_character_drivers_block(conn, pid, ["渡边"])
    finally:
        conn.close()
    assert "· 渡边:" in block
    assert "表层想要:跟绿子相处" in block
    assert "预期弧光:从沉溺自责" in block
    # 未填的字段不出现(用"4 个空格 + 字段名:"精确匹配,避开铁律段提及)
    assert "    深层需要:" not in block
    assert "    致命盲区:" not in block
    assert "    秘密(" not in block  # 秘密字段头都不出现
    assert "铁律" in block


def test_secret_with_hidden_from(client: TestClient, make_user):
    """秘密带 hidden_from → 文案"对 X 瞒着""."""
    u = make_user("drv_secret_hidden")
    pid, _ = _setup_project_with_chars(u["user_id"], [
        {
            "name": "渡边",
            "secrets": [
                {
                    "description": "曾在直子病房答应永远陪她",
                    "hidden_from": ["绿子"],
                }
            ],
        },
    ])
    conn = get_connection()
    try:
        block = build_character_drivers_block(conn, pid, ["渡边"])
    finally:
        conn.close()
    assert "秘密(对 绿子 瞒着):曾在直子病房答应永远陪她" in block


def test_secret_without_hidden_from(client: TestClient, make_user):
    """秘密不带 hidden_from → "对所有人瞒着""."""
    u = make_user("drv_secret_all")
    pid, _ = _setup_project_with_chars(u["user_id"], [
        {
            "name": "渡边",
            "secrets": [{"description": "私藏的日记"}],
        },
    ])
    conn = get_connection()
    try:
        block = build_character_drivers_block(conn, pid, ["渡边"])
    finally:
        conn.close()
    assert "秘密(对所有人瞒着):私藏的日记" in block


def test_multi_chars_mixed(client: TestClient, make_user):
    """多角色,渡边有驱动,绿子全空 → 只显示渡边."""
    u = make_user("drv_mixed")
    pid, _ = _setup_project_with_chars(u["user_id"], [
        {"name": "渡边", "deep_need": "放下自责"},
        {"name": "绿子"},  # 全空
    ])
    conn = get_connection()
    try:
        block = build_character_drivers_block(conn, pid, ["渡边", "绿子"])
    finally:
        conn.close()
    assert "· 渡边:" in block
    assert "深层需要:放下自责" in block
    assert "· 绿子:" not in block  # 全空,不显示


def test_empty_character_names_returns_empty(client: TestClient):
    """character_names 为 [] → 直接返空,不查 DB."""
    conn = get_connection()
    try:
        block = build_character_drivers_block(conn, "any_pid", [])
    finally:
        conn.close()
    assert block == ""
