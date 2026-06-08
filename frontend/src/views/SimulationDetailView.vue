<script setup lang="ts">
/**
 * SimulationDetailView — 单条推演详情页(Sprint 1.J)。
 *
 * URL: /simulations/:id   需登录
 *
 * 状态驱动 4 屏:
 *   running / queued / directing / composing → 进度条 + 阶段文案 + 实时 cost
 *   done                                      → NarrativeStream(autoStream=false 立即全显)
 *   failed                                    → error_message
 *   error(本地 GET 失败)                     → 网络错误提示
 *
 * 操作:
 *   ← 返回项目(项目作品列表 Tab;无 project_id 时兜底回 dashboard)
 *   重新推演 → router push 到 /projects/{project_id}(用户在项目页自己点 AI 续写)
 *   删除    → 确认后 DELETE,回 /projects/{project_id} 的作品列表 Tab
 *            (M7.E-fix 2026-05-20:此前回 /dashboard 是 IA 重构前残留 bug)
 *
 * 复用:NarrativeStream(浅色阅读 + 复制 + 下载 md);useSimulation.subscribe()。
 */
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "../api/client";
import { useEventBus } from "../stores/events";
import {
  ApiError,
  USER_FIXABLE_AUDIT_KINDS,
  type AuditIssue,
  type AuditIssueKind,
  type EmotionalStateRecord,
  type SimulationFull,
  type SimulationSummary,
} from "../api/types";
import CanonicalGuardianPanel from "../components/CanonicalGuardianPanel.vue";
import QualityScoreCard from "../components/QualityScoreCard.vue";
// SP-5.1(2026-05-28):伏笔账本面板
// SP-6.1 TensionCurvePanel 组件已就绪,集成留 SP-6.2(OutlineReviewView 更合适)
import PlotThreadsPanel from "../components/PlotThreadsPanel.vue";
import StateTimelinePanel from "../components/StateTimelinePanel.vue";
import BadgeDownloadDialog from "../components/BadgeDownloadDialog.vue";
import CharacterEmotionModal from "../components/CharacterEmotionModal.vue";
import ExportSimulationDialog from "../components/ExportSimulationDialog.vue";
import SceneHintInput from "../components/SceneHintInput.vue";
import SkeletonBlock from "../components/SkeletonBlock.vue";
import NarrativeStream from "../components/NarrativeStream.vue";
import { useAudit } from "../composables/useAudit";
import { confirm as confirmDialog } from "../composables/useConfirm";
import { toast } from "../composables/useToast";
import {
  formatSimulationEvent,
  useSimulation,
  type FormattedEvent,
} from "../composables/useSimulation";

const route = useRoute();
const router = useRouter();
const events = useEventBus();

// 路由参数 — 直接读 params.id;切到另一条详情时 watch 触发 subscribe 重订
const simId = computed(() => String(route.params.id ?? ""));

// useSimulation 的 getProjectId 这里其实不会被 start() 用到(我们走 subscribe),
// 但签名要求,塞 sim 自带的 project_id 当占位
const session = useSimulation(
  () => session.simulation.value?.project_id ?? "",
);

// 项目名:GET 时一并拿(后端 SimulationFull 没带 project_name,
// 单独 GET project 一次拿名字。或:从 dashboard 跳过来时通过 router state 传)
const projectName = ref<string>("");
// 2.D 正典守护者:initial 态不适用,需要从 project.mode 判断
const projectMode = ref<"initial" | "middle" | "end" | "cycle" | null>(null);

async function loadProjectName(projectId: string) {
  if (!projectId) return;
  try {
    const proj = await api.get<{
      name: string;
      mode: "initial" | "middle" | "end" | "cycle";
    }>(`/projects/${projectId}`);
    projectName.value = proj.name;
    projectMode.value = proj.mode;
  } catch {
    projectName.value = "(无法加载项目名)";
    projectMode.value = null;
  }
}

// 正典审计适用性:initial 态项目无原作 canon → 隐藏面板
const canonicalApplicable = computed(
  () => projectMode.value !== null && projectMode.value !== "initial",
);

// Sprint 3.A 修:mode 专属动词(与 SimulationDock.dockHeaderConfig + ProjectView.simulateButtonLabel 同 mapping)
// — 失败屏标题用此拼"X 失败",避免末尾态产物详情页显"推演失败"的语义错位
const modeActionLabel = computed(() => {
  const m = projectMode.value;
  if (m === "middle") return "AI 重塑";
  if (m === "end") return "AI 续写";
  if (m === "cycle") return "AI 长篇";
  return "AI 推演";   // initial 默认 / mode 未加载
});

// 2.E 导出对话框
const exportDialogOpen = ref(false);

// Sprint D.3:创作徽章 dialog 开关(透明 AI 协作 — 用户贴到同人圈作品发布页)
const badgeDialogOpen = ref(false);

// Sprint 3.A 末尾态原作末段折叠默认收起
const tailExpanded = ref(false);

watch(
  simId,
  (id) => {
    if (id) {
      void session.subscribe(id);
    }
  },
  { immediate: true },
);

// 拿到 simulation 后异步补 project name
watch(
  () => session.simulation.value?.project_id,
  (pid) => {
    if (pid && !projectName.value) {
      void loadProjectName(pid);
    }
  },
);

// ============================================================
// Sprint 6.A2 路线图 #2(2026-05-22):角色情绪曲线
// 拿到 sim + state=done + mode=evolution 时拉 emotional_states
// quick mode 不跑 emotional_state_tracker → 返空 [] → section v-if 自动隐藏
// stale-while-revalidate:切 sim 时保留旧数据无缝替换,避免 loading 闪
// ============================================================

const emotionalStates = ref<EmotionalStateRecord[]>([]);
const emotionalStatesLoading = ref(false);

async function loadEmotionalStates(sim: SimulationFull) {
  // 只有 evolution mode + done 状态才有数据;其他状态直接清空,section 自动隐藏
  if (sim.mode !== "evolution" || sim.state !== "done") {
    emotionalStates.value = [];
    return;
  }
  const isFirstLoad = emotionalStates.value.length === 0;
  if (isFirstLoad) emotionalStatesLoading.value = true;
  try {
    emotionalStates.value = await api.get<EmotionalStateRecord[]>(
      `/simulations/${sim.id}/emotional_states`,
    );
  } catch (e) {
    if (!(e instanceof ApiError)) {
      if (import.meta.env.DEV) {
        console.warn("[SimulationDetailView] 情绪曲线拉取失败:", e);
      }
    }
    // 失败时不清空旧数据(stale-while-revalidate);失败不阻塞主流程
  } finally {
    if (isFirstLoad) emotionalStatesLoading.value = false;
  }
}

watch(
  () => session.simulation.value,
  (sim) => {
    if (sim) void loadEmotionalStates(sim);
  },
);

/** 按 character_id 分组成轨迹,稳定 UI 顺序按角色名拼音 */
const emotionTrajectories = computed(() => {
  const groups = new Map<string, {
    characterId: string;
    characterName: string;
    records: EmotionalStateRecord[];
  }>();
  for (const r of emotionalStates.value) {
    let g = groups.get(r.character_id);
    if (!g) {
      g = { characterId: r.character_id, characterName: r.character_name, records: [] };
      groups.set(r.character_id, g);
    }
    g.records.push(r);
  }
  return Array.from(groups.values()).sort((a, b) =>
    a.characterName.localeCompare(b.characterName, "zh-CN"),
  );
});

/** section 总开关:loading 中 / 有数据时都显;quick mode / 无数据时自动隐藏 */
const showEmotionSection = computed(
  () => emotionalStatesLoading.value || emotionTrajectories.value.length > 0,
);

/** 模态:选中的角色 id;null = 关闭。改 chip 列表 + 点击弹模态,避免一股脑渲染占屏 */
const selectedCharacterId = ref<string | null>(null);
const selectedTrajectory = computed(() =>
  emotionTrajectories.value.find((t) => t.characterId === selectedCharacterId.value) ?? null,
);

// ============================================================
// Sprint 6.A2 M6-fix4(2026-05-20)— outline-first 状态检测
// 用户从 outline 审核页返回 → 我的剧情线 → 点进 sim → 这里
// 若 sim.use_outline_first + outline 未批准 → 显 banner + 跳转 CTA
// ============================================================

