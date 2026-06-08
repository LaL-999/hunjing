<script setup lang="ts">
/**
 * SimulationsListPanel — ProjectView 的"作品列表"Tab 主组件。
 *
 * Sprint 6.A2 M7.D-fix(2026-05-20):
 *   用户进项目后默认看到的是该项目下的全部推演产物(sim 列表),不是图谱编辑面。
 *   每条 sim 卡片显示:重塑% / 进度 / 第 N 代接续 badge / 状态 / 时间 / divergence 摘要。
 *
 * 数据:onMounted 调 GET /api/projects/{id}/simulations(已带 inheritance 字段)。
 *      不轮询 — 列表无需实时(state=running 时用户在 SimulationDetailView 看实时)。
 *
 * 点击 sim 卡片 → router push /simulations/{id}
 * 空态:引导用户点右上"AI 续写"按钮(顶栏)开始第一篇
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api } from "../api/client";
import {
  ApiError,
  type SimulationSummary,
} from "../api/types";
import { useEventBus } from "../stores/events";
import SkeletonBlock from "./SkeletonBlock.vue";
import Icon from "./Icon.vue";

const props = defineProps<{
  projectId: string;
  /** M7.I(2026-05-20):是否允许创作 — false 时禁 CTA(角色未抽完 / 数量不足等);
   *  父按 readyToSimulate 注入,避免在用户准备好之前误导点击 */
  canCreate?: boolean;
  /** M7.I:按 project.mode 派生的按钮文案(同顶栏:AI 续写 / AI 重塑 / AI 长篇)
   *  2026-06-08 UI 大升级:ctaIcon 改为 Icon 组件 name(如 "spark" / "compass"),
   *  默认 "spark"。原字符串 emoji "✦" 形式已废弃 */
  ctaIcon?: string;
  ctaText?: string;
}>();

const emit = defineEmits<{
  /** M7.I:用户点空态 CTA / 列表顶"+ 新建"入口 → 父打开 SimulationDock */
  (e: "start-create"): void;
}>();

const router = useRouter();

const allowCreate = computed(() => props.canCreate !== false);
// 2026-06-08 UI 升级:ctaIcon 改 SVG icon name(默认 "spark"),旧 emoji "✦" 废弃
const ctaIcon = computed(() => props.ctaIcon || "spark");
const ctaText = computed(() => props.ctaText || "AI 推演");

const simulations = ref<SimulationSummary[]>([]);
const loading = ref(true);
const errorMessage = ref<string | null>(null);

// Bug 修复(2026-05-22):违反 CLAUDE.md 开发避坑铁律 #1 —
// 原 `onMounted(load)` 只首挂时跑;Vue Router 切 /projects/A → /projects/B 时 ProjectView
// 实例复用,本组件 props.projectId 变了但 load 不会再触发 → 用户切项目后作品列表仍显旧数据,
// 必须切到图谱编辑 tab 再切回(v-if 切换让本组件 unmount/remount)才刷新。
// 修法:改 watch(projectId) + immediate;并加 stale-while-revalidate(切项目时保留旧列表无缝替换)
// 2026-06-02 hotfix R3:stale-while-revalidate 仅适用于"同项目刷新",
// **切项目时**必须清空旧列表(那是别的项目的,不是过期数据)
// 用 lastLoadedProjectId 区分两种场景
const hasLoadedOnce = ref(false);
const lastLoadedProjectId = ref<string | null>(null);

async function load() {
  const targetPid = props.projectId;
  const isSwitchProject = lastLoadedProjectId.value !== targetPid;
  if (isSwitchProject) {
    // 切项目场景:清空旧列表 + 显 loading(避免用户看到别项目的 sim)
    simulations.value = [];
    loading.value = true;
  } else if (!hasLoadedOnce.value) {
    loading.value = true;
  }
  errorMessage.value = null;
  try {
    const fresh = await api.get<SimulationSummary[]>(
      `/projects/${targetPid}/simulations`,
    );
    // 防 race:用户快速连切项目时,只接受最新一次拉取结果
    if (props.projectId === targetPid) {
      simulations.value = fresh;
      lastLoadedProjectId.value = targetPid;
    }
  } catch (e) {
    errorMessage.value =
      e instanceof ApiError ? e.message : "加载作品列表失败";
    // 同项目失败 → 保留旧列表(stale-while-revalidate);切项目失败 → 上面已清空
  } finally {
    loading.value = false;
    hasLoadedOnce.value = true;
  }
}

watch(() => props.projectId, load, { immediate: true });

