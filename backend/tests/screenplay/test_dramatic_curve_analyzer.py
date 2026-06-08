"""dramatic_curve_analyzer 单元测试 — 阶段 8.4+ Phase 2(2026-06-08)。

测试覆盖:
  1. 基础数据汇总(空 / 单场 / 多场)
  2. tension_score 规则各加分项
  3. tension_delta 计算
  4. cliffhanger_potential 各加分项
  5. 候选切点(末场 / 钩子场 / 章节边界)
  6. 桥接 SP-4 退化(无 link / 空数据 / 异常)
  7. 桥接成功增强 tension_score

不依赖真实 DB — 全部 mock `huimeng_bridge._resolve_project_id`
+ conn.execute 行为。
"""
from __future__ import annotations

import pytest

from app.screenplay.services import dramatic_curve_analyzer as dca
from app.screenplay.services.dramatic_curve_analyzer import (
    CurveAnalysisResult,
    SceneCurveData,
    analyze_dramatic_curve,
)


# ============================================================
# 辅助:构造测试数据
# ============================================================


def _make_scene(
    sid: str,
    *,
    number: int = 1,
    chapter: int | None = 1,
    summary: str = "",
    transition: str = "CUT_TO",
    elements: list[dict] | None = None,
) -> dict:
    """构造一个 yaml-shape 的 scene dict。"""
    return {
        "id": sid,
        "number": number,
        "summary": summary,
        "source": {"chapter": chapter} if chapter is not None else {},
        "transition_to_next": transition,
        "elements": elements or [],
    }


def _e_action(text: str = "动作描述") -> dict:
    return {"type": "action", "text": text}


def _e_dialogue(character: str = "林墨", text: str = "对白") -> dict:
    return {"type": "dialogue", "character_name": character, "text": text}


def _e_inner(character: str = "林墨", text: str = "心里想") -> dict:
    return {
        "type": "voiceover",
        "character_name": character,
        "text": text,
        "is_inner_monologue": True,
    }


class _FakeConn:
    """模拟 sqlite3.Connection — 不需要真实 DB。"""

    def execute(self, *args, **kwargs):
        # 默认所有 SQL 返空结果(用于触发 bridge fallback 路径)
        return _FakeCursor([])


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


# ============================================================
# Group 1: 基础数据汇总
# ============================================================


def test_empty_scenes_returns_empty_result(monkeypatch):
    """空 scenes 列表 → 空结果,bridge_used=False。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=[],
    )
    assert isinstance(result, CurveAnalysisResult)
    assert result.scenes == []
    assert result.bridge_used is False
    assert result.candidate_cut_points == []


def test_single_scene_basic_fields(monkeypatch):
    """单场基础字段填对。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    s = _make_scene(
        "scene_001", number=1, chapter=2, summary="林墨进入车间",
        transition="FADE_OUT",
        elements=[_e_action(), _e_action(), _e_dialogue(), _e_inner()],
    )
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=[s],
    )
    assert len(result.scenes) == 1
    sd = result.scenes[0]
    assert sd.scene_id == "scene_001"
    assert sd.scene_number == 1
    assert sd.chapter == 2
    assert sd.element_count == 4
    assert sd.dialogue_count == 1
    assert sd.voiceover_count == 1
    assert sd.action_count == 2
    assert sd.has_inner_monologue is True
    assert sd.transition_to_next == "FADE_OUT"
    assert sd.is_strong_transition is True
    # est_minutes 公式与 episode_planner 一致(elements/25, min 0.4)
    assert sd.est_minutes == pytest.approx(0.4, abs=0.01)


