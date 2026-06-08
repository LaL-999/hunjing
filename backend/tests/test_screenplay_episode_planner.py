"""阶段 8.4 — episode_planner MVP 单元测试。

覆盖关键路径:
  1. 无剧本 → EpisodePlanError("尚未生成剧本")
  2. 无场景 → EpisodePlanError("剧本无场景")
  3. 正常切分 — 所有 scene_id 恰好出现 1 次(铁律)
  4. 章节边界优先 — 在 chapter 切换处切集
  5. 短剧目标(2 分钟)→ 多集 / 长剧目标(10 分钟)→ 少集
  6. 标题用首场 summary 前 14 字
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest
import yaml as yamllib

from app.db import get_connection
from app.screenplay.services import episode_planner


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_user(conn) -> str:
    uid = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO users (id, email, plan, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (uid, f"{uid[:6]}@ep-test.com", "free", _now(), _now()),
    )
    return uid


def _make_novel(conn, user_id: str) -> str:
    nid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sp_novels (id, user_id, title, source_format, "
        "source_filename, total_chars, total_chapters, uploaded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (nid, user_id, "t", "txt", "t.txt", 0, 0, _now()),
    )
    return nid


def _make_screenplay(conn, novel_id: str, *, yaml_text: str) -> str:
    sid = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO sp_screenplays (id, novel_id, yaml_text, stats_json, "
        "warnings_json, failed_chapters_json, schema_version, model_name, "
        "created_at, optimization_origin) "
        "VALUES (?, ?, ?, '{}', '[]', '[]', '1.0', NULL, ?, 'initial')",
        (sid, novel_id, yaml_text, _now()),
    )
    return sid


def _build_yaml(scenes: list[dict]) -> str:
    """造一个合法的剧本 yaml,scenes 由参数指定。"""
    full = {
        "meta": {"schema_version": "1.0", "title": "测试"},
        "characters": [],
        "locations": [],
        "scenes": scenes,
    }
    return yamllib.dump(full, allow_unicode=True)


def _scene(
    idx: int, *, chapter: int = 1,
    elements_count: int = 12,
    transition: str = "CUT_TO",
    summary: str = "",
) -> dict:
    """工具:造一个 scene yaml dict。"""
    return {
        "id": f"scene_{idx:03d}",
        "number": idx,
        "heading": {"int_ext": "INT", "location_id": "loc_001", "time_of_day": "日"},
        "summary": summary or f"第 {idx} 场摘要",
        "characters_present": [],
        "source": {"chapter": chapter, "paragraph_range": [1, 5]},
        "transition_to_next": transition,
        "elements": [{"type": "action", "text": f"动作 {i}"} for i in range(elements_count)],
    }


# ============================================================
# 测试
# ============================================================


class TestEpisodePlanner:
    def test_no_screenplay_raises(self):
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            conn.commit()
            with pytest.raises(episode_planner.EpisodePlanError, match="尚未生成剧本"):
                episode_planner.plan_episodes(nid, user_id=uid)
        finally:
            conn.close()

    def test_no_scenes_raises(self):
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            _make_screenplay(conn, nid, yaml_text=_build_yaml(scenes=[]))
            conn.commit()
            with pytest.raises(episode_planner.EpisodePlanError, match="剧本无场景"):
                episode_planner.plan_episodes(nid, user_id=uid)
        finally:
            conn.close()

    def test_all_scene_ids_exactly_once(self):
        """**铁律**:所有 scene_id 必须出现且只出现 1 次。"""
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            scenes = [_scene(i, chapter=(i + 1) // 5) for i in range(1, 21)]
            _make_screenplay(conn, nid, yaml_text=_build_yaml(scenes))
            conn.commit()

            plan = episode_planner.plan_episodes(
                nid, user_id=uid, target_minutes_per_ep=3.0,
            )
            all_ids = [sid for ep in plan.episodes for sid in ep.scene_ids]
            # 每个 scene_id 恰好出现一次
            assert len(all_ids) == 20
            assert len(set(all_ids)) == 20
            # 顺序保持
            assert all_ids == [f"scene_{i:03d}" for i in range(1, 21)]
        finally:
            conn.close()

    def test_chapter_boundary_preferred(self):
        """章节切换处应该作为分集边界(在 70%-130% 时间窗口内)。"""
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            # 8 场,每场 ~0.5 分钟(12 elements / 25),目标 1.5 分钟:
            # 章节 1: scene 1-3(~1.5 分钟)
            # 章节 2: scene 4-6
            # 章节 3: scene 7-8
            scenes = []
            for i in range(1, 9):
                ch = 1 if i <= 3 else (2 if i <= 6 else 3)
                scenes.append(_scene(i, chapter=ch, elements_count=12))
            _make_screenplay(conn, nid, yaml_text=_build_yaml(scenes))
            conn.commit()

            plan = episode_planner.plan_episodes(
                nid, user_id=uid, target_minutes_per_ep=1.5,
            )
            # 每集的 last_chapter 应该跟下一集的 first_chapter 不同
            # (说明 boundary 落在 chapter 切换处)
            for i, ep in enumerate(plan.episodes[:-1]):
                next_ep = plan.episodes[i + 1]
                if ep.boundary_reason == "chapter_change":
                    assert ep.last_chapter != next_ep.first_chapter
        finally:
            conn.close()

    def test_short_target_more_episodes(self):
        """目标时长越短,集数越多(短剧场景)。"""
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            # 20 场,每场 0.5 分钟 = 10 分钟总时长
            scenes = [_scene(i, chapter=(i + 1) // 5, elements_count=12) for i in range(1, 21)]
            _make_screenplay(conn, nid, yaml_text=_build_yaml(scenes))
            conn.commit()

            # 短剧 1.5 分钟 / 集
            short_plan = episode_planner.plan_episodes(
                nid, user_id=uid, target_minutes_per_ep=1.5,
            )
            # 长剧 5 分钟 / 集
            long_plan = episode_planner.plan_episodes(
                nid, user_id=uid, target_minutes_per_ep=5.0,
            )
            assert len(short_plan.episodes) > len(long_plan.episodes)
        finally:
            conn.close()

    def test_title_uses_first_scene_summary(self):
        """标题应该带首场 summary 前 14 字。"""
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            scenes = [
                _scene(1, chapter=1, summary="林墨初入潘西"),
                _scene(2, chapter=1, summary="..."),
                _scene(3, chapter=1, summary="..."),
            ]
            _make_screenplay(conn, nid, yaml_text=_build_yaml(scenes))
            conn.commit()

            plan = episode_planner.plan_episodes(
                nid, user_id=uid, target_minutes_per_ep=10.0,
            )
            assert plan.episodes[0].title.startswith("第 1 集")
            assert "林墨初入潘西" in plan.episodes[0].title
        finally:
            conn.close()

    def test_total_minutes_close_to_sum(self):
        """total_minutes 应该约等于所有 episode est_minutes 之和。"""
        conn = get_connection()
        try:
            uid = _make_user(conn)
            nid = _make_novel(conn, uid)
            scenes = [_scene(i, chapter=1, elements_count=10) for i in range(1, 6)]
            _make_screenplay(conn, nid, yaml_text=_build_yaml(scenes))
            conn.commit()

            plan = episode_planner.plan_episodes(
                nid, user_id=uid, target_minutes_per_ep=3.0,
            )
            sum_minutes = sum(ep.est_minutes for ep in plan.episodes)
            assert abs(plan.total_minutes - sum_minutes) < 0.01
            assert plan.total_scenes == 5
        finally:
            conn.close()

    def test_cross_user_access_raises(self):
        """user A 访问 user B 的 novel → 视为不存在(无剧本)。"""
        conn = get_connection()
        try:
            user_a = _make_user(conn)
            user_b = _make_user(conn)
            novel_b = _make_novel(conn, user_b)
            scenes = [_scene(1, chapter=1)]
            _make_screenplay(conn, novel_b, yaml_text=_build_yaml(scenes))
            conn.commit()
            # user_a 用 user_b 的 novel_id → screenplay_store JOIN 校验返 None
            # → episode_planner 抛 EpisodePlanError
            with pytest.raises(episode_planner.EpisodePlanError, match="尚未生成剧本"):
                episode_planner.plan_episodes(novel_b, user_id=user_a)
        finally:
            conn.close()
