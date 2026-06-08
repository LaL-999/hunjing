"""续写引擎服务 — Sprint 1.G simulate.py 服务化 + 1.K kick_off 修 + 1.L 流式 B2。

【核心职责】
  从 scripts/simulate.py 的命令行架构(Director-Agent-Composer 三层)抽提到 service 层,
  对接 DB 角色数据(初始态),状态机驱动,产物落库,亚秒级事件流(SSE + in-memory queue)。

【架构】
  Director : 编排每轮场景 / 在场 / 发声 / 契机                (1 个 LLM 调用 / 轮)
  Agent    : 每个角色独立扮演,产出 monologue/action/dialogue (N 个 / 轮)
  Composer : 把 timeline 编织成 markdown 叙事                  (1 个 LLM 调用 / 推演)

【状态机】
  queued → directing → composing → done
                                 ↘ failed       任一阶段 LLM 失败
                                 ↘ cancelled    用户主动停(本 sprint 不暴露端点)

【事件流(Sprint 1.L)】
  EVENT_QUEUES: dict[sim_id, list[(asyncio.Queue, event_loop)]]
  - SSE handler 注册自己的 queue;runner 从工作线程跨线程 put 事件
  - 跨线程安全:loop.call_soon_threadsafe(queue.put_nowait, event)
  - in-memory only,进程重启丢失;DB 状态保留进度,刷新页面照常恢复
  - 事件 kind 集合:snapshot / round_start / director_start / director_done /
    agent_start / agent_done / round_done / composing_start / composing_done /
    state_change / done / error / heartbeat

【kick_off 替换点】
  生产 _default_kick_off 用 threading.Thread(daemon)直接起后台线程跑;
  测试环境 monkeypatch kick_off = run_simulation 同步跑,POST 返回时已 done。
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

from app.db import execute, fetch_all, fetch_one, get_connection
from app.models.character import Character
from app.models.relationship import Relationship
from app.models.simulation import Simulation
from app.services.credit_service import (
    consume_credits,
    credit_units_for_text_call,
)
from app.services.llm_client import (
    call_llm_json,
    call_llm_text,
    estimate_cost_yuan,
)
from app.services.project_service import (
    ResourceNotFoundOrForbidden,
    get_project_or_403,
    iso_now,
)

log = logging.getLogger(__name__)


# === 路径(同 refine_service) ===
PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"


# ======================================================================
# 事件流基础设施(Sprint 1.L)
# ======================================================================

# 模块级:每个 sim_id 对应 N 个订阅者的 (queue, event_loop) 列表
# 用 list 是因为允许多个客户端同时 SSE 订阅同一 sim(比如同一用户开两个标签页)
EVENT_QUEUES: dict[
    str,
    list[tuple["asyncio.Queue[dict]", "asyncio.AbstractEventLoop"]],
] = {}

# 保护 EVENT_QUEUES 字典本身的并发增删(runner 线程 / SSE handler 协程都会动它)
_QUEUES_LOCK = threading.Lock()

# 单 queue 上限:防订阅者太慢导致 runner 内存爆。满了直接 skip 新事件(降级)
_QUEUE_MAXSIZE = 200


def _register_subscriber(
    sim_id: str,
    queue: "asyncio.Queue[dict]",
    loop: "asyncio.AbstractEventLoop",
) -> None:
    """SSE handler 启动时注册 queue。"""
    with _QUEUES_LOCK:
        EVENT_QUEUES.setdefault(sim_id, []).append((queue, loop))


def _unregister_subscriber(
    sim_id: str, queue: "asyncio.Queue[dict]"
) -> None:
    """SSE handler 关闭(用户跳页 / 终态)时清理。"""
    with _QUEUES_LOCK:
        subs = EVENT_QUEUES.get(sim_id, [])
        EVENT_QUEUES[sim_id] = [(q, lp) for (q, lp) in subs if q is not queue]
        if not EVENT_QUEUES[sim_id]:
            del EVENT_QUEUES[sim_id]


def _emit_event(sim_id: str, event: dict) -> None:
    """从 runner(工作线程)跨线程推事件到所有订阅者。

    - 没人订阅 → 丢弃(0 订阅者也要正常跑,不能阻塞 runner)
    - put 失败(loop 关 / queue 满)→ 吞掉(订阅者太慢是订阅者的问题,
      不能拖 runner;DB 上的最新状态依然准,丢的只是细粒度事件)
    """
    with _QUEUES_LOCK:
        subs = list(EVENT_QUEUES.get(sim_id, []))   # 拷贝,锁外迭代

    for queue, loop in subs:
        try:
            loop.call_soon_threadsafe(queue.put_nowait, event)
        except (RuntimeError, asyncio.QueueFull):
            # RuntimeError: loop 已关闭(handler 异常退出未清理)
            # QueueFull: 订阅者太慢
            pass


# === 业务常量 ===
MIN_CHARACTERS_FOR_SIMULATION = 3
"""推演至少需要 3 个角色,与 refine 一致。"""


# 滚雪球续写(Sprint 1.O)
MAX_CONTEXT_SIMULATIONS = 10
"""单次推演最多接续 10 条前文(配 schema 上限)。"""

SUMMARIZE_THRESHOLD_CHARS = 8000
"""前文总字数超此阈值 → 摘要化注入(否则全文注入)。

DeepSeek V3 单次 64K context;director / agent 的 system + history 已占
~10K,前文留 8K 是安全上限。超过就摘要化(每条压到 ~800 字)。
"""

SUMMARY_TARGET_CHARS = 800
"""单条 narrative 摘要目标字数 — 保留关键事件 / 对白 / 情绪,丢细节。"""


# 重塑度 → 轮次线性派生表(doc 3 三维度之第 2 维:agent 互动最大轮次)
RESHAPE_MIN_PERCENT = 10
RESHAPE_MAX_PERCENT = 90
ROUNDS_AT_MIN_RESHAPE = 5
ROUNDS_AT_MAX_RESHAPE = 50


def reshape_to_rounds(reshape_percent: int) -> int:
    """重塑度百分比 → 推演轮次。

    线性映射 10% → 5 轮 / 90% → 50 轮。
    | reshape | rounds |
    |---------|--------|
    | 10%     | 5      |
    | 30%     | 16     |  (free 上限)
    | 50%     | 27     |  (default)
    | 60%     | 33     |  (standard 上限)
    | 90%     | 50     |  (super 上限)
    """
    span_p = RESHAPE_MAX_PERCENT - RESHAPE_MIN_PERCENT
    span_r = ROUNDS_AT_MAX_RESHAPE - ROUNDS_AT_MIN_RESHAPE
    raw = ROUNDS_AT_MIN_RESHAPE + (reshape_percent - RESHAPE_MIN_PERCENT) / span_p * span_r
    return round(raw)


# Sprint 6.A2 M3.D-fix2 v2(2026-05-18,用户拍板"提前预估匹配重塑度的字数区间"):
# 字数 = f(rounds) 而非用户随便选 — 防止 "4000 字配 28 轮" 这种不合理组合
# 公式:每轮平均 ~350 字(中短篇阅读体验最佳)+ 区间 ±30%(让用户在合理范围内微调)
#
# | reshape | rounds | 推荐中心字数 | 推荐区间       | 标签           |
# |---------|--------|-------------|----------------|----------------|
# | 10%     | 5      | 1750        | 1200 - 2300    | 微改 / 片段    |
# | 30%     | 16     | 5600        | 3900 - 7300    | 短篇           |
# | 50%     | 28     | 9800        | 6900 - 12700   | 中短篇         |
# | 70%     | 41     | 14350       | 10000 - 18700  | 中篇           |
# | 90%     | 50     | 17500       | 12300 - 22700  | 中长篇起点     |
CHARS_PER_ROUND_CENTER = 350
CHARS_RANGE_RATIO = 0.30   # ±30%


def reshape_to_recommended_chars(reshape_percent: int) -> dict:
    """重塑度 → 推荐字数 {center, low, high, rounds, label}。

    返回:
      {
        "rounds": int,         # 派生轮次
        "center": int,         # 推荐字数中心
        "low":    int,         # 推荐区间下界
        "high":   int,         # 推荐区间上界
        "label":  str,         # "微改 / 短篇 / 中短篇 / 中篇 / 中长篇起点"
      }

    用户在 [low, high] 内自由微调 target_chars;前端 UI 应 clamp 滑块到这个区间。
    后端 schema 也校验 target_chars ∈ [low, high]。
    """
    rounds = reshape_to_rounds(reshape_percent)
    center = rounds * CHARS_PER_ROUND_CENTER
    low = int(center * (1 - CHARS_RANGE_RATIO))
    high = int(center * (1 + CHARS_RANGE_RATIO))

    # 按 reshape 档位贴标签
    if reshape_percent <= 15:
        label = "微改 / 片段"
    elif reshape_percent <= 35:
        label = "短篇"
    elif reshape_percent <= 55:
        label = "中短篇"
    elif reshape_percent <= 75:
        label = "中篇"
    else:
        label = "中长篇起点"

    return {
        "rounds": rounds,
        "center": center,
        "low": low,
        "high": high,
        "label": label,
    }


# === 异常 ===

class TooFewCharactersForSimulation(Exception):
    """角色数不足以触发推演。"""


class InvalidAnchorEvent(Exception):
    """Sprint 6.A2 M7.J(2026-05-20):传入的 anchor_event_id 不存在或不属于本项目。"""


class InvalidContextSimulations(Exception):
    """滚雪球前文 ids 校验失败(跨项目 / 未完成 / 不属于该用户)。"""

    def __init__(self, message: str, invalid_ids: list[str]):
        super().__init__(message)
        self.invalid_ids = invalid_ids


class SimulationNotResumable(Exception):
    """sim 不能恢复(state 是 done / 没进度 / 等)。"""


class SimulationStillRunning(Exception):
    """sim 当前正在被 worker 跑,resume 必须等其结束。"""


# === 并发保护:防止同一 sim 被多个 worker 同时跑(double resume / kick_off 重复)===
_RUNNING_SIMS: set[str] = set()
_RUNNING_LOCK = threading.Lock()


# ======================================================================
# 角色快照构造 — 把 DB Character + Relationship 适配成 simulate prompt 期待格式
# ======================================================================

def _character_to_snapshot_dict(
    char: Character,
    relationships: list[Relationship],
    chars_by_id: dict[str, Character],
) -> dict[str, Any]:
    """DB Character → simulate 期待的角色 dict 格式。

    初始态字段缺位处理(中间态接入文件上传后再补):
      - behavioral_rules:    [] (中间态从 generate_characters.py 提取)
      - voice_fingerprint.high_freq_words: []
      - voice_fingerprint.speech_style_notes: ""

    relationships 字段:展开成"对方名字 + 关系类型 + 描述",方便 agent 自我感知。
    """
    rels: list[dict[str, str]] = []
    for r in relationships:
        if r.source_id == char.id:
            other = chars_by_id.get(r.target_id)
            if other is None:
                continue
            desc_part = f" · {r.description}" if r.description else ""
            rels.append({"type": r.type, "description": f"{other.name}{desc_part}"})
        elif r.target_id == char.id:
            other = chars_by_id.get(r.source_id)
            if other is None:
                continue
            desc_part = f" · {r.description}" if r.description else ""
            rels.append({"type": r.type, "description": f"{other.name}{desc_part}"})

    return {
        "id": char.id,
        "name": char.name,
        "identity": char.identity,
        "personality": char.personality,
        "behavioral_rules": [],
        "voice_fingerprint": {
            "quotes": list(char.quotes or []),
            "high_freq_words": [],
            "speech_style_notes": "",
        },
        "no_go_list": list(char.no_go_list or []),
        "relationships": rels,
    }


def build_characters_snapshot(
    conn: sqlite3.Connection, project_id: str
) -> list[dict[str, Any]]:
    """从 DB 拉项目下所有 character + relationship,组装快照。

    按 created_at 排序保证稳定。raise TooFewCharactersForSimulation 当 <3。
    """
    char_rows = fetch_all(
        conn,
        "SELECT * FROM characters WHERE project_id=? ORDER BY created_at",
        (project_id,),
    )
    if len(char_rows) < MIN_CHARACTERS_FOR_SIMULATION:
        raise TooFewCharactersForSimulation(
            f"推演至少需要 {MIN_CHARACTERS_FOR_SIMULATION} 个角色,当前 {len(char_rows)}"
        )
    characters = [Character.from_row(r) for r in char_rows]

    rel_rows = fetch_all(
        conn,
        "SELECT * FROM relationships WHERE project_id=? ORDER BY created_at",
        (project_id,),
    )
    relationships = [Relationship.from_row(r) for r in rel_rows]

    chars_by_id = {c.id: c for c in characters}
    return [
        _character_to_snapshot_dict(c, relationships, chars_by_id)
        for c in characters
    ]


def _build_project_meta(
    conn: sqlite3.Connection, project_id: str
) -> dict[str, Any]:
    """拉项目元信息给 composer 做语体自适应(Sprint 1.Q)。

    返回:{name, type_label, tags}
    type_label 优先 custom_type_name(用户自定义类型,如"舞台剧"),否则映射 type 字段。
    """
    proj_row = fetch_one(
        conn,
        "SELECT name, type, custom_type_name, tags FROM projects WHERE id=?",
        (project_id,),
    )
    if not proj_row:
        return {"name": "未命名", "type_label": "故事", "tags": []}

    ptype = proj_row["type"]
    custom_type = proj_row["custom_type_name"]
    if ptype == "generic" and custom_type:
        type_label = custom_type
    else:
        type_label = {
            "novel": "小说", "comic": "漫画", "anime": "番剧",
        }.get(ptype, "故事")

    # B1-C(2026-05-27):tags 字段防御 — 不仅 catch JSONDecodeError,还 catch TypeError
    # (DB 中 tags 列若存了非字符串值如 int 会导致 TypeError);非 str 类型直接视为无 tags
    raw_tags = proj_row["tags"]
    if not isinstance(raw_tags, str) or not raw_tags:
        tags = []
    else:
        try:
            tags = json.loads(raw_tags)
            if not isinstance(tags, list):
                tags = []
        except (json.JSONDecodeError, TypeError) as e:
            import logging as _logging
            _logging.getLogger(__name__).warning(
                f"_get_project_meta: tags parse failed for project_row tags={raw_tags!r}: {e}"
            )
            tags = []

    return {
        "name": proj_row["name"] or "未命名",
        "type_label": type_label,
        "tags": tags,
    }


def _build_scene_from_project(
    conn: sqlite3.Connection,
    project_id: str,
    snapshot: list[dict[str, Any]],
) -> str:
    """合成场景描述:项目元信息 + 角色名表 + 已存事件作为背景。

    初始态没有像 ch74 抄检那样的强场景;靠用户填的 events 作软背景,
    具体冲突点由 divergence 给。
    """
    proj_row = fetch_one(
        conn,
        "SELECT name, type, custom_type_name, tags FROM projects WHERE id=?",
        (project_id,),
    )
    name = proj_row["name"] if proj_row else "未命名项目"
    ptype = proj_row["type"] if proj_row else "novel"
    custom_type = proj_row["custom_type_name"] if proj_row else None
    tags_raw = proj_row["tags"] if proj_row else "[]"
    try:
        tags = json.loads(tags_raw) if tags_raw else []
    except json.JSONDecodeError:
        tags = []

    # 类型标签:custom_type_name 优先(用户自定义),其次预设映射,兜底"故事"
    if ptype == "generic" and custom_type:
        type_label = custom_type
    else:
        type_label = {"novel": "小说", "comic": "漫画", "anime": "番剧"}.get(
            ptype, "故事"
        )

    char_lines = "\n".join(
        f"  - {c['name']}({c['identity'] or '未填身份'})" for c in snapshot
    )

    event_rows = fetch_all(
        conn,
        "SELECT description, participants FROM events WHERE project_id=? "
        "ORDER BY created_at LIMIT 10",
        (project_id,),
    )
    if event_rows:
        evt_lines = []
        id_to_name = {c["id"]: c["name"] for c in snapshot}
        for ev in event_rows:
            try:
                parts_ids = json.loads(ev["participants"])
            except json.JSONDecodeError:
                parts_ids = []
            # 历史脏数据(角色已删但 events.participants 残留旧 id)→ 跳过 lookup 失败的
            # 不再写 "?",避免污染 LLM 输入
            parts_names = [
                id_to_name[pid] for pid in parts_ids if pid in id_to_name
            ]
            parts_str = " / ".join(parts_names) if parts_names else "(无标注)"
            evt_lines.append(f"  - {ev['description']} (参与:{parts_str})")
        evt_section = "\n【已发生的关键事件】\n" + "\n".join(evt_lines)
    else:
        evt_section = ""

    tag_str = " / ".join(tags) if tags else "未标注"
    return (
        f"作品《{name}》(类型:{type_label} · 标签:{tag_str})。\n\n"
        f"【角色阵容】\n{char_lines}\n"
        f"{evt_section}"
    )


# ======================================================================
# Prompt 构造(直接迁移自 scripts/simulate.py,保持行为一致)
# ======================================================================

def _format_list(items: list, sep: str = "\n  - ") -> str:
    if not items:
        return "  (无)"
    return sep + sep.join(items)


def _safe_format_template(template: str, fields: dict[str, str]) -> str:
    """避免用 .format() — 用户输入含 {} 会炸。改用逐字段 replace。

    与 .format() 行为对齐:模板中字面 `{{` `}}` 还原为 `{` `}`(用于 prompt 里
    展示 JSON 例子)。**这一步必须做** — 否则 LLM 看到 prompt 里的 `{{` 会照搬,
    输出 `{{ ... }}` 导致 json.loads 解析失败(Sprint 1.G 撞过此坑)。

    顺序:先 replace 占位符,再还原 `{{`/`}}`。占位符 `{key}` 是单层花括号,
    不会被 `{{` 还原误吃;value 字符串如果含 `{{` 字面(罕见,用户名/身份不会
    含 JSON 转义),也会被还原 — 可接受的边界。
    """
    out = template
    for key, value in fields.items():
        out = out.replace("{" + key + "}", value)
    out = out.replace("{{", "{").replace("}}", "}")
    return out


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _build_agent_system_prompt(char: dict) -> str:
    template = _load_prompt("agent_system.md")
    rels_text = _format_list(
        [f"{r['type']}: {r['description']}" for r in char.get("relationships", [])]
    )
    quotes_text = "\n".join(
        f"  {i+1}. “{q}”"
        for i, q in enumerate(char.get("voice_fingerprint", {}).get("quotes", []))
    ) or "  (无)"
    no_go_text = _format_list(char.get("no_go_list", []))
    behavioral_text = _format_list(char.get("behavioral_rules", []))
    high_freq = "、".join(
        char.get("voice_fingerprint", {}).get("high_freq_words", [])
    ) or "(无)"
    style = char.get("voice_fingerprint", {}).get("speech_style_notes") or "(无)"

    return _safe_format_template(template, {
        "name":                char["name"],
        "identity":            char.get("identity") or "",
        "personality":         char.get("personality") or "",
        "relationships":       rels_text,
        "high_freq_words":     high_freq,
        "quotes":              quotes_text,
        "speech_style_notes":  style,
        "no_go_list":          no_go_text,
        "behavioral_rules":    behavioral_text,
    })


def _build_agent_user_prompt(
    char_name: str, scene: str, divergence: str, location: str,
    round_seed: str, narrator_note: str, flat_history: list[dict],
    prior_context: str = "",
) -> str:
    if flat_history:
        hist_str = "\n".join(
            f"  - [Round {h['round']}] {h['speaker']}({h['action_type']}):{h['content']}"
            for h in flat_history
        )
    else:
        hist_str = "  (这是第 1 轮第一位发声者,你之前还没有任何人开口)"

    prior_section = ""
    if prior_context:
        # 2026-06-02 patch B:agent 也加简化版接续约束
        last_tail = _extract_last_tail_excerpt(prior_context, tail_chars=500)
        last_tail_block = ""
        if last_tail:
            last_tail_block = (
                "\n【⚠ 你刚刚经历的最后场景(本轮发声从此处之后)】\n"
                f"……{last_tail}\n"
            )
        prior_section = (
            "\n【前情(你已经历过的剧情;新发声要与之连贯)】\n"
            f"{prior_context}\n"
            f"{last_tail_block}"
            "**发声铁律**:你的对白/动作/独白必须延续「最后场景」的情绪状态,**不要回到更早场景**.\n"
        )

    return f"""【场景设定】
{scene}
{prior_section}
【关键反事实变量 / 接下来想看到的剧情】
{divergence}

