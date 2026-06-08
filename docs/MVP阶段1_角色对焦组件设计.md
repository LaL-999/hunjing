# MVP 阶段 1:角色对焦(Character Focus)组件设计

最后更新:2026-05-09 / Day 5
关联:`docs/MVP阶段1_初始态流程图.md` 节点 7

---

## 1. 组件北极星

**这一个组件,是浑晶产品差异化的灵魂**(doc 5 原话:意难平用户最需要"我心中的版本被认真对待",不是"客观还原")。

它的存在意义,不是"AI 帮用户写档案"——这件事任何 GPT 套壳都能做。它的存在意义是把"创作前的繁琐功课"翻译成"对 AI 草稿的快速审阅",让用户在 5 分钟内,把自己创作的角色从"60 分稀疏档案"调到"90 分可续写档案",且整个调法是用户主动的——**用户自己一条条决定接受 / 拒绝 / 改写**。

完成时,系统给用户看的最后一句话是:

> "这是你心中的《[项目名]》。它和别人心中的不一样。"

这句话不是文案修辞,是产品哲学的兑现。

---

## 2. 在 MVP 流程中的位置

```
[节点 6 空白图谱编辑器]
        ↓ 用户点"启动续写",检测角色数 ≥ 3
        ↓ ★ 7  弹出 CharacterFocus 模态(全屏覆盖)
[节点 8 续写设置 + 配额确认]
```

**触发条件**:
- 主触发:用户在编辑器里点"启动续写" → 检查角色数 ≥ 3 → 自动弹
- 副触发:用户在 EditorToolbar 主动点"帮我审视这些角色" → 不需要满足角色数门槛(但 < 3 时弹"再创建几个角色,AI 才能给出有意义的建议")
- 跳过路径:用户点"暂时跳过" → emit skip(伴随质量警告"你的角色档案可能稀疏,生成质量受影响")

**触发频次**:
- 阶段 1 MVP:每次启动续写都触发(可跳过,但会做温和提示)
- 阶段 2 起优化:同一项目内,如果上次对焦后没新增/修改角色,自动跳过(状态记 `last_refined_at` vs `characters_updated_at`)

---

## 3. 用户旅程(精确到屏)

```
屏 1  loading      "AI 正在审视你创建的 N 个角色…"
                   预计 5-15s,水晶 spinner + 滚动文案
                   ↓ 后端 LLM 完成
屏 2  reviewing    主屏幕:卡片流,顶部进度条 (3/15)
                   每张卡片显示:
                     - 顶部:角色头像色块 + 角色名 + 建议类型 chip
                     - 中部:suggestion_text(1 句话,≤ 70 字)
                     - 底部:三个按钮:✓ 采纳 / ✕ 不采纳 / ✎ 改一改
                   用户处理一张 → 卡片淡出 + 下一张滑入
                   底部:跳过剩余 / 全部完成
                   ↓ 全部处理完
屏 3  done         全屏粒子动画 + 一行字:
                   "这是你心中的《[项目名]》。它和别人心中的不一样。"
                   下方按钮:进入续写
                   ↓ 用户点击
                   emit complete → 父组件进入续写设置 dock
```

错误旁支:
```
屏 1.1 error       LLM 失败 / 超时:
                   "AI 累了,但你创建的角色已经够好,可以直接续写"
                   按钮:跳过对焦 / 重试
```

---

## 4. LLM Prompt 草稿(可直接用)

存放路径:`prompts/character_focus.md`,版本号 v1。

