"""Sprint 6.A1(2026-05-18)— 主角判定 + agent 档案补全测试。

覆盖范围:
  - protagonist_judger:4 维度评分 + 上限 + user_pinned 不覆盖
  - characters PATCH 加 is_protagonist 字段后,自动设 protagonist_user_pinned=true
  - 2 个新 endpoint:POST /projects/{id}/judge_protagonists / GET /projects/{id}/protagonists
  - migration 035 字段默认值正确(老角色不 crash)
  - agent_profile_enricher:已填字段不覆盖(幂等)
"""
from __future__ import annotations

import json

from fastapi.testclient import TestClient


# ============================================================
# Helpers
# ============================================================

def _upgrade_to_super_max(user_id: str) -> None:
    """升级到超级 max 档(characters_per_project=100,够 20 角色用例)。"""
    from app.db import get_connection
    conn = get_connection()
    try:
        conn.execute("UPDATE users SET plan='super_max' WHERE id=?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def _create_project_with_characters(
    client: TestClient, headers: dict, char_names: list[str],
    user_id: str | None = None,
) -> tuple[str, dict[str, str]]:
    """造项目 + N 个角色,返回 (project_id, name → char_id 字典)。

    若 N > 10,要先 _upgrade_to_super_max(传 user_id 触发)。
    """
    if user_id is not None and len(char_names) > 10:
        _upgrade_to_super_max(user_id)

    p = client.post(
        "/api/projects", headers=headers,
        json={"name": f"测试_{char_names[0]}", "type": "novel", "tags": []},
    )
    assert p.status_code == 201, p.text
    proj_id = p.json()["id"]
    name_to_id: dict[str, str] = {}
    for n in char_names:
        r = client.post(
            f"/api/projects/{proj_id}/characters", headers=headers,
            json={"name": n},
        )
        assert r.status_code == 201, r.text
        name_to_id[n] = r.json()["id"]
    return proj_id, name_to_id


def _seed_chunk_results(
    proj_id: str, user_id: str,
    appearance_map: dict[str, list[int]],
):
    """直接 SQL 插 graph_extraction_jobs + extract_chunk_results 模拟原作抽取数据。

    appearance_map: { "角色名": [chunk_index_0, chunk_index_1, ...] }
                     每个 chunk_index 出现一次 → entities 含此角色
    """
    from app.db import get_connection
    from app.services.project_service import iso_now
    import uuid

    conn = get_connection()
    try:
        # 1. 造 job(state=done)
        job_id = uuid.uuid4().hex
        upload_id = uuid.uuid4().hex
        now = iso_now()
        conn.execute(
            """INSERT INTO uploads (id, project_id, user_id, filename, storage_path,
                                     mime_type, size_bytes, sha256,
                                     parsed_text_chars, state, uploaded_at)
               VALUES (?, ?, ?, 'test.txt', '/tmp/x', 'text/plain', 100,
                       ?, 1000, 'ready', ?)""",
            (upload_id, proj_id, user_id, f"hash_{upload_id[:8]}", now),
        )
        conn.execute(
            """INSERT INTO graph_extraction_jobs
                (id, project_id, upload_id, user_id, state, is_admin_retag,
                 started_at)
               VALUES (?, ?, ?, ?, 'done', 0, ?)""",
            (job_id, proj_id, upload_id, user_id, now),
        )

        # 2. 计算所有 chunk_index 集合
        all_chunks = set()
        for chunks in appearance_map.values():
            all_chunks.update(chunks)
        if not all_chunks:
            return

        max_chunk = max(all_chunks)
        # 给每个 chunk 写 entities
        for ci in range(max_chunk + 1):
            entities = []
            for name, chunks in appearance_map.items():
                if ci in chunks:
                    entities.append({"name": name, "type": "PERSON"})
            graph_json = json.dumps(
                {"entities": entities, "relations": []},
                ensure_ascii=False,
            )
            conn.execute(
                """INSERT INTO extract_chunk_results
                   (job_id, chunk_index, chunk_text_hash, graph_json,
                    tokens_input, tokens_output, completed_at)
                   VALUES (?, ?, 'hash', ?, 0, 0, ?)""",
                (job_id, ci, graph_json, now),
            )
        conn.commit()
    finally:
        conn.close()


# ============================================================
# protagonist_judger 算法
# ============================================================

