<script setup lang="ts">
/**
 * CounterfactualWorkbench — 反事实工作台(Sprint 2.C+)。
 *
 * 用户的"动刀"中心 — 三 tab 集中管理反事实变量:
 *   ① 角色反事实 — 列出项目所有 PERSON,inline 改 5 字段(name / identity / personality / quotes / no_go_list)
 *   ② 事件反事实 — 列出项目所有 EVENT,inline 改 description / outcome
 *   ③ 世界观反事实 ⭐ — 6 个维度结构化:genre / setting / magic_system / time_axis / tone / free_form
 *
 * 每条反事实都强烈推荐填 user_intent(自然语言意图)— LLM 编排时优先级最高。
 *
 * 入口:
 *   - 顶栏「反事实工作台」按钮
 *   - SimulationDock「✎ 编辑反事实」按钮
 *   - 3D 图谱节点右键(后续 sprint)
 *
 * Props:
 *   open                 父控制开关
 *   composable           父传 useCounterfactuals 实例(共享状态)
 *   characters           项目所有角色(用于角色 tab 列表)
 *   events               项目所有事件
 *
 * Emits:
 *   close                用户关闭
 *   changed              创建 / 撤销反事实成功 — 父可据此 reload 3D 图谱(让字段还原反映)
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api } from "../api/client";
import {
  ApiError,
  type CounterfactualChange,
  type Character,
  type Project,
  type ProjectEvent,
  type WorldCounterfactualField,
  WORLD_FIELD_META,
} from "../api/types";
import { confirm as confirmDialog } from "../composables/useConfirm";
import { toast } from "../composables/useToast";
import type { useCounterfactuals } from "../composables/useCounterfactuals";

type CFComposable = ReturnType<typeof useCounterfactuals>;

/**
 * 自适应高度 textarea 指令 — 内容多少就长多少,不出滑动条。
 *
 * 触发点:
 *   - mounted:首次挂载,按当前 value 算 scrollHeight
 *   - updated:value 变化(异步填 baseline / 父更新)→ 重新算
 *   - input:用户敲键 → 实时长
 *
 * 算法:先 height='auto' 让 scrollHeight 反映实际内容,再赋 height=scrollHeight+'px'。
 */
function autosizeEl(el: HTMLTextAreaElement) {
  el.style.height = "auto";
  el.style.height = el.scrollHeight + "px";
}

const vAutosize = {
  mounted(el: HTMLTextAreaElement & { _autosizeFn?: () => void }) {
    autosizeEl(el);
    const handler = () => autosizeEl(el);
    el.addEventListener("input", handler);
    // 关掉手动 resize 把手 — 视觉更干净,容器高度由内容决定
    el.style.resize = "none";
    el.style.overflow = "hidden";
    // 2026-06-02 hotfix:存 handler 到 el,unmounted 时移除(治 listener 泄漏)
    el._autosizeFn = handler;
  },
  updated(el: HTMLTextAreaElement) {
    autosizeEl(el);
  },
  unmounted(el: HTMLTextAreaElement & { _autosizeFn?: () => void }) {
    if (el._autosizeFn) {
      el.removeEventListener("input", el._autosizeFn);
      delete el._autosizeFn;
    }
  },
};

const props = defineProps<{
  open: boolean;
  composable: CFComposable;
  characters: Character[];
  events: ProjectEvent[];
  /** 2.C+ polish: 当前项目(用于读 world_baseline 预填 + 显示项目名 + infer 端点 URL)
   *  null 时世界观 tab 显示无 baseline 状态(罕见 — 父组件应总传) */
  project: Project | null;
  /** 2026-06-06:跳"组合树"时附 query.returnTo,组合树"返回"按钮按此决定跳回哪
   *  'graph' = 从 3D 图谱页打开(返回时回图谱);未传 = 默认行为(返回项目作品列表) */
  returnTo?: "graph";
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "changed"): void;
  /** 2.C+ polish: AI 识别 baseline 后通知父刷新 project state */
  (e: "baseline-inferred", project: Project): void;
}>();

const router = useRouter();

type TabKey = "character" | "event" | "world";
const activeTab = ref<TabKey>("character");

// 使用指南折叠状态
const guideOpen = ref(false);

// Sprint 6.A2 CT(2026-05-21)— 组合树入口
// 2026-06-05 增强:智能跳转 — 项目有最新批次时,直接跳进度页;
//                  否则跳配置页(创建新批次)
async function openCombinationTree() {
  if (!props.project) return;
  emit("close");
  const projectId = props.project.id;
  // 2026-06-06:把"来源"附在 query.returnTo,组合树"返回项目"按钮按此决定跳回哪
  // 父组件传 returnTo='graph' 表示从 3D 图谱页打开,默认(undefined)是项目作品列表
  const queryWithReturn = props.returnTo
    ? { returnTo: props.returnTo }
    : undefined;
  // 查最新 batch,有就跳进度页;查询失败 / 没有就走配置页(老逻辑兜底)
  try {
    const resp = await api.get<{ combo_id: string | null }>(
      `/projects/${projectId}/counterfactual-combinations/latest`,
    );
    if (resp.combo_id) {
      router.push({
        name: "counterfactual-tree",
        params: { id: projectId, combo_id: resp.combo_id },
        query: queryWithReturn,
      });
      return;
    }
  } catch (_e) {
    // 查询失败不影响 — 静默回退到配置页
  }
  router.push({
    name: "counterfactual-tree",
    params: { id: projectId },
    query: queryWithReturn,
  });
}

// ============================================================
// 角色 tab — 5 个推演影响字段
// ============================================================

const CHARACTER_FIELDS: { key: string; label: string; type: "text" | "lines" }[] = [
  { key: "name", label: "名字", type: "text" },
  { key: "identity", label: "身份", type: "text" },
  { key: "personality", label: "性格", type: "text" },
  { key: "quotes", label: "台词(每行 1 条)", type: "lines" },
  { key: "no_go_list", label: "禁忌(每行 1 条)", type: "lines" },
];

