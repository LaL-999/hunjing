# 剧创态并入浑晶 · 迁徙工作笔记

> Sprint SP-S(2026-06-07)— 把比赛仓库 `hunjing-screenplay` 整合为浑晶第 5 创作态。
> 当前状态:**阶段 1/7 完成**(Dashboard 入口 + 路由占位)。

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
| **5.2-5.7** | 6 agent 逐个接通 SP-2/3/4/7 资产 | ⏳ 接力中 |
| 6 | 视觉融合(精修)| ⏳ |
| 7 | 测试 + 文档收尾 | ⏳ |

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
