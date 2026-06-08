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
| 3 | 后端代码迁入(加 sp_ 前缀 + user_id) | ⏳ |
| 4 | DB 迁徙 + Quota 接入 | ⏳ |
| **5** | 故事圣经 A 隔离 + 角色 Agent 复用层(关键) | ⏳ |
| 6 | 视觉融合(精修)| ⏳ |
| 7 | 测试 + 文档收尾 | ⏳ |

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
