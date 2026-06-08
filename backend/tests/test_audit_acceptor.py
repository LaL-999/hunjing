"""Sprint 6.A2 M7.C(2026-05-20)— 自洽守护者「采纳」按钮端到端测试。

覆盖:
  A. happy character_thin:append personality + append quotes + append no_go_list
  B. event_inconsistent:append event.description
  C. relationship_off:append description + replace type
  D. 拒绝场景:LLM-only kind / 已 accepted / 无 payload / 越界 / 跨用户
  E. 兜底:identity 已有值 → replace 被 skip / relationship.type 非枚举 → skip
  F. issues_json 持久化 accepted_at 时间戳(用 GET latest 验证)
"""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.db import _connect, fetch_one, transaction, execute as db_execute
from app.services.project_service import iso_now
from app.services.simulation_service import reshape_to_rounds


# ============================================================
# helpers
# ============================================================

def _create_project_with_chars(client, headers, chars):
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": "采纳测试项目", "type": "novel", "mode": "initial"},
    ).json()
    project_id = p["id"]
    char_ids = {}
    for c in chars:
        r = client.post(
            f"/api/projects/{project_id}/characters", headers=headers, json=c,
        ).json()
        char_ids[c["name"]] = r["id"]
    return project_id, char_ids


def _preload_simulation_llm(ctrl, char_ids, reshape_percent=10):
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
    ctrl.text_queue.append("# 测试 narrative\n\n夜色深沉……")


def _create_done_sim(client, headers, patched_simulation_llm):
    project_id, char_ids = _create_project_with_chars(
        client, headers,
        chars=[
            {"name": "李寻欢", "identity": "", "personality": ""},
            {"name": "孙小红", "identity": "江湖义士", "personality": "豪爽"},
            {"name": "上官金虹"},
        ],
    )
    _preload_simulation_llm(patched_simulation_llm, char_ids)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=headers,
        json={"divergence": "如果他不去客栈,而是直奔江畔", "reshape_percent": 10},
    )
    assert r.status_code == 201, r.text
    return project_id, r.json()["simulation_id"], char_ids


def _patch_audit_llm(monkeypatch, output, usage=None):
    used = {"input_tokens": 1500, "output_tokens": 500}
    if usage is not None:
        used = usage
    calls = []

    def fake(system_prompt, user_input, **kwargs):
        calls.append({"user": user_input})
        return output, used

    monkeypatch.setattr("app.services.audit_service.call_llm_json", fake)
    return calls


def _trigger_audit(client, headers, sim_id, audit_output, monkeypatch):
    _patch_audit_llm(monkeypatch, audit_output)
    r = client.post(f"/api/simulations/{sim_id}/audit", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _insert_event(project_id, description):
    """直接 INSERT 一个 event(api 没现成端点 / 不在范围内)。"""
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    event_id = str(uuid.uuid4())
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO events (id, project_id, description, participants, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_id, project_id, description, "[]", iso_now()),
            )
    finally:
        conn.close()
    return event_id


def _insert_relationship(project_id, source_id, target_id, rtype, description=""):
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    rel_id = str(uuid.uuid4())
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO relationships "
                "(id, project_id, source_id, target_id, type, description, "
                " color, strength, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, 'moderate', ?)",
                (rel_id, project_id, source_id, target_id, rtype, description, iso_now()),
            )
    finally:
        conn.close()
    return rel_id


