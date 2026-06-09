<script setup lang="ts">
/**
 * 分集规划面板 — 阶段 8.4+ 完整版(2026-06-08)。
 *
 * UX:
 *   - 预设档 chip(短剧 / 长剧 / 番剧 / 自定义)
 *   - 一键运行 → 后端跑 3 视角 + 评分 + LLM logline
 *   - 视角对比 tabs(rhythm 节奏 / hook 钩子 / arc 角色弧光)
 *   - 推荐方案 🏆 自动选 aggregate 最高
 *   - 每集卡:LLM logline 标题 + 4 维评分条 + cliffhanger 强度 + 下集预告
 *   - 桥接增益 banner(SP-4 时间线启用时显示)
 *   - tension 曲线 mini chart(SVG 内联)
 *
 * 与 Phase 1-6 后端的衔接:
 *   POST /screenplay/novels/{id}/plan-episodes-multi
 *   → MultiPerspectivePlanApi(3 个 perspectives + perspective_scores)
 */
import { computed, onMounted, ref, watch } from "vue";

import {
  deleteEpisodePlan,
  downloadEpisodePlan,
  getEpisodePlan,
  getEpisodePresets,
  listEpisodePlans,
  planEpisodesMulti,
  renameEpisodePlan,
  saveEpisodePlan,
  type EpisodePlanExportMode,
  type EpisodePlanSummaryApi,
  type EpisodePresetApi,
  type EpisodeWithMetaApi,
  type ExportFormat,
  type MultiPerspectivePlanApi,
  type PerspectiveDescApi,
  type PerspectivePlanApi,
  type PlanQualityScoresApi,
} from "../api/screenplay-client";
import { toast } from "../../composables/useToast";
import { confirm } from "../../composables/useConfirm";

