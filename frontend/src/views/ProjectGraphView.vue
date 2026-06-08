<script setup lang="ts">
/**
 * ProjectGraphView v3 — 3D 图谱编辑器(Sprint 1.M.1)。
 *
 * 从 v2 只读视图升级:
 *   - 点节点 → 右抽屉 NodeEditDrawer 显详情 + inline 编辑(PERSON 完整字段)
 *   - 拖节点 → 后端 PATCH /characters/{id} 持久化 position(debounced 800ms)
 *   - 节点编辑保存后 → reload 整个 graphData(标签 / 关系反映新数据)
 *   - 角色删除 → reload + 关抽屉(若删的是当前选中节点则取消选中)
 *
 * router.meta.fullscreen=true → App.vue 不渲染 sidebar,本视图占满全屏。
 * 顶栏改用浅色风格(返回箭头 + 项目名 + FPS),3D canvas 内部保留深空背景。
 */
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api } from "../api/client";
import {
  ApiError,
  STRENGTH_THRESHOLD_HINT,
  type Character,
  type Project,
  type ProjectEvent,
  type InsufficientCreditsDetail,
  type QuotaExceededDetail,
  type UpdateCharacterRequest,
} from "../api/types";
import CharacterFocusModal from "../components/CharacterFocusModal.vue";
import CounterfactualWorkbench from "../components/CounterfactualWorkbench.vue";
import CrystalGraph from "../components/CrystalGraph.vue";
import GraphHelpModal from "../components/GraphHelpModal.vue";
import NodeCreatorMenu from "../components/NodeCreatorMenu.vue";
import NodeEditDrawer from "../components/NodeEditDrawer.vue";
import RelationshipCreator from "../components/RelationshipCreator.vue";
import SimulationDock from "../components/SimulationDock.vue";
import { useCounterfactuals } from "../composables/useCounterfactuals";
import { useProjectGraph } from "../composables/useProjectGraph";
import { useSimulation } from "../composables/useSimulation";
import { useQuotaStore } from "../stores/quota";
import { useAddonModal } from "../composables/useAddonModal";
import { useUpgradeModal } from "../composables/useUpgradeModal";
import { toast } from "../composables/useToast";
import type { GraphNode } from "../types/graph";

const props = defineProps<{ id: string }>();

const router = useRouter();
// Sprint 2.E polish:传 getter 让 useProjectGraph 内部 watch props.id —
// 侧栏切项目时自动重拉数据(原签名只接 string 时,切项目后图谱不会刷新)
const { loading, error, graphData, project, load } = useProjectGraph(() => props.id);

// ============================================================
// Sprint 3.A polish — 关系强度阈值滑块(顶栏)
// ============================================================

/** 本地滑动值 — 拖动时即时响应,松手后 PATCH 持久化 + reload。
 *  这样拖动滑块时图谱密度不会每个 step 都重拉 graph(防多次 reload 抖动)。 */
const thresholdLocal = ref(30);

// 项目数据到位时同步 local 值
watch(
  () => project.value?.graph_strength_threshold,
  (v) => {
    if (typeof v === "number") thresholdLocal.value = v;
  },
  { immediate: true },
);

function onThresholdInput(v: number) {
  // 拖动中只更新本地 — 不发请求(防滑块每个 step 都 PATCH + reload)
  thresholdLocal.value = v;
}

async function onThresholdCommit() {
  // D.2.B fix:松手时 PATCH 持久化,但**不再 reload graph** —
  //   阈值通过 `graphStrengthThreshold` prop 传 CrystalGraph,内部 watch 即时刷
  //   link material opacity(force-graph 不重建,沙盘下无闪烁)。
  //   reload 仍会破坏沙盘玻璃态线条 → 改成纯前端 + 异步 PATCH。
  if (!project.value) return;
  if (thresholdLocal.value === project.value.graph_strength_threshold) return;
  try {
    await api.patch<Project>(
      `/projects/${props.id}`,
      { graph_strength_threshold: thresholdLocal.value },
    );
    // 同步本地 project.value,避免下次 commit 时 stale 比较
    project.value.graph_strength_threshold = thresholdLocal.value;
  } catch (e) {
    toast.error(
      e instanceof ApiError ? `阈值保存失败:${e.message}` : "阈值保存失败",
    );
    thresholdLocal.value = project.value.graph_strength_threshold;
  }
}

const thresholdHintLabel = computed(() => {
  const v = thresholdLocal.value;
  for (const tier of STRENGTH_THRESHOLD_HINT) {
    if (v <= tier.max) return tier.label;
  }
  return STRENGTH_THRESHOLD_HINT[STRENGTH_THRESHOLD_HINT.length - 1].label;
});

const thresholdHintDetail = computed(() => {
  const v = thresholdLocal.value;
  for (const tier of STRENGTH_THRESHOLD_HINT) {
    if (v <= tier.max) return tier.hint;
  }
  return STRENGTH_THRESHOLD_HINT[STRENGTH_THRESHOLD_HINT.length - 1].hint;
});
const quota = useQuotaStore();
const upgradeModal = useUpgradeModal();
const addonModal = useAddonModal();

const fps = ref(0);

// 1.M Polish:3D 图谱使用说明书 modal(顶栏 ? 按钮触发)
const helpOpen = ref(false);

// ============================================================
// 1.M.2:AI 对焦联动(选中节点 → AI 对焦,refine 完后被改节点脉搏发光)
// ============================================================

