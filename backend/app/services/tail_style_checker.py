"""Tail Style Checker — Sprint 6.A2 TS(2026-05-21)。

每幕 narrator multi-sample voting 时,每个候选过此 checker 拿 0-100 综合分,
作为 voting tiebreaker(critical → warning → -style_score → char_deviation)。

5 维评分:
  - 客观(基于原作特征 vs 新段重算的统计)
      1) sentence_length_score   平均句长偏离度(0-100,偏离越小越高)
      2) short_long_ratio_score  短/长句占比偏离(0-100)
  - LLM 主观(调一次 m_tail_style_checker.md)
      3) vocab_score            核心词汇复用度(0-100)
      4) perspective_score      视角一致性(0-100)
      5) tone_score             情感基调一致(0-100)

综合分:加权平均(可调权重,默认均权 1:1:1:1:1)

设计:
  - 主观 3 维 LLM 一次输出
  - 客观 2 维直接对比统计(无 LLM)
  - LLM 失败 → 主观 3 维 fallback 50 分(中位,不偏向任何样本)
  - 综合分用于 voting tiebreaker,**不触发重生**(用户决策)
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from pathlib import Path

from app.services.llm_client import call_llm_json
from app.services.tail_style_analyzer import (
    TailStyleFeatures,
    _compute_objective_stats,
)

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


@dataclass
class StyleScore:
    """5 维评分 + 综合分。"""
    sentence_length_score: int   # 客观,0-100
    short_long_ratio_score: int  # 客观,0-100
    vocab_score: int             # LLM 主观,0-100
    perspective_score: int       # LLM 主观,0-100
    tone_score: int              # LLM 主观,0-100
    composite_score: int         # 5 维加权平均,0-100
    reasoning: str = ""          # LLM 给出的简短理由

    def to_dict(self) -> dict:
        return asdict(self)

    def to_summary(self) -> str:
        return (
            f"句长 {self.sentence_length_score} / "
            f"短长比 {self.short_long_ratio_score} / "
            f"词汇 {self.vocab_score} / "
            f"视角 {self.perspective_score} / "
            f"基调 {self.tone_score} → "
            f"综合 {self.composite_score}/100"
        )


_FALLBACK_LLM_SCORES = {
    "vocab_score": 50,
    "perspective_score": 50,
    "tone_score": 50,
    "reasoning": "(LLM 失败,主观分取中位)",
}


def _score_avg_deviation(
    original: float, generated: float, tolerance_ratio: float = 0.3,
) -> int:
    """客观维度评分通用:偏离 tolerance_ratio 以内得满分,2× tolerance 0 分,中间线性。

    Args:
      original: 原作统计值
      generated: 新段统计值
      tolerance_ratio: 偏离阈值占原作的比例(默认 30%)

    Returns:
      0-100 分(线性映射)
    """
    if original <= 0:
        # 原作没数据 → 不评分,中位 50
        return 50
    diff_ratio = abs(generated - original) / original
    # 0-30% 偏离 → 100 分;30-60% → 100→0 线性;>60% → 0 分
    if diff_ratio <= tolerance_ratio:
        return 100
    if diff_ratio >= 2 * tolerance_ratio:
        return 0
    # 线性映射:tolerance → 100,2× tolerance → 0
    span = 2 * tolerance_ratio - tolerance_ratio
    score = 100 * (1 - (diff_ratio - tolerance_ratio) / span)
    return max(0, min(100, int(round(score))))


def _score_ratio_diff(original: float, generated: float) -> int:
    """客观维度评分(占比型)— 绝对差 0.1 内得满分,差 0.4+ 得 0 分。"""
    diff = abs(generated - original)
    if diff <= 0.1:
        return 100
    if diff >= 0.4:
        return 0
    return max(0, min(100, int(round(100 * (1 - (diff - 0.1) / 0.3)))))


def check_style_alignment(
    features: TailStyleFeatures,
    generated_segment: str,
) -> tuple[StyleScore, dict]:
    """评 5 维 + 综合。返 (StyleScore, llm_usage_dict)。

    LLM 失败 → 主观 3 维全 50 分,客观 2 维仍有效。
    """
    segment = (generated_segment or "").strip()
    if len(segment) < 50:
        # 过短无法评 → 中位
        return StyleScore(
            sentence_length_score=50,
            short_long_ratio_score=50,
            vocab_score=50,
            perspective_score=50,
            tone_score=50,
            composite_score=50,
            reasoning="段落过短无法评分",
        ), {"input_tokens": 0, "output_tokens": 0}

    # 客观维度
    gen_stats = _compute_objective_stats(segment)
    sentence_length_score = _score_avg_deviation(
        features.sentence_length_avg,
        gen_stats["sentence_length_avg"],
        tolerance_ratio=0.3,
    )
    # 短/长句占比偏离(短 + 长 各算一次,取平均)
    short_score = _score_ratio_diff(
        features.short_sentence_ratio, gen_stats["short_sentence_ratio"],
    )
    long_score = _score_ratio_diff(
        features.long_sentence_ratio, gen_stats["long_sentence_ratio"],
    )
    short_long_ratio_score = (short_score + long_score) // 2

    # LLM 主观维度
    llm_scores = _FALLBACK_LLM_SCORES.copy()
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        system_prompt = _load_prompt("m_tail_style_checker.md")
        user_input = {
            "original_features": {
                "vocab_set": features.vocab_set,
                "perspective": features.perspective,
                "tone_baseline": features.tone_baseline,
            },
            "generated_segment": segment[:1500],
        }
        parsed, usage = call_llm_json(
            system_prompt, user_input,
            max_tokens=250, temperature=0.2,
        )
        if isinstance(parsed, dict):
            llm_scores["vocab_score"] = _clamp_int(
                parsed.get("vocab_score"), 0, 100, fallback=50,
            )
            llm_scores["perspective_score"] = _clamp_int(
                parsed.get("perspective_score"), 0, 100, fallback=50,
            )
            llm_scores["tone_score"] = _clamp_int(
                parsed.get("tone_score"), 0, 100, fallback=50,
            )
            llm_scores["reasoning"] = str(
                parsed.get("reasoning") or ""
            ).strip()[:200]
    except FileNotFoundError:
        logger.warning("m_tail_style_checker.md 缺失 → 主观分走 fallback")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"tail_style_checker LLM failed: {e}")

    # 综合分:5 维均权平均
    composite = (
        sentence_length_score
        + short_long_ratio_score
        + llm_scores["vocab_score"]
        + llm_scores["perspective_score"]
        + llm_scores["tone_score"]
    ) // 5

    return StyleScore(
        sentence_length_score=sentence_length_score,
        short_long_ratio_score=short_long_ratio_score,
        vocab_score=llm_scores["vocab_score"],
        perspective_score=llm_scores["perspective_score"],
        tone_score=llm_scores["tone_score"],
        composite_score=composite,
        reasoning=llm_scores["reasoning"],
    ), usage


def _clamp_int(raw, lo: int, hi: int, fallback: int) -> int:
    """安全转 int 并 clamp 到 [lo, hi]。无效值用 fallback。"""
    try:
        v = int(raw)
    except (TypeError, ValueError):
        return fallback
    return max(lo, min(hi, v))
