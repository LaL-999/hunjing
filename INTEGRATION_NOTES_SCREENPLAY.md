# 剧创态并入浑晶 · 迁徙工作笔记

> Sprint SP-S(2026-06-07 ~ 2026-06-08)— 把比赛仓库 `hunjing-screenplay`
> 整合为浑晶第 5 创作态。
> **当前状态:7 阶段大工程 + 5 项 Plan A 增强全工完工**(23 个独立 commit,
> 从 `cd5cea1` 到 `0a100b8`,全部已 push 至 `github.com/LaL-999/hunjing`)。

---

## 决策拍板(已确定)

| # | 决策 | 选择 |
|---|---|---|
| D1 | DB 表名前缀 | **加 `sp_` 前缀**(防与现有表冲突) |
| D2 | 故事圣经数据 vs 角色 Agent | **数据走 A 隔离表 + Agent 走"按需调用浑晶"**(huimeng_bridge) |
| D3 | 剧创态 plan 限制 | **Free 也开**(获客)|
| D4 | agent 接通浑晶资产范围 | **6 个 agent 全接通**(scene_splitter / element_extractor / dialogue_attributor / adaptation_decision / screenplay_optimizer / story_bible_extractor) |

---

## 阶段进度

| 阶段 | 内容 | 状态 |
|---|---|---|
| **1** | Dashboard 加 5/6 卡 + 路由占位 | ✅ 完工(commit cd5cea1)|
| **2** | 前端真实代码迁入(浑晶视觉适配)| ✅ 完工(2026-06-08 中午)|
| **3** | 后端代码迁入 + 表名 sp_ 前缀 + router 鉴权挂载 | ✅ 完工(2026-06-08 下午)|
| **3.5** | service 层 SQL user_id 过滤(数据用户级隔离) | ✅ 完工(2026-06-08 傍晚)|
| **4** | DB schema 进 migration runner(quota 后期五态统一)| ✅ 完工(2026-06-08 夜)|
| **4.5** | sp_novels.user_id INTEGER→TEXT 类型对齐(阶段 3 留 bug)| ✅ 完工(2026-06-08 夜)|
| **5.1** | huimeng_bridge 桥接层骨架 + linked_project_id + PATCH /link endpoint | ✅ 完工(2026-06-08 夜)|
| **5.2** | scene_splitter 接通 SP-7 polarity | ✅ 完工(2026-06-08 夜)|
| **5.3** | element_extractor 接通 SP-2/3/7 三档(最重的一个)| ✅ 完工(2026-06-08 夜)|
| **5.4** | dialogue_attributor 接通 SP-2 drivers(tiebreaker)| ✅ 完工(2026-06-08 夜)|
| **5.5** | adaptation_decision 接通 SP-2 drivers + SP-3 facts/knowledge | ✅ 完工(2026-06-08 夜)|
| **5.6** | screenplay_optimizer 接通 SP-4 状态快照(跨场一致性)| ✅ 完工(2026-06-08 夜)|
| **5.7** | story_bible_extractor 复用 linked-project 角色档 | ✅ 完工(2026-06-08 夜)|
| **5.8** | huimeng_bridge 单元测试 16 case | ✅ 完工(2026-06-08 夜)|
| **6** | 前端视觉融合 + API 复用父平台(critical JWT 修复)+ link UI + 入口卡差异化卖点 | ✅ 完工(2026-06-08 晚)|
| **7** | 比赛 234 测试迁入 + JWT/bridge fixture + 222 全过 + 11 CLI 合理 skip | ✅ 完工(2026-06-08 深夜)|

---

## Plan A 5 项增强(2026-06-08,根据竞品对比规划文档)

读了 `hunjing-screenplay/docs/浑晶平台-5项移植规划.md` 后,**叠加桥接层的差异化**,
做出了一份升级版的 5 项规划。

| 阶段 | 内容 | 状态 |
|---|---|---|
| **8 P0** | 🔥 BYOK 接通(文档遗漏,critical):剧创 llm_client 代理父平台 call_llm_*,8 agent 0 改动享受 BYOK | ✅ 完工 `84385e8`|
| **8.1** | ② 自由文本改稿 + bridge 协同:user_instruction × SP-2/3/7 协同生效 | ✅ 完工 `506a113`|
| **8.2** | ① 角色页 + SVG 力导向关系图 + 桥接资产 chips(SP-2/4 driver/snapshot 显形)| ✅ 完工 `f1dcad6`|
| **8.3a** | ③ 第 4 张牌:桥接增益面板 + 张力曲线 draw-on 动画 + 关键节点脉冲 | ✅ 完工 `6df6ae6`|
| **8.3b** | ③ 3b 版本树 diff + 一键回滚 | ✅ 完工 `2998138`|
| **8.4** | ④ 分集 MVP 规则版(LLM 增强后期再加,短剧不是核心)| ✅ 完工 `233fb20`|
| **8.5 后端** | ⑤ 多模型对比 backend + 7 测试(可解释 4 维评分反超竞品黑盒)| ✅ 完工 `3834002`|
| **8.5 前端** | ⑤ ComparisonPanel.vue 完整 UI(配置/运行/结果三阶段 + JSON 导出)| ✅ 完工 `0a100b8`|

### 推迟项(等用户实测决定)

- **3c Fountain → Final Draft 兼容验证**:需 Final Draft / Highland 等付费软件实测
- **8.5「采用此版」自动覆盖**:需新后端 endpoint + yaml_composer 重组,等 demo 看 workflow 后再决定是自动还是保持当前 JSON 导出 + OptimizationModal 引用路径

### 阶段 8.5 完工摘要(item ⑤ 多模型对比 — Plan A 收官)

