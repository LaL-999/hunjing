"""阶段 5.8 — huimeng_bridge 桥接层单元测试。

每个 get_*_block 函数测 3 路径:
  1. novel 不存在 → 返 "" / []
  2. novel 存在但未 link → 返 "" / []
  3. novel 已 link 到有数据的 project → 返非空块

加测 link_novel_to_project 双向校验(novel 不属用户 / project 不属用户)。

测试自包含 — 直接走父平台 fixture(reset_test_db 重建空库 + migration 跑齐 sp_*
和 086 linked_project_id),自己造 user / project / character / sp_novels 测数据。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.db import get_connection
from app.screenplay.services import huimeng_bridge


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _create_user(conn, user_id: str = None) -> str:
    """造一个 user,返 user_id。"""
    uid = user_id or str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, email, plan, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, f"{uid[:8]}@test.com", "free", _now(), _now()),
    )
    return uid


def _create_project(conn, user_id: str, project_id: str = None) -> str:
    """造一个 project,返 project_id。"""
    pid = project_id or str(uuid.uuid4())
    conn.execute(
        "INSERT INTO projects (id, user_id, name, type, mode, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pid, user_id, "测试项目", "novel", "initial", _now(), _now()),
    )
    return pid


def _create_character(
    conn, project_id: str, name: str, *,
    surface_goal: str = None, deep_need: str = None,
    secret_json: str = None, identity: str = "",
) -> str:
    """造一个 character,返 character_id。"""
    cid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO characters (id, project_id, name, identity, personality, "
        "quotes, no_go_list, created_at, updated_at, "
        "surface_goal, deep_need, secret_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, project_id, name, identity, "性格", "[]", "[]",
         _now(), _now(), surface_goal, deep_need, secret_json),
    )
    return cid


def _create_sp_novel(
    conn, user_id: str, novel_id: str = None,
    linked_project_id: str | None = None,
) -> str:
    """造一个 sp_novels 行(可选 link 到 project)。"""
    nid = novel_id or uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sp_novels (id, user_id, title, source_format, "
        "source_filename, total_chars, total_chapters, uploaded_at, "
        "linked_project_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (nid, user_id, "测试小说", "txt", "test.txt", 0, 0, _now(),
         linked_project_id),
    )
    return nid


# ============================================================
# get_character_drivers_block
# ============================================================

class TestCharacterDriversBlock:
    def test_novel_not_exist_returns_empty(self):
        user_id = "ghost_user"
        conn = get_connection()
        try:
            out = huimeng_bridge.get_character_drivers_block(
                conn, user_id=user_id, novel_id="nonexistent",
                character_names=["X"],
            )
        finally:
            conn.close()
        assert out == ""

    def test_unlinked_novel_returns_empty(self):
        conn = get_connection()
        try:
            user_id = _create_user(conn)
            novel_id = _create_sp_novel(conn, user_id, linked_project_id=None)
            conn.commit()
            out = huimeng_bridge.get_character_drivers_block(
                conn, user_id=user_id, novel_id=novel_id,
                character_names=["X"],
            )
        finally:
            conn.close()
        assert out == ""

    def test_linked_novel_with_drivers_returns_block(self):
        conn = get_connection()
        try:
            user_id = _create_user(conn)
            project_id = _create_project(conn, user_id)
            _create_character(
                conn, project_id, "渡边",
                surface_goal="想跟绿子相处但又放不下直子",
                deep_need="承认直子已死、放下自责",
                secret_json='[{"description":"曾经背叛直子","hidden_from":["绿子"]}]',
            )
            novel_id = _create_sp_novel(
                conn, user_id, linked_project_id=project_id,
            )
            conn.commit()
            out = huimeng_bridge.get_character_drivers_block(
                conn, user_id=user_id, novel_id=novel_id,
                character_names=["渡边"],
            )
        finally:
            conn.close()
        # 应该包含 driver 标记
        assert "渡边" in out
        assert "想跟绿子相处" in out
        assert "直子已死" in out
        assert "对 绿子 瞒着" in out  # 秘密格式

    def test_cross_user_access_returns_empty(self):
        """用户 A 拿到用户 B 的 novel_id 不能拉到 driver 资产。"""
        conn = get_connection()
        try:
            user_a = _create_user(conn)
            user_b = _create_user(conn)
            project_b = _create_project(conn, user_b)
            _create_character(conn, project_b, "B 角色", surface_goal="X")
            novel_b = _create_sp_novel(
                conn, user_b, linked_project_id=project_b,
            )
            conn.commit()
            # user_a 拿 user_b 的 novel_id 查
            out = huimeng_bridge.get_character_drivers_block(
                conn, user_id=user_a, novel_id=novel_b,
                character_names=["B 角色"],
            )
        finally:
            conn.close()
        assert out == ""


# ============================================================
# get_relationship_polarity_block
# ============================================================

class TestRelationshipPolarityBlock:
    def test_unlinked_returns_empty(self):
        conn = get_connection()
        try:
            user_id = _create_user(conn)
            novel_id = _create_sp_novel(conn, user_id)
            conn.commit()
            out = huimeng_bridge.get_relationship_polarity_block(
                conn, user_id=user_id, novel_id=novel_id,
                character_names=["A", "B"],
            )
        finally:
            conn.close()
        assert out == ""

    def test_linked_with_polarity_returns_block(self):
        conn = get_connection()
        try:
            user_id = _create_user(conn)
            project_id = _create_project(conn, user_id)
            cid_a = _create_character(conn, project_id, "渡边")
            cid_b = _create_character(conn, project_id, "绿子")
            # 关系:渡边 → 绿子, type=情侣, polarity=positive
            conn.execute(
                "INSERT INTO relationships (id, project_id, source_id, "
                "target_id, type, description, created_at, polarity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), project_id, cid_a, cid_b,
                 "情侣", "复杂感情", _now(), "positive"),
            )
            novel_id = _create_sp_novel(
                conn, user_id, linked_project_id=project_id,
            )
            conn.commit()
            out = huimeng_bridge.get_relationship_polarity_block(
                conn, user_id=user_id, novel_id=novel_id,
                character_names=["渡边", "绿子"],
            )
        finally:
            conn.close()
        assert "渡边" in out
        assert "绿子" in out
        assert "情侣" in out
        assert "正向" in out  # polarity_label

    def test_fewer_than_2_characters_returns_empty(self):
        conn = get_connection()
        try:
            user_id = _create_user(conn)
            novel_id = _create_sp_novel(conn, user_id)
            conn.commit()
            out = huimeng_bridge.get_relationship_polarity_block(
                conn, user_id=user_id, novel_id=novel_id,
                character_names=["仅一个角色"],
            )
        finally:
            conn.close()
        assert out == ""  # 少于 2 角色没关系可谈


# ============================================================
# get_story_facts_block
# ============================================================

class TestStoryFactsBlock:
    def test_unlinked_returns_empty(self):
        conn = get_connection()
        try:
            user_id = _create_user(conn)
            novel_id = _create_sp_novel(conn, user_id)
            conn.commit()
            out = huimeng_bridge.get_story_facts_block(
                conn, user_id=user_id, novel_id=novel_id,
            )
        finally:
            conn.close()
        assert out == ""

    def test_linked_with_facts_returns_block(self):
        conn = get_connection()
        try:
            user_id = _create_user(conn)
            project_id = _create_project(conn, user_id)
            # 加 2 个 facts
            conn.execute(
                "INSERT INTO story_facts (id, project_id, description, "
                "is_sensitive, first_revealed_scene, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), project_id, "林叔叔有间钥匙工坊",
                 0, 3, _now()),
            )
            conn.execute(
                "INSERT INTO story_facts (id, project_id, description, "
                "is_sensitive, first_revealed_scene, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (str(uuid.uuid4()), project_id, "主角曾在伦敦留学",
                 1, None, _now()),  # 敏感事实
            )
            novel_id = _create_sp_novel(
                conn, user_id, linked_project_id=project_id,
            )
            conn.commit()
            out = huimeng_bridge.get_story_facts_block(
                conn, user_id=user_id, novel_id=novel_id,
            )
        finally:
            conn.close()
        assert "钥匙工坊" in out
        assert "伦敦留学" in out
        assert "🔒" in out  # 敏感标记
        # 「钥匙工坊」必须出现在「不许写成」铁律里
        assert "原作锚定" in out


# ============================================================
# get_linked_characters
# ============================================================

class TestLinkedCharacters:
    def test_unlinked_returns_empty(self):
        conn = get_connection()
        try:
            user_id = _create_user(conn)
            novel_id = _create_sp_novel(conn, user_id)
            conn.commit()
            out = huimeng_bridge.get_linked_characters(
                conn, user_id=user_id, novel_id=novel_id,
            )
        finally:
            conn.close()
        assert out == []

    def test_linked_returns_character_list(self):
        conn = get_connection()
        try:
            user_id = _create_user(conn)
            project_id = _create_project(conn, user_id)
            _create_character(conn, project_id, "Alice", identity="侦探")
            _create_character(conn, project_id, "Bob", identity="嫌疑犯")
            novel_id = _create_sp_novel(
                conn, user_id, linked_project_id=project_id,
            )
            conn.commit()
            out = huimeng_bridge.get_linked_characters(
                conn, user_id=user_id, novel_id=novel_id,
            )
        finally:
            conn.close()
        assert len(out) == 2
        names = {c["name"] for c in out}
        assert names == {"Alice", "Bob"}


# ============================================================
# link_novel_to_project
# ============================================================

class TestLinkNovelToProject:
    def test_link_success(self):
        conn = get_connection()
        try:
            user_id = _create_user(conn)
            project_id = _create_project(conn, user_id)
            novel_id = _create_sp_novel(conn, user_id)
            conn.commit()
            ok = huimeng_bridge.link_novel_to_project(
                conn, user_id=user_id, novel_id=novel_id,
                project_id=project_id,
            )
            assert ok is True
            # 验证 link 写入
            row = conn.execute(
                "SELECT linked_project_id FROM sp_novels WHERE id=?",
                (novel_id,),
            ).fetchone()
            assert row["linked_project_id"] == project_id
        finally:
            conn.close()

    def test_unlink_success(self):
        conn = get_connection()
        try:
            user_id = _create_user(conn)
            project_id = _create_project(conn, user_id)
            novel_id = _create_sp_novel(
                conn, user_id, linked_project_id=project_id,
            )
            conn.commit()
            ok = huimeng_bridge.link_novel_to_project(
                conn, user_id=user_id, novel_id=novel_id,
                project_id=None,  # 解绑
            )
            assert ok is True
            row = conn.execute(
                "SELECT linked_project_id FROM sp_novels WHERE id=?",
                (novel_id,),
            ).fetchone()
            assert row["linked_project_id"] is None
        finally:
            conn.close()

    def test_link_cross_user_novel_fails(self):
        """user_a 试图 link user_b 的 novel — 拒绝。"""
        conn = get_connection()
        try:
            user_a = _create_user(conn)
            user_b = _create_user(conn)
            project_a = _create_project(conn, user_a)
            novel_b = _create_sp_novel(conn, user_b)
            conn.commit()
            ok = huimeng_bridge.link_novel_to_project(
                conn, user_id=user_a, novel_id=novel_b,
                project_id=project_a,
            )
            assert ok is False
        finally:
            conn.close()

    def test_link_cross_user_project_fails(self):
        """user_a 试图把 novel 绑到 user_b 的 project — 拒绝。"""
        conn = get_connection()
        try:
            user_a = _create_user(conn)
            user_b = _create_user(conn)
            project_b = _create_project(conn, user_b)
            novel_a = _create_sp_novel(conn, user_a)
            conn.commit()
            ok = huimeng_bridge.link_novel_to_project(
                conn, user_id=user_a, novel_id=novel_a,
                project_id=project_b,
            )
            assert ok is False
            # 验证 novel_a 仍未绑定
            row = conn.execute(
                "SELECT linked_project_id FROM sp_novels WHERE id=?",
                (novel_a,),
            ).fetchone()
            assert row["linked_project_id"] is None
        finally:
            conn.close()


# ============================================================
# 异常隔离 — bridge 不应因父平台 service 抛错而泄露
# ============================================================

class TestExceptionIsolation:
    def test_drivers_block_handles_missing_table(self, monkeypatch):
        """模拟父平台 character_drivers_util 抛错 → bridge 返 ""。"""
        def boom(conn, project_id, character_names):
            raise RuntimeError("假装父平台 service 挂了")

        monkeypatch.setattr(
            "app.services.character_drivers_util.build_character_drivers_block",
            boom,
        )
        conn = get_connection()
        try:
            user_id = _create_user(conn)
            project_id = _create_project(conn, user_id)
            novel_id = _create_sp_novel(
                conn, user_id, linked_project_id=project_id,
            )
            conn.commit()
            out = huimeng_bridge.get_character_drivers_block(
                conn, user_id=user_id, novel_id=novel_id,
                character_names=["X"],
            )
        finally:
            conn.close()
        assert out == ""  # 父平台抛错 → bridge 静默吞 + 返空
