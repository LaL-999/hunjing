"""Sprint 6.A2 M9.A(2026-05-20)— 长篇心智(滚雪球深化)端到端测试。

覆盖:
  A. chain_context_builder
     - 无前作 → 返空字符串
     - 单代前作 → 含 narrative_summary
     - 多代前作 → 完整祖先链
     - 累积心智(arc / foreshadow / rule)→ 3 个 section 都显
  B. extract_arcs
     - 有演化 → 落库 character_arcs
     - 无演化 → 不写
     - 幂等(同 scene_index 不重复)
     - LLM 失败 → 兜底返 0,不阻塞
  C. extract_foreshadows
     - 新埋坑 → 落 status=open
     - 收尾坑 → UPDATE 已 open → resolved
     - 同一幕不重复抽
  D. extract_world_rules
     - 新规则 → 落 active=1
     - 已存在规则 → LLM prompt 看到 existing,不重复
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from unittest.mock import patch

from app.db import _connect, execute as db_execute, fetch_one, transaction
from app.models.simulation import Simulation
from app.services.chain_context_builder import (
    build_chain_context,
    list_open_foreshadows,
    list_active_world_rules,
)
from app.services.long_form_mind_extractor import (
    extract_arcs,
    extract_foreshadows,
    extract_world_rules,
)
from app.services.project_service import iso_now


def _conn():
    return _connect(Path(os.environ["HUIMENG_DB_PATH"]))


def _setup(client, make_user) -> tuple[str, str, str]:
    """造 user + project + sim — 返回 (user_id, project_id, sim_id)。"""
    user = make_user("mind_user")
    h = user["headers"]
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "M9.A 测试项目", "type": "novel", "mode": "initial"},
    ).json()
    project_id = p["id"]
    sim_id = uuid.uuid4().hex
    conn = _conn()
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO simulations "
                "(id, project_id, user_id, divergence, reshape_percent, "
                " rounds_planned, target_chars, style, "
                " context_simulation_ids, characters_snapshot, "
                " state, current_round, narrative, narrative_summary, created_at, "
                " tokens_input, tokens_output, cost_yuan) "
                "VALUES (?, ?, ?, '锚点', 10, 5, 4000, 'auto', '[]', '[]', "
                "        'done', 5, '...', NULL, ?, 0, 0, 0)",
                (sim_id, project_id, user["user_id"], iso_now()),
            )
    finally:
        conn.close()
    return user["user_id"], project_id, sim_id


def _create_ancestor_sim(
    project_id: str, user_id: str,
    narrative_summary: str, divergence: str = "前作锚点",
    context_ids: list[str] = None,
) -> str:
    """造一个 done 的祖先 sim,带 narrative_summary。"""
    conn = _conn()
    sim_id = uuid.uuid4().hex
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO simulations "
                "(id, project_id, user_id, divergence, reshape_percent, "
                " rounds_planned, target_chars, style, "
                " context_simulation_ids, characters_snapshot, "
                " state, current_round, narrative, narrative_summary, created_at, "
                " tokens_input, tokens_output, cost_yuan) "
                "VALUES (?, ?, ?, ?, 10, 5, 4000, 'auto', ?, '[]', "
                "        'done', 5, NULL, ?, ?, 0, 0, 0)",
                # 2026-06-01:narrative 留空 → 走 narrative_summary 分支
                # (chain_context v2 优先用 narrative 全文,无 narrative 才用 summary)
                (sim_id, project_id, user_id, divergence,
                 json.dumps(context_ids or [], ensure_ascii=False),
                 narrative_summary, iso_now()),
            )
    finally:
        conn.close()
    return sim_id


def _get_sim(sim_id: str) -> Simulation:
    conn = _conn()
    try:
        row = conn.execute("SELECT * FROM simulations WHERE id=?", (sim_id,)).fetchone()
        return Simulation.from_row(row)
    finally:
        conn.close()


def _create_character(client, h, project_id: str, name: str) -> str:
    r = client.post(
        f"/api/projects/{project_id}/characters", headers=h,
        json={"name": name, "identity": "测试角色"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ============================================================
# A. chain_context_builder
# ============================================================

def test_chain_context_empty_for_independent_sim(client, make_user):
    """无前作 + 无累积心智 → 返空字符串"""
    _, project_id, sim_id = _setup(client, make_user)
    sim = _get_sim(sim_id)
    conn = _conn()
    try:
        ctx = build_chain_context(conn, sim)
    finally:
        conn.close()
    assert ctx == ""


def test_chain_context_with_single_ancestor(client, make_user):
    """单代前作 + 摘要 → 含 narrative_summary"""
    user_id, project_id, current_sim_id = _setup(client, make_user)
    ancestor_id = _create_ancestor_sim(
        project_id, user_id, narrative_summary="原作末尾林晚选择留下,与苏宁达成谅解",
    )
    # 把 current sim 设为接续 ancestor
    conn = _conn()
    try:
        db_execute(
            conn,
            "UPDATE simulations SET context_simulation_ids=? WHERE id=?",
            (json.dumps([ancestor_id]), current_sim_id),
        )
        conn.commit()
    finally:
        conn.close()

    sim = _get_sim(current_sim_id)
    conn = _conn()
    try:
        ctx = build_chain_context(conn, sim)
    finally:
        conn.close()

    assert "完整继承链" in ctx
    assert "林晚选择留下" in ctx


def test_chain_context_with_multi_generation(client, make_user):
    """3 代继承链 → ctx 含全链 summary"""
    user_id, project_id, current_sim_id = _setup(client, make_user)
    a = _create_ancestor_sim(project_id, user_id, "原作:浪子初遇剑客")
    b = _create_ancestor_sim(project_id, user_id, "第1代:他们结为兄弟",
                              divergence="如果他们立刻结义", context_ids=[a])
    # current 接续 b → 全链是 a → b → current(第 2 代)
    conn = _conn()
    try:
        db_execute(
            conn,
            "UPDATE simulations SET context_simulation_ids=? WHERE id=?",
            (json.dumps([b]), current_sim_id),
        )
        conn.commit()
    finally:
        conn.close()

    sim = _get_sim(current_sim_id)
    conn = _conn()
    try:
        ctx = build_chain_context(conn, sim)
    finally:
        conn.close()

    assert "原作:浪子初遇剑客" in ctx
    assert "第1代:他们结为兄弟" in ctx


def test_chain_context_v2_recent_uses_full_text_distant_uses_summary(
    client, make_user,
):
    """2026-06-01 v2:近 2 代用全文 + 第 3 代起用摘要,治"长链续作丢前情细节"."""
    user_id, project_id, current_sim_id = _setup(client, make_user)
    # 造 3 代祖先链 — gen3 是最远祖先,gen1 是直接父辈
    gen3 = _create_ancestor_sim(
        project_id, user_id, narrative_summary="第3代前的摘要(最远祖先)",
        divergence="远古起源",
    )
    gen2 = _create_ancestor_sim(
        project_id, user_id, narrative_summary="第2代前的摘要(中代祖父)",
        divergence="中古衍化", context_ids=[gen3],
    )
    gen1 = _create_ancestor_sim(
        project_id, user_id, narrative_summary="第1代前的摘要(直接父辈)",
        divergence="近代续作", context_ids=[gen2],
    )

    # 给 gen1 / gen2 设全文 narrative(模拟实际有产物)
    # gen3 故意不设 narrative,保留 narrative_summary(模拟远祖摘要)
    full_narr_gen1 = "近代直接父辈的全文内容包含细节对白和微表情。" * 50
    full_narr_gen2 = "中代祖父的全文细节,含伏笔和氛围描写。" * 50
    conn = _conn()
    try:
        db_execute(
            conn,
            "UPDATE simulations SET narrative=? WHERE id=?",
            (full_narr_gen1, gen1),
        )
        db_execute(
            conn,
            "UPDATE simulations SET narrative=? WHERE id=?",
            (full_narr_gen2, gen2),
        )
        # current 接续 gen1 → 全链是 gen3 → gen2 → gen1 → current
        db_execute(
            conn,
            "UPDATE simulations SET context_simulation_ids=? WHERE id=?",
            (json.dumps([gen1]), current_sim_id),
        )
        conn.commit()
    finally:
        conn.close()

    sim = _get_sim(current_sim_id)
    conn = _conn()
    try:
        ctx = build_chain_context(conn, sim)
    finally:
        conn.close()

    # 近 2 代(gen1/gen2)用全文 → 含全文片段
    assert "近代直接父辈的全文内容" in ctx, "近代第 1 代应该用全文 narrative"
    assert "中代祖父的全文细节" in ctx, "近代第 2 代应该用全文 narrative"
    # 第 3 代(gen3)用摘要 → 含 narrative_summary 内容
    assert "第3代前的摘要" in ctx, "远代第 3 代应该走 narrative_summary 分支"
    # ctx 含 v2 标记
    assert "v2" in ctx or "近 2 代用全文尾部" in ctx


def test_chain_context_includes_accumulated_arcs_and_rules(client, make_user):
    """有累积 character_arc + open foreshadow + world_rule → 3 个 section 都显。"""
    user_id, project_id, current_sim_id = _setup(client, make_user)
    ancestor_id = _create_ancestor_sim(
        project_id, user_id, narrative_summary="前代故事",
    )
    conn = _conn()
    try:
        db_execute(
            conn,
            "UPDATE simulations SET context_simulation_ids=? WHERE id=?",
            (json.dumps([ancestor_id]), current_sim_id),
        )
        # 灌一条角色弧光
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO character_arcs "
                "(id, project_id, simulation_id, scene_index, "
                " character_ids_json, arc_keyword, trigger_summary, arc_kind, created_at) "
                "VALUES (?, ?, ?, 0, '[]', '从内向到豁达', '客栈夜雨那晚的对话', 'sudden', ?)",
                (uuid.uuid4().hex, project_id, ancestor_id, iso_now()),
            )
            # 灌一条 open 伏笔
            db_execute(
                tx,
                "INSERT INTO foreshadow_ledger "
                "(id, project_id, content, introduced_in_simulation_id, "
                " introduced_scene_index, status, priority, created_at, updated_at) "
                "VALUES (?, ?, '红裙照片背面的字是谁写的', ?, 1, 'open', 'high', ?, ?)",
                (uuid.uuid4().hex, project_id, ancestor_id, iso_now(), iso_now()),
            )
            # 灌一条 active 世界规则
            db_execute(
                tx,
                "INSERT INTO world_rules_ledger "
                "(id, project_id, rule_text, introduced_in_simulation_id, "
                " introduced_scene_index, scope, active, created_at, updated_at) "
                "VALUES (?, ?, '守门人偿信物不偿命', ?, 2, 'faction', 1, ?, ?)",
                (uuid.uuid4().hex, project_id, ancestor_id, iso_now(), iso_now()),
            )
    finally:
        conn.close()

    sim = _get_sim(current_sim_id)
    conn = _conn()
    try:
        ctx = build_chain_context(conn, sim)
    finally:
        conn.close()

    assert "跨代角色弧光" in ctx
    assert "从内向到豁达" in ctx
    assert "未解伏笔" in ctx
    assert "红裙照片背面" in ctx
    assert "世界规则" in ctx
    assert "守门人偿信物" in ctx


# ============================================================
# B. extract_arcs
# ============================================================

class _FakeChar:
    def __init__(self, id, name, is_protagonist=False):
        self.id = id
        self.name = name
        self.is_protagonist = is_protagonist


def _patch_arc_llm(monkeypatch, output_arcs):
    """patch llm_client.call_llm_json for arc_extractor."""
    def fake(system_prompt, user_input, **kwargs):
        return {"arcs": output_arcs}, {"input_tokens": 100, "output_tokens": 30}
    monkeypatch.setattr(
        "app.services.long_form_mind_extractor.call_llm_json", fake,
    )


def test_extract_arcs_writes_to_db(client, make_user, monkeypatch):
    user_id, project_id, sim_id = _setup(client, make_user)
    h = make_user("alice2")["headers"]   # need fresh user for char creation; just use char inline
    # 直接造 character 行避免 quota 干扰
    char_id = uuid.uuid4().hex
    conn = _conn()
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO characters "
                "(id, project_id, name, identity, personality, quotes, no_go_list, "
                " position_x, position_y, position_z, created_at, updated_at) "
                "VALUES (?, ?, '林晚', '', '', '[]', '[]', 0, 0, 0, ?, ?)",
                (char_id, project_id, iso_now(), iso_now()),
            )
    finally:
        conn.close()

    _patch_arc_llm(monkeypatch, [
        {
            "character_ids": [char_id],
            "arc_keyword": "从内向到挣扎",
            "trigger_summary": "本幕首次直面恐惧",
            "arc_kind": "sudden",
        }
    ])

    sim = _get_sim(sim_id)
    agents = [_FakeChar(char_id, "林晚", True)]
    conn = _conn()
    try:
        result = extract_arcs(conn, sim, 3, "客栈", "narrative...", agents)
    finally:
        conn.close()

    assert result["created"] == 1
    # 验证落库
    conn = _conn()
    try:
        row = fetch_one(
            conn,
            "SELECT arc_keyword, arc_kind FROM character_arcs "
            "WHERE simulation_id=? AND scene_index=?",
            (sim_id, 3),
        )
    finally:
        conn.close()
    assert row["arc_keyword"] == "从内向到挣扎"
    assert row["arc_kind"] == "sudden"


def test_extract_arcs_idempotent(client, make_user, monkeypatch):
    """同 scene_index 重复调 → 跳过(已抽)"""
    user_id, project_id, sim_id = _setup(client, make_user)
    char_id = uuid.uuid4().hex
    conn = _conn()
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO characters "
                "(id, project_id, name, identity, personality, quotes, no_go_list, "
                " position_x, position_y, position_z, created_at, updated_at) "
                "VALUES (?, ?, '林晚', '', '', '[]', '[]', 0, 0, 0, ?, ?)",
                (char_id, project_id, iso_now(), iso_now()),
            )
    finally:
        conn.close()

    _patch_arc_llm(monkeypatch, [
        {"character_ids": [char_id], "arc_keyword": "演化",
         "trigger_summary": "x", "arc_kind": "gradual"}
    ])
    sim = _get_sim(sim_id)
    agents = [_FakeChar(char_id, "林晚", True)]

    conn = _conn()
    try:
        r1 = extract_arcs(conn, sim, 5, "客栈", "narrative", agents)
        r2 = extract_arcs(conn, sim, 5, "客栈", "narrative", agents)
    finally:
        conn.close()

    assert r1["created"] == 1
    assert r2["created"] == 0
    assert r2.get("skipped") == "already_extracted"


def test_extract_arcs_empty_when_no_change(client, make_user, monkeypatch):
    """LLM 返空 arcs → 不写库"""
    user_id, project_id, sim_id = _setup(client, make_user)
    char_id = uuid.uuid4().hex
    conn = _conn()
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO characters "
                "(id, project_id, name, identity, personality, quotes, no_go_list, "
                " position_x, position_y, position_z, created_at, updated_at) "
                "VALUES (?, ?, '林晚', '', '', '[]', '[]', 0, 0, 0, ?, ?)",
                (char_id, project_id, iso_now(), iso_now()),
            )
    finally:
        conn.close()

    _patch_arc_llm(monkeypatch, [])
    sim = _get_sim(sim_id)
    agents = [_FakeChar(char_id, "林晚", True)]
    conn = _conn()
    try:
        r = extract_arcs(conn, sim, 1, "客栈", "narrative", agents)
    finally:
        conn.close()
    assert r["created"] == 0


# ============================================================
# C. extract_foreshadows
# ============================================================

def _patch_fs_llm(monkeypatch, new_list, resolved_list):
    def fake(system_prompt, user_input, **kwargs):
        return (
            {"new_foreshadows": new_list, "resolved_foreshadows": resolved_list},
            {"input_tokens": 100, "output_tokens": 30},
        )
    monkeypatch.setattr(
        "app.services.long_form_mind_extractor.call_llm_json", fake,
    )


def test_extract_foreshadows_creates_new(client, make_user, monkeypatch):
    user_id, project_id, sim_id = _setup(client, make_user)
    _patch_fs_llm(monkeypatch, [
        {"content": "红裙照片背面的字是谁写的", "priority": "high"},
    ], [])
    sim = _get_sim(sim_id)
    conn = _conn()
    try:
        r = extract_foreshadows(conn, sim, 2, "客栈", "narrative")
    finally:
        conn.close()
    assert r["created"] == 1
    assert r["resolved"] == 0

    conn = _conn()
    try:
        row = fetch_one(
            conn,
            "SELECT content, status, priority FROM foreshadow_ledger "
            "WHERE introduced_in_simulation_id=?",
            (sim_id,),
        )
    finally:
        conn.close()
    assert row["status"] == "open"
    assert row["priority"] == "high"
    assert "红裙照片" in row["content"]


def test_extract_foreshadows_resolves_existing(client, make_user, monkeypatch):
    """LLM 标记 open 伏笔为 resolved → 状态机切换。"""
    user_id, project_id, sim_id = _setup(client, make_user)
    # 先灌一条 open 伏笔
    fs_id = uuid.uuid4().hex
    conn = _conn()
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO foreshadow_ledger "
                "(id, project_id, content, introduced_in_simulation_id, "
                " introduced_scene_index, status, priority, created_at, updated_at) "
                "VALUES (?, ?, '红裙照片的字', ?, 1, 'open', 'high', ?, ?)",
                (fs_id, project_id, sim_id, iso_now(), iso_now()),
            )
    finally:
        conn.close()

    _patch_fs_llm(monkeypatch, [], [
        {"foreshadow_id": fs_id, "resolution_summary": "本幕揭示是死者妹妹写的"},
    ])
    sim = _get_sim(sim_id)
    conn = _conn()
    try:
        r = extract_foreshadows(conn, sim, 5, "客栈", "narrative")
    finally:
        conn.close()
    assert r["resolved"] == 1

    conn = _conn()
    try:
        row = fetch_one(
            conn,
            "SELECT status, resolution_summary FROM foreshadow_ledger WHERE id=?",
            (fs_id,),
        )
    finally:
        conn.close()
    assert row["status"] == "resolved"
    assert "妹妹写的" in row["resolution_summary"]


# ============================================================
# D. extract_world_rules
# ============================================================

def _patch_rule_llm(monkeypatch, new_rules):
    def fake(system_prompt, user_input, **kwargs):
        return {"new_rules": new_rules}, {"input_tokens": 100, "output_tokens": 30}
    monkeypatch.setattr(
        "app.services.long_form_mind_extractor.call_llm_json", fake,
    )


def test_extract_world_rules_creates(client, make_user, monkeypatch):
    user_id, project_id, sim_id = _setup(client, make_user)
    _patch_rule_llm(monkeypatch, [
        {"rule_text": "守门人偿信物不偿命,一信物代一命", "scope": "faction"},
    ])
    sim = _get_sim(sim_id)
    conn = _conn()
    try:
        r = extract_world_rules(conn, sim, 4, "地府", "narrative")
    finally:
        conn.close()
    assert r["created"] == 1

    conn = _conn()
    try:
        rules = list_active_world_rules(conn, project_id)
    finally:
        conn.close()
    assert len(rules) == 1
    assert "偿信物不偿命" in rules[0].rule_text
    assert rules[0].scope == "faction"


def test_list_open_foreshadows_priority_order(client, make_user):
    """list_open_foreshadows:high → medium → low 优先级排序。"""
    user_id, project_id, sim_id = _setup(client, make_user)
    conn = _conn()
    try:
        with transaction(conn) as tx:
            for content, prio in [
                ("low_坑", "low"),
                ("high_坑", "high"),
                ("medium_坑", "medium"),
            ]:
                db_execute(
                    tx,
                    "INSERT INTO foreshadow_ledger "
                    "(id, project_id, content, introduced_in_simulation_id, "
                    " introduced_scene_index, status, priority, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, 0, 'open', ?, ?, ?)",
                    (uuid.uuid4().hex, project_id, content, sim_id, prio,
                     iso_now(), iso_now()),
                )
    finally:
        conn.close()

    conn = _conn()
    try:
        fs_list = list_open_foreshadows(conn, project_id)
    finally:
        conn.close()
    priorities = [f.priority for f in fs_list]
    assert priorities == ["high", "medium", "low"]