def _get_character(client, headers, char_id):
    r = client.get(f"/api/characters/{char_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _get_event_description(event_id):
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        row = fetch_one(conn, "SELECT description FROM events WHERE id=?", (event_id,))
        return row["description"] if row else None
    finally:
        conn.close()


def _get_relationship(rel_id):
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    try:
        row = fetch_one(
            conn,
            "SELECT type, description FROM relationships WHERE id=?",
            (rel_id,),
        )
        return dict(row) if row else None
    finally:
        conn.close()


# ============================================================
# A. happy character_thin
# ============================================================

def test_accept_character_thin_appends_all_fields(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """character_thin issue 含完整 payload → 应用到 personality + quotes + identity。"""
    user = make_user("alice")
    h = user["headers"]
    _, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    li_id = char_ids["李寻欢"]    # 此角色 identity / personality 都空

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 60,
        "issues": [
            {
                "kind": "character_thin",
                "subject_id": li_id,
                "subject_name": "李寻欢",
                "evidence_in_narrative": "narrative 中李寻欢只说了一句台词",
                "root_cause_in_setup": "「性格」字段为空",
                "actionable_fix": "补充性格 + 加 3 条台词 + 补 identity",
                "actionable_fix_payload": {
                    "target": "character",
                    "operations": [
                        {"field": "identity", "op": "replace",
                         "value": "江湖夜行的浪子,飞刀百晓生唯一传人"},
                        {"field": "personality", "op": "append",
                         "value": "面对危机会先冷静分析三秒再行动,与人辩论时偏好用反问句"},
                        {"field": "quotes", "op": "append",
                         "value": ["饮酒,本就该一醉方休。", "出刀时不要犹豫,犹豫便会败北。"]},
                    ],
                },
            },
        ],
        "regenerate_recommendation": "建议先补完角色再重生成",
    }, monkeypatch)
    audit_id = audit["id"]

    # 调采纳
    r = client.post(
        f"/api/audits/{audit_id}/issues/0/accept", headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "character"
    assert body["subject_id"] == li_id
    assert len(body["operations_applied"]) == 3
    assert body["operations_skipped"] == []
    assert body["accepted_at"]

    # 验证 character 字段已更新
    char = _get_character(client, h, li_id)
    assert char["identity"] == "江湖夜行的浪子,飞刀百晓生唯一传人"
    assert "冷静分析三秒" in char["personality"]
    assert "饮酒,本就该一醉方休。" in char["quotes"]
    assert "出刀时不要犹豫,犹豫便会败北。" in char["quotes"]


# ============================================================
# B. event_inconsistent
# ============================================================

def test_accept_event_inconsistent_appends_description(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    event_id = _insert_event(project_id, "原描述:某夜两人对峙")

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 55,
        "issues": [
            {
                "kind": "event_inconsistent",
                "subject_id": event_id,
                "subject_name": "某夜两人对峙",
                "evidence_in_narrative": "narrative 里两人很客气没有对峙感",
                "root_cause_in_setup": "「事件描述」过于简略,无张力来源",
                "actionable_fix": "在事件描述里写明对峙的具体情境与张力",
                "actionable_fix_payload": {
                    "target": "event",
                    "operations": [
                        {"field": "description", "op": "append",
                         "value": "两人因三年前一次背叛事件结下死结,此次对峙是冰封多年的仇恨爆发点"},
                    ],
                },
            },
        ],
        "regenerate_recommendation": "补完事件细节再生成",
    }, monkeypatch)
    audit_id = audit["id"]

    r = client.post(
        f"/api/audits/{audit_id}/issues/0/accept", headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "event"

    desc = _get_event_description(event_id)
    assert "原描述:某夜两人对峙" in desc
    assert "三年前一次背叛事件" in desc


# ============================================================
# C. relationship_off — append + replace type
# ============================================================

def test_accept_relationship_off_replaces_type_and_appends_desc(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    rel_id = _insert_relationship(
        project_id, char_ids["李寻欢"], char_ids["孙小红"],
        "同事", description="初识",
    )

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 50,
        "issues": [
            {
                "kind": "relationship_off",
                "subject_id": rel_id,
                "subject_name": "李寻欢 & 孙小红",
                "evidence_in_narrative": "narrative 里两人有暧昧细节",
                "root_cause_in_setup": "关系标 同事,与产物里的暧昧不符",
                "actionable_fix": "改关系类型为情侣,描述补暧昧来源",
                "actionable_fix_payload": {
                    "target": "relationship",
                    "operations": [
                        {"field": "type", "op": "replace", "value": "情侣"},
                        {"field": "description", "op": "append",
                         "value": "两人在客栈夜雨那晚互生情愫,但彼此未挑明"},
                    ],
                },
            },
        ],
        "regenerate_recommendation": "改关系定位后重生成",
    }, monkeypatch)
    audit_id = audit["id"]

    r = client.post(
        f"/api/audits/{audit_id}/issues/0/accept", headers=h,
    )
    assert r.status_code == 200, r.text

    rel = _get_relationship(rel_id)
    assert rel["type"] == "情侣"
    assert "初识" in rel["description"]
    assert "客栈夜雨" in rel["description"]


# ============================================================
# D. 拒绝场景
# ============================================================