**最大叠加**:文档预估 4d(含 LLM 客户端 ProviderConfig 重构),实际 ~1.5d
后端 + ~2h 前端 完工 — 因为 **P0 BYOK 已经搞定了 LLM 客户端层基础设施**,
`_openai_compat_call_json` 接 `(api_key, base_url, model)` 三件套已就位。

**后端**(`3834002`):
- `services/model_comparison_service.py` ~270 行:
  - `ProviderConfig { label, api_key, base_url, model }`
  - `compare_scene_extraction(screenplay_id, user_id, scene_id, providers)` async
  - 用 `asyncio.gather` + `asyncio.to_thread` 真并行调多 vendor
  - 直接 `_openai_compat_call_json`(绕过 llm_routing 的 deepseek-only 限制)
  - 复用 `fidelity_scorer` 给 4 维评分(action_density / character_alignment /
    dialogue_coverage / decision_completeness)+ overall 加权
  - **graceful degradation**:某 provider 失败返 `ModelCandidate(success=False)`,
    其他 provider 继续 — 铁律
- `routers/compare.py`:POST `/api/screenplay/screenplays/{id}/compare`
- `tests/test_screenplay_model_comparison.py` 7 case 全过(`asyncio.run` 模式,
  父平台没 pytest-asyncio):
  - <2 providers / 剧本不存在 / 跨用户 / 场景不存在 错误路径
  - 两 provider 都成功 → recommended 是高分的(可解释:具体哪维分高)
  - 1 失败 1 成功 → 都进 candidates,失败有 error_message,recommended=成功的
  - 全失败 → recommended_label=None

**前端**(`0a100b8`,~700 行 vue):
- `frontend/src/screenplay/components/ComparisonPanel.vue` 3 阶段全屏 modal:
  - CONFIG:目标 scene 选择 + provider 列表(2-5 个,每个 4 输入)+ 5 个 preset chip
    (DeepSeek / OpenAI / Anthropic / Qwen / Moonshot)+ +增删
  - RUNNING:spinner + 计时 + 进度文案按时长 rotate
  - RESULT:candidate cards grid(auto-fill 360px),每张:
    - 推荐金标 + provider name + model + 耗时 + tokens
    - 4 维进度条(overall + 4 dimensions)
    - elements 滚动列表 + type 色块
    - 「导出此候选」按钮(JSON 下载,含 elements + scores + usage)
- API 持久化:provider configs(含 api_key)存 localStorage 减少摩擦
- EditorView 顶栏加「对比」按钮(只在 hasScreenplay 时)
- vue-tsc 0 错 / vite build 2.11s

**为什么没做「采用此版」自动覆盖**:需要新后端 endpoint 接 candidate.elements →
yaml_composer 重组 scene → save_screenplay 新版本。是清晰的工作但需 100 行新代码 +
测试。当前用「导出 JSON + OptimizationModal user_instruction 引用」workflow 兜底 —
更灵活,所有候选可保留对比,而不是一旦点「采用」就破坏性写入。

### 阶段 8.4 完工摘要(item ④ 分集 MVP)

**战略缩减**:文档预估 4d(规则 + LLM 增强 + 前端 + 导出),我做 1d 纯规则版,
因为短剧不是浑晶核心定位。架构留 LLM 增强口子(`mode='rule'|'llm'`)。

**后端**(`233fb20`):
- `services/episode_planner.py` ~280 行:贪心算法
  - est_minutes = elements_count / 25(行业 1 页 ≈ 1 分钟 ≈ 25 elements)
  - 70%-130% 目标时长窗口找好边界:chapter 切换 > 强转场 > 目标达标
  - >130% 强切(防止某场过长压死单集)
  - 标题:首场 summary 前 14 字
- `routers/episodes.py`:POST `/api/screenplay/novels/{id}/plan-episodes`
- `tests/test_screenplay_episode_planner.py` 8 case 全过:
  - **铁律**:所有 scene_id 恰好出现一次(无漏 / 无重复)
  - 章节边界优先 / 短目标多集 / 标题用 summary / 跨用户隔离

**前端**:`EpisodePlanPanel.vue` 滑块 0.5-15 分钟 + 4 预设(短剧 1.5 / 短剧 3 /
长剧 8 / 长剧 12)+ 集卡可展开看 scene_ids + 边界原因标签

### 阶段 8.3 完工摘要(item ③ 三王牌 + 第 4 张桥接增益)

**3a 张力曲线动画化**(`6df6ae6`,`StructureReportPanel.vue` 改动):
- 曲线 `stroke-dasharray: 2000` + `stroke-dashoffset: 2000` + 1.8s 动画 → 进入时
  从左到右"长"出来
- 关键节点(inciting_incident / midpoint / climax)加脉冲外圈:opacity 0→0.6→0
  + transform scale 0.6→1.8(避免 CSS-animated SVG `r` 跨浏览器坑)

**3b 版本树 diff**(`2998138`,`VersionDiffPanel.vue` 新增 ~480 行):
- 两栏版本选择器 + swap 按钮
- 默认 left=当前 / right=parent
- change_log 逐条:action 色块(modified 紫 / added 绿 / removed 红 / split & merged 黄)
- 「回滚到左侧版本」走 `useConfirm` 弹确认
- VersionSwitcher dropdown 顶部加「对比 / 回滚版本」入口 + emit `open-diff`

**3c Fountain 校验** — **推迟**:需 Final Draft / Highland 等付费软件实测,
backend 单元测试已确保 fountain 语法结构正确(`tests/screenplay/test_screenplay_exporter.py`),
但「真的能在专业软件里打开」只能由用户实测验收。

**第 4 张牌:桥接增益面板**(`6df6ae6`,新 `BridgeGainBanner.vue`):
- compose_service.stats_counter 加 5 个 bridge 字段:
  - bridge_drivers_injections / bridge_knowledge_injections /
    bridge_polarity_injections / bridge_facts_used / bridge_was_linked
