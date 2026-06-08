"""Sprint 6.A2 M9.A.3(2026-05-20)— 长篇心智 3 个 LLM extractor。

每幕 narrator 完成后调用,从本幕产物 narrative_segment 抽取:
  - 角色弧光片段 → character_arcs
  - 新埋 / 收尾的伏笔 → foreshadow_ledger
  - 续作创立的世界规则 → world_rules_ledger

3 个 extractor 都是:
  1. 加载对应 prompt
  2. call_llm_json 跑 LLM
  3. 兜底校验 + 写库
  4. 失败 log warning,**不阻塞主循环**(对标 M5 entity_registrar 链路)

设计原则:
  - **失败兜底** — 任何 extractor 失败都不影响 sim 完成(降级到无累积心智)
  - **幂等** — 同一幕重复调用不会重复写(基于 scene_index 检查)
  - **token 控制** — 每 extractor 单独调用,失败一个不连累其他

API:
  extract_arcs(conn, sim, scene_index, narrative_segment, agents) → dict
  extract_foreshadows(conn, sim, scene_index, narrative_segment) → dict
  extract_world_rules(conn, sim, scene_index, narrative_segment) → dict

Returns each: {"created": N, "usage": {input_tokens, output_tokens}}
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional

from app.db import execute, fetch_all, fetch_one, transaction
from app.models.character import Character
from app.models.simulation import Simulation
from app.services.llm_client import call_llm_json, LlmCallFailed, LlmJsonParseFailed
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"

# 兜底常量
ALLOWED_ARC_KINDS = ("gradual", "sudden", "revelation", "regression")
ALLOWED_FORESHADOW_PRIORITIES = ("high", "medium", "low")
ALLOWED_WORLD_RULE_SCOPES = ("global", "faction", "location", "character")
MAX_ARCS_PER_SCENE = 3              # 单幕最多抽 3 条角色弧
MAX_NEW_FORESHADOWS_PER_SCENE = 3   # 单幕最多抽 3 个新坑
MAX_RESOLVED_FORESHADOWS_PER_SCENE = 5
MAX_NEW_RULES_PER_SCENE = 2
MAX_RULE_TEXT_LEN = 150
MAX_FORESHADOW_CONTENT_LEN = 200


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


# ======================================================================
# 1. 角色弧光抽取
# ======================================================================

def extract_arcs(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene_index: int,
    scene_name: str,
    narrative_segment: str,
    agents: list[Character],
) -> dict:
    """从本幕 narrative_segment 抽取角色弧光片段并写库。"""
    if not narrative_segment or not agents:
        return {"created": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}

    # 幂等:本幕已抽过(scene_index 已有 arc)→ 跳过
    existing = fetch_one(
        conn,
        "SELECT COUNT(*) AS c FROM character_arcs "
        "WHERE simulation_id=? AND scene_index=?",
        (sim.id, scene_index),
    )
    if existing and existing["c"] > 0:
        return {"created": 0, "usage": {"input_tokens": 0, "output_tokens": 0}, "skipped": "already_extracted"}

    try:
        system_prompt = _load_prompt("m9_arc_extractor.md")
    except FileNotFoundError:
        logger.warning("m9_arc_extractor.md not found, skip arc extraction")
        return {"created": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}

    valid_agent_ids = {a.id for a in agents}
    user_input = {
        "scene_index": scene_index,
        "scene_name": scene_name,
        "narrative_segment": narrative_segment[:1500],
        "agents_present": [
            {"id": a.id, "name": a.name, "is_protagonist": a.is_protagonist}
            for a in agents
        ],
    }

    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input, max_tokens=600, temperature=0.3,
        )
    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"arc_extractor LLM failed sim={sim.id} scene={scene_index}: {e}")
        return {"created": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}

    raw_arcs = parsed.get("arcs") if isinstance(parsed, dict) else None
    if not isinstance(raw_arcs, list) or not raw_arcs:
        return {"created": 0, "usage": usage}

    now = iso_now()
    created = 0
    with transaction(conn) as tx:
        for arc in raw_arcs[:MAX_ARCS_PER_SCENE]:
            if not isinstance(arc, dict):
                continue
            # 字段兜底
            char_ids_raw = arc.get("character_ids") or []
            if not isinstance(char_ids_raw, list):
                continue
            # 只保留真实 agent id
            char_ids = [
                str(c) for c in char_ids_raw
                if isinstance(c, str) and c in valid_agent_ids
            ]
            if not char_ids:
                continue
            arc_keyword = str(arc.get("arc_keyword") or "").strip()[:50]
            if not arc_keyword:
                continue
            trigger = str(arc.get("trigger_summary") or "").strip()[:200]
            arc_kind = arc.get("arc_kind", "gradual")
            if arc_kind not in ALLOWED_ARC_KINDS:
                arc_kind = "gradual"

            execute(
                tx,
                "INSERT INTO character_arcs "
                "(id, project_id, simulation_id, scene_index, "
                " character_ids_json, arc_keyword, trigger_summary, arc_kind, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex, sim.project_id, sim.id, scene_index,
                    json.dumps(char_ids, ensure_ascii=False),
                    arc_keyword, trigger, arc_kind, now,
                ),
            )
            created += 1

    return {"created": created, "usage": usage}


# ======================================================================
# 2. 伏笔追踪
# ======================================================================

def extract_foreshadows(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene_index: int,
    scene_name: str,
    narrative_segment: str,
) -> dict:
    """从本幕 narrative_segment 抽取新埋的坑 + 收尾的坑,写库 / UPDATE 状态。"""
    if not narrative_segment:
        return {"created": 0, "resolved": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}

    # 幂等:本幕已抽过 → 跳过
    existing = fetch_one(
        conn,
        "SELECT COUNT(*) AS c FROM foreshadow_ledger "
        "WHERE introduced_in_simulation_id=? AND introduced_scene_index=?",
        (sim.id, scene_index),
    )
    if existing and existing["c"] > 0:
        return {"created": 0, "resolved": 0, "usage": {"input_tokens": 0, "output_tokens": 0}, "skipped": "already_extracted"}

    try:
        system_prompt = _load_prompt("m9_foreshadow_tracker.md")
    except FileNotFoundError:
        logger.warning("m9_foreshadow_tracker.md not found, skip foreshadow extraction")
        return {"created": 0, "resolved": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}

    # 拉本 project open 伏笔(给 LLM 看,让它判断哪些被收尾)
    open_rows = fetch_all(
        conn,
        "SELECT id, content, priority, introduced_scene_index "
        "FROM foreshadow_ledger "
        "WHERE project_id=? AND status='open' "
        "ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
        "  created_at ASC LIMIT 10",
        (sim.project_id,),
    )
    open_foreshadows_input = [
        {
            "id": r["id"], "content": r["content"],
            "priority": r["priority"], "introduced_scene_index": r["introduced_scene_index"],
        }
        for r in open_rows
    ]

    user_input = {
        "scene_index": scene_index,
        "scene_name": scene_name,
        "narrative_segment": narrative_segment[:1500],
        "open_foreshadows": open_foreshadows_input,
    }

    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input, max_tokens=600, temperature=0.3,
        )
    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"foreshadow_tracker LLM failed sim={sim.id} scene={scene_index}: {e}")
        return {"created": 0, "resolved": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}

    if not isinstance(parsed, dict):
        return {"created": 0, "resolved": 0, "usage": usage}

    new_list = parsed.get("new_foreshadows") or []
    resolved_list = parsed.get("resolved_foreshadows") or []
    if not isinstance(new_list, list):
        new_list = []
    if not isinstance(resolved_list, list):
        resolved_list = []

    valid_open_ids = {r["id"] for r in open_rows}
    now = iso_now()
    created = 0
    resolved_count = 0

    with transaction(conn) as tx:
        # 新埋坑
        for f in new_list[:MAX_NEW_FORESHADOWS_PER_SCENE]:
            if not isinstance(f, dict):
                continue
            content = str(f.get("content") or "").strip()[:MAX_FORESHADOW_CONTENT_LEN]
            if not content:
                continue
            priority = f.get("priority", "medium")
            if priority not in ALLOWED_FORESHADOW_PRIORITIES:
                priority = "medium"
            execute(
                tx,
                "INSERT INTO foreshadow_ledger "
                "(id, project_id, content, "
                " introduced_in_simulation_id, introduced_scene_index, "
                " status, priority, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'open', ?, ?, ?)",
                (
                    uuid.uuid4().hex, sim.project_id, content,
                    sim.id, scene_index, priority, now, now,
                ),
            )
            created += 1

        # 收尾坑
        for r in resolved_list[:MAX_RESOLVED_FORESHADOWS_PER_SCENE]:
            if not isinstance(r, dict):
                continue
            fid = r.get("foreshadow_id")
            if not fid or fid not in valid_open_ids:
                continue
            resolution = str(r.get("resolution_summary") or "").strip()[:200]
            execute(
                tx,
                "UPDATE foreshadow_ledger "
                "SET status='resolved', resolved_in_simulation_id=?, "
                "    resolved_scene_index=?, resolution_summary=?, updated_at=? "
                "WHERE id=? AND status='open'",
                (sim.id, scene_index, resolution, now, fid),
            )
            resolved_count += 1

    return {"created": created, "resolved": resolved_count, "usage": usage}


# ======================================================================
# 3. 世界规则抽取
# ======================================================================

def extract_world_rules(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene_index: int,
    scene_name: str,
    narrative_segment: str,
) -> dict:
    """从本幕 narrative_segment 抽取续作创立的新世界规则,写库。"""
    if not narrative_segment:
        return {"created": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}

    # 幂等:本幕已抽过 → 跳过
    existing = fetch_one(
        conn,
        "SELECT COUNT(*) AS c FROM world_rules_ledger "
        "WHERE introduced_in_simulation_id=? AND introduced_scene_index=?",
        (sim.id, scene_index),
    )
    if existing and existing["c"] > 0:
        return {"created": 0, "usage": {"input_tokens": 0, "output_tokens": 0}, "skipped": "already_extracted"}

    try:
        system_prompt = _load_prompt("m9_world_rule_extractor.md")
    except FileNotFoundError:
        logger.warning("m9_world_rule_extractor.md not found, skip world rule extraction")
        return {"created": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}

    # 拉 active 已有规则(给 LLM 看,避免冲突 / 重复)
    existing_rules = fetch_all(
        conn,
        "SELECT rule_text FROM world_rules_ledger "
        "WHERE project_id=? AND active=1 ORDER BY created_at ASC LIMIT 15",
        (sim.project_id,),
    )
    existing_rules_input = [r["rule_text"] for r in existing_rules]

    user_input = {
        "scene_index": scene_index,
        "scene_name": scene_name,
        "narrative_segment": narrative_segment[:1500],
        "existing_rules": existing_rules_input,
    }

    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input, max_tokens=500, temperature=0.3,
        )
    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"world_rule_extractor LLM failed sim={sim.id} scene={scene_index}: {e}")
        return {"created": 0, "usage": {"input_tokens": 0, "output_tokens": 0}}

    if not isinstance(parsed, dict):
        return {"created": 0, "usage": usage}

    raw_rules = parsed.get("new_rules") or []
    if not isinstance(raw_rules, list) or not raw_rules:
        return {"created": 0, "usage": usage}

    now = iso_now()
    created = 0
    with transaction(conn) as tx:
        for rule in raw_rules[:MAX_NEW_RULES_PER_SCENE]:
            if not isinstance(rule, dict):
                continue
            rule_text = str(rule.get("rule_text") or "").strip()[:MAX_RULE_TEXT_LEN]
            if not rule_text:
                continue
            scope = rule.get("scope", "global")
            if scope not in ALLOWED_WORLD_RULE_SCOPES:
                scope = "global"
            execute(
                tx,
                "INSERT INTO world_rules_ledger "
                "(id, project_id, rule_text, "
                " introduced_in_simulation_id, introduced_scene_index, "
                " scope, scope_target_id, active, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, NULL, 1, ?, ?)",
                (
                    uuid.uuid4().hex, sim.project_id, rule_text,
                    sim.id, scene_index, scope, now, now,
                ),
            )
            created += 1

    return {"created": created, "usage": usage}


__all__ = [
    "extract_arcs",
    "extract_foreshadows",
    "extract_world_rules",
]
