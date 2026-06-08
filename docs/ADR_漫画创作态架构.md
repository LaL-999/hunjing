# ADR — 漫画创作态(comic mode)架构决策记录

**别名(UI 短名)**:漫创态 — Sprint 2.B+(2026-05-12)起,UI 用户可见层(`CreationModeQuadrant.title` /
`NewProjectModal.label` / `MyComicsView` 等)统一使用 2 字短名 **"漫创态"**,与"初始态/中间态/末尾态"
3 字节奏对齐。本文档及其他架构层文档(后端注释 / 项目记忆架构 section)保留**全称"漫画创作态"**。
后端 enum 字面量 `'cycle'` 不变。

**状态**:🧪 **工程内测**(D.9 Sprint 2.A 后端 4 agent + 2.B 前端 UI 完工 + Sprint 2.B+ 入口收尾;
Sprint 3 接 _agent_director 全链路;法务备案 + 公司注册完成后对读者层正式开放)
**日期**:2026-05-11(v1) / 2026-05-12(v2 + v3 双重修订 + Sprint 2.A/2.B/2.B+ 落地)
**v2 修订要点**(D.8 完工后):
1. D.8 国产 API 路由层 + 横评完工 → 主路由锁定 Doubao Seedream 4.0(原 3.0 已 deprecated) + Qwen-VL Max
2. 新增 2 个 agent:**画风定调员** + **角色锚定员**(6 agent → 8 agent)
3. 新增 **角色一致性 4 层方案** 独立章节
4. 修订 DB schema:`comic_projects` 加 3 字段 + 新表 `character_cards`
5. 修订成本估算:¥32 → ¥51.4 / 本

**v3 修订要点**(2026-05-12 第二轮横评后用户反馈):
1. **画风定调员升级 v2** — 用户上传 3 张参考图 → Qwen-VL 多模态分析视觉 DNA → DeepSeek 综合
   出 200-400 字详细 prompt → 出 5 张定调图(原 v2 是 LLM 纯文字推 5 风格,实测"不出彩")
2. **新增 Agent #5 素材库抽取员**:扩 extract_service 抽取维度
   - `character_visuals`(角色面部/体态/穿搭细节)— 让 L1 立绘卡描述符从 8-12 句 → 20-30 句
   - `scenes`(场景元素 — 名称/地理/时代/建筑/光照/季节)
   - `props`(道具 — 名称/类型/拥有者/描述)
3. **AI 对焦机制扩展**:从只对角色,扩展到对焦场景/道具
4. **3D 图谱扩展**:加 scene/prop 节点
5. **副作用收益**:中间态续写也受益(导演 agent 拿到丰富场景/道具描述)
6. 工程量 12-13 → **14-16 sprint**(+2-3);单本成本 ¥51.4 → **~¥53**(+¥1.6)

**作者**:用户提出构想 + 协作 AI 整理
**触发**:用户主动质疑"周期态语义与中间态重合"+ 提出大胆构想"批量生成漫画"
**影响范围**:第 4 态语义重定义 / 后端 enum 未来重命名 / 国产 LLM 路由扩展 / D 阶段计划重排

---

## 1. 背景与起源

### 1.1 周期态语义问题(已暴露)

原 Sprint 3.B "周期态" 设计:**中间态 + 末尾态结合,多变量多轮推演,产物累积成长篇**。

用户暴露的核心问题(2026-05-11 对话):
> 周期态是更改变量加上续写原作末尾,如果被更改的变量位于原文的中间,是不是原文后半段都要被改变了,然后续写被改变的原文 — 这个逻辑在中间态也能做到。

**工程师确认**:中间态自 Sprint 1.O 起就支持 **滚雪球**(`context_simulation_ids`):
1. 第 N 次推演:在 N-1 产物上接续 + 加新反事实 → 第 N 章
2. 累积 50 次 → 20 万字长篇

**这跟"周期态"设计目标 100% 重合**。周期态作为独立 mode 是冗余的 — 同一引擎的 UI 双包装。

### 1.2 用户新构想:把第 4 态重定义为"漫画创作态"

用户拍板(2026-05-11):
> 大模型已经具备图像生成能力,那么是不是就代表我们可以批量生成漫画了?这是一个很大胆的构想,但是只要能实现,那就不是构想而是目标。

输入源 2 选 1:
- **平台内**:用户在「我的剧情线」里的产物(前 3 态创作产出)
- **平台外**:用户手动导入的文本

**隔离原则**:前 3 态项目页 / 详情页**不能有跳转到第 4 态的链接**(防生态污染 + 画蛇添足)。

---

## 2. 决策:接受漫画态作为第 4 态新语义

### 2.1 为什么接受

| 维度 | 判定 |
|---|---|
| **产品差异化** | ✅ 同人圈核心痛点之一(我想看 X 配 Y 漫画);竞品文本 AI 没图谱 RAG,绘图 AI 没文本理解 |
| **DNA 匹配度** | ✅ 浑晶 `extract_service` 已经抽出全量角色 / 关系 / 事件,**漫画态独家可复用此图谱保证角色一致性** |
| **商业模型对齐** | ✅ 单本漫画成本 ~¥53(v3 修订,见 §8),与 doc 13 268 元创作者层 ≈ 5 本/月对应 |
| **法务合规可走通** | 🟡 需切国产绘图 API + 国产多模态质检(GPT-4V / Midjourney 出境违规)+ **用户上传参考图作画风分析的合规边界**(v3 新增,需法务确认是否需单独协议) |
| **工程量** | 🔴 **13-15 sprint**(v3 Sprint 0 后下调 1 sprint:L3 ref image 不必死磕 + 素材库抽取 prompt 一版到位)|
| **角色一致性挑战** | 🟢 低(v1 是🔴 / v2 是🟡)— v3 立绘卡描述符 20-30 句(含面部 + 体态 + 穿搭),配合 4 层方案,**Sprint 0 实测 L2 单层就达 95/100** → 预期一致性 92-95% |

### 2.2 为什么不立即开发(延后到 E 阶段后)

**4 个前置条件未达成**:

1. **公司注册 + AIGC 算法 / 服务备案下证**(P0 法务死线,见项目记忆 P0 公司层)
2. **国产绘图 / 多模态 API 路由选型完成**(独立 1-2 sprint 技术决策周期 — 通义万相 / 即梦 / 可灵 / 文心一格横评,Qwen-VL Max / GLM-4V / 文心多模态选型)
3. **读者层(初始 + 中间 + 末尾)真实用户跑起来 + 商业模型验证**(避免凑齐 4 态的"完美主义陷阱")
4. **法务确认"AI 生成漫画"在 AIGC 备案范畴内 + 视觉内容审核合规通路打通**

只有这 4 条都达成,才进入漫画态 **12-13 sprint** 的开发周期(v2 工程量,详见 §2.1 决策矩阵)。

---

## 3. 架构:10 agent(v3 — 在 v2 的 8 agent 上 + 多模态画风定调 v2 + 素材库抽取员)

### 3.1 Gemini 9 agent 评估

原架构(Gemini 提案 — 用户转述):

1. 总控/主编 Orchestrator
2. 编剧 Screenwriter
3. 世界观/设定 Lore Master
4. 导演/分镜 Storyboarder
5. 提示词翻译 Prompt Engineer
6. 图像生成 Illustrator
7. 局部重绘 Inpainter
8. 视觉质检 QA Reviewer
9. 排版嵌字 Typesetter

### 3.2 v3 修订 — 浑晶 10 agent 版本

**演进历史**:
- v1 Gemini 9 → 浑晶 6:合并导演 + 提示词翻译,砍世界观(复用 extract_service 图谱)
- v2 浑晶 6 → 8:**+画风定调员 + 角色锚定员**(角色一致性核心)
- **v3 浑晶 8 → 10**:**+画风定调员升级到多模态视觉 DNA 提取 + 素材库抽取员**(用户拍板:
  prompt 工程化要"长 prompt + 视觉锚",抽取要"角色细节 + 场景 + 道具")

