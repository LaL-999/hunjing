<script setup lang="ts">
/**
 * CreateComicModal — D.9 Sprint 2.B+ 漫创态新建漫画弹窗
 *
 * 解决 Sprint 2.B 留的 UX 死锁:原 MyComicsView 的「新建漫画」按钮把用户踢回 dashboard
 * 让走 4 态卡片路径,但 4 态卡片选 cycle 又跳回 /my-comics → 空态 → 又被踢回 → 死循环。
 *
 * 本 modal 让用户**就地**新建漫画,不再跳来跳去。
 *
 * 黄金标准参照:`ChooseModeModal.vue`(中央 backdrop + blur + Esc 关 + Teleport body),
 * 表单部分参照 NewProjectModal.vue 的 input 风格 + ConfirmDialog 的 cta button 样式。
 *
 * Sprint 2.B+ 范围(YAGNI):
 *   - 仅支持 `type: "internal"` + `simulation_ids` 输入源(从「我的剧情线」选 done 推演)
 *   - upload_ids 输入源(`type: "external"`)Sprint 3 加(需先选 project 再选 upload,UX 更重)
 *   - 外部文本粘贴源 Sprint 4 再加(后端尚未实现 text body endpoint)
 *
 * 契约对齐:
 *   - 后端 backend/app/schemas/comic.py CreateComicRequest:
 *     - name: 1-30 字符,自动 strip
 *     - source.type: "internal"(本 sprint 唯一支持)
 *     - source.simulation_ids: 必填非空,后端校验都是当前 user 的 done 推演
 *   - 后端 comic_service.py:_RUNNING_COMICS 异步推进 _agent_scripter
 *
 * Emits:
 *   close                — 用户按 Esc / 点 backdrop / 点 X
 *   created(comicId)     — 创建成功,父 view push /comics/:id
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api } from "../api/client";
import {
  ApiError,
  type Comic,
  type PlanPreviewResponse,
  type ReadyUploadItem,
  type SimulationSummaryWithProject,
} from "../api/types";
import { toast } from "../composables/useToast";
import { useQuotaStore } from "../stores/quota";
import SkeletonBlock from "./SkeletonBlock.vue";

const router = useRouter();
const quota = useQuotaStore();

const props = defineProps<{
  /** modal 开关 — 由父组件控制 */
  open: boolean;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "created", comicId: string): void;
}>();

// ============================================================
// 表单状态
// ============================================================

const name = ref<string>("");
const selectedSimIds = ref<Set<string>>(new Set());

const simulations = ref<SimulationSummaryWithProject[]>([]);
const loadingSims = ref<boolean>(false);
const loadError = ref<string | null>(null);

// Sprint 4.D(2026-05-13):external 源(文本上传)tab
type SourceType = "internal" | "external";
const sourceType = ref<SourceType>("internal");
const selectedUploadIds = ref<Set<string>>(new Set());
const readyUploads = ref<ReadyUploadItem[]>([]);
const loadingUploads = ref<boolean>(false);
const uploadsError = ref<string | null>(null);

/** 提交进行中 — 防双击 + 禁所有交互 */
const submitting = ref<boolean>(false);
const submitError = ref<string | null>(null);

// ============================================================
// Sprint C.4 AI Planner state
// ============================================================

const planLoading = ref<boolean>(false);
const planError = ref<string | null>(null);
const plan = ref<PlanPreviewResponse | null>(null);
/** 用户调过的页数(用户拍板优先);未调时用 plan.recommended_total_pages */
const userPages = ref<number | null>(null);

/** 最终用的 target_pages — 用户调过的 OR 推荐 OR 默认 12 */
const finalTargetPages = computed<number>(() => {
  if (userPages.value !== null) return userPages.value;
  if (plan.value) return plan.value.recommended_total_pages;
  return 12;
});

/** 按当前 target_pages 调整 credit 预算(线性外推 plan 的 per-page 单价)*/
const adjustedCredits = computed<number>(() => {
  if (!plan.value) return 260;   // 默认 12 页基线
  const rec = plan.value.recommended_total_pages;
  const recCredits = plan.value.estimated_credits;
  if (rec === finalTargetPages.value) return recCredits;
  // 线性派生:(credits - overhead 40) / rec_pages * cur_pages + overhead 40
  const perPage = (recCredits - 40) / rec;
  return Math.max(100, Math.round(finalTargetPages.value * perPage + 40));
});

