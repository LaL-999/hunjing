"""Sprint 6.A2 路线图 #1(2026-05-22)— agent_profile_enricher 补 behavior_baseline 测试。

覆盖 enrich_character 对 behavior_baseline 字段的:
  1. 空 baseline + LLM 输出合法 → 入库
  2. 已填 baseline → 跳过保留(已填不覆盖铁律)
  3. LLM 输出全非法 → baseline 不入库,其他字段正常处理
  4. LLM 输出部分子字段非法 → 合法保留 + 非法置 None
  5. 只 baseline 空(其他都填了) → 单独补 baseline
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import fetch_one, get_connection
from app.models.character import Character
from app.services.agent_profile_enricher import enrich_character


# ============================================================
# helpers
# ============================================================

def _create_project(client, headers, name):
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": name, "type": "novel", "mode": "initial"},
    ).json()
    return p["id"]


def _create_char(client, headers, project_id, **kwargs):
    body = {"name": kwargs.pop("name", "测试角色")}
    body.update(kwargs)
    r = client.post(
        f"/api/projects/{project_id}/characters",
        headers=headers, json=body,
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _get_char_from_db(char_id) -> Character:
    conn = get_connection()
    try:
        row = fetch_one(conn, "SELECT * FROM characters WHERE id=?", (char_id,))
        assert row is not None
        return Character.from_row(row)
    finally:
        conn.close()


def _patch_enricher_llm(monkeypatch, output):
    """注入 agent_profile_enricher 的 call_llm_json"""
    def fake(system_prompt, user_input, **kwargs):
        return output, {"input_tokens": 500, "output_tokens": 300}
    monkeypatch.setattr(
        "app.services.agent_profile_enricher.call_llm_json", fake,
    )


def _run_enrich(char_id):
    """跑 enrich_character 主流程,返回 result dict"""
    conn = get_connection()
    try:
        char = Character.from_row(
            fetch_one(conn, "SELECT * FROM characters WHERE id=?", (char_id,)),
        )
        return enrich_character(conn, char)
    finally:
        conn.close()


# ============================================================
# 1. 空 baseline + LLM 输出合法 → 入库
# ============================================================

def test_enrich_empty_baseline_with_valid_llm_output(
    client: TestClient, make_user, monkeypatch,
):
    user = make_user("enricher-baseline-empty")
    h = user["headers"]
    project_id = _create_project(client, h, "空 baseline 项目")
    char_id = _create_char(client, h, project_id, name="渡边")

    _patch_enricher_llm(monkeypatch, {
        "identity": "我是大学生渡边",
        "personality": "我习惯独处,不喜欢说话",
        "quotes": ["晚上见", "我去福岛"],
        "no_go_list": ["不主动说情话"],
        "behavior_baseline": {
            "speech_register": "平和",
            "emotional_intensity": 3,
            "moral_compass": "灰",
            # P0G.2(2026-05-24):out_of_baseline_examples 已永久删除
        },
    })

    result = _run_enrich(char_id)

    assert result["error"] is None
    assert "behavior_baseline" in result["updated_fields"]

    char_after = _get_char_from_db(char_id)
    assert char_after.behavior_baseline is not None
    assert char_after.behavior_baseline["speech_register"] == "平和"
    assert char_after.behavior_baseline["emotional_intensity"] == 3
    assert char_after.behavior_baseline["moral_compass"] == "灰"


# ============================================================
# 2. 已填 baseline → 跳过保留
# ============================================================

def test_enrich_existing_baseline_skipped(
    client: TestClient, make_user, monkeypatch,
):
    """用户已经填了 baseline → 不覆盖 + skipped_fields 包含"""
    user = make_user("enricher-baseline-existing")
    h = user["headers"]
    project_id = _create_project(client, h, "已填 baseline 项目")
    char_id = _create_char(
        client, h, project_id,
        name="渡边",
        # 其他字段空让 enricher 仍能跑
        behavior_baseline={
            "speech_register": "强硬",
            "emotional_intensity": 7,
            "moral_compass": "善",
            "out_of_baseline_examples": [],
        },
    )

    # LLM 即便返回不同 baseline,enricher 也应忽略
    _patch_enricher_llm(monkeypatch, {
        "identity": "我是渡边",
        "personality": "我喜独处",
        "quotes": ["a", "b"],
        "no_go_list": ["c"],
        "behavior_baseline": {
            "speech_register": "卑微",   # 不同
            "emotional_intensity": 1,
            "moral_compass": "恶",
            "out_of_baseline_examples": ["abc"],
        },
    })

    result = _run_enrich(char_id)

    assert "behavior_baseline" not in result["updated_fields"]
    assert "behavior_baseline" in result["skipped_fields"]

    char_after = _get_char_from_db(char_id)
    assert char_after.behavior_baseline["speech_register"] == "强硬"
    assert char_after.behavior_baseline["emotional_intensity"] == 7
    assert char_after.behavior_baseline["moral_compass"] == "善"


# ============================================================
# 3. LLM 输出 baseline 全非法 → 不入库,其他字段正常
# ============================================================

def test_enrich_all_invalid_baseline_not_persisted(
    client: TestClient, make_user, monkeypatch,
):
    user = make_user("enricher-baseline-invalid")
    h = user["headers"]
    project_id = _create_project(client, h, "全非法 baseline 项目")
    char_id = _create_char(client, h, project_id, name="渡边")

    _patch_enricher_llm(monkeypatch, {
        "identity": "我是大学生",
        "personality": "我喜独处",
        "quotes": ["晚上见"],
        "no_go_list": ["不主动"],
        "behavior_baseline": {
            "speech_register": "中等",          # 非枚举
            "emotional_intensity": 99,           # 超范围
            "moral_compass": "中立",             # 非枚举
            "out_of_baseline_examples": "not_list",  # 类型错
        },
    })

    result = _run_enrich(char_id)

    # baseline 没入库
    assert "behavior_baseline" not in result["updated_fields"]
    # 其他字段正常补
    assert "identity" in result["updated_fields"]

    char_after = _get_char_from_db(char_id)
    # baseline 仍为 None
    assert char_after.behavior_baseline is None
    # 其他字段已补
    assert char_after.identity == "我是大学生"


# ============================================================
# 4. LLM 部分子字段非法 → 合法保留 + 非法置 None
# ============================================================

def test_enrich_partial_valid_baseline(
    client: TestClient, make_user, monkeypatch,
):
    user = make_user("enricher-baseline-partial")
    h = user["headers"]
    project_id = _create_project(client, h, "部分合法 baseline 项目")
    char_id = _create_char(client, h, project_id, name="渡边")

    _patch_enricher_llm(monkeypatch, {
        "identity": "我是大学生",
        "personality": "我喜独处",
        "quotes": ["晚上见"],
        "no_go_list": ["不主动"],
        "behavior_baseline": {
            "speech_register": "中等",     # 非枚举 → None
            "emotional_intensity": 6,       # 合法
            "moral_compass": "善",          # 合法
            # P0G.2(2026-05-24):out_of_baseline_examples 已永久删除
        },
    })

    result = _run_enrich(char_id)

    # 有合法子字段 → baseline 整体入库
    assert "behavior_baseline" in result["updated_fields"]

    char_after = _get_char_from_db(char_id)
    assert char_after.behavior_baseline["speech_register"] is None   # 非法 → None
    assert char_after.behavior_baseline["emotional_intensity"] == 6
    assert char_after.behavior_baseline["moral_compass"] == "善"


# ============================================================
# 5. 只 baseline 空(其他都填了) → 只补 baseline
# ============================================================

def test_enrich_only_baseline_missing(
    client: TestClient, make_user, monkeypatch,
):
    user = make_user("enricher-baseline-only")
    h = user["headers"]
    project_id = _create_project(client, h, "只缺 baseline 项目")
    char_id = _create_char(
        client, h, project_id,
        name="渡边",
        identity="我是大学生",
        personality="我喜独处",
        quotes=["晚上见", "我去福岛"],
        no_go_list=["不主动"],
        # behavior_baseline 不填 → 应该被补
    )

    _patch_enricher_llm(monkeypatch, {
        # 其他字段返回不同值,enricher 应该忽略(已填不补)
        "identity": "应忽略",
        "personality": "应忽略",
        "quotes": ["应忽略"],
        "no_go_list": ["应忽略"],
        "behavior_baseline": {
            "speech_register": "平和",
            "emotional_intensity": 4,
            "moral_compass": "灰",
            "out_of_baseline_examples": [],
        },
    })

    result = _run_enrich(char_id)

    # 只 baseline 被更新
    assert result["updated_fields"] == ["behavior_baseline"]

    char_after = _get_char_from_db(char_id)
    # 其他字段保留原值
    assert char_after.identity == "我是大学生"
    assert char_after.personality == "我喜独处"
    assert "晚上见" in char_after.quotes
    # baseline 已补
    assert char_after.behavior_baseline["speech_register"] == "平和"
    assert char_after.behavior_baseline["emotional_intensity"] == 4
