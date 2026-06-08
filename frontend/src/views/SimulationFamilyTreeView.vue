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
      <button class="back-btn" @click="backToProject" title="返回项目">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
          <path d="M19 12H5M12 19l-7-7 7-7" />
        </svg>
        <span>项目</span>
      </button>
      <div class="ft-title-block">
        <h2 class="ft-title">续作家族树</h2>
        <p v-if="data" class="ft-meta">
          <span class="meta-num mono">{{ data.stats.total_sims }}</span>
          <span class="meta-lbl">篇推演</span>
          <span class="meta-dot">·</span>
          <span class="meta-num mono">{{ data.stats.roots }}</span>
          <span class="meta-lbl">根</span>
          <span class="meta-dot">·</span>
          <span class="meta-lbl">最深</span>
          <span class="meta-num mono">{{ data.stats.max_depth + 1 }}</span>
          <span class="meta-lbl">代</span>
          <span v-if="data.stats.combo_batches > 0" class="meta-dot">·</span>
          <template v-if="data.stats.combo_batches > 0">
            <span class="meta-num mono">{{ data.stats.combo_batches }}</span>
            <span class="meta-lbl">反事实批次</span>
          </template>
        </p>
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
        <!-- 边(子→父连线)— 2026-06-08 改 class 让 CSS 控制色,响应双主题 -->
        <g class="ft-edges">
          <path
            v-for="(e, i) in edges"
            :key="`edge-${i}`"
            :d="`M ${e.x1} ${e.y1} C ${e.x1} ${(e.y1 + e.y2) / 2}, ${e.x2} ${(e.y1 + e.y2) / 2}, ${e.x2} ${e.y2}`"
            fill="none"
            :class="e.isMultiParent ? 'ft-edge ft-edge--cf' : 'ft-edge ft-edge--default'"
          />
        </g>

        <!-- combination batch 容器(2026-06-08 重设计:去虚线 PPT 感,改左侧 indicator + 顶部标签) -->
        <g class="ft-batch-bg">
          <!-- 左边 2px 紫色 indicator 表示 batch 起始边界 -->
          <line
            v-for="(g, i) in layout.comboGroups"
            :key="`batch-line-${i}`"
            :x1="nodeX(g.rootCol) - 12"
            :y1="nodeY(0) - 6"
            :x2="nodeX(g.rootCol) - 12"
            :y2="nodeY(g.row) + NODE_H + 6"
            stroke="var(--color-accent)"
            stroke-width="2"
            stroke-linecap="round"
            opacity="0.55"
          />
          <text
            v-for="(g, i) in layout.comboGroups"
            :key="`batch-lbl-${i}`"
            :x="nodeX(g.rootCol) - 4"
            :y="nodeY(0) - 14"
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
                <span v-if="n.with_grand_finale" class="ft-node-finale" title="走向终章">
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="currentColor" stroke="none">
                    <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
                  </svg>
                  终章
                </span>
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
            >
              <span class="dr-state-dot" :style="{ background: stateColor(drawerNode.state).text }"></span>
              {{ stateLabel(drawerNode.state) }}
            </span>
          </div>
          <button class="dr-close" @click="closeDrawer" aria-label="关闭">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.5" stroke-linecap="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
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
            <span v-if="drawerNode.with_grand_finale" class="dr-finale">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" stroke="none">
                <path d="M12 2l3.09 6.26L22 9.27l-5 4.87 1.18 6.88L12 17.77l-6.18 3.25L7 14.14 2 9.27l6.91-1.01L12 2z" />
              </svg>
              走向终章
            </span>
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
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M7 16V4 M3 8l4-4 4 4 M17 8v12 M21 16l-4 4-4-4" />
            </svg>
            查看批次
          </button>
          <button class="dr-btn dr-btn--primary" @click="gotoDetail(drawerNode)">
            <span>查看完整</span>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">
              <path d="M5 12h14 M12 5l7 7-7 7" />
            </svg>
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
/* 2026-06-08 UI 大升级 T10:
 *   - 全部硬编码色 → var(--color-*) tokens(响应双主题)
 *   - chip 药丸 → inline meta(像剧创态共 N 篇风格)
 *   - back-btn 去米色框 → ghost(透明 hover 出底)
 *   - canvas 加 faint grid 背景纹理(去空旷)
 */

.ft-view {
  padding: var(--space-6) var(--space-8);
  max-width: 1600px;
  margin: 0 auto;
  position: relative;
  background: var(--color-bg);
  min-height: calc(100vh - var(--topbar-height));
}

