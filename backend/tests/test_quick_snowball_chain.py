"""快速模式滚雪球链质量测试(2026-06-02 patch A/B/C).

覆盖:
  - Patch A: compile_final_work _strip_narrative_meta_lines() 清除"> 语体:..."meta
  - Patch B: _extract_last_tail_excerpt() 从 prior_context 提取直接父辈尾巴
  - Patch C: _gather_prior_narratives() 近全 + 远摘双层策略
"""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from app.models.simulation import Simulation
from app.services.compile_final_work import (
    _strip_narrative_meta_lines,
    compile_final_work,
)


# ============================================================
# Patch A — _strip_narrative_meta_lines
# ============================================================

class TestStripNarrativeMetaLines:
    def test_strip_first_line_meta(self):
        narrative = "> 语体:现代都市言情 · 由 AI 根据角色 + 题材自适应\n\n正文第一段。\n\n正文第二段。"
        result = _strip_narrative_meta_lines(narrative)
        assert result == "正文第一段。\n\n正文第二段。"

    def test_strip_middle_meta_lines(self):
        """LLM 偶尔在段中也输出 meta 行(罕见但已观察到)— 也清除."""
        narrative = (
            "正文第一段。\n\n"
            "> 语体:现代都市言情 · 由 AI 根据角色 + 题材自适应\n\n"
            "正文第二段。\n\n"
            "> 语体:武侠 · 由用户自定义\n\n"
            "正文第三段。"
        )
        result = _strip_narrative_meta_lines(narrative)
        assert result == "正文第一段。\n\n正文第二段。\n\n正文第三段。"

    def test_no_meta_line_unchanged(self):
        narrative = "正文第一段。\n\n正文第二段。"
        result = _strip_narrative_meta_lines(narrative)
        assert result == "正文第一段。\n\n正文第二段。"

    def test_empty_returns_empty(self):
        assert _strip_narrative_meta_lines("") == ""
        assert _strip_narrative_meta_lines(None) == ""  # type: ignore[arg-type]

    def test_compress_trailing_blank_lines(self):
        narrative = "正文。\n\n\n\n\n> 语体:xx · 由用户自定义\n\n\n下一段。"
        result = _strip_narrative_meta_lines(narrative)
        # 删 meta 行后压缩多余空行
        assert result == "正文。\n\n下一段。"

    def test_meta_with_chinese_colon_also_stripped(self):
        """中文冒号":"和英文冒号":"都识别."""
        narrative = "> 语体:武侠 · 由 AI 推断\n\n正文。"
        result = _strip_narrative_meta_lines(narrative)
        assert result == "正文。"


# ============================================================
# compile_final_work 集成测试
# ============================================================

