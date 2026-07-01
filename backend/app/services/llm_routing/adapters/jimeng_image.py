"""图像生成 adapter — Sprint D.8 Step 2 实现,Sprint 5.4.1+ 通用化(2026-05-13),
Sprint 5.10++ 加智谱 BigModel CogView 分支(2026-05-14)。

支持 vendor(基于 api_base 域名自动探测):
  - 字节火山方舟 Doubao Seedream(`ark.cn-beijing.volces.com`)
    - 模型:`doubao-seedream-*` / `doubao-seededit-*`
    - size 范围:2K+(像素总数 ≥ 3.7M 是 Seedream 5.0 硬约束)
    - body 字段:OpenAI 标准(model/prompt/size/n)
  - SiliconFlow 开源模型(`api.siliconflow.cn`)
    - 模型:`Kwai-Kolors/Kolors` / `stabilityai/stable-diffusion-3-5-large` / `black-forest-labs/FLUX.1-schnell` / `Qwen/Qwen-Image` 等
    - size 范围:1024x1024 推荐(免费档典型)
    - body 字段:`image_size`(非 OpenAI 标准 `size`)+ `batch_size` + `num_inference_steps` + `guidance_scale`,经 extra_body 透传
  - 智谱 BigModel(`open.bigmodel.cn`)— Sprint 5.10++ 加入
    - 模型:`cogview-4-250304` / `cogview-4` / `cogview-3-plus` / `cogview-3`
    - size 范围:CogView-4 支持 7 个固定尺寸(1024x1024 / 864x1152 / 1152x864 等)
    - body 字段:OpenAI 标准(model/prompt/size),CogView 不接受 n 字段(单图)
    - 路由:智谱有 OpenAI 兼容 endpoint,**无需引入官方 zai SDK**(零新依赖)

设计权衡:
  - 不引入 vendor=fastapi config(显式 vendor 字段)— 用 api_base 域名探测,
    用户改 .env 只需一行 HUIMENG_JIMENG_API_BASE,不用同步改第 2 个字段(YAGNI)
  - 类名仍是 JimengImageAdapter(历史名,不重命名以免改 llm_routing.router 注册),
    但 vendor_label 动态显示真实 vendor
  - 智谱走 OpenAI 兼容而非 zai SDK:官方 zai SDK `client.images.generations()` 与
    OpenAI SDK `client.images.generate()` 底层都是 POST `/images/generations`,
    走 OpenAI 兼容路径可复用现有 client / retry / error handling,零新依赖

ImageResult 字段:
  - url:vendor 返回的临时图像 URL(火山方舟 ~24h 过期 / SiliconFlow URL 也可能过期 /
        智谱 ~1 天过期,生产时长期保存需自行下载落 OSS)
  - image_count:1
  - usage:不返回 token,填 0 让 pricing.lookup_price(unit_type='image') 能按张计费
"""
from __future__ import annotations

from typing import Optional

from app.config import settings
from app.services.llm_routing.protocols import (
    ImageResult,
    LlmCallFailed,
    Usage,
)


# 火山方舟 ARK Seedream 5.0:像素总数 ≥ 3.7M 硬约束
_ARK_ASPECT_TO_SIZE: dict[str, str] = {
    "1:1":  "2048x2048",   # 4,194,304 px
    "4:3":  "2304x1728",   # 3,981,312 px
    "3:4":  "1728x2304",   # 3,981,312 px
    "16:9": "2560x1440",   # 3,686,400 px(刚到下限)
    "9:16": "1440x2560",   # 3,686,400 px
}

# SiliconFlow Kolors / FLUX:1024x1024 推荐(免费档配置,2K+ 走付费档)
_SF_ASPECT_TO_SIZE: dict[str, str] = {
    "1:1":  "1024x1024",
    "4:3":  "1024x768",
    "3:4":  "768x1024",
    "16:9": "1280x720",
    "9:16": "720x1280",
}