.ft-hdr {
  display: flex;
  align-items: center;
  gap: var(--space-5);
  margin-bottom: var(--space-6);
  padding-bottom: var(--space-4);
  border-bottom: 1px solid var(--color-border);
}
.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  padding: 5px 10px 5px 8px;
  font-size: var(--text-xs);
  cursor: pointer;
  color: var(--color-text-muted);
  transition: all var(--duration-fast) var(--ease-out);
}
.back-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.ft-title-block {
  display: flex;
  flex-direction: column;
  gap: 4px;
  min-width: 0;
  flex: 1;
}
.ft-title {
  margin: 0;
  font-size: var(--text-xl);
  font-weight: 500;
  color: var(--color-text);
  font-family: var(--font-serif);
  letter-spacing: 0.01em;
}
.ft-meta {
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px;
  font-size: var(--text-xs);
  letter-spacing: 0.04em;
  color: var(--color-text-subtle);
}
.meta-num {
  color: var(--color-accent-text);
  font-weight: 600;
  font-size: var(--text-sm);
}
.meta-lbl {
  color: var(--color-text-muted);
}
.meta-dot {
  color: var(--color-text-subtle);
  opacity: 0.6;
  margin: 0 2px;
}
.mono {
  font-family: var(--font-mono);
}

.err-banner {
  background: var(--color-danger-soft);
  border-left: 3px solid var(--color-danger);
  color: var(--color-danger);
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  margin-bottom: var(--space-4);
  font-size: var(--text-sm);
}

.ft-skeleton {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5) 0;
}

.empty-state {
  text-align: center;
  padding: var(--space-16) 0;
  color: var(--color-text-subtle);
}
.empty-title {
  font-size: var(--text-base);
  font-weight: 500;
  color: var(--color-text-muted);
  margin: 0 0 6px;
  font-family: var(--font-serif);
}
.empty-hint {
  font-size: var(--text-xs);
  margin: 0;
}

/* ===== SVG 画布 — 加 faint grid 治"空旷感"(grid 在 ::before,主 bg 纯净) ===== */
.ft-canvas {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-5) var(--space-6);
  overflow: auto;
  max-height: calc(100vh - 220px);
  position: relative;
}
/* 浅 grid 背景纹理 — 极淡,只在 canvas 大留白时给视觉锚定,不抢节点焦点 */
.ft-canvas::before {
  content: "";
  position: absolute;
  inset: 0;
  background-image:
    linear-gradient(to right, var(--color-border) 1px, transparent 1px),
    linear-gradient(to bottom, var(--color-border) 1px, transparent 1px);
  background-size: 48px 48px;
  opacity: 0.35;
  pointer-events: none;
  border-radius: var(--radius-lg);
}

.ft-svg {
  display: block;
  position: relative;
}

/* 边 — class 化以响应 tokens / 双主题 */
.ft-edge--default {
  stroke: var(--color-border-strong);
  stroke-width: 1.2;
}
.ft-edge--cf {
  stroke: var(--color-accent-border);
  stroke-width: 1.5;
  stroke-dasharray: 4 4;
}

/* ===== 节点卡 — 2026-06-08 重设计,全 tokens + 阴影克制 ===== */
.ft-node {
  box-sizing: border-box;
  height: 100%;
  width: 100%;
  padding: var(--space-3) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 6px;
  font-family: var(--font-sans);
  transition: all var(--duration-fast) var(--ease-out);
  position: relative;
  overflow: hidden;
}
/* 左侧 3px 状态色 indicator(全身高,加粗一点更可识别)*/
.ft-node::before {
  content: "";
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 3px;
  background: var(--color-text-subtle);
}
.ft-node--done::before    { background: var(--color-success); }
.ft-node--running::before { background: var(--color-accent); }
.ft-node--pending::before { background: var(--color-text-subtle); }
.ft-node--failed::before  { background: var(--color-danger); }

.ft-node:hover {
  border-color: var(--color-accent-border);
  box-shadow: var(--shadow-md);
  transform: translateY(-1px);
}

/* 独立合并产物 — 金色右上角小角标,克制 */
.ft-node--final {
  border-color: var(--color-warning);
}
.ft-node--final::after {
  content: "合并产物";
  position: absolute;
  top: 0;
  right: 0;
  background: var(--color-warning-soft);
  color: var(--color-warning);
  font-size: 9.5px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 0 var(--radius-lg) 0 var(--radius-sm);
  letter-spacing: 0.04em;
}

/* row1 — 第 N 代 · 状态 */
.ft-node-row1 {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 10.5px;
  color: var(--color-text-subtle);
  font-weight: 500;
  letter-spacing: 0.04em;
}
.ft-node-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
  background: var(--color-text-subtle);
}
.ft-dot--done    { background: var(--color-success); }
.ft-dot--running {
  background: var(--color-accent);
  animation: ft-dot-pulse 1.4s ease-in-out infinite;
}
.ft-dot--pending { background: var(--color-text-subtle); }
.ft-dot--failed  { background: var(--color-danger); }
.ft-node-depth {
  color: var(--color-text);
  font-weight: 600;
  font-family: var(--font-mono);
  font-size: 11px;
}
.ft-node-state {
  margin-left: auto;
  color: var(--color-text-muted);
}

