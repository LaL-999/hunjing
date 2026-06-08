"""章节体系测试(2026-06-01)— narrative_chapterizer / sim_chapter_helper / compile_final_work."""
from __future__ import annotations

import sqlite3
import uuid

import pytest

from app.models.simulation import Simulation
from app.services.narrative_chapterizer import chapterize
from app.services.sim_chapter_helper import (
    _get_chapter_size_range_for_project,
    compute_start_chapter_for_sim,
    get_effective_start_chapter,
)
from app.services.compile_final_work import (
    compile_final_work,
    maybe_compile_and_persist_final_work,
)


# ============================================================
# narrative_chapterizer.chapterize
# ============================================================

class TestChapterize:
    def test_empty_returns_empty_list(self):
        assert chapterize("", 1500, 2500) == []
        assert chapterize("   \n\n   ", 1500, 2500) == []

    def test_short_text_one_chapter(self):
        text = "短短的一段文字。"
        result = chapterize(text, 1500, 2500)
        assert len(result) == 1
        assert result[0]["global_chapter_no"] == 1
        assert result[0]["char_count"] == len(text)
        assert "短短" in result[0]["first_words"]

    def test_long_text_multiple_chapters_paragraph_break(self):
        # 8 段 × 400 字 = 3200 字,区间 1500~2500 → 应切 2 章左右
        para = "这是一段填充文字" * 50  # ~400 字
        text = "\n\n".join([para] * 8)
        result = chapterize(text, 1500, 2500)
        assert len(result) >= 2
        # 每章字数都落在合理范围(只末章可能例外)
        for ch in result[:-1]:
            assert 1500 <= ch["char_count"] <= 2500 + 200, (
                f"chapter {ch['global_chapter_no']} char_count={ch['char_count']} out of range"
            )

    def test_start_chapter_no_offset(self):
        para = "填充内容" * 100  # 400 字
        text = "\n\n".join([para] * 5)
        result = chapterize(text, 1000, 1800, start_chapter_no=6)
        # 滚雪球场景:前篇结束第 5 章,本 sim 起步第 6 章
        assert result[0]["global_chapter_no"] == 6
        assert result[1]["global_chapter_no"] == 7

    def test_force_cut_at_max_when_no_paragraph_break(self):
        # 全是单一长段,无 \n\n,会强切
        text = "无段落分隔" * 800  # 4000 字
        result = chapterize(text, 1500, 2500)
        assert len(result) >= 2
        # 第一章会强切在 max(2500)附近
        assert result[0]["char_count"] <= 2500 + 50  # +50 容差给句末标点扫描

    def test_first_words_extraction(self):
        text = "他推开门走进客厅,所有人都看着他。" + "\n\n" + "填充段" * 1000
        result = chapterize(text, 1000, 2000)
        # 第一章 first_words 应能识别开头
        assert "他推开门" in result[0]["first_words"]


# ============================================================
# sim_chapter_helper
# ============================================================

