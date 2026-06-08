<script setup lang="ts">
/**
 * 角色关系图 — SVG 力导向(2D)。
 *
 * 阶段 8.2(2026-06-08):自己实现 — 不引 d3-force / cytoscape / antv g6 等
 * 第三方库,~200 行代码 + 完全控制视觉。
 *
 * 算法(简化 Fruchterman-Reingold):
 *   - 每帧:所有节点对的库仑斥力 + 边的胡克引力 + 中心吸引力
 *   - 速度阻尼避免震荡
 *   - 60 帧停止迭代,布局稳定后允许鼠标拖拽节点(高级特性 — 此版先不做拖拽)
 *
 * 视觉:
 *   - 节点大小:weight(戏份)开方
 *   - 节点颜色:role_tier(主角紫 / 配角蓝 / 龙套灰 / 群演淡灰)
 *   - 节点外圈:has_bridge_assets 时高亮(虚线 accent 色)= 显形桥接接通
 *   - 边颜色:polarity(positive=绿 / negative=红 / neutral=灰 / 未标=淡灰虚线)
 *   - 边粗细:固定 1.2
 *   - 边 label:type(夫妻 / 父子 / 同事...)
 *
 * 交互:
 *   - hover 节点 → 高亮该节点及其所有邻居,其他节点淡化
 *   - hover 边 → 显示 description tooltip
 *   - 点击节点 → emit 'select-character' 让父组件高亮卡片
 */
import { computed, onMounted, onBeforeUnmount, ref, watch } from "vue";

import type {
  GraphNodeApi,
  GraphEdgeApi,
} from "../api/screenplay-client";

const props = defineProps<{
  nodes: GraphNodeApi[];
  edges: GraphEdgeApi[];
  selectedId?: string;
  width?: number;
  height?: number;
}>();

const emit = defineEmits<{
  (e: "select-character", id: string): void;
  (e: "hover-edge", edge: GraphEdgeApi | null): void;
}>();

const W = computed(() => props.width ?? 720);
const H = computed(() => props.height ?? 480);

interface SimNode {
  id: string;
  name: string;
  role_tier: string;
  weight: number;
  is_protagonist: boolean;
  has_bridge_assets: boolean;
  x: number;
  y: number;
  vx: number;
  vy: number;
}

const simNodes = ref<SimNode[]>([]);
const hoverNodeId = ref<string>("");
const hoverEdgeIdx = ref<number>(-1);

// 2026-06-08 用户精修:拖拽支持
const svgRef = ref<SVGSVGElement | null>(null);
const draggingId = ref<string>("");          // 当前拖拽的节点 id
let dragMoved = false;                       // 区分单击 vs 拖动(防 click 误触)
let dragOffsetX = 0;                         // 鼠标按下时,鼠标 - 节点中心的偏移
let dragOffsetY = 0;

let rafId: number | null = null;
let iter = 0;

const adjacency = computed(() => {
  // for each node id → set of neighbor ids
  const m = new Map<string, Set<string>>();
  for (const n of props.nodes) m.set(n.id, new Set());
  for (const e of props.edges) {
    m.get(e.source)?.add(e.target);
    m.get(e.target)?.add(e.source);
  }
  return m;
});

function initSim() {
  // 节点初始化:圆周分布(比随机更稳定)
  const n = props.nodes.length;
  if (n === 0) {
    simNodes.value = [];
    return;
  }
  const cx = W.value / 2;
  const cy = H.value / 2;
  const radius = Math.min(W.value, H.value) * 0.35;
  simNodes.value = props.nodes.map((node, i) => {
    const angle = (i / n) * Math.PI * 2 - Math.PI / 2;
    return {
      ...node,
      x: cx + radius * Math.cos(angle),
      y: cy + radius * Math.sin(angle),
      vx: 0,
      vy: 0,
    };
  });
  iter = 0;
  if (rafId !== null) cancelAnimationFrame(rafId);
  step();
}