```markdown
# 角色对焦助手 · System Prompt v1

你是浑晶平台的"角色对焦助手"。用户在浑晶里**从零创建**了一个虚构作品的角色集合(没有原作),
你的任务是帮 ta 把每个角色的档案从"60 分草稿"调到"90 分可用",但你必须遵守以下边界:

- 你不是替用户创作角色,你是替用户**审视**角色
- 你的每条建议都必须基于"用户已经填的内容"做合理推演,**禁止凭训练数据虚构**
- 你的建议要让用户能在 12 秒内决定接受 / 拒绝 / 改写

---

## 输入(运行时由后端注入)

```json
{
  "project": {
    "name": "<项目名>",
    "type": "novel|comic|anime|generic",
    "tags": ["<题材标签数组>"]
  },
  "characters": [
    {
      "id": "<uuid>",
      "name": "<角色名>",
      "identity": "<可空,身份描述>",
      "personality": "<可空,性格描述>",
      "quotes": ["<可空,标志性原话数组>"],
      "no_go_list": ["<可空,雷区数组>"]
    }
  ],
  "relationships": [
    {
      "source_id": "<uuid>",
      "target_id": "<uuid>",
      "type": "亲属|敌对|朋友|情侣|师徒|同事|其他",
      "description": "<关系简述>"
    }
  ]
}
```

---

## 任务

对每个角色生成 3-5 条建议。**每条建议必须严格属于以下五类之一**:

### 类型 1:identity_补全
触发条件:用户填了 name,但 identity 空 OR < 8 字
你做什么:基于其他线索(personality / 关系 / 引言 / 项目题材)给出 **1 个**合理身份候选(不要给多个,选最契合的)
suggestion_text 模板:"你给【姓名】的身份是空的。基于【线索】,我建议补:【候选身份】"
suggestion_payload 形态:`{ "field": "identity", "value": "<候选身份>" }`

### 类型 2:personality_补充
触发条件:personality 描述 < 20 字 OR 仅 1 个特质 OR 全是正面/负面词(扁平化)
你做什么:补 1-2 个互补特质,避免"维基百科扁平化"
suggestion_text 模板:"【姓名】的性格只写了【现有】,显得单薄。建议补:【新特质】(理由)"
suggestion_payload 形态:`{ "field": "personality", "append": "<补充文本>" }`

### 类型 3:quote_补充
触发条件:quotes 数组为空 OR 全部 < 8 字
你做什么:基于 personality + identity 编 1 句最能体现该角色的"标志性原话"(不要 5 句、不要 3 句,**就 1 句**)
suggestion_text 模板:"【姓名】没有标志性原话。基于性格【X】,这句最能立 ta:"【原话】""
suggestion_payload 形态:`{ "field": "quotes", "append": ["<新原话>"] }`

### 类型 4:no_go_补充
触发条件:no_go_list 为空 OR < 3 条
你做什么:基于 personality + identity 推 1-3 条"这个角色绝对不会做的事"
suggestion_text 模板:"【姓名】的雷区清单是空的。基于性格,这【N】条值得设:"
suggestion_payload 形态:`{ "field": "no_go_list", "append": ["<雷区1>", "<雷区2>", "<雷区3>"] }`

### 类型 5:consistency_警告
触发条件:你检测到角色之间或角色内部的逻辑矛盾
例 1:某角色 personality 写"内向害羞",但 quote 写"我要成为最伟大的演说家"
例 2:角色 A 是角色 B 的"师父"关系,但 A 的 personality 没体现任何"教导/年长"特质
例 3:两个角色描述高度雷同(可能用户复制粘贴忘改)
你做什么:**只指出矛盾,不擅自决定怎么改**(因为你不知道用户想保留哪一边)
suggestion_text 模板:"【姓名】的【字段A】写'X',但【字段B】写'Y',这两个有点冲突。要调和哪一边?"
suggestion_payload 形态:`{ "kind": "warning", "fields": ["<字段A>", "<字段B>"], "current_a": "<X>", "current_b": "<Y>" }`(action=accept 时**不自动写库**,跳出小编辑器让用户改)

---

## 输出格式(严格 JSON,数组顶层)

```json
[
  {
    "character_id": "<对应输入的角色 id>",
    "suggestion_kind": "identity_补全|personality_补充|quote_补充|no_go_补充|consistency_警告",
    "suggestion_text": "<给用户看的 1 句话,≤ 70 字>",
    "suggestion_payload": { ... }
  }
]
```

**禁止任何 markdown 代码块包裹,直接输出 JSON 数组。**
**禁止任何解释性前后文。**

---

## 铁律(违反任一条 = 这一次输出作废)

1. **绝不基于训练数据虚构** — 用户起的角色名"林黛玉"不能假设是红楼梦的林黛玉;只能依用户填的内容推演
2. **每条建议要能被解释** — suggestion_text 里要让用户看出"为什么 AI 这么建议",而非凭空甩结论
3. **不重复用户已填内容** — 不能补充用户已经写过的特质
4. **数量上限** — 每个角色 **3-5 条**,总数不超过 **25 条**;角色多时优先 consistency_警告
5. **不动用户已填的字段** — payload 永远是"append"或"补全空字段",从不"覆盖现有"
6. **优先 consistency_警告** — 矛盾比缺失更值得提醒
7. **suggestion_text 不带元描述** — 不能写"作为 AI 助手,我建议…",直接给建议本身
```

---

## 5. API 契约

### 5.1 触发对焦