const currentBalance = computed<number>(
  () => quota.status?.credit_balance.total_credits ?? 0,
);
const isFounder = computed<boolean>(
  () => quota.status?.plan === "founder",
);
const insufficient = computed<boolean>(
  () => !isFounder.value && adjustedCredits.value > currentBalance.value,
);

/**
 * 仅 done 推演可作输入源(对齐后端 comic_service 校验)。
 */
const eligibleSimulations = computed(() =>
  simulations.value.filter((s) => s.state === "done"),
);

/**
 * UI 优化(2026-05-21 八轮):"同项目内多选"机制 — 防跨项目混拼无关作品
 *   - 用户第一次勾选某项时,锁定该项的 project_id 作为"本次选择的项目"
 *   - 后续勾选必须 project_id 一致,否则 toggle 被拒(toast 提示)
 *   - 当所有项取消勾选时,锁定的 project_id 自动释放(允许换项目)
 *
 * lockedProjectId:当前已勾选项所属项目 id(null = 还没选)
 * lockedProjectName:对应项目名(用于顶部状态 chip + tooltip)
 */
const lockedProjectId = computed<string | null>(() => {
  if (sourceType.value === "internal") {
    const firstId = selectedSimIds.value.values().next().value;
    if (!firstId) return null;
    const sim = simulations.value.find((s) => s.id === firstId);
    return sim?.project_id ?? null;
  }
  const firstId = selectedUploadIds.value.values().next().value;
  if (!firstId) return null;
  const upload = readyUploads.value.find((u) => u.id === firstId);
  return upload?.project_id ?? null;
});
const lockedProjectName = computed<string>(() => {
  if (!lockedProjectId.value) return "";
  if (sourceType.value === "internal") {
    const sim = simulations.value.find((s) => s.project_id === lockedProjectId.value);
    return sim?.project_name ?? "";
  }
  const upload = readyUploads.value.find((u) => u.project_id === lockedProjectId.value);
  return upload?.project_name ?? "";
});

/** 至少选 1 条源(internal=推演 / external=upload)才能提交 */
const selectionCount = computed<number>(() =>
  sourceType.value === "internal"
    ? selectedSimIds.value.size
    : selectedUploadIds.value.size,
);

const canSubmit = computed<boolean>(() => {
  return (
    name.value.trim().length > 0
    && name.value.trim().length <= 30
    && selectionCount.value > 0
    && !submitting.value
    && !planLoading.value
  );
});

// ============================================================
// 加载推演列表
// ============================================================

async function loadSimulations(): Promise<void> {
  loadingSims.value = true;
  loadError.value = null;
  try {
    simulations.value = await api.get<SimulationSummaryWithProject[]>(
      "/simulations",
    );
  } catch (e) {
    loadError.value = e instanceof ApiError ? e.message : "加载推演列表失败";
  } finally {
    loadingSims.value = false;
  }
}

/** Sprint 4.D:拉当前用户所有 ready uploads(external tab 用) */
async function loadReadyUploads(): Promise<void> {
  loadingUploads.value = true;
  uploadsError.value = null;
  try {
    readyUploads.value = await api.get<ReadyUploadItem[]>("/uploads/ready");
  } catch (e) {
    uploadsError.value = e instanceof ApiError ? e.message : "加载已上传文件失败";
  } finally {
    loadingUploads.value = false;
  }
}

// 每次打开 modal 都重新拉(用户可能刚跑完一条新推演 / 上传新文件)
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      // 重置状态
      name.value = "";
      selectedSimIds.value = new Set();
      selectedUploadIds.value = new Set();
      sourceType.value = "internal";   // 默认 internal tab
      submitError.value = null;
      // Sprint C.4:重置 planner 状态
      plan.value = null;
      planError.value = null;
      userPages.value = null;
      void loadSimulations();
      void loadReadyUploads();          // Sprint 4.D:并行预拉 uploads(切 tab 时无延迟)
    }
  },
);

// Sprint 4.D:切换 tab 时清选择 + 清 plan(避免 plan stale)
watch(sourceType, () => {
  selectedSimIds.value = new Set();
  selectedUploadIds.value = new Set();
  plan.value = null;
  planError.value = null;
  userPages.value = null;
});