class TestCompileFinalWorkStripsMeta:
    @pytest.fixture
    def conn(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE simulations (
                id TEXT PRIMARY KEY,
                narrative TEXT
            )
        """)
        try:
            yield conn
        finally:
            conn.close()

    def test_compile_strips_meta_from_each_section(self, conn):
        """合并时每段都 strip,中段 meta 不残留."""
        prev1_narrative = (
            "> 语体:现代都市言情 · 由 AI 根据角色 + 题材自适应\n\n"
            "第一篇正文段落 1。\n\n"
            "第一篇正文段落 2。"
        )
        prev2_narrative = (
            "> 语体:现代都市言情 · 由 AI 根据角色 + 题材自适应\n\n"
            "第二篇正文段落 1。"
        )
        cur_narrative = (
            "> 语体:现代都市言情 · 由 AI 根据角色 + 题材自适应\n\n"
            "本篇正文段落。"
        )
        conn.execute(
            "INSERT INTO simulations (id, narrative) VALUES (?, ?)",
            ("p1", prev1_narrative),
        )
        conn.execute(
            "INSERT INTO simulations (id, narrative) VALUES (?, ?)",
            ("p2", prev2_narrative),
        )
        sim = _make_sim(context=["p1", "p2"], narrative=cur_narrative)
        result = compile_final_work(conn, sim)
        # 所有 meta 行都应该被清除,合并结果不含 "> 语体"
        assert "> 语体" not in result
        # 内容拼接正确
        assert "第一篇正文段落 1" in result
        assert "第一篇正文段落 2" in result
        assert "第二篇正文段落 1" in result
        assert "本篇正文段落" in result


# ============================================================
# Patch B — _extract_last_tail_excerpt
# ============================================================

class TestExtractLastTailExcerpt:
    def test_single_section_returns_tail(self):
        from app.services.simulation_service import _extract_last_tail_excerpt
        # 单段 header + 长内容(不重复 header)
        text = "【前文 1/1 · 锚点:开头】\n" + ("这是一段长内容。" * 100)
        result = _extract_last_tail_excerpt(text, tail_chars=200)
        assert len(result) <= 200
        # 应该是文本最后部分,不含头部 header
        assert "【前文" not in result
        assert "锚点" not in result

    def test_multi_section_extracts_last_only(self):
        from app.services.simulation_service import _extract_last_tail_excerpt
        text = (
            "【前文 1/2 · 锚点:第一段】\n第一段全文内容。第一段结尾。\n\n---\n\n"
            "【前文 2/2 · 锚点:第二段】\n第二段全文内容。第二段结尾。"
        )
        result = _extract_last_tail_excerpt(text, tail_chars=200)
        # 应该只包含最后一段(第二段)的内容,不含第一段
        assert "第二段" in result
        assert "第一段" not in result

    def test_empty_returns_empty(self):
        from app.services.simulation_service import _extract_last_tail_excerpt
        assert _extract_last_tail_excerpt("") == ""

    def test_skips_section_header(self):
        from app.services.simulation_service import _extract_last_tail_excerpt
        text = "【前文 1/1 · 锚点:测试】\n内容主体"
        result = _extract_last_tail_excerpt(text, tail_chars=100)
        # header 行应该被跳过
        assert not result.startswith("【")
        assert "内容主体" in result


# ============================================================
# Patch C — _gather_prior_narratives 双层策略
# ============================================================

class TestGatherPriorNarrativesDoubleLayer:
    """验证近代用全文尾部 + 远代用摘要."""

    @pytest.fixture
    def conn(self, tmp_path):
        """完整 sim schema(双层策略测试需要)."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE simulations (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                user_id TEXT,
                divergence TEXT,
                reshape_percent INTEGER,
                rounds_planned INTEGER,
                target_chars INTEGER,
                style TEXT,
                custom_style_hint TEXT,
                context_simulation_ids TEXT,
                characters_snapshot TEXT,
                original_tail_excerpt TEXT,
                mode TEXT,
                use_outline_first INTEGER DEFAULT 0,
                anchor_event_id TEXT,
                with_grand_finale INTEGER DEFAULT 0,
                narrative_pacing TEXT,
                chapter_size_chars INTEGER DEFAULT 2000,
                state TEXT,
                current_round INTEGER DEFAULT 0,
                timeline_json TEXT,
                narrative TEXT,
                narrative_summary TEXT,
                error_message TEXT,
                tokens_input INTEGER DEFAULT 0,
                tokens_output INTEGER DEFAULT 0,
                cost_yuan REAL DEFAULT 0,
                final_compiled_narrative TEXT,
                is_final_compilation INTEGER DEFAULT 0,
                compiled_from_sim_id TEXT,
                start_chapter_locked INTEGER,
                created_at TEXT,
                started_at TEXT,
                completed_at TEXT
            )
        """)
        try:
            yield conn
        finally:
            conn.close()

    def test_short_chain_uses_full_text(self, conn):
        """2 篇前作 → 近代用全文,无需 LLM 摘要."""
        from app.services.simulation_service import _gather_prior_narratives
        # 造 2 篇短前作
        for i, content in enumerate(["第一篇内容。" * 200, "第二篇内容。" * 200], start=1):
            conn.execute(
                "INSERT INTO simulations "
                "(id, project_id, user_id, divergence, reshape_percent, rounds_planned, "
                " target_chars, style, custom_style_hint, characters_snapshot, "
                " mode, state, current_round, narrative, created_at) "
                "VALUES (?, ?, ?, ?, 10, 5, 5000, 'A', NULL, '[]', "
                " 'quick', 'done', 5, ?, ?)",
                (f"prev{i}", "proj1", "user1", f"前作{i}", content, f"2026-06-01T0{i}:00:00"),
            )
        sim = _make_sim(
            sim_id="curr",
            project_id="proj1",
            user_id="user1",
            context=["prev1", "prev2"],
        )
        text, in_tok, out_tok, cost = _gather_prior_narratives(conn, sim)
        # 应包含两篇内容
        assert "第一篇内容" in text
        assert "第二篇内容" in text
        # 应标注"全文尾部"
        assert "全文尾部" in text
        # 不应调用 LLM 摘要(近代不摘要)
        assert in_tok == 0
        assert out_tok == 0
        assert cost == 0.0

    def test_long_chain_uses_summary_for_distant(self, conn):
        """5 篇前作时,最远 3 篇用摘要(命中缓存则不调 LLM)."""
        from app.services.simulation_service import _gather_prior_narratives
        # 造 5 篇,每篇配缓存摘要避免触发真 LLM
        for i in range(1, 6):
            conn.execute(
                "INSERT INTO simulations "
                "(id, project_id, user_id, divergence, reshape_percent, rounds_planned, "
                " target_chars, style, custom_style_hint, characters_snapshot, "
                " mode, state, current_round, narrative, narrative_summary, created_at) "
                "VALUES (?, ?, ?, ?, 10, 5, 5000, 'A', NULL, '[]', "
                " 'quick', 'done', 5, ?, ?, ?)",
                (
                    f"prev{i}", "proj1", "user1", f"前作{i}",
                    f"全文 narrative {i}。" * 200,
                    f"摘要内容 {i}",  # 已缓存,避免触发 LLM
                    f"2026-06-01T0{i}:00:00",
                ),
            )
        sim = _make_sim(
            sim_id="curr",
            project_id="proj1",
            user_id="user1",
            context=["prev1", "prev2", "prev3", "prev4", "prev5"],
        )
        text, in_tok, out_tok, cost = _gather_prior_narratives(conn, sim)
        # 最近 2 篇(prev4, prev5) → 全文尾部
        # 更远 3 篇(prev1, prev2, prev3) → 摘要
        assert "全文 narrative 5" in text or "全文 narrative 4" in text
        assert "摘要内容" in text  # 远代用摘要
        # 因摘要命中缓存,不调 LLM
        assert in_tok == 0
        assert out_tok == 0


# ============================================================
# Helpers
# ============================================================

def _make_sim(
    *,
    sim_id: str = "sim1",
    project_id: str = "proj1",
    user_id: str = "user1",
    context: list[str] | None = None,
    narrative: str = "",
) -> Simulation:
    return Simulation(
        id=sim_id,
        project_id=project_id,
        user_id=user_id,
        divergence="测试",
        reshape_percent=10,
        rounds_planned=5,
        target_chars=5000,
        style="A",
        custom_style_hint=None,
        context_simulation_ids=context or [],
        narrative_summary=None,
        characters_snapshot=[],
        state="done",
        current_round=5,
        timeline=None,
        narrative=narrative,
        tokens_input=0,
        tokens_output=0,
        cost_yuan=0.0,
        error_message=None,
        created_at="2026-06-02T00:00:00",
        started_at=None,
        completed_at=None,
    )