【当前位置 · 时间】
{location}

【迄今已发生(跨轮)】
{hist_str}

【本轮场面旁白】
{narrator_note}

【本轮契机(导演刚刚布置,你必须直接对此反应)】
{round_seed}

—— 你的回合 ——
作为【{char_name}】,在这一刻产出你的:

1. monologue:此刻内心独白(不超过 30 字,真实想法)
2. action:此刻外在动作(不超过 30 字)
3. dialogue:你说出口的话(不超过 80 字;若此刻不开口,留空字符串)

输出格式(严格 JSON):
{{
  "monologue": "...",
  "action": "...",
  "dialogue": "..."
}}"""


def _summarize_char_for_director(char: dict) -> str:
    return (
        f"{char['id']}({char['name']}):{(char.get('identity') or '')[:25]} | "
        f"性格 {(char.get('personality') or '')[:35]}"
    )


def _extract_last_tail_excerpt(prior_context: str, tail_chars: int = 800) -> str:
    """从 prior_context 文本提取"直接父辈 narrative 最后 N 字"作为续作起点 anchor.

    2026-06-02 patch B:_gather_prior_narratives 输出的 prior_context 是多段拼接的,
    最后一段就是直接父辈(按时序 ASC).这里提取它的最后 tail_chars 字.

    实现:简单按 prior_context 整体取尾部 tail_chars 字(覆盖直接父辈尾部 + 部分上一段 header).
    简单但有效;过滤掉 sections separator 等噪音.
    """
    if not prior_context:
        return ""
    # 找最后一个 "---" 分隔符之后的内容(即最后一段)
    last_sep_idx = prior_context.rfind("\n\n---\n\n")
    if last_sep_idx >= 0:
        last_section = prior_context[last_sep_idx + len("\n\n---\n\n"):]
    else:
        last_section = prior_context
    # 跳过 section header 行(【前文 ...】)
    nl_idx = last_section.find("\n")
    if nl_idx >= 0 and last_section.startswith("【"):
        last_section = last_section[nl_idx + 1:]
    # 取尾部
    last_section = last_section.strip()
    if len(last_section) > tail_chars:
        return last_section[-tail_chars:]
    return last_section


def _build_director_user_prompt(
    scene: str, divergence: str, characters: dict[str, dict],
    history: list[dict], round_num: int,
    prior_context: str = "",
    counterfactual_section: str = "",
    affected_scope_section: str = "",
    original_tail: str = "",
    anchor_event_description: str = "",
) -> str:
    """编排单轮 director user prompt。

    Sprint 2.C 新增两块可选 section:
      counterfactual_section: counterfactual_service.render_counterfactual_section_text 输出
                              (用户对图谱节点的具体改动 — 结构化 vs divergence 的自然语言)
      affected_scope_section: BFS 算的"影响范围内角色 id 集合"文案
                              (reshape_percent 第 3 维 — director 应让推演聚焦这些节点)

    Sprint 3.A 末尾态新增:
      original_tail:         项目的原作末尾约 2000 字(simulations.original_tail_excerpt 缓存)
                             非空时插入一段强约束 prior_section,让 director 延续原作末段语气 /
                             物理位置 / 人物状态。与滚雪球的 prior_context 不同(那是浑晶产物;
                             这是原作正文)— 不能混淆铁律措辞。
    """
    char_lines = "\n".join(
        "  - " + _summarize_char_for_director(c) for c in characters.values()
    )

    if history:
        # B2-A(2026-05-27):防 history 数据结构异常导致 KeyError 崩溃 —
        # 历史 timeline 数据结构若被改 / 老 sim 缺字段,旧实现直接抛 KeyError。
        # 改用 .get() + isinstance 防御,缺字段时跳过该轮但不崩,保持 director 能继续。
        hist_lines = []
        for r in history:
            if not isinstance(r, dict):
                continue
            round_num = r.get("round", "?")
            location = r.get("location", "")
            time_advance = r.get("time_advance", "")
            narrator_note = r.get("narrator_note", "")
            hist_lines.append(
                f"\n  Round {round_num} [{location} · {time_advance}]:"
            )
            if narrator_note:
                hist_lines.append(f"    旁白: {narrator_note}")
            events = r.get("events", [])
            if not isinstance(events, list):
                continue
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                speaker = ev.get("speaker", "")
                action = ev.get("action", "")
                dialogue = ev.get("dialogue", "")
                if action:
                    hist_lines.append(f"    {speaker} 动作:{action}")
                if dialogue:
                    hist_lines.append(f"    {speaker} 对白:{dialogue}")
        hist_str = "\n".join(hist_lines)
    else:
        hist_str = "  (这是第 1 轮,反事实刚刚发生,还没有任何后续事件)"

    rules_blocks = []
    for char in characters.values():
        rules = char.get("behavioral_rules") or []
        if not rules:
            continue
        block = [f"\n[{char['name']}]"]
        for rule in rules:
            block.append(f"  - {rule}")
        rules_blocks.append("\n".join(block))

    rules_section = ""
    if rules_blocks:
        rules_section = "\n\n【character_behavioral_rules:每个角色受其世界规矩约束】\n"
        rules_section += (
            "**编排 round_seed 时必须自检:不能让任何角色做违反这些规矩的事**(铁律 8)。\n"
        )
        rules_section += "\n".join(rules_blocks)

    # Sprint 3.A 末尾态:原作末段独立 section(标签 / 铁律措辞与滚雪球 prior_context 区分)
    original_tail_section = ""
    if original_tail:
        original_tail_section = (
            "\n【原作末段(必读 — 这次推演接续这段原文,**不动原作正文**)】\n"
            f"{original_tail}\n\n"
            "**编排时铁律(末尾态)**:\n"
            "  - 这是原作正文片段,**不要复述这段已发生的事**,从此段之后开始推演\n"
            "  - 第 1 轮的 location / time_advance 必须自然承接原作末段的物理状态与时刻\n"
            "  - 人物当前关系 / 情绪 / 站位以末段为准,不要凭空翻转\n"
            "  - 这是末尾续写,**不引入反事实变量**(用户没改原作,只想看接下来)\n"
        )

    prior_section = ""
    if prior_context:
        # 2026-06-02 patch B:加"必须从尾巴接续"强约束 + 显式标注最后一段尾巴
        # 治"AI 续写跑回更早场景 / 重复尾巴桥段"问题
        last_tail_excerpt = _extract_last_tail_excerpt(prior_context)
        last_tail_block = ""
        if last_tail_excerpt:
            last_tail_block = (
                "\n【⚠ 直接父辈 narrative 最后 800 字(本轮叙事必须从此处之后接续)】\n"
                f"……{last_tail_excerpt}\n\n"
            )
        prior_section = (
            "\n【前序产物(必读 — 浑晶在此项目上已生成过以下叙事,本次接续)】\n"
            f"{prior_context}\n"
            f"{last_tail_block}"
            "**编排时铁律(滚雪球续作)**:\n"
            "  ① 第 1 轮的 location / time_advance 必须从**直接父辈 narrative 的最后一句**自然衔接\n"
            "     (角色站在哪、刚做了什么动作、刚说了什么 — 都从尾巴接,不许跳)\n"
            "  ② **绝对禁止**复述任何前序已发生的剧情(包括已演的场景 / 已说的对白 / 已揭的伏笔)\n"
            "  ③ **绝对禁止**回到比「直接父辈 narrative 尾巴」更早的场景或时间(只能往后走)\n"
            "  ④ 角色当前情绪 / 关系 / 物理位置 全部以「直接父辈 narrative 尾巴」为准\n"
            "  ⑤ 编排第 1 轮前默念:「我读了尾巴最后 800 字,我的开场就是从那一秒之后的下一秒开始」\n"
        )

    cf_block = ""
    if counterfactual_section:
        cf_block = "\n" + counterfactual_section + "\n"

    scope_block = ""
    if affected_scope_section:
        scope_block = "\n" + affected_scope_section + "\n"

    # Sprint 6.A2 M7.J(2026-05-20):中间态起点锚点 — 用户在创建 sim 时选了一个原作 event
    # 作为"时间起点",LLM 应以该事件刚结束为起点续推,而非"独立新场景"
    # 与 cf_block(反事实改动)区别:anchor 不改事件,只是声明"在原作哪个时间点开始"
    anchor_block = ""
    if anchor_event_description:
        anchor_block = (
            "\n【起点锚点(必读 — 本次推演以该事件刚结束的时刻为时间起点)】\n"
            f"原作事件:{anchor_event_description}\n\n"
            "**编排时铁律(中间态起点锚点)**:\n"
            "  - 第 1 轮的 location / time_advance 必须自然承接该事件结束的物理位置与时刻\n"
            "  - 在场角色的情绪 / 站位 / 关系应是该事件刚结束的状态\n"
            "  - **不要复述该事件本身已发生的内容**,从此事件之后开始推演\n"
            "  - 此事件**不可被反事实推翻**(锚点 = 已发生的真实);如有反事实,从此事件之后体现\n"
        )

    return f"""【场景设定】
{scene}
{original_tail_section}{prior_section}{anchor_block}
【关键反事实变量 / 接下来想看到的剧情】
{divergence}
{cf_block}{scope_block}
【可用角色(共 {len(characters)} 个 agent)】
{char_lines}
{rules_section}

