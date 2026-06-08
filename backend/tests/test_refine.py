"""Refine 服务端到端测试 — Sprint 1.D 验收基线。

测试分组(对齐 docs/MVP阶段1_角色对焦组件设计.md §16):
  A. 基础 API     too_few / 成功 / 401 / 跨用户 404
  B. 兜底 ①(value 覆盖非空 identity → 过滤)
  C. 兜底 ②(已知作品专属词 → 过滤)
  D. 兜底 ③(语义自相矛盾 → 过滤)
  E. Action 流程  accept / reject / 已处理 409 / 跨用户 404
  F. Skip 流程    标记 pending → skipped

设计原则:
- 用真实场景数据(《江湖夜雨》李寻欢、《双面镇》陈默等),不用 "test1/test2"
- patched_llm 注入 LLM 输出(测兜底 + API,不消耗 DeepSeek 配额)
- 真实 LLM 行为已在 scripts/verify_prompt.py 验证(本文件不重复跑真实 LLM)
"""
from __future__ import annotations

from fastapi.testclient import TestClient


# ============================================================
# helper:创建项目 + 角色,返回 (project_id, {char_name: char_id})
# ============================================================

def _create_project_with_chars(
    client: TestClient,
    headers: dict,
    project_name: str,
    chars: list[dict],
    project_type: str = "novel",
    tags: list[str] | None = None,
) -> tuple[str, dict[str, str]]:
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": project_name, "type": project_type, "tags": tags or []},
    ).json()
    project_id = p["id"]

    char_ids: dict[str, str] = {}
    for c in chars:
        r = client.post(
            f"/api/projects/{project_id}/characters", headers=headers, json=c,
        ).json()
        char_ids[c["name"]] = r["id"]

    return project_id, char_ids


# ============================================================
# A. 基础 API
# ============================================================

def test_refine_too_few_characters_returns_422(client: TestClient, make_user, patched_llm):
    user = make_user("hero")
    h = user["headers"]
    project_id, _ = _create_project_with_chars(
        client, h, "薄角色项目",
        chars=[{"name": "甲"}, {"name": "乙"}],   # 仅 2 个
    )
    r = client.post(f"/api/projects/{project_id}/refine", headers=h)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "TOO_FEW_CHARACTERS"