/* row2 — divergence 主标题 */
.ft-node-title {
  font-size: 12.5px;
  color: var(--color-text);
  line-height: 1.55;
  font-weight: 500;
  flex: 1;
  min-height: 0;
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
  text-overflow: ellipsis;
  word-break: break-all;
}
/* 反事实组合路径标签 — 用衬线斜体 + 微紫,去掉过亮的紫底色 */
.ft-path {
  font-family: var(--font-mono);
  color: var(--color-accent-text);
  font-size: 11.5px;
  background: var(--color-accent-soft);
  padding: 1px 7px;
  border-radius: var(--radius-sm);
  letter-spacing: 0.02em;
}

/* row3 — 字数 / 模式 / 终章 */
.ft-node-row3 {
  display: flex;
  align-items: center;
  gap: 5px;
  font-size: 10.5px;
  color: var(--color-text-subtle);
  font-variant-numeric: tabular-nums;
}
.ft-node-chars {
  color: var(--color-text);
  font-weight: 600;
  font-family: var(--font-mono);
}
.ft-node-sep { color: var(--color-border-strong); }
.ft-node-mode {
  text-transform: lowercase;
  letter-spacing: 0.02em;
  color: var(--color-text-muted);
}
.ft-node-finale {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  color: var(--color-warning);
  font-weight: 600;
  background: var(--color-warning-soft);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-size: 9.5px;
}

@keyframes ft-dot-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.4; transform: scale(0.75); }
}

/* combination batch 文字标签 — fill 用 accent-text 而非 hardcode */
.ft-batch-label {
  font-size: 11px;
  fill: var(--color-accent-text);
  font-weight: 500;
  letter-spacing: 0.04em;
  font-family: var(--font-sans);
}

/* ===== drawer — 全 tokens 化,宽度从 440 固定 → clamp 自适应 ===== */
.ft-drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: clamp(440px, 36vw, 560px);
  max-width: 95vw;
  background: var(--color-surface);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-modal);
  display: flex;
  flex-direction: column;
}
.ft-drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.18);
  backdrop-filter: blur(2px);
  z-index: var(--z-modal-backdrop);
}

.dr-hdr {
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border);
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.dr-title {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex: 1;
}
.dr-depth {
  font-weight: 600;
  font-size: var(--text-base);
  color: var(--color-text);
  font-family: var(--font-mono);
}
.dr-state {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--text-xs);
  font-weight: 500;
  letter-spacing: 0.04em;
}
.dr-state-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dr-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  background: transparent;
  border: none;
  color: var(--color-text-subtle);
  cursor: pointer;
  border-radius: var(--radius-full);
  transition: all var(--duration-fast) var(--ease-out);
}
.dr-close:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}

.dr-body {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-5);
}
.dr-label {
  color: var(--color-text-muted);
  font-weight: 500;
  margin-right: 4px;
  font-size: var(--text-xs);
  letter-spacing: 0.04em;
}
.dr-divergence,
.dr-tree-path {
  font-size: var(--text-sm);
  line-height: 1.7;
  color: var(--color-text);
  margin: 0 0 var(--space-3);
}
.dr-tree-path {
  font-family: var(--font-mono);
  color: var(--color-accent-text);
}
.dr-meta {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex-wrap: wrap;
  margin: 0 0 var(--space-4);
  letter-spacing: 0.04em;
}
.dr-meta-sep {
  color: var(--color-text-subtle);
  opacity: 0.5;
}
.dr-finale {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--color-warning);
  font-weight: 500;
}

.dr-section {
  margin-top: var(--space-3);
}
.dr-section-hdr {
  font-size: 10.5px;
  font-weight: 600;
  color: var(--color-text-subtle);
  letter-spacing: 0.08em;
  margin-bottom: var(--space-2);
  text-transform: uppercase;
}
.dr-preview {
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-sm);
  line-height: 1.8;
  color: var(--color-text);
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--font-serif);   /* 衬线给"小说预览"文学质感 */
  max-height: 320px;
  overflow-y: auto;
}
.dr-more {
  color: var(--color-text-subtle);
}
.dr-preview-loading,
.dr-preview-empty {
  text-align: center;
  padding: var(--space-5) 0;
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

.dr-footer {
  padding: var(--space-3) var(--space-5);
  border-top: 1px solid var(--color-border);
  display: flex;
  gap: var(--space-2);
  justify-content: flex-end;
}
.dr-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 14px;
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  cursor: pointer;
  border: 1px solid var(--color-border);
  transition: all var(--duration-fast) var(--ease-out);
}
.dr-btn--ghost {
  background: transparent;
  color: var(--color-text-muted);
}
.dr-btn--ghost:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
  border-color: var(--color-border-strong);
}
.dr-btn--primary {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
  font-weight: 500;
}
.dr-btn--primary:hover {
  background: var(--color-accent-hover);
  border-color: var(--color-accent-hover);
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
