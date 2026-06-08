"""Sprint 6.A2 M7.B(2026-05-20)— 续作新角色同步入库。

每幕 entity_registrar 落 canonical_entities 后调用,把 entity_type='character'
且原 project.characters 没有的新角色,**写入 characters 表**(标 origin_simulation_id)。
顺便给每个新角色建一条与主角的关系("其他" type + "续作新登场"description),
让用户在关系图谱里直观看到新血液。

设计原则:
  - **新增不覆盖** — 已存在同名(canonical_name 或 aliases 任一) → 跳过,不动用户的人物档案
  - **来源标记** — 新写入的 row.origin_simulation_id = sim.id,前端可显示"续作生成"badge
  - **自动关系** — 与主角建"其他"关系(中性,description 提示用户细化);若项目无主角则跳过
  - **行为兜底** — 任何步骤失败都不阻塞主循环(LLM 已经赚到 token,产物已落库,
    入库失败 log warning 即可,下次手动可补)
  - **幂等** — 重复调用同一幕不会重复创建(checks existing characters by name + aliases)

Sprint 6.A2 路线图 #1 自然延伸(2026-05-23)— 续作角色 baseline 补完:
  canonical_entities 表无 baseline 字段(只有 name/aliases/description/scene),新角色 INSERT
  时 behavior_baseline_json 必为 NULL。原本依赖用户后续手动 character_focus 才能补 baseline,
  本次改动**在 INSERT 成功后立即同步调 enrich_character**,复用现有 enricher 逻辑给新角色补
  identity 之外的 4 个空字段(quotes / no_go_list / behavior_baseline + LLM 若需要的 personality 增强)。
  - 单角色 LLM ~3-5s,每幕新角色 1-3 个,加 10-15s 时延,在 sim 整体几分钟主流程里可接受
  - enrich 失败兜底:角色仍创建(基础 INSERT 已 commit),只是 baseline NULL,后续 character_focus 仍可补
  - 已填(identity / personality 由 description 派生已非空)→ enricher 内部"已填不覆盖"自动跳过

API:
  sync_new_characters_from_canonical(conn, sim, scene_index, agents) → dict
    返回 {"created_characters": [...], "created_relationships": [...], "skipped": [...],
         "enriched_characters": [{id, name, updated_fields}, ...]}
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.models.character import Character
from app.models.simulation import Simulation
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)


# 关系类型(对齐 relationships CHECK 约束 + relationship.py 注释)
# 用 "其他" 作中性默认 — 不假设是朋友 / 敌对 / 亲属 / ...,留给用户细化
DEFAULT_NEW_CHAR_RELATION_TYPE = "其他"
DEFAULT_NEW_CHAR_RELATION_STRENGTH = "weak"


def sync_new_characters_from_canonical(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene_index: int,
    agents: list[Character],
) -> dict:
    """从本 sim 的 canonical_entities(character 类型)同步新角色到 characters 表。

    Args:
      sim: 当前推演
      scene_index: 当前幕号(用于 description 注明来源)
      agents: 当前在场角色(用于跳过已知角色 + 寻找主角作关系 target)

    Returns:
      {
        "created_characters": [{"id": ..., "name": ..., "origin_scene": ...}, ...],
        "created_relationships": [{"id": ..., "new_char": ..., "to": ...}, ...],
        "skipped": [{"canonical_name": ..., "reason": "name_exists"}, ...],
      }
    """
    summary: dict = {
        "created_characters": [],
        "created_relationships": [],
        "skipped": [],
        # Sprint 6.A2 #1 延伸(2026-05-23):新角色 INSERT 后 enricher 补完报告
        "enriched_characters": [],
    }

    # 1. 拉本 sim 全部 character 类型 canonical_entities
    rows = fetch_all(
        conn,
        "SELECT * FROM canonical_entities "
        "WHERE simulation_id=? AND entity_type='character' "
        "ORDER BY first_introduced_scene ASC, created_at ASC",
        (sim.id,),
    )
    if not rows:
        return summary

    # 2. 拉本 project 已有 characters(name + 把 quotes / no_go_list 不读;
    #    只用 name 集合判 + identity 看是否标过续作来源)
    existing_rows = fetch_all(
        conn,
        "SELECT id, name, is_protagonist, origin_simulation_id "
        "FROM characters WHERE project_id=?",
        (sim.project_id,),
    )
    existing_name_set = {r["name"] for r in existing_rows if r["name"]}
    # 寻找主角(给新角色自动建关系):优先 is_protagonist=1;若无,取 agents[0]
    protagonist_id = next(
        (r["id"] for r in existing_rows if r["is_protagonist"]),
        None,
    )
    if not protagonist_id and agents:
        protagonist_id = agents[0].id

    # 3. 遍历 character 类型 canonical_entities,新增不冲突的
    now = iso_now()
    for r in rows:
        canonical_name = (r["canonical_name"] or "").strip()
        if not canonical_name:
            continue

        # 解析 aliases(也用作冲突检测)
        raw_aliases = r["aliases_json"] or "[]"
        try:
            aliases_list = json.loads(raw_aliases)
            if not isinstance(aliases_list, list):
                aliases_list = []
        except (json.JSONDecodeError, TypeError):
            aliases_list = []

        # 冲突判定:canonical_name 或 任一 alias 已在 project.characters 里
        name_candidates = {canonical_name, *[a for a in aliases_list if isinstance(a, str) and a.strip()]}
        if name_candidates & existing_name_set:
            summary["skipped"].append({
                "canonical_name": canonical_name,
                "reason": "name_exists",
            })
            continue

        # 4. INSERT characters 行(标 origin_simulation_id)
        new_char_id = uuid.uuid4().hex
        description = (r["description"] or "").strip()[:200]
        first_scene = int(r["first_introduced_scene"])
        identity_text = f"续作第 {first_scene} 幕首次登场;{description}".strip()[:200]
        # quotes / no_go_list 暂留空,等用户后续 character_focus 补;
        # personality 用 description 简化作初值(用户可编辑)
        personality_text = description[:80]

        try:
            execute(
                conn,
                "INSERT INTO characters "
                "(id, project_id, name, identity, personality, quotes, no_go_list, "
                " position_x, position_y, position_z, color, "
                " created_at, updated_at, origin_simulation_id) "
                "VALUES (?, ?, ?, ?, ?, '[]', '[]', 0, 0, 0, NULL, ?, ?, ?)",
                (
                    new_char_id, sim.project_id, canonical_name,
                    identity_text, personality_text,
                    now, now, sim.id,
                ),
            )
        except sqlite3.IntegrityError as e:
            # 例如 project_id FK 失效 / unique 撞 — 罕见但兜底
            logger.warning(
                f"sequel_character_sync: insert character failed name={canonical_name} "
                f"sim={sim.id}: {e}"
            )
            summary["skipped"].append({
                "canonical_name": canonical_name,
                "reason": f"db_error:{type(e).__name__}",
            })
            continue

        summary["created_characters"].append({
            "id": new_char_id,
            "name": canonical_name,
            "origin_scene": first_scene,
        })
        # 更新本地集合,防同一次循环里重名重插
        existing_name_set.add(canonical_name)
        existing_name_set.update(name_candidates)

        # 4b. Sprint 6.A2 #1 延伸(2026-05-23):立即调 enricher 给新角色补 baseline 等空字段
        # 复用 agent_profile_enricher 的"已填不覆盖 + 单角色补"逻辑 —
        # description 派生的 identity / personality 非空会被 enricher 自动跳过,
        # 空的 quotes / no_go_list / behavior_baseline 会被 LLM 补完。
        # 失败兜底:enricher 内部已有完整 try/except,这里 broader 防御性再加一层 —
        # enrich 失败不抛(角色 INSERT 已成功并 commit),baseline 留 NULL,后续 character_focus 可补。
        try:
            from app.services.agent_profile_enricher import enrich_character

            new_char_row = fetch_one(
                conn,
                "SELECT * FROM characters WHERE id=?",
                (new_char_id,),
            )
            if new_char_row is not None:
                new_char_obj = Character.from_row(new_char_row)
                enrich_result = enrich_character(conn, new_char_obj)
                updated = enrich_result.get("updated_fields") or []
                if updated:
                    summary["enriched_characters"].append({
                        "id": new_char_id,
                        "name": canonical_name,
                        "updated_fields": list(updated),
                    })
                elif enrich_result.get("error"):
                    # enricher 返回了 error(LLM 失败 / prompt 加载失败 / parse 失败等)
                    # 已经在 enricher 内部 log warning,这里只 debug 级补充上下文
                    logger.debug(
                        f"sequel_character_sync: enricher returned error for "
                        f"new char id={new_char_id} name={canonical_name}: "
                        f"{enrich_result.get('error')}"
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"sequel_character_sync: enrich new character failed "
                f"id={new_char_id} name={canonical_name}: {e}"
            )

        # 5. 给新角色与主角建一条 "其他" 关系("续作中首次登场,关系待用户细化")
        if protagonist_id and protagonist_id != new_char_id:
            rel_id = uuid.uuid4().hex
            rel_desc = f"续作第 {first_scene} 幕新登场,与主角建立连接(待用户在关系图谱中细化具体类型)"
            try:
                execute(
                    conn,
                    "INSERT INTO relationships "
                    "(id, project_id, source_id, target_id, type, description, "
                    " color, strength, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                    (
                        rel_id, sim.project_id, protagonist_id, new_char_id,
                        DEFAULT_NEW_CHAR_RELATION_TYPE, rel_desc,
                        DEFAULT_NEW_CHAR_RELATION_STRENGTH, now,
                    ),
                )
                summary["created_relationships"].append({
                    "id": rel_id,
                    "new_char": canonical_name,
                    "from_protagonist_id": protagonist_id,
                })
            except sqlite3.IntegrityError as e:
                logger.warning(
                    f"sequel_character_sync: insert relationship failed "
                    f"new_char={new_char_id} protagonist={protagonist_id} "
                    f"sim={sim.id}: {e}"
                )

    conn.commit()
    return summary


def list_sequel_characters(
    conn: sqlite3.Connection,
    project_id: str,
    simulation_id: Optional[str] = None,
) -> list[Character]:
    """查询某项目的续作产物角色(可选按 simulation_id 过滤)。

    给前端"项目角色列表"展示"续作生成"标签 / "续作产物角色面板"用。
    """
    if simulation_id:
        rows = fetch_all(
            conn,
            "SELECT * FROM characters WHERE project_id=? AND origin_simulation_id=? "
            "ORDER BY created_at ASC",
            (project_id, simulation_id),
        )
    else:
        rows = fetch_all(
            conn,
            "SELECT * FROM characters WHERE project_id=? AND origin_simulation_id IS NOT NULL "
            "ORDER BY created_at ASC",
            (project_id,),
        )
    return [Character.from_row(r) for r in rows]


__all__ = [
    "sync_new_characters_from_canonical",
    "list_sequel_characters",
]
