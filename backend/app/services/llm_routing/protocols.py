"""Sprint D.8 — 多 vendor LLM / 图像 / 多模态 路由层 Protocol 定义。

详见 docs/ADR_D.8_国产API路由层.md。

**设计原则:**
- 用 `typing.Protocol`(结构化类型)而不是 ABC — adapter 不需要显式继承
- `Usage` 用 `TypedDict` — service 层老代码 `usage["input_tokens"]` 仍能直接用
- 图像 / 多模态结果用 `dataclass(frozen=True)`,不允许悄悄 mutate
- 异常类与 `llm_client.LlmCallFailed` 同根,service 层 except 不需变

**为什么不 import LlmCallFailed 而是这里重定义?**
反向 — 我们要把 llm_client 的异常类**搬到这里**作为路由层底层异常,llm_client 改 reexport。
但 Sprint D.8 step 1 为了最小改动,**保留 llm_client.LlmCallFailed 作为权威定义**,
本文件的 LlmCallFailed = llm_client.LlmCallFailed(直接 import)。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Protocol, TypedDict, runtime_checkable

# 异常 — 沿用 llm_client 的现有定义,避免触发 service 层 except 全改
from app.services.llm_client import LlmCallFailed, LlmJsonParseFailed  # noqa: F401


class Usage(TypedDict):
    """LLM 调用的 token 用量。

    保持与 llm_client.call_llm_* 返回的 dict 结构完全一致 —
    service 层 `usage["input_tokens"]` 写法无需修改。
    """
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class ImageResult:
    """图像生成的产物。

    国产 API 大多返回 URL(临时 OSS / CDN),不返回 base64;
    image_count 区分 1-call N-image 的 vendor(即梦支持 1 次出 4 张)。
    usage 字段在不返回 token 的 vendor 上填 {"input_tokens": 0, "output_tokens": 0}。
    """
    url: str
    image_count: int
    usage: Usage


@runtime_checkable
class TextLlm(Protocol):
    """文本 LLM adapter 接口 — 与现有 `call_llm_json` / `call_llm_text` 1:1 对应。

    frequency_penalty / presence_penalty(提案 B,2026-05-24):
      OpenAI 兼容协议的标准参数;默认 0.0 不开启,长文本场景显式传 0.3~0.5
      治"n-gram 原文复读"。非 OpenAI 兼容的 adapter(若未来加 Anthropic/Gemini)
      可忽略此参数。
    """

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
    ) -> tuple[Any, Usage]: ...

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
    ) -> tuple[str, Usage]: ...


@runtime_checkable
class ImageGen(Protocol):
    """图像生成 adapter 接口 — 漫画态分镜生成 + 局部重绘用。"""

    def generate(
        self,
        prompt: str,
        *,
        style: str = "default",
        ref_image_url: Optional[str] = None,
        aspect_ratio: str = "1:1",
        seed: Optional[int] = None,
    ) -> ImageResult:
        """生成图像。

        Args:
            prompt: 中文 prompt;adapter 内部按 vendor 习惯做必要的转译
            style: 风格 tag(古风工笔 / 国漫 / 写实 / ...);adapter 映射到 vendor 内置风格
            ref_image_url: 参考图(角色一致性,IP-Adapter 类),None 时纯文生图
            aspect_ratio: "1:1" / "4:3" / "3:4" / "16:9" / "9:16"
            seed: 随机种子(测试可复现用);None 时 vendor 自选
        """
        ...


@runtime_checkable
class VisionLlm(Protocol):
    """多模态视觉理解 adapter 接口 — 漫画态质检 agent + 角色一致性核查用。"""

    def describe(
        self,
        image_url: str,
        question: str,
        *,
        max_tokens: int = 1000,
        timeout: float = 30.0,
    ) -> tuple[str, Usage]:
        """看图回答问题。

        Args:
            image_url: 图像 URL(可以是 OSS / CDN / 公开链接)
            question: 中文问题;vendor 答中文
        """
        ...