/** 当前展开编辑的 character id(单选展开,简化 UX) */
const expandedCharId = ref<string | null>(null);

function toggleCharExpand(id: string) {
  expandedCharId.value = expandedCharId.value === id ? null : id;
}

/** 找该 character 某 field 当前 active 反事实 */
function findActiveForChar(charId: string, field: string): CounterfactualChange | null {
  for (const cf of props.composable.items.value) {
    if (
      cf.target_type === "character"
      && cf.target_id === charId
      && cf.field === field
    ) return cf;
  }
  return null;
}

/** 该 character 当前 active 反事实的总数(给 entity-head 绿色"已改 N"chip 用) */
function countActiveForChar(charId: string): number {
  let n = 0;
  for (const cf of props.composable.items.value) {
    if (cf.target_type === "character" && cf.target_id === charId) n++;
  }
  return n;
}

// ============================================================
// 事件 tab — description + outcome 字段
// ============================================================

// 事件 tab 只保留 description 一个字段:
//   "原 / 改"足以表达事件如何改写;再单独一个 outcome 字段会与"改"重复,
//   且 outcome 不在 events 表 + 后端撤销白名单里,会导致撤销失败。
//   蝴蝶效应 / 想要的走向 走 ★ 你的意图。
const EVENT_FIELDS: { key: string; label: string; type: "text" }[] = [
  { key: "description", label: "事件描述(原 / 改 自由表达)", type: "text" },
];

const expandedEventId = ref<string | null>(null);

function toggleEventExpand(id: string) {
  expandedEventId.value = expandedEventId.value === id ? null : id;
}

function findActiveForEvent(evtId: string, field: string): CounterfactualChange | null {
  for (const cf of props.composable.items.value) {
    if (
      cf.target_type === "event"
      && cf.target_id === evtId
      && cf.field === field
    ) return cf;
  }
  return null;
}

function countActiveForEvent(evtId: string): number {
  let n = 0;
  for (const cf of props.composable.items.value) {
    if (cf.target_type === "event" && cf.target_id === evtId) n++;
  }
  return n;
}

// ============================================================
// 世界观 tab — 6 个维度
// ============================================================

const WORLD_FIELDS: WorldCounterfactualField[] = [
  "genre", "setting", "magic_system", "time_axis", "tone", "free_form",
];

function findActiveWorld(field: WorldCounterfactualField): CounterfactualChange | null {
  for (const cf of props.composable.items.value) {
    if (cf.target_type === "world" && cf.field === field) return cf;
  }
  return null;
}

/** 2.C+ polish: 取项目 world_baseline 中该 field 的"原"预填值 */
function getWorldBaselineFor(field: WorldCounterfactualField): string {
  return props.project?.world_baseline?.[field] ?? "";
}

/** 2.C+ polish: 项目是否已识别过 baseline(任一字段非空 = 已识别) */
const hasWorldBaseline = computed(() => {
  const bl = props.project?.world_baseline;
  if (!bl) return false;
  return Object.values(bl).some((v) => v && v.trim().length > 0);
});

const inferringBaseline = ref(false);

async function handleInferBaseline() {
  if (!props.project) return;
  inferringBaseline.value = true;
  try {
    const updated = await api.post<Project>(
      `/projects/${props.project.id}/infer_world_baseline`,
    );
    toast.success("AI 识别完成 — 6 维度世界观已预填到「原」字段");

    // 用 backend 返的 updated.world_baseline **同步**写 editStates 的 oldValue
    // 不依赖 emit → 父 await load() → props 响应式更新 这条异步链
    // (异步链 race 输给 Vue 同步重渲染 → 字段不刷新的 bug 根因)
    //
    // ⭐ 2.C+ polish v2:**只填"原",不填"改"** —
    //   "改"字段保持空白让用户自己写,这是产品级的 UX 原则
    //   (避免预填让用户产生"AI 已经填好"的错觉,误以为不需要再改)
    const newBaseline = updated.world_baseline ?? {};
    for (const f of WORLD_FIELDS) {
      const k = editKey("world", "_global_", f);
      const oldValue = newBaseline[f] ?? "";
      const existing = editStates.value[k];
      if (!existing) {
        editStates.value[k] = {
          oldValue,
          newValue: "",        // 留白
          userIntent: "",
        };
      } else {
        // 已存在:只更新 oldValue,不动用户可能已经在写的 newValue / userIntent
        existing.oldValue = oldValue;
      }
    }

    emit("baseline-inferred", updated);
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.code === "PROJECT_HAS_NO_UPLOAD") {
        toast.warning("项目还没上传作品文件,无法识别世界观");
      } else if (e.code === "INFER_META_FAILED") {
        toast.error("AI 识别失败,请稍后重试");
      } else {
        toast.error(e.message || "操作失败,请稍后再试");
      }
    } else {
      toast.error("AI 识别失败,请稍后重试");
    }
  } finally {
    inferringBaseline.value = false;
  }
}

// ============================================================
// 编辑状态(三类共用,key 是 'type:targetId:field')
// ============================================================

interface EditState {
  oldValue: string;
  newValue: string;
  userIntent: string;
}

const editStates = ref<Record<string, EditState>>({});

function editKey(type: string, targetId: string, field: string): string {
  return `${type}:${targetId}:${field}`;
}

/** 拿当前编辑状态,若未初始化则用反事实(若有)或 originalValue/baseline 兜底
 *
 * 用户拍板原则(2.C+ polish v2):
 *   - "原"字段:用 originalValue / baseline 自动填(给用户看参照)
 *   - "改"字段:**留白**让用户自己写(避免预填让用户产生"已填好"的错觉)
 *   - 已有 activeCf 反事实:newValue 用 cf.new_value(用户之前填的真值)
 */