def test_refine_success_returns_session_and_refinements(
    client: TestClient, make_user, patched_llm
):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "云端之上", project_type="novel", tags=["科幻"],
        chars=[{"name": "陈默"}, {"name": "林夏"}, {"name": "苏教授"}],
    )
    patched_llm.set_output([{
        "character_id": char_ids["陈默"],
        "suggestion_kind": "personality_补充",
        "suggestion_text": "陈默的性格是空的,显得单薄。建议补:孤傲深沉,藏着旧伤",
        "suggestion_payload": {
            "field": "personality",
            "append": "孤傲深沉,藏着旧伤",
        },
    }])
    r = client.post(f"/api/projects/{project_id}/refine", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"]
    assert len(body["refinements"]) == 1
    assert body["refinements"][0]["character_name"] == "陈默"
    assert body["refinements"][0]["status"] == "pending"
    assert body["stats"]["characters_count"] == 3
    assert body["stats"]["filtered_out"] == 0
    assert body["stats"]["cost_yuan"] >= 0  # 估算后


def test_refine_no_token_returns_401(client: TestClient):
    r = client.post("/api/projects/some-id/refine")
    assert r.status_code == 401


def test_refine_others_project_returns_404(
    client: TestClient, make_user, patched_llm
):
    a = make_user("alpha")
    b = make_user("beta")
    project_id, _ = _create_project_with_chars(
        client, a["headers"], "A 的项目",
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    r = client.post(
        f"/api/projects/{project_id}/refine", headers=b["headers"]
    )
    assert r.status_code == 404


# ============================================================
# B. 兜底 ①:value 覆盖非空 identity → 过滤
# ============================================================

def test_filter_value_overwrites_existing_identity(
    client: TestClient, make_user, patched_llm
):
    """v2 verify 真实 bug 复现:LLM 用 value 覆盖陈默已填的 identity → 必须被过滤掉。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "双面镇",
        chars=[
            {"name": "陈默", "identity": "新生代调查记者,擅长社会工程学"},
            {"name": "林夏"},
            {"name": "苏教授"},
        ],
    )
    patched_llm.set_output([
        # 违规:value 覆盖陈默非空 identity(模式 A)
        {
            "character_id": char_ids["陈默"],
            "suggestion_kind": "identity_补全",
            "suggestion_text": "陈默的身份只有几个字。基于...建议补:深耕科技黑箱的独立调查员",
            "suggestion_payload": {
                "field": "identity",
                "value": "深耕科技黑箱的独立调查员",
            },
        },
        # 合法:append 到林夏 personality
        {
            "character_id": char_ids["林夏"],
            "suggestion_kind": "personality_补充",
            "suggestion_text": "林夏的性格是空的,建议补:活泼健谈但易冲动",
            "suggestion_payload": {
                "field": "personality",
                "append": "活泼健谈但易冲动",
            },
        },
    ])
    r = client.post(f"/api/projects/{project_id}/refine", headers=h)
    body = r.json()
    assert body["stats"]["filtered_out"] == 1, body
    assert len(body["refinements"]) == 1
    assert body["refinements"][0]["character_name"] == "林夏"


def test_value_set_on_empty_identity_passes(
    client: TestClient, make_user, patched_llm
):
    """identity 真为空时,value 可正常通过(不被兜底误伤)。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "空项目",
        chars=[
            {"name": "白名"},   # identity 默认 ""
            {"name": "乙"},
            {"name": "丙"},
        ],
    )
    patched_llm.set_output([{
        "character_id": char_ids["白名"],
        "suggestion_kind": "identity_补全",
        "suggestion_text": "你给白名的身份是空的,建议补:神秘的旅人",
        "suggestion_payload": {
            "field": "identity",
            "value": "神秘的旅人",
        },
    }])
    r = client.post(f"/api/projects/{project_id}/refine", headers=h)
    body = r.json()
    assert body["stats"]["filtered_out"] == 0
    assert len(body["refinements"]) == 1


# ============================================================
# C. 兜底 ②:已知作品专属词 → 过滤
# ============================================================

def test_reserved_term_filtered_when_lixunhuan_present(
    client: TestClient, make_user, patched_llm
):
    """v3 verify 真实 bug 复现:输入李寻欢 + LLM 输出"百晓生" → 必须被过滤。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨", project_type="novel", tags=["武侠"],
        chars=[
            {"name": "李寻欢"},
            {"name": "孙小红"},
            {"name": "上官金虹"},
        ],
    )
    patched_llm.set_output([
        # 违规:含"百晓生"(古龙原作专属词)
        {
            "character_id": char_ids["孙小红"],
            "suggestion_kind": "identity_补全",
            "suggestion_text": "孙小红的身份是空的。建议补:消息灵通的江湖百晓生",
            "suggestion_payload": {
                "field": "identity",
                "value": "消息灵通的江湖百晓生",
            },
        },
        # 合法:无 reserved_term
        {
            "character_id": char_ids["上官金虹"],
            "suggestion_kind": "personality_补充",
            "suggestion_text": "上官金虹的性格是空的,建议补:阴鸷多疑",
            "suggestion_payload": {
                "field": "personality",
                "append": "阴鸷多疑",
            },
        },
    ])
    r = client.post(f"/api/projects/{project_id}/refine", headers=h)
    body = r.json()
    assert body["stats"]["filtered_out"] == 1, body
    assert len(body["refinements"]) == 1
    assert "百晓生" not in str(body["refinements"][0])


def test_reserved_term_not_triggered_when_no_known_name(
    client: TestClient, make_user, patched_llm
):
    """所有角色名都不在 KNOWN_WORK 字典里时,reserved_terms 不触发。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "原创武侠",
        chars=[
            {"name": "江风"},
            {"name": "苏晴"},
            {"name": "黑山老怪"},
        ],
    )
    patched_llm.set_output([{
        "character_id": char_ids["江风"],
        "suggestion_kind": "personality_补充",
        "suggestion_text": "江风性格是空,建议补:擅用飞刀的孤勇者",
        "suggestion_payload": {
            "field": "personality",
            "append": "擅用飞刀的孤勇者",
        },
    }])
    r = client.post(f"/api/projects/{project_id}/refine", headers=h)
    body = r.json()
    assert body["stats"]["filtered_out"] == 0


