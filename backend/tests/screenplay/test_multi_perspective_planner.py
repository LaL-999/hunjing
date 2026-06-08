"""multi_perspective_planner 单元测试 — 阶段 8.4+ Phase 3(2026-06-08)。

测试覆盖:
  1. 主入口 — 3 视角全返,顶层字段正确
  2. preset 解析(short / long / anime / custom + 自定义 target 覆盖)
  3. rhythm 视角(tension valley 切集)
  4. hook 视角(cliff 阈值过滤)
  5. arc 视角 LLM 成功 / 失败 / 非法 cuts sanitize
  6. _cuts_to_episodes 元数据(tension_peak / cliff / summary_preview)
  7. 空剧本 / 剧本 YAML 错抛 MultiPerspectivePlanError
  8. recommended_perspective 启发式
"""
from __future__ import annotations

import pytest
import yaml as yamllib

from app.screenplay.services import multi_perspective_planner as mpp
from app.screenplay.services.multi_perspective_planner import (
    PRESETS,
    MultiPerspectivePlanError,
    PerspectivePlan,
    plan_with_perspectives,
)


# ============================================================
# 辅助:造剧本 yaml
# ============================================================


def _make_yaml(scenes_data: list[dict], characters: list[dict] | None = None) -> str:
    """造一个最小可解析的剧本 yaml。"""
    data = {
        "scenes": scenes_data,
        "characters": characters or [],
        "locations": [],
        "version": "1.0",
    }
    return yamllib.safe_dump(data, allow_unicode=True)


def _scene(
    sid: str,
    *,
    number: int = 1,
    chapter: int | None = 1,
    summary: str = "",
    transition: str = "CUT_TO",
    characters_present: list[str] | None = None,
    elements: list[dict] | None = None,
) -> dict:
    """造一个 scene yaml dict。"""
    return {
        "id": sid,
        "number": number,
        "summary": summary,
        "source": {"chapter": chapter} if chapter is not None else {},
        "transition_to_next": transition,
        "characters_present": characters_present or [],
        "elements": elements or [],
    }


def _action(text: str = "动作") -> dict:
    return {"type": "action", "text": text}


def _dlg(name: str = "林墨", text: str = "对白") -> dict:
    return {"type": "dialogue", "character_name": name, "text": text}


def _inner(name: str = "林墨", text: str = "心想") -> dict:
    return {"type": "voiceover", "character_name": name, "text": text, "is_inner_monologue": True}


class _FakeConn:
    """空 SQL connection — 所有 execute 返空。"""

    def execute(self, *args, **kwargs):
        return _FakeCursor([])

    def close(self):
        pass


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


# ============================================================
# 公共 fixture
# ============================================================


@pytest.fixture(autouse=True)
def _no_bridge_no_db(monkeypatch):
    """默认:bridge 不命中 + get_connection 返 fake。
       想测桥接命中的测试,自己再 monkeypatch _resolve_project_id 返 project_id。
    """
    # bridge resolve 返 None → 全部走 rule_fallback
    monkeypatch.setattr(
        mpp.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    # dca 内的也要 patch(同一个 fn 但 import path 不同)
    monkeypatch.setattr(
        mpp.dca.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    monkeypatch.setattr(mpp, "get_connection", lambda: _FakeConn())
    monkeypatch.setattr(mpp.dca, "get_connection", lambda: _FakeConn(), raising=False)
    yield


def _mock_screenplay_store(monkeypatch, yaml_text: str):
    """让 screenplay_store.get_latest_screenplay 返一个含给定 yaml 的 record。"""
    def _fake_get(novel_id, *, user_id):
        return {"id": "sp_001", "novel_id": novel_id, "yaml_text": yaml_text}
    monkeypatch.setattr(mpp.screenplay_store, "get_latest_screenplay", _fake_get)


# ============================================================
# Group 1: 主入口 — 3 视角全返
# ============================================================


def test_main_entry_returns_3_perspectives(monkeypatch):
    """主入口返 3 个 perspectives(rhythm / hook / arc)。"""
    scenes = [
        _scene(f"s{i}", number=i, chapter=(i // 3) + 1,
               elements=[_action()] * 10)
        for i in range(1, 11)
    ]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))

    # LLM 全 mock 失败 → arc 退到 rhythm
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("test LLM blocked")),
    )

    result = plan_with_perspectives(
        novel_id="n1", user_id="u1", preset="short_drama",
    )
    assert len(result.perspectives) == 3
    perspectives = [p.perspective for p in result.perspectives]
    assert perspectives == ["rhythm", "hook", "arc"]
    # arc LLM 失败应有标志
    arc = next(p for p in result.perspectives if p.perspective == "arc")
    assert arc.llm_used is True
    assert arc.llm_failed is True


