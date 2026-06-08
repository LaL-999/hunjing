<script setup lang="ts">
/**
 * SimulationFamilyTreeView — 续作家族树可视化(2026-06-06)。
 *
 * 设计:
 *   - 后端 /api/projects/:id/simulation_family_tree 一次返所有 sim + 关系
 *   - 前端按 inheritance_depth 分行,SVG 手画垂直树状图(类 git log --graph)
 *   - 同 combination_run_id 的 sim 横向并排显示(批次 = 兄弟)
 *   - 节点 = sim 卡片(状态色 + 字数 + divergence 摘要)
 *   - 双击节点 → drawer 弹 narrative 前 800 字预览 + 跳详情按钮
 *
 * 节点位置算法:
 *   每个根开一个独立"列",根的所有后裔在同列垂直堆叠;
 *   反事实组合批次的 N 个兄弟横向并排在同一行(占 N 列宽度)。
 */
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { api, ApiError } from "../api/client";
import type {
  FamilyTreeCombinationRun,
  FamilyTreeNode,
  SimulationFamilyTreeResponse,
} from "../api/types";
import { toast } from "../composables/useToast";
import SkeletonBlock from "../components/SkeletonBlock.vue";

const props = defineProps<{
  id: string; // project_id
}>();

const router = useRouter();

const loading = ref(false);
const errMsg = ref<string | null>(null);
const data = ref<SimulationFamilyTreeResponse | null>(null);

