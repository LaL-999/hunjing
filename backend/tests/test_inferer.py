"""SP-1.5 / SP-8.1 / SP-2.1 inferer 测试(2026-05-29).

3 个 inferer 各 2 case:
- 成功路径(LLM 返合法 JSON → 写库)
- 失败兜底(LLM 抛 → 返 _FALLBACK_RESULT)

注意:每个 get_connection() 用完必须 close,否则 Windows 上 teardown 时 db 文件被占
导致 reset_test_db 失败 → 后续 case 全 ERROR.
"""
from __future__ import annotations

import uuid

import pytest

from app.db import execute, fetch_one, get_connection
from app.services.llm_client import LlmCallFailed


def _make_project_with_text(user_id: str, *, with_char: bool = False) -> tuple[str, str]:
    pid = str(uuid.uuid4())
    cid = ""
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
        if with_char:
            cid = str(uuid.uuid4())
            execute(
                conn,
                "INSERT INTO characters "
                "(id, project_id, name, identity, personality, quotes, no_go_list, "
                " position_x, position_y, position_z, color, created_at, updated_at, "
                " is_protagonist) "
                "VALUES (?, ?, '渡边', '主角', '内向', '[]', '[]', "
                " 0, 0, 0, NULL, ?, ?, 1)",
                (cid, pid, now, now),
            )
        conn.commit()
        return pid, cid
    finally:
        conn.close()


def _run_with_conn(fn):
    """开新 conn 跑 fn,保证 close."""
    conn = get_connection()
    try:
        return fn(conn)
    finally:
        conn.close()


# ============================================================
# SP-1.5 story_core_inferer
# ============================================================