# ============================================================
# D. 兜底 ③:语义自相矛盾 → 过滤
# ============================================================

def test_self_contradictory_text_when_identity_non_empty(
    client: TestClient, make_user, patched_llm
):
    """suggestion_text 含"是空的"但 char.identity 实际非空 → 过滤。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "矛盾项目",
        chars=[
            {"name": "陈默", "identity": "新生代调查记者"},
            {"name": "乙"},
            {"name": "丙"},
        ],
    )
    patched_llm.set_output([{
        "character_id": char_ids["陈默"],
        "suggestion_kind": "personality_补充",
        "suggestion_text": "陈默的 identity 是空的,建议先补 identity",
        "suggestion_payload": {
            "field": "personality",
            "append": "...",
        },
    }])
    r = client.post(f"/api/projects/{project_id}/refine", headers=h)
    body = r.json()
    assert body["stats"]["filtered_out"] == 1


# ============================================================
# E. Action 流程
# ============================================================

def _refine_one(
    client: TestClient, headers: dict, patched_llm,
    project_id: str, char_id: str,
    payload: dict | None = None,
    suggestion_kind: str = "personality_补充",
) -> str:
    """工具:跑一次 refine,返回第一条 refinement_id。"""
    patched_llm.set_output([{
        "character_id": char_id,
        "suggestion_kind": suggestion_kind,
        "suggestion_text": "测试用建议",
        "suggestion_payload": payload or {"field": "personality", "append": "X"},
    }])
    body = client.post(
        f"/api/projects/{project_id}/refine", headers=headers
    ).json()
    return body["refinements"][0]["id"]


def test_action_accept_appends_to_personality(
    client: TestClient, make_user, patched_llm
):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "项目",
        chars=[
            {"name": "甲", "personality": "内向"},
            {"name": "乙"},
            {"name": "丙"},
        ],
    )
    refinement_id = _refine_one(
        client, h, patched_llm, project_id, char_ids["甲"],
        payload={"field": "personality", "append": "但被惹急时会爆发"},
    )

    r = client.post(
        f"/api/refinements/{refinement_id}/action",
        headers=h, json={"action": "accept"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["applied_to_character"] is True

    char_after = client.get(
        f"/api/characters/{char_ids['甲']}", headers=h
    ).json()
    assert char_after["personality"] == "内向 但被惹急时会爆发"


def test_action_reject_does_not_change_character(
    client: TestClient, make_user, patched_llm
):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "项目",
        chars=[
            {"name": "甲", "personality": "内向"},
            {"name": "乙"},
            {"name": "丙"},
        ],
    )
    refinement_id = _refine_one(
        client, h, patched_llm, project_id, char_ids["甲"],
        payload={"field": "personality", "append": "X"},
    )

    r = client.post(
        f"/api/refinements/{refinement_id}/action",
        headers=h, json={"action": "reject"},
    )
    assert r.status_code == 200
    assert r.json()["applied_to_character"] is False

    char_after = client.get(
        f"/api/characters/{char_ids['甲']}", headers=h
    ).json()
    assert char_after["personality"] == "内向"  # 未动


def test_action_accept_appends_to_quotes(
    client: TestClient, make_user, patched_llm
):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "项目",
        chars=[
            {"name": "甲", "quotes": ["第一句"]},
            {"name": "乙"},
            {"name": "丙"},
        ],
    )
    refinement_id = _refine_one(
        client, h, patched_llm, project_id, char_ids["甲"],
        payload={"field": "quotes", "append": ["新追加的标志性话"]},
        suggestion_kind="quote_补充",
    )

    client.post(
        f"/api/refinements/{refinement_id}/action",
        headers=h, json={"action": "accept"},
    )
    char_after = client.get(
        f"/api/characters/{char_ids['甲']}", headers=h
    ).json()
    assert char_after["quotes"] == ["第一句", "新追加的标志性话"]


def test_action_already_actioned_returns_409(
    client: TestClient, make_user, patched_llm
):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "项目",
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    refinement_id = _refine_one(
        client, h, patched_llm, project_id, char_ids["甲"]
    )
    client.post(
        f"/api/refinements/{refinement_id}/action",
        headers=h, json={"action": "accept"},
    )
    r2 = client.post(
        f"/api/refinements/{refinement_id}/action",
        headers=h, json={"action": "accept"},
    )
    assert r2.status_code == 409
    assert r2.json()["detail"]["code"] == "ALREADY_ACTIONED"


def test_action_others_refinement_returns_404(
    client: TestClient, make_user, patched_llm
):
    a = make_user("alpha")
    b = make_user("beta")
    project_id, char_ids = _create_project_with_chars(
        client, a["headers"], "A 的项目",
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    refinement_id = _refine_one(
        client, a["headers"], patched_llm, project_id, char_ids["甲"]
    )
    r = client.post(
        f"/api/refinements/{refinement_id}/action",
        headers=b["headers"], json={"action": "accept"},
    )
    assert r.status_code == 404


# ============================================================
# F. Skip 流程
# ============================================================

def test_skip_session_marks_pending_skipped(
    client: TestClient, make_user, patched_llm
):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "项目",
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    patched_llm.set_output([
        {
            "character_id": char_ids["甲"],
            "suggestion_kind": "personality_补充",
            "suggestion_text": "建议 1",
            "suggestion_payload": {"field": "personality", "append": "A"},
        },
        {
            "character_id": char_ids["乙"],
            "suggestion_kind": "personality_补充",
            "suggestion_text": "建议 2",
            "suggestion_payload": {"field": "personality", "append": "B"},
        },
    ])
    body = client.post(
        f"/api/projects/{project_id}/refine", headers=h
    ).json()
    session_id = body["session_id"]

    r = client.post(
        f"/api/refine_sessions/{session_id}/skip",
        headers=h, json={"reason": "user_skipped"},
    )
    assert r.status_code == 200
    out = r.json()
    assert out["skipped"] is True
    assert out["remaining_refinements"] == 2


def test_skip_session_already_completed_returns_409(
    client: TestClient, make_user, patched_llm
):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "项目",
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    patched_llm.set_output([{
        "character_id": char_ids["甲"],
        "suggestion_kind": "personality_补充",
        "suggestion_text": "建议",
        "suggestion_payload": {"field": "personality", "append": "X"},
    }])
    session_id = client.post(
        f"/api/projects/{project_id}/refine", headers=h
    ).json()["session_id"]

    client.post(
        f"/api/refine_sessions/{session_id}/skip",
        headers=h, json={"reason": "user_skipped"},
    )
    r2 = client.post(
        f"/api/refine_sessions/{session_id}/skip",
        headers=h, json={"reason": "user_skipped"},
    )
    assert r2.status_code == 409


# ============================================================
# G. LLM 失败处理
# ============================================================

def test_refine_llm_call_failed_returns_503(
    client: TestClient, make_user, patched_llm
):
    """LLM 调用底层失败时,API 返回 503 LLM_UNAVAILABLE。"""
    from app.services.llm_client import LlmCallFailed

    user = make_user("hero")
    h = user["headers"]
    project_id, _ = _create_project_with_chars(
        client, h, "项目",
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    patched_llm.raise_on_call(LlmCallFailed("网络超时"))
    r = client.post(f"/api/projects/{project_id}/refine", headers=h)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "LLM_UNAVAILABLE"


# ============================================================
# G. Sprint 6.A2 FOCUS(2026-05-21):behavior_baseline_补充
#    新增 4 个子字段建议,扩展兜底覆盖
# ============================================================

def test_behavior_baseline_speech_register_value_on_empty_passes(
    client: TestClient, make_user, patched_llm
):
    """speech_register 当前为 null 时,value 模式可正常落库。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "言情项目", project_type="novel", tags=["都市言情"],
        chars=[
            {"name": "白名", "personality": "温柔得体,从不顶撞长辈"},
            {"name": "乙"}, {"name": "丙"},
        ],
    )
    patched_llm.set_output([{
        "character_id": char_ids["白名"],
        "suggestion_kind": "behavior_baseline_补充",
        "suggestion_text": "白名的语气登记空着,基于性格【温柔得体】,建议设为【卑微】",
        "suggestion_payload": {
            "field": "behavior_baseline.speech_register",
            "value": "卑微",
        },
    }])
    r = client.post(f"/api/projects/{project_id}/refine", headers=h)
    body = r.json()
    assert body["stats"]["filtered_out"] == 0
    assert len(body["refinements"]) == 1