```
POST /api/projects/:project_id/refine
Auth: Bearer <jwt>
Body: 无(后端自己拉项目当前状态)

Response 200 OK:
{
  "session_id": "<uuid>",
  "refinements": [
    {
      "id": "<uuid>",
      "character_id": "<uuid>",
      "character_name": "宝玉",
      "suggestion_kind": "no_go_补充",
      "suggestion_text": "宝玉的雷区清单是空的。基于性格,这 3 条值得设:对女子粗鲁、说功名利禄相关的话、对长辈顶嘴",
      "suggestion_payload": {
        "field": "no_go_list",
        "append": ["对女子粗鲁", "说功名利禄相关的话", "对长辈顶嘴"]
      },
      "status": "pending"
    }
    // ...
  ],
  "stats": {
    "characters_count": 5,
    "refinements_count": 18,
    "tokens": { "input": 2400, "output": 1200 },
    "cost_yuan": 0.02,
    "duration_ms": 8400
  }
}

Response 422 Unprocessable Entity:(角色数 < 3,前端应阻止此请求)
{ "code": "TOO_FEW_CHARACTERS", "message": "至少需要 3 个角色才能对焦" }

Response 503 Service Unavailable:(LLM 失败,已重试 1 次)
{ "code": "LLM_UNAVAILABLE", "message": "AI 累了,稍后重试或跳过对焦" }
```

后端伪代码:
```python
def refine(project_id, user_id):
    project = db.get_project(project_id, user_id)
    characters = db.list_characters(project_id)
    if len(characters) < 3:
        return 422, "TOO_FEW_CHARACTERS"

    relationships = db.list_relationships(project_id)
    prompt_input = build_prompt_input(project, characters, relationships)
    system_prompt = load_prompt("character_focus.md")

    try:
        llm_output = call_deepseek(
            system=system_prompt,
            user=json.dumps(prompt_input, ensure_ascii=False),
            max_tokens=4000,
            temperature=0.6
        )
    except (LLMTimeout, LLMError):
        # 重试 1 次
        try:
            llm_output = call_deepseek(...)
        except:
            return 503, "LLM_UNAVAILABLE"

    refinements = parse_and_validate_json(llm_output)
    if not refinements:
        return 503, "LLM_UNAVAILABLE"

    session_id = uuid()
    for r in refinements:
        db.insert_refinement(session_id, project_id, r, status='pending')

    return 200, {...}
```

### 5.2 处理一条建议

```
POST /api/refinements/:refinement_id/action
Auth: Bearer <jwt>
Body:
{
  "action": "accept" | "reject" | "edit",
  "user_edit": <仅当 action=edit 时,与 suggestion_payload 同 schema>
}

Response 200 OK:
{
  "id": "<refinement_id>",
  "status": "accepted" | "rejected" | "edited",
  "applied_to_character": <bool, accept/edit 时为 true,reject 时 false>,
  "character_after": <Character 完整对象,仅当 applied_to_character=true>
}
```

后端伪代码(accept/edit 时的写库逻辑):
```python
def apply_refinement(refinement, action, user_edit=None):
    payload = user_edit if action == 'edit' else refinement.suggestion_payload
    char = db.get_character(refinement.character_id)

    if payload.get('kind') == 'warning':
        # consistency_警告 不自动写库,需要 user_edit
        if action != 'edit':
            return  # accept warning 但没给 user_edit,空操作
        # 应用 user_edit 到指定 fields
        for field, value in user_edit.get('fields', {}).items():
            setattr(char, field, value)
    elif 'value' in payload:
        # identity_补全 等"覆盖空字段"型
        setattr(char, payload['field'], payload['value'])
    elif 'append' in payload:
        # personality_补充 / quote_补充 / no_go_补充
        existing = getattr(char, payload['field']) or ('' if isinstance(payload['append'], str) else [])
        if isinstance(payload['append'], str):
            new_value = (existing + ' ' + payload['append']).strip() if existing else payload['append']
        else:
            new_value = list(existing) + list(payload['append'])
        setattr(char, payload['field'], new_value)

    db.update_character(char)
    db.update_refinement(refinement.id, status=action)
```

### 5.3 跳过整个 session

```
POST /api/refine_sessions/:session_id/skip
Auth: Bearer <jwt>
Body: { "reason": "user_skipped" | "llm_failed" }

Response 200 OK:
{ "skipped": true, "remaining_refinements": <int, 标记为 'skipped'> }
```

---

## 6. 前端组件契约

### 6.1 CharacterFocus.vue

