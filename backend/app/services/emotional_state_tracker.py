"""Sprint 6.A2 M5.6(2026-05-20)— Character Emotional State Tracker(角色情绪链)。

每幕 narrator 合稿后调用,LLM 抽每个在场角色的本幕末 8 维情绪向量。

8 维情绪(Plutchik 简化):
  joy / sadness / anger / fear / surprise / disgust / trust / anticipation
  每维 0-10 整数

跨幕连续性约束:
  - consistency_checker 看到"上幕末 fear=9 → 本幕 fear=2"突变 ≥ 5 档
    且无剧情铺垫 → 判 EMOTIONAL_DISCONTINUITY critical

API:
  - track_emotional_states(conn, sim, scene_index, narrative, agents) → tuple[int, dict]
  - get_latest_emotional_state(conn, sim_id, character_id) → Optional[CharacterEmotionalState]
    返回某角色最新一幕的情绪(给下幕 narrator 注入用);若该角色还没有情绪记录返 None
  - list_latest_emotional_states_for_scene(conn, sim_id, scene_index, agents) →
      dict[character_id, CharacterEmotionalState]
    给定 scene_index,返回本幕在场角色的"上一次情绪记录"(scene_index < N 的最大那条)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.models.character import Character
from app.models.emotional_state import EMOTION_KEYS, CharacterEmotionalState
from app.models.simulation import Simulation
from app.services.llm_client import call_llm_json
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


def track_emotional_states(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene_index: int,
    narrative_segment: str,
    agents: list[Character],
) -> tuple[int, dict]:
    """从一幕 narrative_segment 抽各角色本幕末情绪,落库。

    Returns: (inserted_count, llm_usage)
    """
    if not narrative_segment or len(narrative_segment.strip()) < 50:
        return 0, {"input_tokens": 0, "output_tokens": 0}
    if not agents:
        return 0, {"input_tokens": 0, "output_tokens": 0}

    # 拉本幕每个 agent 的"上一次情绪记录"(给 LLM 当 baseline)
    prev_states = list_latest_emotional_states_for_scene(
        conn, sim.id, scene_index, agents,
    )

    name_to_id = {a.name: a.id for a in agents}

    user_input = {
        "scene_index": scene_index,
        "narrative_segment": narrative_segment,
        "agents_present": [
            {"id": a.id, "name": a.name, "is_protagonist": a.is_protagonist}
            for a in agents
        ],
        "previous_emotional_states": [
            {
                "character_name": next(
                    (a.name for a in agents if a.id == cid), "未知"
                ),
                "scene_index": st.scene_index,
                "emotion": st.emotion,
                "rationale": st.rationale,
            }
            for cid, st in prev_states.items()
        ],
    }

    system_prompt = _load_prompt("m5_emotional_state_tracker.md")
    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input,
            max_tokens=1500, temperature=0.2,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"emotional_state_tracker LLM failed sim={sim.id} scene={scene_index}: {e}"
        )
        return 0, {"input_tokens": 0, "output_tokens": 0}

    if not isinstance(parsed, dict):
        return 0, usage
    states_list = parsed.get("emotional_states") or []
    if not isinstance(states_list, list):
        return 0, usage

    inserted = 0
    now = iso_now()
    for st in states_list:
        if not isinstance(st, dict):
            continue
        name = str(st.get("character_name") or "").strip()
        if not name:
            continue
        cid = name_to_id.get(name)
        if not cid:
            # name 不在 agents → 可能 LLM 抽到了 inferred 角色,跳过
            continue
        emotion_raw = st.get("emotion") or {}
        if not isinstance(emotion_raw, dict):
            continue
        # 8 维校验:每个键 0-10 整数,缺失填 0
        emotion_dict = {}
        for k in EMOTION_KEYS:
            v = emotion_raw.get(k)
            if isinstance(v, (int, float)):
                emotion_dict[k] = max(0, min(10, int(v)))
            else:
                emotion_dict[k] = 0
        rationale = str(st.get("rationale") or "").strip()[:300]

        # UPSERT:同角色同幕只一行
        existing = fetch_one(
            conn,
            """SELECT id FROM character_emotional_states
               WHERE simulation_id=? AND character_id=? AND scene_index=?""",
            (sim.id, cid, scene_index),
        )
        emotion_json = json.dumps(emotion_dict, ensure_ascii=False)
        if existing:
            execute(
                conn,
                """UPDATE character_emotional_states
                   SET emotion_json=?, rationale=?, created_at=?
                   WHERE id=?""",
                (emotion_json, rationale, now, existing["id"]),
            )
        else:
            execute(
                conn,
                """INSERT INTO character_emotional_states
                   (id, simulation_id, character_id, scene_index,
                    emotion_json, rationale, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    uuid.uuid4().hex, sim.id, cid, scene_index,
                    emotion_json, rationale, now,
                ),
            )
        inserted += 1

    conn.commit()
    return inserted, usage