// Sprint C.4 + 4.D:用户选 / 改输入源时,自动跑 planner(debounce 600ms 防快速点击)
// 监听 selectionCount 即可,无论 internal / external 都触发
let planTimer: ReturnType<typeof setTimeout> | null = null;
watch(
  [() => Array.from(selectedSimIds.value), () => Array.from(selectedUploadIds.value), sourceType],
  () => {
    if (planTimer) clearTimeout(planTimer);
    plan.value = null;        // 立即清旧推荐
    userPages.value = null;
    if (selectionCount.value === 0) {
      planError.value = null;
      planLoading.value = false;
      return;
    }
    planTimer = setTimeout(() => {
      void fetchPlanPreview();
    }, 600);
  },
);

/** Sprint 4.D:按当前 tab 构造 source payload(internal / external 统一出口) */
function buildSourcePayload() {
  if (sourceType.value === "internal") {
    return {
      type: "internal" as const,
      simulation_ids: Array.from(selectedSimIds.value),
    };
  }
  return {
    type: "external" as const,
    upload_ids: Array.from(selectedUploadIds.value),
  };
}

async function fetchPlanPreview() {
  if (selectionCount.value === 0) return;
  planLoading.value = true;
  planError.value = null;
  try {
    plan.value = await api.post<PlanPreviewResponse>(
      "/comics/plan_preview",
      {
        source: buildSourcePayload(),
        user_preference: "auto",
      },
    );
  } catch (e) {
    planError.value =
      e instanceof ApiError ? e.message : "AI 推荐暂不可用,你可自己定页数";
  } finally {
    planLoading.value = false;
  }
}

// ============================================================
// 选择交互
// ============================================================

function toggleSelect(simId: string): void {
  // UI 优化(2026-05-21 八轮):同项目内多选,跨项目禁止
  const next = new Set(selectedSimIds.value);
  if (next.has(simId)) {
    next.delete(simId);
  } else {
    // 检查项目一致性
    const sim = simulations.value.find((s) => s.id === simId);
    if (sim && lockedProjectId.value && sim.project_id !== lockedProjectId.value) {
      toast.warning(
        `已锁定项目《${lockedProjectName.value}》— 跨项目无法混选,取消所有选项后才能换项目`,
        4000,
      );
      return;
    }
    next.add(simId);
  }
  // Set 在 Vue 3 ref 里默认不响应式 mutate — 用新 Set 替换触发 reactive
  selectedSimIds.value = next;
}

function isSelected(simId: string): boolean {
  return selectedSimIds.value.has(simId);
}

/** UI 优化(2026-05-21 八轮):判断该项是否因"跨项目"被锁定不可勾选 */
function isSimDisabled(simId: string): boolean {
  if (!lockedProjectId.value || selectedSimIds.value.has(simId)) return false;
  const sim = simulations.value.find((s) => s.id === simId);
  return sim ? sim.project_id !== lockedProjectId.value : false;
}

// Sprint 4.D:external tab 的 upload 选择 — 同 pattern + 同项目约束
function toggleSelectUpload(uploadId: string): void {
  const next = new Set(selectedUploadIds.value);
  if (next.has(uploadId)) {
    next.delete(uploadId);
  } else {
    const upload = readyUploads.value.find((u) => u.id === uploadId);
    if (
      upload && lockedProjectId.value
      && upload.project_id !== lockedProjectId.value
    ) {
      toast.warning(
        `已锁定项目《${lockedProjectName.value}》— 跨项目无法混选,取消所有选项后才能换项目`,
        4000,
      );
      return;
    }
    next.add(uploadId);
  }
  selectedUploadIds.value = next;
}

function isUploadSelected(uploadId: string): boolean {
  return selectedUploadIds.value.has(uploadId);
}

function isUploadDisabled(uploadId: string): boolean {
  if (!lockedProjectId.value || selectedUploadIds.value.has(uploadId)) return false;
  const upload = readyUploads.value.find((u) => u.id === uploadId);
  return upload ? upload.project_id !== lockedProjectId.value : false;
}

// ============================================================
// 提交创建
// ============================================================

/**
 * 提交创建漫画 — Sprint 2.B+ 五修(2026-05-12)
 *
 * Bug 修复:原本流程是 emit('created') 让父组件 push 详情页,但父组件先 reactive
 * `createComicOpen.value = false` 触发 Teleport 卸载,再调 `router.push`,可能导致
 * navigation 与 modal 卸载产生 race condition,push 被静默吞掉。
 *
 * 修复策略:**modal 内自管 navigation**
 *   1. api.post 成功
 *   2. toast.success
 *   3. **await router.push 在 modal 内直接跳详情页**(关掉单点故障)
 *   4. push resolved 后才 emit('created') 让父扫尾(close modal + reload 列表)
 *
 * 异常处理:push 失败(NavigationFailure)不阻塞 emit,父仍能 close + reload,
 * 用户至少能看到新创建的漫画卡片。
 */
