"""Sprint 6.A2 M4.1(2026-05-19)— World State Extractor。

每幕 narrator 合稿后,LLM 从 narrative_segment 抽取 5 类事实落库:
  - LIFE_STATUS         角色生死/重伤
  - LOCATION            角色物理位置
  - RULE_LOCK           反派/规则系统发布的游戏规则(默认 LOCKED)
  - EVENT_DONE          关键事件已发生
  - RELATIONSHIP_CHANGE 关系状态变化

设计原则(对齐"以最高标准开发,做最好的产品"铁律):
  - 用 LLM 抽取(非 NLP 规则)— 中文 NLP 在无 jieba/中文模型下不可靠,LLM 是最务实路径
  - **覆盖判定**:同 subject + 同 fact_type 的旧 ACTIVE fact,新 fact 自动覆盖
    (旧 fact.status: ACTIVE → SUPERSEDED + superseded_by_fact_id 指向新 fact)
  - **LOCKED 不可覆盖**:反派规则一旦发布,后续 narrative 抽到同类规则,旧 LOCKED 保留
    (LLM 在 narrator prompt 中已被强约束"不许改规则",这里是数据层兜底)
  - LLM 失败 → 本幕跳过 extractor,主流程不阻塞(降级容错)

API:
  - extract_world_facts(conn, sim, scene_index, narrative_segment, agents) → tuple[int, dict]
    抽取 + 落库;返回 (新增 facts 数量, llm_usage)
  - list_active_facts(conn, simulation_id, fact_types=None) → list[WorldFact]
    拉所有 ACTIVE/LOCKED 事实(scene_picker / agent_dialogue 用)
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

from app.db import execute, fetch_all
from app.models.character import Character
from app.models.simulation import Simulation
from app.models.world_fact import FactStatus, FactType, WorldFact
from app.services.llm_client import call_llm_json
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)

# 这些 fact_type 在抽取时若 LLM 标 RULE_LOCK,我们硬置 status=LOCKED
_RULE_TYPES_AUTO_LOCKED = {"RULE_LOCK"}

_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"


def _load_prompt(name: str) -> str:
    """加载 prompts/<name>。失败抛 FileNotFoundError(主循环捕获)。"""
    path = _PROMPTS_DIR / name
    return path.read_text(encoding="utf-8")


# ============================================================
# 抽取入口
# ============================================================

def extract_world_facts(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene_index: int,
    narrative_segment: str,
    agents: list[Character],
) -> tuple[int, dict]:
    """从一幕 narrative_segment 抽取事实,落库 + 覆盖旧事实。

    Args:
      sim: 当前推演(必须 mode='evolution')
      scene_index: 本幕索引
      narrative_segment: narrator 合稿产物
      agents: 本幕在场角色(用于 LLM 上下文 + subject_id 反查)

    Returns:
      (新增 facts 数量, llm_usage_dict)
    """
    if not narrative_segment or not narrative_segment.strip():
        return 0, {"input_tokens": 0, "output_tokens": 0}

    # 拉本 sim 所有 ACTIVE + LOCKED 事实作为"已知世界状态"喂给 LLM
    # → LLM 不要重复抽相同事实;若要"覆盖",必须明显矛盾才覆盖
    existing_facts = list_active_facts(conn, sim.id)

    user_input = {
        "scene_index": scene_index,
        "narrative_segment": narrative_segment,
        "agents_present": [
            {"id": a.id, "name": a.name} for a in agents
        ],
        "existing_facts": [
            {
                "id": f.id,
                "fact_type": f.fact_type,
                "subject_name": f.subject_name,
                "content": f.content,
                "status": f.status,
            }
            for f in existing_facts
        ],
    }

    system_prompt = _load_prompt("m4_world_state_extractor.md")
    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input,
            max_tokens=1200, temperature=0.2,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"world_state_extractor LLM failed sim={sim.id} scene={scene_index}: {e}"
        )
        return 0, {"input_tokens": 0, "output_tokens": 0}

    if not isinstance(parsed, dict):
        return 0, usage
    new_facts = parsed.get("new_facts") or []
    if not isinstance(new_facts, list):
        return 0, usage

    # 反查 agent name → id(LLM 可能只给 subject_name)
    name_to_id = {a.name: a.id for a in agents}

    inserted = 0
    now = iso_now()
    for fact_dict in new_facts:
        if not isinstance(fact_dict, dict):
            continue
        ftype = str(fact_dict.get("fact_type") or "").strip()
        if ftype not in {"LIFE_STATUS", "LOCATION", "RULE_LOCK",
                         "EVENT_DONE", "RELATIONSHIP_CHANGE"}:
            continue
        subject_name = str(fact_dict.get("subject_name") or "").strip()
        if not subject_name and ftype != "EVENT_DONE":
            # 非 EVENT_DONE 类必须有 subject_name
            continue
        content = str(fact_dict.get("content") or "").strip()[:500]
        if not content:
            continue
        # subject_id 反查(可空,LLM 给的 subject_name 不一定在 agents 里)
        subject_id = name_to_id.get(subject_name) or fact_dict.get("subject_id")

        # 默认 status:RULE_LOCK → LOCKED;其它 → ACTIVE
        status: FactStatus = "LOCKED" if ftype in _RULE_TYPES_AUTO_LOCKED else "ACTIVE"

        new_fact_id = uuid.uuid4().hex
        # 顺序铁律:**先 INSERT 新 fact,再 UPDATE 旧 fact 指向新 fact**
        # 因为 superseded_by_fact_id REFERENCES world_facts(id) 是 FK,反过来会失败
        execute(
            conn,
            """INSERT INTO world_facts
               (id, simulation_id, scene_index, fact_type,
                subject_id, subject_name, content, status,
                superseded_by_fact_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)""",
            (
                new_fact_id, sim.id, scene_index, ftype,
                subject_id, subject_name, content, status, now,
            ),
        )
        # 覆盖判定:同 subject_id + 同 fact_type 的旧 ACTIVE fact → SUPERSEDED
        # (LOCKED 永不被覆盖 — 这是 RULE_LOCK 的产品契约)
        _supersede_conflicting_facts(
            conn, sim.id, ftype, subject_id, subject_name, new_fact_id,
        )
        inserted += 1

    conn.commit()
    return inserted, usage


def _supersede_conflicting_facts(
    conn: sqlite3.Connection,
    simulation_id: str,
    fact_type: FactType,
    subject_id: Optional[str],
    subject_name: str,
    new_fact_id: str,
) -> None:
    """把"同 subject + 同 fact_type 的 ACTIVE 旧事实"标为 SUPERSEDED。

    匹配键(防 subject_id 为 NULL 时无法对齐):
      - subject_id 非空 → 按 subject_id + fact_type 匹配
      - subject_id 为空(LLM 给的 subject_name 不在 agents 里)→ 按 subject_name + fact_type 匹配

    铁律:
      - LOCKED 状态永不被覆盖(反派规则不可改铁律)— WHERE status='ACTIVE' 守护
      - **不覆盖刚插入的新 fact 自身** — WHERE id != new_fact_id 守护
        (新 fact 也是 ACTIVE,不排除则会自指 SUPERSEDED 自己,bug 史)
    """
    if subject_id:
        execute(
            conn,
            """UPDATE world_facts
               SET status='SUPERSEDED', superseded_by_fact_id=?
               WHERE simulation_id=? AND fact_type=?
                 AND subject_id=? AND status='ACTIVE'
                 AND id != ?""",
            (new_fact_id, simulation_id, fact_type, subject_id, new_fact_id),
        )
    elif subject_name:
        execute(
            conn,
            """UPDATE world_facts
               SET status='SUPERSEDED', superseded_by_fact_id=?
               WHERE simulation_id=? AND fact_type=?
                 AND subject_id IS NULL AND subject_name=? AND status='ACTIVE'
                 AND id != ?""",
            (new_fact_id, simulation_id, fact_type, subject_name, new_fact_id),
        )


# ============================================================
# 查询接口(scene_picker / agent_dialogue / narrator 消费)
# ============================================================

def list_active_facts(
    conn: sqlite3.Connection,
    simulation_id: str,
    fact_types: Optional[list[FactType]] = None,
) -> list[WorldFact]:
    """拉某 sim 当前所有生效事实(ACTIVE + LOCKED)。

    Args:
      fact_types: 过滤指定类型;None=全部

    Returns:
      WorldFact list,按 scene_index 升序(老事实在前;LOCKED 在 ACTIVE 之后排
      仅当 status 字段排序时,SQLite 默认字典序 'ACTIVE'<'LOCKED'<'SUPERSEDED')
    """
    if fact_types:
        placeholders = ",".join("?" for _ in fact_types)
        rows = fetch_all(
            conn,
            f"""SELECT * FROM world_facts
                WHERE simulation_id=?
                  AND status IN ('ACTIVE', 'LOCKED')
                  AND fact_type IN ({placeholders})
                ORDER BY scene_index ASC, created_at ASC""",
            (simulation_id, *fact_types),
        )
    else:
        rows = fetch_all(
            conn,
            """SELECT * FROM world_facts
               WHERE simulation_id=?
                 AND status IN ('ACTIVE', 'LOCKED')
               ORDER BY scene_index ASC, created_at ASC""",
            (simulation_id,),
        )
    return [WorldFact.from_row(r) for r in rows]


def list_facts_by_subject(
    conn: sqlite3.Connection,
    simulation_id: str,
    subject_id: str,
    *,
    only_active: bool = True,
) -> list[WorldFact]:
    """拉某角色相关的所有事实(agent_dialogue 准备本人 baseline 用)。"""
    status_filter = "AND status IN ('ACTIVE', 'LOCKED')" if only_active else ""
    rows = fetch_all(
        conn,
        f"""SELECT * FROM world_facts
            WHERE simulation_id=? AND subject_id=? {status_filter}
            ORDER BY scene_index ASC""",
        (simulation_id, subject_id),
    )
    return [WorldFact.from_row(r) for r in rows]
