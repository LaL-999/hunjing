"""Sprint 6.A2 FOCUS.11(2026-05-22):
  ① 跨 chunk LOCATION 归一(治"雪国/雪国温泉村/雪国温泉客栈"分裂 + 全标 1 章)
  ② 跨 job 角色归一(治"中年艺妓"被旧 job 残留,新 job LLM 给驹子加 alias 不合并)
"""
from __future__ import annotations

import json
import pytest


# ============================================================
# A. _merge_location_aliases (scene_extractor 内)
# ============================================================

def test_location_substring_merge_combines_chunks():
    """雪国 ⊂ 雪国温泉村 ⊂ 雪国温泉客栈 三层子串,合并后 chunks 取并集 = 3 章。"""
    from app.services.scene_extractor import _merge_location_aliases

    agg = {
        "雪国": {"aliases": set(), "descriptions": ["大地名"], "chunks": {1}},
        "雪国温泉村": {"aliases": set(), "descriptions": ["温泉村"], "chunks": {2}},
        "雪国温泉客栈": {"aliases": set(), "descriptions": ["客栈"], "chunks": {3}},
    }
    _merge_location_aliases(agg)
    # 合并后只剩 1 个 root,chunks 是 {1,2,3}
    assert len(agg) == 1
    root_name, root_entry = next(iter(agg.items()))
    assert root_entry["chunks"] == {1, 2, 3}
    # 被合并的两个 name 应该都在 root.aliases 里(root 是哪个不重要)
    all_names = {"雪国", "雪国温泉村", "雪国温泉客栈"}
    non_root = all_names - {root_name}
    assert root_entry["aliases"] >= non_root


def test_location_alias_intersection_merge():
    """两个 LOCATION 的 aliases 集合有交集 → 合并。"""
    from app.services.scene_extractor import _merge_location_aliases

    agg = {
        "客栈": {
            "aliases": {"温泉旅馆"}, "descriptions": ["客栈描述"], "chunks": {1},
        },
        "温泉客栈": {
            "aliases": {"温泉旅馆"}, "descriptions": ["温泉客栈描述"], "chunks": {3},
        },
    }
    _merge_location_aliases(agg)
    assert len(agg) == 1
    _, root_entry = next(iter(agg.items()))
    assert root_entry["chunks"] == {1, 3}


def test_location_distinct_names_not_merged():
    """完全不同的 LOCATION 不合并(东京 / 浜松 / 信号所)。"""
    from app.services.scene_extractor import _merge_location_aliases

    agg = {
        "东京": {"aliases": set(), "descriptions": [], "chunks": {2}},
        "浜松": {"aliases": set(), "descriptions": [], "chunks": {2}},
        "信号所": {"aliases": set(), "descriptions": [], "chunks": {1}},
    }
    _merge_location_aliases(agg)
    assert len(agg) == 3   # 都保留


def test_location_single_char_not_merged():
    """单字 name 不应被合并(避免"东" 误并到"东京")。"""
    from app.services.scene_extractor import _merge_location_aliases

    agg = {
        "东": {"aliases": set(), "descriptions": [], "chunks": {1}},
        "东京": {"aliases": set(), "descriptions": [], "chunks": {2}},
    }
    _merge_location_aliases(agg)
    # 单字"东"不满足"短 ≥ 2 字"条件 → 两者都保留
    assert len(agg) == 2


def test_location_empty_or_single_entry():
    """空 / 单条 agg → 不抛错,不改变。"""
    from app.services.scene_extractor import _merge_location_aliases

    empty_agg: dict = {}
    _merge_location_aliases(empty_agg)
    assert empty_agg == {}

    single_agg = {"东京": {"aliases": set(), "descriptions": [], "chunks": {1}}}
    _merge_location_aliases(single_agg)
    assert len(single_agg) == 1


# ============================================================
# B. _save_to_project 跨 job 归一(治"中年艺妓"残留)
# ============================================================

