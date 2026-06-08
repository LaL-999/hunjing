"""分集规划 — 阶段 8.4 MVP(2026-06-08)。

把已生成的剧本场景序列按"目标单集时长"贪心切成集,优先在 **act 边界 /
章节边界** 切,保留剧本节奏感。

设计原则:
  - 纯规则,无 LLM(MVP 速度优先;后续可加 LLM 增强 logline / cliffhanger)
  - 时长估算公式:est_minutes ≈ elements_count / 25(1 页 ≈ 1 分钟 ≈ 25
    个 elements,行业惯例)
  - 边界优先级:act 切换 > 章节切换 > 强转场(FADE_OUT / SMASH_CUT) > 累计达标
  - 单集时长允许 ±30% 浮动(找最近的好边界比卡死分钟数重要)

输出契约:
  Episode { episode_number, title, scene_ids[], est_minutes,
            scene_count, first_chapter, last_chapter }
  EpisodePlan { episodes[], total_minutes, target_minutes_per_ep }

约束:
  - 所有 scene_id 出现且只出现 1 次(无遗漏 / 无重复)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

import yaml as yamllib

from app.screenplay.services import screenplay_store

logger = logging.getLogger(__name__)


# ============================================================
# 数据契约
# ============================================================


@dataclass
class Episode:
    """一集:含若干 scene_ids + 估算时长。"""
    episode_number: int
    title: str                       # "第 1 集 · 林墨初入潘西"
    scene_ids: list[str] = field(default_factory=list)
    est_minutes: float = 0.0
    scene_count: int = 0
    first_chapter: Optional[int] = None
    last_chapter: Optional[int] = None
    # 边界选择原因(给前端展示"为什么这里切")
    boundary_reason: str = ""  # "act_change" | "chapter_change" | "strong_transition" | "target_met"


@dataclass
class EpisodePlan:
    """完整规划结果。"""
    episodes: list[Episode] = field(default_factory=list)
    total_minutes: float = 0.0
    total_scenes: int = 0
    target_minutes_per_ep: float = 0.0
    mode: str = "rule"  # 'rule' | 'llm'(LLM 增强 — MVP 阶段固定 rule)


# ============================================================
# 主入口
# ============================================================


class EpisodePlanError(Exception):
    """分集规划失败。"""


def plan_episodes(
    novel_id: str,
    user_id: str,
    *,
    target_minutes_per_ep: float = 3.0,
) -> EpisodePlan:
    """对该 novel 的最新 screenplay 做分集规划。

    Args:
        target_minutes_per_ep: 单集目标时长(分钟),短剧 2-3 / 长剧 8-12

    Returns:
        EpisodePlan

    Raises:
        EpisodePlanError: novel 不存在 / 无 screenplay / yaml 解析失败
    """
    record = screenplay_store.get_latest_screenplay(novel_id, user_id=user_id)
    if record is None:
        raise EpisodePlanError("作品尚未生成剧本,无法分集")

    try:
        parsed = yamllib.safe_load(record["yaml_text"]) or {}
    except yamllib.YAMLError as e:
        raise EpisodePlanError(f"剧本 YAML 解析失败:{e}")

    scenes = parsed.get("scenes") if isinstance(parsed, dict) else None
    if not isinstance(scenes, list) or not scenes:
        raise EpisodePlanError("剧本无场景,无法分集")

    # 1. 给每场算 est_minutes
    scene_details = _enrich_scenes(scenes)

    # 2. 规则贪心切集
    episodes = _greedy_split(scene_details, target_minutes_per_ep)

    # 3. 给每集起标题
    for i, ep in enumerate(episodes):
        ep.episode_number = i + 1
        ep.title = _generate_title(ep, scene_details)

    total_minutes = sum(ep.est_minutes for ep in episodes)
    total_scenes = sum(ep.scene_count for ep in episodes)

    # 4. **断言**:所有 scene_id 恰好出现一次(单测铁律)
    all_ids = [sid for ep in episodes for sid in ep.scene_ids]
    if len(all_ids) != len(set(all_ids)):
        raise EpisodePlanError("分集错误:某 scene_id 出现多次")
    if len(all_ids) != len(scene_details):
        raise EpisodePlanError("分集错误:有 scene 未被收入任一集")

    return EpisodePlan(
        episodes=episodes,
        total_minutes=round(total_minutes, 2),
        total_scenes=total_scenes,
        target_minutes_per_ep=target_minutes_per_ep,
        mode="rule",
    )


# ============================================================
# 内部
# ============================================================


@dataclass
class _SceneDetail:
    """单场内部表示。"""
    scene_id: str
    number: int
    summary: str
    chapter: Optional[int]
    transition_to_next: str
    act: Optional[str] = None  # 来自 structure_analyzer act("act1" / "act2" / "act3")
    est_minutes: float = 0.0
    element_count: int = 0


def _enrich_scenes(yaml_scenes: list[dict]) -> list[_SceneDetail]:
    """yaml.scenes → _SceneDetail[] 带时长估算。"""
    out: list[_SceneDetail] = []
    for s in yaml_scenes:
        if not isinstance(s, dict):
            continue
        sid = s.get("id", "")
        if not sid:
            continue
        elements = s.get("elements") or []
        element_count = len(elements) if isinstance(elements, list) else 0
        source = s.get("source") or {}
        chapter = source.get("chapter") if isinstance(source, dict) else None
        # est_minutes 经验:1 页 ≈ 1 分钟 ≈ ~25 elements;短场最少 0.4 分钟
        est = max(0.4, element_count / 25.0)
        out.append(_SceneDetail(
            scene_id=sid,
            number=int(s.get("number", 0)) if isinstance(s.get("number"), int) else 0,
            summary=str(s.get("summary", "") or ""),
            chapter=chapter if isinstance(chapter, int) else None,
            transition_to_next=str(s.get("transition_to_next", "") or "").upper(),
            element_count=element_count,
            est_minutes=round(est, 2),
        ))
    return out


def _greedy_split(
    scenes: list[_SceneDetail], target: float,
) -> list[Episode]:
    """贪心:累加 est_minutes,在合适边界切集。

    切集策略:
      - 累加到 target 的 70% 时,开始**找好边界**
        (chapter 切换 / strong_transition)
      - 累加到 target 的 130% 时,**强制切**
      - 累加到 target 100% 时,在下一个章节边界切(若 10% 内有)

    "强转场":FADE_OUT / SMASH_CUT / FADE_IN(不算 CUT_TO / CONTINUOUS)
    """
    STRONG_TRANSITIONS = {"FADE_OUT", "SMASH_CUT", "FADE_IN", "DISSOLVE_TO"}

    episodes: list[Episode] = []
    current_scenes: list[_SceneDetail] = []
    current_minutes = 0.0

    for i, sc in enumerate(scenes):
        current_scenes.append(sc)
        current_minutes += sc.est_minutes

        # 计算下一场的 chapter 是不是变了
        next_sc = scenes[i + 1] if i + 1 < len(scenes) else None
        is_chapter_boundary = (
            next_sc is not None
            and sc.chapter is not None
            and next_sc.chapter is not None
            and next_sc.chapter != sc.chapter
        )
        is_strong_transition = sc.transition_to_next in STRONG_TRANSITIONS
        is_last_scene = next_sc is None

        # 决定是否在这里切
        should_cut = False
        reason = ""

        if is_last_scene:
            should_cut = True
            reason = "end_of_screenplay"
        elif current_minutes >= target * 1.3:
            # 太长了,强切
            should_cut = True
            reason = "target_overflow"
        elif current_minutes >= target * 0.7:
            # 落在 70% - 130% 区间 → 找好边界
            if is_chapter_boundary:
                should_cut = True
                reason = "chapter_change"
            elif is_strong_transition:
                should_cut = True
                reason = "strong_transition"
            elif current_minutes >= target:
                # 100% 但下一场也不是好边界,先看看下一场之后是不是好边界,不是就在这切
                # MVP 简化:100% 就切
                should_cut = True
                reason = "target_met"

        if should_cut:
            ep = _scenes_to_episode(current_scenes, reason)
            episodes.append(ep)
            current_scenes = []
            current_minutes = 0.0

    # 防御:还有剩余(理论上 is_last_scene 已经处理了)
    if current_scenes:
        episodes.append(_scenes_to_episode(current_scenes, "remainder"))

    return episodes


def _scenes_to_episode(scenes: list[_SceneDetail], reason: str) -> Episode:
    """把一组 _SceneDetail 打包成 Episode(标题留空,后面统一起)。"""
    chapters = [s.chapter for s in scenes if s.chapter is not None]
    return Episode(
        episode_number=0,  # 后面统一编号
        title="",
        scene_ids=[s.scene_id for s in scenes],
        est_minutes=round(sum(s.est_minutes for s in scenes), 2),
        scene_count=len(scenes),
        first_chapter=min(chapters) if chapters else None,
        last_chapter=max(chapters) if chapters else None,
        boundary_reason=reason,
    )


def _generate_title(ep: Episode, all_scenes: list[_SceneDetail]) -> str:
    """规则版标题:用首场的 summary 前 14 字。

    例如:"第 1 集 · 林墨初入潘西"
    LLM 增强版会改成更戏剧化的 logline,本 MVP 保持朴素。
    """
    if not ep.scene_ids:
        return f"第 {ep.episode_number} 集"
    # 找首场的 summary
    first_sid = ep.scene_ids[0]
    first_scene = next((s for s in all_scenes if s.scene_id == first_sid), None)
    if not first_scene or not first_scene.summary:
        return f"第 {ep.episode_number} 集"
    summary = first_scene.summary.strip()
    # 截短 — 取第一句话或前 14 字
    for sep in ["。", "?", "!", ";", ","]:
        if sep in summary:
            summary = summary.split(sep)[0]
            break
    summary = summary[:14]
    return f"第 {ep.episode_number} 集 · {summary}"


def to_dict(plan: EpisodePlan) -> dict[str, Any]:
    """EpisodePlan → JSON-serializable dict(给 API 返回用)。"""
    return asdict(plan)
