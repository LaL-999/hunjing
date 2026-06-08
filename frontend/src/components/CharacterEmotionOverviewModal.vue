<script setup lang="ts">
/**
 * CharacterEmotionOverviewModal — 角色跨多次推演情绪总览模态(Sprint 6.A2 #2 二期 2026-05-22)
 *
 * 入口:ProjectView 角色卡展开 "📊 情绪总览" 按钮
 * 数据:GET /api/projects/:pid/characters/:cid/emotional_states
 *      返回 CharacterEmotionsBySim[](按 sim.created_at DESC 排序,records 内 scene_index ASC)
 *
 * UX:
 *   - 顶部 chip 列表 = 每次推演一个 chip(时间 + 锚点摘要 + 幕数)
 *   - 默认选第 1 个(最新推演)→ 内嵌 CharacterEmotionChart 显该次曲线
 *   - 切换 chip 不丢失模态,直接 swap chart records
 *   - 空数据:角色没参与过 evolution 推演 → 友好空态
 *
 * 设计与 CharacterEmotionModal 区别:
 *   - 后者展示**单次**推演的情绪曲线(SimulationDetailView 入口)
 *   - 本组件展示**跨多次**推演的总览(ProjectView 角色卡入口),复用前者的 Chart
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { api } from "../api/client";
import { ApiError, type CharacterEmotionsBySim } from "../api/types";
import CharacterEmotionChart from "./CharacterEmotionChart.vue";
import SkeletonBlock from "./SkeletonBlock.vue";

const props = defineProps<{
  open: boolean;
  projectId: string;
  characterId: string;
  characterName: string;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

const sims = ref<CharacterEmotionsBySim[]>([]);
const loading = ref(false);
const errorMsg = ref<string | null>(null);
const selectedIdx = ref(0);

// P-2 修复(2026-05-23):序列 token 防快速切角色时旧请求晚到污染新数据
let _loadSeq = 0;

const selectedSim = computed<CharacterEmotionsBySim | null>(
  () => sims.value[selectedIdx.value] ?? null,
);

async function load() {
  if (!props.projectId || !props.characterId) return;
  const mySeq = ++_loadSeq;
  loading.value = true;
  errorMsg.value = null;
  sims.value = [];
  selectedIdx.value = 0;
  try {
    const resp = await api.get<CharacterEmotionsBySim[]>(
      `/projects/${props.projectId}/characters/${props.characterId}/emotional_states`,
    );
    if (mySeq !== _loadSeq) return;   // 已被新请求覆盖,丢弃
    sims.value = resp;
  } catch (e) {
    if (mySeq !== _loadSeq) return;
    errorMsg.value = e instanceof ApiError ? e.message : "加载失败";
  } finally {
    if (mySeq === _loadSeq) loading.value = false;
  }
}

// 打开时拉数据(关闭时不清,下次复用可直接显;但要确保关闭后再打开会重拉)
watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) void load();
  },
);

function fmtTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${m}-${day} ${hh}:${mm}`;
}

function truncate(s: string, max: number): string {
  if (!s) return "";
  return s.length > max ? `${s.slice(0, max)}…` : s;
}

function onBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) emit("close");
}

function onGlobalKey(e: KeyboardEvent) {
  if (!props.open) return;
  if (e.key === "Escape") {
    e.preventDefault();
    emit("close");
  }
}

onMounted(() => document.addEventListener("keydown", onGlobalKey));
onBeforeUnmount(() => document.removeEventListener("keydown", onGlobalKey));
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        :aria-label="`${characterName} 情绪总览`"
        @click="onBackdrop"
      >
        <div class="modal-card emotion-overview-card">
          <button
            type="button"
            class="close-btn"
            aria-label="关闭"
            @click="emit('close')"
          >×</button>

          <header class="overview-header">
            <h3 class="overview-title">{{ characterName }} · 情绪总览</h3>
            <p class="overview-hint">本项目历次推演的情绪轨迹 · 点击下方推演 chip 切换查看</p>
          </header>

          <div v-if="loading" class="overview-loading" aria-busy="true" aria-live="polite">
            <SkeletonBlock height="28px" width="60%" rounded="md" />
            <SkeletonBlock height="220px" rounded="md" />
          </div>

          <div v-else-if="errorMsg" class="overview-state overview-state--error">
            <p>{{ errorMsg }}</p>
            <button class="ghost-btn" @click="load">重试</button>
          </div>

          <div v-else-if="sims.length === 0" class="overview-state">
            <p class="state-title">暂无情绪数据</p>
            <p class="state-hint">
              该角色还没参与过 evolution(灵魂续写)模式的推演,因此没有情绪轨迹记录。
            </p>
          </div>

          <template v-else>
            <ul class="sim-chip-list" role="tablist" :aria-label="`${characterName} 历次推演`">
              <li v-for="(s, i) in sims" :key="s.sim_id">
                <button
                  type="button"
                  role="tab"
                  :aria-selected="i === selectedIdx"
                  :class="['sim-chip', { 'is-active': i === selectedIdx }]"
                  :title="s.sim_divergence"
                  @click="selectedIdx = i"
                >
                  <span class="chip-time mono">{{ fmtTime(s.sim_created_at) }}</span>
                  <span class="chip-divergence">{{ truncate(s.sim_divergence, 28) }}</span>
                  <span class="chip-meta mono">{{ s.records.length }} 幕</span>
                </button>
              </li>
            </ul>

            <CharacterEmotionChart
              v-if="selectedSim"
              :character-name="characterName"
              :records="selectedSim.records"
            />
          </template>
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

/* 比单次曲线模态宽一点(720 → 820)给 chip 列表横向空间 */
.emotion-overview-card {
  width: 100%;
  max-width: 820px;
  max-height: 90vh;
  overflow-y: auto;
  position: relative;
  z-index: var(--z-modal);
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.close-btn {
  position: absolute;
  top: -10px;
  right: -10px;
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-muted);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  box-shadow: var(--shadow-md);
  transition: all var(--duration-fast) var(--ease-out);
  z-index: 1;
}
.close-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
  border-color: var(--color-border-strong);
}