const focusOpen = ref(false);
const focusCharacterId = ref<string | null>(null);
const focusCharacterName = ref<string | null>(null);
/**
 * refine 完成后传给 CrystalGraph 的 prop — 这些 PERSON 节点开始脉搏发光。
 * 6 秒后自动清空,脉搏停下。
 */
const refinedNodeIds = ref<string[]>([]);
let refinedPulseTimer: ReturnType<typeof setTimeout> | null = null;

function onDrawerRequestFocus(payload: { characterId: string; characterName: string }) {
  focusCharacterId.value = payload.characterId;
  focusCharacterName.value = payload.characterName;
  focusOpen.value = true;
}

async function onFocusClose(refinedIds: string[]) {
  focusOpen.value = false;
  // 配额刷新(无论成功失败,/refine 端点都已扣过 1 次)
  void quota.refresh();
  if (refinedIds.length > 0) {
    // reload graph 让新字段(身份 / 性格等)进入节点 tone 颜色
    await load();
    // 触发脉搏:6 秒后清空
    refinedNodeIds.value = refinedIds;
    if (refinedPulseTimer) clearTimeout(refinedPulseTimer);
    refinedPulseTimer = setTimeout(() => {
      refinedNodeIds.value = [];
      refinedPulseTimer = null;
    }, 6000);
  }
  focusCharacterId.value = null;
  focusCharacterName.value = null;
}

function onFocusQuotaExceeded(detail: QuotaExceededDetail | InsufficientCreditsDetail) {
  if ((detail as { code?: string }).code === "INSUFFICIENT_CREDITS") {
    addonModal.open();
  } else {
    upgradeModal.open(detail);
  }
}

// ============================================================
// 1.M.3:AI 续写联动(框选 → 预填 / 运行高亮 / 完成留光迹)
// ============================================================

const session = useSimulation(() => props.id);

// 框选状态:Ctrl/Cmd+点 PERSON 节点切换入/出
const frameSelectedIds = ref<Set<string>>(new Set());

// Sprint 2.C / 2.C+ 反事实变量(顶层实例化,与 SimulationDock + CounterfactualWorkbench 共享)
const cf = useCounterfactuals(props.id);
const workbenchOpen = ref(false);
// Workbench 需要的 characters / events 完整列表(打开时拉一次)
const wbCharacters = ref<Character[]>([]);
const wbEvents = ref<ProjectEvent[]>([]);

watch(
  () => props.id,
  (pid) => {
    if (pid) {
      cf.bind(pid);
      void cf.reload();
    }
  },
  { immediate: true },
);

async function openWorkbench() {
  // 打开前并行拉 characters / events 完整数据(graphData.nodes 字段不全)
  try {
    const [chars, evts] = await Promise.all([
      api.get<Character[]>(`/projects/${props.id}/characters`),
      api.get<ProjectEvent[]>(`/projects/${props.id}/events`),
    ]);
    wbCharacters.value = chars;
    wbEvents.value = evts;
  } catch {
    // 拉失败仍允许开 workbench(让用户看 active 反事实即可)
    wbCharacters.value = [];
    wbEvents.value = [];
  }
  workbenchOpen.value = true;
}

async function onWorkbenchChanged() {
  // 反事实创建 / 撤销 → reload 整个 graph 让 3D 反映回字段(撤销时 db 已还原)
  await load();
}

async function onBaselineInferred() {
  // 2.C+ polish: AI 识别 baseline 后 reload project 让 Workbench 拿到新 world_baseline
  await load();
}

// 只在 SimulationDock 或 Workbench 打开时显 affected 高亮
// (平时不显,避免视觉混乱;打开时让用户看清"reshape % 触达哪些节点")
const visibleAffectedNodeIds = computed<string[]>(() =>
  simulationOpen.value || workbenchOpen.value
    ? cf.affectedNodeIds.value
    : [],
);

// ============================================================
// Sprint D.2.A — 3D 图谱形态 D(反事实推演沙盘)
// ============================================================

/** 沙盘模式开关 — 默认关(走形态 A/B/C 现有行为)。
 *  打开 = 形态 D 沙盘:反事实变量的源节点显光晕环(类型颜色编码)。
 *  D.2.B 后续会加 BFS 波纹扩散动画。
 *
 *  显示条件:仅 middle / cycle 态(对齐反事实工作台显示规则) */
const sandboxMode = ref(false);

const canShowSandboxMode = computed(
  () => project.value?.mode === "middle" || project.value?.mode === "cycle",
);

function toggleSandboxMode() {
  // 用户拍板(2026-05-12):去掉沙盘 toast,视觉切换已经够明显
  sandboxMode.value = !sandboxMode.value;
}

/** 沙盘按钮 title tooltip — 拆 computed 避免 template 内嵌引号撞 Vue parser
 *  (老写法 :title="cond ? 'A 含 \"双引号\"' : 'B'" 会让 vite:vue 报
 *   Unterminated string constant)*/
const sandboxTitle = computed(() =>
  sandboxMode.value
    ? "退出沙盘 — 回到正常图谱编辑"
    : "进入沙盘模式 — 反事实变量的源节点会显光晕环",
);

/** 派生:反事实源节点 ids 按 type 分桶,传给 CrystalGraph 渲染光晕环
 *    - character / event:具体节点 id → 节点上加光晕环
 *    - world(target_id='_global_'):无具体节点 → 顶栏 chip 展示总数
 *    - relationship:关系是边不是节点,本 sprint 不参与 */
const counterfactualSourceIds = computed<{
  character: string[];
  event: string[];
}>(() => {
  const character: string[] = [];
  const event: string[] = [];
  for (const item of cf.items.value) {
    if (item.target_type === "character") character.push(item.target_id);
    else if (item.target_type === "event") event.push(item.target_id);
  }
  return { character, event };
});

