"""SP-7.1 关系 polarity AI 推断 测试(2026-05-30)."""
from __future__ import annotations

import uuid

import pytest

from app.db import execute, fetch_all, fetch_one, get_connection


def _run(fn):
    conn = get_connection()
    try:
        return fn(conn)
    finally:
        conn.close()


def _make_project_with_rels(user_id: str) -> tuple[str, list[str], list[str]]:
    """造项目 + 3 角色 + 3 关系(夫妻/朋友/敌对).
    返 (pid, char_ids, rel_ids)."""
    pid = str(uuid.uuid4())
    char_ids: list[str] = []
    rel_ids: list[str] = []
    now = "2026-05-30T00:00:00+00:00"
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
        for i, name in enumerate(["渡边", "直子", "永泽"]):
            cid = str(uuid.uuid4())
            char_ids.append(cid)
            execute(
                conn,
                "INSERT INTO characters "
                "(id, project_id, name, identity, personality, quotes, no_go_list, "
                " position_x, position_y, position_z, color, created_at, updated_at, "
                " is_protagonist) "
                "VALUES (?, ?, ?, '', '', '[]', '[]', 0, 0, 0, NULL, ?, ?, ?)",
                (cid, pid, name, now, now, 1 if i == 0 else 0),
            )
        # 3 关系:渡边-直子 情侣 / 渡边-永泽 朋友 / 渡边-永泽 敌对(NPC 测重复 source+target)
        # 实际上重复 source+target 不合理,这里只造 3 条不同三元组
        for src_idx, tgt_idx, typ, desc in [
            (0, 1, "情侣", "深爱"),
            (0, 2, "朋友", "玩世不恭"),
            (1, 2, "敌对", "永泽伤害了直子"),
        ]:
            rid = str(uuid.uuid4())
            rel_ids.append(rid)
            execute(
                conn,
                "INSERT INTO relationships "
                "(id, project_id, source_id, target_id, type, description, color, strength, created_at, polarity) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, 'moderate', ?, NULL)",
                (rid, pid, char_ids[src_idx], char_ids[tgt_idx], typ, desc, now),
            )
        conn.commit()
        return pid, char_ids, rel_ids
    finally:
        conn.close()


