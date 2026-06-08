"""Sprint 6.A2 路线图 #2.5(2026-05-22)— audit patch 跨会话持久化端到端测试。

覆盖:
  - GET /api/projects/:id/pending_audit_patches
      空项目返 [] / 跨用户 404 / 未采纳不返 / 非 sim_config target 不返 /
      多 issue 按 accepted_at ASC 排序
  - POST /api/projects/:id/audit_patches/mark_applied
      标记后再 list 为空 / 幂等(重复 mark 不报错)/
      未采纳 issue → 422 / audit 跨用户 → 422
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.simulation_service import reshape_to_rounds


# ============================================================
# helpers(复刻 test_audit_acceptor.py 范式,test 文件独立)
# ============================================================

def _create_project_with_chars(client, headers, chars):
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": "patch 测试项目", "type": "novel", "mode": "initial"},
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
            {"name": "上官金虹", "identity": "神秘剑客"},
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
    used = usage or {"input_tokens": 1500, "output_tokens": 500}

    def fake(system_prompt, user_input, **kwargs):
        return output, used

    monkeypatch.setattr("app.services.audit_service.call_llm_json", fake)


def _trigger_audit(client, headers, sim_id, audit_output, monkeypatch):
    _patch_audit_llm(monkeypatch, audit_output)
    r = client.post(f"/api/simulations/{sim_id}/audit", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _two_sim_config_issues():
    """构造 2 个 sim_config target issue,给 audit 用"""
    return [
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
                    "custom_style_hint": "对白短句 + 留白",
                },
            },
        },
        {
            "kind": "turn_jarring",
            "subject_id": None,
            "subject_name": "中段转折",
            "evidence_in_narrative": "转折前后突兀",
            "root_cause_in_setup": "重塑度过高",
            "actionable_fix": "降低 reshape",
            "actionable_fix_payload": {
                "target": "sim_config",
                "patches": {
                    "reshape_percent_delta": -20,
                    "custom_style_hint": "节奏放慢",
                },
            },
        },
    ]


# ============================================================
# A. GET pending_audit_patches
# ============================================================

def test_pending_patches_empty_project_returns_empty_list(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """空项目(无 audit)→ pending list 为空"""
    user = make_user("alice")
    h = user["headers"]
    # 只建项目不跑推演
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "空项目", "type": "novel", "mode": "initial"},
    ).json()
    project_id = p["id"]

    r = client.get(f"/api/projects/{project_id}/pending_audit_patches", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == []


def test_pending_patches_unaccepted_and_non_sim_config_excluded(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """audit 有 4 个 issue:
       - 未采纳 sim_config(不返)
       - 已采纳 sim_config(返)
       - 已采纳 character_thin(target=character,不返)
       - 已采纳但已 applied(不返,在另一个测试覆盖)
       本测试只验证前 3 种过滤
    """
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)
    li_id = char_ids["李寻欢"]

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 60,
        "issues": [
            # [0] 已采纳 sim_config(应该返)
            {
                "kind": "dialogue_flat",
                "subject_id": None,
                "subject_name": "全篇",
                "evidence_in_narrative": "对白都是单句",
                "root_cause_in_setup": "缺口吻",
                "actionable_fix": "调 style hint",
                "actionable_fix_payload": {
                    "target": "sim_config",
                    "patches": {"custom_style_hint": "短句 + 留白"},
                },
            },
            # [1] 未采纳 sim_config(不返)
            {
                "kind": "pacing_off",
                "subject_id": None,
                "subject_name": "节奏",
                "evidence_in_narrative": "节奏拖",
                "root_cause_in_setup": "rounds 过多",
                "actionable_fix": "降 rounds",
                "actionable_fix_payload": {
                    "target": "sim_config",
                    "patches": {"reshape_percent_delta": -10},
                },
            },
            # [2] 已采纳 character_thin(target=character,不返)
            {
                "kind": "character_thin",
                "subject_id": li_id,
                "subject_name": "李寻欢",
                "evidence_in_narrative": "李寻欢只 1 句对白",
                "root_cause_in_setup": "personality 空",
                "actionable_fix": "补 personality",
                "actionable_fix_payload": {
                    "target": "character",
                    "operations": [
                        {"field": "personality", "op": "append", "value": "孤傲"},
                    ],
                },
            },
        ],
        "regenerate_recommendation": "调配置",
    }, monkeypatch)
    audit_id = audit["id"]

    # 采纳 [0] sim_config 和 [2] character_thin
    r0 = client.post(f"/api/audits/{audit_id}/issues/0/accept", headers=h)
    assert r0.status_code == 200, r0.text
    r2 = client.post(f"/api/audits/{audit_id}/issues/2/accept", headers=h)
    assert r2.status_code == 200, r2.text
    # [1] 不采纳

    r = client.get(f"/api/projects/{project_id}/pending_audit_patches", headers=h)
    assert r.status_code == 200, r.text
    pending = r.json()
    # 只有 [0] 应该返(已采纳 + sim_config + 未 applied)
    assert len(pending) == 1
    assert pending[0]["audit_id"] == audit_id
    assert pending[0]["issue_idx"] == 0
    assert pending[0]["source_sim_id"] == sim_id
    assert pending[0]["source_label"] == "对白扁平·全篇"
    assert pending[0]["patches"] == {"custom_style_hint": "短句 + 留白"}


def test_pending_patches_multiple_sorted_by_accepted_at(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """采纳 2 个 sim_config issue → list 返 2 条,按 accepted_at ASC"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 65,
        "issues": _two_sim_config_issues(),
        "regenerate_recommendation": "调",
    }, monkeypatch)
    audit_id = audit["id"]

    # 先采纳 [0],再采纳 [1] — accepted_at 先后顺序应反映在 list 排序里
    client.post(f"/api/audits/{audit_id}/issues/0/accept", headers=h).raise_for_status()
    client.post(f"/api/audits/{audit_id}/issues/1/accept", headers=h).raise_for_status()

    r = client.get(f"/api/projects/{project_id}/pending_audit_patches", headers=h)
    assert r.status_code == 200, r.text
    pending = r.json()
    assert len(pending) == 2
    # 排序:[0] 先采纳,[1] 后采纳
    assert pending[0]["issue_idx"] == 0
    assert pending[0]["source_label"] == "对白扁平·全篇对白"
    assert pending[1]["issue_idx"] == 1
    assert pending[1]["source_label"] == "节奏失衡·中段转折"
    assert pending[0]["accepted_at"] <= pending[1]["accepted_at"]


