# ADR — 配额模式从"次数"重构为"credit token"

**状态**:✅ **用户批准(2026-05-13)** — 等待 C.1 启动
**日期**:2026-05-13(v1)
**关联**:
- `docs/ADR_D.8_国产API路由层.md`(LLM 路由层,本 ADR 的成本基础)
- `docs/ADR_漫画创作态架构.md` v3(漫画态成本估算源)
- `项目记忆.md` §"战略定位 + 商业模型"(订阅模式 v2,本 ADR 替代)

---

## 一、决策背景

### 1.1 当前"次数"模式的死穴

| 用户场景 | 配额扣减 | 真实 LLM 成本 |
|---|---|---:|
| A 用户:900 万字小说 → 1 次推演 | 1 次配额 | ¥150 |
| B 用户:9 千字短文 → 1 次推演 | 1 次配额 | ¥0.5 |
| **结论** | 同 1 次配额 | **300 倍成本差** |

漫画态同理:12 页 vs 30 页 LLM tokens 差 3 倍、图片张数差 2.5 倍,**真实成本差近 10 倍**,但都扣"1 本"。

**核心问题**:重度用户暴亏 / 轻度用户白嫖 / 平台毛利不可预测。

### 1.2 设计哲学(对标 Anthropic,优化为 C 端)

| 项 | Anthropic API | OpenAI Plus | **浑晶 v3** |
|---|---|---|---|
| 计费单位 | input/output token 分开 | 5h × 消息次数 | **credit(统一虚拟单位)** |
| 月订阅 | 按用量出账单 | 固定 $20 | **¥X = N credit 包月** |
| 冷却 | 无 | 5h 滚动 | **无**(随用随消耗) |
| 过期 | N/A | 滚动 | **月末清零**(防囤积) |
| 加购 | 自然按量 | 不能加 | **加购包 1 年有效期**(独立钱包) |

---

## 二、Credit 单位定义

### 2.1 汇率锚定

- **1 credit = ¥0.10 成本基线**(平台单 credit LLM 成本)
- **毛利倍数 1.3×**(成本基准 → 用户消耗换算)

### 2.2 各 LLM 调用 credit 单价表(2026-05-13 锁定,价格涨跌触发重评)

| LLM 调用 | 真实单价 | credit 单价 |
|---|---:|---:|
| DeepSeek V3 1K input token | ¥0.0010 | **0.013 c** |
| DeepSeek V3 1K output token | ¥0.0020 | **0.026 c** |
| Qwen-VL Max 1K vision token | ¥0.0200 | **0.26 c** |
| Doubao Seedream 4.0 1 张 | ¥0.20 | **2.6 c** |

### 2.3 典型业务动作 credit 消耗

| 动作 | LLM 组合 | credit |
|---|---|---:|
| 1 次 AI 推演(5M in + 0.5M out) | DeepSeek | **~78 c** |
| 1 次抽图谱(900K in + 100K out) | DeepSeek | **~14 c** |
| 1 次角色对焦 | DeepSeek | **~0.5 c** |
| 1 次自洽守护者 | DeepSeek | **~3 c** |
| 1 次正典守护者 | DeepSeek | **~4 c** |
| 1 次去 IP 字典生成 | DeepSeek | **~1 c** |
| 1 张画风候选 | Qwen-VL + DeepSeek + Seedream | **~3 c** |
| 1 张角色立绘卡 | DeepSeek + Seedream | **~2.6 c** |
| 1 张漫画格图 | DeepSeek + Seedream | **~2.6 c** |
| **1 本 12 页漫画**(72 格) | 综合 | **~222 c** |
| **1 本 30 页漫画**(180 格) | 综合 | **~510 c** |
| **5 万字 / 500 格漫画(分 10 批)** | 综合 | **~1340 c** |

---

## 三、订阅档位与 credit 池

### 3.1 设计目标(用户拍板 2026-05-13 v2)

1. **满配额毛利率加权平均落在 20-30%**(降低毛利,让利用户)
2. **每升一档 10% 阶梯优惠**(单 credit 售价递减 10%)
3. **年付在月付基础上再 -10%**(年付折扣)
4. **不涨订阅价**(¥138 / ¥438 / ¥1388 保持)

