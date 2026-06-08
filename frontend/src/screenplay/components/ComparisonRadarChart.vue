<script setup lang="ts">
/**
 * 多模型对比雷达图 — 阶段 8.5+(2026-06-08)。
 *
 * 4 轴(SP-2/3/4/7 的对应代理):action_density / character_alignment /
 * dialogue_coverage / decision_completeness。
 *
 * N 个 candidate 用不同色 polygon 叠加(fill 半透明 + stroke 实线),
 * 一张图看出"哪个 vendor 在哪个维度强 / 弱"。
 *
 * 自实现 SVG,不引第三方库(跟 CharacterRelationshipGraph 一致风格)。
 */
import { computed, ref } from "vue";

import type { ModelCandidateApi } from "../api/screenplay-client";

const props = defineProps<{
  candidates: ModelCandidateApi[];
  recommendedLabel?: string | null;
  size?: number;
}>();

const SIZE = computed(() => props.size ?? 360);
const CENTER = computed(() => SIZE.value / 2);
const RADIUS = computed(() => SIZE.value * 0.36);

const AXES = [
  { key: "action_density", label: "动作密度", angle: -90 },     // top
  { key: "character_alignment", label: "角色对齐", angle: 0 },  // right
  { key: "dialogue_coverage", label: "对白覆盖", angle: 90 },   // bottom
  { key: "decision_completeness", label: "决策完整", angle: 180 }, // left
] as const;

// 候选颜色环(8 色循环够用)
const CAND_COLORS = [
  "#7C3AED",  // 浑晶紫(推荐位)
  "#5d8aa8",  // 钢蓝
  "#a86d4e",  // 暮棕
  "#6d8a6d",  // 山岚绿
  "#c39657",  // 沙金
  "#8a5fa0",  // 紫红
  "#5d8a8a",  // 青绿
  "#8a8479",  // 灰褐
];

// 颜色:推荐永远拿浑晶紫(色环第 0 个),其余按数组顺序循环
const candColor = (label: string, idx: number): string => {
  if (label === props.recommendedLabel) return CAND_COLORS[0];
  // 跳过 #0(留给推荐),从 #1 开始按 idx 取(避免颜色冲突)
  // 但若没推荐,所有 cand 用 #0,#1,#2... 自然顺序
  if (!props.recommendedLabel) return CAND_COLORS[idx % CAND_COLORS.length];
  return CAND_COLORS[(idx + 1) % CAND_COLORS.length];
};

// hover state — 高亮单个 candidate
const hoverIdx = ref<number>(-1);

// 极坐标 → 笛卡尔
function polar(angleDeg: number, ratio: number): { x: number; y: number } {
  const rad = (angleDeg * Math.PI) / 180;
  return {
    x: CENTER.value + RADIUS.value * ratio * Math.cos(rad),
    y: CENTER.value + RADIUS.value * ratio * Math.sin(rad),
  };
}

// 同心圆刻度(0.25 / 0.5 / 0.75 / 1.0)
const GRID_RATIOS = [0.25, 0.5, 0.75, 1.0];

// 每个 grid level 的 polygon(4 个轴的角点)
const gridPolygons = computed(() =>
  GRID_RATIOS.map(r => {
    const pts = AXES.map(a => polar(a.angle, r));
    return pts.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
  }),
);

// 4 个轴的端点(用来画轴线)
const axisEndpoints = computed(() =>
  AXES.map(a => polar(a.angle, 1.0)),
);

// 4 个轴的 label 位置(略外推)
const axisLabels = computed(() =>
  AXES.map(a => {
    const p = polar(a.angle, 1.15);
    return { ...a, ...p };
  }),
);

// 成功的候选(失败的不入图)
const successfulCandidates = computed(() =>
  props.candidates.filter(c => c.success && c.scores !== null),
);

// 每个 candidate 的 polygon
const candidatePolygons = computed(() =>
  successfulCandidates.value.map((c, idx) => {
    const scores = c.scores!;
    const points = AXES.map(a => {
      const val = (scores as unknown as Record<string, number>)[a.key] ?? 0;
      const ratio = Math.max(0, Math.min(1, val));
      return polar(a.angle, ratio);
    });
    return {
      label: c.provider_label,
      color: candColor(c.provider_label, idx),
      isRecommended: c.provider_label === props.recommendedLabel,
      pointsStr: points.map(p => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" "),
      points,
    };
  }),
);

function isFaded(label: string): boolean {
  if (hoverIdx.value < 0) return false;
  const hovered = successfulCandidates.value[hoverIdx.value];
  return hovered ? label !== hovered.provider_label : false;
}
</script>