const props = defineProps<{
  novelId: string;
  visible: boolean;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

// ===== state =====

const presets = ref<EpisodePresetApi[]>([]);
const perspectiveDescs = ref<PerspectiveDescApi[]>([]);
const selectedPreset = ref<string>("short_drama");
const customMinutes = ref<number>(3.0);
const withLlmTitles = ref<boolean>(true);

const plan = ref<MultiPerspectivePlanApi | null>(null);
const loading = ref<boolean>(false);

// 2026-06-09 P3:tabs(主面板 / 我的方案 列表)+ 已加载方案的 id(对应"覆盖保存")
type MainTab = "plan" | "saved";
const mainTab = ref<MainTab>("plan");
const savedPlans = ref<EpisodePlanSummaryApi[]>([]);
const savedPlansLoading = ref<boolean>(false);
const currentLoadedPlanId = ref<string | null>(null);   // 加载历史方案后记下,改名/删除用

// 保存方案 modal state
const saveDialogOpen = ref<boolean>(false);
const saveDialogName = ref<string>("");
const saving = ref<boolean>(false);
const activePerspective = ref<"rhythm" | "hook" | "arc">("rhythm");
const expandedEpisode = ref<number>(-1);

const PERSPECTIVE_ORDER: Array<"rhythm" | "hook" | "arc"> = [
  "rhythm", "hook", "arc",
];

// ===== computed =====

const currentPlan = computed<PerspectivePlanApi | null>(() => {
  if (!plan.value) return null;
  return (
    plan.value.perspectives.find(
      (p) => p.perspective === activePerspective.value,
    ) || null
  );
});

const currentScores = computed<PlanQualityScoresApi | null>(() => {
  if (!plan.value) return null;
  return plan.value.perspective_scores[activePerspective.value] || null;
});

/**
 * 2026-06-09:判断不同视角是否切出相同 cuts(数据特征导致 — 不是 bug)
 * 例如《麦田》只切 2 集时,3 视角可能因小说太短碰巧选同样切点。
 * 加这个 computed 让 UI 显示友好提示,而不是让用户以为 bug。
 */
const perspectivesMatchCurrent = computed<string[]>(() => {
  if (!plan.value) return [];
  const cur = currentPlan.value;
  if (!cur) return [];
  const matches: string[] = [];
  for (const p of plan.value.perspectives) {
    if (p.perspective === activePerspective.value) continue;
    // cuts 数组完全相同 = 同切点
    if (
      p.cuts.length === cur.cuts.length &&
      p.cuts.every((c, i) => c === cur.cuts[i])
    ) {
      matches.push(p.label || p.perspective);
    }
  }
  return matches;
});

const isCustomPreset = computed(() => selectedPreset.value === "custom");

const effectiveTarget = computed<number>(() => {
  if (!plan.value) {
    // 还没运行,显示当前 preset 默认值或 custom 滑块值
    if (isCustomPreset.value) return customMinutes.value;
    const p = presets.value.find((p) => p.key === selectedPreset.value);
    return p?.default_minutes ?? 3.0;
  }
  return plan.value.target_minutes_per_ep;
});

// ===== actions =====

async function loadPresets() {
  try {
    const data = await getEpisodePresets();
    presets.value = data.presets;
    perspectiveDescs.value = data.perspectives;
  } catch (e) {
    // 后台拉失败 → 用 hardcoded 兜底
    presets.value = [
      { key: "short_drama", label: "短剧(2-3 分钟/集)", description: "", default_minutes: 2.5 },
      { key: "long_drama", label: "长剧(8-12 分钟/集)", description: "", default_minutes: 10 },
      { key: "anime", label: "番剧(22-30 分钟/集)", description: "", default_minutes: 22 },
      { key: "custom", label: "自定义", description: "", default_minutes: null },
    ];
  }
}

async function runPlan() {
  loading.value = true;
  expandedEpisode.value = -1;
  try {
    const result = await planEpisodesMulti(props.novelId, {
      preset: selectedPreset.value,
      target_minutes_per_ep: isCustomPreset.value ? customMinutes.value : null,
      with_llm_titles: withLlmTitles.value,
    });
    plan.value = result;
    // 自动切到推荐 perspective
    if (result.recommended_perspective) {
      activePerspective.value = result.recommended_perspective;
    } else if (result.perspectives.length > 0) {
      activePerspective.value = result.perspectives[0].perspective;
    }
  } catch (e) {
    plan.value = null;
    const msg = e instanceof Error ? e.message : String(e);
    toast.error("规划失败:" + msg);
  } finally {
    loading.value = false;
  }
}

function selectPreset(key: string) {
  selectedPreset.value = key;
}

// =====================================================================
// 2026-06-09 P3:分集方案持久化操作
// =====================================================================

/** 打开保存对话框,默认填一个建议名 */
function openSaveDialog() {
  if (!plan.value) {
    toast.warning("先生成一个方案再保存");
    return;
  }
  // 建议名格式:「预设 · 推荐视角 · MM-DD」
  const presetLabel = presets.value.find(p => p.key === selectedPreset.value)?.label
    ?? selectedPreset.value;
  const persp = plan.value.recommended_perspective ?? "rhythm";
  const perspLabel = perspectiveDescs.value.find(p => p.key === persp)?.label ?? persp;
  const d = new Date();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  saveDialogName.value = `${presetLabel} · ${perspLabel} · ${mm}-${dd}`;
  saveDialogOpen.value = true;
}

function closeSaveDialog() {
  saveDialogOpen.value = false;
  saveDialogName.value = "";
}

/** 真保存 */
async function confirmSave() {
  if (!plan.value) return;
  const name = saveDialogName.value.trim();
  if (!name) {
    toast.warning("请输入方案名");
    return;
  }
  if (name.length > 80) {
    toast.warning("方案名最多 80 字");
    return;
  }
  saving.value = true;
  try {
    await saveEpisodePlan(props.novelId, {
      scheme_name: name,
      preset: selectedPreset.value,
      target_minutes: plan.value.target_minutes_per_ep,
      plan_data: plan.value,
    });
    toast.success(`已保存方案「${name}」`);
    closeSaveDialog();
    // 刷新列表(让用户回 saved tab 时能看到)
    await loadSavedPlans();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    toast.error("保存失败:" + msg);
  } finally {
    saving.value = false;
  }
}

/** 列表拉取 */
async function loadSavedPlans() {
  savedPlansLoading.value = true;
  try {
    savedPlans.value = await listEpisodePlans(props.novelId);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    toast.error("拉取方案列表失败:" + msg);
  } finally {
    savedPlansLoading.value = false;
  }
}

/** 切 tab — 进 saved 时拉列表 */
function switchMainTab(tab: MainTab) {
  mainTab.value = tab;
  if (tab === "saved") {
    void loadSavedPlans();
  }
}

/** 加载历史方案 — 切回主面板,plan/视角 自动回填 */
async function openSavedPlan(planId: string) {
  try {
    const full = await getEpisodePlan(planId);
    plan.value = full.plan_data;
    currentLoadedPlanId.value = full.id;
    selectedPreset.value = full.preset;
    customMinutes.value = full.target_minutes;
    // 自动切回推荐视角
    if (full.plan_data.recommended_perspective) {
      activePerspective.value = full.plan_data.recommended_perspective;
    } else if (full.plan_data.perspectives.length > 0) {
      activePerspective.value = full.plan_data.perspectives[0].perspective;
    }
    mainTab.value = "plan";
    toast.success(`已加载方案「${full.scheme_name}」`);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    toast.error("加载方案失败:" + msg);
  }
}

/** 删除方案 — 二次确认 */
async function deleteSavedPlan(p: EpisodePlanSummaryApi) {
  const ok = await confirm({
    title: "删除方案",
    message: `确定删除「${p.scheme_name}」吗?此操作不可恢复。`,
    confirmLabel: "删除",
    cancelLabel: "取消",
    danger: true,
  });
  if (!ok) return;
  try {
    await deleteEpisodePlan(p.id);
    toast.success("已删除");
    if (currentLoadedPlanId.value === p.id) {
      currentLoadedPlanId.value = null;
    }
    await loadSavedPlans();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    toast.error("删除失败:" + msg);
  }
}

/** 改名 — 复用 saveDialog 受控 input(避免 window.prompt 违反铁律)*/
const renameDialogOpen = ref<boolean>(false);
const renamingPlan = ref<EpisodePlanSummaryApi | null>(null);
const renameDialogName = ref<string>("");

function openRenameDialog(p: EpisodePlanSummaryApi) {
  renamingPlan.value = p;
  renameDialogName.value = p.scheme_name;
  renameDialogOpen.value = true;
}

function closeRenameDialog() {
  renameDialogOpen.value = false;
  renamingPlan.value = null;
  renameDialogName.value = "";
}

/** 导出方案 — 2026-06-09 v2:弹 dialog 选 format + mode 一次性指定 */
const exportingPlanId = ref<string | null>(null);
const exportDialogOpen = ref<boolean>(false);
const exportingPlan = ref<EpisodePlanSummaryApi | null>(null);
const exportFormat = ref<ExportFormat>("fountain");
const exportMode = ref<EpisodePlanExportMode>("full");

function openExportDialog(p: EpisodePlanSummaryApi) {
  exportingPlan.value = p;
  exportFormat.value = "fountain";
  exportMode.value = "full";
  exportDialogOpen.value = true;
}

function closeExportDialog() {
  exportDialogOpen.value = false;
  exportingPlan.value = null;
}

async function confirmExport() {
  const p = exportingPlan.value;
  if (!p) return;
  exportingPlanId.value = p.id;
  try {
    await downloadEpisodePlan(p.id, exportFormat.value, exportMode.value);
    toast.success(`已导出 ${exportFormat.value}(${exportModeLabel(exportMode.value)})`);
    closeExportDialog();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    toast.error("导出失败:" + msg);
  } finally {
    exportingPlanId.value = null;
  }
}

function exportModeLabel(m: EpisodePlanExportMode): string {
  return { outline: "仅大纲", full: "大纲+剧本", script: "仅剧本" }[m];
}

async function confirmRename() {
  const target = renamingPlan.value;
  if (!target) return;
  const trimmed = renameDialogName.value.trim();
  if (!trimmed) {
    toast.warning("方案名不能为空");
    return;
  }
  if (trimmed.length > 80) {
    toast.warning("方案名最多 80 字");
    return;
  }
  if (trimmed === target.scheme_name) {
    closeRenameDialog();
    return;
  }
  try {
    await renameEpisodePlan(target.id, trimmed);
    toast.success("已改名");
    closeRenameDialog();
    await loadSavedPlans();
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    toast.error("改名失败:" + msg);
  }
}

function switchPerspective(p: "rhythm" | "hook" | "arc") {
  activePerspective.value = p;
  expandedEpisode.value = -1;
}

function toggleEpisode(num: number) {
  expandedEpisode.value = expandedEpisode.value === num ? -1 : num;
}

// ===== utility =====

function perspectiveLabel(key: string): string {
  const p = perspectiveDescs.value.find((p) => p.key === key);
  return p?.label || key;
}

function perspectiveDescription(key: string): string {
  const p = perspectiveDescs.value.find((p) => p.key === key);
  return p?.description || "";
}

function perspectiveAggregate(key: string): number | null {
  if (!plan.value) return null;
  const s = plan.value.perspective_scores[key];
  return s?.aggregate ?? null;
}

function getEpisodeQualityClass(score: number | null): string {
  if (score === null) return "qual-na";
  if (score >= 0.75) return "qual-high";
  if (score >= 0.50) return "qual-mid";
  return "qual-low";
}

// 时长字符串化
function fmtMin(v: number): string {
  return v.toFixed(1);
}

// 范围字符串
function chapterRange(ep: EpisodeWithMetaApi): string {
  if (ep.first_chapter === null) return "";
  if (ep.last_chapter !== null && ep.last_chapter !== ep.first_chapter) {
    return `第 ${ep.first_chapter}-${ep.last_chapter} 章`;
  }
  return `第 ${ep.first_chapter} 章`;
}

// tension curve SVG path
const tensionPath = computed<string>(() => {
  if (!plan.value || plan.value.tension_curve.length === 0) return "";
  const curve = plan.value.tension_curve;
  const w = 400;
  const h = 60;
  const stepX = w / Math.max(1, curve.length - 1);
  const points = curve.map((v, i) => {
    const x = i * stepX;
    const y = h - v * (h - 4) - 2;
    return `${i === 0 ? "M" : "L"} ${x.toFixed(1)} ${y.toFixed(1)}`;
  });
  return points.join(" ");
});

// tension curve 切点标记
const cutPointMarkers = computed<{ x: number; perspective: string }[]>(() => {
  if (!plan.value || !currentPlan.value) return [];
  const curve = plan.value.tension_curve;
  if (curve.length === 0) return [];
  const w = 400;
  const stepX = w / Math.max(1, curve.length - 1);
  return currentPlan.value.cuts.map((c) => ({
    x: c * stepX,
    perspective: activePerspective.value,
  }));
});

// ===== lifecycle =====

onMounted(() => {
  void loadPresets();
});

watch(
  () => [props.visible, props.novelId],
  ([v]) => {
    if (v) {
      // 重新打开时重置
      plan.value = null;
      expandedEpisode.value = -1;
      // 默认不自动运行(避免烧 token)
    }
  },
);
</script>

<template>
  <Teleport to="body">
    <div v-if="visible" class="epp-overlay" @click.self="emit('close')">
      <div class="epp-panel screenplay-module">
        <!-- 顶部 -->
        <header class="epp-hdr">
          <div class="epp-hdr-left">
            <h2 class="literary-heading">分集规划 · 多视角</h2>
            <p class="epp-sub">
              3 视角并行(节奏 / 钩子 / 角色弧光)+ 4 维评分 + LLM logline 标题
              {{ plan?.bridge_used ? " · 桥接 SP-4 已启用" : "" }}
            </p>
          </div>
          <button class="epp-close-btn" @click="emit('close')" title="关闭">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </header>

        <!-- 2026-06-09 P3:主 tab 切换 — 主面板 / 我的方案 -->
        <div class="epp-maintab-row">
          <button
            class="epp-maintab"
            :class="{ active: mainTab === 'plan' }"
            @click="switchMainTab('plan')"
          >
            生成方案
          </button>
          <button
            class="epp-maintab"
            :class="{ active: mainTab === 'saved' }"
            @click="switchMainTab('saved')"
          >
            我的方案
            <span v-if="savedPlans.length > 0" class="epp-maintab-count mono">
              {{ savedPlans.length }}
            </span>
          </button>
        </div>

        <!-- 2026-06-09 P3:主面板内容(生成方案 tab)-->
        <div v-show="mainTab === 'plan'" class="epp-tab-content">
        <!-- 控制区 -->
        <section class="ctrl-row">
          <!-- 预设档 chip -->
          <div class="ctrl-presets">
            <span class="ctrl-label">题材预设</span>
            <div class="preset-chips">
              <button
                v-for="p in presets"
                :key="p.key"
                class="preset-chip"
                :class="{ active: selectedPreset === p.key }"
                @click="selectPreset(p.key)"
                :title="p.description"
              >
                {{ p.label }}
              </button>
            </div>
          </div>

          <!-- custom 滑块 -->
          <div v-if="isCustomPreset" class="ctrl-slider-row">
            <label class="ctrl-label">
              单集时长
              <span class="ctrl-value mono">{{ customMinutes.toFixed(1) }} 分钟</span>
            </label>
            <input
              v-model.number="customMinutes"
              type="range"
              min="0.5"
              max="30"
              step="0.5"
              class="ctrl-slider"
            />
          </div>

          <!-- LLM 标题 toggle + 运行 -->
          <div class="ctrl-actions">
            <label class="ctrl-toggle">
              <input v-model="withLlmTitles" type="checkbox" />
              <span>LLM 钩子型标题 + 下集预告</span>
            </label>
            <button class="run-btn" @click="runPlan" :disabled="loading">
              <svg v-if="!loading" width="14" height="14" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <polygon points="5 3 19 12 5 21 5 3" />
              </svg>
              <svg v-else width="14" height="14" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"
                   class="spin">
                <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              </svg>
              {{ loading ? "生成中...(约 30s)" : plan ? "重新生成" : "生成分集" }}
            </button>
          </div>
        </section>

        <!-- 主体:加载中 / 结果 / 空 -->
        <section class="epp-body">
          <!-- 加载 -->
          <div v-if="loading" class="state-msg">
            <div class="loading-pulse"></div>
            <div class="loading-text">3 视角并行规划中,LLM 在写 logline...</div>
          </div>

          <!-- 空(未运行)-->
          <div v-else-if="!plan" class="placeholder">
            <svg class="placeholder-icon" width="48" height="48" viewBox="0 0 24 24"
                 fill="none" stroke="currentColor" stroke-width="1.2"
                 stroke-linecap="round" stroke-linejoin="round">
              <rect x="3" y="3" width="7" height="7" rx="1" />
              <rect x="14" y="3" width="7" height="7" rx="1" />
              <rect x="3" y="14" width="7" height="7" rx="1" />
              <rect x="14" y="14" width="7" height="7" rx="1" />
            </svg>
            <p class="placeholder-text">选好预设档,点击「生成分集」开始</p>
            <p class="placeholder-hint">
              系统会跑 3 个独立视角(节奏 / 钩子 / 角色弧光),
              每个 4 维评分,推荐最佳方案
            </p>
          </div>

          <!-- 结果 -->
          <template v-else>
            <!-- 桥接增益 banner -->
            <div v-if="plan.bridge_used" class="bridge-banner">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
                <path d="M12 2v20M2 12h20" />
              </svg>
              <span class="bridge-text">
                <strong>桥接 SP-4 已启用</strong> — 角色情绪曲线已注入,
                tension 评分基于浑晶推演时间线增强
              </span>
            </div>

            <!-- tension 曲线 -->
            <div class="tension-section">
              <div class="tension-title">
                <span>张力曲线</span>
                <span class="tension-meta">
                  {{ plan.tension_curve.length }} 场 · {{ plan.candidate_cut_count }} 个候选切点
                </span>
              </div>
              <svg class="tension-svg" viewBox="0 0 400 60"
                   preserveAspectRatio="none">
                <!-- 网格基线 -->
                <line x1="0" y1="58" x2="400" y2="58" stroke="var(--border)" stroke-width="0.5" />
                <line x1="0" y1="30" x2="400" y2="30" stroke="var(--border)"
                      stroke-width="0.3" stroke-dasharray="2 3" />
                <!-- 切点垂直线 -->
                <line
                  v-for="(cut, i) in cutPointMarkers"
                  :key="`cut-${i}`"
                  :x1="cut.x" y1="0" :x2="cut.x" y2="60"
                  stroke="var(--accent)" stroke-width="0.8" stroke-dasharray="2 2"
                  opacity="0.6"
                />
                <!-- 曲线 -->
                <path
                  :d="tensionPath"
                  fill="none"
                  stroke="var(--accent)"
                  stroke-width="1.5"
                  stroke-linejoin="round"
                />
              </svg>
            </div>

            <!-- 视角 tabs -->
            <div class="persp-tabs">
              <button
                v-for="key in PERSPECTIVE_ORDER"
                :key="key"
                class="persp-tab"
                :class="{
                  active: activePerspective === key,
                  recommended: plan.recommended_perspective === key,
                }"
                @click="switchPerspective(key)"
              >
                <span class="persp-tab-label">{{ perspectiveLabel(key) }}</span>
                <span
                  v-if="plan.recommended_perspective === key"
                  class="persp-tab-crown"
                  title="推荐方案"
                >🏆</span>
                <span
                  v-if="perspectiveAggregate(key) !== null"
                  class="persp-tab-score mono"
                >
                  {{ ((perspectiveAggregate(key) || 0) * 100).toFixed(0) }}
                </span>
              </button>
            </div>

            <!-- 当前视角内容 -->
            <div v-if="currentPlan" class="persp-content">
              <!-- rationale + LLM 失败提示 -->
              <p class="persp-rationale">{{ currentPlan.rationale }}</p>
              <div
                v-if="currentPlan.llm_failed"
                class="llm-failed-warn"
              >
                ⚠ 此视角 LLM 调用失败,已自动退到节奏算法
              </div>

              <!-- 2026-06-09:不同视角推荐同一组切点时的友好提示
                   (作品太短 / 张力曲线扁平时常出现 — 不是 bug) -->
              <div
                v-if="perspectivesMatchCurrent.length > 0"
                class="persp-match-info"
              >
                <svg
                  width="14" height="14" viewBox="0 0 24 24" fill="none"
                  stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
                >
                  <circle cx="12" cy="12" r="10" />
                  <path d="M12 16v-4" />
                  <path d="M12 8h.01" />
                </svg>
                <span>
                  {{ perspectivesMatchCurrent.join(' / ') }}
                  视角也推荐相同切点 —
                  作品较短 / 节奏均匀时 3 个算法常会汇聚到同一组切集,属正常。
                </span>
              </div>

              <!-- 4 维评分卡 -->
              <div v-if="currentScores" class="quality-card">
                <div class="quality-row quality-aggregate">
                  <span class="quality-label">综合评分</span>
                  <div class="quality-bar-wrap">
                    <div
                      class="quality-bar quality-bar-agg"
                      :style="`width: ${(currentScores.aggregate * 100).toFixed(0)}%`"
                    ></div>
                  </div>
                  <span class="quality-num mono">
                    {{ (currentScores.aggregate * 100).toFixed(0) }}
                  </span>
                </div>
                <div class="quality-dims">
                  <div class="quality-row">
                    <span class="quality-label">集尾钩子</span>
                    <div class="quality-bar-wrap">
                      <div class="quality-bar"
                           :style="`width: ${(currentScores.cliffhanger_strength * 100).toFixed(0)}%`"></div>
                    </div>
                    <span class="quality-num mono">
                      {{ (currentScores.cliffhanger_strength * 100).toFixed(0) }}
                    </span>
                  </div>
                  <div class="quality-row">
                    <span class="quality-label">时长均匀</span>
                    <div class="quality-bar-wrap">
                      <div class="quality-bar"
                           :style="`width: ${(currentScores.pacing_evenness * 100).toFixed(0)}%`"></div>
                    </div>
                    <span class="quality-num mono">
                      {{ (currentScores.pacing_evenness * 100).toFixed(0) }}
                    </span>
                  </div>
                  <div class="quality-row">
                    <span class="quality-label">主角戏份均衡</span>
                    <div class="quality-bar-wrap">
                      <div class="quality-bar"
                           :style="`width: ${(currentScores.character_balance * 100).toFixed(0)}%`"></div>
                    </div>
                    <span class="quality-num mono">
                      {{ (currentScores.character_balance * 100).toFixed(0) }}
                    </span>
                  </div>
                  <div class="quality-row">
                    <span class="quality-label">章节边界尊重</span>
                    <div class="quality-bar-wrap">
                      <div class="quality-bar"
                           :style="`width: ${(currentScores.chapter_continuity * 100).toFixed(0)}%`"></div>
                    </div>
                    <span class="quality-num mono">
                      {{ (currentScores.chapter_continuity * 100).toFixed(0) }}
                    </span>
                  </div>
                </div>
                <p class="quality-summary">{{ currentScores.summary }}</p>
              </div>

              <!-- 集卡列表 -->
              <ul v-if="currentPlan.episodes.length > 0" class="ep-list">
                <li
                  v-for="ep in currentPlan.episodes"
                  :key="ep.episode_number"
                  class="ep-card"
                  :class="[
                    { expanded: expandedEpisode === ep.episode_number },
                    getEpisodeQualityClass(ep.quality_score),
                  ]"
                >
                  <div class="ep-hdr" @click="toggleEpisode(ep.episode_number)">
                    <!-- 左:集号 + 标题 + teaser -->
                    <div class="ep-main">
                      <div class="ep-title-row">
                        <span class="ep-num mono">第 {{ ep.episode_number }} 集</span>
                        <span class="ep-title">
                          {{ ep.title.replace(/^第 \d+ 集 · /, "") || "(无副标题)" }}
                        </span>
                      </div>
                      <div v-if="ep.teaser" class="ep-teaser">
                        <span class="teaser-prefix">下集预告:</span>{{ ep.teaser }}
                      </div>
                    </div>

                    <!-- 右:量化数据 -->
                    <div class="ep-stats">
                      <div class="ep-stat-row">
                        <span class="meta-pill meta-duration">
                          {{ fmtMin(ep.est_minutes) }} 分钟
                        </span>
                        <span class="meta-pill meta-scenes">{{ ep.scene_count }} 场</span>
                        <span v-if="ep.first_chapter !== null" class="meta-pill meta-chapter">
                          {{ chapterRange(ep) }}
                        </span>
                      </div>
                      <div class="ep-bars">
                        <div class="cliff-bar-wrap" :title="`钩子强度 ${(ep.cliffhanger_potential * 100).toFixed(0)}`">
                          <span class="cliff-bar-label">钩</span>
                          <div class="cliff-bar-track">
                            <div class="cliff-bar-fill"
                                 :style="`width: ${(ep.cliffhanger_potential * 100).toFixed(0)}%`"></div>
                          </div>
                        </div>
                        <div
                          v-if="ep.quality_score !== null"
                          class="qual-bar-wrap"
                          :title="`本集质量 ${(ep.quality_score * 100).toFixed(0)}`"
                        >
                          <span class="qual-bar-label">质</span>
                          <div class="qual-bar-track">
                            <div class="qual-bar-fill"
                                 :style="`width: ${(ep.quality_score * 100).toFixed(0)}%`"></div>
                          </div>
                        </div>
                      </div>
                    </div>

                    <svg
                      class="ep-chev"
                      :class="{ open: expandedEpisode === ep.episode_number }"
                      width="14" height="14" viewBox="0 0 24 24" fill="none"
                      stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"
                    >
                      <polyline points="6 9 12 15 18 9" />
                    </svg>
                  </div>

                  <!-- 展开:详情 -->
                  <div v-if="expandedEpisode === ep.episode_number" class="ep-detail">
                    <div v-if="ep.summary_preview" class="ep-summary">
                      <span class="ep-detail-label">首场摘要</span>
                      <p>{{ ep.summary_preview }}</p>
                    </div>
                    <div class="ep-tension-info">
                      <span class="ep-detail-label">张力</span>
                      <p class="mono">
                        峰 {{ ep.tension_peak.toFixed(2) }} ·
                        均 {{ ep.tension_avg.toFixed(2) }} ·
                        集尾钩子 {{ ep.cliffhanger_potential.toFixed(2) }}
                      </p>
                    </div>
                    <div class="ep-scene-ids">
                      <span class="ep-detail-label">场景({{ ep.scene_ids.length }} 个)</span>
                      <ol class="scene-id-list">
                        <li v-for="sid in ep.scene_ids" :key="sid" class="mono">
                          {{ sid }}
                        </li>
                      </ol>
                    </div>
                  </div>
                </li>
              </ul>
              <p v-else class="empty">该视角无可分集场景</p>
            </div>
          </template>
        </section>

        <!-- 底部说明 + 2026-06-09 P3:保存按钮 -->
        <footer class="epp-ftr" v-if="plan">
          <span class="ftr-meta">
            预设:<strong>{{ plan.preset_label }}</strong> ·
            目标 {{ fmtMin(plan.target_minutes_per_ep) }} 分钟/集 ·
            数据源:{{ plan.bridge_data_source === "sp4_simulation" ? "桥接 SP-4 增强" : "纯规则" }}
          </span>
          <button class="ftr-save-btn" @click="openSaveDialog">
            <svg
              width="14" height="14" viewBox="0 0 24 24" fill="none"
              stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
            >
              <path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z" />
              <polyline points="17 21 17 13 7 13 7 21" />
              <polyline points="7 3 7 8 15 8" />
            </svg>
            保存方案
          </button>
        </footer>

        </div><!-- /epp-tab-content plan -->

        <!-- 2026-06-09 P3:我的方案 tab -->
        <div v-show="mainTab === 'saved'" class="epp-tab-content epp-saved-tab">
          <div v-if="savedPlansLoading" class="epp-saved-loading">加载中…</div>
          <div v-else-if="savedPlans.length === 0" class="epp-saved-empty">
            <p>还没保存任何方案</p>
            <p class="epp-saved-empty-hint">
              切到「生成方案」tab 跑一次分集,跑完后点底部「保存方案」即可
            </p>
          </div>
          <ul v-else class="saved-list">
            <li
              v-for="p in savedPlans"
              :key="p.id"
              class="saved-row"
            >
              <div class="saved-main" @click="openSavedPlan(p.id)">
                <div class="saved-name">{{ p.scheme_name }}</div>
                <div class="saved-meta">
                  <span>{{ p.preset }}</span>
                  <span class="meta-sep">·</span>
                  <span>{{ p.episode_count }} 集 / {{ p.scene_count }} 场</span>
                  <span class="meta-sep">·</span>
                  <span>目标 {{ p.target_minutes }} 分钟</span>
                  <span class="meta-sep">·</span>
                  <span class="mono">{{ p.created_at.slice(0, 10) }}</span>
                </div>
              </div>
              <div class="saved-actions">
                <!-- 2026-06-09 v2:统一导出按钮(弹 dialog 选 format + mode)-->
                <button
                  class="saved-action-btn saved-action-btn--export"
                  :disabled="exportingPlanId === p.id"
                  title="导出方案(选格式 + 模式)"
                  @click="openExportDialog(p)"
                >
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <polyline points="7 10 12 15 17 10" />
                    <line x1="12" y1="15" x2="12" y2="3" />
                  </svg>
                </button>
                <button class="saved-action-btn" @click="openRenameDialog(p)" title="改名">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M12 20h9" />
                    <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z" />
                  </svg>
                </button>
                <button class="saved-action-btn saved-action-btn--danger" @click="deleteSavedPlan(p)" title="删除">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M3 6h18" />
                    <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                    <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                  </svg>
                </button>
              </div>
            </li>
          </ul>
        </div>
      </div>
    </div>

    <!-- 2026-06-09 P3:保存方案 dialog -->
    <div v-if="saveDialogOpen" class="epp-dialog-overlay" @click.self="closeSaveDialog">
      <div class="epp-dialog screenplay-module">
        <h3 class="epp-dialog-title">保存方案</h3>
        <p class="epp-dialog-desc">给这套分集起个名字,方便以后从「我的方案」找回</p>
        <input
          v-model="saveDialogName"
          type="text"
          class="epp-dialog-input"
          maxlength="80"
          placeholder=""
          @keyup.enter="confirmSave"
        />
        <div class="epp-dialog-ftr">
          <button class="btn-cancel" @click="closeSaveDialog" :disabled="saving">取消</button>
          <button class="btn-primary" @click="confirmSave" :disabled="saving">
            {{ saving ? "保存中…" : "保存" }}
          </button>
        </div>
      </div>
    </div>

    <!-- 2026-06-09 P3:改名 dialog(复用保存对话框样式)-->
    <div v-if="renameDialogOpen" class="epp-dialog-overlay" @click.self="closeRenameDialog">
      <div class="epp-dialog screenplay-module">
        <h3 class="epp-dialog-title">方案改名</h3>
        <input
          v-model="renameDialogName"
          type="text"
          class="epp-dialog-input"
          maxlength="80"
          @keyup.enter="confirmRename"
        />
        <div class="epp-dialog-ftr">
          <button class="btn-cancel" @click="closeRenameDialog">取消</button>
          <button class="btn-primary" @click="confirmRename">改名</button>
        </div>
      </div>
    </div>

    <!-- 2026-06-09 v2:导出 dialog — 选 format + mode -->
    <div v-if="exportDialogOpen" class="epp-dialog-overlay" @click.self="closeExportDialog">
      <div class="epp-dialog epp-export-dialog screenplay-module">
        <h3 class="epp-dialog-title">导出方案</h3>
        <p class="epp-dialog-desc">
          {{ exportingPlan?.scheme_name }}
        </p>

        <!-- 模式选择(2 选 1)— 2026-06-09 v3:
             删「仅剧本」(跟顶栏「导出」剧本本体职能重叠);
             文案简化,删 .export-mode-use 提示行 -->
        <div class="export-section-label">导出内容</div>
        <div class="export-mode-list">
          <label class="export-mode-item" :class="{ active: exportMode === 'outline' }">
            <input type="radio" v-model="exportMode" value="outline" />
            <div class="export-mode-main">
              <div class="export-mode-name">仅大纲</div>
              <div class="export-mode-hint">
                <strong>无剧本内容</strong> · 集标题 + logline 概要 + scene ID 列表 + 钩子 + 预告 · 文件最轻
              </div>
            </div>
          </label>
          <label class="export-mode-item" :class="{ active: exportMode === 'full' }">
            <input type="radio" v-model="exportMode" value="full" />
            <div class="export-mode-main">
              <div class="export-mode-name">大纲 + 剧本 <span class="recommended-tag">推荐</span></div>
              <div class="export-mode-hint">
                <strong>大纲 + 剧本全都有</strong> · 每集嵌入对应场景的动作 + 对白 + 集间过渡 logline / 钩子
              </div>
            </div>
          </label>
        </div>

        <!-- 格式选择(横向 3 选 1)-->
        <div class="export-section-label">文件格式</div>
        <div class="export-format-tabs">
          <button
            class="export-format-tab"
            :class="{ active: exportFormat === 'fountain' }"
            @click="exportFormat = 'fountain'"
          >
            .fountain
            <span class="export-format-sub">行业标准</span>
          </button>
          <button
            class="export-format-tab"
            :class="{ active: exportFormat === 'txt' }"
            @click="exportFormat = 'txt'"
          >
            .txt
            <span class="export-format-sub">中文人读</span>
          </button>
          <button
            class="export-format-tab"
            :class="{ active: exportFormat === 'yaml' }"
            @click="exportFormat = 'yaml'"
          >
            .yaml
            <span class="export-format-sub">备份</span>
          </button>
        </div>

        <div class="epp-dialog-ftr">
          <button class="btn-cancel" @click="closeExportDialog" :disabled="exportingPlanId !== null">取消</button>
          <button class="btn-primary" @click="confirmExport" :disabled="exportingPlanId !== null">
            {{ exportingPlanId ? "下载中…" : "下载" }}
          </button>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.epp-overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 16, 12, 0.55);
  backdrop-filter: blur(5px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 200;
}
.epp-panel {
  background: var(--bg);
  border-radius: var(--radius-lg);
  width: 96vw;
  max-width: 1100px;
  height: 92vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  color: var(--text);
}