def test_inferer_writes_polarity_for_all_rels(monkeypatch, make_user):
    """LLM 返合法 → service 三元组映射 → 写入 polarity."""
    from app.services import relationship_polarity_inferer as rpi

    u = make_user("rp_ok")
    pid, cids, rids = _make_project_with_rels(u["user_id"])

    monkeypatch.setattr(
        rpi, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1500,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "polarity_decisions": [
                    {"source_name": "渡边", "target_name": "直子", "type": "情侣", "polarity": "positive"},
                    {"source_name": "渡边", "target_name": "永泽", "type": "朋友", "polarity": "neutral"},
                    {"source_name": "直子", "target_name": "永泽", "type": "敌对", "polarity": "negative"},
                ],
                "reasoning": "type 强信号 + description 微调",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(rpi, "call_llm_json", fake_llm)

    result = _run(lambda c: rpi.infer_polarity_for_project(c, pid))
    assert len(result["polarity_decisions"]) == 3

    report = _run(lambda c: rpi.cache_polarity_to_project(c, pid, result, overwrite=False))
    assert report["applied"] is True
    assert report["updated_count"] == 3
    assert report["no_match_count"] == 0

    rels = _run(lambda c: fetch_all(c, "SELECT type, polarity FROM relationships WHERE project_id=?", (pid,)))
    polarity_by_type = {r["type"]: r["polarity"] for r in rels}
    assert polarity_by_type["情侣"] == "positive"
    assert polarity_by_type["朋友"] == "neutral"
    assert polarity_by_type["敌对"] == "negative"


def test_overwrite_false_keeps_user_filled(monkeypatch, make_user):
    """overwrite=False:用户已手动 chip 切过的关系不被覆盖."""
    from app.services import relationship_polarity_inferer as rpi

    u = make_user("rp_keep")
    pid, cids, rids = _make_project_with_rels(u["user_id"])

    # 用户已手动给"情侣"关系标 negative(故意反 type 直觉)
    _run(lambda c: (
        execute(c, "UPDATE relationships SET polarity=? WHERE id=?", ("negative", rids[0])),
        c.commit(),
    ))

    monkeypatch.setattr(
        rpi, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1500,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "polarity_decisions": [
                    {"source_name": "渡边", "target_name": "直子", "type": "情侣", "polarity": "positive"},
                    {"source_name": "渡边", "target_name": "永泽", "type": "朋友", "polarity": "neutral"},
                    {"source_name": "直子", "target_name": "永泽", "type": "敌对", "polarity": "negative"},
                ],
                "reasoning": "",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(rpi, "call_llm_json", fake_llm)

    result = _run(lambda c: rpi.infer_polarity_for_project(c, pid))
    report = _run(lambda c: rpi.cache_polarity_to_project(c, pid, result, overwrite=False))

    # 用户的 negative 被保留 → 该条 skipped
    assert report["skipped_count"] >= 1
    cur = _run(lambda c: fetch_one(c, "SELECT polarity FROM relationships WHERE id=?", (rids[0],)))
    assert cur["polarity"] == "negative"


def test_overwrite_true_replaces_user_filled(monkeypatch, make_user):
    """overwrite=True:覆盖用户手动切过的."""
    from app.services import relationship_polarity_inferer as rpi

    u = make_user("rp_ow")
    pid, cids, rids = _make_project_with_rels(u["user_id"])

    _run(lambda c: (
        execute(c, "UPDATE relationships SET polarity=? WHERE id=?", ("negative", rids[0])),
        c.commit(),
    ))

    monkeypatch.setattr(
        rpi, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1500,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "polarity_decisions": [
                    {"source_name": "渡边", "target_name": "直子", "type": "情侣", "polarity": "positive"},
                ],
                "reasoning": "",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(rpi, "call_llm_json", fake_llm)

    result = _run(lambda c: rpi.infer_polarity_for_project(c, pid))
    report = _run(lambda c: rpi.cache_polarity_to_project(c, pid, result, overwrite=True))

    assert report["updated_count"] == 1
    cur = _run(lambda c: fetch_one(c, "SELECT polarity FROM relationships WHERE id=?", (rids[0],)))
    assert cur["polarity"] == "positive"


def test_initial_mode_no_upload(monkeypatch, make_user):
    """无 upload(初始态)→ LLM 收到 mode_source=initial + 仅 relationships 字段."""
    from app.services import relationship_polarity_inferer as rpi

    u = make_user("rp_init")
    pid, _, _ = _make_project_with_rels(u["user_id"])

    monkeypatch.setattr(
        rpi, "_get_full_text_for_project",
        lambda conn, project_id: None,
    )

    captured: dict = {}

    def fake_llm(system_prompt, user_input, **kwargs):
        captured.update(user_input)
        return (
            {"polarity_decisions": [], "reasoning": ""},
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(rpi, "call_llm_json", fake_llm)

    _run(lambda c: rpi.infer_polarity_for_project(c, pid))

    assert captured.get("mode_source") == "initial"
    assert "relationships" in captured
    assert len(captured["relationships"]) == 3
    # 不应有 head_excerpt(那是 upload 形态)
    assert "head_excerpt" not in captured


def test_no_relationships_returns_fallback(monkeypatch, make_user):
    """项目无关系 → 返 fallback 提示."""
    from app.services import relationship_polarity_inferer as rpi

    u = make_user("rp_empty")
    pid = str(uuid.uuid4())
    now = "2026-05-30T00:00:00+00:00"
    _run(lambda c: (
        execute(c,
            "INSERT INTO projects "
            "(id, user_id, name, type, custom_type_name, tags, mode, created_at, updated_at) "
            "VALUES (?, ?, '空', 'novel', NULL, '[]', 'middle', ?, ?)",
            (pid, u["user_id"], now, now),
        ),
        c.commit(),
    ))

    result = _run(lambda c: rpi.infer_polarity_for_project(c, pid))
    assert result["polarity_decisions"] == []
    assert "关系" in result["reasoning"]


def test_three_tuple_matching_handles_partial_match(monkeypatch, make_user):
    """LLM 输出 type 略改 → service 应能用 source+target fallback 匹配."""
    from app.services import relationship_polarity_inferer as rpi

    u = make_user("rp_partial")
    pid, _, _ = _make_project_with_rels(u["user_id"])

    monkeypatch.setattr(
        rpi, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1500,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        # 故意改 type 字符串(模拟 LLM 输出"恋人"而非输入的"情侣")
        return (
            {
                "polarity_decisions": [
                    {"source_name": "渡边", "target_name": "直子",
                     "type": "恋人", "polarity": "positive"},
                ],
                "reasoning": "",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(rpi, "call_llm_json", fake_llm)

    result = _run(lambda c: rpi.infer_polarity_for_project(c, pid))
    # service 层应能 source+target 二元组兜底匹配
    report = _run(lambda c: rpi.cache_polarity_to_project(c, pid, result, overwrite=False))
    assert report["updated_count"] == 1
    assert report["no_match_count"] == 0


def test_endpoint_returns_report(client, make_user, monkeypatch):
    """端到端:endpoint 返结构化 report."""
    from app.services import relationship_polarity_inferer as rpi

    u = make_user("rp_ep")
    pid, _, _ = _make_project_with_rels(u["user_id"])

    monkeypatch.setattr(
        rpi, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1500,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "polarity_decisions": [
                    {"source_name": "渡边", "target_name": "直子", "type": "情侣", "polarity": "positive"},
                ],
                "reasoning": "type 强信号",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(rpi, "call_llm_json", fake_llm)

    r = client.post(
        f"/api/projects/{pid}/infer/relationship_polarity",
        headers=u["headers"],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True
    assert body["updated_count"] == 1
    assert body["reasoning"]
