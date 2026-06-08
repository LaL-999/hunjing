"""Sprint 6.A2 MP(2026-05-21)— 张力规划员服务 Scene Tension Planner。

每幕 narrator 合稿**之前**调用,LLM 根据"全篇位置 + 故事现状 + (可选)大纲意图"
预测本幕应有的:
  - tension_percent: 0-100 张力强度
  - pacing_tempo:   fast / normal / slow 节奏速度
  - reasoning:      简短理由(给开发者 debug + 给前端可视化提示用)

主循环消费:
  - outline-first 模式:planner 输出回写 outline_scenes.tension_percent + pacing_tempo
  - 灵魂续写模式:planner 输出仅注入本幕 narrator system_prompt(transient)
  - narrator system_prompt 顶部 prepend "本幕目标张力 X% / 节奏 Y" 指令

设计原则(对齐 consistency_checker / plot_tracker 范本):
  - LLM 失败 → 默认中位值(50, normal),不阻塞主流程
  - 单次调用,< 200 输入 token / < 100 输出 token,成本可忽略
  - 不修改任何下游数据,只输出建议
  - 灵魂续写模式无 outline_scene → 不持久化,仅 transient inject

API:
  - plan_scene_tension(scene_index, total_scenes, global_arc, scene_purpose,
                       scene_summary, narrative_so_far_tail, recent_tension_curve)
    → (PlannerDecision, usage)
  - PlannerDecision.to_narrator_hint() 生成给 narrator system_prompt 的提示文本
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from app.services.llm_client import call_llm_json

logger = logging.getLogger(__name__)

PacingTempo = Literal["fast", "normal", "slow"]

_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


@dataclass
class PlannerDecision:
    tension_percent: int  # 0-100
    pacing_tempo: PacingTempo
    reasoning: str  # < 80 字

    def to_narrator_hint(self) -> str:
        """生成给 narrator system_prompt 的"张力指令"段。"""
        tempo_label = {
            "fast": "快(句短促紧凑,推进感强,密集动作 / 心理 / 感官冲击)",
            "normal": "中(自然叙事节奏,对话与动作交错,情绪起伏适度)",
            "slow": "慢(长句铺陈细节,环境描写,角色内心独白,慢镜头铺垫)",
        }[self.pacing_tempo]
        return (
            f"【本幕张力规划】\n"
            f"  · 目标张力:{self.tension_percent}% / 100\n"
            f"  · 节奏速度:{tempo_label}\n"
            f"  · 规划理由:{self.reasoning}\n"
            f"  ⚠ 笔法须匹配:高张力(≥70%)句短促紧凑 / 低张力(≤30%)长句铺陈细节"
        )


# 兜底默认值(LLM 失败时使用)
_FALLBACK_DECISION = PlannerDecision(
    tension_percent=50,
    pacing_tempo="normal",
    reasoning="planner LLM 失败,取保守中位",
)


def plan_scene_tension(
    *,
    scene_index: int,
    total_scenes: int,
    global_arc: str = "",
    scene_purpose: str = "",
    scene_summary: str = "",
    narrative_so_far_tail: str = "",
    recent_tension_curve: Optional[list[int]] = None,
) -> tuple[PlannerDecision, dict]:
    """规划本幕张力 + 节奏。返回 (PlannerDecision, llm_usage_dict)。

    Args:
      scene_index: 0-based 当前幕索引
      total_scenes: 全篇总幕数(rounds_planned)
      global_arc: outline 全局起承转合(灵魂续写为空)
      scene_purpose: outline 本幕作用(灵魂续写为空)
      scene_summary: outline 本幕概要(灵魂续写为空)
      narrative_so_far_tail: 前 1-2 幕叙事文本截断(< 1500 字)
      recent_tension_curve: 上 N 幕的 tension_percent 列表(< 5 个)

    Returns:
      (PlannerDecision, usage_dict)
      LLM 失败 / 输出无效 → 返 _FALLBACK_DECISION + 零 token usage
    """
    # 输入截断防止 token 爆炸
    narrative_tail = (narrative_so_far_tail or "")[-1500:]
    curve = (recent_tension_curve or [])[-5:]

    user_input = {
        "scene_index": scene_index,
        "total_scenes": total_scenes,
        "global_arc": global_arc or "",
        "scene_purpose": scene_purpose or "",
        "scene_summary": scene_summary or "",
        "narrative_so_far_tail": narrative_tail,
        "recent_tension_curve": curve,
    }

    try:
        system_prompt = _load_prompt("m_planner.md")
    except FileNotFoundError:
        logger.warning("m_planner.md 缺失 → 用 fallback decision")
        return _FALLBACK_DECISION, {"input_tokens": 0, "output_tokens": 0}

    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input,
            max_tokens=200, temperature=0.4,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"scene_planner LLM failed scene={scene_index}/{total_scenes}: {e}"
        )
        return _FALLBACK_DECISION, {"input_tokens": 0, "output_tokens": 0}

    if not isinstance(parsed, dict):
        return _FALLBACK_DECISION, usage

    # 解析 + 校验
    try:
        tp_raw = parsed.get("tension_percent")
        if tp_raw is None:
            return _FALLBACK_DECISION, usage
        tension_percent = int(tp_raw)
        if tension_percent < 0:
            tension_percent = 0
        elif tension_percent > 100:
            tension_percent = 100
    except (ValueError, TypeError):
        return _FALLBACK_DECISION, usage

    pacing_raw = str(parsed.get("pacing_tempo") or "").strip().lower()
    if pacing_raw not in ("fast", "normal", "slow"):
        pacing_raw = "normal"

    reasoning = str(parsed.get("reasoning") or "").strip()[:200]
    if not reasoning:
        reasoning = "(未提供理由)"

    return PlannerDecision(
        tension_percent=tension_percent,
        pacing_tempo=pacing_raw,  # type: ignore[arg-type]
        reasoning=reasoning,
    ), usage


def write_back_to_outline_scene(
    conn,
    outline_scene_id: str,
    decision: PlannerDecision,
) -> None:
    """outline-first 模式:planner 跑完把结果写回 outline_scene 行(持久化)。

    灵魂续写模式不会调此函数(无 outline_scene 行可写)。
    幂等:重复跑会覆盖。
    """
    from app.services.project_service import iso_now
    conn.execute(
        """UPDATE outline_scenes
           SET tension_percent = ?, pacing_tempo = ?, updated_at = ?
           WHERE id = ?""",
        (
            decision.tension_percent,
            decision.pacing_tempo,
            iso_now(),
            outline_scene_id,
        ),
    )
    conn.commit()