/* 顶部 */
.epp-hdr {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  padding: 18px 24px 14px;
  border-bottom: 1px solid var(--border-soft);
}
.epp-hdr-left h2 {
  margin: 0 0 4px;
  font-size: 20px;
  font-weight: 500;
  color: var(--text-strong);
}
.epp-sub {
  margin: 0;
  font-size: 11.5px;
  color: var(--text-muted);
  letter-spacing: 0.04em;
}
.epp-close-btn {
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  width: 32px;
  height: 32px;
  cursor: pointer;
  color: var(--text-muted);
  display: flex;
  align-items: center;
  justify-content: center;
}
.epp-close-btn:hover {
  color: var(--text);
  background: var(--hover-bg);
}

/* 控制区 */
.ctrl-row {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 14px 24px;
  border-bottom: 1px solid var(--border-soft);
  background: var(--bg-deep);
}
.ctrl-label {
  font-size: 11.5px;
  color: var(--text-muted);
  letter-spacing: 0.06em;
  margin-bottom: 6px;
  display: inline-flex;
  align-items: baseline;
  gap: 8px;
}
.ctrl-presets .preset-chips {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.preset-chip {
  padding: 6px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--card-bg);
  color: var(--text);
  font-size: 12px;
  cursor: pointer;
  transition: all 150ms;
}
.preset-chip:hover {
  border-color: var(--accent);
  color: var(--accent);
}
.preset-chip.active {
  background: var(--accent);
  border-color: var(--accent);
  color: white;
  font-weight: 500;
}

