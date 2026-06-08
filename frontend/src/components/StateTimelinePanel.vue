<script setup lang="ts">
/**
 * StateTimelinePanel — SP-4.1(2026-06-02)角色状态时间线 — chip 列表入口
 *
 * 数据源:GET /api/simulations/{sim_id}/state_timeline(SP-4 落地的 character_state_snapshots)
 * 字段:position(场景)/ hp_status / emotion_vec / known_fact_ids 跨幕变化
 *
 * UX(2026-06-02 改版):
 *   - 跟"角色情绪轨迹" section 视觉一致(chip 紧凑列表 + 点击弹模态)
 *   - 不再直接渲染 SVG 占屏(原版所有场景标签挤在一行重叠不可读)
 *   - 点 chip → StateTimelineModal 弹出 SVG 时间线 + 信息边界小卡
 *
 * 设计:与 emotion-section 视觉模式一致(参照 SimulationDetailView.emotion-section)
 */
import { computed, onBeforeUnmount, ref, watch } from "vue";

import { api } from "../api/client";
import { ApiError } from "../api/types";
import { useEventBus } from "../stores/events";
import StateTimelineModal from "./StateTimelineModal.vue";
import SkeletonBlock from "./SkeletonBlock.vue";

interface Snapshot {
  scene_index: number;
  character_id: string;
  character_name: string;
  position: string | null;
  hp_status: string | null;
  status_note: string | null;
  emotion_vec: Record<string, number> | null;
  inventory: string[] | null;
  known_fact_ids: string[] | null;
}
interface StateTimelineResponse {
  timelines: Record<string, Snapshot[]>;
  characters: Array<{ id: string; name: string }>;
  scene_count: number;
  simulation_id: string;
  project_id: string;
}

const props = defineProps<{
  simulationId: string;
}>();

const data = ref<StateTimelineResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const selectedCharId = ref<string | null>(null);

async function load() {
  if (!props.simulationId) return;
  loading.value = true;
  error.value = null;
  try {
    data.value = await api.get<StateTimelineResponse>(
      `/simulations/${props.simulationId}/state_timeline`,
    );
  } catch (e) {
    if (!(e instanceof ApiError)) {
      if (import.meta.env.DEV) {
        // eslint-disable-next-line no-console
        console.warn("[StateTimelinePanel] load failed:", e);
      }
    }
    // 失败不清空旧数据(stale-while-revalidate)
  } finally {
    loading.value = false;
  }
}

watch(() => props.simulationId, () => {
  data.value = null;
  selectedCharId.value = null;
  void load();
}, { immediate: true });

// 监听 sim:done — 推演完成后刷新时间线
const events = useEventBus();
const _unsubDone = events.on("sim:done", (payload) => {
  if (payload.sim_id === props.simulationId) {
    void load();
  }
});
onBeforeUnmount(_unsubDone);

// 按角色派生 chip 显示信息(本角色出现几幕)
interface CharChip {
  id: string;
  name: string;
  sceneCount: number;
  snapshots: Snapshot[];
}
const charChips = computed<CharChip[]>(() => {
  if (!data.value) return [];
  return data.value.characters
    .map((c) => {
      const snaps = data.value!.timelines[c.id] ?? [];
      return { id: c.id, name: c.name, sceneCount: snaps.length, snapshots: snaps };
    })
    .filter((c) => c.sceneCount > 0)
    .sort((a, b) => a.name.localeCompare(b.name, "zh-CN"));
});

const selectedChip = computed<CharChip | null>(
  () => charChips.value.find((c) => c.id === selectedCharId.value) ?? null,
);

const sceneCount = computed(() => data.value?.scene_count ?? 0);
</script>

<template>
  <section v-if="loading || (data && data.characters.length > 0)" class="state-timeline-section">
    <header class="state-timeline-header">
      <h3 class="state-timeline-title">角色状态时间线</h3>
      <span class="state-timeline-hint">
        点角色查看完整时间线 · hp_status / 场景 / 已知事实跨幕变化
      </span>
    </header>

    <div v-if="loading && !data" class="state-timeline-loading" aria-busy="true" aria-live="polite">
      <SkeletonBlock height="32px" width="96px" rounded="full" />
      <SkeletonBlock height="32px" width="96px" rounded="full" />
      <SkeletonBlock height="32px" width="96px" rounded="full" />
    </div>

    <ul v-else class="state-chip-list">
      <li v-for="c in charChips" :key="c.id">
        <button
          type="button"
          class="state-chip"
          @click="selectedCharId = c.id"
        >
          <span class="state-chip-name">{{ c.name }}</span>
          <span class="state-chip-meta mono">{{ c.sceneCount }} 幕</span>
        </button>
      </li>
    </ul>

    <StateTimelineModal
      :open="selectedChip !== null"
      :character-name="selectedChip?.name ?? ''"
      :snapshots="selectedChip?.snapshots ?? []"
      :scene-count="sceneCount"
      @close="selectedCharId = null"
    />
  </section>
</template>

<style scoped>
.state-timeline-section {
  margin-top: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.state-timeline-header {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  flex-wrap: wrap;
}
.state-timeline-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.state-timeline-hint {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.state-timeline-loading {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.state-chip-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.state-chip {
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
.state-chip:hover {
  border-color: var(--color-accent-border);
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
}
.state-chip-name {
  font-weight: 500;
}
.state-chip-meta {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.state-chip:hover .state-chip-meta {
  color: var(--color-accent-text);
}
.mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
</style>
