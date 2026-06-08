"""Simulation 服务端到端测试 — Sprint 1.G + 1.H reshape 补丁验收基线。

测试分组:
  A. 创建 + 鉴权 / 422 / 跨用户 / 配额(continuation + reshape)
  B. happy path:create → sync runner → done + narrative
  C. snapshot 冻结:create 后改角色不影响已存推演
  D. LLM 失败:state=failed + error_message 落库
  E. List / Detail / Delete
  F. /api/quota 暴露 continuations
  G. reshape_percent 派生 rounds + 配额(Sprint 1.H)

设计原则:
  - patched_simulation_llm preset 全部 LLM 响应,不消耗 DeepSeek
  - sync_simulation_runner 让 kick_off 同步阻塞跑,POST 响应即终态
  - 用真实场景数据(《江湖夜雨》李寻欢、孙小红、上官金虹),不用 test1/test2
  - _preload helper 按 reshape_percent 自动算 round 数,测试与产线公式同步
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.llm_client import LlmCallFailed
from app.services.simulation_service import reshape_to_rounds


# ============================================================
# helper:创建项目 + 角色
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


def _preload_minimal_simulation_llm(
    ctrl, char_ids: dict, reshape_percent: int = 10
):
    """每轮 1 director + 1 agent;最后 1 composer。便于 happy path 断言。

    director 永远只挑 char_ids 第一个角色 speak,简化 preset。
    rounds 由 reshape_percent 派生(与产线 reshape_to_rounds 公式一致),
    所以测试改 reshape 时不用手动同步 preset 数量。

    reshape_percent=10 → 5 轮(测试默认,LLM mock 11 次,够用且最快)。
    """
    first_id = next(iter(char_ids.values()))
    rounds = reshape_to_rounds(reshape_percent)

    for r in range(1, rounds + 1):
        ctrl.json_queue.append({
            "present_agents": list(char_ids.values()),
            "speaking_agents": [first_id],
            "location":     f"客栈大堂(第 {r} 轮)",
            "time_advance": "片刻后",
            "round_seed":   f"第 {r} 轮的契机:有人推门进来",
            "narrator_note": "灯火摇曳,风穿堂过。",
        })
        ctrl.json_queue.append({
            "monologue": "心下一沉",
            "action":    "缓缓抬眼",
            "dialogue":  f"你来了——这是第 {r} 轮的对白。",
        })
    ctrl.text_queue.append(
        "# 江湖夜雨\n\n夜色深沉,风穿堂过,客栈灯火摇曳……(测试 narrative)"
    )


# ============================================================
# A. 创建 + 鉴权 + 422 + 配额
# ============================================================

def test_create_simulation_too_few_characters_returns_422(
    client: TestClient, make_user, patched_simulation_llm
):
    user = make_user("hero")
    h = user["headers"]
    project_id, _ = _create_project_with_chars(
        client, h, "薄项目", chars=[{"name": "甲"}, {"name": "乙"}],   # 仅 2 个
    )
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果他没有进门那一晚的事"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "TOO_FEW_CHARACTERS"


def test_create_simulation_unauthenticated_returns_401(client: TestClient):
    r = client.post(
        "/api/projects/random_id/simulations",
        json={"divergence": "如果什么什么"},
    )
    assert r.status_code == 401


def test_create_simulation_cross_user_returns_404(
    client: TestClient, make_user, patched_simulation_llm
):
    u1 = make_user("alice")
    u2 = make_user("bob")
    project_id, _ = _create_project_with_chars(
        client, u1["headers"], "u1 project",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    # u2 试图在 u1 的 project 上创建推演
    r = client.post(
        f"/api/projects/{project_id}/simulations",
        headers=u2["headers"],
        json={"divergence": "如果探春没有打那一巴掌"},
    )
    assert r.status_code == 404


def test_create_simulation_free_tier_no_longer_blocks_count(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """Sprint C.1 credit 重构后:simulation 创建不再有"次数"配额。
    free 档可以连续创建多次(只要 credit 余额够),credit 不足才 429。

    此测试只验证"创建不再因 1 次/月限制 429"。credit 不足 → InsufficientCredits
    由 Sprint C.3 接通 LLM 真扣后单独测。
    """
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨", project_type="novel", tags=["武侠"],
        chars=[
            {"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"},
        ],
    )

    # 第 1 次 — 成功
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r1 = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果当晚的相遇没有发生", "reshape_percent": 10},
    )
    assert r1.status_code == 201, r1.text

    # 第 2 次 — Sprint C.1:不再 429(次数模式已删)
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r2 = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果他们再次相遇于客栈灯下", "reshape_percent": 10},
    )
    assert r2.status_code == 201, r2.text


# ============================================================
# B. Happy path
# ============================================================

def test_simulation_happy_path_returns_done_with_narrative(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[
            {"name": "李寻欢", "identity": "江湖浪子", "personality": "豪爽仗义"},
            {"name": "孙小红", "identity": "江湖义士"},
            {"name": "上官金虹", "identity": "神秘剑客"},
        ],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)

    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果探春没有打那一巴掌", "reshape_percent": 10},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "done"
    sim_id = body["simulation_id"]

    # GET 详情
    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    assert detail["state"] == "done"
    expected_rounds = reshape_to_rounds(10)   # = 5
    assert detail["current_round"] == expected_rounds
    assert detail["rounds_planned"] == expected_rounds
    assert detail["reshape_percent"] == 10
    assert detail["narrative"].startswith("# 江湖夜雨")
    assert detail["timeline"] is not None
    assert len(detail["timeline"]["rounds"]) == expected_rounds
    assert detail["completed_at"] is not None
    assert detail["error_message"] is None
    assert detail["cost_yuan"] > 0

    # LLM 调用次数:N director + N agent + 1 composer
    assert len(patched_simulation_llm.json_calls) == expected_rounds * 2
    assert len(patched_simulation_llm.text_calls) == 1


def test_simulation_includes_characters_snapshot_in_full_response(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[
            {"name": "李寻欢", "identity": "江湖浪子",
             "quotes": ["小李飞刀,例不虚发"]},
            {"name": "孙小红"}, {"name": "上官金虹"},
        ],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)

    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果初见不在客栈而在江畔", "reshape_percent": 10},
    )
    sim_id = r.json()["simulation_id"]

    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    snap = detail["characters_snapshot"]
    assert len(snap) == 3
    # 每个 snapshot 都该有 simulate prompt 期待的字段
    for c in snap:
        assert "id" in c and "name" in c and "personality" in c
        assert "voice_fingerprint" in c
        assert "no_go_list" in c
        assert "behavioral_rules" in c
    # 李寻欢的 quotes 应该被搬进 voice_fingerprint
    li = next(c for c in snap if c["name"] == "李寻欢")
    assert "小李飞刀,例不虚发" in li["voice_fingerprint"]["quotes"]


# ============================================================
# C. Snapshot 冻结:create 后改 character,sim 不受影响
# ============================================================

def test_simulation_snapshot_frozen_on_create(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[
            {"name": "李寻欢", "identity": "江湖浪子"},
            {"name": "孙小红", "identity": "江湖义士"},
            {"name": "上官金虹", "identity": "神秘剑客"},
        ],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)

    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果某夜的相遇被改写", "reshape_percent": 10},
    )
    sim_id = r.json()["simulation_id"]

    # 创建 + 跑完后,改一个角色名
    li_id = char_ids["李寻欢"]
    client.patch(
        f"/api/characters/{li_id}", headers=h,
        json={"name": "李探花", "identity": "改了的身份"},
    )

    # snapshot 仍是旧名字
    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    names = [c["name"] for c in detail["characters_snapshot"]]
    assert "李寻欢" in names
    assert "李探花" not in names


# ============================================================
# D. LLM 失败 → state=failed
# ============================================================

def test_simulation_director_llm_failure_marks_failed(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    user = make_user("hero")
    h = user["headers"]
    project_id, _ = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[
            {"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"},
        ],
    )
    # 第一次 director 调用直接抛
    patched_simulation_llm.json_queue.append(
        LlmCallFailed("simulated DeepSeek timeout")
    )

    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果这是 LLM 直接失败的失败案例", "reshape_percent": 10},
    )
    # POST 仍 201(行已落库),但 state=failed
    assert r.status_code == 201
    assert r.json()["state"] == "failed"
    sim_id = r.json()["simulation_id"]

    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    assert detail["state"] == "failed"
    assert detail["error_message"] is not None
    assert "simulated DeepSeek timeout" in detail["error_message"]
    assert detail["narrative"] is None
    assert detail["completed_at"] is not None


def test_simulation_composer_llm_failure_marks_failed_with_partial_timeline(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[
            {"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"},
        ],
    )
    # director + agent 都成功,composer 失败
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    # 把队列尾部的 composer 替换为异常
    patched_simulation_llm.text_queue.clear()
    patched_simulation_llm.text_queue.append(
        LlmCallFailed("composer model returned 500")
    )

    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果前面 director 成功而 composer 失败", "reshape_percent": 10},
    )
    assert r.status_code == 201
    assert r.json()["state"] == "failed"
    sim_id = r.json()["simulation_id"]

    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    assert detail["state"] == "failed"
    assert "composer" in detail["error_message"].lower()
    # timeline 已全部轮次落库(director+agent 都跑完才轮到 composer 失败)
    expected_rounds = reshape_to_rounds(10)
    assert detail["timeline"] is not None
    assert len(detail["timeline"]["rounds"]) == expected_rounds
    assert detail["narrative"] is None


# ============================================================
# E. List / Get / Delete
# ============================================================

def test_list_simulations_returns_summary_for_project(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )

    # 起 1 个 sim(免费档配额 1)
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果他们在某个雨夜重逢于江畔", "reshape_percent": 10},
    )

    list_resp = client.get(f"/api/projects/{project_id}/simulations", headers=h)
    assert list_resp.status_code == 200
    data = list_resp.json()
    assert len(data) == 1
    item = data[0]
    # summary 字段(对齐 SimulationSummaryResponse)
    assert "id" in item and "state" in item and "divergence" in item
    assert "narrative" not in item
    assert "timeline" not in item
    assert "characters_snapshot" not in item


def test_get_simulation_cross_user_returns_404(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    u1 = make_user("alice")
    u2 = make_user("bob")
    project_id, char_ids = _create_project_with_chars(
        client, u1["headers"], "u1 project",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=u1["headers"],
        json={"divergence": "如果当年的两人从未相遇", "reshape_percent": 10},
    )
    sim_id = r.json()["simulation_id"]

    # u2 试图读 u1 的 sim
    r404 = client.get(f"/api/simulations/{sim_id}", headers=u2["headers"])
    assert r404.status_code == 404


def test_delete_simulation_removes_from_db(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果这条推演结果会被删除", "reshape_percent": 10},
    )
    sim_id = r.json()["simulation_id"]

    r_del = client.delete(f"/api/simulations/{sim_id}", headers=h)
    assert r_del.status_code == 204

    r_get = client.get(f"/api/simulations/{sim_id}", headers=h)
    assert r_get.status_code == 404


def test_delete_simulation_cross_user_returns_404(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    u1 = make_user("alice")
    u2 = make_user("bob")
    project_id, char_ids = _create_project_with_chars(
        client, u1["headers"], "u1 project",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=u1["headers"],
        json={"divergence": "如果尝试跨用户删除推演记录", "reshape_percent": 10},
    )
    sim_id = r.json()["simulation_id"]

    r_del = client.delete(f"/api/simulations/{sim_id}", headers=u2["headers"])
    assert r_del.status_code == 404


# ============================================================
# F. /api/quota 暴露 continuations 字段
# ============================================================

def test_quota_status_includes_credit_balance(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """Sprint C.1 credit 重构:GET /api/quota 返回 credit_balance 而非 continuations 次数。"""
    user = make_user("hero")
    h = user["headers"]
    r = client.get("/api/quota", headers=h)
    assert r.status_code == 200
    body = r.json()
    # Free 档:20 credit 月发放(ECON-1 v5,30 → 20)+ 加购 0
    assert body["plan"] == "free"
    assert body["limits"]["monthly_credits_quota"] == 20
    assert body["credit_balance"]["subscription_credits"] == 20
    assert body["credit_balance"]["addon_credits"] == 0
    assert body["credit_balance"]["total_credits"] == 20


# ============================================================
# G. reshape_percent 派生 + 配额(Sprint 1.H)
# ============================================================

def test_reshape_default_50_when_omitted_but_blocked_by_free_tier_max(
    client: TestClient, make_user, patched_simulation_llm
):
    """omit reshape_percent → schema 默认 50;但 free 档 max=30 → 429。

    覆盖两条:① schema 默认值起作用 ② enforce_reshape_quota 闸门起作用
    """
    user = make_user("hero")
    h = user["headers"]
    project_id, _ = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果用默认重塑度但免费档不够"},   # omit reshape_percent
    )
    assert r.status_code == 429
    detail = r.json()["detail"]
    assert detail["code"] == "QUOTA_EXCEEDED"
    assert detail["kind"] == "reshape_percent"
    assert detail["used"] == 50      # 用户请求的(默认)
    assert detail["limit"] == 30     # free 档上限


def test_reshape_at_free_tier_max_succeeds(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """reshape_percent = plan.reshape_max_percent(边界值)→ 通过。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    # 30% 派生轮次 16,preset 16 轮
    _preload_minimal_simulation_llm(
        patched_simulation_llm, char_ids, reshape_percent=30,
    )

    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "如果免费档刚好顶到上限不溢出",
            "reshape_percent": 30,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["state"] == "done"

    sim_id = r.json()["simulation_id"]
    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    assert detail["reshape_percent"] == 30
    assert detail["rounds_planned"] == reshape_to_rounds(30)   # 16