.ctrl-slider-row {
  display: flex;
  flex-direction: column;
}
.ctrl-value {
  color: var(--accent);
  font-weight: 600;
}
.ctrl-slider {
  width: 100%;
  cursor: pointer;
}

.ctrl-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.ctrl-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--text);
  cursor: pointer;
}
.ctrl-toggle input {
  cursor: pointer;
}

.run-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: all 150ms;
}
.run-btn:hover:not(:disabled) {
  background: var(--accent-hover);
}
.run-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

@keyframes spin-rotate {
  to { transform: rotate(360deg); }
}
.spin {
  animation: spin-rotate 1s linear infinite;
}

/* 主体 */
.epp-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 24px;
}

/* 加载 */
.state-msg {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 200px;
  gap: 12px;
}
.loading-pulse {
  width: 60px;
  height: 4px;
  background: var(--accent);
  border-radius: 2px;
  animation: pulse-x 1.5s ease-in-out infinite;
}
@keyframes pulse-x {
  0%, 100% { transform: scaleX(0.3); opacity: 0.5; }
  50% { transform: scaleX(1); opacity: 1; }
}
.loading-text {
  font-size: 12px;
  color: var(--text-muted);
  letter-spacing: 0.06em;
}

/* 占位 */
.placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 280px;
  text-align: center;
  color: var(--text-muted);
}
.placeholder-icon {
  opacity: 0.3;
  margin-bottom: 14px;
}
.placeholder-text {
  font-size: 14px;
  color: var(--text);
  margin: 0 0 6px;
}
.placeholder-hint {
  font-size: 11.5px;
  color: var(--text-muted);
  margin: 0;
  max-width: 360px;
  line-height: 1.6;
}

