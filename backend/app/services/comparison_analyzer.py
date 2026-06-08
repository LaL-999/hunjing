"""SP-9 enhancement(2026-05-29 末)— 反事实分支对比 LLM 因果分析.

输入:SP-9 compare endpoint 已组装好的 data(sim_a/sim_b metadata + cf_diff + scene_alignment)
输出:LLM 写的结构化分析报告

核心价值:把 SP-9 从"原始数据并排展示"升级为"AI 分析助手".
用户看到 AI 给出的因果链:"反事实变量 X(改了角色性格)在分支 A 的第 N 幕导致了 Y(narrative 走向);
分支 B 没应用此变量,所以同 N 幕走向 Z;两者整体差异 = ..."

设计:
- 与 SP-9 compare endpoint 解耦 — 输入 compare data dict,返分析报告 dict
- 失败兜底:LLM 抛 / 返非法 → 返程序级简短分析(基于场景数 + cf 数)
- token 控制:cf_diff + scene_alignment 可能很大,要截断
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Optional

from app.services.llm_client import call_llm_json, LlmCallFailed, LlmJsonParseFailed

logger = logging.getLogger(__name__)


# 每个 scene 的 narrative_segment 截断长度(防 prompt 爆)
_SCENE_TEXT_MAX = 800
# scene 对齐最多取多少(头中尾 + 关键转折)
_SCENE_ALIGNMENT_MAX = 25


def _load_prompt() -> str:
    prompt_path = (
        Path(__file__).parent.parent.parent.parent
        / "prompts" / "comparison_analyzer.md"
    )
    return prompt_path.read_text(encoding="utf-8")


def _trim_scene_alignment(alignment: list[dict]) -> list[dict]:
    """trim scene_alignment 给 LLM 用 — 控制 prompt 长度.

    策略:
      - 保留所有 only_a / only_b 幕(独有 = 关键差异点)
      - both 且 similar=False(明显差异)优先保留
      - both 且 similar=True(几乎相同)只保留少量样本
    """
    if len(alignment) <= _SCENE_ALIGNMENT_MAX:
        # 整批保留,但每幕 narrative 截断
        return [_trim_scene_item(item) for item in alignment]

    # 按重要度分类
    only_sides = [i for i in alignment if i["diff_kind"] != "both"]
    both_differ = [i for i in alignment if i["diff_kind"] == "both" and not i.get("similar")]
    both_similar = [i for i in alignment if i["diff_kind"] == "both" and i.get("similar")]

    # 优先选 only + differ(关键差异)
    picked = (only_sides + both_differ)[:_SCENE_ALIGNMENT_MAX]
    # 还有余额 → 加 similar 样本(让 LLM 知道整体哪部分相同)
    remaining = _SCENE_ALIGNMENT_MAX - len(picked)
    if remaining > 0 and both_similar:
        # 取头尾各取一些
        sample_n = min(remaining, len(both_similar))
        step = max(1, len(both_similar) // sample_n)
        picked.extend(both_similar[::step][:sample_n])

    # 按 scene_index 重新排序
    picked.sort(key=lambda x: x["scene_index"])
    return [_trim_scene_item(item) for item in picked]


def _trim_scene_item(item: dict) -> dict:
    """单个 scene 对齐项 — narrative_segment 截断."""
    out = {
        "scene_index": item["scene_index"],
        "diff_kind": item["diff_kind"],
        "similar": item.get("similar", False),
    }
    for side in ("a", "b"):
        s = item.get(side)
        if s is None:
            out[side] = None
        else:
            seg = s.get("narrative_segment") or ""
            out[side] = {
                "scene_name": s.get("scene_name", ""),
                "time_anchor": s.get("time_anchor", ""),
                "characters_present_count": len(s.get("characters_present", [])),
                "narrative_excerpt": (
                    seg[:_SCENE_TEXT_MAX] + ("…" if len(seg) > _SCENE_TEXT_MAX else "")
                ),
            }
    return out


def _trim_cf_diff(cf_diff: dict) -> dict:
    """cf_diff 简化给 LLM:每条 cf 保留 id / target_name / field / old / new / user_intent."""
    def _simplify(items: list[dict]) -> list[dict]:
        out: list[dict] = []
        for cf in items:
            out.append({
                "id": cf.get("id", ""),
                "target_type": cf.get("target_type", ""),
                "target_name": cf.get("target_name", ""),
                "field": cf.get("field", ""),
                "old_value": (cf.get("old_value") or "")[:200],
                "new_value": (cf.get("new_value") or "")[:200],
                "user_intent": (cf.get("user_intent") or "")[:200],
            })
        return out
    return {
        "common": _simplify(cf_diff.get("common") or []),
        "only_in_a": _simplify(cf_diff.get("only_in_a") or []),
        "only_in_b": _simplify(cf_diff.get("only_in_b") or []),
    }


def _build_program_fallback(compare_data: dict) -> dict:
    """LLM 失败时,返简短程序级分析(基于 stats + cf 数,不调 LLM)."""
    stats = compare_data.get("stats") or {}
    cf_diff = compare_data.get("counterfactual_diff") or {}
    only_a = len(cf_diff.get("only_in_a") or [])
    only_b = len(cf_diff.get("only_in_b") or [])
    common = len(cf_diff.get("common") or [])
    similar = stats.get("similar_scene_count", 0)
    total_common = stats.get("common_scene_count", 0)

    if total_common > 0:
        similar_ratio = similar / total_common
    else:
        similar_ratio = 0.0

    if only_a == 0 and only_b == 0:
        summary = f"两分支应用了完全相同的 {common} 条反事实变量,但 narrative 在 {total_common - similar} 幕走向不同 — 同样的初始假设下,LLM 随机性带来了表层差异."
    else:
        summary = f"分支 A 应用了 {only_a + common} 条反事实(其中 {only_a} 条独有),分支 B 应用了 {only_b + common} 条(其中 {only_b} 条独有);两分支共有 {total_common} 幕重叠,其中 {similar} 幕高度相似({int(similar_ratio * 100)}%)."

    return {
        "summary": summary,
        "verdict_a": "见原始 narrative",
        "verdict_b": "见原始 narrative",
        "key_turning_points": [],
        "cf_impact_chains": [],
        "reasoning": "AI 分析暂不可用,以上为程序级简要统计.重新点击 AI 分析或手动审阅原始数据.",
        "is_fallback": True,
    }


def _validate_llm_result(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"LLM result not a dict: {type(raw)}")

    def _str(v: Any, max_len: int = 600) -> str:
        if not isinstance(v, str):
            return ""
        return v.strip()[:max_len]

    out: dict[str, Any] = {
        "summary": _str(raw.get("summary"), 500),
        "verdict_a": _str(raw.get("verdict_a"), 400),
        "verdict_b": _str(raw.get("verdict_b"), 400),
        "reasoning": _str(raw.get("reasoning"), 600),
        "is_fallback": False,
    }

    tps_raw = raw.get("key_turning_points") or []
    if not isinstance(tps_raw, list):
        tps_raw = []
    turning_points: list[dict] = []
    for tp in tps_raw[:8]:
        if not isinstance(tp, dict):
            continue
        idx = tp.get("scene_index")
        if not isinstance(idx, int):
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                continue
        if idx < 0:
            continue
        turning_points.append({
            "scene_index": idx,
            "what_diverged": _str(tp.get("what_diverged"), 400),
            "likely_cause_cf_id": _str(tp.get("likely_cause_cf_id"), 100),
        })
    out["key_turning_points"] = turning_points

    chains_raw = raw.get("cf_impact_chains") or []
    if not isinstance(chains_raw, list):
        chains_raw = []
    chains: list[dict] = []
    for ch in chains_raw[:10]:
        if not isinstance(ch, dict):
            continue
        side = ch.get("side")
        if side not in ("only_in_a", "only_in_b", "common"):
            continue
        chains.append({
            "cf_id": _str(ch.get("cf_id"), 100),
            "side": side,
            "narrative_consequence": _str(ch.get("narrative_consequence"), 500),
        })
    out["cf_impact_chains"] = chains

    return out


def analyze_compare(compare_data: dict) -> dict:
    """LLM 因果分析 SP-9 compare data.

    Args:
      compare_data: SP-9 compare endpoint 返回的完整 dict
        必含 sim_a / sim_b / counterfactual_diff / scene_alignment / stats

    Returns:
      dict:
        summary, verdict_a, verdict_b, key_turning_points[], cf_impact_chains[],
        reasoning, is_fallback(LLM 失败时 True)
    """
    try:
        # 组装 LLM 输入 — trim + 简化
        sim_a = compare_data.get("sim_a") or {}
        sim_b = compare_data.get("sim_b") or {}
        cf_diff = _trim_cf_diff(compare_data.get("counterfactual_diff") or {})
        scenes = _trim_scene_alignment(compare_data.get("scene_alignment") or [])
        stats = compare_data.get("stats") or {}

        user_input = {
            "sim_a": {
                "divergence": sim_a.get("divergence", ""),
                "reshape_percent": sim_a.get("reshape_percent", 0),
                "current_round": sim_a.get("current_round", 0),
            },
            "sim_b": {
                "divergence": sim_b.get("divergence", ""),
                "reshape_percent": sim_b.get("reshape_percent", 0),
                "current_round": sim_b.get("current_round", 0),
            },
            "counterfactual_diff": cf_diff,
            "scene_alignment_trimmed": scenes,
            "stats": stats,
        }
        system_prompt = _load_prompt()

        result, _usage = call_llm_json(
            system_prompt=system_prompt,
            user_input=user_input,
            max_tokens=2500,
            temperature=0.4,  # 分析任务偏稳定
            retries=2,
            timeout=90.0,
        )
        validated = _validate_llm_result(result)
        logger.info(
            f"comparison_analyzer: summary={len(validated['summary'])}c / "
            f"turning_points={len(validated['key_turning_points'])} / "
            f"chains={len(validated['cf_impact_chains'])}"
        )
        return validated

    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"comparison_analyzer: LLM failed: {e}")
        return _build_program_fallback(compare_data)
    except ValueError as e:
        logger.warning(f"comparison_analyzer: LLM result invalid: {e}")
        return _build_program_fallback(compare_data)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"comparison_analyzer: unexpected error: {e}")
        return _build_program_fallback(compare_data)


__all__ = ["analyze_compare"]
