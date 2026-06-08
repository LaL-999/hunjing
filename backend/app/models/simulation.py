"""Simulation 表的 Python 表示 — Sprint 1.G。

字段说明:
  characters_snapshot — JSON 数组,创建时冻结的角色快照(改项目角色不影响已存推演)
  timeline            — JSON 对象 {rounds: [...]},每轮 director plan + events
  narrative           — Composer 产物,markdown 文本
  original_tail_excerpt — Sprint 3.A 末尾态专属:创建时缓存的原作末尾 ~2000 字
                          (其它 mode 为 None;run_simulation 仅末尾态读取并注入)

状态机(对齐 ADR §1):
  queued     创建,瞬时(<1s)等 worker 接手
  directing  Director + Agents 循环中(主耗时,~5 分钟)
  composing  Composer 编织 markdown(~30-50s)
  done       完成,narrative 可读
  failed     任一 LLM 失败,error_message 落库
  cancelled  用户主动停(状态机已留位,本 sprint 不暴露端点)
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Optional


SIMULATION_STATES = ("queued", "directing", "composing", "done", "failed", "cancelled")
TERMINAL_STATES = ("done", "failed", "cancelled")


@dataclass
class Simulation:
    id: str
    project_id: str
    user_id: str
    divergence: str
    reshape_percent: int                   # 用户面向 10-90,plan 上限
    rounds_planned: int                    # 由 reshape_percent 在 service 派生
    target_chars: int
    style: str                             # 'A' / 'C' / 'custom'
    custom_style_hint: Optional[str]       # style='custom' 时用户写的笔法描述
    context_simulation_ids: list[str]      # 前文 sim ids;空列表 = 独立推演
    narrative_summary: Optional[str]       # ~800 字摘要,被后续 sim 引用时生成并缓存
    characters_snapshot: list[dict]
    state: str
    current_round: int
    timeline: Optional[dict]               # parsed JSON 或 None
    narrative: Optional[str]
    tokens_input: int
    tokens_output: int
    cost_yuan: float
    error_message: Optional[str]
    created_at: str
    started_at: Optional[str]
    completed_at: Optional[str]
    # Sprint 3.A 末尾态:创建时缓存的原作末段(其它 mode 为 None)
    original_tail_excerpt: Optional[str] = None
    # Sprint 6.A2 M3.A(2026-05-18):续写模式 'quick' (老路径) / 'evolution' (灵魂续写)
    mode: str = "quick"
    # Sprint 6.A2 M6(2026-05-20):是否走 outline-first 长篇生成
    # 1=走 outline-first(创建 sim 后先 LLM 生成 outline,用户审核 → 按 outline 跑)
    # 0=走原 evolution / quick 自由 scene_picker(向后兼容老 sim)
    use_outline_first: int = 0
    # Sprint 6.A2 M7.J(2026-05-20):起点锚点事件(仅中间态可设)
    # NULL = 旧"独立新场景"语义;非空 = 以该事件刚结束为起点续推
    # ON DELETE SET NULL(事件被删 → sim 退回旧语义,不破坏 sim)
    anchor_event_id: Optional[str] = None
    # Sprint 6.A2 路线图 #5(2026-05-23):用户边写边干预 — 待生效的下一幕 hint
    # NULL=无,非空=用户已提交,续写主循环下一幕开始前读取后立即清空(消耗式)
    # 详见 migration 057
    pending_scene_hint: Optional[str] = None
    # P2.A(2026-05-24,migration 060):是否走向大结局收尾
    # 0=默认 — 禁止"主角告别过去/接受未来"类大结局动作,允许超长篇续作
    # 1=鼓励大结局 — 主线收束 / 情绪闭环 / 角色弧光完成
    with_grand_finale: int = 0
    # P2.B(2026-05-24,migration 060):叙事节奏档位
    # 'slow'     — 慢节奏:允许多幕同场景 + 鼓励氛围(适合村上/川端类)
    # 'standard' — 默认:当前行为
    # 'fast'     — 紧凑:每 2-3 幕必须切场景 + 主动制造波折(适合三体类)
    narrative_pacing: str = "standard"
    # P0H.2(2026-05-24,migration 063):用户自定义每章字数
    # 前端 SimulationReadView 按此值切分 narrative 加 ## 章节 N 标题
    # 合理范围 1000-10000,默认 2000
    # 2026-06-01:被 projects.chapter_size_min/max 替代,但保留向下兼容(老 sim 用此值)
    chapter_size_chars: int = 2000
    # 2026-06-01:章节体系 — 创建时锁定的起始章号(沿继承链推导出来)
    # NULL = 老 sim 不知起始章号,降级用 1
    # 非空 = 写入后不变,前篇怎么改文本本 sim start_chapter 都不动 → 规避副作用
    start_chapter_locked: Optional[int] = None
    # 2026-06-01:走向终章 + 多代滚雪球 → 沿继承链合并的最终作品
    # NULL = 无(孤本 / 未走向终章 / 仍未跑完)
    # 非空 = 合并文本(本 sim narrative + 所有 ancestors narrative,按时序拼接)
    # 2026-06-01 v2:此字段 deprecated — 新合并产物建独立 sim 行(is_final_compilation=1),
    #               旧字段保留向下兼容(老 sim 已写入的不动)
    final_compiled_narrative: Optional[str] = None
    # 2026-06-01 v2:独立合并产物标识 — 0=普通 sim;1=自动合并最终作品(独立 sim 行)
    is_final_compilation: int = 0
    # 2026-06-01 v2:合并产物的来源 sim id(触发合并的最后一个 sim)— 仅 is_final_compilation=1 有值
    compiled_from_sim_id: Optional[str] = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Simulation":
        timeline_raw = row["timeline_json"]
        timeline: Optional[dict] = json.loads(timeline_raw) if timeline_raw else None
        ctx_raw = row["context_simulation_ids"]
        ctx_ids: list[str] = json.loads(ctx_raw) if ctx_raw else []
        # 兼容老库(migration 019 前无此列):row 字典访问失败 → None
        tail_excerpt = (
            row["original_tail_excerpt"]
            if "original_tail_excerpt" in row.keys()
            else None
        )
        # 兼容老库(migration 038 前无 mode 列)
        # 2026-06-02 hotfix Y1:统一 catch 4 异常(老代码只 catch KeyError/IndexError,
        # 漏 ValueError(int 转非数字)+ TypeError(SQLite NULL 进 int))
        try:
            mode_val = row["mode"] or "quick"
        except (KeyError, IndexError, ValueError, TypeError):
            mode_val = "quick"
        try:
            uof_val = int(row["use_outline_first"] or 0)
        except (KeyError, IndexError, ValueError, TypeError):
            uof_val = 0
        try:
            anchor_val = row["anchor_event_id"]
        except (KeyError, IndexError, ValueError, TypeError):
            anchor_val = None
        try:
            pending_hint_val = row["pending_scene_hint"]
        except (KeyError, IndexError, ValueError, TypeError):
            pending_hint_val = None
        try:
            grand_finale_val = int(row["with_grand_finale"] or 0)
        except (KeyError, IndexError, ValueError, TypeError):
            grand_finale_val = 0
        try:
            pacing_val = row["narrative_pacing"] or "standard"
        except (KeyError, IndexError, ValueError, TypeError):
            pacing_val = "standard"
        try:
            chapter_size_val = int(row["chapter_size_chars"] or 2000)
        except (KeyError, IndexError, ValueError, TypeError):
            chapter_size_val = 2000
        # 2026-06-01:start_chapter_locked / final_compiled_narrative 兜底(migration 076 前老库无此列)
        try:
            start_chap_raw = row["start_chapter_locked"]
            start_chapter_val = int(start_chap_raw) if start_chap_raw is not None else None
        except (KeyError, IndexError, ValueError, TypeError):
            start_chapter_val = None
        try:
            fcn_raw = row["final_compiled_narrative"]
            final_compiled_val = fcn_raw if fcn_raw else None
        except (KeyError, IndexError):
            final_compiled_val = None
        # 2026-06-01 v2:独立合并产物字段兜底(migration 078 前老库无此列)
        try:
            is_compilation_raw = row["is_final_compilation"]
            is_compilation_val = int(is_compilation_raw or 0)
        except (KeyError, IndexError, ValueError, TypeError):
            is_compilation_val = 0
        try:
            compiled_from_val = row["compiled_from_sim_id"]
        except (KeyError, IndexError):
            compiled_from_val = None
        return cls(
            id=row["id"],
            project_id=row["project_id"],
            user_id=row["user_id"],
            divergence=row["divergence"],
            reshape_percent=row["reshape_percent"],
            rounds_planned=row["rounds_planned"],
            target_chars=row["target_chars"],
            style=row["style"],
            custom_style_hint=row["custom_style_hint"],
            context_simulation_ids=ctx_ids,
            narrative_summary=row["narrative_summary"],
            characters_snapshot=json.loads(row["characters_snapshot"]),
            state=row["state"],
            current_round=row["current_round"],
            timeline=timeline,
            narrative=row["narrative"],
            tokens_input=row["tokens_input"],
            tokens_output=row["tokens_output"],
            cost_yuan=row["cost_yuan"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            original_tail_excerpt=tail_excerpt,
            mode=mode_val,
            use_outline_first=uof_val,
            anchor_event_id=anchor_val,
            pending_scene_hint=pending_hint_val,
            with_grand_finale=grand_finale_val,
            narrative_pacing=pacing_val,
            chapter_size_chars=chapter_size_val,
            start_chapter_locked=start_chapter_val,
            final_compiled_narrative=final_compiled_val,
            is_final_compilation=is_compilation_val,
            compiled_from_sim_id=compiled_from_val,
        )

    def to_summary(self) -> dict[str, Any]:
        """列表展示用 — 不带 timeline / narrative / characters_snapshot 重字段。"""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "divergence": self.divergence,
            "reshape_percent": self.reshape_percent,
            "rounds_planned": self.rounds_planned,
            "current_round": self.current_round,
            "state": self.state,
            "cost_yuan": round(self.cost_yuan, 4),
            "error_message": self.error_message,
            "context_simulation_ids": self.context_simulation_ids,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            # hotfix(2026-06-01):透出续写模式给前端列表区分 chip
            "mode": self.mode,
            "use_outline_first": bool(self.use_outline_first),
            # 2026-06-01 v2:独立合并产物标识 — 列表 UI 用此区分"最终作品 vs 普通推演"
            "is_final_compilation": bool(self.is_final_compilation),
            "compiled_from_sim_id": self.compiled_from_sim_id,
        }

    def to_full(self) -> dict[str, Any]:
        # 末尾态产物详情页要让用户看见"AI 接的是哪段原文",所以 to_full 暴露 excerpt;
        # to_summary 不带,避免列表接口体积膨胀(末尾态项目可能有几十条 sim)
        return {
            **self.to_summary(),
            "target_chars": self.target_chars,
            "style": self.style,
            "custom_style_hint": self.custom_style_hint,
            "narrative_summary": self.narrative_summary,
            "characters_snapshot": self.characters_snapshot,
            "timeline": self.timeline,
            "narrative": self.narrative,
            "tokens_input": self.tokens_input,
            "tokens_output": self.tokens_output,
            "started_at": self.started_at,
            "original_tail_excerpt": self.original_tail_excerpt,
            "mode": self.mode,
            "use_outline_first": bool(self.use_outline_first),
            "anchor_event_id": self.anchor_event_id,
            # 2026-06-01:章节体系 + 走向终章合并
            "start_chapter_locked": self.start_chapter_locked,
            "final_compiled_narrative": self.final_compiled_narrative,
            # 2026-06-01 v2:独立合并产物完整字段
            "is_final_compilation": bool(self.is_final_compilation),
            "compiled_from_sim_id": self.compiled_from_sim_id,
        }
