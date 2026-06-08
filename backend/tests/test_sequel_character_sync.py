"""Sprint 6.A2 M7.B(2026-05-20)— 续作新角色入库同步测试。

覆盖:
  A. happy path:character 类型 canonical_entities → 写入 characters + 建关系
  B. 重名跳过:canonical_name 或 alias 撞已有角色 → 不重复插
  C. 类型过滤:object / location / event 类型不会被同步到 characters
  D. 无主角项目:auto 建关系跳过(主角 = agents[0] 兜底)
  E. CharacterResponse 暴露 origin_simulation_id 字段
  F. list_sequel_characters 查询辅助 — 按 sim 过滤 / 全量

设计原则(对齐 test_canonical_guardian.py 模式):
  - 用 _create_done_sim_middle 造已 done 的 sim 作 fixtures
  - 直接 INSERT canonical_entities 模拟 entity_registrar 已落库
  - 调用 sync_new_characters_from_canonical 验证 characters / relationships 增量
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db import _connect, fetch_all, fetch_one, transaction, execute as db_execute
from app.models.character import Character
from app.models.simulation import Simulation
from app.services.project_service import iso_now
from app.services.sequel_character_sync import (
    list_sequel_characters,
    sync_new_characters_from_canonical,
)
from app.services.simulation_service import reshape_to_rounds


# ============================================================
# helpers(部分复用 test_canonical_guardian 的 fixture 模式)
# ============================================================

def _create_middle_project_with_chars(
    client: TestClient, headers: dict, chars: list[dict],
) -> tuple[str, dict[str, str]]:
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": "M7.B 测试", "type": "novel", "mode": "middle", "tags": []},
    ).json()
    project_id = p["id"]
    char_ids: dict[str, str] = {}
    for c in chars:
        r = client.post(
            f"/api/projects/{project_id}/characters", headers=headers, json=c,
        ).json()
        char_ids[c["name"]] = r["id"]
    return project_id, char_ids


def _preload_minimal_simulation_llm(ctrl, char_ids: dict, reshape_percent: int = 10):
    first_id = next(iter(char_ids.values()))
    rounds = reshape_to_rounds(reshape_percent)
    for r in range(1, rounds + 1):
        ctrl.json_queue.append({
            "present_agents": list(char_ids.values()),
            "speaking_agents": [first_id],
            "location": f"客栈(第 {r} 轮)",
            "time_advance": "片刻后",
            "round_seed": f"第 {r} 轮契机",
            "narrator_note": "灯火摇曳",
        })
        ctrl.json_queue.append({
            "monologue": "心下一沉",
            "action": "缓缓抬眼",
            "dialogue": f"第 {r} 轮的对白。",
        })
    ctrl.text_queue.append(
        "# 测试 narrative\n\n夜色深沉,客栈灯火摇曳……(测试产物文本)"
    )


def _create_done_sim(
    client: TestClient, headers: dict, patched_simulation_llm,
    chars: list[dict] = None,
) -> tuple[str, str, dict[str, str]]:
    """造 mode=middle 项目 + done sim,返回 (project_id, sim_id, char_ids)。"""
    if chars is None:
        chars = [
            {"name": "李寻欢", "identity": "江湖浪子", "personality": "豪爽仗义"},
            {"name": "孙小红", "identity": "江湖义士"},
            {"name": "上官金虹", "identity": "神秘剑客"},
        ]
    project_id, char_ids = _create_middle_project_with_chars(client, headers, chars)
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=headers,
        json={"divergence": "如果初见不在客栈而在江畔", "reshape_percent": 10},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    return project_id, body["simulation_id"], char_ids


def _set_protagonist(project_id: str, character_id: str) -> None:
    """直接 UPDATE characters.is_protagonist=1,模拟 ProtagonistJudger 已判定。"""
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "UPDATE characters SET is_protagonist=1 WHERE id=? AND project_id=?",
                (character_id, project_id),
            )
    finally:
        conn.close()


def _insert_canonical_entity(
    sim_id: str, entity_type: str, canonical_name: str,
    aliases: list[str], description: str, first_introduced_scene: int = 1,
) -> str:
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    entity_id = str(uuid.uuid4())
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO canonical_entities "
                "(id, simulation_id, entity_type, canonical_name, aliases_json, "
                " description, first_introduced_scene, locked_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    entity_id, sim_id, entity_type, canonical_name,
                    json.dumps(aliases, ensure_ascii=False),
                    description, first_introduced_scene, iso_now(), iso_now(),
                ),
            )
    finally:
        conn.close()
    return entity_id


def _load_sim(sim_id: str) -> Simulation:
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        row = fetch_one(conn, "SELECT * FROM simulations WHERE id=?", (sim_id,))
        assert row is not None, f"sim {sim_id} 不存在"
        return Simulation.from_row(row)
    finally:
        conn.close()


def _load_characters_for_project(project_id: str) -> list[Character]:
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        rows = fetch_all(
            conn, "SELECT * FROM characters WHERE project_id=? ORDER BY created_at ASC",
            (project_id,),
        )
        return [Character.from_row(r) for r in rows]
    finally:
        conn.close()


def _load_relationships_for_project(project_id: str) -> list[dict]:
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        rows = fetch_all(
            conn,
            "SELECT id, source_id, target_id, type, description, strength "
            "FROM relationships WHERE project_id=? ORDER BY created_at ASC",
            (project_id,),
        )
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ============================================================
# A. happy path — 新角色入库 + 关系图谱补全
# ============================================================

def test_sync_creates_new_character_and_relationship(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """续作 canonical_entities 中的新角色 → 写入 characters + 与主角建关系。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    # 把 "李寻欢" 标为主角(模拟 ProtagonistJudger 已判)
    li_id = char_ids["李寻欢"]
    _set_protagonist(project_id, li_id)

    # canonical_entities 注入 1 个新角色
    _insert_canonical_entity(
        sim_id, "character", "林小满",
        aliases=["小满"], description="续作新登场,三年前从天台跳下",
        first_introduced_scene=2,
    )

    # 调用同步服务
    sim = _load_sim(sim_id)
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        agents = _load_characters_for_project(project_id)
        summary = sync_new_characters_from_canonical(conn, sim, 2, agents)
    finally:
        conn.close()

    # 断言:1 角色 + 1 关系被创建
    assert len(summary["created_characters"]) == 1
    assert summary["created_characters"][0]["name"] == "林小满"
    assert len(summary["created_relationships"]) == 1
    assert summary["skipped"] == []

    # DB 中能查到该角色 + origin_simulation_id 标对
    chars_now = _load_characters_for_project(project_id)
    new_char = next(c for c in chars_now if c.name == "林小满")
    assert new_char.origin_simulation_id == sim_id
    assert "续作第 2 幕首次登场" in new_char.identity
    assert "三年前从天台跳下" in new_char.identity

    # 关系建立:主角(李寻欢)→ 林小满 + type=其他 + 含"续作第 2 幕"提示
    rels = _load_relationships_for_project(project_id)
    sequel_rels = [r for r in rels if r["target_id"] == new_char.id]
    assert len(sequel_rels) == 1
    assert sequel_rels[0]["source_id"] == li_id
    assert sequel_rels[0]["type"] == "其他"
    assert sequel_rels[0]["strength"] == "weak"
    assert "续作第 2 幕" in sequel_rels[0]["description"]


