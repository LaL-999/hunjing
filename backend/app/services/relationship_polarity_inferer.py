"""SP-7.1(2026-05-30)— LLM 推断项目所有关系的 polarity.

用户痛点:
SP-7 polarity 需要用户在 RelationshipsTimelineSection 逐个点击 chip 循环切换.
项目关系多时(20+)手动设极性是体力活,且用户不一定记得清每对关系是 positive 还是 negative.

方案:
一次性给项目所有 relationships 推断 polarity.
LLM 输出三元组列表 [{source_name, target_name, type, polarity, confidence}],
service 做三元组 → relationship_id 映射后批量 UPDATE.

mode-aware:
- 有 upload(中/末/漫):基于头中尾采样 + 关系网 + 角色互动语气
- 无 upload(初始):基于关系 type + description + polarity 已填的提示

overwrite 双模式:
- False(默认):仅填 polarity 为 NULL 的关系(保留用户已手动 chip 切换的值)
- True:覆盖所有关系(用户选择"重置极性")

普适性:type 是强信号(夫妻/朋友 → positive / 宿敌/敌对 → negative / 师生/同事 → neutral),
description 微调,upload 文本辅助消歧(如"前夫"应判 negative).
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
_VALID_POLARITY = ("positive", "negative", "neutral")

_FALLBACK_RESULT = {
    "polarity_decisions": [],
    "reasoning": "AI 推断失败,请手动 chip 切换 polarity",
}


def _load_prompt() -> str:
    return (
        Path(__file__).parent.parent.parent.parent
        / "prompts" / "relationship_polarity_inferer.md"
    ).read_text(encoding="utf-8")


def _sample_text(full_text: str) -> dict[str, str]:
    n = len(full_text)
    sc = _SAMPLE_CHARS
    if n <= sc * 3:
        return {
            "head_excerpt": full_text[:sc],
            "middle_excerpt": full_text[sc : sc * 2] if n > sc else "",
            "tail_excerpt": full_text[-sc:] if n > sc * 2 else "",
        }
    return {
        "head_excerpt": full_text[:sc],
        "middle_excerpt": full_text[(n - sc) // 2 : (n - sc) // 2 + sc],
        "tail_excerpt": full_text[-sc:],
    }


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
    pr = file_parser.parse_file(abs_path, row["mime_type"])
    if not pr.success or not pr.text:
        return None
    return pr.text


def _get_relationships_with_names(
    conn: sqlite3.Connection, project_id: str,
) -> list[dict[str, Any]]:
    """拉项目所有关系含 source/target 角色名 + 现有 polarity."""
    rows = conn.execute(
        "SELECT r.id, r.source_id, r.target_id, r.type, r.description, "
        "       r.strength, r.polarity, "
        "       sc.name AS source_name, tc.name AS target_name "
        "FROM relationships r "
        "JOIN characters sc ON sc.id = r.source_id "
        "JOIN characters tc ON tc.id = r.target_id "
        "WHERE r.project_id=? "
        "ORDER BY r.created_at ASC",
        (project_id,),
    ).fetchall()
    return [
        {
            "id": r["id"],
            "source_name": r["source_name"],
            "target_name": r["target_name"],
            "type": r["type"] or "",
            "description": (r["description"] or "")[:200],
            "strength": r["strength"] or "moderate",
            "polarity": r["polarity"],  # 可能 None / positive / negative / neutral
        }
        for r in rows
    ]


def _validate_llm_result(raw: Any) -> dict:
    """LLM 应返 {polarity_decisions: [...], reasoning: ...}."""
    if not isinstance(raw, dict):
        raise ValueError(f"LLM result not a dict: {type(raw)}")
    decisions_raw = raw.get("polarity_decisions", [])
    if not isinstance(decisions_raw, list):
        decisions_raw = []
    decisions: list[dict[str, Any]] = []
    for d in decisions_raw:
        if not isinstance(d, dict):
            continue
        src = d.get("source_name", "")
        tgt = d.get("target_name", "")
        typ = d.get("type", "")
        pol = d.get("polarity")
        if not isinstance(src, str) or not src.strip():
            continue
        if not isinstance(tgt, str) or not tgt.strip():
            continue
        if pol not in _VALID_POLARITY:
            continue
        decisions.append({
            "source_name": src.strip()[:50],
            "target_name": tgt.strip()[:50],
            "type": (typ if isinstance(typ, str) else "")[:50],
            "polarity": pol,
        })
    reasoning = raw.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = ""
    return {
        "polarity_decisions": decisions,
        "reasoning": reasoning.strip()[:500],
    }


def infer_polarity_for_project(
    conn: sqlite3.Connection, project_id: str,
) -> dict:
    """LLM 推断项目所有关系 polarity.失败返 _FALLBACK_RESULT."""
    try:
        relationships = _get_relationships_with_names(conn, project_id)
        if not relationships:
            return {
                **_FALLBACK_RESULT,
                "reasoning": "项目无关系,先在 关系 section 建几条关系再 AI 推断",
            }

        full_text = _get_full_text_for_project(conn, project_id)
        has_upload = bool(full_text and len(full_text.strip()) >= 500)

        rel_list_for_llm = [
            {
                "source_name": r["source_name"],
                "target_name": r["target_name"],
                "type": r["type"],
                "description": r["description"],
                "current_polarity": r["polarity"] or "",
            }
            for r in relationships
        ]

        if has_upload:
            samples = _sample_text(full_text)
            user_input = {
                "mode_source": "upload",
                **samples,
                "relationships": rel_list_for_llm,
            }
        else:
            user_input = {
                "mode_source": "initial",
                "relationships": rel_list_for_llm,
            }

        system_prompt = _load_prompt()
        result, _usage = call_llm_json(
            system_prompt=system_prompt,
            user_input=user_input,
            max_tokens=2000,
            temperature=0.3,  # polarity 判断要稳定,降温
            retries=2,
            timeout=90.0,
        )
        validated = _validate_llm_result(result)
        logger.info(
            f"relationship_polarity_inferer: project {project_id} → "
            f"{len(validated['polarity_decisions'])} decisions for "
            f"{len(relationships)} relationships"
        )
        return validated

    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"relationship_polarity_inferer: LLM call/parse failed for {project_id}: {e}")
        return _FALLBACK_RESULT
    except ValueError as e:
        logger.warning(f"relationship_polarity_inferer: invalid for {project_id}: {e}")
        return _FALLBACK_RESULT
    except Exception as e:  # noqa: BLE001
        logger.warning(f"relationship_polarity_inferer: unexpected for {project_id}: {e}")
        return _FALLBACK_RESULT


def _match_relationship(
    decision: dict[str, Any],
    relationships: list[dict[str, Any]],
) -> Optional[str]:
    """LLM decision(source/target/type)→ relationship_id 三元组匹配.

    匹配优先级:
      1. source_name + target_name + type 全匹配
      2. source_name + target_name 匹配(忽略 type — LLM 可能改了 type 字符串)
      3. None(无匹配)
    """
    src = decision.get("source_name", "")
    tgt = decision.get("target_name", "")
    typ = decision.get("type", "")
    if not src or not tgt:
        return None

    # Level 1: 三元组全匹配
    for r in relationships:
        if r["source_name"] == src and r["target_name"] == tgt and r["type"] == typ:
            return str(r["id"])

    # Level 2: source+target 匹配
    candidates = [
        r for r in relationships
        if r["source_name"] == src and r["target_name"] == tgt
    ]
    if len(candidates) == 1:
        return str(candidates[0]["id"])

    return None


def cache_polarity_to_project(
    conn: sqlite3.Connection,
    project_id: str,
    result: dict,
    *,
    overwrite: bool = False,
) -> dict:
    """把 LLM 决策写入 relationships.polarity.

    overwrite=False:仅更新 polarity 当前为 NULL 的关系
    overwrite=True:全部更新(包括用户手动 chip 切过的)

    返回 {applied: bool, updated_count: int, skipped_count: int, no_match_count: int}
    """
    decisions = result.get("polarity_decisions") or []
    if not decisions:
        return {
            "applied": False,
            "updated_count": 0,
            "skipped_count": 0,
            "no_match_count": 0,
        }

    relationships = _get_relationships_with_names(conn, project_id)
    rel_by_id = {r["id"]: r for r in relationships}

    updated = 0
    skipped = 0
    no_match = 0
    now = iso_now()
    for d in decisions:
        rid = _match_relationship(d, relationships)
        if not rid:
            no_match += 1
            continue
        cur = rel_by_id.get(rid)
        if not cur:
            no_match += 1
            continue
        # 跳过逻辑:overwrite=False + 当前已有非 NULL polarity → 跳过
        if not overwrite and cur["polarity"] in _VALID_POLARITY:
            skipped += 1
            continue
        new_pol = d["polarity"]
        if cur["polarity"] == new_pol:
            # 已经一样了不算 update
            skipped += 1
            continue
        execute(
            conn,
            "UPDATE relationships SET polarity=? WHERE id=?",
            (new_pol, rid),
        )
        updated += 1
    conn.commit()

    return {
        "applied": updated > 0,
        "updated_count": updated,
        "skipped_count": skipped,
        "no_match_count": no_match,
    }


__all__ = [
    "infer_polarity_for_project",
    "cache_polarity_to_project",
]
