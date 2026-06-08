"""SP-3.1 知识边界 AI 推断 测试(2026-05-29)."""
from __future__ import annotations

import uuid

import pytest

from app.db import execute, fetch_all, fetch_one, get_connection


def _run(fn):
    """开新 conn 跑 fn 保证 close(防 Windows teardown PermissionError)."""
    conn = get_connection()
    try:
        return fn(conn)
    finally:
        conn.close()


def _make_project_with_chars(user_id: str, char_count: int = 3) -> tuple[str, list[str]]:
    pid = str(uuid.uuid4())
    cids: list[str] = []
    now = "2026-05-29T00:00:00+00:00"
    conn = get_connection()
    try:
        execute(
            conn,
            "INSERT INTO projects "
            "(id, user_id, name, type, custom_type_name, tags, mode, "
            " created_at, updated_at) "
            "VALUES (?, ?, '测试', 'novel', NULL, '[]', 'middle', ?, ?)",
            (pid, user_id, now, now),
        )
        for i in range(char_count):
            cid = str(uuid.uuid4())
            cids.append(cid)
            is_protag = 1 if i == 0 else 0
            execute(
                conn,
                "INSERT INTO characters "
                "(id, project_id, name, identity, personality, quotes, no_go_list, "
                " position_x, position_y, position_z, color, created_at, updated_at, "
                " is_protagonist) "
                "VALUES (?, ?, ?, ?, '', '[]', '[]', 0, 0, 0, NULL, ?, ?, ?)",
                (cid, pid, ["渡边", "直子", "绿子", "玲子"][i],
                 f"角色{i}身份", now, now, is_protag),
            )
        conn.commit()
        return pid, cids
    finally:
        conn.close()


