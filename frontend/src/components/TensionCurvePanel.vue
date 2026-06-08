<script setup lang="ts">
/**
 * TensionCurvePanel — SP-6.1(2026-05-28).
 *
 * 张力曲线 + 三幕分区背景.
 * 用 inline SVG 渲染,不引依赖.
 *
 * 数据源:outlineScenes 列表
 *   - scene_index → X 轴位置
 *   - tension_percent → Y 轴高度
 *   - structure_act → 三幕分区背景色
 */
import { computed } from "vue";
import type { OutlineScene } from "../api/types";

const props = defineProps<{
  scenes: OutlineScene[];
}>();

// 画布尺寸
const WIDTH = 800;
const HEIGHT = 200;
const PADDING = { top: 16, right: 16, bottom: 32, left: 40 };
const INNER_W = WIDTH - PADDING.left - PADDING.right;
const INNER_H = HEIGHT - PADDING.top - PADDING.bottom;

const sortedScenes = computed(() =>
  [...props.scenes].sort((a, b) => a.scene_index - b.scene_index),
);

const totalScenes = computed(() => sortedScenes.value.length);

// 把 scene_index → X 坐标
function sceneX(idx: number): number {
  if (totalScenes.value <= 1) return PADDING.left + INNER_W / 2;
  return PADDING.left + (idx / (totalScenes.value - 1)) * INNER_W;
}

// 把 tension(0-100)→ Y 坐标(顶部 high tension)
function tensionY(t: number): number {
  return PADDING.top + INNER_H - (t / 100) * INNER_H;
}

// 折线 path(只连接有 tension_percent 的点)
const linePoints = computed(() => {
  const pts: string[] = [];
  sortedScenes.value.forEach((s, idx) => {
    if (s.tension_percent !== null && s.tension_percent !== undefined) {
      const x = sceneX(idx);
      const y = tensionY(s.tension_percent);
      pts.push(`${x},${y}`);
    }
  });
  return pts.join(" ");
});

const pointMarkers = computed(() =>
  sortedScenes.value
    .map((s, idx) => ({
      x: sceneX(idx),
      y: s.tension_percent !== null && s.tension_percent !== undefined
        ? tensionY(s.tension_percent)
        : null,
      tension: s.tension_percent ?? null,
      sceneNo: s.scene_index + 1,
    }))
    .filter((m) => m.y !== null),
);

// 三幕分区:把 sortedScenes 按 structure_act 分段,算各幕的 X 范围
interface ActRange {
  act: "act1_setup" | "act2_confrontation" | "act3_resolution";
  startX: number;
  endX: number;
}

const actRanges = computed<ActRange[]>(() => {
  const ranges: ActRange[] = [];
  let currentAct: ActRange["act"] | null = null;
  let startIdx = 0;
  sortedScenes.value.forEach((s, idx) => {
    if (s.structure_act && s.structure_act !== currentAct) {
      if (currentAct !== null) {
        ranges.push({
          act: currentAct,
          startX: sceneX(startIdx) - 8,
          endX: sceneX(idx) - 8,
        });
      }
      currentAct = s.structure_act;
      startIdx = idx;
    }
  });
  // 收尾:最后一段
  if (currentAct !== null && startIdx < sortedScenes.value.length) {
    ranges.push({
      act: currentAct,
      startX: sceneX(startIdx) - 8,
      endX: sceneX(sortedScenes.value.length - 1) + 8,
    });
  }
  return ranges;
});

function actColor(act: ActRange["act"]): string {
  if (act === "act1_setup") return "rgba(59, 130, 246, 0.08)"; // 浅蓝
  if (act === "act2_confrontation") return "rgba(217, 119, 6, 0.08)"; // 浅橙
  return "rgba(124, 58, 237, 0.08)"; // 浅紫
}

function actLabel(act: ActRange["act"]): string {
  if (act === "act1_setup") return "第一幕 · 建置";
  if (act === "act2_confrontation") return "第二幕 · 冲突";
  return "第三幕 · 收束";
}

// Y 轴刻度
const yTicks = [0, 25, 50, 75, 100];

const hasTensionData = computed(() =>
  sortedScenes.value.some(
    (s) => s.tension_percent !== null && s.tension_percent !== undefined,
  ),
);
</script>

