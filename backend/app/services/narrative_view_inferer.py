"""SP-8.1(2026-05-29)— LLM 推断视角扩展三件套.

输出:
- narrative_focus_character_id(从已抽角色中匹配名字)
- narrator_reliability:reliable / unreliable / uncertain
- narrative_distance:omniscient / limited / close / intimate

策略:
- 给 LLM 头/中/尾采样 + 角色列表(含主角标 + 出场频次估算)
- LLM 输出 focus_character_name(字符串名),service 层映射成 id
- 匹配失败 → focus_character_id=None,但 reliability/distance 仍可用

普适性:基于实际文本的人称代词分布 + 内心独白 vs 外部描述比例判定,
       不依赖训练数据中作品的预存印象.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from app.db import execute, fetch_one
from app.services.llm_client import call_llm_json, LlmCallFailed, LlmJsonParseFailed
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)


_SAMPLE_CHARS = 1500

_VALID_RELIABILITY = ("reliable", "unreliable", "uncertain")
_VALID_DISTANCE = ("omniscient", "limited", "close", "intimate")

_FALLBACK_RESULT = {
    "narrative_focus_character_id": None,
    "narrative_focus_character_name": "",
    "narrator_reliability": None,
    "narrative_distance": None,
    "reasoning": "AI 推断失败,请手动填写三件套",
}


def _load_prompt() -> str:
    prompt_path = (
        Path(__file__).parent.parent.parent.parent
        / "prompts" / "narrative_view_inferer.md"
    )
    return prompt_path.read_text(encoding="utf-8")


def _sample_text(full_text: str, sample_chars: int = _SAMPLE_CHARS) -> dict[str, str]:
    n = len(full_text)
    if n <= sample_chars * 3:
        return {
            "head_excerpt": full_text[:sample_chars],
            "middle_excerpt": full_text[sample_chars : sample_chars * 2] if n > sample_chars else "",
            "tail_excerpt": full_text[-sample_chars:] if n > sample_chars * 2 else "",
        }
    head = full_text[:sample_chars]
    middle_start = (n - sample_chars) // 2
    middle = full_text[middle_start : middle_start + sample_chars]
    tail = full_text[-sample_chars:]
    return {"head_excerpt": head, "middle_excerpt": middle, "tail_excerpt": tail}


def _get_full_text_for_project(
    conn: sqlite3.Connection, project_id: str,
) -> Optional[str]:
    from app.config import settings
    from app.services import file_parser

    row = fetch_one(
        conn,
        "SELECT storage_path, mime_type FROM uploads "
        "WHERE project_id=? AND state='ready' "
        "ORDER BY uploaded_at DESC LIMIT 1",
        (project_id,),
    )
    if not row:
        return None
    abs_path = settings.uploads_abs_dir / row["storage_path"]
    if not abs_path.exists():
        return None
    parse_result = file_parser.parse_file(abs_path, row["mime_type"])
    if not parse_result.success or not parse_result.text:
        return None
    return parse_result.text


def _get_characters_for_view(
    conn: sqlite3.Connection, project_id: str,
) -> list[dict[str, Any]]:
    """拉角色:id + name + aliases + is_protagonist.LLM 选 focus 时优先主角.

    给 LLM 后,LLM 输出 name,service 层映射到 id.
    """
    rows = conn.execute(
        "SELECT id, name, aliases_json, is_protagonist FROM characters "
        "WHERE project_id=? "
        "ORDER BY is_protagonist DESC, name ASC LIMIT 20",
        (project_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        aliases: list[str] = []
        try:
            aj = r["aliases_json"]
            if aj:
                parsed = json.loads(aj)
                if isinstance(parsed, list):
                    aliases = [a for a in parsed if isinstance(a, str)]
        except (json.JSONDecodeError, TypeError):
            pass
        out.append({
            "id": r["id"],
            "name": r["name"],
            "aliases": aliases,
            "is_protagonist": bool(r["is_protagonist"]),
        })
    return out


def _map_focus_name_to_id(
    char_name: str, characters: list[dict[str, Any]],
) -> Optional[str]:
    """LLM 输出 name → 找 id.先精确,再 alias 包含,无匹配返 None."""
    if not char_name:
        return None
    name_trim = char_name.strip()
    if not name_trim:
        return None
    for c in characters:
        if c["name"] == name_trim:
            return str(c["id"])
    for c in characters:
        if name_trim in c.get("aliases", []):
            return str(c["id"])
    # 子串兜底(LLM 可能输出"渡边君"匹配"渡边")
    for c in characters:
        if c["name"] in name_trim or name_trim in c["name"]:
            return str(c["id"])
    return None


def _validate_llm_result(raw: Any, characters: list[dict[str, Any]]) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"LLM result not a dict: {type(raw)}")

    focus_name = raw.get("narrative_focus_character_name", "")
    if focus_name is None:
        focus_name = ""
    if not isinstance(focus_name, str):
        focus_name = ""
    focus_id = _map_focus_name_to_id(focus_name, characters)

    reliability = raw.get("narrator_reliability")
    if reliability not in _VALID_RELIABILITY:
        reliability = None

    distance = raw.get("narrative_distance")
    if distance not in _VALID_DISTANCE:
        distance = None

    reasoning = raw.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = ""

    return {
        "narrative_focus_character_id": focus_id,
        "narrative_focus_character_name": focus_name.strip()[:50],
        "narrator_reliability": reliability,
        "narrative_distance": distance,
        "reasoning": reasoning.strip()[:500],
    }


def _build_initial_mode_context_for_view(
    conn: sqlite3.Connection, project_id: str,
) -> dict[str, Any]:
    """初始态:基于 world_baseline + characters(尤其主角) + 关系 polarity 概貌
    推断 view.材料稀薄,LLM 应坦诚标注信心.
    """
    proj_row = fetch_one(
        conn,
        "SELECT world_baseline_json, narrative_pov FROM projects WHERE id=?",
        (project_id,),
    )
    baseline: dict[str, str] = {}
    existing_pov = ""
    if proj_row:
        try:
            parsed = json.loads(proj_row["world_baseline_json"] or "{}")
            if isinstance(parsed, dict):
                baseline = {
                    k: str(v)[:200] for k, v in parsed.items()
                    if isinstance(v, str) and v.strip()
                }
        except (json.JSONDecodeError, TypeError):
            pass
        existing_pov = (proj_row["narrative_pov"] or "")

    char_rows = conn.execute(
        "SELECT name, identity, is_protagonist FROM characters "
        "WHERE project_id=? "
        "ORDER BY is_protagonist DESC, name ASC LIMIT 10",
        (project_id,),
    ).fetchall()
    characters_brief = [
        {
            "name": r["name"],
            "is_protagonist": bool(r["is_protagonist"]),
            "identity_brief": (r["identity"] or "")[:120],
        }
        for r in char_rows
    ]

    # 关系 polarity 分布(暗示叙述基调)
    rel_rows = conn.execute(
        "SELECT polarity FROM relationships WHERE project_id=?",
        (project_id,),
    ).fetchall()
    polarity_summary = {"positive": 0, "negative": 0, "neutral": 0, "unset": 0}
    for r in rel_rows:
        p = r["polarity"] or "unset"
        polarity_summary[p] = polarity_summary.get(p, 0) + 1

    return {
        "world_baseline": baseline,
        "existing_narrative_pov": existing_pov,  # 用户已填的人称(LLM 可参考)
        "characters_brief": characters_brief,
        "relationship_polarity_summary": polarity_summary,
    }


def infer_narrative_view_for_project(
    conn: sqlite3.Connection, project_id: str,
) -> dict:
    """LLM 推断视角扩展三件套.

    mode-aware:
      - 有 upload:基于头中尾采样 + characters(用代词分布 / 内心独白集中度判)
      - 无 upload(初始态):基于 world_baseline + characters(主角) + 关系 polarity 概貌
        (材料稀薄,LLM 会标注信心;narrative_pov 已填时优先回收作为 distance 暗示)

    失败兜底返 _FALLBACK_RESULT,绝不抛.
    """
    try:
        full_text = _get_full_text_for_project(conn, project_id)
        has_upload = bool(full_text and len(full_text.strip()) >= 500)
        characters = _get_characters_for_view(conn, project_id)

        if has_upload:
            samples = _sample_text(full_text)
            user_input = {
                "mode_source": "upload",
                **samples,
                "characters": [
                    {
                        "name": c["name"],
                        "aliases": c["aliases"],
                        "is_protagonist": c["is_protagonist"],
                    }
                    for c in characters
                ],
            }
        else:
            # 初始态:角色 < 3 时不推(材料过少)
            if len(characters) < 3:
                return {
                    **_FALLBACK_RESULT,
                    "reasoning": "材料不足,请先建至少 3 个角色再点 AI 推断(或在中/末尾态上传作品)",
                }
            initial_ctx = _build_initial_mode_context_for_view(conn, project_id)
            user_input = {
                "mode_source": "initial",
                **initial_ctx,
            }

        system_prompt = _load_prompt()

        result, _usage = call_llm_json(
            system_prompt=system_prompt,
            user_input=user_input,
            max_tokens=1200,
            temperature=0.4,
            retries=2,
            timeout=60.0,
        )
        validated = _validate_llm_result(result, characters)
        logger.info(
            f"narrative_view_inferer: project {project_id} → "
            f"focus={validated['narrative_focus_character_name']} (id={validated['narrative_focus_character_id']}) / "
            f"reliability={validated['narrator_reliability']} / "
            f"distance={validated['narrative_distance']}"
        )
        return validated

    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"narrative_view_inferer: LLM call/parse failed for {project_id}: {e}")
        return _FALLBACK_RESULT
    except ValueError as e:
        logger.warning(f"narrative_view_inferer: LLM result invalid for {project_id}: {e}")
        return _FALLBACK_RESULT
    except Exception as e:  # noqa: BLE001
        logger.warning(f"narrative_view_inferer: unexpected error for {project_id}: {e}")
        return _FALLBACK_RESULT


def cache_narrative_view_to_project(
    conn: sqlite3.Connection, project_id: str, result: dict,
) -> None:
    """写入 projects 表 3 字段(用户主动按钮触发,不懒触发)."""
    execute(
        conn,
        "UPDATE projects SET "
        "  narrative_focus_character_id = ?, "
        "  narrator_reliability = ?, "
        "  narrative_distance = ?, "
        "  updated_at = ? "
        "WHERE id = ?",
        (
            result["narrative_focus_character_id"],
            result["narrator_reliability"],
            result["narrative_distance"],
            iso_now(),
            project_id,
        ),
    )
    conn.commit()


__all__ = [
    "infer_narrative_view_for_project",
    "cache_narrative_view_to_project",
]
