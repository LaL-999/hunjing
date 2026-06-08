"""Audit 服务端到端测试 — Sprint 1.R 自洽守护者验收基线。

测试分组:
  A. 基础 API:happy / sim_not_done(422)/ 跨用户(404)/ 401
  B. LLM 兜底:非法 kind 过滤 / 缺 evidence 过滤 / 超 8 条截断 / 顶层非 dict 报 503
  C. Latest 端点:存在返回最新 / 不存在 404 / 多次 audit 取最新
  D. 鉴权边界

设计原则:
- 复用 _create_project_with_chars + _preload_minimal_simulation_llm + sync_simulation_runner
  快速造一条 done sim 作为 audit 的输入
- 用 monkeypatch 注入 audit_service.call_llm_json,与 simulation/refine 的 patch 隔离
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.simulation_service import reshape_to_rounds


# ============================================================
# helpers
# ============================================================

def _create_project_with_chars(
    client: TestClient,
    headers: dict,
    project_name: str,
    chars: list[dict],
) -> tuple[str, dict[str, str]]:
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": project_name, "type": "novel", "tags": []},
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
            "location": f"客栈大堂(第 {r} 轮)",
            "time_advance": "片刻后",
            "round_seed": f"第 {r} 轮契机",
            "narrator_note": "灯火摇曳",
        })
        ctrl.json_queue.append({
            "monologue": "心下一沉",
            "action": "缓缓抬眼",
            "dialogue": f"你来了——这是第 {r} 轮的对白。",
        })
    ctrl.text_queue.append(
        "# 江湖夜雨\n\n夜色深沉,风穿堂过,客栈灯火摇曳……(测试 narrative)"
    )


def _create_done_sim(
    client: TestClient, headers: dict, patched_simulation_llm,
) -> tuple[str, dict[str, str]]:
    """造一个 state=done 的 sim,返回 (sim_id, char_ids)。"""
    project_id, char_ids = _create_project_with_chars(
        client, headers, "江湖夜雨",
        chars=[
            {"name": "李寻欢", "identity": "江湖浪子", "personality": "豪爽仗义"},
            {"name": "孙小红", "identity": "江湖义士"},
            {"name": "上官金虹", "identity": "神秘剑客"},
        ],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=headers,
        json={"divergence": "如果初见不在客栈而在江畔", "reshape_percent": 10},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "done"
    return body["simulation_id"], char_ids


def _patch_audit_llm(monkeypatch, output, raises=None, usage=None):
    """注入 audit_service.call_llm_json。

    output:LLM 解析后的 dict(audit prompt 输出);raises 优先生效抛异常。
    """
    used = {"input_tokens": 1500, "output_tokens": 500}
    if usage is not None:
        used = usage
    calls: list[dict] = []

    def fake(system_prompt, user_input, **kwargs):
        calls.append({"system_len": len(system_prompt), "user": user_input})
        if raises is not None:
            raise raises
        return output, used

    monkeypatch.setattr("app.services.audit_service.call_llm_json", fake)
    return calls


# ============================================================
# A. 基础 API
# ============================================================

def test_audit_happy_path_returns_full_response(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    user = make_user("hero")
    h = user["headers"]
    sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)

    li_id = char_ids["李寻欢"]
    audit_output = {
        "overall_score": 65,
        "issues": [
            {
                "kind": "character_thin",
                "subject_id": li_id,
                "subject_name": "李寻欢",
                "evidence_in_narrative": "narrative 中李寻欢只有 1 句对白",
                "root_cause_in_setup": "personality 字段过于简短",
                "actionable_fix": "补 50-100 字内心特征,再加 3 条带情绪的 quotes",
            },
            {
                "kind": "dialogue_flat",
                "subject_id": None,
                "subject_name": "全篇",
                "evidence_in_narrative": "对白几乎都是单句陈述",
                "root_cause_in_setup": "无角色独有口吻",
                "actionable_fix": "在续写设置 dock 加 custom_style_hint",
            },
        ],
        "regenerate_recommendation": "建议先补完李寻欢的设定再重生成",
    }
    _patch_audit_llm(monkeypatch, audit_output)

    r = client.post(f"/api/simulations/{sim_id}/audit", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["simulation_id"] == sim_id
    assert body["overall_score"] == 65
    assert len(body["issues"]) == 2
    assert body["issues"][0]["kind"] == "character_thin"
    assert body["issues"][0]["subject_id"] == li_id
    assert body["issues"][1]["kind"] == "dialogue_flat"
    assert body["issues"][1]["subject_id"] is None
    assert body["regenerate_recommendation"].startswith("建议先补完")
    assert body["cost_yuan"] >= 0


def test_audit_translates_english_field_names_to_chinese(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """LLM 漏的英文字段名(personality / quotes / no_go_list / divergence 等)→
    backend 翻译网兜底替换为「中文」,前端永远只看到中文字段名。"""
    user = make_user("hero")
    h = user["headers"]
    sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)

    _patch_audit_llm(monkeypatch, {
        "overall_score": 60,
        "issues": [
            {
                "kind": "character_thin",
                "subject_id": char_ids["李寻欢"],
                "subject_name": "李寻欢",
                "evidence_in_narrative": "narrative 中李寻欢只说了一句话",
                "root_cause_in_setup": "personality 字段空,quotes 也只有 1 条",
                "actionable_fix": "在 personality 写 50 字,加 3 条 quotes,补 no_go_list",
            },
        ],
        "regenerate_recommendation": "先补完 personality 再重生成,或调 divergence 措辞",
    })

    r = client.post(f"/api/simulations/{sim_id}/audit", headers=h)
    assert r.status_code == 200
    issue = r.json()["issues"][0]
    # 不应出现英文 key
    assert "personality" not in issue["root_cause_in_setup"]
    assert "quotes" not in issue["root_cause_in_setup"]
    assert "personality" not in issue["actionable_fix"]
    assert "no_go_list" not in issue["actionable_fix"]
    # 应出现中文翻译
    assert "「性格」" in issue["root_cause_in_setup"]
    assert "「台词」" in issue["root_cause_in_setup"]
    assert "「性格」" in issue["actionable_fix"]
    assert "「禁忌」" in issue["actionable_fix"]
    # recommendation 也走翻译
    rec = r.json()["regenerate_recommendation"]
    assert "personality" not in rec
    assert "divergence" not in rec
    assert "「性格」" in rec
    assert "「剧情锚点」" in rec


def test_audit_sim_not_done_returns_422(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """造一个 failed sim → audit 应该 422 不让诊断。"""
    from app.services.llm_client import LlmCallFailed
    user = make_user("hero")
    h = user["headers"]
    project_id, _ = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[
            {"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"},
        ],
    )
    # director LLM 直接抛异常 → sim 落 failed
    patched_simulation_llm.json_queue.append(LlmCallFailed("director 模型挂了"))
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果探春那一巴掌没打下去", "reshape_percent": 10},
    )
    assert r.status_code == 201
    sim_id = r.json()["simulation_id"]

    # 这条 sim 状态是 failed,audit 应该拒
    r = client.post(f"/api/simulations/{sim_id}/audit", headers=h)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "SIMULATION_NOT_AUDITABLE"


def test_audit_unauthenticated_returns_401(client: TestClient):
    r = client.post("/api/simulations/some-id/audit")
    assert r.status_code == 401


def test_audit_cross_user_returns_404(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    alice = make_user("alice")
    bob = make_user("bob")
    sim_id, _ = _create_done_sim(client, alice["headers"], patched_simulation_llm)

    _patch_audit_llm(monkeypatch, {"overall_score": 80, "issues": [], "regenerate_recommendation": ""})
    r = client.post(f"/api/simulations/{sim_id}/audit", headers=bob["headers"])
    assert r.status_code == 404


# ============================================================
# B. LLM 兜底过滤
# ============================================================

def test_audit_filters_invalid_kind(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    user = make_user("hero")
    h = user["headers"]
    sim_id, char_ids = _create_done_sim(client, h, patched_simulation_llm)

    # LLM 输出含一个非法 kind,应被过滤掉
    _patch_audit_llm(monkeypatch, {
        "overall_score": 70,
        "issues": [
            {
                "kind": "character_thin",
                "subject_id": char_ids["李寻欢"],
                "subject_name": "李寻欢",
                "evidence_in_narrative": "evidence ok",
                "root_cause_in_setup": "rc",
                "actionable_fix": "fix",
            },
            {
                "kind": "made_up_kind",   # 非法
                "subject_id": None,
                "subject_name": "x",
                "evidence_in_narrative": "y",
                "root_cause_in_setup": "z",
                "actionable_fix": "w",
            },
        ],
        "regenerate_recommendation": "ok",
    })

    r = client.post(f"/api/simulations/{sim_id}/audit", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert len(body["issues"]) == 1
    assert body["issues"][0]["kind"] == "character_thin"


def test_audit_filters_issue_missing_evidence(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """evidence_in_narrative 缺失 → 视为编造,过滤。"""
    user = make_user("hero")
    h = user["headers"]
    sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    _patch_audit_llm(monkeypatch, {
        "overall_score": 70,
        "issues": [
            {
                "kind": "dialogue_flat",
                "subject_id": None,
                "subject_name": "全篇",
                "evidence_in_narrative": "",   # 空 → 过滤
                "root_cause_in_setup": "rc",
                "actionable_fix": "fix",
            },
        ],
        "regenerate_recommendation": "ok",
    })
    r = client.post(f"/api/simulations/{sim_id}/audit", headers=h)
    assert r.status_code == 200
    assert len(r.json()["issues"]) == 0


def test_audit_caps_at_8_issues(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """LLM 输出 12 条 → 截到 8 条(prompt 铁律 3)。"""
    user = make_user("hero")
    h = user["headers"]
    sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    issues_12 = [
        {
            "kind": "dialogue_flat",
            "subject_id": None,
            "subject_name": f"段 {i}",
            "evidence_in_narrative": f"句子 {i}",
            "root_cause_in_setup": "rc",
            "actionable_fix": "fix",
        }
        for i in range(12)
    ]
    _patch_audit_llm(monkeypatch, {
        "overall_score": 50,
        "issues": issues_12,
        "regenerate_recommendation": "重生成",
    })
    r = client.post(f"/api/simulations/{sim_id}/audit", headers=h)
    assert r.status_code == 200
    assert len(r.json()["issues"]) == 8


def test_audit_score_clamped_to_range(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    user = make_user("hero")
    h = user["headers"]
    sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    # LLM 输出 999 分 → 夹到 100
    _patch_audit_llm(monkeypatch, {
        "overall_score": 999,
        "issues": [],
        "regenerate_recommendation": "完美",
    })
    r = client.post(f"/api/simulations/{sim_id}/audit", headers=h)
    assert r.status_code == 200
    assert r.json()["overall_score"] == 100


def test_audit_top_level_non_dict_returns_503(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """LLM 返回 list 而不是 dict → 503 LLM_OUTPUT_INVALID。"""
    user = make_user("hero")
    h = user["headers"]
    sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    _patch_audit_llm(monkeypatch, ["not", "a", "dict"])
    r = client.post(f"/api/simulations/{sim_id}/audit", headers=h)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "LLM_OUTPUT_INVALID"


def test_audit_llm_call_failure_returns_503(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    from app.services.llm_client import LlmCallFailed
    user = make_user("hero")
    h = user["headers"]
    sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    _patch_audit_llm(monkeypatch, None, raises=LlmCallFailed("audit LLM 挂了"))
    r = client.post(f"/api/simulations/{sim_id}/audit", headers=h)
    assert r.status_code == 503
    assert r.json()["detail"]["code"] == "LLM_UNAVAILABLE"


# ============================================================
# C. Latest 端点
# ============================================================

def test_get_latest_audit_returns_404_when_no_audit(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    user = make_user("hero")
    h = user["headers"]
    sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)
    r = client.get(f"/api/simulations/{sim_id}/audit/latest", headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "NO_AUDIT_YET"


def test_get_latest_audit_returns_most_recent(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
    monkeypatch,
):
    """同一 sim 跑 2 次 audit,latest 返回最新那条。"""
    import time
    user = make_user("hero")
    h = user["headers"]
    sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)

    # 第 1 次:score 50
    _patch_audit_llm(monkeypatch, {
        "overall_score": 50, "issues": [], "regenerate_recommendation": "first",
    })
    r1 = client.post(f"/api/simulations/{sim_id}/audit", headers=h)
    assert r1.status_code == 200
    assert r1.json()["overall_score"] == 50

    # 等 10ms 确保 triggered_at 不同(SQLite ISO 文本字段排序)
    time.sleep(0.01)

    # 第 2 次:score 80
    _patch_audit_llm(monkeypatch, {
        "overall_score": 80, "issues": [], "regenerate_recommendation": "second",
    })
    r2 = client.post(f"/api/simulations/{sim_id}/audit", headers=h)
    assert r2.status_code == 200
    assert r2.json()["overall_score"] == 80

    # latest 应该是第 2 次
    r3 = client.get(f"/api/simulations/{sim_id}/audit/latest", headers=h)
    assert r3.status_code == 200
    assert r3.json()["overall_score"] == 80
    assert r3.json()["regenerate_recommendation"] == "second"


def test_get_latest_audit_cross_user_returns_404(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    alice = make_user("alice")
    bob = make_user("bob")
    sim_id, _ = _create_done_sim(client, alice["headers"], patched_simulation_llm)
    r = client.get(f"/api/simulations/{sim_id}/audit/latest", headers=bob["headers"])
    assert r.status_code == 404