async function handleSubmit(): Promise<void> {
  if (!canSubmit.value) return;
  submitting.value = true;
  submitError.value = null;
  try {
    const comic = await api.post<Comic>("/comics", {
      name: name.value.trim(),
      source: buildSourcePayload(),       // Sprint 4.D:internal / external 统一构造
      target_pages: finalTargetPages.value,    // Sprint C.4
    });
    toast.success(`漫画《${comic.name}》已创建,即将进入参考图上传`, 3500);
    // 2026-06-01:广播 comic:created → MyComicsView 实时显示新漫画
    import("../stores/events").then(({ useEventBus }) => {
      try {
        useEventBus().emit("comic:created", { comic_id: comic.id });
      } catch (err) {
        if (import.meta.env.DEV) {
          // eslint-disable-next-line no-console
          console.warn("[CreateComicModal] emit comic:created failed:", err);
        }
      }
    });

    // 关键修复:modal 内主动 push,不依赖父组件 emit handler。
    // 即使父组件 onComicCreated 因任何原因没接到 emit / push 失败,这里也保证跳走。
    try {
      await router.push(`/comics/${comic.id}`);
    } catch (navErr) {
      // NavigationFailure(duplicate / aborted / cancelled)不应阻塞用户流程
      if (import.meta.env.DEV) {
        console.warn("[CreateComicModal] navigation push failed:", navErr);
      }
    }

    // push 完成后通知父(让父 close modal + reload 列表作扫尾)
    emit("created", comic.id);
  } catch (e) {
    if (e instanceof ApiError) {
      const detail = (e.detail as { message?: string } | null)?.message;
      submitError.value = detail || e.message;
    } else {
      submitError.value = "创建失败,请重试";
    }
  } finally {
    submitting.value = false;
  }
}

// ============================================================
// 关闭交互
// ============================================================

function handleBackdrop(e: MouseEvent): void {
  if (submitting.value) return;          // 提交中禁关
  if (e.target === e.currentTarget) emit("close");
}

function onGlobalKey(e: KeyboardEvent): void {
  if (!props.open) return;
  if (e.key === "Escape" && !submitting.value) {
    e.preventDefault();
    emit("close");
  }
}

onMounted(() => document.addEventListener("keydown", onGlobalKey));
onBeforeUnmount(() => document.removeEventListener("keydown", onGlobalKey));

