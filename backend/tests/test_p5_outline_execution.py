"""P5.1(2026-05-27)— outline 执行率检测单元测试.

治 2026-05-27 实测挪威森林 28 幕续作"剧情空心化"现象:
LLM 用"看星星/吃面"日常琐事替换"打电话/问直子/崩溃大哭"核心剧情。

测试 5 个核心 case:
  1. 关键词提取算法正确性(虚词过滤、≥ 2 字保留)
  2. 全覆盖 → 0 critical
  3. 单条 0 命中 → 1 warning(具体哪条被跳)
  4. 覆盖率 < 50% → 1 critical(整体空心化)+ N warning
  5. 无 outline 的 sim → 完全跳过(返空)
"""
from __future__ import annotations

import json
import sqlite3
import uuid

import pytest

from app.services.consistency_checker import (
    _check_outline_execution,
    _extract_keywords_from_event,
)


# ============================================================
# 1. 关键词提取算法
# ============================================================

def test_extract_keywords_basic():
    """从典型 key_event 描述中提取 ≥ 2 字的核心词。"""
    kws = _extract_keywords_from_event("渡边在电话亭与绿子通话")
    # 应至少包含核心实体 / 动词的 2-字 substring
    assert "渡边" in kws
    assert "电话亭" in kws or "电话" in kws
    assert "绿子" in kws
    assert "通话" in kws
    # 单字虚词不应入(在/与)
    assert "在" not in kws
    assert "与" not in kws


def test_extract_keywords_filters_short():
    """单字虚词不入,所有关键词长度 ≥ 2。"""
    kws = _extract_keywords_from_event("他和她说了话")
    for kw in kws:
        assert len(kw) >= 2


def test_extract_keywords_handles_punct():
    """逗号 / 句号 / 顿号作分界。"""
    kws = _extract_keywords_from_event("永泽告诉他自己通过了外交官考试,即将去德国")
    assert "永泽" in kws
    assert "外交官" in kws or "外交" in kws
    assert "考试" in kws
    assert "德国" in kws


# ============================================================
# 2. 检测器 fixture
# ============================================================