// 2026-06-01:实时更新 — 监听全局 sim 事件,本项目相关时 reload
// 治用户报告"推演完成后作品列表不更新需手刷新页"
// 2026-06-02 hotfix:订阅 + visibility 注册全部移到 onMounted,避免 setup 边界异常
function onSimChanged(payload: { sim_id: string; project_id?: string | null }) {
  // project_id 匹配本组件 props.projectId(或 payload 没传 project_id 时无差别 reload)
  if (!payload.project_id || payload.project_id === props.projectId) {
    void load();
  }
}
function onVisibilityChange() {
  if (document.visibilityState === "visible") {
    void load();
  }
}
const _eventUnsubs: Array<() => void> = [];
onMounted(() => {
  try {
    const events = useEventBus();
    _eventUnsubs.push(events.on("sim:done", onSimChanged));
    _eventUnsubs.push(events.on("sim:created", onSimChanged));
    _eventUnsubs.push(events.on("sim:deleted", onSimChanged));
    _eventUnsubs.push(events.on("sim:failed", onSimChanged));
    _eventUnsubs.push(events.on("sim:cancelled", onSimChanged));
  } catch (e) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn("[SimulationsListPanel] event bus subscription failed:", e);
    }
  }
  document.addEventListener("visibilitychange", onVisibilityChange);
});
onBeforeUnmount(() => {
  for (const unsub of _eventUnsubs) {
    try { unsub(); } catch { /* noop */ }
  }
  document.removeEventListener("visibilitychange", onVisibilityChange);
});

function goToSim(simId: string) {
  router.push(`/simulations/${simId}`);
}

// ============================================================
// SP-9(2026-05-29):反事实分支并排对比 — 对比模式
// 用户开启 compareMode → 卡片可勾选(仅 done 状态)→ 选 2 个跳 compare 路由
// ============================================================
const compareMode = ref(false);
const selectedSimIds = ref<Set<string>>(new Set());

function toggleCompareMode() {
  compareMode.value = !compareMode.value;
  if (!compareMode.value) selectedSimIds.value.clear();
}

function toggleSelected(simId: string) {
  if (!compareMode.value) return;
  const next = new Set(selectedSimIds.value);
  if (next.has(simId)) {
    next.delete(simId);
  } else {
    // 最多 2 个
    if (next.size >= 2) {
      // 移除最早加入的(简单 FIFO:转 array)
      const first = Array.from(next)[0];
      next.delete(first);
    }
    next.add(simId);
  }
  selectedSimIds.value = next;
}

function isSelected(simId: string): boolean {
  return selectedSimIds.value.has(simId);
}

const canStartCompare = computed(() => selectedSimIds.value.size === 2);

function startCompare() {
  if (!canStartCompare.value) return;
  const [a, b] = Array.from(selectedSimIds.value);
  router.push({
    path: `/projects/${props.projectId}/compare`,
    query: { a, b },
  });
}

function isComparable(sim: SimulationSummary): boolean {
  // 只允许 done 状态参与对比(其他状态 narrative 不全)
  return sim.state === "done";
}

function onSimClick(sim: SimulationSummary) {
  if (compareMode.value) {
    if (isComparable(sim)) toggleSelected(sim.id);
    return;
  }
  goToSim(sim.id);
}

const STATE_LABEL: Record<string, string> = {
  queued: "排队中",
  directing: "创作中",
  composing: "编织中",
  done: "完成",
  failed: "失败",
  cancelled: "已取消",
};

// hotfix(2026-06-01):续写模式 chip — "快速 / 灵魂 / 灵魂·outline" 不显眼标识
function modeLabel(sim: SimulationSummary): string {
  if (sim.mode === "evolution") {
    return sim.use_outline_first ? "灵魂·outline" : "灵魂";
  }
  return "快速";
}
function modeTitle(sim: SimulationSummary): string {
  if (sim.mode === "evolution") {
    return sim.use_outline_first
      ? "灵魂续写 outline 模式 — LLM 先生成大纲再逐幕生成,可审阅大纲"
      : "灵魂续写模式 — LLM 逐幕生成,产 simulation_scenes 表数据,可逐幕审阅 / 反事实并排对比";
  }
  return "快速模式 — LLM 一次性生成整篇 narrative,不分幕,反事实并排对比无法用";
}

