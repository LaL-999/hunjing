"""SP-1.5(2026-05-29)— LLM 推断故事内核三件套.

用户痛点:用户不一定知道作品的"核心戏剧问题 / 主题 / 终点情绪"怎么填,
即便看完整本书也未必能简练成句.续作 AI 若没有这三个北极星 →
- 提前泄气(没目标弧)
- 偏离主题(漂到无关支线)
- 漂移终点(选错情绪基调)

方案:基于已上传原作头中尾采样 + 已抽角色,LLM 推断三个字段,
       用户审阅 / 修改 / 保存.

复用 pacing_inferer 模式:
- 同样的采样函数 _sample_text(_SAMPLE_CHARS=1800,比 pacing 略大,内核要更深)
- 同样的失败兜底返回(_FALLBACK_RESULT 三字段空 + 解释)
- 同样的 _validate_llm_result 校验
- 触发方式不是懒触发,而是用户主动点"AI 推断"按钮(infer-by-demand)

普适性:不依赖训练数据中作品的预存印象,基于实际文本采样.
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


# 采样大小(头/中/尾各 1800,故事内核比节奏要更深)
_SAMPLE_CHARS = 1800

_FALLBACK_RESULT = {
    "core_dramatic_question": "",
    "theme": "",
    "ending_direction": "",
    "reasoning": "AI 推断失败,请手动填写三件套",
}


def _load_prompt() -> str:
    prompt_path = (
        Path(__file__).parent.parent.parent.parent
        / "prompts" / "story_core_inferer.md"
    )
    return prompt_path.read_text(encoding="utf-8")


def _sample_text(full_text: str, sample_chars: int = _SAMPLE_CHARS) -> dict[str, str]:
    """切头/中/尾三段(对齐 pacing_inferer)."""
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
    """复用 pacing_inferer 的策略:最近 ready upload 解析后文本."""
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


def _get_characters_brief(
    conn: sqlite3.Connection, project_id: str,
) -> list[dict[str, str]]:
    """拉已抽角色简要(name + identity 头 80 字),给 LLM 上下文."""
    rows = fetch_one(
        conn,
        "SELECT COUNT(*) AS cnt FROM characters WHERE project_id=?",
        (project_id,),
    )
    if not rows or rows["cnt"] == 0:
        return []
    char_rows = conn.execute(
        "SELECT name, identity FROM characters WHERE project_id=? "
        "ORDER BY is_protagonist DESC, name ASC LIMIT 8",
        (project_id,),
    ).fetchall()
    return [
        {
            "name": r["name"],
            "identity_brief": (r["identity"] or "")[:80],
        }
        for r in char_rows
    ]


def _build_initial_mode_context(
    conn: sqlite3.Connection, project_id: str,
) -> dict[str, Any]:
    """初始态上下文(无 upload):用 world_baseline + characters 详细 + relationships + events.

    用户已填的 angle 越多,LLM 推断越准.最低 ≥ 3 个 character 才尝试推断.
    """
    proj_row = fetch_one(
        conn,
        "SELECT world_baseline_json FROM projects WHERE id=?",
        (project_id,),
    )
    baseline: dict[str, str] = {}
    if proj_row and proj_row["world_baseline_json"]:
        try:
            parsed = json.loads(proj_row["world_baseline_json"])
            if isinstance(parsed, dict):
                baseline = {
                    k: str(v)[:200] for k, v in parsed.items()
                    if isinstance(v, str) and v.strip()
                }
        except (json.JSONDecodeError, TypeError):
            pass

    # 角色详细(initial 态用户填的字段都给 LLM)
    char_rows = conn.execute(
        "SELECT name, identity, personality, quotes, no_go_list, is_protagonist, "
        " surface_goal, deep_need, arc_from_to "
        "FROM characters WHERE project_id=? "
        "ORDER BY is_protagonist DESC, name ASC LIMIT 12",
        (project_id,),
    ).fetchall()
    characters_detailed: list[dict[str, Any]] = []
    for r in char_rows:
        try:
            quotes = json.loads(r["quotes"] or "[]")[:5]
        except (json.JSONDecodeError, TypeError):
            quotes = []
        try:
            no_go = json.loads(r["no_go_list"] or "[]")[:5]
        except (json.JSONDecodeError, TypeError):
            no_go = []
        c_dict = {
            "name": r["name"],
            "is_protagonist": bool(r["is_protagonist"]),
            "identity": (r["identity"] or "")[:200],
            "personality": (r["personality"] or "")[:200],
            "quotes": quotes,
            "no_go_list": no_go,
        }
        # 只在用户已填时透出(避免空字段噪声)
        if (r["surface_goal"] or "").strip():
            c_dict["surface_goal"] = (r["surface_goal"] or "")[:200]
        if (r["deep_need"] or "").strip():
            c_dict["deep_need"] = (r["deep_need"] or "")[:200]
        if (r["arc_from_to"] or "").strip():
            c_dict["arc_from_to"] = (r["arc_from_to"] or "")[:200]
        characters_detailed.append(c_dict)

    # 关系简要
    rel_rows = conn.execute(
        "SELECT source_id, target_id, type, description, polarity "
        "FROM relationships WHERE project_id=? "
        "ORDER BY created_at ASC LIMIT 30",
        (project_id,),
    ).fetchall()
    name_by_id: dict[str, str] = {}
    for r in char_rows:
        # 反查 id 需要再查一次;为简化我们用 name 字段(同名字段会冲突,但初始态角色少,可接受)
        pass
    char_id_rows = conn.execute(
        "SELECT id, name FROM characters WHERE project_id=?",
        (project_id,),
    ).fetchall()
    for r in char_id_rows:
        name_by_id[r["id"]] = r["name"]
    relationships: list[dict[str, str]] = []
    for r in rel_rows:
        s_name = name_by_id.get(r["source_id"], "?")
        t_name = name_by_id.get(r["target_id"], "?")
        relationships.append({
            "source": s_name,
            "target": t_name,
            "type": r["type"] or "",
            "polarity": r["polarity"] or "",
            "description": (r["description"] or "")[:120],
        })

    # 事件(初始态用户填的"会发生的"事件)
    event_rows = conn.execute(
        "SELECT description, time_anchor FROM events WHERE project_id=? "
        "ORDER BY created_at ASC LIMIT 20",
        (project_id,),
    ).fetchall()
    events = [
        {
            "description": (r["description"] or "")[:200],
            "time_anchor": r["time_anchor"] or "",
        }
        for r in event_rows
    ]

    return {
        "world_baseline": baseline,
        "characters_detailed": characters_detailed,
        "relationships": relationships,
        "events": events,
    }


def _validate_llm_result(raw: Any) -> dict:
    """校验 LLM 返回 — 三字段必须存在(可空字符串)."""
    if not isinstance(raw, dict):
        raise ValueError(f"LLM result not a dict: {type(raw)}")
    out: dict[str, str] = {}
    for key in ("core_dramatic_question", "theme", "ending_direction"):
        val = raw.get(key, "")
        if val is None:
            val = ""
        if not isinstance(val, str):
            raise ValueError(f"{key} not a string: {type(val)}")
        # 长度上限对齐 Pydantic schema(300 / 200 / 300)
        max_len = 200 if key == "theme" else 300
        out[key] = val.strip()[:max_len]
    reasoning = raw.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = ""
    out["reasoning"] = reasoning.strip()[:500]
    return out


def infer_story_core_for_project(
    conn: sqlite3.Connection, project_id: str,
) -> dict:
    """LLM 推断故事内核三件套.

    mode-aware 双路径:
      - 有 upload(中/末尾/漫画态):用头中尾采样 + characters 简要
      - 无 upload(初始态):用 world_baseline + characters_detailed + relationships + events

    返回 dict:{core_dramatic_question, theme, ending_direction, reasoning}
    失败任何阶段都返 _FALLBACK_RESULT,绝不抛.
    """
    try:
        full_text = _get_full_text_for_project(conn, project_id)
        has_upload = bool(full_text and len(full_text.strip()) >= 500)

        if has_upload:
            # 中/末尾/漫画态:基于原作采样
            samples = _sample_text(full_text)
            characters = _get_characters_brief(conn, project_id)
            user_input = {
                "mode_source": "upload",
                **samples,
                "characters": characters,
            }
        else:
            # 初始态:基于用户手填的字段
            initial_ctx = _build_initial_mode_context(conn, project_id)
            # 至少要有 3 个角色才推断,否则材料不足
            if len(initial_ctx["characters_detailed"]) < 3:
                return {
                    **_FALLBACK_RESULT,
                    "reasoning": "材料不足,请先建至少 3 个角色再点 AI 推断(或在中/末尾态上传作品)",
                }
            user_input = {
                "mode_source": "initial",
                **initial_ctx,
            }

        system_prompt = _load_prompt()

        result, _usage = call_llm_json(
            system_prompt=system_prompt,
            user_input=user_input,
            max_tokens=1500,
            temperature=0.5,
            retries=2,
            timeout=90.0,
        )
        validated = _validate_llm_result(result)
        logger.info(
            f"story_core_inferer: project {project_id} → "
            f"q={len(validated['core_dramatic_question'])}c / "
            f"theme={len(validated['theme'])}c / "
            f"end={len(validated['ending_direction'])}c"
        )
        return validated

    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"story_core_inferer: LLM call/parse failed for {project_id}: {e}")
        return _FALLBACK_RESULT
    except ValueError as e:
        logger.warning(f"story_core_inferer: LLM result invalid for {project_id}: {e}")
        return _FALLBACK_RESULT
    except Exception as e:  # noqa: BLE001
        logger.warning(f"story_core_inferer: unexpected error for {project_id}: {e}")
        return _FALLBACK_RESULT


def cache_story_core_to_project(
    conn: sqlite3.Connection,
    project_id: str,
    result: dict,
) -> None:
    """把推断结果写入 projects 表 3 字段(覆盖现有值).

    用户后续可手动编辑 — 此函数只在用户主动点"AI 推断"按钮后调用,
    不在 simulation 创建时懒触发(因为是创作意图层,用户应该有主动权).
    """
    execute(
        conn,
        "UPDATE projects SET "
        "  core_dramatic_question = ?, "
        "  theme = ?, "
        "  ending_direction = ?, "
        "  updated_at = ? "
        "WHERE id = ?",
        (
            result["core_dramatic_question"] or None,
            result["theme"] or None,
            result["ending_direction"] or None,
            iso_now(),
            project_id,
        ),
    )
    conn.commit()


__all__ = [
    "infer_story_core_for_project",
    "cache_story_core_to_project",
]
