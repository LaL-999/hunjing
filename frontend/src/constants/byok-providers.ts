/**
 * BYOK 自携密钥 — 预设 provider 清单
 *
 * 用户只需填 api_key,平台代填 base_url + 默认 model_name。
 * 用户也可选 "custom" 完全自定义。
 *
 * 协议统一:OpenAI compatible /chat/completions(中国主流模型都支持)
 */

export interface BYOKProviderPreset {
  /** 后端 provider 标识 — 与 byok_configs.provider 字段对应(全局唯一,不跨模态重名) */
  id:
    | "deepseek" | "qwen" | "zhipu" | "doubao" | "moonshot" | "custom"
    | "siliconflow" | "seedream" | "cogview" | "custom_image";
  /** 模态:text 文本 LLM(默认)/ image 图像生成模型(漫创态用) */
  modality?: "text" | "image";
  /** 展示名 */
  label: string;
  /** 厂商简介 */
  description: string;
  /** OpenAI compatible 基础 URL(custom 时由用户填) */
  base_url: string;
  /** 默认模型(用户可改) */
  default_model: string;
  /** 可选模型列表(下拉提示,custom 不限) */
  model_suggestions: string[];
  /** 申请 key 的官方地址 */
  apply_url: string;
}

export const BYOK_PROVIDERS: BYOKProviderPreset[] = [
  {
    id: "deepseek",
    label: "DeepSeek",
    description: "深度求索 — 浑晶后端默认在用的模型,性价比第一",
    base_url: "https://api.deepseek.com/v1",
    default_model: "deepseek-chat",
    model_suggestions: ["deepseek-chat", "deepseek-reasoner"],
    apply_url: "https://platform.deepseek.com/api_keys",
  },
  {
    id: "qwen",
    label: "通义千问(Qwen)",
    description: "阿里云百炼 — 千问全系列,长上下文友好",
    base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1",
    default_model: "qwen-plus",
    model_suggestions: [
      "qwen-plus",
      "qwen-max",
      "qwen-turbo",
      "qwen-long",
      "qwen2.5-72b-instruct",
    ],
    apply_url: "https://bailian.console.aliyun.com/?apiKey=1",
  },
  {
    id: "zhipu",
    label: "智谱 GLM",
    description: "智谱 AI — GLM-4 系列,中文文学叙事强",
    base_url: "https://open.bigmodel.cn/api/paas/v4",
    default_model: "glm-4-plus",
    model_suggestions: ["glm-4-plus", "glm-4-air", "glm-4-flash", "glm-4-long"],
    apply_url: "https://bigmodel.cn/usercenter/proj-mgmt/apikeys",
  },
  {
    id: "doubao",
    label: "豆包(Doubao)",
    description: "字节火山引擎 — 豆包系列,响应快",
    base_url: "https://ark.cn-beijing.volces.com/api/v3",
    default_model: "doubao-pro-32k",
    model_suggestions: [
      "doubao-pro-32k",
      "doubao-pro-128k",
      "doubao-lite-32k",
      "doubao-1.5-pro-32k",
    ],
    apply_url: "https://www.volcengine.com/docs/82379/1099475",
  },
  {
    id: "moonshot",
    label: "Kimi(Moonshot)",
    description: "月之暗面 — 长上下文之王(128k / 200k 起)",
    base_url: "https://api.moonshot.cn/v1",
    default_model: "moonshot-v1-32k",
    model_suggestions: [
      "moonshot-v1-8k",
      "moonshot-v1-32k",
      "moonshot-v1-128k",
      "moonshot-v1-auto",
    ],
    apply_url: "https://platform.moonshot.cn/console/api-keys",
  },
  {
    id: "custom",
    label: "自定义(任意 OpenAI 兼容接口)",
    description: "完全自由 — 自填 base_url + 模型名(满足 OpenAI compatible 协议即可)",
    base_url: "",
    default_model: "",
    model_suggestions: [],
    apply_url: "",
  },

  // ===== 图像模型(v5 item2:漫创态生图,走你自己的图像 key,不占平台额度)=====
  {
    id: "siliconflow",
    modality: "image",
    label: "硅基流动(SiliconFlow)",
    description: "开源生图最省 — Kolors / FLUX / Qwen-Image,新用户有免费额度",
    base_url: "https://api.siliconflow.cn/v1",
    default_model: "Kwai-Kolors/Kolors",
    model_suggestions: [
      "Kwai-Kolors/Kolors",
      "black-forest-labs/FLUX.1-schnell",
      "Qwen/Qwen-Image",
      "stabilityai/stable-diffusion-3-5-large",
    ],
    apply_url: "https://cloud.siliconflow.cn/account/ak",
  },
  {
    id: "seedream",
    modality: "image",
    label: "火山 Seedream(豆包生图)",
    description: "字节火山方舟 — Doubao Seedream,画质高、人物一致性好",
    base_url: "https://ark.cn-beijing.volces.com/api/v3",
    default_model: "doubao-seedream-3-0-t2i-250415",
    model_suggestions: [
      "doubao-seedream-3-0-t2i-250415",
      "doubao-seedream-4-0-250828",
    ],
    apply_url: "https://www.volcengine.com/docs/82379/1541523",
  },
  {
    id: "cogview",
    modality: "image",
    label: "智谱 CogView",
    description: "智谱 AI — CogView-4,中文语义理解到位,固定尺寸出图",
    base_url: "https://open.bigmodel.cn/api/paas/v4",
    default_model: "cogview-4",
    model_suggestions: ["cogview-4", "cogview-4-250304", "cogview-3-plus"],
    apply_url: "https://bigmodel.cn/usercenter/proj-mgmt/apikeys",
  },
  {
    id: "custom_image",
    modality: "image",
    label: "自定义图像模型(OpenAI 兼容 /images)",
    description: "自填 base_url + 模型名,走 OpenAI 兼容 /images/generations 协议",
    base_url: "",
    default_model: "",
    model_suggestions: [],
    apply_url: "",
  },
];

/** 通过 id 查 preset */
export function findProviderPreset(id: string): BYOKProviderPreset | undefined {
  return BYOK_PROVIDERS.find((p) => p.id === id);
}

/** 按模态过滤预设(text 默认 —— 无 modality 字段视为 text) */
export function providersByModality(
  modality: "text" | "image",
): BYOKProviderPreset[] {
  return BYOK_PROVIDERS.filter((p) => (p.modality ?? "text") === modality);
}

/** 该 provider 是否图像模态 */
export function isImageProvider(id: string): boolean {
  return findProviderPreset(id)?.modality === "image";
}