function getEdit(type: string, targetId: string, field: string, originalValue: string): EditState {
  const k = editKey(type, targetId, field);
  if (!(k in editStates.value)) {
    let activeCf: CounterfactualChange | null = null;
    let oldFallback = originalValue;
    if (type === "character") {
      activeCf = findActiveForChar(targetId, field);
    } else if (type === "event") {
      activeCf = findActiveForEvent(targetId, field);
    } else if (type === "world") {
      activeCf = findActiveWorld(field as WorldCounterfactualField);
      // world 类型 oldValue 优先级 → cf.old_value > project.world_baseline > ""
      oldFallback = getWorldBaselineFor(field as WorldCounterfactualField);
    }
    editStates.value[k] = {
      oldValue: activeCf?.old_value ?? oldFallback,
      // ⭐ "改"字段:有 activeCf 用 cf.new_value;无则**留空**(不预填,让用户自己写)
      newValue: activeCf?.new_value ?? "",
      userIntent: activeCf?.user_intent ?? "",
    };
  }
  return editStates.value[k];
}

/** 把数组字段(quotes/no_go_list)序列化成多行字符串显示;读回去再 parse */
function arrToLines(v: string[] | string | null): string {
  if (Array.isArray(v)) return v.join("\n");
  if (typeof v === "string") {
    try {
      const parsed = JSON.parse(v);
      if (Array.isArray(parsed)) return parsed.join("\n");
    } catch {
      /* 落到 raw */
    }
    return v;
  }
  return "";
}

function linesToValue(lines: string, fieldType: string): string | string[] {
  if (fieldType === "lines") {
    return lines.split("\n").map((s) => s.trim()).filter(Boolean);
  }
  return lines;
}

// ============================================================
// 创建 / 撤销
// ============================================================

async function handleCreateCharacterCf(charId: string, field: string, fieldType: string) {
  const k = editKey("character", charId, field);
  const state = editStates.value[k];
  if (!state) return;
  const old_value = JSON.stringify(linesToValue(state.oldValue, fieldType));
  const new_value = JSON.stringify(linesToValue(state.newValue, fieldType));
  // 文本字段简化:直接传字符串(后端 _serialize_value 会处理)
  const oldStr = fieldType === "lines" ? old_value : state.oldValue;
  const newStr = fieldType === "lines" ? new_value : state.newValue;
  if (oldStr === newStr && !state.userIntent.trim()) {
    toast.warning("没有改动也没有意图,反事实没必要创建");
    return;
  }
  try {
    await props.composable.createChange({
      target_type: "character",
      target_id: charId,
      field,
      old_value: oldStr,
      new_value: newStr,
      user_intent: state.userIntent.trim() || undefined,
    });
    toast.success("反事实已创建,推演时会注入 director");
    emit("changed");
  } catch (e) {
    toast.error("创建反事实失败");
  }
}

async function handleCreateEventCf(evtId: string, field: string) {
  const k = editKey("event", evtId, field);
  const state = editStates.value[k];
  if (!state) return;
  if (state.oldValue === state.newValue && !state.userIntent.trim()) {
    toast.warning("没有改动也没有意图,反事实没必要创建");
    return;
  }
  try {
    await props.composable.createChange({
      target_type: "event",
      target_id: evtId,
      field,
      old_value: state.oldValue,
      new_value: state.newValue,
      user_intent: state.userIntent.trim() || undefined,
    });
    toast.success("事件反事实已创建,推演时强制改写该决策");
    emit("changed");
  } catch (e) {
    toast.error("创建反事实失败");
  }
}

async function handleCreateWorldCf(field: WorldCounterfactualField) {
  const k = editKey("world", "_global_", field);
  const state = editStates.value[k];
  if (!state || !state.newValue.trim()) {
    toast.warning("世界观反事实必须填'改成'值");
    return;
  }
  try {
    await props.composable.createWorldChange({
      field,
      old_value: state.oldValue.trim() || undefined,
      new_value: state.newValue.trim(),
      user_intent: state.userIntent.trim() || undefined,
    });
    toast.success(`世界观「${WORLD_FIELD_META[field].label}」已改写,推演时为最高优先级`);
    emit("changed");
  } catch (e) {
    toast.error("创建世界观反事实失败");
  }
}

async function handleRevert(cf: CounterfactualChange) {
  const ok = await confirmDialog({
    title: "撤销该反事实?",
    message: "推演不再考虑这个 what-if;若是字段改动会还原原值。",
    danger: false,
    confirmLabel: "撤销",
  });
  if (!ok) return;
  try {
    const result = await props.composable.revert(cf.id);
    if (result.target_field_restored) {
      toast.success("已撤销并还原原值");
    } else {
      toast.warning(result.restore_error ?? "已标撤销但 db 还原失败");
    }
    // 清除该 field 的本地编辑状态(下次展开重新拉)
    delete editStates.value[editKey(cf.target_type, cf.target_id, cf.field)];
    emit("changed");
  } catch (e) {
    toast.error("撤销失败");
  }
}

// ============================================================
// 监听 open 重置编辑状态
// ============================================================

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      editStates.value = {};
      expandedCharId.value = null;
      expandedEventId.value = null;
      void props.composable.reload();
    }
  },
);

// 全局 keydown 监听 — overlay 用 tabindex 不 focus 时,@keydown 不触发(Teleport
// 到 body 后无法靠 DOM 冒泡)。改 document 级监听后,modal 一打开就能按 Esc 关。
function onGlobalKey(e: KeyboardEvent) {
  if (!props.open) return;
  if (e.key !== "Escape") return;
  // 嵌套优先:指南开着时 ESC 只关指南,不关 Workbench
  if (guideOpen.value) {
    guideOpen.value = false;
    e.stopPropagation();
    return;
  }
  emit("close");
}
onMounted(() => document.addEventListener("keydown", onGlobalKey));
onBeforeUnmount(() => document.removeEventListener("keydown", onGlobalKey));