def test_reshape_below_min_fails_schema_validation(
    client: TestClient, make_user, patched_simulation_llm
):
    """reshape_percent < 10 → Pydantic 422(schema 范围,先于 enforce)。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, _ = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果重塑度低于下限", "reshape_percent": 5},
    )
    assert r.status_code == 422   # FastAPI Pydantic 校验


def test_reshape_to_rounds_unit_anchor_values():
    """单元锚点 — 若公式被改且产线偏移,这里立刻红。"""
    assert reshape_to_rounds(10) == 5
    assert reshape_to_rounds(30) == 16
    assert reshape_to_rounds(60) == 33
    assert reshape_to_rounds(90) == 50


# ============================================================
# I.O 滚雪球续写 — Sprint 1.O
# ============================================================

def test_snowball_no_context_works_as_before(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """空 context_simulation_ids → 普通推演,向后兼容,detail 含空列表。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)

    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果独立推演不带前文", "reshape_percent": 10},
    )
    assert r.status_code == 201
    sim_id = r.json()["simulation_id"]

    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    assert detail["context_simulation_ids"] == []
    assert detail["narrative_summary"] is None


def test_snowball_with_one_prior_full_text_injection(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """1 条前文(narrative ~50 字 < 8000)→ 全文注入,不跑摘要 LLM。"""
    import sqlite3
    from app.config import settings

    user = make_user("hero")
    h = user["headers"]
    # 升 plan 让免费 1 次配额限制不挡路(滚雪球至少要 2 次推演)
    conn = sqlite3.connect(str(settings.db_abs_path))
    conn.execute("UPDATE users SET plan='pro' WHERE id=?", (user["user_id"],))
    conn.commit()
    conn.close()

    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )

    # 第 1 次推演 — 作为前文
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r1 = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "第 1 段:江湖夜雨初见", "reshape_percent": 10},
    )
    assert r1.status_code == 201
    prior_sim_id = r1.json()["simulation_id"]

    # 第 2 次推演 — 接续第 1 次
    text_call_count_before = len(patched_simulation_llm.text_calls)
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r2 = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "第 2 段:接着江湖夜雨的故事写下去",
            "reshape_percent": 10,
            "context_simulation_ids": [prior_sim_id],
        },
    )
    assert r2.status_code == 201, r2.text
    next_sim_id = r2.json()["simulation_id"]

    # detail 暴露 context_simulation_ids
    detail = client.get(f"/api/simulations/{next_sim_id}", headers=h).json()
    assert detail["context_simulation_ids"] == [prior_sim_id]

    # < 8000 字阈值 → 不应走摘要(text_calls 只多了 1 次:本次推演的 composer)
    text_call_count_after = len(patched_simulation_llm.text_calls)
    assert text_call_count_after - text_call_count_before == 1, (
        f"前文 < 8000 字应全文注入,不应跑摘要 LLM。"
        f"实际 text 调用增加 {text_call_count_after - text_call_count_before} 次"
    )

    # 前文 sim 的 narrative_summary 应仍为 None(没被摘要化)
    prior_detail = client.get(
        f"/api/simulations/{prior_sim_id}", headers=h,
    ).json()
    assert prior_detail["narrative_summary"] is None


