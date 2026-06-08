"""gender_audit — 角色性别 / 亲属关系审计 agent(P0M.1,2026-05-24).

起源:Gemini 评测雪国实战发现 — build_graph LLM 在 description 里脑补性别 / 亲属关系。
经典 case:"师傅 = 行男的父亲" — 原文实际是"行男的母亲"(中文"师傅"中性,LLM 训练
数据男性 prior 高 → 默认填父亲)。

设计哲学(LLM-first,反规则膨胀):
  不堆性别词字典 / 不做硬规则替换 — 改让 **LLM 二次审计**:
  - 扫所有 PERSON description 含性别词 / 亲属称谓
  - 拉该角色的原文采样(类似 life_status_inferer 的分布采样)
  - LLM 看采样 vs description,verify 性别/亲属是否一致
  - 错 → 给出修正(只改性别/亲属字眼,保留其他信息)
  - 对 ≈ 5 元/项目(几十角色,每个一次 LLM),用户已批准成本翻倍

挂载位置:extract done hook 里,在 life_status_inferer 之后跑(都是 description 后审计类)

普适性:任何作品 — 文学 / 科幻 / 武侠 / 古风,只要 description 含性别词都跑

created 2026-05-24 / P0M.1
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Callable, Optional

from app.db import execute, fetch_all, fetch_one
from app.services.llm_client import (
    LlmCallFailed,
    LlmJsonParseFailed,
    call_llm_json,
)
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)

# 触发审计的性别 / 亲属关键词(只扫含这些词的 description,跳过没必要审的)
_GENDER_TRIGGER_KEYWORDS: tuple[str, ...] = (
    # 性别词
    "他", "她", "男", "女",
    # 亲属角色(高频出现)
    "父亲", "母亲", "爸爸", "妈妈",
    "丈夫", "妻子", "老婆", "老公",
    "儿子", "女儿",
    "哥哥", "姐姐", "弟弟", "妹妹",
    "爷爷", "奶奶", "外公", "外婆",
    "叔叔", "伯伯", "姑姑", "舅舅", "阿姨",
    # 中性词雷区(易脑补)— 必须审
    "师傅", "掌柜", "医生", "律师", "教授", "老板",
)

# 采样大小
_SAMPLE_WINDOW = 400
_MAX_SAMPLES = 6  # 性别审计不需要那么多段,4-6 段就够


def _load_prompt() -> str:
    p = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "prompts" / "gender_audit.md"
    )
    return p.read_text(encoding="utf-8")


def _needs_audit(description: str) -> bool:
    """判 description 是否含触发词需要审计."""
    if not description:
        return False
    return any(kw in description for kw in _GENDER_TRIGGER_KEYWORDS)


def _build_samples(
    full_text: str, character_name: str, aliases: list[str],
) -> list[str]:
    """从全文按角色名 + aliases 找出场片段,取 _MAX_SAMPLES 段返回."""
    names_to_search = [character_name] + [
        a for a in (aliases or [])
        if isinstance(a, str) and a.strip() and a != character_name
    ]

    # 找所有出场位置
    positions: set[int] = set()
    for n in names_to_search:
        cursor = 0
        while True:
            pos = full_text.find(n, cursor)
            if pos == -1:
                break
            positions.add(pos)
            cursor = pos + len(n)

    if not positions:
        return []

    sorted_positions = sorted(positions)
    # 等距取样
    if len(sorted_positions) <= _MAX_SAMPLES:
        sampled = sorted_positions
    else:
        indices = [
            round(i * (len(sorted_positions) - 1) / (_MAX_SAMPLES - 1))
            for i in range(_MAX_SAMPLES)
        ]
        sampled = [sorted_positions[i] for i in indices]

    samples: list[str] = []
    seen_starts: list[int] = []
    for pos in sampled:
        start = max(0, pos - 100)
        # 防重叠
        if any(abs(start - s) < 100 for s in seen_starts):
            continue
        end = min(len(full_text), pos + _SAMPLE_WINDOW)
        snippet = full_text[start:end].strip()
        if snippet:
            samples.append(snippet)
            seen_starts.append(start)
    return samples


def _validate_llm_result(raw: object) -> Optional[dict]:
    """校验 LLM 输出 — 不合法返 None,合法返结构化 dict."""
    if not isinstance(raw, dict):
        return None
    verdict = raw.get("verdict")
    if verdict not in ("correct", "wrong", "no_evidence"):
        return None
    reasoning = raw.get("reasoning", "") or ""
    if not isinstance(reasoning, str):
        reasoning = ""
    fix_desc = raw.get("fix_description", "") or ""
    if not isinstance(fix_desc, str):
        fix_desc = ""
    return {
        "verdict": verdict,
        "reasoning": reasoning.strip()[:500],
        "fix_description": fix_desc.strip()[:500],
    }


def _load_full_text(conn: sqlite3.Connection, project_id: str) -> Optional[str]:
    """拉项目所有 ready upload 的全文(同 life_status_inferer 的做法)."""
    from app.config import settings
    from app.services import file_parser

    rows = fetch_all(
        conn,
        "SELECT storage_path, mime_type FROM uploads "
        "WHERE project_id=? AND state='ready' "
        "ORDER BY uploaded_at ASC",
        (project_id,),
    )
    if not rows:
        return None

    parts: list[str] = []
    for r in rows:
        try:
            abs_path = settings.uploads_abs_dir / r["storage_path"]
            result = file_parser.parse_file(abs_path, r["mime_type"])
            if result.success and result.text:
                parts.append(result.text)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"gender_audit: parse {r['storage_path']}: {e}")

    return "\n\n".join(parts) if parts else None


def audit_one_character(
    conn: sqlite3.Connection,
    project_id: str,
    character_id: str,
    character_name: str,
    description: str,
    aliases: list[str],
    full_text: Optional[str] = None,
) -> dict:
    """对单角色跑性别 / 亲属审计 LLM."""
    if not _needs_audit(description):
        return {
            "verdict": "skipped",
            "reasoning": "description 无性别 / 亲属触发词,跳过",
            "fix_description": "",
            "error": None,
        }

    if full_text is None:
        full_text = _load_full_text(conn, project_id)
    if not full_text:
        return {
            "verdict": "no_evidence",
            "reasoning": "无可用原作,无法审计",
            "fix_description": "",
            "error": "no_upload",
        }

    samples = _build_samples(full_text, character_name, aliases)
    if not samples:
        return {
            "verdict": "no_evidence",
            "reasoning": f"{character_name} 在原文中未出现,无法审计",
            "fix_description": "",
            "error": None,
        }

    try:
        system_prompt = _load_prompt()
    except Exception as e:  # noqa: BLE001
        return {
            "verdict": "skipped",
            "reasoning": f"prompt 加载失败:{e}",
            "fix_description": "",
            "error": f"prompt_load: {type(e).__name__}",
        }

    user_input = {
        "character_name": character_name,
        "current_description": description,
        "samples": samples,
    }

    try:
        parsed, _usage = call_llm_json(
            system_prompt=system_prompt,
            user_input=user_input,
            max_tokens=600,
            temperature=0.3,
            retries=2,
            timeout=45.0,
        )
    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"gender_audit: LLM 失败 for {character_name}: {e}")
        return {
            "verdict": "skipped",
            "reasoning": f"LLM 调用失败:{type(e).__name__}",
            "fix_description": "",
            "error": f"llm_failed: {type(e).__name__}",
        }

    validated = _validate_llm_result(parsed)
    if validated is None:
        logger.warning(f"gender_audit: 非法输出 for {character_name}: {parsed}")
        return {
            "verdict": "skipped",
            "reasoning": "LLM 输出格式不合法",
            "fix_description": "",
            "error": "invalid_output",
        }
    return {**validated, "error": None}


def audit_all_characters_in_project(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    emit_progress: Optional[Callable[[str, str, int, int], None]] = None,
) -> dict:
    """批量审计项目所有 PERSON 的性别 / 亲属描述准确性.

    Args:
      emit_progress: 每完成 1 个角色触发的回调
                     (name, verdict, current_idx, total) -> None

    Returns: {audited, fixed, skipped, errors, results: [...]}
    """
    rows = fetch_all(
        conn,
        "SELECT id, name, identity, aliases_json FROM characters "
        "WHERE project_id=?",
        (project_id,),
    )
    if not rows:
        return {"audited": 0, "fixed": 0, "skipped": 0, "errors": 0, "results": []}

    full_text = _load_full_text(conn, project_id)
    if not full_text:
        return {
            "audited": 0, "fixed": 0, "skipped": len(rows), "errors": 0,
            "results": [], "warning": "no_upload",
        }

    import json
    audited = 0
    fixed = 0
    skipped = 0
    errors = 0
    results: list[dict] = []
    total = len(rows)

    for idx, r in enumerate(rows):
        char_id = r["id"]
        name = r["name"]
        description = r["identity"] or ""
        try:
            aliases = json.loads(r["aliases_json"] or "[]")
            if not isinstance(aliases, list):
                aliases = []
        except (TypeError, ValueError):
            aliases = []

        try:
            result = audit_one_character(
                conn, project_id, char_id, name, description, aliases,
                full_text=full_text,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"gender_audit: {name} 异常: {e}", exc_info=True)
            errors += 1
            if emit_progress:
                try:
                    emit_progress(name, "error", idx + 1, total)
                except Exception:  # noqa: BLE001
                    pass
            continue

        verdict = result.get("verdict")
        if verdict == "skipped":
            skipped += 1
        elif verdict == "wrong" and result.get("fix_description"):
            # 修正 description(只 wrong + 有 fix 才更新)
            try:
                execute(
                    conn,
                    "UPDATE characters SET identity=?, updated_at=? WHERE id=?",
                    (result["fix_description"], iso_now(), char_id),
                )
                conn.commit()
                fixed += 1
                audited += 1
                logger.info(
                    f"gender_audit: 修正 {name}: '{description}' → "
                    f"'{result['fix_description']}'"
                )
            except Exception as e:  # noqa: BLE001
                logger.error(f"gender_audit: 写库失败 for {name}: {e}")
                errors += 1
        else:
            # correct / no_evidence
            audited += 1

        results.append({
            "character_id": char_id,
            "name": name,
            **result,
        })

        if emit_progress:
            try:
                emit_progress(name, verdict or "skipped", idx + 1, total)
            except Exception:  # noqa: BLE001
                pass

    logger.info(
        f"gender_audit: project {project_id} "
        f"audited={audited} fixed={fixed} skipped={skipped} errors={errors}"
    )
    return {
        "audited": audited,
        "fixed": fixed,
        "skipped": skipped,
        "errors": errors,
        "results": results,
    }


__all__ = ["audit_one_character", "audit_all_characters_in_project"]
