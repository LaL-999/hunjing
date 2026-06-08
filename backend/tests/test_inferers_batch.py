"""一键灌满北极星(2026-05-29 末)pytest."""
from __future__ import annotations

import uuid

import pytest

from app.db import execute, fetch_one, get_connection


def _run(fn):
    conn = get_connection()
    try:
        return fn(conn)
    finally:
        conn.close()


def _make_project(user_id: str, char_count: int = 2) -> tuple[str, list[str]]:
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
            "VALUES (?, ?, '测试', 'novel', NULL, '[]', 'initial', ?, ?, '{}', 'first')",
            (pid, user_id, now, now),
        )
        for i in range(char_count):
            cid = str(uuid.uuid4())
            cids.append(cid)
            execute(
                conn,
                "INSERT INTO characters "
                "(id, project_id, name, identity, personality, quotes, no_go_list, "
                " position_x, position_y, position_z, color, created_at, updated_at, "
                " is_protagonist) "
                "VALUES (?, ?, ?, '主角身份', '内向', '[]', '[]', "
                " 0, 0, 0, NULL, ?, ?, ?)",
                (cid, pid, ["渡边", "直子", "绿子"][i], now, now, 1 if i == 0 else 0),
            )
        conn.commit()
        return pid, cids
    finally:
        conn.close()


def test_all_boards_runs_all_stages_with_mocks(monkeypatch, make_user):
    """一键场景:4 个 inferer 全部成功 + 角色 drivers loop."""
    from app.services import inferers_batch
    from app.services import (
        story_core_inferer,
        narrative_view_inferer,
        knowledge_boundaries_inferer,
        character_drivers_inferer,
    )

    u = make_user("ab_ok")
    # story_core / narrative_view 初始态 ≥ 3 角色才推断
    pid, cids = _make_project(u["user_id"], char_count=3)

    # 给项目加 1 条事件让 initial 路径 inferer 通过门槛(knowledge 不需要事件,
    # 但角色少于 2 时会 fallback,我们造了 2 角色;knowledge 需要事件或关系)
    now = "2026-05-29T00:00:00+00:00"
    _run(lambda c: (
        execute(c,
            "INSERT INTO events (id, project_id, description, participants, created_at) "
            "VALUES (?, ?, '渡边与直子相遇', '[]', ?)",
            (str(uuid.uuid4()), pid, now),
        ),
        c.commit(),
    ))

    monkeypatch.setattr(
        story_core_inferer, "_get_full_text_for_project",
        lambda conn, project_id: None,  # 初始态(无 upload)
    )
    monkeypatch.setattr(
        narrative_view_inferer, "_get_full_text_for_project",
        lambda conn, project_id: None,
    )
    monkeypatch.setattr(
        knowledge_boundaries_inferer, "_get_full_text_for_project",
        lambda conn, project_id: None,
    )
    monkeypatch.setattr(
        character_drivers_inferer, "_get_full_text_for_project",
        lambda conn, project_id: None,
    )

    # 4 个 fake_llm
    def fake_sc(system_prompt, user_input, **kwargs):
        return (
            {
                "core_dramatic_question": "孤独中能否相互取暖",
                "theme": "孤独与共生",
                "ending_direction": "哀而不伤",
                "reasoning": "基于初始态 2 角色 + 1 事件推",
            },
            {"input_tokens": 50, "output_tokens": 30},
        )
    monkeypatch.setattr(story_core_inferer, "call_llm_json", fake_sc)

    def fake_nv(system_prompt, user_input, **kwargs):
        return (
            {
                "narrative_focus_character_name": "渡边",
                "narrator_reliability": "uncertain",
                "narrative_distance": "close",
                "reasoning": "first 人称 + 1 主角",
            },
            {"input_tokens": 50, "output_tokens": 30},
        )
    monkeypatch.setattr(narrative_view_inferer, "call_llm_json", fake_nv)

    def fake_kb(system_prompt, user_input, **kwargs):
        return (
            {
                "facts": [
                    {"description": "渡边遇到直子", "first_revealed_scene": 0, "is_sensitive": False},
                ],
                "character_knowledge": [
                    {"character_name": "渡边", "fact_index": 0, "known_since_scene": 0, "confidence": "confirmed"},
                ],
                "reasoning": "事件 0 推断",
            },
            {"input_tokens": 50, "output_tokens": 30},
        )
    monkeypatch.setattr(knowledge_boundaries_inferer, "call_llm_json", fake_kb)

    def fake_cd(system_prompt, user_input, **kwargs):
        return (
            {
                "surface_goal": "想被理解",
                "deep_need": "无条件接纳",
                "fatal_blind_spot": "退让当深爱",
                "arc_from_to": "从退让到敢表达",
                "secrets": [],
                "reasoning": "基于初始态角色档案",
            },
            {"input_tokens": 50, "output_tokens": 30},
        )
    monkeypatch.setattr(character_drivers_inferer, "call_llm_json", fake_cd)

    # SP-7.1 后加了 polarity stage — 此测试只关注前 3 stage + drivers,
    # 关闭 polarity 以维持原 6 stages 期望(polarity 单测在 test_relationship_polarity_inferer)
    report = _run(lambda c: inferers_batch.infer_all_boards_for_project(
        c, pid, include_drivers=True, force_knowledge=False, include_polarity=False,
    ))

    # 各 stage 全部 applied
    assert report["story_core"]["applied"] is True
    assert report["narrative_view"]["applied"] is True
    assert report["knowledge_boundaries"]["applied"] is True
    assert report["knowledge_boundaries"]["facts_created"] == 1
    # 3 角色都 loop 跑过 + applied
    assert len(report["character_drivers"]) == 3
    for cd in report["character_drivers"]:
        assert cd["applied"] is True

    # stats
    stats = report["stats"]
    assert stats["stages_attempted"] == 6  # 3 项目级 + 3 角色
    assert stats["stages_succeeded"] == 6
    assert stats["failures"] == []

    # 验证 DB
    proj = _run(lambda c: fetch_one(c,
        "SELECT core_dramatic_question, theme, narrative_focus_character_id, "
        "       narrator_reliability, narrative_distance "
        "FROM projects WHERE id=?", (pid,),
    ))
    assert proj["core_dramatic_question"].startswith("孤独中")
    assert proj["theme"] == "孤独与共生"
    assert proj["narrative_focus_character_id"] == cids[0]