def test_snowball_cross_project_context_returns_422(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """前文 sim 属于另一项目 → 422 INVALID_CONTEXT_SIMULATIONS。"""
    user = make_user("hero")
    h = user["headers"]

    # Project A:跑一条 sim
    pa, char_ids_a = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids_a)
    sim_a = client.post(
        f"/api/projects/{pa}/simulations", headers=h,
        json={"divergence": "项目 A 的第 1 段", "reshape_percent": 10},
    ).json()["simulation_id"]

    # Project B:试图引用 A 的 sim 作前文 → 应 422
    pb, char_ids_b = _create_project_with_chars(
        client, h, "云端之上",
        chars=[{"name": "陈默"}, {"name": "林夏"}, {"name": "苏教授"}],
    )
    r = client.post(
        f"/api/projects/{pb}/simulations", headers=h,
        json={
            "divergence": "项目 B 想接 项目 A 的剧情(应失败)",
            "reshape_percent": 10,
            "context_simulation_ids": [sim_a],
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "INVALID_CONTEXT_SIMULATIONS"
    assert sim_a in detail["invalid_ids"]


def test_snowball_in_progress_or_failed_context_rejected(
    client: TestClient, make_user, patched_simulation_llm
):
    """前文 sim 不是 done(比如 failed)→ 422。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )

    # 跑一条会失败的 sim(不开 sync runner,先把 LLM 设成失败)
    # 简化:直接构造一个 fake sim_id 引用,validate 找不到 done 状态的 → 422
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "引用一个不存在的前文 sim",
            "reshape_percent": 10,
            "context_simulation_ids": ["nonexistent-sim-id-12345"],
        },
    )
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "INVALID_CONTEXT_SIMULATIONS"
    assert "nonexistent-sim-id-12345" in detail["invalid_ids"]


def test_snowball_broken_chain_rejected(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """2026-06-01:context list 不是连续单链(第 2 篇的 context 不含第 1 篇)→ 422."""
    import sqlite3
    from app.config import settings

    user = make_user("hero")
    h = user["headers"]
    # 升 plan 让免费 1 次配额限制不挡路
    conn = sqlite3.connect(str(settings.db_abs_path))
    conn.execute("UPDATE users SET plan='pro' WHERE id=?", (user["user_id"],))
    conn.commit()
    conn.close()

    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )

    # 跑 sim A(独立) + sim B(独立,跟 A 无继承关系)
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    rA = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "独立线 A:江湖夜雨独立起步的故事", "reshape_percent": 10},
    )
    assert rA.status_code == 201, rA.text
    sim_a = rA.json()["simulation_id"]

    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    rB = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "独立线 B:跟 A 无关的另一条平行支线", "reshape_percent": 10},
    )
    assert rB.status_code == 201, rB.text
    sim_b = rB.json()["simulation_id"]

    # 用户错传 [sim_a, sim_b] 作 context — B 的 context 不含 A(B 是独立线)→ 422
    rBroken = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "想接续 A + B 两条线的混合续作",
            "reshape_percent": 10,
            "context_simulation_ids": [sim_a, sim_b],
        },
    )
    assert rBroken.status_code == 422, rBroken.text
    detail = rBroken.json()["detail"]
    assert detail["code"] == "INVALID_CONTEXT_SIMULATIONS"
    assert "断裂" in detail["message"] or "断裂" in str(detail)


def test_snowball_valid_chain_accepted(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """2026-06-01:context list 是连续单链([A → B,B 的 context 含 A])→ 通过."""
    import sqlite3
    from app.config import settings

    user = make_user("hero")
    h = user["headers"]
    conn = sqlite3.connect(str(settings.db_abs_path))
    conn.execute("UPDATE users SET plan='pro' WHERE id=?", (user["user_id"],))
    conn.commit()
    conn.close()

    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )

    # sim A 独立
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    rA = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "起点 A:江湖夜雨初见李寻欢", "reshape_percent": 10},
    )
    assert rA.status_code == 201, rA.text
    sim_a = rA.json()["simulation_id"]

    # sim B 接续 A
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    rB = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "接续 A 的故事:夜雨后李寻欢与孙小红重逢",
            "reshape_percent": 10,
            "context_simulation_ids": [sim_a],
        },
    )
    assert rB.status_code == 201, rB.text
    sim_b = rB.json()["simulation_id"]

    # sim C:context=[A, B] 是连续单链(B 的 context 含 A)→ 应通过
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    rC = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "C 是完整链:A + B 之后,故事走向终章",
            "reshape_percent": 10,
            "context_simulation_ids": [sim_a, sim_b],
        },
    )
    assert rC.status_code == 201, rC.text


# ============================================================
# K. 流式 B2 — Sprint 1.L
# ============================================================

def test_sse_token_endpoint_requires_main_jwt(client: TestClient):
    """未带 Bearer → 401。"""
    r = client.post("/api/auth/sse_token")
    assert r.status_code == 401


def test_sse_token_endpoint_returns_short_token_with_aud_sse(
    client: TestClient, make_user
):
    """主 JWT 验过 → 返回短期 token + expires_in。"""
    user = make_user("sse")
    r = client.post("/api/auth/sse_token", headers=user["headers"])
    assert r.status_code == 200
    body = r.json()
    assert body["expires_in"] == 900   # 15 分钟
    sse_tok = body["token"]
    assert isinstance(sse_tok, str) and len(sse_tok) > 20

    # 验 aud='sse' 隔离:用 decode_sse_token 拿 user_id 应成功
    from app.services.auth_service import decode_sse_token
    assert decode_sse_token(sse_tok) == user["user_id"]


def test_main_jwt_cannot_be_used_as_sse_token(client: TestClient, make_user):
    """主 JWT(无 aud)→ decode_sse_token 必须拒(防误用)。"""
    user = make_user("sse")
    main_jwt = user["token"]
    from app.services.auth_service import decode_sse_token
    from jose import JWTError
    import pytest
    with pytest.raises(JWTError):
        decode_sse_token(main_jwt)


def test_sse_stream_rejects_invalid_token(client: TestClient, make_user):
    """SSE endpoint 不接收 Bearer / 错误 token。"""
    user = make_user("sse")
    # 拼一个假 sim_id;真假无关,token 无效就先 401
    r = client.get(
        "/api/simulations/fake-id/stream?token=garbage_token",
    )
    assert r.status_code == 401


def test_sse_stream_emits_snapshot_on_terminal_state(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """sync runner 模式下 sim 已 done,SSE 连上首发 snapshot 含完整产物后退出。"""
    user = make_user("sse")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)

    create_resp = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果用 SSE 看完整流程", "reshape_percent": 10},
    )
    assert create_resp.status_code == 201
    assert create_resp.json()["state"] == "done"   # sync runner 已跑完
    sim_id = create_resp.json()["simulation_id"]

    # 换 SSE token
    tok = client.post("/api/auth/sse_token", headers=h).json()["token"]

    # 流式拉取 — 终态已 done,snapshot 一发完连接关闭
    import json as json_lib
    with client.stream(
        "GET", f"/api/simulations/{sim_id}/stream?token={tok}",
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")

        body = b""
        for chunk in resp.iter_bytes():
            body += chunk
            if b"\n\n" in body:
                break

        text = body.decode("utf-8")
        # 至少一条 data: 行
        assert text.startswith("data: "), f"SSE 应以 data: 开头,实际:{text[:80]!r}"
        line = text.split("\n", 1)[0]
        payload = json_lib.loads(line[len("data: "):])
        assert payload["kind"] == "snapshot"
        assert payload["state"] == "done"
        assert payload["narrative"].startswith("# 江湖夜雨")


def test_emit_event_with_no_subscribers_does_not_raise():
    """emit 没人订阅时静默丢弃,不阻塞 runner。"""
    from app.services.simulation_service import _emit_event
    # 直接调,无注册任何 queue
    _emit_event("nonexistent-sim-id", {"kind": "test"})
    # 不抛即过


# ============================================================
# L. 断点续推 — Sprint 1.P
# ============================================================

def test_resume_continues_from_last_round_to_done(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """第 1 轮 director 成功 + agent 成功 + 第 2 轮 director 抛异常 → state=failed,
    timeline 留 1 轮 → resume → preset 第 2 轮的 LLM → done + timeline 满。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )

    # 第 1 轮 1 dir + 1 agent 成功;第 2 轮 dir 抛
    first_id = next(iter(char_ids.values()))
    patched_simulation_llm.json_queue.append({
        "present_agents": list(char_ids.values()),
        "speaking_agents": [first_id],
        "location": "客栈(第 1 轮)",
        "time_advance": "夜",
        "round_seed": "推门",
        "narrator_note": "灯火",
    })
    patched_simulation_llm.json_queue.append({
        "monologue": "心下一沉",
        "action": "抬眼",
        "dialogue": "你来了。",
    })
    patched_simulation_llm.json_queue.append(
        LlmCallFailed("第 2 轮模拟失败")
    )

    # reshape 30 → 16 轮
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "如果第二轮失败的故事",
            "reshape_percent": 10,    # 5 轮,简单
        },
    )
    assert r.status_code == 201
    assert r.json()["state"] == "failed"
    sim_id = r.json()["simulation_id"]

    # 验:state=failed,timeline 有 1 轮
    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    assert detail["state"] == "failed"
    assert len(detail["timeline"]["rounds"]) == 1
    assert detail["narrative"] is None

    # preset 剩余 4 轮 + composer
    for r_num in range(2, 6):  # 第 2-5 轮
        patched_simulation_llm.json_queue.append({
            "present_agents": list(char_ids.values()),
            "speaking_agents": [first_id],
            "location": f"客栈(第 {r_num} 轮)",
            "time_advance": "片刻后",
            "round_seed": f"第 {r_num} 轮契机",
            "narrator_note": "续",
        })
        patched_simulation_llm.json_queue.append({
            "monologue": "想",
            "action": "动",
            "dialogue": f"第 {r_num} 轮对白",
        })
    patched_simulation_llm.text_queue.append(
        "# 江湖夜雨\n\n夜色深沉……(续推 narrative)"
    )

    # resume
    r2 = client.post(f"/api/simulations/{sim_id}/resume", headers=h)
    assert r2.status_code == 200, r2.text
    assert r2.json()["state"] == "done"

    detail2 = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    assert detail2["state"] == "done"
    assert len(detail2["timeline"]["rounds"]) == 5
    # 第 1 轮的 timeline 应该保留(续推不重跑)
    assert detail2["timeline"]["rounds"][0]["director_plan"]["location"] == "客栈(第 1 轮)"
    # 后续轮也都跑了
    assert detail2["timeline"]["rounds"][4]["director_plan"]["location"] == "客栈(第 5 轮)"
    assert detail2["narrative"].startswith("# 江湖夜雨")


