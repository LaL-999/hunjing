# ADR D.8 — 国产 LLM / 绘图 / 多模态 API 路由层

**状态:已批准(2026-05-12 用户 sign-off)**
**日期:2026-05-12**
**作者:Claude(代笔)+ 创始人(决策)**
**关联**:`docs/ADR_漫画创作态架构.md`(D.9 漫画态依赖此 ADR 落地的路由层)

---

## 1. 背景

`backend/app/services/llm_client.py` 当前单 vendor 硬编码:
- OpenAI SDK 走 `OPENAI_API_BASE`(指向 DeepSeek 兼容端点)
- 单 `LLM_API_KEY` / `LLM_MODEL` 环境变量
- `estimate_cost_yuan` pricing 表硬编码 4 个 model
- 只有 `call_llm_json` / `call_llm_text` 两个文本入口 —— **无图像生成 / 视觉理解**

漫画态(D.9)6 agent 流水线需要的能力中有 **2 类完全没接**:绘图(分镜生成 + 局部重绘)、多模态视觉(质检 agent 看图判断角色一致性 + 合规扫描)。直接在 D.9 内边写边接 vendor 会让本就 11-12 sprint 的工作量再翻 50%,且 service 层会耦合 vendor 细节(每次 vendor 替换要改业务代码)。

**D.8 是漫画态的工程前置**,在 D.9 启动前把 vendor 抽象层落地。

## 2. 范围

**做:**
- `backend/app/services/llm_routing/` 路由层(Protocol + Adapter + 工厂)
- 文本 vendor 包装现有 DeepSeek 代码为 Adapter
- 绘图 / 多模态 vendor 接通 hello-world(只 1 家,不做横评全套)
- 跑 5 + 3 用例横评 → `docs/D.8_vendor_evaluation.md`
- 计费层扩展支持 vendor / unit_type 维度
- `pricing.py` 配置化(取代硬编码价格表)

**不做:**
- 漫画态实际功能(D.9 的事)
- 失败 fallback 自动切 vendor(YAGNI,等用户实际抱怨可用性再加)
- 多 vendor 同类备份(每类 1 家够 PoC,扩展边际成本低)
- 海外 vendor 集成(数据出境红线;海外仅作离线横评,不进生产)

## 3. 决策

### 3.1 Vendor 选型

| 能力 | 主路由 | 公司 | 接入方式 | 选定理由 |
|---|---|---|---|---|
| 文本 | DeepSeek V3 / V3.2 / R1 | 深度求索 | 现有(OpenAI 兼容端点) | 已锁,与浑晶整体 LLM 策略一致 |
| 绘图 | **即梦(Jimeng)** | 字节跳动 | 火山引擎 visual_cn API | 用户拍板;字节绘图模型在国漫 / 商业插画风格上突出 |
| 多模态视觉 | **Qwen-VL Max** | 阿里灵积 | 灵积 DashScope SDK | 评测靠前 + 与未来若启用 Qwen-Max 文本备份可共用 API key |

**不选其他候选的原因:**
- 通义万相(Wanx):图像质量不如即梦的国漫风
- 可灵(Kuai shou)/ 文心一格(Baidu):API 文档与限流不够透明
- GLM-4V / 文心 VL:可作 Qwen-VL 备选(D.9 部署前再评估是否加 fallback)

### 3.2 路由层架构 — Adapter Pattern + Protocol

```
backend/app/services/llm_routing/
├── __init__.py              # 公开 get_text_llm / get_image_gen / get_vision_llm
├── protocols.py             # TextLlm / ImageGen / VisionLlm Protocol + Usage / ImageResult
├── router.py                # 工厂函数 — 读 settings.text_vendor 等,实例化 adapter
├── pricing.py               # vendor.model → unit_type → price 表(取代硬编码)
└── adapters/
    ├── __init__.py
    ├── deepseek_text.py     # DeepSeekTextAdapter(包装现有 llm_client 实现)
    ├── jimeng_image.py      # JimengImageAdapter(火山引擎 visual_cn API)
    └── qwen_vl_vision.py    # QwenVlVisionAdapter(阿里灵积 DashScope)
```

**调用方向后兼容铁律**:`llm_client.py` 的 `call_llm_json` / `call_llm_text` 函数签名**保持不变**,内部改为委托给 router。所有 service 层(`refine_service`、`simulation_service`、`canonical_guardian_service` 等)的调用代码**0 改动**。

### 3.3 环境变量

```ini
# === 老变量保留(向后兼容) ===
OPENAI_API_KEY=xxx
OPENAI_API_BASE=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# === D.8 新增:vendor 选择 ===
HUIMENG_TEXT_VENDOR=deepseek          # deepseek(默认)/ qwen / moonshot
HUIMENG_IMAGE_VENDOR=jimeng           # jimeng(默认)/ wanx / kolors
HUIMENG_VISION_VENDOR=qwen_vl         # qwen_vl(默认)/ glm_4v

# === D.8 新增:各 vendor key(只填要用的) ===
HUIMENG_QWEN_API_KEY=xxx              # 阿里灵积统一 key — Qwen-VL Max 用
HUIMENG_JIMENG_AK=xxx                 # 字节火山引擎 AccessKey ID
HUIMENG_JIMENG_SK=xxx                 # 字节火山引擎 AccessKey Secret
```

