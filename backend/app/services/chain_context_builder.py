"""Sprint 6.A2 M9.A.2(2026-05-20)— 长篇心智全链 context 构建。

输入:当前 sim
输出:可注入 director / agent / narrator prompt 的"长篇心智"文本块,含:
  - 完整祖先链(原作 → 第 1 代 → ... → 父辈)各代的 narrative_summary
  - 跨代 character_arcs(本 project 累积的角色心境演化时间线)
  - 当前 open foreshadows(本 project 未解伏笔,scene_picker 应优先推进)
  - active world_rules(本 project 累积的世界规则,LLM 必须遵守)

为什么治本:
  - 旧实现 simulation_service L1326-1379 只看**直接前作** narrative_summary
    → 第 3 代续作完全看不到原作 / 第 1 代的剧情走向 → LLM 健忘
  - 新实现:递归拉 ancestors_chain 全链 → LLM 看完整脉络 → 跨代连贯
  - 加上 character_arcs / foreshadows / world_rules → LLM 知道
    "这个角色一路怎么变 + 还有哪些坑 + 世界有什么规则"

API:
  build_chain_context(conn, sim) → str
    返回组装好的文本块(各代摘要 + 三库),给 prompt 直接消费;空 → 独立推演无前作时返空

  list_open_foreshadows(conn, project_id) → list[Foreshadow]
    给 scene_picker / outline 单独消费(显式提示"本幕优先推进 high 优先级未解伏笔")
"""
from __future__ import annotations

import json
import logging
import sqlite3

from app.db import fetch_all, fetch_one
from app.models.long_form_mind import CharacterArc, Foreshadow, WorldRule
from app.models.simulation import Simulation

logger = logging.getLogger(__name__)


# 上下文长度兜底(防 token 爆炸):各代 summary 合计 / arcs / foreshadows / rules 上限
MAX_ANCESTOR_SUMMARY_CHARS = 6000     # 全链 summary 合计上限(deprecated,见 v2 双层预算)
MAX_ARCS_LISTED = 20                   # 最近 20 条角色弧
MAX_OPEN_FORESHADOWS = 10              # 当前 open 伏笔数
MAX_WORLD_RULES = 15                   # 全局 active 规则数

# 2026-06-01:双层 context 预算(近代全文 + 远代摘要)
# 治"基于本篇续写时只读 800 字摘要,长篇细节(对白/微表情/伏笔)全丢"
RECENT_GENERATIONS_FULL_TEXT = 2       # 最近 N 代用全文(直接父 + 祖父辈)
RECENT_FULL_TEXT_TAIL_CHARS = 4000     # 单代全文尾部字数(用户最关心的连贯性区域)
DISTANT_SUMMARY_CHARS_PER_GEN = 1500   # 更远代每代摘要字数
MAX_TOTAL_ANCESTOR_CHARS = 11000       # 全链上限(留 token budget 给 narrator 输出)


def build_chain_context(
    conn: sqlite3.Connection,
    sim: Simulation,
) -> str:
    """构建当前 sim 可消费的"长篇心智"全链 context 文本块。

    Returns:
      文本块(可空 — 独立推演无前作 + 无累积心智时);非空时以 markdown 结构注入 prompt。
    """
    sections: list[str] = []

    # 1. 祖先链各代 narrative_summary(递归拉)
    ancestor_section = _build_ancestor_summaries_section(conn, sim)
    if ancestor_section:
        sections.append(ancestor_section)

    # 2. 跨代 character_arcs(按时间排,反映演化轨迹)
    arc_section = _build_character_arcs_section(conn, sim.project_id)
    if arc_section:
        sections.append(arc_section)

    # 3. open foreshadows(未解伏笔 — scene_picker 应优先推进)
    # 阶段 3A(2026-06-02):透传 sim 对象给伏笔 section,让它读 inherited_foreshadow_ids_json
    foreshadow_section = _build_open_foreshadows_section(conn, sim.project_id, sim)
    if foreshadow_section:
        sections.append(foreshadow_section)

    # 4. active world_rules(LLM 必须遵守的累积规则)
    rule_section = _build_world_rules_section(conn, sim.project_id)
    if rule_section:
        sections.append(rule_section)

    if not sections:
        return ""

    return "\n\n".join([
        "## 长篇心智 — 跨代累积上下文(必读)",
        *sections,
    ])