/** 世界观反事实数量(沙盘模式顶栏 chip 显)*/
const worldCfCount = computed(
  () => cf.items.value.filter((c) => c.target_type === "world").length,
);

// SimulationDock 状态
const simulationOpen = ref(false);
const prefillDivergence = ref("");

// 运行 / 光迹 视觉 prop(传给 CrystalGraph)
const runningParticipantIds = ref<string[]>([]);
const dimMode = ref(false);
const trailNodeIds = ref<string[]>([]);
// 光迹关联的 simulation id — 同时下传 NodeEditDrawer,在 PERSON drawer 显跳详情 chip
const recentSimulationId = ref<string | null>(null);
const recentSimulationLabel = ref<string>("");

function onNodeFrameClick(node: GraphNode) {
  if (node.type !== "PERSON") {
    // 框选语义对所有 mode 一致 — 只能围绕角色;不必区分推演/重塑/续写/长篇
    toast.info("只能围绕角色,事件节点不参与框选");
    return;
  }
  // 不可变 Set 写法以触发 ref 响应式
  const next = new Set(frameSelectedIds.value);
  if (next.has(node.id)) next.delete(node.id);
  else next.add(node.id);
  frameSelectedIds.value = next;
}

function clearFrame() {
  frameSelectedIds.value = new Set();
}

const frameSelectedNames = computed(() => {
  if (!graphData.value || frameSelectedIds.value.size === 0) return [];
  return graphData.value.nodes
    .filter((n) => n.type === "PERSON" && frameSelectedIds.value.has(n.id))
    .map((n) => n.name);
});

const realCharacterCount = computed(() => {
  if (!graphData.value) return 0;
  return graphData.value.nodes.filter((n) => n.type === "PERSON").length;
});

/**
 * Sprint 2.E polish:框选浮动 bar 的「AI ___」按钮按 mode 差异化
 *   initial → ✦ AI 推演
 *   middle  → ⟲ AI 重塑
 *   end     → → AI 续写
 *   cycle   → ∞ AI 长篇
 * 与 SimulationDock.dockHeaderConfig + ProjectView.simulateButtonLabel 同套映射
 */
const simulateButtonLabel = computed(() => {
  const m = project.value?.mode ?? "initial";
  if (m === "middle") return { icon: "⟲", text: "AI 重塑" };
  if (m === "end") return { icon: "→", text: "AI 续写" };
  if (m === "cycle") return { icon: "∞", text: "AI 长篇" };
  return { icon: "✦", text: "AI 推演" };
});

function openSimulationFromFrame() {
  const names = frameSelectedNames.value;
  if (names.length < 2) {
    toast.warning(`至少框选 2 个角色才能开启「${simulateButtonLabel.value.text}」`);
    return;
  }
  // 预填 divergence:让 LLM 围绕框选角色展开。文本足够引导 prompt,
  // 不动后端 schema(全项目角色仍传给 simulation,关系敏感性保留)
  prefillDivergence.value = `围绕 ${names.join(" / ")} 这几个角色,接下来发生……`;
  simulationOpen.value = true;
}

function onSimulationClose() {
  simulationOpen.value = false;
  prefillDivergence.value = "";
  // 推演关闭后清框选(若用户开了推演,意图已交付;不开就不清,留给用户继续编辑)
  if (session.phase.value === "running" || session.phase.value === "creating") {
    clearFrame();
  }
}

function onSimulationQuotaExceeded(detail: QuotaExceededDetail | InsufficientCreditsDetail) {
  if ((detail as { code?: string }).code === "INSUFFICIENT_CREDITS") {
    addonModal.open();
  } else {
    upgradeModal.open(detail);
  }
}

/**
 * 监听 session.phase + simulation 变化:
 * - running 启动:participants = snapshot 全部角色 ids,dimMode 开,trail 清
 * - done 落定:participants 清 + dimMode 关 + 这次的 ids 进 trail + 记 recentSimId
 * - failed/error:也清 running 视觉(不 stamp trail,失败的不算"光迹")
 */
watch(
  () => [session.phase.value, session.simulation.value?.id ?? null],
  () => {
    const phase = session.phase.value;
    const sim = session.simulation.value;
    if ((phase === "running" || phase === "creating") && sim) {
      const ids = (sim.characters_snapshot ?? [])
        .map((c) => (c as { id?: string }).id)
        .filter((x): x is string => typeof x === "string");
      runningParticipantIds.value = ids;
      dimMode.value = true;
      // 推演开始时清旧 trail(不混淆"刚演"和"在演")
      trailNodeIds.value = [];
      recentSimulationId.value = null;
      recentSimulationLabel.value = "";
    } else if (phase === "done" && sim) {
      const ids = (sim.characters_snapshot ?? [])
        .map((c) => (c as { id?: string }).id)
        .filter((x): x is string => typeof x === "string");
      runningParticipantIds.value = [];
      dimMode.value = false;
      trailNodeIds.value = ids;
      recentSimulationId.value = sim.id;
      // label 用 divergence 头 16 字 — 跟 DashboardView 卡片标题口径一致
      recentSimulationLabel.value = (sim.divergence ?? "推演").slice(0, 16);
    } else if (phase === "failed" || phase === "error") {
      runningParticipantIds.value = [];
      dimMode.value = false;
      // 失败不留光迹
    }
  },
  { immediate: true },
);

// ============================================================
// 节点选中 + 抽屉(Sprint 1.M.1.A + .B)
// ============================================================

