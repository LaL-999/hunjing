<script setup lang="ts">
/**
 * CounterfactualTreeView — 反事实组合树视图(Sprint 6.A2 CT,2026-05-21)。
 *
 * 两种模式:
 *   - 无 combo_id(URL: /projects/:id/counterfactual-tree)→ 配置页
 *       让用户从当前项目 active 反事实中勾选 1-3 个变量,每个 2 态(原/改)
 *       预估总 token / 时长 / 积分 → 用户确认 → POST 创建批次 → 跳树视图
 *
 *   - 有 combo_id(URL: /projects/:id/counterfactual-tree/:combo_id)→ 树视图
 *       SVG 决策树渲染:根=原作,枝=变量分叉 a/b,叶=2^N 个 sim 卡
 *       色编 sim 状态(灰/蓝脉冲/绿/红),5s 轮询拉最新状态
 *       叶节点 click → 跳 SimulationDetailView
 *
 * 设计:
 *   - 配置 + 树展示同一个 view,简化路由 + 减少跳页
 *   - 树用 SVG 自绘(项目无 d3/echarts,3-8 个叶节点手算坐标完全可控)
 *   - 5s 轮询拉 /tree(各 sim 自己的 SSE 太重,组合树只关心宏观状态)
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "../api/client";
import {
  ApiError,
  type CombinationLeafSim,
  type CombinationPreviewResponse,
  type CombinationRun,
  type CombinationTreeResponse,
  type CounterfactualChange,
  type SelectedVariable,
} from "../api/types";
import { toast } from "../composables/useToast";
import { confirm as confirmDialog } from "../composables/useConfirm";
import SkeletonBlock from "../components/SkeletonBlock.vue";

const props = defineProps<{
  id: string;        // project_id
  combo_id?: string; // 可选 — 有则进入树视图,无则进入配置页
}>();

const router = useRouter();
const route = useRoute();

/** 2026-06-06:智能返回 — 根据 URL query.returnTo 跳回真实来源
 *  ?returnTo=graph → 跳回 3D 图谱页(从反事实工作台过来的路径)
 *  无 → 老逻辑(项目作品列表)
 */
function goBackSmart() {
  if (route.query.returnTo === "graph") {
    router.push({ name: "project-graph", params: { id: props.id } });
    return;
  }
  router.push({ name: "project", params: { id: props.id } });
}

// ============================================================
// 共享状态
// ============================================================

const loading = ref(false);
const errMsg = ref<string | null>(null);

// ============================================================
// 配置模式:拉所有 active 反事实供用户勾选
// ============================================================

const allActiveCfs = ref<CounterfactualChange[]>([]);

interface SelectedVarDraft {
  counterfactual_id: string;
  label_a: string; // 原值的简短描述
  label_b: string; // 改值的简短描述
}

const selected = ref<SelectedVarDraft[]>([]);

const reshapePercent = ref(50);
const targetChars = ref(4000);
const divergence = ref("");
const useOutlineFirst = ref(true);

const preview = ref<CombinationPreviewResponse | null>(null);
const previewLoading = ref(false);

const isConfigMode = computed(() => !props.combo_id);

// P-6 修复(2026-05-23):stale-while-revalidate — 首次加载才显 loading,
// 切项目 / 刷新时保留旧数据无缝替换
const _activeCfsLoadedOnce = ref(false);

// 2026-06-05:配置持久化 — 用户上次选了哪些反事实 + 参数,记在 localStorage
//   - key 按 project_id 分隔,跨项目不串
//   - 包含:勾选的 counterfactual_id 列表 + reshape_percent + target_chars +
//          divergence + use_outline_first
//   - 删批次 / 关浏览器 / 重启服务都不丢
//   - 用户在反事实工作台改了变量后,下次进配置页,旧勾选自动 filter 掉已删 / 已撤销的
const CFG_STORAGE_KEY = (projectId: string) => `cf_tree_cfg:${projectId}`;

interface PersistedConfig {
  selected_ids: string[];
  reshape_percent: number;
  target_chars: number;
  divergence: string;
  use_outline_first: boolean;
}

function loadPersistedConfig(): PersistedConfig | null {
  try {
    const raw = localStorage.getItem(CFG_STORAGE_KEY(props.id));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as PersistedConfig;
    if (!parsed || !Array.isArray(parsed.selected_ids)) return null;
    return parsed;
  } catch {
    return null;
  }
}

function savePersistedConfig() {
  try {
    const payload: PersistedConfig = {
      selected_ids: selected.value.map((s) => s.counterfactual_id),
      reshape_percent: reshapePercent.value,
      target_chars: targetChars.value,
      divergence: divergence.value,
      use_outline_first: useOutlineFirst.value,
    };
    localStorage.setItem(CFG_STORAGE_KEY(props.id), JSON.stringify(payload));
  } catch {
    // 配额不足 / 隐私模式 — 静默失败,不影响主流程
  }
}

/** 用 active 反事实列表 + 持久化配置,自动恢复 selected/参数。
 *  已删 / 已撤销的 cf 自动从勾选中 filter 掉(防 stale id 报错)。 */
function restoreFromPersisted() {
  const cfg = loadPersistedConfig();
  if (!cfg) return;
  // 仍存在 active 的 cf 才保留勾选
  const activeIds = new Set(allActiveCfs.value.map((c) => c.id));
  const restoredSelected: SelectedVarDraft[] = [];
  for (const sid of cfg.selected_ids) {
    if (!activeIds.has(sid)) continue;
    const cf = allActiveCfs.value.find((c) => c.id === sid);
    if (!cf) continue;
    restoredSelected.push({
      counterfactual_id: cf.id,
      label_a: shortLabel("原", cf.old_value || "(空)"),
      label_b: shortLabel("改", cf.new_value || "(空)"),
    });
    if (restoredSelected.length >= 3) break;
  }
  selected.value = restoredSelected;
  // 参数恢复(只在合法范围)
  if (typeof cfg.reshape_percent === "number" && cfg.reshape_percent >= 10 && cfg.reshape_percent <= 90) {
    reshapePercent.value = cfg.reshape_percent;
  }
  if (typeof cfg.target_chars === "number" && cfg.target_chars >= 500 && cfg.target_chars <= 20000) {
    targetChars.value = cfg.target_chars;
  }
  if (typeof cfg.divergence === "string") {
    divergence.value = cfg.divergence;
  }
  if (typeof cfg.use_outline_first === "boolean") {
    useOutlineFirst.value = cfg.use_outline_first;
  }
}

async function loadActiveCounterfactuals() {
  if (!_activeCfsLoadedOnce.value) loading.value = true;
  errMsg.value = null;
  try {
    const resp = await api.get<{
      total_active: number;
      items: CounterfactualChange[];
    }>(`/projects/${props.id}/counterfactuals`);
    allActiveCfs.value = (resp.items || []).filter((c) => c.is_active);
    // 拉到 active 反事实后,从 localStorage 恢复用户上次的勾选 + 参数
    restoreFromPersisted();
    // 恢复后立即重算成本预估
    if (selected.value.length > 0) {
      void refreshPreview();
    }
  } catch (e) {
    errMsg.value = e instanceof ApiError ? e.message : String(e);
  } finally {
    if (loading.value) loading.value = false;
    _activeCfsLoadedOnce.value = true;
  }
}

function toggleSelect(cf: CounterfactualChange) {
  const idx = selected.value.findIndex(
    (s) => s.counterfactual_id === cf.id,
  );
  if (idx >= 0) {
    selected.value.splice(idx, 1);
    return;
  }
  if (selected.value.length >= 3) {
    // 选满时未选卡片已经被 pointer-events:none 拦截 — 这里是兜底
    // (键盘 / a11y 触发等绕过 CSS 的极端路径,仍能给用户清晰反馈)
    toast.warning("已选满 3 个变量,如需更换请先取消已选");
    return;
  }
  selected.value.push({
    counterfactual_id: cf.id,
    label_a: shortLabel("原", cf.old_value || "(空)"),
    label_b: shortLabel("改", cf.new_value || "(空)"),
  });
}

