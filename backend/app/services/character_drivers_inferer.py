"""SP-2.1(2026-05-29)— LLM 推断单角色驱动五件套.

输入:
- 单 character 已填字段(identity / personality / quotes / no_go_list)
- 原作头/中/尾采样(为 LLM 提供该角色的实际行为证据)

输出:
- surface_goal(表层目标)
- deep_need(深层渴求)
- fatal_blind_spot(致命盲点)
- arc_from_to(弧光,开篇 → 结局)
- secrets[](秘密列表,每条 description)

设计:
- 单角色 inferer(类似 agent_profile_enricher 模式)
- LLM 拿到角色现有档案 + 原作文本上下文,只填**空字段**(已填字段保留用户意志)
- 失败兜底:返 _FALLBACK_RESULT(5 字段全空 + reasoning 解释)
- 用户主动点角色 drawer 内"AI 推断驱动"触发

普适性:基于原作角色实际言行,不靠训练印象.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from app.db import execute, fetch_all, fetch_one
from app.services.llm_client import call_llm_json, LlmCallFailed, LlmJsonParseFailed
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)


_SAMPLE_CHARS = 1500

_FALLBACK_RESULT = {
    "surface_goal": "",
    "deep_need": "",
    "fatal_blind_spot": "",
    "arc_from_to": "",
    "secrets": [],
    "reasoning": "AI 推断失败,请手动填写五件套",
}


def _load_prompt() -> str:
    prompt_path = (
        Path(__file__).parent.parent.parent.parent
        / "prompts" / "character_drivers_inferer.md"
    )
    return prompt_path.read_text(encoding="utf-8")


def _sample_text(full_text: str) -> dict[str, str]:
    n = len(full_text)
    sample_chars = _SAMPLE_CHARS
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


def _validate_llm_result(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"LLM result not a dict: {type(raw)}")

    out: dict[str, Any] = {}
    for key in ("surface_goal", "deep_need", "fatal_blind_spot", "arc_from_to"):
        val = raw.get(key, "")
        if val is None:
            val = ""
        if not isinstance(val, str):
            raise ValueError(f"{key} not a string: {type(val)}")
        out[key] = val.strip()[:300]

    # secrets 应为 list[str | dict];宽容处理
    secrets_raw = raw.get("secrets", [])
    if not isinstance(secrets_raw, list):
        secrets_raw = []
    secrets: list[dict[str, Any]] = []
    for s in secrets_raw[:10]:  # 上限 10
        if isinstance(s, str):
            desc = s.strip()
            if desc:
                secrets.append({"description": desc[:300], "hidden_from": []})
        elif isinstance(s, dict):
            desc = s.get("description", "")
            if isinstance(desc, str) and desc.strip():
                hf = s.get("hidden_from", [])
                if not isinstance(hf, list):
                    hf = []
                hf = [h for h in hf if isinstance(h, str)][:20]
                secrets.append({"description": desc.strip()[:300], "hidden_from": hf})
    out["secrets"] = secrets

    reasoning = raw.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = ""
    out["reasoning"] = reasoning.strip()[:500]
    return out


def _get_related_context_for_character(
    conn: sqlite3.Connection, project_id: str, character_id: str,
) -> dict[str, Any]:
    """初始态:拉该角色的关系网 + 同项目其他角色的简要,辅助 LLM 推断驱动力.

    关系暗示角色处境;其他角色暗示该角色在故事网络中的位置.
    """
    # 关系网(以此角色为 source 或 target)
    rel_rows = conn.execute(
        "SELECT r.source_id, r.target_id, r.type, r.description, r.polarity, "
        "       sc.name AS source_name, tc.name AS target_name "
        "FROM relationships r "
        "JOIN characters sc ON sc.id = r.source_id "
        "JOIN characters tc ON tc.id = r.target_id "
        "WHERE r.project_id=? AND (r.source_id=? OR r.target_id=?) "
        "ORDER BY r.created_at ASC LIMIT 20",
        (project_id, character_id, character_id),
    ).fetchall()
    relationships = [
        {
            "source": r["source_name"],
            "target": r["target_name"],
            "type": r["type"] or "",
            "polarity": r["polarity"] or "",
            "description": (r["description"] or "")[:120],
        }
        for r in rel_rows
    ]

    # 其他角色简要(给 LLM 一种"故事生态")
    other_rows = conn.execute(
        "SELECT name, identity, is_protagonist FROM characters "
        "WHERE project_id=? AND id != ? "
        "ORDER BY is_protagonist DESC, name ASC LIMIT 10",
        (project_id, character_id),
    ).fetchall()
    other_characters = [
        {
            "name": r["name"],
            "is_protagonist": bool(r["is_protagonist"]),
            "identity_brief": (r["identity"] or "")[:80],
        }
        for r in other_rows
    ]

    # 提及此角色的事件
    event_rows = conn.execute(
        "SELECT description, participants, time_anchor FROM events "
        "WHERE project_id=? ORDER BY created_at ASC LIMIT 30",
        (project_id,),
    ).fetchall()
    involved_events: list[dict[str, str]] = []
    for r in event_rows:
        try:
            participants = json.loads(r["participants"] or "[]")
        except (json.JSONDecodeError, TypeError):
            participants = []
        if character_id in participants:
            involved_events.append({
                "description": (r["description"] or "")[:200],
                "time_anchor": r["time_anchor"] or "",
            })

    return {
        "relationships": relationships,
        "other_characters": other_characters,
        "involved_events": involved_events,
    }


def infer_drivers_for_character(
    conn: sqlite3.Connection, character_id: str,
) -> dict:
    """LLM 推断单角色驱动五件套.

    mode-aware:
      - 有 upload(中/末尾/漫画态):用头中尾采样 + 角色档案
      - 无 upload(初始态):用角色档案 + 关系网 + 其他角色 + 参与事件

    返回 dict:{surface_goal, deep_need, fatal_blind_spot, arc_from_to, secrets, reasoning}
    失败任何阶段都返 _FALLBACK_RESULT,绝不抛.
    """
    try:
        char_row = fetch_one(
            conn,
            "SELECT project_id, name, identity, personality, quotes, no_go_list, aliases_json "
            "FROM characters WHERE id=?",
            (character_id,),
        )
        if not char_row:
            return {
                **_FALLBACK_RESULT,
                "reasoning": "角色不存在",
            }

        project_id = char_row["project_id"]
        full_text = _get_full_text_for_project(conn, project_id)
        has_upload = bool(full_text and len(full_text.strip()) >= 500)

        # 角色现有档案(两种形态都用)
        try:
            quotes = json.loads(char_row["quotes"] or "[]")
        except (json.JSONDecodeError, TypeError):
            quotes = []
        try:
            no_go = json.loads(char_row["no_go_list"] or "[]")
        except (json.JSONDecodeError, TypeError):
            no_go = []
        try:
            aliases = json.loads(char_row["aliases_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            aliases = []
        character_profile = {
            "name": char_row["name"],
            "aliases": aliases,
            "identity": (char_row["identity"] or "")[:500],
            "personality": (char_row["personality"] or "")[:500],
            "quotes": quotes[:8],
            "no_go_list": no_go[:8],
        }

        if has_upload:
            samples = _sample_text(full_text)
            user_input = {
                "mode_source": "upload",
                **samples,
                "character": character_profile,
            }
        else:
            # 初始态:除了角色档案,加上关系网 + 其他角色 + 参与事件
            # 至少角色档案要有 identity 或 personality 或 quotes 之一
            has_min_material = (
                bool(character_profile["identity"].strip())
                or bool(character_profile["personality"].strip())
                or len(character_profile["quotes"]) > 0
            )
            if not has_min_material:
                return {
                    **_FALLBACK_RESULT,
                    "reasoning": "角色材料过少(identity / personality / quotes 至少填一项)再点 AI 推断",
                }
            related = _get_related_context_for_character(conn, project_id, character_id)
            user_input = {
                "mode_source": "initial",
                "character": character_profile,
                **related,
            }

        system_prompt = _load_prompt()

        result, _usage = call_llm_json(
            system_prompt=system_prompt,
            user_input=user_input,
            max_tokens=1500,
            temperature=0.6,  # 驱动力推断需要文学想象,略提温
            retries=2,
            timeout=90.0,
        )
        validated = _validate_llm_result(result)
        logger.info(
            f"character_drivers_inferer: char {character_id} ({char_row['name']}) → "
            f"sg={len(validated['surface_goal'])}c / "
            f"dn={len(validated['deep_need'])}c / "
            f"secrets={len(validated['secrets'])}"
        )
        return validated

    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"character_drivers_inferer: LLM call/parse failed for {character_id}: {e}")
        return _FALLBACK_RESULT
    except ValueError as e:
        logger.warning(f"character_drivers_inferer: LLM result invalid for {character_id}: {e}")
        return _FALLBACK_RESULT
    except Exception as e:  # noqa: BLE001
        logger.warning(f"character_drivers_inferer: unexpected error for {character_id}: {e}")
        return _FALLBACK_RESULT


def cache_drivers_to_character(
    conn: sqlite3.Connection,
    character_id: str,
    result: dict,
    *,
    overwrite: bool = False,
) -> None:
    """把推断结果写入 characters 表.

    overwrite=False(默认):仅写入当前为空的字段,保留用户已填值(类似 enricher 协议).
    overwrite=True:用户主动选择"覆盖",AI 推断全替换.
    """
    char_row = fetch_one(
        conn,
        "SELECT surface_goal, deep_need, fatal_blind_spot, arc_from_to, secret_json "
        "FROM characters WHERE id=?",
        (character_id,),
    )
    if not char_row:
        return

    def _take(field: str, new_val: str) -> Optional[str]:
        """覆盖模式直接取新值;否则只在旧值空时填."""
        old_val = (char_row[field] or "").strip()
        if overwrite:
            return new_val or None
        if old_val:
            return old_val
        return new_val or None

    surface_goal = _take("surface_goal", result["surface_goal"])
    deep_need = _take("deep_need", result["deep_need"])
    fatal_blind_spot = _take("fatal_blind_spot", result["fatal_blind_spot"])
    arc_from_to = _take("arc_from_to", result["arc_from_to"])

    # secrets 单独处理:overwrite=True 覆盖,否则仅在原 secret_json 空 / 空列表时填
    new_secrets = result.get("secrets") or []
    secrets_json: Optional[str] = None
    if overwrite:
        secrets_json = json.dumps(new_secrets, ensure_ascii=False) if new_secrets else None
    else:
        try:
            existing = json.loads(char_row["secret_json"] or "null")
        except (json.JSONDecodeError, TypeError):
            existing = None
        if existing in (None, []) and new_secrets:
            secrets_json = json.dumps(new_secrets, ensure_ascii=False)
        elif existing is not None:
            # 保留用户已填
            secrets_json = json.dumps(existing, ensure_ascii=False) if existing else None

    execute(
        conn,
        "UPDATE characters SET "
        "  surface_goal = ?, "
        "  deep_need = ?, "
        "  fatal_blind_spot = ?, "
        "  arc_from_to = ?, "
        "  secret_json = ?, "
        "  updated_at = ? "
        "WHERE id = ?",
        (
            surface_goal,
            deep_need,
            fatal_blind_spot,
            arc_from_to,
            secrets_json,
            iso_now(),
            character_id,
        ),
    )
    conn.commit()


__all__ = [
    "infer_drivers_for_character",
    "cache_drivers_to_character",
]
