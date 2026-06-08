"""episode_title_writer 单测 — 阶段 8.4+ Phase 4(2026-06-08)。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pytest

from app.screenplay.services import episode_title_writer as etw


# ============================================================
# 辅助
# ============================================================


@dataclass
class _FakeEpisode:
    """Mock EpisodeWithMeta(避免 import 循环)。"""
    episode_number: int
    title: str
    scene_ids: list[str]
    cliffhanger_potential: float = 0.0
    tension_peak: float = 0.0
    teaser: Optional[str] = None


def _make_yaml_scene(sid: str, summary: str = "", chars: list[str] | None = None) -> dict:
    return {
        "id": sid,
        "summary": summary,
        "characters_present": chars or [],
    }


class _FakeConn:
    def close(self):
        pass


@pytest.fixture(autouse=True)
def _no_bridge(monkeypatch):
    """默认:bridge resolve 返 None → drivers_block 为空字符串。"""
    monkeypatch.setattr(
        etw.huimeng_bridge, "_resolve_project_id", lambda *a, **kw: None,
    )
    monkeypatch.setattr(etw, "get_connection", lambda: _FakeConn())
    yield


# ============================================================
# 1. LLM 成功 case
# ============================================================


def test_llm_success_returns_title_teaser_map(monkeypatch):
    """LLM 返合法 episodes → 返 dict[episode_number → {title, teaser}]。"""
    eps = [
        _FakeEpisode(1, "第 1 集 · 林墨初入潘西", ["s1"]),
        _FakeEpisode(2, "第 2 集 · 师父出场", ["s2"]),
    ]
    scenes_yaml = [
        _make_yaml_scene("s1", summary="林墨第一天到潘西工厂"),
        _make_yaml_scene("s2", summary="师父出现"),
    ]

    def _fake_llm(system_prompt, user_input, **kwargs):
        return ({
            "episodes": [
                {"episode_number": 1, "title": "林墨潜入潘西:首战即遭嘲笑", "teaser": "他立誓三天反转格局"},
                {"episode_number": 2, "title": "师父神秘登场,谁是真正的高手", "teaser": "真身将揭"},
            ],
        }, {})

    monkeypatch.setattr("app.services.llm_client.call_llm_json", _fake_llm)

    result = etw.write_titles_and_teasers(
        eps, user_id="u1", novel_id="n1",
        scenes_yaml=scenes_yaml, characters_yaml=[],
    )
    assert len(result) == 2
    assert result[1]["title"].startswith("林墨潜入")
    assert "三天" in result[1]["teaser"]
    assert result[2]["title"].startswith("师父")


# ============================================================
# 2. LLM 失败 / 异常回退
# ============================================================


def test_llm_failed_returns_empty_dict(monkeypatch):
    """LLM 抛 LlmCallFailed → 返空 dict(不抛)。"""
    from app.services.llm_client import LlmCallFailed

    def _fake_llm(*args, **kwargs):
        raise LlmCallFailed("API offline")

    monkeypatch.setattr("app.services.llm_client.call_llm_json", _fake_llm)

    eps = [_FakeEpisode(1, "第 1 集", ["s1"])]
    result = etw.write_titles_and_teasers(
        eps, user_id="u1", novel_id="n1",
        scenes_yaml=[_make_yaml_scene("s1")], characters_yaml=[],
    )
    assert result == {}


def test_llm_unexpected_exception_returns_empty_dict(monkeypatch):
    """LLM 抛任意异常 → 返空 dict。"""
    def _fake_llm(*args, **kwargs):
        raise RuntimeError("test error")

    monkeypatch.setattr("app.services.llm_client.call_llm_json", _fake_llm)

    eps = [_FakeEpisode(1, "第 1 集", ["s1"])]
    result = etw.write_titles_and_teasers(
        eps, user_id="u1", novel_id="n1",
        scenes_yaml=[_make_yaml_scene("s1")], characters_yaml=[],
    )
    assert result == {}


# ============================================================
# 3. LLM 输出非法格式
# ============================================================


def test_llm_root_not_dict_returns_empty(monkeypatch):
    """LLM 输出非 dict → 空 dict。"""
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: (["not a dict"], {}),
    )
    eps = [_FakeEpisode(1, "第 1 集", ["s1"])]
    result = etw.write_titles_and_teasers(
        eps, user_id="u1", novel_id="n1",
        scenes_yaml=[_make_yaml_scene("s1")], characters_yaml=[],
    )
    assert result == {}


def test_llm_missing_episodes_array_returns_empty(monkeypatch):
    """LLM 缺 episodes 字段 → 空 dict。"""
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: ({"foo": "bar"}, {}),
    )
    eps = [_FakeEpisode(1, "第 1 集", ["s1"])]
    result = etw.write_titles_and_teasers(
        eps, user_id="u1", novel_id="n1",
        scenes_yaml=[_make_yaml_scene("s1")], characters_yaml=[],
    )
    assert result == {}


def test_llm_partial_invalid_episodes_filtered(monkeypatch):
    """LLM 部分 episode 缺字段 → 仅采纳合法的。"""
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: ({
            "episodes": [
                {"episode_number": 1, "title": "好标题", "teaser": "好预告"},
                {"episode_number": "bad", "title": "x", "teaser": "y"},  # episode_number 非 int
                {"episode_number": 2, "title": "", "teaser": "y"},        # 空 title
                {"episode_number": 3, "title": "好的", "teaser": ""},     # 空 teaser 允许
            ],
        }, {}),
    )
    eps = [
        _FakeEpisode(1, "x", ["s1"]),
        _FakeEpisode(2, "x", ["s2"]),
        _FakeEpisode(3, "x", ["s3"]),
    ]
    result = etw.write_titles_and_teasers(
        eps, user_id="u1", novel_id="n1",
        scenes_yaml=[], characters_yaml=[],
    )
    # 只 1 和 3 合法(2 因 title 空被丢)
    assert 1 in result
    assert 3 in result
    assert 2 not in result


# ============================================================
# 4. 截短保护
# ============================================================


def test_long_title_truncated_to_25_chars(monkeypatch):
    """LLM 给出超长 title → 截短到 25 字。"""
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: ({
            "episodes": [
                {"episode_number": 1,
                 "title": "这是一个非常非常非常非常非常非常非常非常非常长的标题超过了二十五个字",
                 "teaser": "x"},
            ],
        }, {}),
    )
    eps = [_FakeEpisode(1, "x", ["s1"])]
    result = etw.write_titles_and_teasers(
        eps, user_id="u1", novel_id="n1",
        scenes_yaml=[], characters_yaml=[],
    )
    assert len(result[1]["title"]) == 25


def test_long_teaser_truncated_to_60_chars(monkeypatch):
    """LLM 给出超长 teaser → 截短到 60 字。"""
    long_teaser = "天" * 80
    monkeypatch.setattr(
        "app.services.llm_client.call_llm_json",
        lambda *a, **kw: ({
            "episodes": [
                {"episode_number": 1, "title": "正常标题", "teaser": long_teaser},
            ],
        }, {}),
    )
    eps = [_FakeEpisode(1, "x", ["s1"])]
    result = etw.write_titles_and_teasers(
        eps, user_id="u1", novel_id="n1",
        scenes_yaml=[], characters_yaml=[],
    )
    assert len(result[1]["teaser"]) == 60


# ============================================================
# 5. apply_to_episodes 行为
# ============================================================


def test_apply_keeps_episode_prefix():
    """keep_rule_prefix=True 默认 → '第 N 集 · LLM_TITLE' 拼接。"""
    eps = [_FakeEpisode(1, "第 1 集 · 老标题", ["s1"])]
    map_ = {1: {"title": "林墨潜入", "teaser": "悬念"}}
    etw.apply_to_episodes(eps, map_, keep_rule_prefix=True)
    assert eps[0].title == "第 1 集 · 林墨潜入"
    assert eps[0].teaser == "悬念"


def test_apply_can_replace_full_title():
    """keep_rule_prefix=False → 完全替换 title。"""
    eps = [_FakeEpisode(1, "第 1 集 · 老标题", ["s1"])]
    map_ = {1: {"title": "林墨潜入", "teaser": "悬念"}}
    etw.apply_to_episodes(eps, map_, keep_rule_prefix=False)
    assert eps[0].title == "林墨潜入"


def test_apply_skips_missing_or_empty():
    """无映射 / 空标题 → 不动 episode。"""
    eps = [
        _FakeEpisode(1, "第 1 集 · 原 1", ["s1"]),
        _FakeEpisode(2, "第 2 集 · 原 2", ["s2"]),
    ]
    map_ = {
        1: {"title": "", "teaser": "新 1"},   # 空 title → 不替换 title
        # 2 缺失整个映射
    }
    etw.apply_to_episodes(eps, map_, keep_rule_prefix=True)
    assert eps[0].title == "第 1 集 · 原 1"  # 未变
    assert eps[0].teaser == "新 1"            # teaser 仍可单独填
    assert eps[1].title == "第 2 集 · 原 2"
    assert eps[1].teaser is None


# ============================================================
# 6. 空输入 / 边界
# ============================================================


def test_empty_episodes_returns_empty():
    """episodes=[] → 直接返 {}(不调 LLM)。"""
    result = etw.write_titles_and_teasers(
        [], user_id="u1", novel_id="n1",
        scenes_yaml=[], characters_yaml=[],
    )
    assert result == {}


def test_scenes_yaml_summaries_passed_to_llm(monkeypatch):
    """user_input 含每集 scene_summaries — 验证 LLM 输入构造正确。"""
    captured = {}

    def _fake_llm(system_prompt, user_input, **kwargs):
        captured["input"] = user_input
        return ({
            "episodes": [{"episode_number": 1, "title": "t", "teaser": "x"}],
        }, {})

    monkeypatch.setattr("app.services.llm_client.call_llm_json", _fake_llm)

    eps = [_FakeEpisode(1, "x", ["s1", "s2"])]
    scenes_yaml = [
        _make_yaml_scene("s1", summary="夏夜抵达"),
        _make_yaml_scene("s2", summary="师父登场"),
        _make_yaml_scene("s_other", summary="不在该集内"),
    ]
    etw.write_titles_and_teasers(
        eps, user_id="u1", novel_id="n1",
        scenes_yaml=scenes_yaml, characters_yaml=[],
    )
    assert "episodes" in captured["input"]
    ep_inputs = captured["input"]["episodes"]
    assert ep_inputs[0]["scene_summaries"] == ["夏夜抵达", "师父登场"]
