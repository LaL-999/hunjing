"""Project 表的 Python 表示。"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from typing import Optional


# Sprint 2.C+ polish:6 维度世界观 baseline(由 infer_meta 阶段 LLM 自动识别)
# 给反事实工作台「世界观 tab」的"原"字段提供预填值
WORLD_BASELINE_FIELDS = (
    "genre", "setting", "magic_system", "time_axis", "tone", "free_form",
)


@dataclass
class Project:
    id: str
    user_id: str
    name: str
    type: str                          # novel / comic / anime / generic
    custom_type_name: Optional[str]    # type='generic' 时用户写的自定义类型名
    tags: list[str]
    mode: str                          # initial / middle / end / cycle
    created_at: str
    updated_at: str
    # Sprint 2.C+ polish: 6 维度 world baseline,从 db world_baseline_json parse
    # 字段缺失或解析失败 → 空 dict(前端兜底显空字符串)
    world_baseline: dict[str, str] = field(default_factory=dict)
    # Sprint 3.A polish: 3D 图谱关系连线显示阈值 10-80,默认 30(过滤 weak 一档)
    # 数字越高显示越严格,越低越宽松;前端 transformBackendGraph 按此过滤 links
    graph_strength_threshold: int = 30
    # Sprint 6.A2 FOCUS.2(2026-05-21,migration 055):作品叙述视角
    # 枚举:"first" | "second" | "third" | "mixed" | None
    # None = 未识别(老项目 / 初始态用户未指定 / extract 未跑);
    # 续写阶段 prompt 看到此字段,强制延续相同人称
    narrative_pov: Optional[str] = None
    # P2.B 升级(2026-05-24,migration 061):AI 推断的叙事节奏档位
    # 触发逻辑:用户首次为该项目创建续作时,若 NULL → 同步跑 pacing_inferer
    # 推断结果缓存到此 4 字段,后续 sim 创建时直接读用
    inferred_pacing: Optional[str] = None  # 'slow' / 'standard' / 'fast' / None
    inferred_pacing_reasoning: Optional[str] = None  # 给用户看的解释
    inferred_pacing_metrics: Optional[dict] = None  # 段落均长/对白比/场景跨度等原始指标
    inferred_pacing_at: Optional[str] = None  # ISO 8601 推断完成时间
    # SP-1 故事内核三件套(2026-05-28,migration 066):灵魂续写脊柱
    # 三字段都允许 NULL(老项目兼容);user 可手填或 build_graph 抽取后回填
    # 与已有字段正交:tone=基调色温 / ending_direction=情绪终点 / theme=故事命题
    core_dramatic_question: Optional[str] = None  # 一句话脊柱
    theme: Optional[str] = None                    # 主题(独立于基调)
    ending_direction: Optional[str] = None         # 终点情绪 / 走向
    # SP-8(2026-05-28,migration 073)— 视角扩展三件套
    # 与 narrative_pov(人称)正交:这三个是焦点 / 可靠性 / 距离
    narrative_focus_character_id: Optional[str] = None  # 焦点角色 id
    narrator_reliability: Optional[str] = None          # reliable / unreliable / uncertain
    narrative_distance: Optional[str] = None            # omniscient / limited / close / intimate
    # 2026-06-01:作品篇幅 — 决定 narrator 的细节颗粒度档位
    #   short  每段都细致(短篇/单章风格)
    #   medium 核心场景细致,过场简略(默认)
    #   long   转折/高潮密集,常规松弛(避免长读疲劳)
    expected_length: str = "medium"
    # 2026-06-01:章节体系 — 每章字数区间(min/max),系统在区间内贪婪找段落切章
    # 用区间而不是单值:AI 不用强求精确字数 → 创作自由 / 章节切分点更自然
    chapter_size_min: int = 1500
    chapter_size_max: int = 2500

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Project":
        # world_baseline_json 可能是 NULL(老项目还没识别)/ 空字符串 / 损坏
        baseline_raw = row["world_baseline_json"] if "world_baseline_json" in row.keys() else None
        baseline: dict[str, str] = {}
        if baseline_raw:
            try:
                parsed = json.loads(baseline_raw)
                if isinstance(parsed, dict):
                    # 只保留白名单内的 key + 强转 str(防 LLM 输出非 str 值)
                    baseline = {
                        k: str(v) for k, v in parsed.items()
                        if k in WORLD_BASELINE_FIELDS and v is not None
                    }
            except (json.JSONDecodeError, TypeError):
                baseline = {}
        # 兼容老库(migration 020 前无此列):row 字典访问失败 → 30 默认值
        threshold = (
            row["graph_strength_threshold"]
            if "graph_strength_threshold" in row.keys()
            else 30
        )
        # Sprint D.7:tags JSON 兜底(脏数据 / 空字符串 / 非 list 都安全 fallback [])
        tags: list[str] = []
        tags_raw = row["tags"]
        if isinstance(tags_raw, str) and tags_raw:
            try:
                parsed_tags = json.loads(tags_raw)
                if isinstance(parsed_tags, list):
                    tags = [str(t) for t in parsed_tags]
            except (json.JSONDecodeError, TypeError):
                pass
        # Sprint 6.A2 FOCUS.2:narrative_pov 兜底(老 db row 缺列 → None)
        pov: Optional[str] = None
        try:
            raw_pov = row["narrative_pov"]
            if isinstance(raw_pov, str) and raw_pov in {"first", "second", "third", "mixed"}:
                pov = raw_pov
        except (KeyError, IndexError):
            pov = None
        # P2.B 升级(2026-05-24,migration 061):inferred_pacing 4 字段兜底
        inferred_pacing_val: Optional[str] = None
        inferred_reasoning_val: Optional[str] = None
        inferred_metrics_val: Optional[dict] = None
        inferred_at_val: Optional[str] = None
        try:
            raw_pacing = row["inferred_pacing"]
            if isinstance(raw_pacing, str) and raw_pacing in {"slow", "standard", "fast"}:
                inferred_pacing_val = raw_pacing
        except (KeyError, IndexError):
            pass
        try:
            inferred_reasoning_val = row["inferred_pacing_reasoning"]
        except (KeyError, IndexError):
            pass
        try:
            metrics_raw = row["inferred_pacing_metrics_json"]
            if isinstance(metrics_raw, str) and metrics_raw:
                try:
                    parsed_metrics = json.loads(metrics_raw)
                    if isinstance(parsed_metrics, dict):
                        inferred_metrics_val = parsed_metrics
                except (json.JSONDecodeError, TypeError):
                    pass
        except (KeyError, IndexError):
            pass
        try:
            inferred_at_val = row["inferred_pacing_at"]
        except (KeyError, IndexError):
            pass
        # SP-1 故事内核三件套兜底(migration 066 前老库无此列)
        cdq_val: Optional[str] = None
        theme_val: Optional[str] = None
        ending_dir_val: Optional[str] = None
        try:
            cdq_val = row["core_dramatic_question"]
        except (KeyError, IndexError):
            pass
        try:
            theme_val = row["theme"]
        except (KeyError, IndexError):
            pass
        try:
            ending_dir_val = row["ending_direction"]
        except (KeyError, IndexError):
            pass
        # SP-8 视角扩展三件套兜底(migration 073 前老库无此列)
        focus_char_val: Optional[str] = None
        reliability_val: Optional[str] = None
        distance_val: Optional[str] = None
        try:
            focus_char_val = row["narrative_focus_character_id"]
        except (KeyError, IndexError):
            pass
        try:
            rel_raw = row["narrator_reliability"]
            if rel_raw in ("reliable", "unreliable", "uncertain"):
                reliability_val = rel_raw
        except (KeyError, IndexError):
            pass
        try:
            dist_raw = row["narrative_distance"]
            if dist_raw in ("omniscient", "limited", "close", "intimate"):
                distance_val = dist_raw
        except (KeyError, IndexError):
            pass
        # 2026-06-01:expected_length 兜底(migration 075 前老库无此列)
        expected_length_val = "medium"
        try:
            el_raw = row["expected_length"]
            if el_raw in ("short", "medium", "long"):
                expected_length_val = el_raw
        except (KeyError, IndexError):
            pass
        # 2026-06-01:chapter_size 区间兜底(migration 077 前老库无此俩列)
        chapter_min_val = 1500
        chapter_max_val = 2500
        try:
            cmin_raw = row["chapter_size_min"]
            if isinstance(cmin_raw, int) and 500 <= cmin_raw <= 8000:
                chapter_min_val = cmin_raw
        except (KeyError, IndexError):
            pass
        try:
            cmax_raw = row["chapter_size_max"]
            if isinstance(cmax_raw, int) and 1500 <= cmax_raw <= 10000:
                chapter_max_val = cmax_raw
        except (KeyError, IndexError):
            pass
        # 保护:max 必须 ≥ min + 500
        if chapter_max_val < chapter_min_val + 500:
            chapter_max_val = chapter_min_val + 500
        return cls(
            id=row["id"],
            user_id=row["user_id"],
            name=row["name"],
            type=row["type"],
            custom_type_name=row["custom_type_name"],
            tags=tags,
            mode=row["mode"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            world_baseline=baseline,
            graph_strength_threshold=threshold,
            narrative_pov=pov,
            inferred_pacing=inferred_pacing_val,
            inferred_pacing_reasoning=inferred_reasoning_val,
            inferred_pacing_metrics=inferred_metrics_val,
            inferred_pacing_at=inferred_at_val,
            core_dramatic_question=cdq_val,
            theme=theme_val,
            ending_direction=ending_dir_val,
            narrative_focus_character_id=focus_char_val,
            narrator_reliability=reliability_val,
            narrative_distance=distance_val,
            expected_length=expected_length_val,
            chapter_size_min=chapter_min_val,
            chapter_size_max=chapter_max_val,
        )
