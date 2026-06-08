"""Sprint D.8 Step 2 — 阿里灵积(DashScope)Qwen-VL 多模态视觉 adapter。

接入路径:
  - DashScope **OpenAI 兼容端点**(`https://dashscope.aliyuncs.com/compatible-mode/v1`)
  - 用 OpenAI SDK chat.completions API,messages 中 image_url type 传图
  - 凭据:settings.qwen_api_key(单一 sk-xxx 形式)
  - 模型:settings.qwen_vl_model(用户控制台核对精确 ID,如 qwen-vl-max-latest)

为什么走 OpenAI 兼容协议而不是 dashscope 原生 SDK?
  - 与 DeepSeekTextAdapter 架构对称,代码风格一致
  - 不引入新依赖(项目已用 openai SDK)
  - DashScope 兼容协议覆盖 chat / vision / embedding 全功能

文档:
  - https://help.aliyun.com/zh/dashscope/developer-reference/compatibility-of-openai-with-dashscope
  - https://help.aliyun.com/zh/dashscope/developer-reference/qwen-vl-api
"""
from __future__ import annotations

from app.config import settings
from app.services.llm_routing.protocols import LlmCallFailed, Usage


# DashScope OpenAI 兼容端点(2026 稳定 url;新区域出现时改 .env 即可,不动代码)
_DASHSCOPE_OPENAI_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


class QwenVlVisionAdapter:
    """阿里 Qwen-VL 多模态视觉 — Sprint D.8 Step 2 实现。"""

    vendor_label = "Qwen-VL"

    def describe(
        self,
        image_url: str,
        question: str,
        *,
        max_tokens: int = 1000,
        timeout: float = 30.0,
    ) -> tuple[str, Usage]:
        from openai import OpenAI

        if not settings.qwen_api_key:
            raise LlmCallFailed(
                "HUIMENG_QWEN_API_KEY 未配置 — 请去 https://dashscope.console.aliyun.com/apiKey "
                "生成 sk-xxx key 填入项目根 .env"
            )
        # 防中文占位符踩坑(沿用 llm_client 的同款防御)
        try:
            settings.qwen_api_key.encode("ascii")
        except UnicodeEncodeError as e:
            raise LlmCallFailed(
                f"HUIMENG_QWEN_API_KEY 含非 ASCII 字符(可能是占位符):{e}"
            ) from e

        client = OpenAI(
            api_key=settings.qwen_api_key,
            base_url=_DASHSCOPE_OPENAI_BASE,
            timeout=timeout,
        )

        # 多模态 message 结构:user content 是 list,含 image_url type + text type
        # DashScope 与 OpenAI 兼容,image_url.url 接受 https / data URL
        try:
            resp = client.chat.completions.create(
                model=settings.qwen_vl_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": image_url}},
                            {"type": "text", "text": question},
                        ],
                    },
                ],
                max_tokens=max_tokens,
            )
        except Exception as e:
            raise LlmCallFailed(
                f"{self.vendor_label} 调用失败({type(e).__name__}):{e}"
            ) from e

        answer = resp.choices[0].message.content or ""
        usage: Usage = {  # type: ignore[typeddict-item]
            "input_tokens": resp.usage.prompt_tokens if resp.usage else 0,
            "output_tokens": resp.usage.completion_tokens if resp.usage else 0,
        }
        return answer, usage