def test_main_entry_returns_required_top_level_fields(monkeypatch):
    """顶层字段:target_minutes / preset / bridge_used / tension_curve / recommended。"""
    scenes = [_scene(f"s{i}", number=i, elements=[_action()] * 5) for i in range(1, 6)]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("blocked")),
    )

    result = plan_with_perspectives(
        novel_id="n1", user_id="u1", preset="long_drama",
    )
    assert result.target_minutes_per_ep == PRESETS["long_drama"]["target_minutes"]
    assert result.preset == "long_drama"
    assert "长剧" in result.preset_label
    assert result.bridge_used is False
    assert result.bridge_data_source == "rule_fallback"
    assert len(result.tension_curve) == 5
    assert result.recommended_perspective in ("rhythm", "hook", "arc", None)


# ============================================================
# Group 2: preset 解析
# ============================================================


def test_preset_short_drama_default_target(monkeypatch):
    """short_drama 默认 target = 2.5 分钟。"""
    scenes = [_scene(f"s{i}", number=i, elements=[_action()] * 5) for i in range(1, 4)]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("blocked")),
    )

    result = plan_with_perspectives(novel_id="n1", user_id="u1", preset="short_drama")
    assert result.target_minutes_per_ep == 2.5


def test_preset_custom_uses_user_target(monkeypatch):
    """custom preset 用用户指定的 target_minutes_per_ep。"""
    scenes = [_scene(f"s{i}", number=i, elements=[_action()] * 5) for i in range(1, 4)]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("blocked")),
    )

    result = plan_with_perspectives(
        novel_id="n1", user_id="u1", preset="custom",
        target_minutes_per_ep=5.0,
    )
    assert result.target_minutes_per_ep == 5.0


def test_preset_custom_invalid_target_falls_back(monkeypatch):
    """custom + target 缺失或 <=0 → 兜底 3.0。"""
    scenes = [_scene(f"s{i}", number=i, elements=[_action()] * 5) for i in range(1, 4)]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("blocked")),
    )

    result = plan_with_perspectives(
        novel_id="n1", user_id="u1", preset="custom",
        target_minutes_per_ep=0.0,
    )
    assert result.target_minutes_per_ep == 3.0


def test_preset_unknown_falls_back_to_short_drama(monkeypatch):
    """未知 preset → 默认 short_drama 配置。"""
    scenes = [_scene(f"s{i}", number=i, elements=[_action()] * 5) for i in range(1, 4)]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("blocked")),
    )

    result = plan_with_perspectives(
        novel_id="n1", user_id="u1", preset="unknown_xyz",
    )
    assert result.target_minutes_per_ep == 2.5  # short_drama default


# ============================================================
# Group 3: rhythm 视角切集
# ============================================================


def test_rhythm_cuts_at_chapter_boundary_with_tension_window(monkeypatch):
    """rhythm 视角:进入 target±30% 窗口 + 章节边界 → 切。"""
    # 设计:每场 ~1 分钟(25 elements)。target=2.5 → window [1.75, 3.25]。
    # chapter 1 有 2 场,chapter 2 接着 → 第 2 场后 chapter boundary,累积~2 分钟,在窗口
    scenes = [
        _scene("s1", number=1, chapter=1, elements=[_action()] * 25),
        _scene("s2", number=2, chapter=1, elements=[_action()] * 25),
        _scene("s3", number=3, chapter=2, elements=[_action()] * 25),  # ← s2 末是边界
        _scene("s4", number=4, chapter=2, elements=[_action()] * 25),
    ]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("blocked")),
    )

    result = plan_with_perspectives(novel_id="n1", user_id="u1", preset="short_drama")
    rhythm = next(p for p in result.perspectives if p.perspective == "rhythm")
    # 至少 1 集,最后一集末场必入
    assert len(rhythm.episodes) >= 1
    assert rhythm.cuts[-1] == 3  # 末场 index
    # rhythm rationale 含"张力 / 谷地 / 节奏"
    assert any(kw in rhythm.rationale for kw in ("张力", "谷地", "节奏"))


