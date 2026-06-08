"""Sprint 6.A2 M2(2026-05-18)— 场景提取服务。

产品意图(对齐用户拍板"M2 场景识别 + 角色亲疏地图"):
  build_graph.md 已经在抽 LOCATION 实体(在 extract_chunk_results.graph_json.entities 里),
  本服务把这些实体聚合落 project_scenes 表,作为前三态项目的"场所图谱"。

数据流:
  1. extract_chunk_results.graph_json.entities → 过滤 type='LOCATION'
  2. 同 name 去重(同一场所在多 chunk 出现 = 一条记录;appearance_chunk_count 累加)
  3. 合并 aliases / description(取最长 description;aliases 并集)
  4. 写入 project_scenes 表(UPSERT)

成本:零 LLM 调用,纯本地 Python 聚合;典型项目 50 chunk × ~3 LOCATION/chunk = 150 entities,
       去重后落库 10-30 个 scene,毫秒级完成。

调用时机:
  - extract_service 完工 hook(抽完图谱后自动跑)— 已是教训 #15 ("prompt 升级后需主动重抽")的最佳契机
  - 用户手动重新触发(若需要,未来加 endpoint;M2 暂不开手动入口,YAGNI)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.models.project_scene import ProjectScene
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)


def extract_scenes_from_project(
    conn: sqlite3.Connection, project_id: str,
) -> dict:
    """从 extract_chunk_results 抽 LOCATION 实体 + 去重 + UPSERT 到 project_scenes。

    返回报告 dict {
      "scenes_count": int,         # 最终去重后写入的 scene 数
      "chunks_processed": int,     # 扫了多少 chunk
      "raw_locations_seen": int,   # 抽到多少 LOCATION 实体 (含重复)
    }
    """
    # 1. 拉本项目所有 chunk graph_json
    chunk_rows = fetch_all(
        conn,
        """SELECT ecr.chunk_index, ecr.graph_json
           FROM extract_chunk_results ecr
           JOIN graph_extraction_jobs gej ON gej.id = ecr.job_id
           WHERE gej.project_id=?
           ORDER BY ecr.chunk_index ASC""",
        (project_id,),
    )
    if not chunk_rows:
        return {
            "scenes_count": 0,
            "chunks_processed": 0,
            "raw_locations_seen": 0,
        }

    # 2. 扫所有 chunk,按 LOCATION name 聚合
    # 结构:{ name: {aliases: set, descriptions: list[str], chunks: set[int]} }
    agg: dict[str, dict] = {}
    raw_count = 0

    for row in chunk_rows:
        try:
            graph = json.loads(row["graph_json"]) if row["graph_json"] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        entities = graph.get("entities") or []
        if not isinstance(entities, list):
            continue

        for ent in entities:
            if not isinstance(ent, dict):
                continue
            ent_type = (ent.get("type") or "").upper()
            if ent_type != "LOCATION":
                continue
            name = (ent.get("name") or "").strip()
            if not name:
                continue

            raw_count += 1
            entry = agg.setdefault(name, {
                "aliases": set(),
                "descriptions": [],
                "chunks": set(),
            })
            # aliases 并集
            aliases = ent.get("aliases") or []
            if isinstance(aliases, list):
                for a in aliases:
                    if isinstance(a, str) and a.strip():
                        entry["aliases"].add(a.strip())
            # description 累积(后面取最长)
            desc = (ent.get("description") or "").strip()
            if desc:
                entry["descriptions"].append(desc)
            # chunk 计数(用 set 去重 — 同 chunk 多次出现仍算 1)
            entry["chunks"].add(int(row["chunk_index"]))

    # === Sprint 6.A2 FOCUS.11(2026-05-22):跨 chunk LOCATION 归一 ===
    # 治"LLM 在 chunk 1 写'雪国' / chunk 2 写'雪国温泉村' / chunk 3 写'雪国温泉客栈',
    # scene_extractor 严格 name 匹配 → 每名只 1 chunk → 全标 1 章 + 场景数虚高"
    # 镜像 _post_merge_alias_dedup 的语义,但针对 LOCATION:
    #   ① 短 name 是长 name 的真子串(≥ 2 字)→ 合并
    #   ② 一个 name 在另一条的 aliases 里 → 合并
    #   ③ aliases 集合有交集 → 合并
    # 合并后:chunks 取并集,aliases 累积,description 选最长。
    _merge_location_aliases(agg)

    # 3. UPSERT 到 project_scenes
    now = iso_now()
    written = 0
    for name, entry in agg.items():
        aliases_list = sorted(entry["aliases"])
        # 取最长 description(信息量最大);若全空 = 空字符串
        descs = entry["descriptions"]
        description = max(descs, key=len) if descs else ""
        appearance = len(entry["chunks"])

        # UPSERT(SQLite ON CONFLICT)
        # 若已有同 (project_id, name) 行:更新 aliases / description / appearance_count
        scene_id = uuid.uuid4().hex
        execute(
            conn,
            """INSERT INTO project_scenes
                (id, project_id, name, aliases_json, description,
                 appearance_chunk_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(project_id, name) DO UPDATE SET
                   aliases_json=excluded.aliases_json,
                   description=excluded.description,
                   appearance_chunk_count=excluded.appearance_chunk_count,
                   updated_at=excluded.updated_at""",
            (
                scene_id, project_id, name,
                json.dumps(aliases_list, ensure_ascii=False),
                description, appearance, now, now,
            ),
        )
        written += 1

    conn.commit()
    return {
        "scenes_count": written,
        "chunks_processed": len(chunk_rows),
        "raw_locations_seen": raw_count,
    }


def list_scenes_for_project(
    conn: sqlite3.Connection, project_id: str,
) -> list[ProjectScene]:
    """列出项目所有 scene(按出现 chunk 数倒序 — 主战场在最前)。"""
    rows = fetch_all(
        conn,
        """SELECT * FROM project_scenes
           WHERE project_id=?
           ORDER BY appearance_chunk_count DESC, name ASC""",
        (project_id,),
    )
    return [ProjectScene.from_row(r) for r in rows]


def get_scene_by_name(
    conn: sqlite3.Connection, project_id: str, name: str,
) -> Optional[ProjectScene]:
    """按 name 查项目内的 scene(供 character_affinity 用)。"""
    row = fetch_one(
        conn,
        "SELECT * FROM project_scenes WHERE project_id=? AND name=?",
        (project_id, name),
    )
    return ProjectScene.from_row(row) if row else None


def get_scene_by_id(
    conn: sqlite3.Connection, scene_id: str,
) -> Optional[ProjectScene]:
    """按 id 查 scene(无 project 范围;鉴权由 router 通过 project_id 做)。"""
    row = fetch_one(
        conn, "SELECT * FROM project_scenes WHERE id=?", (scene_id,),
    )
    return ProjectScene.from_row(row) if row else None


# ============================================================
# M8.C(2026-05-21)场景编辑 + 合并 + 删除
# ============================================================

class SceneNotFound(Exception):
    pass


class SceneConflict(Exception):
    """合并 / rename 时 target name 冲突等。"""


# ============================================================
# INIT.6(2026-05-21)初始态用户手动创建场景
# ============================================================

def create_scene(
    conn: sqlite3.Connection,
    project_id: str,
    name: str,
    description: str = "",
    aliases: Optional[list[str]] = None,
) -> ProjectScene:
    """初始态用户从零创建场景。

    与 extract 抽出的场景区别:
      - appearance_chunk_count = 0(用户手动加,无原文 chunk)
      - origin_simulation_id = None(原作语义,跟 extract 同)
      - aliases / description 用户可填

    冲突检查:同 project_id 下不允许 name 重复。
    """
    import json
    import uuid

    from app.services.project_service import iso_now

    name = (name or "").strip()
    if not name:
        raise SceneConflict("name 不能为空")

    # 冲突检查
    existing = get_scene_by_name(conn, project_id, name)
    if existing:
        raise SceneConflict(f"场景名 '{name}' 已存在,换一个名字或合并到现有场景")

    scene_id = uuid.uuid4().hex
    now = iso_now()
    aliases_json = json.dumps(aliases or [], ensure_ascii=False)

    conn.execute(
        """INSERT INTO project_scenes
            (id, project_id, name, aliases_json, description,
             appearance_chunk_count, created_at, updated_at,
             origin_simulation_id)
           VALUES (?, ?, ?, ?, ?, 0, ?, ?, NULL)""",
        (scene_id, project_id, name, aliases_json, description[:500], now, now),
    )
    conn.commit()

    row = fetch_one(conn, "SELECT * FROM project_scenes WHERE id=?", (scene_id,))
    return ProjectScene.from_row(row)


def update_scene_fields(
    conn: sqlite3.Connection,
    scene_id: str,
    project_id_for_auth: str,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    aliases: Optional[list[str]] = None,
) -> ProjectScene:
    """更新 scene 的可编辑字段。

    Raises:
      SceneNotFound: scene 不存在 / 不属于该 project
      SceneConflict: name 冲突(项目内已有同名场景)
    """
    row = fetch_one(
        conn,
        "SELECT * FROM project_scenes WHERE id=? AND project_id=?",
        (scene_id, project_id_for_auth),
    )
    if row is None:
        raise SceneNotFound(f"scene {scene_id} 不存在或不属于 project")

    updates: list[tuple[str, object]] = []
    if name is not None:
        new_name = str(name).strip()[:30]
        if not new_name:
            raise SceneConflict("场景名不能为空")
        if new_name != row["name"]:
            # 检查项目内是否已有同名
            dup = fetch_one(
                conn,
                "SELECT id FROM project_scenes "
                "WHERE project_id=? AND name=? AND id<>?",
                (project_id_for_auth, new_name, scene_id),
            )
            if dup is not None:
                raise SceneConflict(f"项目内已存在场景「{new_name}」")
            updates.append(("name", new_name))
    if description is not None:
        updates.append(("description", str(description).strip()[:500]))
    if aliases is not None:
        cleaned = [str(a).strip()[:30] for a in aliases if a and str(a).strip()][:10]
        updates.append(("aliases_json", json.dumps(cleaned, ensure_ascii=False)))

    if not updates:
        return ProjectScene.from_row(row)

    now = iso_now()
    set_clause = ", ".join(f"{c}=?" for c, _ in updates)
    params = [v for _, v in updates] + [now, scene_id]
    execute(
        conn,
        f"UPDATE project_scenes SET {set_clause}, updated_at=? WHERE id=?",
        tuple(params),
    )
    conn.commit()
    updated_row = fetch_one(
        conn, "SELECT * FROM project_scenes WHERE id=?", (scene_id,),
    )
    return ProjectScene.from_row(updated_row)


def delete_scene(
    conn: sqlite3.Connection,
    scene_id: str,
    project_id_for_auth: str,
) -> None:
    """删除 scene(物理删 row)。

    Raises:
      SceneNotFound
    """
    row = fetch_one(
        conn,
        "SELECT id FROM project_scenes WHERE id=? AND project_id=?",
        (scene_id, project_id_for_auth),
    )
    if row is None:
        raise SceneNotFound(f"scene {scene_id} 不存在或不属于 project")
    execute(conn, "DELETE FROM project_scenes WHERE id=?", (scene_id,))
    conn.commit()


def merge_scenes(
    conn: sqlite3.Connection,
    project_id_for_auth: str,
    source_scene_id: str,
    target_scene_id: str,
) -> ProjectScene:
    """把 source 合并到 target:
    - aliases:source.aliases + source.name 都加到 target.aliases
    - appearance_chunk_count:相加
    - source row 物理删除
    - 返回 target row(更新后)

    注意:outline_scenes / simulation_scenes 的 location 是文本字段(不是 FK),
    若它们引用了 source.name,字符串值留旧名 — 这是 M8.C 的设计权衡:
      ① 改 location 文本会污染已用户编辑的产物
      ② 用户可在 outline 编辑面板手动改 location 文本到新 target name
      ③ 后续推演会自动用 target.name(因为 source 已删)
    """
    if source_scene_id == target_scene_id:
        raise SceneConflict("source 和 target 相同,无法合并")
    src = fetch_one(
        conn,
        "SELECT * FROM project_scenes WHERE id=? AND project_id=?",
        (source_scene_id, project_id_for_auth),
    )
    if src is None:
        raise SceneNotFound(f"source scene {source_scene_id} 不存在")
    tgt = fetch_one(
        conn,
        "SELECT * FROM project_scenes WHERE id=? AND project_id=?",
        (target_scene_id, project_id_for_auth),
    )
    if tgt is None:
        raise SceneNotFound(f"target scene {target_scene_id} 不存在")

    # 合并 aliases:tgt.aliases + src.aliases + src.name(去重)
    def _safe_list(raw: object) -> list[str]:
        if not raw or not isinstance(raw, str):
            return []
        try:
            parsed = json.loads(raw)
            return [str(x) for x in parsed if x] if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    tgt_aliases = _safe_list(tgt["aliases_json"])
    src_aliases = _safe_list(src["aliases_json"])
    merged_aliases = list(tgt_aliases)
    for a in [src["name"]] + src_aliases:
        if a and a not in merged_aliases and a != tgt["name"]:
            merged_aliases.append(a)
    merged_aliases = merged_aliases[:15]    # 上限防膨胀

    # 合并 appearance_chunk_count
    merged_count = int(tgt["appearance_chunk_count"]) + int(src["appearance_chunk_count"])

    now = iso_now()
    execute(
        conn,
        "UPDATE project_scenes "
        "SET aliases_json=?, appearance_chunk_count=?, updated_at=? "
        "WHERE id=?",
        (
            json.dumps(merged_aliases, ensure_ascii=False),
            merged_count, now, target_scene_id,
        ),
    )
    execute(conn, "DELETE FROM project_scenes WHERE id=?", (source_scene_id,))
    conn.commit()

    updated_row = fetch_one(
        conn, "SELECT * FROM project_scenes WHERE id=?", (target_scene_id,),
    )
    return ProjectScene.from_row(updated_row)


# ======================================================================
# Sprint 6.A2 FOCUS.11(2026-05-22):跨 chunk LOCATION 归一
# ======================================================================

def _merge_location_aliases(agg: dict[str, dict]) -> None:
    """In-place 合并 agg 字典中的 LOCATION,治"同一地点多 chunk 起不同名字"分裂。

    实测痛点(雪国 3 chunks):
      Chunk 1: 雪国 / 信号所 / 客栈 / 驹子的房间
      Chunk 2: 雪国温泉村 / 客栈房间 / 滑雪场 / ...
      Chunk 3: 雪国温泉客栈 / 驹子家 / ...
    LLM 跨 chunk 没记忆 → 同地点起 2-3 个名字 → scene_extractor 严格 name 匹配
    → 每名只 1 chunk → 全标"1 章"+ 场景数虚高(21 个伪场景,实际 8-10 个)。

    合并规则(对齐 _post_merge_alias_dedup 的 PERSON 版本):
      ① 短 name 是长 name 的真子串,且短 ≥ 2 字 → 合并(雪国 ⊂ 雪国温泉村)
      ② 一个 name 在另一条的 aliases 里 → 合并
      ③ aliases 集合有交集 → 合并

    合并后:chunks 取并集(appearance_chunk_count 真实反映跨段出现),
            aliases 累积,description 选最长。
    选 root:出现次数(chunks 数)+ aliases 数权重高的胜出。
    """
    names = list(agg.keys())
    if len(names) < 2:
        return

    # 1. union-find 数据结构
    parent: dict[str, str] = {n: n for n in names}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]   # 路径压缩
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        # 选 score 高的做 root:chunks 数权重最高 + aliases 数辅助
        ea, eb = agg[ra], agg[rb]
        score_a = len(ea["chunks"]) * 10 + len(ea["aliases"])
        score_b = len(eb["chunks"]) * 10 + len(eb["aliases"])
        if score_a >= score_b:
            parent[rb] = ra
        else:
            parent[ra] = rb

    # 2. 对所有 name pair 应用 3 个合并条件
    for i, name_a in enumerate(names):
        for name_b in names[i + 1:]:
            # 条件 ①:真子串(短 ≥ 2 字)
            shorter, longer = sorted((name_a, name_b), key=len)
            if len(shorter) >= 2 and shorter != longer and shorter in longer:
                union(name_a, name_b)
                continue
            # 条件 ②:name 在 aliases 里
            if name_a in agg[name_b]["aliases"]:
                union(name_a, name_b)
                continue
            if name_b in agg[name_a]["aliases"]:
                union(name_a, name_b)
                continue
            # 条件 ③:aliases 交集非空
            if agg[name_a]["aliases"] & agg[name_b]["aliases"]:
                union(name_a, name_b)
                continue

    # 3. 按 root 分组
    groups: dict[str, list[str]] = {}
    for n in names:
        groups.setdefault(find(n), []).append(n)

    # 4. 合并 members 数据到 root,删除非 root 条目
    for root, members in groups.items():
        if len(members) == 1:
            continue
        root_entry = agg[root]
        for m in members:
            if m == root:
                continue
            m_entry = agg[m]
            # name(被合并的)进 root.aliases
            root_entry["aliases"].add(m)
            # m.aliases 累积进 root.aliases(但跳过 root.name 本身)
            root_entry["aliases"].update(m_entry["aliases"])
            root_entry["aliases"].discard(root)
            # description 累积
            root_entry["descriptions"].extend(m_entry["descriptions"])
            # chunks 取并集(关键 — 这才让 appearance_chunk_count 跨段累加)
            root_entry["chunks"].update(m_entry["chunks"])
            # 删除被合并的 name
            del agg[m]
