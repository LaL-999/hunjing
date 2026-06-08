"""SP-3.1(2026-05-29)— LLM 推断知识边界(事实清单 + 角色 known 矩阵).

用户痛点:
- 知识边界手填工作量大(每个事实 + 每个角色 known 矩阵)
- 用户不知道一本书里哪些是"关键事实"(谁知道直子已死 / 谁知道凶手是谁等)

方案:
- LLM 一次输出 facts list(每条 description + first_revealed_scene + is_sensitive)
  + character_knowledge matrix(每条 character_name + fact_index + known_since_scene + confidence)
- service 做 name→id 映射(name 模糊匹配)
- 双模式:overwrite=False(项目已有 facts → 拒绝)/ True(全删全建)

mode-aware:
- 有 upload(中/末/漫):基于头中尾采样推断
- 无 upload(初始):基于用户填的事件 + 关系 + 角色档案推断"心里想的隐含事实"

普适性:不依赖训练数据印象,基于实际输入材料.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from app.db import execute, fetch_all, fetch_one
from app.services import character_knowledge_service as kb
from app.services.llm_client import call_llm_json, LlmCallFailed, LlmJsonParseFailed

logger = logging.getLogger(__name__)


_SAMPLE_CHARS = 1500
_MAX_FACTS = 15  # 单次推断上限,防 LLM 输出爆炸
_MAX_KNOWLEDGE_PER_FACT = 20

_FALLBACK_RESULT: dict[str, Any] = {
    "facts": [],
    "character_knowledge": [],
    "reasoning": "AI 推断失败,请手动录入事实",
}


def _load_prompt() -> str:
    prompt_path = (
        Path(__file__).parent.parent.parent.parent
        / "prompts" / "knowledge_boundaries_inferer.md"
    )
    return prompt_path.read_text(encoding="utf-8")


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


def _get_characters_brief(
    conn: sqlite3.Connection, project_id: str,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, name, aliases_json, is_protagonist, identity "
        "FROM characters WHERE project_id=? "
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
            "identity_brief": (r["identity"] or "")[:100],
        })
    return out


def _build_initial_mode_context(
    conn: sqlite3.Connection, project_id: str,
    characters: list[dict[str, Any]],
) -> dict[str, Any]:
    """初始态:用户填的事件 + 关系 + 角色 → 让 LLM 推断"用户心里的隐含事实"."""
    # 关系
    rel_rows = conn.execute(
        "SELECT r.type, r.description, r.polarity, "
        "       sc.name AS src_name, tc.name AS tgt_name "
        "FROM relationships r "
        "JOIN characters sc ON sc.id = r.source_id "
        "JOIN characters tc ON tc.id = r.target_id "
        "WHERE r.project_id=? ORDER BY r.created_at ASC LIMIT 30",
        (project_id,),
    ).fetchall()
    relationships = [
        {
            "source": r["src_name"],
            "target": r["tgt_name"],
            "type": r["type"] or "",
            "polarity": r["polarity"] or "",
            "description": (r["description"] or "")[:120],
        }
        for r in rel_rows
    ]
    # 事件
    ev_rows = conn.execute(
        "SELECT description, time_anchor, participants FROM events "
        "WHERE project_id=? ORDER BY created_at ASC LIMIT 30",
        (project_id,),
    ).fetchall()
    char_name_by_id = {c["id"]: c["name"] for c in characters}
    events: list[dict[str, Any]] = []
    for r in ev_rows:
        try:
            pids = json.loads(r["participants"] or "[]")
        except (json.JSONDecodeError, TypeError):
            pids = []
        p_names = [char_name_by_id.get(p, "?") for p in pids if p in char_name_by_id]
        events.append({
            "description": (r["description"] or "")[:200],
            "time_anchor": r["time_anchor"] or "",
            "participants": p_names,
        })
    return {"relationships": relationships, "events": events}


def _map_name_to_character_id(
    name: str, characters: list[dict[str, Any]],
) -> Optional[str]:
    """LLM 输出 character_name → id 映射.精确→aliases→子串."""
    if not name:
        return None
    nm = name.strip()
    if not nm:
        return None
    for c in characters:
        if c["name"] == nm:
            return str(c["id"])
    for c in characters:
        if nm in c.get("aliases", []):
            return str(c["id"])
    for c in characters:
        if c["name"] in nm or nm in c["name"]:
            return str(c["id"])
    return None


def _validate_llm_result(raw: Any) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(f"LLM result not a dict: {type(raw)}")

    facts_raw = raw.get("facts", [])
    if not isinstance(facts_raw, list):
        facts_raw = []
    facts: list[dict[str, Any]] = []
    for f in facts_raw[:_MAX_FACTS]:
        if not isinstance(f, dict):
            continue
        desc = f.get("description", "")
        if not isinstance(desc, str) or not desc.strip():
            continue
        scene = f.get("first_revealed_scene")
        if scene is not None and not isinstance(scene, int):
            try:
                scene = int(scene)
            except (TypeError, ValueError):
                scene = None
        if isinstance(scene, int) and scene < 0:
            scene = None
        facts.append({
            "description": desc.strip()[:500],
            "first_revealed_scene": scene,
            "is_sensitive": bool(f.get("is_sensitive", False)),
        })

    ck_raw = raw.get("character_knowledge", [])
    if not isinstance(ck_raw, list):
        ck_raw = []
    knowledge: list[dict[str, Any]] = []
    for k in ck_raw[: _MAX_FACTS * _MAX_KNOWLEDGE_PER_FACT]:
        if not isinstance(k, dict):
            continue
        cname = k.get("character_name", "")
        fidx = k.get("fact_index")
        if not isinstance(cname, str) or not cname.strip():
            continue
        if not isinstance(fidx, int):
            try:
                fidx = int(fidx)
            except (TypeError, ValueError):
                continue
        if fidx < 0 or fidx >= len(facts):
            continue
        confidence = k.get("confidence", "confirmed")
        if confidence not in ("suspected", "confirmed", "wrong"):
            confidence = "confirmed"
        since = k.get("known_since_scene")
        if since is not None and not isinstance(since, int):
            try:
                since = int(since)
            except (TypeError, ValueError):
                since = None
        if isinstance(since, int) and since < 0:
            since = None
        knowledge.append({
            "character_name": cname.strip()[:50],
            "fact_index": fidx,
            "known_since_scene": since,
            "confidence": confidence,
        })

    reasoning = raw.get("reasoning", "")
    if not isinstance(reasoning, str):
        reasoning = ""
    return {
        "facts": facts,
        "character_knowledge": knowledge,
        "reasoning": reasoning.strip()[:500],
    }


def infer_knowledge_boundaries(
    conn: sqlite3.Connection, project_id: str,
) -> dict:
    """LLM 推断知识边界.返回 dict {facts, character_knowledge, reasoning}.失败返 _FALLBACK_RESULT."""
    try:
        characters = _get_characters_brief(conn, project_id)
        if len(characters) < 2:
            return {
                **_FALLBACK_RESULT,
                "reasoning": "材料不足,请先建至少 2 个角色再点 AI 推断",
            }

        full_text = _get_full_text_for_project(conn, project_id)
        has_upload = bool(full_text and len(full_text.strip()) >= 500)

        # hotfix(2026-06-01):追加场景下,让 LLM 看到现有事实,语义级去重
        # 程序级去重只抓 description 字面完全相同,LLM 经常推出"语义相同但措辞不同"的事实
        # → 必须把现有事实喂回 LLM,prompt 里加铁律"不重复推断已存在内容"
        existing_facts_raw = kb.list_project_facts(conn, project_id)
        existing_facts_brief = [
            {
                "description": f.get("description", ""),
                "first_revealed_scene": f.get("first_revealed_scene"),
                "is_sensitive": bool(f.get("is_sensitive", 0)),
            }
            for f in existing_facts_raw
        ]

        if has_upload:
            samples = _sample_text(full_text)
            user_input = {
                "mode_source": "upload",
                **samples,
                "characters": [
                    {"name": c["name"], "aliases": c["aliases"],
                     "is_protagonist": c["is_protagonist"]}
                    for c in characters
                ],
                "existing_facts": existing_facts_brief,
            }
        else:
            initial_ctx = _build_initial_mode_context(conn, project_id, characters)
            if not initial_ctx["events"] and not initial_ctx["relationships"]:
                return {
                    **_FALLBACK_RESULT,
                    "reasoning": "初始态材料不足:无事件也无关系;请先建几条事件或关系再点 AI 推断(或中末尾态上传作品)",
                }
            user_input = {
                "mode_source": "initial",
                "characters": [
                    {"name": c["name"], "aliases": c["aliases"],
                     "is_protagonist": c["is_protagonist"],
                     "identity_brief": c["identity_brief"]}
                    for c in characters
                ],
                **initial_ctx,
                "existing_facts": existing_facts_brief,
            }

        system_prompt = _load_prompt()

        result, _usage = call_llm_json(
            system_prompt=system_prompt,
            user_input=user_input,
            max_tokens=2500,  # facts + knowledge 两层输出,token 给宽
            temperature=0.5,
            retries=2,
            timeout=120.0,
        )
        validated = _validate_llm_result(result)
        logger.info(
            f"knowledge_boundaries_inferer: project {project_id} → "
            f"facts={len(validated['facts'])} / knowledge={len(validated['character_knowledge'])}"
        )
        return validated

    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"knowledge_boundaries_inferer: LLM call/parse failed for {project_id}: {e}")
        return _FALLBACK_RESULT
    except ValueError as e:
        logger.warning(f"knowledge_boundaries_inferer: LLM result invalid for {project_id}: {e}")
        return _FALLBACK_RESULT
    except Exception as e:  # noqa: BLE001
        logger.warning(f"knowledge_boundaries_inferer: unexpected for {project_id}: {e}")
        return _FALLBACK_RESULT


def cache_knowledge_boundaries(
    conn: sqlite3.Connection,
    project_id: str,
    result: dict,
    *,
    overwrite: bool = False,
) -> dict:
    """写入 story_facts + character_knowledge 表.

    overwrite=False(默认,增量追加):
      在现有 facts 基础上追加新的 LLM 推断结果;
      按 description 完全相同去重(防重复插入同一条事实).
      已有 facts 完全保留.

    overwrite=True(清空重建):
      先删除项目所有 facts(级联 character_knowledge)→ 全插入新的.
      用户主动选"重新生成全部",会丢已有内容.

    返回:
      {applied: bool, fact_ids_created: [...], knowledge_created: int,
       skipped_duplicates: int, skipped_reason?: str}
    """
    facts = result.get("facts") or []
    if not facts:
        return {
            "applied": False,
            "fact_ids_created": [],
            "knowledge_created": 0,
            "skipped_duplicates": 0,
            "skipped_reason": "AI 未推断出任何事实(可能材料不足)",
        }

    if overwrite:
        # 用户主动选清空重建 → 先级联清现有
        conn.execute(
            "DELETE FROM character_knowledge "
            "WHERE fact_id IN (SELECT id FROM story_facts WHERE project_id=?)",
            (project_id,),
        )
        conn.execute(
            "DELETE FROM story_facts WHERE project_id=?", (project_id,),
        )
        conn.commit()

    # 拉现有 description → id map,做去重(overwrite=True 时为空 dict)
    existing_rows = fetch_all(
        conn,
        "SELECT id, description FROM story_facts WHERE project_id=?",
        (project_id,),
    )
    existing_desc_to_id: dict[str, str] = {
        (r["description"] or "").strip(): r["id"]
        for r in existing_rows
    }

    # 插入 facts(去重 + 同 batch 内去重)
    fact_ids: list[str] = []           # 按 LLM 输出顺序,每个位置存对应 fact_id 或 ""
    new_fact_ids: list[str] = []       # 真正新建的(区分追加 vs 复用)
    skipped_duplicates = 0
    seen_this_batch_to_id: dict[str, str] = {}
    for f in facts:
        desc = (f["description"] or "").strip()
        if not desc:
            fact_ids.append("")
            continue
        if desc in existing_desc_to_id:
            # 已存在 → 用现有 id(allow character_knowledge 映射继续工作)
            fact_ids.append(existing_desc_to_id[desc])
            skipped_duplicates += 1
            continue
        if desc in seen_this_batch_to_id:
            # 同次推断内部重复 → 用 batch 内已建的 id
            fact_ids.append(seen_this_batch_to_id[desc])
            skipped_duplicates += 1
            continue
        fid = kb.register_fact(
            conn,
            project_id=project_id,
            description=desc,
            first_revealed_scene=f.get("first_revealed_scene"),
            is_sensitive=bool(f.get("is_sensitive", False)),
        )
        fact_ids.append(fid)
        new_fact_ids.append(fid)
        seen_this_batch_to_id[desc] = fid

    # 映射 character_knowledge
    characters = _get_characters_brief(conn, project_id)
    knowledge_created = 0
    for k in result.get("character_knowledge") or []:
        fidx = k.get("fact_index")
        if not isinstance(fidx, int) or fidx < 0 or fidx >= len(fact_ids):
            continue
        fid_at = fact_ids[fidx]
        if not fid_at:
            # 跳过的 fact 找不到现有同名 → 占位空串,无法关联 knowledge
            continue
        char_id = _map_name_to_character_id(k.get("character_name", ""), characters)
        if not char_id:
            continue
        try:
            kb.mark_known(
                conn,
                character_id=char_id,
                fact_id=fid_at,
                known_since_scene=k.get("known_since_scene"),
                confidence=k.get("confidence", "confirmed"),
            )
            knowledge_created += 1
        except ValueError:
            continue

    return {
        "applied": bool(new_fact_ids) or knowledge_created > 0,
        "fact_ids_created": new_fact_ids,  # 仅新建,不含复用的
        "knowledge_created": knowledge_created,
        "skipped_duplicates": skipped_duplicates,
    }


__all__ = [
    "infer_knowledge_boundaries",
    "cache_knowledge_boundaries",
]
