"""去 IP 化导出端到端测试 — Sprint 2.E 验收基线。

测试分组:
  A. 字典生成:happy + 无角色 422 + 重新生成覆盖 + LLM 兜底
  B. apply_de_ip:longest-match-first / 空字典 / 缺字段
  C. 导出:original / de_ip happy / sim 非 done 422 / 无字典 422
  D. 鉴权:跨用户 404 / 401
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.services.simulation_service import reshape_to_rounds


# ============================================================
# helpers
# ============================================================

def _create_project_with_chars(
    client: TestClient, headers: dict, project_name: str, chars: list[dict],
    mode: str = "middle",
) -> tuple[str, dict[str, str]]:
    p = client.post(
        "/api/projects", headers=headers,
        json={"name": project_name, "type": "novel", "mode": mode, "tags": []},
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
            "location": f"地点(第 {r} 轮)",
            "time_advance": "片刻后",
            "round_seed": f"第 {r} 轮契机",
            "narrator_note": "灯火摇曳",
        })
        ctrl.json_queue.append({
            "monologue": "心下一沉",
            "action": "缓缓抬眼",
            "dialogue": f"第 {r} 轮的对白。",
        })
    # narrative 包含可替换的人名 + 地名
    ctrl.text_queue.append(
        "# 测试 narrative\n\n林黛玉走进大观园,贾府门前贾母正等着她。\n"
        "贾宝玉迎了上来,黛玉笑了一下。\n\n林黛玉是聪明的姑娘,她和贾宝玉很熟。"
    )


def _create_done_sim(client, headers, patched_simulation_llm, project_id=None, char_ids=None):
    if project_id is None:
        project_id, char_ids = _create_project_with_chars(
            client, headers, "红楼梦",
            chars=[
                {"name": "林黛玉"},
                {"name": "贾宝玉"},
                {"name": "贾母"},
            ],
        )
    _preload_minimal_simulation_llm(patched_simulation_llm, char_ids)
    r = client.post(
        f"/api/projects/{project_id}/simulations", headers=headers,
        json={"divergence": "如果黛玉性格豁达开朗", "reshape_percent": 10},
    )
    return project_id, r.json()["simulation_id"], char_ids


def _patch_de_ip_llm(monkeypatch, mapping, notes=None, usage=None, raises=None):
    """注入 de_ip_service.call_llm_json。"""
    used = {"input_tokens": 800, "output_tokens": 300}
    if usage is not None:
        used = usage
    output = {"mapping": mapping, "notes": notes}

    def fake(system_prompt, user_input, **kwargs):
        if raises is not None:
            raise raises
        return output, used

    monkeypatch.setattr(
        "app.services.de_ip_service.call_llm_json", fake,
    )


# ============================================================
# A. 字典生成
# ============================================================

def test_generate_de_ip_dictionary_happy(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """生成字典 → 拿到 mapping + notes。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, _, _ = _create_done_sim(client, h, patched_simulation_llm)
    _patch_de_ip_llm(monkeypatch, mapping={
        "林黛玉": "黛影", "贾宝玉": "黄少卿", "贾母": "仁母",
        "贾府": "仁府", "大观园": "锦绣园",
    }, notes="清代古典审美保留")

    r = client.post(
        f"/api/projects/{project_id}/de_ip_dictionary", headers=h,
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["mapping"]["林黛玉"] == "黛影"
    assert body["mapping"]["贾府"] == "仁府"
    assert body["notes"] == "清代古典审美保留"


def test_generate_rejects_project_without_characters(
    client: TestClient, make_user, monkeypatch,
):
    """项目无角色 → 422 NO_CHARACTERS_TO_REPLACE。"""
    user = make_user("alice")
    h = user["headers"]
    p = client.post(
        "/api/projects", headers=h,
        json={"name": "空项目", "type": "novel", "mode": "middle"},
    ).json()
    r = client.post(
        f"/api/projects/{p['id']}/de_ip_dictionary", headers=h,
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "NO_CHARACTERS_TO_REPLACE"


def test_regenerate_overwrites_mapping_keeps_created_at(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """重新生成 → mapping 被覆盖,created_at 保留(updated_at 刷新)。"""
    import time
    user = make_user("alice")
    h = user["headers"]
    project_id, _, _ = _create_done_sim(client, h, patched_simulation_llm)

    _patch_de_ip_llm(monkeypatch, mapping={"林黛玉": "黛影"})
    r1 = client.post(f"/api/projects/{project_id}/de_ip_dictionary", headers=h)
    first_created = r1.json()["created_at"]
    first_updated = r1.json()["updated_at"]
    time.sleep(1.1)   # 等 1s+ 让 iso_now 秒精度走开

    _patch_de_ip_llm(monkeypatch, mapping={"林黛玉": "林婉清", "贾宝玉": "贾少卿"})
    r2 = client.post(f"/api/projects/{project_id}/de_ip_dictionary", headers=h)
    body2 = r2.json()
    assert body2["mapping"]["林黛玉"] == "林婉清"   # 已覆盖
    assert "贾宝玉" in body2["mapping"]              # 新加
    assert body2["created_at"] == first_created     # 保留首次时间
    assert body2["updated_at"] != first_updated     # 刷新


def test_generate_cleans_invalid_mapping_entries(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """LLM 输出含非法 mapping(空 / 同名 / 非 str)→ 全部清洗。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, _, _ = _create_done_sim(client, h, patched_simulation_llm)
    _patch_de_ip_llm(monkeypatch, mapping={
        "林黛玉": "黛影",       # OK
        "贾宝玉": "贾宝玉",      # 同名 → drop
        "": "x",                # 空 key → drop
        "y": "",                # 空 value → drop
        "贾母": "仁母",          # OK
    })
    r = client.post(f"/api/projects/{project_id}/de_ip_dictionary", headers=h)
    body = r.json()
    assert "林黛玉" in body["mapping"]
    assert "贾母" in body["mapping"]
    assert "贾宝玉" not in body["mapping"]
    assert "" not in body["mapping"]
    assert len(body["mapping"]) == 2


# ============================================================
# B. apply_de_ip 算法
# ============================================================

def test_apply_de_ip_longest_match_first():
    """长 key 先替换 — '贾母' 优先于 '贾',防被前缀吃。"""
    from app.services.de_ip_service import apply_de_ip
    mapping = {"贾母": "仁母", "贾": "仁", "贾宝玉": "黄少卿"}
    text = "贾母和贾宝玉来到贾府,贾母又叮嘱了贾。"
    result, counts = apply_de_ip(text, mapping)
    # 期望:
    #   "贾宝玉" (3 字) 先替换 → 1 次
    #   "贾母" (2 字) 再替换 → 2 次("贾母和" / "贾府,贾母")
    #   "贾"  (1 字) 最后替换剩余 → "贾府"中的"贾"→"仁",末尾"贾"→"仁"
    assert "贾母" not in result, f"贾母未替换:{result}"
    assert "贾宝玉" not in result
    assert "仁母" in result
    assert "黄少卿" in result
    assert counts["贾母"] == 2
    assert counts["贾宝玉"] == 1


def test_apply_de_ip_empty_mapping_returns_text_unchanged():
    from app.services.de_ip_service import apply_de_ip
    text = "林黛玉走进大观园"
    result, counts = apply_de_ip(text, {})
    assert result == text
    assert counts == {}


def test_apply_de_ip_empty_text():
    from app.services.de_ip_service import apply_de_ip
    result, counts = apply_de_ip("", {"a": "b"})
    assert result == ""
    assert counts == {}


# ============================================================
# C. 导出
# ============================================================

def test_export_original_version_no_replacement(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """version='original' → 直接拿 narrative,无替换。"""
    user = make_user("alice")
    h = user["headers"]
    _, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)
    r = client.post(
        f"/api/simulations/{sim_id}/export", headers=h,
        json={"version": "original"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["version"] == "original"
    assert body["replacements"] is None
    assert body["total_replacements"] == 0
    # 原作角色名应在内容里
    assert "林黛玉" in body["content"]
    assert "大观园" in body["content"]
    assert body["filename"].endswith(".md")
    assert "_deip" not in body["filename"]


def test_export_de_ip_replaces_names_and_returns_trail(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    """version='de_ip' → 替换全部 + 返 trail。"""
    user = make_user("alice")
    h = user["headers"]
    project_id, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)
    _patch_de_ip_llm(monkeypatch, mapping={
        "林黛玉": "黛影", "贾宝玉": "黄少卿", "贾母": "仁母",
        "贾府": "仁府", "大观园": "锦绣园",
    })
    client.post(f"/api/projects/{project_id}/de_ip_dictionary", headers=h)

    r = client.post(
        f"/api/simulations/{sim_id}/export", headers=h,
        json={"version": "de_ip"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == "de_ip"
    assert body["filename"].endswith("_deip.md")
    # 原作角色名全被替换
    assert "林黛玉" not in body["content"]
    assert "大观园" not in body["content"]
    # 替换名出现
    assert "黛影" in body["content"]
    assert "锦绣园" in body["content"]
    # trail 按 count 降序
    assert body["replacements"] is not None
    assert body["total_replacements"] > 0
    # 林黛玉在 narrative 出现 3 次(根据 _preload_minimal_simulation_llm 文本)
    lin_entry = next((r for r in body["replacements"] if r["original"] == "林黛玉"), None)
    assert lin_entry is not None
    assert lin_entry["replaced"] == "黛影"
    assert lin_entry["count"] >= 2   # narrative 中至少 2 次


def test_export_de_ip_without_dictionary_returns_422(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    """version='de_ip' 但项目无字典 → 422 NO_DE_IP_DICTIONARY。"""
    user = make_user("alice")
    h = user["headers"]
    _, sim_id, _ = _create_done_sim(client, h, patched_simulation_llm)
    r = client.post(
        f"/api/simulations/{sim_id}/export", headers=h,
        json={"version": "de_ip"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "NO_DE_IP_DICTIONARY"


def test_export_simulation_not_done_returns_422(
    client: TestClient, make_user, monkeypatch,
):
    """sim 非 done → 422 SIMULATION_NOT_EXPORTABLE。"""
    import os
    import uuid
    from pathlib import Path
    from app.db import _connect, transaction, execute as db_execute
    from app.services.project_service import iso_now

    user = make_user("alice")
    h = user["headers"]
    project_id, char_ids = _create_project_with_chars(
        client, h, "测试", [{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    # 直接 INSERT queued sim
    test_db = Path(os.environ["HUIMENG_DB_PATH"])
    sim_id = str(uuid.uuid4())
    conn = _connect(test_db)
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

    r = client.post(
        f"/api/simulations/{sim_id}/export", headers=h,
        json={"version": "original"},
    )
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "SIMULATION_NOT_EXPORTABLE"


# ============================================================
# D. 鉴权
# ============================================================

def test_get_dictionary_returns_404_when_none(
    client: TestClient, make_user,
):
    user = make_user("alice")
    h = user["headers"]
    project_id, _ = _create_project_with_chars(
        client, h, "未生成字典", [{"name": "甲"}, {"name": "乙"}, {"name": "丙"}],
    )
    r = client.get(f"/api/projects/{project_id}/de_ip_dictionary", headers=h)
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "DE_IP_DICTIONARY_NOT_FOUND"


def test_generate_cross_user_returns_404(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner, monkeypatch,
):
    alice = make_user("alice")
    bob = make_user("bob")
    project_id, _, _ = _create_done_sim(client, alice["headers"], patched_simulation_llm)
    _patch_de_ip_llm(monkeypatch, mapping={"林黛玉": "黛影"})
    r = client.post(
        f"/api/projects/{project_id}/de_ip_dictionary", headers=bob["headers"],
    )
    assert r.status_code == 404


def test_export_cross_user_returns_404(
    client: TestClient, make_user, patched_simulation_llm, sync_simulation_runner,
):
    alice = make_user("alice")
    bob = make_user("bob")
    _, sim_id, _ = _create_done_sim(client, alice["headers"], patched_simulation_llm)
    r = client.post(
        f"/api/simulations/{sim_id}/export", headers=bob["headers"],
        json={"version": "original"},
    )
    assert r.status_code == 404


def test_export_unauthenticated_returns_401(client: TestClient):
    r = client.post("/api/simulations/x/export", json={"version": "original"})
    assert r.status_code == 401