# ============================================================
# Group 4: hook 视角切集
# ============================================================


def test_hook_prefers_high_cliffhanger_scenes(monkeypatch):
    """hook 视角:高 cliff 场优先作集尾。"""
    # s2 高 cliff(强转场 FADE_OUT + 内心独白 + 钩子词 summary)
    scenes = [
        _scene("s1", number=1, chapter=1, elements=[_action()] * 25),
        _scene("s2", number=2, chapter=1, summary="真相突然暴露",
               transition="FADE_OUT",
               elements=[_action()] * 20 + [_dlg()] * 4 + [_inner()]),
        _scene("s3", number=3, chapter=1, elements=[_action()] * 25),
        _scene("s4", number=4, chapter=1, elements=[_action()] * 25),
    ]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("blocked")),
    )

    result = plan_with_perspectives(novel_id="n1", user_id="u1", preset="short_drama")
    hook = next(p for p in result.perspectives if p.perspective == "hook")
    # s2(index 1)应该是某集的集尾
    assert 1 in hook.cuts


# ============================================================
# Group 5: arc 视角 LLM mock
# ============================================================


def test_arc_llm_success_with_valid_cuts(monkeypatch):
    """arc 视角 LLM 返合法 cuts → 直接采用。"""
    scenes = [_scene(f"s{i}", number=i, elements=[_action()] * 10) for i in range(1, 8)]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))

    # mock LLM 返 cuts=[2, 4, 6](末场)
    def _fake_llm(system_prompt, user_input, **kwargs):
        return ({"cuts": [2, 4, 6], "rationale": "在 act 边界切"}, {"input_tokens": 100, "output_tokens": 50})

    monkeypatch.setattr("app.services.llm_client.call_llm_json", _fake_llm)

    result = plan_with_perspectives(novel_id="n1", user_id="u1", preset="short_drama")
    arc = next(p for p in result.perspectives if p.perspective == "arc")
    assert arc.llm_used is True
    assert arc.llm_failed is False
    assert arc.cuts == [2, 4, 6]
    # 3 集
    assert len(arc.episodes) == 3


def test_arc_llm_sanitizes_invalid_cuts(monkeypatch):
    """arc 视角 LLM 返非法 cuts(out of range / duplicate / non-int)→ 过滤后仍可用。"""
    scenes = [_scene(f"s{i}", number=i, elements=[_action()] * 10) for i in range(1, 6)]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))

    def _fake_llm(system_prompt, user_input, **kwargs):
        return ({
            "cuts": [-1, 2, "bad", 2, 100, 4],  # 含负数 / 字符串 / 重复 / 越界
            "rationale": "test",
        }, {})

    monkeypatch.setattr("app.services.llm_client.call_llm_json", _fake_llm)

    result = plan_with_perspectives(novel_id="n1", user_id="u1", preset="short_drama")
    arc = next(p for p in result.perspectives if p.perspective == "arc")
    # sanitize 后:只 2 和 4 合法;末场 4 已是末
    assert arc.cuts == [2, 4]


def test_arc_llm_appends_last_scene_if_missing(monkeypatch):
    """arc LLM 没返末场 → 强制补末场。"""
    scenes = [_scene(f"s{i}", number=i, elements=[_action()] * 10) for i in range(1, 6)]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))

    def _fake_llm(system_prompt, user_input, **kwargs):
        return ({"cuts": [1, 3], "rationale": "test"}, {})  # 没末场 4

    monkeypatch.setattr("app.services.llm_client.call_llm_json", _fake_llm)

    result = plan_with_perspectives(novel_id="n1", user_id="u1", preset="short_drama")
    arc = next(p for p in result.perspectives if p.perspective == "arc")
    assert arc.cuts == [1, 3, 4]


