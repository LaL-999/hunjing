<script setup lang="ts">
/**
 * ProjectView v2 — 浅色 + 行内自动保存(对齐 Notion / Linear 体验)。
 *
 * 行为:
 *   - 角色 / 关系 / 事件 三个 section,每个 section 末尾有"+ 新建"占位行
 *   - 点击占位行 → 进入编辑态(input 主色边框)
 *   - input 失焦 / Enter → 自动保存(POST 创建 OR PATCH 更新)
 *   - 同一行内 Tab 在 input 间跳不触发保存(用 e.relatedTarget 检测)
 *   - 名字空 + 新建 → 取消(行消失)
 *   - 名字空 + 已存在 → 拒绝保存,保留 edit + 错误
 *   - 已存在角色:点击行进入编辑,失焦/Enter 自动 PATCH
 *   - 关系 / 事件:支持创建 + 删除,改字段留下个 sprint
 *
 * 查看图谱:点击前先 document.activeElement.blur() 触发保存,等 saving 完,
 * 任何"填了内容但没保存掉"的脏行 → 弹提示 + 滚动定位 + 焦点跳过去。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "../api/client";
import {
  ApiError,
  type BehaviorBaseline,
  type Character,
  type CreateCharacterRequest,
  type MoralCompass,
  PRESET_RELATIONSHIP_TYPES,
  type ProjectEvent,
  type Project,
  type InsufficientCreditsDetail,
  type QuotaExceededDetail,
  type Relationship,
  type RelationshipPolarity,
  type RelationshipType,
  type SecretItem,
  type SpeechRegister,
  type UpdateCharacterRequest,
  type UpdateEventRequest,
  type UpdateRelationshipRequest,
} from "../api/types";
import { useInferenceStore } from "../stores/inference";
import { useQuotaStore } from "../stores/quota";
import { useAddonModal } from "../composables/useAddonModal";
import { toast } from "../composables/useToast";
import { useUpgradeModal } from "../composables/useUpgradeModal";
import AuthorCompassPanel from "../components/AuthorCompassPanel.vue";
import CharacterEmotionOverviewModal from "../components/CharacterEmotionOverviewModal.vue";
import CharacterFocusModal from "../components/CharacterFocusModal.vue";
import CounterfactualWorkbench from "../components/CounterfactualWorkbench.vue";
// UI 升级(2026-05-21):统一 SVG icon 组件,替换显眼位置的 emoji
import Icon from "../components/Icon.vue";
import ProjectUploadsPanel from "../components/ProjectUploadsPanel.vue";
import StoryFactsPanel from "../components/StoryFactsPanel.vue";
import ProtagonistWall from "../components/ProtagonistWall.vue";
// INIT.2(2026-05-21):初始态关系时间轴 — chip 末尾 🕒 按钮展开 phases 编辑
import RelationshipTimeline from "../components/RelationshipTimeline.vue";
import SceneGraph from "../components/SceneGraph.vue";
// INIT.5(2026-05-21):初始态世界观 6 维编辑
import WorldBaselineEditor from "../components/WorldBaselineEditor.vue";
import SimulationDock from "../components/SimulationDock.vue";
import SimulationsListPanel from "../components/SimulationsListPanel.vue";
import SkeletonBlock from "../components/SkeletonBlock.vue";
import { useCounterfactuals } from "../composables/useCounterfactuals";

const props = defineProps<{ id: string }>();
const router = useRouter();
const route = useRoute();

// Sprint 6.A2 路线图 #2.5(2026-05-22):audit 采纳的 sim_config patches 持久化到 DB
// 之前用 sessionStorage(use-once / 跨会话丢失);现改为 GET /api/projects/:id/pending_audit_patches
// 拉所有"已采纳未应用"patches → ProjectView mount 时 fetch,失败静默不阻塞主流程
// dock 提交推演成功后 POST mark_applied 批量标记 → 下次 fetch 不返这些条目
interface PendingAuditPatch {
  audit_id: string;
  issue_idx: number;
  source_sim_id: string;          // audit 关联 sim,默认勾上作前文(滚雪球)
  source_label: string;           // 给 dock banner 显示,如 "对白扁平·全篇" / "节奏失衡·中段"
  patches: SimConfigPatchPayload;
  accepted_at: string;            // ISO,sort key
}
const pendingPatches = ref<PendingAuditPatch[]>([]);

async function loadPendingPatches() {
  try {
    pendingPatches.value = await api.get<PendingAuditPatch[]>(
      `/projects/${props.id}/pending_audit_patches`,
    );
  } catch (e) {
    // audit 功能为可选优化,失败不阻塞主项目页;dev 期保留诊断
    if (e instanceof ApiError) {
      if (import.meta.env.DEV) {
        console.warn("[ProjectView] pending audit patches 拉取失败:", e.message);
      }
    }
  }
}
onMounted(loadPendingPatches);

// M7.I(2026-05-20):URL ?continueFrom=<simId> 链路 — SimulationDetailView 的"基于本篇续写"
// 传入待预选的 sim id;dock 打开时自动 snowballOn + 勾上该 sim
// 兜底:最新一条 pending patch 的 source_sim_id(audit 采纳新链路,DB 持久化)
const continueFromIds = computed<string[]>(() => {
  const raw = route.query.continueFrom;
  if (raw) {
    // route.query 值可能是 string 或 string[];都规整成 array
    return (Array.isArray(raw) ? raw : [raw]).filter(
      (s): s is string => typeof s === "string" && s.length > 0,
    );
  }
  // 用最新采纳的 sim 作前文(accepted_at ASC,取最后一条 = 最新)
  const last = pendingPatches.value[pendingPatches.value.length - 1];
  return last ? [last.source_sim_id] : [];
});

// M7.J(2026-05-20):传给 SimulationDock 的 events 列表(中间态起点锚点 dropdown 用)
// 只取已存在的 event(skip pending row 还未保存的);description 非空才参与
const anchorableEvents = computed(() =>
  eventRows.value
    .filter((r) => r.event && (r.event.description ?? "").trim().length > 0)
    .map((r) => ({ id: r.event!.id, description: r.event!.description })),
);

// M7.K(2026-05-20)从 URL ?applySimPatch=<base64-json> 解析 sim_config patch
// 来自 SimulationDetailView LLM-only 类采纳跳转(dialogue_flat / turn_jarring / ...)
// dock 打开时按此 patch 预填 reshape_percent / target_chars / custom_style_hint / divergence prefix
interface SimConfigPatchPayload {
  reshape_percent_delta?: number;
  target_chars_delta?: number;
  custom_style_hint?: string;
  divergence_prefix?: string;
}
const applySimPatch = computed<SimConfigPatchPayload | null>(() => {
  const raw = route.query.applySimPatch;
  if (raw && !Array.isArray(raw) && typeof raw === "string") {
    try {
      const json = decodeURIComponent(escape(atob(raw)));
      const parsed = JSON.parse(json);
      if (typeof parsed === "object" && parsed !== null) {
        return parsed as SimConfigPatchPayload;
      }
    } catch {
      /* fall through to DB pending patches fallback */
    }
  }
  // Sprint 6.A2 #2.5 兜底:合并所有 pending patches(accepted_at ASC,后采纳的覆盖前者)
  if (pendingPatches.value.length === 0) return null;
  const merged: SimConfigPatchPayload = {};
  for (const p of pendingPatches.value) {
    Object.assign(merged, p.patches);
  }
  return merged;
});

// M7.K-fix(2026-05-20)issue 中文标签 — 给 dock banner 显"自洽守护者建议(转折突兀)"
const auditSourceLabel = computed<string>(() => {
  const raw = route.query.auditSource;
  if (raw && !Array.isArray(raw) && typeof raw === "string") return raw;
  // Sprint 6.A2 #2.5 兜底:合并所有 pending patches 的 source_label,用 " / " 拼接
  if (pendingPatches.value.length === 0) return "";
  return pendingPatches.value.map((p) => p.source_label).join(" / ");
});

/**
 * Sprint 6.A2 #2.5(2026-05-22):dock 提交推演成功后批量标 applied_at
 * 该函数由 dock 的 `created` emit 触发,把当前所有 pendingPatches 标记为已应用,
 * 然后清空本地 ref(下次 fetch 不返这些条目)
 */
async function markPatchesAppliedAfterSimCreated() {
  if (pendingPatches.value.length === 0) return;
  const items = pendingPatches.value.map((p) => ({
    audit_id: p.audit_id,
    issue_idx: p.issue_idx,
  }));
  try {
    await api.post(
      `/projects/${props.id}/audit_patches/mark_applied`,
      { items },
    );
    pendingPatches.value = [];   // 本地立即清空避免 UI 错位
  } catch (e) {
    // P-3 修复(2026-05-23):失败时显式 toast 告知用户(原行为只静默 console.warn,
    // 导致用户下次回项目仍看到这些 patches 残留,以为"我已经用过了为啥又来了")
    const msg = e instanceof ApiError ? e.message : "网络异常";
    toast.warning(
      `推演已创建,但建议标记失败(${msg})— 下次进项目可能还会看到这条建议,可放心忽略`,
      8000,
    );
  }
}
const quota = useQuotaStore();
const upgradeModal = useUpgradeModal();
const addonModal = useAddonModal();

// ============================================================
// 数据 + 加载
// ============================================================

const project = ref<Project | null>(null);
const loading = ref(true);
const errorMessage = ref<string | null>(null);

// 三类 row state(支持行内编辑)
type RowMode = "view" | "edit" | "saving";

interface CharacterRow {
  /** 已落库的 character;新占位行为 null */
  character: Character | null;
  editName: string;
  editIdentity: string;
  /** 性格(自由文本,LLM "personality_补充" 写这里) */
  editPersonality: string;
  /** 台词原始文本(每行一句),保存时 split('\n') → list,最多 20 条 */
  editQuotesRaw: string;
  /** 禁忌原始文本(每行一条),保存时 split('\n') → list,最多 20 条 */
  editNoGoRaw: string;
  // Sprint 6.A2 INIT.3(2026-05-21):4 维度行为基线 edit 字段
  //   ""/null/undefined = 未设(后端 NULL,consistency_checker fallback)
  //   speechRegister / moralCompass 是枚举字符串,emotionalIntensity 0-10 整数
  //   editBaselineOutRaw 是 textarea 一行一条文本(对齐 quotes/no_go 模式)
  editSpeechRegister: SpeechRegister | "";
  editEmotionalIntensity: number | null;
  editMoralCompass: MoralCompass | "";
  editBaselineOutRaw: string;
  // SP-2(2026-05-28)— 角色驱动五件套 edit 字段
  // 4 个 string("" = 未填,后端 NULL)+ 1 个 secrets textarea(一行一条 description,hidden_from 留空 = 对全员瞒)
  editSurfaceGoal: string;
  editDeepNeed: string;
  editFatalBlindSpot: string;
  editArcFromTo: string;
  editSecretsRaw: string;
  mode: RowMode;
  errorMsg: string | null;
  /** 当前 saving 的 promise,viewGraph 时等它 */
  savePromise: Promise<void> | null;
}

interface RelationshipRow {
  relationship: Relationship | null;
  editSource: string;
  editTarget: string;
  editType: RelationshipType;
  editDesc: string;
  mode: RowMode;
  errorMsg: string | null;
  savePromise: Promise<void> | null;
  /**
   * INIT.1 fix(2026-05-21):一个关系镜像显示在 source / target 两个角色卡内,
   * 但 row 在 relationshipRows 里只有一份 → mode='edit' 时两个卡片会同时进入 edit。
   * 用 editingFromCharId 记录"从哪个角色卡发起的 edit",模板里只有匹配的卡显示 edit form,
   * 另一边继续显示 chip(共享同一 row.relationship,PATCH 后自动响应更新)。
   * null = 占位新建行 / 全局新建(addingRelForCharId 已经分流,这里 null 兼容)。
   */
  editingFromCharId: string | null;
}

interface EventRow {
  event: ProjectEvent | null;
  editDesc: string;
  editParticipants: string[];
  // INIT.7(2026-05-21):事件时间锚
  editTimeAnchor: string;
  mode: RowMode;
  errorMsg: string | null;
  savePromise: Promise<void> | null;
}

const characterRows = ref<CharacterRow[]>([]);
const relationshipRows = ref<RelationshipRow[]>([]);
const eventRows = ref<EventRow[]>([]);

/*
  INIT 视觉统一(2026-05-21):初始态 5 个 section 折叠态对齐
    - 世界观 / 场景图谱已是卡片+折叠
    - 角色 / 关系 / 事件原本是无折叠 section,改造为同款卡片+折叠
    - 默认折叠,用户进入页面后选择性展开,避免一进来就被全部内容淹没
*/
const charsSectionCollapsed = ref(true);      // 角色:默认折叠(用户统一要求)
// INIT.1(2026-05-21):关系移入角色卡 — 删独立 section,relsSectionCollapsed 删除
const eventsSectionCollapsed = ref(true);     // 事件:默认折叠(可选项)
// hotfix(2026-06-01):SP-1 / SP-3 / SP-8 三个新卡片加折叠,与世界观/角色/事件视觉一致
const storyCoreCollapsed = ref(true);          // 故事脊柱:默认折叠
const narrativeViewCollapsed = ref(true);      // 视角扩展:默认折叠
const storyFactsCollapsed = ref(true);         // 知识边界:默认折叠

/** Sprint D.7 fix 2(2026-05-12 用户报告):row 切到 edit 模式时,HTML `autofocus`
 *  属性会触发浏览器 scroll-into-view 副作用 — 把 input 滚到视图中部,叠加 row 高度从
 *  40px 变成 ~140px(input + 参与者 chip 列表),整页可滚动空间扩展,视觉上"上移闪烁"。
 *  改用 `focus({ preventScroll: true })` 阻止滚动副作用 — 标准 Vue 3 自定义指令做法。
 *  HTMLElement.focus(options) 是 W3C focus API,所有现代浏览器都支持。 */
const vFocus = {
  mounted: (el: HTMLElement) => el.focus({ preventScroll: true }),
};

// Sprint 6.A2 M7.G(2026-05-20):用统一预设清单(来自 api/types.ts);
// 编辑现有关系时,若关系当前 type 是 LLM 自创 / 用户自定义且不在预设列表,
// 仍能正常显示(option 标签 + 同名 value)— 由 typeOptionsForEdit 兜底注入
const REL_TYPES = PRESET_RELATIONSHIP_TYPES;

const characterById = computed(() => {
  const m = new Map<string, Character>();
  characterRows.value.forEach((r) => {
    if (r.character) m.set(r.character.id, r.character);
  });
  return m;
});

/** Sprint D.7:已落库 Character 列表(non-null narrow,供下拉 / 多选用)
 *  替代旧的 `characterRows.filter(r => r.character)` + `r.character!.id` 非空断言写法 */
const realCharacters = computed<Character[]>(() =>
  characterRows.value
    .map((r) => r.character)
    .filter((c): c is Character => c !== null),
);

/**
 * INIT.1 fix v3(2026-05-21):角色 edit 改 drawer 模式
 *   - 原本 row.mode === 'edit' 时 inline 渲染 700+px 完整 form,挤掉其他 row 视觉重量
 *   - 现在 inline 只显示精简 view + "正在编辑"高亮态,完整 form 在右侧 drawer 中
 *   - 单例:同时只有 1 个角色处于 edit 模式(由 closeOtherEdits 已保证),所以查 first match 即可
 *
 * editingCharRow / editingCharIdx 给 drawer template 用来渲染目标 row 的 v-model 绑定
 * 点击外部 / Esc / × → cancelCharEdit(还原)或 tryPersistChar(保存)→ row.mode = 'view' → drawer 自动消失
 */
const editingCharRow = computed<CharacterRow | null>(() => {
  return characterRows.value.find((r) => r.mode === "edit") ?? null;
});
const editingCharIdx = computed<number>(() => {
  return characterRows.value.findIndex((r) => r.mode === "edit");
});

/** drawer 关闭(× / 点遮罩):走 tryPersistChar 保存 → 显式切 row.mode='view' → drawer 消失
 *  INIT.1 fix v3:tryPersistChar 不再自动切 mode(否则 drawer 内任意 input blur 都会消失);
 *  显式由本函数负责切。错误时 mode 已被 catch 设回 'edit',drawer 保持打开让用户重试。
 */
async function closeCharEditDrawer() {
  const row = editingCharRow.value;
  const idx = editingCharIdx.value;
  if (!row || idx < 0) return;
  await tryPersistChar(row, idx);
  // 成功保存后 mode 是 'edit'(刚改的);失败时也是 'edit'(catch 设的)
  // 但成功时 errorMsg=null,失败时 errorMsg 非空 — 用 errorMsg 判定
  if (!row.errorMsg) {
    row.mode = "view";   // 成功 → 关闭 drawer
  }
  // 失败 → 保持 'edit' → drawer 继续打开,用户看 errorMsg 提示
}

/** drawer 内 Esc 键:取消编辑(还原原值,不保存)*/
function cancelCharEditDrawer() {
  const row = editingCharRow.value;
  if (!row) return;
  cancelCharEdit(row);
}

/**
 * 全局 Esc 监听:drawer 打开时按 Esc → cancelCharEditDrawer
 *   - 不依赖 input focus(用户可能在 select / slider 后丢焦点,Esc 仍要 work)
 *   - 仅在 editingCharRow 存在时触发,避免影响其他模态
 */
function onGlobalEscape(e: KeyboardEvent) {
  if (e.key !== "Escape") return;
  if (!editingCharRow.value) return;
  e.preventDefault();
  cancelCharEditDrawer();
}
onMounted(() => document.addEventListener("keydown", onGlobalEscape));
onBeforeUnmount(() => document.removeEventListener("keydown", onGlobalEscape));

/**
 * Bug 修复(2026-05-22):侧边栏重命名当前项目后,header 名字即时同步。
 *
 * AppSidebar 重命名 PATCH 成功后 dispatch 一个全局 CustomEvent;ProjectView 监听之,
 * 如果改的是当前打开的项目,直接更新 project.value.name(无需重拉整个项目数据)。
 *
 * 为什么用 CustomEvent 而非 Pinia store:sidebar 和 ProjectView 是路由兄弟,
 * 引入共享 store 工程量大;事件总线 mitt 是新依赖(违反"YAGNI + 架构冻结闸门");
 * window CustomEvent 零依赖、稳健、跨路由组件 work。
 */
function onProjectRenamed(e: Event) {
  const detail = (e as CustomEvent<{ id: string; name: string }>).detail;
  if (!detail || !project.value) return;
  if (detail.id === props.id && project.value.id === detail.id) {
    project.value.name = detail.name;
  }
}
onMounted(() => window.addEventListener("huimeng:project-renamed", onProjectRenamed));
onBeforeUnmount(() => window.removeEventListener("huimeng:project-renamed", onProjectRenamed));

/**
 * INIT.1(2026-05-21):关系移入角色卡 — 按 character id 反查相关关系(双向)
 *
 * 返回 array of `{ row, idx, direction, otherId }`:
 *   - row / idx:relationshipRows 里的原 row 引用 + index(用于复用 tryPersistRel / del 等)
 *   - direction:"out" = charId 是 source(显示 → 对方);"in" = charId 是 target(显示 ← 对方)
 *   - otherId:对端角色 id(显示对端名字用 characterById 查)
 *
 * 用于角色 li 末尾的 .char-rels 子区,**不**包含占位行(row.relationship === null)。
 * 占位行只在用户点击"+ 加关系"时按需创建,逻辑见 startAddRelationFor。
 */
interface RelForChar {
  row: RelationshipRow;
  idx: number;
  direction: "out" | "in";
  otherId: string;
}
function relsForCharacter(charId: string): RelForChar[] {
  const out: RelForChar[] = [];
  relationshipRows.value.forEach((row, idx) => {
    const r = row.relationship;
    if (!r) return;  // 跳过占位行(未保存)
    if (r.source_id === charId) {
      out.push({ row, idx, direction: "out", otherId: r.target_id });
    } else if (r.target_id === charId) {
      out.push({ row, idx, direction: "in", otherId: r.source_id });
    }
  });
  return out;
}

/**
 * INIT.1:跟踪"哪个角色卡正在 inline 创建新关系"
 *   null = 都不在创建;string = 该 char id 卡内已展开 add-form
 * 同一时刻只允许一个卡 add-form(防多个表单同时浮起来视觉混乱)
 */
const addingRelForCharId = ref<string | null>(null);
/** INIT.1:inline add-form 3 字段的 staging state(避免直接 v-model 深路径)
 *  保存时一次性写入占位 row → 调 tryPersistRel */
const addingRelTargetSelect = ref<string>("");
const addingRelTypeSelect = ref<RelationshipType>("朋友");
const addingRelDescInput = ref<string>("");

/** 把 staging state 同步到 placeholder row(每次字段变更触发,准备 blur 时一次 persist)*/
function bindAddingTargetToRow() {
  const charId = addingRelForCharId.value;
  if (!charId) return;
  const idx = relationshipRows.value.findIndex(
    (r) => !r.relationship && r.editSource === charId && r.mode === "edit",
  );
  if (idx === -1) return;
  const row = relationshipRows.value[idx];
  row.editTarget = addingRelTargetSelect.value;
  row.editType = addingRelTypeSelect.value;
  row.editDesc = addingRelDescInput.value;
}

/**
 * INIT.2(2026-05-21)+ fix v2(2026-05-21):关系演化时间轴 — chip 末尾 🕒 按钮切换展开
 *
 * 状态:
 *   expandedRelKey — 当前唯一展开的"角色卡+关系"复合 key(string | null,**全局互斥**)
 *                    格式:`${charId}|${relationshipId}`
 *                    **关键**:同一关系在两端角色卡都显示一次 chip,但需作为独立 UI 实例
 *                    判定展开,否则展开 A 卡的 chip 会让 B 卡的同一关系 chip 也展开
 *   phaseCounts    — relId → phaseCount(chip 上显"🕒 N"摘要;未加载时 undefined → 显 "🕒")
 *                    这里仍按 relId 索引(后端层面一条关系只有一份 phases 数据)
 *
 * 数据加载策略:
 *   不在 loadAll 主动 prefetch 所有 phases(避免首页 N 次网络请求拖慢);
 *   用户首次展开 timeline 时,内部 RelationshipTimeline 自己 loadPhases,然后 emit
 *   phases-changed 带 phaseCount → 这里更新缓存。下次再渲染该 chip 即显数字。
 *
 * 闪烁防护:复刻 ProtagonistWall.onPhasesChanged v2 — 不全量 reload,
 *   immutable update 单条 relationship.current_phase_id 让 Vue 局部 patch。
 */
const expandedRelKey = ref<string | null>(null);
const phaseCounts = ref<Map<string, number>>(new Map());

function relExpandKey(charId: string, relId: string): string {
  return `${charId}|${relId}`;
}

function toggleRelTimeline(charId: string, relId: string, e?: Event) {
  e?.stopPropagation();
  const key = relExpandKey(charId, relId);
  // 互斥语义:点已展开的 → 关闭;点未展开的 → 覆盖(自动关闭其他卡 / 其他关系)
  expandedRelKey.value = expandedRelKey.value === key ? null : key;
}

interface RelPhasesChangedPayload {
  relationshipId: string;
  phaseCount: number;
  currentPhaseId: string | null | undefined;
}

function onRelPhasesChanged(payload: RelPhasesChangedPayload) {
  // 1. phaseCounts 局部更新(Map immutable replace 触发 chip 重渲染)
  const newCounts = new Map(phaseCounts.value);
  newCounts.set(payload.relationshipId, payload.phaseCount);
  phaseCounts.value = newCounts;

  // 2. current_phase_id 局部更新(undefined = 不动)
  if (payload.currentPhaseId !== undefined) {
    const rowIdx = relationshipRows.value.findIndex(
      (r) => r.relationship?.id === payload.relationshipId,
    );
    if (rowIdx >= 0 && relationshipRows.value[rowIdx].relationship) {
      // immutable update — Vue 视为该 row 变了,旁边 chip 不重建
      const oldRel = relationshipRows.value[rowIdx].relationship!;
      relationshipRows.value[rowIdx].relationship = {
        ...oldRel,
        current_phase_id: payload.currentPhaseId,
      };
    }
  }
}

/** 把 staging state 同步到 row + 触发 tryPersistRel(blur/Enter 走这里)*/
async function bindAndPersistAddingRel(charId: string) {
  if (addingRelForCharId.value !== charId) return;
  bindAddingTargetToRow();
  const idx = relationshipRows.value.findIndex(
    (r) => !r.relationship && r.editSource === charId && r.mode === "edit",
  );
  if (idx === -1) return;
  // target 必填(没选 → 维持 edit,不报 — 用户可能在挑选)
  if (!addingRelTargetSelect.value) return;
  await tryPersistRel(relationshipRows.value[idx], idx);
  // 成功保存后,清 staging 让下一次 add 不残留旧值
  if (relationshipRows.value[idx]?.relationship) {
    addingRelTargetSelect.value = "";
    addingRelTypeSelect.value = "朋友";
    addingRelDescInput.value = "";
  }
}