```typescript
interface Props {
  projectId: string;
  /** 主触发 vs 副触发,影响初始 UI(主触发跳"启动续写",副触发跳"返回编辑器") */
  trigger: 'pre_generation' | 'manual';
  /** 上游已经传入的角色和关系(避免后端拉两次) */
  charactersSnapshot: Character[];
  relationshipsSnapshot: Relationship[];
}

interface Emits {
  /** 全部处理完(包括"跳过剩余"),返回处理后的角色 id 列表 */
  (e: 'complete', refinedCharacterIds: string[]): void;
  /** 用户点了"跳过对焦"(整体跳过) */
  (e: 'skip', reason: 'user_skipped' | 'llm_failed'): void;
  /** 用户关闭模态(回到编辑器,不进续写) */
  (e: 'close'): void;
}
```

### 6.2 内部状态

```typescript
type Phase = 'loading' | 'reviewing' | 'done' | 'error';

const phase = ref<Phase>('loading');
const sessionId = ref<string | null>(null);
const refinements = ref<Refinement[]>([]);
const currentIndex = ref(0);  // 当前展示第几条
const stats = ref<RefineStats | null>(null);
const errorMessage = ref<string | null>(null);
```

### 6.3 关键计算属性

```typescript
const currentRefinement = computed(() => refinements.value[currentIndex.value]);
const progress = computed(() => `${currentIndex.value + 1} / ${refinements.value.length}`);
const allDone = computed(() => currentIndex.value >= refinements.value.length);
const refinedCharacterIds = computed(() =>
  Array.from(new Set(
    refinements.value
      .filter(r => r.status === 'accepted' || r.status === 'edited')
      .map(r => r.character_id)
  ))
);
```

### 6.4 关键方法

```typescript
async function handleAction(action: 'accept' | 'reject' | 'edit', userEdit?: any) {
  const r = currentRefinement.value;
  if (!r) return;

  try {
    await api.post(`/refinements/${r.id}/action`, { action, user_edit: userEdit });
    r.status = action === 'edit' ? 'edited' : action;
    currentIndex.value++;
    if (allDone.value) phase.value = 'done';
  } catch (err) {
    // 单条失败不阻塞整体,本地标记为 reject 跳过
    r.status = 'rejected';
    currentIndex.value++;
  }
}

async function skipRemaining() {
  await api.post(`/refine_sessions/${sessionId.value}/skip`, { reason: 'user_skipped' });
  phase.value = 'done';
}

function onComplete() {
  emit('complete', refinedCharacterIds.value);
}
```

---

## 7. 状态机

```
              [props 进入,phase=loading]
                       │
                       ▼
              POST /projects/:id/refine
                       │
            ┌──────────┴──────────┐
            │                     │
       成功 │                     │ 失败(已重试 1 次)
            ▼                     ▼
    [phase=reviewing]       [phase=error]
            │                     │
            │                     ├─ 用户点"跳过对焦" → emit skip(llm_failed)
            │                     └─ 用户点"重试" → 回到 loading
            │
            ├─ 用户处理一条 → currentIndex++
            │
            ├─ 用户点"跳过剩余" → POST /refine_sessions/:sid/skip
            │                    → phase=done
            │
            └─ 全部处理完(currentIndex >= refinements.length)
                       │
                       ▼
              [phase=done]
                       │
                       ├─ 用户点"进入续写" → emit complete
                       └─ 用户点 X 关闭 → emit close
```

---

## 8. 数据模型(SQL DDL,可执行)

```sql
-- 一次对焦会话(每次触发对焦生成一条)
CREATE TABLE refine_sessions (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL REFERENCES users(id),
    triggered_at    TEXT NOT NULL,                       -- ISO 8601
    completed_at    TEXT,                                 -- NULL = 未完成
    skip_reason     TEXT,                                 -- NULL / user_skipped / llm_failed
    tokens_input    INTEGER,
    tokens_output   INTEGER,
    cost_yuan       REAL,
    duration_ms     INTEGER
);

-- 每条具体建议
CREATE TABLE character_refinements (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES refine_sessions(id) ON DELETE CASCADE,
    character_id        TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    suggestion_kind     TEXT NOT NULL CHECK (suggestion_kind IN
                          ('identity_补全','personality_补充','quote_补充',
                           'no_go_补充','consistency_警告')),
    suggestion_text     TEXT NOT NULL,
    suggestion_payload  TEXT NOT NULL,                    -- JSON
    status              TEXT NOT NULL DEFAULT 'pending'   -- pending/accepted/rejected/edited/skipped
                          CHECK (status IN ('pending','accepted','rejected','edited','skipped')),
    user_edit           TEXT,                              -- JSON,仅 status='edited' 时有
    actioned_at         TEXT
);

CREATE INDEX idx_refine_session_project ON refine_sessions(project_id);
CREATE INDEX idx_refine_status ON character_refinements(session_id, status);
```

---

