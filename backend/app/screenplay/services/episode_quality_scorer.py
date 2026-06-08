"""分集质量评分 — 阶段 8.4+ Phase 5(2026-06-08)。

为单集 + 整方案算 4 维启发式评分,让用户能用客观数据对比多方案。

---

4 维设计:

  1. cliffhanger_strength 0-1     权重 0.35
     该集尾 cliffhanger_potential。短剧致命因素 — 集尾弱 = 没人追第二集。

  2. pacing_evenness 0-1          权重 0.25
     集间时长方差(越均匀越好)。1 / (1 + cv) 归一化,
     cv = std / mean。3 集都 2.5 min → cv≈0 → pacing≈1;
     2/2/10 → cv≈1.0 → pacing≈0.5。

  3. character_balance 0-1        权重 0.20
     主角(出场 ≥50% 场)戏份在各集的分布均匀度。
     某集没主角戏 = 大扣分(用户看不进去)。

  4. chapter_continuity 0-1       权重 0.20
     cuts 落在章节边界的比例。原作章节切割尊重度,影响"忠实改编"感。

加权总分 aggregate_quality 0-1,用于:
  - PlanQualityScores.aggregate
  - MultiPerspectivePlan.recommended_perspective(Phase 6 后改用)

---

设计原则:
  - 纯启发式 — 无 LLM,可单测,< 50ms
  - 评分公式必须有"明确语义",每个分数 hover 能解释
  - 整方案 + 每集独立打分(用户能看出"这集差")
"""
from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# 权重
_WEIGHTS = {
    "cliffhanger_strength": 0.35,
    "pacing_evenness": 0.25,
    "character_balance": 0.20,
    "chapter_continuity": 0.20,
}


@dataclass
class EpisodeQualityScores:
    """单集质量评分。"""

    episode_number: int
    cliffhanger: float = 0.0           # 0-1
    duration_deviation: float = 0.0    # 与目标时长的偏离(0-1, 1=完美)
    quality: float = 0.0               # 综合 0-1
    notes: list[str] = field(default_factory=list)  # 人话解释


@dataclass
class PlanQualityScores:
    """整方案质量评分。"""

    aggregate: float = 0.0
    cliffhanger_strength: float = 0.0
    pacing_evenness: float = 0.0
    character_balance: float = 0.0
    chapter_continuity: float = 0.0
    episode_scores: list[EpisodeQualityScores] = field(default_factory=list)
    summary: str = ""                  # 一句话总结(用户看)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# 主入口
# ============================================================


def score_plan(
    episodes: list[Any],   # list[EpisodeWithMeta] (避免循环 import)
    *,
    target_minutes_per_ep: float,
    cuts: list[int],
    scenes_yaml: list[dict],
    characters_yaml: list[dict] | None = None,
) -> PlanQualityScores:
    """为整方案打 4 维分 + 每集独立分。

    Args:
        episodes: EpisodeWithMeta 列表(必须含 cliffhanger_potential / est_minutes / scene_ids)
        target_minutes_per_ep: 目标单集时长(用于评 pacing 和 duration)
        cuts: scene index 切点列表(用于评 chapter_continuity)
        scenes_yaml: 原 yaml scenes(用于 chapter_continuity 反查)
        characters_yaml: 角色档(可选,用于 character_balance)

    Returns:
        PlanQualityScores
    """
    if not episodes:
        return PlanQualityScores(summary="无分集可评")

    # 1. cliffhanger_strength = 平均集尾 cliff
    cliff_strength = _calc_cliffhanger_strength(episodes)

    # 2. pacing_evenness = 时长均匀度
    pacing = _calc_pacing_evenness(episodes, target_minutes_per_ep)

    # 3. character_balance = 主角戏份分布
    char_balance = _calc_character_balance(episodes, scenes_yaml, characters_yaml or [])

    # 4. chapter_continuity = 切点落在章节边界的比例
    chapter_cont = _calc_chapter_continuity(cuts, scenes_yaml)

    # 加权总分
    aggregate = (
        cliff_strength * _WEIGHTS["cliffhanger_strength"]
        + pacing * _WEIGHTS["pacing_evenness"]
        + char_balance * _WEIGHTS["character_balance"]
        + chapter_cont * _WEIGHTS["chapter_continuity"]
    )

    # 每集独立分
    ep_scores = [
        _score_single_episode(ep, target_minutes_per_ep)
        for ep in episodes
    ]

    # 人话总结
    summary = _build_summary(aggregate, cliff_strength, pacing, char_balance, chapter_cont)

    return PlanQualityScores(
        aggregate=round(aggregate, 3),
        cliffhanger_strength=round(cliff_strength, 3),
        pacing_evenness=round(pacing, 3),
        character_balance=round(char_balance, 3),
        chapter_continuity=round(chapter_cont, 3),
        episode_scores=ep_scores,
        summary=summary,
    )