/** 启动从某角色卡 inline 添加新关系
 *   - 在 relationshipRows 末尾追加一个 prefill source 的占位 row(mode='edit')
 *   - 设 addingRelForCharId = charId 让模板渲染该卡的 add-form
 *   - 占位 row 的 editSource 锁为当前 charId,target / type / desc 由用户填
 */
function startAddRelationFor(charId: string) {
  // 同时只能一个卡处于 adding;若已在其他卡,先 cancel 那个
  if (addingRelForCharId.value && addingRelForCharId.value !== charId) {
    cancelAddRelationFromCard();
  }
  // 找现有的"末尾占位 row"或创建一个新的
  let placeholderIdx = relationshipRows.value.findIndex(
    (r) => !r.relationship && r.mode === "view",
  );
  if (placeholderIdx === -1) {
    relationshipRows.value.push(makeNewRelRow());
    placeholderIdx = relationshipRows.value.length - 1;
  }
  const row = relationshipRows.value[placeholderIdx];
  row.editSource = charId;
  row.editTarget = "";
  row.editType = "朋友";
  row.editDesc = "";
  row.mode = "edit";
  row.errorMsg = null;
  addingRelForCharId.value = charId;
  // 同步 staging state(清空,准备让用户填)
  addingRelTargetSelect.value = "";
  addingRelTypeSelect.value = "朋友";
  addingRelDescInput.value = "";
}

/** 取消 inline 添加(空字段 → 行删除回空状态)*/
function cancelAddRelationFromCard() {
  const charId = addingRelForCharId.value;
  if (!charId) return;
  // 找到 source=charId 且 mode='edit' 且未 saved 的占位 row
  const idx = relationshipRows.value.findIndex(
    (r) => !r.relationship && r.editSource === charId,
  );
  if (idx !== -1) {
    const row = relationshipRows.value[idx];
    row.editSource = "";
    row.editTarget = "";
    row.editType = "朋友";
    row.editDesc = "";
    row.mode = "view";
  }
  addingRelForCharId.value = null;
}

// v6.3:态分屏 — 初始态走手动卡(原样);中间/末尾/周期态走 AI 抽图谱
const isInitialMode = computed(() => project.value?.mode === "initial");
const projectMode = computed(() => project.value?.mode ?? "initial");
const MODE_LABEL_MAP: Record<string, string> = {
  initial: "初始态",
  middle: "中间态",
  end: "末尾态",
  cycle: "漫创态",   // 原"周期态"已废除,字面量 cycle 向后兼容
};
const projectModeLabel = computed(() => MODE_LABEL_MAP[projectMode.value] ?? "项目");

/**
 * Sprint 2.E polish:顶栏「AI 推演 / 重塑 / 续写 / 长篇」按钮按 mode 差异化
 * — 与 SimulationDock.dockHeaderConfig 同一套 4 态 label 映射
 *
 * 入口动词错位会让用户心智撕裂(中间态点开是「AI 重塑」modal,但按钮写「AI 续写」)
 *   initial → ✦ AI 推演
 *   middle  → ⟲ AI 重塑
 *   end     → → AI 续写
 *   cycle   → ∞ AI 长篇
 */
/**
 * 2026-06-08 UI 大升级:icon 从 unicode 字符 → SVG icon name(Icon.vue 名)。
 * middle/end/cycle/initial 各自一个语义 icon,模板里用 <Icon :name="..." />。
 */
const simulateButtonLabel = computed(() => {
  const m = projectMode.value;
  if (m === "middle") return { icon: "rotate_ccw", text: "AI 重塑" };
  if (m === "end") return { icon: "arrow_right", text: "AI 续写" };
  if (m === "cycle") return { icon: "refresh", text: "AI 长篇" };
  return { icon: "spark", text: "AI 推演" };
});

const simulateButtonTitle = computed(() => {
  const m = projectMode.value;
  if (m === "middle") return "改一个反事实变量,AI 重新推演 what-if 走向";
  if (m === "end") return "从原作末尾接,AI 用原作角色续写不影响正文";
  if (m === "cycle") return "在已有产物上累积新章,反事实可逐轮叠加";
  return "给一个起点,AI 用你的角色推演剧情走向";
});

/** Sprint 3.A:抽图谱就绪后的引导语
 *  UI 优化(2026-05-21 二轮):全 mode 都不再展示操作引导文字 —
 *  主界面四态选项卡已说明每态用法 + 右上角按钮本身即 CTA,banner 里复述显多余。
 *  保留 computed 以便未来按需再启,当前恒返空字符串。
 */
const readyCardDescription = computed(() => "");

/** v6.4:非初始态需要先抽图谱才能玩 AI 续写 / 对焦;通过项目内是否已有 characters 判断 */
const hasGraphData = computed(
  () => characterRows.value.some((r) => r.character),
);

/** AI 操作按钮 disabled 原因:
 *   - 初始态:无限制(原样)
 *   - 非初始态 + 还没抽图谱:disabled,提示先抽图谱
 *   - 非初始态 + 已有图谱:可用(同初始态)
 */
const aiActionsDisabledReason = computed<string | null>(() => {
  if (!project.value) return null;
  if (isInitialMode.value) return null;
  if (hasGraphData.value) return null;
  // 2026-06-08 UI 升级:文案里 ✦ emoji 字符废弃(按钮已改 SVG icon,文案不再引用 emoji)
  return `先在「作品文件」上传 + 点「AI 抽图谱」`;
});

/** 抽图谱完成回调:reload 项目数据(characters / relationships / events 已落库)*/
async function onExtractDone() {
  await loadAll();
  errorMessage.value = null;
}

/**
 * Sprint 6.A2 FOCUS.5(2026-05-22):upload 被删除回调。
 * 后端 delete_upload 已级联清角色/关系/事件/场景/项目元数据(tags / world_baseline /
 * narrative_pov),前端必须 reload 让 ProtagonistWall / SceneGraph / "图谱已就绪 banner"
 * / header pov chip 全部根据新 state 重新渲染。
 */
async function onUploadDeleted(_uploadId: string) {
  await loadAll();
  errorMessage.value = null;
}

function makeNewCharRow(): CharacterRow {
  return {
    character: null,
    editName: "",
    editIdentity: "",
    editPersonality: "",
    editQuotesRaw: "",
    editNoGoRaw: "",
    // INIT.3:4 维度行为基线初始值都是"未设"(""/null) — 老 db 角色 fallback 路径
    editSpeechRegister: "",
    editEmotionalIntensity: null,
    editMoralCompass: "",
    editBaselineOutRaw: "",
    // SP-2:角色驱动 5 字段全空(占位行)
    editSurfaceGoal: "",
    editDeepNeed: "",
    editFatalBlindSpot: "",
    editArcFromTo: "",
    editSecretsRaw: "",
    mode: "view",        // 占位行的初始 mode 是 view,点击进 edit
    errorMsg: null,
    savePromise: null,
  };
}
function makeNewRelRow(): RelationshipRow {
  return {
    relationship: null,
    editSource: "",
    editTarget: "",
    editType: "朋友",
    editDesc: "",
    mode: "view",
    errorMsg: null,
    savePromise: null,
    editingFromCharId: null,
  };
}
function makeNewEventRow(): EventRow {
  return {
    event: null,
    editDesc: "",
    editParticipants: [],
    editTimeAnchor: "",
    mode: "view",
    errorMsg: null,
    savePromise: null,
  };
}

async function loadAll() {
  // Sprint 6.A2 polish(2026-05-22):切换 project 不闪屏
  // - 首次加载(project===null):loading=true 显示 skeleton
  // - 切换 project / refetch:保留旧数据无缝替换(stale-while-revalidate),
  //   避免触发模板 v-if="!loading" 整块隐藏 → 用户感受到"内容闪一下消失再出现"
  // - reactive 在 microtask 内一次性 swap 所有 ref,DOM 平滑过渡(列表外层若包了
  //   TransitionGroup name="list-stagger" 还会自动 fade);失败时旧数据保留 + error 提示
  const isFirstLoad = project.value === null;
  if (isFirstLoad) loading.value = true;
  errorMessage.value = null;
  try {
    const resp = await api.get<{
      project: Project;
      characters: Character[];
      relationships: Relationship[];
      events: ProjectEvent[];
    }>(`/projects/${props.id}/graph`);
    project.value = resp.project;

    characterRows.value = [
      ...resp.characters.map<CharacterRow>((c) => ({
        character: c,
        editName: c.name,
        editIdentity: c.identity,
        editPersonality: c.personality,
        editQuotesRaw: (c.quotes ?? []).join("\n"),
        editNoGoRaw: (c.no_go_list ?? []).join("\n"),
        // INIT.3:从 behavior_baseline 读出 4 维度 edit 字段
        editSpeechRegister: (c.behavior_baseline?.speech_register ?? "") as SpeechRegister | "",
        editEmotionalIntensity: c.behavior_baseline?.emotional_intensity ?? null,
        editMoralCompass: (c.behavior_baseline?.moral_compass ?? "") as MoralCompass | "",
        editBaselineOutRaw: (c.behavior_baseline?.out_of_baseline_examples ?? []).join("\n"),
        // SP-2:角色驱动 5 字段从 character 读
        editSurfaceGoal: c.surface_goal ?? "",
        editDeepNeed: c.deep_need ?? "",
        editFatalBlindSpot: c.fatal_blind_spot ?? "",
        editArcFromTo: c.arc_from_to ?? "",
        editSecretsRaw: (c.secrets ?? []).map((s) => s.description).join("\n"),
        mode: "view",
        errorMsg: null,
        savePromise: null,
      })),
      makeNewCharRow(),
    ];
    relationshipRows.value = [
      ...resp.relationships.map<RelationshipRow>((r) => ({
        relationship: r,
        editSource: r.source_id,
        editTarget: r.target_id,
        editType: r.type,
        editDesc: r.description,
        mode: "view",
        errorMsg: null,
        savePromise: null,
        editingFromCharId: null,
      })),
      makeNewRelRow(),
    ];
    // 已存角色 id set,用于 lazy 清理 events.participants 里的脏 id
    // (历史上某角色被删但 events.participants JSON 里残留 — 老 bug,新版 DELETE 已级联清理)
    const validCharIds = new Set(resp.characters.map((c) => c.id));
    eventRows.value = [
      ...resp.events.map<EventRow>((e) => ({
        event: e,
        editDesc: e.description,
        // editParticipants 直接初始化为过滤后的 — 用户进 edit 改任意字段 + 保存,
        // 后端 PATCH 检测到 participants 变了,会写入清理后的版本,DB 自然净化
        editParticipants: e.participants.filter((id) => validCharIds.has(id)),
        editTimeAnchor: e.time_anchor ?? "",
        mode: "view",
        errorMsg: null,
        savePromise: null,
      })),
      makeNewEventRow(),
    ];
  } catch (e) {
    errorMessage.value = e instanceof ApiError ? e.message : "加载失败";
  } finally {
    if (isFirstLoad) loading.value = false;
  }
}

// ============================================================
// "失焦判定"工具:e.relatedTarget 仍在同一行内则不触发保存
// ============================================================

const rowRefs = reactive(new Map<string, HTMLElement | null>());

function setRowRef(key: string, el: Element | null) {
  rowRefs.set(key, el as HTMLElement | null);
}

function focusStillInRow(rowKey: string, e: FocusEvent): boolean {
  const next = e.relatedTarget as HTMLElement | null;
  const el = rowRefs.get(rowKey);
  if (!next || !el) return false;
  return el.contains(next);
}

function rowKeyChar(idx: number): string { return `char-${idx}`; }
function rowKeyRel(idx: number): string  { return `rel-${idx}`; }
function rowKeyEvent(idx: number): string { return `event-${idx}`; }

// ============================================================
// 角色行内自动保存
// ============================================================

/**
 * 同一时刻只允许一行处于 edit 态(对齐 Notion / Linear)。
 * 进入新行 edit 前,把其他所有 edit 态行 commit(自动保存 OR 还原 OR 取消)。
 * fire and forget — 不 await,用户点击立刻响应。
 */
function closeOtherEdits(except: unknown) {
  characterRows.value.forEach((r, i) => {
    if (r === except || r.mode !== "edit") return;
    void (async () => {
      await tryPersistChar(r, i);
      if (r.mode === "edit") cancelCharEdit(r);   // 保存失败也强制退出
    })();
  });
  relationshipRows.value.forEach((r, i) => {
    if (r === except || r.mode !== "edit") return;
    void (async () => {
      await tryPersistRel(r, i);
      if (r.mode === "edit") cancelRelEdit(r);
    })();
  });
  eventRows.value.forEach((r, i) => {
    if (r === except || r.mode !== "edit") return;
    void (async () => {
      await tryPersistEvent(r, i);
      if (r.mode === "edit") cancelEventEdit(r);
    })();
  });
}

function enterCharEdit(row: CharacterRow) {
  if (row.mode === "saving") return;
  closeOtherEdits(row);
  row.mode = "edit";
  row.errorMsg = null;
}

/**
 * 整个 ProjectView 任意空白点击 → 关闭所有 edit 行(对齐 Notion / Linear)。
 *
 * 注意:handler 绑在 .project-page 而非 .main,因为 .main 用 max-width:880px + margin:auto 居中,
 * 卡片左右两侧的空白其实在 .main 之外、.project-page 之内。原来绑 .main 时点击红框那块完全
 * 收不到事件,用户感知就是"展开卡片后,点旁边没反应"。
 *
 * 点击在某个 row 内则忽略(由各行自己的 click handler 处理)。
 */
function onPageBlankClick(e: MouseEvent) {
  const target = e.target as HTMLElement;
  // INIT.1 fix v3(2026-05-21):drawer 内点击跳过 — overlay 自有 click.self 关闭逻辑,
  // 这里如果不跳过,drawer 内任意 click 都会冒泡到 .project-page,
  // 触发 closeOtherEdits(null) → cancelCharEdit → 把 row.mode 切 'view' → drawer 消失
  const drawerOverlay = document.querySelector(".char-edit-drawer-overlay");
  if (drawerOverlay && drawerOverlay.contains(target)) return;
  // 如果点击在某个 row DOM 内,跳过(那是行内交互)
  for (const el of rowRefs.values()) {
    if (el && el.contains(target)) return;
  }
  closeOtherEdits(null);
}

/**
 * textarea 自动撑高指令:挂载时按内容初始化,input 时同步 height = scrollHeight。
 * 不显滚动条(.row-textarea 配 overflow: hidden + resize: none)。
 */
const vAutogrow = {
  mounted(el: HTMLTextAreaElement & { _autogrowFn?: () => void }) {
    const grow = () => {
      el.style.height = "auto";
      el.style.height = `${el.scrollHeight}px`;
    };
    // 等浏览器渲染一帧再算 scrollHeight,避免 0 初值
    requestAnimationFrame(grow);
    el.addEventListener("input", grow);
    // 2026-06-02 hotfix:存 ref 到 el,unmounted 时移除(治 listener 泄漏)
    el._autogrowFn = grow;
  },
  unmounted(el: HTMLTextAreaElement & { _autogrowFn?: () => void }) {
    if (el._autogrowFn) {
      el.removeEventListener("input", el._autogrowFn);
      delete el._autogrowFn;
    }
  },
};

/**
 * raw textarea 文本 → list:按 \n 切,去空白,去空行,截前 20 项。
 * 与后端 schema 限 max_length=20 一致(角色 quotes / no_go_list)。
 */
function parseList(raw: string): string[] {
  return raw
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .slice(0, 20);
}

/** 两个 list 等价(顺序也比),用于"无变化跳过 PATCH"判定 */
function listsEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

/**
 * INIT.3(2026-05-21):从 4 个 edit 字段构造 BehaviorBaseline payload。
 *   - 4 字段全空 → 返 null(后端清空列,consistency_checker fallback)
 *   - 任一字段有值 → 返完整 dict(空维度 = undefined,后端 Pydantic 可选)
 */
function buildBaselinePayload(row: CharacterRow): BehaviorBaseline | null {
  // P0G.2(2026-05-24)— 删 out_of_baseline_examples,不再读 editBaselineOutRaw
  const hasAny =
    row.editSpeechRegister !== "" ||
    row.editEmotionalIntensity !== null ||
    row.editMoralCompass !== "";
  if (!hasAny) return null;
  return {
    speech_register: row.editSpeechRegister || null,
    emotional_intensity: row.editEmotionalIntensity,
    moral_compass: row.editMoralCompass || null,
  };
}

/** INIT.3:两 baseline 等价判定(P0G.2 缩 3 字段)*/
function baselinesEqual(a: BehaviorBaseline | null | undefined, b: BehaviorBaseline | null | undefined): boolean {
  const na = a ?? null;
  const nb = b ?? null;
  if (na === null && nb === null) return true;
  if (na === null || nb === null) return false;
  if ((na.speech_register ?? null) !== (nb.speech_register ?? null)) return false;
  if ((na.emotional_intensity ?? null) !== (nb.emotional_intensity ?? null)) return false;
  if ((na.moral_compass ?? null) !== (nb.moral_compass ?? null)) return false;
  return true;
}

// ============================================================
// SP-2(2026-05-28):角色驱动 helper(secrets 简化版,UI 只填 description)
// ============================================================

/** raw textarea → SecretItem[](每行一条 description,hidden_from=[] = 对全员瞒;后期可加多选)*/
function parseSecretsRaw(raw: string): SecretItem[] {
  return raw
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
    .slice(0, 10)
    .map<SecretItem>((description) => ({ description, hidden_from: [] }));
}

/** SecretItem[] → raw textarea(用 description 行连)*/
function secretsRawFromList(secrets: SecretItem[] | null | undefined): string {
  return (secrets ?? []).map((s) => s.description).join("\n");
}

/** 两 secrets list 等价(只比 description,hidden_from 暂不在 UI 编辑)*/
function secretsListEqual(
  a: SecretItem[] | null | undefined,
  b: SecretItem[] | null | undefined,
): boolean {
  const na = a ?? [];
  const nb = b ?? [];
  if (na.length !== nb.length) return false;
  for (let i = 0; i < na.length; i++) {
    if (na[i].description !== nb[i].description) return false;
  }
  return true;
}

async function tryPersistChar(row: CharacterRow, idx: number, e?: FocusEvent) {
  if (e && focusStillInRow(rowKeyChar(idx), e)) return;
  if (row.mode !== "edit") return;

  const name = row.editName.trim();
  const identity = row.editIdentity.trim();
  const personality = row.editPersonality.trim();
  const quotes = parseList(row.editQuotesRaw);
  const noGo = parseList(row.editNoGoRaw);
  // INIT.3:构造行为基线 payload
  const baseline = buildBaselinePayload(row);
  // SP-2(2026-05-28):角色驱动 5 字段
  const surfaceGoal = row.editSurfaceGoal.trim();
  const deepNeed = row.editDeepNeed.trim();
  const fatalBlindSpot = row.editFatalBlindSpot.trim();
  const arcFromTo = row.editArcFromTo.trim();
  const secrets = parseSecretsRaw(row.editSecretsRaw);

  // 占位行 + 名字空 → 取消(其他字段填了也丢弃,name 是必填硬约束)
  if (!row.character && !name) {
    row.editIdentity = "";
    row.editPersonality = "";
    row.editQuotesRaw = "";
    row.editNoGoRaw = "";
    // INIT.3:占位行取消时,4 维基线也清空
    row.editSpeechRegister = "";
    row.editEmotionalIntensity = null;
    row.editMoralCompass = "";
    row.editBaselineOutRaw = "";
    // SP-2:同样清空 5 字段
    row.editSurfaceGoal = "";
    row.editDeepNeed = "";
    row.editFatalBlindSpot = "";
    row.editArcFromTo = "";
    row.editSecretsRaw = "";
    row.errorMsg = null;
    return;
  }

  // 已存在 + 名字空 → 拒绝
  if (row.character && !name) {
    row.errorMsg = "名字不能为空";
    return;
  }

  // 无变化 → 早退(不切 mode,由 caller 负责)
  // INIT.1 fix v3:drawer 内 input blur 不该让 row.mode 改变 → 否则 drawer 消失
  if (
    row.character &&
    name === row.character.name &&
    identity === row.character.identity &&
    personality === row.character.personality &&
    listsEqual(quotes, row.character.quotes ?? []) &&
    listsEqual(noGo, row.character.no_go_list ?? []) &&
    baselinesEqual(baseline, row.character.behavior_baseline) &&
    // SP-2:5 字段 diff
    surfaceGoal === (row.character.surface_goal ?? "") &&
    deepNeed === (row.character.deep_need ?? "") &&
    fatalBlindSpot === (row.character.fatal_blind_spot ?? "") &&
    arcFromTo === (row.character.arc_from_to ?? "") &&
    secretsListEqual(secrets, row.character.secrets)
  ) {
    row.errorMsg = null;
    return;
  }

  // 触发保存
  row.mode = "saving";
  row.errorMsg = null;

  const promise = (async () => {
    try {
      if (row.character) {
        // PATCH:只把变了的字段塞进去(后端用 model_dump(exclude_unset=True))
        const body: UpdateCharacterRequest = {};
        if (name !== row.character.name) body.name = name;
        if (identity !== row.character.identity) body.identity = identity;
        if (personality !== row.character.personality) body.personality = personality;
        if (!listsEqual(quotes, row.character.quotes ?? [])) body.quotes = quotes;
        if (!listsEqual(noGo, row.character.no_go_list ?? [])) body.no_go_list = noGo;
        // INIT.3:baseline 变了就整对象覆盖(null = 清空列)
        if (!baselinesEqual(baseline, row.character.behavior_baseline)) {
          body.behavior_baseline = baseline;
        }
        // SP-2:5 字段单独 PATCH(用 ?? "" 兜底老 character 没有该字段时的 undefined)
        if (surfaceGoal !== (row.character.surface_goal ?? "")) {
          body.surface_goal = surfaceGoal || null;
        }
        if (deepNeed !== (row.character.deep_need ?? "")) {
          body.deep_need = deepNeed || null;
        }
        if (fatalBlindSpot !== (row.character.fatal_blind_spot ?? "")) {
          body.fatal_blind_spot = fatalBlindSpot || null;
        }
        if (arcFromTo !== (row.character.arc_from_to ?? "")) {
          body.arc_from_to = arcFromTo || null;
        }
        if (!secretsListEqual(secrets, row.character.secrets)) {
          body.secrets = secrets;
        }

        const updated = await api.patch<Character>(
          `/characters/${row.character.id}`,
          body,
        );
        row.character = updated;
        row.editName = updated.name;
        row.editIdentity = updated.identity;
        row.editPersonality = updated.personality;
        row.editQuotesRaw = (updated.quotes ?? []).join("\n");
        row.editNoGoRaw = (updated.no_go_list ?? []).join("\n");
        // INIT.3:saved 后同步 baseline edit 字段(防止下次 diff 误判)
        row.editSpeechRegister = (updated.behavior_baseline?.speech_register ?? "") as SpeechRegister | "";
        row.editEmotionalIntensity = updated.behavior_baseline?.emotional_intensity ?? null;
        row.editMoralCompass = (updated.behavior_baseline?.moral_compass ?? "") as MoralCompass | "";
        row.editBaselineOutRaw = (updated.behavior_baseline?.out_of_baseline_examples ?? []).join("\n");
        // SP-2:同步 5 字段(防止下次 diff 误判)
        row.editSurfaceGoal = updated.surface_goal ?? "";
        row.editDeepNeed = updated.deep_need ?? "";
        row.editFatalBlindSpot = updated.fatal_blind_spot ?? "";
        row.editArcFromTo = updated.arc_from_to ?? "";
        row.editSecretsRaw = secretsRawFromList(updated.secrets);
        // INIT.1 fix v3:不自动切 'view'(否则 drawer 内 input blur 时 drawer 提前消失)
        // 由 closeCharEditDrawer / closeOtherEdits 显式切;成功 = 回到 'edit' 让 drawer 继续显示
        row.mode = "edit";
      } else {
        // POST 创建,占位行变已存在;末尾追加新占位行
        const body: CreateCharacterRequest = {
          name,
          identity,
          personality,
          quotes,
          no_go_list: noGo,
        };
        // INIT.3:创建时带 baseline(非 null 才传,Pydantic Optional)
        if (baseline !== null) body.behavior_baseline = baseline;
        // SP-2:创建时也带 5 字段(非空才传)
        if (surfaceGoal) body.surface_goal = surfaceGoal;
        if (deepNeed) body.deep_need = deepNeed;
        if (fatalBlindSpot) body.fatal_blind_spot = fatalBlindSpot;
        if (arcFromTo) body.arc_from_to = arcFromTo;
        if (secrets.length > 0) body.secrets = secrets;
        const created = await api.post<Character>(
          `/projects/${props.id}/characters`,
          body,
        );
        row.character = created;
        row.editName = created.name;
        row.editIdentity = created.identity;
        row.editPersonality = created.personality;
        row.editQuotesRaw = (created.quotes ?? []).join("\n");
        row.editNoGoRaw = (created.no_go_list ?? []).join("\n");
        // INIT.3:同上,从响应同步
        row.editSpeechRegister = (created.behavior_baseline?.speech_register ?? "") as SpeechRegister | "";
        row.editEmotionalIntensity = created.behavior_baseline?.emotional_intensity ?? null;
        row.editMoralCompass = (created.behavior_baseline?.moral_compass ?? "") as MoralCompass | "";
        row.editBaselineOutRaw = (created.behavior_baseline?.out_of_baseline_examples ?? []).join("\n");
        // SP-2:从响应同步 5 字段
        row.editSurfaceGoal = created.surface_goal ?? "";
        row.editDeepNeed = created.deep_need ?? "";
        row.editFatalBlindSpot = created.fatal_blind_spot ?? "";
        row.editArcFromTo = created.arc_from_to ?? "";
        row.editSecretsRaw = secretsRawFromList(created.secrets);
        // INIT.1 fix v3:同 PATCH 分支,不自动切 'view',保留 'edit' 让 drawer 持续显示
        row.mode = "edit";

        // 列表末尾追加新占位行(如果当前不是最后一个,说明有别的占位行)
        if (
          !characterRows.value[characterRows.value.length - 1].character &&
          characterRows.value[characterRows.value.length - 1] !== row
        ) {
          // 已有占位,不追加
        } else if (characterRows.value[characterRows.value.length - 1] === row) {
          characterRows.value.push(makeNewCharRow());
        }
      }
    } catch (err) {
      if (err instanceof ApiError && err.code === "QUOTA_EXCEEDED") {
        // 角色数超限 → 弹升级 modal,占位行还原(避免一直留着脏的 edit)
        upgradeModal.open(err.detail as QuotaExceededDetail);
        cancelCharEdit(row);
      } else {
        row.errorMsg = err instanceof ApiError ? err.message : "保存失败";
        row.mode = "edit";
        // 2026-06-02 hotfix R2:加 toast,治"用户焦点已移开看不到 row 内 errorMsg"
        toast.error(row.errorMsg);
      }
    } finally {
      row.savePromise = null;
    }
  })();
  row.savePromise = promise;
  await promise;
}