def test_chapter_boundary_after_flag(monkeypatch):
    """is_chapter_boundary_after 在章节切换处为 True。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    scenes = [
        _make_scene("s1", number=1, chapter=1),
        _make_scene("s2", number=2, chapter=1),
        _make_scene("s3", number=3, chapter=2),  # ← chapter 切
        _make_scene("s4", number=4, chapter=2),
    ]
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=scenes,
    )
    assert result.scenes[0].is_chapter_boundary_after is False  # 1→1
    assert result.scenes[1].is_chapter_boundary_after is True   # 1→2
    assert result.scenes[2].is_chapter_boundary_after is False  # 2→2
    assert result.scenes[3].is_chapter_boundary_after is False  # 末场无 next


# ============================================================
# Group 2: tension_score 规则
# ============================================================


def test_tension_score_low_for_sparse_scene(monkeypatch):
    """元素稀少 → tension_score 接近 0。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    s = _make_scene("s1", elements=[_e_action(), _e_action()])  # 2 elements
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=[s],
    )
    # density = clip((2-5)/25, 0, 1) = 0;无 inner monologue;无强转场;
    assert result.scenes[0].tension_score == 0.0


def test_tension_score_high_for_dense_scene(monkeypatch):
    """元素密集 + 内心独白 + 多对白 → tension_score 高。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    # 30 elements + 10 对白 + 内心独白 + 强转场
    elements = (
        [_e_action()] * 10
        + [_e_dialogue()] * 10
        + [_e_inner()] * 1
        + [_e_action()] * 9
    )
    s = _make_scene("s1", transition="SMASH_CUT", elements=elements)
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=[s],
    )
    # density = clip((30-5)/25, 0, 1) = 1.0;+0.10 inner + 0.08 dialogue + 0.06 action + 0.05 trans
    # 总 1.29 但 clip 到 1.0
    assert result.scenes[0].tension_score == 1.0


def test_tension_score_inner_monologue_bonus(monkeypatch):
    """内心独白 +0.10 加分(其他条件一致下,差值精确等于 0.10)。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    # 10 elements:5 action(不到 6 触发)+ 5 dialogue(不到 8 触发)
    # density = (10-5)/25 = 0.20
    base_elements = [_e_action()] * 5 + [_e_dialogue()] * 5
    # 同密度 + 一个内心独白(替掉一个 dialogue,保持 element_count=10)
    inner_elements = [_e_action()] * 5 + [_e_dialogue()] * 4 + [_e_inner()]
    base_scene = _make_scene("s1", elements=base_elements)
    inner_scene = _make_scene("s2", elements=inner_elements)
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1",
        scenes_yaml=[base_scene, inner_scene],
    )
    # base: density 0.20 + 0 = 0.20
    # inner: density 0.20 + 0.10 inner = 0.30
    # 差值精确 0.10
    assert result.scenes[0].tension_score == 0.2
    assert result.scenes[1].tension_score == 0.3


# ============================================================
# Group 3: tension_delta
# ============================================================


def test_tension_delta_first_scene_zero(monkeypatch):
    """首场 delta 永远 0。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    scenes = [
        _make_scene("s1", elements=[_e_action()] * 30),  # tension 高
        _make_scene("s2", elements=[_e_action()] * 5),   # tension 低
    ]
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=scenes,
    )
    assert result.scenes[0].tension_delta == 0.0
    # s2 比 s1 低,delta < 0
    assert result.scenes[1].tension_delta < 0


def test_tension_delta_ascending(monkeypatch):
    """张力上升 → delta > 0。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    scenes = [
        _make_scene("s1", elements=[_e_action()] * 5),
        _make_scene("s2", elements=[_e_action()] * 25),
    ]
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=scenes,
    )
    assert result.scenes[1].tension_delta > 0


# ============================================================
# Group 4: cliffhanger_potential
# ============================================================