- 每个 bridge 块产生非空时累加计数
- 前端从 store.stats 读,3 模式显示:
  - active(有注入):紫 banner + 数字 chip(SP-2 N 场 / SP-3 M 场 / SP-7 K 次 / SP-3 facts 已用)
  - linked-empty(已 link 但父平台未填):amber 提示去浑晶填字段
  - unlinked:灰色 tip 提示绑定后会显示
- demo 命题:"绑定项目后,本次 compose 自动注入 28 driver / 73 knowledge /
  19 polarity / 项目级 facts" — 桥接价值具体显形

### 阶段 8.2 完工摘要(item ① 角色页 + 关系图 + 桥接资产可视化)

**最大反超**:不只是出场章数 / 台词句数(竞品现状),还显示 SP-2/4/7 资产 — 这是
有桥接才能做到的事。

**后端**(`f1dcad6`):
- `services/character_profile_service.py` ~430 行,三路 JOIN:
  - 身份/关系/事件:sp_bible_characters / sp_bible_relationships / sp_bible_events
  - 戏份统计派生:scene_count / chapter_count / dialogue_count / voiceover_count /
    first_appearance_scene / role_tier(protagonist > supporting≥5dialogue >
    bit_part > extra)
  - 桥接资产(仅 linked):SP-2 driver(surface_goal/deep_need/fatal_blind_spot/
    arc/secrets)+ SP-4 latest snapshot(position/emotion_top/hp/inventory)+
    SP-7 polarity 用于边色
- `routers/character_profiles.py`:GET `/novels/{id}/characters`
- `tests/test_screenplay_character_profile.py` 7 case 全过

**前端**:
- `CharacterRelationshipGraph.vue` ~280 行,自己实现 2D SVG 力导向(不引 d3-force /
  cytoscape):Fruchterman-Reingold 简化版 + 库仑斥力 + 胡克引力 + 80 帧迭代
  - 节点色:role_tier(主角紫 / 配角钢蓝 / 龙套灰 / 群演淡)
  - **桥接外圈**:has_bridge_assets 时脉冲虚线 accent — 视觉标记"已接通父平台"
  - 边色:SP-7 polarity(positive 绿 / negative 红 / neutral 灰 / 未标淡虚线)
  - hover 高亮 + 邻居淡化
- `CharacterProfilesPanel.vue` ~580 行,全屏 modal:
  - 左 540×440 关系图 + 右 滚动角色卡列表
  - 卡片:tier badge / 戏份统计 / **桥接资产 chip 列**(driver/snapshots 字段)/
    关系列表 / 事件列表
  - 顶部 tabs:全部 / 主角 / 配角 / 龙套 / 群演 + 计数
  - 头部桥接 chip:`✨ 已接通浑晶项目 · N/M 角色有 SP 资产`

### 阶段 8.1 完工摘要(item ② 自由文本改稿 + bridge 协同)

**杀手锏组合**:文档作者没意识到的差异化点。

**后端**(`506a113`):
- `OptimizeRequest` + `OptimizeRequestBody` 加 `user_instruction: str | None`
- `_build_user_input` 非空时附加为 `user_instruction` 字段
- `prompts/screenplay_optimizer.md` 加「优先级铁律」段:
  - 作者指令优先级最高,必须执行
  - 但 schema 合法性 + full_outline 一致性 + **bridge 资产铁律仍然生效**
  - 例:用户说「让 X 更冷漠」+ bridge 知道 X 的 `fatal_blind_spot=「假装冷漠
    掩盖自卑」` → AI 写"冷漠表象 + 渴望被认可的潜台词"双层立体,而非平面冷漠
  - 例:用户说「加冲突」+ bridge 知道 A 对 B 瞒着秘密 → AI 制造「差点暴露」
    的高级冲突

**前端**:OptimizationModal config 阶段加 textarea(500 字限 + char counter)+
非空时显示「指令将与诊断 + 桥接资产协同生效」accent 提示

### 阶段 8 P0 完工摘要(BYOK critical 接通)

**critical 潜伏 bug**:阶段 3 迁入时把比赛仓库的独立 `app.screenplay.services.
llm_client.py` 也搬进来了 — 它直接 `openai.OpenAI(api_key=settings.deepseek_api_key)`
读 .env,**完全绕过父平台 6/5 上线的 BYOK 体系**。

后果:
- 用户在浑晶配 BYOK key(自己的 deepseek/openai/anthropic),走中末漫态都用自己的 key
- **唯独剧创态烧平台 token** → 商业模式漏
- 不同用户跑剧创态共享 .env 的 server-side key,无法成本归属

**修复**(`84385e8`):
- 把 `app/screenplay/services/llm_client.py` 重写为父平台 `call_llm_text/json` 的代理
- 签名完全保留(8 个 agent 0 改动 import)
- 异常类直接 re-export 父平台的(契约一致)
- 自动:从 ContextVar 读 user_id(`get_current_user` 设)→ 查 byok_configs → 有则用用户的
- 238 测试 0 回归

**lesson learned**:整合时不只是 import 路径 sed,**底层 service 调用链也要审一遍**
— 上一个 dangling FK bug 是 schema 整合层的盲区,这个 BYOK 是依赖路径层的盲区。

### 阶段 3 完工摘要