/**
 * INIT.5(2026-05-21):初始态世界观 6 维保存
 *   - WorldBaselineEditor emit('save', patchDict) → PATCH /projects/:id
 *   - 后端 schema 已支持 world_baseline 字段(dict[str, str])
 *   - 响应回来更新 project.value.world_baseline
 */
async function saveWorldBaseline(patch: Record<string, string>) {
  if (!project.value) return;
  try {
    const updated = await api.patch<Project>(
      `/projects/${project.value.id}`,
      { world_baseline: patch },
    );
    project.value = updated;
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "保存世界观失败");
  }
}

/**
 * SP-1(2026-05-28):故事内核三件套编辑 — 灵魂续写北极星·目的层
 *   绑定 project.value 的 3 字段,提供本地草稿 + 保存按钮.
 *   注入位置:hard_constraints section 0.0(evolution)+ simulation_service quick prepend.
 *   作用:给 LLM 一个目标弧,治"AI 没目标就提前泄气"内伤.
 */
const scDraft = ref<{
  core_dramatic_question: string;
  theme: string;
  ending_direction: string;
}>({ core_dramatic_question: "", theme: "", ending_direction: "" });
const scSaving = ref(false);

watch(
  () => project.value,
  (p) => {
    if (p) {
      scDraft.value = {
        core_dramatic_question: p.core_dramatic_question ?? "",
        theme: p.theme ?? "",
        ending_direction: p.ending_direction ?? "",
      };
    }
  },
  { immediate: true },
);

async function saveStoryCore() {
  if (!project.value || scSaving.value) return;
  scSaving.value = true;
  try {
    // 空串 → null(避免存空白)
    const payload = {
      core_dramatic_question: scDraft.value.core_dramatic_question.trim() || null,
      theme: scDraft.value.theme.trim() || null,
      ending_direction: scDraft.value.ending_direction.trim() || null,
    };
    const updated = await api.patch<Project>(
      `/projects/${project.value.id}`,
      payload,
    );
    project.value = updated;
    toast.success("故事脊柱已保存");
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "保存故事脊柱失败");
  } finally {
    scSaving.value = false;
  }
}

// ============================================================
// SP-8(2026-05-28):视角扩展三件套 — 灵魂续写北极星 · 叙述层
// ============================================================
const nvDraft = ref<{
  narrative_focus_character_id: string;     // "" = 不指定;character_id 字符串 = 指定焦点
  narrator_reliability: "" | "reliable" | "unreliable" | "uncertain";
  narrative_distance: "" | "omniscient" | "limited" | "close" | "intimate";
  // 2026-06-01:作品篇幅(决定细节颗粒度档)
  expected_length: "short" | "medium" | "long";
}>({
  narrative_focus_character_id: "",
  narrator_reliability: "",
  narrative_distance: "",
  expected_length: "medium",
});
const nvSaving = ref(false);

watch(
  () => project.value,
  (p) => {
    if (p) {
      nvDraft.value = {
        narrative_focus_character_id: p.narrative_focus_character_id ?? "",
        narrator_reliability: (p.narrator_reliability ?? "") as
          | "" | "reliable" | "unreliable" | "uncertain",
        narrative_distance: (p.narrative_distance ?? "") as
          | "" | "omniscient" | "limited" | "close" | "intimate",
        expected_length: (p.expected_length ?? "medium") as
          | "short" | "medium" | "long",
      };
    }
  },
  { immediate: true },
);

async function saveNarrativeView() {
  if (!project.value || nvSaving.value) return;
  nvSaving.value = true;
  try {
    const payload = {
      narrative_focus_character_id: nvDraft.value.narrative_focus_character_id || null,
      narrator_reliability: nvDraft.value.narrator_reliability || null,
      narrative_distance: nvDraft.value.narrative_distance || null,
      expected_length: nvDraft.value.expected_length,  // 总有值(default 'medium')
    };
    const updated = await api.patch<Project>(
      `/projects/${project.value.id}`,
      payload,
    );
    project.value = updated;
    toast.success("视角已保存");
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "保存视角失败");
  } finally {
    nvSaving.value = false;
  }
}

// ============================================================
// 章节字数区间(2026-06-01)— 全 mode 通用,影响阅读器切章 + AI 章节自觉
// 区间形式:用户编辑 1000~3000,前端切章在此区间内找段落分隔符
// ============================================================
const chapterRangeCollapsed = ref(true);
const chapterRangeDraft = ref<{ chapter_size_min: number; chapter_size_max: number }>({
  chapter_size_min: 1500,
  chapter_size_max: 2500,
});
const chapterRangeSaving = ref(false);

watch(
  () => project.value,
  (p) => {
    if (p) {
      chapterRangeDraft.value = {
        chapter_size_min: typeof p.chapter_size_min === "number" ? p.chapter_size_min : 1500,
        chapter_size_max: typeof p.chapter_size_max === "number" ? p.chapter_size_max : 2500,
      };
    }
  },
  { immediate: true },
);

async function saveChapterRange() {
  if (!project.value || chapterRangeSaving.value) return;
  let cmin = Math.round(Number(chapterRangeDraft.value.chapter_size_min) || 1500);
  let cmax = Math.round(Number(chapterRangeDraft.value.chapter_size_max) || 2500);
  // 前端兜底:必须落在合理范围,max ≥ min + 500
  if (cmin < 500) cmin = 500;
  if (cmin > 8000) cmin = 8000;
  if (cmax < 1500) cmax = 1500;
  if (cmax > 10000) cmax = 10000;
  if (cmax < cmin + 500) cmax = cmin + 500;
  chapterRangeDraft.value.chapter_size_min = cmin;
  chapterRangeDraft.value.chapter_size_max = cmax;
  chapterRangeSaving.value = true;
  try {
    const updated = await api.patch<Project>(
      `/projects/${project.value.id}`,
      { chapter_size_min: cmin, chapter_size_max: cmax },
    );
    project.value = updated;
    toast.success("章节字数区间已保存");
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "保存失败");
  } finally {
    chapterRangeSaving.value = false;
  }
}

// ============================================================
// SP-7 polarity hotfix(2026-06-01):关系 chip 内 polarity 小色点 + 点击循环
// 单条关系级别的快速切;项目级 AI 推断 polarity 已接入"一键灌满"Stage 4
// ============================================================
function polarityLabel(p: RelationshipPolarity | null | undefined): string {
  if (p === "positive") return "正面";
  if (p === "negative") return "对立";
  if (p === "neutral") return "中性";
  return "未标";
}
function polarityCycle(
  cur: RelationshipPolarity | null | undefined,
): RelationshipPolarity | null {
  if (cur === null || cur === undefined) return "positive";
  if (cur === "positive") return "negative";
  if (cur === "negative") return "neutral";
  return null;
}
const polaritySaving = ref<Set<string>>(new Set());
async function cycleRelPolarity(row: RelationshipRow, e: MouseEvent) {
  e.stopPropagation();
  const rel = row.relationship;
  if (!rel) return;
  if (polaritySaving.value.has(rel.id)) return;
  polaritySaving.value.add(rel.id);
  const next = polarityCycle(rel.polarity);
  try {
    const updated = await api.patch<Relationship>(`/relationships/${rel.id}`, {
      polarity: next,
    });
    row.relationship = updated;
  } catch (err) {
    toast.error(err instanceof ApiError ? err.message : "切换极性失败");
  } finally {
    polaritySaving.value.delete(rel.id);
  }
}

// ============================================================
// SP-1.5 / SP-8.1(2026-05-29):AI 推断故事内核 / 视角扩展
// ============================================================
// 用户主动按钮触发(项目页 sc-actions 内"✨ AI 推断"按钮)
// 后端同步等待 LLM(~10-30s),返回更新后 Project + _inference_reasoning 透出 toast

const scInferring = ref(false);
const nvInferring = ref(false);

async function inferStoryCore() {
  if (!project.value || scInferring.value) return;
  scInferring.value = true;
  try {
    const updated = await api.post<Project & { _inference_reasoning?: string }>(
      `/projects/${project.value.id}/infer/story_core`,
      {},
    );
    project.value = updated;
    // 同步 draft(刚刚 LLM 推断的值)
    scDraft.value = {
      core_dramatic_question: updated.core_dramatic_question ?? "",
      theme: updated.theme ?? "",
      ending_direction: updated.ending_direction ?? "",
    };
    const reasoning = updated._inference_reasoning ?? "";
    if (
      !updated.core_dramatic_question
      && !updated.theme
      && !updated.ending_direction
    ) {
      toast.warning(reasoning || "AI 推断未填出有效内容,请手动填写");
    } else {
      toast.success(
        reasoning
          ? `AI 已推断 ✓\n${reasoning.slice(0, 200)}${reasoning.length > 200 ? "…" : ""}`
          : "AI 推断完成",
        7000,
      );
    }
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "AI 推断失败");
  } finally {
    scInferring.value = false;
  }
}

async function inferNarrativeView() {
  if (!project.value || nvInferring.value) return;
  nvInferring.value = true;
  try {
    const updated = await api.post<
      Project & { _inference_reasoning?: string; _inference_focus_name?: string }
    >(
      `/projects/${project.value.id}/infer/narrative_view`,
      {},
    );
    project.value = updated;
    nvDraft.value = {
      narrative_focus_character_id: updated.narrative_focus_character_id ?? "",
      narrator_reliability: (updated.narrator_reliability ?? "") as
        | "" | "reliable" | "unreliable" | "uncertain",
      narrative_distance: (updated.narrative_distance ?? "") as
        | "" | "omniscient" | "limited" | "close" | "intimate",
      // 2026-06-02:expected_length 不在 narrative_view inferer 输出里,保留原值
      expected_length: nvDraft.value.expected_length,
    };
    const reasoning = updated._inference_reasoning ?? "";
    const focusName = updated._inference_focus_name ?? "";
    // 给特殊提示:如果 LLM 输出了 focus name 但 service 没匹配到 character_id
    if (
      focusName
      && !updated.narrative_focus_character_id
    ) {
      toast.warning(
        `AI 建议焦点角色:「${focusName}」,但项目角色列表中没找到匹配,请手动选择或先建该角色`,
        9000,
      );
    } else if (
      !updated.narrative_focus_character_id
      && !updated.narrator_reliability
      && !updated.narrative_distance
    ) {
      toast.warning(reasoning || "AI 推断未填出有效内容");
    } else {
      toast.success(
        reasoning
          ? `AI 已推断 ✓\n${reasoning.slice(0, 200)}${reasoning.length > 200 ? "…" : ""}`
          : "AI 推断完成",
        7000,
      );
    }
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "AI 推断失败");
  } finally {
    nvInferring.value = false;
  }
}

// SP-2.1:单角色驱动 AI 推断(在 drawer 内调用,定位当前 editingCharRow)
const driversInferring = ref(false);
const driversOverwrite = ref(false);

async function inferCharacterDrivers() {
  const row = editingCharRow.value;
  if (!row?.character || driversInferring.value) return;
  driversInferring.value = true;
  try {
    const resp = await api.post<{
      character: Character;
      reasoning: string;
      applied_overwrite: boolean;
    }>(
      `/characters/${row.character.id}/infer/drivers?overwrite=${driversOverwrite.value}`,
      {},
    );
    // 同步 row 与 character
    row.character = resp.character;
    row.editSurfaceGoal = resp.character.surface_goal ?? "";
    row.editDeepNeed = resp.character.deep_need ?? "";
    row.editFatalBlindSpot = resp.character.fatal_blind_spot ?? "";
    row.editArcFromTo = resp.character.arc_from_to ?? "";
    row.editSecretsRaw = secretsRawFromList(resp.character.secrets);
    const r = resp.reasoning;
    toast.success(
      r
        ? `AI 已推断 ✓ ${resp.applied_overwrite ? "(已覆盖)" : "(仅填空字段)"}\n${r.slice(0, 200)}${r.length > 200 ? "…" : ""}`
        : "AI 推断完成",
      7000,
    );
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "AI 推断失败");
  } finally {
    driversInferring.value = false;
  }
}

// ============================================================
// 一键灌满北极星(2026-05-29 末)— 编排 4+N 个 inferer
// 后端串行调用所有 inferer + 异常隔离,返完整 report
//
// hotfix(2026-06-01):state + SSE 全部上提到 useInferenceStore,
// 切换项目时 ProjectView unmount 但 store 不销毁 → SSE 不中断 → 切回来还能看到在跑.
// ============================================================

// 本地只保留"用户输入的选项"(checkbox 状态)— 进度状态读 store
const allBoardsForceKnowledge = ref(false);
const allBoardsIncludeDrivers = ref(true);

const inferenceStore = useInferenceStore();

// 读 store 派生 — 只有当 store 跑的就是本项目时才显示进度
const allBoardsLoading = computed(() =>
  inferenceStore.isRunningForProject(props.id),
);
// 2026-06-02 cleanup:allBoardsHasHistory 派生未用 — 已被 stageHistory.length 替代
const allBoardsTotalStages = computed(() => inferenceStore.totalStages);
const allBoardsCurrentIndex = computed(() => inferenceStore.currentIndex);
const allBoardsCurrentLabel = computed(() => inferenceStore.currentLabel);
const allBoardsStageHistory = computed(() => inferenceStore.stageHistory);

async function inferAllBoards() {
  if (!project.value) return;
  // store 自己做 loading 互斥,这里再 check 一下避免多余调用
  if (inferenceStore.loading) {
    if (inferenceStore.projectId === props.id) {
      // 本项目已在跑 — 静默忽略
      return;
    }
    // 其他项目在跑 — 提醒
    toast.warning("已有另一个项目在跑 AI 推断,请等它完成再启动新的", 6000);
    return;
  }

  await inferenceStore.startAllBoardsInference(props.id, {
    includeDrivers: allBoardsIncludeDrivers.value,
    forceKnowledge: allBoardsForceKnowledge.value,
    onFinish: async ({ report, forceKnowledge, fatalError }) => {
      if (fatalError) {
        toast.error(`一键灌满异常:${fatalError}`, 10000);
        return;
      }
      if (!report) {
        toast.warning("流提前结束,未拿到完整报告", 8000);
        return;
      }
      // reload 整个项目数据(但用户可能已经切走了 — 仍然 reload 是 OK 的,
      // 因为 loadAll 用 props.id 拉数据;切走的话 ProjectView 已 unmount 不会渲染)
      await loadAll();
      const stats = report.stats;
      const sc = report.story_core?.applied ? "✓" : "✗";
      const nv = report.narrative_view?.applied ? "✓" : "✗";
      const kbMode = forceKnowledge ? "重建" : "新增";
      const kb = report.knowledge_boundaries?.applied
        ? `✓ ${kbMode} ${report.knowledge_boundaries.facts_created} 条事实`
        : (report.knowledge_boundaries?.skipped_reason
          ? `跳过:${report.knowledge_boundaries.skipped_reason}`
          : "✗");
      const driversSummary = (() => {
        const list = report.character_drivers ?? [];
        if (list.length === 0) return "未推";
        const ok = list.filter((d) => d.applied).length;
        return `${ok}/${list.length} 角色`;
      })();
      const summary =
        `故事脊柱 ${sc} · 视角 ${nv} · 知识边界 ${kb} · 角色驱动 ${driversSummary}`;
      if (stats.stages_succeeded === 0) {
        toast.error(
          `一键灌满未能写入任何字段:${stats.failures.join(", ") || "全部 LLM 失败"}`,
          10000,
        );
      } else if (stats.failures.length > 0) {
        toast.warning(
          `部分成功(${stats.stages_succeeded}/${stats.stages_attempted})\n${summary}`,
          10000,
        );
      } else {
        toast.success(
          `AI 一键灌满完成 ✓\n${summary}`,
          10000,
        );
      }
    },
  });
}

/**
 * Sprint 6.A2 FOCUS.2(2026-05-21):叙述视角 chip 助手
 *   povLabel:把枚举映射成人类可读文本
 *   onPovChange:select change 时 PATCH 到后端;空字符串 → 后端 narrative_pov=null
 */
function povLabel(pov: Project["narrative_pov"]): string {
  if (!pov) return "未识别";
  if (pov === "first") return "第一人称(我)";
  if (pov === "second") return "第二人称(你)";
  if (pov === "third") return "第三人称(他/她)";
  if (pov === "mixed") return "多视角混合";
  return "未识别";
}

async function onPovChange(event: Event) {
  if (!project.value) return;
  const target = event.target as HTMLSelectElement;
  const raw = target.value;
  // 空字符串 = 用户选"— 视角未识别 —" → 清空(后端存 NULL)
  const newPov = raw === "" ? null : (raw as NonNullable<Project["narrative_pov"]>);
  if (newPov === project.value.narrative_pov) return;   // 没变化
  try {
    const updated = await api.patch<Project>(
      `/projects/${project.value.id}`,
      { narrative_pov: newPov },
    );
    project.value = updated;
    toast.success(`叙述视角已更新为「${povLabel(newPov)}」`);
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "保存叙述视角失败");
    // 失败时还原 select 显示
    target.value = project.value.narrative_pov ?? "";
  }
}

/**
 * INIT.3(2026-05-21):行为基线 view 模式 chip helper
 *   hasBaselineSet:任一维度有值就显 chip
 *   formatBaselineSummary:chip 上显短摘要 — "卑微·4·灰"(空维度跳过)
 *   formatBaselineTitle:hover tooltip 完整内容
 *   P0G.2(2026-05-24):删 out_of_baseline_examples 字段引用 — 雷区已合并进 no_go_list
 */
function hasBaselineSet(c: Character): boolean {
  const b = c.behavior_baseline;
  if (!b) return false;
  return (
    !!b.speech_register ||
    b.emotional_intensity !== null && b.emotional_intensity !== undefined ||
    !!b.moral_compass
  );
}
function formatBaselineSummary(c: Character): string {
  const b = c.behavior_baseline;
  if (!b) return "";
  const parts: string[] = [];
  if (b.speech_register) parts.push(b.speech_register);
  if (b.emotional_intensity !== null && b.emotional_intensity !== undefined) {
    parts.push(String(b.emotional_intensity));
  }
  if (b.moral_compass) parts.push(b.moral_compass);
  return parts.join("·") || "(未设维度)";
}
function formatBaselineTitle(c: Character): string {
  const b = c.behavior_baseline;
  if (!b) return "";
  const lines = ["行为基线(AI 一致性自检消费):"];
  if (b.speech_register) lines.push(`· 语气登记:${b.speech_register}`);
  if (b.emotional_intensity !== null && b.emotional_intensity !== undefined) {
    lines.push(`· 情绪强度基线:${b.emotional_intensity} / 10`);
  }
  if (b.moral_compass) lines.push(`· 道德罗盘:${b.moral_compass}`);
  return lines.join("\n");
}

/**
 * INIT.4(2026-05-21):初始态主角钉死
 *   - 点击 chip toggle is_protagonist;后端自动设 protagonist_user_pinned=true
 *   - 钉死后 AI 后续重判不会覆盖用户的决定
 *   - @click.stop 防止冒泡到 row 触发 edit mode
 */
async function toggleProtagonist(row: CharacterRow, event: Event) {
  event.stopPropagation();
  if (!row.character) return;
  const nextValue = !row.character.is_protagonist;
  try {
    const updated = await api.patch<Character>(
      `/characters/${row.character.id}`,
      { is_protagonist: nextValue },
    );
    row.character = updated;
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "切换主角失败");
  }
}

async function deleteCharRow(row: CharacterRow) {
  if (!row.character) {
    // 占位行,直接清掉本地
    row.editName = "";
    row.editIdentity = "";
    row.mode = "view";
    return;
  }
  try {
    const deletedId = row.character.id;
    await api.delete(`/characters/${deletedId}`);
    // 移除该行
    const idx = characterRows.value.indexOf(row);
    if (idx >= 0) characterRows.value.splice(idx, 1);
    // 同时剥离引用该角色的关系行(后端有 ON DELETE CASCADE,前端这步避免 reload 前 UI 残留)
    relationshipRows.value = relationshipRows.value.filter(
      (r) => !r.relationship ||
             (r.relationship.source_id !== deletedId &&
              r.relationship.target_id !== deletedId),
    );
    // 同步过滤 events.participants 里的此 id(后端 DELETE 已级联清理 DB,
    // 前端这步是为了 reload 前本地 UI 立刻干净 — 避免显示"?"残留)
    for (const er of eventRows.value) {
      if (!er.event) continue;
      const before = er.event.participants.length;
      er.event.participants = er.event.participants.filter((pid) => pid !== deletedId);
      er.editParticipants = er.editParticipants.filter((pid) => pid !== deletedId);
      if (before !== er.event.participants.length && er.editParticipants.length === 0) {
        // 全部参与者被清空 — UI 仍保留事件本身,只是不显参与者
      }
    }
  } catch (err) {
    errorMessage.value = err instanceof ApiError ? err.message : "删除失败";
  }
}

function onCharKey(row: CharacterRow, _idx: number, e: KeyboardEvent) {
  if (e.key === "Enter") {
    // textarea 的 Enter 是换行(台词/禁忌/性格的多行编辑刚需),不能拦
    // 只有单行 input 的 Enter 才触发 blur 保存
    if ((e.currentTarget as HTMLElement).tagName === "TEXTAREA") return;
    e.preventDefault();
    // currentTarget 是绑定 handler 的元素本身,比 target 稳;
    // blur 会触发 @blur → tryPersistChar → 保存
    (e.currentTarget as HTMLElement).blur();
  } else if (e.key === "Escape") {
    e.preventDefault();
    cancelCharEdit(row);
  }
}

function cancelCharEdit(row: CharacterRow) {
  if (row.character) {
    row.editName = row.character.name;
    row.editIdentity = row.character.identity;
    row.editPersonality = row.character.personality;
    row.editQuotesRaw = (row.character.quotes ?? []).join("\n");
    row.editNoGoRaw = (row.character.no_go_list ?? []).join("\n");
    // INIT.3:还原 4 维度行为基线
    row.editSpeechRegister = (row.character.behavior_baseline?.speech_register ?? "") as SpeechRegister | "";
    row.editEmotionalIntensity = row.character.behavior_baseline?.emotional_intensity ?? null;
    row.editMoralCompass = (row.character.behavior_baseline?.moral_compass ?? "") as MoralCompass | "";
    row.editBaselineOutRaw = (row.character.behavior_baseline?.out_of_baseline_examples ?? []).join("\n");
  } else {
    row.editName = "";
    row.editIdentity = "";
    row.editPersonality = "";
    row.editQuotesRaw = "";
    row.editNoGoRaw = "";
    // INIT.3:占位行回到全空
    row.editSpeechRegister = "";
    row.editEmotionalIntensity = null;
    row.editMoralCompass = "";
    row.editBaselineOutRaw = "";
  }
  row.mode = "view";
  row.errorMsg = null;
}

// ============================================================
// 关系:占位行创建 + 删除(改留下个 sprint)
// ============================================================

/**
 * INIT.1 fix(2026-05-21):从哪个角色卡发起 edit。
 * fromCharId 必填 — 模板只有 `editingFromCharId === row.character.id` 的卡显示 edit form,
 * 另一边卡保持 view chip(共享同一 relationship reactive 引用,PATCH 后自动更新)。
 */
function enterRelEdit(row: RelationshipRow, fromCharId: string) {
  if (row.mode === "saving") return;
  closeOtherEdits(row);
  row.mode = "edit";
  row.errorMsg = null;
  row.editingFromCharId = fromCharId;
}