/* 桥接 banner */
.bridge-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 14px;
  background: var(--accent-soft);
  border: 1px solid var(--accent);
  border-radius: var(--radius-md);
  margin-bottom: 14px;
  color: var(--accent-text);
  font-size: 12px;
}
.bridge-text strong {
  font-weight: 600;
}

/* tension 曲线 */
.tension-section {
  margin-bottom: 16px;
  padding: 12px 14px;
  background: var(--bg-deep);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
}
.tension-title {
  display: flex;
  justify-content: space-between;
  font-size: 11.5px;
  color: var(--text-muted);
  letter-spacing: 0.06em;
  margin-bottom: 8px;
}
.tension-meta {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-muted);
}
.tension-svg {
  width: 100%;
  height: 60px;
  display: block;
}

/* perspective tabs */
.persp-tabs {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border-soft);
  margin-bottom: 14px;
}
.persp-tab {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 10px 16px;
  background: transparent;
  border: none;
  color: var(--text-muted);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  position: relative;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  transition: all 150ms;
}
.persp-tab:hover {
  color: var(--text);
}
.persp-tab.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
}
.persp-tab.recommended {
  /* recommend 标志由 crown emoji + score 凸显 */
}
.persp-tab-crown {
  font-size: 14px;
}
.persp-tab-score {
  font-size: 11px;
  font-weight: 700;
  padding: 1px 6px;
  background: var(--card-bg);
  border-radius: 8px;
  color: var(--text);
}
.persp-tab.active .persp-tab-score {
  background: var(--accent-soft);
  color: var(--accent-text);
}

