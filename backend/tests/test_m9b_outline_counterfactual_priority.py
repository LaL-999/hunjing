"""Sprint 6.A2 M9.B(2026-05-21)— Outline-first 与反事实冲突优先级 测试。

覆盖:
  - director_system.md 含 铁律 9.6 + 核心条款
  - 当 outline 已批准 + 反事实同时存在时,导演 system prompt 仍包含两层裁定:
      * 结构层走 outline(location / 关键事件 / characters_present 不变)
      * 表现层走反事实(角色行为 / 事件结果走向 / 世界观规则在每幕中体现)
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ============================================================
# helpers
# ============================================================

def _load_director_system_md() -> str:
    """直接读取 prompt 文件原文,不依赖 _load_prompt 私有 helper。"""
    # backend/tests/ → backend/ → repo_root/ → prompts/director_system.md
    repo_root = Path(__file__).resolve().parent.parent.parent
    p = repo_root / "prompts" / "director_system.md"
    assert p.exists(), f"director_system.md 缺失: {p}"
    return p.read_text(encoding="utf-8")


# ============================================================
# 1. 铁律 9.6 静态条款断言
# ============================================================

def test_director_prompt_contains_m9b_priority_rule():
    """director_system.md 必须含铁律 9.6 标题。"""
    text = _load_director_system_md()
    assert "铁律 9.6" in text, "director_system.md 应含 铁律 9.6 标题"
    # 9.6 应排在 9.5 之后、输出格式之前
    idx_95 = text.find("铁律 9.5")
    idx_96 = text.find("铁律 9.6")
    idx_output = text.find("【输出格式】")
    assert idx_95 < idx_96 < idx_output, (
        f"铁律 9.6 应在 9.5 之后、输出格式之前,实际 9.5={idx_95} 9.6={idx_96} output={idx_output}"
    )


def test_m9b_rule_declares_outline_structure_locked():
    """铁律 9.6 必须声明 outline 结构层不可被反事实改动。"""
    text = _load_director_system_md()
    # 截 9.6 段
    start = text.find("铁律 9.6")
    end = text.find("【输出格式】", start)
    assert start >= 0 and end > start
    section = text[start:end]

    # 必须含结构层守约定
    assert "结构骨架" in section or "结构层" in section, (
        "铁律 9.6 应明确 outline 的结构层(骨架)概念"
    )
    # location / characters_present 不变
    assert "location" in section, "9.6 应提及 location 字段约束"
    assert "characters_present" in section, "9.6 应提及 characters_present 字段约束"
    # 不许跳幕 / 删幕 / 加幕
    assert ("跳幕" in section or "删幕" in section or "加幕" in section), (
        "9.6 应明确禁止跳幕/删幕/加幕"
    )


def test_m9b_rule_declares_counterfactual_takes_expression_layer():
    """铁律 9.6 必须声明反事实在"每幕表现层"生效。"""
    text = _load_director_system_md()
    start = text.find("铁律 9.6")
    end = text.find("【输出格式】", start)
    section = text[start:end]

    # 应有"表现层"或"血肉填充"等表达
    assert "表现层" in section or "血肉填充" in section, (
        "9.6 应将反事实定位为'表现层 / 血肉填充'"
    )
    # 应给出具体例子(行为 / 台词 / 事件结果)
    assert ("行为" in section and "事件结果" in section), (
        "9.6 应给出反事实在'行为 / 事件结果'层生效的实例"
    )


def test_m9b_rule_gives_conflict_resolution():
    """铁律 9.6 必须给出冲突裁定:结构走 outline,表现走反事实。"""
    text = _load_director_system_md()
    start = text.find("铁律 9.6")
    end = text.find("【输出格式】", start)
    section = text[start:end]

    # 冲突裁定关键词
    assert ("冲突" in section or "裁定" in section or "优先" in section), (
        "9.6 应有冲突裁定段"
    )
    # 应有反例(禁止把 outline 幕删掉)
    assert "反例" in section or "禁止" in section, (
        "9.6 应给出反例提示禁止删 outline 幕"
    )


# ============================================================
# 2. 与 9.5 协同:不可重复 / 不可冲突
# ============================================================

def test_m9b_does_not_override_world_counterfactual_constitution():
    """9.6 不能直接说"outline 高于世界观反事实",否则会推翻 9.5"宪法"语义。

    9.5 已声明世界观反事实是宪法,9.6 应在保持 outline 结构骨架的前提下,
    让世界观反事实在"表现层 / 招式 / 道具体系"生效 — 而不是粗暴地说"outline > 世界观"。
    """
    text = _load_director_system_md()
    start = text.find("铁律 9.6")
    end = text.find("【输出格式】", start)
    section = text[start:end]

    # 反向断言:9.6 不应有"outline 高于世界观"或"忽略世界观"等粗暴覆盖语
    forbidden = [
        "outline 高于世界观",
        "outline 覆盖世界观",
        "忽略世界观反事实",
        "废除世界观反事实",
    ]
    for kw in forbidden:
        assert kw not in section, (
            f"9.6 不应粗暴推翻 9.5 的宪法语义,但发现禁词 '{kw}'"
        )


# ============================================================
# 3. director system prompt 在 simulation_service 加载完整
# ============================================================

def test_simulation_service_loads_director_prompt_with_m9b():
    """_load_prompt("director_system.md") 加载后应含 9.6 段。"""
    from app.services.simulation_service import _load_prompt
    loaded = _load_prompt("director_system.md")
    assert "铁律 9.6" in loaded, (
        "simulation_service._load_prompt 加载的 director_system.md 应含 9.6"
    )
    assert "outline" in loaded, "director_system.md 应提及 outline 概念"
