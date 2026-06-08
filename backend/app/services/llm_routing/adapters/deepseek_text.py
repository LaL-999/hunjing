"""Sprint D.8 — DeepSeek 文本 LLM adapter。

包装 `llm_client._openai_compat_call_*` impl(OpenAI 兼容端点协议),
读 settings.llm_api_key / llm_api_base / llm_model 作为凭据。

为什么不直接重新写一遍 OpenAI SDK 调用?
  → 现有 `llm_client.py` 内有 markdown fence 剥离 + JSON 修复 + 重试 + ASCII 凭据校验
    等踩坑代码,复用比重写更稳。adapter 只负责"注入 vendor 上下文"。
"""
from __future__ import annotations

from typing import Any

from app.config import settings
from app.services.llm_client import (
    _openai_compat_call_json,
    _openai_compat_call_text,
)
from app.services.llm_routing.protocols import Usage


class DeepSeekTextAdapter:
    """DeepSeek 系列(deepseek-chat / deepseek-reasoner / deepseek-v3)的 TextLlm 实现。"""

    vendor_label = "DeepSeek"

    def call_json(
        self,
        system_prompt: str,
        user_input: dict | str,
        *,
        max_tokens: int = 4000,
        temperature: float = 0.6,
        retries: int = 2,
        timeout: float = 60.0,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
    ) -> tuple[Any, Usage]:
        # 注:Usage 是 TypedDict,运行时就是 dict,返回 dict 完全兼容
        return _openai_compat_call_json(  # type: ignore[return-value]
            system_prompt,
            user_input,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            model=settings.llm_model,
            vendor_label=self.vendor_label,
            max_tokens=max_tokens,
            temperature=temperature,
            retries=retries,
            timeout=timeout,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
        )

    def call_text(
        self,
        system_prompt: str,
        user_input: dict | str,
        *,
        max_tokens: int = 8000,
        temperature: float = 0.65,
        retries: int = 1,
        frequency_penalty: float = 0.0,
        presence_penalty: float = 0.0,
    ) -> tuple[str, Usage]:
        return _openai_compat_call_text(  # type: ignore[return-value]
            system_prompt,
            user_input,
            api_key=settings.llm_api_key,
            api_base=settings.llm_api_base,
            model=settings.llm_model,
            vendor_label=self.vendor_label,
            max_tokens=max_tokens,
            temperature=temperature,
            retries=retries,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
        )