def test_arc_llm_call_failed_falls_back_to_rhythm(monkeypatch):
    """arc LLM 抛 LlmCallFailed → 退到 rhythm 算法。"""
    scenes = [_scene(f"s{i}", number=i, elements=[_action()] * 10) for i in range(1, 6)]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))

    from app.services.llm_client import LlmCallFailed

    def _fake_llm(*args, **kwargs):
        raise LlmCallFailed("API key invalid")

    monkeypatch.setattr("app.services.llm_client.call_llm_json", _fake_llm)

    result = plan_with_perspectives(novel_id="n1", user_id="u1", preset="short_drama")
    arc = next(p for p in result.perspectives if p.perspective == "arc")
    assert arc.llm_failed is True
    # 回退到 rhythm 算法,但 perspective 仍标 arc
    assert arc.perspective == "arc"
    # 仍有 episodes(rhythm 算法兜底)
    assert len(arc.episodes) >= 1


# ============================================================
# Group 6: Episode 元数据
# ============================================================


def test_episode_meta_fields_populated(monkeypatch):
    """EpisodeWithMeta 的 tension_peak / cliff / summary_preview 填对。"""
    scenes = [
        _scene("s1", number=1, summary="林墨初登场", elements=[_action()] * 25),
        _scene("s2", number=2, summary="他遇见师父", elements=[_action()] * 25),
    ]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("blocked")),
    )

    result = plan_with_perspectives(novel_id="n1", user_id="u1", preset="short_drama")
    rhythm = next(p for p in result.perspectives if p.perspective == "rhythm")
    assert len(rhythm.episodes) >= 1
    ep1 = rhythm.episodes[0]
    # 至少有首集
    assert ep1.episode_number == 1
    assert ep1.title.startswith("第 1 集")
    assert "林墨" in ep1.summary_preview or "林墨" in ep1.title
    assert ep1.tension_peak >= 0.0
    assert ep1.tension_avg >= 0.0
    # quality_score Phase 5 后填,目前 None
    assert ep1.quality_score is None
    assert ep1.teaser is None


# ============================================================
# Group 7: 错误处理
# ============================================================


def test_no_screenplay_raises(monkeypatch):
    """无剧本 → MultiPerspectivePlanError。"""
    monkeypatch.setattr(
        mpp.screenplay_store, "get_latest_screenplay",
        lambda novel_id, *, user_id: None,
    )
    with pytest.raises(MultiPerspectivePlanError, match="尚未生成剧本"):
        plan_with_perspectives(novel_id="n1", user_id="u1", preset="short_drama")


def test_invalid_yaml_raises(monkeypatch):
    """剧本 YAML 解析失败 → MultiPerspectivePlanError。"""
    monkeypatch.setattr(
        mpp.screenplay_store, "get_latest_screenplay",
        lambda novel_id, *, user_id: {"yaml_text": ":\n  invalid: ::: yaml ::: ["},
    )
    with pytest.raises(MultiPerspectivePlanError, match="YAML 解析失败"):
        plan_with_perspectives(novel_id="n1", user_id="u1", preset="short_drama")


def test_empty_scenes_raises(monkeypatch):
    """yaml 解析成功但无 scenes → MultiPerspectivePlanError。"""
    _mock_screenplay_store(monkeypatch, yamllib.safe_dump({"scenes": []}))
    with pytest.raises(MultiPerspectivePlanError, match="无场景"):
        plan_with_perspectives(novel_id="n1", user_id="u1", preset="short_drama")


# ============================================================
# Group 8: 推荐方案启发式
# ============================================================


def test_recommended_perspective_is_one_of_three(monkeypatch):
    """recommended_perspective 必须是 rhythm/hook/arc 之一,或 None。"""
    scenes = [_scene(f"s{i}", number=i, elements=[_action()] * 10) for i in range(1, 6)]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("blocked")),
    )

    result = plan_with_perspectives(novel_id="n1", user_id="u1", preset="short_drama")
    assert result.recommended_perspective in ("rhythm", "hook", "arc", None)


def test_to_dict_serialization(monkeypatch):
    """MultiPerspectivePlan.to_dict 返完整 JSON 友好结构。"""
    scenes = [_scene(f"s{i}", number=i, elements=[_action()] * 10) for i in range(1, 4)]
    _mock_screenplay_store(monkeypatch, _make_yaml(scenes))
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("blocked")),
    )

    result = plan_with_perspectives(novel_id="n1", user_id="u1", preset="short_drama")
    d = result.to_dict()
    assert "perspectives" in d
    assert len(d["perspectives"]) == 3
    assert "tension_curve" in d
    assert "preset" in d
    assert "recommended_perspective" in d