# Sprint 5.4.1+:SiliconFlow Kolors 推荐推理参数(官方 curl 默认值)
_SF_DEFAULT_INFERENCE = {
    "batch_size": 1,
    "num_inference_steps": 20,
    "guidance_scale": 7.5,
}

# Sprint 5.10++:智谱 BigModel CogView-4 支持的固定尺寸映射
# 官方支持 7 个固定尺寸,我们的 5 个 aspect_ratio 映射到最贴近的 cogview-4 尺寸:
#   1:1   → 1024x1024(标准方图)
#   4:3   → 1152x864(横屏 4:3 精确匹配)
#   3:4   → 864x1152(竖屏 4:3 精确匹配,漫画格首选)
#   16:9  → 1440x720(横屏 2:1 接近 16:9,cogview-4 不支持精确 16:9)
#   9:16  → 720x1440(竖屏 1:2 接近 9:16)
# 备注:cogview-4 还支持 1344x768 / 768x1344,但我们的 5 个 aspect 用上面 5 个就够
_ZHIPU_ASPECT_TO_SIZE: dict[str, str] = {
    "1:1":  "1024x1024",
    "4:3":  "1152x864",
    "3:4":  "864x1152",
    "16:9": "1440x720",
    "9:16": "720x1440",
}


def _detect_vendor(base_url: Optional[str] = None) -> str:
    """从 base_url 域名探测 vendor(未传则用平台 settings.jimeng_api_base)。

    v5 item2:加 base_url 形参 —— BYOK 用户自带图像 key 时按其 base_url 探测 vendor。
    返回:"siliconflow" | "ark" | "zhipu" | "unknown"
    """
    base = (base_url if base_url is not None else (settings.jimeng_api_base or "")).lower()
    if "siliconflow.cn" in base or "siliconflow.com" in base:
        return "siliconflow"
    if "ark.cn-beijing.volces.com" in base or "volces.com" in base:
        return "ark"
    if "bigmodel.cn" in base or "open.bigmodel" in base:
        return "zhipu"
    return "unknown"


def _vendor_label(base_url: Optional[str] = None, model: Optional[str] = None) -> str:
    """动态生成 vendor_label(用于日志 / 错误信息),包含 model 名便于排错。"""
    vendor = _detect_vendor(base_url)
    model = model or settings.jimeng_model or "?"
    if vendor == "siliconflow":
        return f"SiliconFlow-{model}"
    if vendor == "ark":
        return f"Doubao-Seedream({model})"
    if vendor == "zhipu":
        return f"Zhipu-CogView({model})"
    return f"ImageGen({vendor}, {model})"


