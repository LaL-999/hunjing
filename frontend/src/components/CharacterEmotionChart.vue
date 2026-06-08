<script setup lang="ts">
/**
 * CharacterEmotionChart — 单角色 8 维情绪折线图(Sprint 6.A2 路线图 #2,2026-05-22)。
 *
 * 数据源:GET /api/simulations/{sim_id}/emotional_states 按 character_id group 后传入。
 * 视觉:纯 SVG 手画,无 chart 库依赖,完全 token 化配色。
 *
 * 输入:
 *   characterName  string                       角色名(标题用)
 *   records        EmotionalStateRecord[]       已按 scene_index 升序排序的某角色全部情绪记录
 *
 * 设计:
 *   - 横轴 scene_index(每幕 1 标签),纵轴 0-10 整数
 *   - 8 色折线 + 数据点,hover 显 `<title>` tooltip(浏览器原生)
 *   - 单幕(sceneCount=1)时不画折线,只画 8 个点
 *   - 0 幕:父组件应 v-if 兜底不渲染本组件,本组件自带空态文案兜底防意外
 *   - 配色:Plutchik 真色降饱和度,8 维距离足够 + 不抢浑晶紫主色
 */
import { computed } from "vue";

import {
  EMOTION_KEYS,
  EMOTION_LABELS_CN,
  type EmotionKey,
  type EmotionalStateRecord,
} from "../api/types";

const props = defineProps<{
  characterName: string;
  records: EmotionalStateRecord[];
}>();

/** 8 维情绪配色:Plutchik 真色调微降饱和度,与浑晶米白底协调
 *  disgust 用棕灰(腐败感),避免与 joy / trust 同色家族;不引主色 #7C3AED 防冲突 */
const EMOTION_COLORS: Record<EmotionKey, string> = {
  joy:          "#E6B833",   // 暖金黄
  trust:        "#4FA86F",   // 翠绿
  fear:         "#4A7C9A",   // 海蓝
  surprise:     "#6FAEAE",   // 灰青
  sadness:      "#4A6CB0",   // 沉蓝
  disgust:      "#8B6F3F",   // 棕灰
  anger:        "#C24555",   // 砖红
  anticipation: "#DC8E47",   // 暖橙
};

// SVG viewBox 600 × 220;padding 给坐标轴预留空间
const VIEWBOX_W = 600;
const VIEWBOX_H = 220;
const PAD_L = 40;
const PAD_R = 20;
const PAD_T = 20;
const PAD_B = 30;
const DRAW_W = VIEWBOX_W - PAD_L - PAD_R;
const DRAW_H = VIEWBOX_H - PAD_T - PAD_B;

const sceneCount = computed(() => props.records.length);
const hasMultipleScenes = computed(() => sceneCount.value > 1);
/** 1 幕时所有点画在水平居中位置,>1 幕按等间距分布 */
const stepX = computed(() =>
  hasMultipleScenes.value ? DRAW_W / (sceneCount.value - 1) : 0,
);

function yForValue(v: number): number {
  // v ∈ [0, 10] → y ∈ [底, 顶];y 轴向下增长所以 high value = small y
  return PAD_T + DRAW_H - (v / 10) * DRAW_H;
}

function xForIndex(i: number): number {
  if (!hasMultipleScenes.value) return PAD_L + DRAW_W / 2;
  return PAD_L + i * stepX.value;
}

const polylines = computed(() =>
  EMOTION_KEYS.map((key) => ({
    key,
    color: EMOTION_COLORS[key],
    label: EMOTION_LABELS_CN[key],
    points: props.records
      .map((r, i) => `${xForIndex(i)},${yForValue(r.emotion[key])}`)
      .join(" "),
  })),
);

const yTicks = [0, 5, 10];
const xTicks = computed(() =>
  props.records.map((r, i) => ({
    x: xForIndex(i),
    label: `幕${r.scene_index + 1}`,
  })),
);
</script>

<template>
  <div class="emotion-chart surface">
    <header class="chart-header">
      <h4 class="chart-title">{{ characterName }}</h4>
      <span class="chart-meta mono">{{ sceneCount }} 幕</span>
    </header>

    <div v-if="sceneCount === 0" class="chart-empty">
      此角色暂无情绪记录
    </div>

    <template v-else>
      <svg
        :viewBox="`0 0 ${VIEWBOX_W} ${VIEWBOX_H}`"
        class="chart-svg"
        role="img"
        :aria-label="`${characterName} 角色 8 维情绪曲线`"
        preserveAspectRatio="xMidYMid meet"
      >
        <!-- Y 轴网格 + 刻度 -->
        <g class="chart-grid">
          <line
            v-for="v in yTicks"
            :key="`y-${v}`"
            :x1="PAD_L"
            :x2="VIEWBOX_W - PAD_R"
            :y1="yForValue(v)"
            :y2="yForValue(v)"
            class="grid-line"
          />
          <text
            v-for="v in yTicks"
            :key="`yt-${v}`"
            :x="PAD_L - 6"
            :y="yForValue(v) + 4"
            class="axis-label"
            text-anchor="end"
          >{{ v }}</text>
        </g>

        <!-- X 轴刻度 -->
        <g class="chart-axis">
          <text
            v-for="t in xTicks"
            :key="`xt-${t.label}`"
            :x="t.x"
            :y="VIEWBOX_H - PAD_B + 16"
            class="axis-label"
            text-anchor="middle"
          >{{ t.label }}</text>
        </g>

        <!-- 8 条情绪折线(仅 ≥ 2 幕时画线)-->
        <g v-if="hasMultipleScenes" class="chart-lines">
          <polyline
            v-for="line in polylines"
            :key="`line-${line.key}`"
            :points="line.points"
            :stroke="line.color"
            fill="none"
            stroke-width="1.5"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </g>

        <!-- 数据点 + 浏览器原生 hover tooltip -->
        <g class="chart-dots">
          <template v-for="line in polylines" :key="`dots-${line.key}`">
            <circle
              v-for="(r, i) in records"
              :key="`${line.key}-${i}`"
              :cx="xForIndex(i)"
              :cy="yForValue(r.emotion[line.key])"
              r="2.5"
              :fill="line.color"
            >
              <title>{{ line.label }} = {{ r.emotion[line.key] }} · 幕{{ r.scene_index + 1 }}</title>
            </circle>
          </template>
        </g>
      </svg>

      <!-- 图例 -->
      <div class="chart-legend">
        <span
          v-for="line in polylines"
          :key="`legend-${line.key}`"
          class="legend-item"
        >
          <span class="legend-dot" :style="{ background: line.color }" aria-hidden="true"></span>
          <span class="legend-label">{{ line.label }}</span>
        </span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.emotion-chart {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
}

.chart-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.chart-title {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.chart-meta {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

.chart-svg {
  width: 100%;
  height: auto;
  display: block;
}

.grid-line {
  stroke: var(--color-border);
  stroke-width: 1;
  stroke-dasharray: 2 3;
}

.axis-label {
  font-size: 10px;
  fill: var(--color-text-muted);
  font-family: var(--font-mono);
}

.chart-legend {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  padding-top: var(--space-1);
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.legend-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.chart-empty {
  padding: var(--space-4);
  color: var(--color-text-subtle);
  font-size: var(--text-sm);
  text-align: center;
}
</style>
