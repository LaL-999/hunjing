"""Sprint 6.A2 M3.A(2026-05-18)— Agent Runtime 基础设施。

为 M3.B 灵魂续写主循环提供:
  - load_agent_context(): 拉单 agent 的完整上下文(档案 + 私有记忆 + 关系 phases)
  - record_memory(): 落库一条 agent 私有记忆
  - list_agent_memories(): 拉 agent 在指定 scene 范围的记忆
  - share_witnessed_memory(): 把一条对白/行动复制成"在场角色"的 witnessed 记忆

设计原则(对齐用户拍板"最高标准铁律"):
  - **信息不对称严格守护**:agent 只看自己 character_id 的 memory(包括 witnessed)
  - **记忆按 scene_index 切片**:跨幕 reflection 时能回看前几幕
  - **无上帝视角 history**:旧 simulation_service 的共享 history 列表在 evolution 路径**不复用**
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.models.agent_private_memory import AgentPrivateMemory, MemoryType
from app.models.character import Character
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    """单 agent 在某幕的完整上下文 — 喂给 LLM 的素材包。"""
    character_id: str
    character_name: str
    # 档案 4 字段(M1 / 6.A1):identity / personality / quotes / no_go_list
    identity: str
    personality: str
    quotes: list[str]
    no_go_list: list[str]
    is_protagonist: bool
    # 私有记忆(按 scene_index 升序;只含本 agent 的,绝不含他人)
    memories: list[AgentPrivateMemory] = field(default_factory=list)
    # 与他人当前关系阶段(M1 relationship_phases)
    # { other_char_id: { name: str, phase_type: str, phase_anchor: str } }
    relationship_phases: dict = field(default_factory=dict)


# ============================================================
# load_agent_context — 给 LLM 喂素材
# ============================================================

def load_agent_context(
    conn: sqlite3.Connection,
    simulation_id: str,
    character_id: str,
    *,
    up_to_scene_index: Optional[int] = None,
    memory_limit: int = 50,
) -> AgentContext:
    """拉单 agent 完整上下文(M3.B 主循环每幕开始时调)。

    Args:
      simulation_id: 当前推演 id
      character_id: 这个 agent 的 id
      up_to_scene_index: 只取 scene_index < N 的记忆(看到本幕之前的);None=所有
      memory_limit: 记忆条数上限(防 LLM context 爆),取最近的 N 条

    Returns:
      AgentContext:LLM ready 素材包
    """
    # 1. 拉 character 档案
    char_row = fetch_one(
        conn,
        "SELECT * FROM characters WHERE id=?",
        (character_id,),
    )
    if char_row is None:
        # inferred 角色(character_id 'script:hash' 之类)— 后续支持,M3.A 先抛
        raise ValueError(
            f"character {character_id} 未找到 — M3.A 暂不支持 inferred 角色"
        )
    char = Character.from_row(char_row)

    # 2. 拉私有记忆(按 scene_index 升序;限 memory_limit 取最近的)
    if up_to_scene_index is not None:
        memories = fetch_all(
            conn,
            """SELECT * FROM agent_private_memories
               WHERE simulation_id=? AND character_id=? AND scene_index < ?
               ORDER BY scene_index ASC, created_at ASC
               LIMIT ?""",
            (simulation_id, character_id, up_to_scene_index, memory_limit),
        )
    else:
        memories = fetch_all(
            conn,
            """SELECT * FROM agent_private_memories
               WHERE simulation_id=? AND character_id=?
               ORDER BY scene_index ASC, created_at ASC
               LIMIT ?""",
            (simulation_id, character_id, memory_limit),
        )
    memory_list = [AgentPrivateMemory.from_row(r) for r in memories]

    # 3. 拉与他人当前关系阶段(M1 relationship_phases.current_phase_id)
    # 取该角色作为 source 或 target 的关系 + 其 current_phase 内容
    rel_rows = fetch_all(
        conn,
        """SELECT r.id AS rel_id,
                  r.source_id, r.target_id,
                  r.type AS fallback_type, r.current_phase_id,
                  rp.type AS phase_type, rp.start_anchor AS phase_anchor
           FROM relationships r
           LEFT JOIN relationship_phases rp ON rp.id = r.current_phase_id
           WHERE (r.source_id=? OR r.target_id=?)""",
        (character_id, character_id),
    )
    # 拉 character_id → name 反查
    other_ids = set()
    for r in rel_rows:
        other_id = r["target_id"] if r["source_id"] == character_id else r["source_id"]
        other_ids.add(other_id)
    name_by_id: dict[str, str] = {}
    if other_ids:
        placeholders = ",".join(["?"] * len(other_ids))
        name_rows = fetch_all(
            conn,
            f"SELECT id, name FROM characters WHERE id IN ({placeholders})",
            tuple(other_ids),
        )
        name_by_id = {r["id"]: r["name"] for r in name_rows}

    relationship_phases: dict = {}
    for r in rel_rows:
        other_id = r["target_id"] if r["source_id"] == character_id else r["source_id"]
        effective_type = r["phase_type"] or r["fallback_type"]
        if not effective_type:
            continue
        relationship_phases[other_id] = {
            "name": name_by_id.get(other_id, "?"),
            "phase_type": effective_type,
            "phase_anchor": r["phase_anchor"] or "",
            "is_evolved": r["current_phase_id"] is not None,
        }

    return AgentContext(
        character_id=char.id,
        character_name=char.name,
        identity=char.identity,
        personality=char.personality,
        quotes=char.quotes,
        no_go_list=char.no_go_list,
        is_protagonist=char.is_protagonist,
        memories=memory_list,
        relationship_phases=relationship_phases,
    )


# ============================================================
# record_memory — 落库一条记忆
# ============================================================

def record_memory(
    conn: sqlite3.Connection,
    *,
    simulation_id: str,
    character_id: str,
    scene_index: int,
    memory_type: MemoryType,
    content: str,
    other_chars: Optional[list[str]] = None,
) -> str:
    """落库一条 agent 私有记忆,返回 memory id。

    调用方:M3.B 主循环每轮产出后调,把 reflection / dialogue / action 落库。
    witnessed 类型由 share_witnessed_memory 派生。
    """
    mem_id = uuid.uuid4().hex
    now = iso_now()
    execute(
        conn,
        """INSERT INTO agent_private_memories
            (id, simulation_id, character_id, scene_index,
             memory_type, content, other_chars_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            mem_id, simulation_id, character_id, scene_index,
            memory_type, content,
            json.dumps(other_chars or [], ensure_ascii=False),
            now,
        ),
    )
    conn.commit()
    return mem_id