// ============================================================
// 显示工具
// ============================================================

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-comic-title"
        @click="handleBackdrop"
      >
        <div class="modal-card surface" role="document">
          <button
            class="close-btn"
            type="button"
            aria-label="关闭"
            :disabled="submitting"
            @click="emit('close')"
          >×</button>

          <header class="modal-header">
            <h2 id="create-comic-title" class="modal-title">新建漫画</h2>
            <!-- UI 优化(2026-05-21 五轮):删除"剧情线"提及 + 简化副标题 -->
            <p class="modal-subtitle">
              选一个输入源 → AI 自动生成漫画
            </p>
          </header>

          <!-- 漫画名 -->
          <div class="field">
            <label class="field-label" for="comic-name">
              漫画名 <span class="field-required" aria-hidden="true">*</span>
            </label>
            <input
              id="comic-name"
              v-model="name"
              type="text"
              maxlength="30"
              placeholder="例:网恋风云·番外篇"
              class="text-input"
              :disabled="submitting"
              autocomplete="off"
            />
            <!-- UI 优化(2026-05-21 八轮):删左侧"1-30 字"提示,右侧 0/30 计数已自含约束信息 -->
            <div class="field-hint">
              <span class="mono">{{ name.trim().length }}/30</span>
            </div>
          </div>

          <!-- 输入源选择(Sprint 4.D:internal 推演 / external 已上传文件 双 tab) -->
          <div class="field">
            <label class="field-label">
              输入源 <span class="field-required" aria-hidden="true">*</span>
            </label>

            <!-- Tab 切换 -->
            <div class="source-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                :aria-selected="sourceType === 'internal'"
                class="source-tab"
                :class="{ 'is-active': sourceType === 'internal' }"
                :disabled="submitting"
                @click="sourceType = 'internal'"
              >
                <span aria-hidden="true">🎭</span>
                <span>从推演选</span>
                <span v-if="sourceType === 'internal' && selectedSimIds.size > 0"
                      class="tab-badge">{{ selectedSimIds.size }}</span>
              </button>
              <button
                type="button"
                role="tab"
                :aria-selected="sourceType === 'external'"
                class="source-tab"
                :class="{ 'is-active': sourceType === 'external' }"
                :disabled="submitting"
                @click="sourceType = 'external'"
              >
                <span aria-hidden="true">📄</span>
                <span>从已上传文件选</span>
                <span v-if="sourceType === 'external' && selectedUploadIds.size > 0"
                      class="tab-badge">{{ selectedUploadIds.size }}</span>
              </button>
            </div>

            <!--
              UI 优化(2026-05-21 八轮):
                - 简化文案,两 tab 同格式,保留"多选 + 顺序拼接"信息
                - 暗示"同项目"约束(让用户预期跨项目不可混)
            -->
            <p class="field-sub field-sub--detached">
              <template v-if="sourceType === 'internal'">
                多选同项目下的推演,按选中顺序拼接
              </template>
              <template v-else>
                多选同项目下的文件,按选中顺序拼接
              </template>
            </p>

            <!--
              UI 优化(2026-05-21 八轮):已锁定项目状态 chip — 让用户清楚知道当前在哪个项目内多选
              点击 ✕ 清空所有选项,允许换项目
            -->
            <!--
              Bug #6 修(2026-05-21):lockedProjectId 存在但 lockedProjectName 未加载完时,
              `《》` 会显示破损书名号。改为同时校验 lockedProjectName 非空才渲染
            -->
            <div v-if="lockedProjectId && lockedProjectName" class="locked-project-chip">
              <span class="locked-icon" aria-hidden="true">📌</span>
              <span class="locked-text">已锁定项目:<strong>《{{ lockedProjectName }}》</strong></span>
              <button
                type="button"
                class="locked-clear"
                aria-label="清空所有选项以解锁项目"
                title="清空所有选项,换项目"
                @click="sourceType === 'internal' ? (selectedSimIds = new Set()) : (selectedUploadIds = new Set())"
              >✕</button>
            </div>

            <!-- internal tab 内容 -->
            <template v-if="sourceType === 'internal'">
              <div v-if="loadingSims" class="sim-list" aria-busy="true" aria-live="polite">
                <div v-for="i in 3" :key="i" class="sim-row sim-row--skeleton">
                  <SkeletonBlock height="16px" width="160px" />
                  <SkeletonBlock height="14px" width="240px" />
                  <SkeletonBlock height="12px" width="100px" />
                </div>
              </div>

              <div v-else-if="loadError" class="state-msg state-error">
                {{ loadError }}
                <button class="retry-btn" type="button" @click="loadSimulations">重试</button>
              </div>

              <!-- UI 优化(2026-05-21 五轮):删"剧情线"提及 + 短小直接的提示 -->
              <div v-else-if="eligibleSimulations.length === 0" class="state-msg">
                还没有已完成的推演。
                <br />
                请先在项目内完成一次 AI 推演,或切换到「从已上传文件选」。
              </div>

              <ul v-else class="sim-list" role="listbox" aria-multiselectable="true">
                <li
                  v-for="sim in eligibleSimulations"
                  :key="sim.id"
                  class="sim-row"
                  :class="{
                    'is-selected': isSelected(sim.id),
                    'is-disabled': isSimDisabled(sim.id),
                  }"
                  role="option"
                  :aria-selected="isSelected(sim.id)"
                  :aria-disabled="isSimDisabled(sim.id)"
                  :tabindex="isSimDisabled(sim.id) ? -1 : 0"
                  :title="isSimDisabled(sim.id)
                    ? `已锁定项目《${lockedProjectName}》— 取消所有选项才能换项目`
                    : undefined"
                  @click="toggleSelect(sim.id)"
                  @keydown.enter.prevent="toggleSelect(sim.id)"
                  @keydown.space.prevent="toggleSelect(sim.id)"
                >
                  <span class="sim-check" aria-hidden="true">
                    <span v-if="isSelected(sim.id)" class="check-mark">✓</span>
                  </span>
                  <div class="sim-content">
                    <div class="sim-head">
                      <span class="sim-project">《{{ sim.project_name }}》</span>
                      <span class="sim-meta-inline mono">
                        {{ sim.reshape_percent }}% · {{ sim.rounds_planned }}轮
                      </span>
                    </div>
                    <p class="sim-divergence">{{ sim.divergence }}</p>
                    <div class="sim-meta-row">
                      <span class="sim-time mono">{{ formatTime(sim.created_at) }}</span>
                    </div>
                  </div>
                </li>
              </ul>
            </template>

            <!-- external tab 内容 -->
            <template v-else>
              <div v-if="loadingUploads" class="sim-list" aria-busy="true" aria-live="polite">
                <div v-for="i in 3" :key="i" class="sim-row sim-row--skeleton">
                  <SkeletonBlock height="16px" width="180px" />
                  <SkeletonBlock height="14px" width="220px" />
                  <SkeletonBlock height="12px" width="100px" />
                </div>
              </div>

              <div v-else-if="uploadsError" class="state-msg state-error">
                {{ uploadsError }}
                <button class="retry-btn" type="button" @click="loadReadyUploads">重试</button>
              </div>

              <div v-else-if="readyUploads.length === 0" class="state-msg">
                你还没有可用的已上传文件 — 漫画输入源需要 state='ready' 的文本(txt / docx / epub)。
                <br />
                请先在「我的项目」中创建项目,上传文件并完成解析。
              </div>

              <ul v-else class="sim-list" role="listbox" aria-multiselectable="true">
                <li
                  v-for="up in readyUploads"
                  :key="up.id"
                  class="sim-row"
                  :class="{
                    'is-selected': isUploadSelected(up.id),
                    'is-disabled': isUploadDisabled(up.id),
                  }"
                  role="option"
                  :aria-selected="isUploadSelected(up.id)"
                  :aria-disabled="isUploadDisabled(up.id)"
                  :tabindex="isUploadDisabled(up.id) ? -1 : 0"
                  :title="isUploadDisabled(up.id)
                    ? `已锁定项目《${lockedProjectName}》— 取消所有选项才能换项目`
                    : undefined"
                  @click="toggleSelectUpload(up.id)"
                  @keydown.enter.prevent="toggleSelectUpload(up.id)"
                  @keydown.space.prevent="toggleSelectUpload(up.id)"
                >
                  <span class="sim-check" aria-hidden="true">
                    <span v-if="isUploadSelected(up.id)" class="check-mark">✓</span>
                  </span>
                  <div class="sim-content">
                    <div class="sim-head">
                      <span class="sim-project">{{ up.filename }}</span>
                      <span v-if="up.parsed_text_chars" class="sim-meta-inline mono">
                        {{ up.parsed_text_chars.toLocaleString() }} 字
                      </span>
                    </div>
                    <p class="sim-divergence">
                      来自项目《{{ up.project_name }}》
                    </p>
                    <div class="sim-meta-row">
                      <span class="sim-time mono">{{ formatTime(up.uploaded_at) }}</span>
                    </div>
                  </div>
                </li>
              </ul>
            </template>
          </div>

          <!-- Sprint C.4:AI Planner 推荐区(选完源后自动跑)
               Sprint 4.D:internal / external 都触发 -->
          <div
            v-if="selectionCount > 0"
            class="planner-box"
            :class="{ 'is-loading': planLoading, 'is-insufficient': insufficient }"
          >
            <div v-if="planLoading" class="planner-loading">
              <span class="planner-spinner" aria-hidden="true">⟳</span>
              <span>AI 规划员分析中(约 5 秒)…</span>
            </div>

            <div v-else-if="planError" class="planner-error">
              ⚠ {{ planError }} — 已使用保守默认 12 页
            </div>

            <template v-else-if="plan">
              <div class="planner-header">
                <span class="planner-icon" aria-hidden="true">🤖</span>
                <strong>AI 规划员建议</strong>
                <span class="planner-reasoning">{{ plan.reasoning }}</span>
              </div>

              <div
                v-if="plan.long_text_warning"
                class="planner-warning"
                role="alert"
              >
                ⚠ 原文较长(超 9600 字)— 单本上限 12 页,建议未来分多本完整呈现
                <br />
                <span style="font-size: 10px; opacity: 0.7;">
                  (技术细节:Phase 2 真分批承接接通后可放开到 30 页)
                </span>
              </div>

              <div class="page-slider-row">
                <label class="slider-label" for="target-pages-slider">
                  目标页数
                </label>
                <input
                  id="target-pages-slider"
                  type="range"
                  min="6"
                  max="18"
                  step="1"
                  :value="finalTargetPages"
                  class="page-slider"
                  :disabled="submitting"
                  @input="userPages = Number(($event.target as HTMLInputElement).value)"
                />
                <span class="slider-value mono">
                  {{ finalTargetPages }} 页
                  <span
                    v-if="userPages === null"
                    class="auto-tag"
                  >推荐</span>
                </span>
              </div>

              <div class="credit-row" :class="{ 'is-insufficient': insufficient }">
                <span class="credit-label">预计消耗</span>
                <span class="credit-value mono">
                  <strong>{{ adjustedCredits }}</strong> c
                </span>
                <span class="credit-separator">·</span>
                <span class="credit-balance">
                  余额 <span class="mono">{{ currentBalance.toLocaleString() }}</span> c
                </span>
                <span v-if="insufficient" class="credit-warn">⚠ 余额不足</span>
              </div>
            </template>
          </div>

          <!-- 提交错误展示 -->
          <p v-if="submitError" class="submit-error">{{ submitError }}</p>

          <!-- 底部 CTA -->
          <footer class="modal-footer">
            <button
              type="button"
              class="btn btn-ghost"
              :disabled="submitting"
              @click="emit('close')"
            >取消</button>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="!canSubmit"
              @click="handleSubmit"
            >
              <span v-if="submitting">创建中…</span>
              <span v-else>创建漫画</span>
            </button>
          </footer>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(31, 31, 30, 0.4);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  max-width: 640px;
  max-height: calc(100vh - var(--space-8));
  padding: var(--space-6) var(--space-6) var(--space-5);
  position: relative;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-modal);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.close-btn {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.close-btn:hover:not(:disabled) {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.close-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.modal-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.modal-title {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.modal-subtitle {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin: 0;
  line-height: 1.55;
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.field-label {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
}
.field-required {
  color: var(--color-danger);
  margin-left: 2px;
}
.field-sub {
  font-weight: 400;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
/* Sprint 4.D:field-sub 在 tab 下独立成行(替代 label 内联模式) */
.field-sub--detached {
  display: block;
  margin: var(--space-1) 0 var(--space-2) 0;
}

/*
  UI 优化(2026-05-21 八轮):已锁定项目状态 chip
    - 浅紫底 + 📌 图标 + 项目名 + ✕ 清空按钮
    - 让用户清楚知道"当前在哪个项目内多选,不可跨项目"
*/
.locked-project-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 8px 4px 10px;
  margin: var(--space-1) 0 var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border, var(--color-accent));
  border-radius: var(--radius-md);
}
.locked-icon {
  font-size: var(--text-xs);
  flex-shrink: 0;
}
.locked-text {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 280px;
}
.locked-text strong {
  color: var(--color-accent-text);
  font-weight: 600;
}
.locked-clear {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.locked-clear:hover {
  background: rgba(0, 0, 0, 0.08);
  color: var(--color-text);
}

/* Sprint 4.D:source tab 切换 */
.source-tabs {
  display: flex;
  gap: var(--space-1);
  margin-top: var(--space-2);
  border-bottom: 1px solid var(--color-border);
}
.source-tab {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-2) var(--space-3);
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  color: var(--color-text-muted);
  font: inherit;
  font-size: 0.9em;
  cursor: pointer;
  transition: color 0.15s ease, border-color 0.15s ease;
  margin-bottom: -1px;   /* 与 source-tabs border-bottom 对齐避免双线 */
}
.source-tab:hover:not(:disabled):not(.is-active) {
  color: var(--color-text);
}
.source-tab.is-active {
  color: var(--color-accent);
  border-bottom-color: var(--color-accent);
  font-weight: 600;
}
.source-tab:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.tab-badge {
  display: inline-block;
  min-width: 18px;
  padding: 0 5px;
  background: var(--color-accent);
  color: var(--color-on-accent, #fff);
  border-radius: 9px;
  font-size: 0.75em;
  text-align: center;
  line-height: 16px;
  font-weight: 600;
}
.field-hint {
  /* UI 优化(2026-05-21 八轮):去掉左侧"1-30 字"后,计数右对齐 */
  display: flex;
  justify-content: flex-end;
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

.text-input {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  outline: none;
  transition: border-color var(--duration-fast) var(--ease-out),
              box-shadow var(--duration-fast) var(--ease-out);
}
.text-input:focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.12);
}
.text-input:disabled {
  background: var(--color-bg-subtle);
  cursor: not-allowed;
  opacity: 0.7;
}

/* ===== 推演列表 ===== */
.sim-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-height: 320px;
  overflow-y: auto;
  padding: 0;
}

.sim-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-3);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  outline: none;
  transition: border-color var(--duration-fast) var(--ease-out),
              background var(--duration-fast) var(--ease-out);
}
.sim-row:hover,
.sim-row:focus-visible {
  border-color: var(--color-accent-border);
  background: var(--color-surface-hover);
}
.sim-row.is-selected {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}
/*
  UI 优化(2026-05-21 八轮):跨项目锁定的禁用态
    - 半透明 + cursor not-allowed,让用户立刻知道不可选
    - hover 不再变色(去掉视觉鼓励)
    - title tooltip 提供解释
*/
.sim-row.is-disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.sim-row.is-disabled:hover,
.sim-row.is-disabled:focus-visible {
  border-color: var(--color-border);
  background: var(--color-surface);
}
.sim-row--skeleton {
  cursor: default;
  pointer-events: none;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.sim-check {
  flex-shrink: 0;
  width: 18px;
  height: 18px;
  border: 1.5px solid var(--color-border-strong);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-surface);
  transition: all var(--duration-fast) var(--ease-out);
  margin-top: 2px;
}
.sim-row.is-selected .sim-check {
  border-color: var(--color-accent);
  background: var(--color-accent);
}
.check-mark {
  color: var(--color-text-on-accent);
  font-size: var(--text-xs);
  font-weight: 700;
  line-height: 1;
}

