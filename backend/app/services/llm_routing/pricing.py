"""Sprint D.8 — 多 vendor 价格表(替代 llm_client.estimate_cost_yuan 硬编码)。

设计:
  - 配置驱动,加 vendor 改这张表就够,**不动业务代码**
  - 单位:**元 / 1K token**(token 类)或 **元 / 张**(图像 / vision)
  - 老入口 `estimate_cost_yuan(input_tokens, output_tokens)` 保留向后兼容,
    内部走 `lookup_price(text_vendor, llm_model, "token", in, out)`

为什么不用 JSON 文件?
  - 价格是开发者编译时已知,放代码里方便 IDE 跳转 + git diff 审查
  - JSON 还得文件 IO + 解析失败兜底,YAGNI
"""
from __future__ import annotations

from typing import Optional


# (input_price_per_1k_yuan, output_price_per_1k_yuan)
# 单价口径:2026 年中国大陆主流 API 公开价格(用户 / 创始人核对一次即可冻结)
_TOKEN_PRICES: dict[tuple[str, str], tuple[float, float]] = {
    # === 文本 LLM(token 计费) ===
    # DeepSeek 系列(深度求索)
    ("deepseek", "deepseek-chat"):     (0.0010, 0.0020),
    ("deepseek", "deepseek-reasoner"): (0.0040, 0.0160),
    ("deepseek", "deepseek-v3"):       (0.0010, 0.0020),
    # 阿里灵积 Qwen 系列
    ("qwen", "qwen-plus"):             (0.0040, 0.0120),
    ("qwen", "qwen-turbo"):            (0.0003, 0.0006),
    ("qwen", "qwen-max"):              (0.0200, 0.0600),
    # Moonshot Kimi(暂未启用,留位)
    ("moonshot", "moonshot-v1-8k"):    (0.0120, 0.0120),
    ("moonshot", "moonshot-v1-128k"):  (0.0600, 0.0600),
    # 离线横评用(不进生产) — gpt-4o-mini
    ("openai", "gpt-4o-mini"):         (0.0011, 0.0043),

    # === 多模态视觉(token 计费;输入含图像 token) ===
    ("qwen_vl", "qwen-vl-max"):         (0.0200, 0.0200),
    ("qwen_vl", "qwen-vl-max-latest"):  (0.0200, 0.0200),   # Sprint D.8 Step 3 实测填
    ("qwen_vl", "qwen-vl-plus"):        (0.0080, 0.0080),
    ("qwen_vl", "qwen-vl-plus-latest"): (0.0080, 0.0080),
    ("qwen_vl", "qwen3-vl-plus"):       (0.0080, 0.0080),
    ("qwen_vl", "qwen3-vl-max"):        (0.0200, 0.0200),
    ("glm_4v", "glm-4v"):               (0.0500, 0.0500),
}


# 元 / 张(图像生成不计 token)
# 注意:service 层 `consume_credits` 当前硬编码 vendor="jimeng",所以本表所有图像生成
# vendor key 都用 "jimeng"(即便实际 vendor 是 SiliconFlow / 智谱);仅 model 字段区分。
# 若后续打散 vendor 字段(comic_service / refine_service 全栈改),本表 key 同步重命名。
_IMAGE_PRICES: dict[tuple[str, str], float] = {
    # 字节火山方舟 Doubao Seedream 系列(2026 实测官方价目)
    # ⚠️ 3.0-t2i-250415 已 deprecated(账号列表残留可见,实际调用 404)
    ("jimeng", "doubao-seedream-3-0-t2i-250415"): 0.20,
    # 4.0(2025-08)/ 4.5(2025-11)单价待官方账单校准,暂按合理推测
    ("jimeng", "doubao-seedream-4-0-250828"):     0.20,
    ("jimeng", "doubao-seedream-4-5-251128"):     0.25,
    # 5.0(2026-01)2K 画质,偏写实
    ("jimeng", "doubao-seedream-5-0-260128"):     0.30,
    # SiliconFlow 开源模型(Sprint 5.4.1+ 通用化加入)
    # ⚠️ 公开标价仅作占位,免费试用额度内单价为 0,付费档实际计费请以 SiliconFlow 控制台为准
    ("jimeng", "Qwen/Qwen-Image"):                0.07,
    ("jimeng", "Kwai-Kolors/Kolors"):             0.00,   # 免费档
    ("jimeng", "stabilityai/stable-diffusion-3-5-large-turbo"): 0.00,
    ("jimeng", "black-forest-labs/FLUX.1-schnell"): 0.00,
    # 智谱 BigModel CogView 系列(Sprint 5.10++ 加入,2026-05-14)
    # CogView-4 公开标价 ¥0.5 / 张(1024x1024 std quality;hd quality 翻倍)
    # CogView-3-Plus 公开标价 ¥0.06 / 张(便宜 8 倍,但画质略弱;可作 fallback)
    ("jimeng", "cogview-4-250304"):    0.50,
    ("jimeng", "cogview-4"):           0.50,
    ("jimeng", "cogview-3-plus"):      0.06,
    ("jimeng", "cogview-3"):           0.06,
    # 阿里通义万相(备选,D.9 部署前可评估)
    ("wanx", "wanx-v1"):               0.16,
    ("wanx", "wanx-v2"):               0.24,
    # 快手可灵(备选)
    ("kolors", "kolors-v1"):           0.10,
}


def lookup_price(
    vendor: str,
    model: str,
    unit_type: str,
    input_count: int,
    output_count: int,
    image_count: Optional[int] = None,
) -> float:
    """统一价格查询入口 — 业务代码只调这个。

    Args:
        vendor: deepseek / qwen / jimeng / qwen_vl / ...
        model: vendor 内的具体型号
        unit_type: "token"(文本 / 多模态)/ "image"(图像生成)
        input_count: token 数(token 单位时);image 单位时填 0
        output_count: token 数(token 单位时);image 单位时填 0
        image_count: 生成图像数(image 单位时必填)

    Returns:
        总成本(人民币元,保留 4 位小数由调用方决定)

    Behavior:
        - 未知 vendor/model → 返回 0.0(不阻断业务,但日志层应该 warn)
        - token 单位时 image_count 被忽略
        - image 单位时 input/output_count 被忽略
    """
    key = (vendor, model)
    if unit_type == "token":
        if key not in _TOKEN_PRICES:
            return 0.0
        in_p, out_p = _TOKEN_PRICES[key]
        return (input_count * in_p + output_count * out_p) / 1000
    elif unit_type == "image":
        if key not in _IMAGE_PRICES:
            return 0.0
        return (image_count or 0) * _IMAGE_PRICES[key]
    else:
        return 0.0