【迄今已发生(本次推演内)】
{hist_str}

—— 你的任务:编排第 {round_num} 轮 ——
基于上面的状态,产出第 {round_num} 轮的导演方案。

【2026-06-02 治"对话原地打转"铁律 — 必须遵守】
  ① **同一焦点问题全篇 ≤ 3 轮**:如果前 2-3 轮都在围绕同一个对话焦点
     (如"梁淼排第几 / 你心里装多少人 / 你能不能保证 / 撕照片",或类似的反复追问类),
     **本轮必须强行推进**,绝不允许第 4 轮还在同一焦点上转
  ② **推进的合法方式**(任选):
     · 场景切换(从教室走廊 → 操场 / 食堂 / 楼梯间 / 家门口)
     · 新角色登场(室友闯入 / 老师叫去 / 家长来电 / 旁观者插话)
     · 新事件触发(突发消息 / 物件出现 / 时间跳到第二天)
     · 一方主动结束(其中一人摔门走 / 起身离去 / 给出明确答案/拒绝)
  ③ **scene_index 与 round_seed 必须体现推进**:
     · 第 N 轮 location 不能跟第 N-1 轮完全相同(必须有位移或时间跳)
     · round_seed 不能跟 history 里已经出现过的 seed 雷同
  ④ **编排第 {round_num} 轮前,先扫一遍 history**:
     · 数一下"同一焦点问题"出现了几轮 → 已 ≥ 3 轮 → 本轮**必须切**
     · 数一下角色相对位置 → 已 3 轮没动 → 本轮**必须移动**