def test_accept_llm_only_kind_no_payload_returns_no_payload_code(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """M7.K(2026-05-20)— LLM-only kind 无 payload 时返 ISSUE_NO_PAYLOAD(原 ISSUE_NOT_USER_FIXABLE 已废弃)。"""
    user = make_user("alice")
    h = user["headers"]
    _, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 70,
        "issues": [
            {
                "kind": "dialogue_flat",
                "subject_id": None,
                "subject_name": "全篇",
                "evidence_in_narrative": "对白单一",
                "root_cause_in_setup": "无角色独有口吻",
                "actionable_fix": "调语体",
                # 无 actionable_fix_payload → 走 ISSUE_NO_PAYLOAD 兜底
            },
        ],
        "regenerate_recommendation": "调语体后重生成",
    }, monkeypatch)

    r = client.post(
        f"/api/audits/{audit['id']}/issues/0/accept", headers=h,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "ISSUE_NO_PAYLOAD"


def test_accept_llm_only_kind_with_sim_config_patch(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """M7.K(2026-05-20)— LLM-only kind 有 sim_config patch → 200 + 返回 patch + 标 accepted_at。

    覆盖:dialogue_flat(custom_style_hint)+ turn_jarring(reshape_percent_delta)
    """
    user = make_user("alice")
    h = user["headers"]
    _, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 70,
        "issues": [
            {
                "kind": "dialogue_flat",
                "subject_id": None,
                "subject_name": "全篇对白",
                "evidence_in_narrative": "对白几乎都是单句陈述",
                "root_cause_in_setup": "缺角色独有口吻",
                "actionable_fix": "在 custom_style_hint 加描述",
                "actionable_fix_payload": {
                    "target": "sim_config",
                    "patches": {
                        "custom_style_hint": "对白要短句 + 留白 + 偶有方言",
                    },
                },
            },
            {
                "kind": "turn_jarring",
                "subject_id": None,
                "subject_name": "中段转折",
                "evidence_in_narrative": "转折前后两段引用",
                "root_cause_in_setup": "重塑度过高,LLM 跨步太大",
                "actionable_fix": "降低 reshape",
                "actionable_fix_payload": {
                    "target": "sim_config",
                    "patches": {
                        "reshape_percent_delta": -20,
                        "custom_style_hint": "节奏放慢,多铺垫",
                    },
                },
            },
        ],
        "regenerate_recommendation": "调配置重生成",
    }, monkeypatch)
    audit_id = audit["id"]

    # 采纳第 0 条(dialogue_flat)
    r = client.post(f"/api/audits/{audit_id}/issues/0/accept", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target"] == "sim_config"
    assert body["sim_config_patch"]["custom_style_hint"] == "对白要短句 + 留白 + 偶有方言"
    assert body["accepted_at"]
    # operations_applied 为空(sim_config 不动 DB)
    assert body["operations_applied"] == []

    # 采纳第 1 条(turn_jarring,多 field patch)
    r2 = client.post(f"/api/audits/{audit_id}/issues/1/accept", headers=h)
    assert r2.status_code == 200, r2.text
    body2 = r2.json()
    assert body2["sim_config_patch"]["reshape_percent_delta"] == -20
    assert body2["sim_config_patch"]["custom_style_hint"] == "节奏放慢,多铺垫"

    # GET latest:两条都标 accepted_at
    r3 = client.get(f"/api/simulations/{sim_id}/audit/latest", headers=h)
    issues = r3.json()["issues"]
    assert issues[0]["accepted_at"]
    assert issues[1]["accepted_at"]

    # 重复采纳第 0 条 → 422 ISSUE_ALREADY_ACCEPTED
    r4 = client.post(f"/api/audits/{audit_id}/issues/0/accept", headers=h)
    assert r4.status_code == 422
    assert r4.json()["detail"]["code"] == "ISSUE_ALREADY_ACCEPTED"


def test_accept_sim_config_range_clamps_out_of_bounds(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """M7.K(2026-05-20)— sim_config patch 数值字段超 range → 兜底 clamp(audit_service 清洗时)。"""
    user = make_user("alice")
    h = user["headers"]
    _, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 60,
        "issues": [
            {
                "kind": "pacing_off",
                "subject_id": None,
                "subject_name": "节奏",
                "evidence_in_narrative": "中段拖沓",
                "root_cause_in_setup": "字数过多",
                "actionable_fix": "砍字数",
                "actionable_fix_payload": {
                    "target": "sim_config",
                    "patches": {
                        "reshape_percent_delta": 999,        # 超过 +30 上限 → 兜底为 30
                        "target_chars_delta": -100000,       # 超过 -5000 下限 → 兜底为 -5000
                        "custom_style_hint": "a" * 200,      # 超过 100 字 → 截断
                    },
                },
            },
        ],
        "regenerate_recommendation": "rec",
    }, monkeypatch)

    r = client.post(f"/api/audits/{audit['id']}/issues/0/accept", headers=h)
    assert r.status_code == 200, r.text
    patch = r.json()["sim_config_patch"]
    assert patch["reshape_percent_delta"] == 30
    assert patch["target_chars_delta"] == -5000
    assert len(patch["custom_style_hint"]) == 100