def test_cross_job_alias_redirect_merges_into_existing():
    """已有 character"中年艺妓",新 entity"驹子" aliases=['中年艺妓'] → 不新建,合到旧。"""
    import sqlite3
    from app.services.extract_service import _save_to_project

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # 建表(最小化 schema)
    conn.executescript("""
        CREATE TABLE characters (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            name TEXT,
            identity TEXT DEFAULT '',
            personality TEXT DEFAULT '',
            quotes TEXT DEFAULT '[]',
            no_go_list TEXT DEFAULT '[]',
            position_x REAL DEFAULT 0,
            position_y REAL DEFAULT 0,
            position_z REAL DEFAULT 0,
            color TEXT,
            created_at TEXT,
            updated_at TEXT,
            aliases_json TEXT,
            behavior_baseline_json TEXT,
            is_protagonist INTEGER DEFAULT 0,
            protagonist_score REAL DEFAULT 0,
            protagonist_reasons_json TEXT DEFAULT '[]',
            protagonist_user_pinned INTEGER DEFAULT 0,
            origin_simulation_id TEXT,
            life_status TEXT DEFAULT 'alive',
            status_note TEXT DEFAULT ''
        );
        CREATE TABLE relationships (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            source_id TEXT,
            target_id TEXT,
            type TEXT,
            strength TEXT,
            description TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT,
            type_label TEXT
        );
        CREATE TABLE events (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            description TEXT,
            participants TEXT DEFAULT '[]',
            chapter_index INTEGER,
            time_anchor TEXT,
            created_at TEXT,
            updated_at TEXT
        );
    """)
    project_id = "proj-1"
    # 已有 character "中年艺妓"(模拟旧 job 留下)
    conn.execute(
        "INSERT INTO characters (id, project_id, name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("char-old", project_id, "中年艺妓", "2026-05-22", "2026-05-22"),
    )

    # 新 job 抽出"驹子",aliases 含"中年艺妓"
    entities = [
        {"name": "驹子", "type": "PERSON", "aliases": ["艺妓", "中年艺妓", "驹姐"],
         "description": "温泉旅馆艺妓"},
    ]
    relations = []
    top_persons = entities

    counts = _save_to_project(
        conn, project_id, entities, relations,
        profiles_by_name={}, top_persons=top_persons,
    )

    # 关键断言:DB 里仍只有 1 个 character(没新建驹子)
    rows = conn.execute(
        "SELECT id, name, aliases_json FROM characters WHERE project_id=?",
        (project_id,),
    ).fetchall()
    assert len(rows) == 1, f"应只有 1 个 character,实际 {len(rows)}"
    assert rows[0]["id"] == "char-old", "应保留旧的 char-old(用户可能手改过)"

    # 旧 character 的 aliases 应该累积了"驹子 / 艺妓 / 驹姐"
    aliases = json.loads(rows[0]["aliases_json"] or "[]")
    assert "驹子" in aliases
    assert "艺妓" in aliases
    assert "驹姐" in aliases

    # skipped 应为 1(驹子被合并视为 skip)
    assert counts["skipped"] == 1


