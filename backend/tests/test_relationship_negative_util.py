"""Patch E.1 — relationship_negative_util 派生明示陌生对测试.

治"角色关系闪现"创作质量瑕疵.
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from app.services.relationship_negative_util import (
    UnfamiliarPair,
    build_unfamiliar_pairs_block,
    derive_unfamiliar_pairs,
)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE characters (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            name TEXT NOT NULL,
            is_protagonist INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE relationships (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            type TEXT NOT NULL DEFAULT '朋友',
            description TEXT DEFAULT '',
            color TEXT,
            strength TEXT DEFAULT 'moderate',
            current_phase_id TEXT,
            created_at TEXT
        )
    """)
    try:
        yield conn
    finally:
        conn.close()


def _add_char(conn, project_id, name, is_protagonist=False):
    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO characters (id, project_id, name, is_protagonist) VALUES (?, ?, ?, ?)",
        (cid, project_id, name, 1 if is_protagonist else 0),
    )
    return cid


def _add_rel(conn, project_id, source_id, target_id, rel_type="朋友"):
    conn.execute(
        "INSERT INTO relationships (id, project_id, source_id, target_id, type, created_at) "
        "VALUES (?, ?, ?, ?, ?, '2026-06-02')",
        (str(uuid.uuid4()), project_id, source_id, target_id, rel_type),
    )


class TestDeriveUnfamiliarPairs:
    def test_no_characters_returns_empty(self, conn):
        assert derive_unfamiliar_pairs(conn, "proj1") == []

    def test_single_character_returns_empty(self, conn):
        _add_char(conn, "proj1", "A")
        assert derive_unfamiliar_pairs(conn, "proj1") == []

    def test_two_chars_no_rel_returns_one_pair(self, conn):
        a = _add_char(conn, "proj1", "甲")
        b = _add_char(conn, "proj1", "乙")
        pairs = derive_unfamiliar_pairs(conn, "proj1", exclude_protagonist=False)
        assert len(pairs) == 1
        # 验证名字对(id 顺序不固定)
        names = {pairs[0].a_name, pairs[0].b_name}
        assert names == {"甲", "乙"}

    def test_two_chars_with_rel_returns_empty(self, conn):
        a = _add_char(conn, "proj1", "甲")
        b = _add_char(conn, "proj1", "乙")
        _add_rel(conn, "proj1", a, b, "朋友")
        pairs = derive_unfamiliar_pairs(conn, "proj1", exclude_protagonist=False)
        assert pairs == []

    def test_rel_direction_normalized(self, conn):
        """A→B 和 B→A 都算 已声明."""
        a = _add_char(conn, "proj1", "甲")
        b = _add_char(conn, "proj1", "乙")
        _add_rel(conn, "proj1", b, a, "朋友")  # 反向
        pairs = derive_unfamiliar_pairs(conn, "proj1", exclude_protagonist=False)
        assert pairs == []

    def test_protagonist_excluded_by_default(self, conn):
        """男主 × 各女主 自动跳过(男主认识每个女主已知)."""
        p = _add_char(conn, "proj1", "李爽", is_protagonist=True)
        f1 = _add_char(conn, "proj1", "陈绮")
        f2 = _add_char(conn, "proj1", "刘欣悦")
        pairs = derive_unfamiliar_pairs(conn, "proj1")
        # 男主排除后,只剩 陈绮 × 刘欣悦 一对
        assert len(pairs) == 1
        names = {pairs[0].a_name, pairs[0].b_name}
        assert names == {"陈绮", "刘欣悦"}

    def test_protagonist_included_when_flag_off(self, conn):
        p = _add_char(conn, "proj1", "李爽", is_protagonist=True)
        f1 = _add_char(conn, "proj1", "陈绮")
        pairs = derive_unfamiliar_pairs(conn, "proj1", exclude_protagonist=False)
        # 李爽 × 陈绮 此时也会被列出
        assert len(pairs) == 1

    def test_realistic_scenario_lishuang(self, conn):
        """模拟《网恋风云》真实场景:男主 × 4 女主,女主之间都互不相识."""
        p = _add_char(conn, "proj1", "李爽", is_protagonist=True)
        a = _add_char(conn, "proj1", "陈绮")
        b = _add_char(conn, "proj1", "刘欣悦")
        c = _add_char(conn, "proj1", "梁淼")
        d = _add_char(conn, "proj1", "张静怡")
        # 男主和每个女主都有关系(户外关系网)
        _add_rel(conn, "proj1", p, a, "现女友")
        _add_rel(conn, "proj1", p, b, "游戏搭子")
        _add_rel(conn, "proj1", p, c, "初恋")
        _add_rel(conn, "proj1", p, d, "前女友")
        # 女主之间 - 全部没有关系声明(=陌生)
        pairs = derive_unfamiliar_pairs(conn, "proj1")
        # C(4,2) = 6 对(陈×刘 / 陈×梁 / 陈×张 / 刘×梁 / 刘×张 / 梁×张)
        assert len(pairs) == 6
        names_set = {(p.a_name, p.b_name) for p in pairs}
        # 验证刘欣悦 ⟷ 张静怡 在陌生对里(这是用户报告的"静怡姐"bug 源头)
        expected_pair = ("刘欣悦", "张静怡") if "刘欣悦" < "张静怡" else ("张静怡", "刘欣悦")
        assert expected_pair in names_set or ("刘欣悦", "张静怡") in names_set or ("张静怡", "刘欣悦") in names_set

    def test_max_pairs_truncation(self, conn):
        """20+ 角色项目会爆 200+ 对,max_pairs 应截断."""
        for i in range(8):
            _add_char(conn, "proj1", f"角色{i}")
        # C(8,2) = 28 对
        pairs = derive_unfamiliar_pairs(
            conn, "proj1",
            exclude_protagonist=False,
            max_pairs=10,
        )
        assert len(pairs) == 10


class TestBuildUnfamiliarPairsBlock:
    def test_empty_returns_empty_string(self, conn):
        assert build_unfamiliar_pairs_block(conn, "proj1") == ""

    def test_block_contains_pairs_and_rules(self, conn):
        p = _add_char(conn, "proj1", "李爽", is_protagonist=True)
        a = _add_char(conn, "proj1", "陈绮")
        b = _add_char(conn, "proj1", "刘欣悦")
        _add_rel(conn, "proj1", p, a, "现女友")
        _add_rel(conn, "proj1", p, b, "游戏搭子")
        block = build_unfamiliar_pairs_block(conn, "proj1")
        # 应该有标题
        assert "明示陌生关系" in block
        # 应该列出陌生对
        assert "陈绮" in block
        assert "刘欣悦" in block
        # 应该含铁律
        assert "初次见面" in block
        assert "禁止" in block