def test_behavior_baseline_invalid_enum_filtered(
    client: TestClient, make_user, patched_llm
):
    """LLM 输出非枚举值(如"中等") → 兜底过滤,不落库。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "项目",
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    patched_llm.set_output([{
        "character_id": char_ids["甲"],
        "suggestion_kind": "behavior_baseline_补充",
        "suggestion_text": "甲的语气登记空着,建议设为【中等】",
        "suggestion_payload": {
            "field": "behavior_baseline.speech_register",
            "value": "中等",   # 非枚举白名单
        },
    }])
    r = client.post(f"/api/projects/{project_id}/refine", headers=h)
    body = r.json()
    assert body["stats"]["filtered_out"] == 1
    assert len(body["refinements"]) == 0


def test_behavior_baseline_emotional_intensity_out_of_range_filtered(
    client: TestClient, make_user, patched_llm
):
    """LLM 输出 emotional_intensity 11(超 1-10) → 兜底过滤。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "项目",
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    patched_llm.set_output([{
        "character_id": char_ids["甲"],
        "suggestion_kind": "behavior_baseline_补充",
        "suggestion_text": "甲情绪强度建议设为 11",
        "suggestion_payload": {
            "field": "behavior_baseline.emotional_intensity",
            "value": 11,   # 超 1-10 上限
        },
    }])
    r = client.post(f"/api/projects/{project_id}/refine", headers=h)
    body = r.json()
    assert body["stats"]["filtered_out"] == 1


