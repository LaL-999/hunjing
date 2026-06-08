"""反事实变量 + 重塑度三维度服务 — Sprint 2.C。

核心抽象:
  反事实变量(counterfactual)= 用户在 3D 图谱里"动刀"改的字段(角色 personality 改 X / 事件 description 改 Y / 关系 type 改 Z)
  推演时编译进 director prompt 的"反事实变量上下文区",LLM 显式知道这是 what-if 假设

重塑度 reshape_percent ∈ [10, 90] 绑定三个物理维度(记忆 line 51-54):
  第 1 维 — max_touched_characters:可改属性的角色数上限
              公式 max(1, ceil(reshape_percent / 5))   10%→2 / 50%→10 / 90%→18
  第 2 维 — rounds_planned:agent 互动最大轮次(已实现于 quota_service.reshape_to_rounds)
              公式 5 + (percent - 10) / 80 * 45      10%→5 / 50%→27 / 90%→50
  第 3 维 — graph_distance_hops:与改动节点的图谱距离 BFS 半径
              公式 floor((percent - 10) / 10)         10%→0 / 50%→4 / 90%→8

设计纪律:
  * record_change 用 merge 语义(同 project+target_type+target_id+field 仅 1 个 active row;
    用户多次改同字段更新原行 new_value)→ 撤销简单 + UI"反事实数"语义自然
  * BFS 边集 = relationships(character ↔ character) + events.participants(character ↔ event);
    OBJECT/LOCATION 实体不计入边图(它们不参与推演 agent)
  * compute_affected 在 hops=0 时只返回 seed 集合(reshape 10% 语义:只影响被动刀的本节点)
"""
from __future__ import annotations

import json
import math
import sqlite3
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Iterable, Optional

from app.db import execute, fetch_all, fetch_one, transaction
from app.models.counterfactual_change import (
    CounterfactualChange,
    VALID_TARGET_TYPES,
    VALID_WORLD_FIELDS,
    WORLD_TARGET_ID,
)
from app.services.project_service import ResourceNotFoundOrForbidden, get_project_or_403


# ============================================================
# 重塑度三维度公式
# ============================================================

def reshape_to_max_touched_characters(reshape_percent: int) -> int:
    """第 1 维 — 该 reshape % 下用户能改的最大角色数(active 反事实涉及的去重 character 数)。

    公式 max(1, ceil(reshape_percent / 5)):
      10%→2 / 30%→6 / 60%→12 / 90%→18
    平衡"允许改"vs"防过度偏离"— 红楼梦 30 主角场景下 90% 允许改 18 个(60%)。
    """
    return max(1, math.ceil(reshape_percent / 5))


