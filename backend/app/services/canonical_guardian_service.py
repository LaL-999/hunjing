"""正典守护者服务 — Sprint 2.D(差异化王牌)。

设计哲学(doc 3):
  自洽守护者 1.R:产物 vs 用户设定(应然)
  **正典守护者 2.D:产物 vs 原作 canon(已然)** — 这条线
  竞品短期内难抄走:需要 prompt + 推演链路 + 反事实豁免算法的完整集成

模式约束:
  initial 态没原作可守 → 正典审计 N/A
  middle / end / cycle 态 → 才可触发

不扣配额(对齐 1.R)— 创作辅助工具,鼓励反复用

异步设计(对齐 simulation_service / extract_service):
  trigger_canonical_audit:创建行 state='running' + kick_off worker
  run_canonical_audit:线程内跑 LLM(可能 30-60s),完成后 state='done'/'failed'
  前端 polling /latest 监视状态
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from app.db import execute, fetch_all, fetch_one, get_connection, transaction
from app.models.canonical_audit import (
    CanonicalAudit,
    VALID_DIMENSIONS,
    VALID_SEVERITIES,
)
from app.services.counterfactual_service import build_director_context
from app.services.llm_client import call_llm_json, estimate_cost_yuan
from app.services.project_service import (
    ResourceNotFoundOrForbidden,
    get_project_or_403,
    iso_now,
)
from app.services.simulation_service import get_simulation_or_404


PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"


# === 异常 ===

class CanonicalAuditNotApplicable(Exception):
    """模式不适用(initial 态没原作可守)。"""


class SimulationNotAuditable(Exception):
    """推演非 done 状态 / narrative 空。"""


class CanonicalAuditNotFoundOrForbidden(Exception):
    """audit 不存在 / 不属于该用户。"""


# ======================================================================
# trigger + run worker
# ======================================================================

def _load_prompt() -> str:
    return (PROMPTS_DIR / "canonical_guardian.md").read_text(encoding="utf-8")


def trigger_canonical_audit(
    conn: sqlite3.Connection, sim_id: str, user_id: str
) -> dict:
    """同步触发:创建 audit 行 + kick_off worker,返回 audit 初始状态。

    Raises:
      ResourceNotFoundOrForbidden     sim 不属于用户
      CanonicalAuditNotApplicable     项目 mode='initial'(无原作)
      SimulationNotAuditable          sim 非 done / narrative 空
    """
    sim = get_simulation_or_404(conn, sim_id, user_id)
    project = get_project_or_403(conn, sim.project_id, user_id)
    if project.mode == "initial":
        raise CanonicalAuditNotApplicable(
            "初始态项目无原作 canon,正典审计不适用 — 只在导入原作的中间态 / 末尾态 / 周期态启用"
        )
    if sim.state != "done":
        raise SimulationNotAuditable(
            f"只能审计已完成的推演(当前 state={sim.state})"
        )
    if not sim.narrative:
        raise SimulationNotAuditable("产物 narrative 为空,无可审")

    audit_id = str(uuid.uuid4())
    now = iso_now()
    with transaction(conn) as tx:
        execute(
            tx,
            "INSERT INTO canonical_audits "
            "(id, simulation_id, project_id, user_id, state, created_at) "
            "VALUES (?, ?, ?, ?, 'running', ?)",
            (audit_id, sim_id, project.id, user_id, now),
        )

    kick_off_canonical_audit(audit_id)

    row = fetch_one(
        conn, "SELECT * FROM canonical_audits WHERE id=?", (audit_id,),
    )
    # Sprint D.7:刚 kick_off,is_alive 实时查注册表(测试 monkeypatch 同步 kick_off
    # 时 worker 已完成,is_audit_alive 返 False — 真实生产是 True)
    return CanonicalAudit.from_row(row).to_response(
        is_alive=is_audit_alive(audit_id),
    )


# ======================================================================
# Sprint D.7:zombie 检测注册表(对齐 extract_service._RUNNING_EXTRACTS)
#
# 问题:run_canonical_audit 跑 LLM 30-60s,期间 backend 重启 → DB state='running'
# 但实际线程已死,前端轮 /latest 永远看到 running → 误以为 LLM 还在跑。
#
# 解法:模块级注册表 + is_audit_alive 判定:
#   ① DB state='running' + is_audit_alive=True  → 真在跑
#   ② DB state='running' + is_audit_alive=False → 僵尸,前端可显"重新触发"
#
# 与 extract_service 不同点:guardian 不支持 resume(LLM 单次调用,断了重跑代价低 < ¥0.1)
# 所以这里只暴露 is_alive 派生字段,不实现 cancel 协议。
# ======================================================================

_RUNNING_AUDITS: dict[str, threading.Thread] = {}
_RUNNING_LOCK = threading.Lock()


def _register_audit(audit_id: str, thread: threading.Thread) -> None:
    with _RUNNING_LOCK:
        _RUNNING_AUDITS[audit_id] = thread


def _unregister_audit(audit_id: str) -> None:
    with _RUNNING_LOCK:
        _RUNNING_AUDITS.pop(audit_id, None)


def is_audit_alive(audit_id: str) -> bool:
    """该 audit 当前是否有活跃 worker(给 CanonicalAuditResponse.is_alive 派生字段用)。

    僵尸态识别:db state='running' 且 is_audit_alive=False → 后端重启后的僵尸,
    前端轮询时识别并显"重新触发"按钮(guardian 不支持 resume,失败成本低,
    直接重试比写复杂的断点续审更便宜)。
    """
    with _RUNNING_LOCK:
        return audit_id in _RUNNING_AUDITS


def _default_kick_off(audit_id: str) -> None:
    """生产:起后台 daemon 线程跑 run_canonical_audit。

    Sprint D.7:注册 worker 到 _RUNNING_AUDITS,run_canonical_audit finally 解注册。
    2026-06-05 BYOK:capture context → 让 thread 里 LLM 调用能拿到用户 user_id。
    """
    from app.services.byok_context import capture_current_context
    ctx = capture_current_context()
    thread = threading.Thread(
        target=ctx.run,
        args=(_run_with_register, audit_id),
        daemon=True,
        name=f"canonical-{audit_id[:8]}",
    )
    _register_audit(audit_id, thread)
    thread.start()


def _run_with_register(audit_id: str) -> None:
    """包装 run_canonical_audit:确保 finally 解注册(即使 worker crash 也清理注册表)"""
    try:
        run_canonical_audit(audit_id)
    finally:
        _unregister_audit(audit_id)


# 模块级可替换(测试 monkeypatch 为同步直跑)
kick_off_canonical_audit: Callable[[str], None] = _default_kick_off


def run_canonical_audit(audit_id: str) -> None:
    """worker:拉数据 + LLM 调用 + 落 issues。"""
    conn = get_connection()
    try:
        audit_row = fetch_one(
            conn, "SELECT * FROM canonical_audits WHERE id=?", (audit_id,),
        )
        if not audit_row:
            return
        audit = CanonicalAudit.from_row(audit_row)

        # 拉 sim / project / characters / relationships / events
        sim_row = fetch_one(
            conn, "SELECT * FROM simulations WHERE id=?", (audit.simulation_id,),
        )
        if not sim_row:
            _mark_failed(conn, audit_id, "关联推演已被删除")
            return
        project_row = fetch_one(
            conn, "SELECT * FROM projects WHERE id=?", (audit.project_id,),
        )
        if not project_row:
            _mark_failed(conn, audit_id, "关联项目已被删除")
            return

        # 编译 LLM input
        work_name = project_row["name"]
        narrative = sim_row["narrative"] or ""

        world_baseline_block = _render_world_baseline_block(project_row)
        character_baseline_block = _render_character_baseline_block(conn, audit.project_id)
        relationship_baseline_block = _render_relationship_baseline_block(conn, audit.project_id)
        event_baseline_block = _render_event_baseline_block(conn, audit.project_id)

        # 反事实(只取本 sim 关联的 + reverted_at IS NULL)
        counterfactuals_block = _render_counterfactuals_block(conn, audit.project_id, audit.simulation_id)

        # Sprint 6.A2 M7.A(2026-05-20):续作新引入的实体清单 — 给 LLM 看,治"误判新角色为事件偏离"
        # 数据来源:canonical_entities 表(M5.1 加,entity_registrar 每幕落)
        sequel_new_entities_block = _render_sequel_new_entities_block(conn, audit.simulation_id)

        # B5.3(2026-05-27):身体描写尺度基线 — 第 9 维度 body_register_alignment 审计输入
        # 数据来源:author_compass.internal_metrics.身体描写尺度(P4 加,LLM 反推得出)
        # 若用户没生成 author_compass → 返空,LLM 会跳过此维度审计
        body_register_block = _render_body_register_block(conn, audit.project_id)

        # P5.2(2026-05-27):outline key_events 清单 — 第 10 维度 outline_execution 审计输入
        # 数据来源:outline_scenes 表(outline-first 长篇模式才有)
        # 若 sim 没走 outline → 返空,LLM 跳过此维度
        outline_execution_block = _render_outline_execution_block(conn, audit.simulation_id)

        # SP-3.1(2026-06-02):信息边界审计输入 — 第 11 维度
        # 数据来源:character_knowledge + story_facts 表(SP-3 落地)
        # 若项目未录入 story_facts → 返空,LLM 跳过此维度
        information_boundary_block = _render_information_boundary_block(
            conn, audit.project_id, audit.simulation_id,
        )

        # SP-1 终审(2026-06-02):故事内核坚守度审计输入 — 第 12 维度
        # 数据来源:projects.core_dramatic_question / theme / ending_direction(SP-1 落地)
        # 三件套全空 → 返空,LLM 跳过此维度
        story_core_block_for_audit = _render_story_core_block_for_audit(
            conn, audit.project_id,
        )

        user_prompt = _build_user_prompt(
            work_name=work_name,
            world_baseline_block=world_baseline_block,
            character_baseline_block=character_baseline_block,
            relationship_baseline_block=relationship_baseline_block,
            event_baseline_block=event_baseline_block,
            counterfactuals_block=counterfactuals_block,
            sequel_new_entities_block=sequel_new_entities_block,
            body_register_block=body_register_block,
            outline_execution_block=outline_execution_block,
            information_boundary_block=information_boundary_block,
            story_core_block=story_core_block_for_audit,
            narrative_text=narrative,
        )

        system_prompt = _load_prompt()
        # 9 维度(B5.3 加 body_register_alignment)+ life_status block(B5.1)+ 多 issue + 引文
        # 2026-05-27:加了维度 + block 后 prompt 变长,12000+ 字续作实测可能 150-180s,
        #             timeout 120s → 180s,前端 poll 上限同步 200s 留余量
        parsed, usage = call_llm_json(
            system_prompt, user_prompt,
            max_tokens=6000, timeout=180.0, retries=2,
        )

        cleaned_issues = _validate_response(parsed)
        cost = estimate_cost_yuan(usage["input_tokens"], usage["output_tokens"])
        completed_at = iso_now()

        with transaction(conn) as tx:
            execute(
                tx,
                "UPDATE canonical_audits SET state='done', issues_json=?, "
                "tokens_input=?, tokens_output=?, cost_yuan=?, completed_at=? "
                "WHERE id=?",
                (
                    json.dumps(cleaned_issues, ensure_ascii=False),
                    usage["input_tokens"], usage["output_tokens"],
                    cost, completed_at, audit_id,
                ),
            )

    except Exception as e:  # noqa: BLE001
        import logging
        import traceback
        traceback.print_exc()
        logging.error("run_canonical_audit %s 失败: %s", audit_id, e)
        _mark_failed(conn, audit_id, f"{type(e).__name__}: {e}"[:300])
    finally:
        conn.close()


def _mark_failed(
    conn: sqlite3.Connection, audit_id: str, err_msg: str,
) -> None:
    completed_at = iso_now()
    with transaction(conn) as tx:
        execute(
            tx,
            "UPDATE canonical_audits SET state='failed', error_message=?, "
            "completed_at=? WHERE id=? AND state='running'",
            (err_msg, completed_at, audit_id),
        )


# ======================================================================
# baseline 编译 — 把 db 数据转 LLM 可读文本块
# ======================================================================

def _render_world_baseline_block(project_row: sqlite3.Row) -> str:
    """world_baseline_json → 6 维度可读块。"""
    raw = project_row["world_baseline_json"] if "world_baseline_json" in project_row.keys() else None
    if not raw:
        return "  (未识别 — 用户未跑 AI 识别原作世界观)"
    try:
        baseline = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "  (baseline 损坏,跳过此维度)"
    if not isinstance(baseline, dict):
        return "  (baseline 格式错,跳过此维度)"

    field_labels = {
        "genre": "体裁",
        "setting": "背景设定",
        "magic_system": "超能力体系",
        "time_axis": "时间轴",
        "tone": "整体基调",
        "free_form": "自由描述",
    }
    lines = []
    for key, label in field_labels.items():
        val = baseline.get(key) or "(未识别)"
        lines.append(f"  - {label}:{val}")
    return "\n".join(lines)


def _render_character_baseline_block(
    conn: sqlite3.Connection, project_id: str,
) -> str:
    """characters 表 → top 角色的 name | identity | personality | life_status 列表.

    2026-05-27(B4 漏检 1)— SQL 加 life_status / status_note。
    P0P 改了 narrator prompt 禁止已死角色出现(允尸体回顾,禁复活互动),
    但 canonical_guardian 的 baseline 块原本不含 life_status,LLM 审计时
    无法判"角色 X 已死但本幕重新活跃"为 severe_breach。
    本次补全:已死 / 在远方 / 未知等 non-alive 状态标注出来,LLM 审计有信号。
    """
    try:
        rows = fetch_all(
            conn,
            "SELECT name, identity, personality, life_status, status_note "
            "FROM characters WHERE project_id=? ORDER BY created_at LIMIT 30",
            (project_id,),
        )
    except Exception:  # noqa: BLE001 — 老 schema 缺 life_status 列时降级
        rows = fetch_all(
            conn,
            "SELECT name, identity, personality FROM characters "
            "WHERE project_id=? ORDER BY created_at LIMIT 30",
            (project_id,),
        )
    if not rows:
        return "  (无角色数据)"

    # life_status 中文标签 — 给 LLM 直观信号
    status_label = {
        "deceased": "⚠️ 已死",
        "in_facility": "🏥 在特定地点",
        "absent": "🚶 暂离",
        "unknown": "❓ 状态未知",
    }

    lines = []
    for r in rows:
        name = r["name"]
        identity = (r["identity"] or "").strip()[:80]
        personality = (r["personality"] or "").strip()[:80] or "(无)"
        # 取 life_status:可能不存在(降级 query)→ alive 默认
        try:
            ls = r["life_status"] or "alive"
            note = (r["status_note"] or "").strip()[:60]
        except (IndexError, KeyError):
            ls = "alive"
            note = ""
        status_str = ""
        if ls != "alive":
            tag = status_label.get(ls, ls)
            status_str = f" | {tag}"
            if note:
                status_str += f"({note})"
        lines.append(f"  - {name} | {identity} | {personality}{status_str}")
    return "\n".join(lines)


def _render_relationship_baseline_block(
    conn: sqlite3.Connection, project_id: str,
) -> str:
    """relationships 表 → top 关系列表。"""
    rows = fetch_all(
        conn,
        "SELECT r.type, r.description, sc.name AS sn, tc.name AS tn "
        "FROM relationships r "
        "LEFT JOIN characters sc ON sc.id=r.source_id "
        "LEFT JOIN characters tc ON tc.id=r.target_id "
        "WHERE r.project_id=? "
        "ORDER BY r.created_at LIMIT 50",
        (project_id,),
    )
    if not rows:
        return "  (无关系数据)"
    lines = []
    for r in rows:
        sn = r["sn"] or "?"
        tn = r["tn"] or "?"
        rtype = r["type"] or "?"
        desc = (r["description"] or "").strip()[:60]
        lines.append(f"  - {sn} ↔ {tn} [{rtype}] {desc}")
    return "\n".join(lines)


def _render_event_baseline_block(
    conn: sqlite3.Connection, project_id: str,
) -> str:
    """events 表 → top 事件列表。"""
    rows = fetch_all(
        conn,
        "SELECT description FROM events WHERE project_id=? "
        "ORDER BY created_at LIMIT 30",
        (project_id,),
    )
    if not rows:
        return "  (无事件数据)"
    lines = []
    for r in rows:
        desc = (r["description"] or "").strip()[:100]
        lines.append(f"  - {desc}")
    return "\n".join(lines)


def _render_counterfactuals_block(
    conn: sqlite3.Connection, project_id: str, simulation_id: str,
) -> str:
    """该 sim 关联的反事实 → "豁免列表"块给 LLM 看。

    用 counterfactual_service.build_director_context 复用:
      - 它已经过滤 reverted_at IS NULL
      - 已编译 target_name / field_label / from / to / user_intent
      - 这里只复用编译产物,加上 "豁免" 语境
    """
    # 拿该 sim 的 link 子集(如果没 link 则用全部 active,但通常 sim 都有 link)
    from app.services.counterfactual_service import get_linked_counterfactual_ids
    linked = get_linked_counterfactual_ids(conn, simulation_id)
    cf_ctx = build_director_context(conn, project_id, linked)
    items = cf_ctx.get("items", [])
    if not items:
        return "  (无反事实变量 — 推演纯按原作演,无豁免)"
    lines = []
    for it in items:
        target = it.get("target_name", "?")
        field_label = it.get("field_label", "")
        old = (it.get("from") or "")[:60]
        new = (it.get("to") or "")[:60]
        intent = it.get("user_intent") or ""
        line = f"  - [{it['target_type']}] {target} {field_label}:原「{old}」→ 改「{new}」"
        if intent:
            line += f" / 意图:{intent[:80]}"
        lines.append(line)
    return "\n".join(lines)


def _render_sequel_new_entities_block(
    conn: sqlite3.Connection, simulation_id: str,
) -> str:
    """读 canonical_entities 表,渲染本 sim 期间新引入的实体清单。

    Sprint 6.A2 M7.A(2026-05-20):给 LLM 看"续作新血液"白名单,治"误判新角色为事件偏离"。

    数据按 entity_type 分组(character / object / location / event)展示;
    若 sim 没产生任何新实体(可能是 quick 模式或老 sim 没跑 entity_registrar),
    返回明确提示"无续作新实体登记"——LLM 看到这块为空时,按原 canon 严格审,
    不放松任何角色 / 事件 / 道具的"原作存在性"检查。
    """
    rows = fetch_all(
        conn,
        "SELECT entity_type, canonical_name, aliases_json, description, first_introduced_scene "
        "FROM canonical_entities WHERE simulation_id=? "
        "ORDER BY entity_type, first_introduced_scene, canonical_name",
        (simulation_id,),
    )
    if not rows:
        return (
            "  (本次续作未登记任何新实体 — 若 narrative 中出现明显新角色 / 新事件 / 新道具,"
            "判定标准:与原作 canon 不矛盾即合规)"
        )

    # entity_type 中文标签
    type_labels = {
        "character": "新角色",
        "object":    "新道具",
        "location":  "新场景",
        "event":     "新事件",
    }

    # 按类型分桶
    buckets: dict[str, list[str]] = {k: [] for k in type_labels}
    for r in rows:
        et = r["entity_type"]
        if et not in buckets:
            continue
        # 解析 aliases
        raw_aliases = r["aliases_json"] or "[]"
        try:
            aliases = json.loads(raw_aliases)
            if not isinstance(aliases, list):
                aliases = []
        except (json.JSONDecodeError, TypeError):
            aliases = []
        canonical = r["canonical_name"]
        # 别名去重(剔除与 canonical 同名的)
        clean_aliases = [a for a in aliases if a and a != canonical]
        alias_str = f"(别名:{' / '.join(clean_aliases[:3])})" if clean_aliases else ""
        desc = (r["description"] or "").strip()[:80]
        scene = r["first_introduced_scene"]
        line = f"  - {canonical}{alias_str} — {desc}(第 {scene} 幕首次登场)"
        buckets[et].append(line)

    parts: list[str] = []
    for et, label in type_labels.items():
        bucket = buckets[et]
        if not bucket:
            continue
        parts.append(f"### {label}({len(bucket)} 个)")
        # 单类别上限 20 条,防 prompt 过载
        parts.extend(bucket[:20])
        if len(bucket) > 20:
            parts.append(f"  (...另有 {len(bucket) - 20} 个 {label} 略)")

    if not parts:
        return "  (本次续作未登记任何新实体)"
    return "\n".join(parts)


def _render_body_register_block(
    conn: sqlite3.Connection, project_id: str,
) -> str:
    """B5.3(2026-05-27)— 身体描写尺度基线渲染.

    数据来源:author_compass.internal_metrics.身体描写尺度(P4 加).
    若用户没跑过 P3 author_compass 或字段缺失 → 返空,LLM 会跳过此维度审计.
    """
    try:
        from app.services.author_compass_service import get_compass
        compass = get_compass(conn, project_id)
    except Exception as e:  # noqa: BLE001
        # 2026-06-02 批次 2:加 log(老库无 author_compass 表 / 项目无 compass 时降级)
        import logging
        logging.getLogger(__name__).debug(
            f"canonical_guardian: get_compass failed project={project_id}: {e}"
        )
        return ""
    if compass is None:
        return ""

    # 选数据源(同 author_compass_util 逻辑):锁定 → final;否则 internal
    if compass.user_locked and compass.final_compass:
        internal = (
            compass.final_compass.get("internal_metrics")
            or compass.final_compass.get("内部反推")
            or compass.final_compass
        )
    else:
        internal = compass.internal_metrics

    if not isinstance(internal, dict):
        return ""
    body = internal.get("身体描写尺度")
    if not isinstance(body, dict):
        return ""

    freq = body.get("频率") or ""
    explicit = body.get("直白度") or ""
    attitude = body.get("态度") or ""
    function = body.get("功能") or ""
    comment = body.get("评注") or ""

    if not (freq or explicit or attitude or function):
        return ""

    lines = ["  原作身体描写尺度(P4 基线,用于审计第 9 维度 body_register_alignment):"]
    if freq:
        lines.append(f"  - 频率: {freq}")
    if explicit:
        lines.append(f"  - 直白度: {explicit}")
    if attitude:
        lines.append(f"  - 态度: {attitude}")
    if function:
        lines.append(f"  - 功能: {function}")
    if comment:
        lines.append(f"  - 评注: {comment}")
    return "\n".join(lines)


def _render_outline_execution_block(
    conn: sqlite3.Connection, simulation_id: str,
) -> str:
    """P5.2(2026-05-27)— outline key_events 清单渲染.

    数据来源:outline_scenes 表(若 sim 没用 outline-first 模式 → 返空,LLM 跳过此维度).
    用于审计第 10 维 outline_execution — 给 LLM 提供"每幕必须发生的关键事件",
    它据此判断 narrative 是否完成。
    """
    try:
        from app.services.outline_orchestrator import load_outline_scenes_for_sim
        scenes = load_outline_scenes_for_sim(conn, simulation_id)
    except Exception as e:  # noqa: BLE001
        # 2026-06-02 批次 2:加 log(老 sim 无 outline 时降级)
        import logging
        logging.getLogger(__name__).debug(
            f"canonical_guardian: load_outline_scenes failed sim={simulation_id}: {e}"
        )
        return ""
    if not scenes:
        return ""

    lines: list[str] = [f"  共 {len(scenes)} 幕 outline。每幕的 key_events 必须在 narrative 中显性完成:"]
    # 取前 30 幕的 key_events(更长的 outline 截断,避免 prompt 爆 context)
    for sc in scenes[:30]:
        ev_count = len(sc.key_events)
        if ev_count == 0:
            continue
        # 每幕概要 + 关键事件清单
        lines.append(
            f"\n  ─ 第 {sc.scene_index + 1} 幕 @ {sc.location} / {sc.time_anchor or '(无时间锚)'}"
        )
        if sc.scene_summary:
            lines.append(f"    概要: {sc.scene_summary[:100]}")
        for i, ev in enumerate(sc.key_events):
            lines.append(f"    - key_event #{i + 1}: {ev[:120]}")
    if len(scenes) > 30:
        lines.append(f"\n  ... (后 {len(scenes) - 30} 幕 outline 截断,审计依前 30 幕为准)")
    return "\n".join(lines)


def _render_information_boundary_block(
    conn: sqlite3.Connection, project_id: str, simulation_id: str,
) -> str:
    """SP-3.1(2026-06-02)— 信息边界审计输入.

    给 LLM 提供"该项目每个角色已知 fact 清单"(character_knowledge + story_facts 表).
    LLM 据此判定 narrative 中是否有角色用了不该知道的信息(没在场/未告知/已被排除).

    数据来源:
      - story_facts:项目级事实全集
      - character_knowledge:每个角色 N:M 关联到 facts,含 confidence(known/suspected/unknown)
        + introduced_scene_index 时间锚
    若项目无 story_facts(用户未录入/未推断)→ 返空,LLM 跳过此维度.
    """
    try:
        from app.db import fetch_all as _fetch_all
        # 拉项目所有 facts
        fact_rows = _fetch_all(
            conn,
            "SELECT id, fact_text FROM story_facts "
            "WHERE project_id=? ORDER BY created_at ASC LIMIT 50",
            (project_id,),
        )
        if not fact_rows:
            return ""
        # 拉所有 character + 各 character 已知 facts(confidence='known')
        char_rows = _fetch_all(
            conn,
            "SELECT id, name FROM characters WHERE project_id=? ORDER BY name ASC",
            (project_id,),
        )
        if not char_rows:
            return ""
        knowledge_rows = _fetch_all(
            conn,
            "SELECT character_id, fact_id, confidence, introduced_scene_index "
            "FROM character_knowledge "
            "WHERE character_id IN (SELECT id FROM characters WHERE project_id=?) "
            "ORDER BY introduced_scene_index ASC",
            (project_id,),
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).debug(
            f"_render_information_boundary_block failed project={project_id}: {e}"
        )
        return ""

    # 索引化:fact_id → fact_text;character_id → name
    fact_text_by_id = {r["id"]: r["fact_text"] for r in fact_rows}
    char_name_by_id = {r["id"]: r["name"] for r in char_rows}
    # 聚合:char_id → list[(fact_id, confidence, scene_index)]
    by_char: dict[str, list[tuple[str, str, int]]] = {}
    for kr in knowledge_rows:
        cid = kr["character_id"]
        by_char.setdefault(cid, []).append(
            (kr["fact_id"], kr["confidence"] or "known", kr["introduced_scene_index"] or 0)
        )

    lines = ["  共 {} 个角色 / {} 条事实.每个角色'截止某幕'已知的事实:".format(
        len(char_rows), len(fact_rows),
    )]
    for cid, cname in char_name_by_id.items():
        known = by_char.get(cid, [])
        if not known:
            lines.append(f"\n  ─ 【{cname}】(未录入任何已知事实,审计该角色时只看 '能否合理推断')")
            continue
        lines.append(f"\n  ─ 【{cname}】已知 {len(known)} 条事实:")
        for fact_id, conf, scene_idx in known[:15]:  # 单角色截 15 条
            ftext = (fact_text_by_id.get(fact_id) or "")[:80]
            conf_tag = {
                "known": "确知",
                "suspected": "疑信",
                "unknown": "误信",
            }.get(conf, conf)
            lines.append(f"    · 第 {scene_idx + 1} 幕起[{conf_tag}]: {ftext}")
        if len(known) > 15:
            lines.append(f"    ... (另 {len(known) - 15} 条略)")
    return "\n".join(lines)


def _render_story_core_block_for_audit(
    conn: sqlite3.Connection, project_id: str,
) -> str:
    """SP-1 终审(2026-06-02)— 故事内核坚守度审计输入.

    给 LLM 项目级 SP-1 三件套:core_dramatic_question / theme / ending_direction.
    LLM 据此判 narrative 是否偏离这三个北极星(核心戏剧问题被早早闭合 / 主题漂走 / 终点偏向).
    项目未填三件套 → 返空,LLM 跳过此维度.
    """
    try:
        from app.db import fetch_one as _fetch_one
        row = _fetch_one(
            conn,
            "SELECT core_dramatic_question, theme, ending_direction FROM projects WHERE id=?",
            (project_id,),
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).debug(
            f"_render_story_core_block_for_audit failed project={project_id}: {e}"
        )
        return ""
    if not row:
        return ""
    cdq = (row["core_dramatic_question"] or "").strip()
    theme = (row["theme"] or "").strip()
    ending = (row["ending_direction"] or "").strip()
    if not (cdq or theme or ending):
        return ""

    lines = ["  项目级故事内核三件套(SP-1,LLM 必须坚守):"]
    if cdq:
        lines.append(f"  - 核心戏剧问题: {cdq}")
    if theme:
        lines.append(f"  - 主题: {theme}")
    if ending:
        lines.append(f"  - 终点情绪 / 走向: {ending}")
    lines.append("\n  审计要点:")
    lines.append("    · 核心戏剧问题被**提前闭合 / 草率回答 / 完全忽略** → severe_breach")
    lines.append("    · 主题被偷换 / 渐次漂走 → obvious_drift")
    lines.append("    · 终点情绪与原作约定相反(给希望 vs 给悲剧 / 收束 vs 发散)→ severe_breach")
    return "\n".join(lines)


def _build_user_prompt(
    work_name: str,
    world_baseline_block: str,
    character_baseline_block: str,
    relationship_baseline_block: str,
    event_baseline_block: str,
    counterfactuals_block: str,
    sequel_new_entities_block: str,
    body_register_block: str,
    outline_execution_block: str,
    narrative_text: str,
    information_boundary_block: str = "",
    story_core_block: str = "",
) -> str:
    """对齐 prompts/canonical_guardian.md 的 user prompt 模板(12 维度)。

    M7.A(2026-05-20):新增 sequel_new_entities_block — 续作新实体白名单,治"新角色被误判"。
    B5.3(2026-05-27):新增 body_register_block — 第 9 维 身体描写尺度对齐输入。
    P5.2(2026-05-27):新增 outline_execution_block — 第 10 维 outline 执行率审计输入。
    SP-3.1(2026-06-02):新增 information_boundary_block — 第 11 维 角色信息边界审计输入.
    SP-1 终审(2026-06-02):新增 story_core_block — 第 12 维 故事内核坚守度审计输入.
    """
    # narrative 过长会爆 context;LLM 64K 容易撑爆
    # 经验:留 3 万字给 narrative + 8K baseline + 8K 输出 ≈ 50K token
    narrative_truncated = narrative_text[:30000]
    if len(narrative_text) > 30000:
        narrative_truncated += "\n\n[...产物过长,后段已截断;审计依前段为准...]"

    return (
        "请审计以下推演产物对原作正典的偏离情况(**10 维度**,反事实豁免 + 续作创新豁免)。\n\n"
        f"## 原作作品名\n{work_name}\n\n"
        f"## 原作 canon baseline\n\n"
        f"### 体裁 / 设定 / 时间轴 / 基调(world baseline)\n{world_baseline_block}\n\n"
        f"### 关键角色(name | identity | personality | 生命状态)\n"
        f"{character_baseline_block}\n"
        f"  ⚠ **生命状态铁律**(2026-05-27):标注「⚠️ 已死」「🏥 在特定地点」「🚶 暂离」「❓ 状态未知」的角色不在 alive 默认态。\n"
        f"  - 已死(deceased):本幕**不许出现互动 / 说话 / 行动**;允许尸体场景 / 葬礼 / 他人回忆。\n"
        f"  - 在特定地点(in_facility):该角色固定在某地,主角不到该地不应同框。\n"
        f"  - 暂离(absent):类似 in_facility 但软铁律。\n"
        f"  违反 = severe_breach(character_consistency 维度)。\n\n"
        f"### 关键关系\n{relationship_baseline_block}\n\n"
        f"### 关键事件\n{event_baseline_block}\n\n"
        f"## 用户设的反事实变量(★ 这些豁免,不算违规)\n{counterfactuals_block}\n\n"
        f"## 本次续作中新引入的实体(★ 这些是合法续作新血液,不算违规;铁律 3)\n{sequel_new_entities_block}\n\n"
        + (
            f"## 原作身体描写尺度基线(★ 第 9 维 body_register_alignment 审计输入)\n"
            f"{body_register_block}\n\n"
            if body_register_block
            else "## 原作身体描写尺度基线\n  (用户未生成 author_compass — 第 9 维度跳过,出 1 条 strict_canonical \"基线缺失,跳过审计\")\n\n"
        )
        + (
            f"## outline key_events 清单(★ 第 10 维 outline_execution 审计输入)\n"
            f"{outline_execution_block}\n\n"
            if outline_execution_block
            else "## outline key_events 清单\n  (该 sim 未走 outline-first 模式 — 第 10 维度跳过,出 1 条 strict_canonical \"无 outline,跳过审计\")\n\n"
        )
        + (
            f"## 角色信息边界(★ 第 11 维 information_boundary 审计输入,SP-3.1 / 2026-06-02)\n"
            f"{information_boundary_block}\n\n"
            f"  审计要点:\n"
            f"  - 角色用了 narrator 上帝视角的信息(没在场目睹 / 没被告知 / 已标 unknown)→ severe_breach\n"
            f"  - 角色用了'确知'层级以外的信息(suspected 当确定来用)→ obvious_drift\n"
            f"  - 续作合法新角色 / 新事件不在表中,不算违规(豁免)\n\n"
            if information_boundary_block
            else "## 角色信息边界\n  (项目未录入 story_facts — 第 11 维度跳过,出 1 条 strict_canonical \"基线缺失,跳过审计\")\n\n"
        )
        + (
            f"## 故事内核三件套(★ 第 12 维 story_core_adherence 审计输入,SP-1 / 2026-06-02)\n"
            f"{story_core_block}\n\n"
            if story_core_block
            else "## 故事内核三件套\n  (项目未填 SP-1 三件套 — 第 12 维度跳过,出 1 条 strict_canonical \"基线缺失,跳过审计\")\n\n"
        )
        + f"## 推演产物 — 完整 narrative\n\"\"\"\n{narrative_truncated}\n\"\"\"\n\n"
        "请按 system prompt 的 schema 严格输出 JSON。"
    )


# ======================================================================
# LLM 响应校验 + 兜底
# ======================================================================

def _validate_response(parsed: Any) -> list[dict]:
    """从 LLM 输出取 issues 列表 + 清洗 + 维度兜底。

    - 顶层非 dict → []
    - issues 缺失 / 非 list → []
    - 每个 issue 缺 dimension / 维度非法 → drop
    - severity 非法 → 默认 minor_drift
    - counterfactual_exempt 缺 → false
    """
    if not isinstance(parsed, dict):
        return []
    raw_issues = parsed.get("issues")
    if not isinstance(raw_issues, list):
        return []

    cleaned: list[dict] = []
    for item in raw_issues:
        if not isinstance(item, dict):
            continue
        dim = item.get("dimension")
        if dim not in VALID_DIMENSIONS:
            continue
        sev = item.get("severity")
        if sev not in VALID_SEVERITIES:
            sev = "minor_drift"
        finding = str(item.get("finding") or "")[:300]
        evidence = str(item.get("evidence_excerpt") or "")[:300]
        canon_ref = str(item.get("canon_reference") or "")[:200]
        exempt = bool(item.get("counterfactual_exempt", False))
        exempt_reason = item.get("exempt_reason")
        if exempt_reason is not None:
            exempt_reason = str(exempt_reason)[:200]
        cleaned.append({
            "dimension": dim,
            "severity": sev,
            "finding": finding,
            "evidence_excerpt": evidence,
            "canon_reference": canon_ref,
            "counterfactual_exempt": exempt,
            "exempt_reason": exempt_reason,
        })
    return cleaned


# ======================================================================
# 查询接口
# ======================================================================

def get_canonical_audit_or_404(
    conn: sqlite3.Connection, audit_id: str, user_id: str,
) -> CanonicalAudit:
    row = fetch_one(
        conn, "SELECT * FROM canonical_audits WHERE id=?", (audit_id,),
    )
    if not row:
        raise CanonicalAuditNotFoundOrForbidden(f"audit {audit_id} 不存在")
    audit = CanonicalAudit.from_row(row)
    if audit.user_id != user_id:
        raise CanonicalAuditNotFoundOrForbidden(f"audit {audit_id} 不属于你")
    return audit


def get_latest_for_simulation(
    conn: sqlite3.Connection, sim_id: str, user_id: str,
) -> Optional[CanonicalAudit]:
    """该 sim 最新一条 audit(给前端 polling /latest 用)。

    先鉴权 sim 属于 user;无 audit 返 None(router 转 404)。
    """
    get_simulation_or_404(conn, sim_id, user_id)
    row = fetch_one(
        conn,
        # ROWID DESC 作 tiebreak — iso_now() 是秒精度,紧挨着插入会同时间,
        # 必须用 SQLite 隐式 rowid 保证"后插入的算最新"(对齐 1.R audit_service)
        "SELECT * FROM canonical_audits WHERE simulation_id=? "
        "ORDER BY created_at DESC, ROWID DESC LIMIT 1",
        (sim_id,),
    )
    return CanonicalAudit.from_row(row) if row else None


def list_for_simulation(
    conn: sqlite3.Connection, sim_id: str, user_id: str,
) -> list[CanonicalAudit]:
    """历史 audits(按 created_at DESC + ROWID DESC tiebreak)。"""
    get_simulation_or_404(conn, sim_id, user_id)
    rows = fetch_all(
        conn,
        "SELECT * FROM canonical_audits WHERE simulation_id=? "
        "ORDER BY created_at DESC, ROWID DESC",
        (sim_id,),
    )
    return [CanonicalAudit.from_row(r) for r in rows]


__all__ = [
    "CanonicalAuditNotApplicable",
    "SimulationNotAuditable",
    "CanonicalAuditNotFoundOrForbidden",
    "trigger_canonical_audit",
    "run_canonical_audit",
    "kick_off_canonical_audit",
    "get_canonical_audit_or_404",
    "get_latest_for_simulation",
    "list_for_simulation",
]
