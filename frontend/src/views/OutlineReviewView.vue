<script setup lang="ts">
/**
 * OutlineReviewView — Outline-first 长篇生成审核 + 编辑界面(Sprint 6.A2 M6,2026-05-20)。
 *
 * URL: /simulations/:id/outline   需登录
 *
 * 产品角色:
 *   M6 治本核心 — 用户在按 outline 生成长篇之前,先审核 / 编辑 outline 大纲
 *   每幕的 location / key_events / key_props / transition_from_last 是
 *   跨幕全局一致性的"图纸",一旦批准 → 按 outline 逐幕跑 → 严格遵守
 *
 * 状态驱动:
 *   drafting       LLM 正在生成 → 转圈等待(自动 2s 轮询)
 *   awaiting_user  用户审核态 → 显完整编辑界面 + 批准按钮
 *   approved       已批准 → 自动跳 /simulations/:id 看进度
 *   generating     正在按 outline 跑 → 跳 /simulations/:id
 *   done           已跑完 → 跳 /simulations/:id 看产物
 *   failed         outline 生成失败 → 显错误 + 重新生成按钮
 *
 * 操作:
 *   ← 返回项目页
 *   编辑某幕(scene_summary / location / key_events / key_props / transition)
 *   编辑 global theme / arc
 *   重新生成 outline(failed / awaiting_user 时可用)
 *   批准启动生成
 */
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "../api/client";
import {
  ApiError,
  type OutlineScene,
  type OutlineSceneKeyProp,
  type SimulationOutline,
  type UpdateOutlineGlobalRequest,
  type UpdateOutlineSceneRequest,
} from "../api/types";
import { confirm as confirmDialog } from "../composables/useConfirm";
import { toast } from "../composables/useToast";
import TensionCurvePanel from "../components/TensionCurvePanel.vue";

const route = useRoute();
const router = useRouter();

const simId = computed(() => String(route.params.id ?? ""));

const outline = ref<SimulationOutline | null>(null);
const loading = ref(true);
const errorMessage = ref<string | null>(null);

/** 编辑中的 scene id(展开编辑卡片);null = 全部折叠 */
const editingSceneId = ref<string | null>(null);

/** 编辑中的全局 theme/arc */
const editingGlobal = ref(false);

/** 批准中 loading 防双击 */
const approving = ref(false);

/** drafting 状态轮询 timer */
let pollTimer: ReturnType<typeof setInterval> | null = null;

// ============================================================
// 拉 outline
// ============================================================

async function fetchOutline() {
  errorMessage.value = null;
  try {
    const data = await api.get<SimulationOutline>(
      `/simulations/${simId.value}/outline`,
    );
    outline.value = data;
    loading.value = false;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      // M6-fix2:可能 outline 占位 row 还没 INSERT(后台线程刚启动几百 ms)
      // 不 mark error,继续轮询(loading 保持 true,显示工作流转圈)
      // 启动 outline-not-yet 轮询
      if (pollTimer === null) {
        startPollingIfDrafting();
      }
      return;
    }
    loading.value = false;
    if (e instanceof ApiError) {
      errorMessage.value = e.message;
    } else {
      errorMessage.value = "网络异常,无法获取 outline";
    }
  }
}

function startPollingIfDrafting() {
  if (pollTimer !== null) return;
  pollTimer = setInterval(async () => {
    await fetchOutline();
    // outline 已到 awaiting_user / failed / approved / generating / done → 停轮询
    if (outline.value && outline.value.state !== "drafting") {
      stopPolling();
      // 若 generating / done → 跳详情
      if (
        outline.value.state === "generating" ||
        outline.value.state === "done"
      ) {
        router.replace(`/simulations/${simId.value}`);
      }
    }
  }, 2000);
}