function shortLabel(prefix: string, raw: string): string {
  const cleaned = (raw || "").replace(/\s+/g, " ").trim();
  return `${prefix}-${cleaned.slice(0, 20)}${cleaned.length > 20 ? "…" : ""}`;
}

function isSelected(cfId: string): boolean {
  return selected.value.some((s) => s.counterfactual_id === cfId);
}

const totalCombinations = computed(() => {
  const n = selected.value.length;
  return n > 0 ? 2 ** n : 0;
});

async function refreshPreview() {
  if (selected.value.length === 0) {
    preview.value = null;
    return;
  }
  previewLoading.value = true;
  try {
    const resp = await api.post<CombinationPreviewResponse>(
      `/projects/${props.id}/counterfactual-combinations/preview`,
      {
        selected_variable_count: selected.value.length,
        reshape_percent: reshapePercent.value,
        target_chars: targetChars.value,
      },
    );
    preview.value = resp;
  } catch (e) {
    if (e instanceof ApiError) toast.error("预估失败,请稍后重试");
  } finally {
    previewLoading.value = false;
  }
}

async function submitCreate() {
  if (selected.value.length < 1) {
    toast.warning("至少勾选 1 个反事实变量");
    return;
  }
  if (!divergence.value.trim()) {
    toast.warning("请输入分歧点描述");
    return;
  }
  loading.value = true;
  try {
    const variables: SelectedVariable[] = selected.value.map((s) => ({
      counterfactual_id: s.counterfactual_id,
      label_a: s.label_a,
      label_b: s.label_b,
    }));
    const resp = await api.post<CombinationRun>(
      `/projects/${props.id}/counterfactual-combinations`,
      {
        selected_variables: variables,
        reshape_percent: reshapePercent.value,
        target_chars: targetChars.value,
        style: "A",
        use_outline_first: useOutlineFirst.value,
        divergence: divergence.value.trim(),
      },
    );
    toast.success(`组合批次已启动 — ${resp.total_combinations} 个推演正在跑`);
    router.push({
      name: "counterfactual-tree",
      params: { id: props.id, combo_id: resp.id },
    });
  } catch (e) {
    if (e instanceof ApiError) {
      toast.error(e.message || "创建失败,请稍后再试");
    } else {
      toast.error("创建失败,请稍后再试");
    }
  } finally {
    loading.value = false;
  }
}

// ============================================================
// 树模式:轮询拉树数据
// ============================================================

const tree = ref<CombinationTreeResponse | null>(null);
let pollTimer: ReturnType<typeof setInterval> | null = null;
const lastRefreshTs = ref<number | null>(null);

async function loadTree() {
  if (!props.combo_id) return;
  try {
    const resp = await api.get<CombinationTreeResponse>(
      `/counterfactual-combinations/${props.combo_id}/tree`,
    );
    tree.value = resp;
    lastRefreshTs.value = Date.now();
  } catch (e) {
    errMsg.value = e instanceof ApiError ? e.message : String(e);
  }
}

// 2026-06-05 用户拍板:精简 hint 文案 — 去掉"下次刷新 Xs"倒计时,只留"正在并行编排"
// 之前的 nextRefreshText / nowMs / nowTicker 整套已删,降噪

// ---- 批次列表(切批次下拉) ----
interface BatchListItem {
  id: string;
  state: string;
  total_combinations: number;
  created_at: string;
  completed_at: string | null;
  done_count: number;
}
const batchList = ref<BatchListItem[]>([]);
const batchPickerOpen = ref(false);

async function loadBatchList() {
  if (!props.id) return;
  try {
    const resp = await api.get<{ items: BatchListItem[] }>(
      `/projects/${props.id}/counterfactual-combinations`,
    );
    batchList.value = resp.items || [];
  } catch (_e) {
    // 静默失败 — 切批次是 nice-to-have,失败不阻塞主流程
    batchList.value = [];
  }
}

function formatBatchDate(iso: string): string {
  try {
    const d = new Date(iso);
    const mm = String(d.getMonth() + 1).padStart(2, "0");
    const dd = String(d.getDate()).padStart(2, "0");
    const hh = String(d.getHours()).padStart(2, "0");
    const mi = String(d.getMinutes()).padStart(2, "0");
    return `${mm}-${dd} ${hh}:${mi}`;
  } catch {
    return iso;
  }
}

function switchBatch(batchId: string) {
  batchPickerOpen.value = false;
  if (batchId === props.combo_id) return;
  router.push({
    name: "counterfactual-tree",
    params: { id: props.id, combo_id: batchId },
  });
}

/** 2026-06-05:头部"删除本批次"按钮 — 1 个批次也能用(无下拉时唯一删除入口)*/
async function deleteCurrentBatch() {
  if (!props.combo_id) return;
  const cur = batchList.value.find((b) => b.id === props.combo_id);
  if (!cur) {
    // 批次列表还没加载好 — 兜底:用最小数据 + 当前 combo_id 构造一个临时项
    await deleteBatch({
      id: props.combo_id,
      state: tree.value?.combination_run.state || "unknown",
      total_combinations: tree.value?.leaves.length || 0,
      created_at: tree.value?.combination_run.created_at || new Date().toISOString(),
      completed_at: tree.value?.combination_run.completed_at || null,
      done_count: 0,
    });
    return;
  }
  await deleteBatch(cur);
}

async function deleteBatch(batch: BatchListItem, evt?: Event) {
  // 下拉里嵌套调用时需要 stopPropagation 防触发 switchBatch
  // 头部独立按钮调用时无 evt,跳过即可
  evt?.stopPropagation();
  const isCurrent = batch.id === props.combo_id;
  const dateStr = formatBatchDate(batch.created_at);
  const ok = await confirmDialog({
    title: `删除批次「${dateStr}」?`,
    message:
      `批次的 ${batch.total_combinations} 个推演 + 全部 narrative / outline 都会一并删除,无法恢复。` +
      (isCurrent ? "\n\n这是当前正在查看的批次 — 删除后会自动跳到剩余批次或配置页。" : ""),
    danger: true,
    confirmLabel: "确认删除",
  });
  if (!ok) return;
  try {
    await api.delete(`/counterfactual-combinations/${batch.id}`);
    toast.success(`已删除批次 + ${batch.total_combinations} 个推演`);
    // 重新拉列表
    await loadBatchList();
    // 若删的是当前批次 — 跳到剩余批次的第一个,或配置页
    if (isCurrent) {
      batchPickerOpen.value = false;
      const nextBatch = batchList.value[0];
      if (nextBatch) {
        router.push({
          name: "counterfactual-tree",
          params: { id: props.id, combo_id: nextBatch.id },
        });
      } else {
        router.push({
          name: "counterfactual-tree",
          params: { id: props.id },
        });
      }
    }
  } catch (e) {
    toast.error(e instanceof ApiError ? `删除失败:${e.message}` : "删除失败");
  }
}

