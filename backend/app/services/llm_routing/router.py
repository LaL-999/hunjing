"""Sprint D.8 — vendor adapter 工厂 + 模块级缓存。

为什么用模块级单例缓存?
  - adapter 是无状态的(只读 settings),实例化轻;不缓存也不慢
  - 但每次调用都 new adapter 会让测试 mock 烦人(每次 fresh 实例)
  - 缓存后可以 monkeypatch `_text_llm_cache` 单实例做行为注入

为什么 lazy import adapter?
  - 路由层 + adapter 之间避免 import cycle(adapter import settings,
    settings 不 import adapter,顺序很重要)
  - 启动时 jimeng / qwen_vl 模块可能因缺 SDK 失败,lazy import 让
    "用不到的 vendor 不影响启动"
"""
from __future__ import annotations

from typing import Optional

from app.config import settings
from app.services.llm_routing.protocols import (
    ImageGen,
    TextLlm,
    VisionLlm,
)


# 模块级缓存:首次实例化后保留;reset_for_testing() 可清
_text_llm_cache: Optional[TextLlm] = None
_image_gen_cache: Optional[ImageGen] = None
_vision_llm_cache: Optional[VisionLlm] = None


def get_text_llm() -> TextLlm:
    """按 settings.text_vendor 实例化文本 LLM adapter。

    默认 'deepseek',与 Sprint D.8 之前的硬编码行为一致。
    """
    global _text_llm_cache
    if _text_llm_cache is not None:
        return _text_llm_cache

    vendor = settings.text_vendor.lower()
    if vendor == "deepseek":
        from app.services.llm_routing.adapters.deepseek_text import (
            DeepSeekTextAdapter,
        )
        _text_llm_cache = DeepSeekTextAdapter()
    else:
        # Sprint D.8 step 1 只接 deepseek;其它 vendor 留给 step 2 或 D.8 之后
        raise NotImplementedError(
            f"text_vendor={vendor!r} 尚未实现 — "
            f"Sprint D.8 step 1 仅接 deepseek;在 .env 把 HUIMENG_TEXT_VENDOR 设为 'deepseek'"
        )
    return _text_llm_cache


def get_image_gen() -> ImageGen:
    """按 settings.image_vendor 实例化图像生成 adapter。

    Sprint D.8 完工(2026-05-12):JimengImageAdapter 已真实接通火山方舟
    OpenAI 兼容端点 + Doubao Seedream 3.0;横评 5 用例全过(¥1.6 实测)。
    """
    global _image_gen_cache
    if _image_gen_cache is not None:
        return _image_gen_cache

    vendor = settings.image_vendor.lower()
    if vendor == "jimeng":
        from app.services.llm_routing.adapters.jimeng_image import (
            JimengImageAdapter,
        )
        _image_gen_cache = JimengImageAdapter()
    else:
        raise NotImplementedError(
            f"image_vendor={vendor!r} 尚未实现 — "
            f"Sprint D.8 仅接 jimeng;在 .env 把 HUIMENG_IMAGE_VENDOR 设为 'jimeng'"
        )
    return _image_gen_cache


def get_vision_llm() -> VisionLlm:
    """按 settings.vision_vendor 实例化多模态视觉 adapter。

    Sprint D.8 完工(2026-05-12):QwenVlVisionAdapter 已真实接通阿里灵积
    DashScope OpenAI 兼容端点 + Qwen-VL Max;横评 3 题全命中(¥0.01 实测)。
    """
    global _vision_llm_cache
    if _vision_llm_cache is not None:
        return _vision_llm_cache

    vendor = settings.vision_vendor.lower()
    if vendor == "qwen_vl":
        from app.services.llm_routing.adapters.qwen_vl_vision import (
            QwenVlVisionAdapter,
        )
        _vision_llm_cache = QwenVlVisionAdapter()
    else:
        raise NotImplementedError(
            f"vision_vendor={vendor!r} 尚未实现 — "
            f"Sprint D.8 仅接 qwen_vl;在 .env 把 HUIMENG_VISION_VENDOR 设为 'qwen_vl'"
        )
    return _vision_llm_cache


def reset_for_testing() -> None:
    """清空模块级缓存 — 仅测试用。

    typical use:
        @pytest.fixture(autouse=True)
        def _reset_llm_routing():
            yield
            from app.services.llm_routing.router import reset_for_testing
            reset_for_testing()
    """
    global _text_llm_cache, _image_gen_cache, _vision_llm_cache
    _text_llm_cache = None
    _image_gen_cache = None
    _vision_llm_cache = None