function stopPolling() {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

// 初始化
fetchOutline().then(() => {
  if (outline.value?.state === "drafting") {
    startPollingIfDrafting();
  } else if (
    outline.value?.state === "generating" ||
    outline.value?.state === "done"
  ) {
    // 已批准 → 跳详情
    router.replace(`/simulations/${simId.value}`);
  }
});

// 切换 simId 时(理论上不会)重订
// P-6 修复(2026-05-23):stale-while-revalidate — 不立刻清 outline 显 loading,
// 保留旧数据无缝替换,fetchOutline 拉来新数据后自然覆盖
watch(simId, () => {
  fetchOutline();
});

onBeforeUnmount(() => {
  stopPolling();
});

// ============================================================
// 编辑某幕
// ============================================================

/** 复制 scene 字段到本地草稿,避免直接污染 outline.scenes */
const sceneDraft = ref<UpdateOutlineSceneRequest>({});

// M8.A(2026-05-20)— location 字段 datalist 推荐项:
// 列出 outline 内已用的 unique locations(用户改 / 加幕时一目了然看到已用场景集),
// 鼓励复用已有 + 自由输入新场景(自创的会被后端 sequel_scene_sync 入库)
const locationSuggestions = computed<string[]>(() => {
  const set = new Set<string>();
  if (outline.value?.scenes) {
    for (const s of outline.value.scenes) {
      const loc = (s.location || "").trim();
      if (loc) set.add(loc);
    }
  }
  return Array.from(set).sort();
});

// M8.D(2026-05-21)— 场景多样性健康度 chip
// 标尺:unique location 数 vs 总幕数
//   - unique >= ⌈N/2⌉ → 健康(绿)
//   - unique >= ⌈N/4⌉ → 一般(灰)
//   - unique <  ⌈N/4⌉ → 警告(黄,清一色教室征兆)
const diversityChip = computed<{ text: string; tone: "ok" | "neutral" | "warn"; tooltip: string } | null>(() => {
  if (!outline.value?.scenes || outline.value.scenes.length === 0) return null;
  const total = outline.value.scenes.length;
  const unique = locationSuggestions.value.length;
  const minHealthy = Math.ceil(total / 2);
  const minNeutral = Math.ceil(total / 4);
  let tone: "ok" | "neutral" | "warn" = "ok";
  let tooltip = "";
  if (unique >= minHealthy) {
    tone = "ok";
    tooltip = `场景丰富度健康(${unique}/${total},≥ N/2)`;
  } else if (unique >= minNeutral) {
    tone = "neutral";
    tooltip = `场景丰富度一般(${unique}/${total},≥ N/4 但 < N/2)`;
  } else {
    tone = "warn";
    tooltip = `场景过于单一(${unique}/${total},< N/4)— 重新生成时建议勾选「强化多样性」让 LLM 多自创新场景`;
  }
  return {
    text: `场景 ${unique} / ${total} 幕`,
    tone,
    tooltip,
  };
});


// ============================================================
// Sprint 6.A2 MP(2026-05-21)— planner 张力可视化
// ============================================================

/** 张力 chip 配色:< 30 蓝低 / 30-69 黄中 / >= 70 红高 */
function tensionChipClass(percent: number): string {
  if (percent >= 70) return "tension-chip--high";
  if (percent >= 30) return "tension-chip--mid";
  return "tension-chip--low";
}

/** 节奏标签:fast → ⚡ / normal → · / slow → 🌙 */
function pacingLabel(tempo: string | null | undefined): string {
  if (tempo === "fast") return "⚡ 快";
  if (tempo === "slow") return "🌙 慢";
  return "中速";
}

/** 张力 tooltip(显示 planner reasoning 暂未存于 OutlineScene,展示静态描述) */
function tensionTooltip(scene: OutlineScene): string {
  const tp = scene.tension_percent ?? 0;
  const level =
    tp >= 70 ? "高张力(句短促紧凑 / 心理密集 / 感官冲击)"
    : tp >= 30 ? "中张力(自然叙事节奏)"
    : "低张力(长句铺陈 / 环境描写 / 慢镜头)";
  const tempo = scene.pacing_tempo === "fast"
    ? "快(推进感强)"
    : scene.pacing_tempo === "slow"
    ? "慢(铺垫细腻)"
    : "中速(自然节奏)";
  return `张力 ${tp}% — ${level} · 节奏 ${tempo}`;
}

// 2026-06-02 cleanup:tensionCurve 已搬到 TensionCurvePanel 内部计算,这里删除未用 computed


// ============================================================
// Sprint 6.A2 M8.B(2026-05-21)— 加幕 / 删幕 / 拖拽重排
// ============================================================

const operating = ref(false);     // 加 / 删 / 重排 进行中防双击

/** 在指定 position 插入新空白幕 */
async function handleInsertScene(position: number) {
  if (!outline.value || operating.value) return;
  operating.value = true;
  try {
    const data = await api.post<SimulationOutline>(
      `/simulations/${simId.value}/outline/scenes`,
      {
        position,
        scene_summary: "新增幕(请编辑)",
        scene_purpose: "推进主线",
        location: "",
        time_anchor: "",
      },
    );
    outline.value = data;
    toast.success(`已在第 ${position + 1} 幕处插入新幕,记得编辑细节`);
  } catch (e) {
    toast.error(e instanceof ApiError ? `插入失败:${e.message}` : "插入失败");
  } finally {
    operating.value = false;
  }
}

/** 删某幕 — 隐晦提示(红色按钮 + disable 防连点),无二次确认 prevent 烦扰熟练用户 */
async function handleDeleteScene(scene: OutlineScene) {
  if (!outline.value || operating.value) return;
  if ((outline.value.scenes?.length ?? 0) <= 1) {
    toast.warning("outline 至少保留 1 幕,不能全删");
    return;
  }
  operating.value = true;
  try {
    const data = await api.delete<SimulationOutline>(
      `/simulations/${simId.value}/outline/scenes/${scene.id}`,
    );
    outline.value = data;
    toast.success("已删除该幕");
  } catch (e) {
    toast.error(e instanceof ApiError ? `删除失败:${e.message}` : "删除失败");
  } finally {
    operating.value = false;
  }
}

/** 拖拽 — HTML5 native draggable;在 dragstart 记 source index,drop 时触发 reorder */
const draggedSceneId = ref<string | null>(null);
const dragOverSceneId = ref<string | null>(null);

function onDragStart(scene: OutlineScene, ev: DragEvent) {
  if (!canEdit.value || operating.value) {
    ev.preventDefault();
    return;
  }
  draggedSceneId.value = scene.id;
  if (ev.dataTransfer) {
    ev.dataTransfer.effectAllowed = "move";
    ev.dataTransfer.setData("text/plain", scene.id);
  }
}

function onDragOver(scene: OutlineScene, ev: DragEvent) {
  if (!draggedSceneId.value || draggedSceneId.value === scene.id) return;
  ev.preventDefault();    // 允许 drop
  if (ev.dataTransfer) ev.dataTransfer.dropEffect = "move";
  dragOverSceneId.value = scene.id;
}

function onDragLeave(scene: OutlineScene) {
  if (dragOverSceneId.value === scene.id) {
    dragOverSceneId.value = null;
  }
}

async function onDrop(targetScene: OutlineScene, ev: DragEvent) {
  ev.preventDefault();
  const sourceId = draggedSceneId.value;
  draggedSceneId.value = null;
  dragOverSceneId.value = null;
  if (!sourceId || sourceId === targetScene.id || !outline.value) return;

  // 构造新顺序:把 source 移到 target 当前位置
  const current = outline.value.scenes.map((s) => s.id);
  const sourceIdx = current.indexOf(sourceId);
  const targetIdx = current.indexOf(targetScene.id);
  if (sourceIdx < 0 || targetIdx < 0) return;
  const newOrder = [...current];
  newOrder.splice(sourceIdx, 1);                          // 拿走 source
  newOrder.splice(targetIdx, 0, sourceId);                // 插到 target 当前位置

  operating.value = true;
  try {
    const data = await api.patch<SimulationOutline>(
      `/simulations/${simId.value}/outline/reorder`,
      { ordered_scene_ids: newOrder },
    );
    outline.value = data;
    toast.success("已重排");
  } catch (e) {
    toast.error(e instanceof ApiError ? `重排失败:${e.message}` : "重排失败");
  } finally {
    operating.value = false;
  }
}

function onDragEnd() {
  draggedSceneId.value = null;
  dragOverSceneId.value = null;
}

function startEditScene(scene: OutlineScene) {
  editingSceneId.value = scene.id;
  sceneDraft.value = {
    scene_summary: scene.scene_summary,
    scene_purpose: scene.scene_purpose,
    location: scene.location,
    time_anchor: scene.time_anchor,
    characters_present: [...scene.characters_present],
    key_events: [...scene.key_events],
    key_props: scene.key_props.map((p) => ({
      name: p.name,
      action: p.action,
      properties: { ...p.properties },
    })),
    transition_from_last: scene.transition_from_last,
  };
}

function cancelEditScene() {
  editingSceneId.value = null;
  sceneDraft.value = {};
}

async function saveSceneEdit(scene: OutlineScene) {
  if (!outline.value) return;
  try {
    const updated = await api.patch<OutlineScene>(
      `/simulations/${simId.value}/outline/scenes/${scene.id}`,
      sceneDraft.value,
    );
    // 写回 outline.scenes
    const idx = outline.value.scenes.findIndex((s) => s.id === updated.id);
    if (idx >= 0) {
      outline.value.scenes[idx] = updated;
    }
    editingSceneId.value = null;
    sceneDraft.value = {};
    toast.success("已保存本幕修改");
  } catch (e) {
    toast.error(
      e instanceof ApiError ? `保存失败:${e.message}` : "保存失败",
    );
  }
}

// 添加 / 删除 key_events
function addKeyEvent() {
  if (!sceneDraft.value.key_events) sceneDraft.value.key_events = [];
  if (sceneDraft.value.key_events.length >= 8) {
    toast.warning("一幕最多 8 个 key_events");
    return;
  }
  sceneDraft.value.key_events.push("");
}
function removeKeyEvent(idx: number) {
  if (sceneDraft.value.key_events) {
    sceneDraft.value.key_events.splice(idx, 1);
  }
}

// 添加 / 删除 key_props
function addKeyProp() {
  if (!sceneDraft.value.key_props) sceneDraft.value.key_props = [];
  if (sceneDraft.value.key_props.length >= 6) {
    toast.warning("一幕最多 6 个 key_props");
    return;
  }
  sceneDraft.value.key_props.push({
    name: "",
    action: "introduced",
    properties: {},
  });
}
function removeKeyProp(idx: number) {
  if (sceneDraft.value.key_props) {
    sceneDraft.value.key_props.splice(idx, 1);
  }
}
function addPropProperty(prop: OutlineSceneKeyProp) {
  const key = `属性${Object.keys(prop.properties).length + 1}`;
  prop.properties[key] = "";
}
function removePropProperty(prop: OutlineSceneKeyProp, key: string) {
  delete prop.properties[key];
}

// ============================================================
// 编辑全局
// ============================================================

const globalDraft = ref<UpdateOutlineGlobalRequest>({});

function startEditGlobal() {
  if (!outline.value) return;
  editingGlobal.value = true;
  globalDraft.value = {
    global_theme: outline.value.global_theme,
    global_arc: outline.value.global_arc,
  };
}
function cancelEditGlobal() {
  editingGlobal.value = false;
  globalDraft.value = {};
}
async function saveGlobalEdit() {
  if (!outline.value) return;
  try {
    const updated = await api.patch<SimulationOutline>(
      `/simulations/${simId.value}/outline/global`,
      globalDraft.value,
    );
    outline.value = updated;
    editingGlobal.value = false;
    globalDraft.value = {};
    toast.success("已保存整篇主题 / 走向");
  } catch (e) {
    toast.error(
      e instanceof ApiError ? `保存失败:${e.message}` : "保存失败",
    );
  }
}

// ============================================================
// 继续生成 outline(M6-fix3 — 从断点接力)
// ============================================================

const continuing = ref(false);

async function continueOutlineGen() {
  if (!outline.value) return;
  continuing.value = true;
  try {
    const data = await api.post<SimulationOutline>(
      `/simulations/${simId.value}/outline/continue`,
      {},
    );
    outline.value = data;
    const totalScenes = data.scenes.length;
    const target = data.total_scenes_planned;
    if (totalScenes >= target) {
      toast.success(`✓ 已补齐完整 ${target} 幕`, 4000);
    } else {
      toast.info(
        `已续生成 → ${totalScenes}/${target} 幕,可再点"继续生成"补齐剩余`,
        4500,
      );
    }
  } catch (e) {
    toast.error(
      e instanceof ApiError ? `续生成失败:${e.message}` : "续生成失败",
    );
  } finally {
    continuing.value = false;
  }
}

// 是否需要"继续生成"按钮(scenes 少于 total)
const isOutlineIncomplete = computed(
  () => outline.value !== null
    && outline.value.scenes.length < outline.value.total_scenes_planned
    && outline.value.state === "awaiting_user",
);

// ============================================================
// 重新生成 outline
// ============================================================

async function regenerateOutline() {
  // M8.D(2026-05-21):若当前场景多样性警告 → 自动传 strengthen_diversity 让 LLM 多自创场景
  const shouldStrengthen = diversityChip.value?.tone === "warn";
  const message = shouldStrengthen
    ? "当前场景过于单一;重新生成时会自动「强化多样性」让 LLM 多自创新场景。"
    : "当前 outline 内容会被丢弃,LLM 重新生成一版新的(成本同 outline 一次调用)";
  const ok = await confirmDialog({
    title: "重新生成 outline?",
    message,
    confirmLabel: "重新生成",
    danger: false,
  });
  if (!ok) return;
  loading.value = true;
  try {
    const body: Record<string, unknown> = {};
    if (shouldStrengthen) {
      body.strengthen_diversity = true;
    }
    const data = await api.post<SimulationOutline>(
      `/simulations/${simId.value}/outline/regenerate`,
      body,
    );
    outline.value = data;
    if (data.state === "drafting") {
      startPollingIfDrafting();
    }
    toast.success(
      shouldStrengthen
        ? "已开始重新生成(强化多样性),请稍候"
        : "已开始重新生成 outline,请稍候",
    );
  } catch (e) {
    toast.error(
      e instanceof ApiError ? `重新生成失败:${e.message}` : "重新生成失败",
    );
  } finally {
    loading.value = false;
  }
}

// ============================================================
// 批准启动
// ============================================================

async function approveAndLaunch() {
  if (!outline.value) return;
  if (outline.value.state !== "awaiting_user") {
    toast.warning("当前状态不允许批准");
    return;
  }
  const ok = await confirmDialog({
    title: "批准 outline 并启动生成?",
    message:
      `共 ${outline.value.total_scenes_planned} 幕,按图纸严格生成。\n` +
      "启动后 outline 不可再改,运行中可在项目「作品列表」查看进度。",
    confirmLabel: "批准 + 启动",
    danger: false,
  });
  if (!ok) return;
  approving.value = true;
  try {
    await api.post(
      `/simulations/${simId.value}/outline/approve`,
      { confirm: true },
    );
    toast.success("已批准 + 启动生成,正在跳转…");
    // 跳详情页看进度
    router.push(`/simulations/${simId.value}`);
  } catch (e) {
    approving.value = false;
    toast.error(
      e instanceof ApiError ? `批准失败:${e.message}` : "批准失败",
    );
  }
}

// ============================================================
// 工具方法
// ============================================================

function goBack() {
  router.back();
}

const stateLabel = computed(() => {
  if (!outline.value) return "";
  switch (outline.value.state) {
    case "drafting":
      return "AI 正在生成 outline…";
    case "awaiting_user":
      return "请审核 / 编辑 outline";
    case "approved":
      return "已批准,准备启动";
    case "generating":
      return "正在按 outline 跑";
    case "done":
      return "已完成";
    case "failed":
      return "生成失败";
  }
  return "";
});

const canEdit = computed(
  () => outline.value?.state === "awaiting_user",
);
const canApprove = computed(
  () => outline.value?.state === "awaiting_user" && !approving.value,
);
</script>

<template>
  <main class="outline-view">
    <header class="outline-header surface">
      <button class="back-btn" @click="goBack" type="button">
        ← 返回
      </button>
      <h1 class="page-title">长篇 outline 审核</h1>
      <span class="state-chip" :class="`state-${outline?.state ?? 'unknown'}`">
        {{ stateLabel }}
      </span>
    </header>

    <!-- loading 骨架 -->
    <div v-if="loading" class="loading-block">
      <p>正在加载 outline…</p>
    </div>

    <!-- error -->
    <div v-else-if="errorMessage" class="error-block surface">
      <h3>❌ 加载失败</h3>
      <p>{{ errorMessage }}</p>
      <button class="primary-btn" @click="fetchOutline">重试</button>
    </div>

    <!-- drafting:工作流视图(防"创建中"卡死焦虑感) -->
    <div
      v-else-if="outline?.state === 'drafting' || (loading && !outline)"
      class="drafting-block surface"
    >
      <div class="spinner" />
      <p class="drafting-title">AI 正在生成 outline…</p>
      <p class="drafting-hint">
        预计 20-60 秒。outline 一次性确定整篇剧情骨架,包含每幕的物理位置 / 关键事件 /
        关键道具属性等全局状态,奠定剧情连贯性的基础。
      </p>

      <!-- 工作流步骤视图 -->
      <ol class="workflow-steps">
        <li class="step step--done">
          <span class="step-icon">✓</span>
          <div class="step-content">
            <p class="step-title">创建推演</p>
            <p class="step-meta">已落库 sim row + outline 占位</p>
          </div>
        </li>
        <li class="step step--active">
          <span class="step-icon">
            <span class="step-spinner-dot"></span>
          </span>
          <div class="step-content">
            <p class="step-title">LLM 生成全篇 outline 草稿</p>
            <p class="step-meta">
              主题 + 起承转合 + N 幕图纸 · 通常 20-60s · 自动轮询不需手动刷新
            </p>
          </div>
        </li>
        <li class="step step--pending">
          <span class="step-icon">·</span>
          <div class="step-content">
            <p class="step-title">用户审核 / 编辑(下一步)</p>
            <p class="step-meta">改某幕 / 加幕 / 删幕 / 重新生成 / 批准启动</p>
          </div>
        </li>
        <li class="step step--pending">
          <span class="step-icon">·</span>
          <div class="step-content">
            <p class="step-title">按 outline 逐幕生成产物</p>
            <p class="step-meta">5-15 min · 跳详情页看 SSE 进度</p>
          </div>
        </li>
      </ol>
    </div>

    <!-- failed:失败 -->
    <div
      v-else-if="outline?.state === 'failed'"
      class="failed-block surface"
    >
      <h3>❌ 大纲生成失败</h3>
      <p class="failed-message">{{ outline.error_message || "生成中断,请重试" }}</p>
      <button class="primary-btn" @click="regenerateOutline">
        重新生成
      </button>
    </div>

    <!-- awaiting_user / approved / generating / done:正常审核界面 -->
    <template v-else-if="outline">
      <!-- M6-fix1:partial 截断 warning(awaiting_user 时 error_message 非空 = LLM 被截断) -->
      <section
        v-if="outline.state === 'awaiting_user' && outline.error_message"
        class="partial-warning surface"
      >
        <p>{{ outline.error_message }}</p>
      </section>

      <!-- 全局主题 / 走向 -->
      <section class="global-section surface">
        <header class="section-header">
          <h2>整篇主题与走向</h2>
          <button
            v-if="canEdit && !editingGlobal"
            class="ghost-btn"
            @click="startEditGlobal"
            type="button"
          >
            编辑
          </button>
        </header>

        <div v-if="!editingGlobal" class="global-readonly">
          <p class="global-row">
            <strong>主题:</strong>{{ outline.global_theme || "(空)" }}
          </p>
          <p class="global-row">
            <strong>起承转合:</strong>{{ outline.global_arc || "(空)" }}
          </p>
          <p class="global-meta">
            共 {{ outline.total_scenes_planned }} 幕 ·
            创建于 {{ outline.created_at.slice(0, 19).replace("T", " ") }}
          </p>
        </div>

        <div v-else class="global-editor">
          <label class="editor-label">
            主题 (< 60 字)
            <input
              v-model="globalDraft.global_theme"
              type="text"
              maxlength="60"
              class="text-input"
            />
          </label>
          <label class="editor-label">
            起承转合走向 (< 300 字)
            <textarea
              v-model="globalDraft.global_arc"
              maxlength="300"
              rows="4"
              class="text-input textarea"
            />
          </label>
          <div class="editor-actions">
            <button class="ghost-btn" @click="cancelEditGlobal" type="button">
              取消
            </button>
            <button class="primary-btn" @click="saveGlobalEdit" type="button">
              保存
            </button>
          </div>
        </div>
      </section>

      <!-- 操作行 -->
      <section class="actions-bar">
        <!-- M6-fix3:继续生成(只在 outline 不完整 + awaiting_user 时显)-->
        <button
          v-if="isOutlineIncomplete"
          class="primary-btn primary-btn--continue"
          @click="continueOutlineGen"
          :disabled="continuing"
          type="button"
          :title="`已生成 ${outline?.scenes.length}/${outline?.total_scenes_planned} 幕,从断点接力补齐`"
        >
          {{ continuing
            ? "续生成中…"
            : `▶ 继续生成 (${outline?.scenes.length}/${outline?.total_scenes_planned})` }}
        </button>
        <button
          class="ghost-btn"
          @click="regenerateOutline"
          :disabled="!canEdit"
          type="button"
        >
          🔄 重新生成 outline
        </button>
        <button
          class="primary-btn primary-btn--approve"
          @click="approveAndLaunch"
          :disabled="!canApprove"
          type="button"
        >
          {{ approving ? "启动中…" : "✓ 批准 + 启动生成" }}
        </button>
      </section>

      <!-- 每幕列表 -->
      <section class="scenes-section">
        <h2 class="scenes-title">
          每幕图纸 ({{ outline.scenes.length }} 幕)
          <!-- M8.D(2026-05-21)场景多样性健康度 chip -->
          <span
            v-if="diversityChip"
            class="diversity-chip"
            :class="`diversity-chip--${diversityChip.tone}`"
            :title="diversityChip.tooltip"
          >{{ diversityChip.text }}</span>
          <span class="scenes-hint">
            点击展开编辑;<span v-if="canEdit">拖拽 ⠿ 句柄重排,可加 / 删幕</span>
          </span>
        </h2>

        <!-- SP-6.2(2026-05-28):升级到 TensionCurvePanel(三幕分区背景 + 张力折线)
             换掉旧的 inline sparkline:加 act1/act2/act3 背景色 + hover 显第 N 幕张力 -->
        <TensionCurvePanel
          v-if="outline.scenes.length >= 2"
          :scenes="outline.scenes"
          class="outline-tension"
        />

        <!-- M8.B(2026-05-21):在每幕之前的插入条(点击在该位置加新幕)-->
        <template v-for="(scene, sceneIdx) in outline.scenes" :key="scene.id">
          <!-- 上方插入条(仅 canEdit 时显示;hover 显示按钮) -->
          <div
            v-if="canEdit"
            class="insert-divider"
            :class="{ 'insert-divider--disabled': operating }"
          >
            <button
              type="button"
              class="insert-btn"
              :disabled="operating"
              :title="`在第 ${sceneIdx + 1} 幕之前插入新幕`"
              @click="handleInsertScene(sceneIdx)"
            >+ 在此插入新幕</button>
          </div>

        <article
          class="scene-card surface"
          :class="{
            'scene-card--editing': editingSceneId === scene.id,
            'scene-card--edited': scene.user_edited,
            'scene-card--dragging': draggedSceneId === scene.id,
            'scene-card--drag-over': dragOverSceneId === scene.id,
          }"
          :draggable="canEdit && !operating && editingSceneId !== scene.id"
          @dragstart="onDragStart(scene, $event)"
          @dragover="onDragOver(scene, $event)"
          @dragleave="onDragLeave(scene)"
          @drop="onDrop(scene, $event)"
          @dragend="onDragEnd"
        >
          <!-- 折叠态:概要 -->
          <header v-if="editingSceneId !== scene.id" class="scene-header">
            <div class="scene-index-block">
              <!-- M8.B 拖拽 handle(纯视觉,真实拖拽由整个 article 承担)-->
              <span
                v-if="canEdit"
                class="drag-handle"
                title="拖拽此幕到其他位置重排"
                aria-hidden="true"
              >⠿</span>
              <span class="scene-num">第 {{ scene.scene_index + 1 }} 幕</span>
              <span class="scene-loc">📍 {{ scene.location }}</span>
              <span v-if="scene.time_anchor" class="scene-time">
                · ⏱ {{ scene.time_anchor }}
              </span>
              <span v-if="scene.user_edited" class="edited-mark">✎ 已修改</span>
            </div>
            <div class="scene-header-actions">
              <!-- M8.B 删幕按钮(canEdit + 多于 1 幕时) -->
              <button
                v-if="canEdit && outline.scenes.length > 1"
                class="ghost-btn ghost-btn--small ghost-btn--danger"
                :disabled="operating"
                title="删除此幕"
                type="button"
                @click="handleDeleteScene(scene)"
              >× 删幕</button>
              <button
                v-if="canEdit"
                class="ghost-btn ghost-btn--small"
                @click="startEditScene(scene)"
                type="button"
            >
              编辑
            </button>
            </div>
          </header>

          <div v-if="editingSceneId !== scene.id" class="scene-body">
            <p class="scene-summary">{{ scene.scene_summary }}</p>

            <div class="scene-meta">
              <span class="meta-pill">作用:{{ scene.scene_purpose }}</span>
              <!-- MP(2026-05-21) planner 张力 chip -->
              <span
                v-if="scene.tension_percent != null"
                class="meta-pill tension-chip"
                :class="tensionChipClass(scene.tension_percent)"
                :title="tensionTooltip(scene)"
              >
                张力 {{ scene.tension_percent }}%
                <span v-if="scene.pacing_tempo">
                  · {{ pacingLabel(scene.pacing_tempo) }}
                </span>
              </span>
              <span
                v-for="(c, i) in scene.characters_present"
                :key="i"
                class="meta-pill meta-pill--char"
              >
                {{ c }}
              </span>
            </div>

            <details v-if="scene.key_events.length" class="details-block">
              <summary>📋 关键事件 ({{ scene.key_events.length }})</summary>
              <ol class="key-events-list">
                <li v-for="(e, i) in scene.key_events" :key="i">{{ e }}</li>
              </ol>
            </details>

            <details v-if="scene.key_props.length" class="details-block">
              <summary>🎁 关键道具 ({{ scene.key_props.length }})</summary>
              <ul class="key-props-list">
                <li v-for="(p, i) in scene.key_props" :key="i" class="prop-item">
                  <strong>{{ p.name }}</strong>
                  <span class="prop-action">({{ p.action }})</span>
                  <ul v-if="Object.keys(p.properties).length" class="prop-properties">
                    <li v-for="(v, k) in p.properties" :key="k">
                      <code>{{ k }}</code> = {{ v }}
                    </li>
                  </ul>
                </li>
              </ul>
            </details>

            <p v-if="scene.transition_from_last" class="scene-transition">
              ↪ 上幕连接:{{ scene.transition_from_last }}
            </p>
          </div>

          <!-- 编辑态:全字段表单 -->
          <div v-else class="scene-editor">
            <label class="editor-label">
              本幕概要 (1-2 句话)
              <textarea
                v-model="sceneDraft.scene_summary"
                rows="3"
                maxlength="500"
                class="text-input textarea"
              />
            </label>

            <div class="editor-row">
              <label class="editor-label editor-label--half">
                物理位置
                <input
                  v-model="sceneDraft.location"
                  type="text"
                  maxlength="50"
                  class="text-input"
                  list="outline-location-suggestions"
                  placeholder="可选已用场景或自由输入新场景(自创会入库共享)"
                />
                <!-- M8.A:datalist 仅显示 outline 内已用 unique locations,
                     鼓励复用 + 自由输入新场景(后端 sequel_scene_sync 自动入库) -->
                <datalist id="outline-location-suggestions">
                  <option
                    v-for="loc in locationSuggestions"
                    :key="loc"
                    :value="loc"
                  />
                </datalist>
              </label>
              <label class="editor-label editor-label--half">
                时间锚
                <input
                  v-model="sceneDraft.time_anchor"
                  type="text"
                  maxlength="50"
                  class="text-input"
                  placeholder="如 次日下午 / 深夜"
                />
              </label>
            </div>

            <label class="editor-label">
              本幕作用
              <input
                v-model="sceneDraft.scene_purpose"
                type="text"
                maxlength="50"
                class="text-input"
                placeholder="推进主线 / 引入伏笔 / 情绪转折"
              />
            </label>

            <!-- 关键事件 -->
            <div class="editor-label">
              <div class="list-header">
                <span>📋 关键事件(narrator 必完整覆盖)</span>
                <button
                  class="ghost-btn ghost-btn--small"
                  @click="addKeyEvent"
                  type="button"
                >
                  + 添加
                </button>
              </div>
              <div
                v-for="(_, idx) in sceneDraft.key_events"
                :key="idx"
                class="event-row"
              >
                <input
                  v-model="sceneDraft.key_events![idx]"
                  type="text"
                  maxlength="300"
                  class="text-input"
                  :placeholder="`事件 ${idx + 1}`"
                />
                <button
                  class="remove-btn"
                  @click="removeKeyEvent(idx)"
                  type="button"
                  title="删除"
                >
                  ×
                </button>
              </div>
            </div>

            <!-- 关键道具 -->
            <div class="editor-label">
              <div class="list-header">
                <span>🎁 关键道具(属性一旦定下,后续幕严禁覆盖)</span>
                <button
                  class="ghost-btn ghost-btn--small"
                  @click="addKeyProp"
                  type="button"
                >
                  + 添加
                </button>
              </div>
              <div
                v-for="(prop, idx) in sceneDraft.key_props"
                :key="idx"
                class="prop-edit-card"
              >
                <div class="prop-row">
                  <input
                    v-model="prop.name"
                    type="text"
                    maxlength="50"
                    class="text-input"
                    placeholder="道具名"
                  />
                  <input
                    v-model="prop.action"
                    type="text"
                    maxlength="30"
                    class="text-input"
                    placeholder="action(introduced / referenced)"
                  />
                  <button
                    class="remove-btn"
                    @click="removeKeyProp(idx)"
                    type="button"
                  >
                    ×
                  </button>
                </div>
                <div class="prop-properties-edit">
                  <p class="prop-properties-label">属性 (key=value):</p>
                  <div
                    v-for="(_v, k) in prop.properties"
                    :key="k"
                    class="prop-property-row"
                  >
                    <input
                      :value="k"
                      readonly
                      class="text-input prop-key-input"
                    />
                    <input
                      v-model="prop.properties[k]"
                      type="text"
                      maxlength="200"
                      class="text-input"
                    />
                    <button
                      class="remove-btn"
                      @click="removePropProperty(prop, String(k))"
                      type="button"
                    >
                      ×
                    </button>
                  </div>
                  <button
                    class="ghost-btn ghost-btn--small"
                    @click="addPropProperty(prop)"
                    type="button"
                  >
                    + 加属性
                  </button>
                </div>
              </div>
            </div>

            <label class="editor-label">
              ↪ 与上幕的物理 / 时间连接
              <textarea
                v-model="sceneDraft.transition_from_last"
                rows="2"
                maxlength="300"
                class="text-input textarea"
                placeholder="例:张凡从玄关穿过走廊到主卧 / 半小时后镜头切回教室"
              />
            </label>

            <div class="editor-actions">
              <button
                class="ghost-btn"
                @click="cancelEditScene"
                type="button"
              >
                取消
              </button>
              <button
                class="primary-btn"
                @click="saveSceneEdit(scene)"
                type="button"
              >
                保存
              </button>
            </div>
          </div>
        </article>
        </template>

        <!-- M8.B(2026-05-21):末尾追加按钮(在最后一幕之后)-->
        <div v-if="canEdit && outline.scenes.length > 0" class="insert-divider insert-divider--tail">
          <button
            type="button"
            class="insert-btn insert-btn--tail"
            :disabled="operating"
            title="在末尾追加新幕"
            @click="handleInsertScene(outline.scenes.length)"
          >+ 在末尾追加新幕</button>
        </div>
      </section>
    </template>
  </main>
