<script setup lang="ts">
/**
 * StateTimelineModal — SP-4.1(2026-06-02)角色状态时间线 — 模态展示
 *
 * 跟随"角色情绪轨迹"的 chip + modal 模式:chip 列表在 StateTimelinePanel,
 * 点击 chip 弹本模态展示完整 SVG 时间线 + 信息边界小卡.
 *
 * 设计参照 CharacterEmotionModal.vue:
 *   - Teleport to body + modal-fade + backdrop-filter blur
 *   - Esc 关闭 / backdrop 点击关闭 / × 关闭
 */
import { onBeforeUnmount, onMounted, computed } from "vue";

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

const props = defineProps<{
  open: boolean;
  characterName: string;
  snapshots: Snapshot[];
  sceneCount: number;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

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

// SVG 几何 — 2026-06-02 改版:只剩横轴 + 圆点 + 幕号,标注挪到下面
const W = 720;
const H_SLIM = 50;       // 旧版 120(含标注高度);新版 50 紧凑
const PAD_X = 40;
const ROW_Y = 20;        // 横轴 y 坐标(圆点中心)
const innerW = W - PAD_X * 2;

function xForScene(idx: number): number {
  const max = Math.max(1, props.sceneCount - 1);
  return PAD_X + (idx / max) * innerW;
}

function nodeFillForSnapshot(s: Snapshot): string {
  switch (s.hp_status) {
    case "deceased":  return "#dc2626";
    case "in_facility": return "#f59e0b";
    case "absent":    return "#9ca3af";
    case "unknown":   return "#a78bfa";
    default:          return "#10b981";
  }
}

function statusLabel(s: Snapshot): string {
  switch (s.hp_status) {
    case "deceased":  return "已死";
    case "in_facility": return "在某处";
    case "absent":    return "暂离";
    case "unknown":   return "未知";
    default:          return "在场";
  }
}

const keyChanges = computed<Array<{ scene: number; reason: string }>>(() => {
  const out: Array<{ scene: number; reason: string }> = [];
  for (let i = 1; i < props.snapshots.length; i++) {
    const prev = props.snapshots[i - 1];
    const cur = props.snapshots[i];
    if (prev.hp_status !== cur.hp_status) {
      out.push({ scene: cur.scene_index, reason: `${statusLabel(prev)} → ${statusLabel(cur)}` });
    } else if (prev.position !== cur.position && cur.position) {
      const prevPos = prev.position ?? "(未知)";
      out.push({ scene: cur.scene_index, reason: `场景:${prevPos} → ${cur.position}` });
    }
  }
  return out;
});

// 关键变化幕号集合 — SVG 标记三角用
const keyChangeSet = computed<Set<number>>(
  () => new Set(keyChanges.value.map((k) => k.scene)),
);

// 信息边界:最近 3 幕的已知事实数
const recentFacts = computed(() =>
  props.snapshots
    .filter((s) => s.known_fact_ids && s.known_fact_ids.length > 0)
    .slice(-3),
);
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        :aria-label="`${characterName} 角色状态时间线`"
        @click="onBackdrop"
      >
        <div class="modal-card state-timeline-modal-card">
          <button
            type="button"
            class="close-btn"
            aria-label="关闭"
            @click="emit('close')"
          >×</button>

          <header class="modal-header">
            <h3 class="modal-title">{{ characterName }} · 状态时间线</h3>
            <p class="modal-hint">每幕末快照 — hp_status / 场景 / 已知事实跨幕变化</p>
          </header>

          <div v-if="snapshots.length === 0" class="modal-empty">
            该角色在本推演中无快照数据.
          </div>

          <template v-else>
            <!-- 2026-06-02 改版:SVG 只保留圆点 + 幕号,关键变化挪到下面清单
                 (旧版紫色标注挤在一行重叠不可读) -->
            <div class="timeline-svg-wrap">
              <svg :viewBox="`0 0 ${W} ${H_SLIM}`" class="timeline-svg">
                <!-- 横轴 -->
                <line
                  :x1="PAD_X" :y1="ROW_Y" :x2="W - PAD_X" :y2="ROW_Y"
                  stroke="#e5e7eb" stroke-width="2"
                />
                <!-- 每幕节点 + 幕号 + 紫色标记(若该幕有关键变化)-->
                <g v-for="s in snapshots" :key="s.scene_index">
                  <!-- 紫色小三角:该幕有关键变化时显示 -->
                  <polygon
                    v-if="keyChangeSet.has(s.scene_index)"
                    :points="`${xForScene(s.scene_index) - 4},${ROW_Y - 12} ${xForScene(s.scene_index) + 4},${ROW_Y - 12} ${xForScene(s.scene_index)},${ROW_Y - 5}`"
                    fill="#7c3aed"
                  />
                  <circle
                    :cx="xForScene(s.scene_index)" :cy="ROW_Y" r="5"
                    :fill="nodeFillForSnapshot(s)" stroke="#fff" stroke-width="1.5"
                  >
                    <title>第 {{ s.scene_index + 1 }} 幕 · {{ statusLabel(s) }}{{ s.position ? " · 场景: " + s.position : "" }}{{ s.known_fact_ids && s.known_fact_ids.length > 0 ? " · 已知事实: " + s.known_fact_ids.length + " 条" : "" }}</title>
                  </circle>
                  <text
                    :x="xForScene(s.scene_index)" :y="ROW_Y + 18"
                    text-anchor="middle" font-size="10" fill="#6b7280"
                  >{{ s.scene_index + 1 }}</text>
                </g>
              </svg>

              <div class="timeline-legend">
                <span class="legend-item"><i class="dot dot-alive" /> 在场</span>
                <span class="legend-item"><i class="dot dot-deceased" /> 已死</span>
                <span class="legend-item"><i class="dot dot-facility" /> 在某处</span>
                <span class="legend-item"><i class="dot dot-absent" /> 暂离</span>
                <span class="legend-item"><i class="dot dot-unknown" /> 未知</span>
                <span class="legend-item legend-item--key">
                  <i class="dot dot-key" /> 关键变化(下方清单)
                </span>
              </div>
            </div>

            <!-- 关键变化清单 — 一行一条,清晰可读 -->
            <div v-if="keyChanges.length > 0" class="key-changes-list">
              <h4 class="key-changes-title">关键变化清单({{ keyChanges.length }} 处)</h4>
              <ul class="key-changes-ul">
                <li
                  v-for="(k, i) in keyChanges" :key="`k-${i}`"
                  class="key-changes-row"
                >
                  <span class="key-changes-scene mono">第 {{ k.scene + 1 }} 幕</span>
                  <span class="key-changes-reason">{{ k.reason }}</span>
                </li>
              </ul>
            </div>

            <div v-if="recentFacts.length > 0" class="info-boundary-mini">
              <h4 class="info-boundary-title">信息边界(最近 3 个有变化的幕)</h4>
              <div
                v-for="s in recentFacts" :key="`fact-${s.scene_index}`"
                class="info-boundary-row"
              >
                <span class="info-boundary-scene mono">第 {{ s.scene_index + 1 }} 幕末</span>
                <span class="info-boundary-text">
                  已知 <strong>{{ s.known_fact_ids?.length || 0 }}</strong> 条事实
                </span>
              </div>
            </div>
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
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
}
.modal-card {
  position: relative;
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  max-width: 820px;
  width: calc(100% - var(--space-8));
  max-height: 80vh;
  overflow-y: auto;
  padding: var(--space-6);
}
.state-timeline-modal-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.close-btn {
  position: absolute;
  top: var(--space-3);
  right: var(--space-4);
  font-size: var(--text-xl);
  background: none;
  border: 0;
  color: var(--color-text-subtle);
  line-height: 1;
}
.close-btn:hover {
  color: var(--color-text);
}
.modal-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.modal-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.modal-hint {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin: 0;
}
.modal-empty {
  padding: var(--space-6);
  text-align: center;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}

