# 3 天 PR 计划 — 剧创态参赛仓库

> 全程主分支可跑。每 PR 一件事。commit 时间戳必须落在赛题窗口内。
> **目标**:14 个 PR,均匀铺满 3 天,每个都对应"一句话能说清的功能",合规 + 过程分双满。

---

## Day 0(开题瞬间 — 30 分钟)

### Commit 0-1:仓库初始化 + 第一版 README
- 仓库名:`huimeng-screenplay`(或用户自定)
- 初始 README:1 段简介 + "本仓库是浑晶平台剧创态的独立实现"声明
- `.gitignore` / LICENSE(MIT)
- 目录结构:`/backend` `/frontend` `/docs`

### Commit 0-2:INTEGRATION_NOTES.md + SCHEMA_DESIGN.md
- 把本目录的两份文档拷过去
- **D0 提交完成,Day 1 第一个 PR 开始**

---

## Day 1 — 脚手架 + Schema + 摄入(5 PR)

### PR#1 — 仓库脚手架 + 技术栈对齐
- **标题**:`chore: bootstrap monorepo with FastAPI backend + Vue 3 frontend`
- **内容**:
  - `backend/` FastAPI 起步,`/health` endpoint
  - `frontend/` Vue 3 + Vite + TypeScript 起步
  - `requirements.txt` / `package.json` 第三方依赖列明
  - `make dev` 一键起两个服务
- **测试方式**:`make dev` → 浏览器 `http://localhost:5173` 看到 hello world,`/health` 返 200
- **预期 commit 数**:3-5(每个步骤独立 commit)

### PR#2 — YAML Schema v1.0 + JSON Schema 校验配套
- **标题**:`feat(schema): define screenplay YAML schema v1.0 with JSON Schema validation`
- **内容**:
  - `docs/SCHEMA_DESIGN.md` 完整文档(本目录已写好)
  - `backend/app/schemas/screenplay.json` JSON Schema 严格校验定义
  - `backend/app/services/yaml_validator.py` 校验函数 + 单元测试
  - 配 example fixture(一份合法剧本 + 一份故意非法的)
- **实现思路**:Pydantic 定义结构 + `jsonschema` 库做硬校验。失败时返人类可读错误消息
- **测试方式**:`pytest tests/test_yaml_validator.py` 全过
- **复用声明**:无(全新设计)

### PR#3 — 小说摄入与章节解析
- **标题**:`feat(ingest): parse .txt/.epub/.docx into chapters with paragraph indexing`
- **内容**:
  - 上传 endpoint(POST `/novels`,multipart)
  - .txt / .epub / .docx 解析(用 `ebooklib` + `python-docx`)
  - 自动分章(`第 N 章` / `Chapter N` / `# `等多种标题模式)
  - 每段保留 `paragraph_index` 用于后续溯源
  - SQLite 存储:`novels` + `chapters` + `paragraphs` 三表
- **测试方式**:fixture 用《麦田守望者》第一章 .txt,断言章节数和段落数
- **复用声明**:借鉴浑晶 `epub_parser` 的分章逻辑思路,代码全新

### PR#4 — JSON 故事圣经导入(角色 / 地点 / 事件)
- **标题**:`feat(bible): import story bible JSON (characters/locations/events)`
- **内容**:
  - POST `/novels/{id}/story-bible` 接收 JSON
  - 数据结构与浑晶 `characters_input` schema 兼容(README 注明)
  - 也支持"无 bible 启动" — 用 LLM 自动从前 3 章抽取
- **实现思路**:用 deepseek-chat 抽取 characters / locations / events 三类。prompt 严格 JSON 输出。
- **测试方式**:同时跑"导入预制 bible"和"LLM 自动抽取"两条路径
- **复用声明**:接口语义同构浑晶 `extract_service`,代码全新

### PR#5 — 配套 JSON Schema 校验 + CLI 验证工具
- **标题**:`feat(cli): add 'screenplay validate' CLI tool for offline YAML validation`
- **内容**:
  - 命令行 `python -m screenplay validate path/to/script.yaml`
  - 用 JSON Schema 校验,失败时人类可读输出
- **测试方式**:用合法和非法 YAML 跑 CLI,exit code 验证
- **预期 commit 数**:2-3

---

## Day 2 — 流水线核心(5 PR — 全部影响打分)

### PR#6 — 场景切分器(scene_splitter)
- **标题**:`feat(splitter): segment chapters into scenes by location×time×event transitions`
- **内容**:
  - `backend/app/services/scene_splitter.py`
  - 输入:整章 prose + 故事圣经
  - 输出:scene 候选列表(heading + 段落范围)
  - LLM 用 deepseek-chat,prompt 严格规定 JSON 输出
  - 判定逻辑:"地点变化 / 时间跳跃 / 事件性变化"三种 boundary
- **实现思路**:借鉴浑晶 director 的 `transition_from_prev_scene` 判定
- **测试方式**:fixture 一章已知有 3 个场景的段落,断言切出 3 段
- **复用声明**:借鉴 director 的 transition 判定思想,代码全新