# ======================================================================
# 各 section builder
# ======================================================================

def _build_ancestor_summaries_section(
    conn: sqlite3.Connection,
    sim: Simulation,
) -> str:
    """递归拉 ancestors_chain 全链各代,**近代用全文 + 远代用摘要**.

    2026-06-01 v2 升级(治"长链续作丢前情细节"):
      - 最近 RECENT_GENERATIONS_FULL_TEXT 代 → 全文 narrative 尾部 N 字
        (用户最关心连贯性的区域,对白/微表情/伏笔细节都在)
      - 更远代 → narrative_summary[:1500](核心走向 + 角色弧光)
      - 全链总预算 MAX_TOTAL_ANCESTOR_CHARS,超额跳远祖

    M7.D 已经做了 inheritance_depth + ancestors_chain 计算 (compute_inheritance_metadata_for_user),
    但只用于 dashboard 显示.这里 walk parent chain 拉每代内容串联.
    """
    parents = sim.context_simulation_ids or []
    if not parents:
        return ""

    # 递归收集祖先(最多 5 代防爆,实际产品场景应该 ≤ 3 代)
    chain: list[tuple[int, Simulation]] = []   # (depth, sim);depth=1 是直接父
    visited: set[str] = set()
    current = sim
    depth = 1
    while True:
        if not current.context_simulation_ids:
            break
        # 2026-06-02 hotfix:取 context_simulation_ids[-1] 作直接父
        # context_simulation_ids 按时序 ASC:[最远祖先, ..., 直接父]
        # 原实现用 [0] = 误取最远祖先 → 灵魂续写 prior context 错读祖先全文,直接父辈被忽略
        parent_id = current.context_simulation_ids[-1]
        if parent_id in visited or depth > 5:
            break
        visited.add(parent_id)
        parent_row = fetch_one(
            conn, "SELECT * FROM simulations WHERE id=?", (parent_id,),
        )
        if parent_row is None:
            break
        parent_sim = Simulation.from_row(parent_row)
        chain.append((depth, parent_sim))
        current = parent_sim
        depth += 1

    if not chain:
        return ""

    # chain 是 [直接父(d=1), 祖父(d=2), ...] — 近代在前,远代在后
    # 我们渲染时:近代优先全文 + 远代退化为摘要,然后整体反转成"原作 → 父辈"语序

    rendered_blocks: list[tuple[int, str]] = []  # (depth, block_text)
    total_chars = 0

    for d, ancestor in chain:
        label = f"前作 · 直系第 {d} 代上溯"
        anchor_label = f"锚点「{(ancestor.divergence or '')[:40]}」"

        # 是否用全文(近代)
        use_full_text = d <= RECENT_GENERATIONS_FULL_TEXT

        block_text: str = ""
        if use_full_text and ancestor.narrative:
            full = ancestor.narrative.strip()
            # 取尾部 — 续作接的是末段,前面铺陈对当前 sim 帮助小
            if len(full) > RECENT_FULL_TEXT_TAIL_CHARS:
                snippet = "…(开头部分省略以省 token)…\n" + full[-RECENT_FULL_TEXT_TAIL_CHARS:]
            else:
                snippet = full
            block_text = (
                f"- **{label}**(全文尾部,**重点参考**)· {anchor_label}\n"
                f"```\n{snippet}\n```"
            )
        else:
            # 远代或无 narrative → 用 summary
            summary = (ancestor.narrative_summary or "").strip()
            if not summary:
                block_text = f"- {label} · {anchor_label}(摘要未生成,可能内容太短)"
            else:
                snippet = summary[:DISTANT_SUMMARY_CHARS_PER_GEN]
                block_text = f"- **{label}**(摘要)· {anchor_label}\n  {snippet}"

        # 预算守护:超额 → 跳远祖
        if total_chars + len(block_text) > MAX_TOTAL_ANCESTOR_CHARS:
            rendered_blocks.append(
                (d, f"- 前作 · 第 {d} 代及更远 · (合计超预算 {MAX_TOTAL_ANCESTOR_CHARS} 字,省略以省 token)")
            )
            break

        rendered_blocks.append((d, block_text))
        total_chars += len(block_text)

    if not rendered_blocks:
        return ""

    # 反转使原作根在最前,父辈在最后(LLM 自然阅读顺序)
    rendered_blocks.sort(key=lambda x: -x[0])
    lines = [
        "### 完整继承链(从最早祖先到本代父辈,**按时序读**)",
        "_2026-06-01 v2:近 2 代用全文尾部(细节/对白/伏笔保真),更远代用摘要(节省 token)_",
    ]
    for _, block in rendered_blocks:
        lines.append(block)

    return "\n".join(lines)


