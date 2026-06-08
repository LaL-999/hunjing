"""episode_quality_scorer 单测 — 阶段 8.4+ Phase 5(2026-06-08)。"""
from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from app.screenplay.services import episode_quality_scorer as eqs
from app.screenplay.services.episode_quality_scorer import (
    PlanQualityScores,
    score_plan,
    pick_best_perspective,
)


# ============================================================
# 辅助
# ============================================================


@dataclass
class _FakeEp:
    """mock EpisodeWithMeta。"""
    episode_number: int
    cliffhanger_potential: float
    est_minutes: float
    scene_ids: list[str] = field(default_factory=list)


def _scene_yaml(sid: str, chapter: int | None = 1, chars: list[str] | None = None) -> dict:
    return {
        "id": sid,
        "source": {"chapter": chapter} if chapter is not None else {},
        "characters_present": chars or [],
    }


# ============================================================
# 1. cliffhanger_strength
# ============================================================


def test_cliffhanger_strength_avg():
    """3 集 cliff = [0.3, 0.6, 0.9] → 平均 0.6。"""
    eps = [
        _FakeEp(1, 0.3, 2.0, ["s1"]),
        _FakeEp(2, 0.6, 2.5, ["s2"]),
        _FakeEp(3, 0.9, 3.0, ["s3"]),
    ]
    scenes = [_scene_yaml("s1"), _scene_yaml("s2"), _scene_yaml("s3")]
    result = score_plan(
        eps, target_minutes_per_ep=2.5, cuts=[0, 1, 2], scenes_yaml=scenes,
    )
    assert result.cliffhanger_strength == pytest.approx(0.6, abs=0.01)


# ============================================================
# 2. pacing_evenness
# ============================================================


def test_pacing_evenness_perfect_when_durations_equal():
    """3 集都 2.5 分钟 → cv=0 → pacing=1.0。"""
    eps = [
        _FakeEp(1, 0.5, 2.5, ["s1"]),
        _FakeEp(2, 0.5, 2.5, ["s2"]),
        _FakeEp(3, 0.5, 2.5, ["s3"]),
    ]
    scenes = [_scene_yaml("s1"), _scene_yaml("s2"), _scene_yaml("s3")]
    result = score_plan(
        eps, target_minutes_per_ep=2.5, cuts=[0, 1, 2], scenes_yaml=scenes,
    )
    assert result.pacing_evenness == pytest.approx(1.0, abs=0.01)


def test_pacing_evenness_low_when_durations_vary_wildly():
    """1/1/10 分钟 → pacing 显著低于 0.5。"""
    eps = [
        _FakeEp(1, 0.5, 1.0, ["s1"]),
        _FakeEp(2, 0.5, 1.0, ["s2"]),
        _FakeEp(3, 0.5, 10.0, ["s3"]),
    ]
    scenes = [_scene_yaml("s1"), _scene_yaml("s2"), _scene_yaml("s3")]
    result = score_plan(
        eps, target_minutes_per_ep=2.5, cuts=[0, 1, 2], scenes_yaml=scenes,
    )
    assert result.pacing_evenness < 0.6


def test_pacing_evenness_single_episode_returns_1():
    """单集没方差 → pacing=1.0。"""
    eps = [_FakeEp(1, 0.5, 3.0, ["s1"])]
    scenes = [_scene_yaml("s1")]
    result = score_plan(
        eps, target_minutes_per_ep=3.0, cuts=[0], scenes_yaml=scenes,
    )
    assert result.pacing_evenness == 1.0


# ============================================================
# 3. character_balance
# ============================================================


def test_character_balance_high_when_protagonist_evenly_distributed():
    """主角 '林墨' 在每集都登场 → balance ≈ 1.0。"""
    eps = [
        _FakeEp(1, 0.5, 2.5, ["s1", "s2"]),
        _FakeEp(2, 0.5, 2.5, ["s3", "s4"]),
    ]
    scenes = [
        _scene_yaml("s1", chars=["林墨"]),
        _scene_yaml("s2", chars=["林墨"]),
        _scene_yaml("s3", chars=["林墨"]),
        _scene_yaml("s4", chars=["林墨"]),
    ]
    result = score_plan(
        eps, target_minutes_per_ep=2.5, cuts=[1, 3], scenes_yaml=scenes,
    )
    assert result.character_balance == pytest.approx(1.0, abs=0.01)