### 3.2 数学约束 — 三选一必有妥协

| | 方案 A(✅ 选定) | 方案 B | 方案 C |
|---|---|---|---|
| 思路 | 严格 10% 阶梯,接受高档满配额毛利 < 20% | 全档 20-30%,阶梯减弱到 ~6% | Pro 略超 30%,全档接近 20% 下限 |
| Pro 毛利 | 30% | 30% | 35% |
| Max 毛利 | 22% | 25% | 28% |
| 超级 Max 毛利 | 14%(出 20% 下限) | 20% | 20% |
| 阶梯优惠 | 严格 -10% / 段 | -6.7% / -6.2% | 严格 -10% / 段 |
| **加权平均** | **26%** ✓ | 26% ✓ | 29% ✓ |

**为什么选 A**:
- 阶梯优惠是**升档动力的核心**,不能减弱(否则 3 个 Pro ≈ 1 个 Max,用户多开号)
- 超级 Max 满配额毛利 14% 是"极端跑满"才出现,实际超级 Max 用户实跑率通常 30-50%(他们买的是"上限保障")
- **加权平均 26%** 落在 20-30% 目标区间

### 3.3 4 档月度 credit + 年付(月付 × 12 × 0.9)

| 档 | 月价 | **年付** | **月度 credit** | 单 c 售价 | 阶梯优惠 | 满配额毛利 |
|---|---:|---:|---:|---:|---|---:|
| Free | ¥0 | — | **30 c** | — | — | -¥3(获客) |
| Pro | **¥138** | **¥1488**(省 ¥168) | **970 c** | ¥0.1423/c | **基准** | **29.7%** |
| Max | **¥438** | **¥4728**(省 ¥528) | **3400 c** | ¥0.1288/c | **-9.5% 比 Pro** | **22.4%** |
| 超级 Max | **¥1388** | **¥14988**(省 ¥1668) | **12000 c** | ¥0.1157/c | **-10.2% 比 Max(累计 -18.7%)** | **13.6%** |
| 创始人 | — | — | **∞** | — | — | — |

**年付定价**:严格 `月价 × 12 × 0.9` 取整到尾数 8:
- ¥138 × 12 × 0.9 = ¥1490.4 → **¥1488**
- ¥438 × 12 × 0.9 = ¥4730.4 → **¥4728**
- ¥1388 × 12 × 0.9 = ¥14990.4 → **¥14988**

### 3.4 升档动力验证

**多个 Pro vs Max**:
| 选择 | 总价 | 总 credit | 单价 |
|---|---:|---:|---:|
| 3 × Pro 账号 | ¥414 | 2910 c | ¥0.1423/c |
| **1 × Max** ⭐ | **¥438** | **3400 c** | **¥0.1288/c** |
| **差距** | +¥24 | **+490 c** | -9.5% |

→ Max 多花 ¥24 多拿 490c + **单账号管理便利 + 全功能解锁**,升档有动力 ✓

**多个 Max vs 超级 Max**:
| 选择 | 总价 | 总 credit |
|---|---:|---:|
| 4 × Max 账号 | ¥1752 | 13600 c |
| **1 × 超级 Max** ⭐ | **¥1388** | **12000 c** |

→ 超级 Max 省 ¥364,credit 略少 1600,**适合不需要 4× Max 量的真重度用户** ✓

**月付 vs 年付**:
| 选择 | 一年总价 | 单月 credit |
|---|---:|---:|
| 12 × 月付 Pro | ¥1656 | 970 c |
| **年付 Pro** ⭐ | **¥1488**(省 ¥168 / 10.1%) | 970 c |

→ 年付省 10%,**鼓励长期承诺** ✓

### 3.5 实跑视角(为什么超级 Max 14% 不可怕)

满配额毛利只是**极端跑满**情况;实际用户实跑率多在 30-70%:

| 档 | 满配额毛利 | 实跑 50% 毛利 | 实跑 30% 毛利 |
|---|---:|---:|---:|
| Pro | 29.7% | **65%** | 79% |
| Max | 22.4% | **61%** | 76% |
| 超级 Max | 13.6% | **57%** | 74% |

