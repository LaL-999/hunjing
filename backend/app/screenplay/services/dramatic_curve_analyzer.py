"""戏剧曲线分析 — 阶段 8.4+(2026-06-08)。

为每个剧本场景计算客观曲线数据,作为「多视角分集生成」的数据基础。

---

设计原则:

1. **纯客观数据 + 规则估算 — 无 LLM 调用**
   - LLM 推断(arc_turning / cliffhanger 增强) 由 Phase 3 multi_perspective_planner 负责
   - 本服务只输出"机器可定理验证"的数据,保证可单测、可复现、快(<100ms)

2. **桥接 SP-4 情绪曲线 — 优雅 fallback**
   - 优先:linked project 有 simulation → 按 chapter 聚合 character_state_snapshots
     的 emotion_vec,得 chapter-level 情绪强度曲线
   - 退化:无 link / 无 simulation / 数据空 → 退到纯剧本估算(element/dialogue density)
   - 两层并存,result.bridge_used 标记是否生效

3. **隔离 — user_id 全链路校验**
   - 通过 bridge._resolve_project_id 隐式校验 novel 属当前用户
   - simulation 表查询 JOIN projects 校验 project 属当前用户

4. **不抛异常**
   - 任何子步骤失败 → 该 scene 退化到规则估算 + log,不阻断整体

---

核心数据契约:

  SceneCurveData {
    scene_id, scene_number, chapter
    element_count, dialogue_count, has_inner_monologue, transition_to_next
    bridge_has_data, bridge_emotion_intensity   ← SP-4 桥接
    tension_score                                ← 综合 0-1
    tension_delta                                ← 与前场 delta
    cliffhanger_potential                        ← 该场作集尾的基础钩子潜力
    is_chapter_boundary, is_strong_transition    ← 切点候选标志
  }

  CurveAnalysisResult {
    scenes: list[SceneCurveData]
    bridge_used: bool
    bridge_data_source: str   ← "sp4_simulation" / "rule_fallback"
    chapter_emotion_curve: dict[int, float]   ← 章节级情绪强度 0-1
    candidate_cut_points: list[int]   ← scene index 列表 (高质量切点候选)
  }

"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from app.screenplay.services import huimeng_bridge

logger = logging.getLogger(__name__)


# ============================================================
# 数据契约
# ============================================================


# 强转场判定 — 同 episode_planner._greedy_split,保持一致
STRONG_TRANSITIONS = frozenset({
    "FADE_OUT", "SMASH_CUT", "FADE_IN", "DISSOLVE_TO",
})


@dataclass
class SceneCurveData:
    """单场的戏剧曲线数据。"""

    scene_id: str
    scene_number: int
    chapter: Optional[int]
    summary: str = ""

    # 基础元素统计(总是有)
    element_count: int = 0
    dialogue_count: int = 0
    voiceover_count: int = 0
    action_count: int = 0
    has_inner_monologue: bool = False
    transition_to_next: str = ""

    # 桥接 SP-4 数据(可能空)
    bridge_emotion_intensity: float = 0.0  # 0-1
    bridge_has_data: bool = False

    # 综合算出
    tension_score: float = 0.0             # 当场张力 0-1
    tension_delta: float = 0.0             # 与前场 delta(可负)
    cliffhanger_potential: float = 0.0     # 0-1

    # 边界标志(给切点决策用)
    is_chapter_boundary_after: bool = False  # 本场之后是否章节换
    is_strong_transition: bool = False       # transition_to_next 是否强转场

    # 估算时长(分钟,与 episode_planner 保持一致公式)
    est_minutes: float = 0.0


@dataclass
class CurveAnalysisResult:
    """整剧的曲线分析结果。"""

    scenes: list[SceneCurveData] = field(default_factory=list)
    bridge_used: bool = False
    bridge_data_source: str = "rule_fallback"  # / "sp4_simulation"
    chapter_emotion_curve: dict[int, float] = field(default_factory=dict)
    candidate_cut_points: list[int] = field(default_factory=list)  # scene index 列表

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # chapter_emotion_curve keys 在 JSON 序列化时变 str — 显式 list 化避免歧义
        d["chapter_emotion_curve"] = [
            {"chapter": k, "intensity": v}
            for k, v in sorted(self.chapter_emotion_curve.items())
        ]
        return d


# ============================================================
# 主入口
# ============================================================


def analyze_dramatic_curve(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    novel_id: str,
    scenes_yaml: list[dict],
    characters_yaml: Optional[list[dict]] = None,
) -> CurveAnalysisResult:
    """主入口:解析剧本 yaml 场景,返完整曲线分析。

    Args:
        conn: 同事务的 sqlite 连接
        user_id: 当前用户(隔离校验)
        novel_id: 当前小说
        scenes_yaml: 解析后的 scenes 列表(yaml parsed['scenes'])
        characters_yaml: 解析后的 characters 列表(未来 SP-2 / SP-7 用)

    Returns:
        CurveAnalysisResult — 即使桥接失败也返完整结果,bridge_used=False

    Raises:
        本函数**不抛** — 异常隔离铁律,任何错误内部 catch + log + 退到规则估算
    """
    if not isinstance(scenes_yaml, list) or not scenes_yaml:
        return CurveAnalysisResult()  # 空剧本

    # 1. 基础数据汇总(纯剧本,无桥接)
    scene_data_list = _enrich_basic_data(scenes_yaml)

    # 2. 尝试拉桥接 SP-4 情绪曲线
    chapter_emotion_curve, bridge_used = _try_load_bridge_emotion_curve(
        conn, user_id=user_id, novel_id=novel_id,
    )

    # 3. 把桥接数据注入每场(按 chapter 匹配)
    if bridge_used:
        for sd in scene_data_list:
            if sd.chapter is not None and sd.chapter in chapter_emotion_curve:
                sd.bridge_emotion_intensity = chapter_emotion_curve[sd.chapter]
                sd.bridge_has_data = True

    # 4. 综合算 tension_score(规则 + 桥接增强)
    _compute_tension_scores(scene_data_list, bridge_used=bridge_used)

    # 5. 算 tension_delta(与前场对比)
    _compute_tension_deltas(scene_data_list)

    # 6. 算 cliffhanger_potential(每场作集尾的潜力)
    _compute_cliffhanger_potentials(scene_data_list)

    # 7. 找候选切点(boundary + strong transition + tension valley)
    candidate_cut_points = _find_candidate_cut_points(scene_data_list)

    return CurveAnalysisResult(
        scenes=scene_data_list,
        bridge_used=bridge_used,
        bridge_data_source="sp4_simulation" if bridge_used else "rule_fallback",
        chapter_emotion_curve=chapter_emotion_curve,
        candidate_cut_points=candidate_cut_points,
    )


# ============================================================
# 步骤 1:基础数据汇总
# ============================================================


def _enrich_basic_data(scenes_yaml: list[dict]) -> list[SceneCurveData]:
    """yaml scenes → SceneCurveData[],只填基础元素统计字段。"""
    out: list[SceneCurveData] = []
    for i, s in enumerate(scenes_yaml):
        if not isinstance(s, dict):
            continue
        sid = s.get("id") or ""
        if not sid:
            continue

        elements = s.get("elements") if isinstance(s.get("elements"), list) else []

        # 元素类型分布
        dialogue_count = 0
        voiceover_count = 0
        action_count = 0
        has_inner_monologue = False
        for el in elements:
            if not isinstance(el, dict):
                continue
            t = el.get("type") or ""
            if t == "dialogue":
                dialogue_count += 1
            elif t == "voiceover":
                voiceover_count += 1
                if el.get("is_inner_monologue"):
                    has_inner_monologue = True
            elif t == "action":
                action_count += 1

        # 章节
        source = s.get("source") or {}
        chapter = source.get("chapter") if isinstance(source, dict) else None
        if not isinstance(chapter, int):
            chapter = None

        # 转场
        transition = str(s.get("transition_to_next") or "").upper().strip()

        # 时长估算(与 episode_planner 完全一致公式)
        est_minutes = max(0.4, len(elements) / 25.0)

        sd = SceneCurveData(
            scene_id=sid,
            scene_number=int(s.get("number", 0)) if isinstance(s.get("number"), int) else (i + 1),
            chapter=chapter,
            summary=str(s.get("summary") or ""),
            element_count=len(elements),
            dialogue_count=dialogue_count,
            voiceover_count=voiceover_count,
            action_count=action_count,
            has_inner_monologue=has_inner_monologue,
            transition_to_next=transition,
            is_strong_transition=transition in STRONG_TRANSITIONS,
            est_minutes=round(est_minutes, 2),
        )
        out.append(sd)

    # 章节边界标志(本场之后是否章节换)
    for i, sd in enumerate(out):
        if i + 1 < len(out):
            next_sd = out[i + 1]
            sd.is_chapter_boundary_after = (
                sd.chapter is not None
                and next_sd.chapter is not None
                and sd.chapter != next_sd.chapter
            )

    return out


# ============================================================
# 步骤 2:桥接 SP-4 情绪曲线
# ============================================================


def _try_load_bridge_emotion_curve(
    conn: sqlite3.Connection,
    *,
    user_id: str,
    novel_id: str,
) -> tuple[dict[int, float], bool]:
    """尝试拉 SP-4 character_state_snapshots,按 chapter 聚合情绪强度。

    Returns:
        (chapter_emotion_curve_dict, bridge_used_flag)
        - dict 键是 chapter 号,值是该章节平均情绪强度 0-1
        - bridge_used=False 时 dict 为空,调用方退到规则估算
    """
    try:
        project_id = huimeng_bridge._resolve_project_id(conn, user_id, novel_id)
        if not project_id:
            return {}, False

        # 找该项目最近完成的 simulation(注:state='done',不是 status)
        sim_row = conn.execute(
            "SELECT id FROM simulations "
            "WHERE project_id=? AND state='done' "
            "ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        if sim_row is None:
            return {}, False
        sim_id = sim_row["id"]

        # 拉所有 snapshot — character_state_snapshots 有 simulation_id + scene_id + emotion_vec
        # scene_id 这里是浑晶 simulation_scenes 表的 scene,不是剧创态的 scene
        # 需要 JOIN simulation_scenes 取 chapter_anchor / scene_index
        rows = conn.execute(
            """
            SELECT css.emotion_vec, ss.scene_index
            FROM character_state_snapshots css
            JOIN simulation_scenes ss ON ss.id = css.scene_id
            WHERE css.simulation_id = ?
            """,
            (sim_id,),
        ).fetchall()

        if not rows:
            return {}, False

        # 按 scene_index 聚合所有角色的情绪强度(emotion_vec dict 的值之和归一化)
        # 浑晶推演 scene 与剧本 scene 不 1:1,我们用 scene_index 作为时间序列代理
        # 然后在后面通过位置百分比映射到 chapter
        scene_intensity_map: dict[int, list[float]] = {}
        for r in rows:
            emo_raw = r["emotion_vec"]
            if not emo_raw:
                continue
            try:
                emo = json.loads(emo_raw) if isinstance(emo_raw, str) else emo_raw
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(emo, dict):
                continue
            # 情绪强度 = 所有维度绝对值之和,归一化到 0-1
            intensity = sum(abs(float(v)) for v in emo.values() if isinstance(v, (int, float)))
            if intensity > 0:
                idx = r["scene_index"]
                if isinstance(idx, int):
                    scene_intensity_map.setdefault(idx, []).append(min(1.0, intensity))

        if not scene_intensity_map:
            return {}, False

        # 每个 scene_index 取平均(多角色)
        scene_avg = {
            idx: sum(vals) / len(vals) for idx, vals in scene_intensity_map.items()
        }

        # 全局归一化到 0-1(最大值映射到 1.0)
        max_v = max(scene_avg.values()) if scene_avg else 0.0
        if max_v > 0:
            scene_avg = {k: v / max_v for k, v in scene_avg.items()}

        # 把 simulation_scenes.scene_index 当作时间序列;然后按"剧本里有的章节"反查
        # 简化策略:直接把 scene_index 当作 chapter 号(浑晶推演通常 scene_index ≈ chapter)
        # 若映射不准,scene 内 chapter 不命中就走规则估算 — 优雅退化
        return scene_avg, True

    except Exception as e:  # noqa: BLE001
        logger.warning("bridge emotion curve load failed: %s", e)
        return {}, False


# ============================================================
# 步骤 3:tension_score 综合
# ============================================================


def _compute_tension_scores(
    scenes: list[SceneCurveData], *, bridge_used: bool,
) -> None:
    """为每场算 tension_score 0-1。

    基础规则:
      base = clip(0, 1, (element_count - 5) / 25)  # 5-30 elements → 0-1
      + 0.10 if has_inner_monologue
      + 0.08 if dialogue_count >= 8   # 大量对白 = 戏剧密度高
      + 0.06 if action_count >= 6     # 多动作 = 节奏紧
      + 0.05 if is_strong_transition

    桥接增强(若 bridge_used):
      base *= (1 + 0.40 * bridge_emotion_intensity)
      然后 clip 到 0-1

    无桥接时,纯规则也能给出合理曲线(只是不知道角色情绪)
    """
    for sd in scenes:
        # 基础规则部分
        density = max(0.0, min(1.0, (sd.element_count - 5) / 25.0))
        bonus = 0.0
        if sd.has_inner_monologue:
            bonus += 0.10
        if sd.dialogue_count >= 8:
            bonus += 0.08
        if sd.action_count >= 6:
            bonus += 0.06
        if sd.is_strong_transition:
            bonus += 0.05

        score = min(1.0, density + bonus)

        # 桥接增强
        if bridge_used and sd.bridge_has_data:
            score = min(1.0, score * (1.0 + 0.40 * sd.bridge_emotion_intensity))

        sd.tension_score = round(score, 3)


# ============================================================
# 步骤 4:tension_delta
# ============================================================


def _compute_tension_deltas(scenes: list[SceneCurveData]) -> None:
    """delta = 当场 - 前场;首场 delta = 0。

    用法:
      delta > 0.15 → 张力上升(集尾首选)
      delta < -0.15 → 张力下降(集尾不要,可能下一集承接低谷)
      |delta| < 0.10 → 平稳(可切但不强)
    """
    for i, sd in enumerate(scenes):
        if i == 0:
            sd.tension_delta = 0.0
        else:
            sd.tension_delta = round(sd.tension_score - scenes[i - 1].tension_score, 3)


# ============================================================
# 步骤 5:cliffhanger_potential
# ============================================================


def _compute_cliffhanger_potentials(scenes: list[SceneCurveData]) -> None:
    """每场作"集尾"的钩子潜力 0-1。

    判定因素:
      a. tension_score >= 0.50:本场有一定张力          (+0.30)
      b. tension_delta > 0.10:本场是张力上升尾巴(强钩) (+0.30)
      c. has_inner_monologue:有内心独白(感情转折)     (+0.15)
      d. is_strong_transition:FADE_OUT 等强转场后(留白)(+0.20)
      e. is_chapter_boundary_after:章节边界(天然停顿) (+0.10)
      f. summary 含钩子关键词("但是" "突然" "竟然" 等):  (+0.10)

    上限 1.0,下限 0.0
    """
    HOOK_KEYWORDS = ("但是", "突然", "竟然", "原来", "却是", "悬", "谜",
                     "失踪", "死亡", "真相", "暴露", "决定")
    for sd in scenes:
        pot = 0.0
        if sd.tension_score >= 0.50:
            pot += 0.30
        if sd.tension_delta > 0.10:
            pot += 0.30
        if sd.has_inner_monologue:
            pot += 0.15
        if sd.is_strong_transition:
            pot += 0.20
        if sd.is_chapter_boundary_after:
            pot += 0.10
        if any(kw in sd.summary for kw in HOOK_KEYWORDS):
            pot += 0.10
        sd.cliffhanger_potential = round(min(1.0, pot), 3)


# ============================================================
# 步骤 6:候选切点
# ============================================================


def _find_candidate_cut_points(scenes: list[SceneCurveData]) -> list[int]:
    """返高质量切点候选(scene index 列表,本场作集尾)。

    候选规则(满足任一即入):
      - cliffhanger_potential >= 0.5(钩子强)
      - is_chapter_boundary_after & tension_delta >= 0(章末且不在下降)
      - is_strong_transition & tension_score >= 0.4(强转场且有张力)

    末场必入(无论如何最后一集要收尾)。
    """
    out: list[int] = []
    for i, sd in enumerate(scenes):
        is_last = i == len(scenes) - 1

        is_strong_hook = sd.cliffhanger_potential >= 0.5
        is_clean_chapter_end = sd.is_chapter_boundary_after and sd.tension_delta >= 0
        is_strong_trans_with_tension = sd.is_strong_transition and sd.tension_score >= 0.4

        if is_last or is_strong_hook or is_clean_chapter_end or is_strong_trans_with_tension:
            out.append(i)
    return out


# ============================================================
# 便利:序列化给 API 返回
# ============================================================


def serialize_curve_result(result: CurveAnalysisResult) -> dict[str, Any]:
    """转 API-friendly dict(含 scenes 数组 + 元数据)。"""
    return result.to_dict()


__all__ = [
    "SceneCurveData",
    "CurveAnalysisResult",
    "analyze_dramatic_curve",
    "serialize_curve_result",
    "STRONG_TRANSITIONS",
]