## 9. 性能预算

### 9.1 LLM 调用成本(单次对焦)

| 项 | 估算 |
|---|---|
| Input token | 5 角色 × ~80 字 + 项目元数据 + system prompt ≈ **2,400 token** |
| Output token | 25 条建议 × ~60 字 ≈ **1,500 token** |
| DeepSeek V3 单价 | $0.27 / M input,$1.10 / M output |
| 单次成本 | $0.0019 + $0.00165 ≈ **¥0.025 / 次对焦** |

**结论**:成本可忽略,放心做。

### 9.2 时间预算

| 阶段 | 时长 |
|---|---|
| 后端调 LLM(DeepSeek V3 流式) | 5-15s |
| 用户审阅 25 条 × 12s/条 | ~5 分钟 |
| 后端写库 + 状态切换 | < 100ms |

**总用户耗时**:约 **5 分钟**(对齐 doc 5 的预期)

### 9.3 配额计费

- **角色对焦不单独计配额**(避免用户对"为什么对焦也算钱"反感)
- 成本含在"完整推演"配额里(单次推演 ≤ 30 元目标里,~¥0.025 占比 0.08%,可吸收)
- **但要在 UsageLog 表里独立记一行**,便于后续做成本分析

---

## 10. 错误态处理(全集)

| 错误来源 | 后端响应 | 前端表现 | 用户出口 |
|---|---|---|---|
| 角色数 < 3 | 422 TOO_FEW_CHARACTERS | 应在前端预拦截,不应走到这里 | "再创建几个角色"提示 |
| LLM 调用超时 30s | 自动重试 1 次 | 持续 loading | — |
| LLM 重试后仍失败 | 503 LLM_UNAVAILABLE | phase=error 屏 | 跳过对焦 / 重试 |
| LLM 返回非合法 JSON | 后端解析,失败时 503 | phase=error 屏 | 跳过对焦 / 重试 |
| LLM 返回 0 条建议 | 200,refinements=[] | 直接跳到 phase=done(不展示空 reviewing) | 进入续写 |
| 单条 action API 失败 | 500 | 该条本地标 reject,自动 next | — |
| 用户中途关闭浏览器 | 已 actioned 的不回滚 | 下次进入同项目时,refine_session 状态 = 未完成 → 提示"上次对焦没完成,继续吗?" | 继续 / 重新对焦 |
| 用户中途新增/修改角色 | refine_session 标记为 stale | 下次启动续写时,**强制重新对焦**(因为旧建议针对的是旧角色集合) | — |

---

## 11. 与其他组件的边界

### 11.1 上游 — ProjectGraph(节点 6)

```vue
<!-- views/ProjectGraph.vue -->
<template>
  <CrystalGraph editable :data="..." />
  <EditorToolbar @start-generation="onStartGeneration" />

  <CharacterFocus
    v-if="focusOpen"
    :project-id="projectId"
    trigger="pre_generation"
    :characters-snapshot="characters"
    :relationships-snapshot="relationships"
    @complete="onFocusComplete"
    @skip="onFocusSkip"
    @close="focusOpen = false"
  />
</template>

<script setup>
async function onStartGeneration() {
  if (characters.value.length < 3) {
    showToast("再创建几个角色,AI 才能给出有意义的对焦建议");
    return;
  }
  focusOpen.value = true;
}

function onFocusComplete(refinedCharacterIds) {
  focusOpen.value = false;
  // 重新拉一次角色数据(因为对焦过程改了字段)
  await reloadCharacters();
  // 进入续写设置 dock
  showGenerationDock.value = true;
}

function onFocusSkip(reason) {
  focusOpen.value = false;
  if (reason === 'user_skipped') {
    showToast("跳过对焦,你的角色档案可能稀疏,生成质量受影响");
  }
  showGenerationDock.value = true;
}
</script>
```

### 11.2 下游 — 续写设置 dock(节点 8)

CharacterFocus emit complete 后,父组件直接打开续写设置 dock。两者**无直接依赖**(对焦只对角色数据有副作用,对续写流程无 props 传递)。

### 11.3 旁路 — UsageLog

```python
# 后端在每次 refine 调用结束时写入
db.insert_usage_log(
    user_id=user_id,
    action='refine',
    quota_consumed=0,  # 不算配额
    cost_yuan=session.cost_yuan,
    metadata={'session_id': session.id, 'refinements_count': len(refinements)}
)
```

---

## 12. 测试场景(端到端,真实数据,不 mock)