const selectedNodeId = ref<string | null>(null);
const selectedNodeType = ref<"PERSON" | "EVENT" | "OTHER">("OTHER");
const drawerOpen = ref(false);
// 抽屉模式 — edit 是常规点节点编辑;create 是右键菜单选了类型,准备填字段创建新节点
const drawerMode = ref<"edit" | "create">("edit");

// 2026-05-12 删除 hover 白色浮窗(用户拍板):
//   原 2026-05 P2 加了 NodeHoverCard 跟随鼠标显角色名+identity,但用户反馈白色卡片
//   太大太挤、与 force-graph 自带的"黑底小字 tooltip"重复。黑底 tooltip 更直观且不
//   占空间,删白色浮窗,保留 force-graph 默认 hover 行为 + 点节点开 drawer 完整档案。

function onNodeClick(node: GraphNode) {
  selectedNodeId.value = node.id;
  selectedNodeType.value =
    node.type === "PERSON" || node.type === "EVENT" ? node.type : "OTHER";
  drawerMode.value = "edit";
  drawerOpen.value = true;
}

function closeDrawer() {
  drawerOpen.value = false;
  drawerMode.value = "edit";   // 复位,避免下次点节点撞到上次的 create 残留
  // 留 selectedNodeId 不清,下次再开同节点不闪烁;真切节点时会被覆盖
}

async function onDrawerUpdated() {
  // 字段保存成功 → reload graph 让 3D 标签 / tone 反映新数据
  await load();
}

async function onDrawerDeleted() {
  // 角色已删 → 关抽屉 + reload graph(被删节点消失)
  drawerOpen.value = false;
  selectedNodeId.value = null;
  drawerMode.value = "edit";
  await load();
}

/**
 * create 模式提交成功 — drawer POST 完返回新节点。
 * reload graph 让新节点显示,然后关 drawer 复位 mode。
 *
 * 注意:不立刻切到 edit 模式接着改,因为 5 字段已经在 create 表单里填完了;
 * 用户想再改可以点新节点重新打开 edit drawer,体验更直觉。
 */
async function onDrawerCreated(_payload:
  | { type: "PERSON"; node: Character }
  | { type: "EVENT"; node: ProjectEvent }
) {
  await load();
  drawerOpen.value = false;
  drawerMode.value = "edit";
  selectedNodeId.value = null;
  selectedNodeType.value = "OTHER";
}

// ============================================================
// 拖节点持久化(Sprint 1.M.1.C)— debounce 800ms 后 PATCH
// 只对 PERSON 节点生效(EVENT 没 position 字段)
// ============================================================

const pendingPosition: Map<string, { x: number; y: number; z: number }> = new Map();
let positionFlushTimer: ReturnType<typeof setTimeout> | null = null;

function onNodeDragEnd(node: GraphNode, position: { x: number; y: number; z: number }) {
  if (node.type !== "PERSON") return;   // events 后端 schema 无 position,忽略
  pendingPosition.set(node.id, position);

  if (positionFlushTimer) clearTimeout(positionFlushTimer);
  positionFlushTimer = setTimeout(flushPositions, 800);
}

async function flushPositions() {
  positionFlushTimer = null;
  if (pendingPosition.size === 0) return;
  // 拷贝 + 清空,异步过程中再发生拖动不影响这批
  const batch = new Map(pendingPosition);
  pendingPosition.clear();

  // 并行 PATCH(角色互不依赖)
  await Promise.allSettled(
    Array.from(batch.entries()).map(([charId, pos]) => {
      const body: UpdateCharacterRequest = {
        position_x: pos.x,
        position_y: pos.y,
        position_z: pos.z,
      };
      return api.patch(`/characters/${charId}`, body).catch((e) => {
        // 单条失败不阻塞其他;静默丢(下次拖会再尝试)
        if (import.meta.env.DEV) {
          console.warn(
            `保存节点 ${charId} 位置失败:`,
            e instanceof ApiError ? e.message : e,
          );
        }
      });
    }),
  );
  // **不 reload** — reload 会触发 force-graph 重新仿真把节点弹回平衡点,
  //  抹掉用户刚拖的视觉。后端已存,下次进入图谱时通过 character.position_xyz 应用即可
}

onBeforeUnmount(() => {
  // 卸载前 flush 一次,防丢
  if (positionFlushTimer) {
    clearTimeout(positionFlushTimer);
    void flushPositions();
  }
  // 1.M.2:refined pulse timer 也要清,避免泄露
  if (refinedPulseTimer) {
    clearTimeout(refinedPulseTimer);
    refinedPulseTimer = null;
  }
});

// ============================================================
// 1.M.1.D 右键空白 → 弹 NodeCreatorMenu
// ============================================================

const menuOpen = ref(false);
const menuPos = ref({ x: 0, y: 0 });

function onBackgroundRclick(pos: { x: number; y: number }) {
  // Shift 选中态下右键 → 取消 Shift,不弹菜单(避免误操作)
  if (pendingShiftSource.value) {
    pendingShiftSource.value = null;
    return;
  }
  // 2026-05-12 用户拍板:末尾态禁用"新角色 / 新事件"。
  // 末尾态语义 = "继承原作末段意志,从末尾接着写",用户不应该往原作图谱里加节点
  // (会破坏续写场景的"原作完整性");需要新建的话,创建新的中间态项目。
  if (project.value?.mode === "end") {
    toast.info("末尾态承接原作末段,不可新建节点 — 想新建,请用中间态项目");
    return;
  }
  menuPos.value = pos;
  menuOpen.value = true;
}

function closeMenu() {
  menuOpen.value = false;
}