@pytest.fixture
def conn_with_outline(tmp_path):
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE simulation_outlines (
        id TEXT PRIMARY KEY,
        simulation_id TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL DEFAULT 'pending',
        target_chars INTEGER, max_scenes INTEGER,
        overall_theme TEXT, qi_cheng_zhuan_he_json TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    )""")
    # 对齐 migrations/043 实际 schema(去掉 CHECK / FK,测试简化)
    conn.execute("""CREATE TABLE outline_scenes (
        id                            TEXT PRIMARY KEY,
        outline_id                    TEXT NOT NULL,
        scene_index                   INTEGER NOT NULL,
        scene_summary                 TEXT NOT NULL,
        scene_purpose                 TEXT NOT NULL DEFAULT '推进主线',
        location                      TEXT NOT NULL,
        time_anchor                   TEXT NOT NULL DEFAULT '',
        characters_present_json       TEXT NOT NULL DEFAULT '[]',
        key_events_json               TEXT NOT NULL DEFAULT '[]',
        key_props_json                TEXT NOT NULL DEFAULT '[]',
        transition_from_last          TEXT NOT NULL DEFAULT '',
        user_edited                   INTEGER NOT NULL DEFAULT 0,
        state                         TEXT NOT NULL DEFAULT 'pending',
        generated_simulation_scene_id TEXT,
        error_message                 TEXT,
        created_at                    TEXT NOT NULL,
        updated_at                    TEXT NOT NULL
    )""")
    conn.commit()
    return conn


def _insert_outline(conn, sim_id: str, scenes: list[dict]):
    """造一个 approved outline + N 幕。"""
    outline_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO simulation_outlines VALUES (?,?,?,?,?,?,?,?,?,?)",
        (outline_id, sim_id, "approved", 4000, len(scenes),
         "test theme", None, None, "2026-05-27", "2026-05-27"),
    )
    for sc in scenes:
        # 字段顺序对齐 CREATE TABLE
        conn.execute(
            "INSERT INTO outline_scenes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                uuid.uuid4().hex,                                                 # id
                outline_id,                                                       # outline_id
                sc["scene_index"],                                                # scene_index
                sc.get("scene_summary", "test"),                                  # scene_summary
                sc.get("scene_purpose", "推进主线"),                              # scene_purpose
                sc.get("location", "test"),                                       # location
                sc.get("time_anchor", "test"),                                    # time_anchor
                json.dumps(sc.get("characters_present", []), ensure_ascii=False), # characters_present_json
                json.dumps(sc.get("key_events", []), ensure_ascii=False),         # key_events_json
                json.dumps(sc.get("key_props", []), ensure_ascii=False),          # key_props_json
                sc.get("transition_from_last", ""),                               # transition_from_last
                0,                                                                # user_edited
                "pending",                                                        # state
                None,                                                             # generated_simulation_scene_id
                None,                                                             # error_message
                "2026-05-27",                                                     # created_at
                "2026-05-27",                                                     # updated_at
            ),
        )
    conn.commit()


# ============================================================
# 3. 全覆盖 → 0 critical
# ============================================================

def test_full_coverage_no_violations(conn_with_outline):
    _insert_outline(conn_with_outline, "sim-1", [{
        "scene_index": 0,
        "key_events": [
            "渡边在电话亭与绿子通话",
            "绿子问他在哪里",
            "他只说想见她",
        ],
    }])
    narrative = "渡边走进电话亭,投币,听筒里传来绿子的声音。" \
                "「你在哪里?」绿子问。渡边沉默片刻,说「我想见你」。" \
                "电话亭外的霓虹灯一闪一闪。" * 3
    v = _check_outline_execution(conn_with_outline, "sim-1", 0, narrative)
    crits = [vi for vi in v if vi.severity == "critical"]
    assert len(crits) == 0


# ============================================================
# 4. 单条 0 命中 → warning
# ============================================================

def test_one_missed_event_warns(conn_with_outline):
    _insert_outline(conn_with_outline, "sim-1", [{
        "scene_index": 0,
        "key_events": [
            "渡边在电话亭与绿子通话",         # 命中
            "绿子问他是否还爱直子",            # 缺失
            "渡边说直子永远是他的一部分",     # 缺失
        ],
    }])
    narrative = "渡边走进电话亭,绿子在听筒那头。" \
                "他们聊了好一会儿,聊乌冬面,聊电车,聊烟。" \
                "渡边没有提起任何过去的事。" * 5
    v = _check_outline_execution(conn_with_outline, "sim-1", 0, narrative)
    # 直子 完全没出现 → 2 条 warning + 1 条 critical(覆盖率 1/3 = 33% < 50%)
    warns = [vi for vi in v if vi.severity == "warning"]
    crits = [vi for vi in v if vi.severity == "critical"]
    assert len(warns) >= 2, f"应报 2 条缺失 event,实际 {len(warns)}"
    assert len(crits) >= 1, "覆盖率 33% < 50% 应报 critical"
    assert any("直子" in w.evidence for w in warns)


# ============================================================
# 5. 全部跳过 → 整体 critical(剧情空心化)
# ============================================================

def test_full_skip_critical(conn_with_outline):
    """模拟挪威森林第 22 幕:葬礼后崩溃大哭被替换成早班车回家。
    narrative 完全没出现"酒吧/喝酒/崩溃/大哭"等独特词 → 2 event 都缺失 → critical。
    """
    _insert_outline(conn_with_outline, "sim-1", [{
        "scene_index": 0,
        "key_events": [
            "渡边和绿子在新宿地下爵士酒吧喝酒",
            "绿子终于崩溃大哭",
        ],
    }])
    # narrative 用第一人称叙述,完全没有 event 独特词(酒吧/喝酒/崩溃/大哭/新宿/爵士/渡边)
    narrative = "我回到东京时已是黄昏。她从检票口出来。" \
                "两人一起搭电车回去。路上没有说话。" \
                "天色渐渐暗下来。" * 5
    v = _check_outline_execution(conn_with_outline, "sim-1", 0, narrative)
    crits = [vi for vi in v if vi.severity == "critical"]
    warns = [vi for vi in v if vi.severity == "warning"]
    assert len(crits) >= 1
    assert len(warns) == 2, "2 条 key_events 都缺失 → 2 warning"
    # critical evidence 应含"空心化"
    assert any("空心化" in c.evidence for c in crits)


# ============================================================
# 6. 无 outline 的 sim → 完全跳过(降级容错)
# ============================================================

def test_no_outline_returns_empty(conn_with_outline):
    """sim 没有 outline → _check_outline_execution 返空(走原 evolution 路径)。"""
    # 不 insert outline
    v = _check_outline_execution(
        conn_with_outline, "sim-no-outline", 0,
        "一段长长的产物文本" * 50,
    )
    assert v == []


# ============================================================
# 7. scene_index 越界 → 返空(降级)
# ============================================================

def test_scene_index_out_of_range_returns_empty(conn_with_outline):
    _insert_outline(conn_with_outline, "sim-1", [{
        "scene_index": 0,
        "key_events": ["渡边打电话"],
    }])
    # 查 scene_index=5 但 outline 只有 1 幕 → 找不到对应 scene → 返空
    v = _check_outline_execution(
        conn_with_outline, "sim-1", 5,
        "一段长长的产物文本" * 50,
    )
    assert v == []