async function tryPersistRel(row: RelationshipRow, idx: number, e?: FocusEvent) {
  if (e && focusStillInRow(rowKeyRel(idx), e)) return;
  if (row.mode !== "edit") return;

  const src = row.editSource;
  const tgt = row.editTarget;
  const desc = row.editDesc.trim();

  // 必填空 → 不动(用户还在填)
  if (!src || !tgt) return;
  if (src === tgt) {
    row.errorMsg = "角色不能与自己建立关系";
    return;
  }

  if (row.relationship) {
    // ===== PATCH 分支:已存在关系 =====
    // source / target 不允许改(等同新关系),只能改 type 和 description
    const body: UpdateRelationshipRequest = {};
    if (row.editType !== row.relationship.type) body.type = row.editType;
    if (desc !== row.relationship.description) body.description = desc;

    if (Object.keys(body).length === 0) {
      row.mode = "view";
      row.errorMsg = null;
      row.editingFromCharId = null;
      return;
    }

    row.mode = "saving";
    row.errorMsg = null;
    const promise = (async () => {
      try {
        // Sprint D.7:guarded 早退替代非空断言(到此处 row.relationship 必有,但 TS 不确定)
        if (!row.relationship) {
          row.mode = "view";
          row.editingFromCharId = null;
          return;
        }
        const updated = await api.patch<Relationship>(
          `/relationships/${row.relationship.id}`,
          body,
        );
        row.relationship = updated;
        row.editType = updated.type;
        row.editDesc = updated.description;
        row.mode = "view";
        row.editingFromCharId = null;
      } catch (err) {
        row.errorMsg = err instanceof ApiError ? err.message : "保存失败";
        row.mode = "edit";
      } finally {
        row.savePromise = null;
      }
    })();
    row.savePromise = promise;
    await promise;
    return;
  }

  // ===== POST 分支:占位行新建 =====
  row.mode = "saving";
  row.errorMsg = null;
  const promise = (async () => {
    try {
      const created = await api.post<Relationship>(
        `/projects/${props.id}/relationships`,
        {
          source_id: src,
          target_id: tgt,
          type: row.editType,
          description: desc,
        },
      );
      row.relationship = created;
      row.mode = "view";
      if (relationshipRows.value[relationshipRows.value.length - 1] === row) {
        relationshipRows.value.push(makeNewRelRow());
      }
      // INIT.1:角色卡 inline add-form 成功保存后,关闭 add-form 状态
      if (addingRelForCharId.value === src) {
        addingRelForCharId.value = null;
      }
    } catch (err) {
      row.errorMsg = err instanceof ApiError ? err.message : "保存失败";
      row.mode = "edit";
    } finally {
      row.savePromise = null;
    }
  })();
  row.savePromise = promise;
  await promise;
}

function onRelKey(row: RelationshipRow, _idx: number, e: KeyboardEvent) {
  if (e.key === "Enter") {
    e.preventDefault();
    // select 在 Chrome 上 Enter 不会 blur,必须主动调
    (e.currentTarget as HTMLElement).blur();
  } else if (e.key === "Escape") {
    e.preventDefault();
    cancelRelEdit(row);
  }
}

function cancelRelEdit(row: RelationshipRow) {
  if (row.relationship) {
    // 还原到上次保存的值
    row.editSource = row.relationship.source_id;
    row.editTarget = row.relationship.target_id;
    row.editType = row.relationship.type;
    row.editDesc = row.relationship.description;
  } else {
    row.editSource = "";
    row.editTarget = "";
    row.editType = "朋友";
    row.editDesc = "";
  }
  row.mode = "view";
  row.errorMsg = null;
  row.editingFromCharId = null;
  // INIT.1:cancel 占位行 → 关闭 add-form 状态
  if (!row.relationship && addingRelForCharId.value) {
    addingRelForCharId.value = null;
  }
}

async function deleteRelRow(row: RelationshipRow) {
  if (!row.relationship) {
    // 占位行直接清空
    row.editSource = "";
    row.editTarget = "";
    row.editType = "朋友";
    row.editDesc = "";
    row.mode = "view";
    return;
  }
  try {
    await api.delete(`/relationships/${row.relationship.id}`);
    const idx = relationshipRows.value.indexOf(row);
    if (idx >= 0) relationshipRows.value.splice(idx, 1);
  } catch (err) {
    errorMessage.value = err instanceof ApiError ? err.message : "删除失败";
  }
}

// ============================================================
// 事件:占位行创建 + 删除
// ============================================================

function enterEventEdit(row: EventRow) {
  if (row.mode === "saving") return;
  closeOtherEdits(row);
  row.mode = "edit";
  row.errorMsg = null;
}

async function tryPersistEvent(row: EventRow, idx: number, e?: FocusEvent) {
  if (e && focusStillInRow(rowKeyEvent(idx), e)) return;
  if (row.mode !== "edit") return;

  const desc = row.editDesc.trim();

  if (row.event) {
    // ===== PATCH 分支:已存在事件 =====
    if (!desc) {
      row.errorMsg = "事件描述不能为空";
      return;
    }
    const body: UpdateEventRequest = {};
    if (desc !== row.event.description) body.description = desc;
    const newParts = [...row.editParticipants].sort();
    const oldParts = [...row.event.participants].sort();
    if (JSON.stringify(newParts) !== JSON.stringify(oldParts)) {
      body.participants = [...row.editParticipants];
    }
    // INIT.7(2026-05-21):time_anchor 变化时 PATCH
    const newAnchor = row.editTimeAnchor.trim();
    const oldAnchor = row.event.time_anchor ?? "";
    if (newAnchor !== oldAnchor) {
      body.time_anchor = newAnchor;
    }

    if (Object.keys(body).length === 0) {
      row.mode = "view";
      row.errorMsg = null;
      return;
    }

    row.mode = "saving";
    row.errorMsg = null;
    const promise = (async () => {
      try {
        // Sprint D.7:guarded 早退替代非空断言
        if (!row.event) {
          row.mode = "view";
          return;
        }
        const updated = await api.patch<ProjectEvent>(
          `/events/${row.event.id}`,
          body,
        );
        row.event = updated;
        row.editDesc = updated.description;
        row.editParticipants = [...updated.participants];
        row.editTimeAnchor = updated.time_anchor ?? "";
        row.mode = "view";
      } catch (err) {
        row.errorMsg = err instanceof ApiError ? err.message : "保存失败";
        row.mode = "edit";
      } finally {
        row.savePromise = null;
      }
    })();
    row.savePromise = promise;
    await promise;
    return;
  }

  // ===== POST 分支:占位行新建 =====
  if (!desc) {
    // 描述空 → 取消
    row.editParticipants = [];
    row.mode = "view";
    return;
  }

  row.mode = "saving";
  row.errorMsg = null;
  const promise = (async () => {
    try {
      const created = await api.post<ProjectEvent>(
        `/projects/${props.id}/events`,
        {
          description: desc,
          participants: [...row.editParticipants],
          // INIT.7(2026-05-21):新建事件时带 time_anchor
          time_anchor: row.editTimeAnchor.trim() || undefined,
        },
      );
      row.event = created;
      row.editTimeAnchor = created.time_anchor ?? "";
      row.mode = "view";
      if (eventRows.value[eventRows.value.length - 1] === row) {
        eventRows.value.push(makeNewEventRow());
      }
    } catch (err) {
      row.errorMsg = err instanceof ApiError ? err.message : "保存失败";
      row.mode = "edit";
    } finally {
      row.savePromise = null;
    }
  })();
  row.savePromise = promise;
  await promise;
}

function onEventKey(row: EventRow, _idx: number, e: KeyboardEvent) {
  if (e.key === "Enter") {
    e.preventDefault();
    (e.currentTarget as HTMLElement).blur();
  } else if (e.key === "Escape") {
    e.preventDefault();
    cancelEventEdit(row);
  }
}

function cancelEventEdit(row: EventRow) {
  if (row.event) {
    row.editDesc = row.event.description;
    row.editParticipants = [...row.event.participants];
    row.editTimeAnchor = row.event.time_anchor ?? "";
  } else {
    row.editDesc = "";
    row.editParticipants = [];
    row.editTimeAnchor = "";
  }
  row.mode = "view";
  row.errorMsg = null;
}

async function deleteEventRow(row: EventRow) {
  if (!row.event) {
    row.editDesc = "";
    row.editParticipants = [];
    row.mode = "view";
    return;
  }
  try {
    await api.delete(`/events/${row.event.id}`);
    const idx = eventRows.value.indexOf(row);
    if (idx >= 0) eventRows.value.splice(idx, 1);
  } catch (err) {
    errorMessage.value = err instanceof ApiError ? err.message : "删除失败";
  }
}

function toggleEventParticipant(row: EventRow, charId: string) {
  const i = row.editParticipants.indexOf(charId);
  if (i >= 0) row.editParticipants.splice(i, 1);
  else row.editParticipants.push(charId);
}

// ============================================================
// 查看图谱:先 blur 触发保存 + 等 saving 完 + 校验脏行
// ============================================================

// ============================================================
// AI 角色对焦 modal
// ============================================================

const focusOpen = ref(false);

async function openFocus() {
  // 打开前先把脏 input 提交(同 viewGraph)
  if (document.activeElement instanceof HTMLElement) {
    document.activeElement.blur();
  }
  await nextTick();

  const realChars = characterRows.value.filter((r) => r.character);
  if (realChars.length < 3) {
    errorMessage.value = `AI 角色对焦至少需要 3 个角色,当前 ${realChars.length} 个`;
    return;
  }
  errorMessage.value = null;
  focusOpen.value = true;
}

/**
 * 对焦 modal 关闭(用户主动关 / 全部审完 / 配额超限自动 close([]) )。
 * - refinedIds.length > 0:有 accept/edit 落库,reload 同步前端展示
 *   (后端已经把内容写进 character.identity / disambiguation / reserved_terms)
 * - 始终 quota.refresh():无论成功失败,/refine 端点都已记一次 refine_count
 *   或在 quota 拒绝时直接 429,sidebar 进度条都需要立刻同步
 */
async function onFocusClose(refinedIds: string[]) {
  focusOpen.value = false;
  if (refinedIds.length > 0) {
    await loadAll();
  }
  void quota.refresh();
}

/**
 * 对焦 modal 在 start() 撞上 QUOTA_EXCEEDED 时会先 emit('quota-exceeded', detail)
 * 再 emit('close', []),所以这里只负责弹升级 modal,关闭 focus modal 的事
 * 由 onFocusClose 接管。
 */
function onFocusQuotaExceeded(detail: QuotaExceededDetail | InsufficientCreditsDetail) {
  // Sprint C.3:credit 不足 → 弹加购(更贴当下);资源容量类 → 升档
  if ((detail as { code?: string }).code === "INSUFFICIENT_CREDITS") {
    addonModal.open();
  } else {
    upgradeModal.open(detail);
  }
}

// ============================================================
// AI 续写 dock — Sprint 1.H
// ============================================================

const simulateOpen = ref(false);

const realCharacterCount = computed(
  () => characterRows.value.filter((r) => r.character).length,
);

// ============================================================
// Sprint 2.C+ 反事实工作台(中间态 / 周期态用)
// ============================================================

const cf = useCounterfactuals(props.id);
const workbenchOpen = ref(false);
// Workbench 需要的 characters / events 完整列表(打开时拉一次)
const wbCharsList = ref<Character[]>([]);
const wbEventsList = ref<ProjectEvent[]>([]);

watch(
  () => props.id,
  (pid) => {
    if (pid) {
      cf.bind(pid);
      void cf.reload();
    }
  },
  { immediate: true },
);

const cfTotalActive = computed(() => cf.totalActive.value);

async function openCounterfactualWorkbench() {
  try {
    const [chars, evts] = await Promise.all([
      api.get<Character[]>(`/projects/${props.id}/characters`),
      api.get<ProjectEvent[]>(`/projects/${props.id}/events`),
    ]);
    wbCharsList.value = chars;
    wbEventsList.value = evts;
  } catch {
    wbCharsList.value = [];
    wbEventsList.value = [];
  }
  workbenchOpen.value = true;
}

async function onWorkbenchChanged() {
  // 反事实创建 / 撤销 → reload 项目让字段反映回(撤销时 db 已还原)
  await loadAll();
}

async function openSimulate() {
  // 同 openFocus:打开前 blur 把脏 input 提交,避免推演时角色 snapshot 丢用户最后改动
  if (document.activeElement instanceof HTMLElement) {
    document.activeElement.blur();
  }
  await nextTick();
  errorMessage.value = null;
  simulateOpen.value = true;
}

function onSimulateClose() {
  simulateOpen.value = false;
  // 推演创建时已扣 continuation 配额,刷新 sidebar 进度条
  void quota.refresh();
}

function onSimulateQuotaExceeded(detail: QuotaExceededDetail | InsufficientCreditsDetail) {
  // Sprint C.3:credit 不足 → 弹加购(更贴当下);资源容量类 → 升档
  if ((detail as { code?: string }).code === "INSUFFICIENT_CREDITS") {
    addonModal.open();
  } else {
    upgradeModal.open(detail);
  }
}

// ============================================================
// 查看 3D 图谱
// ============================================================

const goingToGraph = ref(false);

async function viewGraph() {
  goingToGraph.value = true;
  try {
    // 1. 强制失焦当前 input,触发自动保存
    if (document.activeElement instanceof HTMLElement) {
      document.activeElement.blur();
    }
    await nextTick();

    // 2. 等待所有 saving 状态完成
    const allRows = [
      ...characterRows.value,
      ...relationshipRows.value,
      ...eventRows.value,
    ];
    const savings = allRows.map((r) => r.savePromise).filter(Boolean) as Promise<void>[];
    if (savings.length > 0) await Promise.allSettled(savings);

    // 3. 检查脏行(有内容但仍 edit 态,说明保存失败或必填缺字段)
    const dirtyChars = characterRows.value.filter(
      (r) => r.mode === "edit" && r.editName.trim() && (!r.character),
    );
    if (dirtyChars.length > 0) {
      // 滚动到第一个 + focus
      const idx = characterRows.value.indexOf(dirtyChars[0]);
      const el = rowRefs.get(rowKeyChar(idx));
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        const input = el.querySelector("input");
        if (input instanceof HTMLInputElement) input.focus();
      }
      errorMessage.value = `还有 ${dirtyChars.length} 个角色没保存好,请先处理`;
      return;
    }

    // 4. 至少要有 1 个角色才能去看图谱(空图谱无意义)
    const realChars = characterRows.value.filter((r) => r.character);
    if (realChars.length === 0) {
      errorMessage.value = "至少创建 1 个角色后再查看图谱";
      return;
    }

    // 5. 跳转
    router.push(`/projects/${props.id}/graph`);
  } finally {
    goingToGraph.value = false;
  }
}

// Sprint 2.E polish:Vue Router 切换 /projects/A → /projects/B 时复用同一个
// ProjectView 实例,只换 props.id;原本 onMounted(loadAll) 只跑一次 → 切项目后
// 数据是上一个项目的,顶栏 chip / 角色列表都不刷新。改 immediate watch 修这个。
watch(
  () => props.id,
  () => { void loadAll(); },
  { immediate: true },
);

// M7.I / M7.K(2026-05-20)— 来自 SimulationDetailView 的两种跳转链路:
//   I. ?continueFrom=<simId>:基于本篇续写(M7.I,滚雪球预填)
//   K. ?continueFrom=<simId> + ?applySimPatch=<base64>:LLM-only 采纳 patch(M7.K)
// 项目加载就绪 → 自动打开 SimulationDock + 应用对应预填;清掉 query 防刷新重复
//
// Bug 修复(2026-05-22):#2.5 patch 持久化后,continueFromIds 多了一条兜底链路 —
// 从 pendingPatches[最新].source_sim_id 取 sim id;这让用户**每次进项目**(无 URL query
// 也无任何主动操作)只要有 pending patch 就 continueFromIds 非空 → 此 watch 自动开 dock。
// 修法:**加 hasUrlTrigger 校验**,只有 URL 显式带 ?continueFrom/?applySimPatch 才自动开 dock;
// pendingPatches 兜底仅作"用户主动点 AI 续写时 dock 默认勾本篇为前文 + 预填字段",不再自动弹。
watch(
  [continueFromIds, () => project.value],
  ([ids, proj]) => {
    const hasUrlTrigger =
      !!route.query.continueFrom || !!route.query.applySimPatch;
    if (
      hasUrlTrigger
      && ids.length > 0
      && proj
      && !simulateOpen.value
      && !aiActionsDisabledReason.value
    ) {
      void openSimulate();
      router.replace({
        query: {
          ...route.query,
          continueFrom: undefined,
          applySimPatch: undefined,    // M7.K:patch query 也一并清
          auditSource: undefined,      // M7.K-fix:issue label query 也一并清
        },
      });
    }
  },
  { immediate: true },
);

// ============================================================
// Sprint 6.A2 M7.D-fix(2026-05-20)— Tab 切换
//   activeTab='simulations' (默认) → 显示该项目所有 sim 列表(作品列表)
//   activeTab='graph'              → 显示图谱编辑面(原 ProjectView 全部内容)
//
// 默认进入作品列表:用户进项目后**先看到作品列表**(用户的核心需求),
// 想编辑图谱时再切到「图谱」Tab。
// 切项目时 reset 到 'simulations'(immediate watch)。
// ============================================================

// P3 Day 3.2(2026-05-26):加 "作者指南针" tab — 让续作"像川端 / 像村上"的入口
type ProjectTab = "simulations" | "graph" | "compass";
// Sprint 6.A2 路线图 #6(2026-05-23):支持 ?tab=graph URL query 让搜索跳转直接进图谱编辑 tab
function resolveTabFromUrl(q: unknown): ProjectTab {
  if (q === "graph") return "graph";
  if (q === "compass") return "compass";
  return "simulations";
}
const initialTab = resolveTabFromUrl(route.query.tab);
const activeTab = ref<ProjectTab>(initialTab);

/*
  UI 优化(2026-05-21 十一轮):图谱编辑 tab 两个 section 手风琴联动
    - protagWallRef / sceneGraphRef:通过 defineExpose 拿到子组件的 collapseSection()
    - onProtagExpanded:用户刚展开 protag → 把 scene 折叠
    - onSceneExpanded:反之
    - 不持有 collapsed 状态(状态留在子组件内),只协调"互斥"
*/
const protagWallRef = ref<InstanceType<typeof ProtagonistWall> | null>(null);
const sceneGraphRef = ref<InstanceType<typeof SceneGraph> | null>(null);
function onProtagExpanded() {
  sceneGraphRef.value?.collapseSection();
}
function onSceneExpanded() {
  protagWallRef.value?.collapseSection();
}

// Sprint 6.A2 #2 二期(2026-05-22):角色情绪总览模态(跨多次推演的情绪轨迹)
// 入口:ProtagonistWall 角色卡展开 "📊 情绪总览" 按钮 → emit('open-emotion-overview', {id, name})
const emotionOverviewOpen = ref(false);
const emotionOverviewCharId = ref("");
const emotionOverviewCharName = ref("");
function openEmotionOverview(payload: { id: string; name: string }) {
  emotionOverviewCharId.value = payload.id;
  emotionOverviewCharName.value = payload.name;
  emotionOverviewOpen.value = true;
}

// 切项目时重置 tab(若 URL 带 ?tab=graph / ?tab=compass 则用对应 tab,否则默认 simulations)
watch(
  () => props.id,
  () => {
    activeTab.value = resolveTabFromUrl(route.query.tab);
  },
);

// Sprint 6.A2 路线图 #6(2026-05-23):URL ?tab= 变化时同步(全局搜索跳转到同项目不同 tab 时生效)
watch(
  () => route.query.tab,
  (newTab) => {
    if (newTab === "graph") activeTab.value = "graph";
    else if (newTab === "compass") activeTab.value = "compass";
  },
);

// Bug 修复(2026-05-23 B3):activeTab 反向同步到 URL query.tab
// 之前只单向(URL → activeTab)。结果:用户手动切回作品列表 tab 后,URL 仍是 ?tab=graph
// → 再次搜索点同项目角色 → router.push 同 URL = no-op → watch 不触发 → activeTab 卡死
// 修法:每次 activeTab 变化,同步 URL,保证双向一致
watch(activeTab, (newTab) => {
  const currentTabInUrl = route.query.tab;
  if (newTab === "graph" && currentTabInUrl !== "graph") {
    void router.replace({ query: { ...route.query, tab: "graph" } });
  } else if (newTab === "compass" && currentTabInUrl !== "compass") {
    void router.replace({ query: { ...route.query, tab: "compass" } });
  } else if (newTab === "simulations" && currentTabInUrl !== undefined) {
    // 用对象 rest 删 tab 键
    const newQuery = { ...route.query };
    delete newQuery.tab;
    void router.replace({ query: newQuery });
  }
});
</script>

