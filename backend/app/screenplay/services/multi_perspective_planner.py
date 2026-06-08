"""多视角分集规划 — 阶段 8.4+ Phase 3(2026-06-08)。

为同一剧本同时生成 3 个独立的分集方案,让作者对比择优 —— 这是从「机器
单一答案」升级到「作者级提案桌面」的关键差异化。

---

3 个视角:

  1. **rhythm 节奏视角** — 张力波形驱动
     在 dramatic_curve_analyzer 算出的 tension_curve 上找"上升后回落"的
     谷地切集。让用户每集结尾感到一个完整起伏,翻篇时情绪舒展不憋。

  2. **hook 钩子视角** — 集尾钩子最大化
     按 cliffhanger_potential 降序排,在 target±30% 范围内,选钩子最强
     的场作为集尾。短剧黄金法则,适合追看强度高的题材。

  3. **arc 角色弧光视角** — 转折点定锚(LLM 推断)
     桥接 SP-2 character drivers,让 LLM 看角色弧光 arc_from_to + 各场
     summary,推断哪些场是角色弧光的"转折瞬间",作为集间切点。

  失败回退:arc 视角 LLM 失败 → 回退到 rhythm 策略,绝不阻断整体输出。

---

设计纪律:

  - 3 视角独立运行,某视角失败不影响其他
  - 桥接资产缺失时,arc 视角自动退到 rhythm 策略(标 fallback)
  - 每视角输出 rationale 一句话,告诉作者"为什么这样切"
  - quality_scores 字段预留 None,由 Phase 5 episode_quality_scorer 后填
  - 不写 DB(分集结果每次重算)

---

预设档(preset)— 不同题材的"切集口味"配方:

  - short_drama:2-3 min/集,cliff 阈值 0.5(强钩子)
  - long_drama:8-12 min/集,cliff 阈值 0.3(弱钩子允许)
  - anime:22-30 min/集,act 结构感强
  - custom:用户自填 target_minutes,其他参数默认 short_drama
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import yaml as yamllib

from app.db import get_connection
from app.screenplay.services import dramatic_curve_analyzer as dca
from app.screenplay.services import huimeng_bridge, screenplay_store
from app.screenplay.services.dramatic_curve_analyzer import SceneCurveData

logger = logging.getLogger(__name__)


# ============================================================
# 数据契约
# ============================================================


PERSPECTIVE_LABELS = {
    "rhythm": "节奏视角",
    "hook": "钩子视角",
    "arc": "角色弧光视角",
}

PERSPECTIVE_DESCRIPTIONS = {
    "rhythm": "在张力波形的谷地切集,让每集有完整起伏(适合长剧 / 情感戏)",
    "hook": "选 cliffhanger 最强的场作集尾,追看欲拉满(适合短剧 / 悬疑)",
    "arc": "看角色弧光转折点切集,人物成长感强(适合人物剧 / 番剧)",
}


# 预设档(可由前端 chip 切换)
PRESETS = {
    "short_drama": {
        "target_minutes": 2.5,
        "min_cliff_for_cut": 0.50,
        "tension_weight": 0.40,
        "label": "短剧(2-3 分钟/集)",
        "description": "高密度钩子,适合手机端竖屏追看",
    },
    "long_drama": {
        "target_minutes": 10.0,
        "min_cliff_for_cut": 0.35,
        "tension_weight": 0.30,
        "label": "长剧(8-12 分钟/集)",
        "description": "节奏从容,允许情感场延展",
    },
    "anime": {
        "target_minutes": 22.0,
        "min_cliff_for_cut": 0.35,
        "tension_weight": 0.30,
        "label": "番剧(22-30 分钟/集)",
        "description": "act 结构感强,每集独立故事",
    },
    "custom": {
        "target_minutes": None,  # 由参数决定
        "min_cliff_for_cut": 0.40,
        "tension_weight": 0.35,
        "label": "自定义",
        "description": "按用户指定时长切",
    },
}


@dataclass
class EpisodeWithMeta:
    """单集 — 比 episode_planner.Episode 多了曲线元数据。"""

    episode_number: int
    title: str
    scene_ids: list[str]
    est_minutes: float
    scene_count: int
    first_chapter: Optional[int] = None
    last_chapter: Optional[int] = None
    boundary_reason: str = ""

    # 曲线元数据(给前端可视化用)
    cliffhanger_potential: float = 0.0   # 本集最后一场的钩子潜力
    tension_peak: float = 0.0            # 本集所有场的最大 tension_score
    tension_avg: float = 0.0             # 本集平均 tension
    summary_preview: str = ""            # 集首场 summary 前 30 字

    # Phase 4 + 5 后填的字段(MVP 阶段为 None)
    teaser: Optional[str] = None         # 下集预告(40 字以内)
    quality_score: Optional[float] = None  # 本集质量 0-1


@dataclass
class PerspectivePlan:
    """单视角的完整分集方案。"""

    perspective: str            # rhythm / hook / arc
    label: str                  # 中文标签
    description: str            # 视角说明
    episodes: list[EpisodeWithMeta] = field(default_factory=list)
    total_minutes: float = 0.0
    total_scenes: int = 0
    rationale: str = ""         # 一句话解释切法
    cuts: list[int] = field(default_factory=list)  # scene index 切点列表

    # 元数据
    bridge_used: bool = False
    llm_used: bool = False
    llm_failed: bool = False    # arc 视角 LLM 失败 fallback 标志

    # Phase 5 后填
    aggregate_quality: Optional[float] = None  # 整方案平均质量


@dataclass
class MultiPerspectivePlan:
    """多视角对比结果(API 顶层返回)。"""

    perspectives: list[PerspectivePlan] = field(default_factory=list)
    target_minutes_per_ep: float = 0.0
    preset: str = "short_drama"
    preset_label: str = ""
    bridge_used: bool = False    # dramatic_curve_analyzer 是否用了桥接
    bridge_data_source: str = "rule_fallback"

    # 曲线数据(供前端画 tension curve 图)
    tension_curve: list[float] = field(default_factory=list)
    candidate_cut_count: int = 0

    # 推荐:三方案里 aggregate_quality 最高的 perspective
    recommended_perspective: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ============================================================
# 主入口
# ============================================================


class MultiPerspectivePlanError(Exception):
    """分集规划失败(顶层错误,例如剧本不存在)。"""


def plan_with_perspectives(
    novel_id: str,
    user_id: str,
    *,
    preset: str = "short_drama",
    target_minutes_per_ep: Optional[float] = None,
) -> MultiPerspectivePlan:
    """主入口:跑 3 视角分集 + 桥接增强。

    Args:
        novel_id: 当前小说
        user_id: 隔离校验
        preset: short_drama / long_drama / anime / custom
        target_minutes_per_ep: 若 preset="custom" 必填;其他档可覆盖默认

    Returns:
        MultiPerspectivePlan(3 个 PerspectivePlan)

    Raises:
        MultiPerspectivePlanError: 剧本不存在或解析失败
    """
    # 1. 拉剧本
    record = screenplay_store.get_latest_screenplay(novel_id, user_id=user_id)
    if record is None:
        raise MultiPerspectivePlanError("作品尚未生成剧本,无法分集")

    try:
        parsed = yamllib.safe_load(record["yaml_text"]) or {}
    except yamllib.YAMLError as e:
        raise MultiPerspectivePlanError(f"剧本 YAML 解析失败:{e}")

    scenes_yaml = parsed.get("scenes") if isinstance(parsed, dict) else None
    if not isinstance(scenes_yaml, list) or not scenes_yaml:
        raise MultiPerspectivePlanError("剧本无场景,无法分集")

    characters_yaml = parsed.get("characters") if isinstance(parsed, dict) else []
    if not isinstance(characters_yaml, list):
        characters_yaml = []

    # 2. 解析 preset
    preset_cfg = PRESETS.get(preset) or PRESETS["short_drama"]
    if preset == "custom":
        if target_minutes_per_ep is None or target_minutes_per_ep <= 0:
            target_minutes_per_ep = 3.0  # 兜底
    else:
        if target_minutes_per_ep is None:
            target_minutes_per_ep = preset_cfg["target_minutes"]

    # 3. 算曲线数据(无 LLM)
    conn = get_connection()
    try:
        curve = dca.analyze_dramatic_curve(
            conn,
            user_id=user_id,
            novel_id=novel_id,
            scenes_yaml=scenes_yaml,
            characters_yaml=characters_yaml,
        )
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass

    # 4. 三视角并行(rhythm / hook 纯规则,arc 调 LLM)
    rhythm_plan = _plan_rhythm(curve, scenes_yaml, target_minutes_per_ep, preset_cfg)
    hook_plan = _plan_hook(curve, scenes_yaml, target_minutes_per_ep, preset_cfg)
    arc_plan = _plan_arc(
        curve, scenes_yaml, characters_yaml,
        target_minutes_per_ep, preset_cfg,
        user_id=user_id, novel_id=novel_id,
    )

    # 5. 组装顶层结果
    result = MultiPerspectivePlan(
        perspectives=[rhythm_plan, hook_plan, arc_plan],
        target_minutes_per_ep=target_minutes_per_ep,
        preset=preset,
        preset_label=preset_cfg["label"],
        bridge_used=curve.bridge_used,
        bridge_data_source=curve.bridge_data_source,
        tension_curve=[round(s.tension_score, 3) for s in curve.scenes],
        candidate_cut_count=len(curve.candidate_cut_points),
    )

    # 推荐:目前没评分,选 cuts 数量最多但 episode 平均时长最接近 target 的(轻启发式)
    # Phase 5 后会改用 aggregate_quality
    result.recommended_perspective = _heuristic_pick_recommended(
        [rhythm_plan, hook_plan, arc_plan], target_minutes_per_ep,
    )

    return result


# ============================================================
# 视角 1: rhythm — 节奏视角
# ============================================================


def _plan_rhythm(
    curve: dca.CurveAnalysisResult,
    scenes_yaml: list[dict],
    target: float,
    preset_cfg: dict,
) -> PerspectivePlan:
    """节奏视角:tension valley 优先切。

    算法:
      1. 累加 est_minutes
      2. 进入 target±30% 区间时,**找最近的 tension valley(本场比前场低)**
      3. 没 valley 但 130% 超出 → 强切
      4. 章节边界 + valley → 加权选

    rationale 说明:"在张力回落的谷地切集,每集尾有一个完整的起伏弧"
    """
    if not curve.scenes:
        return PerspectivePlan(
            perspective="rhythm",
            label=PERSPECTIVE_LABELS["rhythm"],
            description=PERSPECTIVE_DESCRIPTIONS["rhythm"],
            rationale="无场景可切",
        )

    cuts: list[int] = []
    current_idx_start = 0
    current_minutes = 0.0

    for i, sd in enumerate(curve.scenes):
        current_minutes += sd.est_minutes
        next_sd = curve.scenes[i + 1] if i + 1 < len(curve.scenes) else None
        is_last = next_sd is None

        # 是否在 70%-130% target 窗口内
        in_window = (target * 0.7) <= current_minutes <= (target * 1.3)
        overflow = current_minutes > target * 1.3

        # 节奏视角偏好:本场是 valley(下一场 tension 更低 = 本场是局部峰 = 不切;
        # 反之 tension_delta < -0.10 = 张力刚下降到这,适合留白切)
        is_valley_after = (
            next_sd is not None
            and next_sd.tension_score < sd.tension_score - 0.05
        )

        should_cut = False
        reason = ""

        if is_last:
            should_cut = True
            reason = "end_of_screenplay"
        elif overflow:
            should_cut = True
            reason = "target_overflow"
        elif in_window:
            # 优先级:章节边界 > valley > strong_transition > 100% target
            if sd.is_chapter_boundary_after:
                should_cut = True
                reason = "chapter_change_in_window"
            elif is_valley_after:
                should_cut = True
                reason = "tension_valley"
            elif sd.is_strong_transition:
                should_cut = True
                reason = "strong_transition"
            elif current_minutes >= target:
                should_cut = True
                reason = "target_met"

        if should_cut:
            cuts.append(i)
            current_idx_start = i + 1
            current_minutes = 0.0

    # cuts → episodes
    episodes = _cuts_to_episodes(curve.scenes, scenes_yaml, cuts)
    total_min = sum(ep.est_minutes for ep in episodes)
    total_scenes = sum(ep.scene_count for ep in episodes)

    rationale = (
        f"在张力回落的谷地切集,每集共 {len(episodes)} 集,"
        f"平均 {total_min / max(1, len(episodes)):.1f} 分钟。"
        f"{'桥接 SP-4 情绪曲线启用;' if curve.bridge_used else ''}"
        f"优先在章节边界 + tension 下降处留白。"
    )

    return PerspectivePlan(
        perspective="rhythm",
        label=PERSPECTIVE_LABELS["rhythm"],
        description=PERSPECTIVE_DESCRIPTIONS["rhythm"],
        episodes=episodes,
        total_minutes=round(total_min, 2),
        total_scenes=total_scenes,
        rationale=rationale,
        cuts=cuts,
        bridge_used=curve.bridge_used,
        llm_used=False,
    )


# ============================================================
# 视角 2: hook — 钩子视角
# ============================================================


def _plan_hook(
    curve: dca.CurveAnalysisResult,
    scenes_yaml: list[dict],
    target: float,
    preset_cfg: dict,
) -> PerspectivePlan:
    """钩子视角:每集尾必须是高 cliffhanger 场。

    算法:
      1. 累加 est_minutes 到 target±30%
      2. 区间内选 cliffhanger_potential 最高的场作切点
      3. 区间内无 candidate(全部低于阈值)→ 强切到下个候选 / 强切当前
    """
    if not curve.scenes:
        return PerspectivePlan(
            perspective="hook",
            label=PERSPECTIVE_LABELS["hook"],
            description=PERSPECTIVE_DESCRIPTIONS["hook"],
            rationale="无场景可切",
        )

    min_cliff = preset_cfg.get("min_cliff_for_cut", 0.40)
    cuts: list[int] = []
    window_start = 0
    accumulated_minutes = 0.0

    for i, sd in enumerate(curve.scenes):
        accumulated_minutes += sd.est_minutes
        next_sd = curve.scenes[i + 1] if i + 1 < len(curve.scenes) else None
        is_last = next_sd is None

        # 是否累到 target 上下沿
        in_lower_window = accumulated_minutes >= target * 0.7
        in_upper_window = accumulated_minutes >= target * 1.3

        should_cut = False
        reason = ""

        if is_last:
            should_cut = True
            reason = "end_of_screenplay"
        elif in_upper_window:
            # 超出上沿,强切 — 但首选 window 内 cliff 最高的场作 cut
            # 在 [window_start, i] 区间内找 cliff 最高的
            best_idx = i
            best_cliff = sd.cliffhanger_potential
            for j in range(window_start, i + 1):
                if curve.scenes[j].cliffhanger_potential > best_cliff:
                    best_cliff = curve.scenes[j].cliffhanger_potential
                    best_idx = j
            cuts.append(best_idx)
            window_start = best_idx + 1
            # 重算累积:被砍掉的尾段不算在本集
            accumulated_minutes = sum(
                curve.scenes[k].est_minutes
                for k in range(best_idx + 1, i + 1)
            )
            continue
        elif in_lower_window and sd.cliffhanger_potential >= min_cliff:
            should_cut = True
            reason = f"hook_above_threshold({sd.cliffhanger_potential:.2f})"

        if should_cut:
            cuts.append(i)
            window_start = i + 1
            accumulated_minutes = 0.0

    episodes = _cuts_to_episodes(curve.scenes, scenes_yaml, cuts)
    total_min = sum(ep.est_minutes for ep in episodes)
    total_scenes = sum(ep.scene_count for ep in episodes)

    # 统计有多少集是高钩子结尾
    high_hook_count = sum(1 for ep in episodes if ep.cliffhanger_potential >= 0.50)

    rationale = (
        f"每集尾选 cliffhanger_potential ≥ {min_cliff:.2f} 的场,"
        f"共 {len(episodes)} 集,其中 {high_hook_count} 集尾达高钩子级别。"
    )

    return PerspectivePlan(
        perspective="hook",
        label=PERSPECTIVE_LABELS["hook"],
        description=PERSPECTIVE_DESCRIPTIONS["hook"],
        episodes=episodes,
        total_minutes=round(total_min, 2),
        total_scenes=total_scenes,
        rationale=rationale,
        cuts=cuts,
        bridge_used=curve.bridge_used,
        llm_used=False,
    )


# ============================================================
# 视角 3: arc — 角色弧光视角(LLM)
# ============================================================


_ARC_LLM_SYSTEM_PROMPT = """你是资深剧集策划师,负责判断角色弧光转折点作为分集集间切点。