interface OutlineQuickState {
  state: string;
  total_scenes_planned: number;
  scenes_count: number;
}
const outlineQuickState = ref<OutlineQuickState | null>(null);

async function loadOutlineStateIfApplicable(sim: SimulationFull) {
  if (!sim.use_outline_first) {
    outlineQuickState.value = null;
    return;
  }
  // 已 generating / done(outline 已批准、正在跑或已完成)→ 不显 banner,继续正常详情
  if (sim.state !== "queued") {
    outlineQuickState.value = null;
    return;
  }
  try {
    const data = await api.get<{
      state: string;
      total_scenes_planned: number;
      scenes: unknown[];
    }>(`/simulations/${sim.id}/outline`);
    outlineQuickState.value = {
      state: data.state,
      total_scenes_planned: data.total_scenes_planned,
      scenes_count: data.scenes.length,
    };
  } catch {
    outlineQuickState.value = null;
  }
}

watch(
  () => session.simulation.value,
  (sim) => {
    if (sim) {
      void loadOutlineStateIfApplicable(sim);
    }
  },
);

const shouldShowOutlineBanner = computed(() => {
  const sim = session.simulation.value;
  if (!sim) return false;
  if (!sim.use_outline_first) return false;
  // sim.state='queued' 但 outline 未到 generating/done → 用户需要批准 outline
  if (sim.state !== "queued") return false;
  if (!outlineQuickState.value) return false;
  return (
    outlineQuickState.value.state === "drafting"
    || outlineQuickState.value.state === "awaiting_user"
    || outlineQuickState.value.state === "failed"
  );
});

function goToOutline() {
  router.push(`/simulations/${simId.value}/outline`);
}

// ============================================================
// 滚雪球前情(Sprint 1.O)— 如果该 sim 接续了 N 条前文,拉那些前文 summary 展示
// ============================================================

const priorSims = ref<SimulationSummary[]>([]);

async function loadPriorSims(ctxIds: string[], projectId: string) {
  if (ctxIds.length === 0) {
    priorSims.value = [];
    return;
  }
  try {
    // 复用项目 sim 列表端点(已包含所有 done 的 sim)
    const all = await api.get<SimulationSummary[]>(
      `/projects/${projectId}/simulations`,
    );
    // 按 ctxIds 顺序保留(老在前 — 与后端拼接顺序一致)
    const byId = new Map(all.map((s) => [s.id, s]));
    const sorted = ctxIds
      .map((id) => byId.get(id))
      .filter((s): s is SimulationSummary => !!s)
      .sort((a, b) => a.created_at.localeCompare(b.created_at));
    priorSims.value = sorted;
  } catch {
    priorSims.value = [];
  }
}

watch(
  () => [
    session.simulation.value?.context_simulation_ids ?? [],
    session.simulation.value?.project_id ?? "",
  ] as const,
  ([ctxIds, pid]) => {
    if (pid && ctxIds.length > 0) {
      void loadPriorSims(ctxIds, pid);
    } else {
      priorSims.value = [];
    }
  },
  { immediate: true },
);

onBeforeUnmount(() => {
  session.reset();
});

// ============================================================
// 操作
// ============================================================

function goBack() {
  // 2026-06-05:智能返回 — 根据 query.returnTo 跳回真实来源,而不是一律跳项目页
  // 来源矩阵:
  //   ?returnTo=combo-tree&combo_id=xxx → 跳回反事实组合树进度页
  //   (其他 returnTo 值预留扩展)
  //   无 query → 老逻辑(项目作品列表 / dashboard 兜底)
  const returnTo = route.query.returnTo;
  if (returnTo === "combo-tree" && typeof route.query.combo_id === "string") {
    const pid = session.simulation.value?.project_id;
    if (pid) {
      router.push({
        name: "counterfactual-tree",
        params: { id: pid, combo_id: route.query.combo_id },
      });
      return;
    }
  }
  // 2026-06-06:从家族树跳来 → 返回家族树
  if (returnTo === "family-tree") {
    const pid = session.simulation.value?.project_id;
    if (pid) {
      router.push({
        name: "simulation-family-tree",
        params: { id: pid },
      });
      return;
    }
  }

  // Sprint 6.A2 M7.E-fix(2026-05-20):返回逻辑与 handleDelete 一致 —
  // 来自项目的 sim 应回到项目作品列表 Tab;无 project_id 兜底回 dashboard
  const pid = session.simulation.value?.project_id;
  if (pid) {
    router.push(`/projects/${pid}`);
  } else {
    router.push("/dashboard");
  }
}

/** M7.E-fix(2026-05-20)返回按钮文案 — 取决于是否能拿到 project_id */
const backLabel = computed(() => {
  return session.simulation.value?.project_id ? "返回项目" : "返回";
});

function handleRerun() {
  const pid = session.simulation.value?.project_id;
  if (pid) router.push(`/projects/${pid}`);
}

/**
 * M7.I(2026-05-20)"基于本篇续写"链路:跳回项目 + URL 带 ?continueFrom=<simId>
 * ProjectView 检测到 query 自动打开 SimulationDock + 勾上本 sim 作为前文(滚雪球)
 *
 * 与 handleRerun 区别:
 *   - handleRerun 仅跳项目页(纯重定向);用户进项目页后可自由选要不要滚雪球
 *   - continueFromThis 携带本 sim id 自动预选,语义明确"基于本篇延续"
 */
function continueFromThis() {
  const sim = session.simulation.value;
  if (!sim) return;
  router.push({
    path: `/projects/${sim.project_id}`,
    query: { continueFrom: sim.id },
  });
}

// ============================================================
// 断点续推(Sprint 1.P)
// ============================================================

const completedRounds = computed(
  () => session.simulation.value?.timeline?.rounds?.length ?? 0,
);
const remainingRounds = computed(() => {
  const sim = session.simulation.value;
  if (!sim) return 0;
  return Math.max(0, sim.rounds_planned - completedRounds.value);
});

/** sim 满足以下全部 → 可恢复:
 *  - 不是 done
 *  - 有进度(timeline 至少 1 轮)
 *  - state ∈ {failed, cancelled, directing, composing}(directing/composing 是僵尸状态时常见)
 */
const canResume = computed(() => {
  const sim = session.simulation.value;
  if (!sim) return false;
  if (sim.state === "done") return false;
  if (completedRounds.value === 0) return false;
  return ["failed", "cancelled", "directing", "composing"].includes(sim.state);
});

const resumeButtonLabel = computed(() => {
  const sim = session.simulation.value;
  if (!sim) return "继续完成";
  if (remainingRounds.value > 0) {
    return `继续完成(剩 ${remainingRounds.value} 轮)`;
  }
  // 所有轮跑完仅 composer 失败的场景
  return "继续编织最终叙事";
});

async function handleResume() {
  if (!simId.value) return;
  await session.resume(simId.value);
}

const deleting = ref(false);
async function handleDelete() {
  if (!simId.value) return;
  const ok = await confirmDialog({
    title: "删除这条推演产物?",
    message: "此操作不可恢复。",
    danger: true,
    confirmLabel: "删除",
  });
  if (!ok) return;
  // Sprint 6.A2 M7.E-fix(2026-05-20):删除后跳回项目作品列表,而非 dashboard。
  // IA 重构后 dashboard 是创作态选择页;来自项目的 sim 应回到项目作品列表 Tab。
  // 兜底:若拿不到 project_id(罕见 — sim 已加载但 project_id 丢失),回 dashboard。
  const projectId = session.simulation.value?.project_id ?? null;
  deleting.value = true;
  try {
    await api.delete(`/simulations/${simId.value}`);
    // 2026-06-01:广播 sim:deleted → 列表 panel 实时移除卡片
    if (simId.value) {
      events.emit("sim:deleted", {
        sim_id: simId.value,
        project_id: projectId,
      });
    }
    if (projectId) {
      router.push(`/projects/${projectId}`);
    } else {
      router.push("/dashboard");
    }
  } catch (e) {
    deleting.value = false;
    toast.error(
      e instanceof ApiError ? `删除失败:${e.message}` : "删除失败",
    );
  }
}

