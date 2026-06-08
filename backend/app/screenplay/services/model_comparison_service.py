"""多模型对比服务 — 阶段 8.5(2026-06-08)。

让用户对同一场场景用多个 LLM(deepseek / openai / anthropic / qwen / 等)
并行跑 element_extractor,横向对比产出 + 用 fidelity_scorer 打分。

为什么对比 element_extractor?
  - 是 LLM 输出最丰富的任务(20+ elements / 场)
  - 用户能直观看出"哪个模型对白更自然 / 动作更细 / 内心独白更准"
  - 复用 fidelity_scorer 给可解释打分(动作密度 / 角色对齐 / 对白覆盖 / 决策完整度)
  - **可解释对比** = 文档里说的"反超竞品黑盒打分"差异化

设计原则:
  - 不依赖父平台 llm_routing 单 vendor 限制 — 直接用 _openai_compat_call_json
  - 用户提供 provider configs 数组(每个 {label, api_key, base_url, model})
  - asyncio.gather 真并行
  - graceful degradation:某 provider 失败不阻断其他
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import yaml as yamllib

from app.services.llm_client import (
    LlmCallFailed,
    LlmJsonParseFailed,
    _openai_compat_call_json,
)
from app.screenplay.services import screenplay_store
from app.screenplay.services.pipeline.element_extractor import (
    CharacterRef,
    ScreenplayElement,
    _parse_and_validate_elements,
    _build_valid_names,
)
from app.screenplay.services.pipeline.fidelity_scorer import (
    FidelityInput,
    score_scene_fidelity,
)

logger = logging.getLogger(__name__)


# ============================================================
# 数据契约
# ============================================================


@dataclass
class ProviderConfig:
    """用户提供的 provider 配置。"""
    label: str           # 显示用 "DeepSeek V3" / "OpenAI GPT-4o"
    api_key: str
    base_url: str        # https://api.deepseek.com/v1 等
    model: str           # deepseek-chat / gpt-4o / claude-sonnet-4 / qwen-max


@dataclass
class CompareScores:
    """fidelity 4 维 + 加权总分。"""
    overall: float
    action_density: float
    character_alignment: float
    dialogue_coverage: float
    decision_completeness: float
    elements_count: int
    dialogue_count: int
    voiceover_count: int


@dataclass
class ModelCandidate:
    """单 provider 的产出 + 评分。"""
    provider_label: str
    model: str
    success: bool
    elements: list[dict] = field(default_factory=list)
    scores: Optional[CompareScores] = None
    error_message: str = ""
    usage: dict = field(default_factory=dict)
    duration_ms: int = 0


@dataclass
class ComparisonResult:
    """对比响应。"""
    scene_id: str
    candidates: list[ModelCandidate] = field(default_factory=list)
    recommended_label: Optional[str] = None  # 分数最高的 provider_label


# ============================================================
# 主入口
# ============================================================


class ComparisonError(Exception):
    """对比任务失败(scene 不存在 / 输入非法)。"""


_PROMPT_PATH = (
    Path(__file__).parent.parent / "prompts" / "element_extractor.md"
)
_SYSTEM_PROMPT_CACHE: Optional[str] = None


def _get_system_prompt() -> str:
    global _SYSTEM_PROMPT_CACHE
    if _SYSTEM_PROMPT_CACHE is None:
        _SYSTEM_PROMPT_CACHE = _PROMPT_PATH.read_text(encoding="utf-8")
    return _SYSTEM_PROMPT_CACHE


async def compare_scene_extraction(
    screenplay_id: str,
    user_id: str,
    *,
    scene_id: str,
    providers: list[ProviderConfig],
) -> ComparisonResult:
    """对一个场景用多个 provider 并行跑 element_extractor + fidelity 打分。

    Args:
        screenplay_id: 已生成的剧本 ID
        user_id: 当前用户(隔离校验)
        scene_id: 目标场景
        providers: 至少 2 个 provider config

    Returns:
        ComparisonResult,包含每个 provider 的产出 + 评分 + 推荐

    Raises:
        ComparisonError: 剧本不存在 / 不属用户 / 目标场景不存在 / providers 不足
    """
    if len(providers) < 2:
        raise ComparisonError("对比至少需要 2 个 provider")

    # 1. 拉剧本 + 找目标场景
    record = screenplay_store.get_screenplay_by_id(screenplay_id, user_id=user_id)
    if record is None:
        raise ComparisonError("剧本不存在或不属于该用户")

    try:
        parsed = yamllib.safe_load(record["yaml_text"]) or {}
    except yamllib.YAMLError as e:
        raise ComparisonError(f"yaml 解析失败:{e}")

    scenes = parsed.get("scenes") or []
    target_scene = next(
        (s for s in scenes if isinstance(s, dict) and s.get("id") == scene_id),
        None,
    )
    if target_scene is None:
        raise ComparisonError(f"场景 {scene_id} 不存在")

    # 2. 构造 element_extractor 的 user_input
    scene_text = _extract_scene_text(target_scene)
    if not scene_text:
        raise ComparisonError("场景原文为空")

    characters_section = parsed.get("characters") or []
    char_id_to_ref = {}
    for c in characters_section:
        if not isinstance(c, dict):
            continue
        cid = c.get("id")
        nm = c.get("name", "")
        if cid and nm:
            char_id_to_ref[cid] = CharacterRef(
                id=cid, name=nm, aka=list(c.get("aka") or []),
            )
    chars_in_scene_ids = target_scene.get("characters_present") or []
    chars_in_scene = [char_id_to_ref[cid] for cid in chars_in_scene_ids if cid in char_id_to_ref]

    if not chars_in_scene:
        raise ComparisonError("场景无在场角色,无法对比")

    location = next(
        (l for l in (parsed.get("locations") or [])
         if isinstance(l, dict) and l.get("id") == (target_scene.get("heading") or {}).get("location_id")),
        None,
    )
    scene_heading = {
        "int_ext": (target_scene.get("heading") or {}).get("int_ext", "INT"),
        "location_name": location.get("name", "未命名") if location else "未命名",
        "time_of_day": (target_scene.get("heading") or {}).get("time_of_day", "日"),
    }

    user_input = {
        "scene_summary": target_scene.get("summary", ""),
        "scene_heading": scene_heading,
        "scene_text": scene_text,
        "characters_in_scene": [
            {"id": c.id, "name": c.name, "aka": c.aka}
            for c in chars_in_scene
        ],
    }

    # 3. 并行调用每个 provider
    valid_names = _build_valid_names(chars_in_scene)
    tasks = [
        asyncio.create_task(_call_provider_async(
            p, user_input, scene_text, chars_in_scene, valid_names,
        ))
        for p in providers
    ]
    candidates = await asyncio.gather(*tasks, return_exceptions=False)

    # 4. 找最高分作推荐
    recommended_label = None
    best_overall = -1.0
    for c in candidates:
        if c.success and c.scores is not None and c.scores.overall > best_overall:
            best_overall = c.scores.overall
            recommended_label = c.provider_label

    return ComparisonResult(
        scene_id=scene_id,
        candidates=candidates,
        recommended_label=recommended_label,
    )


# ============================================================
# 内部:单 provider 调用 + 打分(异步包装)
# ============================================================


async def _call_provider_async(
    provider: ProviderConfig,
    user_input: dict,
    scene_text: str,
    chars_in_scene: list[CharacterRef],
    valid_names: dict[str, str],
) -> ModelCandidate:
    """跑一个 provider 的 element 抽取 + 打分。

    任何错误转 ModelCandidate.success=False,不抛(graceful degradation 铁律)。
    """
    import time
    start = time.monotonic()

    def _sync_call() -> tuple[list[dict], dict]:
        """同步部分:_openai_compat_call_json + 解析 elements。"""
        parsed, usage = _openai_compat_call_json(
            _get_system_prompt(),
            user_input,
            api_key=provider.api_key,
            api_base=provider.base_url,
            model=provider.model,
            vendor_label=f"COMPARE-{provider.label}",
            max_tokens=4000,
            temperature=0.3,
            retries=1,  # 对比场景少重试,失败就快速降级
            timeout=60.0,
        )
        if not isinstance(parsed, dict):
            raise LlmJsonParseFailed("响应根节点不是 dict")
        elements_raw = parsed.get("elements")
        if not isinstance(elements_raw, list):
            raise LlmJsonParseFailed("缺 elements 数组")
        elements = _parse_and_validate_elements(elements_raw, valid_names)
        elements_dicts = [
            {
                "type": el.type,
                "text": el.text,
                "character_name": el.character_name,
                "parenthetical": el.parenthetical,
                "is_inner_monologue": el.is_inner_monologue,
            }
            for el in elements
        ]
        return elements_dicts, usage, elements  # 第 3 个是 ScreenplayElement 列表,给 scorer 用

    try:
        # 用 asyncio.to_thread 把同步 LLM 调用塞到 thread pool
        elements_dicts, usage, screen_elements = await asyncio.to_thread(_sync_call)
    except (LlmCallFailed, LlmJsonParseFailed) as e:
        return ModelCandidate(
            provider_label=provider.label,
            model=provider.model,
            success=False,
            error_message=f"{type(e).__name__}: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("compare provider %s unexpected error: %s", provider.label, e)
        return ModelCandidate(
            provider_label=provider.label,
            model=provider.model,
            success=False,
            error_message=f"未预期错误: {e}",
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    # fidelity 打分
    try:
        present_names = [c.name for c in chars_in_scene]
        aka_lookup = {c.name: list(c.aka) for c in chars_in_scene}
        result = score_scene_fidelity(FidelityInput(
            scene_text=scene_text,
            characters_present_names=present_names,
            elements=screen_elements,
            decisions=[],  # 对比阶段不跑 propose_decisions,decision_completeness 会偏低
            character_aka_lookup=aka_lookup,
        ))
        # FidelityResult.dimensions 是 list[DimensionScore],按 name 取
        dim_by_name = {d.name: d.score for d in result.dimensions}
        # result.score 是加权 0-1
        scores = CompareScores(
            overall=round(float(result.score), 3),
            action_density=round(dim_by_name.get("element_density", 0.0), 3),
            character_alignment=round(dim_by_name.get("character_alignment", 0.0), 3),
            dialogue_coverage=round(dim_by_name.get("dialogue_coverage", 0.0), 3),
            decision_completeness=round(dim_by_name.get("decision_completeness", 0.0), 3),
            elements_count=len(elements_dicts),
            dialogue_count=sum(1 for e in elements_dicts if e["type"] == "dialogue"),
            voiceover_count=sum(1 for e in elements_dicts if e["type"] == "voiceover"),
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("compare scorer failed for %s: %s", provider.label, e)
        scores = None

    return ModelCandidate(
        provider_label=provider.label,
        model=provider.model,
        success=True,
        elements=elements_dicts,
        scores=scores,
        usage=usage,
        duration_ms=int((time.monotonic() - start) * 1000),
    )


def _extract_scene_text(scene: dict) -> str:
    """从 scene 元数据 + paragraph_range 拿原文。

    阶段 8.5 MVP:由于完整 paragraph 文本需要 JOIN sp_paragraphs(对比 demo
    场景下用户大概率能接受小延迟,但代码简洁起见,我们用 scene 自带的
    elements 文本拼接作为 source — 这跟 element_extractor 真用的 scene_text
    略不同,但对横评不同模型够用了)。
    """
    elements = scene.get("elements") or []
    if not isinstance(elements, list):
        return ""
    parts = []
    for el in elements:
        if isinstance(el, dict):
            text = el.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(parts)


def to_dict(result: ComparisonResult) -> dict:
    """ComparisonResult → JSON-serializable dict。"""
    return asdict(result)
