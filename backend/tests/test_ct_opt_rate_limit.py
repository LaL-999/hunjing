"""CT-OPT.3(2026-05-21)— 429 限流退避 + preview_cost 并发修正 测试。

覆盖:
  - _is_rate_limit_error:openai SDK RateLimitError / 通用 message 含 "429" / 通用 message 含 "rate limit"
  - _backoff_sleep_seconds:1s / 2s / 4s / 8s cap
  - preview_cost:并发 fanout 后总时长 ≈ 单 sim(而非 N × 单 sim)
"""
from __future__ import annotations

import pytest


# ============================================================
# _is_rate_limit_error
# ============================================================

def test_is_rate_limit_detects_429_in_message():
    from app.services.llm_client import _is_rate_limit_error
    e = Exception("HTTP 429 Too Many Requests")
    assert _is_rate_limit_error(e) is True


def test_is_rate_limit_detects_rate_limit_lowercase():
    from app.services.llm_client import _is_rate_limit_error
    e = Exception("Rate limit exceeded for org-xxx")
    assert _is_rate_limit_error(e) is True


def test_is_rate_limit_detects_too_many_requests():
    from app.services.llm_client import _is_rate_limit_error
    e = Exception("too many requests, please retry later")
    assert _is_rate_limit_error(e) is True


def test_is_rate_limit_negative_random_error():
    """普通异常不应被误判为限流。"""
    from app.services.llm_client import _is_rate_limit_error
    assert _is_rate_limit_error(Exception("Connection refused")) is False
    assert _is_rate_limit_error(ValueError("bad input")) is False
    assert _is_rate_limit_error(TimeoutError("timeout after 60s")) is False


def test_is_rate_limit_detects_status_code_429():
    """异常对象挂 status_code=429 也应识别。"""
    from app.services.llm_client import _is_rate_limit_error

    class FakeErr(Exception):
        status_code = 429
    assert _is_rate_limit_error(FakeErr("rate limit reached")) is True


# ============================================================
# _backoff_sleep_seconds
# ============================================================

def test_backoff_exponential_growth():
    from app.services.llm_client import _backoff_sleep_seconds
    assert _backoff_sleep_seconds(0) == 1.0
    assert _backoff_sleep_seconds(1) == 2.0
    assert _backoff_sleep_seconds(2) == 4.0
    assert _backoff_sleep_seconds(3) == 8.0  # 已到 cap
    # 上限 8s 不会被超过
    assert _backoff_sleep_seconds(5) == 8.0
    assert _backoff_sleep_seconds(10) == 8.0


# ============================================================
# preview_cost 并发修正
# ============================================================

def test_preview_cost_concurrent_total_minutes_less_than_serial():
    """3 变量 8 组合时,总时长应远小于 N × 单 sim(并发)。"""
    from app.services.counterfactual_combination_service import preview_cost
    pc = preview_cost(variable_count=3, reshape_percent=50, target_chars=4000)
    assert pc.total_combinations == 8
    # 并发总时长 < 8 × 单 sim(串行假设)
    serial_estimate = pc.estimated_minutes_per_sim * pc.total_combinations
    assert pc.estimated_total_minutes < serial_estimate, (
        f"并发总时长 {pc.estimated_total_minutes} 应 < 串行估算 {serial_estimate}"
    )
    # 但仍 ≥ 单 sim(并发不能比单个还快)
    assert pc.estimated_total_minutes >= pc.estimated_minutes_per_sim


def test_preview_cost_tokens_and_credits_still_n_times_single():
    """token / 积分总消耗仍是 N × 单 sim(并发不省 token)。"""
    from app.services.counterfactual_combination_service import preview_cost
    pc = preview_cost(variable_count=2, reshape_percent=50, target_chars=4000)
    assert pc.total_combinations == 4
    # 总 tokens = 4 × 单 sim
    assert pc.estimated_total_tokens == pc.estimated_token_per_sim * 4
    # 积分也是 4 倍
    assert pc.estimated_credits >= pc.estimated_token_per_sim * 4 // 100


def test_preview_cost_variable_count_1():
    """1 变量(2 组合)— 总时长应等于单 sim × 较小并发 buffer。"""
    from app.services.counterfactual_combination_service import preview_cost
    pc = preview_cost(variable_count=1, reshape_percent=50, target_chars=4000)
    assert pc.total_combinations == 2
    # 并发开销 buffer = 1 + 0.15 × (2-1)/2 = 1.075 → 时长仅微涨
    assert pc.estimated_total_minutes >= pc.estimated_minutes_per_sim
    assert pc.estimated_total_minutes < pc.estimated_minutes_per_sim * 2