实际只需 **2 个新 key 组**:阿里灵积单 key、字节火山引擎 AK/SK 对(DeepSeek 复用老 `OPENAI_API_KEY`)。

### 3.4 计费扩展

新 migration `023_usage_vendor.sql`:

```sql
ALTER TABLE usage_records ADD COLUMN vendor TEXT NOT NULL DEFAULT 'deepseek';
ALTER TABLE usage_records ADD COLUMN unit_type TEXT NOT NULL DEFAULT 'token';
ALTER TABLE usage_records ADD COLUMN output_count INTEGER;   -- 图像张数 / vision 推理张数
```

- 老记录全部默认 `vendor='deepseek' / unit_type='token' / output_count=NULL`
- `estimate_cost_yuan` 改成 `lookup_price(vendor, model, unit_type, input, output, output_count)`
- 价格表挪到 `llm_routing/pricing.py`(配置化,无需改代码加 vendor)

### 3.5 失败 fallback

**YAGNI,不做。** 主路由失败时直接抛 `LlmCallFailed`,前端 toast 显示"AI 服务暂时不可用,请稍后再试"。

理由:
- fallback 让测试矩阵 ×N(单元 + 集成 + e2e 都要测主用 / 备用 / 切换路径)
- 不同 vendor 输出风格不一致(漫画绘图换 vendor → 画风跳变,用户体验更差)
- 出问题时让人介入更稳

D.9 部署前如果实测可用性确实低,再补 fallback,届时已经有真实数据指导设计。

### 3.6 横评方法

输出 `docs/D.8_vendor_evaluation.md`,5 + 3 用例:

**绘图(即梦)** — 5 用例:
1. 红楼梦 "林黛玉,17 岁,弱柳扶风,葬花" — 古风工笔
2. 现代都市 "穿西装的男人在咖啡馆,黄昏窗光" — 测现代风
3. 同一角色不同表情(笑 / 怒 / 哭)— 测角色一致性(无 IP-Adapter 看是否真无解)
4. 双人对话(角色 A + 角色 B 在花园)— 测多角色构图
5. 镜头语言对比(特写 vs 远景同 prompt)

**视觉(Qwen-VL)** — 3 题:
1. "图中有几个人?各自衣着颜色?" — 测基础识别
2. "图中主角的情绪是?" — 测高阶语义
3. "图中是否包含血腥 / 政治敏感内容?" — 测合规扫描(漫画质检 agent 核心)

每项:prompt + API 响应原样截屏 + 主观评分(1-5)+ 单次成本

## 4. 实施步骤

| 阶段 | 工作内容 | 工作量 | 依赖 |
|---|---|---|---|
| Step 1 | Protocols + DeepSeek adapter + router + config + migration 023 + pricing.py + 老 `llm_client` 委托改造 | 0.5 sprint | 无外部 key |
| Step 2 | 即梦 adapter + Qwen-VL adapter(各调通 1 个 hello-world) | 0.5 sprint | 用户提供阿里灵积 + 火山引擎 key |
| Step 3 | 跑横评 + 输出报告 + 必要时改 pricing 表 | 0.5 sprint | 同上 |

**Step 1 必须 pytest 247 / vue-tsc 全绿**;调用方代码 0 改动,验证向后兼容。

## 5. 验收

- [x] 用户 sign-off ADR(2026-05-12)
- [ ] Step 1 完工:架构骨架就绪 + DeepSeek 走新 router + 老测试全绿
- [ ] Step 2 完工:即梦 + Qwen-VL hello-world 跑通
- [ ] Step 3 完工:横评报告产出 → 决定是否真把这两家定为漫画态主路由

## 6. 风险与对策

| 风险 | 等级 | 对策 |
|---|---|---|
| 即梦 API 限流 / 排队严重 | 🟡 中 | Step 2 hello-world 时验证限流策略;若严重,降级到通义万相做备选 |
| Qwen-VL 多模态在漫画质检场景识别率低 | 🟡 中 | Step 3 横评时用真实漫画质检 prompt 跑;失败则评估 GLM-4V |
| 阿里 / 字节 API 涨价 | 🟢 低 | pricing.py 配置化,改价格不动代码 |
| vendor 抽象层加错抽象使 D.9 接入痛苦 | 🟡 中 | Protocol 设计参照漫画态 ADR 的 6 agent 实际调用形态(prompt / 风格 / ref image) |

---

**本 ADR 是 D.8 sprint 的施工蓝图。** 改动前需更新此文档并标注 superseded。