def test_cliffhanger_low_for_quiet_scene(monkeypatch):
    """平淡场景 cliffhanger_potential 低。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    s = _make_scene("s1", summary="林墨喝茶看报", elements=[_e_action()] * 5)
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=[s],
    )
    assert result.scenes[0].cliffhanger_potential < 0.30


def test_cliffhanger_high_for_hook_scene(monkeypatch):
    """张力高 + 内心独白 + 强转场 + 钩子词 → potential 高。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    # 25 elements + 内心独白 + FADE_OUT + 钩子词
    elements = [_e_action()] * 20 + [_e_dialogue()] * 4 + [_e_inner()]
    s = _make_scene(
        "s1", summary="林墨突然发现了真相",
        transition="FADE_OUT", elements=elements,
    )
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=[s],
    )
    sd = result.scenes[0]
    # tension_score >= 0.50 (+0.30) + has_inner (+0.15) + strong_trans (+0.20) + hook_word (+0.10)
    # tension_delta=0 (首场,不加)
    assert sd.cliffhanger_potential >= 0.70


def test_cliffhanger_chapter_boundary_bonus(monkeypatch):
    """章节边界场尾 +0.10。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    scenes = [
        _make_scene("s1", chapter=1, elements=[_e_action()] * 15),
        _make_scene("s2", chapter=2, elements=[_e_action()] * 5),
    ]
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=scenes,
    )
    # s1 是 chapter 边界,+0.10
    # s2 不是边界
    assert result.scenes[0].is_chapter_boundary_after is True
    assert result.scenes[0].cliffhanger_potential >= 0.10


# ============================================================
# Group 5: 候选切点
# ============================================================


def test_candidate_cut_includes_last_scene(monkeypatch):
    """末场必入候选(无论得分)。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    scenes = [_make_scene("s1"), _make_scene("s2"), _make_scene("s3")]
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=scenes,
    )
    assert 2 in result.candidate_cut_points  # 末场 index


def test_candidate_cut_includes_strong_hook(monkeypatch):
    """高 cliffhanger_potential 场入候选。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    # 第 2 场:25 elements + 内心独白 + FADE_OUT + 钩子词 → 高 potential
    hook_elements = [_e_action()] * 20 + [_e_dialogue()] * 4 + [_e_inner()]
    scenes = [
        _make_scene("s1", elements=[_e_action()] * 5),
        _make_scene("s2", summary="真相突然暴露", transition="FADE_OUT", elements=hook_elements),
        _make_scene("s3", elements=[_e_action()] * 5),
    ]
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=scenes,
    )
    assert 1 in result.candidate_cut_points  # s2 高钩子


def test_candidate_cut_includes_chapter_boundary(monkeypatch):
    """章节边界 + 张力不下降 → 入候选。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    scenes = [
        _make_scene("s1", chapter=1, elements=[_e_action()] * 10),
        _make_scene("s2", chapter=2, elements=[_e_action()] * 15),  # delta > 0,章末
    ]
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=scenes,
    )
    # s1 章节边界 + s2 也是末场(必入)
    # 检查 s1 入选(章末 + 张力不降这种应该入)
    # 但 s1 的 delta=0(首场),s2 比 s1 高 → s1 后续是上升 → s1 入
    # 实际:s1 delta=0,is_chapter_boundary_after=True,_is_clean_chapter_end=True
    assert 0 in result.candidate_cut_points


# ============================================================
# Group 6: 桥接退化
# ============================================================


def test_bridge_no_link_returns_rule_fallback(monkeypatch):
    """_resolve_project_id 返 None → bridge_used=False。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    scenes = [_make_scene("s1", elements=[_e_action()] * 10)]
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=scenes,
    )
    assert result.bridge_used is False
    assert result.bridge_data_source == "rule_fallback"
    assert result.chapter_emotion_curve == {}


def test_bridge_no_simulation_returns_rule_fallback(monkeypatch):
    """linked 但无 simulation → bridge_used=False。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id",
        lambda *a, **kw: "proj_123",
    )

    class _FakeConnNoSim:
        def execute(self, sql, params=()):
            # simulations 查询返空
            return _FakeCursor([])

    scenes = [_make_scene("s1", elements=[_e_action()] * 10)]
    result = analyze_dramatic_curve(
        _FakeConnNoSim(), user_id="u1", novel_id="n1", scenes_yaml=scenes,
    )
    assert result.bridge_used is False


