<script setup lang="ts">
/**
 * ReshapeSlider — 重塑度滑块独立组件(Sprint 2.C)。
 *
 * 浅色 token 系统;显示三个物理维度的实时影响:
 *   第 1 维 — 可改角色数(已用 / 上限)
 *   第 2 维 — agent 推演轮次(派生自 reshape_to_rounds 公式)
 *   第 3 维 — 图谱距离 BFS 影响半径(0-8 跳)
 *
 * 设计:
 *   - input range 走浅色 token + accent 渐变
 *   - plan 上限超出区显示禁用样式(滑块拖到那里会自动 clamp 回 max)
 *   - 三维显示在滑块下方,数据来自 useCounterfactuals.preview(父调 previewAt 实时刷新)
 *   - 角色数超上限时该维度变红 + 提示"撤销反事实或调高重塑度"
 *
 * 不做(留给父组件):
 *   - 反事实列表展示 / 编辑(由 CounterfactualWorkbench 负责)
 *   - 调 previewAt(由父在 watch(reshapePercent) 时触发)— 让父决定 debounce 节奏
 */
import { computed } from "vue";

import type { ReshapePreview } from "../api/types";

const props = defineProps<{
  /** v-model:当前 reshape % (10-90) */
  modelValue: number;
  /** plan 上限(免费 30 / 标准 60 / 超级 90) */
  planMaxPercent: number;
  /** 三维度预览(useCounterfactuals.preview)*/
  preview: ReshapePreview | null;
  /** 是否显示"角色数超上限"红色警告 */
  isOverCharacterLimit?: boolean;
  /** 给"完全自由发挥"的 disable 用,默认 false */
  disabled?: boolean;
}>();

const emit = defineEmits<{
  (e: "update:modelValue", value: number): void;
}>();

function onInput(e: Event) {
  const v = parseInt((e.target as HTMLInputElement).value, 10);
  if (isNaN(v)) return;
  // clamp 到 plan 上限
  const clamped = Math.min(props.planMaxPercent, Math.max(10, v));
  emit("update:modelValue", clamped);
}

/** 滑块轨道 background 的渐变断点 — accent 区到 planMax;之后是 disabled 灰区 */
const trackStyle = computed(() => {
  const max = props.planMaxPercent;
  const stop = ((max - 10) / (90 - 10)) * 100;
  return {
    background: `linear-gradient(to right,
      var(--color-accent) 0%,
      var(--color-accent) ${stop}%,
      var(--color-bg-subtle) ${stop}%,
      var(--color-bg-subtle) 100%)`,
  };
});

const dim1Touched = computed(() => props.preview?.current_touched_count ?? 0);
const dim1Limit = computed(() => props.preview?.max_touched_characters ?? 0);
const dim2Rounds = computed(() => props.preview?.rounds_planned ?? 0);
const dim3Hops = computed(() => props.preview?.graph_distance_hops ?? 0);
const dim3Affected = computed(
  () => props.preview?.current_affected_node_ids?.length ?? 0,
);

const isAtPlanMax = computed(() => props.modelValue === props.planMaxPercent);
const planLabel = computed(() => {
  if (props.planMaxPercent >= 90) return "超级 / 创始人 (上限 90%)";
  if (props.planMaxPercent >= 60) return "标准 (上限 60%)";
  return "免费 (上限 30%)";
});

/** hops 文案 — 0 是"只本节点",8 是"基本全图" */
const hopsLabel = computed(() => {
  const h = dim3Hops.value;
  if (h === 0) return "仅被改节点";
  if (h <= 2) return `${h} 跳邻居`;
  if (h <= 5) return `${h} 跳(中等扩散)`;
  return `${h} 跳(几乎全图)`;
});
</script>