### 场景 A:稀疏角色 — 用户只填了名字
**输入**:
```json
{
  "project": { "name": "江湖夜雨", "type": "novel", "tags": ["武侠", "悬疑"] },
  "characters": [
    { "id": "c1", "name": "李寻欢", "identity": "", "personality": "", "quotes": [], "no_go_list": [] },
    { "id": "c2", "name": "孙小红", "identity": "", "personality": "", "quotes": [], "no_go_list": [] },
    { "id": "c3", "name": "上官金虹", "identity": "", "personality": "", "quotes": [], "no_go_list": [] }
  ],
  "relationships": []
}
```
**预期输出**:
- 每个角色至少 3 条建议
- 主要类型:identity_补全 + personality_补充 + no_go_补充
- 不应出现 consistency_警告(因为没填够多东西可冲突)
- **铁律检查**:LLM 不能假设"李寻欢"是古龙的小李飞刀;只能说"基于项目题材是武侠 + 悬疑,我建议……"

### 场景 B:饱满角色 — 用户填得详尽
**输入**:5 个角色都填了 identity (>30 字) + personality (>30 字) + 5 quotes + 5 no_go_list
**预期输出**:
- 总建议数 ≤ 10 条(因为大部分字段已填够)
- 主要类型:personality_补充(指出可能的扁平化)+ consistency_警告
- 单角色 ≤ 2 条

### 场景 C:有内部矛盾 — 用户填的角色档案自相矛盾
**输入**:
```json
{
  "characters": [
    {
      "id": "c1",
      "name": "甲",
      "personality": "内向害羞,从不主动开口",
      "quotes": ["我要成为最伟大的演说家!", "听好了,这是我对你的最后通牒"]
    }
  ]
}
```
**预期输出**:
- 必出 1 条 consistency_警告,suggestion_text 指出 personality 与 quotes 矛盾
- payload.kind = "warning",fields = ["personality", "quotes"]

### 场景 D:有关系网 — 用户填了角色 + 关系
**输入**:5 角色 + 8 关系,其中 A 是 B 的"师父",但 A 的 personality 没体现教导/年长特质
**预期输出**:
- 至少 1 条 consistency_警告关于"A 是师父但 personality 不像师父"
- 也可能出 personality_补充建议给 A 加"沉稳/年长"特质

### 场景 E:LLM 返回非法 JSON
**模拟方法**:在测试环境注入一个返回乱码的 mock LLM client
**预期**:后端 503,前端 phase=error,跳过/重试按钮可用

### 场景 F:用户中断
**操作**:用户处理 5 条后关闭浏览器,下次重新进同项目并启动续写
**预期**:
- refine_session 状态 = 未完成
- 前端弹"上次对焦没完成,继续上次的还是重新对焦?"

### 场景 G:角色被修改后再触发
**操作**:用户对焦完一次 → 在编辑器里改了角色 c2 的 personality → 再次启动续写
**预期**:
- 旧 refine_session 标记为 stale
- 强制开新一次对焦(因为旧建议过期)

---

## 13. MVP 取舍清单

### ✅ 阶段 1 做
- 5 类建议(identity / personality / quote / no_go / consistency)
- 二元 accept / reject + edit 三选项
- 单次对焦(一次处理完所有角色)
- 跳过路径(整体跳过 + 单条跳过)
- 中断恢复(refine_session 持久化)

### ❌ 阶段 1 不做(留 phase 2 / 3)
- **严格度滑块**(绝对不会 / 通常不会 / 偶尔不会) — 留 phase 2
- **增量对焦**(每填一个角色就触发一次) — 留 phase 2,需要状态机更复杂
- **对焦的多轮迭代**(用户改完后再对焦一次) — 留 phase 2
- **跨项目的"我的角色档案库"** — 留 phase 3
- **对焦质量的反向评分**(用户给 AI 建议打分) — 留 phase 3
- **正典守护者**(只对中间态有意义,初始态用"自洽守护者"由 simulate.py 内嵌处理,不进对焦组件) — 阶段 2 做

---

## 14. 潜在踩坑(开工前必读)

1. **LLM JSON 解析失败** — 参考 V5 锁定时踩过的坑(JSON 9783 字 / max_tokens=4000 截断)。25 条建议 ≤ 1500 token,max_tokens=4000 足够 2.6 倍裕度。但**必须做 JSON 验证 + 失败时不返回前端**(降级为"未生成建议",让用户直接续写)。

2. **用户填得极端稀疏(只有 1 个角色)** — 前端预拦截角色数 < 3 不触发,提示"再创建几个角色"。

3. **AI 建议被认为"不准/被冒犯"** — 文案要委婉。例如:
   - ❌ "你的描述太单薄"
   - ✅ "你可以补充这几条,角色会更立体"

