# D.9 Sprint 0 — Test 3:素材库抽取员产物质量

**生成时间**:2026-05-12T19:19:43.943728
**测试材料**:红楼梦节选(~1057 字)
**调用耗时**:21.5s
**token 用量**:input=1486 / output=2166
**成本**:¥0.0058

---

## 总评

- 角色抽到 **3** 个,标量字段填空率 **63.3%**
- 场景抽到 **5** 个,标量字段填空率 **100.0%**
- 道具抽到 **6** 个,标量字段填空率 **100.0%**

**填空率判定**:
- ≥ 70% → ✅ 可用(Agent #4 描述符 20-30 句的输入质量够)
- 50-70% → 🟡 边缘(L1 一致性预期降到 85%)
- < 50% → 🔴 不够(需要 prompt 调优 / 加 fallback 提问)

## A. character_visuals 详情

| 角色 | 标量填空率 | 配饰数 | 标志道具数 | 灵魂特质 |
|---|---:|---:|---:|---:|
| 林黛玉 | 11/20 (55.0%) | 2 | 0 | ✓ |
| 贾宝玉 | 14/20 (70.0%) | 2 | 1 | ✓ |
| 王熙凤 | 13/20 (65.0%) | 4 | 2 | ✓ |

## B. scenes 详情

| 场景 | 标量填空率 | 关键陈设数 |
|---|---:|---:|
| 荣国府正门 | 5/5 (100.0%) | 2 |
| 大观园 | 5/5 (100.0%) | 4 |
| 潇湘馆 | 5/5 (100.0%) | 3 |
| 怡红院 | 5/5 (100.0%) | 6 |
| 栊翠亭 | 5/5 (100.0%) | 9 |

## C. props 详情

| 道具 | 类型字段填空率 | 拥有者 | 故事意义 |
|---|---:|:---:|:---:|
| 通灵宝玉 | 2/2 | 贾宝玉 | ✓ |
| 粉色绣花团扇 | 2/2 | 王熙凤 | — |
| 银鞘小剑 | 2/2 | 王熙凤 | ✓ |
| 端砚 | 2/2 | 林黛玉 | — |
| 湘妃竹笔 | 2/2 | 林黛玉 | — |
| 象牙棋子 | 2/2 | (无主) | — |

---

## D. 原始 LLM 抽取产物(JSON)

<details><summary>展开查看(用于人工校对漏抽 / 抽错)</summary>

```json
{
  "character_visuals": [
    {
      "name": "林黛玉",
      "face_json": {
        "eye_shape": "含情目",
        "eye_color": "(原文未明)",
        "eyebrow": "罥烟眉",
        "nose": "(原文未明)",
        "mouth": "(原文未明)",
        "face_shape": "(原文未明)",
        "skin_tone": "(原文未明)",
        "marks": "两靥之愁"
      },
      "hair_json": {
        "length": "(原文未明)",
        "color": "(原文未明)",
        "texture": "(原文未明)",
        "hairstyle": "头戴珠翠",
        "bangs": "(原文未明)"
      },
      "body_json": {
        "height_range": "十二三岁",
        "body_type": "纤巧",
        "posture": "弱柳扶风",
        "signature_action": "泪光点点"
      },
      "outfit_json": {
        "garment": "绫罗长裙",
        "color": "月白色",
        "style": "腰系玉色绦带"
      },
      "accessories_json": [
        {
          "name": "珠翠",
          "position": "头上",
          "color": "(原文未明)"
        },
        {
          "name": "白玉簪",
          "position": "鬓边",
          "color": "白"
        }
      ],
      "signature_props_json": [],
      "soul_traits": "心较比干多一窍，病如西子胜三分，闲静时如姣花照水，行动处似弱柳扶风。"
    },
    {
      "name": "贾宝玉",
      "face_json": {
        "eye_shape": "秋波",
        "eye_color": "(原文未明)",
        "eyebrow": "眉如墨画",
        "nose": "鼻如悬胆",
        "mouth": "(原文未明)",
        "face_shape": "面若中秋之月",
        "skin_tone": "色如春晓之花",
        "marks": "(原文未明)"
      },
      "hair_json": {
        "length": "(原文未明)",
        "color": "(原文未明)",
        "texture": "(原文未明)",
        "hairstyle": "束发嵌宝紫金冠",
        "bangs": "齐眉勒着二龙抢珠金抹额"
      },
      "body_json": {
        "height_range": "年约十四",
        "body_type": "身形挺拔",
        "posture": "常带笑容",
        "signature_action": "眼神温润"
      },
      "outfit_json": {
        "garment": "二色金百蝶穿花大红箭袖",
        "color": "大红",
        "style": "束着五彩丝攒花结长穗宫绦"
      },
      "accessories_json": [
        {
          "name": "金螭璎珞",
          "position": "项上",
          "color": "金"
        },
        {
          "name": "五色丝绦",
          "position": "项上",
          "color": "五色"
        }
      ],
      "signature_props_json": [
        {
          "name": "通灵宝玉",
          "description": "玉上有'莫失莫忘，仙寿恒昌'八字"
        }
      ],
      "soul_traits": "虽怒时而似笑，即瞋视而有情，眼神温润常带笑容。"
    },
    {
      "name": "王熙凤",
      "face_json": {
        "eye_shape": "丹凤三角眼",
        "eye_color": "(原文未明)",
        "eyebrow": "柳叶吊梢眉",
        "nose": "(原文未明)",
        "mouth": "丹唇未启笑先闻",
        "face_shape": "粉面含春威不露",
        "skin_tone": "粉面",
        "marks": "(原文未明)"
      },
      "hair_json": {
        "length": "(原文未明)",
        "color": "(原文未明)",
        "texture": "(原文未明)",
        "hairstyle": "金丝八宝攒珠髻",
        "bangs": "(原文未明)"
      },
      "body_json": {
        "height_range": "高挑",
        "body_type": "娉婷",
        "posture": "神情张扬",
        "signature_action": "其声爽利"
      },
      "outfit_json": {
        "garment": "缕金百蝶穿花大红洋缎窄褙袄，外罩五彩刻丝石青银鼠褂，下着翡翠撒花洋绉裙",
        "color": "大红、石青、翡翠",
        "style": "窄褙袄"
      },
      "accessories_json": [
        {
          "name": "朝阳五凤挂珠钗",
          "position": "头上",
          "color": "金"
        },
        {
          "name": "赤金盘螭璎珞圈",
          "position": "项下",
          "color": "赤金"
        },
        {
          "name": "豆绿宫绦",
          "position": "裙边",
          "color": "豆绿"
        },
        {
          "name": "双衡比目玫瑰佩",
          "position": "裙边",
          "color": "玫瑰"
        }
      ],
      "signature_props_json": [
        {
          "name": "粉色绣花团扇",
          "description": "扇面上绣着一双戏水鸳鸯"
        },
        {
          "name": "银鞘小剑",
          "description": "祖上传下的信物"
        }
      ],
      "soul_traits": "粉面含春威不露，丹唇未启笑先闻，其声爽利，其貌威严。"
    }
  ],
  "scenes": [
    {
      "name": "荣国府正门",
      "location_type": "室外",
      "era": "古代",
      "architecture_style": "中式府邸",
      "lighting": "白天",
      "season": "未指明",
      "key_props_json": [
        "石狮子",
        "匾额（敕造宁国府）"
      ]
    },
    {
      "name": "大观园",
      "location_type": "室外",
      "era": "古代",
      "architecture_style": "中式园林",
      "lighting": "白天",
      "season": "春",
      "key_props_json": [
        "翠竹",
        "粉墙",
        "青藤",
        "楼阁"
      ]
    },
    {
      "name": "潇湘馆",
      "location_type": "室内",
      "era": "古代",
      "architecture_style": "中式园林",
      "lighting": "黄昏",
      "season": "春",
      "key_props_json": [
        "紫檀木书案",
        "端砚",
        "湘妃竹笔"
      ]
    },
    {
      "name": "怡红院",
      "location_type": "室内",
      "era": "古代",
      "architecture_style": "中式园林",
      "lighting": "午后阳光",
      "season": "春",
      "key_props_json": [
        "西府海棠",
        "紫檀大床",
        "锦绣帷幔",
        "珍珠帘",
        "古玩玉器",
        "名家字画"
      ]
    },
    {
      "name": "栊翠亭",
      "location_type": "室外",
      "era": "古代",
      "architecture_style": "中式园林",
      "lighting": "白天",
      "season": "夏",
      "key_props_json": [
        "假山",
        "池水",
        "游鱼",
        "垂柳",
        "紫檀棋桌",
        "象牙棋子",
        "青色琉璃瓦",
        "朱红柱子",
        "铜铃"
      ]
    }
  ],
  "props": [
    {
      "name": "通灵宝玉",
      "prop_type": "玉器",
      "owner_character_name": "贾宝玉",
      "visual_description": "五色丝绦系着，上有'莫失莫忘，仙寿恒昌'八字",
      "story_significance": "宝玉衔玉而诞，是身份象征和关键情节道具"
    },
    {
      "name": "粉色绣花团扇",
      "prop_type": "服饰",
      "owner_character_name": "王熙凤",
      "visual_description": "粉色，绣着戏水鸳鸯",
      "story_significance": null
    },
    {
      "name": "银鞘小剑",
      "prop_type": "武器",
      "owner_character_name": "王熙凤",
      "visual_description": "银鞘，祖上传下",
      "story_significance": "祖上传下的信物"
    },
    {
      "name": "端砚",
      "prop_type": "文房",
      "owner_character_name": "林黛玉",
      "visual_description": "端砚，置于紫檀书案",
      "story_significance": null
    },
    {
      "name": "湘妃竹笔",
      "prop_type": "文房",
      "owner_character_name": "林黛玉",
      "visual_description": "湘妃竹制，数支",
      "story_significance": null
    },
    {
      "name": "象牙棋子",
      "prop_type": "其他",
      "owner_character_name": null,
      "visual_description": "象牙制，一副，置于紫檀棋桌",
      "story_significance": null
    }
  ]
}
```

</details>

---

## E. ADR 校准建议

- 平均字段填空率:**87.8%**
- 🟢 ADR v3 §3.2 Agent #5 设计**可用**;描述符 20-30 句的输入质量符合预期