/**
 * 用户在右键菜单选了「新角色 / 新事件」— 不立刻 POST,而是开 NodeEditDrawer 的
 * create 模式,让用户填完整 5 字段 / 2 字段。
 *
 * 用户在 drawer 不填名关掉 → drawer emit close → 完全不会创建任何节点,
 * 杜绝"未命名孤儿节点"残留。
 *
 * 用户填好按「创建」 → drawer 自己 POST → emit created → onDrawerCreated reload + 关。
 */
function handleRequestCreate(payload: { type: "character" | "event" }) {
  selectedNodeId.value = null;
  selectedNodeType.value = payload.type === "character" ? "PERSON" : "EVENT";
  drawerMode.value = "create";
  drawerOpen.value = true;
}

// ============================================================
// 1.M.1.E Shift+点节点 → 连关系(两次 shift+click 之间记 source)
// ============================================================

const pendingShiftSource = ref<{ id: string; name: string } | null>(null);
const relCreatorOpen = ref(false);
const relTargetNode = ref<{ id: string; name: string } | null>(null);

function onNodeShiftClick(node: GraphNode) {
  // 仅 PERSON 节点能连关系(关系连的是角色,不是事件)
  if (node.type !== "PERSON") {
    toast.info("关系只能连在角色之间(事件节点不参与)");
    return;
  }

  if (!pendingShiftSource.value) {
    // 第 1 次 — 记 source,UI 提示用户连第 2 个
    pendingShiftSource.value = { id: node.id, name: node.name };
    return;
  }

  // 第 2 次 — source 已有,弹关系创建对话框
  if (pendingShiftSource.value.id === node.id) {
    // 同节点 — 取消选中
    pendingShiftSource.value = null;
    return;
  }
  relTargetNode.value = { id: node.id, name: node.name };
  relCreatorOpen.value = true;
}

async function onRelCreated() {
  await load();
  pendingShiftSource.value = null;
  relTargetNode.value = null;
}

function onRelCreatorClose() {
  relCreatorOpen.value = false;
  // 关闭对话框时清 source(避免下次 Shift+click 撞到上次残留)
  pendingShiftSource.value = null;
  relTargetNode.value = null;
}

function cancelShiftSource() {
  pendingShiftSource.value = null;
}

// ============================================================
// EVENT 抽屉用:角色列表(participants 多选)
// ============================================================

const charactersForParticipants = computed(() => {
  if (!graphData.value) return [];
  return graphData.value.nodes
    .filter((n) => n.type === "PERSON")
    .map((n) => ({ id: n.id, name: n.name }));
});

function backToProject() {
  router.push(`/projects/${props.id}`);
}

// useProjectGraph 内部 watch(getProjectId, load, { immediate: true }) 已覆盖初次加载 + 切项目重拉
</script>