def test_resume_done_simulation_rejected_with_422(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """已 done 的 sim 不能 resume → 422。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果已完成的不能恢复", "reshape_percent": 10},
    )
    sim_id = r.json()["simulation_id"]

    r2 = client.post(f"/api/simulations/{sim_id}/resume", headers=h)
    assert r2.status_code == 422
    assert r2.json()["detail"]["code"] == "SIMULATION_NOT_RESUMABLE"


def test_resume_zero_progress_simulation_rejected_with_422(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """timeline 0 轮(第 1 轮 director 就失败)→ 不能 resume(应该 重新创建)。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, _ = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    # 第 1 轮 director 立即失败
    patched_simulation_llm.json_queue.append(
        LlmCallFailed("第 1 轮即失败")
    )
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果第一轮就立刻失败", "reshape_percent": 10},
    )
    sim_id = r.json()["simulation_id"]
    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    assert detail["state"] == "failed"
    assert detail["timeline"] is None or len(detail["timeline"]["rounds"]) == 0

    r2 = client.post(f"/api/simulations/{sim_id}/resume", headers=h)
    assert r2.status_code == 422
    assert r2.json()["detail"]["code"] == "SIMULATION_NOT_RESUMABLE"


def test_resume_does_not_recreate_simulation(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """Sprint C.1 credit 重构后:resume 仅恢复运行(simulation 表无新行),
    不再有"次数配额"概念。本测试改为验"resume 不复刻 simulation"基本契约。

    credit 视角:resume 触发的 LLM 调用按真实 token 扣 credit(失败重试也扣),
    这是 Sprint C.3 接通 LLM 真扣后的单独测试场景。
    """
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )

    # 1 轮成功 + 第 2 轮失败
    first_id = next(iter(char_ids.values()))
    patched_simulation_llm.json_queue.append({
        "present_agents": list(char_ids.values()),
        "speaking_agents": [first_id],
        "location": "L1", "time_advance": "T1",
        "round_seed": "S1", "narrator_note": "N1",
    })
    patched_simulation_llm.json_queue.append({
        "monologue": "m", "action": "a", "dialogue": "d",
    })
    patched_simulation_llm.json_queue.append(LlmCallFailed("R2 fail"))

    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "如果配额计数验证用例需要长一点的描述",
            "reshape_percent": 10,
        },
    )
    assert r.status_code == 201, r.text
    sim_id = r.json()["simulation_id"]

    # 验证 simulation 表只有 1 条(未复刻)
    list_resp = client.get("/api/simulations", headers=h).json()
    assert len(list_resp) == 1

    # resume 触发 — preset 剩余轮
    for rn in range(2, 6):
        patched_simulation_llm.json_queue.append({
            "present_agents": list(char_ids.values()),
            "speaking_agents": [first_id],
            "location": f"L{rn}", "time_advance": "T",
            "round_seed": "S", "narrator_note": "N",
        })
        patched_simulation_llm.json_queue.append({
            "monologue": "m", "action": "a", "dialogue": "d",
        })
    patched_simulation_llm.text_queue.append("# n")

    client.post(f"/api/simulations/{sim_id}/resume", headers=h)

    # resume 后:仍只有 1 条 simulation(同 ID,resume 不复刻)
    list_resp2 = client.get("/api/simulations", headers=h).json()
    assert len(list_resp2) == 1
    assert list_resp2[0]["id"] == sim_id