### PR#7 — 动作元素抽取(action_extractor)
- **标题**:`feat(action): extract action elements from scene prose`
- **内容**:
  - `backend/app/services/action_extractor.py`
  - 输入:单场 prose + 圣经
  - 输出:`action` 元素数组
  - LLM prompt:"剧本动作必须可见,不许写心理"
- **测试方式**:fixture 一段含心理描写的 prose,断言输出全是可见动作

### PR#8 — 对白抽取 + 归属(dialogue_extractor)
- **标题**:`feat(dialogue): extract dialogue with character attribution (pronoun resolution)`
- **内容**:
  - `backend/app/services/dialogue_extractor.py`
  - 用故事圣经的 `aka` 表辅助代词消解
  - 输出:`dialogue` + `parenthetical` 元素
- **实现思路**:LLM 第一步识别"说话内容",第二步基于上下文 + aka 表分配 `character_id`
- **测试方式**:fixture 有"他说...""她答..."零标注对白,断言归属正确

### PR#9 — 内心戏转译 + 改编决策(adaptation_decision)
- **标题**:`feat(adaptation): give author 3 options for monologue (V.O./externalize/delete)`
- **内容**:
  - **核心差异化 PR** — 评委最容易被打动的画面
  - `backend/app/services/adaptation_decision.py`
  - 遇内心独白 → LLM 生成 3 备选 → 写入 `adaptation_decisions` 段
  - 默认选 voiceover(作者可改)
- **实现思路**:不偷偷决定,把决策权交给作者(尊重作者创作主权)
- **测试方式**:fixture 一段内心独白,断言生成 3 备选 + 默认选 V.O.
- **复用声明**:思想借鉴浑晶反事实工作台"给用户多选项",代码全新

### PR#10 — YAML 组装 + Schema 校验 + 失败重试
- **标题**:`feat(compose): assemble YAML output with strict validation and auto-repair`
- **内容**:
  - `backend/app/services/screenplay_composer.py`
  - 组装 meta + characters + locations + scenes + adaptation_decisions
  - `jsonschema.validate` 校验
  - 失败 → 局部修复 prompt → 最多重试 2 次
  - 主 endpoint:`POST /novels/{id}/screenplay` 一键转
- **测试方式**:跑全流程,断言产物通过 JSON Schema

---

## Day 3 — UI + 差异化亮点 + Demo(4 PR)

### PR#11 — 双栏对照编辑器(原文 / 剧本 + 溯源高亮)
- **标题**:`feat(ui): dual-pane editor with source paragraph highlight`
- **内容**:
  - 左:原文 paragraph,右:对应场景剧本
  - 点击场景 → 左侧滚动到 `source.paragraph_range`,高亮
  - 沿用浑晶设计语言:奶白底 + 紫色点缀

### PR#12 — fidelity 评分 + 黄标提醒
- **标题**:`feat(fidelity): LLM-based scoring with low-fidelity highlights`
- **内容**:
  - `backend/app/services/fidelity_scorer.py`
  - LLM 评每场 high / medium / low
  - low → 场景卡片黄标 + 显示 issues
- **复用声明**:借鉴 canonical_guardian 12 维评分思想

### PR#13 — 剧本结构报告(三幕分区 + 张力曲线 + 关键场)
- **标题**:`feat(report): show 3-act structure + tension curve + key scene analysis`
- **内容**:
  - 同构浑晶 outline 的张力曲线
  - SVG 折线图:每场张力(0-100)+ 三幕背景色
  - 这是**任何竞品都没有的差异化**

### PR#14 — README 完善 + demo 视频 + 主分支冒烟测试
- **标题**:`docs: finalize README, record demo video, smoke-test main branch`
- **内容**:
  - README 8 条必含项全过(见 chat 蓝图 §8.3)
  - demo 视频脚本 + 录制 → 上 bilibili
  - 主分支跑全流程冒烟 → 截图作为 CI 通过证据

---

## commit 节奏锚点(确保不被判"临尾突击")

| 时间段 | 期望 commit 数 |
|---|---|
| Day 1 上午 | 5-8(PR#1-2)|
| Day 1 下午 | 5-8(PR#3-5)|
| Day 2 上午 | 5-8(PR#6-7)|
| Day 2 下午 | 5-8(PR#8-9)|
| Day 3 上午 | 5-8(PR#10-12)|
| Day 3 下午 | 3-5(PR#13-14)|

**总计**:25-40 commit 跨 14 PR,均匀分布。

---

## 临尾突击防御(用户必读)

1. **第一天必须有 PR#1-2 合并到 main**
2. **第二天必须有 PR#6-7 合并到 main**(说明"流水线工作")
3. **第三天前 4 小时必须 PR#11 合并**(说明"UI 工作")
4. **截止前 2 小时绝对禁止 push 任何代码**(留 buffer 做 demo 录制 + 提交)

---

## 我会持续帮你做什么

1. **每个 PR 起草时**:我帮你写 service 代码 + 测试 + PR 描述四件套
2. **每天结束**:我帮你跑回归测试 + 检查 commit 时间分布健康度
3. **Day 3 demo 视频**:我帮你写脚本 + 关键画面截图清单
4. **README 终稿**:我帮你扎实写完所有 8 条必含项

只要你拍板 D0 开题,我立刻进 PR#1 模式 — 直接 push 代码。