<template>
  <div class="radar-wrap">
    <svg
      :width="SIZE"
      :height="SIZE"
      :viewBox="`0 0 ${SIZE} ${SIZE}`"
      class="radar-svg"
      @mouseleave="hoverIdx = -1"
    >
      <!-- 同心圆 grid -->
      <g class="grid">
        <polygon
          v-for="(pts, i) in gridPolygons"
          :key="`grid-${i}`"
          :points="pts"
          fill="none"
          stroke="var(--border)"
          stroke-width="0.5"
          stroke-dasharray="2 2"
          :opacity="i === GRID_RATIOS.length - 1 ? 0.6 : 0.3"
        />
        <!-- 刻度数字(仅顶轴标) -->
        <text
          v-for="(r, i) in GRID_RATIOS"
          :key="`gn-${i}`"
          :x="CENTER + 3"
          :y="CENTER - RADIUS * r + 3"
          class="grid-num"
        >
          {{ r.toFixed(2) }}
        </text>
      </g>

      <!-- 4 个轴线 -->
      <g class="axes">
        <line
          v-for="(p, i) in axisEndpoints"
          :key="`axis-${i}`"
          :x1="CENTER"
          :y1="CENTER"
          :x2="p.x"
          :y2="p.y"
          stroke="var(--border)"
          stroke-width="0.8"
        />
      </g>

      <!-- candidate polygons(推荐放最上方,所以反向遍历) -->
      <g class="candidates">
        <g
          v-for="(p, i) in candidatePolygons"
          :key="`cand-${i}`"
          class="cand-group"
          :class="{
            recommended: p.isRecommended,
            faded: isFaded(p.label),
          }"
        >
          <polygon
            :points="p.pointsStr"
            :fill="p.color"
            fill-opacity="0.15"
            :stroke="p.color"
            stroke-width="2"
            stroke-linejoin="round"
            class="cand-poly"
          />
          <!-- 顶点小圆 -->
          <circle
            v-for="(pt, j) in p.points"
            :key="`pt-${i}-${j}`"
            :cx="pt.x"
            :cy="pt.y"
            r="3"
            :fill="p.color"
            stroke="var(--card-bg)"
            stroke-width="1.5"
          />
        </g>
      </g>

      <!-- 轴 label(始终在顶层) -->
      <g class="axis-labels">
        <text
          v-for="a in axisLabels"
          :key="`al-${a.key}`"
          :x="a.x"
          :y="a.y"
          text-anchor="middle"
          dominant-baseline="middle"
          class="axis-label"
        >
          {{ a.label }}
        </text>
      </g>
    </svg>

    <!-- 图例 -->
    <ul class="legend">
      <li
        v-for="(c, i) in successfulCandidates"
        :key="`leg-${i}`"
        class="legend-item"
        :class="{ recommended: c.provider_label === props.recommendedLabel }"
        @mouseenter="hoverIdx = i"
        @mouseleave="hoverIdx = -1"
      >
        <span
          class="legend-swatch"
          :style="`background: ${candColor(c.provider_label, i)}`"
        ></span>
        <span class="legend-label">{{ c.provider_label }}</span>
        <span v-if="c.provider_label === props.recommendedLabel" class="legend-crown">🏆</span>
        <span class="legend-score mono">{{ ((c.scores?.overall ?? 0) * 100).toFixed(0) }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.radar-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
  padding: 14px;
  background: var(--bg-deep);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
}
.radar-svg {
  user-select: none;
}
.grid-num {
  font-size: 8.5px;
  fill: var(--text-muted);
  font-family: var(--font-mono);
  pointer-events: none;
}
.axis-label {
  font-size: 11.5px;
  font-family: var(--font-serif);
  fill: var(--text);
  pointer-events: none;
}

.cand-group {
  transition: opacity 200ms ease;
}
.cand-group.faded {
  opacity: 0.15;
}
.cand-group.recommended .cand-poly {
  stroke-width: 2.5;
}
.cand-poly {
  transition: fill-opacity 150ms;
}
.cand-group:not(.faded):hover .cand-poly {
  fill-opacity: 0.28;
}

/* 图例 */
.legend {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  justify-content: center;
  font-size: 11.5px;
  border-top: 1px solid var(--border-soft);
  padding-top: 12px;
  width: 100%;
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 8px;
  border-radius: var(--radius-sm);
  cursor: default;
  transition: background 150ms;
}
.legend-item:hover {
  background: var(--hover-bg);
}
.legend-item.recommended {
  background: var(--accent-soft);
}
.legend-swatch {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  flex-shrink: 0;
}
.legend-label {
  color: var(--text);
  font-weight: 500;
}
.legend-crown {
  font-size: 12px;
}
.legend-score {
  font-weight: 600;
  color: var(--accent-text);
  font-size: 11px;
}
.mono {
  font-family: var(--font-mono);
}
</style>