<template>
  <div class="project-page" @click="onPageBlankClick">
    <!-- 顶栏 — UI 优化(2026-05-21 二轮):tags 改为书名下方的副标题元数据
         不再独立成行通栏 + 不再是 chip 框,而是以 · 分隔的小灰文本,贴近书名
         视觉上"项目名 + 标签"成为一个语义单元,与右侧按钮组并列 -->
    <header class="topbar">
      <div class="topbar-left">
        <h1 v-if="project" class="page-title">
          {{ project.name }}
          <span class="mode-chip-inline">{{ projectModeLabel }}</span>
          <!--
            Sprint 6.A2 FOCUS.2(2026-05-21):叙述视角 chip — 显示 + 一键改
              null = 未识别 → 灰色"未识别"chip;用户可下拉指定
              first/second/third/mixed = 已识别 → 紫色 chip 显示对应文本
            点击 chip 弹出下拉 select 修改;PATCH narrative_pov 到后端
          -->
          <select
            v-if="project"
            class="pov-chip-select"
            :class="{ 'pov-chip-select--unset': !project.narrative_pov }"
            :value="project.narrative_pov ?? ''"
            :title="`作品叙述视角(续写时强制延续)\n当前:${povLabel(project.narrative_pov)}`"
            @change="onPovChange($event)"
          >
            <option value="">— 视角未识别 —</option>
            <option value="first">第一人称(我)</option>
            <option value="second">第二人称(你)</option>
            <option value="third">第三人称(他/她)</option>
            <option value="mixed">多视角混合</option>
          </select>
        </h1>
        <p
          v-if="project && project.tags.length"
          class="page-tags-subtitle"
          :title="project.tags.join(' · ')"
        >
          {{ project.tags.join(" · ") }}
        </p>
      </div>
      <div class="topbar-right">
        <!-- Sprint 2.C+ 中间态 / 周期态:反事实工作台入口 — 让用户不必走 3D 图谱也能改 what-if
             Sprint 3.A 修:末尾态(end)语义是"不动原作,从末尾接着写",反事实工作台
             与末尾态心智冲突,显式排除(初始态本来就排除,末尾态也排除)。 -->
        <button
          v-if="projectMode === 'middle' || projectMode === 'cycle'"
          class="secondary-btn cf-workbench-btn"
          :disabled="loading || !project"
          title="反事实工作台 — 改角色 / 事件 / 世界观 → AI 推演 what-if"
          @click="openCounterfactualWorkbench"
        >
          <span class="cf-icon">⟲</span>
          反事实工作台
          <span v-if="cfTotalActive > 0" class="cf-count mono">{{ cfTotalActive }}</span>
        </button>
        <!-- Sprint 6.A2 polish(2026-05-23):AI 角色对焦从顶部全局 header 移到图谱编辑 tab 内
             作品列表 tab 没角色元素,全局按钮反而困惑用户;移到 tab 内做语境化入口 -->
        <button
          class="secondary-btn"
          :disabled="simulateOpen || loading || !project || !!aiActionsDisabledReason"
          :title="aiActionsDisabledReason || simulateButtonTitle"
          @click="openSimulate"
        >
          <Icon :name="simulateButtonLabel.icon" :size="14" />
          {{ simulateButtonLabel.text }}
        </button>
        <button
          class="primary-btn"
          :disabled="goingToGraph || !!aiActionsDisabledReason"
          :title="aiActionsDisabledReason || '查看 3D 关系图谱'"
          @click="viewGraph"
        >
          {{ goingToGraph ? "保存中…" : "查看 3D 图谱" }}
          <Icon v-if="!goingToGraph" name="arrow_right" :size="14" />
        </button>
      </div>
    </header>

    <!-- M7.D-fix(2026-05-20)Tab 切换 — 作品列表 / 图谱编辑 -->
    <nav v-if="project" class="project-tabs" role="tablist">
      <button
        type="button"
        role="tab"
        class="project-tab"
        :class="{ 'project-tab--active': activeTab === 'simulations' }"
        :aria-selected="activeTab === 'simulations'"
        @click="activeTab = 'simulations'"
      >
        <span class="tab-icon">📋</span>
        <span>作品列表</span>
      </button>
      <button
        type="button"
        role="tab"
        class="project-tab"
        :class="{ 'project-tab--active': activeTab === 'graph' }"
        :aria-selected="activeTab === 'graph'"
        @click="activeTab = 'graph'"
      >
        <span class="tab-icon">🕸</span>
        <span>图谱编辑</span>
      </button>
      <!-- P3 Day 3.2(2026-05-26)— 作者指南针 tab -->
      <button
        type="button"
        role="tab"
        class="project-tab"
        :class="{ 'project-tab--active': activeTab === 'compass' }"
        :aria-selected="activeTab === 'compass'"
        @click="activeTab = 'compass'"
      >
        <span class="tab-icon">🧭</span>
        <span>作者指南针</span>
      </button>
      <!-- 2026-06-06:续作家族树 tab(滚雪球链 + 反事实组合批次可视化)
           注:此 tab 是路由跳转(不是 activeTab 切换),因为家族树是独立 view 占整页 -->
      <button
        type="button"
        role="tab"
        class="project-tab"
        :aria-selected="false"
        @click="router.push({ name: 'simulation-family-tree', params: { id: props.id } })"
      >
        <span class="tab-icon" aria-hidden="true">
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="14" height="14" viewBox="0 0 24 24"
            fill="none" stroke="currentColor" stroke-width="1.8"
            stroke-linecap="round" stroke-linejoin="round"
          >
            <!-- git-branch 图标(树/分叉)— 适合家族树语义 -->
            <line x1="6" y1="3" x2="6" y2="15" />
            <circle cx="18" cy="6" r="3" />
            <circle cx="6" cy="18" r="3" />
            <path d="M18 9a9 9 0 0 1-9 9" />
          </svg>
        </span>
        <span>家族树</span>
      </button>
    </nav>

    <!-- Sprint D.6 loading 骨架屏:模拟 3 个 section(作品文件 / 角色 / 关系)的卡片占位 -->
    <main v-if="loading" class="main" aria-busy="true" aria-live="polite">
      <section class="section">
        <SkeletonBlock height="20px" width="120px" />
        <div class="skeleton-row-stack">
          <SkeletonBlock height="44px" />
          <SkeletonBlock height="44px" />
        </div>
      </section>
      <section class="section">
        <SkeletonBlock height="20px" width="80px" />
        <div class="skeleton-row-stack">
          <SkeletonBlock height="38px" />
          <SkeletonBlock height="38px" />
          <SkeletonBlock height="38px" />
        </div>
      </section>
      <section class="section">
        <SkeletonBlock height="20px" width="80px" />
        <div class="skeleton-row-stack">
          <SkeletonBlock height="38px" />
          <SkeletonBlock height="38px" />
        </div>
      </section>
    </main>
    <main v-else-if="!project" class="state-msg state-error">
      {{ errorMessage || "项目不存在" }}
    </main>

    <main v-else class="main">
      <p v-if="errorMessage" class="error-banner">
        {{ errorMessage }}
        <button class="x-btn" @click="errorMessage = null">×</button>
      </p>

      <!-- M7.D-fix Tab 1:作品列表(默认)— 该项目下所有 sim,继承 badge 展示
           M7.I(2026-05-20):注入 canCreate + ctaText 让空态显眼 CTA,emit 接 openSimulate -->
      <SimulationsListPanel
        v-if="activeTab === 'simulations'"
        :project-id="props.id"
        :can-create="!aiActionsDisabledReason && !!project"
        :cta-icon="simulateButtonLabel.icon"
        :cta-text="simulateButtonLabel.text"
        @start-create="openSimulate"
      />

      <!-- P3 Day 3.2 Tab 3:作者指南针 -->
      <AuthorCompassPanel
        v-else-if="activeTab === 'compass'"
        :project-id="props.id"
        :is-initial-mode="isInitialMode"
      />

      <!-- M7.D-fix Tab 2:图谱编辑(用户主动切来才显示)— 原 ProjectView 全部内容 -->
      <template v-else>
      <!-- Sprint 6.A2 polish(2026-05-23 三轮):AI 角色对焦 pill 改实心填充
           v2 用浅紫 soft 填充 + 无边框 → 色彩对比弱 + 无 affordance 信号,用户感知是"贴图"而非按钮
           v3 改实心紫底 + 白字 + box-shadow(浮起感) + 末尾 → 箭头(动作暗示)
           形状仍是 pill(999px),与顶部矩形 primary "查看 3D 图谱" 同色不同形 — 同主题不同地位
           Bug 修复(2026-05-27):初始态下"作品文件" section 隐藏 → pill 的 absolute 锚点失效,
           会跟下方第一个可见 section(世界观)右上角重合。加 --initial modifier 改 relative 占空间 -->
      <div class="graph-tab-actions" :class="{ 'graph-tab-actions--initial': isInitialMode }">
        <button
          type="button"
          class="graph-focus-pill"
          :disabled="focusOpen || loading || !project || !!aiActionsDisabledReason"
          :title="aiActionsDisabledReason || 'AI 基于你已填的内容做合理推演,给出 3-5 类建议,你逐条采纳/不采纳'"
          @click="openFocus"
        >
          <span class="focus-pill-icon" aria-hidden="true">✦</span>
          <span>AI 角色对焦</span>
          <span class="focus-pill-arrow" aria-hidden="true">→</span>
        </button>
      </div>

      <!-- ========== 作品文件(Sprint 2.A 中间态入口 + 2.B 抽图谱触发)— 仅非初始态显 ========== -->
      <section v-if="!isInitialMode" class="section">
        <ProjectUploadsPanel
          :project-id="props.id"
          @extract-done="onExtractDone"
          @deleted="onUploadDeleted"
        />
      </section>

      <!-- ========== 非初始态:抽图谱完成前 = 引导 / 完成后 = 紧凑就绪 Banner ========== -->
      <!--
        UI 优化(2026-05-21):.ai-pending-card--ready 从大块卡片改为单行紧凑 Banner —
        ✓ 图标 + "图谱已就绪" + 角色/关系/事件计数 全在一行,释放首屏空间给角色卡片墙。
        middle 模式无需任何引导文字(右上角按钮即 CTA);end / cycle 保留差异化提示。
      -->
      <section v-if="!isInitialMode" class="section ai-pending-block">
        <div v-if="hasGraphData" class="ai-pending-card ai-pending-card--ready ai-pending-card--banner">
          <span class="pending-icon">✓</span>
          <span class="pending-banner-text">
            <strong class="pending-title-inline">图谱已就绪</strong>
            <span class="pending-stats">
              <strong>{{ characterRows.filter(r => r.character).length }}</strong> 角色 ·
              <strong>{{ relationshipRows.filter(r => r.relationship).length }}</strong> 关系 ·
              <strong>{{ eventRows.filter(r => r.event).length }}</strong> 事件
            </span>
            <span v-if="readyCardDescription" class="pending-hint">{{ readyCardDescription }}</span>
          </span>
        </div>

        <!-- Sprint 6.A1 + 6.A2 M1++(2026-05-18 重构):主角 agent 卡片墙
             - 顶部 section 可折叠(默认展开)
             - 每张卡可展开:档案预览 + 该角色的关系子列表 + 每条关系内嵌时间轴
             - 关系演化数据(包括 CharacterFocusModal 加的 phase)合并进卡片,不再独立 section
             - 卡片加"🗑 删除主角"+ "▾ 展开"独立按钮(替代原"点击即删除"反直觉设计) -->
        <!--
          UI 优化(2026-05-21 十一轮):手风琴联动 — 两张 section 同时只能展开一张
          展开 protag → 调 sceneGraphRef.collapseSection() 把 scene 折叠
          展开 scene → 调 protagWallRef.collapseSection() 把 protag 折叠
        -->
        <ProtagonistWall
          v-if="hasGraphData"
          ref="protagWallRef"
          :project-id="props.id"
          class="protag-wall-section"
          @expanded="onProtagExpanded"
          @open-emotion-overview="openEmotionOverview"
        />

        <!-- Sprint 6.A2 M2(2026-05-18):场景图谱
             AI 从 build_graph LOCATION 实体聚合;每个场所可展开看"常客"角色(共现 + 主角优先)。
             M3 续写时驱动 scene_picker — 现在仅展示让用户感知。 -->
        <SceneGraph
          v-if="hasGraphData"
          ref="sceneGraphRef"
          :project-id="props.id"
          class="protag-wall-section"
          @expanded="onSceneExpanded"
        />
      </section>

      <!--
        一键灌满北极星 banner(2026-05-29 末)— 全 mode 通用
        点 1 个按钮串行触发所有 inferer(故事脊柱 + 视角 + 知识边界 + N 个角色驱动)
        替代用户点 3-N 次"AI 推断"+ 单独等待
      -->
      <section v-if="project" class="all-boards-banner">
        <div class="all-boards-title-block">
          <h2 class="all-boards-title">
            <Icon name="sparkles" :size="18" class="title-icon" />
            AI 一键灌满北极星
          </h2>
          <p class="all-boards-desc">
            一次推断<strong>故事脊柱</strong> · <strong>视角扩展</strong> · <strong>知识边界</strong>
            <template v-if="allBoardsIncludeDrivers"> · <strong>每个角色的驱动力</strong></template>。
            <span class="all-boards-time">约 30-90 秒(角色多时更长),完成后你可逐项审阅修正</span>
          </p>
        </div>
        <div class="all-boards-actions">
          <label class="all-boards-opt" :title="`关掉则只推断 3 个项目级字段,不给每个角色推驱动力`">
            <input
              v-model="allBoardsIncludeDrivers"
              type="checkbox"
              :disabled="allBoardsLoading"
            />
            <span>含角色驱动({{ realCharacters.length }} 个)</span>
          </label>
          <label
            class="all-boards-opt"
            :title="`勾选 = 清空后全部重新生成\n不勾(默认) = 在现有事实上追加新的`"
          >
            <input
              v-model="allBoardsForceKnowledge"
              type="checkbox"
              :disabled="allBoardsLoading"
            />
            <span>知识边界:清空重建</span>
          </label>
          <button
            type="button"
            class="all-boards-btn"
            :disabled="allBoardsLoading"
            @click="inferAllBoards"
          >
            <Icon name="sparkles" :size="14" />
            <span>{{ allBoardsLoading ? "AI 推断中(可能 1-2 分钟)…" : "一键灌满" }}</span>
          </button>
        </div>

        <!-- SSE 进度面板(2026-05-30²)— 长任务实时反馈 -->
        <div
          v-if="allBoardsLoading || allBoardsStageHistory.length > 0"
          class="all-boards-progress"
        >
          <!-- 进度条 -->
          <div v-if="allBoardsTotalStages > 0" class="ab-progress-bar">
            <div
              class="ab-progress-fill"
              :style="{
                width: `${Math.min(100, (allBoardsCurrentIndex / allBoardsTotalStages) * 100)}%`,
              }"
            />
            <span class="ab-progress-text">
              {{ allBoardsCurrentIndex }} / {{ allBoardsTotalStages }} ·
              <span v-if="allBoardsLoading && allBoardsCurrentLabel">{{ allBoardsCurrentLabel }} 中…</span>
              <span v-else-if="!allBoardsLoading">已完成</span>
            </span>
          </div>
          <!-- stage 列表 -->
          <ul v-if="allBoardsStageHistory.length > 0" class="ab-stage-list">
            <li
              v-for="(s, idx) in allBoardsStageHistory"
              :key="`${s.stage}-${idx}`"
              class="ab-stage-item"
              :class="`ab-stage--${s.status}`"
              :title="s.error || s.reasoning || ''"
            >
              <span class="ab-stage-icon">
                <template v-if="s.status === 'running'">●</template>
                <template v-else-if="s.status === 'done'">✓</template>
                <template v-else-if="s.status === 'failed'">✗</template>
              </span>
              <span class="ab-stage-label">{{ s.label }}</span>
              <span v-if="s.status === 'failed'" class="ab-stage-err">{{ s.error }}</span>
            </li>
          </ul>
        </div>
      </section>

      <!-- ========== SP-1 故事脊柱(2026-05-28 修正:全 mode 通用,不限初始态)==========
           原本只 initial 显示是设计漏洞 — 字段是项目级 + hard_constraints 注入对
           initial/middle/end 都生效,middle/end 用户也该能在 UI 里填,
           即便漫创态目前不消费此字段,也允许填(后期接通自动生效) -->
      <section
        class="section story-core-card"
        :class="{ 'is-collapsed': storyCoreCollapsed }"
        v-if="project"
      >
        <header class="story-core-header">
          <button
            type="button"
            class="collapse-btn"
            :aria-expanded="!storyCoreCollapsed"
            @click="storyCoreCollapsed = !storyCoreCollapsed"
          >{{ storyCoreCollapsed ? "▸" : "▾" }}</button>
          <div class="story-core-title-block">
            <h2 class="story-core-title">
              <Icon name="compass" :size="18" class="title-icon" />
              故事脊柱
            </h2>
            <p v-if="!storyCoreCollapsed" class="story-core-desc">
              告诉 LLM 这个故事在解决什么核心问题、主题是什么、想落在哪个情绪终点。<br />
              没有目标弧时,AI 会把好不容易攒的张力提前泄光;有了脊柱才敢「憋着不解决」。
            </p>
          </div>
        </header>
        <div v-if="!storyCoreCollapsed" class="story-core-body">
          <div class="sc-field">
            <label class="sc-label">核心戏剧问题(一句话脊柱 · ≤ 300 字)</label>
            <textarea
              v-model="scDraft.core_dramatic_question"
              maxlength="300"
              rows="2"
              class="sc-textarea"
              placeholder="例:线上交付的真心,扛不扛得住线下的真相"
            />
          </div>
          <div class="sc-field">
            <label class="sc-label">主题(独立于基调 · ≤ 200 字)</label>
            <textarea
              v-model="scDraft.theme"
              maxlength="200"
              rows="1"
              class="sc-textarea"
              placeholder="例:孤独中相互取暖 / 自由与责任的撕扯"
            />
          </div>
          <div class="sc-field">
            <label class="sc-label">终点情绪 / 走向(≤ 300 字)</label>
            <textarea
              v-model="scDraft.ending_direction"
              maxlength="300"
              rows="2"
              class="sc-textarea"
              placeholder="例:哀而不伤,留一抹希望 / 悲剧收束,救赎落空"
            />
          </div>
          <div class="sc-actions">
            <button
              type="button"
              class="sc-infer-btn"
              :disabled="scInferring"
              :title="`AI 推断故事内核三件套,会覆盖现有值,你可再改`"
              @click="inferStoryCore"
            >
              <Icon name="sparkles" :size="14" />
              {{ scInferring ? "AI 推断中…" : "AI 推断" }}
            </button>
            <button
              type="button"
              class="sc-save-btn"
              :disabled="scSaving"
              @click="saveStoryCore"
            >
              {{ scSaving ? "保存中…" : "保存故事脊柱" }}
            </button>
          </div>
        </div>
      </section>

      <!--
        SP-8(2026-05-28):视角扩展(全 mode 通用,不限初始态)
        谁的"我" + 叙述者多可靠 + 距离多近,叙述层的灵魂续写北极星.
        放在故事脊柱后,目的层(脊柱)+ 叙述层(视角)并列,都是项目级.
      -->
      <section
        class="section narrative-view-card"
        :class="{ 'is-collapsed': narrativeViewCollapsed }"
        v-if="project"
      >
        <header class="story-core-header">
          <button
            type="button"
            class="collapse-btn"
            :aria-expanded="!narrativeViewCollapsed"
            @click="narrativeViewCollapsed = !narrativeViewCollapsed"
          >{{ narrativeViewCollapsed ? "▸" : "▾" }}</button>
          <div class="story-core-title-block">
            <h2 class="story-core-title">
              <Icon name="eye" :size="18" class="title-icon" />
              视角扩展
            </h2>
            <p v-if="!narrativeViewCollapsed" class="story-core-desc">
              告诉 AI 这本书"谁的内心被记 / 叙述者可不可信 / 镜头距离".<br />
              没有视角约束时,LLM 容易在不同幕之间漂(忽 omniscient 忽 first 贴近),灵魂支离破碎.
            </p>
          </div>
        </header>
        <div v-if="!narrativeViewCollapsed" class="story-core-body">
          <div class="sc-field">
            <label class="sc-label">焦点角色(谁的"我" / 谁的内心被记)</label>
            <select
              v-model="nvDraft.narrative_focus_character_id"
              class="sc-select"
            >
              <option value="">— 不指定(LLM 自由)—</option>
              <option
                v-for="c in realCharacters"
                :key="c.id"
                :value="c.id"
              >{{ c.name }}</option>
            </select>
          </div>
          <div class="sc-field">
            <label class="sc-label">叙述者可靠度</label>
            <div class="nv-radio-row">
              <label class="nv-radio">
                <input
                  v-model="nvDraft.narrator_reliability"
                  type="radio"
                  value=""
                /> 未指定
              </label>
              <label class="nv-radio">
                <input
                  v-model="nvDraft.narrator_reliability"
                  type="radio"
                  value="reliable"
                /> 可靠(说真话)
              </label>
              <label class="nv-radio">
                <input
                  v-model="nvDraft.narrator_reliability"
                  type="radio"
                  value="unreliable"
                /> 不可靠(故意 / 误判)
              </label>
              <label class="nv-radio">
                <input
                  v-model="nvDraft.narrator_reliability"
                  type="radio"
                  value="uncertain"
                /> 半可靠(有时偏)
              </label>
            </div>
          </div>
          <div class="sc-field">
            <label class="sc-label">叙述距离</label>
            <div class="nv-radio-row">
              <label class="nv-radio">
                <input
                  v-model="nvDraft.narrative_distance"
                  type="radio"
                  value=""
                /> 未指定
              </label>
              <label class="nv-radio">
                <input
                  v-model="nvDraft.narrative_distance"
                  type="radio"
                  value="omniscient"
                /> 全知(上帝)
              </label>
              <label class="nv-radio">
                <input
                  v-model="nvDraft.narrative_distance"
                  type="radio"
                  value="limited"
                /> 有限(第三贴近)
              </label>
              <label class="nv-radio">
                <input
                  v-model="nvDraft.narrative_distance"
                  type="radio"
                  value="close"
                /> 贴近(第一人称)
              </label>
              <label class="nv-radio">
                <input
                  v-model="nvDraft.narrative_distance"
                  type="radio"
                  value="intimate"
                /> 沉浸(意识流)
              </label>
            </div>
          </div>
          <!-- 2026-06-01:作品篇幅 — 决定 narrator 细节颗粒度
               治"长篇每秒放慢镜头特写,读者疲劳" — Gemini 评测点出的问题 -->
          <div class="sc-field">
            <label class="sc-label">
              作品篇幅(决定细节颗粒度)
            </label>
            <div class="nv-radio-row">
              <label class="nv-radio" title="每段都细致:微表情/动作/声响齐全.适合单章高潮密度.">
                <input
                  v-model="nvDraft.expected_length"
                  type="radio"
                  value="short"
                /> 短篇(高密度)
              </label>
              <label class="nv-radio" title="核心场景细致,过场段落简略.约 7 分密度.">
                <input
                  v-model="nvDraft.expected_length"
                  type="radio"
                  value="medium"
                /> 中篇(平衡 · 默认)
              </label>
              <label class="nv-radio" title="只在转折/高潮密集,常规场景松弛.避免长读疲劳.">
                <input
                  v-model="nvDraft.expected_length"
                  type="radio"
                  value="long"
                /> 长篇(松弛)
              </label>
            </div>
          </div>
          <div class="sc-actions">
            <button
              type="button"
              class="sc-infer-btn"
              :disabled="nvInferring"
              :title="`AI 推断焦点角色 / 可靠度 / 距离,会覆盖现有值,你可再改`"
              @click="inferNarrativeView"
            >
              <Icon name="sparkles" :size="14" />
              {{ nvInferring ? "AI 推断中…" : "AI 推断" }}
            </button>
            <button
              type="button"
              class="sc-save-btn"
              :disabled="nvSaving"
              @click="saveNarrativeView"
            >
              {{ nvSaving ? "保存中…" : "保存视角扩展" }}
            </button>
          </div>
        </div>
      </section>

      <!--
        2026-06-01:章节字数区间(全 mode 通用)
        阅读器按此区间在 [min, max] 内找段落分隔符切章;
        AI 也会拿到这个区间作为"章节自觉"硬约束.
      -->
      <section
        class="section narrative-view-card"
        :class="{ 'is-collapsed': chapterRangeCollapsed }"
        v-if="project"
      >
        <header class="story-core-header">
          <button
            type="button"
            class="collapse-btn"
            :aria-expanded="!chapterRangeCollapsed"
            @click="chapterRangeCollapsed = !chapterRangeCollapsed"
          >{{ chapterRangeCollapsed ? "▸" : "▾" }}</button>
          <div class="story-core-title-block">
            <h2 class="story-core-title">
              <Icon name="book" :size="18" class="title-icon" />
              章节字数区间
            </h2>
            <p v-if="!chapterRangeCollapsed" class="story-core-desc">
              阅读器按此区间切章 — 在 [min, max] 字内找段落分隔符,所以章节会落在自然句尾.<br />
              滚雪球续作中,本设置仅影响"本项目"切章;前篇章号沿继承链自动延续.
            </p>
          </div>
        </header>
        <div v-if="!chapterRangeCollapsed" class="story-core-body">
          <div class="sc-field chapter-range-row">
            <div class="chapter-range-field">
              <label class="sc-label">最少字数 / 章</label>
              <input
                v-model.number="chapterRangeDraft.chapter_size_min"
                type="number"
                class="sc-input chapter-range-input"
                min="500"
                max="8000"
                step="100"
                @keydown.enter.prevent="saveChapterRange"
              />
              <div class="chapter-range-hint">范围 500~8000 · Enter 直接保存</div>
            </div>
            <div class="chapter-range-sep">~</div>
            <div class="chapter-range-field">
              <label class="sc-label">最多字数 / 章</label>
              <input
                v-model.number="chapterRangeDraft.chapter_size_max"
                type="number"
                class="sc-input chapter-range-input"
                min="1500"
                max="10000"
                step="100"
                @keydown.enter.prevent="saveChapterRange"
              />
              <div class="chapter-range-hint">范围 1500~10000,且 ≥ 最少 + 500</div>
            </div>
          </div>
          <div class="sc-actions">
            <button
              type="button"
              class="sc-save-btn"
              :disabled="chapterRangeSaving"
              @click="saveChapterRange"
            >
              {{ chapterRangeSaving ? "保存中…" : "保存章节区间" }}
            </button>
          </div>
        </div>
      </section>

      <!--
        SP-3(2026-05-28):知识边界(全 mode 通用,不限初始态)
        每个角色"知道哪些事实",续作 narrator 拿到此表后,
        硬约束就能 enforce "角色 X 在第 N 幕不知道事实 Y → 不许用此信息".
        放在故事脊柱后,与「角色 / 关系 / 事件」并列层级.
      -->
      <!-- hotfix(2026-06-01):知识边界加折叠 wrapper,与其它卡片视觉一致 -->
      <section
        v-if="project && realCharacters.length > 0"
        class="section story-facts-wrap"
        :class="{ 'is-collapsed': storyFactsCollapsed }"
      >
        <header class="story-core-header story-facts-header-row">
          <button
            type="button"
            class="collapse-btn"
            :aria-expanded="!storyFactsCollapsed"
            @click="storyFactsCollapsed = !storyFactsCollapsed"
          >{{ storyFactsCollapsed ? "▸" : "▾" }}</button>
          <div class="story-core-title-block">
            <h2 class="story-core-title">
              <Icon name="book" :size="18" class="title-icon" />
              知识边界
            </h2>
            <p v-if="!storyFactsCollapsed" class="story-core-desc">
              谁知道哪条事实.续作 AI 用这表防"渡边在第 1 章就知道直子已死"类穿帮 bug.
            </p>
          </div>
        </header>
        <StoryFactsPanel
          v-if="!storyFactsCollapsed"
          :project-id="props.id"
          :characters="realCharacters"
          :embedded="true"
          class="story-facts-embedded"
        />
      </section>

      <!-- ========== 初始态:世界观 / 角色 / 关系 / 事件 手动卡片(从零创造)========== -->
      <template v-if="isInitialMode">

      <!-- 视觉统一(2026-05-21):用 flex 容器 + gap 统一 5 个卡片间距,避免各组件 margin 不一致 -->
      <div class="init-sections-stack">

      <!-- INIT.5(2026-05-21):世界观 6 维编辑 — 决定 LLM 推演的世界规则 -->
      <WorldBaselineEditor
        v-if="project"
        :initial="project.world_baseline ?? {}"
        @save="saveWorldBaseline"
      />

      <!--
        INIT.6(2026-05-21):初始态场景预定义
        复用 SceneGraph 组件,加 allow-create 让用户能从零新增 3-5 个核心场景。
        推演时 scene_picker 会优先用这些场景(避免 AI 凭空造场所)
      -->
      <SceneGraph
        v-if="project"
        :project-id="props.id"
        :allow-create="true"
      />

      <!--
        ========== 角色(INIT 视觉统一 2026-05-21:改造为卡片+折叠样式)==========
        与世界观 / 场景图谱视觉一致,默认展开(主创作元素)
      -->
      <section class="section init-card" :class="{ 'is-collapsed': charsSectionCollapsed }">
        <header class="init-card-header">
          <button
            type="button"
            class="collapse-btn"
            :aria-expanded="!charsSectionCollapsed"
            :aria-label="charsSectionCollapsed ? '展开角色' : '折叠角色'"
            @click="charsSectionCollapsed = !charsSectionCollapsed"
          >{{ charsSectionCollapsed ? "▸" : "▾" }}</button>
          <div class="init-card-title-block">
            <h2 class="init-card-title">
              <Icon name="character" :size="18" class="title-icon" />
              角色
              <span class="title-count mono">{{ characterRows.filter(r => r.character).length }}</span>
            </h2>
            <p v-if="!charsSectionCollapsed" class="init-card-sub">
              填好后回车或点击其它处自动保存
            </p>
          </div>
        </header>

        <ul v-if="!charsSectionCollapsed" class="row-list">
          <li
            v-for="(row, idx) in characterRows"
            :key="row.character?.id ?? `new-${idx}`"
            :ref="(el) => setRowRef(rowKeyChar(idx), el as Element | null)"
            class="row row-character"
            :class="{
              'row--editing-drawer': row.mode === 'edit',
              'row--saving': row.mode === 'saving',
              'row--placeholder': !row.character && row.mode === 'view',
              'row--error': !!row.errorMsg,
            }"
            @click.stop="row.mode === 'view' && enterCharEdit(row)"
          >
            <template v-if="row.mode === 'view' && !row.character">
              <span class="placeholder-text">+ 新角色</span>
            </template>

            <!--
              INIT.1 fix v3(2026-05-21):row 在 view / edit / saving 任意态都渲染相同 view 模板;
              edit 模式高亮(row--editing-drawer class)+ 完整 form 在右侧 drawer 中独立渲染
              避免原 inline 700px form 撑爆 row 撕裂视觉
            -->
            <template v-else-if="row.character">
              <span class="row-main">
                <span class="row-name">{{ row.character.name }}</span>
                <!-- INIT.4(2026-05-21):主角钉死 chip — 点击 toggle is_protagonist
                     ★ 黄星 = 主角 / ☆ 灰星 = 普通;后端自动设 protagonist_user_pinned=true 防 AI 重判 -->
                <button
                  type="button"
                  class="protag-chip"
                  :class="{ 'is-protag': row.character.is_protagonist }"
                  :title="row.character.is_protagonist
                    ? '当前为主角(已钉死,AI 不会重判)。点击取消'
                    : '设为主角(钉死后 AI 推演时此角色优先出场)'"
                  @click="toggleProtagonist(row, $event)"
                >
                  <Icon name="star" :size="12" :filled="row.character.is_protagonist" />
                  {{ row.character.is_protagonist ? "主角" : "设为主角" }}
                </button>
                <span v-if="row.character.identity" class="row-meta">
                  {{ row.character.identity }}
                </span>
                <!--
                  INIT.11 ++(2026-05-21):性格 chip 预览化 — 显示首 18 字 + 省略号
                  原本只显"性格"两个字,信息密度太低
                -->
                <span
                  v-if="row.character.personality"
                  class="row-tag personality-tag"
                  :title="row.character.personality"
                >
                  <Icon name="sparkles" :size="12" />
                  <span class="quotes-preview">{{
                    row.character.personality.length > 18
                      ? row.character.personality.slice(0, 18) + "…"
                      : row.character.personality
                  }}</span>
                </span>
                <!--
                  INIT.11(2026-05-21):台词 chip 强化 — view 模式预览第一句台词,而非只显 "× N"
                  hover title 显示全部;>1 条时 chip 末尾加 "+N 句"
                -->
                <span
                  v-if="row.character.quotes && row.character.quotes.length"
                  class="row-tag quotes-tag"
                  :title="`角色台词(LLM 推演时模仿语气):\n${row.character.quotes.join('\n')}`"
                >
                  <Icon name="message" :size="12" />
                  <span class="quotes-preview">{{ row.character.quotes[0] }}</span>
                  <span v-if="row.character.quotes.length > 1" class="quotes-more">
                    +{{ row.character.quotes.length - 1 }} 句
                  </span>
                </span>
                <!--
                  INIT.8 + INIT.11 ++(2026-05-21):"倾向避免" chip 预览化 — 显示首条 + 余数
                -->
                <span
                  v-if="row.character.no_go_list && row.character.no_go_list.length"
                  class="row-tag nogo-tag"
                  :title="`角色倾向避免(剧情合理时可破):\n${row.character.no_go_list.join('\n')}`"
                >
                  <Icon name="ban" :size="12" />
                  <span class="quotes-preview">{{ row.character.no_go_list[0] }}</span>
                  <span v-if="row.character.no_go_list.length > 1" class="quotes-more">
                    +{{ row.character.no_go_list.length - 1 }} 条
                  </span>
                </span>
                <!--
                  INIT.3(2026-05-21):行为基线 chip — 当 4 维度任一有值时显示
                  显示格式:⚙ 卑微·4·灰  +N 雷区
                  hover title 显完整内容
                -->
                <span
                  v-if="hasBaselineSet(row.character)"
                  class="row-tag baseline-tag"
                  :title="formatBaselineTitle(row.character)"
                >
                  <Icon name="settings" :size="12" />
                  <span class="quotes-preview">{{ formatBaselineSummary(row.character) }}</span>
                </span>
              </span>
              <button
                class="del-btn"
                title="删除"
                @click.stop="deleteCharRow(row)"
              >×</button>

              <!--
                INIT.1(2026-05-21):关系移入角色卡 — 删独立关系 section
                  - 显示该角色相关所有关系(双向):→ B(out)/ ← A(in)
                  - 每条 chip 末尾 [×] 删除按钮(点击调 deleteRelRow)
                  - 点击 chip 主体 → 该关系 row 进入 edit 模式(inline 编辑 type / desc)
                  - "+ 加关系"按钮 → startAddRelationFor(charId)展开 inline 表单
                  - 只在已 saved 角色显示;新创建中(无 row.character)不显示关系区
              -->
              <div class="char-rels" @click.stop>
                <!-- 关系列表 -->
                <template v-for="rel in relsForCharacter(row.character.id)" :key="rel.row.relationship!.id">
                  <!--
                    INIT.1 fix(2026-05-21):一个关系镜像出现在 source / target 两个角色卡内,
                    若仅判 mode==='edit',两个卡片会同时进入 edit form。
                    必须叠加 editingFromCharId === 当前角色 id,这样:
                      - 从 A 卡点击 chip → A.editingFromCharId=A.id → A 卡显 edit,B 卡仍显 chip
                      - PATCH 成功后两边 chip 都自动反映新值(共享 row.relationship reactive 引用)
                  -->
                  <template v-if="rel.row.mode === 'edit' && rel.row.editingFromCharId === row.character.id">
                    <div
                      class="char-rel-edit"
                      :ref="(el) => setRowRef(rowKeyRel(rel.idx), el as Element | null)"
                    >
                      <span class="char-rel-edit-fixed">
                        {{ characterById.get(rel.row.relationship!.source_id)?.name ?? "?" }}
                        <Icon name="arrow" :size="12" class="rel-arrow" />
                        {{ characterById.get(rel.row.relationship!.target_id)?.name ?? "?" }}
                      </span>
                      <select
                        v-model="rel.row.editType"
                        class="row-input char-rel-type-select"
                        :disabled="rel.row.mode !== 'edit'"
                        @blur="tryPersistRel(rel.row, rel.idx, $event)"
                        @keydown="onRelKey(rel.row, rel.idx, $event)"
                      >
                        <option v-for="t in REL_TYPES" :key="t" :value="t">{{ t }}</option>
                        <option
                          v-if="rel.row.editType && !REL_TYPES.includes(rel.row.editType)"
                          :value="rel.row.editType"
                        >{{ rel.row.editType }}(自定义)</option>
                      </select>
                      <input
                        v-model="rel.row.editDesc"
                        type="text"
                        placeholder="描述(可选)"
                        class="row-input char-rel-desc-input"
                        @blur="tryPersistRel(rel.row, rel.idx, $event)"
                        @keydown="onRelKey(rel.row, rel.idx, $event)"
                      />
                      <button
                        type="button"
                        class="del-btn char-rel-del"
                        title="删除关系"
                        @click.stop="deleteRelRow(rel.row)"
                      >×</button>
                    </div>
                  </template>
                  <!-- view 模式 → chip + 可选展开 timeline -->
                  <template v-else>
                    <button
                      type="button"
                      class="char-rel-chip"
                      :class="{ 'char-rel-chip--in': rel.direction === 'in' }"
                      :title="`${rel.direction === 'out' ? '从此角色发出' : '指向此角色'}:${rel.row.relationship!.type}${rel.row.relationship!.description ? ' · ' + rel.row.relationship!.description : ''} · 点击编辑`"
                      @click.stop="enterRelEdit(rel.row, row.character.id)"
                    >
                      <Icon
                        :name="rel.direction === 'out' ? 'arrow' : 'arrow_left'"
                        :size="12"
                        class="rel-arrow"
                      />
                      <span class="char-rel-other">{{ characterById.get(rel.otherId)?.name ?? "?" }}</span>
                      <span class="char-rel-type">{{ rel.row.relationship!.type }}</span>
                      <!-- SP-7 polarity hotfix(2026-06-01):每条关系小色点(灰未标/绿正面/红对立/灰中性)
                           点击循环切换,与"AI 一键推断"(在一键灌满 Stage 4 中)互补 -->
                      <span
                        class="char-rel-polarity"
                        :class="[`polarity-${rel.row.relationship!.polarity ?? 'unset'}`]"
                        :title="`正负极性:${polarityLabel(rel.row.relationship!.polarity)} · 点击切换`"
                        @click.stop="cycleRelPolarity(rel.row, $event)"
                      ></span>
                      <span v-if="rel.row.relationship!.description" class="char-rel-desc">
                        · {{ rel.row.relationship!.description }}
                      </span>
                      <!-- INIT.2:🕒 阶段按钮 — 点击切换 timeline 展开,与编辑/删除分开 -->
                      <span
                        class="char-rel-phases-btn"
                        :class="{ 'is-expanded': expandedRelKey === relExpandKey(row.character.id, rel.row.relationship!.id) }"
                        :title="`关系演化时间轴(${phaseCounts.get(rel.row.relationship!.id) ?? '?'} 阶段) · 点击${expandedRelKey === relExpandKey(row.character.id, rel.row.relationship!.id) ? '收起' : '展开'}`"
                        @click.stop="toggleRelTimeline(row.character.id, rel.row.relationship!.id, $event)"
                      >
                        <Icon name="clock" :size="12" />
                        <span v-if="phaseCounts.has(rel.row.relationship!.id)" class="char-rel-phases-count mono">
                          {{ phaseCounts.get(rel.row.relationship!.id) }}
                        </span>
                        <span class="char-rel-phases-caret" aria-hidden="true">
                          {{ expandedRelKey === relExpandKey(row.character.id, rel.row.relationship!.id) ? "▾" : "▸" }}
                        </span>
                      </span>
                      <span
                        class="char-rel-chip-del"
                        title="删除关系"
                        @click.stop="deleteRelRow(rel.row)"
                      >×</span>
                    </button>
                    <!-- INIT.2:timeline 展开区(占满整行)
                         + fix v1:全局互斥 accordion,同时只有 1 个展开
                         + fix v2:key = charId|relId,同一关系在两端卡视为独立 UI 实例 -->
                    <div
                      v-if="expandedRelKey === relExpandKey(row.character.id, rel.row.relationship!.id)"
                      class="char-rel-timeline-wrap"
                      @click.stop
                    >
                      <RelationshipTimeline
                        :relationship-id="rel.row.relationship!.id"
                        :current-phase-id="rel.row.relationship!.current_phase_id"
                        @phases-changed="onRelPhasesChanged"
                      />
                    </div>
                  </template>
                </template>

                <!-- "+ 加关系" 按钮(仅当不在该卡 inline 添加时显示)-->
                <button
                  v-if="addingRelForCharId !== row.character.id && realCharacters.length >= 2"
                  type="button"
                  class="char-rel-add-btn"
                  @click.stop="startAddRelationFor(row.character.id)"
                >+ 加关系</button>

                <!-- inline 添加新关系表单(仅当 adding 该卡时显示)-->
                <div
                  v-else-if="addingRelForCharId === row.character.id"
                  class="char-rel-edit char-rel-edit--new"
                >
                  <span class="char-rel-edit-fixed">
                    {{ row.character.name }}
                    <Icon name="arrow" :size="12" class="rel-arrow" />
                  </span>
                  <select
                    v-model="addingRelTargetSelect"
                    class="row-input char-rel-target-select"
                    @change="bindAddingTargetToRow"
                  >
                    <option value="">— 选择目标角色 —</option>
                    <option
                      v-for="c in realCharacters.filter(c => c.id !== row.character!.id)"
                      :key="c.id"
                      :value="c.id"
                    >{{ c.name }}</option>
                  </select>
                  <select
                    v-model="addingRelTypeSelect"
                    class="row-input char-rel-type-select"
                    @change="bindAddingTargetToRow"
                  >
                    <option v-for="t in REL_TYPES" :key="t" :value="t">{{ t }}</option>
                  </select>
                  <input
                    v-model="addingRelDescInput"
                    type="text"
                    placeholder="描述(可选)"
                    class="row-input char-rel-desc-input"
                    @blur="bindAndPersistAddingRel(row.character.id)"
                    @keydown.enter.prevent="bindAndPersistAddingRel(row.character.id)"
                    @keydown.esc.prevent="cancelAddRelationFromCard"
                  />
                  <button
                    type="button"
                    class="char-rel-cancel-btn"
                    title="取消"
                    @click.stop="cancelAddRelationFromCard"
                  >×</button>
                </div>

                <!-- 空提示(无关系且未在添加)-->
                <span
                  v-if="relsForCharacter(row.character.id).length === 0
                    && addingRelForCharId !== row.character.id
                    && realCharacters.length < 2"
                  class="char-rels-empty"
                >再创建一个角色后,可在此添加关系</span>
              </div>
            </template>

            <!--
              INIT.1 fix v3(2026-05-21):原 row 内 inline edit form 整段(~230 行)
              已搬至右侧抽屉 `<aside class="char-edit-drawer">`(见 template 末尾)
              row 内不再渲染 form,只通过 .row--editing-drawer class 高亮当前编辑卡
            -->
          </li>
        </ul>
      </section>

      <!--
        INIT.1(2026-05-21):原 独立"关系" section 已删除 — 关系移入角色卡内嵌
        见角色 li 末尾的 `.char-rels` 子区(双向显示 → / ← 箭头 + inline 编辑 / 删除 / 添加)
        relationshipRows 数据流和 tryPersistRel / deleteRelRow / enterRelEdit / cancelRelEdit
        全部复用,只是渲染入口改了。
      -->

      <!-- ========== 事件(可选,INIT 视觉统一)========== -->
      <section class="section init-card" :class="{ 'is-collapsed': eventsSectionCollapsed }">
        <header class="init-card-header">
          <button
            type="button"
            class="collapse-btn"
            :aria-expanded="!eventsSectionCollapsed"
            :aria-label="eventsSectionCollapsed ? '展开事件' : '折叠事件'"
            @click="eventsSectionCollapsed = !eventsSectionCollapsed"
          >{{ eventsSectionCollapsed ? "▸" : "▾" }}</button>
          <div class="init-card-title-block">
            <h2 class="init-card-title">
              <Icon name="event" :size="18" class="title-icon" />
              事件 <span class="opt">(可选)</span>
              <span class="title-count mono">{{ eventRows.filter(r => r.event).length }}</span>
            </h2>
            <p v-if="!eventsSectionCollapsed" class="init-card-sub">
              初始态可不创建事件
            </p>
          </div>
        </header>

        <ul v-if="!eventsSectionCollapsed" class="row-list">
          <li
            v-for="(row, idx) in eventRows"
            :key="row.event?.id ?? `new-ev-${idx}`"
            :ref="(el) => setRowRef(rowKeyEvent(idx), el as Element | null)"
            class="row row-event"
            :class="{
              'row--edit': row.mode === 'edit',
              'row--saving': row.mode === 'saving',
              'row--placeholder': !row.event && row.mode === 'view',
              'row--error': !!row.errorMsg,
            }"
            @click.stop="row.mode === 'view' && enterEventEdit(row)"
          >
            <template v-if="row.mode === 'view' && !row.event">
              <span class="placeholder-text">+ 新事件</span>
            </template>

            <template v-else-if="row.mode === 'view' && row.event">
              <span class="row-main">
                <span class="row-name">{{ row.event.description }}</span>
                <!-- INIT.7(2026-05-21):时间锚显示在事件描述旁,紫色 chip -->
                <span
                  v-if="row.event.time_anchor"
                  class="row-tag time-anchor-tag"
                  :title="`时间锚:${row.event.time_anchor}`"
                >
                  <Icon name="clock" :size="12" />
                  {{ row.event.time_anchor }}
                </span>
                <span
                  v-if="
                    row.event.participants
                      .map((id) => characterById.get(id)?.name)
                      .filter(Boolean).length
                  "
                  class="row-meta"
                >
                  参与:{{
                    row.event.participants
                      .map((id) => characterById.get(id)?.name)
                      .filter((n): n is string => !!n)
                      .join(" / ")
                  }}
                </span>
              </span>
              <button class="del-btn" @click.stop="deleteEventRow(row)">×</button>
            </template>

            <template v-else>
              <input
                v-model="row.editDesc"
                type="text"
                placeholder="事件描述"
                maxlength="300"
                class="row-input row-input-desc"
                :disabled="row.mode === 'saving'"
                v-focus
                @blur="tryPersistEvent(row, idx, $event)"
                @keydown="onEventKey(row, idx, $event)"
              />
              <!-- INIT.7(2026-05-21):时间锚 input — 可选,如"第 5 章" / "T0+3 天" -->
              <input
                v-model="row.editTimeAnchor"
                type="text"
                placeholder="时间锚(可选,如:第 5 章 / T0+3 天)"
                maxlength="30"
                class="row-input row-input-time-anchor"
                :disabled="row.mode === 'saving'"
                @blur="tryPersistEvent(row, idx, $event)"
                @keydown="onEventKey(row, idx, $event)"
              />
              <!--
                INIT.7 bugfix(2026-05-21):原 label + native checkbox 方案触发 input.blur
                  → tryPersistEvent 提前关闭 edit 模式 → 用户感知"点角色就退出编辑"。
                改为 button + @mousedown.prevent:阻止焦点离开 desc/time-anchor input,
                row.mode 保持 'edit',点击仅 toggle 选中。aria-pressed 维持无障碍语义。
              -->
              <div v-if="realCharacters.length" class="participants">
                <button
                  v-for="c in realCharacters"
                  :key="c.id"
                  type="button"
                  class="participant"
                  :class="{ 'is-active': row.editParticipants.includes(c.id) }"
                  :aria-pressed="row.editParticipants.includes(c.id)"
                  @mousedown.prevent
                  @click="toggleEventParticipant(row, c.id)"
                >
                  {{ c.name }}
                </button>
              </div>
              <span v-if="row.mode === 'saving'" class="row-status">保存中…</span>
              <span v-else-if="row.errorMsg" class="row-error">{{ row.errorMsg }}</span>
            </template>
          </li>
        </ul>
      </section>

      </div><!-- 闭合 .init-sections-stack flex 容器 -->
      </template><!-- 闭合 isInitialMode 的 v-if 包裹 -->
      </template><!-- 闭合 M7.D-fix activeTab === 'graph' 包裹 -->
    </main>

    <!-- AI 角色对焦 modal -->
    <CharacterFocusModal
      :open="focusOpen"
      :project-id="props.id"
      :project-name="project?.name ?? ''"
      @close="onFocusClose"
      @quota-exceeded="onFocusQuotaExceeded"
    />

    <!-- AI 续写 dock(Sprint 1.H)
         M7.I(2026-05-20):prefill-context-ids 用于"基于本篇续写"链路 —
         SimulationDetailView 跳来时携带 ?continueFrom=<simId>,这里转成 array 传 dock
         M7.J(2026-05-20):project-events 给中间态起点锚点 dropdown 用 -->
    <SimulationDock
      :open="simulateOpen"
      :project-id="props.id"
      :project-name="project?.name ?? ''"
      :character-count="realCharacterCount"
      :counterfactuals-instance="cf"
      :project-mode="project?.mode"
      :prefill-context-ids="continueFromIds"
      :project-events="anchorableEvents"
      :prefill-sim-patch="applySimPatch"
      :audit-source-label="auditSourceLabel"
      :on-open-workbench="openCounterfactualWorkbench"
      @close="onSimulateClose"
      @quota-exceeded="onSimulateQuotaExceeded"
      @created="markPatchesAppliedAfterSimCreated"
    />

    <!-- Sprint 6.A2 #2 二期(2026-05-22):跨多次推演的角色情绪总览 -->
    <CharacterEmotionOverviewModal
      :open="emotionOverviewOpen"
      :project-id="props.id"
      :character-id="emotionOverviewCharId"
      :character-name="emotionOverviewCharName"
      @close="emotionOverviewOpen = false"
    />

    <!-- Sprint 2.C+ 反事实工作台 + 2.C+ polish: 传 project 让世界观 baseline 预填 -->
    <CounterfactualWorkbench
      :open="workbenchOpen"
      :composable="cf"
      :characters="wbCharsList"
      :events="wbEventsList"
      :project="project ?? null"
      @close="workbenchOpen = false"
      @changed="onWorkbenchChanged"
      @baseline-inferred="onWorkbenchChanged"
    />

    <!--
      INIT.1 fix v3(2026-05-21):角色 edit 抽屉
        - 右侧 480px fixed sheet,半透明遮罩,Esc 取消(不保存) / 点遮罩 自动保存 关闭
        - 内容是从原 row inline form 整段搬过来的 3 个 group + footer
        - row.mode === 'edit' 时 v-if 触发显示;closeOtherEdits 已保证同时只有 1 个 row 处于 edit
        - Esc 走 cancelCharEditDrawer(还原)/ 点遮罩/× 走 closeCharEditDrawer(tryPersistChar)
    -->
    <transition name="drawer-fade">
      <div
        v-if="editingCharRow"
        class="char-edit-drawer-overlay"
        @click.self="closeCharEditDrawer"
        @keydown.esc.prevent="cancelCharEditDrawer"
      >
        <aside
          class="char-edit-drawer"
          role="dialog"
          aria-modal="true"
          :aria-label="`编辑角色:${editingCharRow.editName || '新角色'}`"
        >
          <header class="drawer-header">
            <div class="drawer-title-block">
              <Icon name="edit" :size="18" class="drawer-icon" />
              <h2 class="drawer-title">
                {{ editingCharRow.character ? "编辑:" : "新建角色:" }}
                <span class="drawer-name">{{ editingCharRow.editName || "未命名" }}</span>
              </h2>
            </div>
            <button
              type="button"
              class="drawer-close-btn"
              title="关闭(自动保存)"
              @click="closeCharEditDrawer"
            >×</button>
          </header>

          <div class="drawer-body">
            <!-- ===== 组 1:基础(紫)===== -->
            <div class="char-group char-group--basic">
              <header class="char-group-header">
                <Icon name="character" :size="14" class="char-group-icon" />
                <span class="char-group-title">基础</span>
                <span class="char-group-hint">这个人是谁</span>
              </header>

              <div class="char-edit-line">
                <input
                  v-model="editingCharRow.editName"
                  type="text"
                  placeholder="角色名"
                  maxlength="20"
                  class="row-input row-input-name"
                  :disabled="editingCharRow.mode === 'saving'"
                  v-focus
                  @blur="tryPersistChar(editingCharRow, editingCharIdx, $event)"
                  @keydown="onCharKey(editingCharRow, editingCharIdx, $event)"
                />
                <input
                  v-model="editingCharRow.editIdentity"
                  type="text"
                  placeholder="身份(可选,如:江湖浪子)"
                  maxlength="200"
                  class="row-input row-input-identity"
                  :disabled="editingCharRow.mode === 'saving'"
                  @blur="tryPersistChar(editingCharRow, editingCharIdx, $event)"
                  @keydown="onCharKey(editingCharRow, editingCharIdx, $event)"
                />
              </div>

              <label class="char-field">
                <span class="char-field-label">
                  性格
                  <span class="char-field-hint">{{ editingCharRow.editPersonality.length }} / 500</span>
                </span>
                <textarea
                  v-autogrow
                  v-model="editingCharRow.editPersonality"
                  placeholder="如:沉默寡言,但内心炽热;表面冷漠,实则重情"
                  maxlength="500"
                  rows="2"
                  class="row-input row-textarea"
                  :disabled="editingCharRow.mode === 'saving'"
                  @blur="tryPersistChar(editingCharRow, editingCharIdx, $event)"
                  @keydown="onCharKey(editingCharRow, editingCharIdx, $event)"
                ></textarea>
              </label>
            </div>

            <!-- ===== 组 2:AI 学这些(蓝)===== -->
            <div class="char-group char-group--ai-learn">
              <header class="char-group-header">
                <Icon name="message" :size="14" class="char-group-icon" />
                <span class="char-group-title">AI 学这些</span>
                <span class="char-group-hint">LLM 推演时模仿的样本 / 边界</span>
              </header>

              <label class="char-field">
                <span class="char-field-label">
                  台词风格示例
                  <span class="char-field-hint">
                    每行一句,最多 20 句 · AI 学这些句子的<strong>语气</strong>,不是固定要说的话
                  </span>
                </span>
                <textarea
                  v-autogrow
                  v-model="editingCharRow.editQuotesRaw"
                  placeholder="一句一行,写几句最像 TA 的台词"
                  rows="3"
                  class="row-input row-textarea"
                  :disabled="editingCharRow.mode === 'saving'"
                  @blur="tryPersistChar(editingCharRow, editingCharIdx, $event)"
                  @keydown="onCharKey(editingCharRow, editingCharIdx, $event)"
                ></textarea>
              </label>

              <label class="char-field">
                <span class="char-field-label">
                  倾向避免
                  <span class="char-field-hint">每行一条,最多 20 条(剧情合理时可破,非硬约束)</span>
                </span>
                <textarea
                  v-autogrow
                  v-model="editingCharRow.editNoGoRaw"
                  placeholder="例:尽量不在敌人面前流泪&#10;不愿对女人小孩动手"
                  rows="2"
                  class="row-input row-textarea"
                  :disabled="editingCharRow.mode === 'saving'"
                  @blur="tryPersistChar(editingCharRow, editingCharIdx, $event)"
                  @keydown="onCharKey(editingCharRow, editingCharIdx, $event)"
                ></textarea>
              </label>
            </div>

            <!-- ===== 组 3:AI 自检(灰,折叠)===== -->
            <details class="char-group char-group--baseline">
              <summary class="char-group-header char-group-header--summary">
                <Icon name="settings" :size="14" class="char-group-icon" />
                <span class="char-group-title">AI 自检 · 行为基线</span>
                <span class="char-group-hint">高级配置 · 防角色越级</span>
              </summary>

              <div class="baseline-grid">
                <label class="baseline-field">
                  <span class="baseline-label">
                    语气登记
                    <span class="baseline-hint">日常说话基调</span>
                  </span>
                  <select
                    v-model="editingCharRow.editSpeechRegister"
                    class="row-input baseline-select"
                    :disabled="editingCharRow.mode === 'saving'"
                    @blur="tryPersistChar(editingCharRow, editingCharIdx, $event)"
                    @change="tryPersistChar(editingCharRow, editingCharIdx)"
                  >
                    <option value="">— 未设(AI 自由发挥)—</option>
                    <option value="卑微">卑微(讨好型,常自贬)</option>
                    <option value="平和">平和(理性,情绪平稳)</option>
                    <option value="强硬">强硬(果断,不让步)</option>
                    <option value="恶意">恶意(敌意,主动攻击)</option>
                  </select>
                </label>

                <label class="baseline-field">
                  <span class="baseline-label">
                    情绪强度基线
                    <span class="baseline-hint">
                      {{ editingCharRow.editEmotionalIntensity === null ? "未设" : `${editingCharRow.editEmotionalIntensity} / 10` }}
                      · 本轮发言 ±2 内浮动
                    </span>
                  </span>
                  <div class="baseline-slider-row">
                    <input
                      type="range"
                      min="0"
                      max="10"
                      step="1"
                      :value="editingCharRow.editEmotionalIntensity ?? 5"
                      class="baseline-slider"
                      :disabled="editingCharRow.mode === 'saving'"
                      @input="editingCharRow.editEmotionalIntensity = Number(($event.target as HTMLInputElement).value)"
                      @change="tryPersistChar(editingCharRow, editingCharIdx)"
                    />
                    <button
                      v-if="editingCharRow.editEmotionalIntensity !== null"
                      type="button"
                      class="baseline-clear-btn"
                      title="清空(让 AI 自由判断)"
                      @click.stop="editingCharRow.editEmotionalIntensity = null; tryPersistChar(editingCharRow, editingCharIdx)"
                    >×</button>
                  </div>
                </label>

                <label class="baseline-field">
                  <span class="baseline-label">
                    道德罗盘
                    <span class="baseline-hint">价值取向</span>
                  </span>
                  <select
                    v-model="editingCharRow.editMoralCompass"
                    class="row-input baseline-select"
                    :disabled="editingCharRow.mode === 'saving'"
                    @blur="tryPersistChar(editingCharRow, editingCharIdx, $event)"
                    @change="tryPersistChar(editingCharRow, editingCharIdx)"
                  >
                    <option value="">— 未设(AI 自由发挥)—</option>
                    <option value="善">善(利他,守原则)</option>
                    <option value="灰">灰(实用,情境而定)</option>
                    <option value="恶">恶(自私,可越界)</option>
                  </select>
                </label>

                <!-- P0G.2(2026-05-24)— "雷区" UI 字段已彻底删除。
                     原因:与上层"禁忌"(no_go_list)字段语义重复 + 隐藏字段写入会影响 AI 行为
                          却不在用户视野内,违反"透明 AI 协作"价值观。
                     迁移:已有数据通过 migration 062 自动合并到 no_go_list。 -->
              </div>
            </details>

            <!-- SP-2(2026-05-28):角色驱动五件套 - 灵魂续写北极星 · 实体身份层
                 这 5 字段直接灌进 build_character_drivers_block → hard_constraints section 1.5
                 续作 narrator / agent 都能消费,决定"角色到底在追什么 / 怕什么"
                 全 mode 通用(initial / middle / end / cycle 都生效) -->
            <details class="char-group char-group--drivers">
              <summary class="char-group-header char-group-header--summary">
                <Icon name="compass" :size="14" class="char-group-icon" />
                <span class="char-group-title">角色驱动 · 灵魂续写</span>
                <span class="char-group-hint">5 字段决定 AI 怎么演这个角色</span>
              </summary>

              <div class="drivers-grid">
                <label class="drivers-field">
                  <span class="drivers-label">
                    表层目标
                    <span class="drivers-hint">他自以为要的(例:成绩排第一)</span>
                  </span>
                  <textarea
                    v-model="editingCharRow.editSurfaceGoal"
                    class="drivers-textarea"
                    rows="2"
                    maxlength="300"
                    placeholder="他在追求什么(他自己说得出口的)"
                    :disabled="editingCharRow.mode === 'saving'"
                    @blur="tryPersistChar(editingCharRow, editingCharIdx, $event)"
                    @keydown="onCharKey(editingCharRow, editingCharIdx, $event)"
                  />
                </label>

                <label class="drivers-field">
                  <span class="drivers-label">
                    深层渴求
                    <span class="drivers-hint">他真正缺的(例:被无条件接纳)</span>
                  </span>
                  <textarea
                    v-model="editingCharRow.editDeepNeed"
                    class="drivers-textarea"
                    rows="2"
                    maxlength="300"
                    placeholder="他自己说不出口、但行为里漏出来的渴望"
                    :disabled="editingCharRow.mode === 'saving'"
                    @blur="tryPersistChar(editingCharRow, editingCharIdx, $event)"
                    @keydown="onCharKey(editingCharRow, editingCharIdx, $event)"
                  />
                </label>

                <label class="drivers-field">
                  <span class="drivers-label">
                    致命盲点
                    <span class="drivers-hint">他看不见的事(例:把控制当爱)</span>
                  </span>
                  <textarea
                    v-model="editingCharRow.editFatalBlindSpot"
                    class="drivers-textarea"
                    rows="2"
                    maxlength="300"
                    placeholder="他自我认知里最大的死角,导致他重复犯同种错"
                    :disabled="editingCharRow.mode === 'saving'"
                    @blur="tryPersistChar(editingCharRow, editingCharIdx, $event)"
                    @keydown="onCharKey(editingCharRow, editingCharIdx, $event)"
                  />
                </label>

                <label class="drivers-field">
                  <span class="drivers-label">
                    弧光
                    <span class="drivers-hint">开篇 → 结局的内在变化(例:从控制 → 学会放手)</span>
                  </span>
                  <textarea
                    v-model="editingCharRow.editArcFromTo"
                    class="drivers-textarea"
                    rows="2"
                    maxlength="300"
                    placeholder="他这一生 / 这个故事里走过的内心路径"
                    :disabled="editingCharRow.mode === 'saving'"
                    @blur="tryPersistChar(editingCharRow, editingCharIdx, $event)"
                    @keydown="onCharKey(editingCharRow, editingCharIdx, $event)"
                  />
                </label>

                <label class="drivers-field drivers-field--full">
                  <span class="drivers-label">
                    秘密(每行一条)
                    <span class="drivers-hint">他对所有人瞒着的事,推演会注入"X 知道但不能说"约束</span>
                  </span>
                  <textarea
                    v-model="editingCharRow.editSecretsRaw"
                    class="drivers-textarea"
                    rows="3"
                    placeholder="一行一条秘密,默认对全员瞒(进阶版后期可选择只对部分人瞒)"
                    :disabled="editingCharRow.mode === 'saving'"
                    @blur="tryPersistChar(editingCharRow, editingCharIdx, $event)"
                    @keydown="onCharKey(editingCharRow, editingCharIdx, $event)"
                  />
                </label>

                <!-- SP-2.1(2026-05-29):AI 推断驱动 -->
                <div class="drivers-infer-row">
                  <label class="drivers-overwrite-label">
                    <input
                      v-model="driversOverwrite"
                      type="checkbox"
                      :disabled="driversInferring"
                    />
                    覆盖已填字段(默认只填空白处)
                  </label>
                  <button
                    type="button"
                    class="drivers-infer-btn"
                    :disabled="driversInferring || !editingCharRow.character"
                    :title="editingCharRow.character ? `AI 推断 5 件套(初始态基于该角色档案+关系网+参与事件;中末尾态基于原作)` : `先保存角色后再推断`"
                    @click="inferCharacterDrivers"
                  >
                    <Icon name="sparkles" :size="14" />
                    {{ driversInferring ? "AI 推断中…" : "AI 推断驱动" }}
                  </button>
                </div>
              </div>
            </details>
          </div>

          <footer class="drawer-footer">
            <span v-if="editingCharRow.mode === 'saving'" class="row-status">保存中…</span>
            <span v-else-if="editingCharRow.errorMsg" class="row-error">{{ editingCharRow.errorMsg }}</span>
            <span v-else class="char-edit-hint">点击外部或 × 自动保存 · Esc 取消</span>
          </footer>
        </aside>
      </div>
    </transition>
  </div>