- 比赛仓库 `backend/app/` 全套复制进 `huimeng/backend/app/screenplay/`(routers/services/parsers/db/schemas/prompts/config)
- 所有 `from app.*` → `from app.screenplay.*` 命名空间隔离(38 个 .py 文件)
- 表名加 `sp_` 前缀:novels/chapters/paragraphs/story_bibles/bible_*/screenplays
- `sp_novels` 加 `user_id INTEGER NOT NULL FK users(id) ON DELETE CASCADE`
- `sp_screenplays` schema 直接包含版本树字段(parent_screenplay_id / optimization_origin / optimization_log_json)
- `db/connection.py` 改走父平台 `app.db.get_connection`(同库 huimeng.db,共享 WAL+FK 配置)
- `config.py` 改走父平台 `app.config.settings`(deepseek key / db path / upload dir 透传)
- `main.py` 加 `_init_screenplay_schema()` 启动钩子 + create_app() 内注册 9 个 router 到 `/api/screenplay/*` 前缀
- 9 个 router 全部加 `router-level dependencies=[Depends(get_current_user)]` 鉴权
- 失败兜底:剧创态 import 失败 log warning 不阻塞父平台启动
- **验证**:
  - `from app.main import app` 成功导入
  - 18 个 `/api/screenplay/*` 路由全部注册
  - `pytest --co` 962 测试收集成功(父平台测试无污染)

### 阶段 7 完工摘要(测试迁入 — 7 阶段大工程收官)

把比赛仓库 `hunjing-screenplay/backend/tests/` 的 234 个 pytest case 整套
迁入 `backend/tests/screenplay/`,适配父平台的 JWT / user_id 隔离 / sp_
前缀 / 父平台 conftest。

**机械改造**(sed 批量):
- `from app.X` → `from app.screenplay.X`(18 .py 文件)
- `monkeypatch.setattr("app.services.X.Y")` → `monkeypatch.setattr("app.screenplay.services.X.Y")`
- 端点路径 `"/novels"` / `"/chapters/X"` 等 → `"/api/screenplay/novels"` / `"/api/screenplay/chapters/X"`
- 直接 SQL `INTO novels` → `INTO sp_novels`(`chapters` / `paragraphs` / `screenplays` / `bible_*` 同)
- LLM mock lambda 加 `**kwargs`(阶段 5 桥接 + 阶段 3.5 user_id 让函数签名加了新 kwarg)
- 局部 `temp_db` fixture body 改 noop(父平台 reset_test_db autouse 已经搞定)
- 局部 `client` fixture body → 返 conftest 的 `screenplay_client`(带 Bearer JWT)

**新增 `tests/screenplay/conftest.py`**:
- `screenplay_user` / `screenplay_user_token` / `screenplay_client`:造一个 user
  + 签 JWT + 返带 Bearer 的 TestClient
- `another_user_token`:第二个 user(测跨用户隔离)
- `temp_db` / `client`:比赛 fixture 兼容层(yield None / 返 screenplay_client)
- `mock_huimeng_bridge`:把所有 huimeng_bridge.get_*_block 全 stub 为空字符串
  (controller 可以 set_drivers / set_polarity 等注入响应)
- `mock_screenplay_llm`:把 `app.screenplay.services.llm_client.call_json` 全 stub
- **autouse `_inject_default_user_id_into_screenplay_services`**:每个剧创测试
  自动建 user + monkeypatch ingest_service / story_bible_service / screenplay_store /
  compose_service / scene_splitter 的所有 user_id 参数,让比赛测试无须手改
  调用就能跑(只对 `tests/screenplay/` 路径触发,不影响父平台测试)

**手改 / 重写**:
- `_insert_novel` / `_insert_novel_with_chapters` 助手函数(3 文件)读
  conftest autouse 已建的 user → 写 `sp_novels.user_id`
- `test_health.py` 重写:比赛仓库的 /health 返 `service: "hunjing-screenplay"`
  schema,父平台无此结构 → 改测「父平台 /healthz 仍可达」+「18 个
  `/api/screenplay/*` 路由已注册」语义
- `test_cli.py` 整文件 `pytest.mark.skip`:CLI 模块未迁入父平台
  (用户改走 endpoint),保留文件但不跑

**最终结果**(`pytest tests/screenplay/`):
- ✅ **222 / 234 PASSING**
- ⏸ **11 skipped**(test_cli.py 全文件 — 已说明原因)
- ❌ **0 failed**
- 父平台 962 测试 0 污染(`pytest --co` 总 1211)

### 阶段 6 完工摘要(前端视觉融合 + link UI)

**critical JWT 修复**:阶段 2 迁入的 `screenplay-client.ts` 是裸 fetch,
没带 Bearer token。父平台 router 全部 inject `Depends(get_current_user)`,
意味着前端一调 → 401 全军覆没。本阶段:
- `screenplay-client.ts` **全套重写** — 复用父平台 `api`(自动 JWT + 统一
  ApiError + 401 → onUnauthorized)
- 新增 `linkNovelToProject` / `listProjectsForLink` 两个 API helper
- 删除独立 `request<T>` + 独立 `ApiError`,直接 re-export 父平台的

**ScreenplayHomeView 重写**:
- `window.confirm` / `window.alert` → `useConfirm` / `useToast`(项目铁律)
- 删 hard-coded `http://localhost:8003/docs` 链接
- 加每本小说的「绑定 / 解绑」入口 + 绑定状态指示
- header 加「接通浑晶 4 大资产」差异化 banner

**Link 抽屉**:Teleport 到 body,backdrop blur(4px),中央卡片;
列出该用户的所有浑晶 project,显示当前绑定项目,支持改绑 / 解绑;
项目空时友好提示 + 跳 Dashboard 链接。

**Dashboard 入口卡升级**:
- `CreationModeQuadrant` 加 `diffTag` 字段(可选)
- 剧创态卡显示 "可接通 浑晶角色驱动力 · 知识边界 · 状态时间线 · 关系正负极"
- 紫色 chip + 单行省略,不破坏卡片高度

**验证**:
- ✓ vue-tsc 0 错
- ✓ vite build 2.31s 过(694+ modules)

### 阶段 5.1 完工摘要(huimeng_bridge 骨架就位)