</template>

<style scoped>
.outline-view {
  max-width: 920px;
  margin: 0 auto;
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.outline-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
}

.back-btn {
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: 4px 10px;
  cursor: pointer;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}
.back-btn:hover {
  background: var(--color-bg-subtle);
}

.page-title {
  flex: 1;
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}

.state-chip {
  padding: 4px 10px;
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  font-weight: 500;
}
.state-drafting { background: var(--color-accent-soft); color: var(--color-accent-text); }
.state-awaiting_user { background: rgba(245, 158, 11, 0.12); color: rgb(180, 83, 9); }
.state-approved, .state-generating { background: rgba(59, 130, 246, 0.12); color: rgb(37, 99, 235); }
.state-done { background: rgba(16, 185, 129, 0.12); color: rgb(5, 122, 85); }
.state-failed { background: rgba(220, 38, 38, 0.12); color: rgb(185, 28, 28); }

.loading-block, .drafting-block, .failed-block, .error-block {
  padding: var(--space-6);
  text-align: center;
  border-radius: var(--radius-lg);
}

.spinner {
  width: 36px;
  height: 36px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  margin: 0 auto var(--space-3);
  animation: spin var(--duration-spin) linear infinite;
}

.drafting-title {
  font-size: var(--text-base);
  font-weight: 500;
  color: var(--color-text);
  margin: 0 0 var(--space-2);
}
.drafting-hint {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: 1.6;
  max-width: 540px;
  margin: 0 auto var(--space-4);
}