```
#   浑晶 agent            对应 Gemini   触发时机                       浑晶处置
─────────────────────────────────────────────────────────────────────────────────────
1.  总控 Orchestrator     [Gemini 1]    全程                            ✅ 保留
                          类比 simulation_service 状态机,复用 threading.Thread
                          + _RUNNING_* 注册表 + cancel 协议

2.  编剧 Screenwriter     [Gemini 2]    一次性,项目创建后               ✅ 保留
                          文学语言 → 视觉剧本(分格文字脚本 + 旁白 + 对白 + 动作)
                          国产 LLM:DeepSeek V3(主)/ Qwen-Max(备)

3.  画风定调员 v2 ⭐ 升级  [无对应]      一次性,编剧后用户选风格          ⭐ 浑晶专属
                          v3 升级:多模态视觉 DNA 提取
                          (详细设计见下方"Agent #3 v2"小节)

4.  角色锚定员 ⭐ v2 加    [无对应]      一次性,画风选定后              ⭐ 浑晶专属
                          v3 升级:描述符从 8-12 句 → 20-30 句(含 character_visuals)
                          (详细设计见下方"Agent #4"小节)

5.  素材库抽取员 ⭐ v3 新   [无对应]      一次性,与编剧并行 / 之前         ⭐ 浑晶专属
                          扩 extract_service 抽取维度:character_visuals /
                          scenes / props 三类视觉素材
                          (详细设计见下方"Agent #5"小节)

—.  世界观 / 设定          [Gemini 3]    —                                ❌ 砍 —
                          **浑晶独家优势**:已有完整角色 + 关系 + 事件图谱
                          v3 进一步:加 character_visuals / scenes / props 5 类素材
                          这是与 Gemini 通用方案最大差异点

—.  提示词翻译              [Gemini 5]    —                                ❌ 与导演合并

6.  导演 / 分镜            [Gemini 4+5]  每格 1 次                        ✅ 保留(合并)
                          v3 改造:prompt 头部除塞角色描述符,加 scene / props 字段;
                          目标 200-400 字详细 prompt(v2 实测 60 字"不出彩")
                          合并理由:agent 链路越短失败率指数下降

7.  图像生成 Illustrator   [Gemini 6]    每格 1 次                        ✅ 保留(改造)
                          **v2 锁定**:字节 Doubao Seedream 4.0(原 3.0 deprecated)
                          **v2 改造**:接 4 层一致性方案(§4)
                            L1 用 20-30 句描述符立绘卡 / L3 ref image / L4 同 seed

8.  局部重绘 Inpainter      [Gemini 7]    用户触发                        ✅ 保留
                          默认关闭,用户点"这格不对"主动触发
                          候选 API:Doubao Seedream Edit / 通义万相 inpaint

9.  视觉质检 QA Reviewer    [Gemini 8]    每页首格 + 角色出场首格         ✅ 保留(关键帧)
                          **v2 锁定**:阿里 Qwen-VL Max(D.8 横评通过)
                          自检:"剧本要求 N 个人,图里是不是 N 个?主角穿对衣服了吗?"
                          不全帧扫(关键帧策略覆盖 ~22% 的格子,省 78% 成本)

10. 排版嵌字 Typesetter    [Gemini 9]    每页 1 次                        ✅ 保留(本地)
                          Python PIL 本地处理(无 LLM 成本)
                          对话气泡形状库 + 拟声词字体库
                          从编剧 agent 输出取台词,贴到图片留白区域
```

#### Agent #3 v2 — 画风定调员(v2 多模态升级,2026-05-12 用户拍板)

**作用**:让用户对最终漫画风格有归属感 + 锁定整本一致画风(避免后续每格画风漂移)

**v2 触发**:漫画项目创建后,编剧 agent 完成剧本草稿后,**先让用户上传 3 张参考图**,
                  Qwen-VL 视觉 DNA 提取 → DeepSeek 综合 → 详细 prompt → 出 5 张定调图

**v2 vs v1 的根本差异**:
- v1:LLM(DeepSeek)纯文字推 5 风格(用户实测"不出彩",原因是 LLM 无视觉锚)
- v2:**Qwen-VL 多模态分析用户上传的 3 张参考图** → 提取视觉 DNA(笔触/上色/光影/比例)
       → DeepSeek 综合视觉 DNA + 文本氛围 → 输出 **200-400 字详细 prompt** → 调 Seedream

**v2 完整流程**:
1. **用户上传 3 张参考图**(自己喜欢的画风 — 截网漫页 / 同人圈作品 / 喜欢的国漫页面);
   失败兜底:不到 3 张 / 都失败时,fallback v1 流程(LLM 纯文字推 5 风格)
2. **Qwen-VL Max 多模态分析**:并行调 3 次,每次 1 张图 → 提取视觉特征 JSON:
   - 笔触(厚涂 / 赛璐璐 / 水彩 / 扁平)
   - 上色风格(高饱和 / 莫兰迪 / 暗色调)
   - 线稿(清晰勾线 / 无线 / 朦胧)
   - 角色比例(日漫大眼 / Q版 / 写实)
   - 光影逻辑(强对比 / 扁平 / 氛围光)
   - 构图习惯(中近景 / 远景)
3. **DeepSeek 综合视觉 DNA + 剧本氛围/题材** → 输出**单一详细画风 prompt**(200-400 字)
4. **Seedream 用该详细 prompt 出 5 张细微差异定调图**(同一画风,不同场景/光照/构图)
5. 推送到前端 → 用户 5 选 1
6. 选定 → 存 `style_tag` + `style_anchor_image_url` + `style_visual_dna_json` +
   `style_detailed_prompt`(整本漫画后续所有格生成都贴这个详细 prompt 头部)

**v2 关键产物 → 数据库**:
- `style_reference_image_urls_json`:用户上传的 3 张参考图 URL(OSS / CDN)
- `style_visual_dna_json`:Qwen-VL 提取的视觉 DNA JSON(笔触/上色/光影等)
- `style_detailed_prompt`:DeepSeek 综合后的 200-400 字详细 prompt
- `style_anchor_image_url`:用户选定那张样张
- `style_tag`:简短中文 tag(展示用,如"国漫工笔半厚涂")

**LLM prompt 铁律**(写入 `prompts/style_director_v2.md`):
- ✅ **必须是 2D / 2.5D**:国漫 / 二次元 / 日漫 / 卡通 / 水墨 / 水彩 / 厚涂 / 赛璐璐... 皆可
- ✅ 输出 prompt 必须 **200 字以上 + 含光影 + 含笔触 + 含构图**(D.8 横评教训:60 字 prompt
   出图"不出彩",Seedream 4.0 对长 prompt 响应明显更好)
- ❌ 不输出写实摄影风 / 3D 渲染风 — 加 negative cue("禁止真人写实摄影,禁止 3D 渲染")
- ✅ 综合视觉 DNA 时,**用户上传图是最高优先级**(用户选过的画风不能被 LLM "纠正")

**用户控制点**:
- 5 张样张可点"重生成此张"(每次 ¥0.20)
- 或点"重新上传参考图"(把 Qwen-VL 分析 + Seedream 调用全部重跑)
- 选定后**不可改**(改 = 重启整本生成,因为画风影响所有格 prompt)

**成本**:
- Qwen-VL Max 视觉 DNA 提取:3 张图 × ~¥0.015/张 = ¥0.045
- DeepSeek 综合:1 次 × ~¥0.01 = ¥0.01
- Seedream 出 5 张:5 张 × ¥0.20 = ¥1.00
- **合计:~¥1.05**(每漫画项目一次性,v1 是 ¥1.00,v2 +¥0.05)