def reshape_to_graph_distance_hops(reshape_percent: int) -> int:
    """第 3 维 — BFS 影响半径(从被动刀节点出发的跳数)。

    公式 floor((percent - 10) / 10),范围 0-8:
      10%→0(只本节点) / 30%→2(2 跳) / 90%→8(基本全图)
    用户拍板的"10% 影响相邻 / 90% 全图传播"映射(记忆 line 53)。
    """
    return max(0, min(8, (reshape_percent - 10) // 10))


# ============================================================
# 异常
# ============================================================

class ReshapeCharacterLimitExceeded(Exception):
    """用户改的角色数超过当前 reshape_percent 允许的上限。"""

    def __init__(self, current: int, limit: int, reshape_percent: int):
        self.current = current
        self.limit = limit
        self.reshape_percent = reshape_percent
        super().__init__(
            f"已改 {current} 个角色,超过当前重塑度 {reshape_percent}% 允许的上限 {limit}。"
            f"撤销一些反事实或调高重塑度。"
        )


class CounterfactualNotFoundOrForbidden(Exception):
    """反事实不存在或不属于该用户。"""


# ============================================================
# record / revert / list
# ============================================================

def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _serialize_value(v: object) -> Optional[str]:
    """字段值通用序列化:None → None;str → str;list/dict → JSON 字符串。"""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


def record_change(
    conn: sqlite3.Connection,
    project_id: str,
    target_type: str,
    target_id: str,
    field: str,
    old_value: object,
    new_value: object,
    user_id: str,
    user_intent: Optional[str] = None,
    enforce_character_limit_at_reshape: Optional[int] = None,
) -> CounterfactualChange:
    """记录一个反事实变更(merge 语义:同 project+target+field 已 active → 更新 new_value)。

    Args:
      user_intent: ⭐ 2.C+ 用户自然语言意图("我想让 X 变 Y 让后续大变") —
                   LLM 编排时优先级最高 / merge 时若新值非空就覆盖旧的;
                   PATCH hook 默认 None(隐式 record 没意图);
                   显式工作台创建时强烈推荐传(让 LLM 知道"为什么改")
      enforce_character_limit_at_reshape: 若非 None,对 character 类型做角色数上限预检

    World 反事实(target_type='world')特殊:
      - 不算入 character 上限校验
      - target_id 必须为 WORLD_TARGET_ID
      - field 必须为 VALID_WORLD_FIELDS 之一

    Returns 已落库 / 已 merge 的 CounterfactualChange。

    Raises:
      ReshapeCharacterLimitExceeded
      ValueError: 字段或类型非法 / old==new noop
    """
    if target_type not in VALID_TARGET_TYPES:
        raise ValueError(f"target_type 必须是 {VALID_TARGET_TYPES} 之一,实际:{target_type}")
    if target_type == "world":
        if target_id != WORLD_TARGET_ID:
            raise ValueError(
                f"world 反事实 target_id 必须是 '{WORLD_TARGET_ID}',实际:{target_id}"
            )
        if field not in VALID_WORLD_FIELDS:
            raise ValueError(
                f"world 反事实 field 必须是 {VALID_WORLD_FIELDS} 之一,实际:{field}"
            )

    old_str = _serialize_value(old_value)
    new_str = _serialize_value(new_value)

    # old == new && user_intent 也没变 → 无需记录(用户没真改)
    # world 类型允许 old=NULL new!=NULL(原作没明确该维度)
    if old_str == new_str and user_intent is None:
        existing = _find_active(conn, project_id, target_type, target_id, field)
        if existing:
            return existing
        raise ValueError("old_value 与 new_value 相同 + 无 user_intent,无需记录反事实")

    # 第 1 维强校验(只 character 类型校验;world 不算入)
    if enforce_character_limit_at_reshape is not None and target_type == "character":
        cur_touched = _count_touched_characters(conn, project_id, exclude_target_id=target_id)
        existing_touched = _is_character_already_touched(conn, project_id, target_id)
        prospective = cur_touched + (0 if existing_touched else 1)
        limit = reshape_to_max_touched_characters(enforce_character_limit_at_reshape)
        if prospective > limit:
            raise ReshapeCharacterLimitExceeded(
                current=prospective,
                limit=limit,
                reshape_percent=enforce_character_limit_at_reshape,
            )

    existing = _find_active(conn, project_id, target_type, target_id, field)
    now = _iso_now()
    if existing is not None:
        # merge:更新 new_value + user_intent(如新值非空)
        new_intent = user_intent if user_intent is not None else existing.user_intent
        with transaction(conn) as tx:
            execute(
                tx,
                "UPDATE counterfactual_changes SET new_value=?, user_intent=?, created_at=? "
                "WHERE id=?",
                (new_str, new_intent, now, existing.id),
            )
        existing.new_value = new_str
        existing.user_intent = new_intent
        existing.created_at = now
        return existing

    # 新建
    cf_id = str(uuid.uuid4())
    with transaction(conn) as tx:
        execute(
            tx,
            "INSERT INTO counterfactual_changes "
            "(id, project_id, target_type, target_id, field, old_value, new_value, "
            " user_intent, created_at, reverted_at, applied_in_simulations_json, user_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '[]', ?)",
            (cf_id, project_id, target_type, target_id, field,
             old_str, new_str, user_intent, now, user_id),
        )
    row = fetch_one(conn, "SELECT * FROM counterfactual_changes WHERE id=?", (cf_id,))
    return CounterfactualChange.from_row(row)


def record_world_change(
    conn: sqlite3.Connection,
    project_id: str,
    field: str,
    old_value: Optional[str],
    new_value: Optional[str],
    user_intent: Optional[str],
    user_id: str,
) -> CounterfactualChange:
    """world 反事实专用快捷方法 — 自动填 target_id='_global_' + 校验 field。

    Args:
      field: VALID_WORLD_FIELDS 之一(genre / setting / magic_system / time_axis / tone / free_form)
      old_value: 原作该维度的语义文本(用户可填,可空)
      new_value: 用户想改成的语义文本(必填,否则 record 抛 noop)
      user_intent: 用户的"为什么这样改"(强烈推荐)
    """
    return record_change(
        conn=conn,
        project_id=project_id,
        target_type="world",
        target_id=WORLD_TARGET_ID,
        field=field,
        old_value=old_value,
        new_value=new_value,
        user_id=user_id,
        user_intent=user_intent,
    )


def _find_active(
    conn: sqlite3.Connection, project_id: str,
    target_type: str, target_id: str, field: str,
) -> Optional[CounterfactualChange]:
    row = fetch_one(
        conn,
        "SELECT * FROM counterfactual_changes "
        "WHERE project_id=? AND target_type=? AND target_id=? AND field=? "
        "  AND reverted_at IS NULL "
        "LIMIT 1",
        (project_id, target_type, target_id, field),
    )
    return CounterfactualChange.from_row(row) if row else None


def _count_touched_characters(
    conn: sqlite3.Connection, project_id: str,
    exclude_target_id: Optional[str] = None,
) -> int:
    """活跃反事实涉及的去重 character 数(预校验用)。"""
    if exclude_target_id:
        row = fetch_one(
            conn,
            "SELECT COUNT(DISTINCT target_id) AS n FROM counterfactual_changes "
            "WHERE project_id=? AND target_type='character' "
            "  AND reverted_at IS NULL AND target_id<>?",
            (project_id, exclude_target_id),
        )
    else:
        row = fetch_one(
            conn,
            "SELECT COUNT(DISTINCT target_id) AS n FROM counterfactual_changes "
            "WHERE project_id=? AND target_type='character' AND reverted_at IS NULL",
            (project_id,),
        )
    return int(row["n"]) if row else 0


def _is_character_already_touched(
    conn: sqlite3.Connection, project_id: str, target_id: str,
) -> bool:
    row = fetch_one(
        conn,
        "SELECT 1 FROM counterfactual_changes "
        "WHERE project_id=? AND target_type='character' AND target_id=? "
        "  AND reverted_at IS NULL LIMIT 1",
        (project_id, target_id),
    )
    return row is not None


def list_active(
    conn: sqlite3.Connection, project_id: str, user_id: str,
) -> list[CounterfactualChange]:
    """列出项目所有 active 反事实(按 created_at DESC)。

    鉴权:user 必须拥有 project。
    """
    get_project_or_403(conn, project_id, user_id)   # 鉴权 — 拒非本人项目
    rows = fetch_all(
        conn,
        "SELECT * FROM counterfactual_changes "
        "WHERE project_id=? AND reverted_at IS NULL "
        "ORDER BY created_at DESC",
        (project_id,),
    )
    return [CounterfactualChange.from_row(r) for r in rows]


def get_active_target_ids(
    conn: sqlite3.Connection, project_id: str,
) -> dict[str, set[str]]:
    """按 type 分组的 active target_id 集合(给 BFS / 校验用,跳过鉴权)。"""
    rows = fetch_all(
        conn,
        "SELECT DISTINCT target_type, target_id FROM counterfactual_changes "
        "WHERE project_id=? AND reverted_at IS NULL",
        (project_id,),
    )
    out: dict[str, set[str]] = {"character": set(), "event": set(), "relationship": set()}
    for r in rows:
        t = r["target_type"]
        if t in out:
            out[t].add(r["target_id"])
    return out


def get_or_404(
    conn: sqlite3.Connection, change_id: str, user_id: str,
) -> CounterfactualChange:
    row = fetch_one(
        conn,
        "SELECT * FROM counterfactual_changes WHERE id=?", (change_id,),
    )
    if not row:
        raise CounterfactualNotFoundOrForbidden(f"反事实 {change_id} 不存在")
    cf = CounterfactualChange.from_row(row)
    if cf.user_id != user_id:
        raise CounterfactualNotFoundOrForbidden(f"反事实 {change_id} 不属于你")
    return cf


def revert(
    conn: sqlite3.Connection, change_id: str, user_id: str,
) -> tuple[CounterfactualChange, bool, Optional[str]]:
    """撤销反事实 — 标 reverted_at + 把 db 字段还原到 old_value。

    Returns:
      (counterfactual, restored, error)
        restored: 是否成功把 db 字段还原(失败也 200,前端给 toast)
        error: restored=False 时的失败原因
    """
    cf = get_or_404(conn, change_id, user_id)
    if cf.reverted_at is not None:
        return cf, False, "该反事实已经撤销过了"

    # 1. 标 reverted_at
    now = _iso_now()
    with transaction(conn) as tx:
        execute(
            tx,
            "UPDATE counterfactual_changes SET reverted_at=? WHERE id=?",
            (now, change_id),
        )
    cf.reverted_at = now

    # 2. 把 db 字段还原到 old_value(根据 target_type 走不同表)
    restored, err = _restore_db_field(conn, cf)
    return cf, restored, err


def _restore_db_field(
    conn: sqlite3.Connection, cf: CounterfactualChange,
) -> tuple[bool, Optional[str]]:
    """根据 cf.target_type / cf.field 把 db 字段还原。

    支持的还原路径(白名单 — 防 SQL 注入):
      character: name / identity / personality / quotes / no_go_list
      event:     description / participants
      relationship: type / description
      world:     无需还原 db 字段(world 数据只存在 counterfactual_changes 表;
                 reverted_at 已被外层 revert() 标记 → 推演 build_director_context 自动
                 过滤掉,等同回到原作 baseline,无 db 字段需要还原)
    """
    if cf.target_type == "world":
        # world 反事实撤销 = reverted_at 标记完成即可,db 字段没有"还原"概念
        return True, None

    table_map = {
        "character": "characters",
        "event": "events",
        "relationship": "relationships",
    }
    field_whitelist = {
        "character": {"name", "identity", "personality", "quotes", "no_go_list"},
        "event": {"description", "participants"},
        "relationship": {"type", "description"},
    }
    table = table_map.get(cf.target_type)
    if not table:
        return False, f"不支持的 target_type {cf.target_type}"
    if cf.field not in field_whitelist.get(cf.target_type, set()):
        return False, f"字段 {cf.field} 不在 {cf.target_type} 还原白名单"

    try:
        with transaction(conn) as tx:
            # SQL 字段名安全:已通过白名单校验
            execute(
                tx,
                f"UPDATE {table} SET {cf.field}=? WHERE id=?",
                (cf.old_value, cf.target_id),
            )
        return True, None
    except sqlite3.Error as e:
        return False, f"db 还原失败:{e}"


def overview(
    conn: sqlite3.Connection, project_id: str, user_id: str,
) -> dict:
    """反事实总览(给反事实面板 + ProjectGraphView 顶部 chip 用)。"""
    items = list_active(conn, project_id, user_id)
    by_type: dict[str, int] = {"character": 0, "event": 0, "relationship": 0}
    for c in items:
        if c.target_type in by_type:
            by_type[c.target_type] += 1
    return {
        "total_active": len(items),
        "by_type": by_type,
        "items": [c.to_response() for c in items],
    }


# ============================================================
# BFS 影响范围(第 3 维)
# ============================================================

def compute_affected_node_ids(
    conn: sqlite3.Connection,
    project_id: str,
    seed_node_ids: Iterable[str],
    hops: int,
) -> set[str]:
    """从 seed_node_ids 出发做 BFS,沿 relationships + events.participants 走 hops 跳。

    边集:
      - relationships(character ↔ character,无向)
      - events.participants(character ↔ event,无向)
      - LOCATION / OBJECT 实体不计入图(它们不参与推演 agent)

    hops=0 → 只返回 seed 集合本身(reshape 10% 语义)
    hops≥8 → 基本全图(BFS 自然 saturate)
    """
    seeds = set(s for s in seed_node_ids if s)
    if not seeds:
        return set()
    if hops <= 0:
        return seeds

    # 拉无向邻接表
    adj: dict[str, set[str]] = defaultdict(set)
    rels = fetch_all(
        conn,
        "SELECT source_id, target_id FROM relationships WHERE project_id=?",
        (project_id,),
    )
    for r in rels:
        s, t = r["source_id"], r["target_id"]
        if s and t:
            adj[s].add(t)
            adj[t].add(s)

    events = fetch_all(
        conn,
        "SELECT id, participants FROM events WHERE project_id=?",
        (project_id,),
    )
    for e in events:
        try:
            participants = json.loads(e["participants"] or "[]")
        except (json.JSONDecodeError, TypeError):
            participants = []
        if not isinstance(participants, list):
            continue
        eid = e["id"]
        for p in participants:
            if isinstance(p, str) and p:
                adj[p].add(eid)
                adj[eid].add(p)

    # BFS
    visited: set[str] = set(seeds)
    frontier: set[str] = set(seeds)
    for _ in range(hops):
        next_frontier: set[str] = set()
        for n in frontier:
            for neighbor in adj.get(n, ()):
                if neighbor not in visited:
                    next_frontier.add(neighbor)
                    visited.add(neighbor)
        if not next_frontier:
            break
        frontier = next_frontier
    return visited


# ============================================================
# 推演时 director prompt 上下文构建
# ============================================================

def build_director_context(
    conn: sqlite3.Connection,
    project_id: str,
    selected_counterfactual_ids: Optional[list[str]] = None,
) -> dict:
    """编译活跃反事实成 director 可读的上下文(simulation_service 调)。

    Args:
      selected_counterfactual_ids: 若非 None,仅用这个 id 子集编译(配合 SimulationDock 的
                                   "本次推演用哪些反事实"勾选框);None = 全部 active

    Returns:
      {
        "active_count": int,
        "items": [
          {
            "target_type": "character" | "event" | "relationship" | "world",
            "target_id": "...",
            "target_name": "林黛玉",     # JOIN 取;world 为 "世界观"
            "field_label": "「性格」",     # 中文化
            "from": "...", "to": "...",
            "user_intent": "..." | None    # ⭐ 2.C+ 用户意图
          }, ...
        ]
      }
    """
    if selected_counterfactual_ids is not None and len(selected_counterfactual_ids) == 0:
        # 显式空选 = 不要任何反事实(用户可能想"这次纯按原作演")
        return {"active_count": 0, "items": []}

    if selected_counterfactual_ids is None:
        items_raw = fetch_all(
            conn,
            "SELECT id, target_type, target_id, field, old_value, new_value, user_intent "
            "FROM counterfactual_changes "
            "WHERE project_id=? AND reverted_at IS NULL "
            "ORDER BY "
            "  CASE target_type WHEN 'world' THEN 0 ELSE 1 END, "  # world 排前
            "  created_at",
            (project_id,),
        )
    else:
        placeholders = ",".join("?" for _ in selected_counterfactual_ids)
        items_raw = fetch_all(
            conn,
            f"SELECT id, target_type, target_id, field, old_value, new_value, user_intent "
            f"FROM counterfactual_changes "
            f"WHERE project_id=? AND reverted_at IS NULL AND id IN ({placeholders}) "
            f"ORDER BY "
            f"  CASE target_type WHEN 'world' THEN 0 ELSE 1 END, "
            f"  created_at",
            (project_id, *selected_counterfactual_ids),
        )
    if not items_raw:
        return {"active_count": 0, "items": []}

    # 批量拉 target name(每 type 一次 SQL 避 N+1)
    char_ids = [r["target_id"] for r in items_raw if r["target_type"] == "character"]
    evt_ids = [r["target_id"] for r in items_raw if r["target_type"] == "event"]
    rel_ids = [r["target_id"] for r in items_raw if r["target_type"] == "relationship"]

    name_map: dict[str, str] = {}
    if char_ids:
        placeholders = ",".join("?" for _ in char_ids)
        for r in fetch_all(conn, f"SELECT id, name FROM characters WHERE id IN ({placeholders})", tuple(char_ids)):
            name_map[r["id"]] = r["name"]
    if evt_ids:
        placeholders = ",".join("?" for _ in evt_ids)
        for r in fetch_all(conn, f"SELECT id, description FROM events WHERE id IN ({placeholders})", tuple(evt_ids)):
            name_map[r["id"]] = (r["description"] or "(未命名事件)")[:30]
    if rel_ids:
        placeholders = ",".join("?" for _ in rel_ids)
        for r in fetch_all(
            conn,
            f"SELECT r.id, sc.name AS sn, tc.name AS tn, r.type "
            f"FROM relationships r "
            f"LEFT JOIN characters sc ON sc.id=r.source_id "
            f"LEFT JOIN characters tc ON tc.id=r.target_id "
            f"WHERE r.id IN ({placeholders})",
            tuple(rel_ids),
        ):
            name_map[r["id"]] = f"{r['sn']} ↔ {r['tn']} ({r['type']})"

    # 字段标签
    char_event_rel_field_label = {
        "name": "「名字」",
        "identity": "「身份」",
        "personality": "「性格」",
        "quotes": "「台词」",
        "no_go_list": "「禁忌」",
        "description": "「描述」",
        "participants": "「参与者」",
        "type": "「关系类型」",
    }
    world_field_label = {
        "genre": "「体裁」",
        "setting": "「背景设定」",
        "magic_system": "「超能力体系」",
        "time_axis": "「时间轴」",
        "tone": "「整体基调」",
        "free_form": "「自由描述」",
    }

    items = []
    for r in items_raw:
        if r["target_type"] == "world":
            target_name = "世界观"
            label = world_field_label.get(r["field"], f"「{r['field']}」")
        else:
            target_name = name_map.get(r["target_id"], r["target_id"][:8])
            label = char_event_rel_field_label.get(r["field"], f"「{r['field']}」")
        items.append({
            "target_type": r["target_type"],
            "target_id": r["target_id"],
            "target_name": target_name,
            "field": r["field"],
            "field_label": label,
            "from": r["old_value"] or "(原作:未填)",
            "to": r["new_value"] or "(用户重塑:留空)",
            "user_intent": r["user_intent"],
        })
    return {"active_count": len(items), "items": items}


def render_counterfactual_section_text(context: dict) -> str:
    """把 build_director_context 输出转成可塞 director prompt 的纯文本块(2.C+ 升级)。

    分三段输出(对齐 director_system.md 铁律 9.5):
      1. 世界观反事实(target_type='world')⭐ 最高优先级 — 覆盖所有原作设定
      2. 角色反事实(character / relationship)— 影响该角色及相关情节
      3. 事件反事实(event)— 强制改写关键决策

    每条都附 user_intent(用户为什么这么改)— LLM 优先用 intent 推 LLM 编排走向,
    而不是只看字段 diff。
    """
    items = context.get("items", [])
    if not items:
        return ""

    world_items = [i for i in items if i["target_type"] == "world"]
    char_items = [i for i in items if i["target_type"] == "character"]
    rel_items = [i for i in items if i["target_type"] == "relationship"]
    evt_items = [i for i in items if i["target_type"] == "event"]

    lines = ["## 反事实变量上下文(用户的 what-if 假设,**这些不是原作设定,是用户主动改的,推演必须以这些为准**)"]

    if world_items:
        lines.append("")
        lines.append("### 世界观反事实(覆盖所有原作设定)⭐ 最高优先级")
        for it in world_items:
            lines.append(f"- {it['field_label']}")
            lines.append(f"   原作:{it['from']}")
            lines.append(f"   改成:{it['to']}")
            if it.get("user_intent"):
                lines.append(f"   ★ 用户意图:{it['user_intent']}")

    if char_items or rel_items:
        lines.append("")
        lines.append("### 角色反事实(影响该角色及其相关情节)")
        for it in char_items + rel_items:
            lines.append(
                f"- [{it['target_type']}] {it['target_name']} {it['field_label']}"
            )
            lines.append(f"   原作:{it['from']}")
            lines.append(f"   改成:{it['to']}")
            if it.get("user_intent"):
                lines.append(f"   ★ 用户意图:{it['user_intent']}")

    if evt_items:
        lines.append("")
        lines.append("### 事件反事实(强制改写关键决策)")
        for it in evt_items:
            lines.append(f"- {it['target_name']} {it['field_label']}")
            lines.append(f"   原作:{it['from']}")
            lines.append(f"   改成:{it['to']}")
            if it.get("user_intent"):
                lines.append(f"   ★ 用户意图:{it['user_intent']}")

    return "\n".join(lines)


def mark_applied_in_simulation(
    conn: sqlite3.Connection,
    project_id: str,
    simulation_id: str,
    selected_counterfactual_ids: Optional[list[str]] = None,
) -> None:
    """推演开始时调:把"被这次推演用到的"反事实标记应用过(trace 用)。

    Args:
      selected_counterfactual_ids: None = 全部 active(向下兼容);
                                   list = 只标这些 id

    不删 / 不撤销 — 只是 append simulation_id 到 applied_in_simulations_json。
    """
    if selected_counterfactual_ids is not None and len(selected_counterfactual_ids) == 0:
        return   # 用户显式选 0 个反事实 → 没有要标的

    if selected_counterfactual_ids is None:
        items = fetch_all(
            conn,
            "SELECT id, applied_in_simulations_json FROM counterfactual_changes "
            "WHERE project_id=? AND reverted_at IS NULL",
            (project_id,),
        )
    else:
        placeholders = ",".join("?" for _ in selected_counterfactual_ids)
        items = fetch_all(
            conn,
            f"SELECT id, applied_in_simulations_json FROM counterfactual_changes "
            f"WHERE project_id=? AND reverted_at IS NULL AND id IN ({placeholders})",
            (project_id, *selected_counterfactual_ids),
        )
    if not items:
        return
    with transaction(conn) as tx:
        for r in items:
            try:
                lst = json.loads(r["applied_in_simulations_json"] or "[]")
                if not isinstance(lst, list):
                    lst = []
            except json.JSONDecodeError:
                lst = []
            if simulation_id not in lst:
                lst.append(simulation_id)
                execute(
                    tx,
                    "UPDATE counterfactual_changes SET applied_in_simulations_json=? "
                    "WHERE id=?",
                    (json.dumps(lst, ensure_ascii=False), r["id"]),
                )


def link_simulation_to_counterfactuals(
    conn: sqlite3.Connection,
    simulation_id: str,
    counterfactual_ids: list[str],
) -> None:
    """落 simulation_counterfactual_links 行(用户选了反事实子集时调)。

    counterfactual_ids 为空时不写(意为"用全部 active"或"显式不用反事实")—
    后续 get_active_for_simulation 看到表无该 sim 的行就走默认全集语义。
    """
    if not counterfactual_ids:
        return
    with transaction(conn) as tx:
        for cf_id in counterfactual_ids:
            execute(
                tx,
                "INSERT OR IGNORE INTO simulation_counterfactual_links "
                "(simulation_id, counterfactual_id) VALUES (?, ?)",
                (simulation_id, cf_id),
            )


def get_linked_counterfactual_ids(
    conn: sqlite3.Connection, simulation_id: str,
) -> Optional[list[str]]:
    """读取该 simulation 关联的反事实 id 子集。

    Returns:
      None — 表中无该 sim 的行 → 走"全部 active"语义
      [] — 极特殊场景(写过空集 — 不应该发生,link 函数空 list 不写)
      [id1, id2, ...] — 用户选的子集
    """
    rows = fetch_all(
        conn,
        "SELECT counterfactual_id FROM simulation_counterfactual_links "
        "WHERE simulation_id=?",
        (simulation_id,),
    )
    if not rows:
        return None
    return [r["counterfactual_id"] for r in rows]


# ============================================================
# 重塑度三维度预览(给 ReshapeSlider 实时显)
# ============================================================

def derive_reshape_dimensions(
    conn: sqlite3.Connection,
    project_id: str,
    user_id: str,
    reshape_percent: int,
    plan_max_percent: int,
) -> dict:
    """ReshapePreviewResponse 的数据源 — 给前端 ReshapeSlider 实时显当前 reshape 的三维影响。

    三维同时返:max_touched / rounds / hops + 当前实际占用(touched_count + affected ids)
    """
    from app.services.simulation_service import (
        reshape_to_recommended_chars,
        reshape_to_rounds,
    )   # 避循环
    get_project_or_403(conn, project_id, user_id)

    max_touched = reshape_to_max_touched_characters(reshape_percent)
    rounds = reshape_to_rounds(reshape_percent)
    hops = reshape_to_graph_distance_hops(reshape_percent)
    # Sprint 6.A2 M3.D-fix2 v2(2026-05-18):字数推荐区间
    chars_info = reshape_to_recommended_chars(reshape_percent)

    # 当前 active 反事实涉及的角色数(去重)
    cur_touched = _count_touched_characters(conn, project_id)

    # BFS:从所有 active target_id 出发(包括 character / event / relationship 涉及的节点)
    targets = get_active_target_ids(conn, project_id)
    seed = set(targets["character"]) | set(targets["event"])
    # relationship 的 seed 是它的 source / target character
    if targets["relationship"]:
        placeholders = ",".join("?" for _ in targets["relationship"])
        rel_endpoints = fetch_all(
            conn,
            f"SELECT source_id, target_id FROM relationships WHERE id IN ({placeholders})",
            tuple(targets["relationship"]),
        )
        for r in rel_endpoints:
            if r["source_id"]:
                seed.add(r["source_id"])
            if r["target_id"]:
                seed.add(r["target_id"])

    affected = compute_affected_node_ids(conn, project_id, seed, hops)

    return {
        "reshape_percent": reshape_percent,
        "max_touched_characters": max_touched,
        "rounds_planned": rounds,
        "graph_distance_hops": hops,
        "current_touched_count": cur_touched,
        "current_affected_node_ids": sorted(affected),
        "plan_max_percent": plan_max_percent,
        # Sprint 6.A2 M3.D-fix2 v2:字数推荐区间(基于 rounds 派生)
        "chars_center": chars_info["center"],
        "chars_low": chars_info["low"],
        "chars_high": chars_info["high"],
        "chars_label": chars_info["label"],
    }


__all__ = [
    "ReshapeCharacterLimitExceeded",
    "CounterfactualNotFoundOrForbidden",
    "reshape_to_max_touched_characters",
    "reshape_to_graph_distance_hops",
    "record_change",
    "record_world_change",
    "list_active",
    "get_or_404",
    "get_active_target_ids",
    "revert",
    "overview",
    "compute_affected_node_ids",
    "build_director_context",
    "render_counterfactual_section_text",
    "mark_applied_in_simulation",
    "link_simulation_to_counterfactuals",
    "get_linked_counterfactual_ids",
    "derive_reshape_dimensions",
]