// ============================================================
// 派生
// ============================================================

const isLoading = computed(
  () =>
    session.phase.value === "idle" &&
    !session.simulation.value &&
    !session.errorMessage.value,
);

/** 前情区块只在正常状态(非加载/网络错)展示;running / failed / done 都显 */
const showPriorSection = computed(
  () => priorSims.value.length > 0 && !isLoading.value && session.phase.value !== "error",
);

/** 实时事件流(SSE 推过来的)— 倒序最新在上,最多 30 条 */
const formattedEvents = computed<FormattedEvent[]>(() => {
  const out: FormattedEvent[] = [];
  const arr = session.events.value;
  for (let i = arr.length - 1; i >= 0 && out.length < 30; i--) {
    const f = formatSimulationEvent(arr[i], i);
    if (f) out.push(f);
  }
  return out;
});

// ============================================================
// 1.R 自洽守护者 — done 屏「✦ 不满意?诊断」按钮
// ============================================================

const audit = useAudit();
const auditPanelOpen = ref(false);

// B6(2026-05-27):QualityScoreCard ref,守护者 done 后调它的 reload 让评分卡刷新
const qualityScoreCardRef = ref<{ reload: () => void } | null>(null);

// B6(2026-05-27)— 自洽审计 phase 变 done 时重拉评分卡.
// 2026-06-02 P0Y 命名规范统一:useAudit/useCanonicalGuardian 的完成状态都改为 "done".
//   历史:useAudit 之前用 "ready",B6.1 因混用踩坑;现已统一.
watch(
  () => audit.phase.value,
  (newPhase, oldPhase) => {
    if (newPhase === "done" && oldPhase !== "done") {
      qualityScoreCardRef.value?.reload();
    }
  },
);

function handleCanonicalAuditDone() {
  // B6:正典守护者 done → 让 QualityScoreCard 重拉
  qualityScoreCardRef.value?.reload();
}

/** sim 状态变 done 时,静默拉一次最新 audit(用户回来能看到上次结果)
 *  + 2026-06-01 v2:done 时探测独立合并最终作品(若有 → 详情页加跳转 banner)
 *  + 2026-06-01:广播 sim 状态变化事件 → 列表 panel 实时刷新 */
watch(
  () => session.simulation.value?.state,
  (state, oldState) => {
    if (state === "done" && simId.value) {
      void audit.loadLatest(simId.value);
      void probeFinalWork();
    }
    // 广播状态变化事件(用户在详情页期间 SSE 推动状态,列表 panel 监听后 reload)
    if (state && state !== oldState && simId.value) {
      const projectId = session.simulation.value?.project_id ?? null;
      const payload = { sim_id: simId.value, project_id: projectId };
      if (state === "done") events.emit("sim:done", payload);
      else if (state === "failed") events.emit("sim:failed", payload);
      else if (state === "cancelled") events.emit("sim:cancelled", payload);
    }
  },
  { immediate: true },
);

/** 切到不同 sim 时清 audit 状态 */
watch(simId, () => {
  audit.reset();
  auditPanelOpen.value = false;
  hasLinkedFinalWork.value = null;
});

async function startAudit() {
  if (!simId.value) return;
  auditPanelOpen.value = true;
  await audit.start(simId.value);
}

function toggleAuditPanel() {
  auditPanelOpen.value = !auditPanelOpen.value;
}

/**
 * Sprint 6.A2 polish(2026-05-22):× 关闭按钮行为分阶段
 * - loading / error 阶段 × → 关 panel + audit.reset() 把 phase 回 idle → 入口按钮重显,用户可重新启动
 *   (旧 bug:只关 panel 不 reset,phase 卡 loading → 入口 v-if 条件不命中 → section 空白)
 * - ready 阶段 × → 只关 panel,保留 audit 结果 → section 显折叠态"上次诊断 X 分"
 */
function closeAuditPanel() {
  auditPanelOpen.value = false;
  if (audit.phase.value === "loading" || audit.phase.value === "error") {
    audit.reset();
  }
}

/** Sprint 6.A2(2026-05-22):进入沉浸式阅读器 — 替代原"关闭"按钮的语义 */
function enterReader() {
  if (!simId.value) return;
  void router.push(`/simulations/${simId.value}/read`);
}

/** 2026-06-01 v2:本 sim 是否为独立合并最终作品(自身就是合并产物)*/
const isFinalCompilation = computed(() => {
  const s = session.simulation.value;
  return !!(s && s.is_final_compilation);
});

/** 2026-06-01 v2:本 sim 是否有"已生成的独立合并最终作品 sim"(自己是 source)
 *  实现:轮询 /final_work 端点判断,或直接走 final_compiled_narrative 字段兼容老数据 */
const hasLinkedFinalWork = ref<{ compilation_sim_id: string | null } | null>(null);
async function probeFinalWork() {
  if (!simId.value) return;
  hasLinkedFinalWork.value = null;
  try {
    const data = await api.get<{ compilation_sim_id: string | null }>(
      `/simulations/${simId.value}/final_work`,
    );
    hasLinkedFinalWork.value = data;
  } catch {
    // 404 → 无最终作品,正常
  }
}

/** 跳到独立合并 sim 详情页(或源 sim,若是从独立 sim 反看)*/
function goToLinkedSim(targetSimId: string) {
  void router.push(`/simulations/${targetSimId}`);
}

const KIND_LABEL: Record<AuditIssueKind, string> = {
  character_thin: "角色单薄",
  event_inconsistent: "事件脱节",
  relationship_off: "关系失真",
  dialogue_flat: "对白扁平",
  turn_jarring: "转折突兀",
  pacing_off: "节奏失衡",
  opening_weak: "开篇乏力",
  whitespace_imbalance: "留白失调",
};

function isUserFixable(kind: AuditIssueKind): boolean {
  return USER_FIXABLE_AUDIT_KINDS.has(kind);
}

/** 跳到对应编辑面 — 当前简化版直接跳项目主页(用户在卡片视图找)。
 *  未来可加 query 参数让 ProjectView 高亮目标项。
 */
function gotoFix(_issue: AuditIssue) {
  const pid = session.simulation.value?.project_id;
  if (!pid) return;
  router.push(`/projects/${pid}`);
}

/** M7.C / M7.K(2026-05-20)采纳按钮 → 两类路径:
 *    A. 用户可修类(character/event/relationship)→ 直接改 DB + 显字段更新 toast
 *    B. LLM-only 类(dialogue_flat/turn_jarring/...)→ 返回 sim_config_patch →
 *       跳回项目页携带 ?applySimPatch=<base64-json> 自动预填 dock 配置
 *
 *  错误处理:
 *    - ISSUE_NO_PAYLOAD → toast info(LLM 没给结构化建议)
 *    - 其它已采纳 / 不可修等错误 → toast warning / error
 */
async function acceptFix(issueIdx: number) {
  const result = await audit.acceptIssue(issueIdx);
  if (!result.ok || !result.response) {
    const code = result.errorCode ?? "";
    const msg = result.errorMessage ?? "采纳失败";
    // B7(2026-05-27):AI 输出 payload 格式问题统一提示用户走"去修"路径,
    // 不再把后端术语("缺失主体 ID(subject_id)" 等)直接抛给用户。
    const AI_PAYLOAD_BAD_CODES = new Set([
      "ISSUE_NO_PAYLOAD",
      "ISSUE_NO_SUBJECT",
      "ISSUE_NO_OPERATIONS",
      "ISSUE_NO_PATCHES",
      "ISSUE_INVALID_TARGET",
      "ISSUE_KIND_UNKNOWN",
      "ISSUE_ALL_OPS_SKIPPED",
    ]);
    if (AI_PAYLOAD_BAD_CODES.has(code)) {
      toast.info("AI 没给出可直接采纳的结构化修改 — 请按建议手动改,或点「按建议重生成」让 AI 整体重写");
    } else if (code === "ISSUE_ALREADY_ACCEPTED") {
      toast.warning(msg);
    } else {
      toast.error(msg);
    }
    return;
  }

  // M7.K(2026-05-20)— LLM-only 类:target=sim_config 的 issue
  // Sprint 6.A2 路线图 #2.5(2026-05-22):patch 已由后端持久化到 audit.issues_json 的 applied_at 字段;
  // 前端不再写 sessionStorage / URL query。用户回项目点「AI 续写」时,ProjectView mount 调
  // GET /api/projects/:id/pending_audit_patches 拉所有未 applied 的 patch → 透传 dock 预填。
  // 好处:跨会话 / 关浏览器再开 / 切设备(同账户)— patch 都不丢
  const resp = result.response;
  if (resp.target === "sim_config" && resp.sim_config_patch) {
    toast.success("已采纳 — 下次回项目点「AI 续写」时会自动预填配置");
    return;
  }

  // 原 M7.C 路径:character / event / relationship — 字段已更新 DB
  const skipCount = resp.operations_skipped.length;
  let msg = `已采纳:${resp.subject_name}「`;
  msg += resp.operations_applied.map((o) => o.field).join(" / ");
  msg += "」字段已更新";
  if (skipCount > 0) {
    msg += `(${skipCount} 项被跳过)`;
  }
  toast.success(msg);
}

