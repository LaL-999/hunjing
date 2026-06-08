"""Sprint D.8 — 多 vendor LLM / 图像 / 多模态 路由层。

详见 docs/ADR_D.8_国产API路由层.md。

调用方推荐入口:
  - 文本(向后兼容,业务代码不需要改)
      from app.services.llm_client import call_llm_json, call_llm_text
      → 内部委托给 router.get_text_llm()

  - 图像(D.9 漫画态新增)
      from app.services.llm_routing import get_image_gen
      result = get_image_gen().generate("...", aspect_ratio="3:4")

  - 多模态(D.9 漫画态质检新增)
      from app.services.llm_routing import get_vision_llm
      answer, usage = get_vision_llm().describe(image_url, "图中有几个人?")
"""
from app.services.llm_routing.protocols import (
    ImageGen,
    ImageResult,
    LlmCallFailed,
    LlmJsonParseFailed,
    TextLlm,
    Usage,
    VisionLlm,
)
from app.services.llm_routing.router import (
    get_image_gen,
    get_text_llm,
    get_vision_llm,
    reset_for_testing,
)

__all__ = [
    # Protocols + DTO
    "TextLlm",
    "ImageGen",
    "VisionLlm",
    "Usage",
    "ImageResult",
    # 异常
    "LlmCallFailed",
    "LlmJsonParseFailed",
    # 工厂
    "get_text_llm",
    "get_image_gen",
    "get_vision_llm",
    "reset_for_testing",
]