def test_story_core_inferer_success_writes_three_fields(monkeypatch, make_user):
    """LLM 返合法 JSON → 三字段写入 projects 表."""
    from app.services import story_core_inferer as sci

    u = make_user("sc_ok")
    pid, _ = _make_project_with_text(u["user_id"])

    monkeypatch.setattr(
        sci, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1000,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "core_dramatic_question": "线上交付的真心扛不扛得住线下真相",
                "theme": "孤独中的相互取暖",
                "ending_direction": "哀而不伤,留一抹希望",
                "reasoning": "基于头中尾采样,主角内心独白集中于…",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(sci, "call_llm_json", fake_llm)

    result = _run_with_conn(lambda c: sci.infer_story_core_for_project(c, pid))
    assert result["core_dramatic_question"].startswith("线上交付")
    assert result["theme"] == "孤独中的相互取暖"
    assert result["ending_direction"] == "哀而不伤,留一抹希望"
    assert result["reasoning"]

    _run_with_conn(lambda c: sci.cache_story_core_to_project(c, pid, result))

    row = _run_with_conn(lambda c: fetch_one(
        c,
        "SELECT core_dramatic_question, theme, ending_direction FROM projects WHERE id=?",
        (pid,),
    ))
    assert row["core_dramatic_question"].startswith("线上交付")
    assert row["theme"] == "孤独中的相互取暖"
    assert row["ending_direction"] == "哀而不伤,留一抹希望"


def test_story_core_inferer_llm_failure_returns_fallback(monkeypatch, make_user):
    """LLM 抛 LlmCallFailed → 返 _FALLBACK_RESULT(三字段空)."""
    from app.services import story_core_inferer as sci

    u = make_user("sc_fail")
    pid, _ = _make_project_with_text(u["user_id"])

    monkeypatch.setattr(
        sci, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1000,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        raise LlmCallFailed("mock failure")
    monkeypatch.setattr(sci, "call_llm_json", fake_llm)

    result = _run_with_conn(lambda c: sci.infer_story_core_for_project(c, pid))
    assert result["core_dramatic_question"] == ""
    assert result["theme"] == ""
    assert result["ending_direction"] == ""
    assert "失败" in result["reasoning"] or "推断" in result["reasoning"]


# ============================================================
# SP-8.1 narrative_view_inferer
# ============================================================

def test_narrative_view_inferer_maps_name_to_character_id(monkeypatch, make_user):
    """LLM 输出焦点角色 name → service 映射到 character_id."""
    from app.services import narrative_view_inferer as nvi

    u = make_user("nv_ok")
    pid, cid = _make_project_with_text(u["user_id"], with_char=True)

    monkeypatch.setattr(
        nvi, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1000,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "narrative_focus_character_name": "渡边",
                "narrator_reliability": "uncertain",
                "narrative_distance": "close",
                "reasoning": "头段以'我'开头多 23 次,内心独白集中于渡边",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(nvi, "call_llm_json", fake_llm)

    result = _run_with_conn(lambda c: nvi.infer_narrative_view_for_project(c, pid))
    assert result["narrative_focus_character_id"] == cid
    assert result["narrative_focus_character_name"] == "渡边"
    assert result["narrator_reliability"] == "uncertain"
    assert result["narrative_distance"] == "close"

    _run_with_conn(lambda c: nvi.cache_narrative_view_to_project(c, pid, result))

    row = _run_with_conn(lambda c: fetch_one(
        c,
        "SELECT narrative_focus_character_id, narrator_reliability, narrative_distance "
        "FROM projects WHERE id=?",
        (pid,),
    ))
    assert row["narrative_focus_character_id"] == cid
    assert row["narrator_reliability"] == "uncertain"
    assert row["narrative_distance"] == "close"


def test_narrative_view_inferer_focus_name_not_matched(monkeypatch, make_user):
    """LLM 输出焦点角色 name 但项目无此角色 → focus_id=None,其他字段仍可用."""
    from app.services import narrative_view_inferer as nvi

    u = make_user("nv_nomatch")
    pid, _ = _make_project_with_text(u["user_id"], with_char=False)

    monkeypatch.setattr(
        nvi, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1000,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "narrative_focus_character_name": "不存在的角色",
                "narrator_reliability": "reliable",
                "narrative_distance": "omniscient",
                "reasoning": "全知视角,叙述者可靠",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(nvi, "call_llm_json", fake_llm)

    result = _run_with_conn(lambda c: nvi.infer_narrative_view_for_project(c, pid))
    assert result["narrative_focus_character_id"] is None
    assert result["narrative_focus_character_name"] == "不存在的角色"
    assert result["narrator_reliability"] == "reliable"
    assert result["narrative_distance"] == "omniscient"


# ============================================================
# SP-2.1 character_drivers_inferer
# ============================================================

def test_character_drivers_inferer_no_overwrite_keeps_user_filled(
    monkeypatch, make_user,
):
    """overwrite=False:AI 推断结果只填用户原本空字段,已填值保留."""
    from app.services import character_drivers_inferer as cdi

    u = make_user("cd_ok")
    pid, cid = _make_project_with_text(u["user_id"], with_char=True)

    # 用户已填 surface_goal
    _run_with_conn(lambda c: (
        execute(c, "UPDATE characters SET surface_goal = ? WHERE id = ?",
                ("用户已填:找到适合自己的爱人", cid)),
        c.commit(),
    ))

    monkeypatch.setattr(
        cdi, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1000,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "surface_goal": "AI 推断:成绩排第一",
                "deep_need": "AI 推断:被无条件接纳",
                "fatal_blind_spot": "AI 推断:把控制当爱",
                "arc_from_to": "从拒绝长大到承担责任",
                "secrets": [{"description": "他不知道直子已死", "hidden_from": []}],
                "reasoning": "基于原作中渡边内心独白",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(cdi, "call_llm_json", fake_llm)

    result = _run_with_conn(lambda c: cdi.infer_drivers_for_character(c, cid))
    assert len(result["secrets"]) == 1
    assert result["secrets"][0]["description"].startswith("他不知道")

    _run_with_conn(lambda c: cdi.cache_drivers_to_character(c, cid, result, overwrite=False))

    row = _run_with_conn(lambda c: fetch_one(
        c,
        "SELECT surface_goal, deep_need, fatal_blind_spot, arc_from_to, secret_json "
        "FROM characters WHERE id=?",
        (cid,),
    ))
    assert row["surface_goal"].startswith("用户已填")
    assert row["deep_need"].startswith("AI 推断:被无条件")
    assert row["fatal_blind_spot"].startswith("AI 推断:把控制")
    assert "拒绝长大" in row["arc_from_to"]
    assert row["secret_json"]
    assert "直子" in row["secret_json"]


def test_character_drivers_inferer_overwrite_replaces_all(monkeypatch, make_user):
    """overwrite=True:AI 推断结果覆盖所有 5 字段(包括用户已填的)."""
    from app.services import character_drivers_inferer as cdi

    u = make_user("cd_overwrite")
    pid, cid = _make_project_with_text(u["user_id"], with_char=True)

    _run_with_conn(lambda c: (
        execute(
            c,
            "UPDATE characters SET surface_goal=?, deep_need=?, fatal_blind_spot=?, arc_from_to=? "
            "WHERE id=?",
            ("用户 SG", "用户 DN", "用户 FBS", "用户 ARC", cid),
        ),
        c.commit(),
    ))

    monkeypatch.setattr(
        cdi, "_get_full_text_for_project",
        lambda conn, project_id: "x" * 1000,
    )

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "surface_goal": "新 SG",
                "deep_need": "新 DN",
                "fatal_blind_spot": "新 FBS",
                "arc_from_to": "新 ARC",
                "secrets": [],
                "reasoning": "全部覆盖",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(cdi, "call_llm_json", fake_llm)

    result = _run_with_conn(lambda c: cdi.infer_drivers_for_character(c, cid))
    _run_with_conn(lambda c: cdi.cache_drivers_to_character(c, cid, result, overwrite=True))

    row = _run_with_conn(lambda c: fetch_one(
        c,
        "SELECT surface_goal, deep_need, fatal_blind_spot, arc_from_to FROM characters WHERE id=?",
        (cid,),
    ))
    assert row["surface_goal"] == "新 SG"
    assert row["deep_need"] == "新 DN"
    assert row["fatal_blind_spot"] == "新 FBS"
    assert row["arc_from_to"] == "新 ARC"


# ============================================================
# 初始态路径(无 upload)— 3 个 inferer 全覆盖
# ============================================================

def _make_initial_mode_project(user_id: str, char_count: int = 3) -> tuple[str, list[str]]:
    """造初始态项目:多个角色 + 已填 identity/personality + 几条关系."""
    pid = str(uuid.uuid4())
    cids: list[str] = []
    now = "2026-05-29T00:00:00+00:00"
    conn = get_connection()
    try:
        execute(
            conn,
            "INSERT INTO projects "
            "(id, user_id, name, type, custom_type_name, tags, mode, "
            " created_at, updated_at, world_baseline_json, narrative_pov) "
            "VALUES (?, ?, '初始项目', 'novel', NULL, '[]', 'initial', ?, ?, ?, ?)",
            (
                pid, user_id, now, now,
                '{"genre":"文学","tone":"抒情沉重","setting":"现代都市"}',
                "first",
            ),
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
                "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, NULL, ?, ?, ?)",
                (
                    cid, pid, f"角色{i}",
                    f"角色{i}的身份描述,大学生,内向",
                    f"角色{i}的性格,敏感,容易内疚",
                    '["我不知道该怎么办","我想离开这里"]',
                    '["不会大声说话","不会伤害他人"]',
                    now, now, is_protag,
                ),
            )
        # 一条 negative 关系(角色0 → 角色1)
        if char_count >= 2:
            execute(
                conn,
                "INSERT INTO relationships "
                "(id, project_id, source_id, target_id, type, description, color, strength, created_at, polarity) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, 'strong', ?, ?)",
                (
                    str(uuid.uuid4()), pid, cids[0], cids[1],
                    "宿敌", "深层对立", now, "negative",
                ),
            )
        conn.commit()
        return pid, cids
    finally:
        conn.close()


def test_story_core_inferer_initial_mode_uses_user_filled(monkeypatch, make_user):
    """初始态:无 upload + 有 3 角色 → LLM 看到用户填的角色 / 关系 / 事件 / world_baseline."""
    from app.services import story_core_inferer as sci

    u = make_user("sc_init")
    pid, cids = _make_initial_mode_project(u["user_id"], char_count=3)

    # mock: 无 upload(返 None)
    monkeypatch.setattr(
        sci, "_get_full_text_for_project",
        lambda conn, project_id: None,
    )

    captured_input = {}

    def fake_llm(system_prompt, user_input, **kwargs):
        captured_input.update(user_input)
        return (
            {
                "core_dramatic_question": "深层对立能不能在凡俗里被化解",
                "theme": "理解他者需要先放下自我",
                "ending_direction": "苍凉但不绝望",
                "reasoning": "基于 3 角色 + 1 negative 关系 + tone=抒情沉重 推断",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(sci, "call_llm_json", fake_llm)

    result = _run_with_conn(lambda c: sci.infer_story_core_for_project(c, pid))

    # 关键:LLM input 应携带 initial 标识 + 用户填的字段
    assert captured_input.get("mode_source") == "initial"
    assert "world_baseline" in captured_input
    assert captured_input["world_baseline"].get("tone") == "抒情沉重"
    assert len(captured_input.get("characters_detailed", [])) == 3
    assert any(c.get("is_protagonist") for c in captured_input["characters_detailed"])
    assert len(captured_input.get("relationships", [])) == 1

    assert result["core_dramatic_question"] == "深层对立能不能在凡俗里被化解"
    assert result["theme"] == "理解他者需要先放下自我"


def test_story_core_inferer_initial_mode_too_few_chars_returns_warning(
    monkeypatch, make_user,
):
    """初始态 < 3 角色 → 返 fallback 提示先建角色."""
    from app.services import story_core_inferer as sci

    u = make_user("sc_init_few")
    pid, _ = _make_initial_mode_project(u["user_id"], char_count=2)

    monkeypatch.setattr(
        sci, "_get_full_text_for_project",
        lambda conn, project_id: None,
    )

    result = _run_with_conn(lambda c: sci.infer_story_core_for_project(c, pid))
    assert result["core_dramatic_question"] == ""
    # reasoning 应提示"建至少 3 个角色"
    assert "角色" in result["reasoning"]


def test_character_drivers_inferer_initial_mode_with_relationships(
    monkeypatch, make_user,
):
    """初始态:无 upload,角色 drivers 基于该角色档案 + 关系网 + 其他角色 + 参与事件推断."""
    from app.services import character_drivers_inferer as cdi

    u = make_user("cd_init")
    pid, cids = _make_initial_mode_project(u["user_id"], char_count=3)
    target_cid = cids[0]  # 主角

    monkeypatch.setattr(
        cdi, "_get_full_text_for_project",
        lambda conn, project_id: None,
    )

    captured_input = {}

    def fake_llm(system_prompt, user_input, **kwargs):
        captured_input.update(user_input)
        return (
            {
                "surface_goal": "想被理解",
                "deep_need": "被无条件接纳",
                "fatal_blind_spot": "把退让当深爱",
                "arc_from_to": "从退让到敢于表达",
                "secrets": [],
                "reasoning": "基于该角色 negative 关系 + 抒情 tone",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(cdi, "call_llm_json", fake_llm)

    result = _run_with_conn(lambda c: cdi.infer_drivers_for_character(c, target_cid))

    # 关键:input 应含 initial 标识 + relationships(包含主角的 negative 关系)
    assert captured_input.get("mode_source") == "initial"
    assert "character" in captured_input
    assert "relationships" in captured_input
    assert any(r.get("polarity") == "negative" for r in captured_input["relationships"])
    assert "other_characters" in captured_input

    assert result["surface_goal"] == "想被理解"
    assert result["deep_need"] == "被无条件接纳"


def test_narrative_view_inferer_initial_mode_uses_pov_and_polarity(
    monkeypatch, make_user,
):
    """初始态:无 upload + 已填 narrative_pov=first + 主角标 + polarity 概貌 → LLM 看到."""
    from app.services import narrative_view_inferer as nvi

    u = make_user("nv_init")
    pid, cids = _make_initial_mode_project(u["user_id"], char_count=3)

    monkeypatch.setattr(
        nvi, "_get_full_text_for_project",
        lambda conn, project_id: None,
    )

    captured_input = {}

    def fake_llm(system_prompt, user_input, **kwargs):
        captured_input.update(user_input)
        return (
            {
                "narrative_focus_character_name": "角色0",  # 主角
                "narrator_reliability": "unreliable",
                "narrative_distance": "intimate",
                "reasoning": "first 人称 + 抒情 tone + negative 关系 → 沉浸+不可靠",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(nvi, "call_llm_json", fake_llm)

    result = _run_with_conn(lambda c: nvi.infer_narrative_view_for_project(c, pid))

    # 关键:input 应携带初始态 fields
    assert captured_input.get("mode_source") == "initial"
    assert captured_input.get("existing_narrative_pov") == "first"
    assert "characters_brief" in captured_input
    assert "relationship_polarity_summary" in captured_input
    assert captured_input["relationship_polarity_summary"]["negative"] == 1

    assert result["narrative_focus_character_id"] == cids[0]
    assert result["narrator_reliability"] == "unreliable"
    assert result["narrative_distance"] == "intimate"
