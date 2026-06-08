"""
BYOK LLM 路由层端到端测试。

测试矩阵:
  1. 无 user_id → 走平台默认(get_text_llm 被调)
  2. 有 user_id 但无 BYOK → 走平台默认(BYOK lookup 返 None)
  3. 有 user_id + BYOK active + 有 default config → 走用户 key
  4. user_id 显式参数 优先于 ContextVar
  5. BYOK 订阅过期 → 走平台默认(get_active_llm_config 返 None)
  6. ContextVar 在 thread 间传递(copy_context.run 包装)

mock 策略:
  - _openai_compat_call_json/text 是底层 HTTP 调用,monkeypatch 为返回 fake
  - get_text_llm() 默认 adapter monkeypatch 为返回 fake
  - 这样不依赖网络,纯路由层测试
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.models.byok import BYOKConfigUpsertRequest
from app.services import byok_service
from app.services.byok_context import (
    capture_current_context,
    get_current_user_id,
    set_current_user_id,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def fresh_user_id(make_user):
    """每个 test 一个干净的 user — 用 conftest 的 make_user 工厂"""
    u = make_user("byoktest")
    return u["user_id"], u["token"]


@pytest.fixture(autouse=True)
def reset_byok_context():
    """每个测试前后清 ContextVar"""
    set_current_user_id(None)
    yield
    set_current_user_id(None)


@pytest.fixture
def captured_llm_calls(monkeypatch):
    """捕获所有 LLM 底层调用 — monkeypatch `_openai_compat_call_json/text`"""
    captured = []

    def fake_json(system_prompt, user_input, **kwargs):
        captured.append({
            "fn": "json",
            "system_prompt": system_prompt[:80],
            "api_key": kwargs.get("api_key"),
            "api_base": kwargs.get("api_base"),
            "model": kwargs.get("model"),
            "vendor_label": kwargs.get("vendor_label"),
        })
        return ({"ok": True}, {"input_tokens": 100, "output_tokens": 50})

    def fake_text(system_prompt, user_input, **kwargs):
        captured.append({
            "fn": "text",
            "system_prompt": system_prompt[:80],
            "api_key": kwargs.get("api_key"),
            "api_base": kwargs.get("api_base"),
            "model": kwargs.get("model"),
            "vendor_label": kwargs.get("vendor_label"),
        })
        return ("fake response", {"input_tokens": 100, "output_tokens": 50})

    # mock 底层 — call_llm_json 走 BYOK 时直接调 _openai_compat_call_json
    monkeypatch.setattr(
        "app.services.llm_client._openai_compat_call_json",
        fake_json,
    )
    monkeypatch.setattr(
        "app.services.llm_client._openai_compat_call_text",
        fake_text,
    )

    # mock 平台默认路径的 adapter
    class FakeAdapter:
        def call_json(self, sp, ui, **kw):
            captured.append({
                "fn": "json",
                "system_prompt": sp[:80],
                "vendor_label": "PLATFORM_DEFAULT",
            })
            return ({"ok": True}, {"input_tokens": 100, "output_tokens": 50})

        def call_text(self, sp, ui, **kw):
            captured.append({
                "fn": "text",
                "system_prompt": sp[:80],
                "vendor_label": "PLATFORM_DEFAULT",
            })
            return ("fake response", {"input_tokens": 100, "output_tokens": 50})

    monkeypatch.setattr(
        "app.services.llm_routing.router.get_text_llm",
        lambda: FakeAdapter(),
    )

    return captured


# ============================================================
# Tests
# ============================================================

def test_no_user_id_uses_platform_default(captured_llm_calls):
    """无 user_id 设置时,走平台默认 adapter"""
    from app.services.llm_client import call_llm_json

    result, usage = call_llm_json("you are a test", "hi")
    assert result == {"ok": True}

    assert len(captured_llm_calls) == 1
    assert captured_llm_calls[0]["vendor_label"] == "PLATFORM_DEFAULT"


def test_user_without_byok_uses_platform_default(fresh_user_id, captured_llm_calls):
    """有 user_id 但无 BYOK 订阅 → 走平台默认"""
    from app.services.llm_client import call_llm_json

    user_id, _ = fresh_user_id
    set_current_user_id(user_id)

    result, usage = call_llm_json("you are a test", "hi")

    assert len(captured_llm_calls) == 1
    assert captured_llm_calls[0]["vendor_label"] == "PLATFORM_DEFAULT"


def test_user_with_active_byok_uses_user_key(fresh_user_id, captured_llm_calls, client):
    """有 user_id + 激活 BYOK + 有 default config → 走用户 key + base_url + model"""
    from app.db import get_connection
    from app.services.llm_client import call_llm_json

    user_id, _ = fresh_user_id

    # 后端:purchase + activate + upsert config
    conn = get_connection()
    try:
        sub = byok_service.purchase_subscription(conn, user_id, months=1)
        byok_service.activate_code(conn, user_id, sub.code)
        byok_service.upsert_config(
            conn,
            user_id,
            BYOKConfigUpsertRequest(
                provider="deepseek",
                base_url="https://api.deepseek.com/v1",
                model_name="deepseek-chat",
                api_key="sk-USER-OWN-KEY-12345",
                is_default=True,
            ),
        )
    finally:
        conn.close()

    set_current_user_id(user_id)
    result, usage = call_llm_json("you are a test", "hi")

    assert len(captured_llm_calls) == 1
    call = captured_llm_calls[0]
    assert call["api_key"] == "sk-USER-OWN-KEY-12345"
    assert call["api_base"] == "https://api.deepseek.com/v1"
    assert call["model"] == "deepseek-chat"
    assert call["vendor_label"] == "BYOK-deepseek"


def test_explicit_user_id_param_overrides_contextvar(fresh_user_id, captured_llm_calls):
    """call_llm_json(user_id=X) 显式参数应该覆盖 ContextVar"""
    from app.db import get_connection
    from app.services.llm_client import call_llm_json

    user_id_a, _ = fresh_user_id

    # 给 user_a 配置 BYOK
    conn = get_connection()
    try:
        sub = byok_service.purchase_subscription(conn, user_id_a, months=1)
        byok_service.activate_code(conn, user_id_a, sub.code)
        byok_service.upsert_config(
            conn,
            user_id_a,
            BYOKConfigUpsertRequest(
                provider="qwen",
                base_url="https://dashscope.aliyuncs.com/v1",
                model_name="qwen-plus",
                api_key="sk-A-KEY",
                is_default=True,
            ),
        )
    finally:
        conn.close()

    # ContextVar 设 None,显式传 user_id_a → 应走 BYOK
    set_current_user_id(None)
    call_llm_json("test", "hi", user_id=user_id_a)

    assert len(captured_llm_calls) == 1
    assert captured_llm_calls[0]["api_key"] == "sk-A-KEY"
    assert captured_llm_calls[0]["vendor_label"] == "BYOK-qwen"


def test_expired_byok_falls_back_to_platform(fresh_user_id, captured_llm_calls):
    """BYOK 订阅过期 → get_active_llm_config 返 None → 走平台默认"""
    from app.db import get_connection
    from app.services.llm_client import call_llm_json

    user_id, _ = fresh_user_id

    # 人工写一个已过期的订阅 + config
    conn = get_connection()
    try:
        sub = byok_service.purchase_subscription(conn, user_id, months=1)
        byok_service.activate_code(conn, user_id, sub.code)
        # 强制过期(直接 UPDATE)
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        conn.execute(
            "UPDATE byok_subscriptions SET expires_at=? WHERE id=?",
            (past, sub.id),
        )
        conn.commit()
        byok_service.upsert_config(
            conn,
            user_id,
            BYOKConfigUpsertRequest(
                provider="deepseek",
                base_url="https://api.deepseek.com/v1",
                model_name="deepseek-chat",
                api_key="sk-WONT-USE",
                is_default=True,
            ),
        )
    finally:
        conn.close()

    set_current_user_id(user_id)
    call_llm_json("test", "hi")

    assert len(captured_llm_calls) == 1
    assert captured_llm_calls[0]["vendor_label"] == "PLATFORM_DEFAULT"


def test_contextvar_propagates_to_thread_via_copy_context(
    fresh_user_id, captured_llm_calls,
):
    """capture_current_context + ctx.run 在新 thread 里能拿到 ContextVar"""
    from app.db import get_connection
    from app.services.llm_client import call_llm_json

    user_id, _ = fresh_user_id

    conn = get_connection()
    try:
        sub = byok_service.purchase_subscription(conn, user_id, months=1)
        byok_service.activate_code(conn, user_id, sub.code)
        byok_service.upsert_config(
            conn,
            user_id,
            BYOKConfigUpsertRequest(
                provider="moonshot",
                base_url="https://api.moonshot.cn/v1",
                model_name="moonshot-v1-32k",
                api_key="sk-THREAD-KEY",
                is_default=True,
            ),
        )
    finally:
        conn.close()

    # 主 thread 设 ContextVar
    set_current_user_id(user_id)
    ctx = capture_current_context()

    # 验证主 thread 里能拿到
    assert get_current_user_id() == user_id

    # 新 thread:不用 ctx.run → 拿不到
    result_no_ctx = []

    def thread_without_ctx():
        result_no_ctx.append(get_current_user_id())

    t1 = threading.Thread(target=thread_without_ctx)
    t1.start()
    t1.join()
    assert result_no_ctx[0] is None  # ContextVar 不跨 thread

    # 新 thread:用 ctx.run → 能拿到
    result_with_ctx = []

    def thread_with_ctx():
        result_with_ctx.append(get_current_user_id())
        # 顺便测 LLM 调用确实走 BYOK
        call_llm_json("test", "hi")

    t2 = threading.Thread(target=ctx.run, args=(thread_with_ctx,))
    t2.start()
    t2.join()
    assert result_with_ctx[0] == user_id  # ContextVar 通过 ctx.run 传递

    # LLM 调用应走 BYOK
    assert len(captured_llm_calls) == 1
    assert captured_llm_calls[0]["api_key"] == "sk-THREAD-KEY"
    assert captured_llm_calls[0]["vendor_label"] == "BYOK-moonshot"


def test_byok_query_exception_falls_back_silently(monkeypatch, captured_llm_calls):
    """BYOK 查询过程抛异常 → 静默 fallback 平台默认,不阻断 LLM 调用"""
    from app.services.llm_client import call_llm_json

    # mock byok_service.get_active_llm_config 抛异常
    def boom(*args, **kwargs):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(
        "app.services.byok_service.get_active_llm_config",
        boom,
    )

    set_current_user_id("any-user-id")
    result, usage = call_llm_json("test", "hi")

    # 还能调通(走平台默认)
    assert result == {"ok": True}
    assert len(captured_llm_calls) == 1
    assert captured_llm_calls[0]["vendor_label"] == "PLATFORM_DEFAULT"