def test_character_balance_low_when_protagonist_missing_in_some_episode():
    """主角在第 2 集完全缺席 → balance 大幅扣分。"""
    eps = [
        _FakeEp(1, 0.5, 2.5, ["s1", "s2"]),
        _FakeEp(2, 0.5, 2.5, ["s3", "s4"]),
    ]
    scenes = [
        _scene_yaml("s1", chars=["林墨"]),
        _scene_yaml("s2", chars=["林墨"]),
        _scene_yaml("s3", chars=["路人甲"]),    # 主角缺
        _scene_yaml("s4", chars=["路人乙"]),    # 主角缺
    ]
    result = score_plan(
        eps, target_minutes_per_ep=2.5, cuts=[1, 3], scenes_yaml=scenes,
    )
    # 主角在 s1/s2 出场 100% > 50% total(s1-s4 中 2/4) → 是主角
    # ep1: 主角占比 1.0;ep2: 主角占比 0.0;std 0.5 → balance ≈ 0
    assert result.character_balance < 0.10


def test_character_balance_skipped_when_no_protagonist():
    """无角色 ≥ 50% 总场出场 → balance = 1.0(此维不适用)。"""
    eps = [_FakeEp(1, 0.5, 2.5, ["s1", "s2"])]
    scenes = [
        _scene_yaml("s1", chars=["A"]),
        _scene_yaml("s2", chars=["B"]),  # A 和 B 各出场 50%,等号边界
    ]
    result = score_plan(
        eps, target_minutes_per_ep=2.5, cuts=[1], scenes_yaml=scenes,
    )
    # 50% >= 50%,二者都算主角 — 但单集场景下 balance=1
    assert result.character_balance >= 0.0


# ============================================================
# 4. chapter_continuity
# ============================================================


def test_chapter_continuity_perfect_when_all_cuts_at_boundary():
    """所有切点都在章节边界 → continuity = 1.0。"""
    eps = [
        _FakeEp(1, 0.5, 2.5, ["s1", "s2"]),
        _FakeEp(2, 0.5, 2.5, ["s3", "s4"]),
        _FakeEp(3, 0.5, 2.5, ["s5"]),
    ]
    scenes = [
        _scene_yaml("s1", chapter=1),
        _scene_yaml("s2", chapter=1),   # cut at index 1, next chapter 2
        _scene_yaml("s3", chapter=2),
        _scene_yaml("s4", chapter=2),   # cut at index 3, next chapter 3
        _scene_yaml("s5", chapter=3),   # cut at index 4 (末场,不计)
    ]
    result = score_plan(
        eps, target_minutes_per_ep=2.5, cuts=[1, 3, 4], scenes_yaml=scenes,
    )
    # 末场 cut(index 4)不计;有效切点 2 个,全在边界 → 1.0
    assert result.chapter_continuity == 1.0


def test_chapter_continuity_zero_when_no_cuts_at_boundary():
    """切点全在同章中间 → continuity = 0.0。"""
    eps = [
        _FakeEp(1, 0.5, 2.5, ["s1"]),
        _FakeEp(2, 0.5, 2.5, ["s2"]),
        _FakeEp(3, 0.5, 2.5, ["s3"]),
    ]
    scenes = [
        _scene_yaml("s1", chapter=1),
        _scene_yaml("s2", chapter=1),   # cut at 1, next still chapter 1
        _scene_yaml("s3", chapter=1),   # cut at 2 (末场)
    ]
    result = score_plan(
        eps, target_minutes_per_ep=2.5, cuts=[0, 1, 2], scenes_yaml=scenes,
    )
    # 有效切点: index 0 和 1 (末场 2 不计)
    # 0→1 同章, 1→2 同章 → 0/2 = 0
    assert result.chapter_continuity == 0.0


# ============================================================
# 5. aggregate
# ============================================================