# ============================================================
# share_witnessed_memory — 在场角色复制一份记忆
# ============================================================

def share_witnessed_memory(
    conn: sqlite3.Connection,
    *,
    simulation_id: str,
    speaker_character_id: str,
    witnesses_character_ids: list[str],
    scene_index: int,
    original_memory_type: MemoryType,
    content: str,
) -> int:
    """speaker 产出 dialogue / action 后,在场的其他角色应能"witnessed"。

    给每个 witness 落一条 witnessed 类型记忆(other_chars 含 speaker_id)。
    返回写入条数。

    严格守护信息不对称:
      - 只有 witnesses_character_ids 里的人能拿到 witnessed 记忆
      - 不在场的角色绝不写(防上帝视角)
      - 不复制 reflection 类型(reflection 本就是私有心理活动)
    """
    if original_memory_type == "reflection":
        return 0    # reflection 不外溢
    if not witnesses_character_ids:
        return 0

    now = iso_now()
    written = 0
    for w_id in witnesses_character_ids:
        if w_id == speaker_character_id:
            continue   # speaker 自己已有 dialogue/action 记忆,跳过
        execute(
            conn,
            """INSERT INTO agent_private_memories
                (id, simulation_id, character_id, scene_index,
                 memory_type, content, other_chars_json, created_at)
               VALUES (?, ?, ?, ?, 'witnessed', ?, ?, ?)""",
            (
                uuid.uuid4().hex, simulation_id, w_id, scene_index,
                content,
                json.dumps([speaker_character_id], ensure_ascii=False),
                now,
            ),
        )
        written += 1
    conn.commit()
    return written


# ============================================================
# 工具函数:统计 / 查询
# ============================================================

def list_agent_memories(
    conn: sqlite3.Connection,
    simulation_id: str,
    character_id: str,
    *,
    scene_index: Optional[int] = None,
) -> list[AgentPrivateMemory]:
    """列出 agent 记忆。scene_index=None → 全部;否则只该幕的。"""
    if scene_index is not None:
        rows = fetch_all(
            conn,
            """SELECT * FROM agent_private_memories
               WHERE simulation_id=? AND character_id=? AND scene_index=?
               ORDER BY created_at ASC""",
            (simulation_id, character_id, scene_index),
        )
    else:
        rows = fetch_all(
            conn,
            """SELECT * FROM agent_private_memories
               WHERE simulation_id=? AND character_id=?
               ORDER BY scene_index ASC, created_at ASC""",
            (simulation_id, character_id),
        )
    return [AgentPrivateMemory.from_row(r) for r in rows]


def count_agent_memories(
    conn: sqlite3.Connection,
    simulation_id: str,
    character_id: Optional[str] = None,
) -> int:
    """统计 memory 总数(给 UI 展示 / 测试用)。"""
    if character_id is not None:
        row = fetch_one(
            conn,
            """SELECT COUNT(*) AS cnt FROM agent_private_memories
               WHERE simulation_id=? AND character_id=?""",
            (simulation_id, character_id),
        )
    else:
        row = fetch_one(
            conn,
            "SELECT COUNT(*) AS cnt FROM agent_private_memories WHERE simulation_id=?",
            (simulation_id,),
        )
    return int(row["cnt"]) if row else 0
