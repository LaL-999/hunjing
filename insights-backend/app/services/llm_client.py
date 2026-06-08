"""insights-backend 独立 LLM client(2026-05-27 / INS-A5.1).

设计:
  - **不 import 主平台 backend** — 维持隔离原则
  - 默认调 DeepSeek(跟主平台 stack 一致,成本低)
  - 支持 OpenAI 兼容协议(/v1/chat/completions JSON 输出格式)
  - 提供 call_llm_json 一个入口,返 (parsed_dict, usage_dict)
  - 通过 env LLM_MOCK_MODE=1 可关掉真实 HTTP 调用(测试 / 早期没 API key 用)

为什么不复用主平台 llm_client?
  - 主平台 llm_client 在 backend/app/services/,本服务 import 它会破坏 3 层隔离的
    "代码隔离"原则
  - HTTP 调用代码 ~ 80 行,轻度重复可接受;换成更复杂的 RAG / 流式时
    再考虑抽公共包(那时已经有 monorepo 工具支持)
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Optional

import urllib.error
import urllib.request


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LlmConfig:
    api_key: str
    base_url: str          # OpenAI 兼容协议 chat completion 端点 前缀
    model: str
    timeout_s: int


def _load_config() -> LlmConfig:
    """从 env 读 LLM 配置.

    支持:
      - DEEPSEEK_API_KEY / DEEPSEEK_BASE_URL / DEEPSEEK_MODEL(默认)
      - 或通用 LLM_API_KEY / LLM_BASE_URL / LLM_MODEL(任何 OpenAI 兼容)
    """
    api_key = (
        os.getenv("INSIGHTS_LLM_API_KEY")
        or os.getenv("DEEPSEEK_API_KEY")
        or os.getenv("LLM_API_KEY")
        or ""
    )
    base_url = (
        os.getenv("INSIGHTS_LLM_BASE_URL")
        or os.getenv("DEEPSEEK_BASE_URL")
        or os.getenv("LLM_BASE_URL")
        or "https://api.deepseek.com/v1"
    )
    model = (
        os.getenv("INSIGHTS_LLM_MODEL")
        or os.getenv("DEEPSEEK_MODEL")
        or os.getenv("LLM_MODEL")
        or "deepseek-chat"
    )
    timeout_s = int(os.getenv("INSIGHTS_LLM_TIMEOUT_S", "120"))
    return LlmConfig(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        model=model,
        timeout_s=timeout_s,
    )


def _is_mock_mode() -> bool:
    """LLM_MOCK_MODE=1 时返 stub,用于测试 / 没 API key 的开发场景."""
    return os.getenv("INSIGHTS_LLM_MOCK_MODE") == "1"


# 全局 mock hook — 测试用 monkeypatch 注入 fake response
_mock_response: Optional[dict[str, Any]] = None


def set_mock_response(resp: Optional[dict[str, Any]]) -> None:
    """测试用:注入下一次 call_llm_json 的返回值."""
    global _mock_response
    _mock_response = resp


class LlmCallFailed(Exception):
    """LLM HTTP 调用失败(网络 / 鉴权 / 限流 / 5xx)."""


class LlmJsonParseFailed(Exception):
    """LLM 输出解析为 JSON 失败."""


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    *,
    max_tokens: int = 2000,
    temperature: float = 0.3,
) -> tuple[dict[str, Any], dict[str, int]]:
    """调 LLM 拿 JSON 输出.

    Args:
      system_prompt: 系统提示词(给 LLM 角色 + 输出格式约束)
      user_prompt:   用户消息内容(放数据)
      max_tokens:    输出 token 上限
      temperature:   随机性(数据分析建议 0.2-0.4)

    Returns:
      (parsed_dict, usage_dict)
      - parsed_dict: LLM 输出解析后的 dict(失败 → raise LlmJsonParseFailed)
      - usage_dict:  {"input_tokens": N, "output_tokens": N}

    Raises:
      LlmCallFailed:      HTTP / 网络 / 鉴权失败
      LlmJsonParseFailed: 输出不是合法 JSON

    Mock 模式:
      env INSIGHTS_LLM_MOCK_MODE=1 → 不发真请求,直接返 set_mock_response() 注入的值
      若没注入则返 {"_mocked": True} + 零 tokens
    """
    if _is_mock_mode():
        if _mock_response is not None:
            return (
                _mock_response,
                {"input_tokens": 100, "output_tokens": 100},
            )
        return ({"_mocked": True}, {"input_tokens": 0, "output_tokens": 0})

    config = _load_config()
    if not config.api_key:
        raise LlmCallFailed("LLM api_key 未配置(env INSIGHTS_LLM_API_KEY)")

    url = f"{config.base_url}/chat/completions"
    body = json.dumps({
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=config.timeout_s) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        body_text = ""
        try:
            body_text = e.read().decode("utf-8")[:500]
        except Exception:  # noqa: BLE001
            pass
        raise LlmCallFailed(f"LLM HTTP {e.code}: {body_text}") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise LlmCallFailed(f"LLM 网络失败: {e}") from e

    try:
        data = json.loads(raw)
        content = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
    except (json.JSONDecodeError, KeyError, IndexError) as e:
        raise LlmCallFailed(f"LLM 响应结构异常: {e}") from e

    try:
        parsed = json.loads(content)
        if not isinstance(parsed, dict):
            raise LlmJsonParseFailed("LLM 输出顶层不是 JSON object")
    except json.JSONDecodeError as e:
        raise LlmJsonParseFailed(f"LLM 输出非 JSON: {e}; raw[:200]={content[:200]}") from e

    return (
        parsed,
        {
            "input_tokens": int(usage.get("prompt_tokens", 0)),
            "output_tokens": int(usage.get("completion_tokens", 0)),
        },
    )


# 简单成本估算(DeepSeek 价格,2026-05 估算值;改 LLM 时调整)
_PRICE_INPUT_PER_M_TOKENS = 1.0    # 元 / M tokens
_PRICE_OUTPUT_PER_M_TOKENS = 2.0


def estimate_cost_yuan(input_tokens: int, output_tokens: int) -> float:
    """按 token 数估算成本(元)."""
    return round(
        (input_tokens * _PRICE_INPUT_PER_M_TOKENS
         + output_tokens * _PRICE_OUTPUT_PER_M_TOKENS) / 1_000_000,
        4,
    )


__all__ = [
    "call_llm_json",
    "estimate_cost_yuan",
    "set_mock_response",
    "LlmCallFailed",
    "LlmJsonParseFailed",
]