.sim-content {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.sim-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: var(--space-2);
}
.sim-project {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  min-width: 0;
}
.sim-meta-inline {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.sim-divergence {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  font-style: italic;
  line-height: 1.5;
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.sim-meta-row {
  display: flex;
  gap: var(--space-2);
}
.sim-time {
  font-size: 11px;
  color: var(--color-text-subtle);
}

/* ===== 状态消息 ===== */
.state-msg {
  padding: var(--space-4);
  text-align: center;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  line-height: 1.6;
}
.state-error {
  color: var(--color-danger);
  background: var(--color-danger-soft);
}
.retry-btn {
  margin-left: var(--space-2);
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: transparent;
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
}

/* ===== Sprint C.4 AI Planner 推荐区 ===== */
.planner-box {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
}
.planner-box.is-insufficient {
  background: var(--color-danger-soft);
  border-color: var(--color-danger);
}

.planner-loading,
.planner-error {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  color: var(--color-text-muted);
}
.planner-error { color: var(--color-warning); }
.planner-spinner {
  display: inline-block;
  animation: spin var(--duration-spin) linear infinite;
  font-size: var(--text-md);
}

.planner-header {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.planner-icon { font-size: var(--text-md); }
.planner-reasoning {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  flex: 1;
  min-width: 0;
}

.planner-warning {
  padding: var(--space-1) var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-warning);
  background: rgba(245, 158, 11, 0.1);
  border-radius: var(--radius-sm);
}

.page-slider-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.slider-label {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.page-slider {
  flex: 1;
  height: 6px;
  -webkit-appearance: none;
  appearance: none;
  background: var(--color-border);
  border-radius: var(--radius-full);
  outline: none;
}
.page-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--color-accent);
  cursor: pointer;
  border: 2px solid #fff;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.15);
}
.page-slider::-moz-range-thumb {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: var(--color-accent);
  cursor: pointer;
  border: 2px solid #fff;
}
.slider-value {
  flex-shrink: 0;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
  min-width: 80px;
  text-align: right;
}
.auto-tag {
  margin-left: 4px;
  padding: 1px 4px;
  font-size: 9px;
  font-weight: 500;
  color: var(--color-accent-text);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
  font-family: var(--font-sans);
}

.credit-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  flex-wrap: wrap;
}
.credit-row.is-insufficient {
  color: var(--color-danger);
}
.credit-value strong {
  font-size: var(--text-sm);
  color: var(--color-accent-text);
  font-weight: 700;
}
.credit-row.is-insufficient .credit-value strong {
  color: var(--color-danger);
}
.credit-separator { color: var(--color-text-subtle); }
.credit-warn {
  margin-left: auto;
  font-weight: 600;
  color: var(--color-danger);
}

.submit-error {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-radius: var(--radius-md);
  margin: 0;
}

/* ===== footer CTA ===== */
.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}

.btn {
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
  font-weight: 500;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}

.btn-ghost {
  color: var(--color-text);
  background: transparent;
  border: 1px solid var(--color-border);
}
.btn-ghost:hover:not(:disabled) {
  background: var(--color-surface-hover);
  border-color: var(--color-border-strong);
}

.btn-primary {
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: 1px solid var(--color-accent);
}
.btn-primary:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.mono {
  font-family: var(--font-mono);
}

</style>