<template>
  <div class="graph-page">
    <!-- 顶栏:浅色 + 简洁 -->
    <header class="topbar">
      <button class="back-btn" @click="backToProject">
        <span class="back-arrow">←</span>
        <span>返回项目</span>
      </button>

      <div v-if="project" class="project-meta">
        <span class="project-name">{{ project.name }}</span>
        <span v-if="graphData" class="meta-extra mono">
          {{ graphData.nodes.length }} 节点 · {{ graphData.links.length }} 关系
        </span>
      </div>

      <div class="topbar-right">
        <!-- Sprint 3.A polish:关系强度阈值滑块 — 仅非初始态显
             (初始态用户手动建关系,默认 strength='moderate',阈值 30 时全显示,
              滑块的产品语义对初始态没价值;中间/末尾/周期态 LLM 抽出 5 级强度,
              阈值滑块能根本性改善图谱密度) -->
        <div
          v-if="project && project.mode !== 'initial'"
          class="threshold-control"
          :title="`关系连线显示阈值 ${thresholdLocal}%(${thresholdHintLabel}):${thresholdHintDetail}`"
        >
          <span class="threshold-label">阈值</span>
          <input
            type="range"
            class="threshold-slider"
            min="0" max="100" step="10"
            :value="thresholdLocal"
            @input="onThresholdInput(($event.target as HTMLInputElement).valueAsNumber)"
            @change="onThresholdCommit"
          />
          <span class="threshold-value mono">{{ thresholdLocal }}%</span>
        </div>

        <!-- Sprint 2.C+ 反事实工作台入口(始终显,即使没 active 反事实也能进去创建)
             Sprint 3.A 修:末尾态(end)和初始态(initial)与反事实工作台心智冲突 — 末尾态
             "不动原作,从末尾接着写",根本不该有反事实编辑入口。只在中间/周期态显。 -->
        <button
          v-if="project?.mode === 'middle' || project?.mode === 'cycle'"
          type="button"
          class="cf-chip-btn"
          :class="{ 'cf-chip-empty': cf.totalActive.value === 0 }"
          title="反事实工作台 — 改角色 / 事件 / 世界观 → 让 AI 推演 what-if"
          @click="openWorkbench"
        >
          <span class="cf-chip-icon">⟲</span>
          反事实
          <span v-if="cf.totalActive.value > 0" class="cf-chip-count mono">{{ cf.totalActive.value }}</span>
          <span v-else class="cf-chip-hint">+</span>
        </button>

        <!-- Sprint D.2.A:沙盘模式开关 — 仅 middle / cycle 显
             默认走形态 A/B/C(编辑器 / 对焦 / 续写联动);打开 = 形态 D 沙盘:
             反事实源节点显光晕环(紫=角色 / 蓝=事件) -->
        <button
          v-if="canShowSandboxMode"
          type="button"
          class="sandbox-toggle-btn"
          :class="{ 'sandbox-toggle-btn--active': sandboxMode }"
          :title="sandboxTitle"
          @click="toggleSandboxMode"
        >
          <span class="sandbox-icon">⏣</span>
          {{ sandboxMode ? '沙盘 · 已开' : '沙盘' }}
        </button>

        <!-- 沙盘模式 + 有世界观反事实 → 顶栏 chip 提示
             (世界观 target_id='_global_' 无具体节点,不能渲染光晕环,所以用 chip 兜底) -->
        <span
          v-if="sandboxMode && worldCfCount > 0"
          class="world-cf-chip"
          title="金色 — 世界观反事实:体裁 / 设定 / 超能力体系 / 时间轴 / 基调 / 自由描述"
        >
          <span class="world-cf-icon">🌐</span>
          世界观 × {{ worldCfCount }}
        </span>

        <div class="fps-badge">
          <span class="fps-label">FPS</span>
          <span
            class="fps-value mono"
            :class="{
              'fps-good': fps >= 55,
              'fps-ok':   fps >= 30 && fps < 55,
              'fps-bad':  fps < 30,
            }"
          >{{ fps }}</span>
        </div>

        <!-- 1.M Polish:使用说明按钮 -->
        <button
          type="button"
          class="help-btn"
          aria-label="3D 图谱使用说明"
          title="3D 图谱使用说明(基础操作 / 关系 / AI 联动)"
          @click="helpOpen = true"
        >?</button>
      </div>
    </header>

    <!-- Sprint D.6 加载骨架:3D 图谱首加载时显"星图"占位
         (3D 图谱无法做精确骨架 — 用"等比例随机点 + shimmer"提示"图谱正在生成")-->
    <div v-if="loading" class="state-overlay loading-graph-skeleton" aria-busy="true" aria-live="polite">
      <div class="skeleton-graph-dots">
        <span
          v-for="i in 24"
          :key="i"
          class="skeleton skeleton-dot"
          :style="{
            top: `${10 + ((i * 17) % 80)}%`,
            left: `${5 + ((i * 23) % 90)}%`,
            animationDelay: `${(i % 8) * 0.18}s`,
          }"
        ></span>
      </div>
      <p class="state-msg state-msg--skeleton">加载 3D 图谱…</p>
    </div>

    <div v-else-if="error" class="state-overlay">
      <p class="state-msg state-error">{{ error }}</p>
      <button class="ghost-btn" @click="load">重试</button>
    </div>

    <div
      v-else-if="graphData && graphData.nodes.length === 0"
      class="state-overlay"
    >
      <p class="state-msg">这个项目还没有任何角色或事件</p>
      <button class="ghost-btn" @click="backToProject">回去添加</button>
    </div>

    <!-- 3D 图谱:CrystalGraph 内部仍是深空(产品差异化的视觉,不动) -->
    <CrystalGraph
      v-else-if="graphData"
      class="graph-canvas"
      :data="graphData"
      :zoom-sensitivity="1.0"
      :refined-node-ids="refinedNodeIds"
      :participants="runningParticipantIds"
      :dim-mode="dimMode"
      :trail-node-ids="trailNodeIds"
      :affected-node-ids="visibleAffectedNodeIds"
      :sandbox-mode="sandboxMode"
      :counterfactual-source-ids="counterfactualSourceIds"
      :graph-strength-threshold="thresholdLocal"
      @fps-update="(v) => (fps = v)"
      @node-click="onNodeClick"
      @node-dragend="onNodeDragEnd"
      @node-shift-click="onNodeShiftClick"
      @node-frame-click="onNodeFrameClick"
      @background-rclick="onBackgroundRclick"
    />

    <!-- 2026-05-12 删除 NodeHoverCard 浮窗(用户拍板):
         force-graph 自带黑底小字 tooltip 已足够,白色大卡片冗余 -->

    <!-- Sprint 1.M.1.E:Shift 选中第 1 个节点后,顶部 hint bar 提示连第 2 个 -->
    <div v-if="pendingShiftSource" class="shift-hint">
      已选中
      <strong>{{ pendingShiftSource.name }}</strong>
      作为关系起点 — Shift + 点另一个角色连关系
      <button class="cancel-shift" @click="cancelShiftSource">取消</button>
    </div>

    <!-- Sprint 1.M.1.A + .B + .D + .F + 1.M.2 + 1.M.3:抽屉做编辑 / 创建 / 对焦 / 光迹跳详情 -->
    <NodeEditDrawer
      :open="drawerOpen"
      :node-id="selectedNodeId"
      :node-type="selectedNodeType"
      :characters-for-participants="charactersForParticipants"
      :mode="drawerMode"
      :project-id="props.id"
      :recent-simulation-id="
        recentSimulationId && selectedNodeId && trailNodeIds.includes(selectedNodeId)
          ? recentSimulationId
          : null
      "
      :recent-simulation-label="recentSimulationLabel"
      @close="closeDrawer"
      @updated="onDrawerUpdated"
      @deleted="onDrawerDeleted"
      @created="onDrawerCreated"
      @request-focus="onDrawerRequestFocus"
    />

    <!-- 1.M.2:AI 角色对焦 modal — focusCharacterId 把 modal 锁定到单个角色 -->
    <CharacterFocusModal
      :open="focusOpen"
      :project-id="props.id"
      :project-name="project?.name ?? ''"
      :focus-character-id="focusCharacterId"
      :focus-character-name="focusCharacterName"
      @close="onFocusClose"
      @quota-exceeded="onFocusQuotaExceeded"
    />

    <!-- Sprint 1.M.1.D:右键空白弹小菜单(选类型 → 父开 drawer create 模式让用户填字段) -->
    <NodeCreatorMenu
      :open="menuOpen"
      :x="menuPos.x"
      :y="menuPos.y"
      @close="closeMenu"
      @request-create="handleRequestCreate"
    />

    <!-- 1.M Polish:使用说明 modal(顶栏 ? 按钮触发) -->
    <GraphHelpModal :open="helpOpen" @close="helpOpen = false" />

    <!-- 1.M.3:框选浮动 bar — 至少 1 个 PERSON 框选时浮在底部 -->
    <div v-if="frameSelectedNames.length > 0" class="frame-bar surface">
      <div class="frame-info">
        <span class="frame-icon">📦</span>
        <span class="frame-label">已框选 {{ frameSelectedNames.length }} 个角色:</span>
        <span class="frame-names">{{ frameSelectedNames.join(" / ") }}</span>
      </div>
      <div class="frame-actions">
        <button
          type="button"
          class="ghost-btn frame-clear"
          @click="clearFrame"
        >清空</button>
        <button
          type="button"
          class="primary-btn frame-go"
          :disabled="frameSelectedNames.length < 2"
          @click="openSimulationFromFrame"
        >{{ simulateButtonLabel.icon }} {{ simulateButtonLabel.text }}</button>
      </div>
    </div>

    <!-- 1.M.3:推演 dock — 框选 → AI 续写 走它,prefillDivergence 把名字预填进去 -->
    <!-- Sprint 2.C+:dock 收到 project mode 自动按 4 态差异化标题/子标题/摘要区,
         并且 onOpenWorkbench 直接打开反事实工作台 -->
    <SimulationDock
      :open="simulationOpen"
      :project-id="props.id"
      :project-name="project?.name ?? ''"
      :character-count="realCharacterCount"
      :prefill-divergence="prefillDivergence"
      :counterfactuals-instance="cf"
      :project-mode="(project?.mode as 'initial' | 'middle' | 'end' | 'cycle' | undefined)"
      :on-open-workbench="openWorkbench"
      @close="onSimulationClose"
      @quota-exceeded="onSimulationQuotaExceeded"
    />

    <!-- Sprint 2.C+ 反事实工作台(替换 2.C 的 CounterfactualPanel)-->
    <!-- 2.C+ polish: 传 project 让世界观 tab 用 world_baseline 预填"原"字段 -->
    <CounterfactualWorkbench
      :open="workbenchOpen"
      :composable="cf"
      :characters="wbCharacters"
      :events="wbEvents"
      :project="project ?? null"
      return-to="graph"
      @close="workbenchOpen = false"
      @changed="onWorkbenchChanged"
      @baseline-inferred="onBaselineInferred"
    />

    <!-- Sprint 1.M.1.E:Shift 连关系 → 类型选择对话框 -->
    <RelationshipCreator
      v-if="pendingShiftSource && relTargetNode"
      :open="relCreatorOpen"
      :source-id="pendingShiftSource.id"
      :source-name="pendingShiftSource.name"
      :target-id="relTargetNode.id"
      :target-name="relTargetNode.name"
      :project-id="props.id"
      @close="onRelCreatorClose"
      @created="onRelCreated"
    />
  </div>