.timeline-svg-wrap {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.timeline-svg {
  width: 100%;
  height: auto;
  max-height: 180px;
}
.timeline-legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.dot-alive { background: #10b981; }
.dot-deceased { background: #dc2626; }
.dot-facility { background: #f59e0b; }
.dot-absent { background: #9ca3af; }
.dot-unknown { background: #a78bfa; }
.dot-key {
  width: 0; height: 0; background: transparent;
  border-left: 5px solid transparent;
  border-right: 5px solid transparent;
  border-top: 6px solid #7c3aed;
  border-radius: 0;
}
.legend-item--key {
  color: #6b21a8;
  font-weight: 500;
}

/* 关键变化清单 — 一行一条,可读 */
.key-changes-list {
  background: #faf5ff;
  border: 1px solid #e9d5ff;
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.key-changes-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: #6b21a8;
  margin: 0 0 var(--space-1) 0;
}
.key-changes-ul {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.key-changes-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  font-size: var(--text-sm);
  line-height: 1.55;
  padding: 2px 0;
}
.key-changes-scene {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: #7c3aed;
  font-weight: 600;
  min-width: 56px;
}
.key-changes-reason {
  color: var(--color-text);
  word-break: break-word;
}

.info-boundary-mini {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.info-boundary-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
  margin: 0 0 var(--space-1) 0;
}
.info-boundary-row {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  font-size: var(--text-sm);
}
.info-boundary-scene {
  color: var(--color-text-subtle);
  font-size: var(--text-xs);
  white-space: nowrap;
}
.info-boundary-text {
  color: var(--color-text);
}
.info-boundary-text strong {
  color: var(--color-accent);
  font-weight: 600;
}

.mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
</style>