/* M6-fix2:工作流步骤视图 — 避免用户认为"卡死" */
.workflow-steps {
  list-style: none;
  padding: 0;
  margin: var(--space-4) auto 0;
  max-width: 500px;
  text-align: left;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.step {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
}
.step--done .step-icon {
  background: rgb(16, 185, 129);
  color: white;
}
.step--active {
  background: var(--color-accent-soft);
}
.step--active .step-icon {
  background: var(--color-accent);
  color: white;
}
.step--pending .step-icon {
  background: var(--color-bg-subtle);
  color: var(--color-text-subtle);
  border: 1px solid var(--color-border);
}
.step-icon {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-sm);
  font-weight: 600;
  flex-shrink: 0;
  position: relative;
}
.step-spinner-dot {
  width: 10px;
  height: 10px;
  background: white;
  border-radius: 50%;
  animation: pulse 1s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; transform: scale(1); }
  50% { opacity: 0.4; transform: scale(0.7); }
}
.step-content {
  flex: 1;
  text-align: left;
}
.step-title {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
  margin: 0 0 2px;
}
.step-meta {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin: 0;
  line-height: 1.5;
}

.partial-warning {
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  background: rgba(245, 158, 11, 0.10);
  border: 1px solid rgba(245, 158, 11, 0.30);
}
.partial-warning p {
  margin: 0;
  font-size: var(--text-sm);
  color: rgb(180, 83, 9);
  line-height: 1.6;
}