</template>

<style scoped>
.graph-page {
  position: fixed;
  inset: 0;
  background: var(--color-bg);
}

.graph-canvas {
  position: absolute;
  inset: 0;
}

/* 顶栏:浅色 + 半透明,叠在 3D 上 */
.topbar {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: var(--topbar-height);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 var(--space-5);
  background: rgba(250, 247, 242, 0.85);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--color-border);
  z-index: var(--z-sticky);
}

.back-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text);
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) var(--ease-out);
}

.back-btn:hover {
  background: var(--color-surface-hover);
}

.back-arrow {
  font-size: var(--text-md);
}

.project-meta {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
}

.project-name {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
}

.meta-extra {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

/* 顶栏右侧:FPS + 帮助按钮 */
.topbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

/* Sprint 3.A 顶栏关系强度阈值滑块(中间/末尾/周期态显) */
.threshold-control {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px var(--space-2);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  cursor: help;   /* 整个控件 hover 显 title tooltip(浏览器原生) */
}
.threshold-label {
  letter-spacing: 0.02em;
}
.threshold-slider {
  width: 88px;
  height: 4px;
  appearance: none;
  background: var(--color-border-strong);
  border-radius: 2px;
  cursor: pointer;
  outline: none;
}
.threshold-slider::-webkit-slider-thumb {
  appearance: none;
  width: 14px;
  height: 14px;
  background: var(--color-accent);
  border-radius: 50%;
  cursor: pointer;
  border: 2px solid var(--color-surface);
  box-shadow: 0 0 0 1px var(--color-accent-border);
}
.threshold-slider::-moz-range-thumb {
  width: 14px;
  height: 14px;
  background: var(--color-accent);
  border-radius: 50%;
  cursor: pointer;
  border: 2px solid var(--color-surface);
  box-shadow: 0 0 0 1px var(--color-accent-border);
}
.threshold-value {
  min-width: 32px;
  text-align: right;
  font-size: 11px;
  color: var(--color-accent-text);
  font-weight: 600;
}

/* Sprint 2.C 顶栏反事实 chip */
.cf-chip-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.cf-chip-btn:hover {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}
.cf-chip-icon {
  font-size: var(--text-sm);
  line-height: 1;
}
.cf-chip-count {
  font-size: 11px;
  padding: 0 6px;
  border-radius: var(--radius-sm);
  background: rgba(255, 255, 255, 0.4);
}
.cf-chip-empty {
  background: var(--color-bg-subtle);
  color: var(--color-text-muted);
  border-color: var(--color-border);
}
.cf-chip-empty:hover {
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
  border-color: var(--color-accent-border);
}
.cf-chip-hint {
  font-size: var(--text-base);
  font-weight: 600;
  opacity: 0.6;
}

/* Sprint D.2.A — 沙盘模式切换按钮(顶栏)*/
.sandbox-toggle-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.sandbox-toggle-btn:hover {
  color: var(--color-accent-text);
  border-color: var(--color-accent-border);
  background: var(--color-accent-soft);
}
.sandbox-toggle-btn--active {
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.18);
}
.sandbox-toggle-btn--active:hover {
  filter: brightness(1.05);
  color: var(--color-text-on-accent);
  background: var(--color-accent);
}
.sandbox-icon {
  font-size: var(--text-sm);
  line-height: 1;
}