</template>

<style scoped>
.project-page {
  min-height: 100vh;
  background: var(--color-bg);
}

/* ===== M7.D-fix(2026-05-20)Tab 切换 ===== */
.project-tabs {
  position: sticky;
  top: 0;       /* topbar 自带 sticky:0,这里浮在它下方 — 实际渲染时不会重叠因为 topbar 是 flex 行 */
  display: flex;
  gap: var(--space-2);
  padding: 0 var(--space-6);
  background: var(--color-bg);
  border-bottom: 1px solid var(--color-border);
  z-index: calc(var(--z-sticky) - 1);
}
.project-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition:
    color var(--duration-fast) var(--ease-out),
    border-color var(--duration-fast) var(--ease-out);
}
.project-tab:hover {
  color: var(--color-text);
}
.project-tab--active {
  color: var(--color-accent-text);
  border-bottom-color: var(--color-accent);
  font-weight: 500;
}
.tab-icon {
  font-size: var(--text-base);
  line-height: 1;
}

/* Sprint 6.A2 polish(2026-05-23 四轮):absolute 定位让 button 与第一个 section 标题水平对齐
   - top 与 .main 的 padding-top 同步(var(--space-6) = 24px),并 +3px 微调让 pill 中心对齐
     "作品文件" text-lg 标题 baseline
   - right 与 .main 的 padding-right 同步
   - button 脱离文档流后,下方 section 自动上移 → 用户要求的"作品文件整体往上"自然达成
   - z-index 防被下方 section 内容覆盖(实际不会重叠但保险) */