.failed-block h3 { color: rgb(185, 28, 28); margin-bottom: var(--space-2); }
.failed-message {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin-bottom: var(--space-3);
  font-family: var(--font-mono);
}

/* 全局 section */
.global-section {
  padding: var(--space-4);
  border-radius: var(--radius-lg);
}
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: var(--space-2);
}
.section-header h2 {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}

.global-row {
  font-size: var(--text-sm);
  color: var(--color-text);
  line-height: 1.6;
  margin: 0 0 var(--space-2);
}
.global-row strong {
  color: var(--color-accent-text);
  margin-right: 6px;
}
.global-meta {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  margin: 0;
}

.global-editor, .scene-editor {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* 操作行 */
.actions-bar {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

/* scenes */
.scenes-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.scenes-title {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
}
.scenes-hint {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-weight: 400;
}

/* M8.D(2026-05-21)场景多样性健康度 chip */
.diversity-chip {
  flex-shrink: 0;
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  font-weight: 500;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  cursor: help;
}
.diversity-chip--ok {
  color: #15803D;
  background: rgba(22, 163, 74, 0.12);
  border: 1px solid rgba(22, 163, 74, 0.3);
}
.diversity-chip--neutral {
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
}
.diversity-chip--warn {
  color: #B45309;
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.4);
}

/* MP(2026-05-21)planner 张力 chip + sparkline */
.tension-chip {
  font-weight: 500;
  letter-spacing: 0.02em;
}
.tension-chip--low {
  color: #1e40af;
  background: rgba(59, 130, 246, 0.10);
  border: 1px solid rgba(59, 130, 246, 0.35);
}
.tension-chip--mid {
  color: #854d0e;
  background: rgba(234, 179, 8, 0.10);
  border: 1px solid rgba(234, 179, 8, 0.35);
}
.tension-chip--high {
  color: #991b1b;
  background: rgba(239, 68, 68, 0.10);
  border: 1px solid rgba(239, 68, 68, 0.35);
}

.tension-curve-box {
  margin: var(--space-2) 0 var(--space-4);
  padding: var(--space-2) var(--space-3);
  background: rgba(139, 92, 246, 0.04);
  border: 1px solid rgba(139, 92, 246, 0.15);
  border-radius: var(--radius-md);
}
.tension-curve-label {
  font-size: 0.75rem;
  color: var(--color-text-secondary);
  margin-bottom: var(--space-1);
  letter-spacing: 0.05em;
}
.tension-sparkline {
  width: 100%;
  height: 60px;
  display: block;
}
.tension-curve-axis {
  display: flex;
  justify-content: space-between;
  font-size: 0.7rem;
  color: var(--color-text-tertiary);
  margin-top: 2px;
}

.scene-card {
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  transition: opacity var(--duration-fast) var(--ease-out),
              transform var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out),
              background var(--duration-fast) var(--ease-out);
}
.scene-card--editing {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}
.scene-card--edited {
  border-color: rgba(245, 158, 11, 0.55);
  background: rgba(245, 158, 11, 0.08);
}