def get_latest_emotional_state(
    conn: sqlite3.Connection,
    simulation_id: str,
    character_id: str,
    *,
    before_scene_index: Optional[int] = None,
) -> Optional[CharacterEmotionalState]:
    """拉某角色最新一次情绪记录。

    Args:
      before_scene_index: 若非空,只拉 scene_index < N 的(给本幕看上幕情绪用)
    """
    if before_scene_index is not None:
        row = fetch_one(
            conn,
            """SELECT * FROM character_emotional_states
               WHERE simulation_id=? AND character_id=? AND scene_index < ?
               ORDER BY scene_index DESC LIMIT 1""",
            (simulation_id, character_id, before_scene_index),
        )
    else:
        row = fetch_one(
            conn,
            """SELECT * FROM character_emotional_states
               WHERE simulation_id=? AND character_id=?
               ORDER BY scene_index DESC LIMIT 1""",
            (simulation_id, character_id),
        )
    if row is None:
        return None
    return CharacterEmotionalState.from_row(row)


def list_latest_emotional_states_for_scene(
    conn: sqlite3.Connection,
    simulation_id: str,
    scene_index: int,
    agents: list[Character],
) -> dict[str, CharacterEmotionalState]:
    """给定 scene_index 和在场角色,返回每个角色"上一次情绪记录"(scene_index 之前)。

    用于 emotional_state_tracker 抽本幕情绪时给 LLM 当 baseline,
    也用于 narrator/agent_dialogue 调用前注入"上幕情绪"硬约束。
    """
    out: dict[str, CharacterEmotionalState] = {}
    for a in agents:
        st = get_latest_emotional_state(
            conn, simulation_id, a.id,
            before_scene_index=scene_index,
        )
        if st is not None:
            out[a.id] = st
    return out