function startPolling() {
  if (pollTimer) clearInterval(pollTimer);
  // 2026-06-05:轮询 5s → 2s,运行中字数刷新更及时,视觉"实时"感更强
  pollTimer = setInterval(() => {
    // 全部 sim 都终态 → 停止轮询
    if (tree.value && tree.value.leaves.every(
      (l) => ["completed", "failed"].includes(l.sim_state),
    )) {
      if (pollTimer) {
        clearInterval(pollTimer);
        pollTimer = null;
      }
      return;
    }
    loadTree();
  }, 2000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

// 2026-06-05:nowTicker 已删(文案精简,不再需要倒计时更新)

onMounted(async () => {
  if (isConfigMode.value) {
    await loadActiveCounterfactuals();
  } else {
    await loadTree();
    startPolling();
    await loadBatchList();
  }
});

// 2026-06-05 hotfix:配置模式 → 树模式 跳同一个组件(Vue Router 默认复用实例),
// onMounted 不会再触发 → loadTree/startPolling 不跑 → 用户看到永久"待启动"
// 必须 watch combo_id 变化,显式触发加载 + 启动轮询。
watch(
  () => props.combo_id,
  async (newId, oldId) => {
    if (newId === oldId) return;
    stopPolling();
    // 清旧 tree(防 stale 数据短暂闪现)
    tree.value = null;
    // 清展开 / 对比选择(切到新批次后老批次的选择无意义)
    expandedSet.value = new Set();
    compareSelectedIds.value = [];
    if (newId) {
      await loadTree();
      startPolling();
      await loadBatchList();
    } else {
      lastRefreshTs.value = null;
      // 跳回配置模式 — 重新拉 active 反事实(组件没重建,数据可能 stale)
      await loadActiveCounterfactuals();
    }
  },
);

onBeforeUnmount(() => {
  stopPolling();
});

// ============================================================
// 跳转到单个 sim 详情(老 SVG 树辅助已在 2026-06-05 重设计后移除)
// ============================================================

function openLeaf(leaf: CombinationLeafSim) {
  // 2026-06-05:带 returnTo + combo_id query — SimulationDetailView 的"返回项目"
  // 按钮会读这两个 query 跳回组合树进度页(不再 hard push 项目页)
  router.push({
    name: "simulation-detail",
    params: { id: leaf.simulation_id },
    query: props.combo_id
      ? { returnTo: "combo-tree", combo_id: props.combo_id }
      : undefined,
  });
}

/** 2026-06-05:跳 outline 审核页(outline-first 模式 awaiting_user 状态用) */
function openOutlineReview(leaf: CombinationLeafSim) {
  router.push({
    name: "simulation-outline",
    params: { id: leaf.simulation_id },
  });
}

// preview 自动刷新 — 选择变化时
// 2026-06-05:同步保存到 localStorage(勾选 / 参数都持久化)
function onSelectedChange() {
  savePersistedConfig();
  void refreshPreview();
}

// 监听文本类参数变化(divergence input)— 不走 onSelectedChange 路径
watch([divergence], () => {
  savePersistedConfig();
});

// ============================================================
// 2026-06-05 实时进度面板:状态分组 + 整体进度环 + 内联对比 + 展开预览
// ============================================================

/** 叶子分类:awaiting_outline 优先级最高(等用户审核 outline,sim 不会自己跑) */
type LeafBucket = "awaiting_outline" | "pending" | "running" | "done" | "failed";
function bucketOf(leaf: CombinationLeafSim): LeafBucket {
  // outline-first 模式 sim 卡在 queued/pending,但 outline 已 awaiting_user
  // → 必须分到专用 bucket,引导用户去审核
  if (leaf.outline_state === "awaiting_user") return "awaiting_outline";
  if (leaf.sim_state === "completed") return "done";
  if (leaf.sim_state === "failed") return "failed";
  if (["generating", "directing", "drafting"].includes(leaf.sim_state)) return "running";
  // 2026-06-05:outline-first 模式前 30-60s 在跑 outline,sim_state 还是 queued,
  // 但 outline_state 已经是 'drafting'/'generating' — 这也是"运行中",不是"待启动"
  if (leaf.outline_state && ["drafting", "generating"].includes(leaf.outline_state)) return "running";
  return "pending";
}

const sortedLeavesAsc = computed<CombinationLeafSim[]>(() => {
  if (!tree.value) return [];
  return [...tree.value.leaves].sort((a, b) =>
    a.tree_path.join("") < b.tree_path.join("") ? -1 : 1,
  );
});

const bucketCounts = computed(() => {
  const c = { awaiting_outline: 0, pending: 0, running: 0, done: 0, failed: 0 };
  for (const l of sortedLeavesAsc.value) c[bucketOf(l)]++;
  return c;
});

const totalCount = computed(() => sortedLeavesAsc.value.length);

/** 已结束(完成或失败)/ 总数 — 用于环形进度 */
const terminalCount = computed(
  () => bucketCounts.value.done + bucketCounts.value.failed,
);

const terminalRatio = computed(() =>
  totalCount.value > 0 ? terminalCount.value / totalCount.value : 0,
);

/** 全部跑完(任何终态)→ 关闭"运行中"分组的脉动动画 */
const allDone = computed(() => terminalCount.value === totalCount.value && totalCount.value > 0);

/** 把叶子分桶 — awaiting_outline 优先级最高(用户必须先审核才能继续) */
const groupedLeaves = computed(() => {
  const groups: Record<LeafBucket, CombinationLeafSim[]> = {
    awaiting_outline: [],
    done: [],
    running: [],
    pending: [],
    failed: [],
  };
  for (const l of sortedLeavesAsc.value) {
    groups[bucketOf(l)].push(l);
  }
  return groups;
});

/** 叶子路径 → 三个变量的选择标签合并显示 */
function leafPathLabels(leaf: CombinationLeafSim): string[] {
  if (!tree.value) return [];
  const vars = tree.value.combination_run.selected_variables;
  return leaf.tree_path.map((choice, idx) => {
    const v = vars[idx];
    if (!v) return choice;
    return choice === "a" ? v.label_a : v.label_b;
  });
}

/** 叶子序号(1-based,按 tree_path 字典序) */
function leafIndex(leaf: CombinationLeafSim): number {
  return sortedLeavesAsc.value.findIndex((l) => l.simulation_id === leaf.simulation_id) + 1;
}

// ---- 展开预览 ----
const previewMap = ref<Record<string, string>>({});      // sim_id → narrative 前段
const previewLoadingSet = ref<Set<string>>(new Set());
const expandedSet = ref<Set<string>>(new Set());

async function toggleExpand(leaf: CombinationLeafSim) {
  if (expandedSet.value.has(leaf.simulation_id)) {
    expandedSet.value.delete(leaf.simulation_id);
    expandedSet.value = new Set(expandedSet.value);
    return;
  }
  expandedSet.value.add(leaf.simulation_id);
  expandedSet.value = new Set(expandedSet.value);

  // 未拉过 → 拉一次 narrative 前段
  if (!previewMap.value[leaf.simulation_id]) {
    previewLoadingSet.value.add(leaf.simulation_id);
    previewLoadingSet.value = new Set(previewLoadingSet.value);
    try {
      const detail = await api.get<{ narrative: string | null }>(
        `/simulations/${leaf.simulation_id}`,
      );
      const text = (detail.narrative ?? "").slice(0, 800);
      previewMap.value = { ...previewMap.value, [leaf.simulation_id]: text };
    } catch (e) {
      previewMap.value = {
        ...previewMap.value,
        [leaf.simulation_id]: "[加载失败,可点详情查看完整文本]",
      };
    } finally {
      previewLoadingSet.value.delete(leaf.simulation_id);
      previewLoadingSet.value = new Set(previewLoadingSet.value);
    }
  }
}

// ---- 内联对比(选 2 个 done 卡跳现有 compare 页)----
const compareSelectedIds = ref<string[]>([]);

function toggleCompareSelect(leaf: CombinationLeafSim) {
  if (leaf.sim_state !== "completed") return;
  const idx = compareSelectedIds.value.indexOf(leaf.simulation_id);
  if (idx >= 0) {
    compareSelectedIds.value.splice(idx, 1);
    return;
  }
  if (compareSelectedIds.value.length >= 2) {
    // FIFO 替换:挤掉最早的一个
    compareSelectedIds.value.shift();
  }
  compareSelectedIds.value.push(leaf.simulation_id);
}

function isCompareSelected(leaf: CombinationLeafSim): boolean {
  return compareSelectedIds.value.includes(leaf.simulation_id);
}

function goCompare() {
  if (compareSelectedIds.value.length !== 2) {
    toast.warning("请选中 2 个已完成的组合");
    return;
  }
  router.push({
    name: "simulation-compare",
    params: { id: props.id },
    query: {
      a: compareSelectedIds.value[0],
      b: compareSelectedIds.value[1],
    },
  });
}

// ---- 用时格式化 ----
function formatDuration(
  startIso: string | null | undefined,
  endIso: string | null | undefined,
): string {
  if (!startIso) return "—";
  const start = new Date(startIso).getTime();
  const end = endIso ? new Date(endIso).getTime() : Date.now();
  if (isNaN(start) || isNaN(end) || end < start) return "—";
  const sec = Math.floor((end - start) / 1000);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

/** 整体环形进度 — SVG stroke-dashoffset 推动 */
const RING_RADIUS = 38;
const RING_CIRCUM = 2 * Math.PI * RING_RADIUS;
const ringDashOffset = computed(
  () => RING_CIRCUM * (1 - terminalRatio.value),
);
</script>

<template>
  <div class="cf-tree-view">
    <header class="hdr">
      <button class="back-btn" @click="goBackSmart">
        ← 返回项目
      </button>
      <h2>{{ isConfigMode ? "反事实组合树 · 配置" : "反事实组合树 · 决策树视图" }}</h2>
      <!-- 2026-06-05:进度页加批次切换 + 新建批次 -->
      <div v-if="!isConfigMode" class="hdr-actions">
        <!-- 批次切换下拉 — 多于 1 个批次时显示 -->
        <div v-if="batchList.length > 1" class="batch-picker">
          <button class="batch-picker-trigger" @click="batchPickerOpen = !batchPickerOpen">
            历史批次 ({{ batchList.length }})
            <span class="picker-chev">{{ batchPickerOpen ? '▴' : '▾' }}</span>
          </button>
          <transition name="picker-fade">
            <div v-if="batchPickerOpen" class="batch-picker-pop" @click.stop>
              <div
                v-for="b in batchList"
                :key="b.id"
                class="batch-item"
                :class="{ 'is-current': b.id === props.combo_id }"
                @click="switchBatch(b.id)"
              >
                <div class="batch-item-row">
                  <span class="batch-date">{{ formatBatchDate(b.created_at) }}</span>
                  <span class="batch-state-chip" :data-state="b.state">{{ b.state }}</span>
                  <button
                    class="batch-del-btn"
                    title="删除该批次(不可恢复)"
                    @click="deleteBatch(b, $event)"
                  >×</button>
                </div>
                <div class="batch-item-meta">
                  {{ b.done_count }} / {{ b.total_combinations }} 完成
                  <span v-if="b.id === props.combo_id" class="batch-current-tag">当前</span>
                </div>
              </div>
            </div>
          </transition>
        </div>
        <button
          class="new-batch-btn"
          @click="router.push({ name: 'counterfactual-tree', params: { id: props.id } })"
        >
          + 新建批次
        </button>
        <!-- 2026-06-05:独立"删除本批次"按钮 — 只有 1 个批次时也能用(下拉那时不显示)-->
        <button
          v-if="tree"
          class="del-batch-btn"
          title="删除本批次 + 全部下属推演(不可恢复)"
          @click="deleteCurrentBatch"
        >
          × 删除本批次
        </button>
      </div>
    </header>

    <!-- 错误显示 -->
    <div v-if="errMsg" class="err-banner">{{ errMsg }}</div>

    <!-- ========== 配置模式 ========== -->
    <section v-if="isConfigMode" class="config-section">
      <p class="hint">
        勾选 1-3 个已创建的反事实变量,每个变量将以"原 / 改"二态参与组合 →
        后端跑 2<sup>N</sup> 个推演,用户在树视图对比哪个组合最有戏。
        <strong v-if="selected.length >= 3" class="hint-full">
          已选满 3 个上限 — 如需更换变量,请先点击已选卡片取消勾选。
        </strong>
      </p>

      <div v-if="loading" class="loading-skeleton-list" aria-busy="true" aria-live="polite">
        <div v-for="i in 4" :key="i" class="cf-item-skeleton">
          <SkeletonBlock height="14px" width="35%" />
          <SkeletonBlock height="18px" width="70%" />
          <SkeletonBlock height="12px" width="55%" />
        </div>
      </div>
      <div v-else-if="allActiveCfs.length === 0" class="empty-state">
        当前项目没有 active 反事实变量。请先回项目页用"反事实工作台"创建几条,再回来组合。
      </div>

      <div v-else class="cf-list">
        <div
          v-for="cf in allActiveCfs"
          :key="cf.id"
          class="cf-item"
          :class="{
            'is-selected': isSelected(cf.id),
            'is-disabled': !isSelected(cf.id) && selected.length >= 3,
          }"
          @click="toggleSelect(cf); onSelectedChange();"
        >
          <div class="cf-item-head">
            <span class="cf-target-type">{{ cf.target_type }}</span>
            <span class="cf-field">{{ cf.field }}</span>
            <span v-if="isSelected(cf.id)" class="cf-selected-badge">✓ 已选</span>
          </div>
          <div class="cf-values">
            <span class="cf-old">原 · {{ cf.old_value || "(空)" }}</span>
            <span class="cf-arrow">→</span>
            <span class="cf-new">改 · {{ cf.new_value || "(空)" }}</span>
          </div>
          <div v-if="cf.user_intent" class="cf-intent">★ {{ cf.user_intent }}</div>
        </div>
      </div>

      <!-- 配置参数 -->
      <div v-if="selected.length > 0" class="config-params">
        <h3>共 {{ totalCombinations }} 个组合(2<sup>{{ selected.length }}</sup>)</h3>

        <div class="form-row">
          <label>分歧点描述
            <input v-model="divergence" type="text" placeholder="如:三人深夜进入韩紫雨家找线索" maxlength="500" />
          </label>
        </div>
        <div class="form-row">
          <label>重塑度 {{ reshapePercent }}%
            <input v-model.number="reshapePercent" type="range" min="10" max="90" step="10"
              @change="onSelectedChange()" />
          </label>
        </div>
        <div class="form-row">
          <label>目标字数
            <input v-model.number="targetChars" type="number" min="500" max="20000" step="500"
              @change="onSelectedChange()" />
          </label>
          <label class="checkbox-label">
            <input v-model="useOutlineFirst" type="checkbox" />
            <span>使用 outline-first(推荐)</span>
          </label>
        </div>

        <!-- 预估成本 -->
        <div v-if="preview" class="preview-card">
          <h4>预估成本(每个 sim 大约,串行总和)</h4>
          <ul>
            <li>总组合数:<b>{{ preview.total_combinations }}</b> 个推演</li>
            <li>总 token:~{{ preview.estimated_total_tokens.toLocaleString() }}(单个 ~{{ preview.estimated_token_per_sim.toLocaleString() }})</li>
            <li>预估时长:~{{ preview.estimated_total_minutes }} 分钟(单个 ~{{ preview.estimated_minutes_per_sim }} 分钟)</li>
            <li>预估积分:~{{ preview.estimated_credits.toLocaleString() }}</li>
          </ul>
          <p class="preview-warn">
            ⚠ 这只是粗估,实际可能 ±50%。配额不足时部分 sim 会创建失败(树视图会标红)。
          </p>
        </div>

        <button class="submit-btn" :disabled="loading || !divergence.trim()" @click="submitCreate">
          {{ loading ? "创建中…" : `生成 ${totalCombinations} 组推演` }}
        </button>
      </div>
    </section>

    <!-- ========== 进度面板模式(2026-06-05 重设计)========== -->
    <section v-else class="tree-section">
      <!-- loading skeleton -->
      <div v-if="!tree" class="loading-skeleton-tree" aria-busy="true" aria-live="polite">
        <div class="overview-skeleton">
          <SkeletonBlock height="92px" width="92px" />
          <div class="overview-skel-text">
            <SkeletonBlock height="14px" width="180px" />
            <SkeletonBlock height="10px" width="320px" />
          </div>
        </div>
        <div class="tree-rows">
          <SkeletonBlock v-for="i in 4" :key="i" height="84px" />
        </div>
      </div>

      <template v-else>
        <!-- 顶部:整体进度环 + 状态统计 -->
        <div class="overview-card">
          <div class="overview-ring-wrap">
            <svg class="overview-ring" viewBox="0 0 90 90">
              <!-- 背景圈 -->
              <circle
                cx="45" cy="45" :r="RING_RADIUS"
                fill="none"
                stroke="var(--color-border, #e5e7eb)"
                stroke-width="7"
              />
              <!-- 进度圈(stroke-dashoffset 推动) -->
              <circle
                class="overview-ring-track"
                cx="45" cy="45" :r="RING_RADIUS"
                fill="none"
                stroke="url(#ring-grad)"
                stroke-width="7"
                stroke-linecap="round"
                :stroke-dasharray="RING_CIRCUM"
                :stroke-dashoffset="ringDashOffset"
                transform="rotate(-90 45 45)"
              />
              <defs>
                <linearGradient id="ring-grad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stop-color="#8b5cf6" />
                  <stop offset="100%" stop-color="#a78bfa" />
                </linearGradient>
              </defs>
              <!-- 中心数字 -->
              <text
                x="45" y="42" text-anchor="middle"
                class="ring-num"
              >{{ terminalCount }}<tspan class="ring-sep">/</tspan>{{ totalCount }}</text>
              <text x="45" y="58" text-anchor="middle" class="ring-label">完成度</text>
            </svg>
          </div>
          <div class="overview-stats">
            <h3 class="overview-title">
              并行推演 ·
              <span class="overview-divergence">{{ tree.combination_run.selected_variables.length }} 变量 × 2 态 = {{ totalCount }} 组</span>
            </h3>
            <div class="overview-chips">
              <span
                v-if="bucketCounts.awaiting_outline > 0"
                class="ov-chip ov-chip--review"
              ><span class="ov-dot"></span>待审核 {{ bucketCounts.awaiting_outline }}</span>
              <span class="ov-chip ov-chip--done"><span class="ov-dot"></span>完成 {{ bucketCounts.done }}</span>
              <span class="ov-chip ov-chip--running"><span class="ov-dot"></span>运行中 {{ bucketCounts.running }}</span>
              <span class="ov-chip ov-chip--pending"><span class="ov-dot"></span>待启动 {{ bucketCounts.pending }}</span>
              <span v-if="bucketCounts.failed > 0" class="ov-chip ov-chip--failed"><span class="ov-dot"></span>失败 {{ bucketCounts.failed }}</span>
            </div>
            <p
              v-if="bucketCounts.awaiting_outline > 0"
              class="overview-hint overview-hint--review"
            >
              ⚠ 有 {{ bucketCounts.awaiting_outline }} 个组合的 outline 已生成,等你审核后才会启动逐幕生成
            </p>
            <p v-else-if="!allDone" class="overview-hint">
              <span class="poll-spinner"></span>
              正在并行编排
            </p>
            <p v-else class="overview-hint overview-hint--done">
              ✓ 全部完成
            </p>
          </div>

          <!-- 对比浮动按钮 -->
          <div class="overview-compare-bar" v-if="compareSelectedIds.length > 0">
            <span class="cmp-count">已选 {{ compareSelectedIds.length }}/2</span>
            <button
              class="cmp-btn"
              :disabled="compareSelectedIds.length !== 2"
              @click="goCompare"
            >
              <span>⇆</span>
              对比所选剧情线
            </button>
          </div>
        </div>

        <!-- ========== 等审核 outline 组(最高优先级 — 用户必须处理才能继续) ========== -->
        <div v-if="groupedLeaves.awaiting_outline.length > 0" class="leaf-group">
          <div class="leaf-group-hdr leaf-group-hdr--review">
            <span class="grp-icon grp-icon--pulse">⚠</span>
            <span class="grp-title">等你审核 outline</span>
            <span class="grp-count">{{ groupedLeaves.awaiting_outline.length }}</span>
          </div>
          <article
            v-for="leaf in groupedLeaves.awaiting_outline"
            :key="leaf.simulation_id"
            class="leaf-card leaf-card--review"
          >
            <div class="leaf-card-main">
              <div class="leaf-card-row">
                <span class="leaf-idx">第 {{ leafIndex(leaf) }} 组</span>
                <span
                  v-for="(lbl, i) in leafPathLabels(leaf)"
                  :key="i"
                  class="leaf-tag"
                >{{ lbl }}</span>
                <span class="leaf-state-chip leaf-state-chip--review">⚠ 等审核</span>
              </div>
              <div class="leaf-card-meta">
                outline 已生成 — 审核 / 编辑 / 批准后,sim 才开始逐幕生成
              </div>
            </div>
            <div class="leaf-card-actions">
              <button
                class="leaf-act leaf-act--primary"
                @click="openOutlineReview(leaf)"
              >
                前往审核 →
              </button>
            </div>
          </article>
        </div>

        <!-- ========== 完成 组 ========== -->
        <div v-if="groupedLeaves.done.length > 0" class="leaf-group">
          <div class="leaf-group-hdr leaf-group-hdr--done">
            <span class="grp-icon">✓</span>
            <span class="grp-title">已完成</span>
            <span class="grp-count">{{ groupedLeaves.done.length }}</span>
          </div>
          <article
            v-for="leaf in groupedLeaves.done"
            :key="leaf.simulation_id"
            class="leaf-card leaf-card--done"
            :class="{ 'is-compare-selected': isCompareSelected(leaf) }"
          >
            <div class="leaf-card-main">
              <div class="leaf-card-row">
                <span class="leaf-idx">第 {{ leafIndex(leaf) }} 组</span>
                <span
                  v-for="(lbl, i) in leafPathLabels(leaf)"
                  :key="i"
                  class="leaf-tag"
                >{{ lbl }}</span>
                <span class="leaf-state-chip leaf-state-chip--done">● 完成</span>
              </div>
              <div class="leaf-card-meta">
                <span class="meta-num">{{ leaf.sim_narrative_chars.toLocaleString() }} 字</span>
                <span class="meta-sep">·</span>
                <span>用时 {{ formatDuration(leaf.sim_created_at, leaf.sim_completed_at) }}</span>
              </div>
            </div>
            <div class="leaf-card-actions">
              <button
                class="leaf-act leaf-act--compare"
                :class="{ 'is-on': isCompareSelected(leaf) }"
                @click="toggleCompareSelect(leaf)"
              >
                <span v-if="isCompareSelected(leaf)">✓ 选中</span>
                <span v-else>⇆ 选入对比</span>
              </button>
              <button class="leaf-act" @click="toggleExpand(leaf)">
                {{ expandedSet.has(leaf.simulation_id) ? "收起 ▴" : "预览 ▾" }}
              </button>
              <button class="leaf-act leaf-act--ghost" @click="openLeaf(leaf)">
                详情 →
              </button>
            </div>
            <transition name="preview-fade">
              <div
                v-if="expandedSet.has(leaf.simulation_id)"
                class="leaf-preview"
              >
                <div
                  v-if="previewLoadingSet.has(leaf.simulation_id)"
                  class="preview-loading"
                >加载中…</div>
                <pre v-else class="preview-text">{{ previewMap[leaf.simulation_id] || "(空)" }}<span v-if="previewMap[leaf.simulation_id] && previewMap[leaf.simulation_id].length >= 800" class="preview-more">…</span></pre>
              </div>
            </transition>
          </article>
        </div>

        <!-- ========== 运行中 组 ========== -->
        <div v-if="groupedLeaves.running.length > 0" class="leaf-group">
          <div class="leaf-group-hdr leaf-group-hdr--running">
            <span class="grp-icon grp-icon--pulse">◐</span>
            <span class="grp-title">运行中</span>
            <span class="grp-count">{{ groupedLeaves.running.length }}</span>
          </div>
          <article
            v-for="leaf in groupedLeaves.running"
            :key="leaf.simulation_id"
            class="leaf-card leaf-card--running"
          >
            <div class="leaf-card-main">
              <div class="leaf-card-row">
                <span class="leaf-idx">第 {{ leafIndex(leaf) }} 组</span>
                <span
                  v-for="(lbl, i) in leafPathLabels(leaf)"
                  :key="i"
                  class="leaf-tag"
                >{{ lbl }}</span>
                <span class="leaf-state-chip leaf-state-chip--running">
                  <span class="run-dot"></span>运行中
                </span>
              </div>
              <!-- 字数推进条 — 字数实时跳动 + 微微脉动 -->
              <div class="run-bar">
                <div class="run-bar-fill"></div>
              </div>
              <div class="leaf-card-meta">
                <span class="meta-num meta-num--running">{{ leaf.sim_narrative_chars.toLocaleString() }} 字</span>
                <span class="meta-sep">·</span>
                <span>已运行 {{ formatDuration(leaf.sim_created_at, null) }}</span>
              </div>
            </div>
            <div class="leaf-card-actions">
              <button class="leaf-act leaf-act--ghost" @click="openLeaf(leaf)">
                进度 →
              </button>
            </div>
          </article>
        </div>

        <!-- ========== 待启动 组 ========== -->
        <div v-if="groupedLeaves.pending.length > 0" class="leaf-group">
          <div class="leaf-group-hdr leaf-group-hdr--pending">
            <span class="grp-icon">○</span>
            <span class="grp-title">待启动</span>
            <span class="grp-count">{{ groupedLeaves.pending.length }}</span>
          </div>
          <article
            v-for="leaf in groupedLeaves.pending"
            :key="leaf.simulation_id"
            class="leaf-card leaf-card--pending"
          >
            <div class="leaf-card-main">
              <div class="leaf-card-row">
                <span class="leaf-idx">第 {{ leafIndex(leaf) }} 组</span>
                <span
                  v-for="(lbl, i) in leafPathLabels(leaf)"
                  :key="i"
                  class="leaf-tag leaf-tag--muted"
                >{{ lbl }}</span>
                <span class="leaf-state-chip leaf-state-chip--pending">○ 排队中</span>
              </div>
              <div class="leaf-card-meta leaf-card-meta--muted">
                等待 LLM 调度…
              </div>
            </div>
          </article>
        </div>

        <!-- ========== 失败 组 ========== -->
        <div v-if="groupedLeaves.failed.length > 0" class="leaf-group">
          <div class="leaf-group-hdr leaf-group-hdr--failed">
            <span class="grp-icon">✗</span>
            <span class="grp-title">失败</span>
            <span class="grp-count">{{ groupedLeaves.failed.length }}</span>
          </div>
          <article
            v-for="leaf in groupedLeaves.failed"
            :key="leaf.simulation_id"
            class="leaf-card leaf-card--failed"
          >
            <div class="leaf-card-main">
              <div class="leaf-card-row">
                <span class="leaf-idx">第 {{ leafIndex(leaf) }} 组</span>
                <span
                  v-for="(lbl, i) in leafPathLabels(leaf)"
                  :key="i"
                  class="leaf-tag"
                >{{ lbl }}</span>
                <span class="leaf-state-chip leaf-state-chip--failed">✗ 失败</span>
              </div>
              <div class="leaf-card-meta leaf-card-meta--failed">
                跑这条组合时出错 — 点详情看具体原因
              </div>
            </div>
            <div class="leaf-card-actions">
              <button class="leaf-act leaf-act--ghost" @click="openLeaf(leaf)">
                详情 →
              </button>
            </div>
          </article>
        </div>
      </template>
    </section>
  </div>
</template>

<style scoped>
.cf-tree-view {
  padding: 24px;
  max-width: 1400px;
  margin: 0 auto;
}

.hdr {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}

.back-btn {
  background: var(--color-surface-2, #f1f5f9);
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: 6px;
  padding: 6px 12px;
  cursor: pointer;
}

/* 2026-06-05:进度页"新建批次"按钮 + 批次切换下拉 */
.hdr-actions {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 8px;
}
.new-batch-btn {
  background: transparent;
  border: 1px solid var(--color-border, #ece8de);
  border-radius: 7px;
  padding: 6px 12px;
  font-size: 12px;
  color: #6a665e;
  cursor: pointer;
  transition: background 150ms, color 150ms, border-color 150ms;
}
.new-batch-btn:hover {
  background: #f7f5f0;
  color: #1f1f1e;
  border-color: #c4bfb3;
}

/* 删除本批次按钮 — 默认低调,hover 时显危险红 */
.del-batch-btn {
  background: transparent;
  border: 1px solid var(--color-border, #ece8de);
  border-radius: 7px;
  padding: 6px 12px;
  font-size: 12px;
  color: #9a968d;
  cursor: pointer;
  transition: background 150ms, color 150ms, border-color 150ms;
}
.del-batch-btn:hover {
  background: #fef2f2;
  color: #b91c1c;
  border-color: #fca5a5;
}

/* 批次切换下拉 */
.batch-picker {
  position: relative;
}
.batch-picker-trigger {
  background: transparent;
  border: 1px solid var(--color-border, #ece8de);
  border-radius: 7px;
  padding: 6px 12px;
  font-size: 12px;
  color: #6a665e;
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  transition: background 150ms, border-color 150ms;
}
.batch-picker-trigger:hover {
  background: #f7f5f0;
  border-color: #c4bfb3;
}
.picker-chev {
  font-size: 10px;
  color: #9a968d;
}
.batch-picker-pop {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 6px;
  width: 280px;
  max-height: 360px;
  overflow-y: auto;
  background: white;
  border: 1px solid var(--color-border, #ece8de);
  border-radius: 10px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08), 0 2px 6px rgba(0, 0, 0, 0.04);
  padding: 6px;
  z-index: 50;
}
.batch-item {
  padding: 10px 12px;
  border-radius: 7px;
  cursor: pointer;
  transition: background 120ms;
}
.batch-item:hover {
  background: #f7f5f0;
}
.batch-item.is-current {
  background: rgba(139, 92, 246, 0.08);
}
.batch-item-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
}
.batch-date {
  font-size: 12.5px;
  color: #1f1f1e;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
.batch-state-chip {
  margin-left: auto;
  font-size: 10.5px;
  padding: 1px 7px;
  border-radius: 999px;
  background: #f3f0e8;
  color: #6a665e;
}
.batch-del-btn {
  background: transparent;
  border: none;
  color: #c4bfb3;
  font-size: 16px;
  line-height: 1;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: 4px;
  transition: background 120ms, color 120ms;
}
.batch-del-btn:hover {
  background: #fee2e2;
  color: #b91c1c;
}
.batch-state-chip[data-state="done"] { background: #d1fae5; color: #047857; }
.batch-state-chip[data-state="generating"],
.batch-state-chip[data-state="partial"] { background: #e0e7ff; color: #4338ca; }
.batch-state-chip[data-state="failed"] { background: #fee2e2; color: #b91c1c; }
.batch-item-meta {
  font-size: 11.5px;
  color: #9a968d;
  display: flex;
  align-items: center;
  gap: 6px;
}
.batch-current-tag {
  font-size: 10px;
  padding: 1px 6px;
  background: #8b5cf6;
  color: white;
  border-radius: 999px;
  font-weight: 500;
  margin-left: auto;
}
.picker-fade-enter-active,
.picker-fade-leave-active {
  transition: opacity 150ms, transform 150ms;
}
.picker-fade-enter-from,
.picker-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

/* 上次刷新时间小标 */
.refresh-stamp {
  font-size: 10.5px;
  color: #9a968d;
  font-variant-numeric: tabular-nums;
  margin-left: 2px;
}

/* polling spinner — 持续旋转的小环,让用户看到"系统在工作" */
.poll-spinner {
  display: inline-block;
  width: 10px;
  height: 10px;
  border: 1.5px solid rgba(139, 92, 246, 0.2);
  border-top-color: #8b5cf6;
  border-radius: 50%;
  flex-shrink: 0;
  animation: poll-spin 0.9s linear infinite;
}
@keyframes poll-spin {
  to { transform: rotate(360deg); }
}

.hdr h2 {
  margin: 0;
  font-size: var(--text-xl);
  font-weight: 600;
}

.err-banner {
  background: #fef2f2;
  border: 1px solid #fecaca;
  color: #b91c1c;
  padding: 12px 16px;
  border-radius: 8px;
  margin-bottom: 16px;
}

.hint {
  background: #eff6ff;
  border: 1px solid #bfdbfe;
  color: #1e40af;
  padding: 12px 16px;
  border-radius: 8px;
  margin-bottom: 16px;
}

.loading-state, .empty-state {
  text-align: center;
  padding: 60px 0;
  color: #6b7280;
}

/* Sprint 6.A2 polish(2026-05-22):loading 改 SkeletonBlock 占位
 * - list:模拟 cf-item 卡片结构(target_type + 标题 + meta)4 行
 * - tree:模拟顶部 meta chip 3 个 + 树行 4 行 */
.loading-skeleton-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
  padding: 16px 0;
}
.cf-item-skeleton {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
}
.loading-skeleton-tree {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 8px 0;
}
.overview-skeleton {
  display: flex;
  align-items: center;
  gap: 20px;
  padding: 20px;
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: 16px;
}
.overview-skel-text {
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex: 1;
}
.tree-rows {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.cf-list {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
  margin-bottom: 24px;
}

.cf-item {
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 12px;
  cursor: pointer;
  transition: all 0.15s;
  background: white;
}

.cf-item:hover {
  border-color: #60a5fa;
  background: #f9fafb;
}

.cf-item.is-selected {
  border-color: #2563eb;
  background: #eff6ff;
  box-shadow: 0 0 0 2px #2563eb33;
}

/* 选满 3 个后,未选的卡片视觉灰显并拦截点击 — 防呆于事前 */
.cf-item.is-disabled {
  opacity: 0.45;
  cursor: not-allowed;
  pointer-events: none;
  filter: grayscale(0.4);
}

.hint-full {
  display: block;
  margin-top: 8px;
  color: #B45309;
  font-weight: 600;
}

.cf-item-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--text-sm);
  margin-bottom: 6px;
}

.cf-target-type {
  background: #fef3c7;
  color: #92400e;
  padding: 2px 6px;
  border-radius: 4px;
  font-size: 11px;
}

.cf-field {
  font-weight: 600;
  color: #374151;
}

.cf-selected-badge {
  margin-left: auto;
  color: #2563eb;
  font-size: var(--text-xs);
  font-weight: 600;
}

.cf-values {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--text-sm);
  color: #374151;
  margin-bottom: 4px;
  flex-wrap: wrap;
}

.cf-old {
  color: #6b7280;
  text-decoration: line-through;
}

.cf-arrow {
  color: #9ca3af;
}

.cf-new {
  color: #059669;
  font-weight: 500;
}

.cf-intent {
  font-size: var(--text-xs);
  color: #2563eb;
  margin-top: 4px;
}

.config-params {
  background: #fafafa;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 20px;
  margin-top: 24px;
}

.config-params h3 {
  margin: 0 0 16px 0;
  font-size: var(--text-lg);
  color: #111827;
}

.form-row {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
  align-items: flex-end;
  flex-wrap: wrap;
}

.form-row label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--text-sm);
  color: #374151;
  flex: 1 1 200px;
}

.form-row input[type="text"],
.form-row input[type="number"] {
  border: 1px solid #d1d5db;
  border-radius: 6px;
  padding: 6px 10px;
  font-size: var(--text-base);
}

.checkbox-label {
  flex-direction: row !important;
  align-items: center;
  gap: 6px !important;
}

.preview-card {
  background: white;
  border: 1px solid #e5e7eb;
  border-radius: 8px;
  padding: 16px;
  margin: 16px 0;
}

.preview-card h4 {
  margin: 0 0 8px 0;
  font-size: var(--text-base);
  color: #111827;
}

.preview-card ul {
  margin: 0;
  padding-left: 20px;
  font-size: var(--text-sm);
  color: #374151;
  line-height: 1.8;
}

.preview-warn {
  margin: 8px 0 0 0;
  font-size: var(--text-xs);
  color: #d97706;
}

.submit-btn {
  background: #2563eb;
  color: white;
  border: none;
  border-radius: 8px;
  padding: 12px 24px;
  font-size: var(--text-base);
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s;
}

.submit-btn:hover:not(:disabled) {
  background: #1d4ed8;
}

.submit-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ============================================================
 * 进度面板模式(2026-06-05 高级感重设计)
 * 设计目标:让 8 个并行推演的实时进度在一屏内清晰呈现,
 *          完成态可勾选 2 个一键对比,无需跳出页面
 * ============================================================ */

/* ===== 顶部 overview 卡(进度环 + 统计 + 对比按钮)===== */
.overview-card {
  display: grid;
  grid-template-columns: 110px 1fr auto;
  gap: 20px;
  align-items: center;
  padding: 22px 24px;
  margin-bottom: 24px;
  background: linear-gradient(135deg, rgba(139, 92, 246, 0.04) 0%, rgba(255, 255, 255, 1) 60%);
  border: 1px solid rgba(139, 92, 246, 0.18);
  border-radius: 16px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02), 0 8px 24px rgba(139, 92, 246, 0.04);
}

.overview-ring-wrap {
  width: 110px;
  height: 110px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.overview-ring {
  width: 100%;
  height: 100%;
}
.overview-ring-track {
  transition: stroke-dashoffset 600ms cubic-bezier(0.4, 0, 0.2, 1);
  filter: drop-shadow(0 0 4px rgba(139, 92, 246, 0.25));
}
.ring-num {
  font-size: 16px;
  font-weight: 700;
  fill: #1f1f1e;
  letter-spacing: 0.02em;
}
.ring-sep {
  fill: #c4bfb3;
  font-weight: 500;
  margin: 0 1px;
}
.ring-label {
  font-size: 8px;
  fill: #9a968d;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-weight: 500;
}

.overview-stats {
  min-width: 0;
}
.overview-title {
  margin: 0 0 10px;
  font-size: 16px;
  font-weight: 600;
  color: #1f1f1e;
  letter-spacing: -0.01em;
}
.overview-divergence {
  font-weight: 500;
  color: #6a665e;
  font-size: 13px;
}
.overview-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 8px;
}
.ov-chip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 10px;
  font-size: 11.5px;
  font-weight: 500;
  background: #f7f5f0;
  border-radius: 999px;
  color: #4a4640;
}
.ov-chip .ov-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.ov-chip--done { background: #ecfdf5; color: #047857; }
.ov-chip--done .ov-dot { background: #10b981; }
.ov-chip--running { background: #eef2ff; color: #4338ca; }
.ov-chip--running .ov-dot { background: #6366f1; animation: dot-pulse 1.4s ease-in-out infinite; }
.ov-chip--pending { background: #f3f4f6; color: #6b7280; }
.ov-chip--pending .ov-dot { background: #9ca3af; }
.ov-chip--failed { background: #fef2f2; color: #b91c1c; }
.ov-chip--failed .ov-dot { background: #ef4444; }
.ov-chip--review { background: #fffbeb; color: #b45309; }
.ov-chip--review .ov-dot { background: #f59e0b; animation: dot-pulse 1.4s ease-in-out infinite; }

.overview-hint {
  margin: 0;
  font-size: 12px;
  color: #6a665e;
  display: flex;
  align-items: center;
  gap: 6px;
}
.overview-hint--done {
  color: #047857;
  font-weight: 500;
}
.overview-hint--review {
  color: #b45309;
  font-weight: 500;
}
.dot-blink {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #8b5cf6;
  flex-shrink: 0;
  animation: dot-pulse 1.4s ease-in-out infinite;
}

.overview-compare-bar {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 6px;
}
.cmp-count {
  font-size: 11px;
  color: #6a665e;
  font-weight: 500;
}
.cmp-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  background: linear-gradient(135deg, #8b5cf6 0%, #a78bfa 100%);
  color: white;
  border: none;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  box-shadow: 0 2px 8px rgba(139, 92, 246, 0.3);
  transition: transform 150ms, box-shadow 150ms;
}
.cmp-btn:hover:not(:disabled) {
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(139, 92, 246, 0.4);
}
.cmp-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
  box-shadow: none;
}

/* ===== 状态分组 ===== */
.leaf-group {
  margin-bottom: 24px;
}
.leaf-group-hdr {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 4px 10px;
  font-size: 13px;
  font-weight: 600;
  color: #6a665e;
  border-bottom: 1px solid #ece8de;
  margin-bottom: 12px;
}
.grp-icon {
  display: inline-flex;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
}
.grp-icon--pulse { animation: icon-pulse 1.8s ease-in-out infinite; }
.leaf-group-hdr--done .grp-icon { background: #d1fae5; color: #047857; }
.leaf-group-hdr--done { color: #047857; }
.leaf-group-hdr--running .grp-icon { background: #e0e7ff; color: #4338ca; }
.leaf-group-hdr--running { color: #4338ca; }
.leaf-group-hdr--pending .grp-icon { background: #f3f4f6; color: #6b7280; }
.leaf-group-hdr--pending { color: #6b7280; }
.leaf-group-hdr--failed .grp-icon { background: #fee2e2; color: #b91c1c; }
.leaf-group-hdr--failed { color: #b91c1c; }
.leaf-group-hdr--review .grp-icon { background: #fef3c7; color: #b45309; }
.leaf-group-hdr--review { color: #b45309; }
.grp-title { letter-spacing: 0.04em; }
.grp-count {
  margin-left: auto;
  font-weight: 500;
  font-size: 12px;
  color: #9a968d;
}

/* ===== 叶子卡片 ===== */
.leaf-card {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 16px;
  align-items: center;
  padding: 14px 18px;
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #ece8de);
  border-radius: 12px;
  margin-bottom: 8px;
  transition: box-shadow 200ms, transform 200ms, border-color 200ms;
}
.leaf-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.05);
  transform: translateY(-1px);
}
.leaf-card--done {
  border-left: 3px solid #10b981;
}
.leaf-card--running {
  border-left: 3px solid #6366f1;
  background: linear-gradient(135deg, rgba(99, 102, 241, 0.02) 0%, rgba(255, 255, 255, 1) 50%);
}
.leaf-card--pending {
  border-left: 3px solid #d1d5db;
  opacity: 0.75;
}
.leaf-card--failed {
  border-left: 3px solid #ef4444;
}
.leaf-card--review {
  border-left: 3px solid #f59e0b;
  background: linear-gradient(135deg, rgba(245, 158, 11, 0.04) 0%, rgba(255, 255, 255, 1) 50%);
}
.leaf-card.is-compare-selected {
  border-color: #8b5cf6;
  background: linear-gradient(135deg, rgba(139, 92, 246, 0.05) 0%, rgba(255, 255, 255, 1) 60%);
  box-shadow: 0 0 0 2px rgba(139, 92, 246, 0.18), 0 4px 16px rgba(139, 92, 246, 0.12);
}

.leaf-card-main {
  min-width: 0;
}
.leaf-card-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}
.leaf-idx {
  font-size: 11px;
  font-weight: 600;
  color: #6a665e;
  background: #f3f0e8;
  padding: 2px 8px;
  border-radius: 4px;
  letter-spacing: 0.02em;
}
.leaf-tag {
  font-size: 11.5px;
  padding: 2px 8px;
  background: #ede9fe;
  color: #6d28d9;
  border-radius: 4px;
  font-weight: 500;
}
.leaf-tag--muted {
  background: #f3f4f6;
  color: #6b7280;
}
.leaf-state-chip {
  margin-left: auto;
  font-size: 11px;
  padding: 2px 9px;
  border-radius: 999px;
  font-weight: 500;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.leaf-state-chip--done { background: #d1fae5; color: #047857; }
.leaf-state-chip--running { background: #e0e7ff; color: #4338ca; }
.leaf-state-chip--pending { background: #f3f4f6; color: #6b7280; }
.leaf-state-chip--failed { background: #fee2e2; color: #b91c1c; }
.leaf-state-chip--review { background: #fef3c7; color: #b45309; }
.run-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #6366f1;
  animation: dot-pulse 1.4s ease-in-out infinite;
}

.leaf-card-meta {
  font-size: 12px;
  color: #6a665e;
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.leaf-card-meta--muted { color: #9a968d; font-style: italic; }
.leaf-card-meta--failed { color: #b91c1c; }
.meta-num {
  font-weight: 600;
  color: #1f1f1e;
  font-variant-numeric: tabular-nums;
  transition: color 300ms;
}
.meta-num--running {
  color: #4338ca;
  position: relative;
}
.meta-num--running::after {
  content: "↑";
  font-size: 10px;
  margin-left: 3px;
  color: #6366f1;
  opacity: 0.7;
  animation: arrow-bounce 1.6s ease-in-out infinite;
}
.meta-sep { color: #c4bfb3; }

/* 运行中 — 字数推进条(纯视觉,流动渐变) */
.run-bar {
  margin: 8px 0;
  height: 4px;
  background: #f3f0e8;
  border-radius: 999px;
  overflow: hidden;
  position: relative;
}
.run-bar-fill {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  width: 100%;
  background: linear-gradient(
    90deg,
    transparent 0%,
    rgba(99, 102, 241, 0.6) 35%,
    rgba(139, 92, 246, 0.9) 50%,
    rgba(99, 102, 241, 0.6) 65%,
    transparent 100%
  );
  background-size: 200% 100%;
  animation: run-bar-flow 1.6s linear infinite;
}

/* ===== 卡片右侧操作 ===== */
.leaf-card-actions {
  display: flex;
  gap: 6px;
  align-items: center;
  flex-shrink: 0;
}
.leaf-act {
  padding: 6px 12px;
  font-size: 12px;
  border-radius: 7px;
  border: 1px solid var(--color-border, #ece8de);
  background: var(--color-surface, #fff);
  color: #4a4640;
  cursor: pointer;
  transition: background 150ms, border-color 150ms;
  white-space: nowrap;
}
.leaf-act:hover {
  background: #faf7f2;
  border-color: #c4bfb3;
}
.leaf-act--ghost {
  background: transparent;
  color: #6a665e;
  border-color: transparent;
}
.leaf-act--ghost:hover {
  background: #f3f0e8;
}
.leaf-act--compare {
  border-color: #c4b5fd;
  color: #6d28d9;
  background: #f5f3ff;
  font-weight: 500;
}
.leaf-act--compare:hover {
  background: #ede9fe;
  border-color: #a78bfa;
}
.leaf-act--compare.is-on {
  background: #8b5cf6;
  color: white;
  border-color: #8b5cf6;
  box-shadow: 0 2px 6px rgba(139, 92, 246, 0.3);
}
.leaf-act--primary {
  background: linear-gradient(135deg, #f59e0b 0%, #fbbf24 100%);
  color: white;
  border-color: #f59e0b;
  font-weight: 500;
  box-shadow: 0 2px 6px rgba(245, 158, 11, 0.25);
}
.leaf-act--primary:hover {
  background: linear-gradient(135deg, #d97706 0%, #f59e0b 100%);
  border-color: #d97706;
  box-shadow: 0 3px 10px rgba(245, 158, 11, 0.35);
}

/* ===== 展开预览 ===== */
.leaf-preview {
  grid-column: 1 / -1;
  margin-top: 12px;
  padding: 14px 16px;
  background: #faf7f2;
  border-radius: 8px;
  border: 1px solid #ece8de;
}
.preview-loading {
  font-size: 12px;
  color: #9a968d;
  text-align: center;
  padding: 12px 0;
}
.preview-text {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  font-size: 13px;
  line-height: 1.75;
  color: #2a2724;
  max-height: 240px;
  overflow-y: auto;
}
.preview-more {
  color: #9a968d;
  font-weight: 500;
}
.preview-fade-enter-active,
.preview-fade-leave-active {
  transition: opacity 200ms, transform 200ms;
}
.preview-fade-enter-from,
.preview-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}

/* ===== 动画 keyframes ===== */
@keyframes dot-pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50%      { opacity: 0.4; transform: scale(0.75); }
}
@keyframes icon-pulse {
  0%, 100% { transform: scale(1); }
  50%      { transform: scale(1.08); }
}
@keyframes run-bar-flow {
  0%   { background-position: 200% 0; }
  100% { background-position: -100% 0; }
}
@keyframes arrow-bounce {
  0%, 100% { transform: translateY(0); opacity: 0.7; }
  50%      { transform: translateY(-2px); opacity: 1; }
}

/* 响应式 */
@media (max-width: 768px) {
  .overview-card {
    grid-template-columns: 1fr;
    text-align: center;
  }
  .overview-ring-wrap {
    margin: 0 auto;
  }
  .overview-chips {
    justify-content: center;
  }
  .overview-compare-bar {
    align-items: center;
  }
  .leaf-card {
    grid-template-columns: 1fr;
  }
  .leaf-card-actions {
    justify-content: flex-end;
  }
}
</style>