/* perspective content */
.persp-rationale {
  font-size: 12.5px;
  color: var(--text);
  line-height: 1.6;
  margin: 0 0 12px;
  padding-left: 12px;
  border-left: 3px solid var(--accent);
}
.llm-failed-warn {
  padding: 8px 12px;
  background: var(--danger-soft);
  border-left: 3px solid var(--danger);
  color: var(--danger);
  font-size: 11.5px;
  margin-bottom: 12px;
  border-radius: var(--radius-sm);
}
/* 2026-06-09:视角"碰巧推荐相同切点"的友好信息提示 */
.persp-match-info {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 12px;
  background: var(--accent-soft);
  border-left: 3px solid var(--accent);
  color: var(--accent-text);
  font-size: 11.5px;
  line-height: 1.55;
  margin-bottom: 12px;
  border-radius: var(--radius-sm);
}
.persp-match-info svg {
  flex-shrink: 0;
  margin-top: 1px;
}

/* quality card */
.quality-card {
  background: var(--bg-deep);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  padding: 12px 14px;
  margin-bottom: 16px;
}
.quality-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 0;
  font-size: 12px;
}
.quality-aggregate {
  border-bottom: 1px solid var(--border-soft);
  padding-bottom: 8px;
  margin-bottom: 8px;
}
.quality-aggregate .quality-label,
.quality-aggregate .quality-num {
  font-weight: 700;
  color: var(--text-strong);
}
.quality-dims {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 4px 16px;
}
.quality-label {
  flex: 0 0 90px;
  color: var(--text-muted);
  font-size: 11.5px;
}
.quality-bar-wrap {
  flex: 1;
  height: 6px;
  background: var(--card-bg);
  border-radius: 3px;
  overflow: hidden;
}
.quality-bar {
  height: 100%;
  background: var(--accent);
  border-radius: 3px;
  transition: width 600ms ease-out;
}
.quality-bar-agg {
  background: linear-gradient(90deg, var(--accent), var(--accent-hover));
}
.quality-num {
  font-size: 11.5px;
  font-weight: 600;
  color: var(--accent-text);
  min-width: 24px;
  text-align: right;
}
.quality-summary {
  margin: 10px 0 0;
  font-size: 11.5px;
  color: var(--text-muted);
  line-height: 1.6;
  font-style: italic;
}