/* Sprint D.2.A — 世界观反事实 chip(沙盘模式下显)*/
.world-cf-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px var(--space-2);
  font-size: 11px;
  color: #B45309;
  background: rgba(245, 158, 11, 0.14);
  border: 1px solid rgba(245, 158, 11, 0.4);
  border-radius: var(--radius-full);
  cursor: help;
}
.world-cf-icon {
  font-size: var(--text-xs);
  line-height: 1;
}

.fps-badge {
  display: flex;
  align-items: baseline;
  gap: var(--space-1);
  font-size: var(--text-xs);
}

.fps-label {
  color: var(--color-text-subtle);
}

.fps-value {
  font-weight: 500;
}

.fps-good { color: #16A34A; }
.fps-ok   { color: #D97706; }
.fps-bad  { color: var(--color-danger); }

/* 1.M Polish:使用说明圆形按钮 */
.help-btn {
  width: 24px;
  height: 24px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.help-btn:hover {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
}

/* Shift 选中第 1 节点后的 hint bar(1.M.1.E)*/
.shift-hint {
  position: absolute;
  top: calc(var(--topbar-height) + var(--space-3));
  left: 50%;
  transform: translateX(-50%);
  padding: var(--space-2) var(--space-4);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-full);
  font-size: var(--text-sm);
  color: var(--color-accent-text);
  z-index: var(--z-sticky);
  display: flex;
  align-items: center;
  gap: var(--space-3);
  box-shadow: var(--shadow-md);
  white-space: nowrap;
  max-width: calc(100vw - var(--space-8));
  overflow: hidden;
  text-overflow: ellipsis;
}
.shift-hint strong {
  color: var(--color-text);
  font-weight: 600;
}
.cancel-shift {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: transparent;
  padding: 2px var(--space-2);
  border-radius: var(--radius-sm);
}
.cancel-shift:hover {
  color: var(--color-danger);
  background: var(--color-danger-soft);
}

/* 状态叠加 */
.state-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  color: var(--color-text-muted);
}

.state-msg {
  font-size: var(--text-base);
}

/* Sprint D.6 — 3D 图谱加载骨架:深空底 + 随机点 + 微 shimmer */
.loading-graph-skeleton {
  background: var(--hj-deep-space);   /* 跟 CrystalGraph 的深空底一致,无缝过渡 */
}
.skeleton-graph-dots {
  position: relative;
  width: 100%;
  height: 100%;
  pointer-events: none;
}
.skeleton-dot {
  position: absolute;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: rgba(167, 139, 250, 0.35);  /* 浅紫(浑晶 accent 暗版)*/
  animation: skeleton-dot-pulse 1.6s ease-in-out infinite;
}
.skeleton-dot::after {
  display: none;   /* 覆盖 .skeleton::after shimmer — 点点不要 shimmer 扫光 */
}
@keyframes skeleton-dot-pulse {
  0%, 100% { opacity: 0.15; transform: scale(0.8); }
  50%      { opacity: 0.7;  transform: scale(1.1); }
}
.state-msg--skeleton {
  position: relative;
  z-index: 1;
  color: rgba(229, 220, 200, 0.55);   /* 米色,深空底上柔和 */
}

@media (prefers-reduced-motion: reduce) {
  .skeleton-dot { animation: none; opacity: 0.4; }
}

.state-error {
  color: var(--color-danger);
}

.ghost-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
}

.ghost-btn:hover {
  background: var(--color-surface-hover);
}

/* 1.M.3:框选浮动 bar — 类似 shift-hint bar 但放底部,更宽 */
.frame-bar {
  position: absolute;
  bottom: var(--space-5);
  left: 50%;
  transform: translateX(-50%);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-sticky);
  display: flex;
  align-items: center;
  gap: var(--space-4);
  max-width: calc(100vw - var(--space-8));
  background: var(--color-surface);
  border: 1px solid var(--color-accent-border);
}
.frame-info {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
}
.frame-icon {
  font-size: var(--text-md);
}
.frame-label {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  flex-shrink: 0;
}
.frame-names {
  font-size: var(--text-sm);
  color: var(--color-text);
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.frame-actions {
  display: flex;
  gap: var(--space-2);
  flex-shrink: 0;
}
.frame-clear {
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
}
.frame-go {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) var(--ease-out);
}
.frame-go:hover:not(:disabled) {
  background: var(--color-accent-hover);
}
.frame-go:disabled {
  background: var(--color-text-subtle);
  cursor: not-allowed;
  opacity: 0.6;
}
</style>