4. **suggestion_text 长度失控** — Prompt v2 硬性要求 ≤ 70 字(v1 60 字过严,模板天然就接近 60 字,LLM 经常超 1-5 字)。前端做 fallback 截断到 80 字显示(再宽一点裕度)。

5. **同一角色的多条建议字段冲突** — 例如同时给 personality 出"补充 A"和"警告 personality 与 quotes 矛盾"。后端在收到 LLM 输出后,应**对每个 character_id 限制最多 1 条 consistency_警告 + 最多 4 条补充类**,避免冲突。

6. **跳过对焦后续写质量明显下降** — 阶段 1 不做"对焦版 vs 未对焦版"的差异化标记;阶段 2 在 narrative 元数据里加一个 `was_focused: bool` 用于内部分析。

7. **中文 quote 里的引号转义** — LLM 输出 quote 时常用全角引号 `""`,前端展示用 `<span>"{{ q }}"</span>` 要做去重(避免 `""""text""""` 这种叠加)。

---

## 15. 落地清单(从这份文档到代码)

### 后端
- [ ] `prompts/character_focus.md` v1(从本文 §4 复制)
- [ ] `migrations/00X_create_refine_tables.sql`(从本文 §8 复制)
- [ ] `app/services/character_focus.py`(实现 §5.1 / §5.2 / §5.3 三个 endpoint)
- [ ] `tests/test_character_focus.py`(场景 A-G 共 7 个端到端测试)

### 前端
- [ ] `src/components/CharacterFocus.vue`(主组件)
- [ ] `src/components/CharacterFocusCard.vue`(单条建议卡片)
- [ ] `src/composables/useRefineSession.ts`(API 封装 + 状态管理)
- [ ] `src/types/refinement.ts`(类型定义)
- [ ] 接入 `views/ProjectGraph.vue`(从本文 §11.1 复制)

### 集成验收(全部勾完才算阶段 1 该组件完成)
- [ ] 场景 A 在 DeepSeek V3 上跑通,产出 ≥ 9 条合理建议
- [ ] 场景 C 必出 consistency_警告
- [ ] 场景 E 不让前端崩,有降级 UX
- [ ] 场景 F 中断恢复正确
- [ ] 单次对焦总耗时 ≤ 6 分钟(后端 ≤ 15s + 用户审阅 ≤ 5 分钟 + 切换 ≤ 30s)
- [ ] 单次对焦成本 ≤ ¥0.05

---

## 16. 已知 LLM 行为漂移与后端兜底(P0,必须实现)

prompts/character_focus.md v1 → v3 三轮 verify(2026-05-09,总成本 ¥0.07)证实:
**LLM 行为漂移本质上无法靠 prompt 彻底封住**。即使写 50 个反例 + 加最激进的"绝不"
措辞,LLM 仍可能在第 51 个未覆盖反例上漂移。已锁定 v3 为 prompt 终版,转用**后端
代码兜底**(机制比 prompt 更稳)。

### 16.1 ⚠ 三类已知漂移模式

**模式 A:identity 字段 value 覆盖非空**
- v2 verify 真实案例:用户 `identity="新生代调查记者,擅长社会工程学"`(15 字),
  LLM 截取"调查记者"4 字误判为字数不足,产出 `{field: "identity", value: ...}`
  覆盖了用户已填的完整 identity
- v3 prompt 已严格化触发条件为"完全空字符串",但 v3 verify 仍出现:用户
  `identity="甲的发小"`(4 字),LLM 仍误判触发,**suggestion_text 还自陈"是空的"**

**模式 B:已知作品角色名 → 原作专属词漂移**
- v3 verify 真实案例:用户起角色名"孙小红"(古龙小说人物),LLM 在 identity 推演
  里写出"客栈老板娘,消息灵通的江湖**百晓生**"——百晓生是古龙系列专属概念
- v2/v3 铁律 9 反例表只覆盖了 7 个角色(李寻欢/雪诺/宝玉/鸣人/哈利/佐助/福尔摩斯),
  未覆盖到孙小红 → LLM 在缝里漂移

**模式 C:语义自相矛盾**
- v3 verify 真实案例:LLM 的 `suggestion_text` 写"你给乙的身份是空的",但乙的
  identity 输入实际是"甲的发小"(非空)。LLM 自己说的话与自己看到的输入矛盾

### 16.2 ⚠ 后端必须实现的 3 个兜底(对接 §5 的 apply_refinement)

**兜底 ① 铁律 5 硬实施(防模式 A)**