def test_resume_concurrent_run_blocked_by_running_lock(monkeypatch):
    """sim 当前正在 worker 跑(_RUNNING_SIMS 占用)→ resume 抛 SimulationStillRunning。"""
    import pytest

    from app.models.simulation import Simulation
    from app.services import simulation_service as svc

    sim_id = "fake-running-sim-id"

    # 构造一个看起来"可恢复"的 fake sim:state=failed + timeline 有 1 轮
    fake_sim = Simulation(
        id=sim_id, project_id="p", user_id="u",
        divergence="d" * 15,
        reshape_percent=10, rounds_planned=5,
        target_chars=4000, style="A", custom_style_hint=None,
        context_simulation_ids=[], narrative_summary=None,
        characters_snapshot=[],
        state="failed", current_round=1,
        timeline={"rounds": [{"round": 1}]},
        narrative=None, tokens_input=0, tokens_output=0,
        cost_yuan=0.0, error_message="prev fail",
        created_at="2026-01-01T00:00:00+00:00",
        started_at=None, completed_at=None,
    )
    # bypass DB 鉴权 — 直接返回 fake sim
    monkeypatch.setattr(
        svc, "get_simulation_or_404",
        lambda conn, sid, uid: fake_sim,
    )

    # 占用 _RUNNING_SIMS 模拟"正在跑"
    with svc._RUNNING_LOCK:
        svc._RUNNING_SIMS.add(sim_id)
    try:
        with pytest.raises(svc.SimulationStillRunning):
            svc.resume_simulation(None, sim_id, "u")  # type: ignore[arg-type]
    finally:
        with svc._RUNNING_LOCK:
            svc._RUNNING_SIMS.discard(sim_id)


def test_safe_format_template_escapes_double_braces():
    """Sprint 1.G/O bug 回归 — _safe_format_template 必须把 `{{` 还原为 `{`,
    否则 LLM 看到 prompt 里的 `{{ ... }}` 会照搬输出,JSON 解析炸。
    """
    from app.services.simulation_service import _safe_format_template

    template = """【输出格式】
{{
  "location": "{location_hint}",
  "time_advance": "<时间字符串>"
}}"""
    out = _safe_format_template(template, {"location_hint": "秋爽斋"})

    # `{{` `}}` 必须还原为 `{` `}`
    assert "{{" not in out, f"模板未还原 {{,LLM 会照搬:{out!r}"
    assert "}}" not in out
    assert '"location": "秋爽斋"' in out
    # 仍含单层 `{` `}`(JSON 例子结构保留)
    assert out.count("{") == 1
    assert out.count("}") == 1


def test_repair_llm_json_strips_leading_trailing_double_braces():
    """LLM 偶发输出 `{{ ... }}` → repair 应能救回。"""
    import json
    from app.services.llm_client import _repair_llm_json

    bad_raw = '{{\n  "location": "矿洞深处",\n  "time_advance": "片刻之后"\n}}'
    repaired = _repair_llm_json(bad_raw)
    parsed = json.loads(repaired)
    assert parsed["location"] == "矿洞深处"
    assert parsed["time_advance"] == "片刻之后"


def test_default_kick_off_is_non_blocking_and_runs_in_background(monkeypatch):
    """Sprint 1.K 回归 — _default_kick_off 必须立即返回,不阻塞 POST 请求。

    bug 历史:之前用 asyncio.get_running_loop() 检测异步上下文,在 FastAPI
    sync route 被丢进 starlette threadpool 时(worker 没 event loop),
    fallback 到 run_simulation 同步阻塞,POST 卡 5 分钟。

    本测试 stub run_simulation 为耗时 500ms 的函数,验证 kick_off 立即返回
    + 后台线程确实跑完 stub。
    """
    import time
    from app.services import simulation_service as svc

    called: list[str] = []

    def slow_runner(sim_id: str) -> None:
        time.sleep(0.5)
        called.append(sim_id)

    monkeypatch.setattr(svc, "run_simulation", slow_runner)

    t0 = time.perf_counter()
    svc._default_kick_off("test-sim-12345678")
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # 必须立即返回(< 50ms;线程启动开销通常 < 5ms)
    assert elapsed_ms < 50, f"_default_kick_off 应非阻塞,实际 {elapsed_ms:.0f}ms"

    # 给后台线程 1s 跑完(stub 自身要 500ms)
    time.sleep(0.8)
    assert called == ["test-sim-12345678"], (
        f"后台线程应已执行 run_simulation,但实际 called={called}"
    )


# ============================================================
# H. GET /api/simulations(跨项目用户列表 — Sprint 1.J 我的剧情线)
# ============================================================

def test_list_user_simulations_returns_all_user_sims_with_project_name(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """同用户多项目下都有 sim → /api/simulations 全返回 + 各自 project_name 正确。"""
    user = make_user("hero")
    h = user["headers"]

    p1, c1 = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, c1)
    client.post(
        f"/api/projects/{p1}/simulations", headers=h,
        json={"divergence": "如果探春没有打那一巴掌", "reshape_percent": 10},
    )

    # 先把 free 配额耗完不能再起 sim,但已有 1 条;再用 standard 用户来测多 sim
    user2 = make_user("rich")
    # 改 plan 跳过 OTP 流(测试方便,真实接订阅升级)
    import sqlite3
    from app.config import settings
    conn = sqlite3.connect(str(settings.db_abs_path))
    conn.execute(
        "UPDATE users SET plan='pro' WHERE id=?", (user2["user_id"],)
    )
    conn.commit()
    conn.close()
    h2 = user2["headers"]

    p2, c2 = _create_project_with_chars(
        client, h2, "双面镇",
        chars=[{"name": "陈默"}, {"name": "林夏"}, {"name": "苏教授"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, c2)
    client.post(
        f"/api/projects/{p2}/simulations", headers=h2,
        json={"divergence": "如果当年那场实验没有失败", "reshape_percent": 10},
    )
    p3, c3 = _create_project_with_chars(
        client, h2, "云端之上",
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, c3)
    client.post(
        f"/api/projects/{p3}/simulations", headers=h2,
        json={"divergence": "如果第二个推演也成功完成", "reshape_percent": 10},
    )

    # user2 GET /api/simulations 应得 2 条;user1 应得 1 条
    r1 = client.get("/api/simulations", headers=h)
    assert r1.status_code == 200
    data1 = r1.json()
    assert len(data1) == 1
    assert data1[0]["project_name"] == "江湖夜雨"

    r2 = client.get("/api/simulations", headers=h2)
    assert r2.status_code == 200
    data2 = r2.json()
    assert len(data2) == 2
    project_names = {item["project_name"] for item in data2}
    assert project_names == {"双面镇", "云端之上"}


def test_list_user_simulations_ordered_desc_by_created_at(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """同用户多 sim 按 created_at DESC(新的在前)。"""
    import sqlite3
    import time
    from app.config import settings

    user = make_user("hero")
    h = user["headers"]
    # 升 plan 让 free 1 次配额限制不挡路
    conn = sqlite3.connect(str(settings.db_abs_path))
    conn.execute("UPDATE users SET plan='pro' WHERE id=?", (user["user_id"],))
    conn.commit()
    conn.close()

    p, c = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )

    # 起 3 条,中间各停 1.1s 让 ISO 秒精度区分得开
    for i in range(3):
        _preload_minimal_simulation_llm(patched_simulation_llm, c)
        client.post(
            f"/api/projects/{p}/simulations", headers=h,
            json={
                "divergence": f"如果第 {i + 1} 次推演单独留痕",
                "reshape_percent": 10,
            },
        )
        if i < 2:
            time.sleep(1.1)

    r = client.get("/api/simulations", headers=h)
    data = r.json()
    assert len(data) == 3
    timestamps = [item["created_at"] for item in data]
    assert timestamps == sorted(timestamps, reverse=True), (
        f"应按 created_at DESC,实际:{timestamps}"
    )


def test_list_user_simulations_empty_when_no_sims(
    client: TestClient, make_user
):
    """新注册用户无任何 sim → 返回空列表(不是 404)。"""
    user = make_user("rookie")
    r = client.get("/api/simulations", headers=user["headers"])
    assert r.status_code == 200
    assert r.json() == []


def test_list_user_simulations_unauthenticated_returns_401(client: TestClient):
    r = client.get("/api/simulations")
    assert r.status_code == 401


# ============================================================
# I. 自定义笔法风格(style='custom' + custom_style_hint)
# ============================================================

def test_custom_style_requires_hint_at_least_10_chars(
    client: TestClient, make_user, patched_simulation_llm
):
    """style='custom' 但 hint 缺失 / < 10 字 → schema 422。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, _ = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )

    # 缺 hint
    r1 = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "如果选自定义但不写 hint",
            "reshape_percent": 10,
            "style": "custom",
        },
    )
    assert r1.status_code == 422

    # hint 太短
    r2 = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "如果 hint 只有几个字",
            "reshape_percent": 10,
            "style": "custom",
            "custom_style_hint": "短了",
        },
    )
    assert r2.status_code == 422


def test_custom_style_persists_hint_and_loads_composer_custom_prompt(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """style='custom' + 合法 hint → 落库 + 跑 composer_custom.md 模板。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)

    custom_hint = "民国白话武侠风,语言简练,多用倒装句"
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "如果用自定义笔法续写这一夜",
            "reshape_percent": 10,
            "style": "custom",
            "custom_style_hint": custom_hint,
        },
    )
    assert r.status_code == 201, r.text
    sim_id = r.json()["simulation_id"]

    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    assert detail["style"] == "custom"
    assert detail["custom_style_hint"] == custom_hint
    assert detail["state"] == "done"
    # 验 composer 收到了 custom hint(看 patched_simulation_llm 的 text_calls
    # 系统提示里有 hint 文本) — 末次 text_call 是 composer
    assert len(patched_simulation_llm.text_calls) == 1


