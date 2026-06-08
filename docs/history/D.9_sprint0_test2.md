# D.9 Sprint 0 — Test 2:Qwen-VL 视觉 DNA 提取准度

**生成时间**:2026-05-12T19:22:59.817649
**Seedream 出图成本**:¥0.6000(3 张 × ¥0.20)
**Qwen-VL 分析成本**:¥0.1002(3 次 × token-based)
**总成本**:¥0.7002

---

## 测试材料 — 3 张同画风(国漫工笔半厚涂)不同内容图

### 图 1:人物(古代少女桃树下)
- URL:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/02177858493512643cc1cf6ced7e437334ae832707c2826d335a0_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T112221Z&X-Tos-Expires=86400&X-Tos-Signature=29840a83e2f26c50482c239abb278c7438a9c8131b07e31e9eb14c982dab1857&X-Tos-SignedHeaders=host
- Qwen-VL 分析耗时:5.6s

### 图 2:场景(中式园林亭台)
- URL:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/0217785849436952970295c18f318e807dda86bfc1484747ee4f0_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T112230Z&X-Tos-Expires=86400&X-Tos-Signature=f6b034f3b74860d81d0e66978d8b67d981d9d9d951b0c5b71a126422e4404521&X-Tos-SignedHeaders=host
- Qwen-VL 分析耗时:5.6s

### 图 3:道具(古剑古籍特写)
- URL:https://ark-content-generation-v2-cn-beijing.tos-cn-beijing.volces.com/doubao-seedream-4-0/021778584953540e577630002db537cb4c69978019db15b732891_0.jpeg?X-Tos-Algorithm=TOS4-HMAC-SHA256&X-Tos-Credential=AKLTYWJkZTExNjA1ZDUyNDc3YzhjNTM5OGIyNjBhNDcyOTQ%2F20260512%2Fcn-beijing%2Ftos%2Frequest&X-Tos-Date=20260512T112241Z&X-Tos-Expires=86400&X-Tos-Signature=7402328feda176b0ebe79fdb663373e005f381bae76e4677151ed97cf574a549&X-Tos-SignedHeaders=host
- Qwen-VL 分析耗时:4.5s

---

## 视觉 DNA 一致性评分

**6 字段中 6 个 ≥ 2/3 一致 → 100.0% 一致率 → 🟢 可用(Qwen-VL 能稳定识别画风共性)**

| 字段 | 图1 | 图2 | 图3 | 共识 | 得分 |
|---|---|---|---|---|---:|
| brush_style | 水彩 | 厚涂 | 厚涂 | 厚涂 (2/3) | 66.7% |
| coloring | 明亮 | 高饱和 | 明亮 | 明亮 (2/3) | 66.7% |
| line_work | 朦胧线 | 清晰勾线 | 清晰勾线 | 清晰勾线 (2/3) | 66.7% |
| character_proportion | 日漫大眼 | 其他 | 其他 | 其他 (2/3) | 66.7% |
| lighting_logic | 氛围光 | 氛围光 | 强对比 | 氛围光 (2/3) | 66.7% |
| composition | 中近景 | 全景 | 中近景 | 中近景 (2/3) | 66.7% |

**共享主色**:(3 张无共同主色)

**style_tag 对比**:
- 梦幻水彩风古风少女
- 东方园林夕照唯美风
- 东方古典风格的细腻厚涂

---

## 原始视觉 DNA(每张图)

### 图 1 DNA
```json
{
  "brush_style": "水彩",
  "coloring": "明亮",
  "line_work": "朦胧线",
  "character_proportion": "日漫大眼",
  "lighting_logic": "氛围光",
  "composition": "中近景",
  "color_palette": [
    "粉白",
    "浅灰",
    "淡蓝"
  ],
  "overall_style_tag": "梦幻水彩风古风少女"
}
```

### 图 2 DNA
```json
{
  "brush_style": "厚涂",
  "coloring": "高饱和",
  "line_work": "清晰勾线",
  "character_proportion": "其他",
  "lighting_logic": "氛围光",
  "composition": "全景",
  "color_palette": [
    "橙红",
    "翠绿",
    "深蓝"
  ],
  "overall_style_tag": "东方园林夕照唯美风"
}
```

### 图 3 DNA
```json
{
  "brush_style": "厚涂",
  "coloring": "明亮",
  "line_work": "清晰勾线",
  "character_proportion": "其他",
  "lighting_logic": "强对比",
  "composition": "中近景",
  "color_palette": [
    "米黄",
    "金黄",
    "深棕"
  ],
  "overall_style_tag": "东方古典风格的细腻厚涂"
}
```

---

## ADR 校准建议

- 🟢 ADR v3 §3.2 Agent #3 v2 设计**可用**;视觉 DNA 提取稳定,可作为详细 prompt 输入
- 实施建议:DeepSeek 综合视觉 DNA 时,优先采用 3 张图**多数票**的字段值