输入:
  - 角色驱动力档案(每个主角:surface_goal / deep_need / arc_from_to)
  - 全剧场景清单(scene_id / number / chapter / summary / tension_score)
  - 当前 candidate_cut_points(算法已筛出的高质量切点候选)
  - target_minutes_per_ep(目标单集时长)
  - 全剧估算总分钟数

任务:
  从候选切点(或邻近场)中挑出"恰好是角色弧光转折"的切点 — 优先在角色
  surface_goal 改变 / deep_need 浮现 / arc_from_to 关键节点完成的场切集。
  集数控制在 ceil(总时长 / target ± 20%)。

输出严格 JSON(无 markdown 围栏):
  {
    "cuts": [scene_index, scene_index, ...],  // scene index 列表(0-based),
                                              // 表示"本场作集尾"(末场必入)
    "rationale": "一句话解释为什么这样切",
    "arc_turn_evidence": [
      {"scene_index": N, "character": "林墨", "turn_type": "goal_change",
       "explanation": "本场首次承认自己 deep_need"}
    ]
  }

铁律:
  1. cuts 必须升序,且每个 index 在 0..(scenes_count-1) 范围内
  2. cuts 最后一个必须是末场 scenes_count-1
  3. cuts 之间不许重复
  4. 若桥接角色档为空 → 仍要给出切点(用 scene summary + tension_score 推断),
     此时 arc_turn_evidence 可为空数组
