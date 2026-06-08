"""剧创态 LLM 客户端 — 阶段 8 P0(2026-06-08)改为父平台 LLM client 代理。

历史(2026-06-05 ~ 06):
  比赛仓库直接实例化 openai.OpenAI(api_key=settings.deepseek_api_key, ...),
  阶段 3 迁徙时连这套独立 client 一起搬进来了。

问题(2026-06-08 P0 审计发现):
  父平台 6/5 上线 BYOK + LLM routing:用户可以配自己的 API key(deepseek /
  openai / anthropic / qwen),所有父平台 LLM 调用走
  `app.services.llm_client.call_llm_json/text` → 自动用用户的 key。
  但剧创态用本文件的独立 client → **完全绕过 BYOK**:
    - 用户 BYOK 配置在剧创态不生效
    - 多 user 共用 .env 的 server-side key
    - 烧平台 token,商业模式漏

修复:
  本文件的 `call_chat` / `call_json` 保留原签名(8 个 agent 无需改动),
  内部代理给父平台 `call_llm_text` / `call_llm_json` → 自动:
    1. 从 ContextVar 拿 current_user_id(get_current_user 已 set)
    2. 查 byok_configs 表 → 有则用用户的 vendor / key / base_url / model
    3. 无则走父平台 llm_routing 默认 vendor
    4. 异常类型保留为 LlmCallFailed / LlmJsonParseFailed(契约一致)

兼容性:
  - 测试 monkeypatch `app.screenplay.services.llm_client.call_json` 仍生效
    (mocks_screenplay_llm fixture + 比赛迁入测试均 OK)
  - 8 个 agent 的 import `from app.screenplay.services.llm_client import call_json`
    一字不改,自动享受 BYOK
"""
from __future__ import annotations

import json
import logging
from typing import Any

# 直接复用父平台的异常类,保证 catch 兼容性
from app.services.llm_client import (
    LlmCallFailed,
    LlmJsonParseFailed,
    call_llm_json as _platform_call_json,
    call_llm_text as _platform_call_text,
)
from app.screenplay.config import settings

logger = logging.getLogger(__name__)


# 重新 export 让历史 `from app.screenplay.services.llm_client import LlmCallFailed` 不破
__all__ = ["LlmCallFailed", "LlmJsonParseFailed", "call_chat", "call_json"]


# ============================================================
# 纯文本调用 — 代理给父平台
# ============================================================

def call_chat(
    system_prompt: str,
    user_input: str | dict,
    *,
    max_tokens: int = 2000,
    temperature: float | None = None,
    retries: int | None = None,
) -> tuple[str, dict]:
    """调用 LLM,返回 (text, usage_dict)。

    保留原签名,内部代理给父平台 call_llm_text(自动 BYOK 路由)。

    Args:
        user_input: 字符串直接传,dict 自动 json.dumps
        usage_dict: {input_tokens, output_tokens}

    Raises:
        LlmCallFailed: 网络 / 限流 / 凭据错误
    """
    if temperature is None:
        temperature = settings.llm_default_temperature
    if retries is None:
        retries = settings.llm_max_retries

    if isinstance(user_input, dict):
        user_input = json.dumps(user_input, ensure_ascii=False)

    # 父平台 call_llm_text 签名:
    #   (system_prompt, user_input, *, max_tokens, temperature, retries, timeout, ...)
    #   → (text, usage_dict)
    # user_id 不传 → 父平台从 ContextVar 读(get_current_user Depends 已设置)
    return _platform_call_text(
        system_prompt,
        user_input,
        max_tokens=max_tokens,
        temperature=temperature,
        retries=retries,
    )


# ============================================================
# JSON 调用 — 代理给父平台
# ============================================================

def call_json(
    system_prompt: str,
    user_input: str | dict,
    *,
    max_tokens: int = 4000,
    temperature: float = 0.3,    # JSON 场景默认低温(剧创态 8 个 agent 共用此默认)
    retries: int | None = None,
) -> tuple[Any, dict]:
    """调用 LLM 期望 JSON 输出,自动解析,返回 (parsed_json, usage_dict)。

    保留原签名,内部代理给父平台 call_llm_json(自动 BYOK + 重试 + fence 剥离)。

    Raises:
        LlmJsonParseFailed: 重试后仍解析失败
        LlmCallFailed: 网络层失败
    """
    if retries is None:
        retries = settings.llm_max_retries

    # 父平台 call_llm_json 已经处理 fence 剥离 / repair / 重试
    return _platform_call_json(
        system_prompt,
        user_input,
        max_tokens=max_tokens,
        temperature=temperature,
        retries=retries,
    )