def _build_character_arcs_section(
    conn: sqlite3.Connection,
    project_id: str,
) -> str:
    """跨代角色弧光段 — 显示 character 一路演化轨迹。"""
    rows = fetch_all(
        conn,
        "SELECT * FROM character_arcs WHERE project_id=? "
        "ORDER BY created_at DESC LIMIT ?",
        (project_id, MAX_ARCS_LISTED),
    )
    if not rows:
        return ""
    arcs = [CharacterArc.from_row(r) for r in rows]
    arcs.reverse()  # 倒回时间顺序

    # 拉相关 character 的 name 给 LLM 易读(批量查 IN)
    all_char_ids: set[str] = set()
    for a in arcs:
        all_char_ids.update(a.character_ids)
    char_name_map: dict[str, str] = {}
    if all_char_ids:
        char_rows = fetch_all(
            conn,
            f"SELECT id, name FROM characters WHERE id IN ({','.join(['?']*len(all_char_ids))})",
            tuple(all_char_ids),
        )
        char_name_map = {r["id"]: r["name"] for r in char_rows}

    lines = ["### 跨代角色弧光(已累积的心境演化,本代生成时必须承接)"]
    for a in arcs:
        names = [char_name_map.get(cid, "(unknown)") for cid in a.character_ids]
        names_str = " / ".join(names) if names else "全员"
        lines.append(
            f"- {names_str} · 第 {a.scene_index} 幕 · **{a.arc_keyword}**({a.arc_kind})"
            + (f" — {a.trigger_summary}" if a.trigger_summary else "")
        )

    return "\n".join(lines)