const totalActive = computed(() => props.composable.totalActive.value);
const charCount = computed(() => props.composable.byType.value.character);
const evtCount = computed(() => props.composable.byType.value.event);
const worldCount = computed(
  () => props.composable.items.value.filter((c) => c.target_type === "world").length,
);
</script>

<template>
  <Teleport to="body">
    <transition name="wb-fade">
      <div
        v-if="open"
        class="wb-overlay"
        @click.self="emit('close')"
      >
        <transition name="wb-pop">
          <section
            v-if="open"
            class="wb-card"
            role="dialog"
            aria-label="反事实工作台"
          >
            <header class="wb-header">
              <h2 class="wb-title">
                <span class="wb-icon">⟲</span>
                反事实工作台
                <button
                  type="button"
                  class="guide-toggle"
                  :class="{ 'is-open': guideOpen }"
                  :aria-expanded="guideOpen"
                  aria-label="使用指南"
                  title="使用指南"
                  @click="guideOpen = !guideOpen"
                >ⓘ</button>
                <span v-if="totalActive > 0" class="wb-count mono">{{ totalActive }}</span>
              </h2>
              <p class="wb-sub">把"意难平"变成"可能性" — 改一个变量,故事就走向另一条路</p>
              <!-- Sprint 6.A1(2026-05-18):agent 联动语义提示 — 让用户理解"改属性 = 改 agent 行为" -->
              <p class="wb-agent-note">
                <span class="agent-note-icon" aria-hidden="true">🎭</span>
                改角色的 <strong>身份 / 性格 / 台词 / 禁忌</strong> = 改该角色 agent 的行为逻辑。
                续写时这个 agent 会以新档案扮演,反事实真正生效。
              </p>
              <!-- Sprint 6.A2 CT(2026-05-21)— 组合树入口 -->
              <button
                class="combo-tree-btn"
                type="button"
                aria-label="生成反事实组合树"
                title="把 1-3 个反事实变量组合起来,跑 2-8 个推演对比哪个组合最有戏"
                @click="openCombinationTree"
              >
                <span class="combo-tree-icon">🌳</span>
                组合树
              </button>
              <button class="close-btn" type="button" aria-label="关闭" @click="emit('close')">×</button>
            </header>


            <!-- Tab 切换 -->
            <nav class="wb-tabs" role="tablist">
              <button
                role="tab"
                :aria-selected="activeTab === 'character'"
                class="wb-tab"
                :class="{ 'is-active': activeTab === 'character' }"
                @click="activeTab = 'character'"
              >
                ① 角色反事实
                <span v-if="charCount > 0" class="tab-count mono">{{ charCount }}</span>
              </button>
              <button
                role="tab"
                :aria-selected="activeTab === 'event'"
                class="wb-tab"
                :class="{ 'is-active': activeTab === 'event' }"
                @click="activeTab = 'event'"
              >
                ② 事件反事实
                <span v-if="evtCount > 0" class="tab-count mono">{{ evtCount }}</span>
              </button>
              <button
                role="tab"
                :aria-selected="activeTab === 'world'"
                class="wb-tab wb-tab-world"
                :class="{ 'is-active': activeTab === 'world' }"
                @click="activeTab = 'world'"
              >
                ③ 世界观反事实 <span class="world-star">⭐</span>
                <span v-if="worldCount > 0" class="tab-count mono">{{ worldCount }}</span>
              </button>
            </nav>

            <div class="wb-body">
              <!-- ① 角色 tab -->
              <div v-if="activeTab === 'character'" class="tab-pane">
                <p class="tab-hint">改角色性格 / 身份 / 台词 / 禁忌 — 让 ta 在原作场景里做不同选择</p>
                <ul v-if="characters.length > 0" class="entity-list">
                  <li v-for="ch in characters" :key="ch.id" class="entity-item">
                    <header
                      class="entity-head"
                      :class="{ 'has-cf': countActiveForChar(ch.id) > 0 }"
                      @click="toggleCharExpand(ch.id)"
                    >
                      <span class="entity-name">{{ ch.name }}</span>
                      <span
                        v-if="countActiveForChar(ch.id) > 0"
                        class="cf-mark"
                        :title="`该角色已改 ${countActiveForChar(ch.id)} 项反事实`"
                      >
                        <span class="cf-mark-dot">●</span>
                        已改 {{ countActiveForChar(ch.id) }}
                      </span>
                      <span class="entity-meta mono">{{ ch.identity || "(无身份)" }}</span>
                      <span class="entity-toggle">{{ expandedCharId === ch.id ? "▾" : "▸" }}</span>
                    </header>
                    <div v-if="expandedCharId === ch.id" class="entity-fields">
                      <div
                        v-for="f in CHARACTER_FIELDS"
                        :key="f.key"
                        class="field-block"
                      >
                        <label class="field-label">
                          <span>{{ f.label }}</span>
                          <span
                            v-if="findActiveForChar(ch.id, f.key)"
                            class="active-tag"
                          >已改</span>
                        </label>
                        <div class="field-pair">
                          <div class="field-col">
                            <span class="col-label mono">原</span>
                            <textarea
                              v-if="f.type === 'lines'"
                              v-autosize
                              class="field-textarea"
                              :value="getEdit('character', ch.id, f.key, arrToLines(ch[f.key as keyof typeof ch] as string[])).oldValue"
                              @input="getEdit('character', ch.id, f.key, '').oldValue = ($event.target as HTMLTextAreaElement).value"
                              rows="3"
                              :placeholder="`原作 ${f.label}`"
                            />
                            <input
                              v-else
                              type="text"
                              class="field-input"
                              :value="getEdit('character', ch.id, f.key, String(ch[f.key as keyof typeof ch] ?? '')).oldValue"
                              @input="getEdit('character', ch.id, f.key, '').oldValue = ($event.target as HTMLInputElement).value"
                              :placeholder="`原作 ${f.label}`"
                            />
                          </div>
                          <div class="field-col">
                            <span class="col-label mono">改</span>
                            <textarea
                              v-if="f.type === 'lines'"
                              v-autosize
                              class="field-textarea field-textarea-new"
                              :value="getEdit('character', ch.id, f.key, arrToLines(ch[f.key as keyof typeof ch] as string[])).newValue"
                              @input="getEdit('character', ch.id, f.key, '').newValue = ($event.target as HTMLTextAreaElement).value"
                              rows="3"
                              :placeholder="`你想改成的 ${f.label}`"
                            />
                            <input
                              v-else
                              type="text"
                              class="field-input field-input-new"
                              :value="getEdit('character', ch.id, f.key, String(ch[f.key as keyof typeof ch] ?? '')).newValue"
                              @input="getEdit('character', ch.id, f.key, '').newValue = ($event.target as HTMLInputElement).value"
                              :placeholder="`你想改成的 ${f.label}`"
                            />
                          </div>
                        </div>
                        <label class="intent-label">
                          <span>★ 你的意图(强烈推荐 — LLM 编排时优先看这个)</span>
                          <input
                            type="text"
                            class="intent-input"
                            :value="getEdit('character', ch.id, f.key, '').userIntent"
                            @input="getEdit('character', ch.id, f.key, '').userIntent = ($event.target as HTMLInputElement).value"
                            maxlength="500"
                          />
                        </label>
                        <div class="field-actions">
                          <button
                            v-if="findActiveForChar(ch.id, f.key)"
                            type="button"
                            class="btn-revert"
                            @click="handleRevert(findActiveForChar(ch.id, f.key)!)"
                          >↻ 撤销</button>
                          <button
                            type="button"
                            class="btn-create"
                            @click="handleCreateCharacterCf(ch.id, f.key, f.type)"
                          >✦ {{ findActiveForChar(ch.id, f.key) ? "更新反事实" : "创建反事实" }}</button>
                        </div>
                      </div>
                    </div>
                  </li>
                </ul>
                <p v-else class="empty-state">项目还没有角色 — 先去 3D 图谱创建角色</p>
              </div>

              <!-- ② 事件 tab -->
              <div v-else-if="activeTab === 'event'" class="tab-pane">
                <p class="tab-hint">改事件描述 / 强制改写关键决策 — 例:"小明拒绝表白" → "小明接受表白",蝴蝶效应改后续</p>
                <ul v-if="events.length > 0" class="entity-list">
                  <li v-for="ev in events" :key="ev.id" class="entity-item">
                    <header
                      class="entity-head"
                      :class="{ 'has-cf': countActiveForEvent(ev.id) > 0 }"
                      @click="toggleEventExpand(ev.id)"
                    >
                      <span class="entity-name">{{ (ev.description ?? '').slice(0, 30) || '(无描述)' }}</span>
                      <span
                        v-if="countActiveForEvent(ev.id) > 0"
                        class="cf-mark"
                        :title="`该事件已改 ${countActiveForEvent(ev.id)} 项反事实`"
                      >
                        <span class="cf-mark-dot">●</span>
                        已改 {{ countActiveForEvent(ev.id) }}
                      </span>
                      <span class="entity-meta mono">{{ ev.participants?.length ?? 0 }} 参与者</span>
                      <span class="entity-toggle">{{ expandedEventId === ev.id ? "▾" : "▸" }}</span>
                    </header>
                    <div v-if="expandedEventId === ev.id" class="entity-fields">
                      <div
                        v-for="f in EVENT_FIELDS"
                        :key="f.key"
                        class="field-block"
                      >
                        <label class="field-label">
                          <span>{{ f.label }}</span>
                          <span
                            v-if="findActiveForEvent(ev.id, f.key)"
                            class="active-tag"
                          >已改</span>
                        </label>
                        <div class="field-pair">
                          <div class="field-col">
                            <span class="col-label mono">原</span>
                            <textarea
                              v-autosize
                              class="field-textarea"
                              :value="getEdit('event', ev.id, f.key, f.key === 'description' ? (ev.description ?? '') : '').oldValue"
                              @input="getEdit('event', ev.id, f.key, '').oldValue = ($event.target as HTMLTextAreaElement).value"
                              rows="2"
                              :placeholder="f.key === 'description' ? '原作事件描述' : '原作走向(用户填)'"
                            />
                          </div>
                          <div class="field-col">
                            <span class="col-label mono">改</span>
                            <textarea
                              v-autosize
                              class="field-textarea field-textarea-new"
                              :value="getEdit('event', ev.id, f.key, f.key === 'description' ? (ev.description ?? '') : '').newValue"
                              @input="getEdit('event', ev.id, f.key, '').newValue = ($event.target as HTMLTextAreaElement).value"
                              rows="2"
                              :placeholder="f.key === 'description' ? '你想改成的事件' : '你想要的走向'"
                            />
                          </div>
                        </div>
                        <label class="intent-label">
                          <span>★ 你的意图</span>
                          <input
                            type="text"
                            class="intent-input"
                            :value="getEdit('event', ev.id, f.key, '').userIntent"
                            @input="getEdit('event', ev.id, f.key, '').userIntent = ($event.target as HTMLInputElement).value"
                            maxlength="500"
                          />
                        </label>
                        <div class="field-actions">
                          <button
                            v-if="findActiveForEvent(ev.id, f.key)"
                            type="button"
                            class="btn-revert"
                            @click="handleRevert(findActiveForEvent(ev.id, f.key)!)"
                          >↻ 撤销</button>
                          <button
                            type="button"
                            class="btn-create"
                            @click="handleCreateEventCf(ev.id, f.key)"
                          >✦ {{ findActiveForEvent(ev.id, f.key) ? "更新反事实" : "创建反事实" }}</button>
                        </div>
                      </div>
                    </div>
                  </li>
                </ul>
                <p v-else class="empty-state">项目还没有事件</p>
              </div>

              <!-- ③ 世界观 tab(6 维度) -->
              <div v-else-if="activeTab === 'world'" class="tab-pane">
                <p class="tab-hint world-hint">
                  ⭐ 世界观反事实是<strong>最高优先级</strong> — 改了一个,所有角色 / 事件都按新世界观演。<br />
                  例:把"都市生活"改成"星际穿越",把"纯人类"改成"有神明 + 有魔法",连原作设定都覆盖。
                </p>

                <!-- 2.C+ polish: 没 baseline 时显 AI 识别提示;有则显刷新链接 -->
                <div class="baseline-bar" :class="{ 'no-baseline': !hasWorldBaseline }">
                  <template v-if="!hasWorldBaseline">
                    <span class="baseline-msg">
                      <span class="baseline-icon">ℹ</span>
                      6 维度的"原"字段为空 — 让 AI 识别原作世界观,作为你修改的参照。
                    </span>
                    <button
                      type="button"
                      class="btn-infer-baseline"
                      :disabled="inferringBaseline || !project"
                      @click="handleInferBaseline"
                    >
                      <span v-if="inferringBaseline">识别中…</span>
                      <span v-else>✦ AI 识别原作世界观</span>
                    </button>
                  </template>
                  <template v-else>
                    <span class="baseline-msg baseline-ok">
                      <span class="baseline-icon">✓</span>
                      AI 已识别原作世界观,"原"字段已预填(可手动修正)
                    </span>
                    <button
                      type="button"
                      class="btn-infer-baseline btn-refresh"
                      :disabled="inferringBaseline || !project"
                      title="重新识别(覆盖现有 baseline)"
                      @click="handleInferBaseline"
                    >
                      <span v-if="inferringBaseline">识别中…</span>
                      <span v-else>↻ 重新识别</span>
                    </button>
                  </template>
                </div>
                <ul class="world-fields-list">
                  <li
                    v-for="field in WORLD_FIELDS"
                    :key="field"
                    class="world-field-block"
                    :class="{ 'has-cf': !!findActiveWorld(field) }"
                  >
                    <header class="world-field-head">
                      <span class="world-field-label">
                        <span class="world-field-icon">▸</span>
                        {{ WORLD_FIELD_META[field].label }}
                      </span>
                      <span
                        v-if="findActiveWorld(field)"
                        class="cf-mark"
                        title="该维度已改写"
                      >
                        <span class="cf-mark-dot">●</span>
                        已改
                      </span>
                    </header>
                    <div class="world-field-body">
                      <div class="field-pair">
                        <div class="field-col">
                          <span class="col-label mono">原</span>
                          <input
                            v-if="field !== 'free_form'"
                            type="text"
                            class="field-input"
                            :value="getEdit('world', '_global_', field, '').oldValue"
                            @input="getEdit('world', '_global_', field, '').oldValue = ($event.target as HTMLInputElement).value"
                          />
                          <textarea
                            v-else
                            v-autosize
                            class="field-textarea"
                            :value="getEdit('world', '_global_', field, '').oldValue"
                            @input="getEdit('world', '_global_', field, '').oldValue = ($event.target as HTMLTextAreaElement).value"
                            rows="2"
                          />
                        </div>
                        <div class="field-col">
                          <span class="col-label mono">改</span>
                          <input
                            v-if="field !== 'free_form'"
                            type="text"
                            class="field-input field-input-new"
                            :value="getEdit('world', '_global_', field, '').newValue"
                            @input="getEdit('world', '_global_', field, '').newValue = ($event.target as HTMLInputElement).value"
                          />
                          <textarea
                            v-else
                            v-autosize
                            class="field-textarea field-textarea-new"
                            :value="getEdit('world', '_global_', field, '').newValue"
                            @input="getEdit('world', '_global_', field, '').newValue = ($event.target as HTMLTextAreaElement).value"
                            rows="3"
                          />
                        </div>
                      </div>
                      <label class="intent-label">
                        <span>★ 你的意图(强烈推荐)</span>
                        <input
                          type="text"
                          class="intent-input"
                          :value="getEdit('world', '_global_', field, '').userIntent"
                          @input="getEdit('world', '_global_', field, '').userIntent = ($event.target as HTMLInputElement).value"
                          maxlength="500"
                        />
                      </label>
                      <div class="field-actions">
                        <button
                          v-if="findActiveWorld(field)"
                          type="button"
                          class="btn-revert"
                          @click="handleRevert(findActiveWorld(field)!)"
                        >↻ 撤销</button>
                        <button
                          type="button"
                          class="btn-create"
                          @click="handleCreateWorldCf(field)"
                        >⭐ {{ findActiveWorld(field) ? "更新世界观" : "改写世界观" }}</button>
                      </div>
                    </div>
                  </li>
                </ul>
              </div>
            </div>
          </section>
        </transition>
      </div>
    </transition>
  </Teleport>

  <!-- 使用指南独立 modal(嵌套在 Workbench 之上,z-index 960 > 950)-->
  <Teleport to="body">
    <transition name="guide-fade">
      <div
        v-if="guideOpen"
        class="guide-overlay"
        @click.self="guideOpen = false"
      >
        <transition name="guide-pop">
          <section
            v-if="guideOpen"
            class="guide-card"
            role="dialog"
            aria-label="反事实工作台使用指南"
          >
            <header class="guide-card-header">
              <h3 class="guide-card-title">
                <span class="guide-card-icon">ⓘ</span>
                反事实工作台 — 使用指南
              </h3>
              <button
                class="guide-close-btn"
                type="button"
                aria-label="关闭"
                @click="guideOpen = false"
              >×</button>
            </header>

            <div class="guide-body">
              <p class="guide-block">
                <strong>它是什么</strong><br />
                「what-if 模拟器」的控制台。在这里改一个变量,推演时 AI 按这个 what-if 编排,故事就走向另一条路 — 终结你的"意难平"。
              </p>

              <p class="guide-block">
                <strong>三类反事实</strong>
              </p>
              <ul class="guide-list">
                <li>
                  <strong>① 角色反事实</strong> — 改角色的性格 / 身份 / 台词 / 禁忌,该角色在原作场景里的反应就跟着变。
                </li>
                <li>
                  <strong>② 事件反事实</strong> — 强制改写关键决策的结果,后续蝴蝶效应自然延伸。
                </li>
                <li>
                  <strong>③ 世界观反事实</strong> <span class="guide-star">⭐ 最高优先级</span> — 改全局设定,所有角色 / 事件按新世界观演。
                </li>
              </ul>

              <p class="guide-block">
                <strong>怎么写得好</strong>
              </p>
              <ul class="guide-list">
                <li>
                  <strong>原 / 改</strong> 字段:简洁清楚,一句话能说清最好。
                </li>
                <li>
                  <strong>★ 你的意图</strong> <span class="guide-emph">— 最重要</span>:用自然语言告诉 AI 你<strong>为什么</strong>这么改、<strong>想要什么效果</strong>。比起字段对比,AI 更看这个。
                </li>
              </ul>

              <p class="guide-block">
                <strong>创建之后</strong>
              </p>
              <ul class="guide-list">
                <li>反事实保存到工作台,可随时查看 / 撤销。</li>
                <li>打开 AI 重塑 → 反事实自动注入推演 → 编排按 what-if 走。</li>
                <li>不满意 → 一键撤销,字段还原原值。</li>
              </ul>
            </div>
          </section>
        </transition>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.wb-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal-backdrop);   /* 业务 modal 层 */
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(4px);
  padding: var(--space-4);
}