def test_accept_rejects_already_accepted(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """同一 issue 第 2 次 accept → 422 ISSUE_ALREADY_ACCEPTED。"""
    user = make_user("alice")
    h = user["headers"]
    _, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    li_id = char_ids["李寻欢"]

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 60,
        "issues": [
            {
                "kind": "character_thin",
                "subject_id": li_id,
                "subject_name": "李寻欢",
                "evidence_in_narrative": "narrative 中李寻欢只说一句",
                "root_cause_in_setup": "「性格」字段为空",
                "actionable_fix": "补充性格",
                "actionable_fix_payload": {
                    "target": "character",
                    "operations": [
                        {"field": "personality", "op": "append", "value": "冷静沉着"},
                    ],
                },
            },
        ],
        "regenerate_recommendation": "rec",
    }, monkeypatch)

    r1 = client.post(f"/api/audits/{audit['id']}/issues/0/accept", headers=h)
    assert r1.status_code == 200

    r2 = client.post(f"/api/audits/{audit['id']}/issues/0/accept", headers=h)
    assert r2.status_code == 422
    assert r2.json()["detail"]["code"] == "ISSUE_ALREADY_ACCEPTED"


def test_accept_rejects_no_payload(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """LLM 没给 payload(老 audit) → 422 ISSUE_NO_PAYLOAD。"""
    user = make_user("alice")
    h = user["headers"]
    _, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 60,
        "issues": [
            {
                "kind": "character_thin",
                "subject_id": char_ids["李寻欢"],
                "subject_name": "李寻欢",
                "evidence_in_narrative": "narrative 中只一句",
                "root_cause_in_setup": "「性格」空",
                "actionable_fix": "补充性格",
                # 没 actionable_fix_payload → 清洗后存 None
            },
        ],
        "regenerate_recommendation": "rec",
    }, monkeypatch)

    r = client.post(f"/api/audits/{audit['id']}/issues/0/accept", headers=h)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "ISSUE_NO_PAYLOAD"


def test_accept_rejects_index_out_of_range(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    user = make_user("alice")
    h = user["headers"]
    _, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 80,
        "issues": [],     # 空 issues
        "regenerate_recommendation": "无需修复",
    }, monkeypatch)

    r = client.post(f"/api/audits/{audit['id']}/issues/0/accept", headers=h)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "ISSUE_INDEX_OUT_OF_RANGE"