def _build_open_foreshadows_section(
    conn: sqlite3.Connection,
    project_id: str,
    sim: Simulation | None = None,
) -> str:
    """open 伏笔段 — scene_picker / outline 应主动推进 high 优先级未解坑.

    阶段 3A(2026-06-02):若 sim 有 inherited_foreshadow_ids_json 字段:
      - None / 不存在 → 拉所有 open(向后兼容)
      - [] → 一条不读
      - [...] → 只读子集
    """
    # 读用户选的伏笔 ids(如果有)
    inherited_ids: list[str] | None = None
    if sim is not None:
        try:
            sim_row = fetch_one(
                conn,
                "SELECT inherited_foreshadow_ids_json FROM simulations WHERE id=?",
                (sim.id,),
            )
            if sim_row:
                inh_raw = sim_row["inherited_foreshadow_ids_json"]
                if inh_raw:
                    import json as _json
                    inherited_ids = _json.loads(inh_raw)
        except (sqlite3.OperationalError, KeyError, IndexError, TypeError):
            # 老库无此列 → 走默认路径
            inherited_ids = None

    if inherited_ids == []:
        # 用户主动空选 — 不返伏笔
        return ""

    if inherited_ids:
        # 阶段 3A:用户选了子集
        placeholders = ",".join(["?"] * len(inherited_ids))
        rows = fetch_all(
            conn,
            f"SELECT * FROM foreshadow_ledger "
            f"WHERE project_id=? AND status='open' AND id IN ({placeholders}) "
            f"ORDER BY CASE priority "
            f"  WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
            f"  created_at ASC LIMIT ?",
            (project_id, *inherited_ids, MAX_OPEN_FORESHADOWS),
        )
    else:
        # 默认路径(F1.4 行为):拉所有 open
        rows = fetch_all(
            conn,
            "SELECT * FROM foreshadow_ledger "
            "WHERE project_id=? AND status='open' "
            "ORDER BY CASE priority "
            "  WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
            "  created_at ASC LIMIT ?",
            (project_id, MAX_OPEN_FORESHADOWS),
        )
    if not rows:
        return ""
    foreshadows = [Foreshadow.from_row(r) for r in rows]

    lines = [
        "### 未解伏笔(open,本代续作应主动推进 / 收尾高优先级的)",
    ]
    for f in foreshadows:
        prio_label = {"high": "🔴高", "medium": "🟡中", "low": "🟢低"}.get(f.priority, "")
        lines.append(f"- {prio_label} 「{f.content}」(埋于上代第 {f.introduced_scene_index} 幕)")

    return "\n".join(lines)


def _build_world_rules_section(
    conn: sqlite3.Connection,
    project_id: str,
) -> str:
    """累积世界规则段 — LLM 在本代必须遵守。"""
    rows = fetch_all(
        conn,
        "SELECT * FROM world_rules_ledger "
        "WHERE project_id=? AND active=1 "
        "ORDER BY created_at ASC LIMIT ?",
        (project_id, MAX_WORLD_RULES),
    )
    if not rows:
        return ""
    rules = [WorldRule.from_row(r) for r in rows]

    lines = [
        "### 跨代累积的世界规则(由前代续作创立,本代必须遵守)",
    ]
    for r in rules:
        scope_label = {
            "global": "🌐", "faction": "👥", "location": "📍", "character": "👤",
        }.get(r.scope, "")
        lines.append(f"- {scope_label} {r.rule_text}")

    return "\n".join(lines)


# ======================================================================
# 单独消费 — scene_picker / outline 拉 open 伏笔列表(机器可读)
# ======================================================================

def list_open_foreshadows(
    conn: sqlite3.Connection,
    project_id: str,
    limit: int = MAX_OPEN_FORESHADOWS,
) -> list[Foreshadow]:
    """拉本 project 的 open 伏笔列表,给 scene_picker 显式提示用。

    优先级:high → medium → low;同级按引入时间升序(老坑先解)
    """
    rows = fetch_all(
        conn,
        "SELECT * FROM foreshadow_ledger "
        "WHERE project_id=? AND status='open' "
        "ORDER BY CASE priority "
        "  WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
        "  created_at ASC LIMIT ?",
        (project_id, limit),
    )
    return [Foreshadow.from_row(r) for r in rows]


def list_active_world_rules(
    conn: sqlite3.Connection,
    project_id: str,
) -> list[WorldRule]:
    """拉本 project active 的世界规则列表。"""
    rows = fetch_all(
        conn,
        "SELECT * FROM world_rules_ledger "
        "WHERE project_id=? AND active=1 "
        "ORDER BY created_at ASC LIMIT ?",
        (project_id, MAX_WORLD_RULES),
    )
    return [WorldRule.from_row(r) for r in rows]


__all__ = [
    "build_chain_context",
    "list_open_foreshadows",
    "list_active_world_rules",
]
