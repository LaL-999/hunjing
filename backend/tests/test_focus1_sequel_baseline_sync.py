"""Sprint 6.A2 路线图 #1 自然延伸(2026-05-23)— 续作角色 baseline 补完测试。

覆盖 sync_new_characters_from_canonical 在 INSERT 新角色后调 enrich_character 给空字段
(尤其 behavior_baseline)补完的链路:

  1. happy path:enricher 返回合法 5 字段 → baseline 入库 + quotes / no_go_list 补;
     description 派生的 identity / personality 因"已填不覆盖"被 enricher 跳过
  2. enricher LLM 抛错 → 新角色仍创建(基础 INSERT 已 commit),baseline 留 NULL,
     sync 不抛 + 主流程不阻塞
  3. 多个新角色 → 每个都被独立 enrich(各自一次 LLM 调用,各自落库)
  4. enricher LLM 返回 baseline 全非法 → 角色其他字段被补,baseline 仍 NULL

设计原则:
  - 复用 test_sequel_character_sync 的 sim/canonical 构造模式(_create_done_sim / _insert_canonical_entity)
  - 复用 test_focus1_enricher_baseline 的 LLM mock 模式(monkeypatch call_llm_json)
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import _connect, fetch_all, fetch_one, transaction, execute as db_execute
from app.models.character import Character
from app.models.simulation import Simulation
from app.services.project_service import iso_now
from app.services.sequel_character_sync import sync_new_characters_from_canonical
from app.services.simulation_service import reshape_to_rounds


# ============================================================
# helpers — 复用 test_sequel_character_sync 的模式
# ============================================================

def _create_middle_project_with_chars(
    client: TestClient, headers: dict, chars: list[dict],
) -> tuple[str, dict[str, str]]:
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": "续作 baseline 测试", "type": "novel", "mode": "middle", "tags": []},
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


def _run_sync(sim_id: str, project_id: str, scene_index: int = 2) -> dict:
    sim = _load_sim(sim_id)
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        agents = _load_characters_for_project(project_id)
        return sync_new_characters_from_canonical(conn, sim, scene_index, agents)
    finally:
        conn.close()


def _patch_enricher_llm(monkeypatch, output, *, raise_exc: Exception = None):
    """注入 agent_profile_enricher 的 call_llm_json。
    output: LLM JSON 输出(dict);raise_exc 非空时直接抛(模拟 LLM 失败)。
    """
    def fake(system_prompt, user_input, **kwargs):
        if raise_exc is not None:
            raise raise_exc
        return output, {"input_tokens": 500, "output_tokens": 300}
    monkeypatch.setattr(
        "app.services.agent_profile_enricher.call_llm_json", fake,
    )


# ============================================================
# 1. happy path — enricher 补 baseline + quotes + no_go_list
# ============================================================

def test_sequel_sync_enriches_new_character_baseline(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """续作新角色 INSERT 后 enricher 自动补 baseline + quotes + no_go_list;
    description 派生的 identity / personality(已填)不被覆盖。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    _set_protagonist(project_id, char_ids["李寻欢"])

    # 注入 1 个新角色到 canonical_entities
    _insert_canonical_entity(
        sim_id, "character", "林小满",
        aliases=["小满"], description="三年前从天台跳下,化作幽魂归来寻凶",
        first_introduced_scene=2,
    )

    # mock LLM 返回完整 5 字段(其中 identity / personality 应被 enricher 跳过)
    _patch_enricher_llm(monkeypatch, {
        "identity": "应被忽略 — identity 由 description 派生已非空",
        "personality": "应被忽略 — personality 已填",
        "quotes": ["我要找出杀我的人", "天台上的风,我永远记得"],
        "no_go_list": ["不会主动伤害无辜", "不离开此城"],
        "behavior_baseline": {
            "speech_register": "强硬",
            "emotional_intensity": 8,
            "moral_compass": "灰",
            # P0G.2(2026-05-24):out_of_baseline_examples 已永久删除
        },
    })

    summary = _run_sync(sim_id, project_id)

    # 角色创建
    assert len(summary["created_characters"]) == 1
    assert summary["created_characters"][0]["name"] == "林小满"

    # enricher 补完报告
    assert len(summary["enriched_characters"]) == 1
    enriched = summary["enriched_characters"][0]
    assert enriched["name"] == "林小满"
    # identity / personality 已填 → 不在 updated_fields 里
    assert "identity" not in enriched["updated_fields"]
    assert "personality" not in enriched["updated_fields"]
    # quotes / no_go_list / behavior_baseline 被补
    assert "quotes" in enriched["updated_fields"]
    assert "no_go_list" in enriched["updated_fields"]
    assert "behavior_baseline" in enriched["updated_fields"]

    # DB 状态:角色已落库 + baseline 字段被填
    chars = _load_characters_for_project(project_id)
    new_char = next(c for c in chars if c.name == "林小满")
    # identity / personality 由 sequel sync INSERT 时从 description 派生,enricher 没覆盖
    assert "续作第 2 幕首次登场" in new_char.identity
    assert "三年前从天台跳下" in new_char.identity
    # quotes / no_go_list 由 enricher 补
    assert "我要找出杀我的人" in new_char.quotes
    assert "天台上的风,我永远记得" in new_char.quotes
    assert "不会主动伤害无辜" in new_char.no_go_list
    # baseline 由 enricher 补
    assert new_char.behavior_baseline is not None
    assert new_char.behavior_baseline["speech_register"] == "强硬"
    assert new_char.behavior_baseline["emotional_intensity"] == 8
    assert new_char.behavior_baseline["moral_compass"] == "灰"