class TestComputeStartChapter:
    @pytest.fixture
    def conn(self, tmp_path):
        """造一个临时 SQLite 含 projects + simulations 最小 schema."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE projects (
                id TEXT PRIMARY KEY,
                chapter_size_min INTEGER DEFAULT 1500,
                chapter_size_max INTEGER DEFAULT 2500
            )
        """)
        conn.execute("""
            CREATE TABLE simulations (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                narrative TEXT,
                context_simulation_ids TEXT,
                start_chapter_locked INTEGER
            )
        """)
        try:
            yield conn
        finally:
            conn.close()

    def test_no_ancestors_returns_1(self, conn):
        proj_id = str(uuid.uuid4())
        conn.execute("INSERT INTO projects (id) VALUES (?)", (proj_id,))
        sim = self._make_sim(project_id=proj_id, context=[])
        assert compute_start_chapter_for_sim(conn, sim) == 1

    def test_one_ancestor_2_chapters(self, conn):
        proj_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO projects (id, chapter_size_min, chapter_size_max) "
            "VALUES (?, 1500, 2500)",
            (proj_id,),
        )
        # 前篇 ~4000 字 → 平均章长 2000 → 约 2 章
        prev_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO simulations (id, project_id, narrative, context_simulation_ids) "
            "VALUES (?, ?, ?, ?)",
            (prev_id, proj_id, "填充" * 2000, "[]"),
        )
        sim = self._make_sim(project_id=proj_id, context=[prev_id])
        # 前篇 4000 字 / 平均 2000 = 2 章 → 本 sim 起步第 3 章
        assert compute_start_chapter_for_sim(conn, sim) == 3

    def test_multiple_ancestors_accumulated(self, conn):
        proj_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO projects (id, chapter_size_min, chapter_size_max) "
            "VALUES (?, 1500, 2500)",
            (proj_id,),
        )
        # 两个前篇,各 3000 字 → 每篇约 1-2 章
        prev1 = str(uuid.uuid4())
        prev2 = str(uuid.uuid4())
        for pid in (prev1, prev2):
            conn.execute(
                "INSERT INTO simulations (id, project_id, narrative, context_simulation_ids) "
                "VALUES (?, ?, ?, ?)",
                (pid, proj_id, "x" * 3000, "[]"),
            )
        sim = self._make_sim(project_id=proj_id, context=[prev1, prev2])
        # 每篇 3000 字 / 平均 2000 = ceil(1.5)=2 章 × 2 = 4 → 本 sim 起步第 5 章
        result = compute_start_chapter_for_sim(conn, sim)
        assert result == 5

    def test_missing_ancestor_silently_skipped(self, conn):
        proj_id = str(uuid.uuid4())
        conn.execute("INSERT INTO projects (id) VALUES (?)", (proj_id,))
        # context 指向不存在的 sim id
        sim = self._make_sim(project_id=proj_id, context=["nonexistent-id"])
        # 缺前篇 → total 0 + 1 = 1(降级,不报错)
        assert compute_start_chapter_for_sim(conn, sim) == 1

    def test_get_effective_start_chapter(self):
        sim_a = self._make_sim(start_chapter_locked=5)
        assert get_effective_start_chapter(sim_a) == 5
        sim_b = self._make_sim(start_chapter_locked=None)
        assert get_effective_start_chapter(sim_b) == 1
        sim_c = self._make_sim(start_chapter_locked=0)
        # 0 视为无效 → 降级 1
        assert get_effective_start_chapter(sim_c) == 1

    def test_get_chapter_size_range_default_when_missing_columns(self, tmp_path):
        # 老库无 chapter_size_min/max 列 → 默认 1500/2500
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY)")
        proj_id = str(uuid.uuid4())
        conn.execute("INSERT INTO projects (id) VALUES (?)", (proj_id,))
        try:
            cmin, cmax = _get_chapter_size_range_for_project(conn, proj_id)
            assert cmin == 1500
            assert cmax == 2500
        finally:
            conn.close()

    @staticmethod
    def _make_sim(
        *,
        project_id: str = "p1",
        context: list[str] | None = None,
        start_chapter_locked: int | None = None,
        narrative: str = "",
    ) -> Simulation:
        return Simulation(
            id=str(uuid.uuid4()),
            project_id=project_id,
            user_id="u1",
            divergence="",
            reshape_percent=20,
            rounds_planned=10,
            target_chars=20000,
            style="A",
            custom_style_hint=None,
            context_simulation_ids=context or [],
            narrative_summary=None,
            characters_snapshot=[],
            state="done",
            current_round=10,
            timeline=None,
            narrative=narrative,
            tokens_input=0,
            tokens_output=0,
            cost_yuan=0.0,
            error_message=None,
            created_at="2026-06-01T00:00:00",
            started_at=None,
            completed_at=None,
            start_chapter_locked=start_chapter_locked,
        )


# ============================================================
# compile_final_work
# ============================================================