/* M8.B 拖拽中:source 卡片半透明,target 卡片高亮 */
.scene-card--dragging {
  opacity: 0.4;
}
.scene-card--drag-over {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.18);
  transform: scale(1.01);
}

/* M8.B 拖拽 handle 视觉 */
.drag-handle {
  display: inline-block;
  margin-right: var(--space-2);
  font-size: var(--text-base);
  color: var(--color-text-subtle);
  cursor: grab;
  user-select: none;
}
.scene-card[draggable="true"]:hover .drag-handle {
  color: var(--color-accent-text);
}
.scene-card[draggable="true"] {
  cursor: grab;
}
.scene-card[draggable="true"]:active {
  cursor: grabbing;
}

/* M8.B header 右侧按钮组 */
.scene-header-actions {
  display: flex;
  gap: var(--space-2);
}
.ghost-btn--danger {
  color: var(--color-danger);
  border-color: var(--color-danger-soft);
}
.ghost-btn--danger:hover:not(:disabled) {
  background: var(--color-danger-soft);
  color: var(--color-danger);
}

/* M8.B 插入分隔条 — 默认隐形,hover 显示按钮 */
.insert-divider {
  position: relative;
  height: 8px;
  margin: 2px 0;
  display: flex;
  align-items: center;
  justify-content: center;
}
.insert-divider::before {
  content: "";
  position: absolute;
  left: 0;
  right: 0;
  height: 1px;
  background: transparent;
  transition: background var(--duration-fast) var(--ease-out);
}
.insert-divider:hover::before {
  background: var(--color-accent-border);
}
.insert-btn {
  opacity: 0;
  position: relative;
  z-index: 1;
  padding: 2px var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-surface);
  border: 1px dashed var(--color-accent-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: opacity var(--duration-fast) var(--ease-out),
              background var(--duration-fast) var(--ease-out);
}
.insert-divider:hover .insert-btn,
.insert-btn:focus-visible {
  opacity: 1;
}
.insert-btn:hover:not(:disabled) {
  background: var(--color-accent-soft);
}
.insert-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
/* 末尾追加 — 始终可见(无 hover 隐藏) */
.insert-divider--tail {
  height: 36px;
  margin-top: var(--space-3);
}
.insert-btn--tail {
  opacity: 1;
  padding: 6px var(--space-4);
  font-size: var(--text-sm);
}
.insert-divider--disabled {
  pointer-events: none;
}