def test_judger_returns_zero_protagonists_for_empty_project(
    client: TestClient, make_user,
):
    """空项目 → judger 返 0 主角不 crash。"""
    u = make_user("judger_empty")
    p = client.post(
        "/api/projects", headers=u["headers"],
        json={"name": "空项目", "type": "novel", "tags": []},
    )
    proj_id = p.json()["id"]

    r = client.post(
        f"/api/projects/{proj_id}/judge_protagonists", headers=u["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["judged_count"] == 0
    assert body["protagonist_count"] == 0


def test_judger_picks_high_appearance_character(
    client: TestClient, make_user,
):
    """戏份多的角色被判主角(出场密度维度命中)。"""
    u = make_user("judger_appearance")
    proj_id, name_to_id = _create_project_with_characters(
        client, u["headers"], ["主角A", "路人B"],
    )

    # 主角A 出现 7 次,路人B 出现 1 次
    _seed_chunk_results(proj_id, u["user_id"], {
        "主角A": [0, 1, 2, 3, 4, 5, 6],
        "路人B": [3],
    })

    r = client.post(
        f"/api/projects/{proj_id}/judge_protagonists", headers=u["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["protagonist_count"] >= 1

    # 主角A 应 is_protagonist=true
    r2 = client.get(
        f"/api/projects/{proj_id}/protagonists", headers=u["headers"],
    )
    assert r2.status_code == 200
    protag_names = [c["name"] for c in r2.json()]
    assert "主角A" in protag_names


def test_judger_respects_user_pinned(
    client: TestClient, make_user,
):
    """用户手动改了 is_protagonist → 后续 judge 不覆盖(protagonist_user_pinned)。"""
    u = make_user("judger_pinned")
    proj_id, name_to_id = _create_project_with_characters(
        client, u["headers"], ["不该是主角的人"],
    )
    char_id = name_to_id["不该是主角的人"]

    # 用户手动设为主角(自动 user_pinned=true)
    r = client.patch(
        f"/api/characters/{char_id}", headers=u["headers"],
        json={"is_protagonist": True},
    )
    assert r.status_code == 200
    assert r.json()["is_protagonist"] is True
    assert r.json()["protagonist_user_pinned"] is True

    # judger 跑(没原作数据,4 维度都 0,理论上不会判为主角)
    r2 = client.post(
        f"/api/projects/{proj_id}/judge_protagonists", headers=u["headers"],
    )
    assert r2.status_code == 200
    # skipped_pinned 应 ≥ 1(用户锁定的不被覆盖)
    assert r2.json()["skipped_pinned"] >= 1

    # 验证:用户的"是主角"决定保留
    r3 = client.get(f"/api/characters/{char_id}", headers=u["headers"])
    assert r3.json()["is_protagonist"] is True


def test_judger_protagonist_max_cap(
    client: TestClient, make_user,
):
    """造 20 个高分角色,judger 上限 PROTAGONIST_MAX=15 → 最多 15 个主角。"""
    u = make_user("judger_cap")
    char_names = [f"角色{i:02d}" for i in range(20)]
    proj_id, name_to_id = _create_project_with_characters(
        client, u["headers"], char_names, user_id=u["user_id"],
    )

    # 给所有 20 个角色全高分(每个出现 10 次)
    appearance = {name: list(range(10)) for name in char_names}
    _seed_chunk_results(proj_id, u["user_id"], appearance)

    r = client.post(
        f"/api/projects/{proj_id}/judge_protagonists", headers=u["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    # 候选可能 20 个,但实际 is_protagonist=true 的最多 PROTAGONIST_MAX(15)
    from app.services.protagonist_judger import PROTAGONIST_MAX
    assert body["protagonist_count"] <= PROTAGONIST_MAX


# ============================================================
# Character PATCH 加 is_protagonist 字段
# ============================================================

def test_patch_character_is_protagonist_sets_user_pinned(
    client: TestClient, make_user,
):
    """PATCH 改 is_protagonist=true → protagonist_user_pinned 自动设 true。"""
    u = make_user("patch_protag")
    proj_id, name_to_id = _create_project_with_characters(
        client, u["headers"], ["测试角色"],
    )
    char_id = name_to_id["测试角色"]

    # 初始默认 false
    r0 = client.get(f"/api/characters/{char_id}", headers=u["headers"])
    assert r0.json()["is_protagonist"] is False
    assert r0.json()["protagonist_user_pinned"] is False
    assert r0.json()["protagonist_score"] == 0.0
    assert r0.json()["protagonist_reasons"] == []

    # PATCH 设主角
    r = client.patch(
        f"/api/characters/{char_id}", headers=u["headers"],
        json={"is_protagonist": True},
    )
    assert r.status_code == 200
    assert r.json()["is_protagonist"] is True
    assert r.json()["protagonist_user_pinned"] is True


# ============================================================
# agent_profile_enricher
# ============================================================

def test_enricher_skips_already_filled_fields(
    client: TestClient, make_user, monkeypatch,
):
    """已填的 identity/personality/quotes/no_go_list 字段 → enricher 不覆盖。"""
    u = make_user("enrich_skip")
    proj_id, name_to_id = _create_project_with_characters(
        client, u["headers"], ["全填角色"],
    )
    char_id = name_to_id["全填角色"]

    # 用户已填全部字段(2026-05-27:补 life_status + status_note,enricher 自 P0F.2
    # 起新增 life_status 兜底,旧测试只填 5 个会让 enricher 仍想补 life_status → call_count=1。
    # P0G.2 删了 out_of_baseline_examples,此处不再填)
    client.patch(
        f"/api/characters/{char_id}", headers=u["headers"],
        json={
            "identity": "用户填的身份",
            "personality": "用户填的性格",
            "quotes": ["用户填的台词"],
            "no_go_list": ["用户填的禁忌"],
            "aliases": ["用户填的别名"],   # B5.2(2026-05-27):enricher 现在也会补 aliases,fixture 同步填
            "behavior_baseline": {
                "speech_register": "平和",
                "emotional_intensity": 5,
                "moral_compass": "灰",
            },
            "life_status": "alive",
            "status_note": "在世,小镇商人",   # 非空 status_note → 视为已判定
        },
    )

    # Mock LLM(理论上不会被调用 — 因为所有字段都填了)
    call_count = {"n": 0}

    def fake_call(*args, **kwargs):
        call_count["n"] += 1
        return {
            "identity": "AI 改的",
            "personality": "AI 改的",
            "quotes": ["AI 改的"],
            "no_go_list": ["AI 改的"],
        }, {"input_tokens": 100, "output_tokens": 50}

    monkeypatch.setattr(
        "app.services.agent_profile_enricher.call_llm_json", fake_call,
    )

    r = client.post(
        f"/api/characters/{char_id}/enrich_agent_profile", headers=u["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    # 不应调 LLM
    assert call_count["n"] == 0
    assert body["report"]["error"] == "all_filled"
    # 字段应保持用户填的值
    assert body["character"]["identity"] == "用户填的身份"


def test_enricher_fills_empty_fields_via_llm(
    client: TestClient, make_user, monkeypatch,
):
    """空字段 → enricher 调 LLM 补全 → 新值落库。"""
    u = make_user("enrich_fill")
    proj_id, name_to_id = _create_project_with_characters(
        client, u["headers"], ["空白角色"],
    )
    char_id = name_to_id["空白角色"]

    def fake_call(*args, **kwargs):
        return {
            "identity": "我是 AI 推断的身份",
            "personality": "我是 AI 推断的性格,2 句话内描述行为倾向。",
            "quotes": ["AI 给的台词 1", "AI 给的台词 2"],
            "no_go_list": ["AI 给的禁忌 1"],
        }, {"input_tokens": 500, "output_tokens": 300}

    monkeypatch.setattr(
        "app.services.agent_profile_enricher.call_llm_json", fake_call,
    )

    r = client.post(
        f"/api/characters/{char_id}/enrich_agent_profile", headers=u["headers"],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["error"] is None
    assert set(body["report"]["updated_fields"]) == {
        "identity", "personality", "quotes", "no_go_list",
    }
    assert body["character"]["identity"] == "我是 AI 推断的身份"
    assert body["character"]["quotes"] == ["AI 给的台词 1", "AI 给的台词 2"]
    assert body["character"]["no_go_list"] == ["AI 给的禁忌 1"]


def test_get_protagonists_returns_only_is_protagonist_true(
    client: TestClient, make_user,
):
    """GET /projects/{id}/protagonists 只返 is_protagonist=true 的角色,按 score 倒序。"""
    u = make_user("list_protag")
    proj_id, name_to_id = _create_project_with_characters(
        client, u["headers"], ["主角1", "主角2", "配角"],
    )

    # 手动设 2 个主角(强制 user_pinned)
    client.patch(
        f"/api/characters/{name_to_id['主角1']}", headers=u["headers"],
        json={"is_protagonist": True},
    )
    client.patch(
        f"/api/characters/{name_to_id['主角2']}", headers=u["headers"],
        json={"is_protagonist": True},
    )

    r = client.get(
        f"/api/projects/{proj_id}/protagonists", headers=u["headers"],
    )
    assert r.status_code == 200
    names = [c["name"] for c in r.json()]
    assert "主角1" in names
    assert "主角2" in names
    assert "配角" not in names
    assert len(names) == 2