def test_accept_rejects_cross_user(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    alice = make_user("alice")
    bob = make_user("bob")
    _, sim_id, char_ids = _create_done_sim(client, alice["headers"], patched_simulation_llm)

    audit = _trigger_audit(client, alice["headers"], sim_id, {
        "overall_score": 60,
        "issues": [
            {
                "kind": "character_thin",
                "subject_id": char_ids["李寻欢"],
                "subject_name": "李寻欢",
                "evidence_in_narrative": "evidence",
                "root_cause_in_setup": "cause",
                "actionable_fix": "fix",
                "actionable_fix_payload": {
                    "target": "character",
                    "operations": [
                        {"field": "personality", "op": "append", "value": "冷静"},
                    ],
                },
            },
        ],
        "regenerate_recommendation": "rec",
    }, monkeypatch)

    r = client.post(
        f"/api/audits/{audit['id']}/issues/0/accept", headers=bob["headers"],
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "AUDIT_NOT_FOUND"


# ============================================================
# E. 兜底过滤
# ============================================================

def test_accept_skips_identity_replace_when_already_filled(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """identity 已有非空值 → replace 被 skip(对齐 refine_service 的保护)。"""
    user = make_user("alice")
    h = user["headers"]
    _, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    sun_id = char_ids["孙小红"]    # 该角色 identity="江湖义士",已填

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 60,
        "issues": [
            {
                "kind": "character_thin",
                "subject_id": sun_id,
                "subject_name": "孙小红",
                "evidence_in_narrative": "narrative 中孙小红表现单薄",
                "root_cause_in_setup": "「性格」过简",
                "actionable_fix": "改身份 + 补性格",
                "actionable_fix_payload": {
                    "target": "character",
                    "operations": [
                        # identity 已有"江湖义士",这条会被 skip
                        {"field": "identity", "op": "replace", "value": "新身份描述"},
                        # personality 可以 append
                        {"field": "personality", "op": "append", "value": "对朋友赤诚,对敌人冷酷"},
                    ],
                },
            },
        ],
        "regenerate_recommendation": "rec",
    }, monkeypatch)

    r = client.post(f"/api/audits/{audit['id']}/issues/0/accept", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert len(body["operations_applied"]) == 1   # 只 personality 应用
    assert body["operations_applied"][0]["field"] == "personality"
    assert len(body["operations_skipped"]) == 1
    assert body["operations_skipped"][0]["field"] == "identity"
    assert body["operations_skipped"][0]["reason"] == "identity_already_filled"

    # identity 没被覆盖
    char = _get_character(client, h, sun_id)
    assert char["identity"] == "江湖义士"
    assert "对朋友赤诚" in char["personality"]


def test_accept_skips_relationship_type_not_in_enum(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """relationship.type 不在枚举内 → skip。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    rel_id = _insert_relationship(
        project_id, char_ids["李寻欢"], char_ids["孙小红"], "同事", "初识",
    )

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 50,
        "issues": [
            {
                "kind": "relationship_off",
                "subject_id": rel_id,
                "subject_name": "rel",
                "evidence_in_narrative": "evidence",
                "root_cause_in_setup": "cause",
                "actionable_fix": "fix",
                "actionable_fix_payload": {
                    "target": "relationship",
                    "operations": [
                        # 非枚举值 → skip
                        {"field": "type", "op": "replace", "value": "灵魂伴侣"},
                        # description append 仍生效
                        {"field": "description", "op": "append", "value": "心灵相通"},
                    ],
                },
            },
        ],
        "regenerate_recommendation": "rec",
    }, monkeypatch)

    r = client.post(f"/api/audits/{audit['id']}/issues/0/accept", headers=h)
    assert r.status_code == 200
    body = r.json()
    skipped_fields = [s["field"] for s in body["operations_skipped"]]
    assert "type" in skipped_fields
    rel = _get_relationship(rel_id)
    assert rel["type"] == "同事"     # 没被改
    assert "心灵相通" in rel["description"]


# ============================================================
# F. accepted_at 持久化 + GET latest 透出
# ============================================================

def test_accepted_at_persists_to_get_latest_audit(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """采纳后 GET latest 应能看到该 issue 的 accepted_at 字段。"""
    user = make_user("alice")
    h = user["headers"]
    _, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    li_id = char_ids["李寻欢"]

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 60,
        "issues": [
            {
                "kind": "character_thin",
                "subject_id": li_id,
                "subject_name": "李寻欢",
                "evidence_in_narrative": "evidence",
                "root_cause_in_setup": "cause",
                "actionable_fix": "fix",
                "actionable_fix_payload": {
                    "target": "character",
                    "operations": [
                        {"field": "personality", "op": "append", "value": "冷静"},
                    ],
                },
            },
            {
                "kind": "character_thin",
                "subject_id": char_ids["孙小红"],
                "subject_name": "孙小红",
                "evidence_in_narrative": "evidence2",
                "root_cause_in_setup": "cause2",
                "actionable_fix": "fix2",
                "actionable_fix_payload": {
                    "target": "character",
                    "operations": [
                        {"field": "personality", "op": "append", "value": "豪爽"},
                    ],
                },
            },
        ],
        "regenerate_recommendation": "rec",
    }, monkeypatch)
    audit_id = audit["id"]

    # 采纳第 0 条
    client.post(f"/api/audits/{audit_id}/issues/0/accept", headers=h)

    # GET latest → 第 0 条有 accepted_at,第 1 条没有
    r = client.get(f"/api/simulations/{sim_id}/audit/latest", headers=h)
    assert r.status_code == 200
    body = r.json()
    issues = body["issues"]
    assert issues[0]["accepted_at"] is not None
    assert issues[1]["accepted_at"] is None
