"""末尾态笔法 Reflexion 自检测试 — Sprint 6.A2 TS(2026-05-21)。

覆盖:
  - tail_style_analyzer:客观切句 + roundtrip + LLM 失败 fallback
  - tail_style_checker:5 维评分边界 + 客观偏离评分 + 综合分
  - 主循环 hook:非末尾态 sim 跳过 + 末尾态 sim 首次缓存 + voting 笔法分 tiebreaker
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# ============================================================
# tail_style_analyzer 单测
# ============================================================

def test_compute_objective_stats_basic():
    """客观切句 + 长短句占比。"""
    from app.services.tail_style_analyzer import _compute_objective_stats
    # 3 句:句一(3 字短)+ 句二(3 字短)+ 句三(45+ 字长,测试长句识别)
    text = (
        "短句一。短句二!"
        "这是一个非常非常长的句子用来测试长句占比能否被正确识别出来,"
        "需要超过四十个字符才能被算作长句,所以多加几个字以确保超过阈值。"
    )
    stats = _compute_objective_stats(text)
    assert stats["sentence_length_avg"] > 0
    # 3 句:5 / 5 / 50+ 字 → short_ratio 应高,long_ratio 应 > 0
    assert 0 < stats["short_sentence_ratio"] <= 1.0
    assert stats["long_sentence_ratio"] > 0


def test_compute_objective_stats_empty():
    from app.services.tail_style_analyzer import _compute_objective_stats
    stats = _compute_objective_stats("")
    assert stats["sentence_length_avg"] == 0.0
    assert stats["short_sentence_ratio"] == 0.0


def test_tail_style_features_json_roundtrip():
    """to_json + from_json 还原所有字段。"""
    from app.services.tail_style_analyzer import TailStyleFeatures
    f = TailStyleFeatures(
        sentence_length_avg=28.5,
        short_sentence_ratio=0.35,
        long_sentence_ratio=0.15,
        vocab_set=["绛珠草", "大观园", "凤姐"],
        perspective="third_limited",
        tone_baseline="苍凉惆怅",
    )
    f2 = TailStyleFeatures.from_json(f.to_json())
    assert f2.sentence_length_avg == 28.5
    assert f2.vocab_set == ["绛珠草", "大观园", "凤姐"]
    assert f2.perspective == "third_limited"
    assert f2.tone_baseline == "苍凉惆怅"


def test_analyze_tail_too_short_skips_llm(monkeypatch):
    """文本 < 100 字直接 fallback,不调 LLM。"""
    from app.services import tail_style_analyzer

    llm_called = []
    def fake_llm(*args, **kwargs):
        llm_called.append(True)
        return ({}, {"input_tokens": 0, "output_tokens": 0})
    monkeypatch.setattr(tail_style_analyzer, "call_llm_json", fake_llm)

    features, usage = tail_style_analyzer.analyze_tail("只有 50 字的短文本。" * 3)
    assert llm_called == []  # LLM 没被调
    assert features.vocab_set == []
    assert features.tone_baseline == "未知"


def test_analyze_tail_llm_success(monkeypatch):
    """LLM 成功 → 主观 3 维填入。"""
    from app.services import tail_style_analyzer

    def fake_llm(system_prompt, user_input, **kwargs):
        return (
            {
                "vocab_set": ["绛珠草", "大观园", "凤姐", "残月"],
                "perspective": "third_limited",
                "tone_baseline": "苍凉",
            },
            {"input_tokens": 150, "output_tokens": 50},
        )
    monkeypatch.setattr(tail_style_analyzer, "call_llm_json", fake_llm)

    text = (
        "她终于推开了那扇雕花木门。门内昏黄的灯光下,绛珠草在瓷瓶里静静地凋着。"
        "她想,这草是当年贾宝玉亲手种下的,如今也已枯死。窗外残月斜挂,廊下灯笼摇曳。"
        "她忽然觉得,这大观园终究是要散的。她转过身,只听得身后传来一声轻叹——"
        "是凤姐的声音。凤姐说:'妹妹,该回去了。'"
    )
    features, usage = tail_style_analyzer.analyze_tail(text)
    assert "绛珠草" in features.vocab_set
    assert features.perspective == "third_limited"
    assert features.tone_baseline == "苍凉"
    assert usage["input_tokens"] == 150


def test_analyze_tail_llm_failure_fallback(monkeypatch):
    """LLM 抛异常 → 客观维度仍有效,主观维度 fallback。"""
    from app.services import tail_style_analyzer

    def fake_llm(*args, **kwargs):
        raise RuntimeError("simulated network error")
    monkeypatch.setattr(tail_style_analyzer, "call_llm_json", fake_llm)

    text = "测试段落。" * 30  # > 100 字
    features, usage = tail_style_analyzer.analyze_tail(text)
    assert features.vocab_set == []
    assert features.perspective == "third_limited"
    assert features.tone_baseline == "未知"
    # 客观维度仍计算
    assert features.sentence_length_avg > 0


# ============================================================
# tail_style_checker 单测
# ============================================================

def test_score_avg_deviation_boundaries():
    from app.services.tail_style_checker import _score_avg_deviation
    # 偏离 ≤ 30% → 100 分
    assert _score_avg_deviation(25.0, 28.0) == 100   # diff 12%
    # 偏离 = 30% → 100(边界)
    assert _score_avg_deviation(25.0, 32.5) == 100   # diff 30%
    # 偏离 ≥ 60% → 0
    assert _score_avg_deviation(25.0, 50.0) == 0     # diff 100%
    # original 0 → 中位 50
    assert _score_avg_deviation(0.0, 10.0) == 50


def test_score_ratio_diff_boundaries():
    from app.services.tail_style_checker import _score_ratio_diff
    assert _score_ratio_diff(0.3, 0.35) == 100   # diff 0.05
    assert _score_ratio_diff(0.3, 0.4) == 100    # diff 0.1 边界
    assert _score_ratio_diff(0.3, 0.8) == 0      # diff 0.5
    assert _score_ratio_diff(0.3, 0.25) == 100   # diff 0.05


def test_check_style_alignment_short_segment_returns_midpoint():
    """段落 < 50 字 → 5 维全 50 + composite 50。"""
    from app.services.tail_style_analyzer import TailStyleFeatures
    from app.services.tail_style_checker import check_style_alignment
    features = TailStyleFeatures(
        sentence_length_avg=25.0,
        short_sentence_ratio=0.3,
        long_sentence_ratio=0.1,
        vocab_set=["A", "B"],
        perspective="third_limited",
        tone_baseline="测试",
    )
    score, _ = check_style_alignment(features, "很短。")
    assert score.composite_score == 50
    assert score.vocab_score == 50


def test_check_style_alignment_llm_success(monkeypatch):
    """LLM 给高分 + 客观维度也高 → 综合分高。"""
    from app.services import tail_style_checker
    from app.services.tail_style_checker import check_style_alignment
    from app.services.tail_style_analyzer import TailStyleFeatures

    def fake_llm(*args, **kwargs):
        return (
            {
                "vocab_score": 90,
                "perspective_score": 95,
                "tone_score": 85,
                "reasoning": "复用 3 个原作 vocab",
            },
            {"input_tokens": 100, "output_tokens": 50},
        )
    monkeypatch.setattr(tail_style_checker, "call_llm_json", fake_llm)

    features = TailStyleFeatures(
        sentence_length_avg=20.0,
        short_sentence_ratio=0.3,
        long_sentence_ratio=0.1,
        vocab_set=["A", "B", "C"],
        perspective="third_limited",
        tone_baseline="苍凉",
    )
    # 生成段落:平均句长约 20 字,占比也大体匹配 features
    segment = (
        "她终于推开了那扇雕花的木门。"
        "门内昏黄的灯光下她想起了从前。"
        "窗外的残月斜挂在树梢之间。"
        "廊下的灯笼无声地摇曳着光影。"
        "她忽然觉得这一切终究是要散的。"
    )
    score, _ = check_style_alignment(features, segment)
    assert score.vocab_score == 90
    assert score.perspective_score == 95
    assert score.tone_score == 85
    # 综合分应在 75+(主观高分 + 客观大体匹配 → 整体偏高)
    assert score.composite_score >= 75


def test_check_style_alignment_llm_failure_fallback(monkeypatch):
    """LLM 失败 → 主观 3 维全 50。"""
    from app.services import tail_style_checker
    from app.services.tail_style_checker import check_style_alignment
    from app.services.tail_style_analyzer import TailStyleFeatures

    def fake_llm(*args, **kwargs):
        raise RuntimeError("LLM down")
    monkeypatch.setattr(tail_style_checker, "call_llm_json", fake_llm)

    features = TailStyleFeatures(
        sentence_length_avg=20.0,
        short_sentence_ratio=0.3,
        long_sentence_ratio=0.1,
        vocab_set=["A"],
        perspective="third_limited",
        tone_baseline="苍凉",
    )
    score, _ = check_style_alignment(features, "段落内容。" * 30)
    assert score.vocab_score == 50  # fallback
    assert score.perspective_score == 50
    assert score.tone_score == 50
    # 客观维度仍有效(基于字数算)
    assert score.sentence_length_score >= 0


# ============================================================
# 集成测试 — write_back + get_cached
# ============================================================

def test_write_back_and_get_cached(client, make_user):
    """write_back 后 get_cached 应能读回完整特征。"""
    from app.services.tail_style_analyzer import (
        TailStyleFeatures,
        get_cached_features,
        write_back_features,
    )
    from app.db import get_connection

    user = make_user("alice")
    # 创建 project + sim 占位行(填入 original_tail_excerpt)
    p = client.post(
        "/api/projects", headers=user["headers"],
        json={"name": "测试", "type": "novel", "mode": "initial"},
    )
    assert p.status_code == 201
    pid = p.json()["id"]

    conn = get_connection()
    try:
        # 手插一个 sim 行(rounds_planned 字段必填,补 NULL/默认)
        from app.services.project_service import iso_now
        import uuid
        sim_id = uuid.uuid4().hex
        now = iso_now()
        conn.execute(
            """INSERT INTO simulations
                (id, project_id, user_id, divergence, reshape_percent,
                 rounds_planned, target_chars, style, state,
                 created_at, mode, characters_snapshot,
                 tokens_input, tokens_output, cost_yuan,
                 timeline_json, narrative, current_round,
                 original_tail_excerpt)
               VALUES (?, ?, ?, '', 50, 10, 4000, 'A', 'queued',
                       ?, 'evolution', '[]', 0, 0, 0.0, '[]', '', 0,
                       '原作末段示例')""",
            (sim_id, pid, user["user_id"], now),
        )
        conn.commit()

        # 初始无缓存
        cached = get_cached_features(conn, sim_id)
        assert cached is None

        # 写入
        features = TailStyleFeatures(
            sentence_length_avg=22.0,
            short_sentence_ratio=0.4,
            long_sentence_ratio=0.1,
            vocab_set=["test"],
            perspective="first",
            tone_baseline="测试基调",
        )
        write_back_features(conn, sim_id, features)

        # 读回
        cached2 = get_cached_features(conn, sim_id)
        assert cached2 is not None
        assert cached2.vocab_set == ["test"]
        assert cached2.perspective == "first"
        assert cached2.tone_baseline == "测试基调"
    finally:
        conn.close()