/* 集卡列表 */
.ep-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.ep-card {
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  background: var(--card-bg);
  transition: border-color 150ms;
}
.ep-card:hover {
  border-color: var(--border);
}
.ep-card.qual-high {
  border-left: 3px solid var(--success);
}
.ep-card.qual-mid {
  border-left: 3px solid var(--accent);
}
.ep-card.qual-low {
  border-left: 3px solid var(--danger);
}
.ep-hdr {
  display: grid;
  grid-template-columns: 1fr auto auto;
  gap: 14px;
  padding: 12px 14px;
  cursor: pointer;
  align-items: center;
}
.ep-main {
  min-width: 0;
}
.ep-title-row {
  display: flex;
  align-items: baseline;
  gap: 8px;
}
.ep-num {
  font-size: 11px;
  font-weight: 700;
  color: var(--accent);
  white-space: nowrap;
}
.ep-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-strong);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.ep-teaser {
  margin-top: 4px;
  font-size: 11.5px;
  color: var(--text-muted);
  line-height: 1.5;
  font-style: italic;
}
.teaser-prefix {
  color: var(--accent);
  font-style: normal;
  font-weight: 500;
}

.ep-stats {
  display: flex;
  flex-direction: column;
  gap: 6px;
  align-items: flex-end;
}
.ep-stat-row {
  display: flex;
  gap: 4px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
.meta-pill {
  display: inline-block;
  padding: 1px 8px;
  font-size: 10.5px;
  border-radius: 10px;
  background: var(--code-bg);
  color: var(--text-muted);
  font-family: var(--font-mono);
}
.meta-duration {
  background: var(--accent-soft);
  color: var(--accent-text);
}
.ep-bars {
  display: flex;
  gap: 8px;
  align-items: center;
}
.cliff-bar-wrap, .qual-bar-wrap {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.cliff-bar-label, .qual-bar-label {
  font-size: 9.5px;
  color: var(--text-muted);
  font-weight: 600;
  letter-spacing: 0.04em;
}
.cliff-bar-track, .qual-bar-track {
  width: 60px;
  height: 4px;
  background: var(--bg-deep);
  border-radius: 2px;
  overflow: hidden;
}
.cliff-bar-fill {
  height: 100%;
  background: linear-gradient(90deg, var(--accent), var(--accent-hover));
  border-radius: 2px;
}
.qual-bar-fill {
  height: 100%;
  background: var(--success);
  border-radius: 2px;
}

.ep-chev {
  color: var(--text-muted);
  transition: transform 200ms;
}
.ep-chev.open {
  transform: rotate(180deg);
}

/* 集详情 */
.ep-detail {
  padding: 12px 14px;
  border-top: 1px solid var(--border-soft);
  background: var(--bg-deep);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.ep-detail-label {
  display: inline-block;
  font-size: 10.5px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.08em;
  margin-bottom: 4px;
  font-weight: 600;
}
.ep-summary p, .ep-tension-info p {
  margin: 0;
  font-size: 12px;
  color: var(--text);
  line-height: 1.6;
}
.scene-id-list {
  list-style: none;
  margin: 4px 0 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}
.scene-id-list li {
  padding: 2px 8px;
  background: var(--card-bg);
  border-radius: 4px;
  font-size: 11px;
  color: var(--text-muted);
}

.empty {
  text-align: center;
  color: var(--text-muted);
  padding: 40px 0;
  font-size: 12px;
}

/* footer */
.epp-ftr {
  padding: 10px 24px;
  border-top: 1px solid var(--border-soft);
  background: var(--bg-deep);
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 0.04em;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.ftr-meta strong {
  color: var(--text);
  font-weight: 600;
}
/* 2026-06-09 P3:保存按钮 */
.ftr-save-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 14px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 12px;
  font-weight: 500;
  letter-spacing: 0.04em;
  cursor: pointer;
  transition: all var(--transition-fast);
}
.ftr-save-btn:hover {
  background: var(--accent-hover);
}

/* 2026-06-09 P3:主 tab 切换条 */
.epp-maintab-row {
  display: flex;
  gap: 4px;
  padding: 0 24px;
  border-bottom: 1px solid var(--border-soft);
  background: var(--card-bg);
}
.epp-maintab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 10px 18px;
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  font-size: 12.5px;
  color: var(--text-muted);
  cursor: pointer;
  transition: all var(--transition-fast);
  margin-bottom: -1px;
}
.epp-maintab:hover {
  color: var(--text);
}
.epp-maintab.active {
  color: var(--accent);
  border-bottom-color: var(--accent);
  font-weight: 500;
}
.epp-maintab-count {
  font-size: 10.5px;
  padding: 1px 6px;
  background: var(--accent-soft);
  color: var(--accent-text);
  border-radius: 9px;
}

/* 2026-06-09 P3 v2 bug fix:
 *   旧版用 display: contents + !important 把 v-show 的 inline display:none
 *   覆盖了 → saved tab 在 plan tab 也显示;footer 跨 tab 渗透。
 *   修法:tab content 用 flex column,v-show 正常切换 display 属性,
 *   不用任何 !important / contents hack。
 */
.epp-tab-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  /* 子元素 ctrl-row / persp-tabs / persp-content / footer 按自身规则布局 */
}

/* 我的方案 tab — display block 覆盖 .epp-tab-content 的 flex,内部子项按
 * 普通 document flow 排,加 overflow + padding。 */
.epp-saved-tab {
  display: block;
  overflow-y: auto;
  padding: 20px 24px;
}
.epp-saved-loading,
.epp-saved-empty {
  text-align: center;
  padding: 60px 20px;
  color: var(--text-muted);
  font-size: 13px;
  line-height: 1.7;
}
.epp-saved-empty-hint {
  font-size: 11.5px;
  color: var(--text-subtle);
  margin-top: 6px;
  font-style: italic;
}

.saved-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.saved-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 16px;
  background: var(--card-bg);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  transition: all var(--transition-fast);
}
.saved-row:hover {
  border-color: var(--accent-border);
  background: var(--accent-soft);
}
.saved-main {
  flex: 1;
  min-width: 0;
  cursor: pointer;
}
.saved-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
  margin-bottom: 4px;
}
.saved-meta {
  font-size: 11px;
  color: var(--text-muted);
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  letter-spacing: 0.04em;
}
.meta-sep {
  color: var(--text-subtle);
}
.saved-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
.saved-action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  background: transparent;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  color: var(--text-muted);
  cursor: pointer;
  transition: all var(--transition-fast);
}
.saved-action-btn:hover {
  background: var(--hover-bg);
  color: var(--text);
}
.saved-action-btn--danger:hover {
  background: var(--danger-soft);
  color: var(--danger);
  border-color: var(--danger);
}
/* 2026-06-09 v2:导出 hover 紫 */
.saved-action-btn--export:hover:not(:disabled) {
  background: var(--accent-soft);
  color: var(--accent);
  border-color: var(--accent-border);
}