function step() {
  const nodes = simNodes.value;
  if (nodes.length === 0) return;
  const n = nodes.length;
  const W_VAL = W.value;
  const H_VAL = H.value;
  const cx = W_VAL / 2;
  const cy = H_VAL / 2;

  // Coulomb repulsion
  const k = Math.sqrt((W_VAL * H_VAL) / n) * 0.6;
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      const a = nodes[i];
      const b = nodes[j];
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let d = Math.sqrt(dx * dx + dy * dy);
      if (d < 0.01) {
        dx = (Math.random() - 0.5) * 1;
        dy = (Math.random() - 0.5) * 1;
        d = 1;
      }
      const force = (k * k) / d;
      const fx = (dx / d) * force * 0.025;
      const fy = (dy / d) * force * 0.025;
      a.vx -= fx;
      a.vy -= fy;
      b.vx += fx;
      b.vy += fy;
    }
  }

  // Hooke spring (edges)
  const idxById = new Map<string, number>();
  for (let i = 0; i < n; i++) idxById.set(nodes[i].id, i);
  for (const e of props.edges) {
    const i = idxById.get(e.source);
    const j = idxById.get(e.target);
    if (i === undefined || j === undefined) continue;
    const a = nodes[i];
    const b = nodes[j];
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const d = Math.sqrt(dx * dx + dy * dy);
    if (d < 0.01) continue;
    const ideal = k * 1.2;
    const force = (d - ideal) * 0.04;
    const fx = (dx / d) * force;
    const fy = (dy / d) * force;
    a.vx += fx;
    a.vy += fy;
    b.vx -= fx;
    b.vy -= fy;
  }

  // Center gravity + damping
  const damp = 0.78;
  for (const node of nodes) {
    node.vx += (cx - node.x) * 0.005;
    node.vy += (cy - node.y) * 0.005;
    node.vx *= damp;
    node.vy *= damp;
    node.x += node.vx;
    node.y += node.vy;
    // 边界约束
    const margin = 30;
    node.x = Math.max(margin, Math.min(W_VAL - margin, node.x));
    node.y = Math.max(margin, Math.min(H_VAL - margin, node.y));
  }

  iter++;
  if (iter < 80) {
    rafId = requestAnimationFrame(step);
  } else {
    rafId = null;
  }
}

onMounted(() => initSim());
onBeforeUnmount(() => {
  if (rafId !== null) cancelAnimationFrame(rafId);
});

watch(
  () => [props.nodes.map(n => n.id).join(","), props.edges.length, W.value, H.value],
  () => initSim(),
);

// 视觉:role_tier → 节点颜色 + 大小
const NODE_COLOR: Record<string, string> = {
  protagonist: "var(--accent)",
  supporting: "#5d8aa8",       // 钢蓝
  bit_part: "#8a8479",         // 灰褐
  extra: "var(--text-muted)",
};

function nodeRadius(weight: number): number {
  // weight 1-50,半径 6-20
  return Math.max(6, Math.min(20, 6 + Math.sqrt(weight) * 2));
}

const EDGE_STYLE = {
  positive: { color: "#5f8f6a", dash: "" },         // 山岚绿,实线
  negative: { color: "#c53030", dash: "" },         // 砖红,实线
  neutral: { color: "#8a8479", dash: "" },          // 灰褐,实线
  unset: { color: "var(--border)", dash: "4 3" },   // 淡灰虚线
};

function edgeStyle(polarity: GraphEdgeApi["polarity"]) {
  if (!polarity) return EDGE_STYLE.unset;
  return EDGE_STYLE[polarity];
}

const highlighted = computed(() => {
  if (!hoverNodeId.value) return new Set<string>();
  const set = new Set<string>([hoverNodeId.value]);
  const neighbors = adjacency.value.get(hoverNodeId.value);
  if (neighbors) for (const n of neighbors) set.add(n);
  return set;
});

function isFaded(id: string): boolean {
  if (!hoverNodeId.value) return false;
  return !highlighted.value.has(id);
}

function isEdgeFaded(e: GraphEdgeApi): boolean {
  if (!hoverNodeId.value) return false;
  return !(highlighted.value.has(e.source) && highlighted.value.has(e.target));
}

function handleNodeClick(node: SimNode) {
  // 2026-06-08:拖完不触发 select(防"拖动一下就跳卡片"的烦感)
  if (dragMoved) {
    dragMoved = false;
    return;
  }
  emit("select-character", node.id);
}

// ============================================================
// 2026-06-08 拖拽:气泡般滑溜的交互(用户原话)
// ============================================================

/** 把鼠标 client 坐标换算到 SVG viewBox 坐标系 */
function svgFromClient(e: MouseEvent): { x: number; y: number } | null {
  const svg = svgRef.value;
  if (!svg) return null;
  const pt = svg.createSVGPoint();
  pt.x = e.clientX;
  pt.y = e.clientY;
  const ctm = svg.getScreenCTM();
  if (!ctm) return null;
  const t = pt.matrixTransform(ctm.inverse());
  return { x: t.x, y: t.y };
}