**整体综合毛利估算**:
- 订阅毛利:满配额加权 26% / 实跑 50% 加权 ~61%
- 加购毛利:60-80%(独立,占用户 ~30%)
- **综合平均毛利**:**~40-55%**(对标行业 SaaS 标准)

### 3.6 加购包(1 年有效期,独立钱包,所有档位同价)

| 包 | credit | 价格 | 单价 | 加购毛利 |
|---|---:|---:|---|---:|
| 小包 | 100 c | **¥18** | ¥0.18/c(+80%) | +¥8 / +80% |
| 中包 | 500 c | **¥85** | ¥0.17/c(+70%) | +¥35 / +70% |
| 大包 | 2000 c | **¥320** | ¥0.16/c(+60%) | +¥120 / +60% |

**关键设计 — 加购最便宜的大包仍贵于最高订阅档**:

| | 单价 | 倍数 |
|---|---:|---:|
| 加购大包 2000c | ¥0.160 / c | — |
| Pro 订阅 | ¥0.1423 / c | 加购贵 12% |
| Max 订阅 | ¥0.1288 / c | 加购贵 24% |
| 超级 Max 订阅 | ¥0.1157 / c | **加购贵 38%** |

→ 加购贵 12-38%,**继续鼓励用户上更高档 OR 走年付**(健康)

### 3.7 铁律

- **不再涨订阅价 / 不再涨订阅 credit 池**(任何涨调影响阶梯优惠平衡)
- 需要更多算力 → 加购包(60-80% 毛利)OR 升档(享受更便宜单价)
- Pro 永远是基准档,新档(若未来加)按 10% 阶梯递减
- 年付永远月付 × 12 × 0.9(尾数 8 取整),不打折"5 折特惠"等乱阵脚

### 3.4 月度 credit 清零 vs 加购 1 年有效期

| 维度 | 月订阅 credit | 加购 credit |
|---|---|---|
| 有效期 | **月末清零** | **从购买日起 1 年** |
| 钱包字段 | `subscription_credits` | `addon_credits` |
| 月度重置 | 是(cron `month_reset`) | 否(到期单独清) |
| 用途 | 防囤积压成本 | 用户信任锚(类比 1.N "永久保留承诺") |
| 消耗顺序 | **优先扣订阅 credit**(用了就用了,反正月末清)→ 不够时再扣加购 | 兜底 |

**1 年有效期理由**:
- 防加购包成无限延展财务负债
- 1 年足够任何"鸟枪式囤积"消耗
- 到期前 30 天前端提示 "你的加购 credit 还有 X 在 30 天后过期"
- 已过期 credit 不退款(用户协议条款)

---

## 四、漫画态分批承接机制(借鉴 1.O 滚雪球)

### 4.1 问题

5 万字 → 500 张图的漫画,单次 LLM 调用 / 单次 Seedream 批量都做不到。Sprint 2.B+ 七修 KISS 把 30 页降到 12 页是临时方案,长篇能力丢失。

### 4.2 设计

**整条漫画态流水线分批**,不只编剧分批:

```
全本锚(锚定一次,全批复用 — 保证一致性):
  - 画风 anchor(style_anchor_image_url + detailed_prompt)
  - 角色立绘卡(character_cards 表,前 6 主角,L1-L4 一致性方案)
  - 视觉素材库(character_visuals / scenes / props)
                  ↓
分批游标(每批一个 batch 记录):
  批 1:文本 cursor 0 → 5000 字 → 编剧 6 页 → 36 格图
  批 2:文本 cursor 5000 → 10000 字 → 编剧承接批 1 末页 + 36 格图
  批 3:文本 cursor 10000 → 15000 字 → 编剧承接批 2 末页 + 36 格图
  ...
  批 N:剩余文本 → 收尾
                  ↓
最终拼接:按 batch_index 拼 comic_pages → 完整漫画
```

### 4.3 优势

- ✅ **5 万字 → 500 张图优雅完成**(批次 = LLM 单次输出能力的天花板)
- ✅ **批失败可重跑**(对齐 1.P 断点续推 — `_RUNNING_COMICS` 已有注册表)
- ✅ **credit 按批结算**(用户可看实时进度;中途取消按已跑批次扣 + 退未跑批次)
- ✅ **AI Planner 输出"建议 X 批 × Y 页 = Z credit 总预算"**(用户拍板再启动)
- ✅ **画风 / 角色一致性天然保证**(锚是全本共用,批次只承接剧本上下文)