/* 2026-06-09 v2:导出 dialog */
.epp-export-dialog {
  max-width: 480px;
}
.export-section-label {
  margin: 16px 0 8px;
  font-size: 11px;
  font-weight: 600;
  color: var(--text-muted);
  letter-spacing: 0.08em;
  text-transform: uppercase;
}
.export-mode-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.export-mode-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
}
.export-mode-item:hover {
  border-color: var(--accent-border);
}
.export-mode-item.active {
  background: var(--accent-soft);
  border-color: var(--accent);
}
.export-mode-item input[type="radio"] {
  margin: 3px 0 0 0;
  accent-color: var(--accent);
}
.export-mode-main {
  flex: 1;
  min-width: 0;
}
.export-mode-name {
  font-size: 13px;
  font-weight: 500;
  color: var(--text);
  margin-bottom: 2px;
}
.recommended-tag {
  display: inline-block;
  margin-left: 4px;
  padding: 1px 6px;
  font-size: 10px;
  background: var(--accent);
  color: white;
  border-radius: 9px;
  font-weight: 500;
  letter-spacing: 0.04em;
}
.export-mode-hint {
  font-size: 11px;
  color: var(--text-muted);
  line-height: 1.4;
}

.export-format-tabs {
  display: flex;
  gap: 6px;
}
.export-format-tab {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 10px 12px;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text);
  cursor: pointer;
  transition: all var(--transition-fast);
}
.export-format-tab:hover {
  border-color: var(--accent-border);
}
.export-format-tab.active {
  background: var(--accent-soft);
  border-color: var(--accent);
  color: var(--accent-text);
}
.export-format-sub {
  font-family: var(--font-sans);
  font-size: 10px;
  color: var(--text-muted);
}
.export-format-tab.active .export-format-sub {
  color: var(--accent-text);
}

/* 2026-06-09 P3:保存 / 改名对话框 */
.epp-dialog-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.45);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10001;
}
.epp-dialog {
  width: 100%;
  max-width: 420px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  padding: 24px;
  box-shadow: var(--shadow-lg);
}
.epp-dialog-title {
  margin: 0 0 8px;
  font-size: 16px;
  font-weight: 600;
  color: var(--text);
  font-family: var(--font-serif);
}
.epp-dialog-desc {
  margin: 0 0 14px;
  font-size: 12px;
  color: var(--text-muted);
  line-height: 1.5;
}
.epp-dialog-input {
  width: 100%;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: var(--bg);
  color: var(--text);
  font-size: 13px;
  font-family: inherit;
  margin-bottom: 16px;
  box-sizing: border-box;
}
.epp-dialog-input:focus {
  outline: none;
  border-color: var(--accent);
}
.epp-dialog-ftr {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
}
.epp-dialog-ftr .btn-cancel,
.epp-dialog-ftr .btn-primary {
  padding: 7px 18px;
  border-radius: var(--radius-md);
  font-size: 12.5px;
  font-weight: 500;
  letter-spacing: 0.04em;
  cursor: pointer;
  transition: all var(--transition-fast);
  border: 1px solid var(--border);
}
.epp-dialog-ftr .btn-cancel {
  background: var(--card-bg);
  color: var(--text);
}
.epp-dialog-ftr .btn-cancel:hover:not(:disabled) {
  background: var(--hover-bg);
}
.epp-dialog-ftr .btn-primary {
  background: var(--accent);
  border-color: var(--accent);
  color: white;
}
.epp-dialog-ftr .btn-primary:hover:not(:disabled) {
  background: var(--accent-hover);
}
.epp-dialog-ftr button:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.mono {
  font-family: var(--font-mono);
}
</style>