def test_non_custom_style_drops_hint_silently(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """style='A'(旧客户端兼容)→ 内部归并到 'auto' + 误传 hint 静默丢弃。

    Sprint 1.Q 起 'A' 'C' 已废弃但兼容旧请求,Pydantic validator 把它们改成 'auto'。
    custom_style_hint 仅 style='custom' 时落库,其他都丢。
    """
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "江湖夜雨",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)

    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "如果传旧 A 应被归到 auto",
            "reshape_percent": 10,
            "style": "A",                     # 旧客户端
            "custom_style_hint": "这个不该被存",
        },
    )
    assert r.status_code == 201
    sim_id = r.json()["simulation_id"]

    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    assert detail["style"] == "auto", "1.Q 起 A/C 归并 auto"
    assert detail["custom_style_hint"] is None, "非 custom hint 应丢弃"


# ============================================================
# J. 自定义作品类型(type='generic' + custom_type_name)
# ============================================================

def test_custom_type_name_persisted_and_used_in_scene(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner
):
    """type='generic' + custom_type_name='舞台剧' → scene 描述里用'舞台剧'。"""
    user = make_user("hero")
    h = user["headers"]

    # 创建 generic 项目带自定义类型名
    p_resp = client.post(
        "/api/projects", headers=h,
        json={"name": "雨夜独角戏", "type": "generic", "custom_type_name": "舞台剧"},
    )
    assert p_resp.status_code == 201
    project_id = p_resp.json()["id"]
    assert p_resp.json()["custom_type_name"] == "舞台剧"

    # 加角色 + 跑推演,scene 字符串里应该出现"舞台剧"
    for n in ["主角", "配角A", "配角B"]:
        client.post(
            f"/api/projects/{project_id}/characters", headers=h,
            json={"name": n},
        )
    char_ids_resp = client.get(
        f"/api/projects/{project_id}/characters", headers=h,
    ).json()
    char_ids = {c["name"]: c["id"] for c in char_ids_resp}
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)

    client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果舞台剧的开场没有那段独白", "reshape_percent": 10},
    )

    # director 收到的 user prompt 系统侧 monkeypatch 不直接拿到 user 文本,
    # 但我们能通过 Simulation 的 service 内 _build_scene 正确性已经在 service 里覆盖。
    # 这里换个角度:project GET 应直接暴露 custom_type_name
    p_get = client.get(f"/api/projects/{project_id}", headers=h).json()
    assert p_get["custom_type_name"] == "舞台剧"


def test_custom_type_name_dropped_when_type_is_not_generic(
    client: TestClient, make_user
):
    """type='novel' + 误传 custom_type_name → 静默丢弃。"""
    user = make_user("hero")
    h = user["headers"]
    r = client.post(
        "/api/projects", headers=h,
        json={"name": "测试小说", "type": "novel", "custom_type_name": "这个不该存"},
    )
    assert r.status_code == 201
    assert r.json()["custom_type_name"] is None


def test_build_scene_uses_custom_type_label_when_present(
    client: TestClient, make_user
):
    """_build_scene_from_project 在 type=generic + custom_type_name 时,
    场景描述里出现自定义字符串而非"故事"兜底。"""
    import sqlite3
    from app.config import settings
    from app.services.simulation_service import _build_scene_from_project

    user = make_user("hero")
    h = user["headers"]

    p = client.post(
        "/api/projects", headers=h,
        json={"name": "原创剧本", "type": "generic", "custom_type_name": "广播剧"},
    ).json()
    project_id = p["id"]
    for n in ["角色 0", "角色 1", "角色 2"]:
        client.post(
            f"/api/projects/{project_id}/characters",
            headers=h, json={"name": n},
        )

    # 直接调 service(不走 router)读 scene 字符串
    conn = sqlite3.connect(str(settings.db_abs_path))
    conn.row_factory = sqlite3.Row
    snapshot = [{"id": "_", "name": f"角色 {i}", "identity": ""} for i in range(3)]
    scene = _build_scene_from_project(conn, project_id, snapshot)
    conn.close()

    assert "广播剧" in scene
    assert "小说" not in scene


# ============================================================
# H. Sprint 3.A 末尾态(end mode)
# ============================================================