def test_all_boards_isolates_failures(monkeypatch, make_user):
    """story_core 抛 → 不影响后续 stage;失败被记录到 stats.failures."""
    from app.services import inferers_batch
    from app.services import (
        story_core_inferer,
        narrative_view_inferer,
        knowledge_boundaries_inferer,
        character_drivers_inferer,
    )

    u = make_user("ab_isolate")
    pid, cids = _make_project(u["user_id"], char_count=3)

    # 加 1 事件让 knowledge 初始态过门槛
    now = "2026-05-29T00:00:00+00:00"
    _run(lambda c: (
        execute(c,
            "INSERT INTO events (id, project_id, description, participants, created_at) "
            "VALUES (?, ?, 'X', '[]', ?)",
            (str(uuid.uuid4()), pid, now),
        ),
        c.commit(),
    ))

    # story_core 故意抛
    def bad(conn, pid):
        raise RuntimeError("MOCK_FAIL")
    monkeypatch.setattr(
        story_core_inferer, "infer_story_core_for_project", bad,
    )

    # 其他 inferer 返合法兜底(我们 mock 它们的 LLM 调用 返空 facts/驱动)
    for mod in (narrative_view_inferer, knowledge_boundaries_inferer, character_drivers_inferer):
        monkeypatch.setattr(
            mod, "_get_full_text_for_project",
            lambda conn, project_id: None,
        )

    def fake_nv(system_prompt, user_input, **kwargs):
        return (
            {
                "narrative_focus_character_name": "渡边",
                "narrator_reliability": "reliable",
                "narrative_distance": "limited",
                "reasoning": "x",
            },
            {"input_tokens": 50, "output_tokens": 30},
        )
    monkeypatch.setattr(narrative_view_inferer, "call_llm_json", fake_nv)

    def fake_kb(system_prompt, user_input, **kwargs):
        return (
            {"facts": [], "character_knowledge": [], "reasoning": "材料不足"},
            {"input_tokens": 50, "output_tokens": 30},
        )
    monkeypatch.setattr(knowledge_boundaries_inferer, "call_llm_json", fake_kb)

    def fake_cd(system_prompt, user_input, **kwargs):
        return (
            {
                "surface_goal": "x", "deep_need": "", "fatal_blind_spot": "",
                "arc_from_to": "", "secrets": [], "reasoning": "x",
            },
            {"input_tokens": 50, "output_tokens": 30},
        )
    monkeypatch.setattr(character_drivers_inferer, "call_llm_json", fake_cd)

    report = _run(lambda c: inferers_batch.infer_all_boards_for_project(
        c, pid, include_drivers=True,
    ))

    # story_core 失败但 narrative_view 仍跑通
    assert report["story_core"]["applied"] is False
    assert "story_core" in report["stats"]["failures"]
    assert report["narrative_view"]["applied"] is True
    # knowledge_boundaries 因 facts 空 → applied=False(skipped_reason 有但不算 failure)
    assert report["knowledge_boundaries"]["applied"] is False
    # 角色 drivers 仍 loop 跑(3 个角色)
    assert len(report["character_drivers"]) == 3