.wb-card {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  max-width: 760px;
  max-height: 88vh;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.18);
  overflow: hidden;
}

.wb-header {
  position: relative;
  padding: var(--space-4) var(--space-5) var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.wb-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  display: flex;
  align-items: baseline;
  gap: var(--space-2);
  margin: 0;
}
.wb-icon {
  color: var(--color-accent);
}
.wb-count {
  font-size: var(--text-md);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  padding: 2px 8px;
  border-radius: var(--radius-sm);
}

/* ⓘ 使用指南切换按钮(标题旁)*/
.guide-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
  margin-left: 4px;
}
.guide-toggle:hover {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent-border);
}
.guide-toggle.is-open {
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-color: var(--color-accent);
}

/* 使用指南独立 modal(嵌套在 Workbench 之上)*/
.guide-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal-nested-backdrop);   /* 嵌套于 Workbench 之上 */
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.5);
  backdrop-filter: blur(5px);
  padding: var(--space-4);
}

.guide-card {
  position: relative;
  width: 100%;
  max-width: 540px;
  max-height: 82vh;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: 0 20px 56px rgba(0, 0, 0, 0.22);
  overflow: hidden;
}

.guide-card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3) var(--space-5);
  border-bottom: 1px solid var(--color-border);
  background: var(--color-bg-subtle);
}
.guide-card-title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.guide-card-icon {
  color: var(--color-accent);
  font-size: var(--text-lg);
}
.guide-close-btn {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.guide-close-btn:hover {
  color: var(--color-text);
  background: var(--color-bg);
}

.guide-body {
  padding: var(--space-4) var(--space-5);
  overflow-y: auto;
  font-size: var(--text-sm);
  line-height: 1.7;
  color: var(--color-text);
}
.guide-block {
  margin: var(--space-2) 0 4px 0;
}
.guide-block:first-child {
  margin-top: 0;
}
.guide-block strong {
  color: var(--color-text);
  font-weight: 600;
}
.guide-list {
  margin: 0 0 var(--space-3) 0;
  padding-left: var(--space-4);
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.guide-list li {
  color: var(--color-text);
}
.guide-list li strong {
  color: var(--color-accent-text);
}
.guide-star {
  color: #B45309;
  font-size: var(--text-xs);
  margin-left: 4px;
  font-weight: 600;
}
.guide-emph {
  color: var(--color-danger);
  font-weight: 600;
}

.guide-fade-enter-active,
.guide-fade-leave-active {
  transition: opacity var(--duration-fast) var(--ease-out);
}
.guide-fade-enter-from,
.guide-fade-leave-to {
  opacity: 0;
}
.guide-pop-enter-active,
.guide-pop-leave-active {
  transition:
    transform var(--duration-base) var(--ease-out),
    opacity var(--duration-fast) var(--ease-out);
}
.guide-pop-enter-from,
.guide-pop-leave-to {
  transform: scale(0.94) translateY(10px);
  opacity: 0;
}
.wb-sub {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin: 4px 0 0 0;
}
/* Sprint 6.A1(2026-05-18):agent 联动语义提示 */
.wb-agent-note {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 8px 0 0 0;
  padding: 8px 10px;
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-md);
  line-height: 1.6;
}
.agent-note-icon {
  font-size: var(--text-base);
  flex-shrink: 0;
  margin-top: 1px;
}
.close-btn {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  width: 32px;
  height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.close-btn:hover {
  color: var(--color-text);
  background: var(--color-bg-subtle);
}

/* Sprint 6.A2 CT(2026-05-21)— 组合树入口按钮 */
.combo-tree-btn {
  position: absolute;
  top: var(--space-3);
  right: 56px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
  background: var(--color-surface-2, #f1f5f9);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.combo-tree-btn:hover {
  background: #eff6ff;
  border-color: #93c5fd;
  color: #1e40af;
}
.combo-tree-icon {
  font-size: var(--text-base);
}

/* Tabs */
.wb-tabs {
  display: flex;
  gap: var(--space-1);
  padding: 0 var(--space-5);
  border-bottom: 1px solid var(--color-border);
}
.wb-tab {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  cursor: pointer;
  transition: color var(--duration-fast) var(--ease-out);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.wb-tab:hover {
  color: var(--color-text);
}
.wb-tab.is-active {
  color: var(--color-accent-text);
  border-bottom-color: var(--color-accent);
}
.tab-count {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
}
.world-star {
  font-size: 11px;
  color: #F59E0B;
}
.wb-tab-world.is-active {
  color: #B45309;
  border-bottom-color: #F59E0B;
}

/* Body */
.wb-body {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-4) var(--space-5);
}
.tab-hint {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin-bottom: var(--space-3);
  line-height: 1.6;
}
.world-hint {
  padding: var(--space-2) var(--space-3);
  background: rgba(245, 158, 11, 0.10);
  border: 1px solid rgba(245, 158, 11, 0.30);
  border-radius: var(--radius-md);
}
.world-hint strong {
  color: #B45309;
}

/* 2.C+ polish: world baseline 状态条 */
.baseline-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  margin-bottom: var(--space-3);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
}
.baseline-bar.no-baseline {
  background: rgba(245, 158, 11, 0.08);
  border-color: rgba(245, 158, 11, 0.3);
}
.baseline-msg {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--color-text);
  flex: 1;
  min-width: 0;
}
.baseline-msg.baseline-ok {
  color: #16A34A;
}
.baseline-icon {
  font-size: var(--text-base);
  flex-shrink: 0;
}
.btn-infer-baseline {
  flex-shrink: 0;
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.btn-infer-baseline:hover:not(:disabled) {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}
.btn-infer-baseline:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.btn-refresh {
  background: transparent;
  color: var(--color-text-muted);
  border-color: var(--color-border);
}
.btn-refresh:hover:not(:disabled) {
  color: var(--color-accent-text);
  border-color: var(--color-accent);
}
.empty-state {
  text-align: center;
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  padding: var(--space-6);
  font-style: italic;
}

/* 角色 / 事件 列表 */
.entity-list,
.world-fields-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
  list-style: none;
  padding: 0;
  margin: 0;
}
.entity-item {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.entity-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  cursor: pointer;
  user-select: none;
  transition: background var(--duration-fast) var(--ease-out);
  position: relative;
}
.entity-head:hover {
  background: var(--color-accent-soft);
}
.entity-head.has-cf {
  background: rgba(22, 163, 74, 0.10);
}
.entity-head.has-cf:hover {
  background: rgba(22, 163, 74, 0.16);
}

/* 已改 N 反事实绿色标(entity-head 折叠时也能一眼看到)*/
.cf-mark {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px var(--space-2);
  font-size: 10px;
  font-weight: 600;
  color: #16A34A;
  background: rgba(22, 163, 74, 0.12);
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}
.cf-mark-dot {
  font-size: 8px;
  line-height: 1;
}
.entity-name {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
  flex-shrink: 0;
}
.entity-meta {
  flex: 1;
  font-size: 11px;
  color: var(--color-text-subtle);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.entity-toggle {
  font-size: var(--text-base);
  color: var(--color-text-muted);
}

.entity-fields {
  padding: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  background: var(--color-surface);
  border-top: 1px solid var(--color-border);
}

/* 字段块 */
.field-block,
.world-field-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-bottom: var(--space-2);
  border-bottom: 1px dashed var(--color-border);
}
.field-block:last-child,
.world-field-block:last-child {
  border-bottom: none;
}
.world-field-block {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  position: relative;
  transition: border-color var(--duration-fast) var(--ease-out);
}
.world-field-block.has-cf {
  border-color: rgba(22, 163, 74, 0.45);
  background: rgba(22, 163, 74, 0.06);
}
.field-label,
.world-field-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text);
}
.world-field-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
}
.world-field-icon {
  color: #F59E0B;
}
.active-tag {
  font-size: 9px;
  padding: 1px 5px;
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
  font-weight: 600;
}