class TestCompileFinalWork:
    @pytest.fixture
    def conn(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        # 2026-06-01 v2:完整 schema,因为新 maybe_compile_and_persist_final_work
        # 会 INSERT 完整 sim 行(独立合并产物)
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
                narrative TEXT,
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

    def test_no_ancestors_returns_only_current(self, conn):
        sim = TestComputeStartChapter._make_sim(
            context=[], narrative="独立的一篇产物。",
        )
        result = compile_final_work(conn, sim)
        assert result == "独立的一篇产物。"

    def test_with_ancestors_joins_in_order(self, conn):
        prev1 = "第一篇结尾...继续。"
        prev2 = "第二篇的中段。"
        conn.execute(
            "INSERT INTO simulations (id, narrative) VALUES (?, ?)",
            ("p1", prev1),
        )
        conn.execute(
            "INSERT INTO simulations (id, narrative) VALUES (?, ?)",
            ("p2", prev2),
        )
        cur = "本篇正文。"
        sim = TestComputeStartChapter._make_sim(
            context=["p1", "p2"], narrative=cur,
        )
        result = compile_final_work(conn, sim)
        assert result == f"{prev1}\n\n{prev2}\n\n{cur}"

    def test_empty_ancestor_skipped(self, conn):
        conn.execute(
            "INSERT INTO simulations (id, narrative) VALUES (?, ?)",
            ("p1", ""),  # 空 narrative
        )
        sim = TestComputeStartChapter._make_sim(
            context=["p1"], narrative="本篇正文。",
        )
        result = compile_final_work(conn, sim)
        # 跳过空前篇 → 只剩本 sim
        assert result == "本篇正文。"

    def test_missing_ancestor_skipped(self, conn):
        sim = TestComputeStartChapter._make_sim(
            context=["nonexistent"], narrative="本篇正文。",
        )
        result = compile_final_work(conn, sim)
        # 缺前篇静默跳过
        assert result == "本篇正文。"

    def test_maybe_compile_skipped_when_no_grand_finale(self, conn):
        sim = TestComputeStartChapter._make_sim(
            context=["p1"], narrative="本篇正文。",
        )
        sim.with_grand_finale = 0  # 默认就是 0
        result = maybe_compile_and_persist_final_work(conn, sim)
        assert result is False

    def test_maybe_compile_skipped_when_no_ancestors(self, conn):
        sim = TestComputeStartChapter._make_sim(
            context=[], narrative="独立产物。",
        )
        sim.with_grand_finale = 1
        result = maybe_compile_and_persist_final_work(conn, sim)
        # 孤本即使走向终章也不合并(narrative 自己就是最终作品)
        assert result is False

    def test_maybe_compile_creates_independent_sim_row(self, conn):
        """2026-06-01 v2:合并产物建独立 sim 行(不再 UPDATE 源 sim).

        验证:
          - 返 True(创建了新行)
          - 新 sim 行存在,is_final_compilation=1,compiled_from_sim_id=源
          - 新 sim 的 narrative = 前篇 + 本篇拼接
          - 新 sim 的 mode='compilation', cost=0, rounds=1
        """
        # 先 INSERT 一个源 sim + 一个前篇
        conn.execute(
            "INSERT INTO simulations (id, narrative) VALUES (?, ?)",
            ("ancestor1", "前篇正文。"),
        )
        conn.execute(
            "INSERT INTO simulations (id, narrative, project_id, user_id) "
            "VALUES (?, ?, ?, ?)",
            ("source-sim", "本篇正文。", "proj1", "user1"),
        )
        sim = TestComputeStartChapter._make_sim(
            context=["ancestor1"], narrative="本篇正文。",
        )
        sim.id = "source-sim"
        sim.project_id = "proj1"
        sim.user_id = "user1"
        sim.with_grand_finale = 1
        sim.state = "done"

        result = maybe_compile_and_persist_final_work(conn, sim)
        assert result is True

        # 查新建的合并 sim
        compilation_rows = conn.execute(
            "SELECT id, narrative, is_final_compilation, compiled_from_sim_id, "
            "       mode, cost_yuan, state "
            "FROM simulations WHERE is_final_compilation=1"
        ).fetchall()
        assert len(compilation_rows) == 1
        comp = compilation_rows[0]
        assert comp["is_final_compilation"] == 1
        assert comp["compiled_from_sim_id"] == "source-sim"
        assert comp["narrative"] == "前篇正文。\n\n本篇正文。"
        assert comp["mode"] == "compilation"
        assert comp["cost_yuan"] == 0
        assert comp["state"] == "done"

    def test_maybe_compile_dedupes_same_source(self, conn):
        """同 source 已有合并产物 → 不重复创建(避免 sim done 触发多次合并)."""
        conn.execute(
            "INSERT INTO simulations (id, narrative) VALUES (?, ?)",
            ("ancestor1", "前篇。"),
        )
        conn.execute(
            "INSERT INTO simulations (id, narrative, project_id, user_id) "
            "VALUES (?, ?, ?, ?)",
            ("source-sim", "本篇。", "proj1", "user1"),
        )
        sim = TestComputeStartChapter._make_sim(
            context=["ancestor1"], narrative="本篇。",
        )
        sim.id = "source-sim"
        sim.project_id = "proj1"
        sim.user_id = "user1"
        sim.with_grand_finale = 1
        sim.state = "done"

        # 第一次:创建
        assert maybe_compile_and_persist_final_work(conn, sim) is True
        # 第二次:跳过(判重)
        assert maybe_compile_and_persist_final_work(conn, sim) is False
        # 只有 1 个合并 sim
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM simulations WHERE is_final_compilation=1"
        ).fetchone()
        assert count["c"] == 1

    def test_maybe_compile_skipped_when_sim_is_already_compilation(self, conn):
        """sim 自己已是合并产物 → 不再触发(防止链式合并循环)."""
        sim = TestComputeStartChapter._make_sim(
            context=["a", "b"], narrative="合并后的文本",
        )
        sim.with_grand_finale = 1
        sim.state = "done"
        sim.is_final_compilation = 1  # 自身已是合并产物
        result = maybe_compile_and_persist_final_work(conn, sim)
        assert result is False