function startDrag(node: SimNode, e: MouseEvent) {
  e.preventDefault();
  e.stopPropagation();
  const p = svgFromClient(e);
  if (!p) return;
  draggingId.value = node.id;
  dragMoved = false;
  dragOffsetX = p.x - node.x;
  dragOffsetY = p.y - node.y;

  // 停掉力学迭代,拖拽时不抢主权
  if (rafId !== null) {
    cancelAnimationFrame(rafId);
    rafId = null;
  }
  // 拖拽期间,该节点速度归零,免得 release 后被旧速度甩飞
  const target = simNodes.value.find(n => n.id === node.id);
  if (target) { target.vx = 0; target.vy = 0; }

  window.addEventListener("mousemove", onDrag);
  window.addEventListener("mouseup", endDrag);
}

function onDrag(e: MouseEvent) {
  if (!draggingId.value) return;
  const p = svgFromClient(e);
  if (!p) return;
  const node = simNodes.value.find(n => n.id === draggingId.value);
  if (!node) return;
  // 标记真发生了拖动(超过 3px 阈值)— 用于 click 判别
  if (!dragMoved) {
    const dx = (p.x - dragOffsetX) - node.x;
    const dy = (p.y - dragOffsetY) - node.y;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) dragMoved = true;
  }
  // 边界 clamp
  const margin = 30;
  node.x = Math.max(margin, Math.min(W.value - margin, p.x - dragOffsetX));
  node.y = Math.max(margin, Math.min(H.value - margin, p.y - dragOffsetY));
  node.vx = 0;
  node.vy = 0;
}

function endDrag() {
  if (!draggingId.value) return;
  draggingId.value = "";
  window.removeEventListener("mousemove", onDrag);
  window.removeEventListener("mouseup", endDrag);

  // 释放后重启力学几帧,让邻居"被拽"过去复位 — 气泡般滑溜的灵魂
  iter = Math.max(0, iter - 30);   // 拨回 30 帧的活力度
  if (rafId === null) {
    rafId = requestAnimationFrame(step);
  }
}

function handleEdgeHover(idx: number, edge: GraphEdgeApi | null) {
  hoverEdgeIdx.value = idx;
  emit("hover-edge", edge);
}

// edge label 居中位置
function edgeLabel(e: GraphEdgeApi): { x: number; y: number; text: string } | null {
  const a = simNodes.value.find(n => n.id === e.source);
  const b = simNodes.value.find(n => n.id === e.target);
  if (!a || !b) return null;
  return {
    x: (a.x + b.x) / 2,
    y: (a.y + b.y) / 2,
    text: e.type || "",
  };
}
</script>

<template>
  <div class="relgraph-wrap">
    <svg
      ref="svgRef"
      class="relgraph-svg"
      :viewBox="`0 0 ${W} ${H}`"
      :width="W"
      :height="H"
      preserveAspectRatio="xMidYMid meet"
      :class="{ 'is-dragging': draggingId !== '' }"
      @mouseleave="hoverNodeId = ''"
    >
      <!-- 边 -->
      <g class="edges">
        <template v-for="(e, idx) in props.edges" :key="`e-${idx}`">
          <line
            v-if="simNodes.find(n => n.id === e.source) && simNodes.find(n => n.id === e.target)"
            :x1="simNodes.find(n => n.id === e.source)!.x"
            :y1="simNodes.find(n => n.id === e.source)!.y"
            :x2="simNodes.find(n => n.id === e.target)!.x"
            :y2="simNodes.find(n => n.id === e.target)!.y"
            :stroke="edgeStyle(e.polarity).color"
            :stroke-dasharray="edgeStyle(e.polarity).dash"
            stroke-width="1.5"
            :opacity="isEdgeFaded(e) ? 0.12 : 0.7"
            class="edge-line"
            @mouseenter="handleEdgeHover(idx, e)"
            @mouseleave="handleEdgeHover(-1, null)"
          />
        </template>
      </g>

      <!-- 边 label(浅淡,只在 hover 才完整显示) -->
      <g class="edge-labels">
        <template v-for="(e, idx) in props.edges" :key="`el-${idx}`">
          <text
            v-if="edgeLabel(e)"
            :x="edgeLabel(e)!.x"
            :y="edgeLabel(e)!.y - 4"
            text-anchor="middle"
            class="edge-label-text"
            :opacity="hoverEdgeIdx === idx ? 1 : (isEdgeFaded(e) ? 0 : 0.5)"
          >
            {{ edgeLabel(e)!.text }}
          </text>
        </template>
      </g>

      <!-- 节点 -->
      <g class="nodes">
        <g
          v-for="node in simNodes"
          :key="node.id"
          class="node-group"
          :class="{
            faded: isFaded(node.id),
            selected: node.id === props.selectedId,
            'is-dragged': draggingId === node.id,
          }"
          @mouseenter="hoverNodeId = node.id"
          @mousedown="startDrag(node, $event)"
          @click="handleNodeClick(node)"
        >
          <!-- 桥接外圈虚线(has_bridge_assets 时) -->
          <circle
            v-if="node.has_bridge_assets"
            :cx="node.x"
            :cy="node.y"
            :r="nodeRadius(node.weight) + 4"
            fill="none"
            stroke="var(--accent)"
            stroke-width="1"
            stroke-dasharray="3 2"
            class="node-bridge-ring"
          />
          <!-- 主圈 -->
          <circle
            :cx="node.x"
            :cy="node.y"
            :r="nodeRadius(node.weight)"
            :fill="NODE_COLOR[node.role_tier] || NODE_COLOR.extra"
            :stroke="node.id === props.selectedId ? 'var(--accent)' : 'var(--card-bg)'"
            :stroke-width="node.id === props.selectedId ? 3 : 1.5"
            class="node-circle"
          />
          <!-- 名字 -->
          <text
            :x="node.x"
            :y="node.y + nodeRadius(node.weight) + 14"
            text-anchor="middle"
            class="node-label"
          >
            {{ node.name }}
          </text>
        </g>
      </g>
    </svg>

    <!-- 图例 -->
    <div class="legend">
      <span class="legend-title">图例</span>
      <span class="legend-item">
        <span class="dot" :style="{ background: NODE_COLOR.protagonist }"></span>主角
      </span>
      <span class="legend-item">
        <span class="dot" :style="{ background: NODE_COLOR.supporting }"></span>配角
      </span>
      <span class="legend-item">
        <span class="dot" :style="{ background: NODE_COLOR.bit_part }"></span>龙套
      </span>
      <span class="legend-item">
        <span class="dot dot-ring"></span>已接通父平台资产
      </span>
      <span class="legend-sep">|</span>
      <span class="legend-item">
        <span class="line" :style="{ background: EDGE_STYLE.positive.color }"></span>正向
      </span>
      <span class="legend-item">
        <span class="line" :style="{ background: EDGE_STYLE.negative.color }"></span>负向
      </span>
      <span class="legend-item">
        <span class="line line-dashed"></span>未标 polarity
      </span>
    </div>
  </div>
