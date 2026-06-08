"""Sprint 6.A2 M2(2026-05-18)— 角色亲疏地图服务。

产品意图(对齐用户拍板"M2 场景识别 + 角色亲疏地图"):
  M3 续写时 scene_picker 需要回答 2 个问题:
    Q1. "在场所 X,哪些角色最可能上场?"  → compute_scene_regulars
    Q2. "在所有场所,角色 A 和谁互动最多?" → compute_character_affinity_pairs

  数据源:扫 extract_chunk_results.graph_json.entities,**统计同一 chunk 内**
  PERSON + LOCATION 共现矩阵。

派生数据,不落库:
  - 计算成本低(本地循环 100-500 chunk 量级,毫秒完成)
  - 任何 PROJECT 增删 character / 改 events / 重抽 graph 都会让矩阵失效
  - YAGNI:M3 续写真用时再决策是否加缓存表
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from app.db import fetch_all


@dataclass
class SceneRegular:
    """场景"常客"角色 — 在该 scene 共现次数 + 角色身份。"""
    character_id: str
    character_name: str
    co_occurrence_count: int    # 共现 chunk 数(同 chunk 内同时有此 char 和 此 scene)
    is_protagonist: bool


def compute_scene_regulars(
    conn: sqlite3.Connection,
    project_id: str,
    scene_name: str,
    top_n: int = 10,
) -> list[SceneRegular]:
    """计算给定 scene 的"常客"角色 — 按共现 chunk 数倒序 top-N。

    实现:
      1. 拉本项目所有 chunk graph_json
      2. 找含此 scene_name(LOCATION 实体或其 aliases)的 chunk
      3. 这些 chunk 里的 PERSON 实体名 → 共现次数 +1
      4. PERSON name 反查 characters 表拿 character_id + is_protagonist
      5. 排序 + 截 top_n
    """
    # 1. 拉角色 name → (id, is_protagonist) 反查表
    char_rows = fetch_all(
        conn,
        """SELECT id, name, is_protagonist FROM characters
           WHERE project_id=?""",
        (project_id,),
    )
    name_to_char = {
        r["name"]: (r["id"], bool(r["is_protagonist"]))
        for r in char_rows
    }
    if not name_to_char:
        return []

    # 2. 拉 scene 的 aliases(若存在,加入匹配集合)
    scene_row = fetch_all(
        conn,
        """SELECT aliases_json FROM project_scenes
           WHERE project_id=? AND name=?""",
        (project_id, scene_name),
    )
    scene_match_names: set[str] = {scene_name}
    if scene_row:
        try:
            aliases = json.loads(scene_row[0]["aliases_json"] or "[]")
            if isinstance(aliases, list):
                scene_match_names.update(
                    str(a) for a in aliases if isinstance(a, str) and a.strip()
                )
        except (json.JSONDecodeError, TypeError):
            pass

    # 3. 拉所有 chunk graph_json
    chunk_rows = fetch_all(
        conn,
        """SELECT ecr.chunk_index, ecr.graph_json
           FROM extract_chunk_results ecr
           JOIN graph_extraction_jobs gej ON gej.id = ecr.job_id
           WHERE gej.project_id=?""",
        (project_id,),
    )

    # 4. 扫每 chunk:含此 scene → PERSON 计数
    co_count: dict[str, int] = {}   # character_id → count
    for cr in chunk_rows:
        try:
            graph = json.loads(cr["graph_json"]) if cr["graph_json"] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        entities = graph.get("entities") or []
        if not isinstance(entities, list):
            continue

        # 该 chunk 是否含目标 scene?
        chunk_has_scene = False
        chunk_persons: set[str] = set()
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            ent_type = (ent.get("type") or "").upper()
            ent_name = (ent.get("name") or "").strip()
            if not ent_name:
                continue
            if ent_type == "LOCATION" and ent_name in scene_match_names:
                chunk_has_scene = True
            elif ent_type == "PERSON" and ent_name in name_to_char:
                chunk_persons.add(name_to_char[ent_name][0])

        if chunk_has_scene:
            for cid in chunk_persons:
                co_count[cid] = co_count.get(cid, 0) + 1

    # 5. 排序 + 包装
    results: list[SceneRegular] = []
    for cid, cnt in co_count.items():
        # 反查 name + is_protagonist(用 dict 逆序;O(N) 但 N 小)
        match = next(
            ((n, info) for n, info in name_to_char.items() if info[0] == cid),
            None,
        )
        if not match:
            continue
        name, info = match
        results.append(SceneRegular(
            character_id=cid,
            character_name=name,
            co_occurrence_count=cnt,
            is_protagonist=info[1],
        ))

    # 排序:主角优先 + 共现次数倒序
    results.sort(
        key=lambda r: (-int(r.is_protagonist), -r.co_occurrence_count, r.character_name),
    )
    return results[:top_n]


def compute_character_affinity_pairs(
    conn: sqlite3.Connection,
    project_id: str,
    character_id: str,
    top_n: int = 10,
) -> list[tuple[str, str, int]]:
    """给定 character_id,返回与其他角色的共现次数 top-N。

    返回 [(other_char_id, other_char_name, co_count), ...] 排序倒序。
    M3 续写时 agent_summoner 用此选"和当前角色亲密的其他 agent 上场"。
    """
    char_rows = fetch_all(
        conn,
        """SELECT id, name FROM characters WHERE project_id=?""",
        (project_id,),
    )
    name_to_id = {r["name"]: r["id"] for r in char_rows}
    id_to_name = {r["id"]: r["name"] for r in char_rows}

    target_name = id_to_name.get(character_id)
    if not target_name:
        return []

    chunk_rows = fetch_all(
        conn,
        """SELECT ecr.graph_json
           FROM extract_chunk_results ecr
           JOIN graph_extraction_jobs gej ON gej.id = ecr.job_id
           WHERE gej.project_id=?""",
        (project_id,),
    )

    pair_count: dict[str, int] = {}    # other_char_id → count
    for cr in chunk_rows:
        try:
            graph = json.loads(cr["graph_json"]) if cr["graph_json"] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        entities = graph.get("entities") or []
        if not isinstance(entities, list):
            continue

        # 该 chunk 内 PERSON 集合
        chunk_persons: set[str] = set()
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            if (ent.get("type") or "").upper() != "PERSON":
                continue
            ent_name = (ent.get("name") or "").strip()
            cid = name_to_id.get(ent_name)
            if cid:
                chunk_persons.add(cid)

        if character_id in chunk_persons:
            for other_id in chunk_persons:
                if other_id == character_id:
                    continue
                pair_count[other_id] = pair_count.get(other_id, 0) + 1

    sorted_pairs = sorted(
        pair_count.items(), key=lambda kv: -kv[1],
    )[:top_n]
    return [
        (oid, id_to_name.get(oid, "?"), cnt)
        for oid, cnt in sorted_pairs
    ]