.overview-header {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding-right: var(--space-6);   /* 给 close button 留空间 */
}
.overview-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.overview-hint {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin: 0;
}

.overview-loading {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-3) 0;
}

.overview-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-8) var(--space-4);
  text-align: center;
}
.overview-state--error {
  color: var(--color-danger);
}
.state-title {
  font-size: var(--text-base);
  font-weight: 500;
  color: var(--color-text);
  margin: 0;
}
.state-hint {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin: 0;
  max-width: 480px;
  line-height: var(--line-normal);
}
.ghost-btn {
  padding: 6px 16px;
  font-size: var(--text-sm);
  color: var(--color-text);
  background: transparent;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  transition: all var(--duration-fast) var(--ease-out);
}
.ghost-btn:hover {
  background: var(--color-surface-hover);
}

/* 推演 chip 列表 */
.sim-chip-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.sim-chip {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  color: var(--color-text);
  transition: all var(--duration-fast) var(--ease-out);
  max-width: 100%;
}
.sim-chip:hover {
  border-color: var(--color-accent-border);
  background: var(--color-accent-soft);
}
.sim-chip.is-active {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}
.sim-chip.is-active .chip-time,
.sim-chip.is-active .chip-meta {
  color: var(--color-text-on-accent);
  opacity: 0.85;
}
.chip-time {
  font-size: 11px;
  color: var(--color-text-subtle);
  flex-shrink: 0;
}
.chip-divergence {
  color: var(--color-text);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 240px;
}
.sim-chip.is-active .chip-divergence {
  color: var(--color-text-on-accent);
}
.chip-meta {
  font-size: 11px;
  color: var(--color-text-subtle);
  flex-shrink: 0;
}
</style>