def test_behavior_baseline_value_covers_existing_filtered(
    client: TestClient, make_user, patched_llm
):
    """speech_register 已填非空时,LLM 再出 value 建议 → 兜底过滤(防覆盖)。"""
    user = make_user("hero")
    h = user["headers"]
    # 用 API 创建角色 + PATCH 行为基线(速度优先,避免直接操 DB)
    project_id, char_ids = _create_project_with_chars(
        client, h, "项目",
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    # 先把甲的 speech_register 写成"强硬"
    rr = client.patch(
        f"/api/characters/{char_ids['甲']}",
        headers=h,
        json={
            "behavior_baseline": {
                "speech_register": "强硬",
                "emotional_intensity": None,
                "moral_compass": None,
                "out_of_baseline_examples": [],
            },
        },
    )
    assert rr.status_code == 200, rr.text

    patched_llm.set_output([{
        "character_id": char_ids["甲"],
        "suggestion_kind": "behavior_baseline_补充",
        "suggestion_text": "甲的语气登记建议改为【卑微】",
        "suggestion_payload": {
            "field": "behavior_baseline.speech_register",
            "value": "卑微",   # 已填"强硬",防覆盖
        },
    }])
    r = client.post(f"/api/projects/{project_id}/refine", headers=h)
    body = r.json()
    assert body["stats"]["filtered_out"] == 1


# P0G.2(2026-05-24):删除 2 个 out_of_baseline_examples 相关测试 case
# - test_behavior_baseline_out_of_baseline_examples_append_passes
# - test_behavior_baseline_out_of_baseline_examples_filter_empty_strings
# 字段 out_of_baseline_examples 已永久删除,这些 case 测的功能不存在