### 4.4 新表 `comic_batches`

```sql
CREATE TABLE comic_batches (
    id              TEXT PRIMARY KEY,
    comic_id        TEXT NOT NULL REFERENCES comic_projects(id),
    batch_index     INTEGER NOT NULL,        -- 1-indexed
    source_cursor_start  INTEGER NOT NULL,   -- 本批起始字符位置
    source_cursor_end    INTEGER NOT NULL,   -- 本批结束位置
    pages_range_start    INTEGER NOT NULL,   -- 本批 page_index 起
    pages_range_end      INTEGER NOT NULL,
    state           TEXT NOT NULL,           -- queued/running/done/failed
    cost_credits    INTEGER NOT NULL DEFAULT 0,
    error_message   TEXT,
    created_at      TEXT NOT NULL,
    completed_at    TEXT,
    UNIQUE(comic_id, batch_index)
);
```

---

## 五、新增 Agent — AI 文本规划员(Planner)

### 5.1 定位

漫画态流水线 **Agent #1.5**,在编剧之前跑。轻量 LLM 调用(~0.5 credit / 次)。

### 5.2 输入 / 输出

**输入**:
- source 全文长度
- 图谱角色数 + 关键事件密度(`event_relationships` 表)
- 用户偏好(可选:倾向短篇 / 长篇)

**输出**:
```json
{
  "recommended_total_pages": 18,
  "recommended_panels_per_page": 6,
  "recommended_batches": 3,
  "batch_size_pages": 6,
  "estimated_credit_cost": 380,
  "estimated_minutes": 25,
  "reasoning": "原文 12 万字 / 8 主角 / 35 事件 → 推荐 18 页 108 格,分 3 批"
}
```

### 5.3 UX 流程

```
CreateComicModal 选输入源 → "下一步"
    ↓
AI Planner 跑 5 秒
    ↓
弹规划结果 modal:
  「AI 建议:18 页(可调 6-50)→ 预计 380 credit / 25 分钟」
  [推荐] [我自己定]
    ↓
用户拍板 → 进 RefImageUploadDialog
```

### 5.4 漫画态新流水线(11 agent)

```
#1   Orchestrator(状态机)
#1.5 ⭐ Planner — 新加
#2   Screenwriter — Sprint 3 改分批承接
#3v2 Style Director
#4   Character Anchor
#5   Visual Assets Extractor
#6   Director — Sprint 3
#7   Image Generator — Sprint 3
#8   Visual QA — Sprint 3
#9   Inpainter — Sprint 4
#10  Typesetter — Sprint 4
```

---

## 六、DB schema 变更

### 6.1 新表

#### `user_credit_balances`(每用户钱包)

```sql
CREATE TABLE user_credit_balances (
    user_id              TEXT PRIMARY KEY REFERENCES users(id),
    month_start          TEXT NOT NULL,        -- 本月起点 ISO(月度重置参考点)
    subscription_credits INTEGER NOT NULL,     -- 月订阅 credit(月末清零)
    addon_credits        INTEGER NOT NULL,     -- 加购 credit(1 年有效)
    updated_at           TEXT NOT NULL
);
```

#### `credit_transactions`(明细审计)

```sql
CREATE TABLE credit_transactions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id),
    delta       INTEGER NOT NULL,    -- 正=充值/退款,负=消耗
    wallet      TEXT NOT NULL,       -- subscription / addon
    kind        TEXT NOT NULL,       -- subscribe_grant / addon_purchase / consume / refund / month_reset / addon_expire
    action      TEXT,                -- continuation / extract / refine / comic / vision / image
    related_id  TEXT,                -- sim_id / comic_id / batch_id
    cost_yuan   REAL NOT NULL DEFAULT 0,
    metadata    TEXT,                -- JSON
    created_at  TEXT NOT NULL
);
```

#### `addon_credit_lots`(加购批次 — 跟踪 1 年有效期)