```python
def apply_refinement(refinement, action, user_edit=None):
    payload = user_edit if action == 'edit' else refinement.suggestion_payload
    char = db.get_character(refinement.character_id)

    # 兜底 ①:value 覆盖非空字段 → 拒绝
    if "value" in payload and "field" in payload:
        field = payload["field"]
        existing = getattr(char, field, "")
        if existing and str(existing).strip():
            raise HTTPException(422, code="REFINEMENT_WOULD_OVERWRITE",
                detail=f"建议会覆盖用户已填的 {field}={existing[:20]!r},"
                       f"已拒绝执行。请改用 consistency_警告 或不出此建议。")
    # ... 后续正常应用逻辑
```

**兜底 ② 已知作品专属词拦截(防模式 B)**

```python
# 在 refine 服务里,LLM 输出后、落库前做一遍过滤
KNOWN_WORK_RESERVED_TERMS = {
    "李寻欢": ["飞刀", "兵器谱", "林诗音", "百晓生", "金钱帮", "上官金虹"],
    "孙小红": ["百晓生", "李寻欢", "金钱帮"],
    "上官金虹": ["金钱帮", "李寻欢", "百晓生"],
    "雪诺": ["守夜人", "长城", "临冬城", "私生子", "异鬼", "瑟曦", "丹妮"],
    "宝玉": ["通灵宝玉", "大观园", "怡红院", "绛珠草"],
    "鸣人": ["忍者", "火影", "九尾", "木叶", "查克拉", "佐助"],
    "哈利波特": ["魔杖", "霍格沃茨", "伏地魔", "闪电疤", "邓布利多"],
    "佐助": ["写轮眼", "宇智波", "复仇", "鸣人"],
    "福尔摩斯": ["贝克街", "烟斗", "华生", "莫里亚蒂"],
    # ... 由产品逐步扩展
}

def filter_reserved_terms(refinements, characters):
    # 对每个建议:如果其引用的角色名在 KNOWN_WORK 表里,且建议文本/payload 包含
    # 该角色名对应的 reserved_terms,则将该建议过滤掉(或标记为 needs_review)
    # 注意 reserved_terms 列表是双向的:看到 c1=李寻欢,孙小红和上官金虹的
    # reserved_terms 也要拼进禁用词集合(同一作品的其他角色名/概念)
    ...
```

**兜底 ③ 语义自相矛盾检测(防模式 C)**

```python
# 在 LLM 输出落库前,对每条 identity_补全 建议做二次校验
def validate_identity_refinement(refinement, char):
    if refinement.suggestion_kind != "identity_补全":
        return True
    # 如果输入 identity 非空,但 suggestion_text 暗示"空"
    text = refinement.suggestion_text
    char_identity = char.get("identity", "")
    suspicious_phrases = ["是空的", "为空", "字数不足", "字数太少", "过于简单"]
    if char_identity and char_identity.strip():
        for phrase in suspicious_phrases:
            if phrase in text:
                return False  # 过滤掉
    return True
```

### 16.3 兜底实施清单(P0,与 §15 落地清单合并)

后端 `app/services/character_focus.py` 必须实现:
- [ ] **兜底 ①** apply_refinement 的"value 覆盖非空字段拒绝"分支(防模式 A,直接抄 §16.2)
- [ ] **兜底 ②** refine 服务在 LLM 输出后、落库前调 `filter_reserved_terms`(防模式 B)
- [ ] **兜底 ③** refine 服务在 LLM 输出后、落库前调 `validate_identity_refinement`(防模式 C)
- [ ] **KNOWN_WORK_RESERVED_TERMS 字典**作为可热更新的数据资产(后续可放 SQLite 一张表,产品运营人员可直接维护,不需要发版)

### 16.4 验证策略

实现 3 个兜底后,**重跑 scripts/verify_prompt.py**,对比新旧报告:
- 模式 A 类违规应被 verify 已有的"铁律 5 完整版"100% 抓到
- 模式 B 类违规应不再进入数据库(过滤层兜底)
- 模式 C 类违规应不再进入数据库(语义校验兜底)

verify 报告中"warnings"列里的"百晓生"等可疑词,在加了过滤层之后,应该在 LLM 仍然产出的情况下被后端拦截,不会出现在用户界面。

### 16.5 不要做的事(防止过度兜底)

- ❌ 不要在 prompt 加更多反例追平 LLM 漂移(收益递减,已证实)
- ❌ 不要用更激进的措辞(LLM 不"听话"是模型本质,不是表达问题)
- ❌ 不要把所有 LLM 输出强制做"严格 schema 验证"(过度校验会过滤掉合理建议;只对已知漂移模式做精准拦截)