.scene-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.scene-index-block {
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  flex-wrap: wrap;
}
.scene-num {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-accent-text);
}
.scene-loc {
  font-size: var(--text-sm);
  color: var(--color-text);
}
.scene-time {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.edited-mark {
  font-size: 11px;
  color: rgb(180, 83, 9);
  background: rgba(245, 158, 11, 0.12);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
}

.scene-body {
  margin-top: var(--space-2);
}
.scene-summary {
  font-size: var(--text-sm);
  color: var(--color-text);
  line-height: 1.6;
  margin: 0 0 var(--space-2);
}
.scene-meta {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-bottom: var(--space-2);
}
.meta-pill {
  padding: 2px 8px;
  font-size: 11px;
  border-radius: var(--radius-sm);
  background: var(--color-bg-subtle);
  color: var(--color-text-muted);
}
.meta-pill--char {
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
}

.details-block {
  margin-bottom: var(--space-2);
  font-size: var(--text-sm);
}
.details-block summary {
  cursor: pointer;
  color: var(--color-text-muted);
  font-weight: 500;
}
.key-events-list {
  padding-left: var(--space-4);
  margin: var(--space-2) 0;
  line-height: 1.6;
}
.key-props-list {
  padding-left: var(--space-2);
  margin: var(--space-2) 0;
  list-style: none;
}
.prop-item {
  margin-bottom: var(--space-2);
}
.prop-action {
  font-size: 11px;
  color: var(--color-text-subtle);
  margin-left: 6px;
}
.prop-properties {
  padding-left: var(--space-3);
  margin-top: 4px;
  font-size: var(--text-xs);
}
.prop-properties code {
  color: var(--color-accent-text);
  font-family: var(--font-mono);
}

.scene-transition {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-style: italic;
  margin: var(--space-2) 0 0;
}

/* Editor */
.editor-label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  font-weight: 500;
}
.editor-row {
  display: flex;
  gap: var(--space-2);
}
.editor-label--half {
  flex: 1;
}