```sql
CREATE TABLE addon_credit_lots (
    id           TEXT PRIMARY KEY,
    user_id      TEXT NOT NULL REFERENCES users(id),
    initial_credits  INTEGER NOT NULL,   -- 购买时 credit 数
    remaining_credits INTEGER NOT NULL,   -- 当前剩余(月末清零仅订阅,这里不动)
    purchased_at TEXT NOT NULL,
    expires_at   TEXT NOT NULL,            -- purchased_at + 1 年
    is_expired   INTEGER NOT NULL DEFAULT 0
);
```

#### `comic_batches`(漫画分批)

见 §4.4。

### 6.2 老表保留

- `usage_logs`:保留(审计 + 价格快照兼容),新代码 dual-write 直到 C.7
- `PLAN_LIMITS`:保留 `reshape_max_percent` / `characters_per_project` / `projects_total`(非 AI 消费类硬限);**删除** `*_per_month` 字段
- `user_plan_snapshots`:加字段 `monthly_credits_quota`,老用户老规则铁律落地

### 6.3 老用户迁移

```sql
-- migration 030_credit_system.sql 末尾
-- 给所有现有用户初始化 wallet(按当前 plan 发放本月 credit)
INSERT INTO user_credit_balances (user_id, month_start, subscription_credits, addon_credits, updated_at)
SELECT
  u.id,
  '2026-05-01T00:00:00+00:00',
  CASE u.plan
    WHEN 'free' THEN 30
    WHEN 'pro' THEN 970          -- Sprint C.1(2026-05-13)v2 毛利率 20-30% 控制后
    WHEN 'max' THEN 3400         -- Pro 29.7% / Max 22.4% / 超级 Max 13.6%
    WHEN 'super_max' THEN 12000  -- 加权满配额 26%(60/30/10 用户分布假设)
    WHEN 'founder' THEN 9999999
    ELSE 30
  END,
  0,
  datetime('now')
FROM users u;
```

---

## 七、工程拆分(5 sprint)

| Sprint | 内容 | 工作量 |
|---|---|---:|
| **C.1** ADR sign-off + schema + 老用户迁移 | 本文档落定 / migrations 030-033 / init_db 跑通 / 老用户迁移 SQL | 0.5 |
| **C.2** credit_service 核心 | `consume_credit / refund_credit / get_balance / month_reset / addon_expire` + 单元测试 + cron job | 1.0 |
| **C.3** 接通现有 LLM 调用 | `llm_client.consume_credit_for_call()` wrapper / refine / continuation / extract / comic 全接通 / dual-write usage_logs | 1.0 |
| **C.4** 前端 CreditBalance + 预览 | QuotaIndicator 重写 / 每 AI 动作前显"预计消耗 X c" / 不足弹 UpgradeModal / 加购入口 | 1.0 |
| **C.5** AI Planner + 漫画分批承接 | `_agent_planner` / comic_batches 表 / 编剧分批承接 LLM 调用 / 用户调节 page 数滑块 | 1.0 |
| **C.6** 加购包真支付 + 升降级 | 加购入口 / 老快照兼容 / 月末清零 cron / addon 过期 cron | 0.5 |
| **总计** | | **5 sprint** |

> 注:**C.1-C.5 必须连续做完才能上线**,C.6 可独立(支付通道未接前用 mock 触发)。

---

## 八、UI 重设计要点

### 8.1 QuotaIndicator 重写

```
┌─────────────────────────────┐
│ 浑晶 credit 余额            │
│                             │
│ 订阅 ★ 1,247 / 1,380        │
│ ████████████░░  90%        │
│ 5 月 31 日清零 · 还剩 18 天 │
│                             │
│ 加购 ★ 200 c                │
│ ░░░░░░░░░░ (永久有效)      │
│                             │
│ [加购 100 c · ¥18]          │
└─────────────────────────────┘
```

### 8.2 AI 动作前预览

```
SimulationDock 提交按钮上方:
┌────────────────────────────────────┐
│ 本次 AI 推演预计消耗 ~80 credit    │
│ (按原文 5 万字 × 30 轮估算)         │
│ 你余额 1247 c → 跑完后约 1167 c    │
└────────────────────────────────────┘
[ AI 推演 ]
```

### 8.3 漫画分批进度

