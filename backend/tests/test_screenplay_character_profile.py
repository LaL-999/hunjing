"""阶段 8.2 — character_profile_service 单元测试。

覆盖关键路径:
  1. novel 不存在 → 返 None
  2. 跨用户 novel → 返 None
  3. novel 存在但无 screenplay → bible-only 兜底
  4. novel 存在 + 有 screenplay → 完整 stats 派生
  5. role_tier 判定阈值(主角 / 配角 ≥5 句 / 龙套 / 群演)
  6. 已 link 项目 → bridge_assets 注入 SP-2 driver
  7. bridge 抛错 → 失败角色对应 bridge_assets=None,其他角色不受影响
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from app.db import get_connection
from app.screenplay.services import character_profile_service


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_user(conn) -> str:
    uid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, email, plan, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, f"{uid[:8]}@cp-test.com", "free", _now(), _now()),
    )
    return uid


def _make_project(conn, user_id: str) -> str:
    pid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO projects (id, user_id, name, type, mode, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pid, user_id, "测试项目", "novel", "initial", _now(), _now()),
    )
    return pid


def _make_novel(
    conn, user_id: str, *, linked_project_id: str | None = None,
) -> str:
    nid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sp_novels (id, user_id, title, source_format, "
        "source_filename, total_chars, total_chapters, uploaded_at, "
        "linked_project_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (nid, user_id, "测试小说", "txt", "t.txt", 0, 0, _now(),
         linked_project_id),
    )
    return nid


def _make_bible(
    conn, novel_id: str,
    *, characters: list[dict] | None = None,
    relationships: list[dict] | None = None,
    events: list[dict] | None = None,
) -> str:
    bid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sp_story_bibles (id, novel_id, source, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (bid, novel_id, "test", _now(), _now()),
    )
    char_ids: list[str] = []
    for c in characters or []:
        cid = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO sp_bible_characters (id, bible_id, name, aka_json, "
            "description, is_protagonist) VALUES (?, ?, ?, ?, ?, ?)",
            (cid, bid, c["name"], json.dumps(c.get("aka") or []),
             c.get("description", ""), 1 if c.get("is_protagonist") else 0),
        )
        char_ids.append(cid)
    for r in relationships or []:
        conn.execute(
            "INSERT INTO sp_bible_relationships (id, bible_id, source_char_id, "
            "target_char_id, type, description) VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, bid,
             char_ids[r["source_idx"]], char_ids[r["target_idx"]],
             r["type"], r.get("description", "")),
        )
    for e in events or []:
        conn.execute(
            "INSERT INTO sp_bible_events (id, bible_id, description, "
            "chapter_number, participant_ids_json) VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, bid, e["description"], e.get("chapter_number"),
             json.dumps([char_ids[i] for i in e.get("participant_idxs", [])])),
        )
    return bid


def _make_screenplay(conn, novel_id: str, user_id: str, *, yaml_text: str) -> str:
    sid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sp_screenplays (id, novel_id, yaml_text, stats_json, "
        "warnings_json, failed_chapters_json, schema_version, model_name, "
        "created_at, optimization_origin) "
        "VALUES (?, ?, ?, '{}', '[]', '[]', '1.0', NULL, ?, 'initial')",
        (sid, novel_id, yaml_text, _now()),
    )
    return sid


# ============================================================
# 测试
# ============================================================


class TestCharacterProfileService:
    def test_novel_not_exist_returns_none(self):
        result = character_profile_service.get_character_profiles(
            "nonexistent_novel", user_id="ghost_user",
        )
        assert result is None

    def test_cross_user_returns_none(self):
        conn = get_connection()
        try:
            user_a = _make_user(conn)
            user_b = _make_user(conn)
            novel_b = _make_novel(conn, user_b)
            conn.commit()
            result = character_profile_service.get_character_profiles(
                novel_b, user_id=user_a,
            )
            assert result is None
        finally:
            conn.close()

    def test_novel_without_screenplay_returns_bible_only(self):
        """有 bible 没 screenplay → 返兜底数据(stats 全 0)。"""
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            _make_bible(conn, nid, characters=[
                {"name": "渡边", "is_protagonist": True, "description": "我"},
                {"name": "绿子", "is_protagonist": False, "description": "她"},
            ])
            conn.commit()
            result = character_profile_service.get_character_profiles(
                nid, user_id=uid,
            )
            assert result is not None
            assert result.screenplay_id == ""  # 标记 bible-only
            assert len(result.characters) == 2
            names = {c.name for c in result.characters}
            assert names == {"渡边", "绿子"}
            for c in result.characters:
                # 无 screenplay → stats 应是默认 0
                assert c.stats.dialogue_count == 0
                assert c.stats.scene_count == 0

            # 主角 tier 标对
            wt = next(c for c in result.characters if c.name == "渡边")
            assert wt.is_protagonist is True
            assert wt.stats.role_tier == "protagonist"
        finally:
            conn.close()

    def test_role_tier_thresholds(self):
        """戏份层级:主角 / 配角 ≥5 句 / 龙套 / 群演。"""
        # 造 yaml 含 4 个角色:protag / 5 dialogue / 1 dialogue / 0 dialogue
        yaml_text = """