</template>

<style scoped>
.relgraph-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  background: var(--bg-deep);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  padding: 16px;
}
.relgraph-svg {
  cursor: default;
  user-select: none;
}
/* 2026-06-08:拖拽中 — 全 SVG 光标变 grabbing,视觉反馈"我在抓气泡" */
.relgraph-svg.is-dragging {
  cursor: grabbing;
}
.node-group {
  cursor: grab;
  transition: opacity 200ms ease-out;
}
.node-group:active {
  cursor: grabbing;
}
.relgraph-svg.is-dragging .node-group {
  cursor: grabbing;
}
.node-group.faded {
  opacity: 0.18;
}
/* 被拖拽的节点 — 主圈变粗 + 微弱光晕,像"气泡被拽" */
.node-group.is-dragged .node-circle {
  filter: brightness(1.2) drop-shadow(0 0 6px var(--accent));
  stroke-width: 3;
}
.node-circle {
  transition: filter 150ms, stroke-width 150ms;
}
.node-group:hover .node-circle {
  filter: brightness(1.15);
}
.node-bridge-ring {
  animation: bridge-pulse 2.4s ease-in-out infinite;
}
@keyframes bridge-pulse {
  0%, 100% { opacity: 0.6; }
  50% { opacity: 1; }
}
.node-label {
  font-size: 12px;
  font-family: var(--font-serif);
  fill: var(--text);
  pointer-events: none;
}
.edge-line {
  cursor: pointer;
  transition: opacity 150ms, stroke-width 100ms;
}
.edge-line:hover {
  stroke-width: 2.5;
}
.edge-label-text {
  font-size: 10px;
  fill: var(--text-muted);
  pointer-events: none;
  transition: opacity 150ms;
}

/* 图例 */
.legend {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  font-size: 10.5px;
  color: var(--text-muted);
  align-items: center;
  letter-spacing: 0.04em;
  padding-top: 8px;
  border-top: 1px solid var(--border-soft);
  width: 100%;
  justify-content: center;
}
.legend-title {
  font-weight: 600;
  color: var(--text);
  margin-right: 4px;
}
.legend-item {
  display: inline-flex;
  align-items: center;
  gap: 5px;
}
.legend-sep {
  color: var(--border);
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  display: inline-block;
}
.dot-ring {
  border: 1.5px dashed var(--accent);
  background: transparent !important;
  box-sizing: border-box;
}
.line {
  width: 18px;
  height: 1.5px;
  display: inline-block;
}
.line-dashed {
  background: transparent;
  border-top: 1.5px dashed var(--border);
  height: 0;
  width: 18px;
}
</style>