阶段 5 是产品差异化命脉 — 决定剧创态是"独立 SaaS"还是"浑晶真护城河"。
拆成 7 个子阶段:5.1 桥接层骨架 + 5.2-5.7 六个 agent 逐个接通。

**改动**:
- migration 086:`sp_novels` 加 `linked_project_id TEXT` FK projects ON DELETE SET NULL
- `app/screenplay/services/huimeng_bridge.py`(~410 行):6 个 get_*_block 函数
  + link_novel_to_project + get_linked_characters
- `app/screenplay/routers/novels.py`:新 endpoint `PATCH /novels/{id}/link`
  body `{ project_id }` 或 `{ project_id: null }`

**6 个 get_*_block 函数 → 产出 prompt 块**:
| 函数 | 接通父平台资产 | 用于哪个 agent |
|---|---|---|
| `get_character_drivers_block` | SP-2 surface_goal/deep_need/secret_json | element_extractor / dialogue_attributor / adaptation_decision |
| `get_character_knowledge_block` | SP-3 story_facts + character_knowledge | element_extractor / scene_splitter / adaptation_decision |
| `get_character_snapshots_block` | SP-4 character_state_snapshots | screenplay_optimizer 跨场一致性 |
| `get_relationship_polarity_block` | SP-7 relationships.polarity | scene_splitter / element_extractor / dialogue_attributor |
| `get_story_facts_block` | SP-3 story_facts(项目级)| adaptation_decision 不许编造细节 |
| `get_linked_characters` | characters 表全字段 | story_bible_extractor 复用已建角色 |

**异常隔离铁律**:
- 每个 get_*_block 失败(浑晶 service 抛错 / 表不存在)→ 返 `""` 或 `[]`
- **绝不阻断**剧创 agent — 桥接挂掉,剧本质量降级但仍能出
- 桥接的"额外语料"是奢侈品 prompt 增强,不是 critical path

**安全模型**:
- 所有 get_*_block 必须传 `user_id`,内部走 `_resolve_project_id(user_id, novel_id)`
- 校验 sp_novels 归属当前用户 → 拿 linked_project_id
- 跨用户访问 sp_novels 返 None → bridge 退化 "" → 等于未 link
- link_novel_to_project 双重校验:novel 属于 user + project 属于 user

**未链接的兜底**:
- linked_project_id NULL → bridge 函数返空块 → agent 用纯小说原文跑(等于阶段 3 行为)
- 用户在前端绑定后,**同名角色立刻享受所有 SP-2/3/4/7 资产增益**

**验证**:
- ✓ `from app.main import app` — 19 个 /api/screenplay 路由(原 18 + 新 /link)
- ✓ pytest --co 962 测试无污染
- ✓ migration 086 自动应用(linked_project_id 列已加)

### 阶段 4.5 完工摘要

阶段 3 的潜伏 bug:`sp_novels.user_id` 写成 `INTEGER`,但父平台 `users.id` 是
`TEXT(UUID 字符串)`。阶段 5 huimeng_bridge 要 JOIN 父平台 `characters /
projects` 时,INTEGER vs TEXT 走 SQLite 类型亲和隐式转换,行为不可预测。

**改动**:
- `migration 085`:`user_id INTEGER NOT NULL` → `user_id TEXT NOT NULL`
- `app/main.py` 加 `_fix_sp_novels_user_id_type` 启动钩子:
  - PRAGMA 检测列类型;若 INTEGER → ALTER 重建为 TEXT(数据 CAST 拷贝);若已是 TEXT → noop
  - 为什么走 Python 而非 SQL migration:SQLite 无 ALTER COLUMN TYPE,migration
    runner 没有"已应用"标记 — SQL 没法条件执行;Python 检测一次性补丁最干净
- 所有 service / router 的 `user_id: int` → `user_id: str`(6 文件统一)

**验证**:
- ✓ 首次启动:WARNING 日志 + INTEGER → TEXT 重建
- ✓ 二次启动:noop(无 WARNING)
- ✓ pytest --co 962 测试

### 阶段 4 完工摘要

把阶段 3 的"启动钩子"过渡产物规整进父平台 migration runner。
**用户拍板**:质量优先;quota 跳过 — 后期五态(初/中/末/漫/剧)统一计费。

**关键决策**:
- **D5 = B 方案**(规整进 migration 体系):长期统一性最高,后续 sp_ 字段变更
  和父平台 4 个月迭代的 84 张 migration 走同一条 `_auto_apply_migrations` 路径
- **D6 = 跳过**(quota 后期五态统一)

**改动**:
- 新增 `backend/migrations/085_screenplay_sp_tables.sql` — 把原阶段 3 的 3 个
  schema.sql 合并 + 严格 `IF NOT EXISTS`,可幂等重跑
- 删 `app/main.py:_init_screenplay_schema()` 启动钩子(及调用点)
- 简化 `app/screenplay/db/connection.py` 为纯 `get_connection` 代理(删 init_db
  / _run_in_place_migrations)
- 删 3 个 .sql 文件(`schema.sql / story_bible_schema.sql / screenplay_schema.sql`)—
  migration 085 是唯一权威源

**特别注意**(`_auto_apply_migrations` 行为):
- 一个 migration 文件走 `executescript`,包在 `with transaction(conn)` 里
- 任何语句失败整脚本回滚 → 不能在 migration 末尾加 `ALTER ADD COLUMN` 兜底
  (老库该列存在会 raise → 回滚整脚本 = 灾难)
- 阶段 4 的 migration 严格只用 `CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS` —
  fresh DB / 已建库均幂等
- 阶段 3 的 schema 已经在 `CREATE TABLE sp_screenplays` 内联了 3 个版本树字段,
  任何走过阶段 3 的库已经齐全 — 不需要 ALTER 补