.field-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-2);
}
.field-col {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.col-label {
  font-size: 10px;
  color: var(--color-text-subtle);
  text-align: center;
}
.field-input,
.field-textarea,
.intent-input {
  width: 100%;
  padding: 6px var(--space-2);
  font-size: var(--text-xs);
  font-family: inherit;
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  transition: border-color var(--duration-fast) var(--ease-out);
}
.field-input:focus,
.field-textarea:focus,
.intent-input:focus {
  outline: none;
  border-color: var(--color-accent);
}
.field-textarea {
  resize: vertical;
  line-height: 1.5;
}
.field-input-new,
.field-textarea-new {
  background: var(--color-accent-soft);
  border-color: var(--color-accent-border);
  color: var(--color-accent-text);
  font-weight: 500;
}

.intent-label {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.intent-input {
  font-style: italic;
}
.intent-input::placeholder {
  font-style: italic;
  color: var(--color-text-subtle);
}

.field-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
}
.btn-create {
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.btn-create:hover {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}
.btn-revert {
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.btn-revert:hover {
  color: var(--color-danger);
  border-color: var(--color-danger);
  background: var(--color-danger-soft);
}

/* Transitions */
.wb-fade-enter-active,
.wb-fade-leave-active {
  transition: opacity var(--duration-fast) var(--ease-out);
}
.wb-fade-enter-from,
.wb-fade-leave-to {
  opacity: 0;
}
.wb-pop-enter-active,
.wb-pop-leave-active {
  transition:
    transform var(--duration-base) var(--ease-out),
    opacity var(--duration-fast) var(--ease-out);
}
.wb-pop-enter-from,
.wb-pop-leave-to {
  transform: scale(0.96) translateY(8px);
  opacity: 0;
}
</style>