# ============================================================
# 2. enricher LLM 抛错 → 角色仍创建,baseline NULL,sync 不抛
# ============================================================

def test_sequel_sync_continues_when_enricher_llm_raises(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """enricher 内 LLM 抛异常 → enricher 自身 catch 返 error,sync 不阻塞,
    新角色 baseline 留 NULL 但本体 INSERT 已成功并 commit。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    _set_protagonist(project_id, char_ids["李寻欢"])

    _insert_canonical_entity(
        sim_id, "character", "韩紫雨",
        aliases=[], description="续作新登场角色,LLM 不在线时也要能落库",
        first_introduced_scene=2,
    )

    # LLM 抛 RuntimeError(模拟 503 / 网络问题)
    _patch_enricher_llm(
        monkeypatch, None, raise_exc=RuntimeError("LLM service unavailable"),
    )

    # sync 应当不抛
    summary = _run_sync(sim_id, project_id)

    # 角色仍被创建
    assert len(summary["created_characters"]) == 1
    assert summary["created_characters"][0]["name"] == "韩紫雨"
    # enricher 没成功补完任何字段
    assert summary["enriched_characters"] == []

    # DB 状态:角色已落库,但 baseline NULL
    chars = _load_characters_for_project(project_id)
    new_char = next(c for c in chars if c.name == "韩紫雨")
    assert new_char.behavior_baseline is None
    # identity / personality 仍由 INSERT 时 description 派生(enricher 没机会补)
    assert "续作第 2 幕首次登场" in new_char.identity


# ============================================================
# 3. 多新角色 → 每个独立 enrich
# ============================================================

def test_sequel_sync_enriches_each_new_character_independently(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """同一幕有 2 个新角色 → enricher 被调 2 次(每个新角色 1 次),各自 baseline 落库。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    _set_protagonist(project_id, char_ids["李寻欢"])

    _insert_canonical_entity(
        sim_id, "character", "甲新角色",
        aliases=[], description="续作新人 甲",
        first_introduced_scene=2,
    )
    _insert_canonical_entity(
        sim_id, "character", "乙新角色",
        aliases=[], description="续作新人 乙",
        first_introduced_scene=2,
    )

    # 用计数器追踪 LLM 调用次数;每次返回不同 baseline 让我们区分
    call_count = {"n": 0}

    def fake(system_prompt, user_input, **kwargs):
        call_count["n"] += 1
        char_name = user_input.get("character_name", "")
        # 按名字返回差异化 baseline(便于断言"哪个角色拿到哪条")
        if char_name == "甲新角色":
            return ({
                "identity": "应被忽略",
                "personality": "应被忽略",
                "quotes": ["甲的台词"],
                "no_go_list": ["甲的雷区"],
                "behavior_baseline": {
                    "speech_register": "平和",
                    "emotional_intensity": 4,
                    "moral_compass": "善",
                    "out_of_baseline_examples": [],
                },
            }, {"input_tokens": 500, "output_tokens": 300})
        else:  # 乙新角色
            return ({
                "identity": "应被忽略",
                "personality": "应被忽略",
                "quotes": ["乙的台词"],
                "no_go_list": ["乙的雷区"],
                "behavior_baseline": {
                    "speech_register": "强硬",
                    "emotional_intensity": 9,
                    "moral_compass": "灰",
                    "out_of_baseline_examples": ["乙越级示例"],
                },
            }, {"input_tokens": 500, "output_tokens": 300})

    monkeypatch.setattr(
        "app.services.agent_profile_enricher.call_llm_json", fake,
    )

    summary = _run_sync(sim_id, project_id)

    # 两个新角色都被创建
    assert len(summary["created_characters"]) == 2
    # LLM 被调了 2 次
    assert call_count["n"] == 2
    # enricher 报告里两个都在
    enriched_names = {e["name"] for e in summary["enriched_characters"]}
    assert enriched_names == {"甲新角色", "乙新角色"}

    # DB 校验:各自 baseline 落库(差异化字段)
    chars = _load_characters_for_project(project_id)
    jia = next(c for c in chars if c.name == "甲新角色")
    yi = next(c for c in chars if c.name == "乙新角色")
    assert jia.behavior_baseline["speech_register"] == "平和"
    assert jia.behavior_baseline["emotional_intensity"] == 4
    assert yi.behavior_baseline["speech_register"] == "强硬"
    assert yi.behavior_baseline["emotional_intensity"] == 9
    assert "甲的台词" in jia.quotes
    assert "乙的台词" in yi.quotes


# ============================================================
# 4. enricher 返回 baseline 全非法 → 角色其他字段补,baseline 仍 NULL
# ============================================================

def test_sequel_sync_enricher_invalid_baseline_falls_through(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """LLM 输出 baseline 全非法子字段 → enricher 的 _extract_behavior_baseline_from_profile
    返 None,baseline 不入库;但 quotes / no_go_list 等合法字段照样补。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    _set_protagonist(project_id, char_ids["李寻欢"])

    _insert_canonical_entity(
        sim_id, "character", "刘飞",
        aliases=[], description="续作新登场角色,LLM 输出 baseline 非法的测试",
        first_introduced_scene=2,
    )

    _patch_enricher_llm(monkeypatch, {
        "identity": "应被忽略",
        "personality": "应被忽略",
        "quotes": ["刘飞的台词 1", "刘飞的台词 2"],
        "no_go_list": ["不背叛朋友"],
        "behavior_baseline": {
            "speech_register": "中等",       # 非枚举
            "emotional_intensity": 99,        # 越界
            "moral_compass": "中立",          # 非枚举
            "out_of_baseline_examples": "not_a_list",  # 类型错
        },
    })

    summary = _run_sync(sim_id, project_id)

    # 角色创建 + enricher 补了其他字段
    assert len(summary["created_characters"]) == 1
    assert len(summary["enriched_characters"]) == 1
    enriched = summary["enriched_characters"][0]
    # quotes / no_go_list 被补
    assert "quotes" in enriched["updated_fields"]
    assert "no_go_list" in enriched["updated_fields"]
    # baseline 全非法 → 不在 updated_fields
    assert "behavior_baseline" not in enriched["updated_fields"]

    # DB 状态:baseline 仍 NULL,其他补完
    chars = _load_characters_for_project(project_id)
    new_char = next(c for c in chars if c.name == "刘飞")
    assert new_char.behavior_baseline is None
    assert "刘飞的台词 1" in new_char.quotes
    assert "不背叛朋友" in new_char.no_go_list
