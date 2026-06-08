"""Sprint 6.A2 M6(2026-05-20)— Outline Orchestrator(按 outline 逐幕跑)。

替代 agent_evolution_engine 中的"自由 scene_picker"环节 — 每幕的 location /
characters / key_events / key_props 全部从 outline_scenes 读,narrator 必须
按图纸生成。

核心机制:
  - run_outline_orchestrated_simulation(sim_id) 是主入口
  - 内部复用 agent_evolution_engine 的 _run_scene_dialogue / _narrator_compose 等
  - 在 _pick_scene 位置改为 build_scene_decision_from_outline(从 outline_scene 直接取)
  - 在 _summon_agents 位置改为 load_agents_from_outline(从 characters_present 直接拉)
  - narrator 调用前,hard_constraints 增 prepend "本幕图纸 outline_scene 完整块"

设计原则:
  - **完全复用** M3-M5 底层引擎(私有记忆 / RAG / world_facts / canonical_entities /
    action_ledger / emotional_states / hard_constraints / multi-sample voting /
    consistency_checker / phrase_blacklist / round_visible_actions)
  - **替代**部分仅限"幕级决策":选场景 / 召唤 agent;这两件事现在由 outline 锁定
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Optional

from app.db import fetch_all, fetch_one
from app.models.character import Character
from app.models.outline_scene import OutlineScene

logger = logging.getLogger(__name__)


def build_scene_decision_from_outline(outline_scene: OutlineScene):
    """从 outline_scene 直接构造 SceneDecision,**不调 LLM**。

    Returns:
      SceneDecision(import 自 agent_evolution_engine,延迟 import 防循环)
    """
    # 延迟 import 防循环
    from app.services.agent_evolution_engine import SceneDecision

    # outline_scene.location 可能与项目场景同名,也可能是 LLM 自造
    # 按 location 是否在 project_scenes 里来定 scene_source — 这里简化为 llm_created
    # (orchestrator 调用方可在外部根据 project_scenes 表反查覆盖此值)
    return SceneDecision(
        scene_name=outline_scene.location,
        scene_source="llm_created",  # outline 标准化输出,统一标 llm_created
        time_anchor=outline_scene.time_anchor,
        reasoning=f"按 outline_scene #{outline_scene.scene_index + 1} 图纸执行:"
                  f"{outline_scene.scene_summary[:80]}",
        transition_type="physical_move",  # M11.E:outline 路径默认物理移动
    )


def load_agents_from_outline_scene(
    conn: sqlite3.Connection,
    outline_scene: OutlineScene,
    project_id: str,
) -> list[Character]:
    """从 outline_scene.characters_present 直接拉 Character 列表,**不调 affinity**。

    若某 character_id 不存在 → 跳过(防 outline 阶段误标了不存在的 id)。
    """
    if not outline_scene.characters_present:
        return []
    placeholders = ",".join("?" for _ in outline_scene.characters_present)
    rows = fetch_all(
        conn,
        f"""SELECT * FROM characters
            WHERE id IN ({placeholders}) AND project_id=?""",
        (*outline_scene.characters_present, project_id),
    )
    # 按 outline 给的顺序排
    by_id = {r["id"]: Character.from_row(r) for r in rows}
    return [by_id[cid] for cid in outline_scene.characters_present if cid in by_id]


def build_outline_constraints_block(outline_scene: OutlineScene) -> str:
    """生成本幕图纸的硬铁律 block,prepend 到 narrator / agent_dialogue prompt。

    格式:
      【本幕图纸 outline_scene #1】
        · 物理位置:韩紫雨家卧室(整幕统一,严禁瞬移)
        · 时间锚:深夜
        · 本幕概要:...
        · 必须发生的关键事件:...
        · 关键道具(属性已锁,严禁覆盖):...
        · 上幕连接:...
        ⚠ 铁律:...
    """
    return outline_scene.to_prompt_block()


def load_outline_scenes_for_sim(
    conn: sqlite3.Connection,
    simulation_id: str,
) -> Optional[list[OutlineScene]]:
    """拉某 sim 的 outline_scenes,按 scene_index 升序。

    Returns:
      非空列表 — 该 sim 有已批准 outline + N 幕
      None — sim 无 outline / outline 未批准 → 走原 evolution 路径
    """
    # 拉 outline 主表,验证 state='approved' or 'generating'
    outline_row = fetch_one(
        conn,
        """SELECT id, state FROM simulation_outlines
           WHERE simulation_id=?""",
        (simulation_id,),
    )
    if outline_row is None:
        return None
    if outline_row["state"] not in ("approved", "generating", "done"):
        # 还未批准 / 失败 / 起草中 → 不走 outline 路径
        return None

    rows = fetch_all(
        conn,
        """SELECT * FROM outline_scenes
           WHERE outline_id=?
           ORDER BY scene_index ASC""",
        (outline_row["id"],),
    )
    if not rows:
        return None
    return [OutlineScene.from_row(r) for r in rows]


def mark_outline_state(
    conn: sqlite3.Connection,
    simulation_id: str,
    new_state: str,
    *,
    error_message: Optional[str] = None,
) -> None:
    """设 outline.state(generating / done / failed)。"""
    from app.services.project_service import iso_now
    from app.db import execute
    now = iso_now()
    if error_message:
        execute(
            conn,
            """UPDATE simulation_outlines
               SET state=?, error_message=?, updated_at=?
               WHERE simulation_id=?""",
            (new_state, error_message[:300], now, simulation_id),
        )
    else:
        execute(
            conn,
            """UPDATE simulation_outlines
               SET state=?, updated_at=?
               WHERE simulation_id=?""",
            (new_state, now, simulation_id),
        )
    conn.commit()


def mark_outline_scene_state(
    conn: sqlite3.Connection,
    outline_id: str,
    scene_index: int,
    new_state: str,
    *,
    generated_simulation_scene_id: Optional[str] = None,
    error_message: Optional[str] = None,
) -> None:
    """设某 outline_scene.state(running / done / failed)。"""
    from app.services.project_service import iso_now
    from app.db import execute
    now = iso_now()
    if generated_simulation_scene_id:
        execute(
            conn,
            """UPDATE outline_scenes
               SET state=?, generated_simulation_scene_id=?,
                   error_message=?, updated_at=?
               WHERE outline_id=? AND scene_index=?""",
            (new_state, generated_simulation_scene_id,
             error_message, now, outline_id, scene_index),
        )
    else:
        execute(
            conn,
            """UPDATE outline_scenes
               SET state=?, error_message=?, updated_at=?
               WHERE outline_id=? AND scene_index=?""",
            (new_state, error_message, now, outline_id, scene_index),
        )
    conn.commit()