def test_all_boards_skip_drivers_when_flag_false(monkeypatch, make_user):
    """include_drivers=False → 不 loop 角色 drivers."""
    from app.services import inferers_batch
    from app.services import (
        story_core_inferer,
        narrative_view_inferer,
        knowledge_boundaries_inferer,
    )

    u = make_user("ab_no_drv")
    pid, cids = _make_project(u["user_id"], char_count=3)

    # 简化:三 LLM 全 mock 失败,只看 character_drivers 是否被跳过
    for mod in (story_core_inferer, narrative_view_inferer, knowledge_boundaries_inferer):
        monkeypatch.setattr(
            mod, "_get_full_text_for_project",
            lambda conn, project_id: None,
        )

    def fake_empty(system_prompt, user_input, **kwargs):
        return (
            {"core_dramatic_question": "", "theme": "", "ending_direction": "",
             "narrative_focus_character_name": "", "narrator_reliability": None,
             "narrative_distance": None,
             "facts": [], "character_knowledge": [],
             "reasoning": ""},
            {"input_tokens": 50, "output_tokens": 30},
        )
    for mod in (story_core_inferer, narrative_view_inferer, knowledge_boundaries_inferer):
        monkeypatch.setattr(mod, "call_llm_json", fake_empty)

    # SP-7.1 后加了 polarity stage — 关闭以维持原 3 stages 期望
    report = _run(lambda c: inferers_batch.infer_all_boards_for_project(
        c, pid, include_drivers=False, include_polarity=False,
    ))

    # character_drivers loop 跳过 → 空数组
    assert report["character_drivers"] == []
    # 3 stages attempted(不含角色 + 不含 polarity)
    assert report["stats"]["stages_attempted"] == 3


# ============================================================
# SSE 进度回调测试(2026-05-30²)
# ============================================================

def test_progress_callback_receives_plan_start_done_done(monkeypatch, make_user):
    """progress_callback 应按 plan → stage_start/done × N → done 顺序触发."""
    from app.services import inferers_batch
    from app.services import story_core_inferer, narrative_view_inferer
    from app.services import knowledge_boundaries_inferer

    u = make_user("ab_cb")
    pid, _ = _make_project(u["user_id"], char_count=3)

    def fake_empty(system_prompt, user_input, **kwargs):
        return (
            {"core_dramatic_question": "", "theme": "", "ending_direction": "",
             "narrative_focus_character_name": "", "narrator_reliability": None,
             "narrative_distance": None,
             "facts": [], "character_knowledge": [],
             "polarity_decisions": [],
             "reasoning": ""},
            {"input_tokens": 50, "output_tokens": 30},
        )
    # mock 全部 inferer 的 LLM(快测,关 drivers)
    from app.services import relationship_polarity_inferer
    for mod in (story_core_inferer, narrative_view_inferer,
                knowledge_boundaries_inferer, relationship_polarity_inferer):
        monkeypatch.setattr(mod, "call_llm_json", fake_empty)

    events: list[dict] = []
    def cb(ev):
        events.append(ev)

    _run(lambda c: inferers_batch.infer_all_boards_for_project(
        c, pid, include_drivers=False, include_polarity=True,
        progress_callback=cb,
    ))

    # 第一个事件应为 plan
    assert events[0]["event"] == "plan"
    assert events[0]["total_stages"] == 4  # 3 项目级 + polarity(无角色)

    # 最后一个事件应为 done
    assert events[-1]["event"] == "done"

    # 中间应有 stage_start / stage_done 对
    starts = [e for e in events if e["event"] == "stage_start"]
    dones = [e for e in events if e["event"] == "stage_done"]
    assert len(starts) == 4  # 4 stages
    assert len(dones) == 4
    # stage_index 单调递增
    indices = [e["stage_index"] for e in starts]
    assert indices == [1, 2, 3, 4]