<template>
  <section class="tension-panel">
    <header class="ph">
      <h3 class="title">张力曲线</h3>
      <p class="subtitle">
        每幕张力(0-100)折线 + 三幕分区背景。中段塌陷 / 高潮提前一眼可见。
      </p>
    </header>

    <div v-if="!hasTensionData" class="empty">
      暂无张力数据 — outline planner 未规划或老 sim。
    </div>

    <svg
      v-else
      :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
      :width="WIDTH"
      :height="HEIGHT"
      class="svg"
      preserveAspectRatio="xMidYMid meet"
    >
      <!-- 三幕背景区 -->
      <g class="act-bg">
        <rect
          v-for="(r, i) in actRanges"
          :key="i"
          :x="r.startX"
          :y="PADDING.top"
          :width="r.endX - r.startX"
          :height="INNER_H"
          :fill="actColor(r.act)"
        />
        <text
          v-for="(r, i) in actRanges"
          :key="`label-${i}`"
          :x="(r.startX + r.endX) / 2"
          :y="PADDING.top + 14"
          text-anchor="middle"
          class="act-label"
        >
          {{ actLabel(r.act) }}
        </text>
      </g>

      <!-- Y 轴网格线 + 刻度 -->
      <g class="grid">
        <line
          v-for="t in yTicks"
          :key="t"
          :x1="PADDING.left"
          :x2="WIDTH - PADDING.right"
          :y1="tensionY(t)"
          :y2="tensionY(t)"
          stroke="rgba(0,0,0,0.06)"
          stroke-width="1"
        />
        <text
          v-for="t in yTicks"
          :key="`y-${t}`"
          :x="PADDING.left - 8"
          :y="tensionY(t) + 4"
          text-anchor="end"
          class="axis-text"
        >
          {{ t }}
        </text>
      </g>

      <!-- 张力折线 -->
      <polyline
        :points="linePoints"
        fill="none"
        stroke="var(--color-accent)"
        stroke-width="2"
        stroke-linejoin="round"
        stroke-linecap="round"
      />

      <!-- 数据点 -->
      <g class="points">
        <circle
          v-for="(m, i) in pointMarkers"
          :key="i"
          :cx="m.x"
          :cy="m.y!"
          r="3.5"
          fill="var(--color-accent)"
        >
          <title>第 {{ m.sceneNo }} 幕 · 张力 {{ m.tension }}</title>
        </circle>
      </g>

      <!-- X 轴 -->
      <line
        :x1="PADDING.left"
        :x2="WIDTH - PADDING.right"
        :y1="HEIGHT - PADDING.bottom"
        :y2="HEIGHT - PADDING.bottom"
        stroke="rgba(0,0,0,0.2)"
        stroke-width="1"
      />
      <text
        :x="WIDTH / 2"
        :y="HEIGHT - 6"
        text-anchor="middle"
        class="axis-text"
      >
        幕号(共 {{ totalScenes }} 幕)
      </text>
    </svg>

    <!-- 图例 -->
    <div v-if="hasTensionData" class="legend">
      <span class="legend-item"><i class="lg-color" style="background: rgba(59, 130, 246, 0.2)" />第一幕 建置</span>
      <span class="legend-item"><i class="lg-color" style="background: rgba(217, 119, 6, 0.2)" />第二幕 冲突</span>
      <span class="legend-item"><i class="lg-color" style="background: rgba(124, 58, 237, 0.2)" />第三幕 收束</span>
    </div>
  </section>
</template>

<style scoped>
.tension-panel {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4) var(--space-5);
}
.ph { margin-bottom: var(--space-3); }
.title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.subtitle {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin: var(--space-1) 0 0;
  line-height: 1.6;
}
.empty {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-subtle);
  font-size: var(--text-sm);
}
.svg {
  width: 100%;
  max-width: 800px;
  height: auto;
  display: block;
  margin: var(--space-3) auto 0;
}
.act-label {
  font-size: 11px;
  fill: var(--color-text-muted);
  font-weight: 500;
}
.axis-text {
  font-size: 10px;
  fill: var(--color-text-subtle);
  font-variant-numeric: tabular-nums;
}
.legend {
  display: flex;
  justify-content: center;
  gap: var(--space-4);
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}
.lg-color {
  display: inline-block;
  width: 12px;
  height: 12px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
}
</style>