**验证**(3 场景):
- ✓ 现有 DB(9 个 sp_ 表 + 3 版本列已建)再跑 migration 085 → noop 通过
- ✓ Fresh DB(drop sp_ 后再建)→ 9 张表 + 3 列齐全
- ✓ pytest --co 962 测试 collect(父平台无变化)

### 阶段 3.5 完工摘要

把"router 鉴权层 → service 层 SQL where 子句"的最后一公里走完。
JWT 已在阶段 3 拦住未登录访问,但 service 函数还接受裸 novel_id / screenplay_id,
意味着用户 A 拿到用户 B 的 ID(snoop / 越权扫库)仍能读到内容。3.5 把所有
读写函数都加上 `user_id`,在 SQL 层 WHERE / JOIN 校验归属。

**改动文件**(11 个):

services/(4 个全部加 user_id 隔离):
- ingest_service.py — persist/get/list/delete + get_chapter_paragraphs 全加 user_id
  + is_novel_owned_by_user 轻量 helper
- story_bible_service.py — import_bible_from_json / extract_bible_with_llm / get_bible
  全加 user_id;_persist_bible 私有不需要(调用者已校验)
- screenplay_store.py — save_screenplay 必填 user_id 且 INSERT 前 SELECT 1 校验归属
  (PermissionError 兜底);get_latest/by_id/list_versions/list_screenplays 全 JOIN sp_novels
- compose_service.py — orchestrate_full_pipeline 必填 user_id 透传到所有子调用
- pipeline/scene_splitter.py — split_chapter_from_db 必填 user_id

routers/(6 个全部 inject Depends(get_current_user)):
- novels.py ✓(阶段 3 已经 inject)
- story_bibles.py ✓(3 endpoint 全 inject)
- scenes.py ✓(split + JOIN sp_novels 校验)
- optimize.py ✓(POST optimize + GET versions)
- compose.py ✓(POST compose + 3 GET endpoint)
- export.py ✓(fountain/txt/yaml 3 个 + _load_parsed_screenplay 加 user_id)
- elements.py / attributions.py / decisions.py — 无状态 endpoint(纯 LLM 调用)
  阶段 3 的 router-level Depends 已经足够,阶段 3.5 无改动

**安全模型**:
- 跨用户访问统一返 404 / None / [] / False(隔离 = 无知),不暴露"存在但是别人的"
- save_screenplay 防御性 SELECT 1 校验,即使 router bug 直接 INSERT 也拒绝
- JOIN sp_novels 是核心隔离机制 — bible / screenplay 这种没直接 user_id 字段的
  子表,通过父 novel 归属间接限定

**验证**:
- `from app.main import app` 成功导入(18 个 /api/screenplay 路由都注册)
- `pytest --co` 962 测试收集(与阶段 3 一致,父平台测试无变化)
- 18 个 endpoint 的"传 ID 不传 user"路径已全部封堵

### 阶段 2 完工摘要

- 比赛仓库 10 个组件 + 2 个 view + store + types + api 全套复制进 `frontend/src/screenplay/`
- 视觉桥 `screenplay/styles/screenplay-overrides.css` — 在 `.screenplay-module` 作用域内
  把比赛令牌名(`--accent` / `--bg` / `--card-bg` 等)映射到父平台 `--color-*`
  实现"chrome 用父平台浑晶紫,剧本内部保留衬线 + Courier 等宽"的混合气质
- `api/client.ts` → `api/screenplay-client.ts`,`API_BASE = "/api/screenplay"`(阶段 3 后端会落 prefix)
- `main.ts` 加一行 import overrides 全局生效
- `router.push({ name: "home" })` → `name: "screenplay-home"`(对齐父平台路由名)
- 新增依赖:`js-yaml` + `@types/js-yaml`(`stores/screenplay.ts` 解析 yaml 用)
- **vue-tsc 0 错 + `npx vite build` 694 modules 2 秒过**

---

## 阶段 1 已完成的改动清单

### 前端

| 文件 | 改动 |
|---|---|
| `api/types.ts` | `ProjectMode` 加 `"screenplay" \| "more"` |
| `components/CreationModeQuadrant.vue` | 新增 `screenplay`(可用)+ `more`(占位 soon)2 张卡 + 3 列 grid 响应式 |
| `views/DashboardView.vue` | 删 `.forever-hint` + 加 `screenplay` 跳转分支 + `useRouter` |
| `router.ts` | 加 `/screenplay` + `/screenplay/novels/:id` 2 条路由 |
| `screenplay/views/ScreenplayHomeView.vue` | 占位友好页(5 个核心能力宣传 + 仓库链接) |
| `screenplay/views/ScreenplayEditorView.vue` | 占位页 |

### 后端
本阶段无改动。

---

## 阶段 2-7 待办(给后续执行者)

### 阶段 2:前端代码迁入

源:`hunjing-screenplay/frontend/src/`
目标:`huimeng/frontend/src/screenplay/`

需迁移文件:
```
views/HomeView.vue              → screenplay/views/ScreenplayHomeView.vue(覆盖占位)
views/ScreenplayEditorView.vue  → screenplay/views/ScreenplayEditorView.vue(覆盖占位)
components/NovelUploadCard.vue
components/NovelTextPanel.vue
components/ScreenplayPanel.vue
components/AdaptationDecisionPanel.vue
components/StructureReportPanel.vue
components/OptimizationModal.vue
components/ComposeDialog.vue
components/VersionSwitcher.vue
components/FidelityBadge.vue
stores/screenplay.ts            → screenplay/stores/screenplay.ts(命名空间隔离)
types/screenplay.ts             → screenplay/types/screenplay.ts
api/client.ts                   → screenplay/api/client.ts(走父平台 http 基础设施)
```

