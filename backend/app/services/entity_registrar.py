"""Sprint 6.A2 M5.1(2026-05-20)— Canonical Entity Registrar(实体唯一身份注册)。

每幕 narrator 合稿后调用,LLM 从 narrative_segment 抽出本幕引入的"核心实体"
(character / object / location / event)。

核心逻辑:
  - 抽出新候选实体后,**与已有 canonical_entities 列表做语义匹配**:
    * LLM 判定"林小禾"与已有"林小满"语义重合 → **不创建新实体,把"林小禾"加到林小满的 aliases**
    * 完全不重合 → 创建新 canonical_entity
  - **首次出现即 LOCKED** — locked_at 立即非空,后续任何抽取都不能造同义新身份

API:
  - register_entities_from_segment(conn, sim, scene_index, narrative, agents) → tuple[dict, dict]
    summary = {"created": N, "aliased": M, "alias_added_to": [{id, name}, ...]}
  - list_canonical_entities(conn, simulation_id, entity_types=None) → list[CanonicalEntity]
    给 scene_picker / narrator / agent_dialogue 注入用
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from app.db import execute, fetch_all
from app.models.canonical_entity import CanonicalEntity, EntityType
from app.models.character import Character
from app.models.simulation import Simulation
from app.services.llm_client import call_llm_json
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def register_entities_from_segment(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene_index: int,
    narrative_segment: str,
    agents: list[Character],
) -> tuple[dict, dict]:
    """从一幕 narrative_segment 抽实体并落 canonical_entities。

    Returns:
      ({
        "created": [entity_id, ...],          # 新创建的实体
        "aliased_to": [{"existing_id": "x", "alias_added": "林小禾"}, ...],
                                              # 抽到了与已有实体同义的新身份 → 加 alias
      }, llm_usage)
    """
    summary = {"created": [], "aliased_to": []}
    if not narrative_segment or len(narrative_segment.strip()) < 50:
        return summary, {"input_tokens": 0, "output_tokens": 0}

    # 拉本 sim 已注册的全部实体(LLM 用来做语义匹配)
    existing = list_canonical_entities(conn, sim.id)

    user_input = {
        "scene_index": scene_index,
        "narrative_segment": narrative_segment,
        "agents_present": [
            {"id": a.id, "name": a.name} for a in agents
        ],
        "existing_entities": [
            {
                "id": e.id,
                "entity_type": e.entity_type,
                "canonical_name": e.canonical_name,
                "aliases": e.aliases,
                "description": e.description,
            }
            for e in existing
        ],
    }

    system_prompt = _load_prompt("m5_entity_registrar.md")
    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input,
            max_tokens=1500, temperature=0.2,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"entity_registrar LLM failed sim={sim.id} scene={scene_index}: {e}"
        )
        return summary, {"input_tokens": 0, "output_tokens": 0}

    if not isinstance(parsed, dict):
        return summary, usage

    now = iso_now()

    # 处理 new_entities(全新身份,需要创建)
    new_entities = parsed.get("new_entities") or []
    if isinstance(new_entities, list):
        for ne in new_entities:
            if not isinstance(ne, dict):
                continue
            etype = str(ne.get("entity_type") or "").strip()
            if etype not in ("character", "object", "location", "event"):
                continue
            cname = str(ne.get("canonical_name") or "").strip()[:30]
            if not cname or len(cname) < 1:
                continue
            desc = str(ne.get("description") or "").strip()[:500]
            if not desc:
                continue

            entity_id = uuid.uuid4().hex
            # 别名首次至少含 canonical_name 自己
            aliases_input = ne.get("aliases") or []
            if not isinstance(aliases_input, list):
                aliases_input = []
            aliases = [cname] + [
                str(a).strip()[:30] for a in aliases_input
                if isinstance(a, str) and a.strip() and a.strip() != cname
            ]

            execute(
                conn,
                """INSERT INTO canonical_entities
                   (id, simulation_id, entity_type, canonical_name,
                    aliases_json, description,
                    first_introduced_scene, locked_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    entity_id, sim.id, etype, cname,
                    json.dumps(aliases, ensure_ascii=False), desc,
                    scene_index, now, now,
                ),
            )
            summary["created"].append(entity_id)

    # 处理 alias_additions(LLM 判定新身份是已有实体的别名)
    alias_additions = parsed.get("alias_additions") or []
    if isinstance(alias_additions, list):
        for aa in alias_additions:
            if not isinstance(aa, dict):
                continue
            existing_id = str(aa.get("existing_entity_id") or "").strip()
            new_alias = str(aa.get("new_alias") or "").strip()[:30]
            if not existing_id or not new_alias:
                continue
            # 拉旧实体,把 new_alias 加入 aliases(去重)
            row = next((r for r in existing if r.id == existing_id), None)
            if row is None:
                continue
            if new_alias in row.aliases:
                continue
            updated_aliases = row.aliases + [new_alias]
            execute(
                conn,
                """UPDATE canonical_entities
                   SET aliases_json=?
                   WHERE id=? AND simulation_id=?""",
                (
                    json.dumps(updated_aliases, ensure_ascii=False),
                    existing_id, sim.id,
                ),
            )
            summary["aliased_to"].append({
                "existing_id": existing_id,
                "alias_added": new_alias,
            })

    conn.commit()
    return summary, usage


def list_canonical_entities(
    conn: sqlite3.Connection,
    simulation_id: str,
    entity_types: Optional[list[EntityType]] = None,
) -> list[CanonicalEntity]:
    """拉某 sim 已注册的全部 canonical entities,按 scene_index 升序。

    Args:
      entity_types: 过滤指定类型;None=全部
    """
    if entity_types:
        placeholders = ",".join("?" for _ in entity_types)
        rows = fetch_all(
            conn,
            f"""SELECT * FROM canonical_entities
                WHERE simulation_id=? AND entity_type IN ({placeholders})
                ORDER BY first_introduced_scene ASC, created_at ASC""",
            (simulation_id, *entity_types),
        )
    else:
        rows = fetch_all(
            conn,
            """SELECT * FROM canonical_entities
               WHERE simulation_id=?
               ORDER BY first_introduced_scene ASC, created_at ASC""",
            (simulation_id,),
        )
    return [CanonicalEntity.from_row(r) for r in rows]