```
ComicProjectView 跑漫画时:
全本进度:■■■■□□□□□□  4/10 批
已消耗:520 / 1340 c
当前批:批 4 / 文本 15000-20000 字 / 36 格图正在跑
```

---

## 九、风险与缓解

| 风险 | 缓解 |
|---|---|
| 读者层用户看不懂 credit | UI 加"等价表"提示:"1380 c ≈ 1 本漫画 + 5 次推演 + 几次对焦" |
| 月末清零引发突击使用 | 月末 3 天前端提示"剩余 X c,29 日清零" |
| 加购 1 年到期前用户抱怨 | 到期前 30 天 / 7 天 / 1 天 三次邮件提醒 + 前端横幅 |
| LLM 价格涨成本击穿 | 价格快照(snapshot)锁定用户买单时规则,平台短期承担,季度评估调价 |
| 用户怕"白花钱"不敢点 AI | 每动作前显预计消耗 + 完成后显实际消耗(透明计量) |
| 老用户体感"额度变少" | `user_plan_snapshots.monthly_credits_quota` 保证老用户老规则;新用户走新规则 |
| 加购包永久变 1 年用户不爽 | 1 年期是行业惯例(支付宝充值 / 视频网站会员等),首次提示清楚 |
| 加购毛利 60-80% 过高 | 这部分专门补订阅满配额平本风险,**整体毛利按订阅 50% 实跑 + 加购 70% 综合估 +60%**,符合行业标准 |

---

## 十、Sprint 路线图(整合后)

```
当前位置:Sprint 2.B+ 七修完工(D.9 漫创态 ≈ 35%)
                ↓
【C 系列商业模式重构 5 sprint】← 本 ADR
                ↓
Sprint 3:_agent_director / _generator / _qa(漫画态全链路)1.5 sprint
                ↓
Sprint 4:_inpainter / _typesetter / 阅读器 / PDF 1 sprint
                ↓
E 阶段部署 3 sprint
                ↓
总剩余:~10.5 sprint
```

**关键变更**:**C 系列重构插在 D.9 Sprint 3 前**。Sprint 3 是图像生成主战场(180 格),没有 credit 系统会被刷爆;反之 credit 系统上线后,Sprint 3 的成本控制天然解决(用户自付)。

---

## 十一、批准签名

- **用户(创始人)**:✅ 批准于 2026-05-13(对话内确认 10 项决策点 + 漫画分批承接洞察)
- **AI 工程师(Claude)**:已落本 ADR,等待 C.1 启动指令

---

## 附录 A:Credit 与"次数模式"对照表

| 老"次数"模式 | 新 credit 模式(2026-05-13 v2 毛利率 20-30% 控制版) |
|---|---|
| Free 5 次对焦 | Free 30 c ≈ 60 次对焦(消耗 0.5c/次) |
| Pro 30 次对焦 + 5 次推演 + 1 本漫画 | Pro **970 c** ≈ 40 次对焦 + 3 次推演 + 1 本 12 页漫画(用户自选,毛利 29.7%) |
| Max 100 + 15 + 3 | Max **3400 c** ≈ 全开放 + 单 credit 比 Pro 便宜 9.5%(毛利 22.4%) |
| 超级 Max 300 + 50 + 10 | 超级 Max **12000 c** ≈ 团队级别 + 单 credit 比 Max 便宜 10.2%(毛利 13.6%) |

**核心变化**:用户可以**自主分配**这些 credit 给任何 AI 动作,不再被固定次数锁死。

---

## 附录 B:对外宣传话术(给市场用)

**核心 punchline**:
> "970 浑晶 credit / 月 起 — 你想推演就推演,想做漫画就做漫画,自己定"

**升档 punchline**:
> "Max 单 credit 比 Pro 便宜 9.5%,超级 Max 再便宜 10.2% — 越用越划算,不用开多账号"

**年付 punchline**:
> "包年直接 9 折,Pro 省 ¥168 / Max 省 ¥528 / 超级 Max 省 ¥1668"

**对比 Anthropic / OpenAI**:
> "对标 Anthropic API 的精准计量,但简化为 C 端用户友好的 credit 池模式 — 月发放、月末清零、永不冷却"

**承诺**:
> "永久保留承诺(1.N)" + "credit 透明计量(C 系列)" + "已生成作品永不删除"