**编排前最后默念一遍铁律 8 的 4 个禁区 + 对话推进铁律,确认无违反才输出。**
"""


def _validate_director_plan(
    plan: dict, all_agent_ids: set[str]
) -> tuple[dict, list[str]]:
    """对齐 simulate.py validate_director_plan 行为(不打印,只返回 warnings)。"""
    warnings: list[str] = []
    plan = dict(plan)

    present = [a for a in plan.get("present_agents", []) if a in all_agent_ids]
    speaking = [a for a in plan.get("speaking_agents", []) if a in all_agent_ids]

    invalid_present = set(plan.get("present_agents", [])) - set(present)
    invalid_speaking = set(plan.get("speaking_agents", [])) - set(speaking)
    if invalid_present:
        warnings.append(f"present_agents 含未知 id: {invalid_present},已剔除")
    if invalid_speaking:
        warnings.append(f"speaking_agents 含未知 id: {invalid_speaking},已剔除")

    speaking = [a for a in speaking if a in present]
    if not speaking:
        warnings.append("speaking_agents 为空,启用 fallback:取 present 前 2 个")
        speaking = present[:2] if present else []

    plan["present_agents"] = present
    plan["speaking_agents"] = speaking
    for k in ("location", "time_advance", "round_seed", "narrator_note"):
        plan.setdefault(k, "")
    return plan, warnings


def _build_composer_user_prompt(
    timeline: dict, chars: dict[str, dict], target_chars: int,
    prior_context: str = "",
    project_meta: Optional[dict[str, Any]] = None,
    original_tail: str = "",
    counterfactual_section: str = "",
) -> str:
    lines: list[str] = []

    # 2026-06-05 CRITICAL:反事实改动块 — 必须放在最前面,LLM 第一眼看到
    # 用户在反事实工作台改写的角色/事件/关系/世界观,优先级 > 原作 > project_meta
    # 见 composer.md 铁律 0(反事实变量铁律)
    if counterfactual_section:
        lines.append(
            "【⚠ 用户反事实改动(优先级最高 — 必须严格按此写,违反整次作废)】\n"
            f"{counterfactual_section}\n"
            "**铁律**:\n"
            "  - 角色 name / identity / personality 如已在反事实里改写,**严格用改后的值**,不许用原作\n"
            "  - 事件如已在反事实里改写(如「找未果」→「发现被绑架」),**剧情按改后版本写**\n"
            "  - 关系 / 世界观如已改写,叙事中**必须体现**改后设定\n"
            "  - 反事实和 timeline 矛盾时,以反事实为准(timeline 可能是历史 plan,反事实是用户新意图)\n\n"
            "---\n"
        )

    # 项目元信息(Sprint 1.Q 语体自适应)— 供 composer 第一步语体判断用
    # composer.md 的"7 类语体框架"会读这些字段,自动选最贴合的笔法
    if project_meta:
        meta_lines = [
            "【作品元信息(用于语体自评估)】",
            f"  - 作品名:《{project_meta.get('name', '未命名')}》",
            f"  - 类型:{project_meta.get('type_label', '故事')}",
        ]
        tags = project_meta.get("tags") or []
        meta_lines.append(
            f"  - 题材标签:{', '.join(tags) if tags else '(未标注)'}"
        )
        char_names = [c.get("name", "?") for c in chars.values()]
        meta_lines.append(f"  - 主要角色:{' / '.join(char_names)}")
        lines.append("\n".join(meta_lines) + "\n")

    # Sprint 3.A 末尾态:原作末段独立 section(语体强一致铁律)
    if original_tail:
        lines.append(
            "【原作末段(必读 — 这次叙事接续以下原文,**语体 / 笔法 / 用词必须与原作一致**)】\n"
            f"{original_tail}\n\n"
            "**叙事要求(末尾态)**:\n"
            "  - 开篇自然承接原作末段的氛围 / 物理位置 / 人物状态\n"
            "  - **不要复述这段原文**,从此段之后开始叙事\n"
            "  - 模仿原作的句长 / 用词偏好 / 段落节奏 — 这是末尾续写,语体不能跳变\n"
            "  - 人物称谓 / 物件命名 / 礼仪用法须与原作末段一致\n\n"
            "---\n"
        )

    if prior_context:
        # 2026-06-02 patch B:composer 也加"必须从尾巴接续"强约束
        last_tail = _extract_last_tail_excerpt(prior_context)
        last_tail_block = ""
        if last_tail:
            last_tail_block = (
                "\n【⚠ 直接父辈 narrative 最后 800 字(本篇开篇必须从此处之后接续)】\n"
                f"……{last_tail}\n\n"
            )
        lines.append(
            "【前序产物(必读 — 浑晶已在此项目生成过以下叙事,本次接续)】\n"
            f"{prior_context}\n"
            f"{last_tail_block}"
            "**叙事铁律(滚雪球续作)**:\n"
            "  ① 开篇第 1 段必须从**直接父辈 narrative 的最后一句**自然衔接\n"
            "     (角色站在哪、刚做了什么、刚说了什么 — 全部从尾巴接,不许跳)\n"
            "  ② **绝对禁止**复述前序已发生的剧情(已演的场景 / 已说的对白 / 已揭的伏笔)\n"
            "  ③ **绝对禁止**回到比「直接父辈尾巴」更早的场景或时间(只能往前走)\n"
            "  ④ 不要重新介绍人物身份(读者已读过前序)\n"
            "  ⑤ 角色性格 / 关系 / 物件标识必须与前序一致\n\n"
            "---\n"
        )
    lines.append("【参考材料:本次推演 timeline】\n")

    for r in timeline["rounds"]:
        plan = r["director_plan"]
        lines.append(
            f"\n=== 第 {r['round']} 轮 [{plan['location']} · {plan['time_advance']}] ==="
        )
        # 2026-06-02 Patch F:transition_from_prev_scene — 让 composer 看到时空过渡线索
        # 非第 1 轮时,要求 composer 在本场叙事开头**必须写**过渡桥
        transition = plan.get("transition_from_prev_scene", "")
        if transition and r.get("round", 1) > 1:
            lines.append(
                f"⚠ 时空过渡(必读 · 本场开篇必须自然衔接此过渡桥):{transition}"
            )
        if plan.get("narrator_note"):
            lines.append(f"旁白:{plan['narrator_note']}")
        if plan.get("round_seed"):
            lines.append(f"契机:{plan['round_seed']}")
        for ev in r["events"]:
            lines.append(f"\n  - {ev['speaker']}:")
            if ev.get("monologue"):
                lines.append(f"    内心:{ev['monologue']}")
            if ev.get("action"):
                lines.append(f"    动作:{ev['action']}")
            if ev.get("dialogue"):
                lines.append(f"    对白(必须保留原文):“{ev['dialogue']}”")

    rules_blocks = []
    for char in chars.values():
        rules = char.get("behavioral_rules") or []
        if not rules:
            continue
        block = [f"\n[{char['name']}]"]
        for rule in rules:
            block.append(f"  - {rule}")
        rules_blocks.append("\n".join(block))

    if rules_blocks:
        lines.append("\n\n【character_behavioral_rules:每个角色受其世界规矩约束的行为模式】")
        lines.append("叙述时必须严格遵守这些约束。")
        lines.append("如果上面 timeline 中某个 event 违反了角色的 behavioral_rules,")
        lines.append("不要简单复述,要在叙述中用一句话软化(参照 composer prompt 铁律 11),")
        lines.append("例如:'黛玉本不愿夜深出门,只是事关姊妹清白,只得破例同往。'")
        lines.extend(rules_blocks)

    # Sprint 6.A2 M3.D-fix2 v2(2026-05-18,用户拍板"不要硬截断"):
    # 字数控制从"后端硬截断"改为"前置 reshape→字数区间预估 + prompt 明确指引"
    # 用户在 SimulationDock 选 target_chars 时已被前端 clamp 到合理区间(reshape_preview API)
    # composer prompt 给清晰目标 + 接受区间,LLM 知道合理边界,无需事后截断破坏创作完整性
    upper_bound = int(target_chars * 1.10)
    lower_bound = int(target_chars * 0.90)
    lines.append("\n\n【任务】")
    lines.append(
        f"把上面推演重写为小说体连贯叙事。"
        f"\n\n字数约束(用户已按重塑度选定,请尊重创作完整性同时贴近目标):"
        f"\n  目标字数:{target_chars} 字"
        f"\n  接受区间:{lower_bound} - {upper_bound} 字(±10%)"
        f"\n  详略调节:接近上限时主动收尾,不再展开新场景"
        f"\n\n其它铁律:"
        "对白原文必须逐字保留;旁白与契机融化进叙事;"
        "内心独白只挑 3-5 处用'心下暗忖'等手法点出;"
        "**留白率 ≥ 30%(铁律 10)**;"
        "**严格遵守 character_behavioral_rules(铁律 11)**,timeline 与规矩冲突时用叙述软化。"
    )
    return "\n".join(lines)


# ======================================================================
# 创建 + 鉴权 + 状态读写
# ======================================================================

def validate_context_simulation_ids(
    conn: sqlite3.Connection,
    project_id: str,
    user_id: str,
    ids: list[str],
) -> None:
    """滚雪球前文 ids 校验。空列表立即通过。

    要求每个 id:
      - 属于当前用户(防越权引用)
      - 属于同一 project(跨项目世界观不通,串起来无意义)
      - state='done'(只能引用已完成的产物,in-progress / failed 不行)

    任一不满足 → 抛 InvalidContextSimulations,带具体不合法的 ids。
    """
    if not ids:
        return
    if len(ids) > MAX_CONTEXT_SIMULATIONS:
        raise InvalidContextSimulations(
            f"最多接续 {MAX_CONTEXT_SIMULATIONS} 条前文,当前 {len(ids)}",
            invalid_ids=ids[MAX_CONTEXT_SIMULATIONS:],
        )

    placeholders = ",".join(["?"] * len(ids))
    rows = fetch_all(
        conn,
        f"SELECT id, context_simulation_ids FROM simulations "
        f"WHERE id IN ({placeholders}) "
        f"AND user_id=? AND project_id=? AND state='done'",
        tuple(ids) + (user_id, project_id),
    )
    found = {r["id"]: r for r in rows}
    invalid = [sid for sid in ids if sid not in found]
    if invalid:
        raise InvalidContextSimulations(
            f"前文 simulation 不存在 / 不属于当前项目 / 未完成:{invalid}",
            invalid_ids=invalid,
        )

    # 2026-06-01:校验 ids 构成"连续单链"(用户问题 1 + 2 治本)
    # 规则:除了第 1 个(链顶),每个 sim 的 context 必须包含链中的上一个
    # 用户场景:基于 sim2 续写时,正确的 ids 应该是 [sim1, sim2](sim2 继承自 sim1)
    # 不允许:[sim1, sim2_unrelated_branch] 或 [sim3](sim3 跳过 sim2 直接接 sim1 → 信息断裂)
    if len(ids) > 1:
        for i in range(1, len(ids)):
            prev_id = ids[i - 1]
            cur_id = ids[i]
            cur_row = found.get(cur_id)
            if not cur_row:
                continue  # 上面已校验过 invalid
            cur_ctx_raw = cur_row["context_simulation_ids"]
            try:
                cur_ctx_ids = json.loads(cur_ctx_raw) if cur_ctx_raw else []
            except (ValueError, TypeError):
                cur_ctx_ids = []
            # cur_ctx_ids 应该包含 prev_id(或末尾就是 prev_id)
            if not cur_ctx_ids or prev_id not in cur_ctx_ids:
                raise InvalidContextSimulations(
                    f"前文链断裂:第 {i + 1} 篇({cur_id[:8]}…)的 context 不含上一篇({prev_id[:8]}…)— "
                    "前作必须构成单条时序链,不允许跳代或选无关分支",
                    invalid_ids=[cur_id],
                )


def create_simulation(
    conn: sqlite3.Connection,
    project_id: str,
    user_id: str,
    divergence: str,
    reshape_percent: int,
    target_chars: int,
    style: str,
    custom_style_hint: Optional[str] = None,
    context_simulation_ids: Optional[list[str]] = None,
    selected_counterfactual_ids: Optional[list[str]] = None,
    mode: str = "quick",
    use_outline_first: bool = False,
    anchor_event_id: Optional[str] = None,
    with_grand_finale: bool = False,   # P2.A
    narrative_pacing: str = "standard",  # P2.B
    chapter_size_chars: int = 2000,  # P0H.2
    inherited_foreshadow_ids: Optional[list[str]] = None,  # 阶段 3A(2026-06-02)
) -> str:
    """落库一行 simulation,返回 simulation_id。

    rounds_planned 由 reshape_percent 派生(reshape_to_rounds),不再是用户输入。
    custom_style_hint 仅 style='custom' 时使用;其它 style 此字段被忽略并存 NULL。
    context_simulation_ids 为前文 sim ids 列表,**调用方必须先调
    validate_context_simulation_ids 校验过**(router 层做)。

    selected_counterfactual_ids(2.C+):
      - None  → 用全部 active 反事实(向下兼容)
      - []    → 显式纯按原作演,不用任何反事实
      - list  → 只用这个子集

    Sprint 3.A 末尾态(project.mode=='end')分支:
      - 静默强制 selected_counterfactual_ids = [](末尾态不改原作,不应混入反事实)
      - 从项目最近 ready upload 抽末尾 ~2000 字 → 缓存到 simulations.original_tail_excerpt
        run_simulation 时注入 director / composer 的 prior_context 区
      - 若项目无 ready upload → 抛 NoReadyUploadForTailExcerpt(router 转 422)

    Sprint 6.A2 M3.D-fix5(2026-05-19,用户实测末尾态+滚雪球产物两个 banner 都显):
      - 末尾态 **+ context_simulation_ids 非空** 时,**跳过原作末段缓存**
        (tail_excerpt 保持 None)
      - 原因:前续写产物本身已经接过原作末段,新 sim 再注入原作末段会让 LLM
        在"接原作末段" vs "接前续写末段"之间二选一 — 概率倒向前者(出现在 prompt
        前部 + 末尾态铁律语气更强),用户感知"滚雪球功能像摆设"
      - 此时也不再触发 NoReadyUploadForTailExcerpt(因为不需要原作 upload)
      - 详情页"原作末段"banner 因 original_tail_excerpt=None 自动消失

    raise:
      ResourceNotFoundOrForbidden       — project 不属于该用户
      TooFewCharactersForSimulation     — 角色 < 3
      ReshapeCharacterLimitExceeded     — 已改的角色数超 reshape % 上限(2.C)
      NoReadyUploadForTailExcerpt       — 末尾态但项目无 ready upload(3.A)
    """
    project = get_project_or_403(conn, project_id, user_id)
    snapshot = build_characters_snapshot(conn, project_id)

    # P2.B 升级(2026-05-24)— AI 节奏推断懒触发 hook
    # 若 project 还没 inferred_pacing 且用户传的是默认值 'standard',尝试 LLM 推断
    # 取代默认值(用户显式选了非 standard,优先用用户值)。
    # 失败不阻塞:ensure_project_pacing_inferred 任何错误返回 'standard'。
    try:
        from app.services.pacing_inferer import ensure_project_pacing_inferred
        # 若用户传的是默认 standard → 用 AI 推断值覆盖(用户可在 UI 中显式改回 standard)
        # 若用户传的是 slow/fast(明确选择)→ 尊重用户偏好,但仍触发 inferer 缓存便于下次
        inferred = ensure_project_pacing_inferred(conn, project_id)
        if narrative_pacing == "standard" and inferred != "standard":
            narrative_pacing = inferred
            log.info(
                f"create_simulation: using inferred pacing '{inferred}' for project {project_id}"
            )
    except Exception as e:  # noqa: BLE001 — 任何错误都不阻塞续作创建
        log.warning(f"create_simulation: pacing_inferer hook failed: {e}")

    # === Sprint 3.A 末尾态分支 ===
    # 在反事实校验前判定:末尾态本就不应有反事实,先把 selected_counterfactual_ids 清掉
    # 再走后续校验(touched=0,绝不会触发 ReshapeCharacterLimitExceeded)
    tail_excerpt: Optional[str] = None
    if project.mode == "end":
        # 静默清空反事实选择 — 即使前端误传非空,也按"末尾态语义"覆盖
        # (反事实工作台按钮在末尾态前端已隐藏,这里只是防御层)
        selected_counterfactual_ids = []
        # Sprint 6.A2 M3.D-fix5(2026-05-19):末尾态 + 滚雪球时跳过原作末段缓存
        #   有 context_simulation_ids → 前续写产物已"接过"原作末段,新 sim 接前续写末段即可
        #   无 context_simulation_ids → 末尾态首次续写,正常缓存原作末段(老语义)
        is_snowball_continuation = bool(context_simulation_ids)
        if not is_snowball_continuation:
            # 拉末段原文(无 ready upload 直接 raise,这是末尾态前置条件)
            from app.services.upload_service import (
                DEFAULT_TAIL_EXCERPT_CHARS,
                get_project_tail_excerpt,
            )
            tail_excerpt = get_project_tail_excerpt(
                conn, project_id, user_id, max_chars=DEFAULT_TAIL_EXCERPT_CHARS,
            )
        # 注:末尾态+滚雪球时 tail_excerpt 保持 None,后续 prompt/banner 自动跳过

    # === Sprint 2.C 反事实第 1 维校验(2.C+ 改用 selected_ids 子集语义)===
    # 校验"本次推演实际用到的 character 反事实"涉及的去重角色数 ≤ reshape 上限
    # — 用户选少则少校验,选多则多校验(更精准的角色数压力指标)
    from app.services.counterfactual_service import (
        _count_touched_characters,
        reshape_to_max_touched_characters,
        ReshapeCharacterLimitExceeded,
    )
    if selected_counterfactual_ids is None:
        # 老语义:全部 active 算
        touched = _count_touched_characters(conn, project_id)
    elif len(selected_counterfactual_ids) == 0:
        touched = 0   # 显式 0 选 — 不算反事实压力(末尾态也走这里)
    else:
        # 子集:从 selected_ids 中筛 character 类型并去重 target_id
        placeholders = ",".join("?" for _ in selected_counterfactual_ids)
        rows = fetch_all(
            conn,
            f"SELECT DISTINCT target_id FROM counterfactual_changes "
            f"WHERE project_id=? AND target_type='character' AND reverted_at IS NULL "
            f"AND id IN ({placeholders})",
            (project_id, *selected_counterfactual_ids),
        )
        touched = len(rows)
    limit = reshape_to_max_touched_characters(reshape_percent)
    if touched > limit:
        raise ReshapeCharacterLimitExceeded(
            current=touched, limit=limit, reshape_percent=reshape_percent,
        )

    rounds_planned = reshape_to_rounds(reshape_percent)
    # 非 custom 风格时丢弃 hint,保持数据干净
    persisted_hint = custom_style_hint if style == "custom" else None
    ctx_ids = context_simulation_ids or []
    ctx_json: Optional[str] = (
        json.dumps(ctx_ids, ensure_ascii=False) if ctx_ids else None
    )

    # Sprint 6.A2 M3.B(2026-05-18):mode 字段守护(防非法值)
    safe_mode = mode if mode in ("quick", "evolution") else "quick"

    # Sprint 6.A2 M6(2026-05-20):outline-first 只有 evolution 模式才有意义
    # quick 模式走单 LLM,outline 无用武之地
    safe_use_outline_first = 1 if (use_outline_first and safe_mode == "evolution") else 0

    # Sprint 6.A2 M7.J(2026-05-20):起点锚点事件校验
    # - 仅 mode=middle 接受;end/initial 静默清空
    # - 必须属于本 project 的 events 表;不属于 → 422 InvalidAnchorEvent
    safe_anchor_event_id: Optional[str] = None
    if anchor_event_id and project.mode == "middle":
        anchor_row = fetch_one(
            conn,
            "SELECT id FROM events WHERE id=? AND project_id=?",
            (anchor_event_id, project_id),
        )
        if anchor_row is None:
            raise InvalidAnchorEvent(
                f"起点锚点事件 {anchor_event_id} 不存在或不属于本项目"
            )
        safe_anchor_event_id = anchor_event_id

    # P2.B 守护:narrative_pacing 必须是 3 个合法档之一
    safe_pacing = (
        narrative_pacing
        if narrative_pacing in ("slow", "standard", "fast")
        else "standard"
    )

    # P0H.2 守护:1000 ≤ chapter_size_chars ≤ 10000
    safe_chapter_size = chapter_size_chars
    if not isinstance(safe_chapter_size, int) or safe_chapter_size < 1000 or safe_chapter_size > 10000:
        safe_chapter_size = 2000

    sim_id = str(uuid.uuid4())
    now = iso_now()
    execute(
        conn,
        "INSERT INTO simulations "
        "(id, project_id, user_id, divergence, reshape_percent, rounds_planned, "
        " target_chars, style, custom_style_hint, context_simulation_ids, "
        " characters_snapshot, original_tail_excerpt, mode, use_outline_first, "
        " anchor_event_id, with_grand_finale, narrative_pacing, chapter_size_chars, "
        " state, current_round, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?)",
        (
            sim_id, project_id, user_id, divergence, reshape_percent,
            rounds_planned, target_chars, style, persisted_hint, ctx_json,
            json.dumps(snapshot, ensure_ascii=False), tail_excerpt,
            safe_mode, safe_use_outline_first, safe_anchor_event_id,
            1 if with_grand_finale else 0,  # P2.A
            safe_pacing,                     # P2.B
            safe_chapter_size,               # P0H.2
            now,
        ),
    )
    conn.commit()

    # 阶段 3A(2026-06-02):用户主动选的伏笔 ids 持久化(migration 081)
    # None = 不存(向后兼容默认行为 — outline_generator 拉所有 open)
    # 显式 [...] 或 [] = 存 JSON,outline_generator 按此过滤
    if inherited_foreshadow_ids is not None:
        try:
            execute(
                conn,
                "UPDATE simulations SET inherited_foreshadow_ids_json=? WHERE id=?",
                (json.dumps(inherited_foreshadow_ids, ensure_ascii=False), sim_id),
            )
            conn.commit()
        except sqlite3.OperationalError as e:
            # 老库无此列(migration 081 未跑)→ 静默降级
            log.warning(
                f"create_simulation: persist inherited_foreshadow_ids failed for sim {sim_id}: {e}"
            )

    # 2026-06-01:章节体系 — 创建后立刻锁定起始章号
    # 沿 context_simulation_ids 累加前篇章节,写 start_chapter_locked
    # 失败 → log + 不阻塞(老路径仍能跑,只是章号降级为 1)
    try:
        from app.services.sim_chapter_helper import compute_start_chapter_for_sim
        new_sim = get_simulation_or_404(conn, sim_id, user_id)
        locked_chap = compute_start_chapter_for_sim(conn, new_sim)
        execute(
            conn,
            "UPDATE simulations SET start_chapter_locked=? WHERE id=?",
            (locked_chap, sim_id),
        )
        conn.commit()
    except Exception as e:  # noqa: BLE001
        # 不影响平台创作质量:静默降级,不抛
        import logging as _logging
        _logging.getLogger(__name__).warning(
            f"create_simulation: failed to lock start_chapter for sim {sim_id}: {e}"
        )

    # 2.C+ trace + link:把"用户选定的反事实子集"关联到该 simulation
    # link 表用作 run_simulation 时取子集来编译 director context;
    # mark_applied 仅 trace 用(append simulation_id 到反事实的 applied_in_simulations_json)
    from app.services.counterfactual_service import (
        link_simulation_to_counterfactuals,
        mark_applied_in_simulation,
    )
    if selected_counterfactual_ids:
        link_simulation_to_counterfactuals(conn, sim_id, selected_counterfactual_ids)
    mark_applied_in_simulation(conn, project_id, sim_id, selected_counterfactual_ids)

    return sim_id


def get_simulation_or_404(
    conn: sqlite3.Connection, sim_id: str, user_id: str
) -> Simulation:
    row = fetch_one(
        conn,
        "SELECT * FROM simulations WHERE id=? AND user_id=?",
        (sim_id, user_id),
    )
    if not row:
        raise ResourceNotFoundOrForbidden("simulation", sim_id)
    return Simulation.from_row(row)


def list_simulations_for_project(
    conn: sqlite3.Connection, project_id: str, user_id: str
) -> list[Simulation]:
    """按创建时间降序(新的在前)。"""
    get_project_or_403(conn, project_id, user_id)
    rows = fetch_all(
        conn,
        "SELECT * FROM simulations WHERE project_id=? "
        "ORDER BY created_at DESC",
        (project_id,),
    )
    return [Simulation.from_row(r) for r in rows]


def list_simulations_for_user(
    conn: sqlite3.Connection, user_id: str
) -> list[tuple[Simulation, str]]:
    """跨项目列出当前用户所有推演 + 关联 project name(Sprint 1.J 我的剧情线)。

    返回 list[(Simulation, project_name)],按 created_at DESC。
    用 JOIN projects 一次拿 project name(避免前端 N+1 查询)。
    """
    rows = fetch_all(
        conn,
        "SELECT s.*, p.name AS project_name "
        "FROM simulations s "
        "JOIN projects p ON p.id = s.project_id "
        "WHERE s.user_id=? "
        "ORDER BY s.created_at DESC",
        (user_id,),
    )
    return [(Simulation.from_row(r), r["project_name"]) for r in rows]


# ============================================================
# Sprint 6.A2 M7.D(2026-05-20)— 接续继承链计算
# ============================================================

def compute_inheritance_metadata_for_user(
    sims: list[Simulation] | list[tuple[Simulation, str]],
) -> dict[str, dict]:
    """对一组 sim 一次性计算继承深度 + 祖先链。

    入参:list[Simulation] 或 list[(Simulation, project_name)]
      接受两种形态:跨项目列表(list_simulations_for_user)+ 单项目列表
      (list_simulations_for_project)。project_name 在算法里不需要,
      入参形态由调用方决定。

    返回:`dict[sim_id, {depth: int, ancestors_chain: list[dict]}]`
      - depth = 该 sim 在继承链中的代数(0 = 独立推演 / 原作根节点)
      - ancestors_chain = 不含本 sim 的祖先链,从原作 depth=0 → 父辈 depth=N-1
        每节点 = {id, divergence_short(≤ 40 字), depth}

    实现:内存里构建 id → sim map,递归 + memo 算每个 sim 的祖先链。
    时间 O(N + total_chain_length),空间 O(N)。

    多 parent 兜底:若 context_simulation_ids 有多个(滚雪球祖先链锁定后,数组按时序排),
    取**最后一个**作直接父辈(数组尾 = 最近祖先 = 直接父).
    缺失 parent 兜底:若引用的 parent sim 不在入参 sim 集合内(已删 / 跨项目),
    链中断到此为止(占位"(前作已删除)").

    2026-06-02 hotfix:原实现取 parents[0](误以为"第一个 parent 是主链"),
    实际 context_simulation_ids 按 created_at ASC 排,数组首=最远祖先 / 尾=直接父辈.
    此 bug 在 task #20 加"祖先链自动展开"后暴露:用户的 [11:43, 11:45] 链被算成"直接接 11:43".
    """
    # 标准化 — 把 (sim, name) 转成纯 sim
    if sims and isinstance(sims[0], tuple):
        normalized: list[Simulation] = [s for s, _ in sims]  # type: ignore[misc]
    else:
        normalized = sims  # type: ignore[assignment]

    id_map: dict[str, Simulation] = {sim.id: sim for sim in normalized}

    # memo:sim_id → ancestors_chain(不含自己)
    ancestors_cache: dict[str, list[dict]] = {}
    in_progress: set[str] = set()    # 防环

    def _compute_ancestors(sim_id: str) -> list[dict]:
        if sim_id in ancestors_cache:
            return ancestors_cache[sim_id]
        if sim_id in in_progress:
            # 环 — 不应该发生(context_simulation_ids 是 DAG),但兜底
            return []
        in_progress.add(sim_id)
        try:
            sim = id_map.get(sim_id)
            if sim is None:
                # 该 sim 不在 id_map 内(已删 / 跨项目)
                ancestors_cache[sim_id] = []
                return []
            parents = sim.context_simulation_ids or []
            if not parents:
                # 独立推演 / 原作根 — 无祖先
                ancestors_cache[sim_id] = []
                return []
            # 2026-06-02 hotfix:取**最后一个**作直接父辈
            # context_simulation_ids 按 created_at ASC 排(老在前 / 新在后),
            # 数组尾部 = 最近祖先 = 真正的直接父.
            # 原实现取 parents[0] 会把"祖先链锁定"传入的数组首部(最远祖先)误认为直接父
            # → inheritance_depth 算少 1 + 祖先链断层 + 后端读 context 时方向反向
            parent_id = parents[-1]
            parent_sim = id_map.get(parent_id)
            if parent_sim is None:
                # parent 不在入参 sim 集合内 — 链断在这里,自己 depth=1 但祖先链为占位
                placeholder_node = {
                    "id": parent_id,
                    "divergence_short": "(前作已删除)",
                    "depth": 0,
                }
                ancestors_cache[sim_id] = [placeholder_node]
                return [placeholder_node]
            # 递归算 parent 的祖先 → 拼上 parent 自己
            parent_ancestors = _compute_ancestors(parent_id)
            parent_node = {
                "id": parent_sim.id,
                "divergence_short": (parent_sim.divergence or "").strip()[:40],
                "depth": len(parent_ancestors),
            }
            chain = parent_ancestors + [parent_node]
            ancestors_cache[sim_id] = chain
            return chain
        finally:
            in_progress.discard(sim_id)

    result: dict[str, dict] = {}
    for sim_id in id_map:
        ancestors = _compute_ancestors(sim_id)
        result[sim_id] = {
            "depth": len(ancestors),     # 本 sim 的 depth = 祖先数
            "ancestors_chain": ancestors,
        }
    return result


def delete_simulation(
    conn: sqlite3.Connection, sim_id: str, user_id: str
) -> None:
    """带鉴权 → 删除。状态不限,正在跑的也允许删(后台任务感知不到 → 自然 dangling)。"""
    sim = get_simulation_or_404(conn, sim_id, user_id)
    execute(conn, "DELETE FROM simulations WHERE id=?", (sim.id,))
    conn.commit()


def resume_simulation(
    conn: sqlite3.Connection, sim_id: str, user_id: str
) -> None:
    """断点续推 — 让 failed / 中断的 sim 接着第 N+1 轮跑(Sprint 1.P)。

    raise:
      ResourceNotFoundOrForbidden  — sim 不属于该用户
      SimulationStillRunning       — sim 当前正在被 worker 跑(并发锁阻拦)
      SimulationNotResumable       — done(无需恢复)/ queued(刚创建未跑过)/ 无进度(timeline=0)

    state machine:
      失败前:state ∈ {failed, cancelled, directing 僵尸, composing 僵尸}
      reset → state='queued' + error_message=NULL → kick_off
      新 worker 拿 sim 看到 timeline 已有 N 轮,自动从 N+1 开始(_run_simulation_inner 内续推支持)

    不扣额外 continuation 配额 — 原 sim 创建时已扣过。
    LLM 新调用产生的 cost 累积进 sim.cost_yuan(已存 token 也保留)。
    """
    sim = get_simulation_or_404(conn, sim_id, user_id)

    # 1. 并发锁:不能 resume 当前真在跑的 sim
    with _RUNNING_LOCK:
        if sim_id in _RUNNING_SIMS:
            raise SimulationStillRunning(
                f"sim {sim_id} 正在运行中,无法 resume"
            )

    # 2. 状态可恢复性
    if sim.state == "done":
        raise SimulationNotResumable("sim 已完成,无需恢复")
    if sim.state == "queued":
        raise SimulationNotResumable(
            "sim 处于排队状态,等 worker 启动即可,无需 resume"
        )
    # state ∈ {failed, cancelled, directing 僵尸, composing 僵尸} 都可恢复

    # 3. 必须有进度(0 轮的就该重新发起新 sim)
    timeline_rounds = (sim.timeline or {}).get("rounds") or []
    if len(timeline_rounds) == 0:
        raise SimulationNotResumable(
            "sim 无任何已完成轮次,请重新创建推演"
        )

    # 4. reset state + 清错误 + kick_off
    _update_state(
        conn, sim_id,
        state="queued",
        error_message=None,
        completed_at=None,   # 之前 failed 写过 completed_at,清回去
    )
    log.info(
        "resume sim %s:已有 %d / %d 轮,kick_off 续跑",
        sim_id, len(timeline_rounds), sim.rounds_planned,
    )
    kick_off(sim_id)


# ======================================================================
# 同步 runner — 主循环 + 状态机推进 + 错误兜底
# ======================================================================

def _update_state(
    conn: sqlite3.Connection, sim_id: str, **fields: Any
) -> None:
    """通用状态字段更新。"""
    if not fields:
        return
    parts = ", ".join(f"{k}=?" for k in fields)
    values = list(fields.values()) + [sim_id]
    execute(conn, f"UPDATE simulations SET {parts} WHERE id=?", tuple(values))
    conn.commit()


def _set_started_at_if_null(conn: sqlite3.Connection, sim_id: str) -> None:
    """只在 started_at 为 NULL 时设当前时间(SQL 层兜底,治 Bug 1 elapsed 0s)。

    历史 bug:`_update_state(started_at=sim.started_at or iso_now())` 依赖
    Python 对象的 sim.started_at 字段。如果 sim 对象是某次过时快照(SSE 重连/
    retry 路径),started_at 字段在内存中是 None,OR 兜底会把它重置为当前时间。
    后果:前端 elapsed = _nowTick - started_at = (现在 - 现在) ≈ 0s,
    用户看到"已运行 0s",计时归零。

    修复:不传 started_at 给 _update_state,改为本函数 SQL 层 COALESCE —
    `UPDATE ... SET started_at = ? WHERE id = ? AND started_at IS NULL`,
    DB 里已有值时 WHERE 不命中,绝不覆盖。
    """
    execute(
        conn,
        "UPDATE simulations SET started_at = ? WHERE id = ? AND started_at IS NULL",
        (iso_now(), sim_id),
    )
    conn.commit()


# ======================================================================
# 滚雪球续写:前文 narratives 收集 + 摘要化(Sprint 1.O)
# ======================================================================

def _generate_summary(narrative: str) -> tuple[str, dict]:
    """单次 LLM 调用,把 ~4000 字 narrative 压缩到 ~800 字摘要。

    返回 (summary, usage_dict)。caller 决定怎么计费 / 缓存。
    """
    system_prompt = (
        "你是中文小说编辑。任务:把下面这段叙事压缩成一段 800 字以内的章节梗概。\n"
        "\n"
        "要求:\n"
        "1. 保留所有关键人物名 + 核心情节 + 重要对白(可适度精简但要有原文感)\n"
        "2. 保留情绪走向(场景氛围、人物内心转折)\n"
        "3. 不要添加原文没有的内容\n"
        "4. 不要给章节标题,直接出梗概正文\n"
        "5. 字数硬指标:600-1000 字之间,超过会被截断"
    )
    summary, usage = call_llm_text(
        system_prompt, narrative,
        max_tokens=1500, temperature=0.3,
    )
    # 兜底截断,防 LLM 不听话
    cleaned = summary.strip()[:1200]
    return cleaned, usage


def _gather_prior_narratives(
    conn: sqlite3.Connection,
    sim: Simulation,
) -> tuple[str, int, int, float]:
    """收集前文 narratives 拼成 prior_context 字符串.

    返回 (prior_context_text, tokens_in_used, tokens_out_used, cost_yuan_used).
    后三项是这次摘要化(若发生)消耗的 LLM 资源,叠加到 sim 的总 cost.

    2026-06-02 patch C 升级 — "近全 + 远摘"双层(对齐灵魂续写的 chain_context_builder):
      - 用户在 context_simulation_ids 显式选了 N 条前作 → 按时序处理
      - 最近 RECENT_GENERATIONS_FULL_TEXT(=2)条 → 取 narrative **尾部** 4000 字 verbatim
        (用户最关心的连贯性区域:对白/微表情/伏笔/情绪走向都在这里)
      - 更远的 → 用 800 字 narrative_summary(LLM 摘要,缓存到 DB 避免重复生成)
      - 总预算 MAX_TOTAL_ANCESTOR_CHARS(=11000),超过则跳过最远祖

    治本:旧 8000 字一刀切阈值会让"4 篇短文"也降级到 800 字摘要,长篇细节全丢 → 剧情错乱.
    新策略:即使长链,直接父+祖父辈仍然 verbatim,LLM 能读到具体场景接续.

    emit:
      summarizing_start (前文 i / N)
      summarizing_done  (前文 i / N)
    """
    if not sim.context_simulation_ids:
        return "", 0, 0, 0.0

    # 拉前文 sim 行(同事务连接)
    placeholders = ",".join(["?"] * len(sim.context_simulation_ids))
    rows = fetch_all(
        conn,
        f"SELECT * FROM simulations WHERE id IN ({placeholders}) "
        f"AND user_id=? AND state='done' "
        f"ORDER BY created_at ASC",
        tuple(sim.context_simulation_ids) + (sim.user_id,),
    )
    prior_sims = [Simulation.from_row(r) for r in rows]
    if not prior_sims:
        return "", 0, 0, 0.0

    # 复用 chain_context_builder 的常量(单一来源,跨 quick/evolution 一致)
    from app.services.chain_context_builder import (
        RECENT_GENERATIONS_FULL_TEXT,
        RECENT_FULL_TEXT_TAIL_CHARS,
        DISTANT_SUMMARY_CHARS_PER_GEN,
        MAX_TOTAL_ANCESTOR_CHARS,
    )

    # prior_sims 按 created_at ASC(老在前),续作角度看 "最后一条就是直接父辈"
    # 倒过来索引:idx_from_end=1 是最近 / 直接父,=2 是祖父,...
    sections: list[str] = []
    total_in = total_out = 0
    total_cost = 0.0
    total_used_chars = 0

    n = len(prior_sims)
    for i, ps in enumerate(prior_sims, start=1):
        idx_from_end = n - i + 1   # 1 = 直接父
        is_recent = idx_from_end <= RECENT_GENERATIONS_FULL_TEXT
        narrative = ps.narrative or ""

        # 2026-06-02 hotfix Y5:完全空 narrative 的前作直接跳过,
        # 避免:① 给 LLM 喂空摘要 ② 浪费一个 section 标题给 LLM 分心
        if not narrative.strip() and not (ps.narrative_summary or "").strip():
            log.info(
                "sim %s 前文 %s narrative+summary 都空,跳过",
                sim.id, ps.id,
            )
            continue

        if is_recent and narrative:
            # 近代:取尾部 4000 字 verbatim
            full = narrative.strip()
            if len(full) > RECENT_FULL_TEXT_TAIL_CHARS:
                body = "…(开头省略)…\n" + full[-RECENT_FULL_TEXT_TAIL_CHARS:]
            else:
                body = full
            kind_label = "全文尾部 · 重点参考"
        else:
            # 远代:用摘要(命中缓存则免费,否则跑 LLM 缓存)
            if ps.narrative_summary:
                body = (ps.narrative_summary or "")[:DISTANT_SUMMARY_CHARS_PER_GEN]
            else:
                _emit_event(sim.id, {
                    "kind": "summarizing_start",
                    "prior_index": i,
                    "prior_total": n,
                })
                t0 = time.perf_counter()
                try:
                    body, usage = _generate_summary(narrative)
                except Exception as e:  # noqa: BLE001
                    log.warning(
                        "前文 %s 摘要失败,降级硬截断:%s", ps.id, e,
                    )
                    body = narrative[:1000]
                    usage = {"input_tokens": 0, "output_tokens": 0}
                ms = int((time.perf_counter() - t0) * 1000)
                try:
                    _update_state(conn, ps.id, narrative_summary=body)
                except Exception as e:  # noqa: BLE001
                    log.warning("缓存摘要失败但不阻塞:%s", e)
                total_in += usage["input_tokens"]
                total_out += usage["output_tokens"]
                cost = estimate_cost_yuan(
                    usage["input_tokens"], usage["output_tokens"],
                )
                total_cost += cost
                _emit_event(sim.id, {
                    "kind": "summarizing_done",
                    "prior_index": i,
                    "prior_total": n,
                    "duration_ms": ms,
                    "tokens_input": usage["input_tokens"],
                    "tokens_output": usage["output_tokens"],
                    "summary_chars": len(body),
                })
            kind_label = "摘要(节省 token)"

        # 预算守护:超额则不再加远祖,本段也跳过
        section_text = (
            f"【前文 {i}/{n}(直系第 {idx_from_end} 代上溯 · {kind_label})· "
            f"锚点:{ps.divergence}】\n{body}"
        )
        if total_used_chars + len(section_text) > MAX_TOTAL_ANCESTOR_CHARS:
            log.info(
                "sim %s prior_context 已达预算 %d,跳过更远祖辈",
                sim.id, MAX_TOTAL_ANCESTOR_CHARS,
            )
            break
        sections.append(section_text)
        total_used_chars += len(section_text)

    # 按"原作 → 父辈"自然语序(LLM 阅读顺序):时序 ASC 已经是这样
    prior_context = "\n\n---\n\n".join(sections)
    return prior_context, total_in, total_out, total_cost


def run_simulation(sim_id: str) -> None:
    """同步执行整个推演 — 生产由 _default_kick_off 在线程跑,测试直接调。

    任何阶段抛异常 → 状态置 failed + emit error 事件,error_message 落库。
    DB 连接每次新开(因为可能跨线程)。

    并发保护(Sprint 1.P):同一 sim_id 不能同时被两个 worker 跑。
    入口加锁登记到 _RUNNING_SIMS;若已存在 → log + 直接 return,
    防止 double kick_off / resume 撕裂数据。
    """
    with _RUNNING_LOCK:
        if sim_id in _RUNNING_SIMS:
            log.warning(
                "sim %s 已在运行,跳过重复 kick_off(可能 resume 或并发触发)",
                sim_id,
            )
            return
        _RUNNING_SIMS.add(sim_id)

    # 2026-06-02 hotfix R5:conn = get_connection() 移进 try 内,
    # 防 get_connection 抛错时 finally 被跳过 → _RUNNING_SIMS 永卡
    conn = None
    try:
        conn = get_connection()
        # Sprint 6.A2 M3.B(2026-05-18):按 sim.mode 路由
        # 'quick'     → 老路径 _run_simulation_inner(单 LLM 编排 director-agents-composer)
        # 'evolution' → 新路径 agent_evolution_engine.run_evolution_simulation
        #              (真多 agent 独立 LLM 进程 + 私有记忆 + reflection + 多轮 + narrator)
        row = fetch_one(conn, "SELECT mode FROM simulations WHERE id=?", (sim_id,))
        sim_mode = row["mode"] if row else "quick"

        if sim_mode == "evolution":
            from app.services.agent_evolution_engine import run_evolution_simulation
            # 注:run_evolution_simulation 自己管 conn(open/close + try/except),
            # 这里不复用 conn(免双重 try/except 冲突)。先关 conn 防 SQLite 多线程问题。
            conn.close()
            conn = None
            run_evolution_simulation(sim_id)
        else:
            _run_simulation_inner(conn, sim_id)
    except Exception as e:  # noqa: BLE001 — 顶层兜底必须广捕
        log.exception("simulation %s 失败", sim_id)
        err_msg = f"{type(e).__name__}: {e}"[:500]
        try:
            # conn 可能在 evolution 路径下已被关闭,重新拿一个
            err_conn = conn or get_connection()
            _update_state(
                err_conn, sim_id,
                state="failed",
                error_message=err_msg,
                completed_at=iso_now(),
            )
            if conn is None:
                err_conn.close()
        except Exception:
            log.exception("simulation %s 失败状态写入又失败", sim_id)
        # emit error 让 SSE 订阅者立即知道(DB 更新可能晚 1ms)
        _emit_event(sim_id, {"kind": "error", "message": err_msg})
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
        with _RUNNING_LOCK:
            _RUNNING_SIMS.discard(sim_id)


def _run_simulation_inner(conn: sqlite3.Connection, sim_id: str) -> None:
    """实际跑推演主循环。异常不在这里处理,交给外层 run_simulation。

    支持**断点续推**(Sprint 1.P):
      - 入口读 sim 已存 timeline,推断 start_round = 已完成轮 + 1
      - 从 existing rounds 还原 director_history + flat_history(给 LLM 上下文)
      - token / cost 累积接续 sim.tokens_input/output(不归零)
      - start_round > rounds_planned → 跳过 loop,直接 composer
        (覆盖"所有轮跑完仅 composer 失败"的恢复场景)
    """
    # 拉取行(不走 get_simulation_or_404,因为 worker 跨用户上下文)
    row = fetch_one(conn, "SELECT * FROM simulations WHERE id=?", (sim_id,))
    if not row:
        log.warning("simulation %s 已被删除,worker 退出", sim_id)
        return
    sim = Simulation.from_row(row)

    if sim.state == "done":
        log.info("simulation %s 已 done,worker 跳过", sim_id)
        return
    if sim.state in ("failed", "cancelled"):
        # 不应到这里 — resume_simulation 已将 state 改回 queued
        log.warning(
            "simulation %s 状态为 %s 但被 kick_off,跳过(应走 resume 端点)",
            sim_id, sim.state,
        )
        return

    # === 滚雪球前文收集(Sprint 1.O)===
    # 在 directing 之前做,因为可能要跑摘要 LLM(成本计入本次 sim)
    prior_context, ctx_in, ctx_out, ctx_cost = _gather_prior_narratives(conn, sim)
    if prior_context:
        log.info(
            "sim %s 接续 %d 条前文,context %d 字",
            sim_id, len(sim.context_simulation_ids), len(prior_context),
        )

    # Sprint 6.A2 M7.J(2026-05-20):中间态起点锚点 — 拉 anchor_event_id 对应的 description
    # 加进 director user prompt 的「起点锚点」段;事件被删时 ON DELETE SET NULL 已确保 NULL
    anchor_event_description = ""
    if sim.anchor_event_id:
        anchor_row = fetch_one(
            conn,
            "SELECT description FROM events WHERE id=?",
            (sim.anchor_event_id,),
        )
        if anchor_row and anchor_row["description"]:
            anchor_event_description = anchor_row["description"]

    # === 续推支持(Sprint 1.P):从已存 timeline 还原 ===
    existing_rounds: list[dict] = []
    if sim.timeline and isinstance(sim.timeline.get("rounds"), list):
        existing_rounds = list(sim.timeline["rounds"])
    is_resume = len(existing_rounds) > 0
    start_round = len(existing_rounds) + 1
    if is_resume:
        log.info(
            "sim %s 续推:已有 %d 轮,从第 %d 轮接着跑",
            sim_id, len(existing_rounds), start_round,
        )

    snapshot = sim.characters_snapshot
    chars_by_id = {c["id"]: c for c in snapshot}
    scene = _build_scene_from_project(conn, sim.project_id, snapshot)

    director_system_prompt = _load_prompt("director_system.md")
    timeline_rounds: list[dict] = list(existing_rounds)
    flat_history: list[dict] = []
    director_history: list[dict] = []

    # === Sprint 2.C/2.C+ 反事实变量 + 第 3 维 编译为 director prompt 上下文 ===
    # 2.C+ 改:用 selected_counterfactual_ids 子集(用户在 Dock 勾选的)编译;
    #         若该 sim 没 link 任何反事实(get_linked 返 None),回落到全部 active 语义
    from app.services.counterfactual_service import (
        build_director_context,
        compute_affected_node_ids,
        get_active_target_ids,
        get_linked_counterfactual_ids,
        render_counterfactual_section_text,
        reshape_to_graph_distance_hops,
    )
    selected_ids = get_linked_counterfactual_ids(conn, sim_id)
    cf_context = build_director_context(conn, sim.project_id, selected_ids)
    cf_section_text = render_counterfactual_section_text(cf_context)

    # ============================================================
    # 2026-06-05 CRITICAL BUG FIX:把反事实改动 apply 到 chars_by_id / snapshot
    # ============================================================
    # 老 bug:build_characters_snapshot 直接从 DB 拉 name/identity/personality 原作字段,
    #         director 虽然有 cf_section_text 但只输出短 plan,composer 拿 chars(原作)
    #         写 narrative → 妹妹 name 还是"菲芘"(应是"思颖"),性格还是"聪明"(应是"丑陋邪恶")。
    # 修复:在所有下游消费 snapshot 之前,显式把反事实改动 apply 到内存中的 chars。
    #       director / agent / composer / 旁支函数都不需要改签名 — 它们看到的就是改后的字段。
    # 同 outline_generator.create_outline_draft 的 Layer A 一致(确保两套数据视图都生效)。
    if cf_context["active_count"] > 0:
        _cf_char_overrides: dict[str, dict[str, str]] = {}
        for _it in cf_context["items"]:
            if _it["target_type"] != "character":
                continue
            _tid = _it["target_id"]
            _fld = _it["field"]
            _new_val = _it["to"]
            if not _tid or not _fld:
                continue
            _cf_char_overrides.setdefault(_tid, {})[_fld] = _new_val
        if _cf_char_overrides:
            for _cid, _char_dict in chars_by_id.items():
                _overrides = _cf_char_overrides.get(_cid)
                if not _overrides:
                    continue
                # 仅覆盖 narrative 编排关心的 5 个字段
                for _k in ("name", "identity", "personality"):
                    if _k in _overrides:
                        _char_dict[_k] = _overrides[_k]
                if "quotes" in _overrides:
                    # quotes 在 DB 里是 list,反事实存的是 str(用户在工作台填) — 兼容
                    _raw = _overrides["quotes"]
                    if isinstance(_raw, str):
                        _char_dict.setdefault("voice_fingerprint", {})["quotes"] = [_raw]
                    elif isinstance(_raw, list):
                        _char_dict.setdefault("voice_fingerprint", {})["quotes"] = _raw
                if "no_go_list" in _overrides:
                    _raw = _overrides["no_go_list"]
                    if isinstance(_raw, str):
                        _char_dict["no_go_list"] = [_raw]
                    elif isinstance(_raw, list):
                        _char_dict["no_go_list"] = _raw
                # 标记给 LLM 看(prompt 可强调"该角色被用户反事实改写过")
                _char_dict["_counterfactual_applied"] = True
            log.info(
                "sim %s 反事实改写已 apply 到 %d 个角色 snapshot",
                sim_id, len(_cf_char_overrides),
            )

        # 2026-06-05 Layer C:event 反事实拼进 sim.divergence(顶级强信号位)
        # 同 outline_generator.create_outline_draft 一致 — divergence 在 director_user 中
        # 位置靠前,LLM 必定遵守。把 event 改写指令前置,避免 LLM 偷按原作走。
        _evt_items_for_div = [it for it in cf_context["items"] if it["target_type"] == "event"]
        if _evt_items_for_div:
            _div_raw = sim.divergence or ""
            _evt_block = ["", "[⚠ 用户事件改写 — 必须按改后版本编排,绝对禁止用原作版本]"]
            for _it in _evt_items_for_div:
                _evt_block.append(f"- 原作版本:{_it['from']}")
                _evt_block.append(
                    f"  改后版本:{_it['to']}  ← director plan / 各幕 events 必须按此写"
                )
                if _it.get("user_intent"):
                    _evt_block.append(f"  ★ 用户意图:{_it['user_intent']}")
            # sim 是普通 dataclass,直接 in-memory 改 divergence(不影响 DB)
            sim.divergence = _div_raw + "\n".join(_evt_block)
            log.info(
                "sim %s 反事实 event 改写已合并到 divergence(%d 条事件)",
                sim_id, len(_evt_items_for_div),
            )

    affected_section_text = ""
    if cf_context["active_count"] > 0:
        # 2.C+ BFS seed 改用 cf_context.items 子集(已按 selected_ids 过滤)
        # 这样 affected scope 与"本次推演实际用到的反事实"对齐
        seed: set[str] = set()
        rel_ids_in_subset: list[str] = []
        for item in cf_context["items"]:
            t = item["target_type"]
            if t in ("character", "event"):
                seed.add(item["target_id"])
            elif t == "relationship":
                rel_ids_in_subset.append(item["target_id"])
            # world 类型无 BFS seed(它是全局,不绑定具体节点)
        if rel_ids_in_subset:
            placeholders = ",".join("?" for _ in rel_ids_in_subset)
            for r in fetch_all(
                conn,
                f"SELECT source_id, target_id FROM relationships WHERE id IN ({placeholders})",
                tuple(rel_ids_in_subset),
            ):
                if r["source_id"]:
                    seed.add(r["source_id"])
                if r["target_id"]:
                    seed.add(r["target_id"])
        hops = reshape_to_graph_distance_hops(sim.reshape_percent)
        affected_ids = compute_affected_node_ids(conn, sim.project_id, seed, hops)
        # 仅显示 character 的名字(event 节点 director 不直接调度)
        affected_char_names = [
            chars_by_id[cid]["name"] for cid in affected_ids
            if cid in chars_by_id
        ]
        if affected_char_names:
            affected_section_text = (
                f"## 影响范围(reshape {sim.reshape_percent}% → BFS {hops} 跳触达的角色集)\n"
                f"**编排本轮时,优先调度以下角色,他们最可能被反事实涟漪波及**:\n"
                f"  {', '.join(affected_char_names)}\n"
                f"(范围外的角色可作背景,但不应是本轮焦点)"
            )

    # 重建 history(让续推的 director / agent 看到此前发生了什么)
    for r in existing_rounds:
        plan = r.get("director_plan") or {}
        director_history.append({
            "round": r.get("round"),
            "location": plan.get("location", ""),
            "time_advance": plan.get("time_advance", ""),
            "narrator_note": plan.get("narrator_note", ""),
            "events": r.get("events", []),
        })
        for ev in r.get("events", []):
            if ev.get("action"):
                flat_history.append({
                    "round": r.get("round"), "speaker": ev.get("speaker", ""),
                    "action_type": "action", "content": ev["action"],
                })
            if ev.get("dialogue"):
                flat_history.append({
                    "round": r.get("round"), "speaker": ev.get("speaker", ""),
                    "action_type": "dialogue", "content": ev["dialogue"],
                })

    # === Director + Agents 主循环 ===
    # Bug 1 修(2026-05-24):started_at 走 SQL 层 COALESCE,不依赖 Python 对象,
    # 防 sim 快照过时导致已有 started_at 被重置 → 前端 elapsed 归零
    _set_started_at_if_null(conn, sim_id)
    _update_state(
        conn, sim_id, state="directing",
        error_message=None,
    )
    _emit_event(sim_id, {
        "kind": "state_change",
        "state": "directing",
        "rounds_planned": sim.rounds_planned,
        "resumed": is_resume,
        "completed_rounds_before_resume": len(existing_rounds),
    })

    # 累计计费 — 接续(已存的 + 这次摘要的)
    total_in = sim.tokens_input + ctx_in
    total_out = sim.tokens_output + ctx_out

    for round_num in range(start_round, sim.rounds_planned + 1):
        _emit_event(sim_id, {
            "kind": "round_start",
            "round": round_num,
            "rounds_planned": sim.rounds_planned,
        })

        # --- Director ---
        _emit_event(sim_id, {"kind": "director_start", "round": round_num})
        t_dir = time.perf_counter()
        director_user = _build_director_user_prompt(
            scene, sim.divergence, chars_by_id, director_history, round_num,
            prior_context=prior_context,
            counterfactual_section=cf_section_text,
            affected_scope_section=affected_section_text,
            # Sprint 3.A 末尾态:注入原作末段(其它 mode 为 None,builder 内部静默跳过)
            original_tail=sim.original_tail_excerpt or "",
            # Sprint 6.A2 M7.J(2026-05-20):中间态起点锚点事件 description
            anchor_event_description=anchor_event_description,
        )
        plan_raw, dir_usage = call_llm_json(
            director_system_prompt, director_user,
            max_tokens=600, temperature=0.6,
        )
        dir_ms = int((time.perf_counter() - t_dir) * 1000)
        plan, _warnings = _validate_director_plan(
            plan_raw if isinstance(plan_raw, dict) else {},
            set(chars_by_id.keys()),
        )
        total_in += dir_usage["input_tokens"]
        total_out += dir_usage["output_tokens"]
        _emit_event(sim_id, {
            "kind": "director_done",
            "round": round_num,
            "duration_ms": dir_ms,
            "tokens_input": dir_usage["input_tokens"],
            "tokens_output": dir_usage["output_tokens"],
            "location": plan["location"],
            "time_advance": plan["time_advance"],
            "speaking_agents": [
                chars_by_id[cid]["name"]
                for cid in plan["speaking_agents"]
                if cid in chars_by_id
            ],
        })

        # --- Agents ---
        round_events: list[dict] = []
        for char_id in plan["speaking_agents"]:
            char = chars_by_id[char_id]
            _emit_event(sim_id, {
                "kind": "agent_start",
                "round": round_num,
                "speaker": char["name"],
            })
            t_ag = time.perf_counter()
            agent_system = _build_agent_system_prompt(char)
            agent_user = _build_agent_user_prompt(
                char["name"], scene, sim.divergence,
                f"{plan['location']} · {plan['time_advance']}",
                plan["round_seed"], plan["narrator_note"],
                flat_history,
                prior_context=prior_context,
            )
            output, usage = call_llm_json(
                agent_system, agent_user,
                max_tokens=400, temperature=0.7,
            )
            ag_ms = int((time.perf_counter() - t_ag) * 1000)
            if not isinstance(output, dict):
                output = {"monologue": "", "action": "", "dialogue": ""}
            total_in += usage["input_tokens"]
            total_out += usage["output_tokens"]
            _emit_event(sim_id, {
                "kind": "agent_done",
                "round": round_num,
                "speaker": char["name"],
                "duration_ms": ag_ms,
                "tokens_input": usage["input_tokens"],
                "tokens_output": usage["output_tokens"],
                "dialogue_preview": (output.get("dialogue") or "")[:30],
            })

            ev = {
                "speaker": char["name"],
                "speaker_id": char_id,
                "monologue": output.get("monologue", ""),
                "action":    output.get("action", ""),
                "dialogue":  output.get("dialogue", ""),
            }
            round_events.append(ev)
            if ev["action"]:
                flat_history.append({
                    "round": round_num, "speaker": char["name"],
                    "action_type": "action", "content": ev["action"],
                })
            if ev["dialogue"]:
                flat_history.append({
                    "round": round_num, "speaker": char["name"],
                    "action_type": "dialogue", "content": ev["dialogue"],
                })

        round_record = {
            "round": round_num,
            "director_plan": plan,
            "events": round_events,
        }
        timeline_rounds.append(round_record)
        director_history.append({
            "round": round_num,
            "location": plan["location"],
            "time_advance": plan["time_advance"],
            "narrator_note": plan["narrator_note"],
            "events": round_events,
        })

        # 落盘进度(每轮 → 持续可读 + 失败时已部分留痕)
        cost_so_far = estimate_cost_yuan(total_in, total_out)
        _update_state(
            conn, sim_id,
            current_round=round_num,
            timeline_json=json.dumps(
                {"rounds": timeline_rounds}, ensure_ascii=False,
            ),
            tokens_input=total_in,
            tokens_output=total_out,
            cost_yuan=cost_so_far,
        )
        _emit_event(sim_id, {
            "kind": "round_done",
            "round": round_num,
            "rounds_planned": sim.rounds_planned,
            "cost_yuan": round(cost_so_far, 4),
        })

    # === Composer ===
    _update_state(conn, sim_id, state="composing")
    _emit_event(sim_id, {"kind": "composing_start"})

    # 统一加载新 composer.md(Sprint 1.Q):一份通用 prompt,LLM 自适应语体。
    # 旧 composer_a/c/custom 已废弃,sim.style ∈ {A, C, auto, custom} 都走同一文件。
    # custom_style_hint 为空时,LLM 走"语体自评估";非空时强制覆盖。
    composer_template = _load_prompt("composer.md")
    composer_system = _safe_format_template(
        composer_template,
        {
            "target_chars":      str(sim.target_chars),
            "custom_style_hint": (sim.custom_style_hint or "").strip(),
        },
    )

    # 给 composer 注入项目元信息,供 LLM 第一步语体判断
    project_meta = _build_project_meta(conn, sim.project_id)
    composer_user = _build_composer_user_prompt(
        {"rounds": timeline_rounds}, chars_by_id, sim.target_chars,
        prior_context=prior_context,
        project_meta=project_meta,
        # Sprint 3.A 末尾态:注入原作末段,composer 模仿语体 + 接续叙事
        original_tail=sim.original_tail_excerpt or "",
        # 2026-06-05 CRITICAL:反事实改动 — composer 第一眼看到,优先级最高
        counterfactual_section=cf_section_text,
    )

    # P0X.1(2026-05-26)— 快速模式 prepend 物理 / 语言能力铁律
    # 治 13574/14345/15070/15365 师傅"半身不遂"在 4 份续作里 4 种行为漂移问题。
    # 快速模式之前不经过 hard_constraints,LLM 完全自由解读 status_note。
    # 现在 evolution + quick 共用 physical_constraints_util,见到同一份具体禁律。
    try:
        from app.services.physical_constraints_util import (
            build_physical_constraints_block,
        )
        pc_block = build_physical_constraints_block(conn, sim.project_id)
        if pc_block:
            composer_system = pc_block + "\n\n" + composer_system
            log.info(
                f"P0X.1: quick 模式注入物理铁律 {len(pc_block)} 字 sim={sim_id}"
            )
    except Exception as e:  # noqa: BLE001
        log.warning(f"P0X.1: physical_constraints inject failed: {e}")

    # P3 Day 2(2026-05-26)— 快速模式 prepend 作者指南针
    # 让续作"像川端 / 像村上",双轨制(外部研究 + 内部反推)由 P3 Day 1 LLM 调研得出
    # evolution + quick 共用 author_compass_util
    try:
        from app.services.author_compass_util import build_author_compass_block
        ac_block = build_author_compass_block(conn, sim.project_id)
        if ac_block:
            composer_system = ac_block + "\n\n" + composer_system
            log.info(
                f"P3 Day 2: quick 模式注入作者指南针 {len(ac_block)} 字 sim={sim_id}"
            )
    except Exception as e:  # noqa: BLE001
        log.warning(f"P3 Day 2: author_compass inject failed: {e}")

    # SP-1(2026-05-28)— 故事内核三件套(目的层,最高优先级)
    # 最后 prepend → 出现在 system_prompt 最顶 → LLM attention 权重最大
    # 治"LLM 没目标弧就提前泄气":给推演一个故事终点,它才敢憋住矛盾不解决
    try:
        from app.services.story_core_util import build_story_core_block
        # quick 模式无 scene_index / 总幕数 → 不带进度提示
        sc_block = build_story_core_block(conn, sim.project_id)
        if sc_block:
            composer_system = sc_block + "\n\n" + composer_system
            log.info(
                f"SP-1: quick 模式注入故事内核 {len(sc_block)} 字 sim={sim_id}"
            )
    except Exception as e:  # noqa: BLE001
        log.warning(f"SP-1: story_core inject failed: {e}")

    t_comp = time.perf_counter()
    narrative, comp_usage = call_llm_text(
        composer_system, composer_user,
        max_tokens=8000, temperature=0.65,
    )
    comp_ms = int((time.perf_counter() - t_comp) * 1000)
    total_in += comp_usage["input_tokens"]
    total_out += comp_usage["output_tokens"]
    final_cost = estimate_cost_yuan(total_in, total_out)

    # 2026-06-02:快速模式反复读 retry 闸门
    # 治"全篇反复用同一组动作 / 同一意象"瑕疵(用户实测网恋风云 4520 字快速模式产物
    #   喉结滚 10 次 / 指尖发抖 15+ 次 / 路灯昏黄 6 次)
    # retry 1 次(最多),保留快速模式低成本定位
    try:
        from app.services.quick_repetition_guard import (
            check_full_narrative_repetition,
            build_retry_hint_from_violations,
        )
        repetition_violations = check_full_narrative_repetition(narrative)
        if repetition_violations:
            log.warning(
                "quick_repetition_guard: sim %s 反复读违规 %d 项,触发 1 次 retry. "
                "高频族:%s",
                sim_id,
                len(repetition_violations),
                ", ".join(f"{v.family}×{v.total_count}" for v in repetition_violations[:5]),
            )
            retry_hint = build_retry_hint_from_violations(repetition_violations)
            retry_user_prompt = composer_user + "\n" + retry_hint
            t_retry = time.perf_counter()
            retry_narrative, retry_usage = call_llm_text(
                composer_system, retry_user_prompt,
                max_tokens=8000, temperature=0.6,  # 略降温度让 LLM 更稳
            )
            comp_ms += int((time.perf_counter() - t_retry) * 1000)
            total_in += retry_usage["input_tokens"]
            total_out += retry_usage["output_tokens"]
            final_cost = estimate_cost_yuan(total_in, total_out)
            # 复检 retry 产物;仍违规也用 retry 版(LLM 已尽力)
            retry_violations = check_full_narrative_repetition(retry_narrative)
            log.info(
                "quick_repetition_guard: retry done sim=%s. "
                "retry 前 %d 项 → retry 后 %d 项",
                sim_id, len(repetition_violations), len(retry_violations),
            )
            narrative = retry_narrative
    except Exception as e:  # noqa: BLE001
        log.warning(f"quick_repetition_guard failed: {e}")

    # P0X.1(2026-05-26)— 快速模式 narrative 落库前 program-level dedup 兜底
    # 零 LLM 成本,不走 retry(保留"快速模式"定位)。
    # 治 15365 实测 L19 vs L49 按摩女对白逐字复读、师傅整句对白等问题。
    try:
        from app.services.dialogue_dedup import (
            dedup_repeated_dialogues_in_segment,
            dedup_repeated_narratives_in_segment,
        )
        dialog_removed = 0
        narr_removed = 0
        if narrative:
            narrative, dialog_removed = dedup_repeated_dialogues_in_segment(narrative)
            narrative, narr_removed = dedup_repeated_narratives_in_segment(
                narrative, min_chars=20,
            )
        if dialog_removed > 0 or narr_removed > 0:
            log.info(
                f"P0X.1 quick dedup: 删 {dialog_removed} 处对白复读 + "
                f"{narr_removed} 处叙述句复读 sim={sim_id}"
            )
    except Exception as e:  # noqa: BLE001
        log.warning(f"P0X.1: quick dedup failed: {e}")

    # Sprint 6.A2 M3.D-fix2 v2(2026-05-18,用户反对硬截断):
    # 不再用 _enforce_narrative_length 强行介入创作 — 按用户拍板"以体面方式解决":
    #   ① 创建时让用户在"重塑度推断的合理字数区间"内选 target_chars(reshape_recommended_chars_range)
    #   ② composer prompt 给清晰目标 + 区间引导(已加强,见 _build_composer_user_prompt)
    #   ③ 灵魂续写主循环动态停止(达 target × 0.95 即 break)
    # 不再硬截断 LLM 产物,保留创作完整性。

    _update_state(
        conn, sim_id,
        state="done",
        narrative=narrative,
        tokens_input=total_in,
        tokens_output=total_out,
        cost_yuan=final_cost,
        completed_at=iso_now(),
    )

    # Sprint C.2(2026-05-13)— credit 真扣:done 时按真实总 token 算
    # 失败不阻塞 simulation done(LLM 已跑成功,credit 记账失败仅 warn,运维 audit 跟进)
    # 失败不退(LLM 已发生成本)— 已 done 即"用户已拿到产物"
    try:
        units = credit_units_for_text_call(total_in, total_out)
        consume_credits(
            conn,
            user_id=sim.user_id,
            action="continuation",
            units=units,
            related_id=sim_id,
            cost_yuan=final_cost,
            metadata={
                "input_tokens": total_in,
                "output_tokens": total_out,
                "vendor": "deepseek",
                "rounds_planned": sim.rounds_planned,
            },
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(
            f"consume_credits(continuation) failed for sim {sim_id}: {e}"
        )

    # 2026-06-01:走向终章 + 有继承链 → 自动合并最终作品
    # 条件不满足(无 ancestors / 无 with_grand_finale)→ 静默跳过
    # 失败 → log + 不阻塞 done(用户已拿到本 sim narrative,合并失败时仍能正常读)
    try:
        from app.services.compile_final_work import maybe_compile_and_persist_final_work
        # 重拉 sim — _update_state 已把 state→'done' + narrative 落库,需要拿最新 row
        done_sim = get_simulation_or_404(conn, sim_id, sim.user_id)
        maybe_compile_and_persist_final_work(conn, done_sim)
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(
            f"maybe_compile_and_persist_final_work failed for sim {sim_id}: {e}"
        )

    _emit_event(sim_id, {
        "kind": "composing_done",
        "duration_ms": comp_ms,
        "tokens_input": comp_usage["input_tokens"],
        "tokens_output": comp_usage["output_tokens"],
        "narrative_chars": len(narrative or ""),
    })
    _emit_event(sim_id, {
        "kind": "done",
        "narrative": narrative,
        "cost_yuan": round(final_cost, 4),
        "rounds_planned": sim.rounds_planned,
    })


# ======================================================================
# kick_off — 生产 vs 测试切换点
# ======================================================================

def _default_kick_off(sim_id: str) -> None:
    """生产环境:起后台 daemon 线程跑 run_simulation,POST 立即返回。

    Sprint 1.K bug fix — 之前用 asyncio.get_running_loop() 检测异步上下文,
    但 FastAPI sync route(def api_create_simulation)被 starlette 丢进
    threadpool,**worker 线程没有 running event loop**。因此
    get_running_loop() 抛 RuntimeError → fallback 到 run_simulation 同步阻塞,
    POST 卡 5 分钟,前端用户看到"创建中..."以为崩了。

    用 threading.Thread 直接起 daemon 线程:
      - 在任何上下文工作(sync / async / 测试 / CLI)
      - run_simulation 是纯同步代码(sqlite + sync OpenAI SDK),没必要包 asyncio
      - daemon=True:进程退出时不阻塞;reload / Ctrl+C 干脆利落
      - 测试环境 sync_simulation_runner monkeypatch 覆盖此函数为同步直跑

    2026-06-05 BYOK:capture_current_context() 把当前 endpoint 的 user_id ContextVar
    捕获,带进新 thread 跑 — 否则 thread 里 get_current_user_id() 永远是 None,
    BYOK 路由会失效(误走平台默认 key)。
    """
    import threading
    from app.services.byok_context import capture_current_context

    # ⚠ 必须在主 thread(endpoint 上下文)捕获,新 thread 里捕获就晚了
    ctx = capture_current_context()
    threading.Thread(
        target=ctx.run,
        args=(run_simulation, sim_id),
        daemon=True,
        name=f"sim-{sim_id[:8]}",
    ).start()


# 模块级可替换(测试 monkeypatch 此变量为 run_simulation 直接同步跑)
kick_off: Callable[[str], None] = _default_kick_off


# ======================================================================
# SSE 流式 — in-memory queue + await(Sprint 1.L 真推送,替换原 1.5s 轮询)
# ======================================================================

# SSE 长连接保活心跳间隔(防中间代理 timeout 断连)
_SSE_HEARTBEAT_SECONDS = 30.0
TERMINAL_EVENT_KINDS = ("done", "error", "cancelled")


def _format_sse_event(data: dict) -> str:
    """SSE 单条事件序列化:`data: {json}\\n\\n`。

    EventSource API 默认监听 'message' 事件(无 event 字段);事件类型在
    JSON 内的 `kind` 字段里,前端 onmessage 解析后路由。
    """
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sim_snapshot_payload(sim: Simulation) -> dict:
    """SSE 启动首发 snapshot 事件 — 让客户端立刻拿到当前完整状态。

    含 narrative 当且仅当已 done(中间态省 token)。
    """
    payload = {
        "kind": "snapshot",
        "id": sim.id,
        "state": sim.state,
        "current_round": sim.current_round,
        "rounds_planned": sim.rounds_planned,
        "cost_yuan": round(sim.cost_yuan, 4),
        "error_message": sim.error_message,
    }
    if sim.state == "done":
        payload["narrative"] = sim.narrative
    return payload


async def stream_simulation_state(sim_id: str, user_id: str):
    """async generator — SSE handler 调,产 text/event-stream 字节流。

    流程:
      1. 鉴权 + 拉 DB 当前状态,先 yield 一条 snapshot(支持页面刷新立刻有数据)
      2. 已是终态 → 直接 return(无后续 await)
      3. 否则:注册 queue 到 EVENT_QUEUES,await runner 推过来的事件
      4. 30s 无事件 → 推一条 SSE 注释行 (`: heartbeat\\n\\n`) 防代理断连
      5. 收到终态事件(done/error/cancelled)→ yield 后 break
      6. finally 清理 queue(用户跳页 / 异常断 都走这里)

    跨线程安全性:queue.put 由 runner 线程调,通过 loop.call_soon_threadsafe;
    queue.get 在本协程,asyncio.Queue 自身的 get 是协程方法,无并发问题。
    """
    conn = get_connection()
    try:
        # 1. 鉴权 + 首发快照
        sim = get_simulation_or_404(conn, sim_id, user_id)
        yield _format_sse_event(_sim_snapshot_payload(sim)).encode("utf-8")

        if sim.state in ("done", "failed", "cancelled"):
            return

        # 2. 注册订阅
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        _register_subscriber(sim_id, queue, loop)

        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        queue.get(), timeout=_SSE_HEARTBEAT_SECONDS,
                    )
                except asyncio.TimeoutError:
                    # 30s 无事件 → 心跳。SSE 注释行(以 ":" 开头)前端会忽略
                    yield b": heartbeat\n\n"
                    continue

                yield _format_sse_event(event).encode("utf-8")

                if event.get("kind") in TERMINAL_EVENT_KINDS:
                    break
        finally:
            # 2026-06-02 hotfix R4:unregister 抛错时仍保证 conn.close
            try:
                _unregister_subscriber(sim_id, queue)
            except Exception as e:  # noqa: BLE001
                log.warning(f"stream_simulation_state: unregister failed sim={sim_id}: {e}")
    finally:
        try:
            conn.close()
        except Exception as e:  # noqa: BLE001
            log.warning(f"stream_simulation_state: conn.close failed sim={sim_id}: {e}")


__all__ = [
    "TooFewCharactersForSimulation",
    "InvalidContextSimulations",
    "SimulationNotResumable",
    "SimulationStillRunning",
    "create_simulation",
    "validate_context_simulation_ids",
    "get_simulation_or_404",
    "list_simulations_for_project",
    "list_simulations_for_user",
    "delete_simulation",
    "resume_simulation",
    "run_simulation",
    "kick_off",
    "stream_simulation_state",
    "build_characters_snapshot",
    "reshape_to_rounds",
]