def test_pending_patches_cross_user_404(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """A 用户访问 B 用户项目的 pending patches → 404"""
    alice = make_user("alice")
    bob = make_user("bob")
    project_id, _, _ = _create_done_sim(
        client, bob["headers"], patched_simulation_llm,
    )

    r = client.get(
        f"/api/projects/{project_id}/pending_audit_patches",
        headers=alice["headers"],
    )
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NOT_FOUND"


# ============================================================
# B. POST mark_applied
# ============================================================

def test_mark_applied_removes_from_pending(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """采纳 → list 有 → mark_applied → list 空"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 65,
        "issues": _two_sim_config_issues(),
        "regenerate_recommendation": "调",
    }, monkeypatch)
    audit_id = audit["id"]
    client.post(f"/api/audits/{audit_id}/issues/0/accept", headers=h).raise_for_status()
    client.post(f"/api/audits/{audit_id}/issues/1/accept", headers=h).raise_for_status()

    # 标记 [0] 已应用
    r = client.post(
        f"/api/projects/{project_id}/audit_patches/mark_applied",
        headers=h,
        json={"items": [{"audit_id": audit_id, "issue_idx": 0}]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["marked"] == 1

    # list 应该只剩 [1]
    pending = client.get(
        f"/api/projects/{project_id}/pending_audit_patches", headers=h,
    ).json()
    assert len(pending) == 1
    assert pending[0]["issue_idx"] == 1

    # 标剩下的 [1]
    r2 = client.post(
        f"/api/projects/{project_id}/audit_patches/mark_applied",
        headers=h,
        json={"items": [{"audit_id": audit_id, "issue_idx": 1}]},
    )
    assert r2.status_code == 200
    assert r2.json()["marked"] == 1

    pending2 = client.get(
        f"/api/projects/{project_id}/pending_audit_patches", headers=h,
    ).json()
    assert pending2 == []


def test_mark_applied_idempotent_on_already_applied(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """重复 mark 同一 issue → 第二次 marked=0(已 applied 跳过不报错)"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 65,
        "issues": _two_sim_config_issues()[:1],
        "regenerate_recommendation": "调",
    }, monkeypatch)
    audit_id = audit["id"]
    client.post(f"/api/audits/{audit_id}/issues/0/accept", headers=h).raise_for_status()

    body = {"items": [{"audit_id": audit_id, "issue_idx": 0}]}
    # 第 1 次:marked = 1
    r1 = client.post(
        f"/api/projects/{project_id}/audit_patches/mark_applied", headers=h, json=body,
    )
    assert r1.status_code == 200
    assert r1.json()["marked"] == 1

    # 第 2 次:marked = 0(已 applied 跳过,不报错)
    r2 = client.post(
        f"/api/projects/{project_id}/audit_patches/mark_applied", headers=h, json=body,
    )
    assert r2.status_code == 200
    assert r2.json()["marked"] == 0


def test_mark_applied_not_accepted_returns_422(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """未采纳的 issue 不能 mark applied → 422"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    audit = _trigger_audit(client, h, sim_id, {
        "overall_score": 65,
        "issues": _two_sim_config_issues()[:1],
        "regenerate_recommendation": "调",
    }, monkeypatch)
    audit_id = audit["id"]
    # 不 accept,直接尝试 mark applied

    r = client.post(
        f"/api/projects/{project_id}/audit_patches/mark_applied",
        headers=h,
        json={"items": [{"audit_id": audit_id, "issue_idx": 0}]},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "ISSUE_NOT_ACCEPTED"