def _create_end_mode_project_with_ready_upload(
    client: TestClient,
    headers: dict,
    project_name: str,
    chars: list[dict],
    upload_text: str,
    user_id: str,
) -> tuple[str, dict[str, str], str]:
    """末尾态测试 fixture:
      1. 创建 mode='end' 项目 + 角色
      2. 直接 INSERT 一行 state='ready' 的 uploads(绕过 file_parser + extract_service 链路)
      3. 把 upload_text 写到 storage_path 对应的磁盘位置(get_project_tail_excerpt 会 parse_file 读)

    Returns: (project_id, char_ids, upload_id)
    """
    import sqlite3
    import uuid
    from app.config import settings
    from app.services.project_service import iso_now

    # 1. 创建项目(mode=end)
    p = client.post(
        "/api/projects", headers=headers,
        json={
            "name": project_name, "type": "novel", "mode": "end", "tags": [],
        },
    ).json()
    project_id = p["id"]

    # 2. 创建角色
    char_ids: dict[str, str] = {}
    for c in chars:
        r = client.post(
            f"/api/projects/{project_id}/characters", headers=headers, json=c,
        ).json()
        char_ids[c["name"]] = r["id"]

    # 3. 写磁盘 .txt + INSERT ready upload
    upload_id = str(uuid.uuid4())
    storage_path = f"{user_id}/{upload_id}.txt"
    abs_path = settings.uploads_abs_dir / storage_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(upload_text, encoding="utf-8")

    conn = sqlite3.connect(str(settings.db_abs_path))
    try:
        conn.execute(
            "INSERT INTO uploads "
            "(id, project_id, user_id, filename, storage_path, mime_type, "
            " size_bytes, sha256, parsed_text_chars, state, error_message, uploaded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ready', NULL, ?)",
            (
                upload_id, project_id, user_id, f"{project_name}.txt", storage_path,
                "text/plain", len(upload_text.encode("utf-8")), "x" * 64,
                len(upload_text), iso_now(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return project_id, char_ids, upload_id


def test_end_mode_create_caches_tail_excerpt_and_clears_counterfactuals(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """末尾态 happy path:
       - sim 创建成功 + state=done
       - simulations.original_tail_excerpt 落库(且来自 upload 末段)
       - director / composer prompt 注入了原作末段(验证 _build_director_user_prompt 的调用文本)
    """
    user = make_user("hero")
    h = user["headers"]

    # 原作约 1200 字 — 短于 default 2000,应整体返回
    upload_text = (
        "话说那夜寒风彻骨,客栈灯火摇曳,众人围炉而坐。"
        "李寻欢饮尽杯中酒,缓缓抬眼望向门外,只见雪压松梢,银装素裹。"
        "孙小红轻声道:'师兄,这一程路怕是不好走。'"
        "上官金虹冷笑一声,负手立于窗前,任夜风灌入袍袖。"
        "三人就此各怀心事,谁也不再开口,只听得屋外雪声簌簌。"
    ) * 5  # 重复让文本到 ~1000 字
    project_id, char_ids, _ = _create_end_mode_project_with_ready_upload(
        client, h, "江湖夜雨", chars=[
            {"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"},
        ],
        upload_text=upload_text, user_id=user["user_id"],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)

    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "夜深之后,他们各自回房之前发生了什么",
            "reshape_percent": 10,
            # 故意传非空 selected_counterfactual_ids — 末尾态应静默清空
            "selected_counterfactual_ids": ["non-existent-id-1", "non-existent-id-2"],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["state"] == "done"
    sim_id = body["simulation_id"]

    # detail 暴露 original_tail_excerpt(SimulationFull schema)
    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    assert detail["original_tail_excerpt"] is not None
    tail = detail["original_tail_excerpt"]
    # 末段应包含原文末尾的标志性句(整体短于 2000,smart cut 返回完整)
    assert "雪声簌簌" in tail

    # director / composer 收到了带"原作末段"标签的 prompt
    json_calls = patched_simulation_llm.json_calls
    director_user_prompts = [
        call.get("user_prompt", "") for call in json_calls
        if "你的任务:编排第" in call.get("user_prompt", "")
    ]
    assert len(director_user_prompts) >= 1
    assert "【原作末段" in director_user_prompts[0]
    assert "雪声簌簌" in director_user_prompts[0]

    text_calls = patched_simulation_llm.text_calls
    composer_prompt = text_calls[-1].get("user_prompt", "") if text_calls else ""
    assert "【原作末段" in composer_prompt
    assert "雪声簌簌" in composer_prompt


def test_end_mode_without_ready_upload_returns_422(
    client: TestClient, make_user, patched_simulation_llm,
):
    """末尾态项目无 ready upload → 422 END_MODE_NO_UPLOAD。"""
    user = make_user("hero")
    h = user["headers"]

    p = client.post(
        "/api/projects", headers=h,
        json={"name": "未上传作品", "type": "novel", "mode": "end"},
    ).json()
    project_id = p["id"]
    for n in ["甲", "乙", "丙"]:
        client.post(
            f"/api/projects/{project_id}/characters",
            headers=h, json={"name": n},
        )

    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "测试末尾态无 upload", "reshape_percent": 10},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "END_MODE_NO_UPLOAD"


def test_end_mode_tail_smart_cuts_at_sentence_boundary_for_long_text(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """长文(>2000 字)走 _smart_cut_tail — 末段开头不切在句中。"""
    user = make_user("hero")
    h = user["headers"]

    # 构造 ~3500 字文本:前半铺垫(应被裁掉)+ 末段(应保留)
    paragraph = "客栈夜深,众人各怀心事,雪压松梢,银装素裹。" * 80
    long_text = paragraph + "。" + "李寻欢饮尽杯中酒,缓缓抬眼望向门外。" * 50
    assert len(long_text) > 2500

    project_id, char_ids, _ = _create_end_mode_project_with_ready_upload(
        client, h, "长文测试", chars=[
            {"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"},
        ],
        upload_text=long_text, user_id=user["user_id"],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)

    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "原作末段之后,角色们各怀心事走向不同方向", "reshape_percent": 10},
    )
    assert r.status_code == 201, r.text
    sim_id = r.json()["simulation_id"]
    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    tail = detail["original_tail_excerpt"]
    # 智能切割:第一个字应是中文(不是标点 / 不是半字)
    assert tail and tail[0] not in "。!?,;,;"
    # 末段长度合理(≤ DEFAULT_TAIL_EXCERPT_CHARS=2000 + 一点边界冗余)
    assert len(tail) <= 2000
    # 末段确实从尾部抓的(应包含末尾段文字,不应包含开头铺垫的早期句子)
    assert "缓缓抬眼" in tail


def test_end_mode_smart_cut_unit_no_boundary_hard_slice():
    """无标点超长文本 → 硬切返回(不挂)。"""
    from app.services.upload_service import _smart_cut_tail

    no_punct = "千万年来雪山静默" * 200   # 无 句号 / 换行 / !? 仅"。"
    # 实际 "。" 上面没有,只是有重复字符串(没有 。!?\n\n 标点)
    no_punct_clean = "雪山静默荒原" * 200
    result = _smart_cut_tail(no_punct_clean, 100)
    assert len(result) <= 100
    # 应该是后 100 字
    assert result == no_punct_clean[-100:]


def test_end_mode_smart_cut_unit_short_text_returns_whole():
    """文本短于 max_chars → 整体返回。"""
    from app.services.upload_service import _smart_cut_tail

    short = "短文本。只有一句话。"
    result = _smart_cut_tail(short, 100)
    assert result == short


def test_end_mode_cross_user_returns_404(
    client: TestClient, make_user, patched_simulation_llm,
):
    """跨用户访问末尾态项目 → 404(走通用资源鉴权)。"""
    alice = make_user("alice")
    bob = make_user("bob")
    project_id, _, _ = _create_end_mode_project_with_ready_upload(
        client, alice["headers"], "alice 的末尾项目", chars=[
            {"name": "甲"}, {"name": "乙"}, {"name": "丙"},
        ],
        upload_text="测试原文末尾内容。", user_id=alice["user_id"],
    )
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=bob["headers"],
        json={"divergence": "bob 试图在 alice 的末尾项目跑推演", "reshape_percent": 10},
    )
    assert r.status_code == 404


def test_end_mode_initial_mode_not_affected_no_tail_excerpt(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """初始态项目不走 tail excerpt 路径,original_tail_excerpt 应为 None。"""
    user = make_user("hero")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "初始项目(对比)",
        chars=[{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)

    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "测试初始态不走 end 路径", "reshape_percent": 10},
    )
    assert r.status_code == 201, r.text
    sim_id = r.json()["simulation_id"]
    detail = client.get(f"/api/simulations/{sim_id}", headers=h).json()
    assert detail["original_tail_excerpt"] is None


# Sprint 6.A2 M3.D-fix5(2026-05-19)— 用户实测末尾态产物两个 banner 同时显
def test_end_mode_with_snowball_skips_tail_excerpt(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """末尾态项目 + context_simulation_ids 非空 → 跳过原作末段缓存(fix5)。

    用户实测:在末尾态项目里勾"接续之前的剧情",期望从前续写产物末尾继续写。
    旧逻辑两个 section 同时进 prompt,LLM 优先听"原作末段"导致"滚雪球功能像摆设"。

    新行为:
      - sim2.original_tail_excerpt 为 None(不缓存)
      - director / composer prompt 中不再含"【原作末段"标签
      - 但仍含"【前序产物"标签(滚雪球正常走)
      - 详情页 banner 自动消失(前端 v-if 走 original_tail_excerpt 非空)
    """
    import sqlite3
    from app.config import settings

    user = make_user("hero")
    h = user["headers"]
    # 升 plan 让 ≥2 次推演不被免费配额挡
    conn = sqlite3.connect(str(settings.db_abs_path))
    conn.execute("UPDATE users SET plan='pro' WHERE id=?", (user["user_id"],))
    conn.commit()
    conn.close()

    upload_text = (
        "话说那夜寒风彻骨,客栈灯火摇曳,众人围炉而坐。"
        "李寻欢饮尽杯中酒,缓缓抬眼望向门外,只见雪压松梢,银装素裹。"
        "孙小红轻声道:'师兄,这一程路怕是不好走。'"
        "上官金虹冷笑一声,负手立于窗前,任夜风灌入袍袖。"
        "三人就此各怀心事,谁也不再开口,只听得屋外雪声簌簌。"
    ) * 5
    project_id, char_ids, _ = _create_end_mode_project_with_ready_upload(
        client, h, "江湖夜雨", chars=[
            {"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"},
        ],
        upload_text=upload_text, user_id=user["user_id"],
    )

    # 第 1 次推演:末尾态首次 → 应缓存原作末段(老语义)
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r1 = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "末尾态首次续写不写锚点",
            "reshape_percent": 10,
        },
    )
    assert r1.status_code == 201, r1.text
    first_sim_id = r1.json()["simulation_id"]
    first_detail = client.get(f"/api/simulations/{first_sim_id}", headers=h).json()
    # 首次:original_tail_excerpt 非空(老语义不变)
    assert first_detail["original_tail_excerpt"] is not None, (
        "末尾态首次续写应缓存原作末段"
    )

    # 第 2 次推演:末尾态 + 接续第 1 次 → fix5 跳过原作末段
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    json_call_count_before = len(patched_simulation_llm.json_calls)
    text_call_count_before = len(patched_simulation_llm.text_calls)
    r2 = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={
            "divergence": "在前续写基础上继续推进",
            "reshape_percent": 10,
            "context_simulation_ids": [first_sim_id],
        },
    )
    assert r2.status_code == 201, r2.text
    next_sim_id = r2.json()["simulation_id"]

    # 关键 1:sim2.original_tail_excerpt 应为 None(fix5 核心契约)
    detail = client.get(f"/api/simulations/{next_sim_id}", headers=h).json()
    assert detail["original_tail_excerpt"] is None, (
        "末尾态 + context_simulation_ids 非空时,original_tail_excerpt 应跳过缓存"
    )
    # 关键 2:context_simulation_ids 仍正常落库
    assert detail["context_simulation_ids"] == [first_sim_id]

    # 关键 3:第 2 次的 director / composer prompt 不应含"【原作末段"section
    # 但应含"【前序产物"(滚雪球正常)
    new_json_calls = patched_simulation_llm.json_calls[json_call_count_before:]
    new_text_calls = patched_simulation_llm.text_calls[text_call_count_before:]
    director_user_prompts = [
        call.get("user_prompt", "") for call in new_json_calls
        if "你的任务:编排第" in call.get("user_prompt", "")
    ]
    assert len(director_user_prompts) >= 1
    for dp in director_user_prompts:
        assert "【原作末段" not in dp, (
            "末尾态 + 滚雪球时 director prompt 不应再注入原作末段 section"
        )
        assert "【前序产物" in dp, (
            "末尾态 + 滚雪球时 director prompt 应仍注入前序产物 section"
        )

    if new_text_calls:
        composer_prompt = new_text_calls[-1].get("user_prompt", "")
        assert "【原作末段" not in composer_prompt, (
            "末尾态 + 滚雪球时 composer prompt 不应再注入原作末段 section"
        )
        assert "【前序产物" in composer_prompt, (
            "末尾态 + 滚雪球时 composer prompt 应仍注入前序产物 section"
        )


