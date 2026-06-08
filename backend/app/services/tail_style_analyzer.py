"""Tail Style Analyzer — Sprint 6.A2 TS(2026-05-21)。

末尾态续写时(sim.original_tail_excerpt 非空),首次进入主循环调用一次,
提取 5 维"笔法特征"缓存到 simulations.tail_style_features_json,
后续每幕 narrator 产物会与这些特征做 diff 打分(tail_style_checker)。

5 维拆分(客观 + LLM):
  - sentence_length_avg     平均句长(客观:正则切句)
  - short_sentence_ratio    短句(≤15 字)占比(客观)
  - long_sentence_ratio     长句(>40 字)占比(客观)
  - vocab_set               核心词汇 5-10 个(LLM 主观)
  - perspective             视角(LLM 主观)
  - tone_baseline           情感基调(LLM 主观,≤10 字)

设计:
  - 客观维度直接 re 切句,无需 NLP 库
  - LLM 只调一次,~150 输入 / ~80 输出 token,成本可忽略
  - LLM 失败 → fallback 空 vocab + third_limited + "未知" 基调(不阻塞主流程)
  - 缓存策略:首次写,之后不重算(末尾段本身不变)
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal, Optional

from app.services.llm_client import call_llm_json

logger = logging.getLogger(__name__)

Perspective = Literal["first", "second", "third_omniscient", "third_limited"]

_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


@dataclass
class TailStyleFeatures:
    """原作末段提取的 5 维笔法特征。"""
    # 客观维度
    sentence_length_avg: float
    short_sentence_ratio: float   # 短句占比 [0.0, 1.0]
    long_sentence_ratio: float    # 长句占比 [0.0, 1.0]
    # LLM 主观维度
    vocab_set: list[str] = field(default_factory=list)
    perspective: Perspective = "third_limited"
    tone_baseline: str = "未知"

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "TailStyleFeatures":
        """从 simulations.tail_style_features_json 还原。"""
        try:
            d = json.loads(raw or "{}")
            if not isinstance(d, dict):
                d = {}
        except json.JSONDecodeError:
            d = {}
        return cls(
            sentence_length_avg=float(d.get("sentence_length_avg") or 0.0),
            short_sentence_ratio=float(d.get("short_sentence_ratio") or 0.0),
            long_sentence_ratio=float(d.get("long_sentence_ratio") or 0.0),
            vocab_set=[str(x) for x in (d.get("vocab_set") or []) if x],
            perspective=_safe_perspective(d.get("perspective")),
            tone_baseline=str(d.get("tone_baseline") or "未知")[:20],
        )


def _safe_perspective(raw) -> Perspective:
    valid = ("first", "second", "third_omniscient", "third_limited")
    if raw in valid:
        return raw  # type: ignore[return-value]
    return "third_limited"


# 客观切句:按中文句末标点切(。!?…;)— 包含英文标点兼容
_SENTENCE_SPLIT_RE = re.compile(r"[。!?…;.!?\n]+")


def _split_sentences(text: str) -> list[str]:
    """切句 — 按中英文句末标点。空段过滤。"""
    raw = _SENTENCE_SPLIT_RE.split(text or "")
    return [s.strip() for s in raw if s and s.strip()]


def _compute_objective_stats(text: str) -> dict:
    """计算客观维度:平均句长 + 短/长句占比。"""
    sentences = _split_sentences(text)
    if not sentences:
        return {
            "sentence_length_avg": 0.0,
            "short_sentence_ratio": 0.0,
            "long_sentence_ratio": 0.0,
        }
    lengths = [len(s) for s in sentences]
    avg = sum(lengths) / len(lengths)
    short_count = sum(1 for l in lengths if l <= 15)
    long_count = sum(1 for l in lengths if l > 40)
    return {
        "sentence_length_avg": round(avg, 2),
        "short_sentence_ratio": round(short_count / len(lengths), 3),
        "long_sentence_ratio": round(long_count / len(lengths), 3),
    }


# 兜底:LLM 失败时的默认 LLM 维度
_FALLBACK_LLM_FIELDS = {
    "vocab_set": [],
    "perspective": "third_limited",
    "tone_baseline": "未知",
}


def analyze_tail(text: str) -> tuple[TailStyleFeatures, dict]:
    """分析原作末段,返回 (TailStyleFeatures, llm_usage_dict)。

    Args:
      text: 原作末段文本(通常 1500-2500 字,可短)

    Returns:
      (features, usage) — LLM 失败时 LLM 维度走 fallback,客观维度仍有效
    """
    text = (text or "").strip()
    objective = _compute_objective_stats(text)

    # 文本过短(< 100 字)→ 跳过 LLM,直接 fallback
    if len(text) < 100:
        logger.info(f"tail_style 文本过短({len(text)} 字),跳过 LLM 分析")
        return TailStyleFeatures(
            sentence_length_avg=objective["sentence_length_avg"],
            short_sentence_ratio=objective["short_sentence_ratio"],
            long_sentence_ratio=objective["long_sentence_ratio"],
            **_FALLBACK_LLM_FIELDS,
        ), {"input_tokens": 0, "output_tokens": 0}

    # LLM 主观维度
    try:
        system_prompt = _load_prompt("m_tail_style_analyzer.md")
    except FileNotFoundError:
        logger.warning("m_tail_style_analyzer.md 缺失 → 用 fallback")
        return TailStyleFeatures(
            sentence_length_avg=objective["sentence_length_avg"],
            short_sentence_ratio=objective["short_sentence_ratio"],
            long_sentence_ratio=objective["long_sentence_ratio"],
            **_FALLBACK_LLM_FIELDS,
        ), {"input_tokens": 0, "output_tokens": 0}

    try:
        # 截断到 2500 字防 token 过多
        truncated = text[:2500]
        parsed, usage = call_llm_json(
            system_prompt, {"tail_excerpt": truncated},
            max_tokens=300, temperature=0.3,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"tail_style_analyzer LLM failed: {e}")
        return TailStyleFeatures(
            sentence_length_avg=objective["sentence_length_avg"],
            short_sentence_ratio=objective["short_sentence_ratio"],
            long_sentence_ratio=objective["long_sentence_ratio"],
            **_FALLBACK_LLM_FIELDS,
        ), {"input_tokens": 0, "output_tokens": 0}

    if not isinstance(parsed, dict):
        parsed = {}

    vocab = parsed.get("vocab_set") or []
    if not isinstance(vocab, list):
        vocab = []
    vocab = [str(v).strip()[:20] for v in vocab if v and isinstance(v, str)][:10]

    return TailStyleFeatures(
        sentence_length_avg=objective["sentence_length_avg"],
        short_sentence_ratio=objective["short_sentence_ratio"],
        long_sentence_ratio=objective["long_sentence_ratio"],
        vocab_set=vocab,
        perspective=_safe_perspective(parsed.get("perspective")),
        tone_baseline=str(parsed.get("tone_baseline") or "未知").strip()[:20],
    ), usage


def write_back_features(
    conn: sqlite3.Connection,
    sim_id: str,
    features: TailStyleFeatures,
) -> None:
    """把分析结果缓存到 simulations.tail_style_features_json + 时间戳。

    幂等:重复调用会覆盖。
    """
    from app.services.project_service import iso_now
    conn.execute(
        """UPDATE simulations
           SET tail_style_features_json = ?, tail_style_analyzed_at = ?
           WHERE id = ?""",
        (features.to_json(), iso_now(), sim_id),
    )
    conn.commit()


def get_cached_features(
    conn: sqlite3.Connection,
    sim_id: str,
) -> Optional[TailStyleFeatures]:
    """读已缓存的特征。返 None 表示尚未分析。"""
    from app.db import fetch_one
    row = fetch_one(
        conn,
        "SELECT tail_style_features_json, tail_style_analyzed_at"
        " FROM simulations WHERE id=?",
        (sim_id,),
    )
    if row is None:
        return None
    raw = row["tail_style_features_json"]
    if not raw:
        return None
    return TailStyleFeatures.from_json(raw)