# ============================================================
# B. 重名跳过 — canonical_name / alias 撞已有角色
# ============================================================

def test_sync_skips_when_canonical_name_already_exists(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """canonical_name 与已有 characters.name 撞 → 跳过,不重复插。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    # 已有 "李寻欢";模拟 LLM 又把"李寻欢"作为新实体登记(本不该发生,防御性测试)
    _insert_canonical_entity(
        sim_id, "character", "李寻欢",
        aliases=[], description="名字撞到已有角色",
        first_introduced_scene=2,
    )

    sim = _load_sim(sim_id)
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        agents = _load_characters_for_project(project_id)
        summary = sync_new_characters_from_canonical(conn, sim, 2, agents)
    finally:
        conn.close()

    assert summary["created_characters"] == []
    assert len(summary["skipped"]) == 1
    assert summary["skipped"][0]["canonical_name"] == "李寻欢"
    assert summary["skipped"][0]["reason"] == "name_exists"

    # characters 表里"李寻欢"还是只有 1 个
    chars = _load_characters_for_project(project_id)
    li_count = sum(1 for c in chars if c.name == "李寻欢")
    assert li_count == 1


def test_sync_skips_when_alias_collides_with_existing_name(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """canonical_entities.aliases 中任一项撞已有角色名 → 跳过。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    # 新实体 canonical_name="某神秘人物",但 aliases 含 "孙小红"(已有角色)
    _insert_canonical_entity(
        sim_id, "character", "某神秘人物",
        aliases=["孙小红"], description="alias 撞名场景",
        first_introduced_scene=2,
    )

    sim = _load_sim(sim_id)
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        agents = _load_characters_for_project(project_id)
        summary = sync_new_characters_from_canonical(conn, sim, 2, agents)
    finally:
        conn.close()

    assert summary["created_characters"] == []
    assert len(summary["skipped"]) == 1
    # 未创建,characters 表里没有"某神秘人物"
    chars = _load_characters_for_project(project_id)
    assert not any(c.name == "某神秘人物" for c in chars)


# ============================================================
# C. 类型过滤 — 非 character 类型不入 characters 表
# ============================================================

def test_sync_ignores_non_character_entity_types(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """object / location / event 类型即使在 canonical_entities 也不会进 characters。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    _insert_canonical_entity(sim_id, "object", "神秘日记本", [], "硬皮本", 2)
    _insert_canonical_entity(sim_id, "location", "废弃天台", [], "跳楼现场", 2)
    _insert_canonical_entity(sim_id, "event", "天台事件", [], "三年前的悲剧", 2)
    # 1 个真正的新角色,作对比
    _insert_canonical_entity(sim_id, "character", "新角色甲", [], "续作新人", 3)

    sim = _load_sim(sim_id)
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        agents = _load_characters_for_project(project_id)
        summary = sync_new_characters_from_canonical(conn, sim, 3, agents)
    finally:
        conn.close()

    # 只有 "新角色甲" 被同步,其它 3 类无关 entity 都被忽略
    assert len(summary["created_characters"]) == 1
    assert summary["created_characters"][0]["name"] == "新角色甲"

    chars = _load_characters_for_project(project_id)
    assert not any(c.name in ("神秘日记本", "废弃天台", "天台事件") for c in chars)


# ============================================================
# D. 无主角项目兜底:用 agents[0] 作主角占位
# ============================================================

def test_sync_falls_back_to_first_agent_when_no_protagonist(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """is_protagonist 全 0 时,用 agents[0] 当 relationship.source。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    # 不调用 _set_protagonist,所有角色 is_protagonist=0

    _insert_canonical_entity(
        sim_id, "character", "新登场者",
        aliases=[], description="续作新人,无主角的项目",
        first_introduced_scene=2,
    )

    sim = _load_sim(sim_id)
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        agents = _load_characters_for_project(project_id)
        summary = sync_new_characters_from_canonical(conn, sim, 2, agents)
    finally:
        conn.close()

    # 角色创建 + 关系仍然建立(用 agents[0] 兜底)
    assert len(summary["created_characters"]) == 1
    assert len(summary["created_relationships"]) == 1
    assert summary["created_relationships"][0]["from_protagonist_id"] == agents[0].id


# ============================================================
# E. API 层 — CharacterResponse 暴露 origin_simulation_id
# ============================================================

def test_character_list_api_exposes_origin_simulation_id(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """GET /api/projects/{id}/characters 列表返 origin_simulation_id 字段。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    _insert_canonical_entity(
        sim_id, "character", "续作新人 A",
        aliases=[], description="测试 API 字段透出",
        first_introduced_scene=2,
    )
    sim = _load_sim(sim_id)
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        agents = _load_characters_for_project(project_id)
        sync_new_characters_from_canonical(conn, sim, 2, agents)
    finally:
        conn.close()

    r = client.get(f"/api/projects/{project_id}/characters", headers=h)
    assert r.status_code == 200
    chars = r.json()
    sequel_new = next(c for c in chars if c["name"] == "续作新人 A")
    assert sequel_new["origin_simulation_id"] == sim_id

    # 原作角色应当 origin_simulation_id=None
    li = next(c for c in chars if c["name"] == "李寻欢")
    assert li["origin_simulation_id"] is None


# ============================================================
# F. list_sequel_characters 查询辅助
# ============================================================

def test_list_sequel_characters_filters_by_simulation(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """list_sequel_characters(sim_id) → 只返该 sim 产生的角色;sim_id=None → 全部续作角色。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    _insert_canonical_entity(sim_id, "character", "A 续作角色", [], "from sim 1", 2)
    sim = _load_sim(sim_id)
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        agents = _load_characters_for_project(project_id)
        sync_new_characters_from_canonical(conn, sim, 2, agents)
    finally:
        conn.close()

    # 按 sim 过滤
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        only_this_sim = list_sequel_characters(conn, project_id, simulation_id=sim_id)
        all_sequel = list_sequel_characters(conn, project_id)
    finally:
        conn.close()

    assert len(only_this_sim) == 1
    assert only_this_sim[0].name == "A 续作角色"
    assert only_this_sim[0].origin_simulation_id == sim_id
    assert len(all_sequel) == 1  # 项目里只有这一个续作角色


def test_sync_is_idempotent_within_same_sim(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """同一 sim 同一新角色,sync 调用 2 次只创建 1 次(name 已存在则跳过)。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    _insert_canonical_entity(sim_id, "character", "幂等测试角色", [], "测试", 2)
    sim = _load_sim(sim_id)

    # 第 1 次:创建
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        agents = _load_characters_for_project(project_id)
        s1 = sync_new_characters_from_canonical(conn, sim, 2, agents)
    finally:
        conn.close()
    assert len(s1["created_characters"]) == 1

    # 第 2 次:跳过(name 已存在)
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        agents = _load_characters_for_project(project_id)
        s2 = sync_new_characters_from_canonical(conn, sim, 2, agents)
    finally:
        conn.close()
    assert len(s2["created_characters"]) == 0
    assert len(s2["skipped"]) == 1
    assert s2["skipped"][0]["reason"] == "name_exists"