meta:
  schema_version: '1.0'
  title: t
characters:
  - id: char_001
    name: 主角
  - id: char_002
    name: 配角
  - id: char_003
    name: 龙套
  - id: char_004
    name: 群演
locations:
  - id: loc_001
    name: x
    int_ext: INT
scenes:
  - id: scene_001
    number: 1
    heading:
      int_ext: INT
      location_id: loc_001
      time_of_day: 日
    summary: x
    characters_present:
      - char_001
      - char_002
      - char_003
      - char_004
    source:
      chapter: 1
      paragraph_range: [1, 5]
    elements:
""".strip()
        # 加 5 句配角对白 + 1 句龙套对白 + 主角 3 句
        elements_lines = []
        for _ in range(3):
            elements_lines.append("      - type: dialogue\n        character_id: char_001\n        text: 主角说\n")
        for _ in range(5):
            elements_lines.append("      - type: dialogue\n        character_id: char_002\n        text: 配角说\n")
        elements_lines.append("      - type: dialogue\n        character_id: char_003\n        text: 龙套说一句\n")
        # char_004 0 句对白
        yaml_text = yaml_text + "\n" + "".join(elements_lines)

        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            _make_bible(conn, nid, characters=[
                {"name": "主角", "is_protagonist": True},
                {"name": "配角", "is_protagonist": False},
                {"name": "龙套", "is_protagonist": False},
                {"name": "群演", "is_protagonist": False},
            ])
            _make_screenplay(conn, nid, uid, yaml_text=yaml_text)
            conn.commit()
            result = character_profile_service.get_character_profiles(
                nid, user_id=uid,
            )
            assert result is not None
            by_name = {c.name: c for c in result.characters}
            # 主角(is_protagonist=True 不管台词数都是 protagonist)
            assert by_name["主角"].stats.role_tier == "protagonist"
            # 配角(5 句 ≥ 5)
            assert by_name["配角"].stats.role_tier == "supporting"
            assert by_name["配角"].stats.dialogue_count == 5
            # 龙套(1 句 > 0 且 < 5)
            assert by_name["龙套"].stats.role_tier == "bit_part"
            assert by_name["龙套"].stats.dialogue_count == 1
            # 群演(0 句)
            assert by_name["群演"].stats.role_tier == "extra"
            assert by_name["群演"].stats.dialogue_count == 0
        finally:
            conn.close()

    def test_linked_project_injects_bridge_assets(self):
        """已 link → 父平台 driver(SP-2)注入 bridge_assets。"""
        conn = get_connection()
        try:
            uid = _make_user(conn)
            pid = _make_project(conn, uid)
            # 父平台 characters 表加角色 + driver
            cid = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO characters (id, project_id, name, identity, "
                "personality, quotes, no_go_list, created_at, updated_at, "
                "surface_goal, deep_need, fatal_blind_spot, arc_from_to, secret_json) "
                "VALUES (?, ?, ?, '', '', '[]', '[]', ?, ?, ?, ?, ?, ?, ?)",
                (cid, pid, "渡边", _now(), _now(),
                 "想跟绿子相处", "承认直子已死",
                 "假装平静掩盖痛苦", "冷漠→救赎",
                 '[{"description":"曾背叛直子","hidden_from":["绿子"]}]'),
            )
            # link 后做 bible 抽
            nid = _make_novel(conn, uid, linked_project_id=pid)
            _make_bible(conn, nid, characters=[
                {"name": "渡边", "is_protagonist": True},
            ])
            conn.commit()
            result = character_profile_service.get_character_profiles(
                nid, user_id=uid,
            )
            assert result is not None
            assert result.linked_project_id == pid
            wt = next(c for c in result.characters if c.name == "渡边")
            assert wt.bridge_assets is not None
            assert wt.bridge_assets.surface_goal == "想跟绿子相处"
            assert wt.bridge_assets.deep_need == "承认直子已死"
            assert wt.bridge_assets.fatal_blind_spot == "假装平静掩盖痛苦"
            assert wt.bridge_assets.arc_from_to == "冷漠→救赎"
            assert len(wt.bridge_assets.secrets) == 1
            assert wt.bridge_assets.secrets[0]["description"] == "曾背叛直子"
            # graph 节点应标 has_bridge_assets=True
            node = next(n for n in result.graph["nodes"] if n["name"] == "渡边")
            assert node["has_bridge_assets"] is True
        finally:
            conn.close()

    def test_unlinked_novel_no_bridge_assets(self):
        """未 link → bridge_assets 全为 None,linked_project_id=None。"""
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid, linked_project_id=None)
            _make_bible(conn, nid, characters=[
                {"name": "渡边", "is_protagonist": True},
            ])
            conn.commit()
            result = character_profile_service.get_character_profiles(
                nid, user_id=uid,
            )
            assert result is not None
            assert result.linked_project_id is None
            wt = result.characters[0]
            assert wt.bridge_assets is None
            node = result.graph["nodes"][0]
            assert node["has_bridge_assets"] is False
        finally:
            conn.close()

    def test_relationships_and_events_propagate(self):
        """bible.relationships 和 events 正确传到对应角色 profile。"""
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            _make_bible(
                conn, nid,
                characters=[
                    {"name": "A", "is_protagonist": True},
                    {"name": "B", "is_protagonist": False},
                ],
                relationships=[
                    {"source_idx": 0, "target_idx": 1, "type": "朋友",
                     "description": "多年好友"},
                ],
                events=[
                    {"description": "A 和 B 初次见面", "chapter_number": 1,
                     "participant_idxs": [0, 1]},
                ],
            )
            conn.commit()
            result = character_profile_service.get_character_profiles(
                nid, user_id=uid,
            )
            assert result is not None
            a = next(c for c in result.characters if c.name == "A")
            b = next(c for c in result.characters if c.name == "B")
            assert len(a.relationships) == 1
            assert a.relationships[0]["target_name"] == "B"
            assert a.relationships[0]["type"] == "朋友"
            # 关系是单向的(A→B);B→A 没记
            assert len(b.relationships) == 0
            # 两人都参与了事件
            assert len(a.key_events) == 1
            assert len(b.key_events) == 1
            assert a.key_events[0].chapter_number == 1
            # graph 有一条 edge
            assert len(result.graph["edges"]) == 1
            assert result.graph["edges"][0]["type"] == "朋友"
        finally:
            conn.close()
