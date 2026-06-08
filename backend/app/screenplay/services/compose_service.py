"""主流程编排 — PR#10 commit 3。

把 PR#6-9 各 agent 串成"小说 → 完整剧本 YAML"的端到端流程。

调用栈:
  POST /novels/{id}/compose-screenplay  (commit 4 endpoint)
      ↓
  orchestrate_full_pipeline(novel_id, options)
      ├─ 取 novel + chapters(ingest_service)
      ├─ 取或生成 bible(story_bible_service,缺则自动调 LLM 抽取)
      ├─ for chapter:
      │    ├─ scene_splitter(retry 2 次)
      │    └─ for scene:
      │         ├─ element_extractor(retry 2 次)
      │         ├─ refine_attribution(可选,失败降级)
      │         └─ propose_decisions(可选,失败降级)
      ├─ assemble_screenplay(纯组装,commit 1)
      ├─ validate_screenplay_yaml(校验)
      └─ save_screenplay(持久化,commit 2)

降级策略(对齐 PR#10 设计):
  非阻断:
    - dialogue_attributor 失败 → 用 PR#7 原始结果 + warning
    - propose_decisions 失败 → 空 decisions + warning
    - element_extractor 失败 → 跳过本 scene + warning
  阻断式:
    - scene_splitter 单章重试 N 次仍失败 → 整章跳过 + 进 failed_chapters
    - bible 抽取失败 + 无现有 bible → 整 pipeline raise
    - 所有 scene 都被过滤 → 整 pipeline raise
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from app.screenplay.config import settings
from app.screenplay.services import ingest_service, screenplay_store, story_bible_service
from app.screenplay.services.llm_client import LlmCallFailed, LlmJsonParseFailed
from app.screenplay.services.pipeline.adaptation_decision import (
    AdaptationDecisionError,
    propose_decisions,
)
from app.screenplay.services.pipeline.dialogue_attributor import (
    DialogueAttributionError,
    refine_attribution,
)
from app.screenplay.services.pipeline.element_extractor import (
    CharacterRef,
    ElementExtractError,
    ScreenplayElement,
    SceneTextInput,
    build_scene_text,
    extract_elements,
)
from app.screenplay.services.pipeline.fidelity_scorer import (
    FidelityInput,
    score_scene_fidelity,
)
from app.screenplay.services.pipeline.scene_splitter import (
    ChapterInput,
    SceneSplitError,
    SplitScene,
    split_chapter,
)
from app.screenplay.services.pipeline.yaml_composer import (
    ComposeError,
    ComposeInput,
    SceneAssembleData,
    assemble_screenplay,
)
from app.screenplay.services.yaml_validator import validate_screenplay_yaml

logger = logging.getLogger(__name__)


# ============================================================
# 选项 + 结果 + 异常
# ============================================================


@dataclass
class ComposeOptions:
    """compose 选项。"""

    refine_dialogue: bool = True            # 是否跑 PR#8 精修(贵但提升对白归属准确率)
    propose_decisions: bool = True          # 是否跑 PR#9 改编决策(贵,差异化卖点)
    max_chapters: int | None = None         # 调试时只跑前 N 章(快速 demo / 节省 LLM 钱)
    retry_per_call: int = 2                 # 单次 LLM 失败重试次数(共尝试 retry+1 次)


@dataclass
class ComposePipelineResult:
    """编排结果 — endpoint 直接序列化返前端。"""

    screenplay_id: str                       # 持久化后的 UUID hex
    yaml_text: str
    stats: dict
    warnings: list[dict] = field(default_factory=list)
    failed_chapters: list[int] = field(default_factory=list)
    validation_errors: list[dict] = field(default_factory=list)


class ComposePipelineError(Exception):
    """编排层致命错误 — endpoint 转 4xx/5xx。"""

    def __init__(self, message: str, code: str = "PIPELINE_FAILED") -> None:
        super().__init__(message)
        self.code = code


# ============================================================
# 主入口
# ============================================================


def orchestrate_full_pipeline(
    novel_id: str,
    user_id: str,
    options: ComposeOptions | None = None,
    progress_callback: Callable[[dict], None] | None = None,
) -> ComposePipelineResult:
    """端到端编排:小说 → 多 agent → 组装 → 校验 → 持久化。

    Args:
        novel_id: 已摄入的小说 ID
        user_id: 父平台 users.id(用于 SQL 层数据隔离 — 阶段 3.5)
        options: 编排选项(默认全开)
        progress_callback: 可选,每章完成 / 每场失败时调用 dict 进度事件

    Returns:
        ComposePipelineResult

    Raises:
        ComposePipelineError(code):
          - NOVEL_NOT_FOUND       — novel_id 不存在或不属于该用户
          - NO_CHAPTERS           — novel 无章节
          - BIBLE_FAILED          — 无 bible 且自动抽取也失败
          - BIBLE_EMPTY           — bible 抽完仍无 characters
          - NO_SCENES_PRODUCED    — 所有章节切分 / 抽取都失败,没有可组装的场景
          - COMPOSE_FAILED        — composer 拒绝组装(理论上不应发生,只在数据极端时)
    """
    opts = options or ComposeOptions()

    # ============================================================
    # 1. 取 novel + chapters(必须属于当前用户)
    # ============================================================
    novel = ingest_service.get_novel(novel_id, user_id=user_id)
    if novel is None:
        raise ComposePipelineError(f"novel {novel_id} 不存在", code="NOVEL_NOT_FOUND")

    all_chapters = novel.get("chapters") or []
    if not all_chapters:
        raise ComposePipelineError("novel 无章节", code="NO_CHAPTERS")

    chapters = all_chapters
    if opts.max_chapters is not None and opts.max_chapters > 0:
        chapters = all_chapters[: opts.max_chapters]

    _emit(progress_callback, "pipeline_start", {
        "novel_id": novel_id, "total_chapters": len(chapters),
    })

    # ============================================================
    # 2. 取或生成 bible(user_id 透传,校验归属)
    # ============================================================
    bible = story_bible_service.get_bible(novel_id, user_id=user_id)
    if bible is None:
        _emit(progress_callback, "bible_extracting", {"novel_id": novel_id})
        try:
            story_bible_service.extract_bible_with_llm(
                novel_id, user_id=user_id, max_chapters=3,
            )
        except (LlmCallFailed, LlmJsonParseFailed) as e:
            raise ComposePipelineError(
                f"自动抽取故事圣经失败:{e}", code="BIBLE_FAILED",
            ) from e
        bible = story_bible_service.get_bible(novel_id, user_id=user_id)

    if bible is None or not bible.get("characters"):
        raise ComposePipelineError(
            "故事圣经缺少 characters(自动抽取失败或返回空)", code="BIBLE_EMPTY",
        )

    # 给 composer / pipeline 用的 bible 精简版
    bible_for_composer = {
        "characters": bible.get("characters") or [],
        "locations": bible.get("locations") or [],
    }
    # 给 LLM 喂的更精简版(节省 token)
    bible_for_llm = {
        "characters": [
            {"id": c.get("id"), "name": c.get("name"), "aka": c.get("aka") or []}
            for c in bible_for_composer["characters"]
        ],
        "locations": [
            {"id": l.get("id"), "name": l.get("name"), "int_ext": l.get("int_ext", "INT")}
            for l in bible_for_composer["locations"]
        ],
    }

    # ============================================================
    # 3. 章节循环 → 场景循环
    # ============================================================
    all_scenes: list[SceneAssembleData] = []
    warnings: list[dict] = []
    failed_chapters: list[int] = []
    stats_counter = {
        "split_calls": 0,
        "extract_calls": 0,
        "refine_calls": 0,
        "decision_calls": 0,
        "scenes_skipped": 0,
    }

    # 阶段 5.2:桥接预计算 — SP-7 关系正负极。bible 范围内的角色 + aka 共用,
    # 不必每章重查。挂了就返空块,scene_splitter 退化到无桥接模式。
    splitter_bridge_block = _build_polarity_bridge_block(novel_id, user_id, bible_for_composer)
    # 阶段 5.5:项目级故事事实块(全 compose 共用,decision 用)
    facts_bridge_block = _build_story_facts_bridge_block(novel_id, user_id)

    for ch in chapters:
        ch_num = ch["number"]
        ch_id = ch["id"]
        paragraphs = ingest_service.get_chapter_paragraphs(ch_id, user_id=user_id) or []
        if not paragraphs:
            warnings.append({
                "layer": "chapter", "path": f"chapter[{ch_num}]",
                "message": "章节无段落,跳过",
            })
            failed_chapters.append(ch_num)
            continue

        # 3a. 切分章节为场景
        paragraphs_input = [
            {"index": p["index_in_chapter"], "text": p["text"]} for p in paragraphs
        ]
        split_result = _run_scene_splitter(
            ChapterInput(
                chapter_number=ch_num,
                chapter_title=ch.get("title"),
                paragraphs=paragraphs_input,
                story_bible=bible_for_llm,
            ),
            opts.retry_per_call, warnings,
            bridge_block=splitter_bridge_block,
        )
        stats_counter["split_calls"] += 1

        if split_result is None or not split_result.scenes:
            failed_chapters.append(ch_num)
            _emit(progress_callback, "chapter_failed", {"chapter_number": ch_num})
            continue

        # 3b. 每场抽元素 + 精修 + 决策
        for sp in split_result.scenes:
            scene_text = build_scene_text(
                [{"index_in_chapter": p["index_in_chapter"], "text": p["text"]}
                 for p in paragraphs],
                sp.paragraph_range[0],
                sp.paragraph_range[1],
            )

            chars_in_scene = _resolve_characters_in_scene(sp, bible_for_composer)
            scene_path = f"chapter[{ch_num}].scene[{sp.scene_index_in_chapter}]"

            # 阶段 5.3:按场拉桥接资产 — drivers / knowledge 是「本场在场角色」
            # 维度的,polarity 已在 splitter 算过,这里复用本场角色子集就够
            present_names = [c.name for c in chars_in_scene]
            scene_index_overall = len(all_scenes) + 1
            extractor_bridge = _build_per_scene_bridge(
                novel_id, user_id, present_names, scene_index_overall,
            )

            # 3b1. element_extractor(接通 SP-2/3/7 三档桥接资产)
            elements = _run_element_extractor(
                SceneTextInput(
                    scene_summary=sp.summary,
                    scene_heading={
                        "int_ext": sp.heading.int_ext,
                        "location_name": sp.heading.location_name,
                        "time_of_day": sp.heading.time_of_day,
                    },
                    scene_text=scene_text,
                    characters_in_scene=chars_in_scene,
                ),
                opts.retry_per_call, warnings, scene_path,
                bridge_drivers_block=extractor_bridge["drivers"],
                bridge_knowledge_block=extractor_bridge["knowledge"],
                bridge_polarity_block=extractor_bridge["polarity"],
            )
            stats_counter["extract_calls"] += 1

            if elements is None:
                # 整场被跳过(LLM 抽取 N 次都失败)
                stats_counter["scenes_skipped"] += 1
                continue

            # 3b2. dialogue_attributor(可选,失败降级)
            # 阶段 5.4:driver 块复用 element_extractor 已抓的(同场角色不变)
            if opts.refine_dialogue and elements:
                refined = _run_dialogue_attributor(
                    scene_text, chars_in_scene, elements, warnings, scene_path,
                    bridge_drivers_block=extractor_bridge["drivers"],
                )
                stats_counter["refine_calls"] += 1
                if refined is not None:
                    elements = refined

            # 3b3. propose_decisions(可选,失败降级)
            # 阶段 5.5:接通 SP-2 drivers / SP-3 knowledge / SP-3 facts
            decisions = []
            if opts.propose_decisions and elements:
                decisions = _run_adaptation_decision(
                    scene_text, sp, chars_in_scene, elements, warnings, scene_path,
                    bridge_drivers_block=extractor_bridge["drivers"],
                    bridge_knowledge_block=extractor_bridge["knowledge"],
                    bridge_facts_block=facts_bridge_block,
                )
                stats_counter["decision_calls"] += 1

            # 3b4. fidelity 评分(程序级,无 LLM)
            fidelity_dict = _compute_fidelity(
                scene_text=scene_text,
                characters_present=list(sp.characters_present),
                bible=bible_for_composer,
                elements=elements,
                decisions=decisions,
            )

            all_scenes.append(SceneAssembleData(
                chapter_number=ch_num,
                scene_index_in_chapter=sp.scene_index_in_chapter,
                heading=sp.heading,
                summary=sp.summary,
                characters_present_names=list(sp.characters_present),
                paragraph_range=sp.paragraph_range,
                elements=elements,
                decisions=decisions,
                transition_to_next=sp.transition_to_next,
                fidelity=fidelity_dict,
            ))

        _emit(progress_callback, "chapter_done", {
            "chapter_number": ch_num,
            "scenes_added": len(split_result.scenes),
        })

    if not all_scenes:
        raise ComposePipelineError(
            "所有章节都失败或没有可组装的场景", code="NO_SCENES_PRODUCED",
        )

    # ============================================================
    # 4. 组装
    # ============================================================
    _emit(progress_callback, "assembling", {"scene_count": len(all_scenes)})

    compose_input = ComposeInput(
        novel_title=novel.get("title") or "未命名",
        bible=bible_for_composer,
        scenes=all_scenes,
        source_format=novel.get("source_format"),
        source_filename=novel.get("source_filename"),
        adapted_from_chapters=[ch["number"] for ch in chapters],
        model_name=settings.deepseek_model,
    )
    try:
        compose_result = assemble_screenplay(compose_input)
    except ComposeError as e:
        raise ComposePipelineError(f"组装失败:{e}", code="COMPOSE_FAILED") from e

    # 把 composer warnings 合入
    for w in compose_result.warnings:
        warnings.append({"layer": w.layer, "path": w.path, "message": w.message})

    # ============================================================
    # 5. 校验(理论上必过 — composer 单测已覆盖;不过仍要兜底防 LLM 极端输出)
    # ============================================================
    validation = validate_screenplay_yaml(compose_result.yaml_text)
    validation_errors: list[dict] = []
    if not validation.valid:
        logger.error(
            "Assembled YAML failed validation — this is a bug, please investigate: %s",
            [(i.layer, i.path, i.message) for i in validation.errors()],
        )
        validation_errors = [
            {"layer": i.layer, "path": i.path, "message": i.message, "severity": i.severity}
            for i in validation.errors()
        ]
        # 即使校验失败也持久化(便于事后调试 + 退一步:给用户看半成品也好过 500)

    # ============================================================
    # 6. 持久化 + 返
    # ============================================================
    final_stats = {
        **compose_result.stats,
        **stats_counter,
        "chapters_total": len(chapters),
        "chapters_processed": len(chapters) - len(failed_chapters),
        "yaml_schema_valid": validation.valid,
    }

    # 防御性 catch:理论上 step 1 已经验证 novel 属于 user(get_novel(user_id)),
    # 这里 save_screenplay 内部的 user_id 二次校验不会失败;但若 LLM 跑期间 novel
    # 被删 / 用户权限变了,会抛 PermissionError。转 ComposePipelineError 让 router 报 404。
    try:
        screenplay_id = screenplay_store.save_screenplay(
            novel_id=novel_id,
            user_id=user_id,
            yaml_text=compose_result.yaml_text,
            stats=final_stats,
            warnings=warnings,
            failed_chapters=failed_chapters,
            schema_version="1.0",
            model_name=settings.deepseek_model,
        )
    except PermissionError as e:
        raise ComposePipelineError(
            f"作品状态异常,无法保存剧本(可能在编排期间被删除):{e}",
            code="NOVEL_NOT_FOUND",
        ) from e

    _emit(progress_callback, "pipeline_done", {
        "screenplay_id": screenplay_id,
        "scene_count": len(all_scenes),
        "warnings_count": len(warnings),
        "failed_chapters_count": len(failed_chapters),
    })

    return ComposePipelineResult(
        screenplay_id=screenplay_id,
        yaml_text=compose_result.yaml_text,
        stats=final_stats,
        warnings=warnings,
        failed_chapters=failed_chapters,
        validation_errors=validation_errors,
    )


# ============================================================
# 单元降级 wrapper(每个 agent 一个)
# ============================================================


def _run_scene_splitter(
    chapter_input: ChapterInput,
    retry_count: int,
    warnings: list[dict],
    *,
    bridge_block: str = "",
) -> object | None:
    """split_chapter + 重试。N+1 次都失败 → 记 warning + 返 None。

    bridge_block: 阶段 5.2 — SP-7 关系正负极 prompt 块,透传给 split_chapter。
    """
    ch_num = chapter_input.chapter_number
    last_err: Exception | None = None
    for attempt in range(retry_count + 1):
        try:
            return split_chapter(chapter_input, bridge_block=bridge_block)
        except SceneSplitError as e:
            last_err = e
            logger.warning(
                "scene_splitter chapter %s attempt %d/%d failed: %s",
                ch_num, attempt + 1, retry_count + 1, e,
            )
    warnings.append({
        "layer": "chapter", "path": f"chapter[{ch_num}]",
        "message": f"scene_splitter 重试 {retry_count + 1} 次仍失败:{last_err}",
    })
    return None


def _run_element_extractor(
    scene_input: SceneTextInput,
    retry_count: int,
    warnings: list[dict],
    path: str,
    *,
    bridge_drivers_block: str = "",
    bridge_knowledge_block: str = "",
    bridge_polarity_block: str = "",
) -> list[ScreenplayElement] | None:
    """extract_elements + 重试。N+1 次都失败 → 记 warning + 返 None(跳过本场)。

    阶段 5.3:接通 SP-2/3/7 三档桥接资产,任一非空就透传给 LLM。
    """
    last_err: Exception | None = None
    for attempt in range(retry_count + 1):
        try:
            result = extract_elements(
                scene_input,
                bridge_drivers_block=bridge_drivers_block,
                bridge_knowledge_block=bridge_knowledge_block,
                bridge_polarity_block=bridge_polarity_block,
            )
            return result.elements
        except ElementExtractError as e:
            last_err = e
            logger.warning(
                "element_extractor %s attempt %d/%d failed: %s",
                path, attempt + 1, retry_count + 1, e,
            )
    warnings.append({
        "layer": "scene", "path": path,
        "message": f"element_extractor 重试 {retry_count + 1} 次仍失败,本场跳过:{last_err}",
    })
    return None


def _run_dialogue_attributor(
    scene_text: str,
    characters_in_scene: list[CharacterRef],
    draft_elements: list[ScreenplayElement],
    warnings: list[dict],
    path: str,
    *,
    bridge_drivers_block: str = "",
) -> list[ScreenplayElement] | None:
    """refine_attribution,失败降级为返 None(调用方继续用 draft_elements)。

    阶段 5.4:接通 SP-2 驱动力作为消歧 tiebreaker。
    """
    try:
        result = refine_attribution(
            scene_text, characters_in_scene, draft_elements,
            bridge_drivers_block=bridge_drivers_block,
        )
        return result.elements
    except DialogueAttributionError as e:
        logger.warning("dialogue_attributor %s failed (downgrade): %s", path, e)
        warnings.append({
            "layer": "scene", "path": path,
            "message": f"dialogue_attributor 失败,沿用 PR#7 原始归属:{e}",
        })
        return None


def _run_adaptation_decision(
    scene_text: str,
    sp: SplitScene,
    characters_in_scene: list[CharacterRef],
    elements: list[ScreenplayElement],
    warnings: list[dict],
    path: str,
    *,
    bridge_drivers_block: str = "",
    bridge_knowledge_block: str = "",
    bridge_facts_block: str = "",
) -> list:
    """propose_decisions,失败返空数组(decisions 是可选段)。

    阶段 5.5:接通 SP-2 drivers / SP-3 knowledge / SP-3 facts。
    """
    try:
        result = propose_decisions(
            scene_text=scene_text,
            scene_summary=sp.summary,
            scene_heading={
                "int_ext": sp.heading.int_ext,
                "location_name": sp.heading.location_name,
                "time_of_day": sp.heading.time_of_day,
            },
            characters_in_scene=characters_in_scene,
            elements=elements,
            bridge_drivers_block=bridge_drivers_block,
            bridge_knowledge_block=bridge_knowledge_block,
            bridge_facts_block=bridge_facts_block,
        )
        return list(result.decisions)
    except AdaptationDecisionError as e:
        logger.warning("adaptation_decision %s failed (downgrade): %s", path, e)
        warnings.append({
            "layer": "scene", "path": path,
            "message": f"adaptation_decision 失败,本场无改编决策:{e}",
        })
        return []


# ============================================================
# 辅助 — 角色解析 + 事件推送
# ============================================================


def _compute_fidelity(
    scene_text: str,
    characters_present: list[str],
    bible: dict,
    elements: list[ScreenplayElement],
    decisions: list,
) -> dict | None:
    """对一场调用 fidelity_scorer + 兜底异常,返 dict 写入 SceneAssembleData。

    任何错误 → 返 None(不阻断 pipeline)。
    """
    try:
        # 构 aka lookup(给 character_alignment 兜底用)
        aka_lookup: dict[str, list[str]] = {}
        for c in bible.get("characters") or []:
            if not isinstance(c, dict):
                continue
            nm = (c.get("name") or "").strip()
            if not nm:
                continue
            aka_lookup[nm] = [
                str(a).strip() for a in (c.get("aka") or [])
                if isinstance(a, str) and a.strip()
            ]
        result = score_scene_fidelity(FidelityInput(
            scene_text=scene_text,
            characters_present_names=characters_present,
            elements=elements,
            decisions=decisions,
            character_aka_lookup=aka_lookup,
        ))
        return result.to_dict()
    except Exception as e:   # noqa: BLE001
        logger.warning("fidelity scorer raised, dropping: %s", e)
        return None


def _resolve_characters_in_scene(
    sp: SplitScene, bible: dict,
) -> list[CharacterRef]:
    """把 split_scene.characters_present(name 列表)对照 bible 解析为 CharacterRef。

    若某名字在 bible 找不到,跳过(避免给 LLM 输入幻觉角色)。
    """
    bible_chars = bible.get("characters") or []
    name_to_entry: dict[str, dict] = {}
    for c in bible_chars:
        if not isinstance(c, dict):
            continue
        name = (c.get("name") or "").strip()
        if name:
            name_to_entry[name] = c
        for a in (c.get("aka") or []):
            if isinstance(a, str) and a.strip():
                name_to_entry.setdefault(a.strip(), c)

    out: list[CharacterRef] = []
    seen_ids: set[str] = set()
    for n in sp.characters_present:
        entry = name_to_entry.get((n or "").strip())
        if entry is None:
            continue
        cid = entry.get("id") or ""
        if cid in seen_ids:
            continue
        seen_ids.add(cid)
        out.append(CharacterRef(
            id=cid,
            name=entry.get("name", ""),
            aka=list(entry.get("aka") or []),
        ))
    return out


def _build_per_scene_bridge(
    novel_id: str,
    user_id: str,
    present_names: list[str],
    scene_index: int,
) -> dict[str, str]:
    """阶段 5.3 按场桥接 — 拿 SP-2 drivers / SP-3 knowledge / SP-7 polarity 三块。

    Args:
        present_names: 本场在场角色名
        scene_index: 本场在整本中的索引(给 knowledge 按时间过滤用)

    Returns:
        {"drivers": str, "knowledge": str, "polarity": str} — 全失败也返 3 个空串

    异常隔离铁律:任一资产抓取失败 → 该字段返 "",剩下的继续。
    """
    out = {"drivers": "", "knowledge": "", "polarity": ""}
    if not present_names:
        return out
    try:
        from app.screenplay.db.connection import get_connection
        from app.screenplay.services import huimeng_bridge
        conn = get_connection()
        try:
            # SP-2 drivers
            try:
                out["drivers"] = huimeng_bridge.get_character_drivers_block(
                    conn, user_id=user_id, novel_id=novel_id,
                    character_names=present_names,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("per_scene_bridge drivers failed: %s", e)
            # SP-3 knowledge
            try:
                out["knowledge"] = huimeng_bridge.get_character_knowledge_block(
                    conn, user_id=user_id, novel_id=novel_id,
                    character_names=present_names,
                    current_scene_index=scene_index,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("per_scene_bridge knowledge failed: %s", e)
            # SP-7 polarity(本场角色子集)
            try:
                out["polarity"] = huimeng_bridge.get_relationship_polarity_block(
                    conn, user_id=user_id, novel_id=novel_id,
                    character_names=present_names,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("per_scene_bridge polarity failed: %s", e)
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("per_scene_bridge connect failed: %s", e)
    return out


def _build_story_facts_bridge_block(novel_id: str, user_id: str) -> str:
    """阶段 5.5 桥接预计算 — 项目级故事事实块。

    一次 compose 共用(项目维度,不按场变),给 adaptation_decision 用。
    任何失败返 "" — 不阻断主流程。
    """
    try:
        from app.screenplay.db.connection import get_connection
        from app.screenplay.services import huimeng_bridge
        conn = get_connection()
        try:
            return huimeng_bridge.get_story_facts_block(
                conn, user_id=user_id, novel_id=novel_id,
            )
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("story_facts bridge failed: %s", e)
        return ""


def _build_polarity_bridge_block(
    novel_id: str, user_id: str, bible_for_composer: dict,
) -> str:
    """阶段 5.2 桥接预计算 — bible 范围内全部角色名 → SP-7 关系正负极块。

    一次 compose 全章共用,不必每章重查。挂了就返空块,scene_splitter 退化到无桥接模式。
    """
    char_names: list[str] = []
    for c in bible_for_composer.get("characters") or []:
        if not isinstance(c, dict):
            continue
        nm = (c.get("name") or "").strip()
        if nm:
            char_names.append(nm)
        for a in c.get("aka") or []:
            if isinstance(a, str) and a.strip():
                char_names.append(a.strip())

    if not char_names:
        return ""

    try:
        from app.screenplay.db.connection import get_connection
        from app.screenplay.services import huimeng_bridge
        conn = get_connection()
        try:
            return huimeng_bridge.get_relationship_polarity_block(
                conn, user_id=user_id, novel_id=novel_id,
                character_names=char_names,
            )
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logger.warning("polarity bridge failed (downgrade to no-bridge): %s", e)
        return ""


def _emit(
    callback: Callable[[dict], None] | None,
    stage: str,
    payload: dict,
) -> None:
    """安全推送进度事件 — callback 抛异常不中断主流程。"""
    if callback is None:
        return
    try:
        callback({"stage": stage, **payload})
    except Exception as e:   # noqa: BLE001 — 主动吞,callback 不可信
        logger.warning("progress_callback raised (suppressed): %s", e)