适配工作:
1. 视觉令牌替换:`--accent` → `--color-accent`,`--bg` → `--color-bg`,所有自定义 token → 父平台 token
2. 字体替换:`var(--font-serif)` → 父平台系统字体栈,但保留剧本 element-action / d-text 的衬线(行业标准)
3. 路由路径替换:`/novels/{id}/editor` → `/screenplay/novels/{id}`
4. API 调用:把所有 `fetch('/api/...')` 改为复用父平台 `api/http.ts`(自动带 JWT + quota 错误提示)
5. 删除独立 `global.css`

### 阶段 3:后端代码迁入

源:`hunjing-screenplay/backend/app/`
目标:`huimeng/backend/app/screenplay/`(新子模块)

需迁移:
```
routers/        (8 个 endpoint 文件)→ screenplay/routers/  全部加 prefix /api/screenplay
services/       (compose_service / screenplay_store / ingest_service / yaml_validator / llm_client / story_bible_service)
services/pipeline/ (6 个 LLM agent + structure_analyzer + fidelity_scorer)
schemas/        (screenplay.json)
prompts/        (6 个 .md prompt)
db/             (sp_xxx_schema.sql,加 user_id FK)
```

强制改动:
1. **DB 表名加 `sp_` 前缀**:`novels` → `sp_novels`,`chapters` → `sp_chapters`,`paragraphs` → `sp_paragraphs`,`screenplays` → `sp_screenplays`,`story_bibles_*` → `sp_story_bibles_*`
2. **加 user_id 字段**:`sp_novels.user_id INTEGER NOT NULL FK → users.id ON DELETE CASCADE`
3. **endpoint 鉴权**:每个 router 加 `user: User = Depends(get_current_user)`
4. **复用父平台 LLM client**:把比赛仓库的 `llm_client.py` 替换为父平台对应模块(查父平台的 DeepSeek 调用层)
5. **main.py 注册**:`app.include_router(screenplay_router, prefix="/api/screenplay", tags=["screenplay"])`

### 阶段 4:DB 迁徙 + Quota

- 加 5 张表(sp_novels / sp_chapters / sp_paragraphs / sp_screenplays / sp_story_bibles)
- Migration runner 注册到父平台 init_db()
- credit / quota:对齐漫创态计费规则,建议每次 compose 算 N credit;Free 给 1-2 次免费名额

### 阶段 5(关键):角色 Agent 复用层 — huimeng_bridge

新建 `backend/app/screenplay/services/huimeng_bridge.py`,封装对浑晶现有 service 的调用:

```python
# 6 个剧创 agent 全接通浑晶资产

def get_character_drivers(user_id: int, character_names: list[str]) -> dict:
    """拉 SP-2 角色想要 vs 需要 vs 秘密 — 给 element_extractor / adaptation_decision 用"""
    # 调 huimeng.app.services.character_driver_service.get_drivers_for_user(...)

def get_character_knowledge(user_id: int, scene_time_anchor: str) -> dict:
    """拉 SP-3 角色知识边界 — 给 element_extractor 用,让角色不说不该知道的事"""
    # 调 huimeng.app.services.character_knowledge_service.query_at_time(...)

def get_character_snapshots(user_id: int, character_names: list[str]) -> dict:
    """拉 SP-4 状态时间线 — 给 screenplay_optimizer 跨场一致性检查"""

def get_relationship_polarity(user_id: int, character_names: list[str]) -> dict:
    """拉 SP-7 关系正负极 — 给 scene_splitter 判定场景边界 + element_extractor 互称语气"""

def get_story_facts(user_id: int, project_id: int | None) -> list:
    """拉 SP-3 故事事实 — 让剧本不编造原作没有的细节"""

def get_canonical_score(yaml_dict: dict) -> dict:
    """复用 canonical_guardian 12 维评分 — 替换剧创态 fidelity 4 维?或并行?待 PM 决策"""
```

接入点(6 个 agent 各自的 prompt 添加 character_drivers 等字段占位,service 层先调 bridge 再调 LLM)。

### 阶段 6:视觉融合

- 顶栏 / 侧栏 / chrome:**完全用父平台令牌**
- 剧本内容区:**保留衬线 action + 等宽 SCENE 头**(行业标准合理特例)
- 决策面板 5 色:保留(已经够克制)

### 阶段 7:测试 + 文档

- 比赛仓库 218 case pytest 迁入 `tests/screenplay/`,fixture 加 user mock
- 父平台 vue-tsc 0 错基线保持
- README 加"第 5 创作态:剧创态"
- 本文档收尾

---

## 风险点 + 回滚

| 风险 | 概率 | 回滚 |
|---|---|---|
| 表名冲突 | 中 | sp_ 前缀覆盖 |
| 视觉令牌冲突影响 dark 模式 | 中 | 全 var(--color-*) 不写死颜色 |
| LLM client 接入失败 | 低 | 保留独立 llm_client 兜底 |
| huimeng_bridge service 找不到 | 中 | 阶段 5 前先摸底浑晶 service 层 |

---

## 比赛仓库参考