<template>
  <div class="reshape-slider" :class="{ 'is-disabled': disabled }">
    <!-- 顶部:数值 + plan 上限标签 -->
    <header class="head">
      <div class="head-left">
        <span class="value mono">{{ modelValue }}<small>%</small></span>
        <span class="head-label">重塑度</span>
      </div>
      <span class="plan-chip" :class="{ 'plan-at-max': isAtPlanMax }">
        {{ planLabel }}
      </span>
    </header>

    <!-- 滑块轨道 -->
    <div class="track-wrap" :style="trackStyle">
      <input
        type="range"
        min="10"
        max="90"
        step="1"
        :value="modelValue"
        :disabled="disabled"
        class="range"
        @input="onInput"
      />
    </div>

    <!-- 三维度可视化(数据来自 useCounterfactuals.preview)-->
    <ul class="dimensions">
      <li class="dim" :class="{ 'dim-warn': isOverCharacterLimit }">
        <span class="dim-icon">①</span>
        <span class="dim-label">可改角色</span>
        <span class="dim-value mono">
          {{ dim1Touched }} / {{ dim1Limit }}
        </span>
        <span v-if="isOverCharacterLimit" class="dim-warn-text">超上限</span>
      </li>
      <li class="dim">
        <span class="dim-icon">②</span>
        <span class="dim-label">推演轮次</span>
        <span class="dim-value mono">{{ dim2Rounds }} 轮</span>
      </li>
      <li class="dim">
        <span class="dim-icon">③</span>
        <span class="dim-label">影响半径</span>
        <span class="dim-value mono">
          {{ hopsLabel }} <span v-if="dim3Affected > 0" class="dim-affected">
            ({{ dim3Affected }} 节点)</span>
        </span>
      </li>
    </ul>

    <!-- 角色数超限提示 -->
    <p v-if="isOverCharacterLimit" class="limit-hint">
      ⚠ 已改 {{ dim1Touched }} 个角色,超过当前重塑度 {{ modelValue }}% 允许的 {{ dim1Limit }} 个上限。
      撤销一些反事实或调高重塑度。
    </p>
  </div>
</template>

<style scoped>
.reshape-slider {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.reshape-slider.is-disabled {
  opacity: 0.6;
  pointer-events: none;
}

.head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: var(--space-2);
}
.head-left {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
}
.value {
  font-size: 24px;
  font-weight: 600;
  color: var(--color-accent-text);
  line-height: 1;
}
.value small {
  font-size: var(--text-base);
  font-weight: 400;
  margin-left: 2px;
}
.head-label {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}
.plan-chip {
  font-size: var(--text-xs);
  padding: 2px var(--space-2);
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
}
.plan-chip.plan-at-max {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
}

/* 滑块轨道(用 background gradient 显 plan 上限禁用区) */
.track-wrap {
  position: relative;
  height: 8px;
  border-radius: 4px;
  margin: var(--space-2) 0;
  /* background 由 :style 注入 */
}
.range {
  -webkit-appearance: none;
  appearance: none;
  position: absolute;
  top: -8px;
  left: 0;
  width: 100%;
  height: 24px;
  background: transparent;
  cursor: pointer;
  outline: none;
}
.range::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--color-surface);
  border: 2px solid var(--color-accent);
  cursor: grab;
  transition: transform var(--duration-fast) var(--ease-out);
}
.range::-webkit-slider-thumb:hover {
  transform: scale(1.15);
}
.range::-webkit-slider-thumb:active {
  cursor: grabbing;
  transform: scale(1.25);
}
.range::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: var(--color-surface);
  border: 2px solid var(--color-accent);
  cursor: grab;
}
.range:disabled {
  cursor: not-allowed;
}

/* 三维列表 */
.dimensions {
  display: flex;
  flex-direction: column;
  gap: 4px;
  list-style: none;
  padding: 0;
  margin: 0;
}
.dim {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: 4px var(--space-2);
  font-size: var(--text-xs);
  border-radius: var(--radius-sm);
  background: var(--color-bg-subtle);
}
.dim-icon {
  font-size: 11px;
  color: var(--color-accent-text);
  flex-shrink: 0;
  font-weight: 600;
  width: 16px;
}
.dim-label {
  flex: 1;
  color: var(--color-text);
}
.dim-value {
  color: var(--color-text);
  font-weight: 500;
}
.dim-affected {
  color: var(--color-text-subtle);
  font-weight: 400;
  margin-left: 4px;
}

.dim.dim-warn {
  background: var(--color-danger-soft);
}
.dim.dim-warn .dim-label,
.dim.dim-warn .dim-value {
  color: var(--color-danger);
}
.dim-warn-text {
  font-size: 10px;
  color: var(--color-danger);
  font-weight: 600;
  margin-left: 4px;
}

.limit-hint {
  font-size: var(--text-xs);
  color: var(--color-danger);
  background: var(--color-danger-soft);
  padding: 6px var(--space-2);
  border-radius: var(--radius-sm);
  line-height: 1.5;
}
</style>