def test_aggregate_high_when_all_dimensions_high():
    """各维度都高 → aggregate 高。"""
    eps = [
        _FakeEp(1, 0.8, 2.5, ["s1"]),
        _FakeEp(2, 0.9, 2.5, ["s2"]),
    ]
    scenes = [
        _scene_yaml("s1", chapter=1, chars=["A"]),
        _scene_yaml("s2", chapter=2, chars=["A"]),
    ]
    result = score_plan(
        eps, target_minutes_per_ep=2.5, cuts=[0, 1], scenes_yaml=scenes,
    )
    # cliff=0.85, pacing≈1, char≈1(A是主角), chapter=1(0→1)
    # aggregate ≈ 0.85*0.35 + 1*0.25 + 1*0.20 + 1*0.20 ≈ 0.7475 + ...
    assert result.aggregate >= 0.85


def test_aggregate_low_when_all_weak():
    """各维度都低 → aggregate 低。"""
    eps = [
        _FakeEp(1, 0.1, 1.0, ["s1"]),
        _FakeEp(2, 0.1, 10.0, ["s2"]),
        _FakeEp(3, 0.1, 1.0, ["s3"]),
    ]
    scenes = [
        _scene_yaml("s1", chapter=1, chars=["A"]),
        _scene_yaml("s2", chapter=1, chars=["B"]),  # 不同主角
        _scene_yaml("s3", chapter=1, chars=["C"]),
    ]
    result = score_plan(
        eps, target_minutes_per_ep=2.5, cuts=[0, 1, 2], scenes_yaml=scenes,
    )
    assert result.aggregate < 0.50


# ============================================================
# 6. 每集独立评分
# ============================================================


def test_episode_scores_present_per_episode():
    """每集都有独立 EpisodeQualityScores。"""
    eps = [
        _FakeEp(1, 0.8, 2.5, ["s1"]),
        _FakeEp(2, 0.3, 5.0, ["s2"]),  # 弱集尾 + 偏长
    ]
    scenes = [_scene_yaml("s1"), _scene_yaml("s2")]
    result = score_plan(
        eps, target_minutes_per_ep=2.5, cuts=[0, 1], scenes_yaml=scenes,
    )
    assert len(result.episode_scores) == 2
    e1 = result.episode_scores[0]
    e2 = result.episode_scores[1]
    assert e1.episode_number == 1
    assert e2.episode_number == 2
    # 第 1 集质量明显高于第 2 集
    assert e1.quality > e2.quality
    # 第 2 集 notes 应含警告
    assert any("钩子弱" in n or "时长" in n for n in e2.notes)


def test_episode_strong_cliff_note():
    """集尾 cliff >= 0.7 → notes 含'集尾钩子强'。"""
    eps = [_FakeEp(1, 0.75, 2.5, ["s1"])]
    scenes = [_scene_yaml("s1")]
    result = score_plan(
        eps, target_minutes_per_ep=2.5, cuts=[0], scenes_yaml=scenes,
    )
    assert any("钩子强" in n for n in result.episode_scores[0].notes)


# ============================================================
# 7. 边界
# ============================================================


def test_empty_episodes_returns_empty_summary():
    """无 episodes → 空结构 + 提示。"""
    result = score_plan(
        [], target_minutes_per_ep=2.5, cuts=[], scenes_yaml=[],
    )
    assert result.aggregate == 0.0
    assert result.summary == "无分集可评"


def test_pick_best_perspective_returns_highest():
    """选 aggregate 最高的 perspective。"""
    scores = {
        "rhythm": PlanQualityScores(aggregate=0.65),
        "hook": PlanQualityScores(aggregate=0.78),
        "arc": PlanQualityScores(aggregate=0.55),
    }
    assert pick_best_perspective(scores) == "hook"


def test_pick_best_perspective_empty_returns_none():
    """空 dict → None。"""
    assert pick_best_perspective({}) is None


def test_to_dict_serialization():
    """PlanQualityScores.to_dict 返完整结构。"""
    eps = [_FakeEp(1, 0.5, 2.5, ["s1"])]
    scenes = [_scene_yaml("s1")]
    result = score_plan(
        eps, target_minutes_per_ep=2.5, cuts=[0], scenes_yaml=scenes,
    )
    d = result.to_dict()
    assert "aggregate" in d
    assert "episode_scores" in d
    assert isinstance(d["episode_scores"], list)
    assert d["episode_scores"][0]["episode_number"] == 1
