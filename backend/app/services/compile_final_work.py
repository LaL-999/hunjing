"""走向终章 + 滚雪球合并最终作品(2026-06-01 v2 + 2026-06-02 patch A).

v1(deprecated):把合并文本 UPDATE 到源 sim 的 final_compiled_narrative 字段.
v2:把合并产物建为**独立 sim 行**(is_final_compilation=1),独立卡片 / 阅读 / 导出 / 徽章.
2026-06-02 patch A:合并前清除每段 narrative 首行 "> 语体:..." meta(否则会在合并文本中段残留多次).

触发:sim 完成 + with_grand_finale=True + 有 ancestors(继承链非空)
产物:新 sim 行(state=done, mode=compilation, cost=0, is_final_compilation=1)

合并策略:
  - 沿 context_simulation_ids 顺序拉所有前篇 narrative(按用户当时选择的顺序)
  - 末尾接上本 sim narrative
  - 之间用两个 \\n\\n 作为段落分隔,**不加章标题 / 元信息**
  - 不调 LLM 写过渡 — 因为本 sim 续写时就是从前篇结尾接的,不需要二次过渡
  - **每段先 strip 首行 "> 语体:..." 元数据**(由 composer.md 教 LLM 输出,前端阅读时跳过,合并时也必须跳)

判重:
  - 同 source sim 已有合并产物 → 不重复创建(避免用户多次触发完成时反复合并)
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timezone

from app.db import execute, fetch_one
from app.models.simulation import Simulation

logger = logging.getLogger(__name__)


_SECTION_SEPARATOR = "\n\n"

# 2026-06-02 patch A:匹配 composer.md 教 LLM 输出的元数据行
# 形如 "> 语体:现代都市言情 · 由 AI 根据角色 + 题材自适应"
# 与前端 SimulationReadView.stripMetaLine 的 /^>\s*语体/ 等价
_META_LINE_PATTERN = re.compile(r"^\s*>\s*语体[::]")


def _strip_narrative_meta_lines(narrative: str) -> str:
    """清除 narrative 首行 + 内部夹杂的 "> 语体:..." 元数据行.

    合并时每段都要调,避免:
      - 第 1 段首行 meta(开头看着是 meta,合并到中段就成了"残留")
      - LLM 偶尔在段间也输出 meta 行(罕见但已观察到)

    策略:行级扫描,只删除符合 _META_LINE_PATTERN 的行,其余完整保留.
    同时清除前后多余空白行(避免删除 meta 后留下 3+ 个连续空行).
    """
    if not narrative:
        return ""
    lines = narrative.split("\n")
    kept: list[str] = []
    for ln in lines:
        if _META_LINE_PATTERN.match(ln):
            continue  # skip meta line
        kept.append(ln)
    result = "\n".join(kept).strip()
    # 压缩连续 3+ 空行 → 2 空行(美化)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def compile_final_work(conn: sqlite3.Connection, sim: Simulation) -> str:
    """沿继承链合并所有 ancestor narrative + 本 sim narrative,返合并文本.

    安全策略:
      - 任何 ancestor 拉不到 / narrative 为空 → 跳过(继续合并其余 + 本 sim)
      - 至少要有本 sim narrative;若本 sim narrative 也空 → 返空字符串
      - 不写入 DB(由 caller 决定何时落库)
    """
    parts: list[str] = []

    ctx_ids = sim.context_simulation_ids or []
    if ctx_ids:
        for prev_id in ctx_ids:
            prev_row = fetch_one(
                conn,
                "SELECT narrative FROM simulations WHERE id=?",
                (prev_id,),
            )
            if not prev_row:
                logger.info(
                    f"compile_final_work: ancestor sim {prev_id} not found — skip"
                )
                continue
            # 2026-06-02 patch A:清除 "> 语体:..." 元数据行(否则中段残留)
            prev_narr = _strip_narrative_meta_lines(prev_row["narrative"] or "")
            if prev_narr:
                parts.append(prev_narr)
            else:
                logger.info(
                    f"compile_final_work: ancestor sim {prev_id} has empty narrative — skip"
                )

    # 2026-06-02 patch A:本 sim 也要清,虽然本 sim 是最后一段(strip 首行无显眼影响)
    # 但若 LLM 在段中也输出 meta(罕见),strip 仍然必要
    cur_narr = _strip_narrative_meta_lines(sim.narrative or "")
    if cur_narr:
        parts.append(cur_narr)

    if not parts:
        return ""

    return _SECTION_SEPARATOR.join(parts)


def _find_existing_compilation_for_source(
    conn: sqlite3.Connection, source_sim_id: str,
) -> str | None:
    """查同 source 是否已有合并产物 sim — 避免重复创建.

    返:已有合并 sim 的 id,或 None.
    老库无 compiled_from_sim_id 列 → 返 None(降级到允许创建).
    """
    try:
        row = fetch_one(
            conn,
            "SELECT id FROM simulations "
            "WHERE is_final_compilation=1 AND compiled_from_sim_id=? "
            "LIMIT 1",
            (source_sim_id,),
        )
        return row["id"] if row else None
    except sqlite3.OperationalError:
        # 老库无此列 — 降级:不查重
        return None


def maybe_compile_and_persist_final_work(
    conn: sqlite3.Connection, sim: Simulation,
) -> bool:
    """sim done 时触发的便利函数.

    条件:
      - sim.with_grand_finale=True
      - sim.state=='done'
      - sim.context_simulation_ids 非空(无 ancestors → 孤本,无需合并)
      - 同 source 没有已存在的合并产物(判重)

    满足 → 调 compile_final_work + INSERT 新 sim 行(独立合并产物).
    不满足 → 返 False 静默退出.失败 → log + 返 False(不阻塞主流程).
    返回:是否真的创建了新合并 sim 行.
    """
    if not sim.with_grand_finale:
        return False
    if sim.state != "done":
        return False
    ctx_ids = sim.context_simulation_ids or []
    if not ctx_ids:
        # 孤本 + 走向终章 → 本 sim narrative 自己就是最终作品,无需合并
        return False
    # 本 sim 自己已是合并产物 → 不重复合并
    if sim.is_final_compilation:
        return False
    # 判重:同 source 已有合并产物 → 不创建
    existing_id = _find_existing_compilation_for_source(conn, sim.id)
    if existing_id:
        logger.info(
            f"compile_final_work: skip — sim {sim.id} already has compilation {existing_id}"
        )
        return False

    try:
        compiled = compile_final_work(conn, sim)
        if not compiled:
            logger.warning(
                f"compile_final_work: empty compiled output for sim {sim.id}, skip"
            )
            return False

        # ===== INSERT 新 sim 行作为独立合并产物 =====
        new_sim_id = str(uuid.uuid4())
        now = _iso_now()
        # 合并产物的 context_simulation_ids = source 的 ctx + source 自己(完整继承链)
        full_ctx_ids = list(ctx_ids) + [sim.id]
        # snapshot / target_chars 都从 source sim 派生
        # cost / tokens / rounds 全 0(不算创作)
        execute(
            conn,
            "INSERT INTO simulations "
            "(id, project_id, user_id, divergence, reshape_percent, rounds_planned, "
            " target_chars, style, custom_style_hint, context_simulation_ids, "
            " characters_snapshot, original_tail_excerpt, mode, use_outline_first, "
            " anchor_event_id, with_grand_finale, narrative_pacing, chapter_size_chars, "
            " state, current_round, narrative, "
            " tokens_input, tokens_output, cost_yuan, "
            " is_final_compilation, compiled_from_sim_id, "
            " start_chapter_locked, "
            " created_at, started_at, completed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
            "        'done', 0, ?, "
            "        0, 0, 0, "
            "        1, ?, "
            "        1, "
            "        ?, ?, ?)",
            (
                new_sim_id,
                sim.project_id,
                sim.user_id,
                f"走向终章自动合并:{len(ctx_ids)} 篇前作 + 本篇 → 完整最终作品",
                sim.reshape_percent,
                sim.rounds_planned,
                len(compiled),
                sim.style,
                sim.custom_style_hint,
                json.dumps(full_ctx_ids, ensure_ascii=False),
                json.dumps(sim.characters_snapshot, ensure_ascii=False),
                sim.original_tail_excerpt,
                "compilation",  # 新 mode 值,区分 quick/evolution
                0,  # use_outline_first
                sim.anchor_event_id,
                1,  # with_grand_finale = 1(本身就是终章合并产物)
                sim.narrative_pacing,
                sim.chapter_size_chars,
                compiled,  # narrative
                sim.id,  # compiled_from_sim_id
                now, now, now,  # created_at, started_at, completed_at(瞬间完成)
            ),
        )
        conn.commit()
        logger.info(
            f"compile_final_work: created independent compilation sim {new_sim_id} "
            f"from source {sim.id} ({len(compiled)} chars, "
            f"{len(ctx_ids)} ancestors + self)"
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"compile_final_work failed for sim {sim.id}: {e}")
        return False


__all__ = ["compile_final_work", "maybe_compile_and_persist_final_work"]
