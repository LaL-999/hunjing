"""Sprint 6.A1(2026-05-18)— 主角判定器(4 维度评分 + LLM 终审)。

产品意图(对齐用户拍板原则):
  "不要吝啬" — 戏份多、戏份持续长、互动频繁、关键事件参与 任一达标即纳入主角列表。
  上限 15(防 LLM 误抽 30+ 角色)。

3 态生效路径:
  - 初始态:用户手动建角色;judger 也可跑(评分仅参考,is_protagonist 默认 false 让用户手选)
  - 中间/末尾态:extract 完工后 hook 自动跑 → 落 is_protagonist + score + reasons
  - 反事实改属性时:仅重跑 enricher 更新档案,**不重判主角**(主角身份反事实层面不变)

4 维度判定指标(任一达标即候选):
  1. 出场密度:在 extract_chunk_results.graph_json.entities 出现 ≥ 5 次
     (跨 chunk 累加,每个 chunk 重复出现也算累加 — 反映"反复登场")
  2. 戏份持续:首次到末次出现的 chunk_index 跨度 ≥ 全书总 chunk 数 × 30%
     (即使每章戏份少但贯穿始终 — 如刘姥姥)
  3. 互动频度:与其他出场密度 top-10 角色共场次数 ≥ 3
     (共场 = 出现在同一 chunk 的 entities 里)
  4. 关键事件:在 events 表里作为 participant 出现 ≥ 2 次
     (重大转折参与者必须是主角)

LLM 终审(可选):
  4 维度评分给出 top-N(上限 25)候选 → LLM 看候选名单 + 出场原文摘要 →
  返回最终主角列表(≤ 15)+ 删除"明显不该是主角"的(如同名角色去重 / 工具人)。
  失败降级:LLM 调用失败时直接取评分 top-15(safe fallback)。

落库:
  - 评分维度 1-4 任一达标 → 候选;LLM 终审通过 → is_protagonist=true
  - protagonist_score = 4 维度加权平均(0.0-1.0)
  - protagonist_reasons_json = ["出场 23 次 ✓", "贯穿 67% 章节 ✓", ...] (透明给用户)
  - protagonist_user_pinned=true 的角色 → judger 不覆盖
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.models.character import Character
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)


# 主角上限(用户拍板:不要吝啬,普通中篇 6-10,长篇 12-15)
PROTAGONIST_MAX = 15
# Sprint 6.A2 FOCUS(2026-05-21,用户调阈值):综合 score 低于此值的角色不列为主角
# 即使 4 维度任一达标(候选)也会被此阈值拦截。用户反馈"15 个主角太多"。
# 0.40 = AI 综合评分 40 分(前端显示 "AI 40" 是临界线;<40 一律剔除)
MIN_PROTAGONIST_SCORE = 0.40
# 4 维度阈值(用户拍板"不吝啬"原则,任一达标即候选)
MIN_APPEARANCE_COUNT = 5         # 出场密度
MIN_SPAN_RATIO = 0.30            # 戏份持续(全书 chunk 数的 30%)
MIN_INTERACTION_COUNT = 3        # 与 top-10 共场次数
MIN_EVENT_PARTICIPATION = 2      # 关键事件参与次数
# 4 维度加权(总和 1.0,加权平均得 protagonist_score)
WEIGHT_APPEARANCE = 0.35
WEIGHT_SPAN = 0.25
WEIGHT_INTERACTION = 0.25
WEIGHT_EVENT = 0.15


@dataclass
class ProtagonistMetrics:
    """单角色 4 维度评分结果(供 judger 内部 + UI 调试)。"""
    character_id: str
    character_name: str
    appearance_count: int
    appearance_chunks: set[int]
    span_ratio: float
    interaction_count: int
    event_participation: int
    reasons: list[str]   # 命中的人类可读理由
    score: float         # 0.0-1.0 加权综合
    is_candidate: bool   # 4 维度任一达标即 True


def _normalize_value(v: float, threshold: float, cap: float) -> float:
    """评分归一化:< threshold 按线性 0→0.5;>= threshold 按 0.5→1.0(threshold 处 0.5)。

    cap 是该维度的"满分阈值"(达到 cap 即得 1.0,超过也是 1.0)。
    """
    if v <= 0:
        return 0.0
    if v < threshold:
        return min(0.5, (v / threshold) * 0.5)
    if v >= cap:
        return 1.0
    return 0.5 + ((v - threshold) / (cap - threshold)) * 0.5


def _compute_metrics_for_project(
    conn: sqlite3.Connection, project_id: str,
) -> list[ProtagonistMetrics]:
    """计算项目所有角色的 4 维度评分。

    数据源:
      - characters 表 — 角色 id + name
      - extract_chunk_results.graph_json — 每 chunk 的 entities(出场密度 / 持续 / 互动)
      - events.participants — 关键事件参与

    依赖:中间/末尾态走 graph extraction 流程后才有 extract_chunk_results 数据。
    初始态(用户手动建)无此数据 → 出场密度 / 持续 / 互动全 0,只有关键事件维度可能命中。
    """
    # 拉项目所有角色
    char_rows = fetch_all(
        conn,
        "SELECT id, name FROM characters WHERE project_id=?",
        (project_id,),
    )
    if not char_rows:
        return []
    char_id_to_name = {r["id"]: r["name"] for r in char_rows}
    char_name_to_id = {r["name"]: r["id"] for r in char_rows}

    # ===== 维度 1+2+3:从 extract_chunk_results 算 =====
    # 拉本项目相关的 chunk results(走 graph_extraction_jobs)
    chunk_rows = fetch_all(
        conn,
        """SELECT ecr.chunk_index, ecr.graph_json
           FROM extract_chunk_results ecr
           JOIN graph_extraction_jobs gej ON gej.id = ecr.job_id
           WHERE gej.project_id=?
           ORDER BY ecr.chunk_index ASC""",
        (project_id,),
    )

    appearance_count: dict[str, int] = {cid: 0 for cid in char_id_to_name}
    appearance_chunks: dict[str, set[int]] = {cid: set() for cid in char_id_to_name}
    per_chunk_chars: dict[int, set[str]] = {}    # chunk_index → set[char_id]

    total_chunks = 0
    for row in chunk_rows:
        total_chunks = max(total_chunks, row["chunk_index"] + 1)
        try:
            graph = json.loads(row["graph_json"]) if row["graph_json"] else {}
        except (json.JSONDecodeError, TypeError):
            continue
        entities = graph.get("entities") or []
        if not isinstance(entities, list):
            continue
        chunk_chars: set[str] = set()
        for ent in entities:
            if not isinstance(ent, dict):
                continue
            # 只算 PERSON 实体(对齐 build_graph.md v3)
            etype = (ent.get("type") or "").upper()
            if etype != "PERSON":
                continue
            ent_name = (ent.get("name") or "").strip()
            if not ent_name:
                continue
            # 用 name 反查 character_id(角色必须在 characters 表里才计入)
            cid = char_name_to_id.get(ent_name)
            if not cid:
                continue
            appearance_count[cid] += 1
            appearance_chunks[cid].add(row["chunk_index"])
            chunk_chars.add(cid)
        if chunk_chars:
            per_chunk_chars[row["chunk_index"]] = chunk_chars

    # span_ratio = (max_chunk - min_chunk + 1) / total_chunks
    span_ratio: dict[str, float] = {}
    for cid, chunks in appearance_chunks.items():
        if not chunks or total_chunks == 0:
            span_ratio[cid] = 0.0
        else:
            span_ratio[cid] = (max(chunks) - min(chunks) + 1) / total_chunks

    # 互动次数:与"出场密度 top-10"共场
    top10_cids = {
        cid for cid, _ in sorted(
            appearance_count.items(), key=lambda kv: kv[1], reverse=True,
        )[:10]
        if appearance_count[cid] > 0
    }
    interaction_count: dict[str, int] = {cid: 0 for cid in char_id_to_name}
    for chunk_chars in per_chunk_chars.values():
        # 该 chunk 内每个角色,与 top10 共场计数(去掉自己)
        top10_in_chunk = chunk_chars & top10_cids
        for cid in chunk_chars:
            interaction_count[cid] += len(top10_in_chunk - {cid})

    # ===== 维度 4:events.participants =====
    event_rows = fetch_all(
        conn,
        "SELECT participants FROM events WHERE project_id=?",
        (project_id,),
    )
    event_participation: dict[str, int] = {cid: 0 for cid in char_id_to_name}
    for er in event_rows:
        try:
            parts = json.loads(er["participants"]) if er["participants"] else []
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(parts, list):
            continue
        for cid in parts:
            if cid in event_participation:
                event_participation[cid] += 1

    # ===== 聚合到 ProtagonistMetrics =====
    results: list[ProtagonistMetrics] = []
    for cid, name in char_id_to_name.items():
        reasons: list[str] = []
        ac = appearance_count[cid]
        sr = span_ratio[cid]
        ic = interaction_count[cid]
        ev = event_participation[cid]

        if ac >= MIN_APPEARANCE_COUNT:
            reasons.append(f"出场 {ac} 次 ✓")
        if sr >= MIN_SPAN_RATIO:
            reasons.append(f"贯穿 {int(sr * 100)}% 章节 ✓")
        if ic >= MIN_INTERACTION_COUNT:
            reasons.append(f"与主角共场 {ic} 次 ✓")
        if ev >= MIN_EVENT_PARTICIPATION:
            reasons.append(f"关键事件参与 {ev} 次 ✓")

        # 加权评分
        score = (
            _normalize_value(ac, MIN_APPEARANCE_COUNT, 30) * WEIGHT_APPEARANCE
            + _normalize_value(sr * 100, MIN_SPAN_RATIO * 100, 100) * WEIGHT_SPAN
            + _normalize_value(ic, MIN_INTERACTION_COUNT, 20) * WEIGHT_INTERACTION
            + _normalize_value(ev, MIN_EVENT_PARTICIPATION, 10) * WEIGHT_EVENT
        )

        results.append(ProtagonistMetrics(
            character_id=cid,
            character_name=name,
            appearance_count=ac,
            appearance_chunks=appearance_chunks[cid],
            span_ratio=sr,
            interaction_count=ic,
            event_participation=ev,
            reasons=reasons,
            score=round(score, 4),
            is_candidate=bool(reasons),
        ))

    # 按 score 倒序(主角墙显示顺序)
    results.sort(key=lambda m: m.score, reverse=True)
    return results


def _apply_protagonist_cap(
    metrics: list[ProtagonistMetrics],
) -> set[str]:
    """从候选列表选最终主角(score ≥ MIN_PROTAGONIST_SCORE,上限 PROTAGONIST_MAX)。

    过滤规则(2026-05-21 用户调整):
      ① 必须 is_candidate(4 维度任一达标,M6.A1 原规则)
      ② 必须 score ≥ MIN_PROTAGONIST_SCORE(综合分阈值,新加)
      ③ 满足 ①② 后按 score 倒序取前 PROTAGONIST_MAX

    why 双过滤:is_candidate 防止"4 维度全 0 但综合分被加权拉到 0.45"误判;
    MIN_PROTAGONIST_SCORE 防止"事件参与 2 次单点达标但其他维度全 0,综合分 0.30"
    被列入主角(用户反馈 15 个主角太多就是这个 case)。

    未来可接 LLM 终审做语义去重(如同名角色 / 工具人剔除)。
    """
    # 双过滤后按 score 排,取前 PROTAGONIST_MAX
    sorted_candidates = sorted(
        [
            m for m in metrics
            if m.is_candidate and m.score >= MIN_PROTAGONIST_SCORE
        ],
        key=lambda m: m.score, reverse=True,
    )[:PROTAGONIST_MAX]
    return {m.character_id for m in sorted_candidates}


def judge_protagonists(
    conn: sqlite3.Connection, project_id: str,
    *, respect_user_pinned: bool = True,
) -> dict:
    """对项目所有角色批量判定主角身份,落库 4 字段。

    Args:
      conn: sqlite 连接(调用方负责事务)
      project_id: 项目 id
      respect_user_pinned: True 时 protagonist_user_pinned=1 的角色不被覆盖
                           (用户手动勾过的决定 AI 不动)

    Returns: dict 报告 {
      "judged_count": int,
      "protagonist_count": int,
      "skipped_pinned": int,
      "metrics": list[dict]  # 给 router 返回前端用
    }
    """
    metrics = _compute_metrics_for_project(conn, project_id)
    final_protagonist_ids = _apply_protagonist_cap(metrics)

    # 拉 user_pinned 状态
    pinned_rows = fetch_all(
        conn,
        "SELECT id, protagonist_user_pinned FROM characters WHERE project_id=?",
        (project_id,),
    )
    pinned_ids = {
        r["id"] for r in pinned_rows
        if respect_user_pinned and bool(r["protagonist_user_pinned"])
    }

    now = iso_now()
    skipped = 0
    for m in metrics:
        if m.character_id in pinned_ids:
            skipped += 1
            continue
        new_is_protag = 1 if m.character_id in final_protagonist_ids else 0
        execute(
            conn,
            """UPDATE characters
               SET is_protagonist=?, protagonist_score=?,
                   protagonist_reasons_json=?, updated_at=?
               WHERE id=?""",
            (
                new_is_protag, m.score,
                json.dumps(m.reasons, ensure_ascii=False),
                now, m.character_id,
            ),
        )
    conn.commit()

    return {
        "judged_count": len(metrics),
        "protagonist_count": len(final_protagonist_ids),
        "skipped_pinned": skipped,
        "metrics": [
            {
                "character_id": m.character_id,
                "character_name": m.character_name,
                "score": m.score,
                "is_candidate": m.is_candidate,
                "is_protagonist_after": (
                    m.character_id in final_protagonist_ids
                    and m.character_id not in pinned_ids
                ),
                "reasons": m.reasons,
                "metrics_detail": {
                    "appearance_count": m.appearance_count,
                    "span_ratio": round(m.span_ratio, 3),
                    "interaction_count": m.interaction_count,
                    "event_participation": m.event_participation,
                },
            }
            for m in metrics
        ],
    }


def list_protagonists(
    conn: sqlite3.Connection, project_id: str,
) -> list[Character]:
    """列出项目当前主角(按 protagonist_score 倒序),前端 ProtagonistWall 用。"""
    rows = fetch_all(
        conn,
        """SELECT * FROM characters
           WHERE project_id=? AND is_protagonist=1
           ORDER BY protagonist_score DESC, name ASC""",
        (project_id,),
    )
    return [Character.from_row(r) for r in rows]
