"""角色合并服务(Sprint 6.A2 FOCUS,2026-05-21)。

用户痛点:LLM 抽图谱时偶发把同一人物拆成两条 PERSON(如《挪威的森林》"我" vs
"渡边" / 《雪国》"艺妓" vs "驹子")。即便 prompt v4 加强了归一铁律,LLM 仍会偶发
失误。本服务给用户兜底 — 手动合并两张卡为一张,语义对齐 scene_extractor.merge_scenes。

合并语义(对齐 project_scenes 合并):
  ① target.aliases += source.aliases + [source.name](去重 + 上限 15)
  ② 所有外键引用 source.id 的表 reassign 到 target.id:
     - relationships.source_id / target_id(去重避 self-loop)
     - events.participants(JSON 数组)
     - action_ledger / emotional_state_chain / character_arcs / canonical_entities 等
  ③ 文本字段引用 source.name 的(outline_scenes.characters_present JSON 等)→
     与 scene 合并保持一致:**留旧名,用户后续手动改**(改文本会污染产物)
  ④ 物理删除 source row
  ⑤ 返回 target(更新后)

注意:
  - 同项目校验 — source / target 必须在同一 project_id 下
  - source != target — 防自合并
  - 用户在主角面板能感知:刷新后 source 卡消失,target 卡 aliases 多出 source.name
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.models.character import Character
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)


# === 异常 ===

class CharacterNotFound(Exception):
    """source / target 角色不存在,或不属于当前 project_id。"""


class CharacterMergeConflict(Exception):
    """source == target / 跨项目合并 等语义错误。"""


# === 主入口 ===

def merge_characters(
    conn: sqlite3.Connection,
    project_id_for_auth: str,
    source_character_id: str,
    target_character_id: str,
) -> Character:
    """把 source 角色合并到 target,返回更新后的 target。

    级联清理:
      - aliases:tgt.aliases + src.aliases + src.name(去重 + 上限 15)
      - relationships:source/target 列 reassign 到 target_id,去重 + self-loop 删
      - events.participants:JSON 数组里的 source.id 替换为 target.id,去重
      - 其他直接 FK 引用 character_id 的表 → ON DELETE CASCADE 自动级联(删 source 即删)
      - outline_scenes.characters_present 等 JSON 文本字段引用 source.name → 留旧名
        (与 scene 合并保持一致,改文本会污染产物;用户可手动改 location 文本)

    raise:
      CharacterMergeConflict — source==target
      CharacterNotFound — 任一角色不存在或不属于此项目
    """
    if source_character_id == target_character_id:
        raise CharacterMergeConflict("source 和 target 是同一个角色,无法合并")

    src_row = fetch_one(
        conn,
        "SELECT * FROM characters WHERE id=? AND project_id=?",
        (source_character_id, project_id_for_auth),
    )
    if src_row is None:
        raise CharacterNotFound(
            f"source 角色 {source_character_id} 不存在或不属于此项目"
        )
    tgt_row = fetch_one(
        conn,
        "SELECT * FROM characters WHERE id=? AND project_id=?",
        (target_character_id, project_id_for_auth),
    )
    if tgt_row is None:
        raise CharacterNotFound(
            f"target 角色 {target_character_id} 不存在或不属于此项目"
        )

    # ───── ① 合并 aliases ─────
    def _safe_list(raw: object) -> list[str]:
        if not raw or not isinstance(raw, str):
            return []
        try:
            parsed = json.loads(raw)
            return [str(x) for x in parsed if x] if isinstance(parsed, list) else []
        except (json.JSONDecodeError, TypeError):
            return []

    tgt_aliases = _safe_list(_try_get(tgt_row, "aliases_json"))
    src_aliases = _safe_list(_try_get(src_row, "aliases_json"))
    src_name = src_row["name"]
    tgt_name = tgt_row["name"]

    merged: list[str] = list(tgt_aliases)
    for candidate in [src_name] + src_aliases:
        if candidate and candidate not in merged and candidate != tgt_name:
            merged.append(candidate)
    merged = merged[:15]   # 上限防膨胀(对齐 project_scenes)

    # ───── ② 关系表 reassign:source_id / target_id ─────
    # 先把指向 src 的关系改指向 tgt;再去重(可能产生 self-loop tgt→tgt 或与已存在关系重复)
    _reassign_relationships(conn, source_character_id, target_character_id)

    # ───── ③ events.participants(JSON 数组)reassign + 去重 ─────
    _reassign_events_participants(
        conn, project_id_for_auth, source_character_id, target_character_id
    )

    # ───── ④ 其他 character_id 引用表 reassign(非 FK 级联类)─────
    # canonical_entities / action_ledger / emotional_state_chain / character_arcs 等
    # 这些表如果有 character_id 列且不是 FK ON DELETE,需要主动 reassign;
    # 若有 FK ON DELETE CASCADE,会随 src 删除一起清掉,跳过 reassign(避免冗余)
    _reassign_optional_tables(
        conn, source_character_id, target_character_id
    )

    # ───── ⑤ 写回 target.aliases_json ─────
    now = iso_now()
    execute(
        conn,
        "UPDATE characters SET aliases_json=?, updated_at=? WHERE id=?",
        (json.dumps(merged, ensure_ascii=False), now, target_character_id),
    )

    # ───── ⑥ 物理删除 source(剩余 FK ON DELETE CASCADE 表会被自动清理)─────
    execute(conn, "DELETE FROM characters WHERE id=?", (source_character_id,))
    conn.commit()

    # 重新读 target,返回最新版
    tgt_after = fetch_one(
        conn,
        "SELECT * FROM characters WHERE id=?",
        (target_character_id,),
    )
    if tgt_after is None:
        # 极端情况下 target 在 DELETE 阶段被某 FK 级联误删(不应该发生)
        raise CharacterNotFound(
            f"合并完成但 target {target_character_id} 已不可读取,数据可能损坏"
        )
    return Character.from_row(tgt_after)


# === 内部 helpers ===

def _try_get(row: sqlite3.Row, col: str, default: Optional[object] = None) -> object:
    """从 Row 读列,缺列返回 default(老 DB 兼容)。"""
    try:
        return row[col]
    except (KeyError, IndexError):
        return default


def _reassign_relationships(
    conn: sqlite3.Connection, source_id: str, target_id: str
) -> None:
    """relationships 中 source_id / target_id 引用 source 的全部改为 target。

    流程:
      ① 找出所有受影响行(source 或 target 列指向 source_id)
      ② 逐行计算"重写后的 source_id / target_id"
      ③ 如果重写后 source==target(self-loop)→ 删此行
      ④ 否则 UPDATE 此行;后续依赖 SQL UNIQUE / 业务去重的话,跨用户语义不会冲突
         (relationships 表无 UNIQUE(source_id, target_id),允许多条;不去重)

    why 不简单一句 UPDATE … WHERE source_id=?:因为 source_id / target_id 都可能引用,
    需要同时考虑 4 种 case(src→A / A→src / src→src 极端)。
    """
    rows = fetch_all(
        conn,
        "SELECT id, source_id, target_id FROM relationships "
        "WHERE source_id=? OR target_id=?",
        (source_id, source_id),
    )
    for r in rows:
        new_src = target_id if r["source_id"] == source_id else r["source_id"]
        new_tgt = target_id if r["target_id"] == source_id else r["target_id"]
        if new_src == new_tgt:
            # self-loop 无意义,删此关系
            execute(conn, "DELETE FROM relationships WHERE id=?", (r["id"],))
        else:
            execute(
                conn,
                "UPDATE relationships SET source_id=?, target_id=? WHERE id=?",
                (new_src, new_tgt, r["id"]),
            )


def _reassign_events_participants(
    conn: sqlite3.Connection, project_id: str, source_id: str, target_id: str
) -> None:
    """events.participants 是 JSON 数组(`["c1","c2",...]`)。

    把数组里的 source_id 替换为 target_id;若 target_id 已在 → 去重。
    why not SQL:SQLite JSON1 函数兼容性 + 跨 row 修改容易写错,保险用 Python。
    """
    rows = fetch_all(
        conn,
        "SELECT id, participants FROM events WHERE project_id=?",
        (project_id,),
    )
    for ev in rows:
        try:
            old_parts = json.loads(ev["participants"]) if ev["participants"] else []
            if not isinstance(old_parts, list):
                old_parts = []
        except (TypeError, json.JSONDecodeError):
            old_parts = []

        if source_id not in old_parts:
            continue

        # 替换 + 去重保序
        new_parts: list[str] = []
        for pid in old_parts:
            replaced = target_id if pid == source_id else pid
            if replaced and replaced not in new_parts:
                new_parts.append(replaced)

        execute(
            conn,
            "UPDATE events SET participants=? WHERE id=?",
            (json.dumps(new_parts, ensure_ascii=False), ev["id"]),
        )


def _reassign_optional_tables(
    conn: sqlite3.Connection, source_id: str, target_id: str
) -> None:
    """对非 FK 级联表显式 reassign character_id 引用。

    这些表 character_id 列**不带** ON DELETE CASCADE,删 source 不会自动清,
    必须先 UPDATE 把 character_id 改成 target_id。

    覆盖范围(以表存在性安全 try-except,兼容老 db / 未迁移环境):
      - canonical_entities(M5.1) — 实体注册
      - action_ledger(M5.2) — 角色动作流水
      - emotional_state_chain(M5.6) — 角色情绪链
      - character_arcs(M9.A.1) — 角色弧光时间线
      - foreshadow_ledger(M9.A.1) — 伏笔账本(owner_character_id)

    注意:有些表已有 ON DELETE CASCADE(如 character_refinements / canonical_audit
    等),删 source 时自动清,这里**不主动 reassign**(LLM 给 source 的建议合并到
    target 没意义,直接清更干净)。
    """
    # canonical_entities — 实体注册,character_id 引用
    _safe_update(
        conn,
        "UPDATE canonical_entities SET character_id=? "
        "WHERE character_id=? AND entity_type='character'",
        (target_id, source_id),
    )
    # action_ledger
    _safe_update(
        conn,
        "UPDATE action_ledger SET actor_character_id=? WHERE actor_character_id=?",
        (target_id, source_id),
    )
    # emotional_state_chain
    _safe_update(
        conn,
        "UPDATE emotional_state_chain SET character_id=? WHERE character_id=?",
        (target_id, source_id),
    )
    # character_arcs
    _safe_update(
        conn,
        "UPDATE character_arcs SET character_id=? WHERE character_id=?",
        (target_id, source_id),
    )
    # foreshadow_ledger — owner_character_id(伏笔的责任角色)
    _safe_update(
        conn,
        "UPDATE foreshadow_ledger SET owner_character_id=? WHERE owner_character_id=?",
        (target_id, source_id),
    )


def _safe_update(conn: sqlite3.Connection, sql: str, params: tuple) -> None:
    """执行 UPDATE,表 / 列不存在时静默(log info)→ 不阻断合并主流程。

    这层兜底让老 db / 部分 migration 未跑的环境也能用合并(以丢失少量 reassign 为代价
    换鲁棒性);新 db 全表存在 → 都成功。
    """
    try:
        execute(conn, sql, params)
    except sqlite3.OperationalError as e:
        # 大多是 "no such table" / "no such column" — 跳过即可
        logger.info("character_merger: skipping optional reassign (%s)", e)