# ============================================================
# Sprint 6.A2 路线图 #2(2026-05-22):角色情绪曲线可视化
# GET /api/simulations/{sim_id}/emotional_states 端点 endpoint-level 测试
# happy path 数据填充由 evolution 集成测覆盖,本组只测 wiring + 鉴权
# ============================================================

def test_emotional_states_empty_sim_returns_empty_list(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """quick 模式 sim 没跑 emotional_state_tracker,表无数据 → endpoint 返 []。"""
    user = make_user("emotion-empty")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "情绪空 sim",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果情绪曲线 endpoint 在空 sim 上仍可调", "reshape_percent": 10},
    )
    sim_id = r.json()["simulation_id"]

    r2 = client.get(f"/api/simulations/{sim_id}/emotional_states", headers=h)
    assert r2.status_code == 200
    body = r2.json()
    assert isinstance(body, list)
    assert body == []


def test_emotional_states_cross_user_returns_404(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """跨用户访问情绪曲线 → 404(复用 get_simulation_or_404,不暴露资源存在性)。"""
    u1 = make_user("emo-alice")
    u2 = make_user("emo-bob")
    project_id, char_ids = _create_project_with_chars(
        client, u1["headers"], "u1 情绪项目",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=u1["headers"],
        json={"divergence": "如果跨用户访问情绪曲线就 404", "reshape_percent": 10},
    )
    sim_id = r.json()["simulation_id"]

    r404 = client.get(f"/api/simulations/{sim_id}/emotional_states", headers=u2["headers"])
    assert r404.status_code == 404


def test_emotional_states_with_seeded_data_returns_records(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """直接往 character_emotional_states 表插测试数据,验证 endpoint LEFT JOIN + 序列化正确。"""
    import json as _json
    from app.db import get_connection

    user = make_user("emo-seeded")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "情绪曲线 happy",
        chars=[{"name": "李寻欢"}, {"name": "孙小红"}, {"name": "上官金虹"}],
    )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=h,
        json={"divergence": "如果情绪表里有真实数据 endpoint 能正确返回", "reshape_percent": 10},
    )
    sim_id = r.json()["simulation_id"]

    # 直接插 2 角色 × 3 幕 = 6 条 emotional_state 行,模拟 evolution 已跑
    conn = get_connection()
    try:
        seed_rows = [
            (char_ids["李寻欢"], 0, {"joy": 7, "sadness": 1, "fear": 0, "anger": 0,
                                     "surprise": 2, "disgust": 0, "trust": 6, "anticipation": 5}),
            (char_ids["李寻欢"], 1, {"joy": 3, "sadness": 6, "fear": 2, "anger": 1,
                                     "surprise": 0, "disgust": 0, "trust": 4, "anticipation": 2}),
            (char_ids["李寻欢"], 2, {"joy": 2, "sadness": 8, "fear": 5, "anger": 3,
                                     "surprise": 0, "disgust": 1, "trust": 2, "anticipation": 1}),
            (char_ids["孙小红"], 0, {"joy": 8, "sadness": 0, "fear": 0, "anger": 0,
                                     "surprise": 3, "disgust": 0, "trust": 7, "anticipation": 6}),
            (char_ids["孙小红"], 1, {"joy": 5, "sadness": 3, "fear": 1, "anger": 0,
                                     "surprise": 2, "disgust": 0, "trust": 6, "anticipation": 4}),
            (char_ids["孙小红"], 2, {"joy": 2, "sadness": 7, "fear": 4, "anger": 2,
                                     "surprise": 1, "disgust": 0, "trust": 3, "anticipation": 2}),
        ]
        for char_id, scene_idx, emo in seed_rows:
            conn.execute(
                """INSERT INTO character_emotional_states
                   (id, simulation_id, character_id, scene_index,
                    emotion_json, rationale, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                (f"es-{char_id[:6]}-{scene_idx}", sim_id, char_id, scene_idx,
                 _json.dumps(emo), f"测试 rationale {scene_idx}"),
            )
        conn.commit()
    finally:
        conn.close()

    r2 = client.get(f"/api/simulations/{sim_id}/emotional_states", headers=h)
    assert r2.status_code == 200
    body = r2.json()
    assert len(body) == 6

    # 验证排序(character_id ASC, scene_index ASC)
    chars_in_order = [(x["character_id"], x["scene_index"]) for x in body]
    assert chars_in_order == sorted(chars_in_order)

    # 验证字段完整 + 8 维 emotion 都在
    first = body[0]
    assert set(first.keys()) == {"character_id", "character_name", "scene_index", "emotion", "rationale"}
    assert set(first["emotion"].keys()) == {
        "joy", "sadness", "anger", "fear",
        "surprise", "disgust", "trust", "anticipation",
    }
    assert all(isinstance(v, int) and 0 <= v <= 10 for v in first["emotion"].values())
    # character_name 已 JOIN 拼出
    assert first["character_name"] in ("李寻欢", "孙小红", "上官金虹")