def test_cross_job_substring_redirect_merges_into_existing():
    """已有"绿子",新 entity"小林绿子"(子串关系)→ 不新建,合到旧"绿子"。"""
    import sqlite3
    from app.services.extract_service import _save_to_project

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE characters (
            id TEXT PRIMARY KEY, project_id TEXT, name TEXT,
            identity TEXT DEFAULT '', personality TEXT DEFAULT '',
            quotes TEXT DEFAULT '[]', no_go_list TEXT DEFAULT '[]',
            position_x REAL DEFAULT 0, position_y REAL DEFAULT 0, position_z REAL DEFAULT 0,
            color TEXT, created_at TEXT, updated_at TEXT,
            aliases_json TEXT, behavior_baseline_json TEXT,
            is_protagonist INTEGER DEFAULT 0, protagonist_score REAL DEFAULT 0,
            protagonist_reasons_json TEXT DEFAULT '[]',
            protagonist_user_pinned INTEGER DEFAULT 0,
            origin_simulation_id TEXT,
            life_status TEXT DEFAULT 'alive',
            status_note TEXT DEFAULT ''
        );
        CREATE TABLE relationships (id TEXT PRIMARY KEY, project_id TEXT,
            source_id TEXT, target_id TEXT, type TEXT, strength TEXT,
            description TEXT DEFAULT '', created_at TEXT, updated_at TEXT, type_label TEXT);
        CREATE TABLE events (id TEXT PRIMARY KEY, project_id TEXT, description TEXT,
            participants TEXT DEFAULT '[]', chapter_index INTEGER, time_anchor TEXT,
            created_at TEXT, updated_at TEXT);
    """)
    conn.execute(
        "INSERT INTO characters (id, project_id, name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("char-lvzi", "proj-1", "绿子", "2026-05-22", "2026-05-22"),
    )

    entities = [
        {"name": "小林绿子", "type": "PERSON", "aliases": [], "description": "全名版本"},
    ]
    counts = _save_to_project(
        conn, "proj-1", entities, [],
        profiles_by_name={}, top_persons=entities,
    )

    rows = conn.execute(
        "SELECT id, name, aliases_json FROM characters WHERE project_id='proj-1'",
    ).fetchall()
    assert len(rows) == 1   # 不新建
    aliases = json.loads(rows[0]["aliases_json"] or "[]")
    assert "小林绿子" in aliases   # 全名进绿子的 aliases


def test_cross_job_unrelated_name_creates_new_character():
    """新 entity 与已有无关 → 正常新建。"""
    import sqlite3
    from app.services.extract_service import _save_to_project

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE characters (
            id TEXT PRIMARY KEY, project_id TEXT, name TEXT,
            identity TEXT DEFAULT '', personality TEXT DEFAULT '',
            quotes TEXT DEFAULT '[]', no_go_list TEXT DEFAULT '[]',
            position_x REAL DEFAULT 0, position_y REAL DEFAULT 0, position_z REAL DEFAULT 0,
            color TEXT, created_at TEXT, updated_at TEXT,
            aliases_json TEXT, behavior_baseline_json TEXT,
            is_protagonist INTEGER DEFAULT 0, protagonist_score REAL DEFAULT 0,
            protagonist_reasons_json TEXT DEFAULT '[]',
            protagonist_user_pinned INTEGER DEFAULT 0,
            origin_simulation_id TEXT,
            life_status TEXT DEFAULT 'alive',
            status_note TEXT DEFAULT ''
        );
        CREATE TABLE relationships (id TEXT PRIMARY KEY, project_id TEXT,
            source_id TEXT, target_id TEXT, type TEXT, strength TEXT,
            description TEXT DEFAULT '', created_at TEXT, updated_at TEXT, type_label TEXT);
        CREATE TABLE events (id TEXT PRIMARY KEY, project_id TEXT, description TEXT,
            participants TEXT DEFAULT '[]', chapter_index INTEGER, time_anchor TEXT,
            created_at TEXT, updated_at TEXT);
    """)
    conn.execute(
        "INSERT INTO characters (id, project_id, name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("char-old", "proj-1", "渡边", "2026-05-22", "2026-05-22"),
    )

    entities = [
        {"name": "永泽", "type": "PERSON", "aliases": [], "description": "新角色"},
    ]
    counts = _save_to_project(
        conn, "proj-1", entities, [],
        profiles_by_name={}, top_persons=entities,
    )

    rows = conn.execute(
        "SELECT name FROM characters WHERE project_id='proj-1' ORDER BY name",
    ).fetchall()
    names = [r["name"] for r in rows]
    assert "渡边" in names
    assert "永泽" in names
    assert counts["characters"] == 1   # 新建 1 个
    assert counts["skipped"] == 0