async function load() {
  loading.value = true;
  errMsg.value = null;
  try {
    const resp = await api.get<SimulationFamilyTreeResponse>(
      `/projects/${props.id}/simulation_family_tree`,
    );
    data.value = resp;
  } catch (e) {
    errMsg.value = e instanceof ApiError ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

onMounted(load);

// ============================================================
// 布局算法:把扁平 nodes 排成"行 × 列"网格
// ============================================================

interface PositionedNode extends FamilyTreeNode {
  col: number;   // 列(同根的所有后裔在同列)
  row: number;   // 行(等于 inheritance_depth)
}

interface ComboGroupRender {
  combo: FamilyTreeCombinationRun;
  rootCol: number;       // 该批次在哪列开始(占 N 列宽)
  row: number;           // 在树的哪一行(取批次成员的 max depth)
  members: PositionedNode[];
}

const layout = computed<{
  positioned: PositionedNode[];
  comboGroups: ComboGroupRender[];
  totalCols: number;
  totalRows: number;
}>(() => {
  if (!data.value) return { positioned: [], comboGroups: [], totalCols: 0, totalRows: 0 };
  const nodes = data.value.nodes;

  // 1. 找根(parent_ids 空 且不属于任何 combo batch 或属于 batch 但是 batch 整体作为一个"组根")
  // 简化策略:每个根开一列;同一 combo batch 的 N 个 sim 占 N 列(并排)
  // 跨 combo 的 sim 自己一列

  // 找所有"独立根"(parent_ids 空 + 不在 combo)和"combo 根"(组成员 parent_ids 空且属于 combo)
  const comboMembers = new Map<string, FamilyTreeNode[]>();
  for (const n of nodes) {
    if (n.combination_run_id) {
      const arr = comboMembers.get(n.combination_run_id) ?? [];
      arr.push(n);
      comboMembers.set(n.combination_run_id, arr);
    }
  }
  // 排序 combo members 按 tree_path 字典序(让兄弟横向排列稳定)
  for (const arr of comboMembers.values()) {
    arr.sort((a, b) => (a.tree_path?.join("") ?? "").localeCompare(b.tree_path?.join("") ?? ""));
  }

  const positioned: PositionedNode[] = [];
  const positionedMap = new Map<string, PositionedNode>();
  const comboGroups: ComboGroupRender[] = [];
  let colCursor = 0;

  // 2. 先放 combo batches(按 created_at DESC 时间倒序 — 新批次靠左,与列表页对齐)
  const sortedCombos = [...(data.value.combination_runs ?? [])].sort(
    (a, b) => b.created_at.localeCompare(a.created_at),
  );
  for (const combo of sortedCombos) {
    const members = comboMembers.get(combo.id) ?? [];
    if (members.length === 0) continue;
    const startCol = colCursor;
    const memberRow = Math.max(...members.map((m) => m.inheritance_depth));
    const memberPositioned: PositionedNode[] = [];
    for (const m of members) {
      const p: PositionedNode = { ...m, col: colCursor, row: m.inheritance_depth };
      positioned.push(p);
      positionedMap.set(m.id, p);
      memberPositioned.push(p);
      colCursor++;
    }
    comboGroups.push({
      combo,
      rootCol: startCol,
      row: memberRow,
      members: memberPositioned,
    });
  }

  // 3. 再放独立根(parent_ids 空 + 不在任何 combo batch)
  const independentRoots = nodes.filter(
    (n) => n.parent_ids.length === 0 && !n.combination_run_id,
  );
  // 按 created_at DESC 排
  independentRoots.sort((a, b) => b.created_at.localeCompare(a.created_at));
  for (const r of independentRoots) {
    const p: PositionedNode = { ...r, col: colCursor, row: 0 };
    positioned.push(p);
    positionedMap.set(r.id, p);
    colCursor++;
  }

  // 4. 给所有非根 + 非 combo 成员的 sim 找列(继承直接父辈的列)
  // 同一父可能多个孩子(分支),孩子轮流偏移到右侧空列 — 简化为孩子用父列(允许重叠,SVG 再算偏移)
  const remaining = nodes.filter((n) => !positionedMap.has(n.id));
  // 按 inheritance_depth 升序拓扑放置
  remaining.sort((a, b) => a.inheritance_depth - b.inheritance_depth);
  for (const n of remaining) {
    const parents = n.parent_ids;
    if (parents.length === 0) {
      // 这种情况理论已在 step 3 处理,但兜底:开新列
      const p: PositionedNode = { ...n, col: colCursor, row: 0 };
      positioned.push(p);
      positionedMap.set(n.id, p);
      colCursor++;
      continue;
    }
    // 取最后一个 parent(直接父辈)的列
    const directParent = parents[parents.length - 1];
    const parentPos = positionedMap.get(directParent);
    if (parentPos) {
      // 检查该 (col, row) 是否已被占 — 占用则递增 col(分支偏移)
      let targetCol = parentPos.col;
      let targetRow = n.inheritance_depth;
      while (positioned.some((p) => p.col === targetCol && p.row === targetRow)) {
        targetCol++;
      }
      const p: PositionedNode = { ...n, col: targetCol, row: targetRow };
      positioned.push(p);
      positionedMap.set(n.id, p);
      colCursor = Math.max(colCursor, targetCol + 1);
    } else {
      // parent 不存在(数据异常),开新列兜底
      const p: PositionedNode = { ...n, col: colCursor, row: n.inheritance_depth };
      positioned.push(p);
      positionedMap.set(n.id, p);
      colCursor++;
    }
  }

  const totalRows = positioned.reduce((m, n) => Math.max(m, n.row), 0) + 1;
  return {
    positioned,
    comboGroups,
    totalCols: colCursor,
    totalRows,
  };
});

// SVG 几何参数(2026-06-06 v3 — 高度+宽度调整放下 3 行 divergence)
const COL_W = 300;
const ROW_H = 150;
const NODE_W = 270;
const NODE_H = 116;
const PADDING_X = 28;
const PADDING_Y = 40;

function nodeX(col: number): number {
  return PADDING_X + col * COL_W;
}
function nodeY(row: number): number {
  return PADDING_Y + row * ROW_H;
}

const svgWidth = computed(
  () => PADDING_X * 2 + Math.max(1, layout.value.totalCols) * COL_W,
);
const svgHeight = computed(
  () => PADDING_Y * 2 + Math.max(1, layout.value.totalRows) * ROW_H,
);

// 边:子节点到父节点(只画到直接父辈,即 parent_ids[-1])
interface EdgeRender {
  x1: number; y1: number; x2: number; y2: number;
  isMultiParent: boolean;  // 有多个 parent 时用虚线提示
}
const edges = computed<EdgeRender[]>(() => {
  const out: EdgeRender[] = [];
  const posMap = new Map<string, PositionedNode>(
    layout.value.positioned.map((p) => [p.id, p]),
  );
  for (const n of layout.value.positioned) {
    if (!n.parent_ids.length) continue;
    const directParent = n.parent_ids[n.parent_ids.length - 1];
    const p = posMap.get(directParent);
    if (!p) continue;
    out.push({
      x1: nodeX(p.col) + NODE_W / 2,
      y1: nodeY(p.row) + NODE_H,
      x2: nodeX(n.col) + NODE_W / 2,
      y2: nodeY(n.row),
      isMultiParent: n.parent_ids.length > 1,
    });
  }
  return out;
});

// ============================================================
// 节点视觉
// ============================================================

type StateBucket = "done" | "running" | "failed" | "pending" | "unknown";
function bucketOfState(state: string): StateBucket {
  if (state === "done" || state === "completed") return "done";
  if (state === "failed") return "failed";
  if (state === "generating" || state === "directing" || state === "composing") return "running";
  if (state === "queued") return "pending";
  return "unknown";
}

// 老 stateColor — drawer 内仍用(单点显色)
function stateColor(state: string): { bg: string; border: string; text: string } {
  switch (bucketOfState(state)) {
    case "done":    return { bg: "#ecfdf5", border: "#10b981", text: "#047857" };
    case "failed":  return { bg: "#fef2f2", border: "#ef4444", text: "#b91c1c" };
    case "running": return { bg: "#eef2ff", border: "#6366f1", text: "#4338ca" };
    case "pending": return { bg: "#f3f4f6", border: "#9ca3af", text: "#6b7280" };
    default:        return { bg: "#f3f0e8", border: "#c4bfb3", text: "#4a4640" };
  }
}

function stateLabel(state: string): string {
  switch (state) {
    case "done":
    case "completed": return "完成";
    case "failed": return "失败";
    case "generating":
    case "directing":
    case "composing": return "运行中";
    case "queued": return "排队中";
    default: return state;
  }
}

// ============================================================
// 双击节点 → drawer 预览
// ============================================================

const drawerOpen = ref(false);
const drawerNode = ref<FamilyTreeNode | null>(null);
const drawerPreview = ref<string>("");
const drawerLoading = ref(false);

async function openDrawer(node: FamilyTreeNode) {
  drawerNode.value = node;
  drawerOpen.value = true;
  drawerPreview.value = "";
  if (node.narrative_chars === 0) return;   // 还没 narrative 不拉
  drawerLoading.value = true;
  try {
    const detail = await api.get<{ narrative: string | null }>(
      `/simulations/${node.id}`,
    );
    drawerPreview.value = (detail.narrative ?? "").slice(0, 800);
  } catch (e) {
    drawerPreview.value = e instanceof ApiError ? `[加载失败:${e.message}]` : "[加载失败]";
  } finally {
    drawerLoading.value = false;
  }
}

function closeDrawer() {
  drawerOpen.value = false;
}

function gotoDetail(node: FamilyTreeNode) {
  router.push({
    name: "simulation-detail",
    params: { id: node.id },
    // 让 SimulationDetailView 的"返回项目"按钮回到家族树而不是项目作品列表
    query: { returnTo: "family-tree" },
  });
}

function gotoCompare(node: FamilyTreeNode) {
  if (!node.combination_run_id) {
    toast.info("该 sim 不属于反事实组合批次,无对比基准");
    return;
  }
  router.push({
    name: "counterfactual-tree",
    params: { id: props.id, combo_id: node.combination_run_id },
  });
}

function backToProject() {
  router.push({ name: "project", params: { id: props.id } });
}
</script>

<template>
  <div class="ft-view">
    <header class="ft-hdr">
      <button class="back-btn" @click="backToProject">← 返回项目</button>
      <h2>续作家族树</h2>
      <div v-if="data" class="ft-stats">
        <span class="stat-chip">{{ data.stats.total_sims }} 篇</span>
        <span class="stat-chip">{{ data.stats.roots }} 根</span>
        <span class="stat-chip">最深 {{ data.stats.max_depth + 1 }} 代</span>
        <span class="stat-chip">{{ data.stats.combo_batches }} 反事实批次</span>
      </div>
    </header>

    <div v-if="errMsg" class="err-banner">{{ errMsg }}</div>

    <div v-if="loading" class="ft-skeleton" aria-busy="true">
      <SkeletonBlock height="32px" width="160px" />
      <SkeletonBlock height="240px" />
    </div>

    <div v-else-if="data && data.nodes.length === 0" class="empty-state">
      <p class="empty-title">还没有任何推演作品</p>
      <p class="empty-hint">先回项目页生成第一篇 sim,家族树会自动生长</p>
    </div>

    <div v-else-if="data" class="ft-canvas">
      <svg
        :width="svgWidth"
        :height="svgHeight"
        class="ft-svg"
        :viewBox="`0 0 ${svgWidth} ${svgHeight}`"
      >
        <!-- 边(子→父连线)-->
        <g class="ft-edges">
          <path
            v-for="(e, i) in edges"
            :key="`edge-${i}`"
            :d="`M ${e.x1} ${e.y1} C ${e.x1} ${(e.y1 + e.y2) / 2}, ${e.x2} ${(e.y1 + e.y2) / 2}, ${e.x2} ${e.y2}`"
            fill="none"
            :stroke="e.isMultiParent ? '#c4b5fd' : '#d1d5db'"
            :stroke-width="e.isMultiParent ? 1.5 : 1.2"
            :stroke-dasharray="e.isMultiParent ? '4 4' : 'none'"
          />
        </g>

        <!-- combination batch 背景框(把同 batch 的 N 个兄弟框起来) -->
        <g class="ft-batch-bg">
          <rect
            v-for="(g, i) in layout.comboGroups"
            :key="`batch-${i}`"
            :x="nodeX(g.rootCol) - 8"
            :y="nodeY(0) - 14"
            :width="g.members.length * COL_W - (COL_W - NODE_W) + 8"
            :height="(g.row + 1) * ROW_H + 8"
            rx="10"
            fill="rgba(139, 92, 246, 0.04)"
            stroke="rgba(139, 92, 246, 0.18)"
            stroke-width="1"
            stroke-dasharray="3 3"
          />
          <text
            v-for="(g, i) in layout.comboGroups"
            :key="`batch-lbl-${i}`"
            :x="nodeX(g.rootCol) + 4"
            :y="nodeY(0) - 18"
            class="ft-batch-label"
          >反事实批次 · {{ g.combo.total_combinations }} 组</text>
        </g>

        <!-- 节点 — foreignObject 包 HTML,允许 ellipsis/line-clamp 且 hover 稳定 -->
        <g class="ft-nodes">
          <foreignObject
            v-for="n in layout.positioned"
            :key="n.id"
            :x="nodeX(n.col)"
            :y="nodeY(n.row)"
            :width="NODE_W"
            :height="NODE_H"
          >
            <div
              class="ft-node"
              :class="[
                `ft-node--${bucketOfState(n.state)}`,
                { 'ft-node--final': n.is_final_compilation },
              ]"
              @click="openDrawer(n)"
            >
              <div class="ft-node-row1">
                <span class="ft-node-dot" :class="`ft-dot--${bucketOfState(n.state)}`"></span>
                <span class="ft-node-depth">第 {{ n.inheritance_depth + 1 }} 代</span>
                <span class="ft-node-state">{{ stateLabel(n.state) }}</span>
              </div>
              <div class="ft-node-title">
                <template v-if="n.tree_path">
                  <span class="ft-path">[{{ n.tree_path.join(" · ") }}]</span>
                </template>
                <template v-else>
                  {{ n.divergence || "(无分歧点)" }}
                </template>
              </div>
              <div class="ft-node-row3">
                <span class="ft-node-chars">{{ n.narrative_chars.toLocaleString() }} 字</span>
                <span class="ft-node-sep">·</span>
                <span class="ft-node-mode">{{ n.mode }}</span>
                <span v-if="n.with_grand_finale" class="ft-node-finale" title="走向终章">★ 终章</span>
              </div>
            </div>
          </foreignObject>
        </g>
      </svg>
    </div>

    <!-- ============================================================
         drawer:节点预览
         ============================================================ -->
    <transition name="drawer-slide">
      <aside v-if="drawerOpen && drawerNode" class="ft-drawer" @click.stop>
        <header class="dr-hdr">
          <div class="dr-title">
            <span class="dr-depth">第 {{ drawerNode.inheritance_depth + 1 }} 代</span>
            <span
              class="dr-state"
              :style="{ color: stateColor(drawerNode.state).text }"
            >● {{ stateLabel(drawerNode.state) }}</span>
          </div>
          <button class="dr-close" @click="closeDrawer" aria-label="关闭">×</button>
        </header>

        <div class="dr-body">
          <p v-if="drawerNode.divergence" class="dr-divergence">
            <span class="dr-label">分歧点:</span>{{ drawerNode.divergence }}
          </p>
          <p v-if="drawerNode.tree_path" class="dr-tree-path">
            <span class="dr-label">反事实组合:</span>[{{ drawerNode.tree_path.join(" · ") }}]
          </p>
          <p class="dr-meta">
            <span>{{ drawerNode.narrative_chars.toLocaleString() }} 字</span>
            <span class="dr-meta-sep">·</span>
            <span>{{ drawerNode.mode }} 模式</span>
            <span v-if="drawerNode.with_grand_finale" class="dr-meta-sep">·</span>
            <span v-if="drawerNode.with_grand_finale" class="dr-finale">⭐ 走向终章</span>
          </p>

          <div class="dr-section">
            <div class="dr-section-hdr">narrative 预览(前 800 字)</div>
            <div
              v-if="drawerLoading"
              class="dr-preview-loading"
            >加载中…</div>
            <div
              v-else-if="!drawerPreview"
              class="dr-preview-empty"
            >还没 narrative</div>
            <pre v-else class="dr-preview">{{ drawerPreview }}<span v-if="drawerPreview.length >= 800" class="dr-more">…</span></pre>
          </div>
        </div>

        <footer class="dr-footer">
          <button
            v-if="drawerNode.combination_run_id"
            class="dr-btn dr-btn--ghost"
            @click="gotoCompare(drawerNode)"
          >⇆ 查看批次</button>
          <button class="dr-btn dr-btn--primary" @click="gotoDetail(drawerNode)">
            查看完整 →
          </button>
        </footer>
      </aside>
    </transition>
    <!-- drawer 背景遮罩(点击关闭) -->
    <transition name="mask-fade">
      <div v-if="drawerOpen" class="ft-drawer-mask" @click="closeDrawer" />
    </transition>
  </div>
</template>

<style scoped>
.ft-view {
  padding: 24px;
  max-width: 1600px;
  margin: 0 auto;
  position: relative;
}

.ft-hdr {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
}
.back-btn {
  background: var(--color-surface-2, #f1f5f9);
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: 7px;
  padding: 6px 12px;
  font-size: 12px;
  cursor: pointer;
  color: #4a4640;
}
.back-btn:hover {
  background: #f7f5f0;
}
.ft-hdr h2 {
  margin: 0;
  font-size: var(--text-xl, 18px);
  font-weight: 600;
}
.ft-stats {
  display: flex;
  gap: 6px;
  margin-left: auto;
}
.stat-chip {
  background: #f7f5f0;
  border: 1px solid #ece8de;
  border-radius: 999px;
  padding: 3px 10px;
  font-size: 11.5px;
  color: #6a665e;
  font-weight: 500;
}

.err-banner {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #b91c1c;
  padding: 10px 14px;
  border-radius: 8px;
  margin-bottom: 16px;
  font-size: 13px;
}

.ft-skeleton {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px 0;
}

.empty-state {
  text-align: center;
  padding: 80px 0;
  color: #9a968d;
}
.empty-title {
  font-size: 14px;
  font-weight: 500;
  color: #6a665e;
  margin: 0 0 6px;
}
.empty-hint {
  font-size: 12px;
  margin: 0;
}

/* ===== SVG 画布 ===== */
.ft-canvas {
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #ece8de);
  border-radius: 12px;
  padding: 12px;
  overflow: auto;
  max-height: calc(100vh - 220px);
}

.ft-svg {
  display: block;
}

/* ===== 节点(2026-06-06 v2 重设计 — 低饱和现代风,foreignObject + HTML)===== */
.ft-node {
  box-sizing: border-box;
  height: 100%;
  width: 100%;
  padding: 12px 14px;
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #ece8de);
  border-radius: 10px;
  cursor: pointer;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 4px;
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  transition: border-color 180ms, box-shadow 180ms;
  position: relative;
  overflow: hidden;
}
/* 左侧 2px 状态色 indicator(全身高)— 低调但可识别 */
.ft-node::before {
  content: "";
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 2px;
  background: #d1d5db;
}
.ft-node--done::before    { background: #10b981; }
.ft-node--running::before { background: #6366f1; }
.ft-node--pending::before { background: #9ca3af; }
.ft-node--failed::before  { background: #ef4444; }

/* hover — 只动 box-shadow + border 颜色,不动 transform(避免闪烁)*/
.ft-node:hover {
  border-color: #c4bfb3;
  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.06), 0 1px 3px rgba(0, 0, 0, 0.03);
}

/* 独立合并产物 — 金色虚线右上角小标签代替整个边框,更克制 */
.ft-node--final {
  border-color: #fcd34d;
}
.ft-node--final::after {
  content: "合并产物";
  position: absolute;
  top: 0;
  right: 0;
  background: #fef3c7;
  color: #92400e;
  font-size: 9.5px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: 0 9px 0 6px;
  letter-spacing: 0.04em;
}

/* row1 — 第 N 代 · 状态 */
.ft-node-row1 {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10.5px;
  color: #9a968d;
  font-weight: 500;
  letter-spacing: 0.04em;
}
.ft-node-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
  background: #d1d5db;
}
.ft-dot--done    { background: #10b981; }
.ft-dot--running {
  background: #6366f1;
  animation: ft-dot-pulse 1.4s ease-in-out infinite;
}
.ft-dot--pending { background: #9ca3af; }
.ft-dot--failed  { background: #ef4444; }
.ft-node-depth {
  color: #6a665e;
  font-weight: 600;
}
.ft-node-state {
  margin-left: auto;
  color: #9a968d;
}

/* row2 — divergence 主标题,3 行 line-clamp(短文案 1-2 行,长文案最多 3 行)*/
.ft-node-title {
  font-size: 12.5px;
  color: #2a2724;
  line-height: 1.55;
  font-weight: 500;
  flex: 1;                  /* 占满中间剩余空间 */
  min-height: 0;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
  word-break: break-all;
}
.ft-path {
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  color: #6d28d9;
  font-size: 12px;
  background: #ede9fe;
  padding: 1px 6px;
  border-radius: 4px;
}

/* row3 — 字数 / 模式 / 终章标 */
.ft-node-row3 {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10.5px;
  color: #9a968d;
  font-variant-numeric: tabular-nums;
}
.ft-node-chars {
  color: #4a4640;
  font-weight: 600;
}
.ft-node-sep { color: #d1cdc1; }
.ft-node-mode {
  text-transform: lowercase;
  letter-spacing: 0.02em;
}
.ft-node-finale {
  margin-left: auto;
  color: #d97706;
  font-weight: 600;
  background: #fef3c7;
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 9.5px;
}

@keyframes ft-dot-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.4; transform: scale(0.7); }
}

/* combination batch 标签 */
.ft-batch-label {
  font-size: 11px;
  fill: #8b5cf6;
  font-weight: 500;
}

/* ===== drawer ===== */
.ft-drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: 440px;
  max-width: 92vw;
  background: var(--color-surface, #fff);
  box-shadow: -8px 0 32px rgba(0, 0, 0, 0.12);
  z-index: 60;
  display: flex;
  flex-direction: column;
}
.ft-drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.18);
  backdrop-filter: blur(2px);
  z-index: 55;
}

.dr-hdr {
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border, #ece8de);
  display: flex;
  align-items: center;
  gap: 12px;
}
.dr-title {
  display: flex;
  align-items: center;
  gap: 8px;
  flex: 1;
}
.dr-depth {
  font-weight: 600;
  font-size: 14px;
  color: #1f1f1e;
}
.dr-state {
  font-size: 12px;
  font-weight: 500;
}
.dr-close {
  background: transparent;
  border: none;
  font-size: 22px;
  color: #9a968d;
  cursor: pointer;
  line-height: 1;
  padding: 4px 8px;
}
.dr-close:hover {
  color: #1f1f1e;
}

.dr-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
}
.dr-label {
  color: #6a665e;
  font-weight: 500;
  margin-right: 4px;
}
.dr-divergence,
.dr-tree-path {
  font-size: 13px;
  line-height: 1.6;
  color: #2a2724;
  margin: 0 0 10px;
}
.dr-tree-path {
  font-family: ui-monospace, "SF Mono", Menlo, monospace;
  color: #6d28d9;
}
.dr-meta {
  font-size: 12px;
  color: #6a665e;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin: 0 0 16px;
}
.dr-meta-sep {
  color: #c4bfb3;
}
.dr-finale {
  color: #d97706;
  font-weight: 500;
}

.dr-section {
  margin-top: 12px;
}
.dr-section-hdr {
  font-size: 11.5px;
  font-weight: 600;
  color: #6a665e;
  letter-spacing: 0.04em;
  margin-bottom: 8px;
  text-transform: uppercase;
}
.dr-preview {
  background: #faf7f2;
  border-radius: 8px;
  padding: 12px 14px;
  font-size: 13px;
  line-height: 1.75;
  color: #2a2724;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  max-height: 320px;
  overflow-y: auto;
}
.dr-more {
  color: #9a968d;
}
.dr-preview-loading,
.dr-preview-empty {
  text-align: center;
  padding: 20px 0;
  font-size: 12px;
  color: #9a968d;
}

.dr-footer {
  padding: 14px 20px;
  border-top: 1px solid var(--color-border, #ece8de);
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
.dr-btn {
  padding: 8px 16px;
  border-radius: 7px;
  font-size: 13px;
  cursor: pointer;
  border: 1px solid var(--color-border, #ece8de);
  transition: background 150ms, border-color 150ms;
}
.dr-btn--ghost {
  background: transparent;
  color: #6a665e;
}
.dr-btn--ghost:hover {
  background: #f7f5f0;
  border-color: #c4bfb3;
}
.dr-btn--primary {
  background: #8b5cf6;
  color: white;
  border-color: #8b5cf6;
  font-weight: 500;
}
.dr-btn--primary:hover {
  background: #7c3aed;
}

/* ===== 动画 ===== */
.drawer-slide-enter-active,
.drawer-slide-leave-active {
  transition: transform 220ms cubic-bezier(0.4, 0, 0.2, 1);
}
.drawer-slide-enter-from,
.drawer-slide-leave-to {
  transform: translateX(100%);
}
.mask-fade-enter-active,
.mask-fade-leave-active {
  transition: opacity 200ms;
}
.mask-fade-enter-from,
.mask-fade-leave-to {
  opacity: 0;
}
</style>
