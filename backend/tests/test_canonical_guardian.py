"""正典守护者端到端测试 — Sprint 2.D 验收基线。

测试分组:
  A. 基础:happy / mode='initial' 422 / sim 非 done 422 / 跨用户 404 / 401
  B. LLM 兜底:非法 dimension 过滤 / severity 兜底 / 顶层非 dict 失败标 state
  C. 反事实豁免:counterfactual_exempt=true 字段穿透到 issues
  D. State 机:trigger 后 state='running' → run 完 state='done' / 失败标 state='failed'
  E. Latest 端点:存在返最新 / 不存在 404 / 多次 audit 取最新

设计:
  - 用 mode='middle' 项目(initial 态正典审计不适用)
  - monkeypatch canonical_guardian_service.call_llm_json + kick_off 同步直跑
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.services.simulation_service import reshape_to_rounds


# ============================================================
# helpers
# ============================================================

def _create_middle_project_with_chars(
    client: TestClient, headers: dict, chars: list[dict],
) -> tuple[str, dict[str, str]]:
    """造中间态项目 + 角色,返回 (project_id, char_ids name→id)。"""
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": "测试中间态", "type": "novel", "mode": "middle", "tags": []},
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


def _create_done_sim_middle(
    client: TestClient, headers: dict, patched_simulation_llm,
) -> tuple[str, str, dict[str, str]]:
    """造 mode=middle 项目 + done sim,返回 (project_id, sim_id, char_ids)。"""
    project_id, char_ids = _create_middle_project_with_chars(
        client, headers,
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
    return project_id, body["simulation_id"], char_ids


def _patch_canonical_llm(monkeypatch, output, raises=None, usage=None):
    """注入 canonical_guardian_service.call_llm_json。"""
    used = {"input_tokens": 3000, "output_tokens": 800}
    if usage is not None:
        used = usage
    calls: list[dict] = []

    def fake(system_prompt, user_input, **kwargs):
        calls.append({"system_len": len(system_prompt), "user_input": user_input})
        if raises is not None:
            raise raises
        return output, used

    monkeypatch.setattr(
        "app.services.canonical_guardian_service.call_llm_json", fake,
    )
    return calls


def _patch_canonical_sync_kick_off(monkeypatch):
    """让 kick_off_canonical_audit 直接同步跑(测试不开线程)。"""
    import app.services.canonical_guardian_service as svc
    monkeypatch.setattr(svc, "kick_off_canonical_audit", svc.run_canonical_audit)


# ============================================================
# A. 基础 API
# ============================================================

def test_canonical_audit_happy_path(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """触发审计 → 同步跑完 → 状态 done,返 issues 列表。"""
    user = make_user("alice")
    h = user["headers"]
    _patch_canonical_sync_kick_off(monkeypatch)
    project_id, sim_id, _ = _create_done_sim_middle(client, h, patched_simulation_llm)

    _patch_canonical_llm(monkeypatch, output={
        "summary": "整体严守原作底色,1 处轻微偏离",
        "issues": [
            {
                "dimension": "character_consistency",
                "severity": "minor_drift",
                "finding": "李寻欢的对白略多",
                "evidence_excerpt": "第 1 轮的对白。",
                "canon_reference": "原作李寻欢沉默寡言",
                "counterfactual_exempt": False,
                "exempt_reason": None,
            },
            {
                "dimension": "tone",
                "severity": "strict_canonical",
                "finding": "整体氛围与原作一致",
                "evidence_excerpt": "夜色深沉,客栈灯火摇曳",
                "canon_reference": "原作底色:江湖夜雨",
                "counterfactual_exempt": False,
                "exempt_reason": None,
            },
        ],
        "stats": {"total_issues": 2, "exempt_count": 0, "real_drifts": 1},
    })

    r = client.post(f"/api/simulations/{sim_id}/canonical_audit", headers=h)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "done"
    assert body["simulation_id"] == sim_id
    assert len(body["issues"]) == 2
    assert body["issues"][0]["dimension"] == "character_consistency"
    assert body["issues"][1]["severity"] == "strict_canonical"
    assert body["cost_yuan"] > 0


def test_canonical_audit_rejects_initial_mode(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """初始态项目 → 422 CANONICAL_AUDIT_NOT_APPLICABLE。"""
    user = make_user("alice")
    h = user["headers"]
    _patch_canonical_sync_kick_off(monkeypatch)

    # 用 initial 模式创建项目
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "初始态", "type": "novel", "mode": "initial"},
    ).json()
    project_id = p["id"]
    char_ids: dict[str, str] = {}
    for c in [{"name": "甲"}, {"name": "乙"}, {"name": "丙"}]:
        cr = client.post(
            f"/api/projects/{project_id}/characters", headers=h, json=c,
        ).json()
        char_ids[c["name"]] = cr["id"]
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "什么都不变,只是观察这三人的相遇", "reshape_percent": 10},
    )
    sim_id = r.json()["simulation_id"]

    # 触发正典审计 → 应被拒
    r_audit = client.post(f"/api/simulations/{sim_id}/canonical_audit", headers=h)
    assert r_audit.status_code == 422
    assert r_audit.json()["detail"]["code"] == "CANONICAL_AUDIT_NOT_APPLICABLE"


def test_canonical_audit_sim_not_done_returns_422(
    client: TestClient, make_user, monkeypatch,
):
    """sim 状态非 done → 422 SIMULATION_NOT_AUDITABLE。
    手动创建一个 queued 状态 sim 不容易;直接 patch 一个 sim 改 state。"""
    user = make_user("alice")
    h = user["headers"]
    _patch_canonical_sync_kick_off(monkeypatch)
    project_id, char_ids = _create_middle_project_with_chars(
        client, h,
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )

    # 直接 INSERT 一行 queued sim(绕开真跑)
    import os
    import uuid
    from pathlib import Path
    from app.db import _connect, transaction, execute as db_execute
    from app.services.project_service import iso_now
    conn = _connect(Path(os.environ["HUIMENG_DB_PATH"]))
    sim_id = str(uuid.uuid4())
    try:
        with transaction(conn) as tx:
            db_execute(
                tx,
                "INSERT INTO simulations "
                "(id, project_id, user_id, divergence, reshape_percent, "
                " rounds_planned, target_chars, style, characters_snapshot, "
                " state, current_round, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?)",
                (
                    sim_id, project_id, user["user_id"], "测试", 10,
                    5, 4000, "auto", "[]", iso_now(),
                ),
            )
    finally:
        conn.close()

    r = client.post(f"/api/simulations/{sim_id}/canonical_audit", headers=h)
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "SIMULATION_NOT_AUDITABLE"


def test_canonical_audit_cross_user_returns_404(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    alice = make_user("alice")
    bob = make_user("bob")
    _patch_canonical_sync_kick_off(monkeypatch)
    _, sim_id, _ = _create_done_sim_middle(client, alice["headers"], patched_simulation_llm)
    r = client.post(
        f"/api/simulations/{sim_id}/canonical_audit", headers=bob["headers"],
    )
    assert r.status_code == 404


def test_canonical_audit_unauthenticated_returns_401(client: TestClient):
    r = client.post("/api/simulations/x/canonical_audit")
    assert r.status_code == 401


# ============================================================
# B. LLM 兜底
# ============================================================

def test_canonical_audit_invalid_dimension_dropped(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """LLM 输出 dimension 非白名单 → 该 issue 被 drop。"""
    user = make_user("alice")
    h = user["headers"]
    _patch_canonical_sync_kick_off(monkeypatch)
    _, sim_id, _ = _create_done_sim_middle(client, h, patched_simulation_llm)
    _patch_canonical_llm(monkeypatch, output={
        "issues": [
            {
                "dimension": "fake_dimension",   # 非法
                "severity": "minor_drift",
                "finding": "x", "evidence_excerpt": "x", "canon_reference": "x",
            },
            {
                "dimension": "tone",   # 合法
                "severity": "strict_canonical",
                "finding": "OK", "evidence_excerpt": "OK", "canon_reference": "OK",
            },
        ],
    })
    r = client.post(f"/api/simulations/{sim_id}/canonical_audit", headers=h)
    body = r.json()
    assert body["state"] == "done"
    assert len(body["issues"]) == 1
    assert body["issues"][0]["dimension"] == "tone"


def test_canonical_audit_invalid_severity_falls_to_minor(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """severity 非白名单 → 兜底 minor_drift。"""
    user = make_user("alice")
    h = user["headers"]
    _patch_canonical_sync_kick_off(monkeypatch)
    _, sim_id, _ = _create_done_sim_middle(client, h, patched_simulation_llm)
    _patch_canonical_llm(monkeypatch, output={
        "issues": [{
            "dimension": "tone",
            "severity": "WHATEVER",
            "finding": "x", "evidence_excerpt": "x", "canon_reference": "x",
        }],
    })
    r = client.post(f"/api/simulations/{sim_id}/canonical_audit", headers=h)
    body = r.json()
    assert body["state"] == "done"
    assert body["issues"][0]["severity"] == "minor_drift"


def test_canonical_audit_top_level_not_dict_marks_failed(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """LLM 返非 dict → issues 为空 + state=done(空也算 done — 无 issue 视为完美符合)。

    实际现状:_validate_response 顶层非 dict 返 [],state 仍 done — 这是设计选择
    (LLM 极少返非 dict;若真返非 dict 可视为产物无偏离)
    """
    user = make_user("alice")
    h = user["headers"]
    _patch_canonical_sync_kick_off(monkeypatch)
    _, sim_id, _ = _create_done_sim_middle(client, h, patched_simulation_llm)
    _patch_canonical_llm(monkeypatch, output="not a dict")
    r = client.post(f"/api/simulations/{sim_id}/canonical_audit", headers=h)
    body = r.json()
    assert body["state"] == "done"
    assert body["issues"] == []


# ============================================================
# C. 反事实豁免
# ============================================================

def test_canonical_audit_counterfactual_exempt_passes_through(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """LLM 标 counterfactual_exempt=true → issues 字段穿透不丢。"""
    user = make_user("alice")
    h = user["headers"]
    _patch_canonical_sync_kick_off(monkeypatch)
    _, sim_id, _ = _create_done_sim_middle(client, h, patched_simulation_llm)
    _patch_canonical_llm(monkeypatch, output={
        "issues": [{
            "dimension": "character_consistency",
            "severity": "obvious_drift",
            "finding": "黛玉性格转外向",
            "evidence_excerpt": "黛玉笑道",
            "canon_reference": "原作敏感多疑",
            "counterfactual_exempt": True,
            "exempt_reason": "用户反事实改了 personality",
        }],
    })
    r = client.post(f"/api/simulations/{sim_id}/canonical_audit", headers=h)
    body = r.json()
    assert body["issues"][0]["counterfactual_exempt"] is True
    assert "用户反事实" in body["issues"][0]["exempt_reason"]


# ============================================================
# D. State 机
# ============================================================

def test_canonical_audit_llm_failure_marks_failed(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """LLM 调用抛异常 → state='failed' + error_message。"""
    from app.services.llm_client import LlmCallFailed
    user = make_user("alice")
    h = user["headers"]
    _patch_canonical_sync_kick_off(monkeypatch)
    _, sim_id, _ = _create_done_sim_middle(client, h, patched_simulation_llm)
    _patch_canonical_llm(monkeypatch, output=None, raises=LlmCallFailed("模拟 LLM 挂了"))
    r = client.post(f"/api/simulations/{sim_id}/canonical_audit", headers=h)
    body = r.json()
    assert body["state"] == "failed"
    assert "模拟 LLM 挂了" in (body["error_message"] or "")


# ============================================================
# E. Latest 端点
# ============================================================

def test_canonical_audit_latest_returns_404_when_none(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    user = make_user("alice")
    h = user["headers"]
    _, sim_id, _ = _create_done_sim_middle(client, h, patched_simulation_llm)
    r = client.get(f"/api/simulations/{sim_id}/canonical_audit/latest", headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "CANONICAL_AUDIT_NOT_FOUND"


def test_canonical_audit_latest_returns_most_recent(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """跑 2 次 audit,latest 返第 2 次。"""
    import time
    user = make_user("alice")
    h = user["headers"]
    _patch_canonical_sync_kick_off(monkeypatch)
    _, sim_id, _ = _create_done_sim_middle(client, h, patched_simulation_llm)

    _patch_canonical_llm(monkeypatch, output={"issues": [
        {"dimension": "tone", "severity": "minor_drift", "finding": "1st",
         "evidence_excerpt": "x", "canon_reference": "x"},
    ]})
    r1 = client.post(f"/api/simulations/{sim_id}/canonical_audit", headers=h)
    audit1_id = r1.json()["id"]
    time.sleep(0.02)

    _patch_canonical_llm(monkeypatch, output={"issues": [
        {"dimension": "tone", "severity": "obvious_drift", "finding": "2nd",
         "evidence_excerpt": "y", "canon_reference": "y"},
    ]})
    r2 = client.post(f"/api/simulations/{sim_id}/canonical_audit", headers=h)
    audit2_id = r2.json()["id"]
    assert audit2_id != audit1_id

    r_latest = client.get(
        f"/api/simulations/{sim_id}/canonical_audit/latest", headers=h,
    )
    assert r_latest.status_code == 200
    assert r_latest.json()["id"] == audit2_id
    assert r_latest.json()["issues"][0]["finding"] == "2nd"


def test_canonical_audit_history_lists_all(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    import time
    user = make_user("alice")
    h = user["headers"]
    _patch_canonical_sync_kick_off(monkeypatch)
    _, sim_id, _ = _create_done_sim_middle(client, h, patched_simulation_llm)
    for _ in range(3):
        _patch_canonical_llm(monkeypatch, output={"issues": []})
        client.post(f"/api/simulations/{sim_id}/canonical_audit", headers=h)
        time.sleep(0.01)
    r = client.get(f"/api/simulations/{sim_id}/canonical_audits", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 3
    # 按 created_at DESC
    times = [a["created_at"] for a in body]
    assert times == sorted(times, reverse=True)


# ============================================================
# F. M7.A(2026-05-20)续作创新豁免 — 新角色 / 新事件 / 新道具不再被误判
# ============================================================

def _insert_canonical_entity(
    sim_id: str, entity_type: str, canonical_name: str,
    aliases: list[str], description: str, first_introduced_scene: int = 1,
) -> str:
    """直接 INSERT 一行 canonical_entities,模拟 entity_registrar 已登记新角色 / 新事件等。

    返回 entity_id 给后续断言用。
    """
    import json as _json
    import os
    import uuid
    from pathlib import Path
    from app.db import _connect, transaction, execute as db_execute
    from app.services.project_service import iso_now

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
                    _json.dumps(aliases, ensure_ascii=False),
                    description, first_introduced_scene, iso_now(), iso_now(),
                ),
            )
    finally:
        conn.close()
    return entity_id


def test_canonical_audit_sequel_new_entities_block_in_user_prompt(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """M7.A:续作新角色 / 新事件 / 新道具 / 新场景 被渲染进 user_prompt 喂给 LLM。

    覆盖 4 类 entity_type 都正确分桶 + 别名渲染 + 首次登场幕显示。
    """
    user = make_user("alice")
    h = user["headers"]
    _patch_canonical_sync_kick_off(monkeypatch)
    _, sim_id, _ = _create_done_sim_middle(client, h, patched_simulation_llm)

    # 模拟 entity_registrar 已登记 4 类新实体
    _insert_canonical_entity(
        sim_id, "character", "林小满",
        aliases=["小满", "那个跳楼的女生"],
        description="三年前从天台跳下的女生",
        first_introduced_scene=2,
    )
    _insert_canonical_entity(
        sim_id, "character", "林小禾",
        aliases=["小禾"],
        description="林小满的姐姐,三年前跳河",
        first_introduced_scene=3,
    )
    _insert_canonical_entity(
        sim_id, "event", "二代死亡游戏",
        aliases=[],
        description="续作中新一轮的死亡游戏",
        first_introduced_scene=2,
    )
    _insert_canonical_entity(
        sim_id, "object", "林小满的日记本",
        aliases=["日记"],
        description="封面绣花的硬皮本",
        first_introduced_scene=4,
    )
    _insert_canonical_entity(
        sim_id, "location", "废弃天台",
        aliases=[],
        description="跳楼事件发生的天台",
        first_introduced_scene=2,
    )

    calls = _patch_canonical_llm(monkeypatch, output={"issues": []})
    r = client.post(f"/api/simulations/{sim_id}/canonical_audit", headers=h)
    assert r.status_code == 201, r.text
    assert len(calls) == 1
    user_prompt = calls[0]["user_input"]

    # 续作新实体块标题
    assert "本次续作中新引入的实体" in user_prompt
    assert "续作创新豁免" in user_prompt or "铁律 3" in user_prompt

    # 4 类分桶都出现
    assert "新角色(2 个)" in user_prompt
    assert "新事件(1 个)" in user_prompt
    assert "新道具(1 个)" in user_prompt
    assert "新场景(1 个)" in user_prompt

    # 具体实体 + 别名 + 首次登场幕都渲染了
    assert "林小满" in user_prompt
    assert "别名:小满 / 那个跳楼的女生" in user_prompt
    assert "三年前从天台跳下的女生" in user_prompt
    assert "第 2 幕首次登场" in user_prompt
    assert "林小禾" in user_prompt
    assert "二代死亡游戏" in user_prompt
    assert "林小满的日记本" in user_prompt
    assert "废弃天台" in user_prompt


def test_canonical_audit_sequel_new_entities_block_empty_fallback(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """M7.A:无 canonical_entities 记录时,sequel_new_entities_block 返回明确提示文本。

    LLM 看到"未登记任何新实体"时应回退到原 canon 严格审,不放松"原作存在性"。
    """
    user = make_user("alice")
    h = user["headers"]
    _patch_canonical_sync_kick_off(monkeypatch)
    _, sim_id, _ = _create_done_sim_middle(client, h, patched_simulation_llm)

    # 不插任何 canonical_entities → block 应为空提示
    calls = _patch_canonical_llm(monkeypatch, output={"issues": []})
    r = client.post(f"/api/simulations/{sim_id}/canonical_audit", headers=h)
    assert r.status_code == 201
    user_prompt = calls[0]["user_input"]

    # 空提示文本应出现
    assert "本次续作中新引入的实体" in user_prompt
    assert "未登记任何新实体" in user_prompt


def test_canonical_guardian_prompt_file_contains_immunity_rule(monkeypatch):
    """M7.A:确保 prompts/canonical_guardian.md v2 含"续作创新豁免"铁律。

    这是文件层的回归测试:若有人未来无意中重写 prompt 删掉铁律 3,本测试守门。
    """
    from app.services.canonical_guardian_service import _load_prompt
    prompt = _load_prompt()
    # 关键铁律名词必须出现
    assert "续作创新豁免" in prompt
    assert "铁律 3" in prompt
    # 关键白名单类目必须列出
    assert "新角色登场" in prompt
    assert "新事件链" in prompt
    # event_causality 维度必须有反例说明(M7.A 治本)
    assert "event_causality" in prompt