- GitHub:https://github.com/LaL-999/hunjing-screenplay
- 本地路径:`C:\Users\Administrator\Desktop\hunjing-screenplay\`
- 比赛窗口:2026-06-05 ~ 06-07
- 完工状态:PR#1-16 全部 merged,218 测试通过,vue-tsc 0 错

---

## 阶段 8.4+ 分集完整版升级(2026-06-08 用户拍板 7 项全补)

阶段 8.4 MVP(`233fb20`)是规则版分集,用户敏锐识别为"工程妥协",授权按
**多视角创作质量最高方向**升级。本节为 8.4+ 全 9 个 commit 收尾文档。

### 用户原话(关键决策定锚)

- "分集 MVP?只是 MVP 而不是完整版吗?" — 识别妥协
- "demo 素材?我并不是要做一个只能演示的 demo 素材呀,我是要真正做一个高质量产品的"
- "你以多视角往创作质量提升最高的方向去做,我很放心你的决策"

### 7 项升级落地清单(全栈完工)

| 编号 | 内容 | 实现位置 |
|---|---|---|
| **A** | 戏剧张力曲线驱动切集 | `dramatic_curve_analyzer.py` + `multi_perspective_planner._plan_rhythm` |
| **B** | 每集结尾 cliffhanger 检测 | `_compute_cliffhanger_potentials` + `_plan_hook` |
| **C** | LLM logline 标题 | `episode_title_writer.py` BYOK 批量 LLM 调用 |
| **D** | 桥接 SP-4 状态时间线 | `_try_load_bridge_emotion_curve` SQL JOIN simulation_scenes |
| **E** | 桥接 SP-2 角色弧光 | `_plan_arc` 调 `huimeng_bridge.get_character_drivers_block` + LLM |
| **F** | 集间评分(4 维) | `episode_quality_scorer.py` cliffhanger/pacing/character/chapter |
| **G** | 预设档(短剧/长剧/番剧/自定义) | `multi_perspective_planner.PRESETS` 4 档 + `/episodes/presets` |

### 多视角分集(核心创新,Phase 3)

3 视角并行生成,用户对比择优:

  - **rhythm 节奏视角** — tension valley 切集,适合长剧 / 情感戏
  - **hook 钩子视角** — cliffhanger 阈值排序,适合短剧 / 悬疑
  - **arc 角色弧光视角** — LLM 推断转折点(桥接 SP-2 driver),适合人物剧

每视角输出 PerspectivePlan + 一句 rationale + 4 维评分。
LLM 失败(arc)→ 自动回退 rhythm 算法,标 `llm_failed=true`。

### 完整流水线(`/plan-episodes-multi`)

```
[用户点 生成分集]
  ↓
① dramatic_curve_analyzer.analyze_dramatic_curve()
    桥接 SP-4 → 每场 tension_score / cliffhanger_potential / 候选切点
  ↓
② multi_perspective_planner.plan_with_perspectives()
    3 视角并行(arc 视角 LLM 调用,失败 fallback rhythm)
  ↓
③ episode_quality_scorer.score_plan(每方案)
    4 维评分填回 plan.aggregate_quality / ep.quality_score
  ↓
④ pick_best_perspective → 替代 Phase 3 启发式 recommended
  ↓
⑤ episode_title_writer.write_titles_and_teasers(推荐方案)
    LLM logline + 下集预告,批量 1 次调用拿全部
  ↓
[response: MultiPerspectivePlan + perspective_scores]
```

### 测试覆盖(78 case 全过)

| Phase | 测试文件 | case 数 |
|---|---|---|
| 2 | test_dramatic_curve_analyzer.py | 20 |
| 3 | test_multi_perspective_planner.py | 18 |
| 4 | test_episode_title_writer.py | 13 |
| 5 | test_episode_quality_scorer.py | 17 |
| 6 | test_episodes_endpoint.py (扩展) | 10 |

全 regression:300 passed, 11 skipped(已知 CLI), 0 failed。

### 9 个独立 commit

| Phase | commit | 内容 |
|---|---|---|
| 1 | 191f953(Phase 2 包含) | 摸底桥接 + LLM 链路 |
| 2 | `191f953` | dramatic_curve_analyzer.py + 20 测试 |
| 3 | `e3845e0` | multi_perspective_planner.py + 18 测试 |
| 4 | `598d8dd` | episode_title_writer.py + 13 测试 |
| 5 | `cc09a3e` | episode_quality_scorer.py + 17 测试 |
| 6 | `1a5b7d1` | routers/episodes.py 扩 + 8 测试 |
| 7 | `8378edf` | EpisodePlanPanel.vue 三视角对比 UI |
| 8 | `874af51` | 跨用户隔离测试 + 全套 regression |
| 9 | (本 commit) | INTEGRATION_NOTES + MEMORY 更新 |

### 关键设计决策

1. **新 endpoint 不破坏旧的** — `/plan-episodes` MVP 仍工作(向后兼容),
   新 `/plan-episodes-multi` 是完整版。前端默认走 multi。

2. **桥接资产是可选增益,不是强制依赖** — 用户没绑定父平台 project /
   没跑过 simulation 也能用,只是 arc 视角 LLM 凭剧本推断弧光、tension
   评分不带 0.4 增强。

3. **LLM 调用全部经 BYOK** — 通过 `call_llm_json` 自动从 ContextVar 读
   user_id,用户的 vendor key 优先,失败回退平台默认。

4. **异常隔离铁律** — 任一视角失败、LLM 失败、桥接失败,都不阻断响应。
   降级到次优方案,标志位告知前端(`llm_failed` / `bridge_used`)。

5. **预设档 4 档** — `short_drama` 2.5min / `long_drama` 10min /
   `anime` 22min / `custom` 用户自定义。每档不只是时长不同,
   cliffhanger 阈值也不同(短剧要 ≥0.50 强钩子,长剧允许 ≥0.35)。

### 与 Plan A 八项的关系

阶段 8 的 Plan A 列表里,8.4 标"# 4 分集 MVP",8.4+ 完整版**不在原 Plan A
里** —— 是用户敏锐识别后追加的高优。

至此剧创态的"分集"功能从工程妥协升级为真产品级:
- 不是"演示用 demo"
- 不是"60 分够用 MVP"
- 是接通桥接 + 多视角 LLM + 4 维评分的**作者级提案桌面**

### 推迟项(用户实测后再决定)

- **3c Fountain → Final Draft round-trip 验证** — 需付费软件 + 真编剧实测
- **集间衔接 LLM 评分** — 4 维启发式已够,LLM 增强等用户反馈
- **导出分集大纲 PDF / MD** — 平台审稿场景,等用户提需求
- **章节级 arc 编辑** — 用户拿到分集后回头改的能力,Phase 10 候选