.graph-tab-actions {
  position: absolute;
  top: calc(var(--space-6) + 3px);
  right: var(--space-6);
  z-index: 2;
}

/* Bug 修复(2026-05-27):初始态下"作品文件" section 隐藏 — 失去对齐锚点,
   absolute 的 pill 会浮在第一个可见 section(世界观)右上角,与卡片边框重合。
   --initial modifier 改 relative + flex-end,让 pill 占自己的一行,下方 section 自然下推。 */
.graph-tab-actions--initial {
  position: static;
  top: auto;
  right: auto;
  display: flex;
  justify-content: flex-end;
  margin-bottom: var(--space-4);
}

/* pill 形态 v4:小巧 outline 风 — 主元素(作品文件标题)优先,此按钮降到辅助权重
   v3 用实心紫填充 + 双层阴影,视觉权重逼近 primary 按钮,抢了主标题的戏
   v4:白底 + 1px 紫边框 + 紧凑 padding + xs 字号 + 无阴影,affordance 来自边框
   hover 时反白填充 — 静止安静、激活时清晰 */
.graph-focus-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 12px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-accent-text);
  background: var(--color-surface);
  border: 1px solid var(--color-accent-border);
  border-radius: 999px;
  transition: background var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out);
}
.graph-focus-pill:hover:not(:disabled) {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}
.graph-focus-pill:disabled {
  opacity: 0.55;
}
.focus-pill-icon {
  font-size: var(--text-xs);
  line-height: 1;
}
.focus-pill-arrow {
  font-size: 11px;
  line-height: 1;
  opacity: 0.85;
}

/* ===== 顶栏 ===== */
.topbar {
  position: sticky;
  top: 0;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-4) var(--space-6);
  background: var(--color-bg);
  border-bottom: 1px solid var(--color-border);
  gap: var(--space-4);
  z-index: var(--z-sticky);
}

/* UI 优化(2026-05-21 二轮):topbar-left 改竖向布局,书名 + tags 副标题成一组 */
.topbar-left {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 2px;
  min-width: 0;
  flex: 1;
}

.page-title {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
}

/* v6.3:项目顶部 mode chip 显态(让用户随时知道当前在什么态项目里)*/
.mode-chip-inline {
  font-size: var(--text-xs);
  font-weight: 500;
  padding: 2px var(--space-2);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
}

/*
 * Sprint 6.A2 FOCUS.2(2026-05-21)叙述视角 chip —
 * 用 native select 既能"显示当前值",又能"一键改"。视觉对齐 mode-chip-inline。
 * 已识别 → 紫色背景;未识别(value="") → 灰色淡化
 */
.pov-chip-select {
  font-family: inherit;
  font-size: var(--text-xs);
  font-weight: 500;
  padding: 2px var(--space-2);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
  cursor: pointer;
  appearance: none;     /* 去掉浏览器默认箭头,与 mode-chip 视觉一致 */
  margin-left: var(--space-1);
  transition: border-color 120ms ease, background 120ms ease;
}

.pov-chip-select:hover {
  border-color: var(--color-accent-border);
}

.pov-chip-select:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

.pov-chip-select--unset {
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
}

.page-tags {
  display: flex;
  gap: var(--space-1);
}

/* UI 优化(2026-05-21 二轮):tags 副标题 — 紧贴书名下方,小字号弱化文本,以 · 分隔
 * 不再是 chip 框,看起来像元数据副标题而非交互元素 */
.page-tags-subtitle {
  margin: 0;
  padding: 0;
  font-size: 11px;
  line-height: 1.4;
  color: var(--color-text-muted);
  letter-spacing: 0.01em;
  /* 长 tags 列表自动省略号(超过容器宽度) */
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* v6.3:非初始态占位卡(未抽图谱)/ v6.4:已抽图谱时切色变 success 色 */
.ai-pending-block {
  /* 容器使用 section 默认间距 */
}
.ai-pending-card {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-7) var(--space-5);
  text-align: center;
  background: var(--color-bg-subtle);
  border: 1px dashed var(--color-border-strong);
  border-radius: var(--radius-lg);
}
.ai-pending-card--ready {
  background: rgba(22, 163, 74, 0.06);
  border-color: rgba(22, 163, 74, 0.4);
  border-style: solid;
}
.ai-pending-card--ready .pending-icon {
  color: #16A34A;
}
/* UI 优化(2026-05-21):紧凑 Banner 样式 — 单行 inline 布局,左对齐,垂直留白小 */
.ai-pending-card--banner {
  flex-direction: row;
  align-items: center;
  justify-content: flex-start;
  text-align: left;
  padding: var(--space-3) var(--space-4);
  gap: var(--space-3);
}
.ai-pending-card--banner .pending-icon {
  font-size: var(--text-xl);
  flex-shrink: 0;
}
.pending-banner-text {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text);
}
.pending-title-inline {
  font-weight: 600;
  color: #16A34A;
}
.pending-stats {
  color: var(--color-text-muted);
}
.pending-stats strong {
  color: var(--color-text);
  font-weight: 600;
}
.pending-hint {
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
/* Sprint 6.A1(2026-05-18):主角墙跟图谱就绪卡之间留缝 */
.protag-wall-section {
  margin-top: var(--space-3);
}
.pending-icon {
  /* banner 还在用此类(`.ai-pending-card--banner .pending-icon` override size=20 上方) */
  font-size: 32px;
  color: var(--color-accent);
  line-height: 1;
}

.tag-chip {
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}

.topbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.primary-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
  white-space: nowrap;
}

.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.primary-btn:disabled {
  background: var(--color-text-subtle);
  cursor: not-allowed;
}

.secondary-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-accent-text);
  background: var(--color-surface);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
  white-space: nowrap;
  transition: all var(--duration-fast) var(--ease-out);
}

.secondary-btn:hover:not(:disabled) {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
}

/* Sprint 2.C+ 反事实工作台按钮(中间态 / 周期态显)*/
.cf-workbench-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.cf-workbench-btn .cf-icon {
  font-size: var(--text-base);
}
.cf-workbench-btn .cf-count {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
}

.secondary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ===== 主区 ===== */
.main {
  max-width: 880px;
  margin: 0 auto;
  padding: var(--space-6) var(--space-6) var(--space-12);
  /* Sprint 6.A2 polish(2026-05-23 四轮):给 .graph-tab-actions absolute 子元素提供 anchor
     .main 内此前无 absolute 子元素,加 relative 零副作用 */
  position: relative;
}

.state-msg {
  text-align: center;
  padding: var(--space-12);
  color: var(--color-text-muted);
}

.state-error {
  color: var(--color-danger);
}

.error-banner {
  margin-bottom: var(--space-4);
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-radius: var(--radius-md);
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-2);
}

.x-btn {
  width: 24px;
  height: 24px;
  font-size: var(--text-md);
  line-height: 1;
  color: var(--color-danger);
  background: transparent;
  border-radius: var(--radius-full);
}

.x-btn:hover {
  background: rgba(220, 38, 38, 0.1);
}

/* ===== Section ===== */
.section {
  margin-bottom: var(--space-8);
}

/*
  INIT 视觉统一(2026-05-21):
    - .init-sections-stack flex 容器,用 gap 统一 5 个卡片间距
    - 各子组件(WorldBaselineEditor / SceneGraph / .init-card)自带 margin 被 stack 强制清零
    - :deep() 兜底覆盖子组件根 element 的 margin(scoped CSS 穿透)
*/
.init-sections-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.init-sections-stack > * {
  margin: 0 !important;
}
.init-sections-stack :deep(.wb-section),
.init-sections-stack :deep(.protag-wall) {
  margin: 0 !important;
}

/*
  .init-card 卡片样式,对齐世界观/场景图谱(.wb-section / .protag-wall)
    - 浅色卡片 + 圆角 + 边框,与上方两个组件视觉一致
    - 折叠按钮 ▸/▾ 在最左侧,与各折叠组件同款
    - emoji icon + 标题 + count chip + 副标题(展开时显示)
*/
.init-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4) var(--space-5);
  /* margin-bottom 已由 .init-sections-stack > * 统一清零 */
}
.init-card.is-collapsed {
  padding-bottom: var(--space-4);
}
.init-card-header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}
.init-card .collapse-btn {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  font-size: var(--text-base);
  line-height: 1;
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.init-card .collapse-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.init-card-title-block {
  flex: 1;
  min-width: 0;
}
.init-card-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.init-card .title-icon {
  font-size: var(--text-lg);
}
.init-card .title-count {
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-weight: 500;
}
.init-card-sub {
  margin: 4px 0 0;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.init-card .row-list {
  margin-top: var(--space-4);
}
/* 收紧 init-card 内 ul 默认 margin(原 .row-list 在 section 里有自带 margin) */
.init-card > .row-list {
  margin-bottom: 0;
}

/* Sprint D.6 骨架行堆叠(loadAll 时占位) */
.skeleton-row-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  margin-top: var(--space-3);
}

.section-header {
  margin-bottom: var(--space-3);
}

.section-title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
}

.opt {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-weight: 400;
}

.count {
  font-family: var(--font-mono);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-weight: 400;
}

.section-hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  margin-top: 2px;
}

/* ===== Row(三类共用) ===== */
.row-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background: var(--color-surface);
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: border-color var(--duration-fast) var(--ease-out),
              background var(--duration-fast) var(--ease-out);
  min-height: 40px;
  /* Sprint D.7 移除 content-visibility 优化(2026-05-12 用户报告 bug):
   *   原意是"轻量虚拟化"减少 off-screen layout 成本,但实际行为:
   *   ① content-visibility: auto 让浏览器懒计算视口外 row 尺寸
   *   ② contain-intrinsic-size: auto 64px 估算占位
   *   ③ 当下方 row(如 event)切到 edit 模式 / 点 participant chip 时,
   *      Vue 重渲染触发上方视口外 row 重新评估尺寸
   *   ④ character row 含 quotes/no_go 时实际高度 200-300px,远超 64px 估算
   *   ⑤ → 上方空间塌缩 → 整页上移闪烁
   *   当前项目 row 量 5-20,无虚拟化需求;真到 1000+ row 级别再回头条件化启用 */
}

.row:hover {
  border-color: var(--color-border);
}

.row--placeholder {
  background: transparent;
  color: var(--color-text-subtle);
  border-style: dashed;
  border-color: var(--color-border);
}

.row--placeholder:hover {
  background: var(--color-surface);
  color: var(--color-text-muted);
  border-style: solid;
}

.row--edit {
  background: var(--color-surface);
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.12);
  cursor: default;
}

.row--saving {
  opacity: 0.7;
  cursor: wait;
}

.row--error {
  border-color: var(--color-danger);
}

.placeholder-text {
  font-size: var(--text-sm);
}

.row-main {
  flex: 1;
  display: flex;
  align-items: center;       /* baseline → center,chip 与文字垂直居中防错位 */
  flex-wrap: wrap;           /* hotfix(2026-06-01):chip 太多自动换行不超出 */
  gap: var(--space-2) var(--space-3);  /* 行间距小一点,横间距维持 */
  min-width: 0;
}

.row-name {
  font-size: var(--text-base);
  color: var(--color-text);
  font-weight: 500;
  white-space: nowrap;
  flex-shrink: 0;
}

.row-meta {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rel-arrow {
  color: var(--color-accent);
  margin: 0 4px;
}

.rel-type {
  color: var(--color-accent-text);
  font-weight: 500;
}

.del-btn {
  width: 28px;
  height: 28px;
  font-size: var(--text-lg);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border-radius: var(--radius-full);
  flex-shrink: 0;
  opacity: 0;
  transition: all var(--duration-fast) var(--ease-out);
}

.row:hover .del-btn {
  opacity: 1;
}

.del-btn:hover {
  background: var(--color-danger-soft);
  color: var(--color-danger);
}

.row-input {
  padding: var(--space-1) var(--space-2);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  outline: none;
  min-width: 0;
}

.row-input:focus {
  border-color: var(--color-accent-border);
  background: var(--color-surface);
}

.row-input-name {
  width: 140px;
  flex-shrink: 0;
}

.row-input-identity {
  flex: 1;
}

.row-input-desc {
  flex: 1;
}

.row-status {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  white-space: nowrap;
  flex-shrink: 0;
}

.row-error {
  font-size: var(--text-xs);
  color: var(--color-danger);
  white-space: nowrap;
  flex-shrink: 0;
}

/* 事件行特殊布局:多行 */
.row-event.row--edit {
  flex-direction: column;
  align-items: stretch;
  gap: var(--space-2);
  padding: var(--space-3);
}

.participants {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-1);
}

.participant {
  /* INIT.7 bugfix:从 <label> 切到 <button>,需重置浏览器默认 button 样式 */
  font-family: inherit;
  line-height: 1.3;
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  user-select: none;
  transition:
    color 120ms ease,
    background 120ms ease,
    border-color 120ms ease;
}

.participant:hover {
  background: var(--color-surface-hover);
  border-color: var(--color-border-strong);
}

.participant.is-active {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
}

.participant.is-active:hover {
  /* 已选时 hover 保持 accent 色调,只是略亮 */
  background: var(--color-accent-soft);
  border-color: var(--color-accent-hover);
}

.participant:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 1px;
}

/* ===== 角色行:视图态的小型 meta chip(性格/台词/禁忌) ===== */
.row-tag {
  flex-shrink: 0;
  padding: 1px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  white-space: nowrap;
  cursor: help;
}

/* INIT.11(2026-05-21):台词风格 chip — view 模式预览首句 */
.quotes-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 300px;
  cursor: help;
}
.quotes-preview {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-style: italic;
  color: var(--color-text);
  max-width: 200px;
}
.quotes-more {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  padding: 0 4px;
  border-radius: var(--radius-sm);
}

/* INIT.7(2026-05-21):事件时间锚 chip — 紫色,提示时间锚定 */
.time-anchor-tag {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent-border, var(--color-accent));
}