function stateChipClass(state: string): string {
  if (state === "done") return "chip-done";
  if (state === "failed" || state === "cancelled") return "chip-failed";
  return "chip-running";
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  if (sameDay) return `今天 ${hh}:${mm}`;
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${d.getFullYear()}-${m}-${day} ${hh}:${mm}`;
}

/** M7.D 接续 badge 完整链 tooltip(给 title 属性) */
function inheritanceTooltip(sim: SimulationSummary): string {
  if (!sim.inheritance_depth) return "";
  const lines: string[] = ["继承链:"];
  for (const node of sim.ancestors_chain) {
    const label =
      node.depth === 0 ? "① 原作" : `${depthSymbol(node.depth + 1)} 第 ${node.depth} 代`;
    lines.push(`${label} 《${node.divergence_short || "(无锚点摘要)"}》`);
  }
  lines.push(
    `${depthSymbol(sim.inheritance_depth + 1)} 当前(第 ${sim.inheritance_depth} 代)`,
  );
  return lines.join("\n");
}

function depthSymbol(n: number): string {
  const symbols = ["①", "②", "③", "④", "⑤", "⑥", "⑦", "⑧", "⑨", "⑩"];
  return symbols[n - 1] ?? `${n}.`;
}
</script>

<template>
  <div class="sims-panel">
    <!-- loading -->
    <div v-if="loading" class="sims-loading">
      <SkeletonBlock v-for="i in 3" :key="i" height="80px" rounded="md" />
    </div>

    <!-- error -->
    <p v-else-if="errorMessage" class="error-banner">
      {{ errorMessage }}
      <button class="x-btn" @click="load">重试</button>
    </p>

    <!-- 空态:M7.I(2026-05-20)显眼 CTA,而非引导用户找右上角小按钮
         UI 优化(2026-05-21):删除冗余引导文案"AI 已读完原作 / 基于原作生成..." —
         "还没有推演产物" + CTA 按钮已自足,中间两行属废话填充
         2026-06-08 UI 大升级:○ ✦ → SVG icon,质感统一剧创态 -->
    <div v-else-if="simulations.length === 0" class="sims-empty">
      <Icon name="circle" :size="44" class="empty-icon-svg" />
      <h2 class="empty-title">还没有推演产物</h2>
      <button
        v-if="allowCreate"
        type="button"
        class="empty-cta"
        @click="emit('start-create')"
      >
        <Icon :name="ctaIcon" :size="14" />
        <span>{{ ctaText }}</span>
        <Icon name="arrow_right" :size="14" />
      </button>
      <p v-else class="empty-block-hint">
        请先上传作品并抽取角色 / 关系 / 事件图谱
      </p>
    </div>

    <!-- sim 列表 -->
    <template v-else>
      <!-- Sprint 6.A2 polish(2026-05-23):移除冗余 "AI 续写" 入口 — 顶部 header 已有同一按钮
           保留 list-count 让用户对当前数量有感知 -->
      <div v-if="allowCreate" class="list-header">
        <span class="list-count">共 {{ simulations.length }} 篇推演</span>
        <!-- SP-9(2026-05-29):反事实分支并排对比入口 -->
        <div class="compare-controls">
          <button
            v-if="!compareMode"
            type="button"
            class="compare-toggle-btn"
            :disabled="simulations.filter(isComparable).length < 2"
            :title="simulations.filter(isComparable).length < 2 ? `需要至少 2 篇已完成推演才能对比` : `开启对比模式后,勾选 2 篇查看分支差异`"
            @click="toggleCompareMode"
          >
            <Icon name="swap" :size="14" />
            对比分支
          </button>
          <template v-else>
            <span class="compare-hint mono">
              已选 {{ selectedSimIds.size }} / 2
            </span>
            <button
              type="button"
              class="compare-go-btn"
              :disabled="!canStartCompare"
              @click="startCompare"
            >开始对比</button>
            <button
              type="button"
              class="compare-cancel-btn"
              @click="toggleCompareMode"
            >取消</button>
          </template>
        </div>
      </div>
    <!-- Sprint 6.A2 polish(2026-05-22):TransitionGroup 推演增删平滑 stagger -->
    <TransitionGroup tag="ul" name="list-stagger" class="sim-list">
      <li
        v-for="sim in simulations"
        :key="sim.id"
        class="sim-card"
        :class="{
          'sim-card--failed': sim.state === 'failed',
          'sim-card--compare-mode': compareMode,
          'sim-card--compare-selected': compareMode && isSelected(sim.id),
          'sim-card--compare-disabled': compareMode && !isComparable(sim),
          'sim-card--final-compilation': sim.is_final_compilation,
        }"
        tabindex="0"
        role="button"
        :aria-label="compareMode ? `选择推演加入对比:${sim.divergence}` : `查看推演:${sim.divergence}`"
        @click="onSimClick(sim)"
        @keydown.enter="onSimClick(sim)"
        @keydown.space.prevent="onSimClick(sim)"
      >
        <span
          v-if="compareMode"
          class="compare-checkbox"
          :class="{ checked: isSelected(sim.id), disabled: !isComparable(sim) }"
          aria-hidden="true"
        >
          <Icon v-if="isSelected(sim.id)" name="check" :size="12" />
        </span>
        <!-- 2026-06-01 v2:独立合并最终作品卡片(amber 精简版,不显示重塑/轮/¥)-->
        <template v-if="sim.is_final_compilation">
          <div class="sim-card-head">
            <span class="final-compilation-badge">
              <Icon name="book_open" :size="14" />
              最终作品
            </span>
            <span class="final-compilation-subtitle">
              自动合并 {{ (sim.context_simulation_ids?.length || 1) }} 篇
            </span>
            <span class="state-chip" :class="stateChipClass(sim.state)">
              {{ STATE_LABEL[sim.state] ?? sim.state }}
            </span>
            <span class="sim-time mono">{{ formatTime(sim.created_at) }}</span>
          </div>
          <p class="sim-divergence">{{ sim.divergence }}</p>
        </template>
        <template v-else>
          <div class="sim-card-head">
            <span class="meta-item">
              <span class="meta-num mono">{{ sim.reshape_percent }}%</span>
              <span class="meta-label">重塑度</span>
            </span>
            <span class="meta-sep">·</span>
            <span class="meta-item">
              <span class="meta-num mono">
                {{ sim.current_round }}/{{ sim.rounds_planned }}
              </span>
              <span class="meta-label">轮</span>
            </span>
            <span class="meta-sep">·</span>
            <span class="meta-item">
              <span class="meta-label">¥</span>
              <span class="meta-num mono">{{ sim.cost_yuan.toFixed(4) }}</span>
            </span>
            <!-- M7.D 第 N 代接续 badge + tooltip 完整链
                 2026-06-08 UI 升级:📖 emoji → Icon book line svg -->
            <span
              v-if="sim.inheritance_depth > 0"
              class="inheritance-chip"
              :title="inheritanceTooltip(sim)"
            >
              <Icon name="book" :size="12" />
              第 {{ sim.inheritance_depth }} 代接续
            </span>
            <!-- hotfix(2026-06-01):续写模式 chip — 不显眼区分 3 种模式 -->
            <span class="mode-chip" :title="modeTitle(sim)">{{ modeLabel(sim) }}</span>
            <span class="state-chip" :class="stateChipClass(sim.state)">
              {{ STATE_LABEL[sim.state] ?? sim.state }}
            </span>
            <span class="sim-time mono">{{ formatTime(sim.created_at) }}</span>
          </div>
          <p class="sim-divergence">{{ sim.divergence }}</p>
        </template>
      </li>
    </TransitionGroup>
    </template>
  </div>
</template>

<style scoped>
.sims-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* loading 骨架 */
.sims-loading {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* 空态 */
.sims-empty {
  padding: var(--space-12) var(--space-4);
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  background: var(--color-bg-subtle);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-lg);
}
/* 2026-06-08 UI 升级:旧 .empty-icon(unicode ○ 字符大字号)废弃 */
.empty-icon-svg {
  color: var(--color-text-subtle);
  opacity: 0.5;
  stroke-width: 1.2 !important;  /* 空态图标用更细的描边,更克制 */
}
.empty-title {
  font-size: var(--text-lg);
  font-weight: 500;
  color: var(--color-text);
  margin: 0;
}
/* M7.I(2026-05-20)空态显眼 CTA */
.empty-cta {
  margin-top: var(--space-3);
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-6);
  font-size: var(--text-base);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: none;
  border-radius: var(--radius-lg);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
  box-shadow: 0 1px 4px rgba(124, 58, 237, 0.18);
}
.empty-cta:hover {
  background: var(--color-accent-hover);
  transform: translateY(-1px);
  box-shadow: 0 2px 8px rgba(124, 58, 237, 0.24);
}
/* 2026-06-08 UI 升级:cta-icon / cta-arrow 由 Icon 组件接管,样式留作微调
 * Icon 组件 .huimeng-icon 已自带 flex-shrink + vertical-align */
.empty-block-hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-style: italic;
  margin: var(--space-2) 0 0;
}

/* M7.I(2026-05-20)列表上方"+ 新建推演"轻量入口 */
.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
  padding-bottom: var(--space-2);
  margin-bottom: var(--space-1);
  border-bottom: 1px solid var(--color-border-subtle);
}

/* SP-9(2026-05-29):反事实分支对比控制条 */
.compare-controls {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
.compare-toggle-btn,
.compare-go-btn,
.compare-cancel-btn {
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.compare-toggle-btn {
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
}
.compare-toggle-btn:hover:not(:disabled) {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent-border);
}
.compare-toggle-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.compare-hint {
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  padding: 1px var(--space-2);
  border-radius: var(--radius-sm);
}
.compare-go-btn {
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: 1px solid var(--color-accent);
}
.compare-go-btn:hover:not(:disabled) { background: var(--color-accent-hover); }
.compare-go-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.compare-cancel-btn {
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
}
.compare-cancel-btn:hover { color: var(--color-text); }

/* SP-9:sim-card 在对比模式下加 checkbox 视觉 */
.sim-card--compare-mode {
  display: flex;
  align-items: stretch;
  gap: var(--space-3);
}
.sim-card--compare-selected {
  background: var(--color-accent-soft);
  border-color: var(--color-accent-border);
}
.sim-card--compare-disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.compare-checkbox {
  flex-shrink: 0;
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1.5px solid var(--color-border-strong);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-accent-text);
  font-weight: 600;
  font-size: var(--text-sm);
  margin-top: 2px;
}
.compare-checkbox.checked {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}
.compare-checkbox.disabled {
  background: var(--color-bg-subtle);
  color: var(--color-text-subtle);
  border-color: var(--color-border);
}

.list-count {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}

/* sim 列表 */
.sim-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.sim-card {
  padding: var(--space-4) var(--space-5);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  outline: none;
  transition:
    border-color var(--duration-fast) var(--ease-out),
    box-shadow var(--duration-fast) var(--ease-out);
  content-visibility: auto;
  contain-intrinsic-size: auto 100px;
}
.sim-card:hover,
.sim-card:focus {
  border-color: var(--color-accent-border);
  box-shadow: 0 1px 6px rgba(0, 0, 0, 0.04);
}
.sim-card:focus-visible {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.12);
}
.sim-card--failed {
  border-color: var(--color-danger-soft);
  background: rgba(220, 38, 38, 0.02);
}

.sim-card-head {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.meta-item {
  display: inline-flex;
  align-items: baseline;
  gap: 2px;
}
.meta-num {
  color: var(--color-text);
}
.meta-label {
  color: var(--color-text-subtle);
}
.meta-sep {
  color: var(--color-text-subtle);
}
.sim-time {
  margin-left: auto;
  color: var(--color-text-subtle);
}

.state-chip {
  flex-shrink: 0;
  padding: 1px var(--space-2);
  font-size: var(--text-xs);
  border-radius: var(--radius-sm);
  white-space: nowrap;
}
/* hotfix(2026-06-01):续写模式 chip — 不显眼,纯灰底淡字 */
.mode-chip {
  flex-shrink: 0;
  padding: 1px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  white-space: nowrap;
  cursor: help;
}
/* 2026-06-01 v2:独立合并最终作品 — 卡片整体 amber 配色 + 大 badge */
.sim-card--final-compilation {
  background: linear-gradient(135deg, #fffbeb 0%, #fef3c7 100%);
  border-left: 3px solid #f59e0b;
}
.sim-card--final-compilation:hover {
  background: linear-gradient(135deg, #fef3c7 0%, #fde68a 100%);
}
.final-compilation-badge {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px var(--space-3);
  font-size: var(--text-sm);
  font-weight: 600;
  color: #b45309;
  background: #fef3c7;
  border: 1px solid #fbbf24;
  border-radius: var(--radius-md);
  white-space: nowrap;
}
.final-compilation-subtitle {
  font-size: var(--text-xs);
  color: #92400e;
  white-space: nowrap;
  margin-left: var(--space-1);
}
.chip-done {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
}
.chip-running {
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
}
.chip-failed {
  color: var(--color-danger);
  background: var(--color-danger-soft);
}

.inheritance-chip {
  flex-shrink: 0;
  padding: 1px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: rgba(124, 58, 237, 0.08);
  border: 1px dashed var(--color-accent-border);
  border-radius: var(--radius-sm);
  white-space: nowrap;
  cursor: help;
}

.sim-divergence {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  font-style: italic;
  line-height: 1.55;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
  word-break: break-word;
  margin: 0;
}

/* error */
.error-banner {
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-radius: var(--radius-md);
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.x-btn {
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-danger);
  background: transparent;
  border: 1px solid var(--color-danger);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.x-btn:hover {
  background: rgba(220, 38, 38, 0.08);
}

.mono {
  font-family: var(--font-mono);
}
</style>