**产品心理价值**:用户上传的 3 张图 = 用户参与感 + 视觉锚;用户更难抱怨"AI 出图不对"
(因为视觉 DNA 是用户上传图的提取,用户选过的不能怪 AI)

---

#### Agent #4 — 角色锚定员(v2 加,v3 描述符大幅扩充)

**作用**:为整本漫画的主角生成"身份证级别"立绘卡 + LLM 描述符,后续每格生成时复用,保证角色形象前后一致

**触发**:画风定调员完成后 + 素材库抽取员完成后,导演 agent 之前

**输入**:剧本 + 图谱 + 用户选定的 `style_detailed_prompt` + **素材库 `character_visuals` 表**

**v3 流程**(原 v2 流程的描述符从 8-12 句扩到 20-30 句):
1. LLM 按图谱出场次数排序,选 **前 5-8 个主角**
2. 对每个主角:
   - **直接读 `character_visuals` 表的 LLM 抽取结果**(面部 / 体态 / 穿搭已由 Agent #5 抽好)
   - LLM 出"身份证级超详细描述符" 20-30 句中文,结构:
     ```
     [年龄段 + 性别 + 整体气质]
     [面部:眼型/瞳色/眉形/鼻型/嘴型/脸型/肤色/特殊标记]
     [发型:长度/颜色/发质/常梳法/刘海]
     [体态:身高范围/体型/姿态/标志性动作]
     [穿搭:常服/标志性配饰/常用色调/标志道具]
     [性格关键词:1-2 句的灵魂特质]
     ```
     例:`28 岁男人,沉稳内敛气质;丹凤眼,翡翠绿瞳,剑眉,挺鼻,薄唇,鹅蛋脸,
     冷白皮,左眉角有刀疤;深棕色短发,自然向后梳,无刘海,发质硬挺;身高 180,
     体型修长挺拔,常背手而立;常穿藏蓝长袍,玉佩腰间垂,银鞘剑斜佩;沉稳隐忍,
     话少眼深。`
   - 用 `[style_detailed_prompt 200-400字] + [角色描述符 20-30句] + 正面立绘构图` 调 Seedream
   - 存到 `character_cards` 表(立绘 URL + descriptor + style_tag + character_id)
3. 用户**可选**审核界面 — 看 5-8 张立绘 → 不满意点"重生成";或者**点角色对焦**对面部/体态/穿搭做细调(走 character_focus 机制,§7 列举的扩展之一)
4. 用户确认 → 整本漫画**所有格**生成时由这些立绘卡 + 描述符提供角色"身份"

**v2 → v3 核心差异**:
- v2 描述符 8-12 句,大致对得上;v3 20-30 句**含面部细节 + 体态 + 穿搭具体**,角色一致性预期 +5-10%
- 描述符不再 LLM 凭想象写,而是**读 character_visuals 表的预抽取产物**,稳定性更高
- 用户可走 character_focus 流程精修任何字段(已有机制复用)

**核心约束**:
- descriptor **生成后不可改**(改了等于换角色,前后页就不一致)
- 立绘卡 URL 可重生成,但 descriptor 文字内容不动(保证后续 prompt 描述符稳定)

**成本**:5-8 张 × ¥0.20 = **¥1.00 - ¥1.60**(每漫画项目一次性,平均 ¥1.20 算 6 主角)

**与图像生成 agent 的关系**:角色锚定员产物(20-30 句 descriptor + 立绘卡 URL)= 图像生成 agent 每格调用时的输入参考

---

#### Agent #5 — 素材库抽取员(v3 新增 ⭐,用户拍板 2026-05-12)

**作用**:扩 extract_service 抽取维度 — 从 "抽角色 + 关系 + 事件" 到 "抽角色 + 关系 + 事件 +
**角色视觉细节** + **场景元素** + **道具**",让漫画态的 agent 链有丰富的视觉素材可用

**触发**:漫画态项目导入文本后,与编剧 agent **并行启动**(2 类工作流不互相阻塞)

**输入**:文本 chunk(从 upload_service 拉的原文 chunks)+ 图谱(已抽出的 character 表)

**v3 抽取的 3 类新素材**:

##### 1. `character_visuals`(角色视觉细节,1-to-1 关联 character)

LLM 从原文扫角色视觉描写,抽 7 类字段:
- 面部:眼型 / 瞳色 / 眉形 / 鼻型 / 嘴型 / 脸型 / 肤色 / 特殊标记
- 发型:长度 / 颜色 / 发质 / 常梳法 / 刘海
- 体态:身高范围 / 体型 / 姿态 / 标志动作
- 常服:常穿衣物 + 颜色 + 风格
- 标志配饰:发饰 / 项链 / 玉佩 / 配剑 / 等
- 标志道具:角色常带 / 用 / 是其象征的道具
- 灵魂特质:1-2 句体现内核

**LLM 兜底约束**:原文未明说的字段 → 字段为 `(原文未明)`(不让 LLM 凭空创造),由用户走
**character_focus 机制**补字段(对焦扩展到视觉细节字段)

##### 2. `scenes`(场景元素,新表)

LLM 从原文扫场景描写,抽:
- 名称(用户能识别的场景名:"林家花园" / "客栈" / "皇宫大殿")
- 地理(室内 / 室外 / 山区 / 河边 / ...)
- 时代(古代/现代/未来/未指明)
- 建筑风格(中式园林 / 江南水乡 / 西式哥特 / 极简现代 / ...)
- 光照氛围(白天/夜晚/黄昏/逆光/...)
- 季节(春/夏/秋/冬/未指明)
- 关键陈设(原文提到的家具/景物 5-10 词)

##### 3. `props`(道具,新表)

LLM 从原文扫道具,抽:
- 名称(青铜剑 / 玉佩 / 信物 / 帕子 / ...)
- 类型(武器/服饰/书籍/家具/信物/其他)
- 拥有者(关联到 character.id,可空)
- 视觉描述(用于绘图的细节:"红玉镶嵌,缠藤纹"3-5 词)
- 故事意义(可空;是不是关键情节道具)

**流程**:
1. 与编剧 agent 并行,从 upload_service 拉文本 chunks
2. 对每 chunk 调 DeepSeek:并行抽 `character_visuals` 补字段 + `scenes` 新列表 + `props` 新列表
3. 合并去重(场景同名合并 / 道具同名合并)
4. 落 db 三表
5. 推送到前端"素材库面板"(漫画态专属 view)

**前端展示**:漫画态项目独有"素材库"tab — 列出 character_visuals / scenes / props 三栏,
用户可点任一字段走 character_focus 流程精修

**成本**:LLM 抽取一次性 ~¥1.5(全本视觉素材抽取一次,可重抽 = 额外 ¥1.5)

**对漫画态的核心价值**:
- Agent #4 角色锚定员产物质量 +5-10%(描述符更具体)
- Agent #6 导演 agent 每格 prompt 能塞场景 + 道具(漫画分镜质感上一档)
- 漫画态 3D 图谱可加 scene / prop 节点(全本素材库可视化)

**对中间态的副作用收益**:中间态的 simulation 续写也受益(导演 agent 写新场景时拉素材库)
**前提是**:中间态默认**不强制**抽这 3 类(YAGNI),漫画态项目导入时才触发。中间态可在
"配置"中开"启用素材库抽取"(可选项,免费档不开)。

### 3.3 工作流(v3 修订:加入用户上传参考图 + 素材库抽取员)

```
[输入源]
├─ 浑晶产物(从「我的剧情线」选 1+ simulation,prerequisite:state='done')
└─ 外部文本(用户拖拽 .txt / .epub / .docx,沿用 2.A upload_service)

       ↓

[总控 Agent]
   切分章节 / 场景 → 状态机:queued → scripting/extracting_visuals →
                              style_uploading → style_analyzing →
                              style_voting → character_anchoring →
                              designing → generating → composing → done

       ↓             ↓ (并行)
       │             │
[编剧 Agent]   ╔═══════════════════════════════════════════════════════╗
   每场景     ║ ⭐ [素材库抽取员 Agent]  v3 新增                        ║
   → 分格脚本 ║   ① 与编剧并行,从 upload_service 拉文本 chunks         ║
   (旁白 /    ║   ② LLM 抽 3 类视觉素材(并行)                           ║
   对白 /     ║      character_visuals(面部/体态/穿搭/标志道具)       ║
   动作)      ║      scenes(名称/地理/时代/建筑/光照/季节)              ║
       │     ║      props(名称/类型/拥有者/视觉描述)                   ║
       │     ║   ③ 合并去重 → 落 db 三表                               ║
       │     ║   ④ 推送前端"素材库面板"(用户可对焦补字段)             ║
       │     ╚═══════════════════════════════════════════════════════╝
       │             │
       └─────┬───────┘
             ↓

╔═══════════════════════════════════════════════════════════════════╗
║ ⭐ [画风定调员 v2 Agent]  v3 升级 — 多模态视觉 DNA 提取             ║
║   ① **用户上传 3 张参考图**(自己喜欢的画风,截网漫页 / 同人圈)    ║
║   ② Qwen-VL Max 并行分析 3 张图 → 视觉 DNA JSON                     ║
║      (笔触/上色/光影/比例/线稿/构图)                                ║
║   ③ DeepSeek 综合"视觉 DNA + 剧本氛围/题材" → 200-400 字详细 prompt║
║   ④ Seedream 用详细 prompt 出 5 张细微差异定调图                     ║
║   ⑤ 用户 5 选 1 → 存 style_detailed_prompt + style_anchor_image_url║
║                                                                     ║
║   失败兜底:不到 3 张参考图 → fallback v1 流程(LLM 纯文字推 5 风格) ║
╚═══════════════════════════════════════════════════════════════════╝

                      ↓

╔═══════════════════════════════════════════════════════════════════╗
║ ⭐ [角色锚定员 Agent]  v3 描述符大幅扩充                            ║
║   ① 读 character_visuals 表(Agent #5 抽好的面部/体态/穿搭)         ║
║   ② 每主角 LLM 出 20-30 句"身份证级"描述符(原 v2 是 8-12 句)        ║
║   ③ Seedream 用 [style_detailed_prompt + 描述符] 出立绘卡           ║
║   ④ 存 character_cards 表(20-30 句 descriptor + 立绘 URL)          ║
║   ⑤ 用户可选审核 / 重生成 / 走 character_focus 精修字段             ║
╚═══════════════════════════════════════════════════════════════════╝

                      ↓

[导演 Agent](合并分镜 + 提示词)
   v3 改造:每格 prompt(200-400 字)=
     [style_detailed_prompt] + [本格角色 20-30 句描述符] +
     [本格场景 from scenes 表] + [本格涉及道具 from props 表] +
     [镜头语言(特写/远景/俯视/...)] + [negative cue]

       ↓

[图像生成 Agent](串行,每格一次 API call)
   v2 4 层一致性方案 集成:
     L1 立绘卡:从 character_cards 拉 20-30 句 descriptor + ref
     L2 描述符:导演 agent prompt 已塞
     L3 ref image:调 Seedream 时附立绘卡 URL(若 4.0 支持 image_url)
     L4 seed:整本同 generation_seed

       ↓                  ↓
       │           [失败 / 质检不过]
       │                  │
       │           [重绘 Agent](局部修,默认关 → 用户点"这格不对"触发)
       │                  ↓
       └────────→ [质检 Agent](Qwen-VL Max,每页首格 + 角色出场首格)

       ↓

[排版 Agent](Python PIL 本地)
   贴对话气泡 + 拟声词 → 单页 PNG / 多页 PDF

       ↓

[产物]
   多页漫画(PDF / 单 PNG 序列)+ 原始素材(对话气泡可后期改)
   导出到「我的漫画」(独立列表,不混入「我的剧情线」)
```

---

## 4. 角色一致性 4 层方案(v2 新增章节)

### 4.1 问题陈述

**世界难题**:漫画角色跨页 / 跨章节形象一致 — Disney/Pixar 用 LoRA 训练 + 美工监修可达 95%+,但 AI 批发生成的"主角前面几页同一个样子,后面突然变成另一人"是 AIGC 漫画失败的头号原因。

**浑晶定位**:批发型 2D 网络漫画,**不做 LoRA 训练**(自建 GPU + 单角色训练 ¥50+,反 ROI)→ 必须靠 prompt 工程 + vendor 原生能力 + 数据持久化的组合拳。

### 4.2 4 层方案设计(v3:L1 描述符 8-12 句 → 20-30 句;Sprint 0 实测后 L2 上调)

| 层 | 名称 | 实现位置 | 触发频率 | 增量成本 | 预期效果 |
|---|---|---|---|---|---|
| L1 | **角色立绘卡** | Agent #4 + `character_cards` 表 | 一次性 / 漫画 | ¥1.20/作品 | 视觉身份证;**v3:描述符 20-30 句**(读 character_visuals 表) |
| L2 | **Prompt 描述符锁定** | 导演 agent 每格 prompt 强制塞 20-30 句 + scene + props | 每格 | 0 | **单层 90-95%(Sprint 0 实测 95/100)**;原 v2 假设 80-85%,实测远超 |
| L3 | **Ref Image** | 图像生成 agent 调 Seedream 时附立绘 URL | 每格 | 0 | Sprint 0 实测 **vendor 没明确支持但 L2 已饱和**;锦上添花,不阻塞 |
| L4 | **同 Seed 锁定** | `comic_projects.generation_seed`,整本沿用 | 每格 | 0 | 收敛 vendor 内部随机性 |

**v3 + Sprint 0 实测后的累积效果**:
- 单 L2 → **90-95% 一致性**(Sprint 0 实测,Qwen-VL 评 95/100 同一人,远超 v2 假设)
- L2 + L4 → 92-95% 一致性(seed 锚定补 vendor 随机性)
- L2 + L3 + L4 → **95%+ 一致性**(L3 实测对 L2 已饱和场景无明显增量,但 vendor 升级后保留可能性)

**v2 → v3 → Sprint 0 实测演进**:整体一致性预期从 v2 85% → v3 92% → **Sprint 0 实测 95%**

### 4.3 关键技术不确定 — D.9 启动前必验

**L3 依赖**:Doubao Seedream 3.0 t2i 是否支持 `reference_image` / `image_url` 参数 — D.9 第 0 个 sprint 必须实测确认。

**实测脚本**(D.9 待写):`scripts/d9_ref_image_test.py`
- 调 Seedream 3.0 t2i `images.generate(prompt=..., image_url=立绘卡URL)`
- 若 API 接受参数 + 返回图相似度 > 70% → L3 可用
- 若 API 拒参数 / 返回图无关 → L3 fallback,自动降级到 L2+L4 组合(降到 75-80% 一致性)

**fallback 兜底**:即使 L3 不可用,L1+L2+L4 仍能交付批发型漫画(网络漫画用户对 80% 一致性容忍度已经够)

### 4.4 不做 LoRA 训练的理由(YAGNI 锁定)

| 方案 | 一致性 | 单角色成本 | 工程量 | D.9 是否做 |
|---|---|---|---|---|
| LoRA 训练 | 95%+ | 训练 ¥50 + 存储 + 推理 | 自建 GPU 集群,2+ sprint | ❌ |
| **本 ADR 的 L1-L4** | 80-95%(看 L3) | ¥1.20 / 作品 | D.9 内 1 sprint | ✅ |
| 纯 prompt | 60-70% | 0 | 0 | ❌(失败率太高) |

**L1-L4 是性价比最高的"轻量"方案** — 在不自建 GPU 的前提下,把网络漫画的角色一致性推到批发可用水准。

### 4.5 UI 预期管理(用户教育)

漫画阅读器顶部 chip 显示:**"AI 协作创作 · 角色一致性约 95%"**(Sprint 0 实测后上调,v2 85% / v3 设计 92% / Sprint 0 实测 95)

- 对齐 doc 3 透明创作意志("AI 协作是公开姿态")
- 用户提前知道"不是 Disney 级",售后争议低
- 不满意可点单格"重绘",触发 Agent #8 局部重绘(每次 ¥0.20)

### 4.6 AI 对焦机制扩展(v3 新增 — 用户拍板)

C 阶段已有的 character_focus 机制(角色对焦)从只对 character 字段扩展到对 5 类素材字段:
- `characters.name / identity / personality / quotes / no_go`(C 阶段原版)
- **`character_visuals.*`**(v3:面部 / 体态 / 穿搭等任一字段可对焦)
- **`scenes.*`**(v3:场景任一字段可对焦)
- **`props.*`**(v3:道具任一字段可对焦)

**前端 character_focus UI 扩展**:从只对角色,扩展到点 character_visuals / scenes /
props 表的任一行也能进入对焦流程(用户对 LLM 抽取的字段做赞同/反对/补充)

**为什么这是关键产品价值**:用户对自己作品的画面细节有强 ownership 需求;素材库被抽出
来时不一定准(LLM 漏抽 + 抽错),AI 对焦让用户在 5 类素材层面都能"过一遍 AI 草稿"

---

## 5. 隔离原则(用户明确要求)

### 5.1 单向链路

```
前 3 态(初始 / 中间 / 末尾) ──→ 产物 ──→ 可被漫画态选作输入源
                                          │
                                          ↓
                                       漫画态项目

漫画态 ╳ 跳回 前 3 态(无入口)
前 3 态项目页 ╳ 暴露漫画态入口(无按钮 / 无 chip)
```

### 5.2 落地细节

| 隔离点 | 实现 |
|---|---|
| **DashboardView 时间轴** | 漫画态产物 **不出现在「我的剧情线」**(独立「我的漫画」入口)|
| **ProjectView** | 漫画态项目走独立 view(`/comics/:id`),不复用 ProjectView |
| **SimulationDock** | 漫画态项目无 simulation,无此 dock |
| **NewProjectModal** | 漫画态创建走独立流程(选输入源 → 选风格 → 启动流水线),不复用 NewProjectModal |
| **前 3 态项目页** | 不显示"导出为漫画"按钮(用户得主动新建漫画态项目并选材料库)|
| **「我的剧情线」卡片** | 不显示"用此产物生成漫画"快捷按钮(只能从漫画态项目反向选)|

**这条隔离是产品级架构铁律**,未来开发漫画态时必须严格遵守。

---

## 6. 数据库新增表(v3 修订:character_visuals / scenes / props + comic_projects 加 3 字段)

```sql
-- migration 021(漫画态启动时建)
CREATE TABLE IF NOT EXISTS comic_projects (
    id                          TEXT PRIMARY KEY,
    user_id                     TEXT NOT NULL REFERENCES users(id),
    name                        TEXT NOT NULL,
    -- 输入源:JSON {"type": "internal" | "external",
    --              "simulation_ids": [...]  -- type=internal 时
    --              "upload_ids": [...]      -- type=external 时}
    source_json                 TEXT NOT NULL,

    -- v2 新增:画风定调员 Agent #3 产物
    -- v3 升级:加多模态视觉 DNA 提取 + 详细 prompt 三字段
    style_tag                   TEXT,                      -- 简短中文 tag(展示用)
    style_anchor_image_url      TEXT,                      -- 用户选定那张样张(整本视觉锚点)
    style_candidates_json       TEXT,                      -- 5 张候选样张全量(审计)
    style_reference_image_urls_json   TEXT,                -- ⭐ v3:用户上传的 3 张参考图 URL
    style_visual_dna_json             TEXT,                -- ⭐ v3:Qwen-VL 提取的视觉 DNA JSON
    style_detailed_prompt             TEXT,                -- ⭐ v3:DeepSeek 综合后 200-400 字详细 prompt

    -- v2 新增:角色一致性 L4 — 同作品同 seed 锁定
    generation_seed             INTEGER,

    -- 状态机:queued → scripting/extracting_visuals → style_uploading →
    --        style_analyzing → style_voting → character_anchoring →
    --        designing → generating → composing → done | failed
    -- v3 修订:加 extracting_visuals / style_uploading / style_analyzing 三个新状态
    state                       TEXT NOT NULL,

    cost_yuan                   REAL NOT NULL DEFAULT 0,
    error_message               TEXT,
    created_at                  TEXT NOT NULL,
    completed_at                TEXT
);

-- v2 新增:角色立绘卡(角色一致性 L1 持久化)— 每漫画 N 张主角立绘
-- v3 修订:descriptor 从 8-12 句 → 20-30 句(读 character_visuals 表)
CREATE TABLE IF NOT EXISTS character_cards (
    id                  TEXT PRIMARY KEY,
    comic_id            TEXT NOT NULL REFERENCES comic_projects(id) ON DELETE CASCADE,
    character_id        TEXT NOT NULL,
    character_name      TEXT NOT NULL,
    -- LLM 生成的"身份证级超详细描述符"
    -- v3:20-30 句中文,含面部 + 体态 + 穿搭 + 标志道具 + 性格关键词
    -- 由 Agent #4 角色锚定员读 character_visuals 表生成,一经生成不可改
    descriptor          TEXT NOT NULL,
    card_image_url      TEXT NOT NULL,
    regenerated_count   INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_character_cards_comic ON character_cards(comic_id);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_character_cards_comic_char
    ON character_cards(comic_id, character_id);

-- ⭐ v3 新增:角色视觉细节(1-to-1 关联 character)— 素材库抽取员 Agent #5 产物
-- 漫画态项目导入文本后,Agent #5 与编剧并行,扫原文抽取角色面部/体态/穿搭/标志道具
-- 字段未明时填 '(原文未明)';用户走 character_focus 流程补字段
CREATE TABLE IF NOT EXISTS character_visuals (
    id                  TEXT PRIMARY KEY,
    comic_id            TEXT NOT NULL REFERENCES comic_projects(id) ON DELETE CASCADE,
    character_id        TEXT NOT NULL,    -- 关联 characters.id(同项目内)

    -- 面部(JSON)— {eye_shape, eye_color, eyebrow, nose, mouth, face_shape, skin_tone, marks}
    face_json           TEXT NOT NULL DEFAULT '{}',
    -- 发型(JSON)— {length, color, texture, hairstyle, bangs}
    hair_json           TEXT NOT NULL DEFAULT '{}',
    -- 体态(JSON)— {height_range, body_type, posture, signature_action}
    body_json           TEXT NOT NULL DEFAULT '{}',
    -- 常服(JSON)— {garment, color, style}
    outfit_json         TEXT NOT NULL DEFAULT '{}',
    -- 标志配饰(JSON 数组)— [{name, position, color, ...}]
    accessories_json    TEXT NOT NULL DEFAULT '[]',
    -- 标志道具(JSON 数组)— [{name, description, ...}]
    signature_props_json TEXT NOT NULL DEFAULT '[]',
    -- 灵魂特质(1-2 句)
    soul_traits         TEXT,

    -- 用户对焦修改次数(走 character_focus 时累计)
    focused_count       INTEGER NOT NULL DEFAULT 0,

    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_character_visuals_comic ON character_visuals(comic_id);
CREATE UNIQUE INDEX IF NOT EXISTS uniq_character_visuals_comic_char
    ON character_visuals(comic_id, character_id);

-- ⭐ v3 新增:场景表 — 素材库抽取员 Agent #5 产物
CREATE TABLE IF NOT EXISTS scenes (
    id                  TEXT PRIMARY KEY,
    comic_id            TEXT NOT NULL REFERENCES comic_projects(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,                  -- "林家花园" / "客栈" / "皇宫大殿"
    location_type       TEXT,                            -- "室内" / "室外" / "山区" / "河边"
    era                 TEXT,                            -- "古代" / "现代" / "未来" / "未指明"
    architecture_style  TEXT,                            -- "中式园林" / "江南水乡" / "西式哥特"
    lighting            TEXT,                            -- "白天" / "夜晚" / "黄昏" / "逆光"
    season              TEXT,                            -- "春" / "夏" / "秋" / "冬" / "未指明"
    -- 关键陈设(JSON 数组,3-10 词)
    key_props_json      TEXT NOT NULL DEFAULT '[]',
    focused_count       INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_scenes_comic ON scenes(comic_id);
CREATE INDEX IF NOT EXISTS idx_scenes_name ON scenes(comic_id, name);

-- ⭐ v3 新增:道具表 — 素材库抽取员 Agent #5 产物
CREATE TABLE IF NOT EXISTS props (
    id                  TEXT PRIMARY KEY,
    comic_id            TEXT NOT NULL REFERENCES comic_projects(id) ON DELETE CASCADE,
    name                TEXT NOT NULL,                  -- "青铜剑" / "玉佩" / "信物" / "帕子"
    prop_type           TEXT,                            -- "武器" / "服饰" / "书籍" / "家具" / "信物" / "其他"
    owner_character_id  TEXT,                            -- 关联 characters.id(可空)
    visual_description  TEXT,                            -- 用于绘图的细节(3-5 词)
    story_significance  TEXT,                            -- 是否关键情节道具(可空)
    focused_count       INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_props_comic ON props(comic_id);
CREATE INDEX IF NOT EXISTS idx_props_owner ON props(comic_id, owner_character_id);

-- migration 022
CREATE TABLE IF NOT EXISTS comic_pages (
    id              TEXT PRIMARY KEY,
    comic_id        TEXT NOT NULL REFERENCES comic_projects(id) ON DELETE CASCADE,
    page_index      INTEGER NOT NULL,
    -- 单页 JSON:[{panel_index, image_url, dialogues, narrator, sfx, ...}, ...]
    panels_json     TEXT NOT NULL,
    -- 排版后整页 PNG(对话气泡 + 字体已贴)
    composed_url    TEXT,
    state           TEXT NOT NULL,  -- queued / done / failed
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_comic_pages_comic ON comic_pages(comic_id, page_index);
```

**注**:实际 schema 未来开发时(D.9)再 finalize,这里是 v3 修订后的草稿。

**v2 → v3 关键变化**:
- `comic_projects` v2 加了 `style_tag` / `style_anchor_image_url` / `style_candidates_json` / `generation_seed`
- **v3 再加** `style_reference_image_urls_json` / `style_visual_dna_json` / `style_detailed_prompt` 共 3 字段
- 状态机 v2 加 `style_voting` / `character_anchoring`,**v3 再加** `extracting_visuals` / `style_uploading` / `style_analyzing` 3 个新状态
- `character_cards.descriptor` v2 是 8-12 句,**v3 是 20-30 句**(读 character_visuals 表)
- **v3 新增 3 表**:`character_visuals` / `scenes` / `props`(素材库抽取员 Agent #5 产物)

---

## 7. 模型路由(v2 修订:D.8 主路由已锁定)

### 7.1 选型决策(v3:含 v2 + v3 各 Agent 用 vendor)

**当前状态**(2026-05-12 v2 + v3 双重修订):D.8 横评通过,主路由正式定型。

| Agent / 角色 | 主路由(已锁定)| 备份(D.9 部署前评估) | 备注 |
|---|---|---|---|
| **画风定调员 视觉 DNA 提取**(v3 新)| **阿里 Qwen-VL Max** | GLM-4V | 分析用户上传 3 张参考图(笔触/上色/光影 等)|
| **素材库抽取员**(v3 新)| **DeepSeek V3** | Qwen-Max | 抽 character_visuals / scenes / props,文本→JSON 任务,DeepSeek 性价比最高 |
| 视觉质检(多模态)| **阿里 Qwen-VL Max** | GLM-4V / 文心多模态 | D.8 横评通过(Q1/Q2/Q3 全命中) |
| 图像生成 | **字节 Doubao Seedream 4.0**(t2i)| 通义万相 / 可灵 | D.8 横评后用户拍板试 3.0 → 实测 3.0 deprecated → 退 4.0;v3 用户反馈"prompt 工程化升级后才出彩" |
| 局部重绘 | Doubao Seedream Edit(待 D.9 接入)| 通义万相 inpaint | 与生图同 vendor → 风格 / 一致性更稳 |
| 编剧 / 导演 / 画风综合 / 角色锚定 LLM | DeepSeek V3 | Qwen-Max | 与浑晶整体 LLM 路由完全一致 |

**v1 → v2 → v3 关键变化**:
- 图像生成:候选清单 → **锁定 Seedream 3.0** → **退到 4.0**(3.0 deprecated)
- 视觉质检:候选清单 → **锁定 Qwen-VL Max**
- **v3 加 Qwen-VL Max 第 2 角色**:画风定调员的视觉 DNA 提取(同一 vendor,共享 key,无新增凭据)
- 模型 ID:`doubao-seedream-4-0-250828` / `qwen-vl-max-latest`(可在 `.env` 改)

**离线横评工具**(只用于内部对比,不进生产):GPT-4V / Claude 3.5 Sonnet / Midjourney — 仅作为"国产水准参考线",数据走 mock 不真出境。

### 7.2 备案影响

漫画生成属于 **AIGC 图像生成**,可能需要在原 AIGC 算法备案基础上**追加视觉模块备案**。具体边界待与法务顾问 / 网信办窗口确认。

---

## 8. 成本与定价(v3 修订)

### 8.1 单本漫画成本估算

按"30 页 × 6 格/页 = 180 格" + 6 主角 + 10% 重绘触发率 + 30K 字原文素材抽取 计:

| 项 | 计算 | 成本(¥) |
|---|---|---|
| **画风定调员 v2** ⭐ v3 升级 | Qwen-VL 3 张视觉 DNA × ¥0.015 + DeepSeek 综合 ¥0.01 + Seedream 5 张 × ¥0.20 | 1.06 |
| **素材库抽取员** ⭐ v3 新 | DeepSeek 全本扫 + 抽 character_visuals/scenes/props | 1.50 |
| **角色锚定员** ⭐ v2 新 | 6 张 × ¥0.20(平均 6 主角)| 1.20 |
| 图像生成(主流程) | 180 张 × ¥0.20(Seedream 4.0)| 36.00 |
| 视觉质检(关键帧:每页首格 30 + 角色出场首格 ~10)| ~40 题 × ¥0.004(Qwen-VL token-based) | 0.16 |
| 局部重绘(假设 10% 触发率,4 层一致性已降失败)| 18 张 × ¥0.20 | 3.60 |
| 编剧 + 导演 LLM(导演 prompt 加长到 200-400 字,token 略增)| 30 页 × 4 次 × ¥0.015 | 1.80 |
| 排版 + 字体 | 本地 PIL | 0 |
| 重试乘数(1.2× — 4 层一致性 + 素材库丰富 prompt 让重试率降)| (36 + 0.16 + 3.6 + 1.8)× 0.2 | 8.31 |
| **单本总成本** | | **~ ¥53.6** |

**v2 → v3 修订说明**:
1. **画风定调员**:¥1.00 → ¥1.06(加 Qwen-VL 视觉 DNA 提取 ¥0.05)
2. **素材库抽取员 v3 新**:+¥1.50(一次性全本视觉素材抽取)
3. **导演 LLM**:¥0.01 → ¥0.015(prompt 加长到 200-400 字,token 翻 ~1.5×)
4. **总成本 ¥51.4 → ¥53.6**(+¥2.2)

### 8.2 定价模型对齐 doc 13

- 268 元超级档 ≈ **5 本/月**(¥53.6 × 5 = ¥268,刚好等于月费 → 满配额下毛利为 0)
- 实际用户跑 30-70% 配额时,平均毛利 +15-25% — 可活
- 算力包 318 元 ≈ 6 本/月加量
- 与 doc 13 "创作者层 268 元主战场"对齐,**满配额毛利空间持续收窄,加购包是真利润源**

### 8.3 中间态副作用收益

素材库抽取员是漫画态专属 agent,但 character_visuals / scenes / props 三表对**中间态续写**
也有价值(导演 agent 写新场景 / 描写主角衣着时可拉素材库)。

**默认策略**:中间态项目**不自动跑**素材库抽取(避免增加 C 阶段免费档负担);
用户可在中间态"配置"里勾选"启用素材库抽取"(付费档可用,免费档 disabled)。

中间态启用后单本一次性成本 +¥1.50(素材库抽取一次,后续续写复用)。

---

## 9. 风险清单(v3 修订)

| 风险 | 等级 | 缓解 |
|---|---|---|
| **国产绘图 API 质量参差** | 🟡 中(v1 是🔴)| D.8 横评 + 锁定 Seedream 4.0(原 3.0 deprecated);v3 加 prompt 工程化(200-400 字)+ 视觉 DNA 锚 |
| **角色一致性世界难题** | 🟢 低(v1🔴/v2🟡)| **4 层方案** + L1 描述符 20-30 句(v3 升级,读 character_visuals)→ 预期 92%+ |
| **画风定调员 LLM 输出 5 张同质化** | 🟡 中(v2🟡)| v3 用户上传 3 张参考图 + Qwen-VL 视觉 DNA 提取**根本解决**(用户视觉锚);失败 fallback v1 |
| **角色立绘卡 vendor 不支持 ref image** | 🟢 低(Sprint 0 实测后)| L2 单层已达 95/100,L3 是否支持都不影响 — Sprint 0 实测确认 |
| **多模态视觉 DNA 提取准度**(v3 新)| 🟡 中 | Qwen-VL Max 能否稳定从 3 张图提取"笔触/上色"等抽象概念,需 D.9 第 0 sprint 实测;失败 fallback v1 流程 |
| **用户上传参考图版权风险**(v3 新)| 🔴 高 | 用户上传图可能是他人作品;**法务需确认是否需单独协议**(用户上传图作 AI 风格分析的合规边界);加 user_intent 声明"图仅用于风格分析,不存档/不训练" |
| **素材库抽取 LLM 漏抽 / 抽错**(v3 新)| 🟡 中 | 字段未明 → 填 `(原文未明)`,不凭空创造;走 character_focus 让用户精修 |
| **10 agent 链路失败率累乘** | 🟡 中 | 每 agent 独立 retry + 总控可断点重启;Agent #5 与编剧并行,不增加串行长度 |
| **生成耗时长(单本 30+ 分钟,10 agent 更长)** | 🟡 中 | SSE 流式进度反馈;素材库与编剧并行降总时长 |
| **审核风险**(漫画涉色 / 政治) | 🟡 中 | 复用 red_flag_filter + Qwen-VL 视觉质检 + 国内合规审核 API |
| **角色一致性失败 → 用户失望** | 🟢 低(v1🟡)| 4 层方案 + 局部重绘 + UI chip 预期管理("92% 一致");v3 用户上传参考图后归属感强,争议门槛高 |

---

## 10. 当前动作(本 ADR 生效)

### 10.1 立即落地(v1 2026-05-11 + v2 2026-05-12)

1. ✅ `CreationModeQuadrant` 第 4 卡:"周期态" → "漫画创作态"(状态保持"开发中")
2. ✅ `NewProjectModal.MODE_META.cycle` 文案更新
3. ✅ `handleClick` 漫画态 toast 文案明确"等公司备案 + 国内 API 路由完成"
4. ✅ 写本 ADR 锁定决策(v1)
5. ✅ 更新项目记忆 v9 → v10 → v11
6. ✅ **D.8 国产 API 路由层 sprint 完工**(2026-05-12)
   - 见 `docs/ADR_D.8_国产API路由层.md`(Adapter Pattern + Protocol 架构)
   - 见 `docs/D.8_vendor_evaluation.md`(5 + 3 用例横评报告)
   - 路由层落地:`backend/app/services/llm_routing/`(protocols / router / pricing /
     adapters/deepseek_text / jimeng_image / qwen_vl_vision)
7. ✅ **本 ADR v2 修订**(2026-05-12):
   - 新增 2 agent(画风定调员 + 角色锚定员)
   - 新增 §4 角色一致性 4 层方案
   - 修订 §6 DB schema(`character_cards` 新表 + `comic_projects` 加 3 字段)
   - 修订 §7 模型路由(主路由从候选清单 → 锁定 Seedream 3.0 + Qwen-VL Max)
   - 修订 §8 成本表(¥32 → ¥51.4 / 本)
   - 修订 §9 风险表(2 风险 🔴→🟡;加 2 v2 新风险)
9. ✅ **D.9 Sprint 0 完工**(2026-05-12,Sprint 0 验证报告 `docs/D.9_sprint0_summary.md`):
   - Test 1 ref image:L2 单层 95/100 远超预期 → 一致性预期上调 92% → 95%
   - Test 2 Qwen-VL DNA:6 字段 100% 一致 → 画风定调员 v2 完美支持
   - Test 3 素材库抽取:平均 87.8% 填空率 → Agent #5 设计可用
   - 工程量下调 14-16 → 13-15 sprint

10. ✅ **D.9 Sprint 1 骨架完工**(2026-05-12):
    - Migration 024-028(5 表):comic_projects / character_cards / character_visuals / scenes / props / comic_pages
    - 3 个 v3 prompt:style_director_v2 / visual_assets_extractor / character_anchor_v2
    - models/comic.py + comic_service.py 状态机骨架(13 状态 + zombie 检测)

11. ✅ **D.9 Sprint 2.A 完工**(2026-05-12,后端 4 agent 真实实现 + 路由):
    - 新 prompt:prompts/screenwriter.md(Agent #2 漫画分镜 8 铁律)
    - Migration 029:comic_projects 加 script_json
    - 4 model:character_card / character_visual / scene / prop
    - 4 agent 去 stub 实现:
      * `_agent_scripter` — DeepSeek + screenwriter.md → script_json
      * `_agent_visual_assets_extractor` — DeepSeek + visual_assets_extractor.md → 三表
      * `_agent_style_director_v2` — Qwen-VL × 3 + DeepSeek + Seedream × 5
      * `_agent_character_anchor` — DeepSeek + character_anchor_v2.md + Seedream → character_cards
    - schemas/comic.py + routers/comics.py 接 main.py
    - tests/test_comic_service.py 11 新测试全过 + pytest 243 全过

12. ✅ **D.9 Sprint 2.B 完工**(2026-05-12,前端 UI):
    - api/types.ts:Comic 系列类型 + COMIC_STATE_LABEL 中文 + COMIC_USER_DRIVEN_STATES
    - composables/useComic.ts:polling 2s + 5 个 action(create/upload/vote/cancel/remove)
    - 2 个 Dialog:RefImageUploadDialog(粘贴 3 URL + 预览)+ StyleVotingDialog(5 选 1 grid)
    - 2 个 View:ComicProjectView(按 state 推进 UI)+ MyComicsView(漫画列表)
    - router.ts:加 /my-comics + /comics/:id(独立 view,隔离铁律)
    - CreationModeQuadrant:第 4 卡 status 'soon' → 'beta'(内测)
    - DashboardView onModeSelected:cycle → router.push("/my-comics")
    - vue-tsc 0 错(前端不破坏既有)+ pytest 243 全过(后端不动)

8. ✅ **本 ADR v3 修订**(2026-05-12 第二轮,用户实测横评后反馈):
   - 画风定调员升级 v2(用户上传 3 张参考图 → Qwen-VL 视觉 DNA → 详细 200-400 字 prompt)
   - **新增 Agent #5 素材库抽取员**(character_visuals / scenes / props 3 表)
   - 修订 §4 L1 描述符 8-12 句 → 20-30 句;角色一致性预期 85% → 92%
   - 修订 §4.6 AI 对焦机制扩展到 5 类素材(原只 character)
   - 修订 §6 DB schema(加 character_visuals / scenes / props 三表 + comic_projects 再加 3 字段)
   - 修订 §7 模型路由(Qwen-VL 加视觉 DNA 提取角色 + Seedream 3.0 退 4.0)
   - 修订 §8 成本表(¥51.4 → ¥53.6 / 本)
   - 修订 §9 风险表(加 v3 3 项新风险,其中"用户上传参考图版权"🔴 标红需法务确认)

### 10.2 后端 enum 暂不改

后端 `ProjectMode` enum 仍包含 `'cycle'` 字面量(避免大规模 schema 迁移)。漫画态真开发时:

**未来 migration**:
- enum `cycle` → `comic`(或单独表 `comic_projects` 不复用 `projects` 表)
- 后端代码所有 `mode === 'cycle'` 路径检查 → 跟 mode 解耦或重命名

**当前**:任何 mode 检查代码继续用 `'cycle'` 字面量,前端 label 显示"漫画创作态"(语义层 vs 字面量层解耦)。

### 10.3 不开发的部分(等待触发条件)

- 漫画态实际工程(8 agent / 数据库表 / 阅读器 UI / 导出)
- 漫画阅读器 UI
- 漫画导出(PDF / PNG 序列)
- 「我的漫画」列表入口

---

## 11. 触发开发的 4 个前置条件(v2 状态)

漫画态进入实际开发周期,**必须**满足:

- [ ] **公司注册完成 + AIGC 算法备案 + AIGC 服务备案下证**(P0 法务前提)
- [x] **国产绘图 API 路由选型完成** ✅ — D.8 完工 2026-05-12,主路由 Doubao Seedream 3.0
- [x] **国产多模态 API 选型完成** ✅ — D.8 完工 2026-05-12,主路由 Qwen-VL Max
- [ ] **读者层(初始 + 中间 + 末尾)真实用户跑起来 + 商业模型验证**(避免凑齐 4 态的完美主义陷阱)

**剩 2 条未达成**:法务备案 + 真实用户验证。

**2026-05-12 用户拍板修订**:原 ADR 锁"任一未达成不要启动"是针对**上线**,
**编码 sprint 可以并行启动**(产物不上线 = 不违规)。已启动 D.9 Sprint 1 编码,
**严格不进生产**;法务下证 + 真实付费验证齐备后才上线。

**剩余 sprint 数**:**13-15 sprint**(Sprint 0 验证完后下调 1,L3 不必死磕)

**v3 法务备案补充**:用户上传参考图作 AI 风格分析的合规边界 — 需在 AIGC 服务备案外
额外确认是否需独立用户协议条款("我上传的参考图仅用于本次画风分析,不存档不训练")。

---

## 12. 后续修订

本 ADR 是漫画态的**架构愿景文档**,不是冻结的施工蓝图。开发启动前需评审并按当时实际国产 AI 生态(2026 年下半年的 LLM / 绘图 API 格局)更新。

**评审节点**:漫画态实际开发启动前,本文档需重新评审一次 → 标记为 "已更新" 或 "已替换" 状态。

### 修订历史

- **2026-05-11 v1**:初版 — 6 agent 架构 / 角色一致性策略简略提及 ①②③ / 估算成本 ¥32/本 / 4 前置条件全未达成
- **2026-05-12 v2**:D.8 完工后修订(用户拍板)
  - agent:6 → 8(新增画风定调员 + 角色锚定员)
  - 角色一致性:简略 ①②③ → **§4 完整 4 层方案**(L1 立绘卡 + L2 描述符 + L3 ref image + L4 同 seed)
  - 模型路由:候选清单 → 锁定 Doubao Seedream 3.0 + Qwen-VL Max(D.8 横评通过)
  - 成本:¥32 → ¥51.4 / 本(单价上调到 ¥0.20 / 张 + 新 agent 成本 + 重试乘数调整)
  - DB schema:加 `character_cards` 表 + `comic_projects` 加 `style_tag` / `style_anchor_image_url` /
    `style_candidates_json` / `generation_seed` 共 4 字段
  - 前置条件:4 条 → 已达成 2 条(D.8 双锁定)
  - 风险:2 风险 🔴→🟡(横评通过 + 4 层一致性);加 2 v2 新风险(画风同质化 / vendor 不支持 ref image)
- **2026-05-12 v3**:D.8 二轮横评(Seedream 4.0)用户实测反馈后修订
  - agent:8 → 10(画风定调员 v1 → v2 多模态升级 + 新增素材库抽取员 Agent #5)
  - 画风定调员 v2:LLM 纯文字推 → **用户上传 3 张参考图 + Qwen-VL 视觉 DNA + 200-400 字详细 prompt**
  - 素材库抽取员:新增 character_visuals / scenes / props 三表 + AI 对焦扩展到 5 类素材
  - 角色一致性 L1 描述符:8-12 句 → **20-30 句**(读 character_visuals 表);一致性预期 85% → 92%
  - 模型路由:Seedream 3.0 → **4.0**(原 3.0 deprecated 验证失败);Qwen-VL 加视觉 DNA 提取角色
  - 成本:¥51.4 → ¥53.6 / 本(+¥2.2:Qwen-VL DNA + 素材库抽取 + 导演 prompt 加长)
  - DB schema:`comic_projects` 再加 3 字段(reference_image_urls/visual_dna/detailed_prompt)+
    3 新表(character_visuals / scenes / props)
  - 工程量:12-13 sprint → **14-16 sprint**(+2-3)
  - 法务边界扩展:用户上传参考图作 AI 风格分析的合规协议(🔴 待法务确认)
  - 副作用收益:中间态续写也受益(导演 agent 拉素材库),默认不强制,付费档可勾选

---

> **写给 6 个月后的自己**:
>
> 这个 ADR 是 2026-05-11 你和协作 AI 讨论时的产物(v1)。
> 2026-05-12 同日 D.8 横评完工后修订成 v2 — 加了画风定调员 + 角色锚定员 + 4 层角色一致性方案。
> 2026-05-12 同日 D.8 二轮横评(Seedream 4.0)实测后又修订成 v3 — 画风定调员升级到多模态视觉
> DNA 提取(用户上传 3 张参考图)+ 新增素材库抽取员(character_visuals / scenes / props)+
> AI 对焦扩展到 5 类素材 + L1 描述符 20-30 句。
>
> 你那天的关键洞察:**"prompt 要长,要参考图,要场景/道具/角色细节抽出来"** — 这是漫画态从
> "看着像但出彩不够"提升到"真正能用"的核心区别。v3 不是 over-engineering,是用户实际跑过
> Seedream 4.0 + 1.6 元横评后产生的真实需求。
>
> 如果你 6 个月后看到这个文档想立刻动手,先回头检查 **§11 的 4 个前置条件** — 已勾 2 条
> (D.8 ✅),剩 2 条(法务备案 + 真实用户验证)任一未满足,这个 **14-16 sprint** 项目会
> 卡死在路上。**特别注意 §11 v3 法务补充**:用户上传参考图的合规边界,法律顾问要提前问到。