.row-input-time-anchor {
  flex-shrink: 0;
  max-width: 180px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

/* INIT.4(2026-05-21):主角 chip — 点击 toggle */
.protag-chip {
  flex-shrink: 0;
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-subtle);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  white-space: nowrap;
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.protag-chip:hover {
  border-color: var(--color-accent-border);
  color: var(--color-accent-text);
}
.protag-chip.is-protag {
  color: #B45309;
  background: rgba(245, 158, 11, 0.12);
  border-color: rgba(245, 158, 11, 0.4);
  font-weight: 600;
}
.protag-chip.is-protag:hover {
  background: rgba(245, 158, 11, 0.2);
}

/* INIT.3(2026-05-21):行为基线 chip(view 模式)— 灰色齿轮 + 维度摘要 */
.baseline-tag {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  max-width: 260px;
  cursor: help;
  color: var(--color-text-subtle);
  background: var(--color-surface-sunken);
  border-color: var(--color-border);
}

/*
  INIT.3 ++(2026-05-21):角色 edit 3 语义组视觉重排
    每组 = 浅色背景 + 左 3px 色条 + 顶部 emoji header
    .char-group--basic     紫色组(角色名/身份/性格)
    .char-group--ai-learn  蓝色组(台词/倾向避免)
    .char-group--baseline  灰色组(行为基线,折叠 details)
  沿用项目 token,0 新增颜色
*/
.char-group {
  position: relative;
  margin-top: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
}
.char-group:first-of-type {
  margin-top: 0;
}

/* 组 1:基础(紫)— 沿用项目主色 token,纯背景色区分,不用左侧色条 */
.char-group--basic {
  background: var(--color-accent-soft);
}

/* 组 2:AI 学这些(蓝)— 直接用十六进制是因为项目无独立 info-blue token;
   保持与 accent-soft 同饱和度(luma≈250),与 accent 紫和谐共处 */
.char-group--ai-learn {
  background: #EEF4FE;
}

/* 组 3:行为基线(灰)— sunken surface 已存在;折叠状态 padding 略小 */
.char-group--baseline {
  background: var(--color-surface-sunken);
}
.char-group--baseline:not([open]) {
  padding-top: var(--space-2);
  padding-bottom: var(--space-2);
}

/* SP-2(2026-05-28):角色驱动组 — 复用 baseline 灰底,与 baseline 视觉一致 */
.char-group--drivers {
  background: var(--color-surface-sunken);
}
.char-group--drivers:not([open]) {
  padding-top: var(--space-2);
  padding-bottom: var(--space-2);
}
.char-group--drivers[open] .char-group-header--summary::before {
  transform: rotate(90deg);
}
.char-group--drivers[open] .char-group-header--summary {
  margin-bottom: var(--space-2);
}

/* 组 header(3 组共享) */
.char-group-header {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text);
  user-select: none;
}
.char-group-icon {
  font-size: var(--text-sm);
  flex-shrink: 0;
}
.char-group-title {
  font-weight: 600;
}
.char-group-hint {
  color: var(--color-text-subtle);
  font-size: var(--text-xs);
  font-weight: 400;
}

/* 组 3 是 details/summary,需要折叠箭头 + 点击态 */
.char-group-header--summary {
  cursor: pointer;
  list-style: none;
  margin-bottom: 0;  /* summary 默认贴近,展开后 grid 自带 margin */
}
.char-group-header--summary::-webkit-details-marker {
  display: none;
}
.char-group-header--summary::before {
  content: "▸";
  font-size: 10px;
  color: var(--color-text-subtle);
  transition: transform var(--duration-fast) var(--ease-out);
}
.char-group--baseline[open] .char-group-header--summary::before {
  transform: rotate(90deg);
}
.char-group--baseline[open] .char-group-header--summary {
  margin-bottom: var(--space-2);
}

/* 组内字段间距统一(避免每个 .char-field 自带 margin-top 失控) */
.char-group .char-field + .char-field,
.char-group .char-field + .char-edit-line,
.char-group .char-edit-line + .char-field {
  margin-top: var(--space-3);
}

.baseline-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-3);
  margin-top: var(--space-3);
}

/* SP-2(2026-05-28):drivers-grid 单列(都是 textarea,文本不挤压) */
.drivers-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-3);
  margin-top: var(--space-3);
}
.drivers-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.drivers-field--full {
  grid-column: 1 / -1;
}
.drivers-label {
  display: inline-flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text);
}
.drivers-hint {
  font-size: 10px;
  font-weight: 400;
  color: var(--color-text-subtle);
}
.drivers-textarea {
  width: 100%;
  padding: var(--space-2);
  font-family: inherit;
  font-size: var(--text-sm);
  line-height: var(--line-relaxed);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  outline: none;
  resize: vertical;
  transition: border-color var(--duration-fast) var(--ease-out);
}
.drivers-textarea:focus {
  border-color: var(--color-accent);
}
.drivers-textarea:disabled {
  background: var(--color-bg-subtle);
  cursor: not-allowed;
}
.baseline-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.baseline-field--full {
  grid-column: 1 / -1;
}
.baseline-label {
  display: inline-flex;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text);
}
.baseline-hint {
  font-size: 10px;
  font-weight: 400;
  color: var(--color-text-subtle);
}
.baseline-select {
  width: 100%;
  font-size: var(--text-xs);
}
.baseline-slider-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.baseline-slider {
  flex: 1;
  height: 4px;
  appearance: none;
  -webkit-appearance: none;
  background: var(--color-border);
  border-radius: 2px;
  outline: none;
}
.baseline-slider::-webkit-slider-thumb {
  appearance: none;
  -webkit-appearance: none;
  width: 14px;
  height: 14px;
  background: var(--color-accent);
  border-radius: 50%;
  cursor: pointer;
}
.baseline-slider::-moz-range-thumb {
  width: 14px;
  height: 14px;
  background: var(--color-accent);
  border-radius: 50%;
  cursor: pointer;
  border: none;
}
.baseline-clear-btn {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  padding: 0;
  font-size: var(--text-xs);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: 50%;
  cursor: pointer;
}
.baseline-clear-btn:hover {
  color: var(--color-danger);
  border-color: var(--color-danger);
}

.row-tag-warn {
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-color: var(--color-danger-soft);
}

/* ===== 角色行:编辑态展开成竖向 form(对齐事件行 row-event.row--edit 模式) =====
   INIT.1 fix v3(2026-05-21):此规则不再生效,因为角色 row 改为 drawer 编辑,
   不再加 row--edit class(改加 row--editing-drawer);保留兼容旧 selector,无副作用 */
.row-character.row--edit {
  flex-direction: column;
  align-items: stretch;
  gap: var(--space-3);
  padding: var(--space-3);
}

/*
  INIT.1 fix v3(2026-05-21):row 编辑态高亮 — 当前 row 在右侧 drawer 中编辑
  与未编辑 row 强烈视觉对比:紫色粗边 + 紫色 ring + 加深 shadow
*/
.row-character.row--editing-drawer {
  border-color: var(--color-accent);
  box-shadow:
    0 0 0 2px var(--color-accent-soft),
    0 4px 12px rgba(124, 58, 237, 0.15);
  background: var(--color-surface);
}
.row-character.row--editing-drawer:hover {
  border-color: var(--color-accent);
  box-shadow:
    0 0 0 2px var(--color-accent-soft),
    0 4px 12px rgba(124, 58, 237, 0.15);
}

/*
  INIT.1 fix v3(2026-05-21):角色编辑抽屉 — 右侧 480px sheet,半透明遮罩
  内容是原 row inline form 整段(3 group + footer)搬入,布局结构不变,
  只是位置从 row 内 → 独立浮动 sheet,让 list 保持简洁
*/
.char-edit-drawer-overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.3);
  backdrop-filter: blur(2px);
  display: flex;
  justify-content: flex-end;
  z-index: var(--z-modal);
}
.char-edit-drawer {
  width: 480px;
  max-width: 100vw;
  height: 100vh;
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  box-shadow: -4px 0 24px rgba(0, 0, 0, 0.1);
  display: flex;
  flex-direction: column;
}
.drawer-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border);
  background: var(--color-accent-soft);
}
.drawer-title-block {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
.drawer-icon {
  font-size: var(--text-lg);
  flex-shrink: 0;
}
.drawer-title {
  margin: 0;
  font-size: var(--text-md, 15px);
  font-weight: 500;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.drawer-name {
  font-weight: 700;
  color: var(--color-accent-text);
}
.drawer-close-btn {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  padding: 0;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: 50%;
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.drawer-close-btn:hover {
  color: var(--color-danger);
  border-color: var(--color-danger);
  background: var(--color-danger-soft);
}
.drawer-body {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.drawer-footer {
  padding: var(--space-2) var(--space-4);
  border-top: 1px solid var(--color-border);
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

/* 遮罩淡入 + drawer 从右滑入 */
.drawer-fade-enter-active,
.drawer-fade-leave-active {
  transition: opacity 0.18s var(--ease-out);
}
.drawer-fade-enter-active .char-edit-drawer,
.drawer-fade-leave-active .char-edit-drawer {
  transition: transform 0.22s var(--ease-out);
}
.drawer-fade-enter-from,
.drawer-fade-leave-to {
  opacity: 0;
}
.drawer-fade-enter-from .char-edit-drawer,
.drawer-fade-leave-to .char-edit-drawer {
  transform: translateX(100%);
}

/*
  INIT.1(2026-05-21):角色 view 模式下需要容纳 .char-rels 子区(关系列表)
  让 row-character 在 view 模式也 flex-wrap,.char-rels 强制换行占满整行

  INIT.1 fix(2026-05-21):用户反馈角色之间无明显边界 → 改为"小卡片"风格
    - 默认浅灰可见 border(取代原透明 border)
    - 角色之间增加 vertical gap(覆盖 .row-list 的 1px gap)
    - 加微 shadow 提升卡片浮起感
    - hover 强化主色 border 提示可点击
    - 占位行(+ 新角色)继续 dashed,不加 shadow,弱化二级感
*/
.row-character {
  flex-wrap: wrap;
  align-items: flex-start;
  border-color: var(--color-border);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
}
.row-list .row-character + .row-character,
.row-list .row-character + .row--placeholder {
  margin-top: var(--space-3);
}
.row-character:hover {
  border-color: var(--color-accent-border);
  box-shadow: 0 2px 6px rgba(0, 0, 0, 0.05);
}
/* 占位"+ 新角色"行不要 shadow(它本来就是 dashed,加 shadow 太重)*/
.row-character.row--placeholder {
  box-shadow: none;
}
.char-rels {
  flex-basis: 100%;
  width: 100%;
  margin-top: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
  cursor: default;  /* row 是 cursor:pointer,这里抑制 */
}
/* SP-7 hotfix(2026-06-01):polarity 色点 — 嵌在每条关系 chip 内,点击循环
   - 灰未标 / 绿正面 / 红对立 / 浅灰中性 */
.char-rel-polarity {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
  margin: 0 2px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: transform var(--duration-fast) var(--ease-out);
}
.char-rel-polarity:hover { transform: scale(1.4); }
.char-rel-polarity.polarity-unset    { background: var(--color-border-strong); }
.char-rel-polarity.polarity-positive { background: #22c55e; }
.char-rel-polarity.polarity-negative { background: #ef4444; }
.char-rel-polarity.polarity-neutral  { background: var(--color-text-subtle); }

.char-rel-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 4px 2px 8px;
  font-size: var(--text-xs);
  color: var(--color-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
  max-width: 280px;
}
.char-rel-chip:hover {
  border-color: var(--color-accent);
}
/* "← 指向此角色"chip 用更浅色调区分(in-bound) */
.char-rel-chip--in {
  background: var(--color-surface-sunken);
  border-color: var(--color-border);
}
.char-rel-chip--in:hover {
  border-color: var(--color-text-subtle);
}
.char-rel-chip .rel-arrow {
  color: var(--color-text-subtle);
  font-weight: 600;
}
.char-rel-other {
  font-weight: 600;
}
.char-rel-type {
  color: var(--color-accent-text);
  flex-shrink: 0;
}
.char-rel-chip--in .char-rel-type {
  color: var(--color-text);
}
.char-rel-desc {
  color: var(--color-text-subtle);
  max-width: 100px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.char-rel-chip-del {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 16px;
  height: 16px;
  margin-left: 2px;
  font-size: var(--text-xs);
  line-height: 1;
  color: var(--color-text-subtle);
  border-radius: 50%;
  cursor: pointer;
}
.char-rel-chip-del:hover {
  color: var(--color-danger);
  background: var(--color-danger-soft);
}

/*
  INIT.2(2026-05-21):chip 内"🕒 N ▸/▾"阶段按钮 + 展开的 timeline wrap
    - 按钮:span 元素(button 不能嵌 button),hover 浅紫底强化"可点击"
    - 展开后:caret 旋转 + 浅紫底高亮
    - timeline-wrap:flex-basis 100% 强制换行,占整行;浅紫底 + 描边
*/
.char-rel-phases-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 1px 6px;
  margin-left: 4px;
  font-size: 10px;
  color: var(--color-text-subtle);
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.char-rel-phases-btn:hover {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent-border);
}
.char-rel-phases-btn.is-expanded {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent-border);
}
.char-rel-phases-count {
  font-weight: 600;
}
.char-rel-phases-caret {
  font-size: 9px;
  margin-left: 1px;
}

.char-rel-timeline-wrap {
  flex-basis: 100%;
  width: 100%;
  margin-top: var(--space-2);
  padding: var(--space-2);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-sm);
  cursor: default;
}
/* RelationshipTimeline 内部 .rel-timeline 自己已有 padding/background,
   外层 wrap 套一层浅紫色高亮"这是该 chip 的子区"信号即可 — 内层透明化 */
.char-rel-timeline-wrap :deep(.rel-timeline) {
  background: transparent;
  border: none;
  padding: 0;
}
.char-rel-add-btn {
  display: inline-flex;
  align-items: center;
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  background: transparent;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.char-rel-add-btn:hover {
  color: var(--color-accent-text);
  border-color: var(--color-accent-border);
  background: var(--color-accent-soft);
}
.char-rels-empty {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-style: italic;
}

/* inline edit 表单(关系 chip 点击展开 或 + 加关系展开)*/
.char-rel-edit {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  flex-basis: 100%;
  padding: var(--space-2) var(--space-3);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-sm);
}
.char-rel-edit--new {
  /* "+ 加关系"展开的占位行:更淡的提示底色 */
  background: var(--color-surface);
  border-style: dashed;
}
.char-rel-edit-fixed {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-weight: 600;
  color: var(--color-text);
  flex-shrink: 0;
}
.char-rel-type-select,
.char-rel-target-select {
  max-width: 140px;
  font-size: var(--text-xs);
}
.char-rel-desc-input {
  flex: 1;
  min-width: 120px;
  font-size: var(--text-xs);
}
.char-rel-cancel-btn,
.char-rel-del {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  padding: 0;
  font-size: var(--text-base);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: 50%;
  cursor: pointer;
}
.char-rel-cancel-btn:hover,
.char-rel-del:hover {
  color: var(--color-danger);
  border-color: var(--color-danger);
}

.char-edit-line {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}

.char-edit-line .row-input-name {
  width: 160px;
}

.char-edit-line .row-input-identity {
  flex: 1;
}

.char-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.char-field-label {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}

.char-field-hint {
  color: var(--color-text-subtle);
  font-weight: 400;
}

.row-textarea {
  width: 100%;
  /* 不允许手动拖拉,也不要滑动条 — 高度由 v-autogrow 指令按内容算 */
  resize: none;
  overflow: hidden;
  font-family: inherit;
  line-height: 1.5;
  min-height: 32px;
}

.char-edit-footer {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  min-height: 16px;
}

.char-edit-hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

/* ============================================================
 * SP-1(2026-05-28)— 故事脊柱编辑卡片(全 mode 通用)
 * 5-28 修正:从 init-card 抽出独立 story-core-card 类,
 * 因为它要在 initial / middle / end / cycle 全 mode 显示,
 * 不能依赖 init-card 的"初始态专属"视觉系统
 * ============================================================ */
/* 一键灌满北极星 banner(2026-05-29 末) */
.all-boards-banner {
  background: linear-gradient(
    135deg,
    var(--color-accent-soft) 0%,
    rgba(124, 58, 237, 0.05) 100%
  );
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4) var(--space-5);
  margin-bottom: var(--space-5);
  display: flex;
  align-items: center;
  gap: var(--space-4);
  flex-wrap: wrap;
}
.all-boards-title-block {
  flex: 1;
  min-width: 280px;
}
.all-boards-title {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0 0 var(--space-1);
}
.all-boards-title .title-icon {
  color: var(--color-accent-text);
}
.all-boards-desc {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: 1.6;
  margin: 0;
}
.all-boards-desc strong {
  color: var(--color-text);
  font-weight: 600;
}
.all-boards-time {
  display: block;
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  margin-top: var(--space-1);
}
.all-boards-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
}
.all-boards-opt {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: var(--space-1) var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  user-select: none;
}
.all-boards-opt input { margin: 0; }
.all-boards-opt:hover { color: var(--color-text); }
.all-boards-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out);
}
.all-boards-btn:hover:not(:disabled) { background: var(--color-accent-hover); }
.all-boards-btn:disabled {
  /* hotfix(2026-06-01):取消整体 opacity,白字会跟着糊 — 改用 wait cursor 表达"在跑" */
  cursor: wait;
  background: var(--color-accent);
  /* 视觉提示在跑:背景做一个微弱呼吸,不动文字 */
  animation: all-boards-btn-pulse 1.6s ease-in-out infinite;
}
@keyframes all-boards-btn-pulse {
  0%, 100% { background: var(--color-accent); }
  50% { background: var(--color-accent-hover); }
}

/* SSE 进度面板(2026-05-30²)— 长任务实时反馈
   hotfix(2026-06-01):banner 是浅紫渐变(light theme),原暗色背景配色文字消失,
   全部改成对浅紫高对比的深色文字 + 深色 chip 背景 */
.all-boards-progress {
  grid-column: 1 / -1;
  width: 100%;
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px dashed var(--color-accent-border);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.ab-progress-bar {
  position: relative;
  width: 100%;
  height: 8px;
  background: rgba(124, 58, 237, 0.15);    /* 紫色淡底,跟主题协调 */
  border-radius: var(--radius-full);
  overflow: hidden;
}
.ab-progress-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--color-accent), var(--color-accent-hover));
  transition: width var(--duration-base) var(--ease-out);
}
.ab-progress-text {
  position: absolute;
  top: 12px;
  left: 0;
  font-size: var(--text-xs);
  color: var(--color-text);          /* 黑字而非白字 */
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.ab-stage-list {
  list-style: none;
  margin: var(--space-5) 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 200px;
  overflow-y: auto;
}
.ab-stage-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  border-radius: var(--radius-sm);
  background: var(--color-surface);  /* 白色 chip 在浅紫底上明显 */
  border: 1px solid var(--color-border);
}
.ab-stage-icon {
  display: inline-flex;
  width: 16px;
  height: 16px;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  flex-shrink: 0;
  font-weight: 700;
  font-size: 11px;
}
.ab-stage--running .ab-stage-icon {
  background: var(--color-accent);
  color: #fff;
  animation: ab-pulse 1.2s ease-in-out infinite;
}
.ab-stage--running .ab-stage-label {
  color: var(--color-accent-text);
  font-weight: 600;
}
@keyframes ab-pulse {
  0%, 100% { opacity: 0.6; }
  50% { opacity: 1; }
}
.ab-stage--done .ab-stage-icon {
  background: #16a34a;               /* 实色深绿 */
  color: #fff;
}
.ab-stage--done .ab-stage-label {
  color: var(--color-text);          /* 完成态深色文字,清晰可见 */
}
.ab-stage--failed .ab-stage-icon {
  background: #dc2626;               /* 实色深红 */
  color: #fff;
}
.ab-stage--failed .ab-stage-label {
  color: var(--color-danger);
  font-weight: 500;
}
.ab-stage-label {
  color: var(--color-text);          /* 默认深色字(idle 状态用) */
  flex-shrink: 0;
}
.ab-stage-err {
  color: var(--color-danger);
  font-size: 11px;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/* SP-8(2026-05-28):视角扩展卡 — 复用 story-core-card 样式(并列项目级配置) */
.narrative-view-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-5) var(--space-6);
  margin-bottom: var(--space-5);
}

/* SP-8:select 与 textarea 一致的视觉 */
.sc-select {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  outline: none;
}
.sc-select:focus { border-color: var(--color-accent); }

/* SP-8:radio 横向 chip 群 */
.nv-radio-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.nv-radio {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  cursor: pointer;
  user-select: none;
}
.nv-radio input[type="radio"] {
  margin: 0;
}
.nv-radio:has(input:checked) {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent-border);
}

/* hotfix(2026-06-01):story-facts-wrap 补全白色卡片样式
   与 story-core-card / narrative-view-card 视觉一致 */
.story-facts-wrap {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-5) var(--space-6);
  margin-bottom: var(--space-5);
}

/* hotfix(2026-06-01):3 个卡片折叠时压成单行 */
.story-core-card.is-collapsed,
.narrative-view-card.is-collapsed,
.story-facts-wrap.is-collapsed {
  padding-top: var(--space-3);
  padding-bottom: var(--space-3);
}
.story-core-card.is-collapsed .story-core-header,
.narrative-view-card.is-collapsed .story-core-header,
.story-facts-wrap.is-collapsed .story-core-header {
  margin-bottom: 0;
}
/* 折叠按钮 + 标题区横向布局 */
.story-core-header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
}
.story-core-card .collapse-btn,
.narrative-view-card .collapse-btn,
.story-facts-wrap .collapse-btn {
  flex-shrink: 0;
  margin-top: 2px;
}
/* 知识边界 wrapper 包 panel 时,删 panel 自己的外边距(已被 wrapper 接管) */
.story-facts-wrap .story-facts-embedded {
  margin-bottom: 0;
  border: 0;
  padding: 0;
  background: transparent;
}
/* 知识边界 panel 内已有自己的 header,wrapper header 显示外层标题就够,
   panel 内的 header 在 wrapper 模式下不该再出 — 实际上 panel header 包含"AI 推断"按钮等
   功能性元素,保留它(只是不显示卡片自己的 title 那一段) */

.story-core-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-5) var(--space-6);
  margin-bottom: var(--space-5);
}
.story-core-header {
  display: flex;
  align-items: flex-start;
  margin-bottom: var(--space-4);
}
.story-core-title-block {
  flex: 1;
}
.story-core-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.story-core-title .title-icon {
  color: var(--color-accent);
}
.story-core-desc {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: 1.7;
  margin: var(--space-2) 0 0;
}
.story-core-body {
  display: flex;
  flex-direction: column;
}

.sc-field {
  margin-bottom: var(--space-3);
}
.sc-field:last-of-type {
  margin-bottom: var(--space-4);
}
.sc-label {
  display: block;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  font-weight: 600;
  margin-bottom: var(--space-1);
  letter-spacing: 0.02em;
}
.sc-textarea {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  font-family: inherit;
  font-size: var(--text-sm);
  line-height: 1.6;
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  resize: vertical;
  transition: border-color var(--duration-fast) var(--ease-out);
}
.sc-textarea:focus {
  outline: none;
  border-color: var(--color-accent-border);
  box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.1);
}
.sc-textarea::placeholder {
  color: var(--color-text-subtle);
}
.sc-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-2);
}
.sc-save-btn {
  padding: var(--space-2) var(--space-4);
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border: 0;
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  font-weight: 500;
  transition: background var(--duration-fast) var(--ease-out);
}
.sc-save-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}
.sc-save-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* 2026-06-01:章节字数区间编辑器 — min~max 横排 */
.chapter-range-row {
  display: flex;
  align-items: flex-end;
  gap: var(--space-3);
}
.chapter-range-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  flex: 1;
}
.chapter-range-input {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  background: var(--color-bg-elevated);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  color: var(--color-text);
}
.chapter-range-input:focus {
  outline: none;
  border-color: var(--color-accent-border);
  box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.1);
}
.chapter-range-hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.chapter-range-sep {
  padding-bottom: var(--space-4);
  color: var(--color-text-subtle);
  font-size: var(--text-lg);
}

/* SP-1.5 / SP-8.1(2026-05-29):AI 推断按钮 — ghost 风格,与 save 按钮区分 */
.sc-infer-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: var(--space-2) var(--space-4);
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  font-weight: 500;
  transition: all var(--duration-fast) var(--ease-out);
  cursor: pointer;
}
.sc-infer-btn:hover:not(:disabled) {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}
.sc-infer-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* SP-2.1(2026-05-29):角色 drawer 内 AI 推断驱动 row */
.drivers-infer-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding-top: var(--space-3);
  margin-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
  grid-column: 1 / -1;
}
.drivers-overwrite-label {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  cursor: pointer;
  user-select: none;
}
.drivers-overwrite-label input[type="checkbox"] { margin: 0; }
.drivers-infer-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: var(--space-2) var(--space-3);
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  font-weight: 500;
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.drivers-infer-btn:hover:not(:disabled) {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}
.drivers-infer-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