/** 该 issue 是否可"采纳" — M7.K 后 8 类全部允许(只要 LLM 给了 payload)*/
function canAccept(issue: AuditIssue): boolean {
  return !!issue.actionable_fix_payload && !issue.accepted_at;
}

/** 该 issue 是否已被采纳(显示已采纳 chip) */
function isAccepted(issue: AuditIssue): boolean {
  return !!issue.accepted_at;
}

/** done 状态下走"重新生成"路径:跳到项目页让用户在 SimulationDock 重新填 + 提交 */
function regenerate() {
  handleRerun();
}

const scoreTone = computed<"high" | "mid" | "low">(() => {
  const s = audit.audit.value?.overall_score ?? 0;
  if (s >= 80) return "high";
  if (s >= 60) return "mid";
  return "low";
});

/** 创建时间格式化:YYYY-MM-DD HH:mm */
function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${y}-${m}-${day} ${hh}:${mm}`;
}
</script>

<template>
  <div class="detail-page">
    <!-- 顶栏 -->
    <header class="topbar">
      <button class="back-link" @click="goBack">
        <span aria-hidden>←</span> {{ backLabel }}
      </button>

      <div class="topbar-meta">
        <span v-if="projectName" class="proj-name">《{{ projectName }}》</span>
        <span v-if="session.simulation.value" class="created-at">
          {{ formatTime(session.simulation.value.created_at) }}
        </span>
      </div>

      <div class="topbar-actions">
        <!-- M7.E-fix(2026-05-20)删除"再推演 →"按钮 — 与左上角「返回项目」+
             项目页「AI 续写」入口完全冗余,且按钮名(再推演)实际只是跳转,名实不符。
             保留 audit 卡片内的「按建议重生成」+ failed 兜底的「重新推演(新建)」—
             那两个是语义清晰的差异化入口。 -->
        <button
          class="ghost-btn"
          :disabled="!session.simulation.value || deleting"
          @click="handleDelete"
        >{{ deleting ? "删除中…" : "删除" }}</button>
      </div>
    </header>

    <!-- 主体 — 状态分屏 -->
    <main class="detail-main">
      <!-- Sprint 3.A 末尾态:原作末段预览(让用户看见"AI 接的是哪段原文")
           折叠默认收起,点击展开 — 避免长文挤压主屏
           Sprint 6.A2 M3.D-fix5(2026-05-19)兜底:有滚雪球前情时不再显原作末段 banner
           (用户实测两个 banner 同时显时不知该接哪段;数据层 fix5 已让 tail=None,
           此条件防御旧 sim) -->
      <section
        v-if="session.simulation.value?.original_tail_excerpt && !showPriorSection"
        class="tail-block"
      >
        <button
          type="button"
          class="tail-toggle"
          @click="tailExpanded = !tailExpanded"
          :aria-expanded="tailExpanded"
        >
          {{ tailExpanded ? '▾' : '▸' }}
          📜 原作末段({{ session.simulation.value.original_tail_excerpt.length }} 字 · AI 从此段之后接续)
        </button>
        <pre v-if="tailExpanded" class="tail-content">{{ session.simulation.value.original_tail_excerpt }}</pre>
      </section>

      <!-- 2026-06-02 hotfix:老版"接续自 N 段剧情"banner 删除 — 跟新 amber
           "本作品由系统自动合并 X 篇前作"banner 视觉重复,用户拍板留新版.
           priorSims/showPriorSection 数据保留(原作末段守门用 / 别处可能引用) -->
      <!-- (老版 prior-block 已删 — 2026-06-02) -->

      <!-- Sprint D.6 loading 骨架屏 — 模拟产物详情结构(锚点 + 元数据 + 叙事正文) -->
      <div v-if="isLoading" class="loading-skeleton" aria-busy="true" aria-live="polite">
        <div class="skeleton-meta-row">
          <SkeletonBlock height="14px" width="50%" />
          <SkeletonBlock height="28px" width="80px" rounded="md" />
        </div>
        <SkeletonBlock height="32px" width="180px" />
        <div class="skeleton-paragraph-stack">
          <SkeletonBlock height="14px" width="98%" />
          <SkeletonBlock height="14px" width="95%" />
          <SkeletonBlock height="14px" width="88%" />
          <SkeletonBlock height="14px" width="40%" />
          <div style="height: 12px"></div>
          <SkeletonBlock height="14px" width="92%" />
          <SkeletonBlock height="14px" width="96%" />
          <SkeletonBlock height="14px" width="60%" />
        </div>
      </div>

      <!-- 网络错误 -->
      <div
        v-else-if="
          session.phase.value === 'error' ||
          (session.phase.value === 'idle' && session.errorMessage.value)
        "
        class="state-msg state-error"
      >
        <p class="error-title">无法加载</p>
        <p class="error-msg">{{ session.errorMessage.value }}</p>
        <button class="ghost-btn" @click="goBack">返回</button>
      </div>

      <!-- running / queued / directing / composing -->
      <div
        v-else-if="
          session.phase.value === 'running' ||
          (session.simulation.value &&
            ['queued', 'directing', 'composing'].includes(
              session.simulation.value.state,
            ))
        "
        class="running-block"
      >
        <!-- M6-fix4(2026-05-20):outline 未批准检测 — sim 卡在 queued 等用户回去批准 -->
        <div v-if="shouldShowOutlineBanner" class="outline-pending-banner surface">
          <div class="outline-pending-content">
            <span class="outline-pending-icon">📐</span>
            <div class="outline-pending-text">
              <p class="outline-pending-title">
                outline 等待你审核 / 编辑
              </p>
              <p class="outline-pending-desc">
                这是一篇 outline-first 长篇,创建后 LLM 已生成 outline 草稿
                {{ outlineQuickState?.scenes_count ?? '?' }}/{{ outlineQuickState?.total_scenes_planned ?? '?' }} 幕,
                状态:<strong>{{
                  outlineQuickState?.state === 'drafting' ? '正在生成中(请稍候)'
                    : outlineQuickState?.state === 'awaiting_user' ? '等你审核 / 编辑 / 批准'
                    : outlineQuickState?.state === 'failed' ? '生成失败,可重新生成'
                    : '未知'
                }}</strong>。
                <strong>批准后 sim 才会开始跑;现在的"排队中"并非真正排队。</strong>
              </p>
            </div>
          </div>
          <button class="primary-btn" @click="goToOutline" type="button">
            前往 outline 审核 →
          </button>
        </div>

        <div class="progress-wrap">
          <div class="progress-track">
            <div
              class="progress-fill"
              :style="{ width: `${session.progress.value * 100}%` }"
            ></div>
          </div>
          <p class="progress-text">
            {{ Math.round(session.progress.value * 100) }}% ·
            第 {{ session.simulation.value?.current_round ?? 0 }} /
            {{ session.simulation.value?.rounds_planned ?? 0 }}
            {{ session.simulation.value?.mode === 'evolution' ? '幕' : '轮' }}
            <span v-if="session.elapsedLabel.value" class="elapsed-pill mono">
              · 已运行 {{ session.elapsedLabel.value }}
            </span>
          </p>
        </div>

        <p class="stage-label">{{ session.stageLabel.value }}</p>
        <!-- M6-fix5:细化阶段(让用户知道幕内 AI 在做什么,而非进度条卡死) -->
        <p
          v-if="session.detailedStageLabel.value"
          class="detailed-stage-label"
        >
          {{ session.detailedStageLabel.value }}
        </p>

        <div class="cost-row">
          <span class="cost-label">已消耗</span>
          <span class="cost-value mono">
            ¥{{ (session.simulation.value?.cost_yuan ?? 0).toFixed(4) }}
          </span>
        </div>

        <!-- 实时事件流(Sprint 1.L)-->
        <div class="event-stream">
          <p class="event-stream-title">实时进度</p>
          <ul v-if="formattedEvents.length" class="event-list">
            <li
              v-for="ev in formattedEvents"
              :key="ev.key"
              class="event-row"
              :class="[`tone-${ev.tone}`, `indent-${ev.indent}`]"
            >
              <span class="event-icon mono">{{ ev.icon }}</span>
              <span class="event-main">{{ ev.main }}</span>
              <span v-if="ev.sub" class="event-sub mono">{{ ev.sub }}</span>
            </li>
          </ul>
          <p v-else class="event-empty">等待 AI 启动…</p>
        </div>

        <!-- 长篇模式时间提示(原 long-form-hint 移到卡片底部,2026-05-24)-->
        <p
          v-if="
            session.simulation.value?.mode === 'evolution'
            && session.simulation.value?.use_outline_first
          "
          class="long-form-hint"
        >
          长篇模式预计 30-90 分钟,可关闭页面后再回来查看
        </p>

        <!-- Sprint 6.A2 路线图 #5(2026-05-23):边写边干预输入框
             仅 evolution 模式 + 非终态显示 — quick 模式 30-60s 用户来不及干预
             outline-first 模式当前也不接(LLM 走 outline,不调 scene_picker / narrator 的 hint 路径)-->
        <SceneHintInput
          v-if="
            session.simulation.value
            && session.simulation.value.mode === 'evolution'
            && !session.simulation.value.use_outline_first
            && ['queued', 'directing', 'composing'].includes(session.simulation.value.state)
          "
          :sim-id="session.simulation.value.id"
        />
      </div>

      <!-- failed -->
      <div
        v-else-if="
          session.phase.value === 'failed' ||
          session.simulation.value?.state === 'failed'
        "
        class="failed-block"
      >
        <p class="anchor-divergence">
          锚点:"{{ session.simulation.value?.divergence }}"
        </p>
        <p class="error-title">{{ modeActionLabel }}失败</p>
        <p class="error-msg">
          {{ session.simulation.value?.error_message ?? session.errorMessage.value ?? '推演中断,请稍后重试' }}
        </p>

        <!-- 进度提示:让用户知道损失了什么 / 没损失什么 -->
        <p v-if="session.simulation.value" class="progress-meta">
          已完成 <span class="mono">{{ completedRounds }} / {{ session.simulation.value.rounds_planned }}</span> 轮
          <span v-if="canResume" class="meta-hint">· 可继续</span>
        </p>

        <div class="failed-actions">
          <button
            v-if="canResume"
            class="primary-btn"
            @click="handleResume"
            :disabled="session.phase.value === 'creating'"
          >
            {{
              session.phase.value === 'creating'
                ? '正在恢复…'
                : `✦ ${resumeButtonLabel} →`
            }}
          </button>
          <button class="ghost-btn" @click="handleRerun">重新推演(新建)</button>
        </div>
      </div>

      <!-- done — 接 NarrativeStream(autoStream=false 立即全显,因为这是回看场景) -->
      <div
        v-else-if="session.phase.value === 'done' && session.simulation.value"
        class="done-block"
      >
        <!-- 2026-06-01 v2:本 sim 是独立合并最终作品 → banner 提示来源 + 跳源 sim -->
        <div
          v-if="isFinalCompilation"
          class="final-compilation-banner"
          @click="session.simulation.value?.compiled_from_sim_id && goToLinkedSim(session.simulation.value.compiled_from_sim_id)"
          role="button"
          tabindex="0"
          @keydown.enter="session.simulation.value?.compiled_from_sim_id && goToLinkedSim(session.simulation.value.compiled_from_sim_id)"
          title="点击查看触发本最终作品的源推演"
        >
          <Icon name="book_open" :size="16" />
          <span class="final-banner-text">
            本作品由系统自动合并 <strong>{{ (session.simulation.value?.context_simulation_ids?.length ?? 1) }} 篇</strong>
            前作而成(走向终章 + 滚雪球继承)
          </span>
          <span class="final-banner-link">查看源推演 →</span>
        </div>
        <!-- Sprint 6.A2 polish(2026-05-22):删冗余锚点 + 4 元素重布局 + 样式统一
             左:♾️ 永久保留 chip(信息);右:3 个动作按钮(2 secondary + 1 primary)-->
        <div class="done-meta-row">
          <span
            class="forever-badge"
            title="平台对你的承诺:此作品无限期保留,即使取消订阅、降回免费档也不删除"
          >♾️ 永久保留</span>
          <div class="done-actions">
            <button
              type="button"
              class="action-btn action-btn--secondary"
              title="导出 markdown — 原版 / 去 IP 版"
              @click="exportDialogOpen = true"
            >↓ 导出</button>
            <button
              type="button"
              class="action-btn action-btn--secondary"
              title="下载创作徽章,贴到作品发布页标记 AI 协作"
              @click="badgeDialogOpen = true"
            >✨ 创作徽章</button>
            <!-- 2026-06-01 v2:本 sim 有独立合并最终作品 → 跳转过去(独立 sim 卡片)
                 2026-06-02 hotfix:本 sim 自己就是合并产物时不显此按钮(语义循环) -->
            <button
              v-if="!isFinalCompilation && hasLinkedFinalWork && hasLinkedFinalWork.compilation_sim_id"
              type="button"
              class="action-btn action-btn--final"
              title="本推演是走向终章 + 滚雪球,系统已自动合并所有前作 → 进入最终作品独立卡片"
              @click="goToLinkedSim(hasLinkedFinalWork.compilation_sim_id!)"
            >📖 跳到最终作品 →</button>
            <!-- 2026-06-02 hotfix:合并产物是"终点"不能再被续写,隐藏此按钮 -->
            <button
              v-if="!isFinalCompilation"
              type="button"
              class="action-btn action-btn--primary"
              title="跳回项目并自动打开续写面板,本篇预选为前文(滚雪球续写)"
              @click="continueFromThis"
            >📖 基于本篇续写 →</button>
          </div>
        </div>
        <NarrativeStream
          :narrative="session.simulation.value.narrative ?? ''"
          :project-name="projectName || '此项目'"
          :reshape-percent="session.simulation.value.reshape_percent"
          :rounds-planned="session.simulation.value.rounds_planned"
          :cost-yuan="session.simulation.value.cost_yuan"
          :target-chars="session.simulation.value.target_chars"
          :auto-stream="false"
          :show-read-button="true"
          @close="goBack"
          @enter-reader="enterReader"
        />

        <!-- Sprint 6.A2 路线图 #2 v2(2026-05-22):角色情绪轨迹 — chip 列表 + 点击弹模态
             v1 默认全列出占满屏;改为 chip 紧凑列表,点击对应角色才弹居中卡片(背景虚化)-->
        <section v-if="showEmotionSection" class="emotion-section">
          <header class="emotion-section-header">
            <h3 class="emotion-section-title">角色情绪轨迹</h3>
            <span class="emotion-section-hint">点角色查看 8 维情绪曲线 · Plutchik 简化模型</span>
          </header>

          <div v-if="emotionalStatesLoading" class="emotion-loading" aria-busy="true" aria-live="polite">
            <SkeletonBlock height="32px" width="96px" rounded="full" />
            <SkeletonBlock height="32px" width="96px" rounded="full" />
            <SkeletonBlock height="32px" width="96px" rounded="full" />
          </div>

          <ul v-else class="emotion-chip-list">
            <li v-for="t in emotionTrajectories" :key="t.characterId">
              <button
                type="button"
                class="emotion-chip"
                @click="selectedCharacterId = t.characterId"
              >
                <span class="emotion-chip-name">{{ t.characterName }}</span>
                <span class="emotion-chip-meta mono">{{ t.records.length }} 幕</span>
              </button>
            </li>
          </ul>
        </section>

        <!-- 居中模态 — 复用全局 modal-fade transition + backdrop-filter blur -->
        <CharacterEmotionModal
          :open="selectedTrajectory !== null"
          :character-name="selectedTrajectory?.characterName ?? ''"
          :records="selectedTrajectory?.records ?? []"
          @close="selectedCharacterId = null"
        />

        <!-- SP-4.1(2026-06-02 hotfix):角色状态时间线 — 紧跟情绪轨迹下面,chip + 模态 -->
        <StateTimelinePanel v-if="simId" :simulation-id="simId" />

        <!-- Sprint 6.A2 路线图 #7(2026-05-23):质量评分卡 — 独立组件,顶层总览
             与自洽 / 正典两个守护者平级,而非被任一面板包裹 -->
        <QualityScoreCard
          v-if="simId"
          ref="qualityScoreCardRef"
          :simulation-id="simId"
          :is-applicable="canonicalApplicable"
        />

        <!-- 1.R 自洽守护者:done 屏底部诊断入口 + 折叠面板 -->
        <section class="audit-section">
          <button
            v-if="!auditPanelOpen && audit.phase.value === 'idle'"
            type="button"
            class="audit-trigger"
            @click="startAudit"
          >
            <span class="trigger-spark">✦</span>
            不满意?让自洽守护者诊断
            <span class="trigger-hint">免费 · 看为什么这次不够好 + 怎么改</span>
          </button>

          <!-- 已有诊断结果(loadLatest 拉到的)— 折叠态 -->
          <button
            v-else-if="!auditPanelOpen && audit.phase.value === 'done'"
            type="button"
            class="audit-trigger audit-trigger--has-result"
            @click="toggleAuditPanel"
          >
            <span class="trigger-spark">✦</span>
            上次诊断:{{ audit.audit.value?.overall_score }} 分 ·
            {{ audit.audit.value?.issues.length ?? 0 }} 条问题 — 展开查看
          </button>

          <!-- 展开态:loading / ready / error -->
          <div v-if="auditPanelOpen" class="audit-panel surface">
            <header class="audit-header">
              <h3 class="audit-title">✦ 自洽守护者诊断</h3>
              <button
                class="audit-close"
                type="button"
                aria-label="收起"
                @click="closeAuditPanel"
              >×</button>
            </header>

            <!-- loading -->
            <div v-if="audit.phase.value === 'loading'" class="audit-loading">
              <div class="audit-spinner" aria-hidden="true">
                <span></span><span></span><span></span>
              </div>
              <p>守护者正在审视产物 vs 你的设定…(5-15 秒)</p>
            </div>

            <!-- error -->
            <div v-else-if="audit.phase.value === 'error'" class="audit-error">
              <p>{{ audit.errorMessage.value }}</p>
              <button class="ghost-btn" @click="startAudit">重试</button>
            </div>

            <!-- ready -->
            <div v-else-if="audit.phase.value === 'done' && audit.audit.value" class="audit-ready">
              <div class="audit-score-row">
                <div class="score-block" :class="`score-${scoreTone}`">
                  <span class="score-num mono">{{ audit.audit.value.overall_score }}</span>
                  <span class="score-max">/ 100</span>
                </div>
                <p class="audit-rec">{{ audit.audit.value.regenerate_recommendation }}</p>
              </div>

              <ul v-if="audit.audit.value.issues.length > 0" class="issue-list">
                <li
                  v-for="(issue, idx) in audit.audit.value.issues"
                  :key="idx"
                  class="issue-card"
                  :class="{
                    'issue-card--fixable': isUserFixable(issue.kind),
                    'issue-card--accepted': isAccepted(issue),
                  }"
                >
                  <header class="issue-head">
                    <span
                      class="issue-kind"
                      :class="{ 'issue-kind--fixable': isUserFixable(issue.kind) }"
                    >{{ KIND_LABEL[issue.kind] }}</span>
                    <span class="issue-subject">{{ issue.subject_name }}</span>
                    <!-- M7.C 已采纳 chip -->
                    <span v-if="isAccepted(issue)" class="issue-accepted-chip">
                      ✓ 已采纳
                    </span>
                  </header>
                  <p class="issue-evidence">
                    <span class="evidence-label">产物:</span>
                    <span class="evidence-quote">「{{ issue.evidence_in_narrative }}」</span>
                  </p>
                  <p class="issue-cause">
                    <span class="cause-label">根因:</span>{{ issue.root_cause_in_setup }}
                  </p>
                  <p class="issue-fix">
                    <span class="fix-label">建议:</span>{{ issue.actionable_fix }}
                  </p>

                  <!-- M7.C / M7.K(2026-05-20):两组按钮:
                       - 用户可修类 → [采纳 / 去修]
                       - LLM-only 类 → [采纳建议入续写配置(若有 payload)] -->
                  <div v-if="!isAccepted(issue)" class="issue-actions">
                    <button
                      v-if="canAccept(issue)"
                      type="button"
                      class="issue-accept-btn"
                      :disabled="audit.isAccepting(idx)"
                      @click="acceptFix(idx)"
                      :title="isUserFixable(issue.kind)
                        ? '直接把建议应用到对应字段,无需手动编辑'
                        : '采纳建议 — 跳到续写设置,本篇为前文 + 配置自动预填'"
                    >
                      <span v-if="audit.isAccepting(idx)">采纳中…</span>
                      <span v-else-if="isUserFixable(issue.kind)">✓ 采纳</span>
                      <span v-else>✓ 采纳建议入续写配置</span>
                    </button>
                    <button
                      v-if="isUserFixable(issue.kind)"
                      type="button"
                      class="issue-fix-btn"
                      @click="gotoFix(issue)"
                      title="跳转到项目页手动编辑"
                    >→ 去修</button>
                  </div>
                </li>
              </ul>
              <p v-else class="audit-empty">
                ✓ 守护者没找出明显问题 — 你这次的产物质量不错。
              </p>

              <div class="audit-actions">
                <button
                  type="button"
                  class="ghost-btn"
                  @click="startAudit"
                >再诊断一次</button>
                <button
                  type="button"
                  class="primary-btn"
                  @click="regenerate"
                >用建议续写一篇 →</button>
              </div>

              <p class="audit-cost mono">
                诊断成本:¥{{ audit.audit.value.cost_yuan.toFixed(4) }} · 不扣推演配额
              </p>
            </div>
          </div>
        </section>

        <!-- 2.D 正典守护者 — 与自洽守护者平行;initial 态自动隐藏 -->
        <section v-if="session.phase.value === 'done' && simId" class="canonical-section">
          <CanonicalGuardianPanel
            :simulation-id="simId"
            :is-applicable="canonicalApplicable"
            @audit-done="handleCanonicalAuditDone"
          />
        </section>

        <!-- SP-5.1(2026-05-28):伏笔账本面板 — 后端 plot_threads 暴露
             无论 phase 都显示(伏笔在每幕产出后就有数据,不必等 done)
             F2.1(2026-06-02):快速模式不渲染 — quick mode 不跑 plot_tracker,无数据 -->
        <section
          v-if="simId && session.simulation.value?.mode === 'evolution'"
          class="plot-threads-section"
        >
          <PlotThreadsPanel
            :simulation-id="simId"
            :current-scene="session.simulation.value?.current_round ?? undefined"
          />
        </section>

        <!-- SP-4.1(2026-06-02 hotfix):StateTimelinePanel 已挪到情绪轨迹下面 — 此处删 -->

        <!-- SP-6.1(2026-05-28):张力曲线 — TensionCurvePanel 组件已就绪
             SP-6.2 集成留尾:OutlineReviewView 更合适(那里展示 outline_scenes 全集),
             SimulationDetailView 需 SP-6.2 fetch outline 后再接入 -->
      </div>
    </main>

    <!-- 2.E 导出对话框 — done 屏才需要 -->
    <ExportSimulationDialog
      v-if="simId && session.simulation.value"
      :open="exportDialogOpen"
      :simulation-id="simId"
      :project-id="session.simulation.value.project_id"
      @close="exportDialogOpen = false"
    />

    <!-- Sprint D.3 创作徽章 dialog -->
    <BadgeDownloadDialog
      :open="badgeDialogOpen"
      :project-name="projectName"
      @close="badgeDialogOpen = false"
    />
  </div>
</template>

<style scoped>
.detail-page {
  min-height: 100vh;
  background: var(--color-bg);
  display: flex;
  flex-direction: column;
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

.back-link {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  padding: var(--space-1) var(--space-2);
  border-radius: var(--radius-sm);
  transition: color var(--duration-fast) var(--ease-out);
}
.back-link:hover {
  color: var(--color-accent-text);
}

.topbar-meta {
  flex: 1;
  display: flex;
  align-items: baseline;
  justify-content: center;
  gap: var(--space-2);
  min-width: 0;
}
.proj-name {
  font-size: var(--text-base);
  font-weight: 500;
  color: var(--color-text);
}
.created-at {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-family: var(--font-mono);
}

.topbar-actions {
  display: flex;
  gap: var(--space-2);
}

.ghost-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition: all var(--duration-fast) var(--ease-out);
}
.ghost-btn:hover:not(:disabled) {
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-color: var(--color-danger-soft);
}
.ghost-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.primary-btn {
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
}
.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}
.primary-btn:disabled {
  background: var(--color-text-subtle);
  cursor: not-allowed;
}

/* ===== 主体 ===== */
.detail-main {
  flex: 1;
  max-width: 720px;
  margin: 0 auto;
  width: 100%;
  padding: var(--space-6) var(--space-6) var(--space-12);
}

.state-msg {
  text-align: center;
  padding: var(--space-12);
  color: var(--color-text-muted);
}

/* Sprint D.6 详情页骨架(模拟 done 屏结构) */
.loading-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-4) 0;
}
.skeleton-meta-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.skeleton-paragraph-stack {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: var(--space-3);
}
.state-error {
  color: var(--color-danger);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
}

.error-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-danger);
}
.error-msg {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  max-width: 480px;
  word-break: break-word;
}

.anchor-divergence {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  font-style: italic;
  margin-bottom: var(--space-5);
  padding: var(--space-3) var(--space-4);
  background: var(--color-accent-soft);
  border-radius: var(--radius-md);
}

/* ===== Sprint 3.A 末尾态原作末段折叠预览 ===== */
.tail-block {
  margin-bottom: var(--space-4);
  padding: var(--space-3) var(--space-4);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
}
.tail-toggle {
  width: 100%;
  text-align: left;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 4px 0;
  display: flex;
  align-items: center;
  gap: 6px;
}
.tail-toggle:hover {
  color: var(--color-accent-text);
}
.tail-content {
  margin: var(--space-3) 0 0;
  padding: var(--space-3);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  line-height: 1.75;
  color: var(--color-text);
  max-height: 280px;
  overflow-y: auto;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
}

/* ===== 滚雪球前情(Sprint 1.O)===== */
.prior-block {
  margin-bottom: var(--space-5);
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
}
.prior-title {
  font-size: var(--text-sm);
  color: var(--color-accent-text);
  margin-bottom: var(--space-3);
  font-weight: 500;
}
.prior-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.prior-item {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-sm);
  cursor: pointer;
  outline: none;
  transition: background var(--duration-fast) var(--ease-out);
}
.prior-item:hover,
.prior-item:focus {
  background: var(--color-accent-soft);
}
.prior-index {
  flex-shrink: 0;
  color: var(--color-text-subtle);
  font-size: var(--text-xs);
}
.prior-div {
  flex: 1;
  color: var(--color-text);
  font-size: var(--text-sm);
  font-style: italic;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.prior-jump {
  flex-shrink: 0;
  color: var(--color-accent);
  font-size: var(--text-sm);
}

/* running */
.running-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-6);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

/* M6-fix4:outline 未批准 banner */
.outline-pending-banner {
  padding: var(--space-4) var(--space-5);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-lg);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
}
.outline-pending-content {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  flex: 1;
}
.outline-pending-icon {
  font-size: 24px;
  flex-shrink: 0;
}
.outline-pending-text {
  flex: 1;
}
.outline-pending-title {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--color-accent-text);
  margin: 0 0 4px;
}
.outline-pending-desc {
  font-size: var(--text-sm);
  color: var(--color-text);
  line-height: 1.6;
  margin: 0;
}
.outline-pending-banner .primary-btn {
  flex-shrink: 0;
  white-space: nowrap;
}

.progress-wrap {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.progress-track {
  width: 100%;
  height: 8px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-full);
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: var(--color-accent);
  border-radius: var(--radius-full);
  transition: width 0.4s var(--ease-out);
}
.progress-text {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  text-align: center;
}

.stage-label {
  text-align: center;
  font-size: var(--text-base);
  color: var(--color-accent-text);
}

/* M6-fix5:已运行时间 chip */
.elapsed-pill {
  display: inline-block;
  margin-left: 4px;
  color: var(--color-text-subtle);
  font-size: var(--text-xs);
}

/* M6-fix5:细化阶段(幕内 LLM 在做什么) */
.detailed-stage-label {
  text-align: center;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin-top: -8px;
  font-style: italic;
}

/* M6-fix5:长篇模式提示 */
/* 长篇模式提示 — Sprint 6.A2 polish(2026-05-22):去独立框,改极简脚注 */
.long-form-hint {
  text-align: center;
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  line-height: 1.5;
  margin-top: var(--space-1);
}

.cost-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: var(--space-3) var(--space-4);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
}
.cost-label {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}
.cost-value {
  font-size: var(--text-base);
  color: var(--color-text);
  font-weight: 500;
}

.hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  text-align: center;
}

/* ===== 实时事件流(Sprint 1.L)===== */
.event-stream {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  max-height: 320px;
  overflow-y: auto;
}
.event-stream-title {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  letter-spacing: 0.04em;
}
.event-empty {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  text-align: center;
  padding: var(--space-2) 0;
}
.event-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  list-style: none;
}
.event-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text);
  line-height: 1.6;
  word-break: break-word;
}
.event-row.indent-1 {
  padding-left: var(--space-5);
}
.event-icon {
  flex-shrink: 0;
  width: 12px;
  text-align: center;
  color: var(--color-text-subtle);
}
.event-row.tone-success .event-icon {
  color: var(--color-accent);
}
.event-row.tone-error .event-icon {
  color: var(--color-danger);
}
.event-main {
  flex-shrink: 0;
}
.event-row.tone-error .event-main {
  color: var(--color-danger);
}
.event-sub {
  color: var(--color-text-subtle);
  flex: 1;
  min-width: 0;
}

/* failed */
.failed-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  align-items: center;
  padding: var(--space-8);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  text-align: center;
}
.progress-meta {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}
.meta-hint {
  color: var(--color-accent-text);
  font-weight: 500;
}
.failed-actions {
  display: flex;
  gap: var(--space-3);
  flex-wrap: wrap;
  justify-content: center;
}

/* done — NarrativeStream 会自带 footer 操作,这里只放外层布局 */
.done-block {
  display: flex;
  flex-direction: column;
}

/* 2026-06-01 v2:独立合并最终作品 — 来源 banner(可点跳源 sim)*/
.final-compilation-banner {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-4);
  background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);
  border: 1px solid #fbbf24;
  border-left: 3px solid #f59e0b;
  border-radius: var(--radius-md);
  color: #b45309;
  font-size: var(--text-sm);
  transition: background 120ms ease;
  outline: none;
  /* 鼠标不变手掌 — 用色差引导 */
}
.final-compilation-banner:hover,
.final-compilation-banner:focus-visible {
  background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
}
.final-banner-text {
  flex: 1;
  color: #b45309;
}
.final-banner-text strong {
  color: #92400e;
  font-weight: 600;
}
.final-banner-link {
  font-size: var(--text-xs);
  color: #92400e;
  font-weight: 500;
  white-space: nowrap;
}

/* done 屏顶部 meta 行 — Sprint 6.A2 polish(2026-05-22)
 * 左:♾️ 永久保留 chip;右:3 个动作按钮统一 .action-btn 风格 */
.done-meta-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-5);
  flex-wrap: wrap;
}
.done-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.action-btn {
  padding: 6px var(--space-3);
  font-size: var(--text-sm);
  font-weight: 500;
  border-radius: var(--radius-md);
  white-space: nowrap;
  line-height: 1.4;
  transition: all var(--duration-fast) var(--ease-out);
}
.action-btn--secondary {
  color: var(--color-accent-text);
  background: var(--color-surface);
  border: 1px solid var(--color-accent-border);
}
.action-btn--secondary:hover {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
}
.action-btn--primary {
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: 1px solid var(--color-accent);
  box-shadow: 0 1px 3px rgba(124, 58, 237, 0.18);
}
.action-btn--primary:hover {
  background: var(--color-accent-hover);
  border-color: var(--color-accent-hover);
}
/* 2026-06-01:最终作品按钮 — 与 primary 同色调但 outline 风格,区分主次 */
.action-btn--final {
  color: #b45309; /* amber-700 — 与 accent 紫色区分,提示这是"特别状态"*/
  background: #fef3c7; /* amber-100 */
  border: 1px solid #fbbf24; /* amber-400 */
  box-shadow: 0 1px 3px rgba(180, 83, 9, 0.15);
}
.action-btn--final:hover {
  background: #fde68a; /* amber-200 */
  border-color: #f59e0b; /* amber-500 */
}
.forever-badge {
  flex-shrink: 0;
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-full);
  white-space: nowrap;
  cursor: help;
  align-self: center;
}

/* 2.E 导出按钮(done 屏顶部 meta row)*/
.export-btn {
  flex-shrink: 0;
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-accent-text);
  background: var(--color-surface);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  white-space: nowrap;
  align-self: center;
  transition: all var(--duration-fast) var(--ease-out);
}
.export-btn:hover {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}

/* M7.I(2026-05-20)滚雪球续写按钮 — 用主色实心,与"导出 / 创作徽章"等次级 CTA 形成层次 */
.continue-btn {
  flex-shrink: 0;
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-md);
  cursor: pointer;
  white-space: nowrap;
  transition: all var(--duration-fast) var(--ease-out);
  box-shadow: 0 1px 3px rgba(124, 58, 237, 0.18);
}
.continue-btn:hover {
  background: var(--color-accent-hover);
  border-color: var(--color-accent-hover);
}

/* Sprint D.3 创作徽章按钮(done 屏 meta row,export-btn 旁)*/
.badge-btn {
  flex-shrink: 0;
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  white-space: nowrap;
  align-self: center;
  transition: all var(--duration-fast) var(--ease-out);
}
.badge-btn:hover {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}

.mono {
  font-family: var(--font-mono);
}

/* ============================================================
   1.R 自洽守护者 done 屏诊断面板
   ============================================================ */

.audit-section {
  margin-top: var(--space-6);
  padding-top: var(--space-5);
  border-top: 1px dashed var(--color-border);
}

/* 2.D 正典守护者 section(与 audit-section 同节奏)*/
.canonical-section {
  margin-top: var(--space-6);
  padding-top: var(--space-5);
  border-top: 1px dashed var(--color-border);
}

.audit-trigger {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  width: 100%;
  padding: var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px dashed var(--color-accent-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.audit-trigger:hover {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
  border-style: solid;
}
.audit-trigger--has-result {
  font-weight: 500;
}
.trigger-spark {
  font-size: var(--text-md);
}
.trigger-hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-weight: 400;
}
.audit-trigger:hover .trigger-hint {
  color: var(--color-text-on-accent);
  opacity: 0.8;
}

.audit-panel {
  margin-top: var(--space-3);
  padding: var(--space-5);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-md);
}

.audit-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-4);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.audit-title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
}
.audit-close {
  width: 28px;
  height: 28px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.audit-close:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.audit-loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-6);
  color: var(--color-text-muted);
}
.audit-spinner {
  display: inline-flex;
  gap: 6px;
}
.audit-spinner span {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-accent);
  animation: audit-bounce 1.2s infinite ease-in-out both;
}
.audit-spinner span:nth-child(1) { animation-delay: -0.32s; }
.audit-spinner span:nth-child(2) { animation-delay: -0.16s; }
@keyframes audit-bounce {
  0%, 80%, 100% { transform: scale(0); opacity: 0.4; }
  40%           { transform: scale(1); opacity: 1; }
}

.audit-error {
  text-align: center;
  padding: var(--space-5);
  color: var(--color-danger);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  align-items: center;
}

.audit-score-row {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  margin-bottom: var(--space-5);
  padding: var(--space-4);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
}
.score-block {
  display: flex;
  align-items: baseline;
  gap: 2px;
  padding: var(--space-2) var(--space-4);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  flex-shrink: 0;
}
.score-num {
  font-size: 28px;
  font-weight: 700;
  line-height: 1;
}
.score-max {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.score-high { color: #16A34A; border-color: rgba(22, 163, 74, 0.3); }
.score-mid  { color: #D97706; border-color: rgba(217, 119, 6, 0.3); }
.score-low  { color: var(--color-danger); border-color: var(--color-danger-soft); }

.audit-rec {
  font-size: var(--text-sm);
  color: var(--color-text);
  line-height: 1.5;
  flex: 1;
}

.issue-list {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  list-style: none;
  padding: 0;
  margin: 0 0 var(--space-5) 0;
}
.issue-card {
  padding: var(--space-4);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.issue-card--fixable {
  background: var(--color-surface);
  border-color: var(--color-accent-border);
}
.issue-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-2);
}
.issue-kind {
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
}
.issue-kind--fixable {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
}
.issue-subject {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
}

.issue-evidence,
.issue-cause,
.issue-fix {
  font-size: var(--text-sm);
  color: var(--color-text);
  margin: var(--space-2) 0 0;
  line-height: 1.6;
}
.evidence-label,
.cause-label,
.fix-label {
  color: var(--color-text-subtle);
  font-size: var(--text-xs);
  margin-right: 4px;
}
.evidence-quote {
  color: var(--color-text-muted);
  font-style: italic;
}

/* M7.C(2026-05-20)双按钮 row + 采纳 按钮 + 已采纳 chip + 已采纳卡片态 */
.issue-actions {
  display: flex;
  gap: var(--space-2);
  margin-top: var(--space-3);
  flex-wrap: wrap;
}
.issue-fix-btn {
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.issue-fix-btn:hover {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}
.issue-accept-btn {
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  font-weight: 500;
  color: #ffffff;
  background: #16A34A;        /* 绿色,与"已采纳"语义一致 */
  border: 1px solid #15803D;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.issue-accept-btn:hover:not(:disabled) {
  background: #15803D;
  border-color: #14532D;
}
.issue-accept-btn:disabled {
  opacity: 0.6;
  cursor: progress;
}
.issue-accepted-chip {
  margin-left: auto;          /* 推到最右 */
  padding: 2px 8px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: #15803D;
  background: rgba(22, 163, 74, 0.12);
  border: 1px solid rgba(22, 163, 74, 0.35);
  border-radius: var(--radius-sm);
}
.issue-card--accepted {
  background: rgba(22, 163, 74, 0.04);
  border-color: rgba(22, 163, 74, 0.25);
}
.issue-card--accepted .issue-evidence,
.issue-card--accepted .issue-cause,
.issue-card--accepted .issue-fix {
  opacity: 0.7;                /* 已采纳的卡片正文淡化 */
}

.audit-empty {
  padding: var(--space-5);
  text-align: center;
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  margin-bottom: var(--space-5);
}

.audit-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-bottom: var(--space-3);
}

.audit-cost {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  text-align: center;
}

/* Sprint 6.A2 路线图 #2(2026-05-22):角色情绪曲线 section */
.emotion-section {
  margin-top: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.emotion-section-header {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.emotion-section-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.emotion-section-hint {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.emotion-loading {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.emotion-chip-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.emotion-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  font-size: var(--text-sm);
  color: var(--color-text);
  transition: all var(--duration-fast) var(--ease-out);
}
.emotion-chip:hover {
  border-color: var(--color-accent-border);
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
}
.emotion-chip-name {
  font-weight: 500;
}
.emotion-chip-meta {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.emotion-chip:hover .emotion-chip-meta {
  color: var(--color-accent-text);
}
</style>