def test_bridge_exception_swallowed_to_fallback(monkeypatch):
    """桥接代码抛异常 → 不阻断,降级到 rule_fallback。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id",
        lambda *a, **kw: "proj_123",
    )

    class _FakeConnBoom:
        def execute(self, *args, **kwargs):
            raise RuntimeError("DB exploded")

    scenes = [_make_scene("s1", elements=[_e_action()] * 10)]
    result = analyze_dramatic_curve(
        _FakeConnBoom(), user_id="u1", novel_id="n1", scenes_yaml=scenes,
    )
    assert result.bridge_used is False
    # 但仍能返完整结果(rule fallback)
    assert len(result.scenes) == 1


# ============================================================
# Group 7: 桥接成功增强(模拟有效 simulation + snapshots)
# ============================================================


def test_bridge_success_enhances_tension_score(monkeypatch):
    """桥接命中 → 该 scene 的 tension_score 比无桥接高(0.40 增强)。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id",
        lambda *a, **kw: "proj_123",
    )

    # 模拟 SQL:simulations 查询返 1 行;snapshot 查询返 1 个 scene_index=1 的 emotion
    class _FakeConnWithData:
        def __init__(self):
            self._call_count = 0

        def execute(self, sql, params=()):
            self._call_count += 1
            if "FROM simulations" in sql:
                return _FakeCursor([{"id": "sim_001"}])
            if "character_state_snapshots" in sql:
                # 一条 snapshot:scene_index=1,emotion_vec='{"anger": 0.9, "fear": 0.7}'
                # intensity = 0.9 + 0.7 = 1.6 → clip 到 1.0
                return _FakeCursor([
                    {
                        "scene_index": 1,
                        "emotion_vec": '{"anger": 0.9, "fear": 0.7}',
                    },
                ])
            return _FakeCursor([])

    scenes = [
        _make_scene("s1", chapter=1, elements=[_e_action()] * 10),
    ]

    conn = _FakeConnWithData()
    # 桥接 result 字典中 scene_index 当作 chapter 用,scene chapter=1 应命中
    result = analyze_dramatic_curve(
        conn, user_id="u1", novel_id="n1", scenes_yaml=scenes,
    )
    assert result.bridge_used is True
    assert result.bridge_data_source == "sp4_simulation"
    sd = result.scenes[0]
    assert sd.bridge_has_data is True
    assert sd.bridge_emotion_intensity > 0
    # tension_score 应被增强:无桥接 density=(10-5)/25=0.20,有桥接 0.20*(1+0.4*1.0)=0.28
    assert sd.tension_score > 0.20
    assert sd.tension_score <= 1.0


def test_serialize_curve_result_dict_shape():
    """to_dict 序列化为 API 友好结构(chapter_emotion_curve → list)。"""
    result = CurveAnalysisResult(
        scenes=[],
        bridge_used=True,
        bridge_data_source="sp4_simulation",
        chapter_emotion_curve={1: 0.5, 2: 0.8},
        candidate_cut_points=[0, 3],
    )
    d = result.to_dict()
    assert d["bridge_used"] is True
    assert d["bridge_data_source"] == "sp4_simulation"
    assert d["chapter_emotion_curve"] == [
        {"chapter": 1, "intensity": 0.5},
        {"chapter": 2, "intensity": 0.8},
    ]
    assert d["candidate_cut_points"] == [0, 3]


def test_invalid_scene_skipped(monkeypatch):
    """非 dict 的 scene 元素被静默跳过,不抛。"""
    monkeypatch.setattr(
        dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    scenes = [
        _make_scene("s1"),
        "this is not a dict",  # ← 应被跳过
        _make_scene("s2"),
    ]
    result = analyze_dramatic_curve(
        _FakeConn(), user_id="u1", novel_id="n1", scenes_yaml=scenes,  # type: ignore
    )
    assert len(result.scenes) == 2  # 跳过了非法的
    assert [s.scene_id for s in result.scenes] == ["s1", "s2"]