def test_infer_upload_mode_writes_facts_and_knowledge(monkeypatch, make_user):
    """upload 路径 + LLM 返合法 → 写 facts + character_knowledge."""
    from app.services import knowledge_boundaries_inferer as kbi

    u = make_user("kb_ok")
    pid, cids = _make_project_with_chars(u["user_id"], char_count=3)

    monkeypatch.setattr(
        kbi, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1500,
    )

    captured_input: dict = {}

    def fake_llm(system_prompt, user_input, **kwargs):
        captured_input.update(user_input)
        return (
            {
                "facts": [
                    {"description": "直子已自杀", "first_revealed_scene": 8, "is_sensitive": True},
                    {"description": "渡边和绿子相识于校园", "first_revealed_scene": 3, "is_sensitive": False},
                ],
                "character_knowledge": [
                    {"character_name": "渡边", "fact_index": 0, "known_since_scene": 8, "confidence": "confirmed"},
                    {"character_name": "渡边", "fact_index": 1, "known_since_scene": 3, "confidence": "confirmed"},
                    {"character_name": "绿子", "fact_index": 1, "known_since_scene": 3, "confidence": "confirmed"},
                ],
                "reasoning": "基于头中尾采样,直子精神状态在中段揭示",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(kbi, "call_llm_json", fake_llm)

    result = _run(lambda c: kbi.infer_knowledge_boundaries(c, pid))
    assert captured_input.get("mode_source") == "upload"
    assert len(result["facts"]) == 2
    assert len(result["character_knowledge"]) == 3

    # cache 写入
    report = _run(lambda c: kbi.cache_knowledge_boundaries(c, pid, result, overwrite=False))
    assert report["applied"] is True
    assert len(report["fact_ids_created"]) == 2
    assert report["knowledge_created"] == 3

    # 验证 DB
    facts_in_db = _run(lambda c: fetch_all(c, "SELECT * FROM story_facts WHERE project_id=?", (pid,)))
    assert len(facts_in_db) == 2

    knowledge_in_db = _run(lambda c: fetch_all(
        c,
        "SELECT * FROM character_knowledge ck JOIN story_facts f ON f.id=ck.fact_id WHERE f.project_id=?",
        (pid,),
    ))
    assert len(knowledge_in_db) == 3


def test_infer_overwrite_false_appends_to_existing(monkeypatch, make_user):
    """hotfix(2026-06-01)新语义:overwrite=False 是"追加" — 已有事实保留 + LLM 新事实并存,按 description 去重."""
    from app.services import knowledge_boundaries_inferer as kbi

    u = make_user("kb_append")
    pid, cids = _make_project_with_chars(u["user_id"], char_count=3)

    # 预先塞 2 条 fact:一条"用户已填",一条会跟 LLM 输出重名以验证去重
    now = "2026-05-29T00:00:00+00:00"
    _run(lambda c: (
        execute(c,
            "INSERT INTO story_facts (id, project_id, description, first_revealed_scene, is_sensitive, created_at) "
            "VALUES (?, ?, ?, NULL, 0, ?)",
            (str(uuid.uuid4()), pid, "用户已填的事实", now),
        ),
        execute(c,
            "INSERT INTO story_facts (id, project_id, description, first_revealed_scene, is_sensitive, created_at) "
            "VALUES (?, ?, ?, NULL, 0, ?)",
            (str(uuid.uuid4()), pid, "直子已自杀", now),
        ),
        c.commit(),
    ))

    monkeypatch.setattr(
        kbi, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1500,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "facts": [
                    # 与已有重名 → 应去重不新建
                    {"description": "直子已自杀", "first_revealed_scene": 8, "is_sensitive": True},
                    # 全新 → 应追加
                    {"description": "渡边和绿子相识于校园", "first_revealed_scene": 3, "is_sensitive": False},
                ],
                "character_knowledge": [],
                "reasoning": "",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(kbi, "call_llm_json", fake_llm)

    result = _run(lambda c: kbi.infer_knowledge_boundaries(c, pid))
    report = _run(lambda c: kbi.cache_knowledge_boundaries(c, pid, result, overwrite=False))

    # applied=True(有 1 条新事实建成)
    assert report["applied"] is True
    # 新建 1 条(渡边绿子)
    assert len(report["fact_ids_created"]) == 1
    # 跳过 1 条重复(直子自杀)
    assert report["skipped_duplicates"] == 1

    # DB 应有 3 条:2 条原有 + 1 条新追加
    facts_in_db = _run(lambda c: fetch_all(c, "SELECT description FROM story_facts WHERE project_id=?", (pid,)))
    descriptions = {r["description"] for r in facts_in_db}
    assert "用户已填的事实" in descriptions   # 原有保留
    assert "直子已自杀" in descriptions        # 原有保留(没被覆盖)
    assert "渡边和绿子相识于校园" in descriptions  # 新追加
    assert len(facts_in_db) == 3


def test_infer_overwrite_true_replaces_existing(monkeypatch, make_user):
    """overwrite=True + 已有 facts → 删全 + 重建为 LLM 输出."""
    from app.services import knowledge_boundaries_inferer as kbi

    u = make_user("kb_ow")
    pid, cids = _make_project_with_chars(u["user_id"], char_count=3)

    # 预先塞 2 条 fact + 1 条 knowledge
    now = "2026-05-29T00:00:00+00:00"
    old_fid = str(uuid.uuid4())
    _run(lambda c: (
        execute(c,
            "INSERT INTO story_facts (id, project_id, description, first_revealed_scene, is_sensitive, created_at) "
            "VALUES (?, ?, ?, NULL, 0, ?)",
            (old_fid, pid, "旧事实 A", now),
        ),
        execute(c,
            "INSERT INTO character_knowledge (id, character_id, fact_id, known_since_scene, confidence, created_at) "
            "VALUES (?, ?, ?, NULL, 'confirmed', ?)",
            (str(uuid.uuid4()), cids[0], old_fid, now),
        ),
        c.commit(),
    ))

    monkeypatch.setattr(
        kbi, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1500,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "facts": [
                    {"description": "新事实 X", "first_revealed_scene": 5, "is_sensitive": True},
                ],
                "character_knowledge": [
                    {"character_name": "渡边", "fact_index": 0, "known_since_scene": 5, "confidence": "confirmed"},
                ],
                "reasoning": "覆盖",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(kbi, "call_llm_json", fake_llm)

    result = _run(lambda c: kbi.infer_knowledge_boundaries(c, pid))
    report = _run(lambda c: kbi.cache_knowledge_boundaries(c, pid, result, overwrite=True))

    assert report["applied"] is True
    assert len(report["fact_ids_created"]) == 1

    facts_after = _run(lambda c: fetch_all(c, "SELECT * FROM story_facts WHERE project_id=?", (pid,)))
    assert len(facts_after) == 1
    assert facts_after[0]["description"] == "新事实 X"
    # 旧 knowledge 应被级联清掉
    old_kn = _run(lambda c: fetch_all(c, "SELECT * FROM character_knowledge WHERE fact_id=?", (old_fid,)))
    assert len(old_kn) == 0


def test_infer_initial_mode_uses_events_and_relationships(monkeypatch, make_user):
    """初始态(无 upload):基于事件 + 关系推断."""
    from app.services import knowledge_boundaries_inferer as kbi

    u = make_user("kb_init")
    pid, cids = _make_project_with_chars(u["user_id"], char_count=3)

    # 塞 1 条事件 + 1 条关系
    now = "2026-05-29T00:00:00+00:00"
    import json as _j
    _run(lambda c: (
        execute(c,
            "INSERT INTO events (id, project_id, description, participants, created_at, time_anchor) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (str(uuid.uuid4()), pid, "渡边告诉绿子直子病了",
             _j.dumps([cids[0], cids[2]]), now, "第 3 章"),
        ),
        execute(c,
            "INSERT INTO relationships (id, project_id, source_id, target_id, type, description, color, strength, created_at, polarity) "
            "VALUES (?, ?, ?, ?, '情侣', '', NULL, 'strong', ?, 'positive')",
            (str(uuid.uuid4()), pid, cids[0], cids[2], now),
        ),
        c.commit(),
    ))

    # 无 upload
    monkeypatch.setattr(
        kbi, "_get_full_text_for_project",
        lambda conn, project_id: None,
    )

    captured: dict = {}

    def fake_llm(system_prompt, user_input, **kwargs):
        captured.update(user_input)
        return (
            {
                "facts": [
                    {"description": "直子病了", "first_revealed_scene": 3, "is_sensitive": True},
                ],
                "character_knowledge": [
                    {"character_name": "渡边", "fact_index": 0, "known_since_scene": 3, "confidence": "confirmed"},
                    {"character_name": "绿子", "fact_index": 0, "known_since_scene": 3, "confidence": "confirmed"},
                ],
                "reasoning": "基于初始态事件",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(kbi, "call_llm_json", fake_llm)

    result = _run(lambda c: kbi.infer_knowledge_boundaries(c, pid))

    assert captured.get("mode_source") == "initial"
    assert "events" in captured
    assert len(captured["events"]) == 1
    assert "relationships" in captured
    assert len(captured["relationships"]) == 1

    # cache 写入(项目无 fact → 直接写)
    report = _run(lambda c: kbi.cache_knowledge_boundaries(c, pid, result, overwrite=False))
    assert report["applied"] is True
    assert len(report["fact_ids_created"]) == 1
    assert report["knowledge_created"] == 2


def test_infer_too_few_characters_returns_warning(monkeypatch, make_user):
    """角色 < 2 → 返 fallback 提示."""
    from app.services import knowledge_boundaries_inferer as kbi

    u = make_user("kb_few")
    pid, _ = _make_project_with_chars(u["user_id"], char_count=1)

    result = _run(lambda c: kbi.infer_knowledge_boundaries(c, pid))
    assert result["facts"] == []
    assert "角色" in result["reasoning"]


def test_endpoint_returns_report(client, make_user, monkeypatch):
    """整端到端:endpoint 返回结构化 report."""
    from app.services import knowledge_boundaries_inferer as kbi

    u = make_user("kb_ep")
    pid, cids = _make_project_with_chars(u["user_id"], char_count=3)

    monkeypatch.setattr(
        kbi, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1500,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "facts": [{"description": "事实1", "first_revealed_scene": 0, "is_sensitive": False}],
                "character_knowledge": [
                    {"character_name": "渡边", "fact_index": 0, "known_since_scene": 0, "confidence": "confirmed"},
                ],
                "reasoning": "推断完成",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(kbi, "call_llm_json", fake_llm)

    r = client.post(
        f"/api/projects/{pid}/infer/knowledge_boundaries",
        headers=u["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True
    assert body["facts_created"] == 1
    assert body["knowledge_created"] == 1
    assert body["reasoning"]