def test_progress_callback_emits_failed_on_stage_error(monkeypatch, make_user):
    """某 stage 抛错 → progress_callback 收到 stage_failed."""
    from app.services import inferers_batch
    from app.services import story_core_inferer, narrative_view_inferer
    from app.services import knowledge_boundaries_inferer

    u = make_user("ab_cb_fail")
    pid, _ = _make_project(u["user_id"], char_count=3)

    def fake_empty(system_prompt, user_input, **kwargs):
        return (
            {"core_dramatic_question": "", "theme": "", "ending_direction": "",
             "narrative_focus_character_name": "", "narrator_reliability": None,
             "narrative_distance": None,
             "facts": [], "character_knowledge": [],
             "reasoning": ""},
            {"input_tokens": 50, "output_tokens": 30},
        )
    # 让 narrative_view inferer 抛错(不是 LLM 抛 — service 内部已经有兜底,
    # 我们要让 batch 层的 try/except 兜底.改 cache 函数抛错就行)
    from app.services import narrative_view_inferer as nv_mod
    def fake_cache(*args, **kwargs):
        raise RuntimeError("mock narrative_view cache failed")
    monkeypatch.setattr(nv_mod, "cache_narrative_view_to_project", fake_cache)
    monkeypatch.setattr(story_core_inferer, "call_llm_json", fake_empty)
    monkeypatch.setattr(narrative_view_inferer, "call_llm_json", fake_empty)
    monkeypatch.setattr(knowledge_boundaries_inferer, "call_llm_json", fake_empty)

    events: list[dict] = []
    def cb(ev):
        events.append(ev)

    _run(lambda c: inferers_batch.infer_all_boards_for_project(
        c, pid, include_drivers=False, include_polarity=False,
        progress_callback=cb,
    ))

    # 应有 narrative_view 的 stage_failed
    failed = [e for e in events if e["event"] == "stage_failed"]
    assert len(failed) == 1
    assert failed[0]["stage"] == "narrative_view"
    assert "RuntimeError" in failed[0]["error"]
    # done 事件仍触发(不应中断)
    assert events[-1]["event"] == "done"


def test_progress_callback_safe_when_callback_raises(monkeypatch, make_user):
    """callback 自己抛异常不应中断 inferer 流程."""
    from app.services import inferers_batch
    from app.services import story_core_inferer, narrative_view_inferer
    from app.services import knowledge_boundaries_inferer

    u = make_user("ab_cb_safe")
    pid, _ = _make_project(u["user_id"], char_count=3)

    def fake_empty(system_prompt, user_input, **kwargs):
        return (
            {"core_dramatic_question": "", "theme": "", "ending_direction": "",
             "narrative_focus_character_name": "", "narrator_reliability": None,
             "narrative_distance": None,
             "facts": [], "character_knowledge": [],
             "reasoning": ""},
            {"input_tokens": 50, "output_tokens": 30},
        )
    for mod in (story_core_inferer, narrative_view_inferer, knowledge_boundaries_inferer):
        monkeypatch.setattr(mod, "call_llm_json", fake_empty)

    def bad_cb(ev):
        raise RuntimeError("callback bug")

    # 不应抛错 — _emit 兜底
    report = _run(lambda c: inferers_batch.infer_all_boards_for_project(
        c, pid, include_drivers=False, include_polarity=False,
        progress_callback=bad_cb,
    ))
    assert report["stats"]["stages_attempted"] == 3