class JimengImageAdapter:
    """图像生成 adapter — 多 vendor 通用(火山方舟 / SiliconFlow)。

    类名保留历史 Jimeng(即梦)便于 llm_routing.router 注册不动;
    vendor_label 通过 property 动态生成。
    """

    @property
    def vendor_label(self) -> str:
        return _vendor_label()

    def generate(
        self,
        prompt: str,
        *,
        style: str = "default",
        ref_image_url: Optional[str] = None,
        aspect_ratio: str = "1:1",
        seed: Optional[int] = None,
        override_api_key: Optional[str] = None,
        override_base_url: Optional[str] = None,
        override_model: Optional[str] = None,
    ) -> ImageResult:
        """v5 item2:override_* 三件套 —— BYOK 用户漫创态生图走自己的图像 key。
        任一为 None 则该项回落平台 settings。三者要么齐全(BYOK),要么全 None(平台)。
        """
        from openai import OpenAI

        # 有效凭证:override 优先,否则平台 settings
        api_key = override_api_key or settings.jimeng_api_key
        base_url = override_base_url or settings.jimeng_api_base
        model = override_model or settings.jimeng_model
        label = _vendor_label(base_url, model)

        if not api_key:
            raise LlmCallFailed(
                "图像 API key 未配置 — 平台侧请在 .env 填 HUIMENG_JIMENG_API_KEY;"
                "BYOK 用户请在「自携密钥」里配置一个图像模型"
            )
        try:
            api_key.encode("ascii")
        except UnicodeEncodeError as e:
            raise LlmCallFailed(
                f"图像 API key 含非 ASCII 字符(可能是占位符):{e}"
            ) from e

        client = OpenAI(
            api_key=api_key,
            base_url=base_url,
        )

        vendor = _detect_vendor(base_url)
        # vendor → size table 映射;未知 vendor 走 ARK 表(保守兜底)
        if vendor == "siliconflow":
            size_table = _SF_ASPECT_TO_SIZE
        elif vendor == "zhipu":
            size_table = _ZHIPU_ASPECT_TO_SIZE
        else:
            size_table = _ARK_ASPECT_TO_SIZE
        size = size_table.get(aspect_ratio, size_table["1:1"])

        full_prompt = (
            f"{prompt},风格:{style}" if style and style != "default" else prompt
        )

        # SiliconFlow vs 火山方舟字段差异:
        # - 火山方舟:OpenAI 标准(model/prompt/size/n)
        # - SiliconFlow:image_size(非标准)+ batch_size + num_inference_steps + guidance_scale
        #   不传 OpenAI 标准 size(SiliconFlow 实测部分模型对 size 字段不识别 / 报错)
        # Sprint 5.x bug fix(2026-05-14):加 retry / exponential backoff,
        # 用户报"生图失败率高"根因 = vendor 429 限流 / timeout / 临时网络抖动无重试
        import time as _time
        last_err: Optional[Exception] = None
        # 3 次总尝试(第 1 次原始 + 2 次 retry);backoff 2s/5s
        retry_delays = [2.0, 5.0]
        for attempt in range(len(retry_delays) + 1):
            try:
                if vendor == "siliconflow":
                    extra_body = {
                        "image_size": size,
                        **_SF_DEFAULT_INFERENCE,
                    }
                    if seed is not None:
                        extra_body["seed"] = seed
                    resp = client.images.generate(
                        model=model,
                        prompt=full_prompt,
                        extra_body=extra_body,
                    )
                elif vendor == "zhipu":
                    # 智谱 CogView-4:OpenAI 标准 size,但**不接受 n 字段**(单图 only,
                    # 多图需多次调用);seed 字段未在公开文档列出,暂不透传
                    # quality 字段可选 "standard" / "hd",默认 standard 已够 t2i 漫画用
                    resp = client.images.generate(
                        model=model,
                        prompt=full_prompt,
                        size=size,
                    )
                else:
                    # ARK 走 OpenAI 标准(size + n)
                    resp = client.images.generate(
                        model=model,
                        prompt=full_prompt,
                        size=size,
                        n=1,
                    )
                last_err = None
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                err_name = type(e).__name__
                # 不 retry 的 fatal 错(401 / 400 / 403 等鉴权 / 参数错):立刻抛
                # retry 的 transient 错:429 RateLimit / Timeout / APIConnectionError / APIError 5xx
                is_transient = (
                    err_name in ("RateLimitError", "APITimeoutError", "APIConnectionError")
                    or "429" in str(e)
                    or "timeout" in str(e).lower()
                    or "rate limit" in str(e).lower()
                )
                if not is_transient or attempt >= len(retry_delays):
                    raise LlmCallFailed(
                        f"{label} 调用失败({err_name},"
                        f"已重试 {attempt} 次):{e}"
                    ) from e
                # transient 错,backoff 后重试
                _time.sleep(retry_delays[attempt])
        if last_err:
            raise LlmCallFailed(
                f"{label} 重试全失败({type(last_err).__name__}):{last_err}"
            ) from last_err

        if not resp.data or not resp.data[0].url:
            raise LlmCallFailed(
                f"{label} 返回为空(可能限流 / 内容审核拦截)"
            )

        image_url = resp.data[0].url
        usage: Usage = {  # type: ignore[typeddict-item]
            "input_tokens": 0,
            "output_tokens": 0,
        }
        return ImageResult(url=image_url, image_count=1, usage=usage)