"""


def _plan_arc(
    curve: dca.CurveAnalysisResult,
    scenes_yaml: list[dict],
    characters_yaml: list[dict],
    target: float,
    preset_cfg: dict,
    *,
    user_id: str,
    novel_id: str,
) -> PerspectivePlan:
    """角色弧光视角:LLM 推断转折点。

    步骤:
      1. 桥接拉 SP-2 character_drivers_block(可能空)
      2. 构造 LLM 输入(driver + scene summaries + candidate_cuts)
      3. 调 call_llm_json(BYOK 自动)
      4. 验证 cuts 合法 → 转 episodes
      5. 失败 → 退化到 rhythm 策略,标 llm_failed=True
    """
    if not curve.scenes:
        return PerspectivePlan(
            perspective="arc",
            label=PERSPECTIVE_LABELS["arc"],
            description=PERSPECTIVE_DESCRIPTIONS["arc"],
            rationale="无场景可切",
        )

    # 桥接拉角色 driver(在场角色取所有主角)
    drivers_block = ""
    try:
        # 取所有 scene 出现过的角色名(去重)
        all_char_ids: set[str] = set()
        for s in scenes_yaml:
            if not isinstance(s, dict):
                continue
            chars = s.get("characters_present") or []
            for c in chars:
                if isinstance(c, str):
                    all_char_ids.add(c)
        # id → name 映射
        id_to_name = {}
        for c in characters_yaml:
            if isinstance(c, dict) and c.get("id") and c.get("name"):
                id_to_name[c["id"]] = c["name"]
        char_names = [id_to_name[cid] for cid in all_char_ids if cid in id_to_name]

        if char_names:
            conn = get_connection()
            try:
                drivers_block = huimeng_bridge.get_character_drivers_block(
                    conn, user_id=user_id, novel_id=novel_id,
                    character_names=char_names,
                )
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
    except Exception as e:  # noqa: BLE001
        logger.warning("arc plan: bridge driver block failed: %s", e)
        drivers_block = ""

    # 构造 LLM 输入
    scene_brief = [
        {
            "scene_index": i,
            "scene_id": sd.scene_id,
            "number": sd.scene_number,
            "chapter": sd.chapter,
            "summary": sd.summary[:80],  # 长 summary 截短防 token 爆
            "tension_score": sd.tension_score,
            "cliffhanger_potential": sd.cliffhanger_potential,
            "is_chapter_boundary": sd.is_chapter_boundary_after,
            "est_minutes": sd.est_minutes,
        }
        for i, sd in enumerate(curve.scenes)
    ]
    total_minutes = sum(sd.est_minutes for sd in curve.scenes)
    user_input = {
        "drivers_block": drivers_block or "(无桥接角色档 — 请根据 scene summary 推断弧光)",
        "scenes": scene_brief,
        "candidate_cut_points": curve.candidate_cut_points,
        "target_minutes_per_ep": target,
        "total_minutes": round(total_minutes, 2),
        "estimated_episode_count": max(1, round(total_minutes / max(0.5, target))),
    }

    cuts: list[int] = []
    llm_failed = False

    try:
        # 用父平台 call_llm_json,BYOK 自动拦截
        from app.services.llm_client import call_llm_json, LlmCallFailed, LlmJsonParseFailed

        parsed, _usage = call_llm_json(
            _ARC_LLM_SYSTEM_PROMPT,
            user_input,
            max_tokens=2000,
            temperature=0.3,
            retries=1,
            user_id=user_id,
        )
        if not isinstance(parsed, dict):
            raise LlmJsonParseFailed("响应根节点不是 dict")

        raw_cuts = parsed.get("cuts")
        if not isinstance(raw_cuts, list):
            raise LlmJsonParseFailed("缺 cuts 数组")

        # 验证 cuts:int / 升序 / 范围 / 末场必入
        N = len(curve.scenes)
        verified_cuts = []
        last_v = -1
        for c in raw_cuts:
            if not isinstance(c, int):
                continue
            if c <= last_v:
                continue
            if c < 0 or c >= N:
                continue
            verified_cuts.append(c)
            last_v = c
        # 末场必入
        if not verified_cuts or verified_cuts[-1] != N - 1:
            verified_cuts.append(N - 1)
        cuts = verified_cuts

    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning("arc plan: LLM failed, fallback to rhythm: %s", e)
        llm_failed = True
    except Exception as e:  # noqa: BLE001
        logger.warning("arc plan: unexpected error, fallback to rhythm: %s", e)
        llm_failed = True

    # LLM 失败回退到 rhythm 算法
    if llm_failed or not cuts:
        rhythm_plan = _plan_rhythm(curve, scenes_yaml, target, preset_cfg)
        # 把 perspective / label 改成 arc,标 llm_failed
        return PerspectivePlan(
            perspective="arc",
            label=PERSPECTIVE_LABELS["arc"],
            description=PERSPECTIVE_DESCRIPTIONS["arc"],
            episodes=rhythm_plan.episodes,
            total_minutes=rhythm_plan.total_minutes,
            total_scenes=rhythm_plan.total_scenes,
            rationale="LLM 调用失败,已自动退到节奏视角算法切集" if llm_failed
                      else "无 LLM 输出,退到节奏视角",
            cuts=rhythm_plan.cuts,
            bridge_used=curve.bridge_used,
            llm_used=True,
            llm_failed=True,
        )

    episodes = _cuts_to_episodes(curve.scenes, scenes_yaml, cuts)
    total_min = sum(ep.est_minutes for ep in episodes)
    total_scenes = sum(ep.scene_count for ep in episodes)

    has_drivers = bool(drivers_block)
    rationale = (
        f"LLM {'读取角色驱动力档案后' if has_drivers else '(无桥接资产,凭剧本)'} "
        f"在角色弧光转折点切集,共 {len(episodes)} 集。"
    )

    return PerspectivePlan(
        perspective="arc",
        label=PERSPECTIVE_LABELS["arc"],
        description=PERSPECTIVE_DESCRIPTIONS["arc"],
        episodes=episodes,
        total_minutes=round(total_min, 2),
        total_scenes=total_scenes,
        rationale=rationale,
        cuts=cuts,
        bridge_used=curve.bridge_used,
        llm_used=True,
        llm_failed=False,
    )


# ============================================================
# 内部:cuts → episodes
# ============================================================


def _cuts_to_episodes(
    curve_scenes: list[SceneCurveData],
    scenes_yaml: list[dict],
    cuts: list[int],
) -> list[EpisodeWithMeta]:
    """把 cut 索引列表转成 EpisodeWithMeta[]。

    cuts[i] 表示"本场作第 i+1 集的最后一场"。
    """
    if not cuts:
        return []

    # 确保末场必入(防御)
    N = len(curve_scenes)
    if cuts[-1] != N - 1:
        cuts = cuts + [N - 1]

    # 取 yaml scene id → summary 映射(给 summary_preview)
    sid_to_summary = {
        s.get("id"): str(s.get("summary") or "")
        for s in scenes_yaml
        if isinstance(s, dict) and s.get("id")
    }

    episodes: list[EpisodeWithMeta] = []
    start = 0
    for ep_idx, cut in enumerate(cuts):
        end = cut + 1  # exclusive
        slice_scenes = curve_scenes[start:end]
        if not slice_scenes:
            continue

        last_scene = slice_scenes[-1]
        first_scene = slice_scenes[0]

        chapters = [s.chapter for s in slice_scenes if s.chapter is not None]
        first_chapter = min(chapters) if chapters else None
        last_chapter = max(chapters) if chapters else None

        tension_peak = max((s.tension_score for s in slice_scenes), default=0.0)
        tension_avg = (
            sum(s.tension_score for s in slice_scenes) / len(slice_scenes)
            if slice_scenes else 0.0
        )

        # summary_preview 来自首场
        first_summary = sid_to_summary.get(first_scene.scene_id, "")
        # 截取前 30 字 + 第一句
        for sep in ("。", "?", "!", ";", ","):
            if sep in first_summary:
                first_summary = first_summary.split(sep)[0]
                break
        summary_preview = first_summary[:30]

        # 标题:Phase 4 LLM 改写前,先用规则版"第 N 集 · 首场 summary 前 14 字"
        title_summary = sid_to_summary.get(first_scene.scene_id, "")
        for sep in ("。", "?", "!", ";", ","):
            if sep in title_summary:
                title_summary = title_summary.split(sep)[0]
                break
        title = f"第 {ep_idx + 1} 集 · {title_summary[:14]}" if title_summary else f"第 {ep_idx + 1} 集"

        episodes.append(EpisodeWithMeta(
            episode_number=ep_idx + 1,
            title=title,
            scene_ids=[s.scene_id for s in slice_scenes],
            est_minutes=round(sum(s.est_minutes for s in slice_scenes), 2),
            scene_count=len(slice_scenes),
            first_chapter=first_chapter,
            last_chapter=last_chapter,
            boundary_reason=f"cut_at_scene_{cut}",
            cliffhanger_potential=last_scene.cliffhanger_potential,
            tension_peak=round(tension_peak, 3),
            tension_avg=round(tension_avg, 3),
            summary_preview=summary_preview,
        ))

        start = end

    return episodes


# ============================================================
# 内部:启发式选推荐方案(Phase 5 会替换为评分)
# ============================================================


def _heuristic_pick_recommended(
    plans: list[PerspectivePlan],
    target: float,
) -> Optional[str]:
    """选 episode 平均分钟数最接近 target 且不为空的方案。

    Phase 5 后替换为 aggregate_quality 排序。
    """
    candidates = [p for p in plans if p.episodes]
    if not candidates:
        return None

    def deviation(p: PerspectivePlan) -> float:
        avg = p.total_minutes / max(1, len(p.episodes))
        return abs(avg - target)

    best = min(candidates, key=deviation)
    return best.perspective


__all__ = [
    "PRESETS",
    "PERSPECTIVE_LABELS",
    "EpisodeWithMeta",
    "PerspectivePlan",
    "MultiPerspectivePlan",
    "MultiPerspectivePlanError",
    "plan_with_perspectives",
]