.text-input {
  padding: 8px 12px;
  font-size: var(--text-sm);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  outline: none;
  font-family: inherit;
  transition: border-color 0.15s, box-shadow 0.15s;
}
.text-input.textarea {
  resize: vertical;
  min-height: 60px;
  line-height: 1.5;
}
.text-input:focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.12);
}

.list-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 4px;
}

.event-row, .prop-row, .prop-property-row {
  display: flex;
  gap: 6px;
  margin-bottom: 4px;
}
.event-row .text-input {
  flex: 1;
}
.prop-row .text-input { flex: 1; }
.prop-property-row .text-input { flex: 1; }
.prop-key-input {
  background: var(--color-bg-subtle);
  font-family: var(--font-mono);
  font-size: 11px;
  max-width: 80px;
}

.remove-btn {
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  padding: 2px 8px;
  cursor: pointer;
  color: var(--color-text-subtle);
  font-size: var(--text-sm);
}
.remove-btn:hover {
  background: rgba(220, 38, 38, 0.08);
  color: rgb(185, 28, 28);
}

.prop-edit-card {
  padding: var(--space-2);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  margin-bottom: 4px;
}
.prop-properties-edit {
  padding-top: 4px;
}
.prop-properties-label {
  font-size: 11px;
  color: var(--color-text-subtle);
  margin: 0 0 4px;
}

.editor-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}

/* Buttons */
.ghost-btn {
  padding: 6px 14px;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  font-family: inherit;
}
.ghost-btn--small {
  padding: 3px 10px;
  font-size: 11px;
}
.ghost-btn:hover { background: var(--color-bg-subtle); }
.ghost-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.primary-btn {
  padding: 6px 18px;
  font-size: var(--text-sm);
  color: white;
  background: var(--color-accent);
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  font-family: inherit;
  font-weight: 500;
}
.primary-btn:hover { background: var(--color-accent-strong); }
.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.primary-btn--approve {
  padding: 6px 22px;
  background: rgb(16, 185, 129);
}
.primary-btn--approve:hover { background: rgb(5, 150, 105); }

/* M6-fix3:继续生成按钮(橙色,介于警告和批准之间) */
.primary-btn--continue {
  padding: 6px 18px;
  background: rgb(245, 158, 11);
}
.primary-btn--continue:hover { background: rgb(217, 119, 6); }
</style>