# ============================================================
# 各维度计算
# ============================================================


def _calc_cliffhanger_strength(episodes: list[Any]) -> float:
    """所有集尾 cliffhanger_potential 的平均。"""
    if not episodes:
        return 0.0
    cliffs = [ep.cliffhanger_potential for ep in episodes]
    return sum(cliffs) / len(cliffs)


def _calc_pacing_evenness(episodes: list[Any], target: float) -> float:
    """集间时长方差倒数,归一化 0-1。

    公式:
      mean = avg(est_minutes)
      cv   = std / mean
      pacing = 1 / (1 + cv)

    target 没直接用 — 因为均匀度只看分布,不看是否对齐 target。
    target 通过 _score_single_episode.duration_deviation 间接评估。
    """
    if len(episodes) < 2:
        return 1.0  # 单集无方差概念
    durations = [ep.est_minutes for ep in episodes]
    mean = sum(durations) / len(durations)
    if mean <= 0:
        return 0.0
    var = sum((d - mean) ** 2 for d in durations) / len(durations)
    std = math.sqrt(var)
    cv = std / mean
    return 1.0 / (1.0 + cv)


def _calc_character_balance(
    episodes: list[Any],
    scenes_yaml: list[dict],
    characters_yaml: list[dict],
) -> float:
    """主角戏份在各集的均匀度。

    主角定义:在场场景 ≥ 总场数 50% 的角色(自动识别,不依赖外部标记)

    算法:
      1. 统计每个角色出场场数
      2. 选 出场场数 ≥ 0.5 * total_scenes 的为主角
      3. 对每集,算主角"在场场数 / 该集场数"占比
      4. 跨集占比的方差倒数 = 均衡度
    """
    # 1. 建 scene_id → characters_present 映射
    sid_to_chars: dict[str, set[str]] = {}
    for s in scenes_yaml:
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        if not sid:
            continue
        chars = s.get("characters_present") or []
        sid_to_chars[sid] = {c for c in chars if isinstance(c, str)}

    if not sid_to_chars:
        return 1.0  # 无场角色数据,跳过此维(给满分)

    # 2. 统计每角色出场场数
    char_appearance: dict[str, int] = {}
    total_scenes = len(sid_to_chars)
    for chars in sid_to_chars.values():
        for c in chars:
            char_appearance[c] = char_appearance.get(c, 0) + 1

    # 3. 主角:出场 ≥ 50% 总场
    protagonists = {
        cid for cid, cnt in char_appearance.items()
        if cnt >= total_scenes * 0.5
    }
    if not protagonists:
        # 无主角(很短的剧本)→ 跳过(给满分)
        return 1.0

    # 4. 每集统计主角占比
    ep_protagonist_ratios: list[float] = []
    for ep in episodes:
        if not ep.scene_ids:
            continue
        protag_count = 0
        for sid in ep.scene_ids:
            chars = sid_to_chars.get(sid, set())
            if any(p in chars for p in protagonists):
                protag_count += 1
        ep_protagonist_ratios.append(protag_count / len(ep.scene_ids))

    if not ep_protagonist_ratios:
        return 0.0

    # 5. 均衡度 = 1 - std (std 越小越均衡)
    mean_ratio = sum(ep_protagonist_ratios) / len(ep_protagonist_ratios)
    var = sum((r - mean_ratio) ** 2 for r in ep_protagonist_ratios) / len(ep_protagonist_ratios)
    std = math.sqrt(var)
    # std 范围 0-0.5;归一化:1 - 2*std(std=0.5 → 0,std=0 → 1)
    balance = max(0.0, min(1.0, 1.0 - 2.0 * std))
    return balance


