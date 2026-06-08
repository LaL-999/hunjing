"""Sprint 6.A2 M3.B(2026-05-18)— 灵魂续写主循环 (Agent Evolution Engine)。

用户拍板铁律(2026-05-18):
  "以最高标准开发项目,不要遇难则退,不管多复杂都要做"
  → mode='evolution' 走真多 agent 独立 LLM 进程 + 私有记忆 + reflection + 多轮自由对话 + narrator 合稿。

数据流(单幕):
  1. scene_picker LLM       决定本幕场景 + 时间锚点(M2 数据 + 多样性铁律)
  2. agent_summoner 本地    用 M2 character_affinity 召唤 2-3 个最可能在场的 agent
  3. dialogue 多轮 LLM × N  每 agent 拉 load_agent_context(私有记忆) → 产出 reflection/dialogue/action
     → 落 record_memory + share_witnessed_memory 给在场其他人(严格信息不对称)
  4. narrator LLM           把本幕所有 reflection/dialogue/action → 200-400 字小说段落
                            → 落 simulation_scenes.narrative_segment + 累加 simulations.narrative

主循环 N 幕(由 sim.rounds_planned 派生为幕数;典型 8-15 幕)。

成本(典型 12 幕):
  scene_picker × 12  ≈  12 LLM
  dialogue × N agents × 3 轮 × 12 幕 = ~100 LLM
  narrator × 12 = 12 LLM
  → 总 ~120 LLM 调用,DeepSeek V3 ~¥5-8 / 推演,400-800 credits

主路径 vs 老 simulation_service:
  - 老路径(mode='quick'):simulation_service._run_simulation_inner_*
  - 新路径(mode='evolution'):本模块 run_evolution_simulation()
  - simulation_service.run_simulation 入口按 sim.mode 路由
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from app.db import execute, fetch_all, fetch_one, get_connection
from app.models.agent_private_memory import AgentPrivateMemory
from app.models.character import Character
from app.models.simulation import Simulation
from app.services.agent_runtime import (
    AgentContext,
    load_agent_context,
    record_memory,
    share_witnessed_memory,
)
from app.services.character_affinity import (
    SceneRegular,
    compute_scene_regulars,
)
from app.services.llm_client import call_llm_json, estimate_cost_yuan
from app.services.project_service import iso_now
from app.services.rag_retrieval import ChunkSnippet, retrieve_relevant_chunks
from app.services.scene_extractor import list_scenes_for_project

logger = logging.getLogger(__name__)


# ============================================================
# 配置
# ============================================================

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"

# 每幕的对话轮数(每 agent 轮流说话 N 圈)
DIALOGUE_ROUNDS_PER_SCENE = 3
# 每幕召唤的 agent 数(主角优先,加配角)
AGENTS_PER_SCENE_MIN = 2
AGENTS_PER_SCENE_MAX = 3
# scene_picker 看的可选场景上限(节省 token)
SCENE_PICKER_AVAILABLE_TOP_N = 12
# narrative_so_far 摘要长度上限(给 scene_picker)
# hotfix(2026-06-01):用户反馈 800 字只看末尾 1-2 段 → 干预指令时 AI 看不见全文走向 →
# 容易出现"按 hint 突变但与早期人物建设/主题割裂".扩到 2400(~ 6-8 幕的尾段内容).
NARRATIVE_SUMMARY_MAX_CHARS = 2400
# hotfix(2026-06-01):新增幕级摘要 — 让 scene_picker 看到"全文走过了哪些场景 + 关键节点"
# 治"AI 只看 800 字尾段,前 15 幕全丢失"问题.每幕取头 80 字 + 末 50 字.
PRIOR_SCENES_BRIEF_PER_SCENE_HEAD = 80
PRIOR_SCENES_BRIEF_PER_SCENE_TAIL = 50
PRIOR_SCENES_BRIEF_MAX_SCENES = 20
# 每 agent 私有记忆喂 LLM 的上限(防 context 爆)
AGENT_MEMORY_PER_CALL_LIMIT = 30
# scene_picker 多样性铁律:不连续 N 幕同场景 → 看最近多少幕
RECENT_SCENES_LOOKBACK = 2
# narrator 前序衔接看上一幕段落末尾多少字
PREVIOUS_SEGMENT_TAIL_CHARS = 100
# Sprint 6.A2 M3.C(2026-05-18):RAG 召回每幕拉几条原文 chunk
RAG_TOP_K_PER_SCENE = 3
RAG_SNIPPET_MAX_CHARS = 600

# Sprint 6.A2 M3.D-fix1(2026-05-18):字数控制
# 主循环 break 阈值 = target_chars × THRESHOLD_RATIO 时停
TARGET_REACHED_RATIO = 0.95
# 主循环硬上限(防 LLM 失控 / RAG 错配生成无穷场景)
MAX_SCENES_HARD_CAP = 30
# 主循环硬最小幕数(防 target_chars 太小导致 1 幕就结束)
MIN_SCENES = 3


# ============================================================
# Sprint 6.A2 路线图 #5(2026-05-23):用户边写边干预 hint 处理
# ============================================================

def _consume_pending_hint(
    conn: sqlite3.Connection, sim_id: str,
) -> Optional[str]:
    """读取 simulations.pending_scene_hint 并立即清空(消耗式)。

    并发安全:SELECT + UPDATE 同一 transaction 内,避免"读到 hint 后清空之前用户又写一条
    新 hint → 后端把新 hint 也覆盖掉"。SQLite 默认串行写,本函数也只在续写主循环内调用
    (sim 主循环串行),不会有多读者并发。

    返回:
      - 非空字符串:用户提交了 hint(已清空字段,后续幕拿不到)
      - None:无 hint(字段本来就 NULL,或上次已消费)
    """
    row = fetch_one(
        conn,
        "SELECT pending_scene_hint FROM simulations WHERE id=?",
        (sim_id,),
    )
    if row is None:
        return None
    hint_raw = row["pending_scene_hint"]
    if not hint_raw or not str(hint_raw).strip():
        return None
    # 立即清空(消费式 — 只影响当前幕)
    execute(
        conn,
        "UPDATE simulations SET pending_scene_hint=NULL WHERE id=?",
        (sim_id,),
    )
    conn.commit()
    return str(hint_raw).strip()


def _build_user_hint_block(hint: Optional[str]) -> str:
    """把 hint 包装成 prompt append 块。空 hint → 返空字符串(不动 prompt)。

    采用统一格式让 LLM 在 scene_picker / narrator 不同语境下都能识别"这是用户即时干预,优先级最高"。
    """
    if not hint:
        return ""
    return (
        "\n\n## 用户即时干预(本幕,最高优先级)\n\n"
        f"用户在续写过程中要求:{hint}\n\n"
        "本幕生成时**必须**把这条要求纳入考虑,与上述其他要求冲突时以本条为准。\n"
        "(此 hint 仅影响当前这一幕,后续幕不再生效。)\n"
    )


# ============================================================
# 数据类
# ============================================================

@dataclass
class SceneDecision:
    """scene_picker LLM 的输出。"""
    scene_name: str
    scene_source: str    # 'project_scenes_pick' | 'llm_created'
    time_anchor: str
    reasoning: str
    # M11.E(2026-05-24):场景过渡类型 — 治"渔夫突然冒出""葬礼刚结束"类无锚点瞬移
    #   physical_move  物理移动(渡边走出咖喱餐厅,搭电车到大冢)— 默认值
    #   time_jump      时间跳跃(三天后 / 一周后)— 必须有时间过渡句
    #   memory         记忆 / 回忆(想起 / 闪回原作场景)— 必须有"想起"类引导
    #   hallucination  幻觉 / 想象(主角想象绿子家)— 必须有"想象 / 仿佛"标记
    #   natural_flow   自然延续(同场景同时间,如对话继续)— 无需过渡句
    transition_type: str = "physical_move"


@dataclass
class AgentRoundOutput:
    """每 agent 每轮 dialogue LLM 的输出。"""
    speaker_id: str
    speaker_name: str
    reflection: str
    dialogue: str
    action: str


# ============================================================
# 辅助:emit 事件 + 累计 token
# ============================================================

def _emit(sim_id: str, event: dict) -> None:
    """转发到 simulation_service._emit_event(单一事件总线)。"""
    from app.services.simulation_service import _emit_event
    _emit_event(sim_id, event)


def _update_sim_state(
    conn: sqlite3.Connection, sim_id: str, **fields,
) -> None:
    """同 simulation_service._update_state 简化版,给 evolution 专用。"""
    from app.services.simulation_service import _update_state
    _update_state(conn, sim_id, **fields)


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


# ============================================================
# Step 1: scene_picker
# ============================================================

def _pick_scene(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene_index: int,
    total_planned: int,
    narrative_so_far: str,
    recent_scenes: list[str],
    user_scene_hint: Optional[str] = None,
) -> tuple[SceneDecision, dict]:
    """调 LLM 决定本幕场景。返回 (decision, usage)。

    Sprint 6.A2 M4.1(2026-05-19):新增 world_facts + plot_threads 注入,
    治 Gemini 评测瑕疵 1(剧情死循环)+ 瑕疵 2(时间线悖论)+ 瑕疵 5(反派规则覆写)。
    """
    # 拉项目可选场景(M2 数据)
    all_scenes = list_scenes_for_project(conn, sim.project_id)
    available_scenes = [
        {
            "name": s.name,
            "aliases": s.aliases,
            "description": s.description,
            "appearance_chunk_count": s.appearance_chunk_count,
        }
        for s in all_scenes[:SCENE_PICKER_AVAILABLE_TOP_N]
    ]

    # 摘要 narrative_so_far(取最后 N 字)
    if narrative_so_far and len(narrative_so_far) > NARRATIVE_SUMMARY_MAX_CHARS:
        narrative_brief = "..." + narrative_so_far[-NARRATIVE_SUMMARY_MAX_CHARS:]
    else:
        narrative_brief = narrative_so_far or ""

    # M4.1:拉 world_facts(ACTIVE + LOCKED)+ plot_threads(未解决)
    # 序列化为 prompt 友好的单行表示
    from app.services.world_state_extractor import list_active_facts
    from app.services.plot_tracker import list_active_threads

    world_facts_lines = [
        f.to_prompt_line() for f in list_active_facts(conn, sim.id)
    ]
    # 2026-06-02 cleanup:list_active_threads 不再接受 current_scene_index 参数
    # (F1.2 删除 — "预期回收幕"由 LLM 导演自由发挥,用户不应越权)
    plot_threads_lines = [
        t.to_prompt_line() for t in list_active_threads(conn, sim.id)
    ]

    # ⭐ M11.B(2026-05-24):scene_picker 看到"已访问场景的最后状态摘要",治"小林书店多状态"
    # 续作里同一个场景跨幕重访时(小林书店第 3 幕关闭+父亲过世,第 10 幕开门做咖喱),
    # scene_picker 之前不知道上次该场景结束时的状态,LLM 重选时会"重启"该场景状态。
    # 这里查每个已访问场景的最后一幕 narrative_segment 末 150 字,注入 user_input。
    # 选已访问场景时,LLM 必须承接或显式过渡,杜绝"瞬间复活"。
    previously_visited_rows = fetch_all(
        conn,
        """SELECT scene_name, scene_index, narrative_segment FROM simulation_scenes
           WHERE simulation_id = ? AND scene_index < ?
           ORDER BY scene_index DESC""",
        (sim.id, scene_index),
    )
    # 按 scene_name 去重,保留最新一次出现
    _seen_scene_names: set[str] = set()
    previously_visited_scenes_info: list[dict] = []
    for row in previously_visited_rows:
        sn = row["scene_name"]
        if sn in _seen_scene_names:
            continue
        _seen_scene_names.add(sn)
        tail = (row["narrative_segment"] or "")[-150:].strip()
        previously_visited_scenes_info.append({
            "scene_name": sn,
            "last_scene_index": int(row["scene_index"]),
            "last_state_tail": tail,
        })
        if len(previously_visited_scenes_info) >= 8:  # 最多 8 个,防 prompt 过长
            break

    # ⭐ 提案 A(2026-05-24):scene_picker 看到"主角过去 N 幕动作清单",治 LLM 鬼打墙
    # 续作中主角连续 5 段"摸口袋纸条 + 攥紧又松开 + 后背抵墙"循环,scene_picker
    # 之前只看 recent_scenes(前 2 幕地点)和 plot_threads,看不见"主角已在循环
    # 动作"信号,所以选第 6 幕还是"自己房间"。这里把主角最近 5 幕的原子动作摘要
    # 注入 user_input,scene_picker prompt 会消费此字段做"换场景打破循环"判断。
    from app.services.action_extractor import list_recent_actor_actions
    proto_rows = fetch_all(
        conn,
        "SELECT name FROM characters WHERE project_id=? AND is_protagonist=1",
        (sim.project_id,),
    )
    proto_names = [r["name"] for r in proto_rows]
    recent_proto_actions = list_recent_actor_actions(
        conn, sim.id, proto_names,
        since_scene_index=max(0, scene_index - 5),
        limit=20,
    )
    recent_protagonist_actions_lines = [
        a.to_prompt_line() for a in recent_proto_actions
    ]

    # hotfix(2026-06-01):幕级剧情摘要 — 让 scene_picker 看到全文走向,治"AI 只看
    # 800 字尾段就决策下一幕"问题.每幕取头/末几十字,最多 20 幕,够覆盖全篇主线.
    prior_scenes_rows = fetch_all(
        conn,
        """SELECT scene_index, scene_name, narrative_segment FROM simulation_scenes
           WHERE simulation_id=? AND scene_index < ?
           ORDER BY scene_index ASC""",
        (sim.id, scene_index),
    )
    prior_scenes_brief: list[dict] = []
    if prior_scenes_rows:
        # 早期幕全留 + 末尾近期幕全留;中段密集时按 MAX_SCENES 均匀采样
        rows_list = list(prior_scenes_rows)
        if len(rows_list) > PRIOR_SCENES_BRIEF_MAX_SCENES:
            # 保留前 5 幕 + 后 10 幕 + 中间均匀采 5 幕
            head_n = 5
            tail_n = 10
            mid_n = PRIOR_SCENES_BRIEF_MAX_SCENES - head_n - tail_n
            head_part = rows_list[:head_n]
            tail_part = rows_list[-tail_n:]
            mid_zone = rows_list[head_n:-tail_n] if len(rows_list) > head_n + tail_n else []
            if mid_zone and mid_n > 0:
                step = max(1, len(mid_zone) // mid_n)
                mid_part = mid_zone[::step][:mid_n]
            else:
                mid_part = []
            rows_list = head_part + mid_part + tail_part
        for r in rows_list:
            seg = r["narrative_segment"] or ""
            head_chunk = seg[:PRIOR_SCENES_BRIEF_PER_SCENE_HEAD].strip()
            tail_chunk = seg[-PRIOR_SCENES_BRIEF_PER_SCENE_TAIL:].strip() if len(seg) > PRIOR_SCENES_BRIEF_PER_SCENE_HEAD + PRIOR_SCENES_BRIEF_PER_SCENE_TAIL else ""
            prior_scenes_brief.append({
                "scene_index": int(r["scene_index"]),
                "scene_name": r["scene_name"],
                "head_excerpt": head_chunk,
                "tail_excerpt": tail_chunk,
            })

    user_input = {
        "scene_index": scene_index,
        "total_planned_scenes": total_planned,
        "divergence": sim.divergence,
        "original_tail_excerpt": sim.original_tail_excerpt or "",
        "narrative_so_far": narrative_brief,
        "available_scenes": available_scenes,
        "recent_scenes": recent_scenes,
        # M4.1 新增字段(scene_picker prompt 中铁律消费)
        "world_facts": world_facts_lines,
        "plot_threads": plot_threads_lines,
        # 提案 A 新增字段(主角过去 5 幕动作流水,治"鬼打墙")
        "recent_protagonist_actions": recent_protagonist_actions_lines,
        # M11.B 新增字段(已访问场景的最后状态,治"小林书店多状态错乱")
        "previously_visited_scenes": previously_visited_scenes_info,
        # hotfix(2026-06-01):全文幕级摘要(治"AI 看不到全文走向就被 hint 牵着走")
        "prior_scenes_brief": prior_scenes_brief,
    }

    # ===== Sprint 6.A2 M5.3(2026-05-20)生成前硬铁律 prepend =====
    # 实体身份 / 时间链 / 角色情绪 prepend 到 system_prompt 顶部
    # scene_picker 只读"实体+时间",不读"动作+情绪"(场景选择阶段不必约束这些)
    from app.services.hard_constraints import (
        build_hard_constraints_block, prepend_constraints_to_prompt,
    )
    base_prompt = _load_prompt("m3_scene_picker.md")
    # scene_picker 阶段 agents 还没召唤 → 传空 list,跳过情绪与动作约束
    constraints_block = build_hard_constraints_block(
        conn, sim, scene_index, agents=[],
        skip_actions=True, skip_emotions=True,
    )
    system_prompt = prepend_constraints_to_prompt(base_prompt, constraints_block)
    # #5(2026-05-23):用户即时干预 hint(若有) — append 到 system prompt 末尾,最高优先级
    system_prompt = system_prompt + _build_user_hint_block(user_scene_hint)
    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input,
            max_tokens=400, temperature=0.6,
        )
    except Exception as e:  # noqa: BLE001
        # LLM 失败 → 降级:从 available_scenes 选(优先不在 recent 的;若 recent 饱满全是 available
        # 子集则降级到 recent[0],避免"未命名场景"占位)
        # B2-A(2026-05-27):原实现 recent 饱满时返"未命名场景",narrator 拿到这个无法定位场景;
        # 改为:优先未用过 → 其次 available[0](最常用场景)→ 兜底"未命名场景"
        logger.warning(f"scene_picker LLM failed scene {scene_index}: {e}")
        fallback_name = ""
        fallback_source = "llm_created"
        # 1. 优先找不在 recent 的可用场景
        for s in available_scenes:
            if s["name"] not in recent_scenes:
                fallback_name = s["name"]
                fallback_source = "project_scenes_pick"
                break
        # 2. recent 饱满 → 用 available[0](允许重复出场,比"未命名"好)
        if not fallback_name and available_scenes:
            fallback_name = available_scenes[0]["name"]
            fallback_source = "project_scenes_pick"
            logger.info(
                f"scene_picker fallback: recent 饱满,降级用 available[0]={fallback_name!r}"
            )
        # 3. 极端兜底
        if not fallback_name:
            fallback_name = "未命名场景"
        return SceneDecision(
            scene_name=fallback_name,
            scene_source=fallback_source,
            time_anchor="",
            reasoning=f"LLM 失败降级:{type(e).__name__}",
            transition_type="physical_move",  # 降级走默认
        ), {"input_tokens": 0, "output_tokens": 0}

    if not isinstance(parsed, dict):
        parsed = {}
    scene_name = str(parsed.get("scene_name") or "").strip()[:30]
    scene_source = parsed.get("scene_source") or "llm_created"
    if scene_source not in ("project_scenes_pick", "llm_created"):
        scene_source = "llm_created"
    if not scene_name:
        scene_name = "未命名场景"

    # 多样性铁律守护(后端硬兜底,即便 LLM 违反 prompt 也校正)
    # 若 recent_scenes 含 2 个相同名 且 LLM 又选这个 → 强制改为可用列表中第一个不在黑名单的
    if recent_scenes.count(scene_name) >= 2:
        for s in available_scenes:
            if s["name"] not in recent_scenes:
                scene_name = s["name"]
                scene_source = "project_scenes_pick"
                break

    # M11.E:解析 transition_type,LLM 没给/给非法值 → 默认 physical_move
    valid_transitions = {
        "physical_move", "time_jump", "memory", "hallucination", "natural_flow",
    }
    transition_type = str(parsed.get("transition_type") or "physical_move")
    if transition_type not in valid_transitions:
        transition_type = "physical_move"

    return SceneDecision(
        scene_name=scene_name,
        scene_source=scene_source,
        time_anchor=str(parsed.get("time_anchor") or "")[:50],
        reasoning=str(parsed.get("reasoning") or "")[:120],
        transition_type=transition_type,
    ), usage


# ============================================================
# Step 2: agent_summoner (本地算,无 LLM)
# ============================================================

def _scan_hint_for_character_mentions(
    conn: sqlite3.Connection,
    project_id: str,
    user_scene_hint: str,
) -> list[str]:
    """hotfix(2026-06-01):扫 hint 文本找用户提到的角色名/别名 → 返回命中的 character_id 列表.

    例:hint='让张静怡出场' + 项目有 character name='张静怡' → 返该 character_id.
    - 精确匹配 name 或 aliases_json 中任一项
    - 命中多个 → 全返(主循环 summoner 会按 AGENTS_PER_SCENE_MAX 取前 N)
    - hint 空 / 无命中 → 返空 list,主循环走原 affinity 路径
    """
    if not user_scene_hint or not user_scene_hint.strip():
        return []
    hint = user_scene_hint.strip()
    rows = fetch_all(
        conn,
        "SELECT id, name, aliases_json FROM characters WHERE project_id=?",
        (project_id,),
    )
    hits: list[str] = []
    for r in rows:
        name = r["name"]
        if name and name in hint:
            hits.append(r["id"])
            continue
        # 别名
        try:
            aliases = json.loads(r["aliases_json"] or "[]")
        except (json.JSONDecodeError, TypeError):
            aliases = []
        if any(isinstance(a, str) and a and a in hint for a in aliases):
            hits.append(r["id"])
    return hits


def _summon_agents(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene: SceneDecision,
    user_scene_hint: Optional[str] = None,
) -> list[Character]:
    """用 M2 character_affinity.compute_scene_regulars + 主角优先,召唤 2-3 个 agent。

    策略:
      - hotfix(2026-06-01):**user_scene_hint 中点名的角色优先**(治"hint 让 X 出场但 summoner 没召唤 X"盲点)
      - 拉 scene 的 regulars(top 10)
      - 主角优先;若主角数 ≥ AGENTS_PER_SCENE_MIN → 全选主角
      - 主角数不够 → 用配角补齐(按 affinity 高的)
      - 上限 AGENTS_PER_SCENE_MAX
    """
    # hotfix(2026-06-01):先扫 hint 命中的角色 — 用户点名 = 强意图,优先级最高
    hint_mentioned_ids = _scan_hint_for_character_mentions(
        conn, sim.project_id, user_scene_hint or "",
    )

    # M2 给 scene 找常客
    regulars = compute_scene_regulars(
        conn, sim.project_id, scene.scene_name, top_n=10,
    )

    # LLM_created 场景 regulars 可能空 → fallback:从 project 主角随机挑 2
    if not regulars:
        char_rows = fetch_all(
            conn,
            """SELECT * FROM characters
               WHERE project_id=?
               ORDER BY is_protagonist DESC, protagonist_score DESC
               LIMIT ?""",
            (sim.project_id, AGENTS_PER_SCENE_MAX),
        )
        fallback_list = [Character.from_row(r) for r in char_rows]
        # 把 hint 点名的角色塞前面(若主角已包含则去重)
        if hint_mentioned_ids:
            fallback_ids = {c.id for c in fallback_list}
            need_add = [cid for cid in hint_mentioned_ids if cid not in fallback_ids]
            if need_add:
                add_rows = fetch_all(
                    conn,
                    f"SELECT * FROM characters WHERE id IN ({','.join('?' * len(need_add))})",
                    tuple(need_add),
                )
                added = [Character.from_row(r) for r in add_rows]
                # 主角第 0,hint 角色第 1,其它配角往后挤 — 仍尊重 MAX
                proto = [c for c in fallback_list if c.is_protagonist]
                non_proto = [c for c in fallback_list if not c.is_protagonist]
                merged = proto + added + non_proto
                return merged[:AGENTS_PER_SCENE_MAX]
        return fallback_list

    # 主角优先 + 配角补齐
    selected_ids: list[str] = []
    for r in regulars:
        if r.is_protagonist:
            selected_ids.append(r.character_id)
        if len(selected_ids) >= AGENTS_PER_SCENE_MAX:
            break

    # hotfix(2026-06-01):hint 点名的角色挤进来(放在主角后,把最后一个挤出去)
    # 优先级:主角 > hint 点名 > affinity 高配角
    if hint_mentioned_ids:
        for hcid in hint_mentioned_ids:
            if hcid in selected_ids:
                continue
            if len(selected_ids) < AGENTS_PER_SCENE_MAX:
                selected_ids.append(hcid)
            else:
                # 用 hint 角色替换最后一个非主角配角(主角永远保留)
                for i in range(len(selected_ids) - 1, -1, -1):
                    cid_to_check = selected_ids[i]
                    is_proto = any(
                        r.character_id == cid_to_check and r.is_protagonist
                        for r in regulars
                    )
                    if not is_proto:
                        selected_ids[i] = hcid
                        break

    if len(selected_ids) < AGENTS_PER_SCENE_MIN:
        for r in regulars:
            if r.character_id not in selected_ids:
                selected_ids.append(r.character_id)
            if len(selected_ids) >= AGENTS_PER_SCENE_MIN:
                break

    selected_ids = selected_ids[:AGENTS_PER_SCENE_MAX]
    if not selected_ids:
        return []

    placeholders = ",".join(["?"] * len(selected_ids))
    char_rows = fetch_all(
        conn,
        f"SELECT * FROM characters WHERE id IN ({placeholders})",
        tuple(selected_ids),
    )
    # 按 selected_ids 顺序排
    char_by_id = {r["id"]: Character.from_row(r) for r in char_rows}
    return [char_by_id[cid] for cid in selected_ids if cid in char_by_id]


# ============================================================
# Step 3: 多轮自由对话
# ============================================================

def _agent_dialogue_one_round(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene: SceneDecision,
    scene_index: int,
    agent: Character,
    agents_present: list[Character],
    last_round_witnessed: list[dict],
    related_chunks: Optional[list[ChunkSnippet]] = None,
    round_visible_actions_so_far: Optional[list[dict]] = None,
) -> tuple[AgentRoundOutput, dict]:
    """单 agent 单轮 LLM 调用,返回 (output, usage)。

    Sprint 6.A2 M4.2(2026-05-19,治瑕疵 D 续作"灭灯"撕裂):
      round_visible_actions_so_far — 本 round 内此前 agent 已 perform 的动作 / 对白列表
      ([{speaker, action, dialogue}, ...])。本 agent 必须**承接或回应**这些动作,
      不许重复(张凡已推门 → 不再"踹门"),不许无视(莫晴雨说"绕到后窗" → 必须回应)。
    """
    # 拉 agent 上下文(私有记忆;up_to_scene_index=scene_index 表示能看到本幕之前的)
    ctx = load_agent_context(
        conn, sim.id, agent.id,
        up_to_scene_index=scene_index,
        memory_limit=AGENT_MEMORY_PER_CALL_LIMIT,
    )

    user_input = {
        "name": ctx.character_name,
        "identity": ctx.identity,
        "personality": ctx.personality,
        "quotes": ctx.quotes,
        "no_go_list": ctx.no_go_list,
        "memories": [
            {
                "scene_index": m.scene_index,
                "memory_type": m.memory_type,
                "content": m.content,
                "other_chars": m.other_chars,
            }
            for m in ctx.memories
        ],
        "relationship_phases": ctx.relationship_phases,
        "scene_name": scene.scene_name,
        "scene_source": scene.scene_source,
        "time_anchor": scene.time_anchor,
        "scene_index": scene_index,
        "agents_present": [
            {
                "character_id": a.id,
                "name": a.name,
                "is_protagonist": a.is_protagonist,
            }
            for a in agents_present
        ],
        "last_round_witnessed": list(last_round_witnessed),  # 浅拷贝,防引用泄漏
        # Sprint 6.A2 M4.2(2026-05-19):本 round 内此前 agent 的可见动作
        # **必须浅拷贝**:caller 在后续 agent 迭代中会 append 进 round_visible_actions_so_far,
        # 若这里直接持有 reference,先前 user_input 快照会被污染(测试 bug 史)
        "round_so_far_witnessed": (
            list(round_visible_actions_so_far) if round_visible_actions_so_far else []
        ),
        # Sprint 6.A2 M3.C(2026-05-18):RAG 召回的原文 chunk 片段
        # — 让 agent 真正"读到原著相关段落",不再只看摘要(对齐用户铁律 + 信息丰度)
        "related_original_chunks": [
            {"score": c.score, "text": c.text}
            for c in (related_chunks or [])
        ],
    }

    # ===== Sprint 6.A2 M5.3(2026-05-20)生成前硬铁律 prepend =====
    # agent_dialogue 完整 prepend:实体 + 动作 + 时间 + 情绪
    from app.services.hard_constraints import (
        build_hard_constraints_block, prepend_constraints_to_prompt,
    )
    base_prompt = _load_prompt("m3_agent_dialogue.md")
    constraints_block = build_hard_constraints_block(
        conn, sim, scene_index, agents=agents_present,
    )
    system_prompt = prepend_constraints_to_prompt(base_prompt, constraints_block)
    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input,
            max_tokens=600, temperature=0.75,
            # 提案 B(2026-05-24):agent_dialogue 长对白防 LLM n-gram 复读
            frequency_penalty=0.4, presence_penalty=0.3,
        )
    except Exception as e:  # noqa: BLE001
        # B2-A(2026-05-27):agent_dialogue LLM 失败防级联 —
        # 旧实现 reflection="(LLM 失败)" 污染 LLM 上下文,下游 narrator 看到这文字困惑。
        # 改为完全静默占位(空 reflection / dialogue / action),让 narrator 当作此 agent
        # 本轮无主动表达,基于其他 agent 的对话和场景上下文合稿(降级但不崩溃)。
        logger.warning(
            f"agent_dialogue LLM failed (scene {scene_index} agent {agent.name}): {e}"
        )
        return AgentRoundOutput(
            speaker_id=agent.id,
            speaker_name=agent.name,
            reflection="",
            dialogue="",
            action="",
        ), {"input_tokens": 0, "output_tokens": 0}

    if not isinstance(parsed, dict):
        parsed = {}
    return AgentRoundOutput(
        speaker_id=agent.id,
        speaker_name=agent.name,
        reflection=str(parsed.get("reflection") or "")[:300],
        dialogue=str(parsed.get("dialogue") or "")[:300],
        action=str(parsed.get("action") or "")[:300],
    ), usage


def _run_scene_dialogue(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene: SceneDecision,
    scene_index: int,
    agents: list[Character],
    narrative_so_far: str,
    sim_total_in_baseline: int = 0,
    sim_total_out_baseline: int = 0,
) -> tuple[list[AgentRoundOutput], int, int]:
    """跑完一幕 N 轮 × N agent 对话。
    返回 (all_outputs, total_in_tokens, total_out_tokens)。

    Sprint 6.A2 M3.C:每幕开始时调 RAG 召回相关原文 chunk → 注入所有 agent 的 prompt
    (本幕内 N 轮 × N agent 共用,不重复调用 RAG,节省时间)。

    Sprint 6.A2 M3.D-fix7(2026-05-19,用户实测"已消耗 ¥0"卡死):
    sim_total_in/out_baseline 是 sim 进本幕前的累计 token(此前所有幕 + scene_picker
    + narrator 的总和),用于每次 agent_done 算"sim 累计成本"塞到 cost_yuan 字段,
    让前端实时显示成本变化(不等本幕完成再推 cost)。
    """
    # ===== Sprint 6.A2 M3.C(2026-05-18):RAG 召回 =====
    # query = 场景名 + 在场角色名 + 当前剧情末尾片段 → 找最相关原文 chunk
    rag_query = (
        f"{scene.scene_name} "
        + " ".join(a.name for a in agents)
        + " "
        + (narrative_so_far[-300:] if narrative_so_far else "")
    )
    try:
        related_chunks = retrieve_relevant_chunks(
            conn, sim.project_id, rag_query,
            top_k=RAG_TOP_K_PER_SCENE,
            snippet_max_chars=RAG_SNIPPET_MAX_CHARS,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"RAG retrieval failed scene {scene_index}: {e}")
        related_chunks = []

    if related_chunks:
        _emit(sim.id, {
            "kind": "evolution_rag_retrieved",
            "scene_index": scene_index,
            "chunks": [
                {"score": c.score, "preview": c.text[:60]}
                for c in related_chunks
            ],
        })

    all_outputs: list[AgentRoundOutput] = []
    total_in = 0
    total_out = 0
    last_round_witnessed: list[dict] = []

    for round_index in range(DIALOGUE_ROUNDS_PER_SCENE):
        round_outputs: list[AgentRoundOutput] = []
        # Sprint 6.A2 M4.2(2026-05-19,治瑕疵 D 续作"灭灯"撕裂):
        # round 内 visible actions — 每个 agent 跑完后追加,后续 agent 必读
        # 张凡推门 → 列表追加 {"speaker": "张凡", "action": "试推开门", "dialogue": ""}
        # 李宇天 prompt 收到该列表 → 不会再"踹门",而是"从张凡推开的门缝挤进去"
        round_visible_actions: list[dict] = []
        for agent in agents:
            _emit(sim.id, {
                "kind": "evolution_agent_start",
                "scene_index": scene_index,
                "round_index": round_index,
                "speaker": agent.name,
                "speaker_id": agent.id,
            })

            t0 = time.perf_counter()
            output, usage = _agent_dialogue_one_round(
                conn, sim, scene, scene_index, agent, agents,
                last_round_witnessed, related_chunks,
                round_visible_actions_so_far=round_visible_actions,
            )
            duration_ms = int((time.perf_counter() - t0) * 1000)
            total_in += int(usage.get("input_tokens", 0) or 0)
            total_out += int(usage.get("output_tokens", 0) or 0)

            # 落 agent 自己的记忆
            if output.reflection.strip():
                record_memory(
                    conn, simulation_id=sim.id, character_id=agent.id,
                    scene_index=scene_index, memory_type="reflection",
                    content=output.reflection,
                )
            if output.dialogue.strip():
                record_memory(
                    conn, simulation_id=sim.id, character_id=agent.id,
                    scene_index=scene_index, memory_type="dialogue",
                    content=output.dialogue,
                    other_chars=[a.id for a in agents if a.id != agent.id],
                )
                # 给在场其他人复制 witnessed
                share_witnessed_memory(
                    conn, simulation_id=sim.id,
                    speaker_character_id=agent.id,
                    witnesses_character_ids=[a.id for a in agents if a.id != agent.id],
                    scene_index=scene_index,
                    original_memory_type="dialogue",
                    content=f"{agent.name}:{output.dialogue}",
                )
            if output.action.strip():
                record_memory(
                    conn, simulation_id=sim.id, character_id=agent.id,
                    scene_index=scene_index, memory_type="action",
                    content=output.action,
                    other_chars=[a.id for a in agents if a.id != agent.id],
                )
                share_witnessed_memory(
                    conn, simulation_id=sim.id,
                    speaker_character_id=agent.id,
                    witnesses_character_ids=[a.id for a in agents if a.id != agent.id],
                    scene_index=scene_index,
                    original_memory_type="action",
                    content=f"{agent.name}:{output.action}",
                )

            round_outputs.append(output)
            all_outputs.append(output)

            # M4.2:追加到 round_visible_actions(让本 round 后续 agent 看见)
            #   只有非空 action / dialogue 才有可见性价值
            if output.action.strip() or output.dialogue.strip():
                round_visible_actions.append({
                    "speaker": agent.name,
                    "action": output.action,
                    "dialogue": output.dialogue,
                })

            # Sprint 6.A2 M3.D-fix7(2026-05-19):携带 sim 累计 cost,前端实时显示
            #   累计 token = baseline(进本幕前累计) + 本幕到当前 agent 为止已用
            sim_cumulative_in = sim_total_in_baseline + total_in
            sim_cumulative_out = sim_total_out_baseline + total_out
            sim_cumulative_cost = estimate_cost_yuan(
                sim_cumulative_in, sim_cumulative_out,
            )
            _emit(sim.id, {
                "kind": "evolution_agent_done",
                "scene_index": scene_index,
                "round_index": round_index,
                "speaker": agent.name,
                "duration_ms": duration_ms,
                "dialogue_preview": output.dialogue[:40],
                "tokens_input": int(usage.get("input_tokens", 0) or 0),
                "tokens_output": int(usage.get("output_tokens", 0) or 0),
                # 累计字段:前端 applyEventToSimulation 自动应用到 sim.cost_yuan
                "cost_yuan": round(sim_cumulative_cost, 4),
                "tokens_input_cumulative": sim_cumulative_in,
                "tokens_output_cumulative": sim_cumulative_out,
            })

        # 本轮所有 agent 产出 → 下一轮的 last_round_witnessed
        last_round_witnessed = [
            {
                "speaker": o.speaker_name,
                "speaker_id": o.speaker_id,
                "memory_type": "dialogue" if o.dialogue else "action",
                "content": o.dialogue or o.action,
            }
            for o in round_outputs
            if o.dialogue or o.action
        ]

    return all_outputs, total_in, total_out


# ============================================================
# Step 4: narrator 合稿
# ============================================================

def _narrator_compose(
    sim: Simulation,
    scene: SceneDecision,
    scene_index: int,
    agents: list[Character],
    outputs: list[AgentRoundOutput],
    previous_segment_tail: str,
    target_chars_remaining: int = 0,
    max_segment_chars: int = 400,
    forbidden_phrases: Optional[list[tuple[str, int]]] = None,
    retry_hint: str = "",
    conn: Optional[sqlite3.Connection] = None,
    temperature: float = 0.7,
    outline_scene_block: str = "",
    chain_context_text: str = "",
    planner_hint: str = "",
    narrative_pov: Optional[str] = None,
    user_scene_hint: Optional[str] = None,
) -> tuple[str, dict]:
    """narrator LLM 把对话记录合稿成小说段落。返回 (segment, usage)。

    Sprint 6.A2 M3.D-fix1(2026-05-18):加 target_chars_remaining + max_segment_chars
    控制本幕字数 — 让 narrator 知道整篇还需多少字 + 本幕硬上限,自适应详略。

    Sprint 6.A2 M4.2(2026-05-19,治瑕疵 4 套语复读机):
      forbidden_phrases — 本 sim 历史已用 ≥5 次的高频套语 [(phrase, freq), ...]
      narrator 必须自律避免复读(用同义不同词表达相同状态)。

    Sprint 6.A2 M4.3(2026-05-20,Reflexion retry 回路):
      retry_hint — 若非空表示这是 retry 调用,塞 consistency_checker 给出的违规清单 +
      改写建议。narrator 看到 retry_hint 必须严格规避其中的违规。
    """
    user_input = {
        "scene_name": scene.scene_name,
        "scene_source": scene.scene_source,
        "time_anchor": scene.time_anchor,
        "transition_type": scene.transition_type,  # M11.E:本幕过渡类型
        "scene_index": scene_index,
        # P0Q.3(2026-05-24)— 透出 aliases 给 LLM
        # 治"叶子弟弟"被通篇用而本名"佐一郎"被忽略 — narrator 只看 canonical name,
        # 不知道有更自然的本名可用。加 aliases 让 LLM 优先用真实姓名。
        "agents_present": [
            {
                "name": a.name,
                "aliases": list(a.aliases or []),
                "is_protagonist": a.is_protagonist,
            }
            for a in agents
        ],
        # P1.A 治"心理戏串行泄露"(2026-05-24):不再传 agent 的 reflection
        # 历史 bug:reflection 是 agent 的第一人称内心独白("他来了。比我想象的早。
        # 看来是认真的"),narrator 偶尔机械直接复用 → 内心戏原样打印到正文,
        # 用户读到时如"演员突然念出导演画外音"。
        # 修复:narrator 只看 dialogue + action 客观信号,据此自主推断+融合心理描写,
        # 强制 narrator 做创意工作(转换叙述视角)而非机械转录。
        "dialogue_log": [
            {
                "round": idx // max(len(agents), 1),
                "speaker": o.speaker_name,
                # "reflection": o.reflection,  # ← 故意删除,见上方注释
                "dialogue": o.dialogue,
                "action": o.action,
            }
            for idx, o in enumerate(outputs)
        ],
        "style": sim.style,
        "custom_style_hint": sim.custom_style_hint or "",
        "previous_segment_tail": previous_segment_tail,
        "original_tail_excerpt": sim.original_tail_excerpt or "",
        # Sprint 6.A2 M3.D-fix1:字数控制信号
        "target_chars_remaining": target_chars_remaining,
        "max_segment_chars": max_segment_chars,
        # M4.2:套语禁用清单([(phrase, freq), ...])
        "forbidden_phrases": [
            {"phrase": p, "freq": f}
            for p, f in (forbidden_phrases or [])
        ],
        # M4.3:Reflexion retry hint(首次调为空;retry 时塞 violations)
        "retry_hint": retry_hint,
        # M9.A.2(2026-05-20):长篇心智全链 context — 多代续作时让 narrator
        # 看到完整祖先链 summary + 跨代角色弧 + open 伏笔 + 累积世界规则
        # 独立推演 / 无累积心智 → 空字符串(LLM 仍正常工作)
        "chain_context": chain_context_text,
        # Sprint 6.A2 FOCUS.2(2026-05-21):作品叙述视角硬约束
        # null = 未识别(老项目) → 落 prompt 时显示为 JSON null,narrator 按"模仿原作末段笔法"兜底
        # "first/second/third/mixed" → narrator 铁律 9 强制延续相同人称
        "narrative_pov": narrative_pov,
    }

    # ===== Sprint 6.A2 M5.3(2026-05-20)生成前硬铁律 prepend =====
    # narrator 完整 prepend:实体 + 动作 + 时间 + 情绪
    # ===== Sprint 6.A2 M6(2026-05-20)Outline-First =====
    # 若提供 outline_scene_block → 拼接到硬铁律段末尾(最高优先级)
    base_prompt = _load_prompt("m3_narrator.md")
    if conn is not None:
        from app.services.hard_constraints import (
            build_hard_constraints_block, prepend_constraints_to_prompt,
        )
        constraints_block = build_hard_constraints_block(
            conn, sim, scene_index, agents=agents,
        )
        # M6:把 outline_scene 图纸拼到硬铁律段开头(权重比 M5 entity/action 更高)
        if outline_scene_block:
            outline_header = (
                "\n" + ("=" * 70) + "\n"
                "本幕完整图纸(outline-first 模式 — 你的唯一蓝本)\n"
                + ("=" * 70) + "\n\n"
                + outline_scene_block
                + "\n\n" + ("=" * 70) + "\n"
                "以下是补充硬约束:\n"
                + ("=" * 70) + "\n"
            )
            constraints_block = outline_header + (constraints_block or "")
        # Sprint 6.A2 MP(2026-05-21)— planner 张力指令拼到 constraints 段顶部
        # 灵魂续写模式专用(outline-first 模式张力已在 outline_scene_block 内)
        if planner_hint and not outline_scene_block:
            planner_header = (
                "\n" + ("=" * 70) + "\n"
                "本幕张力规划(MP planner — 笔法基调)\n"
                + ("=" * 70) + "\n\n"
                + planner_hint
                + "\n\n" + ("=" * 70) + "\n"
            )
            constraints_block = planner_header + (constraints_block or "")
        system_prompt = prepend_constraints_to_prompt(base_prompt, constraints_block)
    else:
        system_prompt = base_prompt

    # #5(2026-05-23):用户即时干预 hint(若有) — append 到 system prompt 末尾,最高优先级
    system_prompt = system_prompt + _build_user_hint_block(user_scene_hint)

    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input,
            max_tokens=800, temperature=temperature,
            # 提案 B(2026-05-24):narrator 长叙述防 LLM n-gram 复读
            # (续作出现"我从哪里也不是的场所的正中"一字不差两次,就是这种坑)
            frequency_penalty=0.4, presence_penalty=0.3,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"narrator LLM failed scene {scene_index}: {e}")
        # 降级:简单拼接 dialogue
        fallback = "\n".join(
            f"{o.speaker_name}:{o.dialogue}"
            for o in outputs if o.dialogue
        )
        return fallback[:600] or "(本幕合稿失败)", {"input_tokens": 0, "output_tokens": 0}

    if not isinstance(parsed, dict):
        parsed = {}
    segment = str(parsed.get("narrative_segment") or "").strip()
    if len(segment) < 50:
        # 兜底:LLM 输出太短(异常)→ 简单拼接
        segment = "\n".join(
            f"{o.speaker_name}:{o.dialogue}"
            for o in outputs if o.dialogue
        )[:600] or "(本幕合稿失败)"
    # Sprint 6.A2 M3.D-fix2 v2(2026-05-18,用户反对硬截断):
    # 删 hard_cap 兜底 — 让 narrator LLM 通过 prompt 中的 max_segment_chars 指引
    # 自主控制长度。主循环动态停止机制(target × 0.95 break)已防止整篇超长。
    return segment, usage


# ============================================================
# 主入口
# ============================================================

def run_evolution_simulation(sim_id: str) -> None:
    """灵魂续写主循环。同步执行,生产由 simulation_service kick_off 线程跑。

    任何阶段抛异常 → state=failed + emit error 事件 + error_message 落库。
    DB 连接每次新开(可能跨线程)。
    """
    conn = get_connection()
    try:
        # 1. 拉 sim
        row = fetch_one(conn, "SELECT * FROM simulations WHERE id=?", (sim_id,))
        if row is None:
            logger.error(f"run_evolution_simulation: sim {sim_id} 不存在")
            return
        sim = Simulation.from_row(row)
        if sim.mode != "evolution":
            logger.error(
                f"run_evolution_simulation called with sim.mode={sim.mode!r}, "
                f"expected 'evolution'(simulation_service 路由层未生效?)"
            )
            return

        # 2. 标 directing(复用现有状态名,前端兼容)
        # Bug 1 修(2026-05-24):started_at 走 SQL 层 COALESCE,不依赖 Python 对象。
        # sim 是函数参数,在 SSE 重连/retry 路径下可能是过时快照,started_at 字段
        # 可能为 None,OR 兜底会重置;现改 SQL WHERE started_at IS NULL,绝不覆盖。
        from app.services.simulation_service import _set_started_at_if_null
        _set_started_at_if_null(conn, sim_id)
        _update_sim_state(
            conn, sim_id, state="directing",
            error_message=None,
        )

        # Sprint 6.A2 M3.D-fix6(2026-05-19,用户实测"正在排队"卡死):
        # 状态切到 directing 后立即 emit state_change 事件,前端从"正在排队"切到
        # "AI 正在演…"(对齐 quick 模式 simulation_service.py:1442-1448 pattern)
        _emit(sim_id, {
            "kind": "state_change",
            "state": "directing",
            "rounds_planned": sim.rounds_planned,
            "mode": "evolution",
        })

        # ===== Sprint 6.A2 M6(2026-05-20)Outline-First 分支 =====
        # 若 sim.use_outline_first 且已有 approved outline → 走 outline 路径
        # 每幕的 location / characters / events / props 全部从 outline_scenes 读
        # 不调 _pick_scene / _summon_agents 自由发挥
        from app.services.outline_orchestrator import (
            load_outline_scenes_for_sim, mark_outline_state,
        )
        outline_scenes_list = None
        if bool(getattr(sim, "use_outline_first", 0)):
            outline_scenes_list = load_outline_scenes_for_sim(conn, sim_id)
            if outline_scenes_list:
                # state 标 generating
                mark_outline_state(conn, sim_id, "generating")
                _emit(sim_id, {
                    "kind": "evolution_outline_loaded",
                    "scenes_count": len(outline_scenes_list),
                })

        # Sprint 6.A2 M3.D-fix1(2026-05-18)字数控制:
        # 主循环不再硬跑 rounds_planned 幕,改"达到 target_chars 即停"
        # rounds_planned 仅作"规划上限"(quick 模式仍用),evolution 实际跑多少幕由 narrative 字数决定
        target_chars = max(int(sim.target_chars or 4000), 500)
        max_scenes = min(int(sim.rounds_planned or 12), MAX_SCENES_HARD_CAP)
        # M6:outline-first 模式下,max_scenes 严格 = outline_scenes 数量(用户已审核)
        if outline_scenes_list:
            max_scenes = len(outline_scenes_list)

        _emit(sim_id, {
            "kind": "evolution_start",
            "rounds_planned": sim.rounds_planned,
            "target_chars": target_chars,
            "max_scenes": max_scenes,
            "mode": "evolution",
        })

        # 3. 主循环:动态停止(narrative 接近 target_chars 即 break)
        total_in = sim.tokens_input
        total_out = sim.tokens_output
        narrative_so_far = sim.narrative or ""
        recent_scenes: list[str] = []
        all_segments: list[str] = []
        # Sprint 6.A2 MP(2026-05-21)— planner 张力曲线(供下一幕 planner 看上下文)
        recent_tension_curve: list[int] = []

        # ===== Sprint 6.A2 TS(2026-05-21)— 末尾态笔法特征首次缓存 =====
        # 末尾态 sim(original_tail_excerpt 非空)首次进入主循环时,
        # 一次性分析原作末段 5 维特征 → 缓存到 simulations.tail_style_features_json
        # 后续每幕 narrator multi-sample voting 时,每个候选过 tail_style_checker
        # 拿综合分,作为 voting tiebreaker。
        tail_style_features = None
        if sim.original_tail_excerpt:
            try:
                from app.services.tail_style_analyzer import (
                    analyze_tail,
                    get_cached_features,
                    write_back_features,
                )
                tail_style_features = get_cached_features(conn, sim_id)
                if tail_style_features is None:
                    tail_style_features, ts_usage = analyze_tail(
                        sim.original_tail_excerpt,
                    )
                    write_back_features(conn, sim_id, tail_style_features)
                    total_in += int(ts_usage.get("input_tokens", 0) or 0)
                    total_out += int(ts_usage.get("output_tokens", 0) or 0)
                    _emit(sim_id, {
                        "kind": "evolution_tail_style_analyzed",
                        "perspective": tail_style_features.perspective,
                        "tone_baseline": tail_style_features.tone_baseline,
                        "vocab_count": len(tail_style_features.vocab_set),
                    })
            except Exception as e:  # noqa: BLE001
                logger.warning(f"tail_style 首次分析失败 sim={sim_id}: {e}")
                tail_style_features = None

        # M9.A.2(2026-05-20)— 长篇心智全链 context(一次性构建,本 sim 所有幕复用)
        # 含:祖先链各代 narrative_summary + 跨代角色弧 + open 伏笔 + 累积世界规则
        # 用于注入 narrator user_input,让 LLM 在每幕合稿时"记得"长链。
        # 失败兜底:任何步骤异常都 fall back 到空字符串,不阻塞主循环
        chain_context_text = ""
        try:
            from app.services.chain_context_builder import build_chain_context
            chain_context_text = build_chain_context(conn, sim)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"chain_context build failed sim={sim_id}: {e}")
            chain_context_text = ""

        # Sprint 6.A2 FOCUS.2(2026-05-21)— 项目叙述视角(一次性读取,本 sim 所有幕复用)
        # null = 未识别(老项目 / extract 未跑)→ narrator 按"模仿原作末段笔法"兜底
        # first/second/third/mixed → narrator 铁律 9 强制延续相同人称
        narrative_pov: Optional[str] = None
        try:
            pov_row = fetch_one(
                conn,
                "SELECT narrative_pov FROM projects WHERE id=?",
                (sim.project_id,),
            )
            if pov_row is not None:
                raw_pov = pov_row["narrative_pov"]
                if isinstance(raw_pov, str) and raw_pov in {"first", "second", "third", "mixed"}:
                    narrative_pov = raw_pov
        except Exception as e:  # noqa: BLE001
            logger.warning(f"narrative_pov read failed sim={sim_id}: {e}")
            narrative_pov = None

        for scene_index in range(max_scenes):
            # 字数动态停止:本幕开始前先检查
            # M6-fix6(2026-05-20):outline-first 模式严格按 outline 跑完所有幕
            #   原 M3.D-fix1 的"达 target × 0.95 即 break"机制在 outline 模式下不生效 —
            #   outline 是用户已批准的剧情骨架(含"合"阶段高潮 / 结局),被砍幕 = 剧情不闭环
            #   实测瑕疵:用户编排 28 幕但实际只跑 18 幕,后 10 幕(高潮 + 结局)缺失 → 产物失败
            cur_chars = len(narrative_so_far)
            if (
                not outline_scenes_list  # M6:outline-first 不触发动态停止
                and scene_index >= MIN_SCENES
                and cur_chars >= target_chars * TARGET_REACHED_RATIO
            ):
                _emit(sim_id, {
                    "kind": "evolution_target_reached",
                    "scenes_done": scene_index,
                    "narrative_chars": cur_chars,
                    "target_chars": target_chars,
                })
                break
            _emit(sim_id, {
                "kind": "evolution_scene_start",
                "scene_index": scene_index,
                "total_planned": sim.rounds_planned,
            })

            # #5(2026-05-23):本幕开始前消费用户即时干预 hint(读 + 立即清空,只影响这一幕)
            # 设计:scene_picker / narrator 都看到同一 hint;
            # hint 为空 → 一切照常;非空 → 两阶段都 append 到 system_prompt 末尾
            user_scene_hint = _consume_pending_hint(conn, sim_id)
            if user_scene_hint:
                # hotfix(2026-06-01):hint 历史持久化到 simulation_scenes.user_hint_applied
                # 让前端"提交记录"功能能回看用户在第几幕给过什么干预指令
                # 注:此时本幕的 simulation_scenes 行还没创建(narrator 跑完才 INSERT),
                # 暂存到本地变量,narrator INSERT 完后 UPDATE 上去 — 见下方场景幕落库点
                _emit(sim_id, {
                    "kind": "evolution_scene_hint_consumed",
                    "scene_index": scene_index,
                    "hint_preview": user_scene_hint[:60],
                })

            # 3.1 scene_picker
            # M6 分支:若有 outline → 直接从 outline_scenes 读 location/agents,**不调 LLM**
            current_outline_scene = None
            if outline_scenes_list and scene_index < len(outline_scenes_list):
                from app.services.outline_orchestrator import (
                    build_scene_decision_from_outline,
                    load_agents_from_outline_scene,
                )
                current_outline_scene = outline_scenes_list[scene_index]
                decision = build_scene_decision_from_outline(current_outline_scene)
                picker_usage = {"input_tokens": 0, "output_tokens": 0}
            else:
                decision, picker_usage = _pick_scene(
                    conn, sim, scene_index, sim.rounds_planned,
                    narrative_so_far, recent_scenes,
                    user_scene_hint=user_scene_hint,
                )
            total_in += int(picker_usage.get("input_tokens", 0) or 0)
            total_out += int(picker_usage.get("output_tokens", 0) or 0)
            _emit(sim_id, {
                "kind": "evolution_scene_picked",
                "scene_index": scene_index,
                "scene_name": decision.scene_name,
                "scene_source": decision.scene_source,
                "time_anchor": decision.time_anchor,
                "reasoning": decision.reasoning,
                "from_outline": current_outline_scene is not None,
            })

            # 3.2 summoner(本地算 / M6:从 outline 读)
            # hotfix(2026-06-01):传 user_scene_hint — 治"用户写'让 X 出场' summoner 不召唤 X"盲点
            if current_outline_scene is not None:
                agents = load_agents_from_outline_scene(
                    conn, current_outline_scene, sim.project_id,
                )
            else:
                agents = _summon_agents(conn, sim, decision, user_scene_hint=user_scene_hint)
            if not agents:
                # 项目无角色或全空 → 本幕跳过
                _emit(sim_id, {
                    "kind": "evolution_scene_skipped",
                    "scene_index": scene_index,
                    "reason": "no_agents_available",
                })
                continue
            _emit(sim_id, {
                "kind": "evolution_agents_summoned",
                "scene_index": scene_index,
                "agents": [
                    {"id": a.id, "name": a.name, "is_protagonist": a.is_protagonist}
                    for a in agents
                ],
            })

            # 3.3 落 simulation_scenes 行(先建,等 narrator 跑完 UPDATE narrative_segment)
            scene_row_id = uuid.uuid4().hex
            now = iso_now()
            execute(
                conn,
                """INSERT INTO simulation_scenes
                    (id, simulation_id, scene_index, scene_name, scene_source,
                     time_anchor, characters_present_json, narrative_segment,
                     created_at, user_hint_applied)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '', ?, ?)""",
                (
                    scene_row_id, sim_id, scene_index,
                    decision.scene_name, decision.scene_source,
                    decision.time_anchor,
                    json.dumps([a.id for a in agents], ensure_ascii=False),
                    now,
                    # hotfix(2026-06-01):本幕开始前消费的用户干预 hint(若有)
                    # 让前端"提交记录"能回看完整历史
                    user_scene_hint,
                ),
            )
            conn.commit()

            # 3.4 多轮对话(M3.C:传 narrative_so_far 给 RAG 召回作 query 末尾段)
            # M3.D-fix7:传 sim 累计 token baseline,让 _run_scene_dialogue 每个
            #   agent_done 都能 emit 累计 cost,前端实时显示"已消耗"
            outputs, dialog_in, dialog_out = _run_scene_dialogue(
                conn, sim, decision, scene_index, agents, narrative_so_far,
                sim_total_in_baseline=total_in,
                sim_total_out_baseline=total_out,
            )
            total_in += dialog_in
            total_out += dialog_out

            # 3.5 narrator 合稿(M3.D-fix1:动态字数控制 / M4.2:套语去重)
            prev_tail = (
                all_segments[-1][-PREVIOUS_SEGMENT_TAIL_CHARS:]
                if all_segments else ""
            )
            cur_chars = len(narrative_so_far)
            remaining_chars = max(0, target_chars - cur_chars)
            # M6-fix6(2026-05-20):outline-first 字数均分给所有 outline 幕,
            # 不要被"剩余字数/剩余幕数"动态调整(会导致后面幕越写越短)
            if outline_scenes_list:
                # 严格按 outline 总幕数均分 target_chars,夹 [200, 600] 内
                per_scene_target = target_chars // max(1, len(outline_scenes_list))
                max_segment_chars = max(200, min(600, per_scene_target))
            else:
                # 非 outline 模式:沿用 M3.D-fix1 动态估算
                remaining_scenes_est = max(
                    1, min(max_scenes - scene_index, max(1, remaining_chars // 350))
                )
                max_segment_chars = max(
                    150, min(500, remaining_chars // remaining_scenes_est)
                )
            # M4.2:本 sim 已用 ≥5 次的高频套语(治瑕疵 4 复读机)
            from app.services.phrase_blacklist import build_phrase_blacklist
            try:
                forbidden_phrases = build_phrase_blacklist(conn, sim.id)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"phrase_blacklist failed sim={sim_id} scene={scene_index}: {e}"
                )
                forbidden_phrases = []

            # ===== Sprint 6.A2 M5.4(2026-05-20)Multi-sample voting =====
            # 用户拍板"成本不重要,做最好的产品" — 同一幕生成 3 个候选,
            # 每个过 consistency_checker → 选 critical 最少 + 字数最合理的入库
            from app.services.consistency_checker import check_segment

            NARRATOR_SAMPLE_TEMPERATURES = (0.65, 0.75, 0.85)

            # ===== Sprint 6.A2 MP(2026-05-21)— Planner 张力规划 =====
            # 在 narrator 之前调 planner LLM,根据"全篇位置 + 故事现状 + 大纲意图"
            # 预测本幕目标张力 + 节奏。
            # outline-first 模式:write_back 到 outline_scene 表(用户可见 + 持久化)
            # 灵魂续写模式:transient 只注入本幕 narrator system_prompt
            try:
                from app.services.scene_planner import (
                    plan_scene_tension,
                    write_back_to_outline_scene,
                )
                planner_global_arc = ""
                planner_scene_purpose = ""
                planner_scene_summary = ""
                if current_outline_scene is not None:
                    planner_scene_purpose = (
                        current_outline_scene.scene_purpose or ""
                    )
                    planner_scene_summary = (
                        current_outline_scene.scene_summary or ""
                    )
                    # 拉 outline 全局 arc(从 simulation_outlines 表)
                    try:
                        outline_row = fetch_one(
                            conn,
                            "SELECT global_arc FROM simulation_outlines"
                            " WHERE simulation_id=?",
                            (sim_id,),
                        )
                        if outline_row:
                            planner_global_arc = outline_row["global_arc"] or ""
                    except Exception as e_arc:  # noqa: BLE001
                        logger.warning(f"fetch global_arc failed: {e_arc}")
                planner_decision, planner_usage = plan_scene_tension(
                    scene_index=scene_index,
                    total_scenes=sim.rounds_planned,
                    global_arc=planner_global_arc,
                    scene_purpose=planner_scene_purpose,
                    scene_summary=planner_scene_summary,
                    narrative_so_far_tail=narrative_so_far[-1500:],
                    recent_tension_curve=recent_tension_curve,
                )
                total_in += int(planner_usage.get("input_tokens", 0) or 0)
                total_out += int(planner_usage.get("output_tokens", 0) or 0)
                # outline-first 模式回写 + 同步内存对象(让 to_prompt_block 用新值)
                if current_outline_scene is not None:
                    try:
                        write_back_to_outline_scene(
                            conn, current_outline_scene.id, planner_decision,
                        )
                        current_outline_scene.tension_percent = (
                            planner_decision.tension_percent
                        )
                        current_outline_scene.pacing_tempo = (
                            planner_decision.pacing_tempo
                        )
                    except Exception as e_wb:  # noqa: BLE001
                        logger.warning(f"planner write_back failed: {e_wb}")
                # 累积曲线(给下一幕看上下文)
                recent_tension_curve.append(planner_decision.tension_percent)
                if len(recent_tension_curve) > 5:
                    recent_tension_curve.pop(0)
                planner_hint_text = planner_decision.to_narrator_hint()
                _emit(sim_id, {
                    "kind": "evolution_scene_planned",
                    "scene_index": scene_index,
                    "tension_percent": planner_decision.tension_percent,
                    "pacing_tempo": planner_decision.pacing_tempo,
                    "reasoning": planner_decision.reasoning,
                })
            except Exception as e_planner:  # noqa: BLE001
                logger.warning(
                    f"planner failed sim={sim_id} scene={scene_index}: {e_planner}"
                )
                planner_hint_text = ""

            # M6:若本幕有 outline_scene → 生成图纸 block 注入硬铁律
            outline_scene_block = ""
            if current_outline_scene is not None:
                from app.services.outline_orchestrator import (
                    build_outline_constraints_block,
                )
                outline_scene_block = build_outline_constraints_block(
                    current_outline_scene,
                )

            candidates: list[dict] = []  # {segment, critical_count, char_count, usage_in, usage_out}
            for sample_idx, temp in enumerate(NARRATOR_SAMPLE_TEMPERATURES):
                seg, usage = _narrator_compose(
                    sim, decision, scene_index, agents, outputs, prev_tail,
                    target_chars_remaining=remaining_chars,
                    max_segment_chars=max_segment_chars,
                    forbidden_phrases=forbidden_phrases,
                    retry_hint="",
                    conn=conn,
                    temperature=temp,
                    outline_scene_block=outline_scene_block,
                    chain_context_text=chain_context_text,    # M9.A.2 长篇心智注入
                    planner_hint=planner_hint_text,           # MP(2026-05-21)张力指令
                    narrative_pov=narrative_pov,              # FOCUS.2 叙述视角延续
                    user_scene_hint=user_scene_hint,          # #5(2026-05-23)用户即时干预
                )
                total_in += int(usage.get("input_tokens", 0) or 0)
                total_out += int(usage.get("output_tokens", 0) or 0)

                # 每个候选过 consistency check
                try:
                    check_result, check_usage = check_segment(
                        conn, sim, scene_index, seg, agents,
                    )
                    total_in += int(check_usage.get("input_tokens", 0) or 0)
                    total_out += int(check_usage.get("output_tokens", 0) or 0)
                    crit_count = check_result.critical_count
                    warn_count = len([
                        v for v in check_result.violations
                        if v.severity == "warning"
                    ])
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"consistency_checker (sample {sample_idx}) failed: {e}"
                    )
                    crit_count = 0
                    warn_count = 0

                # 字数离 max_segment_chars 的偏离(绝对值小越好)
                char_dev = abs(len(seg) - max_segment_chars)

                # ===== Sprint 6.A2 TS(2026-05-21)— 末尾态笔法 5 维对齐评分 =====
                # 仅末尾态(tail_style_features 非 None)启用;
                # 非末尾态此分恒为 -1(voting 时不参与)
                style_composite = -1
                style_score_obj = None
                if tail_style_features is not None:
                    try:
                        from app.services.tail_style_checker import (
                            check_style_alignment,
                        )
                        style_score_obj, style_usage = check_style_alignment(
                            tail_style_features, seg,
                        )
                        style_composite = style_score_obj.composite_score
                        total_in += int(style_usage.get("input_tokens", 0) or 0)
                        total_out += int(style_usage.get("output_tokens", 0) or 0)
                    except Exception as e_ts:  # noqa: BLE001
                        logger.warning(
                            f"tail_style_checker (sample {sample_idx}) failed: {e_ts}"
                        )

                candidates.append({
                    "segment": seg,
                    "critical_count": crit_count,
                    "warning_count": warn_count,
                    "char_deviation": char_dev,
                    "char_count": len(seg),
                    "temperature": temp,
                    "style_composite": style_composite,  # -1 = 非末尾态
                })
                _emit(sim_id, {
                    "kind": "evolution_narrator_sample",
                    "scene_index": scene_index,
                    "sample_index": sample_idx,
                    "temperature": temp,
                    "char_count": len(seg),
                    "critical_count": crit_count,
                    "style_composite": style_composite,
                })

            # 候选评分:critical 最少 → warning 最少 → (末尾态)笔法分越高越好 → char 偏离最少
            # Sprint 6.A2 TS:笔法分作为 tiebreaker,用负数让 min 倾向高分
            def _score_key(c: dict) -> tuple:
                return (
                    c["critical_count"],
                    c["warning_count"],
                    -c["style_composite"],  # 高分(末尾态)优先;非末尾态全 -1 平局
                    c["char_deviation"],
                )
            best = min(candidates, key=_score_key)
            segment = best["segment"]
            _emit(sim_id, {
                "kind": "evolution_narrator_selected",
                "scene_index": scene_index,
                "selected_temperature": best["temperature"],
                "selected_critical_count": best["critical_count"],
                "selected_char_count": best["char_count"],
                "selected_style_composite": best["style_composite"],
                "total_candidates": len(candidates),
            })

            # ===== P0S.1(2026-05-24)— M4.3 Reflexion retry 循环升级 =====
            # 原 max 1 retry + 接受违规 → 改 max 3 retry + 每次重 check_segment
            # 治"L35 对白逐字复读没被 catch" 真根因:retry 后没复检,直接接受
            # 死循环防护:**最多 3 次** + 任何步骤异常都跳出循环
            MAX_NARRATOR_RETRIES = 3
            retry_count = 0
            if best["critical_count"] > 0:
                try:
                    final_check, final_check_usage = check_segment(
                        conn, sim, scene_index, segment, agents,
                    )
                    total_in += int(final_check_usage.get("input_tokens", 0) or 0)
                    total_out += int(final_check_usage.get("output_tokens", 0) or 0)

                    while (
                        final_check.has_critical_violation
                        and retry_count < MAX_NARRATOR_RETRIES
                    ):
                        retry_count += 1
                        retry_hint = final_check.to_retry_hint()
                        retry_seg, retry_usage = _narrator_compose(
                            sim, decision, scene_index, agents, outputs, prev_tail,
                            target_chars_remaining=remaining_chars,
                            max_segment_chars=max_segment_chars,
                            forbidden_phrases=forbidden_phrases,
                            retry_hint=retry_hint,
                            conn=conn,
                            temperature=0.7,
                            outline_scene_block=outline_scene_block,
                            chain_context_text=chain_context_text,
                            planner_hint=planner_hint_text,
                            narrative_pov=narrative_pov,
                            user_scene_hint=user_scene_hint,
                        )
                        total_in += int(retry_usage.get("input_tokens", 0) or 0)
                        total_out += int(retry_usage.get("output_tokens", 0) or 0)
                        segment = retry_seg

                        # P0S.1 关键变化:retry 后**复检测**,有 critical 继续 retry
                        retry_check, retry_check_usage = check_segment(
                            conn, sim, scene_index, segment, agents,
                        )
                        total_in += int(retry_check_usage.get("input_tokens", 0) or 0)
                        total_out += int(retry_check_usage.get("output_tokens", 0) or 0)
                        final_check = retry_check

                        _emit(sim_id, {
                            "kind": "evolution_narrator_retried",
                            "scene_index": scene_index,
                            "retry_attempt": retry_count,
                            "remaining_critical": final_check.critical_count,
                        })

                    # 循环结束:无 critical(成功)或达到 MAX_NARRATOR_RETRIES
                    if final_check.has_critical_violation:
                        logger.warning(
                            f"narrator retry exhausted sim={sim_id} scene={scene_index} "
                            f"after {MAX_NARRATOR_RETRIES} attempts, "
                            f"still {final_check.critical_count} critical — 将走 P0S.2 后处理兜底"
                        )

                        # P0S.2 后处理兜底:删完全重复的对白
                        from app.services.dialogue_dedup import (
                            dedup_repeated_dialogues_in_segment,
                            dedup_repeated_narratives_in_segment,
                            dedup_cross_segment_short_repeats,
                        )
                        try:
                            segment, removed_count = dedup_repeated_dialogues_in_segment(
                                segment,
                            )
                            if removed_count > 0:
                                logger.info(
                                    f"P0S.2 post-process: 删 {removed_count} 处复读对白"
                                )
                        except Exception as e:  # noqa: BLE001
                            logger.warning(f"P0S.2 dedup failed: {e}")

                        # P0T.2 后处理兜底:删完全重复的长叙述句
                        # 治"L61/L125 内心独白逐字复读 / L63/L127 嘴唇描写复用"
                        # 对白兜底只扫引号内,叙述层(内心独白 / 环境描写)绕过完全
                        # min_chars=20:屏蔽短呼应("她笑了""屋里很静"),仍能 catch
                        #   "她的嘴唇微微张开,像是要说什么,却永远说不出口了"(24 字)
                        try:
                            segment, narr_removed = dedup_repeated_narratives_in_segment(
                                segment, min_chars=20,
                            )
                            if narr_removed > 0:
                                logger.info(
                                    f"P0T.2 post-process: 删 {narr_removed} 处复读叙述句"
                                )
                        except Exception as e:  # noqa: BLE001
                            logger.warning(f"P0T.2 narrative dedup failed: {e}")

                        # P0V.2(2026-05-24)— 跨幕短句完全相同兜底
                        # 治 14345 实测 "远处传来雪块滚落的闷响" L27/L43/L59 三次完全相同
                        # P0U.2 checker 报 critical 但 LLM retry 改不掉时,这里物理删除
                        try:
                            history_rows = fetch_all(
                                conn,
                                """SELECT narrative_segment FROM simulation_scenes
                                   WHERE simulation_id=? AND scene_index<? AND narrative_segment != ''
                                   ORDER BY scene_index ASC""",
                                (sim_id, scene_index),
                            )
                            history_segs = [r["narrative_segment"] for r in history_rows]
                            if history_segs:
                                segment, cross_removed = dedup_cross_segment_short_repeats(
                                    segment, history_segs,
                                    min_chars=10, history_threshold=2,
                                )
                                if cross_removed > 0:
                                    logger.info(
                                        f"P0V.2 post-process: 删 {cross_removed} 处跨幕短句复读"
                                    )
                        except Exception as e:  # noqa: BLE001
                            logger.warning(f"P0V.2 cross-segment dedup failed: {e}")

                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"consistency_checker retry failed sim={sim_id} scene={scene_index}: {e}"
                    )

            # 落 simulation_scenes.narrative_segment(可能是首次合稿或 retry 重写后的)
            execute(
                conn,
                "UPDATE simulation_scenes SET narrative_segment=? WHERE id=?",
                (segment, scene_row_id),
            )
            conn.commit()

            all_segments.append(segment)
            narrative_so_far = "\n\n".join(all_segments)

            # ===== Sprint 6.A2 M4.1(2026-05-19)hook =====
            # 3.6 world_state_extractor — 抽本幕事实落账本
            #     治瑕疵 2 时间线悖论 + 瑕疵 5 反派规则覆写
            try:
                from app.services.world_state_extractor import extract_world_facts
                _facts_added, facts_usage = extract_world_facts(
                    conn, sim, scene_index, segment, agents,
                )
                total_in += int(facts_usage.get("input_tokens", 0) or 0)
                total_out += int(facts_usage.get("output_tokens", 0) or 0)
                _emit(sim_id, {
                    "kind": "evolution_world_facts_extracted",
                    "scene_index": scene_index,
                    "facts_added": _facts_added,
                })
            except Exception as e:  # noqa: BLE001
                # 抽取失败不阻塞主流程(降级容错)
                logger.warning(
                    f"world_state_extractor failed sim={sim_id} scene={scene_index}: {e}"
                )

            # 3.7 plot_tracker — 追踪主线推进 / 引入新 thread / 标已解决
            #     治瑕疵 1 剧情死循环
            try:
                from app.services.plot_tracker import track_plot_threads
                thread_summary, threads_usage = track_plot_threads(
                    conn, sim, scene_index, segment,
                )
                total_in += int(threads_usage.get("input_tokens", 0) or 0)
                total_out += int(threads_usage.get("output_tokens", 0) or 0)
                _emit(sim_id, {
                    "kind": "evolution_plot_threads_tracked",
                    "scene_index": scene_index,
                    "introduced": len(thread_summary["introduced"]),
                    "resolved": len(thread_summary["resolved"]),
                    "advanced": len(thread_summary["advanced"]),
                    "stale_count": thread_summary["stale_count"],
                })
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"plot_tracker failed sim={sim_id} scene={scene_index}: {e}"
                )

            # ===== Sprint 6.A2 M9.A.3(2026-05-20)长篇心智 3 extractor hook =====
            # 跨代累积心智 — 每幕 narrator 后抽取并落 character_arcs / foreshadow_ledger /
            # world_rules_ledger,供后代续作 chain_context_builder 消费。
            # 失败兜底:任何一个失败都不阻塞主流程(降级到无累积)
            try:
                from app.services.long_form_mind_extractor import (
                    extract_arcs, extract_foreshadows, extract_world_rules,
                )

                # 3.10.1 角色弧光
                try:
                    arc_summary = extract_arcs(
                        conn, sim, scene_index, decision.scene_name, segment, agents,
                    )
                    total_in += int(arc_summary["usage"].get("input_tokens", 0) or 0)
                    total_out += int(arc_summary["usage"].get("output_tokens", 0) or 0)
                    if arc_summary.get("created", 0) > 0:
                        _emit(sim_id, {
                            "kind": "evolution_arcs_extracted",
                            "scene_index": scene_index,
                            "arcs_created": arc_summary["created"],
                        })
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"arc_extractor failed sim={sim_id} scene={scene_index}: {e}"
                    )

                # 3.10.2 伏笔追踪
                try:
                    fs_summary = extract_foreshadows(
                        conn, sim, scene_index, decision.scene_name, segment,
                    )
                    total_in += int(fs_summary["usage"].get("input_tokens", 0) or 0)
                    total_out += int(fs_summary["usage"].get("output_tokens", 0) or 0)
                    if fs_summary.get("created", 0) > 0 or fs_summary.get("resolved", 0) > 0:
                        _emit(sim_id, {
                            "kind": "evolution_foreshadows_tracked",
                            "scene_index": scene_index,
                            "new_foreshadows": fs_summary.get("created", 0),
                            "resolved_foreshadows": fs_summary.get("resolved", 0),
                        })
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"foreshadow_tracker failed sim={sim_id} scene={scene_index}: {e}"
                    )

                # 3.10.3 世界规则
                try:
                    rule_summary = extract_world_rules(
                        conn, sim, scene_index, decision.scene_name, segment,
                    )
                    total_in += int(rule_summary["usage"].get("input_tokens", 0) or 0)
                    total_out += int(rule_summary["usage"].get("output_tokens", 0) or 0)
                    if rule_summary.get("created", 0) > 0:
                        _emit(sim_id, {
                            "kind": "evolution_world_rules_extracted",
                            "scene_index": scene_index,
                            "rules_created": rule_summary["created"],
                        })
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"world_rule_extractor failed sim={sim_id} scene={scene_index}: {e}"
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"long_form_mind_extractor import failed sim={sim_id}: {e}"
                )

            # ===== Sprint 6.A2 M5(2026-05-20)3 个新 extractor hook =====
            # 3.8 entity_registrar — 实体唯一身份注册(治瑕疵 1 核心变量覆写)
            try:
                from app.services.entity_registrar import register_entities_from_segment
                entity_summary, entity_usage = register_entities_from_segment(
                    conn, sim, scene_index, segment, agents,
                )
                total_in += int(entity_usage.get("input_tokens", 0) or 0)
                total_out += int(entity_usage.get("output_tokens", 0) or 0)
                _emit(sim_id, {
                    "kind": "evolution_entities_registered",
                    "scene_index": scene_index,
                    "created": len(entity_summary["created"]),
                    "aliased": len(entity_summary["aliased_to"]),
                })

                # Sprint 6.A2 M7.B(2026-05-20)— 续作新角色同步入库 + 关系图谱
                # 新角色已落 canonical_entities → 立即同步到 characters 表(标 origin_simulation_id)
                # + 给每个新角色与主角建"其他"关系(待用户细化)
                # 失败不阻塞主循环(token 已花,产物已落库,入库失败下次手动可补)
                try:
                    from app.services.sequel_character_sync import (
                        sync_new_characters_from_canonical,
                    )
                    sync_summary = sync_new_characters_from_canonical(
                        conn, sim, scene_index, agents,
                    )
                    if sync_summary["created_characters"] or sync_summary["created_relationships"]:
                        _emit(sim_id, {
                            "kind": "evolution_sequel_characters_synced",
                            "scene_index": scene_index,
                            "new_characters": len(sync_summary["created_characters"]),
                            "new_relationships": len(sync_summary["created_relationships"]),
                            "skipped": len(sync_summary["skipped"]),
                        })
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"sequel_character_sync failed sim={sim_id} scene={scene_index}: {e}"
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"entity_registrar failed sim={sim_id} scene={scene_index}: {e}"
                )

            # M8.A(2026-05-20)— 同步本幕场景到 project_scenes(若是新场景)
            # 这是治"outline 清一色教室"的根因:让 LLM 自创的新场景反向入库,
            # 下次推演的 scene_picker 就能在 available_scenes 里看到累积的新场景。
            try:
                from app.services.sequel_scene_sync import sync_new_scene_from_evolution
                scene_sync_summary = sync_new_scene_from_evolution(
                    conn, sim, scene_index, decision.scene_name,
                )
                if scene_sync_summary["created_scenes"]:
                    _emit(sim_id, {
                        "kind": "evolution_sequel_scenes_synced",
                        "scene_index": scene_index,
                        "new_scenes": [s["name"] for s in scene_sync_summary["created_scenes"]],
                    })
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"sequel_scene_sync failed sim={sim_id} scene={scene_index}: {e}"
                )

            # 3.9 action_extractor — 原子动作流水(治瑕疵 2 动作重复)
            try:
                from app.services.action_extractor import extract_actions_from_segment
                actions_count, actions_usage = extract_actions_from_segment(
                    conn, sim, scene_index, segment, agents,
                )
                total_in += int(actions_usage.get("input_tokens", 0) or 0)
                total_out += int(actions_usage.get("output_tokens", 0) or 0)
                _emit(sim_id, {
                    "kind": "evolution_actions_extracted",
                    "scene_index": scene_index,
                    "actions_count": actions_count,
                })
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"action_extractor failed sim={sim_id} scene={scene_index}: {e}"
                )

            # 3.10 emotional_state_tracker — 角色情绪链(治瑕疵 4 情绪断裂)
            try:
                from app.services.emotional_state_tracker import track_emotional_states
                emo_count, emo_usage = track_emotional_states(
                    conn, sim, scene_index, segment, agents,
                )
                total_in += int(emo_usage.get("input_tokens", 0) or 0)
                total_out += int(emo_usage.get("output_tokens", 0) or 0)
                _emit(sim_id, {
                    "kind": "evolution_emotional_states_tracked",
                    "scene_index": scene_index,
                    "states_count": emo_count,
                })
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"emotional_state_tracker failed sim={sim_id} scene={scene_index}: {e}"
                )

            # 3.11 SP-4(2026-05-28)— 角色状态时间线快照
            # 灵魂续写北极星·基础设施层:每幕末扫在场角色,写一行聚合快照
            # 本 sprint 字段:position(场景名)/ hp_status / status_note(全局)
            # SP-3.4(2026-06-02)— known_fact_ids 自动同步 character_knowledge
            # 后续:SP-2 弧光数据 / SP-4.1 emotion_vec 聚合
            try:
                from app.db import fetch_one as _fo
                from app.db import fetch_all as _fa
                from app.services.character_snapshot_service import write_snapshot
                for ag in agents:
                    char_row = _fo(
                        conn,
                        "SELECT life_status, status_note FROM characters WHERE id=?",
                        (ag.id,),
                    )
                    # SP-3.4(2026-06-02):聚合该角色截止本幕的"已知"fact ids
                    # 数据源:character_knowledge 表(SP-3 落地)
                    # 过滤:confidence='known' 且 introduced_scene_index ≤ scene_index
                    # 失败兜底:任何异常 → None,不阻塞 snapshot 主流程
                    known_fact_ids: Optional[list[str]] = None
                    try:
                        kn_rows = _fa(
                            conn,
                            "SELECT fact_id FROM character_knowledge "
                            "WHERE character_id=? AND confidence='known' "
                            "AND (introduced_scene_index IS NULL OR introduced_scene_index <= ?) "
                            "ORDER BY introduced_scene_index ASC",
                            (ag.id, scene_index),
                        )
                        known_fact_ids = [r["fact_id"] for r in kn_rows if r["fact_id"]]
                        if not known_fact_ids:
                            known_fact_ids = None
                    except Exception as e_kn:  # noqa: BLE001
                        # 老库 / 表不存在 → 静默降级
                        logger.debug(
                            f"snapshot SP-3.4: known_facts aggregation failed "
                            f"char={ag.id}: {e_kn}"
                        )
                        known_fact_ids = None
                    write_snapshot(
                        conn,
                        simulation_id=sim_id,
                        scene_index=scene_index,
                        character_id=ag.id,
                        character_name=ag.name,
                        position=decision.scene_name,
                        hp_status=char_row["life_status"] if char_row else None,
                        status_note=char_row["status_note"] if char_row else None,
                        known_fact_ids=known_fact_ids,
                    )
                _emit(sim_id, {
                    "kind": "evolution_character_snapshots_written",
                    "scene_index": scene_index,
                    "count": len(agents),
                })
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"character_snapshot write failed sim={sim_id} scene={scene_index}: {e}"
                )

            # 更新 recent_scenes(最近 N 幕 — 多样性铁律守护)
            recent_scenes.append(decision.scene_name)
            if len(recent_scenes) > RECENT_SCENES_LOOKBACK:
                recent_scenes = recent_scenes[-RECENT_SCENES_LOOKBACK:]

            # 增量落库
            cost_so_far = estimate_cost_yuan(total_in, total_out)
            _update_sim_state(
                conn, sim_id,
                current_round=scene_index + 1,
                tokens_input=total_in,
                tokens_output=total_out,
                cost_yuan=cost_so_far,
                narrative=narrative_so_far,
            )
            _emit(sim_id, {
                "kind": "evolution_scene_done",
                "scene_index": scene_index,
                "scene_name": decision.scene_name,
                "segment_preview": segment[:80],
                "cost_yuan": round(cost_so_far, 4),
            })

            # M6:标 outline_scene 跑完
            if current_outline_scene is not None:
                from app.services.outline_orchestrator import mark_outline_scene_state
                try:
                    mark_outline_scene_state(
                        conn, current_outline_scene.outline_id,
                        scene_index, "done",
                        generated_simulation_scene_id=scene_row_id,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"mark_outline_scene_state failed scene_index={scene_index}: {e}"
                    )

        # 4. 收尾
        final_cost = estimate_cost_yuan(total_in, total_out)
        _update_sim_state(
            conn, sim_id,
            state="done",
            completed_at=iso_now(),
            tokens_input=total_in,
            tokens_output=total_out,
            cost_yuan=final_cost,
            narrative=narrative_so_far,
        )
        # M6:标 outline 收尾
        if outline_scenes_list:
            from app.services.outline_orchestrator import mark_outline_state
            try:
                mark_outline_state(conn, sim_id, "done")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"mark_outline_state done failed: {e}")

        # F1.3(2026-06-02):sim done 后,把未解 plot_threads 升级到项目级 foreshadow_ledger
        # 这样"基于本篇续写"的新 sim 能通过 chain_context_builder 读到上篇未解伏笔
        # 失败 → log + 不阻塞 done(用户已拿到 narrative,同步失败不破)
        try:
            from app.services.foreshadow_promotion import (
                promote_unresolved_threads_to_foreshadow_ledger,
            )
            inserted, skipped = promote_unresolved_threads_to_foreshadow_ledger(
                conn, sim,
            )
            if inserted > 0:
                logger.info(
                    f"sim {sim_id} done: 升级 {inserted} 条未解伏笔到项目账本(去重跳过 {skipped} 条)"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"foreshadow promotion failed for sim {sim_id}: {e}")

        _emit(sim_id, {
            "kind": "done",
            "scenes_count": len(all_segments),
            "cost_yuan": round(final_cost, 4),
            "narrative_chars": len(narrative_so_far),
        })

    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        err_msg = f"{type(e).__name__}: {e}"[:300]
        try:
            _update_sim_state(
                conn, sim_id, state="failed",
                error_message=err_msg,
                completed_at=iso_now(),
            )
        except Exception as inner_e:  # noqa: BLE001
            # 2026-06-02 批次 2:silent failure 加 log — 失败状态写入又失败(罕见,DB 锁?)
            logger.exception(
                f"agent_evolution: failed state write failed sim={sim_id}: {inner_e}"
            )
        _emit(sim_id, {"kind": "error", "message": err_msg})
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            # conn.close 失败本身不可救援(连接已坏),忽略避免掩盖原异常
            pass