def list_character_emotional_states_across_sims(
    conn: sqlite3.Connection,
    project_id: str,
    character_id: str,
) -> list[dict]:
    """Sprint 6.A2 路线图 #2 二期(2026-05-22):跨多次推演的角色情绪轨迹总览。

    一次 JOIN 拉该角色在该项目**所有 sim** 的情绪记录,group by sim_id 输出。
    前端 ProjectView 角色卡展开 "情绪总览" 按钮触发,弹模态后用此数据 chip 列表 + 切换看曲线。

    Returns:
      [
        {
          "sim_id": str,
          "sim_state": str,                  # done / failed / running 等
          "sim_created_at": str,             # ISO,用于前端排序 + chip 显示
          "sim_divergence": str,             # 推演锚点(前端 chip 显短摘要)
          "records": [                       # 该 sim 内按 scene_index ASC
            {character_id, character_name, scene_index, emotion (8 维 dict), rationale},
            ...
          ],
        },
        ...   # 按 sim.created_at DESC(最新推演在前)
      ]

    空数据返 [](角色没参与过任何 evolution sim);character / project 鉴权由调用方做。
    """
    # 排序:s.created_at DESC + s.id DESC(同秒内用 id 兜底,uuid hex 字典序与创建相关性弱
    # 但确定性,防测试 / 用户场景 same-second 抖动);内部按 scene_index ASC
    rows = fetch_all(
        conn,
        """SELECT s.id AS sim_id, s.state AS sim_state, s.created_at AS sim_created_at,
                  s.divergence AS sim_divergence,
                  ces.character_id, c.name AS character_name,
                  ces.scene_index, ces.emotion_json, ces.rationale
           FROM character_emotional_states ces
           JOIN simulations s ON s.id = ces.simulation_id
           LEFT JOIN characters c ON c.id = ces.character_id
           WHERE s.project_id = ? AND ces.character_id = ?
           ORDER BY s.created_at DESC, s.id DESC, ces.scene_index ASC""",
        (project_id, character_id),
    )

    # 按 sim_id group,保留 SQL 返回的 sim 顺序(已按 created_at DESC)
    by_sim: dict[str, dict] = {}
    sim_order: list[str] = []
    for r in rows:
        sid = r["sim_id"]
        if sid not in by_sim:
            by_sim[sid] = {
                "sim_id": sid,
                "sim_state": r["sim_state"],
                "sim_created_at": r["sim_created_at"],
                "sim_divergence": r["sim_divergence"] or "",
                "records": [],
            }
            sim_order.append(sid)
        try:
            raw = json.loads(r["emotion_json"] or "{}")
            if not isinstance(raw, dict):
                raw = {}
        except (json.JSONDecodeError, TypeError):
            raw = {}
        emotion_full = {
            k: int(raw.get(k, 0))
            if isinstance(raw.get(k), (int, float))
            else 0
            for k in EMOTION_KEYS
        }
        by_sim[sid]["records"].append({
            "character_id": r["character_id"],
            "character_name": r["character_name"] or "(已删除)",
            "scene_index": int(r["scene_index"]),
            "emotion": emotion_full,
            "rationale": r["rationale"] or "",
        })

    return [by_sim[sid] for sid in sim_order]


def list_emotional_states_for_visualization(
    conn: sqlite3.Connection,
    simulation_id: str,
) -> list[dict]:
    """Sprint 6.A2 路线图 #2(2026-05-22):前端"角色情绪曲线"可视化数据源。

    拉某 sim 全部 emotional_state 记录(每角色 × 每幕末),LEFT JOIN characters 拼角色名,
    扁平化按 (character_id, scene_index) 排序。前端按 character_id group + 画 8 色折线图。

    被删除的角色 character_name 兜底为"(已删除)" — 避免前端空字符串渲染异常。
    """
    rows = fetch_all(
        conn,
        """SELECT ces.id, ces.character_id, c.name AS character_name,
                  ces.scene_index, ces.emotion_json, ces.rationale, ces.created_at
           FROM character_emotional_states ces
           LEFT JOIN characters c ON c.id = ces.character_id
           WHERE ces.simulation_id = ?
           ORDER BY ces.character_id, ces.scene_index ASC""",
        (simulation_id,),
    )
    out: list[dict] = []
    for r in rows:
        # 复用 model from_row 的 emotion 兜底逻辑,确保 8 维齐全 + 类型干净
        try:
            raw = json.loads(r["emotion_json"] or "{}")
            if not isinstance(raw, dict):
                raw = {}
        except (json.JSONDecodeError, TypeError):
            raw = {}
        emotion_full = {
            k: int(raw.get(k, 0))
            if isinstance(raw.get(k), (int, float))
            else 0
            for k in EMOTION_KEYS
        }
        out.append({
            "character_id": r["character_id"],
            "character_name": r["character_name"] or "(已删除)",
            "scene_index": int(r["scene_index"]),
            "emotion": emotion_full,
            "rationale": r["rationale"] or "",
        })
    return out