def _calc_chapter_continuity(cuts: list[int], scenes_yaml: list[dict]) -> float:
    """切点落在章节边界的比例。

    末场不算切点(无下一场可比)。
    """
    if not cuts or not scenes_yaml:
        return 0.0

    # scene_index → chapter
    idx_to_chapter = []
    for s in scenes_yaml:
        if not isinstance(s, dict):
            continue
        source = s.get("source") or {}
        ch = source.get("chapter") if isinstance(source, dict) else None
        idx_to_chapter.append(ch if isinstance(ch, int) else None)

    if not idx_to_chapter:
        return 0.0

    valid_cuts = [c for c in cuts if 0 <= c < len(idx_to_chapter) - 1]
    if not valid_cuts:
        return 0.0  # 只有末场切点 → 没法评章节连续性

    boundary_hits = 0
    for c in valid_cuts:
        this_ch = idx_to_chapter[c]
        next_ch = idx_to_chapter[c + 1] if c + 1 < len(idx_to_chapter) else None
        if this_ch is not None and next_ch is not None and this_ch != next_ch:
            boundary_hits += 1

    return boundary_hits / len(valid_cuts)


# ============================================================
# 单集评分
# ============================================================


def _score_single_episode(ep: Any, target: float) -> EpisodeQualityScores:
    """单集 4 项 + 综合。"""
    # 1. cliffhanger
    cliff = ep.cliffhanger_potential

    # 2. duration_deviation:1 - |est - target| / target,clip 0-1
    if target <= 0:
        dur_dev = 1.0
    else:
        deviation_ratio = abs(ep.est_minutes - target) / target
        dur_dev = max(0.0, min(1.0, 1.0 - deviation_ratio))

    # 综合:cliff 60% + dur 40%(单集层面 cliff 比时长更重要)
    quality = cliff * 0.60 + dur_dev * 0.40

    notes: list[str] = []
    if cliff < 0.30:
        notes.append(f"集尾钩子弱(potential={cliff:.2f})")
    if dur_dev < 0.50:
        notes.append(f"时长偏离目标(est={ep.est_minutes:.1f} / target={target:.1f})")
    if cliff >= 0.70:
        notes.append("集尾钩子强")
    if dur_dev >= 0.85 and not notes:
        notes.append("时长精准")

    return EpisodeQualityScores(
        episode_number=ep.episode_number,
        cliffhanger=round(cliff, 3),
        duration_deviation=round(dur_dev, 3),
        quality=round(quality, 3),
        notes=notes,
    )


# ============================================================
# 人话总结
# ============================================================


def _build_summary(
    aggregate: float, cliff: float, pacing: float,
    char_balance: float, chapter_cont: float,
) -> str:
    """根据各维度分数生成 1-2 句话总结(给用户看)。"""
    if aggregate >= 0.80:
        prefix = "优秀:整体节奏 + 钩子 + 角色平衡都到位。"
    elif aggregate >= 0.60:
        prefix = "良好:大体合规,部分维度可优化。"
    else:
        prefix = "待优化:多维度偏弱,建议换视角或调 target 分钟数重新切。"

    weakest = min(
        ("cliffhanger", cliff),
        ("pacing", pacing),
        ("character_balance", char_balance),
        ("chapter_continuity", chapter_cont),
        key=lambda kv: kv[1],
    )
    name_to_label = {
        "cliffhanger": "集尾钩子",
        "pacing": "时长均匀",
        "character_balance": "主角戏份分布",
        "chapter_continuity": "章节边界尊重",
    }
    weakest_label = name_to_label[weakest[0]]
    return f"{prefix} 最弱维度:{weakest_label}({weakest[1]:.2f})。"


# ============================================================
# 跨方案推荐(替换 Phase 3 启发式)
# ============================================================


def pick_best_perspective(
    perspective_scores: dict[str, PlanQualityScores],
) -> str | None:
    """从多方案中选 aggregate 最高的 perspective name。

    Args:
        perspective_scores: {"rhythm": PlanQualityScores, "hook": ..., "arc": ...}

    Returns:
        最高分的 perspective key,或 None(全空时)
    """
    if not perspective_scores:
        return None
    best_name = max(
        perspective_scores.keys(),
        key=lambda k: perspective_scores[k].aggregate,
    )
    return best_name


__all__ = [
    "EpisodeQualityScores",
    "PlanQualityScores",
    "score_plan",
    "pick_best_perspective",
]
