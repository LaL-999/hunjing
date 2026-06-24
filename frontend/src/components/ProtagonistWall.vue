<script setup lang="ts">
/**
 * ProtagonistWall — Sprint 6.A1 + 6.A2 M1++(2026-05-18 重构版)
 *
 * 产品意图(对齐用户拍板"路径 C 多 agent 仿真"):
 *   - 主角 agent 卡片墙(信息架构按主角组织,不按 section 罗列)
 *   - 顶部 section 可折叠(列表长时不撑屏)
 *   - 每张卡:折叠态显信息 + 独立"删除"+"展开"按钮(替代原"点击即删除"反直觉设计)
 *   - 展开态显:档案 4 字段精简预览 + 该角色关系子列表 + 每条关系内嵌时间轴
 *   - 杀掉顶层 RelationshipsTimelineSection — 关系时间轴并入主角卡片(用户反馈"独立 section 繁琐"采纳)
 *
 * 适用 mode:仅 middle / cycle / end(initial 态走手建)
 *
 * 父组件:ProjectView(传 projectId);本组件自管 API 调用 + 本地状态
 */
import { computed, ref, watch } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type Character,
  type ProtagonistJudgeReport,
  type Relationship,
  type RelationshipStrength,
} from "../api/types";
import { toast } from "../composables/useToast";
import { confirm as confirmDialog } from "../composables/useConfirm";
import BehaviorBaselineEditor from "./BehaviorBaselineEditor.vue";
import Icon from "./Icon.vue";
import RelationshipTimeline from "./RelationshipTimeline.vue";
import type { BehaviorBaseline } from "../api/types";

const props = defineProps<{
  projectId: string;
}>();

// UI 优化(2026-05-21 十一轮):手风琴联动 — 展开时 emit,父组件折叠另一个 section
// Sprint 6.A2 #2 二期(2026-05-22):open-emotion-overview — 角色卡 "情绪总览" 按钮触发
//   父 ProjectView 接收后打开 CharacterEmotionOverviewModal
const emit = defineEmits<{
  (e: "expanded"): void;
  (e: "open-emotion-overview", payload: { id: string; name: string }): void;
}>();

// 顶部 section 折叠(默认展开)
// UI 优化(2026-05-21 三轮):默认折叠 — 用户从其它页面进入图谱编辑 tab 时,
// 主角面板始终从折叠态开始,避免一进来就被大块内容淹没。用户主动点 ▸ 展开。
const sectionCollapsed = ref(true);

// 数据
// FOCUS.8(2026-05-22):废弃 protagonists 单独 ref — 列表已派生为 visibleCharacters
// (按 viewMode 过滤 allCharacters);loadAll 不再单独拉 /protagonists 接口,省 1 个 RTT。
const allCharacters = ref<Character[]>([]);
const allRelationships = ref<Relationship[]>([]);
const phaseCounts = ref<Map<string, number>>(new Map());
const loading = ref(false);
const judging = ref(false);
const error = ref<string | null>(null);

// UI 状态
// UI 优化(2026-05-21 十一轮):同时只能展开 1 张角色卡(手风琴)— 从 Set 改为单值
const expandedCardId = ref<string | null>(null);
const expandedRelIds = ref<Set<string>>(new Set());      // 关系子卡的时间轴展开
// UI 优化(2026-05-21):删除按钮收入 "⋯" 更多菜单 — menuOpenCardId 记录当前打开菜单的卡片
const menuOpenCardId = ref<string | null>(null);
// Sprint 6.A2 FOCUS(2026-05-21):合并模式 — 选中 source 后点其他卡 = target,对齐 SceneGraph 合并语义
const mergeFromCharId = ref<string | null>(null);
const operating = ref(false);

// Sprint 6.A2 M1+++(2026-05-18 用户反馈)关系子列表展示控制
// 默认每个角色只显前 RELS_DEFAULT_LIMIT 条强关系,可"展开全部";顶部按钮可全局过滤弱关系
const RELS_DEFAULT_LIMIT = 5;
const expandedRelLists = ref<Set<string>>(new Set());   // 哪些主角卡的关系列表展开全部
// UI 优化(2026-05-21 三轮):删除"已隐弱关系/全部强度"切换器后,该值固定为 true
// 关系子卡始终隐藏 weak / moderately_weak,用户不可切换(消除假切换器疑虑)
const hideWeakRels = ref(true);

// UI 优化(2026-05-21 三轮):主角卡分页(每页 6 个,默认第 1 页)
const PROTAG_PAGE_SIZE = 6;
const protagPage = ref(1);

/**
 * Sprint 6.A2 FOCUS.8(2026-05-22):主角面板视图切换器。
 *
 * 背景:之前只显示主角(is_protagonist=true),想编辑配角必须进 3D 图谱"茫茫大海"里找。
 * 现支持 3 态切换:protagonist(默认) → supporting → all → protagonist 循环。
 *
 * - protagonist:仅 is_protagonist=true(默认,与老行为一致)
 * - supporting:仅 is_protagonist=false(配角)
 * - all:全部角色(允许主/配跨档合并)
 */
type AgentViewMode = "protagonist" | "supporting" | "all";
const viewMode = ref<AgentViewMode>("protagonist");

const VIEW_MODE_LABEL: Record<AgentViewMode, string> = {
  protagonist: "主角 agent",
  supporting: "配角 agent",
  all: "全部角色",
};
const VIEW_MODE_NEXT_TITLE: Record<AgentViewMode, string> = {
  protagonist: "切到 配角",
  supporting: "切到 全部",
  all: "切到 主角",
};

function cycleViewMode() {
  if (viewMode.value === "protagonist") viewMode.value = "supporting";
  else if (viewMode.value === "supporting") viewMode.value = "all";
  else viewMode.value = "protagonist";
  // 切换视图 → 关闭展开的卡 / 菜单 / 翻页归 1
  expandedCardId.value = null;
  menuOpenCardId.value = null;
  mergeFromCharId.value = null;
  protagPage.value = 1;
}

/** 当前视图对应的角色列表(派生 allCharacters,不再单独拉接口)*/
const visibleCharacters = computed<Character[]>(() => {
  if (viewMode.value === "protagonist") {
    return allCharacters.value.filter((c) => c.is_protagonist);
  }
  if (viewMode.value === "supporting") {
    return allCharacters.value.filter((c) => !c.is_protagonist);
  }
  return allCharacters.value;
});

// 关系强度 → 排序权重(高在前)
const _STRENGTH_RANK: Record<RelationshipStrength, number> = {
  strong: 5,
  moderately_strong: 4,
  moderate: 3,
  moderately_weak: 2,
  weak: 1,
};

const charById = computed(() => {
  const m = new Map<string, Character>();
  for (const c of allCharacters.value) m.set(c.id, c);
  return m;
});

async function loadAll() {
  loading.value = true;
  error.value = null;
  try {
    // FOCUS.8(2026-05-22):不再单独拉 /protagonists — visibleCharacters 按 viewMode
    // 客户端过滤 allCharacters,省 1 个接口 RTT(主/配角列表本质是一份数据的视图)
    const [chars, rels] = await Promise.all([
      api.get<Character[]>(`/projects/${props.projectId}/characters`),
      api.get<Relationship[]>(`/projects/${props.projectId}/relationships`),
    ]);
    allCharacters.value = chars;
    allRelationships.value = rels;

    // 2026-06-24 性能:批量一次拉 phase 计数,替代原来逐条 N+1
    // (一个项目 30-80 条关系 = 30-80 个并发请求 → 1 个;这是"点什么都加载一会"的主因)
    const counts = new Map<string, number>();
    try {
      const bulk = await api.get<Record<string, number>>(
        `/projects/${props.projectId}/relationship_phase_counts`,
      );
      // 无 phase 行的老关系不在结果里 → 按隐式 1 阶段补(与后端首访自动迁移语义一致)
      for (const r of rels) {
        counts.set(r.id, bulk[r.id] ?? 1);
      }
    } catch {
      // 批量失败也不再 N+1:全部按 1 兜底
      for (const r of rels) counts.set(r.id, 1);
    }
    phaseCounts.value = counts;
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : "加载角色列表失败";
    allCharacters.value = [];
  } finally {
    loading.value = false;
  }
}

/** 触发后端重新跑主角判定 */
async function reJudge() {
  if (judging.value) return;
  judging.value = true;
  error.value = null;
  try {
    const report = await api.post<ProtagonistJudgeReport>(
      `/projects/${props.projectId}/judge_protagonists`,
    );
    toast.success(
      `主角判定完成 — ${report.protagonist_count} 个主角 / ${report.judged_count} 候选 ` +
      (report.skipped_pinned > 0
        ? `(跳过 ${report.skipped_pinned} 个你手动锁定的)`
        : ""),
    );
    await loadAll();
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : "判定失败,请重试";
    toast.error(error.value);
  } finally {
    judging.value = false;
  }
}

/**
 * Sprint 6.A2 FOCUS.8(2026-05-22):toggleProtagonist — 主/配角双向切换
 *
 * 老 `removeProtagonist` 只能"主角 → 配角"单向;现在配角视图也能"设为主角"。
 * 同时:用 useConfirm 统一中央 modal 替代 native confirm(.claude/skills 规则)。
 *
 * 决策语义:用户手动切 → 设 protagonist_user_pinned=true 阻止 AI 重判覆盖。
 */
async function toggleProtagonist(char: Character, event: MouseEvent) {
  event.stopPropagation();
  const becomeProtagonist = !char.is_protagonist;
  const ok = await confirmDialog({
    title: becomeProtagonist
      ? `设为主角「${char.name}」?`
      : `设为配角「${char.name}」?`,
    message: becomeProtagonist
      ? "添加到主角列表 · 锁定后 AI 重判不会覆盖。"
      : "从主角列表移除 · 锁定后 AI 重判不会再把 ta 标为主角。",
    danger: false,
    confirmLabel: becomeProtagonist ? "设为主角" : "设为配角",
  });
  if (!ok) return;
  try {
    await api.patch<Character>(`/characters/${char.id}`, {
      is_protagonist: becomeProtagonist,
      protagonist_user_pinned: true,
    });
    toast.success(becomeProtagonist ? `已设为主角:${char.name}` : `已设为配角:${char.name}`);
    await loadAll();
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "更新失败");
  }
}

/**
 * Sprint 6.A2 FOCUS.8(2026-05-22):删除角色(真删,后端级联清 relationships + events.participants)
 *
 * 二次确认走 useConfirm(中央 modal + danger 红按钮)。
 * 后端 DELETE /characters/:id 已实现 CASCADE,无需前端做级联。
 */
async function deleteCharacter(char: Character, event: MouseEvent) {
  event.stopPropagation();
  const ok = await confirmDialog({
    title: `删除角色「${char.name}」?`,
    message:
      "该角色及其所有关系会被永久删除,事件中的参与者引用也会一并清除。无法恢复。",
    danger: true,
    confirmLabel: "删除",
  });
  if (!ok) return;
  try {
    await api.delete(`/characters/${char.id}`);
    toast.success(`已删除:${char.name}`);
    expandedCardId.value = null;
    menuOpenCardId.value = null;
    await loadAll();
  } catch (e) {
    toast.error(e instanceof ApiError ? `删除失败:${e.message}` : "删除失败");
  }
}

/** 点 ▸ 按钮展开/折叠 — UI 优化(2026-05-21 十一轮):同时只展开 1 张,点别的卡自动换 */
function toggleCardExpand(charId: string) {
  expandedCardId.value = expandedCardId.value === charId ? null : charId;
}

/**
 * Sprint 6.A2 FOCUS(2026-05-21,fix v2)— 整张卡片的 click 兜底。
 *
 * 必须挂在 <li class="char-card"> 上,而不是仅 ▸ 按钮 — 用户合并时希望"点候选 target 卡片
 * 主体"就触发合并,不需要精确点到 ▸。
 *
 * 行为:
 *   - 非合并模式:不干预任何交互(立即 return,卡片内部展开按钮 / 菜单按钮 / chip 各自处理)
 *   - 合并模式 + 点 source 卡本身:退出合并模式
 *   - 合并模式 + 点候选 target 卡:触发后端合并
 *
 * 关键:卡片内子元素(⋯ 菜单 / ▸ 按钮 / 关系子卡 chip)已 @click.stop 或包在 .stop 容器里
 * (除展开 ▸ 按钮外),冒泡到 li 时只会是"卡片主体空白区"的点击。
 *
 * 修 bug:翻页后点击 target 卡没反应 — 因为分页器在 ▸ 按钮之外的卡片区域无 click handler。
 */
function onCardClick(charId: string, event: Event) {
  if (!mergeFromCharId.value) return;   // 非合并模式 → 不抢用户其他点击意图
  event.stopPropagation();
  if (mergeFromCharId.value === charId) {
    mergeFromCharId.value = null;
    toast.info("已退出合并模式");
    return;
  }
  void confirmMerge(charId);
}

/**
 * Sprint 6.A2 FOCUS(2026-05-21):启动合并模式。
 * 用户从 "⋯" 菜单选"合并到其他角色" → 该卡变 source → toast 提示点击 target
 */
function startMerge(char: Character) {
  if (mergeFromCharId.value === char.id) {
    mergeFromCharId.value = null;
    return;
  }
  mergeFromCharId.value = char.id;
  toast.info(
    `合并模式:点击另一张角色卡作为合并目标(再次点击「${char.name}」取消)`,
    6000,
  );
}

/**
 * Sprint 6.A2 FOCUS(2026-05-21):用户点击 target 卡 → 调后端 merge。
 * 后端会:把 source.aliases + source.name 累积到 target.aliases / reassign 关系 / 删 source。
 */
async function confirmMerge(targetCharId: string) {
  if (!mergeFromCharId.value || operating.value) return;
  const sourceId = mergeFromCharId.value;
  if (sourceId === targetCharId) {
    mergeFromCharId.value = null;
    return;
  }
  const srcChar = allCharacters.value.find((c) => c.id === sourceId);
  const tgtChar = allCharacters.value.find((c) => c.id === targetCharId);
  if (!srcChar || !tgtChar) return;

  operating.value = true;
  try {
    await api.post<Character>(
      `/projects/${props.projectId}/characters/merge`,
      {
        source_character_id: sourceId,
        target_character_id: targetCharId,
      },
    );
    toast.success(`已合并「${srcChar.name}」到「${tgtChar.name}」`);
    mergeFromCharId.value = null;
    await loadAll();
  } catch (e) {
    toast.error(
      e instanceof ApiError ? `合并失败:${e.message}` : "合并失败",
    );
  } finally {
    operating.value = false;
  }
}

/** UI 优化(2026-05-21):切换 "⋯" 更多菜单(每张卡独立) */
function toggleCardMenu(charId: string, event: Event) {
  event.stopPropagation();
  menuOpenCardId.value = menuOpenCardId.value === charId ? null : charId;
}

/** 点击页面任意空白处关闭打开的菜单 */
function closeCardMenu() {
  menuOpenCardId.value = null;
}

/** 关系子卡的时间轴展开/折叠 */
function toggleRelExpand(relId: string, event: Event) {
  event.stopPropagation();
  if (expandedRelIds.value.has(relId)) {
    expandedRelIds.value.delete(relId);
  } else {
    expandedRelIds.value.add(relId);
  }
  expandedRelIds.value = new Set(expandedRelIds.value);
}

/**
 * Sprint 6.A2 M1+++ bug fix v2(2026-05-18 用户反馈"闪现"):
 *
 * 原修法(v1):loadAll 全量重拉 + 保存/恢复 scrollTop
 *   → 仍闪烁:Vue 看到 reactive 数组替换 → v-for 列表 unmount/remount
 *   → DOM 高度短暂归零 → scrollTop 跳顶部 → nextTick 恢复 = 视觉闪现
 *
 * 修法(v2):**完全不重拉,细粒度只改变化的那一条**
 *   - RelationshipTimeline emit 时已带 payload(新 phaseCount + 新 currentPhaseId)
 *   - 这里只更新 phaseCounts Map 中一个 entry + allRelationships 中一行(immutable update)
 *   - protagonists / allCharacters 完全不动 → 主角卡列表不重建 → 0 闪烁
 */
function onPhasesChanged(payload: {
  relationshipId: string;
  phaseCount: number;
  currentPhaseId: string | null | undefined;
}) {
  // 1. 单条 phaseCount 更新(改 Map 一个 entry,Vue 触发 chip 局部更新)
  const newCounts = new Map(phaseCounts.value);
  newCounts.set(payload.relationshipId, payload.phaseCount);
  phaseCounts.value = newCounts;

  // 2. 单条 current_phase_id 更新(undefined 表示不动)
  if (payload.currentPhaseId !== undefined) {
    const idx = allRelationships.value.findIndex(
      (r) => r.id === payload.relationshipId,
    );
    if (idx >= 0) {
      // immutable update — Vue 只把这一行视为变了,旁边的 li 不动
      const newRels = [...allRelationships.value];
      newRels[idx] = {
        ...newRels[idx],
        current_phase_id: payload.currentPhaseId,
      };
      allRelationships.value = newRels;
    }
  }
}

/** 给定主角,找其所有 relationships(source 或 target 等于其 id),
 * 按强度降序排;hideWeakRels 开时过滤掉 weak / moderately_weak。*/
function relsForCharacterAll(charId: string): Relationship[] {
  let rels = allRelationships.value.filter(
    (r) => r.source_id === charId || r.target_id === charId,
  );
  if (hideWeakRels.value) {
    rels = rels.filter(
      (r) => r.strength !== "weak" && r.strength !== "moderately_weak",
    );
  }
  // 按强度降序(strong 在前)
  return [...rels].sort(
    (a, b) =>
      (_STRENGTH_RANK[b.strength] ?? 0) - (_STRENGTH_RANK[a.strength] ?? 0),
  );
}

/** 按"是否展开全部"决定真正渲染的关系子集 */
function relsForCharacterVisible(charId: string): Relationship[] {
  const all = relsForCharacterAll(charId);
  if (expandedRelLists.value.has(charId)) return all;
  return all.slice(0, RELS_DEFAULT_LIMIT);
}

function toggleRelListExpand(charId: string, event: Event) {
  event.stopPropagation();
  if (expandedRelLists.value.has(charId)) {
    expandedRelLists.value.delete(charId);
  } else {
    expandedRelLists.value.add(charId);
  }
  expandedRelLists.value = new Set(expandedRelLists.value);
}

/** 给定主角 + 关系,返回"对端"角色名(另一头) */
function otherEndName(charId: string, rel: Relationship): string {
  const otherId = rel.source_id === charId ? rel.target_id : rel.source_id;
  return charById.value.get(otherId)?.name ?? "?";
}

const isEmpty = computed(
  () => !loading.value && visibleCharacters.value.length === 0,
);

// UI 优化(2026-05-21 三轮):分页 — 总页数 + 当前页切片 + 越界自动归位
// FOCUS.8(2026-05-22):分页 source 改为 visibleCharacters(随 viewMode 切换)
const protagTotalPages = computed(
  () => Math.max(1, Math.ceil(visibleCharacters.value.length / PROTAG_PAGE_SIZE)),
);
const protagPaged = computed(() => {
  const start = (protagPage.value - 1) * PROTAG_PAGE_SIZE;
  return visibleCharacters.value.slice(start, start + PROTAG_PAGE_SIZE);
});
// 列表长度变化时(如重新判定 / 视图切换),若当前页超出总页数则回到末页
watch(
  () => visibleCharacters.value.length,
  () => {
    if (protagPage.value > protagTotalPages.value) {
      protagPage.value = protagTotalPages.value;
    }
  },
);
function goToPage(p: number) {
  if (p < 1 || p > protagTotalPages.value) return;
  protagPage.value = p;
  // 折叠展开的卡(避免翻页后保留旧展开状态)
  expandedCardId.value = null;
  menuOpenCardId.value = null;
}

// Bug 修复(2026-05-22):违反 CLAUDE.md 开发避坑铁律 #1 — 原 onMounted(loadAll) 在
// ProjectView 切项目复用实例时不会重跑(props.projectId 变了但 load 不触发)。
// 当前依赖 tab 切换 v-if unmount/remount 偶然救场;改 watch immediate 让语义稳健。
watch(() => props.projectId, loadAll, { immediate: true });

/**
 * UI 优化(2026-05-21 十一轮)— 手风琴联动:
 *   - toggleSection 切换 sectionCollapsed,展开时 emit('expanded')
 *   - 父组件接收 expanded 事件后调另一个 section 的 collapseSection() 把它折叠
 *   - 父组件不持有 sectionCollapsed 状态,只做"互斥"协调
 */
function toggleSection() {
  sectionCollapsed.value = !sectionCollapsed.value;
  if (!sectionCollapsed.value) {
    emit("expanded");
  }
}
function collapseSection() {
  sectionCollapsed.value = true;
}
defineExpose({ collapseSection });

/**
 * Sprint 6.A2 FOCUS.6(2026-05-22):中间/末尾态可编辑 behavior_baseline。
 * 用户在 ProtagonistWall 卡片展开后,改 4 维任一字段触发 PATCH /characters/:id。
 * 容错:LLM 推荐的值如有非法格式,服务端会拒,toast 报错;不更新前端值。
 */
async function updateBaselineForChar(char: Character, next: BehaviorBaseline | null) {
  // 立即本地乐观更新(让 UI 即时响应)— 失败时下面再 reload 矫正
  (char as Character).behavior_baseline = next;
}

async function saveBaselineForChar(char: Character) {
  try {
    const updated = await api.patch<Character>(`/characters/${char.id}`, {
      behavior_baseline: char.behavior_baseline ?? null,
    });
    // 服务端返回的可能是 normalize 后的值(如空字段被置 null),写回本地保持一致
    (char as Character).behavior_baseline = updated.behavior_baseline ?? null;
  } catch (e) {
    toast.error(
      e instanceof Error ? `保存行为基线失败:${e.message}` : "保存行为基线失败",
    );
    // 失败 → 重拉本角色保证一致(不重拉整 list,只 GET 该角色)
    try {
      const fresh = await api.get<Character>(`/characters/${char.id}`);
      (char as Character).behavior_baseline = fresh.behavior_baseline ?? null;
    } catch {
      /* 即使重拉也失败 → 静默,用户下次刷新会同步 */
    }
  }
}
</script>

<template>
  <section
    class="protag-wall"
    :aria-busy="loading || judging"
    aria-live="polite"
    @click="closeCardMenu"
  >
    <header class="wall-header">
      <button
        type="button"
        class="collapse-btn"
        :aria-expanded="!sectionCollapsed"
        :aria-label="sectionCollapsed ? '展开主角列表' : '折叠主角列表'"
        @click="toggleSection"
      >
        <Icon :name="sectionCollapsed ? 'chevron_right' : 'chevron_down'" :size="14" />
      </button>
      <div class="wall-title-block">
        <h3 class="wall-title">
          <!-- 2026-06-08 UI 升级:🎭 → SVG users(主角群) -->
          <Icon name="character" :size="16" class="title-icon" />
          {{ VIEW_MODE_LABEL[viewMode] }}
          <span class="title-count mono" v-if="!loading">
            {{ visibleCharacters.length }}
          </span>
          <!--
            Sprint 6.A2 FOCUS.8(2026-05-22):视图切换器 — 3 态循环
            protagonist → supporting → all → protagonist
            SVG 用细线 swap/arrow loop 暗示循环切换。
          -->
          <button
            type="button"
            class="view-mode-toggle"
            :title="VIEW_MODE_NEXT_TITLE[viewMode]"
            :aria-label="VIEW_MODE_NEXT_TITLE[viewMode]"
            @click.stop="cycleViewMode"
          >
            <svg
              viewBox="0 0 24 24" width="14" height="14"
              fill="none" stroke="currentColor" stroke-width="1.5"
              stroke-linecap="round" stroke-linejoin="round"
              aria-hidden="true"
            >
              <!-- 双弧箭头(↻ 循环切换的语义) -->
              <path d="M21 12a9 9 0 0 1-9 9 9 9 0 0 1-7-3.5" />
              <polyline points="21 18 21 22 17 22" />
              <path d="M3 12a9 9 0 0 1 9-9 9 9 0 0 1 7 3.5" />
              <polyline points="3 6 3 2 7 2" />
            </svg>
          </button>
        </h3>
      </div>
      <div v-if="!sectionCollapsed" class="header-actions">
        <!--
          UI 优化(2026-05-21 三轮):删除"已隐弱关系/全部强度"切换按钮
            - 该按钮虽逻辑层面有效,但过滤的是"展开主角卡后看到的关系子卡",
              而非"主角列表"。折叠态下用户看不到任何变化 → 误判为假
            - 简化:hideWeakRels 永远为 true(默认隐弱),消除用户疑虑
          原 wall-sub 文案"AI 按 4 维度判定..."也一并删除(主界面状态卡已说明)
        -->
        <button
          type="button"
          class="ghost-btn rejudge-btn"
          :disabled="judging"
          :aria-label="judging ? '正在重新判定' : '让 AI 重新判定主角'"
          @click="reJudge"
        >
          <span v-if="judging" class="spinner" aria-hidden="true"></span>
          <Icon v-else name="spark" :size="13" />
          <span>{{ judging ? "判定中…" : "重新判定" }}</span>
        </button>
      </div>
    </header>

    <!-- 折叠态:只显标题,不显内容 -->
    <template v-if="!sectionCollapsed">
      <!-- Loading -->
      <div v-if="loading" class="wall-state">
        <p>加载主角列表中…</p>
      </div>

      <!-- Error -->
      <div v-else-if="error" class="wall-state wall-state-error">
        <p>{{ error }}</p>
        <button class="ghost-btn" @click="loadAll">重试</button>
      </div>

      <!-- Empty -->
      <div v-else-if="isEmpty" class="wall-state wall-state-empty">
        <p class="empty-title">还没判定过主角</p>
        <p class="empty-hint">
          中间态 / 末尾态导入作品后,后端抽完图谱会自动判定。
          若你手动加了角色或想强制重判,点击右上方「重新判定」。
        </p>
      </div>

      <!-- 主角卡片墙(UI 优化 2026-05-21:protagPaged 分页切片)
           Sprint 6.A2 polish(2026-05-22):普通 ul,翻页时新旧 6 li reactive swap,
           wall-grid 高度稳定不跳位,scrollY 不被浏览器 clamp 到顶 -->
      <ul v-else class="wall-grid">
        <li
          v-for="char in protagPaged"
          :key="char.id"
          class="char-card"
          :class="{
            'is-pinned': char.protagonist_user_pinned,
            'is-expanded': expandedCardId === char.id,
            'is-merge-source': mergeFromCharId === char.id,
            'is-merge-target-candidate': mergeFromCharId && mergeFromCharId !== char.id,
          }"
          @click="onCardClick(char.id, $event)"
        >
          <!-- 卡片头部(基础信息 + 2 个独立按钮)-->
          <header class="card-head">
            <div class="card-head-main">
              <span class="char-name">{{ char.name }}</span>
              <!--
                UI 优化(2026-05-21):紫色数字加 "AI" 前缀让含义自显("AI 70" = AI 综合评分 70 分),
                不再依赖用户 hover 才能知道。完整解释保留在 title tooltip 兜底
              -->
              <span class="score-chip mono" :title="`AI 综合评分 ${(char.protagonist_score * 100).toFixed(0)}/100(4 维度判定:贯穿章节 · 与主角共场 · 关键事件参与 · 名字权重)`">
                <span class="score-chip-label" aria-hidden="true">AI</span>
                {{ (char.protagonist_score * 100).toFixed(0) }}
              </span>
              <span v-if="char.protagonist_user_pinned" class="pin-badge" title="你手动锁定">
                🔒
              </span>
            </div>
            <div class="card-head-actions">
              <!--
                UI 优化(2026-05-21):删除按钮收入 "⋯" 更多菜单,降低误触风险
                  - 默认只显 "⋯" 与 "▸",不直接暴露破坏性操作
                  - 点 "⋯" 弹出小菜单,展示更专业的 SVG trash 图标 + "移除主角" 文字
                  - 点 "⋯" 外任意位置关闭(由父级 @click 触发 closeCardMenu)
              -->
              <div class="card-action-menu-wrap" @click.stop>
                <button
                  type="button"
                  class="card-action-btn card-action-btn--more"
                  :class="{ 'card-action-btn--active': menuOpenCardId === char.id }"
                  title="更多操作"
                  :aria-expanded="menuOpenCardId === char.id"
                  @click="toggleCardMenu(char.id, $event)"
                >⋯</button>
                <div
                  v-if="menuOpenCardId === char.id"
                  class="card-action-menu"
                  role="menu"
                >
                  <!--
                    Sprint 6.A2 FOCUS(2026-05-21):合并到其他角色
                      给 AI 抽取归一错误兜底(如《挪威的森林》"我" vs "渡边" 被拆成两张卡)。
                      点击后该卡变 source,toast 提示点其他卡作为 target;合并完成后 source 被删,
                      target.aliases 累积 source.name + source.aliases。
                      与 SceneGraph 的场景合并语义完全对齐。
                  -->
                  <button
                    type="button"
                    class="card-menu-item"
                    role="menuitem"
                    @click="(startMerge(char), closeCardMenu())"
                  >
                    <svg
                      class="card-menu-item-icon" viewBox="0 0 24 24" width="14" height="14"
                      fill="none" stroke="currentColor" stroke-width="1.5"
                      stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
                    >
                      <circle cx="12" cy="12" r="9" />
                      <path d="M12 8v8 M8 12h8" />
                    </svg>
                    合并到其他角色
                  </button>
                  <!--
                    Sprint 6.A2 FOCUS.8(2026-05-22):菜单结构改造
                      ① 设为配角 / 设为主角(原"移除主角"扩展为双向)
                      ② 删除角色(新增,danger 红色,真删 + 二次确认)
                  -->
                  <button
                    type="button"
                    class="card-menu-item"
                    role="menuitem"
                    @click="(toggleProtagonist(char, $event), closeCardMenu())"
                  >
                    <svg
                      class="card-menu-item-icon" viewBox="0 0 24 24" width="14" height="14"
                      fill="none" stroke="currentColor" stroke-width="1.5"
                      stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
                    >
                      <!-- 五角星(主/配角切换语义) -->
                      <polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2" />
                    </svg>
                    {{ char.is_protagonist ? "设为配角" : "设为主角" }}
                  </button>
                  <button
                    type="button"
                    class="card-menu-item card-menu-item--danger"
                    role="menuitem"
                    @click="(deleteCharacter(char, $event), closeCardMenu())"
                  >
                    <svg
                      class="card-menu-item-icon" viewBox="0 0 24 24" width="14" height="14"
                      fill="none" stroke="currentColor" stroke-width="1.5"
                      stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
                    >
                      <path d="M3 6h18" />
                      <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                      <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                      <path d="M10 11v6" />
                      <path d="M14 11v6" />
                    </svg>
                    删除角色
                  </button>
                </div>
              </div>
              <button
                type="button"
                class="card-action-btn card-action-btn--expand"
                :aria-expanded="expandedCardId === char.id"
                :title="expandedCardId === char.id ? '折叠' : '展开详情'"
                @click="toggleCardExpand(char.id)"
              >
                <Icon :name="expandedCardId === char.id ? 'chevron_down' : 'chevron_right'" :size="14" />
              </button>
            </div>
          </header>

          <!-- 短身份(折叠态也能看)-->
          <p v-if="char.identity" class="char-identity">
            {{ char.identity.length > 70 ? char.identity.slice(0, 70) + "…" : char.identity }}
          </p>
          <p v-else class="char-identity char-identity-empty">
            身份未填,可点 ▸ 展开后用 "AI 补全 agent 档案"
          </p>

          <!-- 判定原因(折叠态也能看)-->
          <ul v-if="char.protagonist_reasons.length > 0" class="reasons-list">
            <li
              v-for="(reason, idx) in char.protagonist_reasons"
              :key="idx"
              class="reason-chip"
            >{{ reason }}</li>
          </ul>

          <!-- 展开区域:档案预览 + 关系时间轴 -->
          <div v-if="expandedCardId === char.id" class="card-expansion">
            <!-- 档案 4 字段精简预览 -->
            <div class="profile-preview">
              <h5 class="exp-section-title">agent 档案</h5>
              <dl class="profile-grid">
                <template v-if="char.personality">
                  <dt>性格</dt>
                  <dd>{{ char.personality.length > 100 ? char.personality.slice(0, 100) + "…" : char.personality }}</dd>
                </template>
                <template v-if="char.quotes.length > 0">
                  <dt>台词风格</dt>
                  <dd>{{ char.quotes.slice(0, 3).join("；") }}{{ char.quotes.length > 3 ? "…" : "" }}</dd>
                </template>
                <template v-if="char.no_go_list.length > 0">
                  <dt>禁忌</dt>
                  <dd>{{ char.no_go_list.slice(0, 3).join("；") }}{{ char.no_go_list.length > 3 ? "…" : "" }}</dd>
                </template>
                <template
                  v-if="!char.personality && char.quotes.length === 0 && char.no_go_list.length === 0"
                >
                  <dt>—</dt>
                  <dd class="profile-empty">档案字段全空。可在 3D 图谱点角色卡用"AI 补全 agent 档案"。</dd>
                </template>
              </dl>
            </div>

            <!--
              Sprint 6.A2 FOCUS.6(2026-05-22):中间/末尾态可编辑 behavior_baseline 4 维。
              填补"AI 自检 + AI 对焦在用 behavior_baseline,但中/末态 UI 看不见"的割裂。
              v-model 直接绑 char.behavior_baseline(props 上是 reactive Character[]);
              @save 触发后端 PATCH。
            -->
            <div class="baseline-section">
              <BehaviorBaselineEditor
                :baseline="char.behavior_baseline ?? null"
                @update:baseline="(next) => updateBaselineForChar(char, next)"
                @save="saveBaselineForChar(char)"
              />
            </div>

            <!-- 关系子列表(以该角色为 source/target 的所有关系)-->
            <!-- Sprint 6.A2 M1+++:默认前 5 条强关系;"展开全部" / 顶部全局过滤弱关系 -->
            <div class="char-relations">
              <h5 class="exp-section-title">
                关系时间轴
                <span class="exp-count mono">
                  {{ relsForCharacterVisible(char.id).length }} / {{ relsForCharacterAll(char.id).length }}
                </span>
              </h5>
              <p v-if="relsForCharacterAll(char.id).length === 0" class="rel-empty">
                这个角色没有强关系
              </p>
              <template v-else>
                <ul class="rel-sublist">
                  <li
                    v-for="rel in relsForCharacterVisible(char.id)"
                    :key="rel.id"
                    class="rel-subitem"
                    :class="{ 'is-rel-expanded': expandedRelIds.has(rel.id) }"
                  >
                    <button
                      type="button"
                      class="rel-row"
                      :aria-expanded="expandedRelIds.has(rel.id)"
                      @click="toggleRelExpand(rel.id, $event)"
                    >
                      <span class="rel-arrow" aria-hidden="true">
                        {{ rel.source_id === char.id ? "→" : "←" }}
                      </span>
                      <span class="rel-other-name">{{ otherEndName(char.id, rel) }}</span>
                      <span class="rel-type-chip">{{ rel.type }}</span>
                      <span class="rel-phases mono">
                        {{ phaseCounts.get(rel.id) ?? "?" }} 阶段
                      </span>
                      <span class="rel-expand-icon" aria-hidden="true">
                        <Icon :name="expandedRelIds.has(rel.id) ? 'chevron_down' : 'chevron_right'" :size="12" />
                      </span>
                    </button>
                    <div v-if="expandedRelIds.has(rel.id)" class="rel-timeline-wrap">
                      <RelationshipTimeline
                        :relationship-id="rel.id"
                        :current-phase-id="rel.current_phase_id"
                        @phases-changed="onPhasesChanged"
                      />
                    </div>
                  </li>
                </ul>
                <!-- "展开全部 / 收起" 按钮(只当条数 > 限制时显)-->
                <button
                  v-if="relsForCharacterAll(char.id).length > RELS_DEFAULT_LIMIT"
                  type="button"
                  class="rel-more-btn"
                  @click="toggleRelListExpand(char.id, $event)"
                >
                  <Icon :name="expandedRelLists.has(char.id) ? 'chevron_up' : 'chevron_down'" :size="12" />
                  {{ expandedRelLists.has(char.id)
                    ? `收起(只看前 ${RELS_DEFAULT_LIMIT} 条)`
                    : `展开剩余 ${relsForCharacterAll(char.id).length - RELS_DEFAULT_LIMIT} 条` }}
                </button>
              </template>
            </div>

            <!-- Sprint 6.A2 #2 二期(2026-05-22):情绪总览入口 — 跨多次推演的情绪轨迹 -->
            <div class="emotion-overview-cta">
              <button
                type="button"
                class="overview-btn"
                title="查看该角色在本项目所有 evolution 推演里的 8 维情绪轨迹"
                @click.stop="emit('open-emotion-overview', { id: char.id, name: char.name })"
              >📊 情绪总览 · 跨多次推演</button>
            </div>
          </div>
        </li>
      </ul>

      <!--
        UI 优化(2026-05-21 三轮):主角分页器 — 小而精致,仅在 ≥ 2 页时显示
        样式:« 1 2 3 » 风格,当前页高亮,边界按钮 disabled
      -->
      <nav
        v-if="!isEmpty && protagTotalPages > 1"
        class="wall-pager"
        aria-label="主角分页"
      >
        <button
          type="button"
          class="wall-pager-btn"
          :disabled="protagPage === 1"
          aria-label="上一页"
          @click="goToPage(protagPage - 1)"
        >‹</button>
        <button
          v-for="p in protagTotalPages"
          :key="p"
          type="button"
          class="wall-pager-btn wall-pager-num"
          :class="{ 'is-current': p === protagPage }"
          :aria-current="p === protagPage ? 'page' : undefined"
          @click="goToPage(p)"
        >{{ p }}</button>
        <button
          type="button"
          class="wall-pager-btn"
          :disabled="protagPage === protagTotalPages"
          aria-label="下一页"
          @click="goToPage(protagPage + 1)"
        >›</button>
      </nav>
    </template>
  </section>
</template>

<style scoped>
.protag-wall {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}

.wall-header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.collapse-btn {
  flex-shrink: 0;
  width: 28px;
  height: 28px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  cursor: pointer;
  margin-top: 2px;
}
.collapse-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.wall-title-block {
  flex: 1;
  min-width: 0;
}

.wall-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
}

.title-icon {
  font-size: var(--text-lg);
}

.title-count {
  padding: 2px 8px;
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
  margin-left: 4px;
}

/* Sprint 6.A2 FOCUS.8(2026-05-22):视图模式切换按钮 — 标题旁的循环 SVG */
.view-mode-toggle {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  margin-left: var(--space-2);
  padding: 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-muted);
  cursor: pointer;
  transition: color 120ms, border-color 120ms, background 120ms;
}
.view-mode-toggle:hover {
  color: var(--color-accent);
  border-color: var(--color-accent-border);
  background: var(--color-accent-soft);
}
.view-mode-toggle:active {
  transform: scale(0.95);
}

/* 代码屎山清理(2026-05-21):.wall-sub / .filter-toggle 已删 — wall-sub 文案已删,
   filter-toggle 切换器已删("已隐弱关系/全部强度"伪开关,治视觉一致性) */

.header-actions {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  flex-wrap: wrap;
}

.rejudge-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: spin var(--duration-spin) linear infinite;
}

.wall-state {
  padding: var(--space-5);
  text-align: center;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}

.wall-state-error {
  color: var(--color-danger);
}

.wall-state-empty .empty-title {
  font-size: var(--text-md);
  color: var(--color-text);
  margin: 0 0 var(--space-2) 0;
}

.wall-state-empty .empty-hint {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.6;
  margin: 0;
  max-width: 480px;
  margin-left: auto;
  margin-right: auto;
}

.wall-grid {
  list-style: none;
  padding: 0;
  margin: 0;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
  gap: var(--space-3);
}

.char-card {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: var(--space-3);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition: all var(--duration-fast) var(--ease-out);
}

.char-card.is-expanded {
  grid-column: 1 / -1;   /* 展开态:占满一整行 */
  background: var(--color-surface);
  border-color: var(--color-accent);
}

.char-card.is-pinned {
  background: rgba(124, 58, 237, 0.04);
  border-color: rgba(124, 58, 237, 0.3);
}

/*
 * Sprint 6.A2 FOCUS(2026-05-21)合并模式视觉 — 对齐 SceneGraph.scene-card 同名 class
 *   source 卡:黄色边框 + 浅黄底,提示"我是被合并的源"
 *   候选 target 卡:浅蓝高亮 + 显式 cursor:pointer,提示"点我作为合并目标"
 */
.char-card.is-merge-source {
  border-color: var(--color-warning);
  background: var(--color-warning-soft);
  cursor: pointer;
}

.char-card.is-merge-target-candidate {
  border-style: dashed;
  border-color: var(--color-accent);
  cursor: pointer;
}

.char-card.is-merge-target-candidate:hover {
  background: var(--color-accent-soft);
  border-style: solid;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.card-head-main {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  min-width: 0;
  flex: 1;
}

.char-name {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.score-chip {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 6px;
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
  cursor: help;
}
/* UI 优化(2026-05-21):"AI" 小标签让数字含义自显 */
.score-chip-label {
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.04em;
  opacity: 0.7;
}

.pin-badge {
  flex-shrink: 0;
  font-size: 10px;
}

.card-head-actions {
  display: inline-flex;
  gap: 4px;
  flex-shrink: 0;
}

.card-action-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.card-action-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
/* 代码屎山清理(2026-05-21):.card-action-btn--delete 已删 —
   UI.5 把 🗑 删除按钮收入 ⋯ 更多菜单,改用 .card-menu-item--danger */
.card-action-btn--expand:hover {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

/* UI 优化(2026-05-21):"⋯" 更多菜单按钮 + 弹层 */
.card-action-menu-wrap {
  position: relative;
  display: inline-block;
}
.card-action-btn--more {
  font-size: var(--text-base);
  font-weight: 700;
  letter-spacing: 1px;
  line-height: 1;
  padding-top: 2px;
}
.card-action-btn--active {
  background: var(--color-surface-hover);
  color: var(--color-text);
  border-color: var(--color-border-strong);
}
.card-action-menu {
  position: absolute;
  top: calc(100% + 4px);
  right: 0;
  z-index: 10;
  min-width: 140px;
  padding: 4px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
  animation: card-menu-in 120ms var(--ease-out, ease-out);
}
@keyframes card-menu-in {
  from { opacity: 0; transform: translateY(-2px); }
  to   { opacity: 1; transform: translateY(0); }
}
.card-menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px 10px;
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: var(--color-text);
  cursor: pointer;
  text-align: left;
  transition: background var(--duration-fast) var(--ease-out);
}
.card-menu-item:hover {
  background: var(--color-surface-hover);
}
.card-menu-item--danger {
  color: var(--color-danger);
}
.card-menu-item--danger:hover {
  background: var(--color-danger-soft);
}
.card-menu-item-icon {
  flex-shrink: 0;
  display: inline-block;
  color: currentColor;
}

.char-identity {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.5;
}

.char-identity-empty {
  color: var(--color-text-subtle);
  font-style: italic;
}

.reasons-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
}

/* UI 优化(2026-05-21):reason-chip 弱化"按钮化"
 *   - 去掉感知边框(原本 background 加微妙轮廓让它像按钮)
 *   - 降低背景不透明度从 0.08 → 0.05(更柔和)
 *   - 加左侧 · 分隔点,让它看起来像元数据 tag 而非可点击元素
 *   - 字色由 #16A34A 改为更柔和的灰绿(降低饱和度,与文本协调)
 */
.reason-chip {
  font-size: 10px;
  padding: 1px 8px 1px 6px;
  color: rgb(50, 130, 80);
  background: rgba(22, 163, 74, 0.05);
  border: none;
  border-radius: var(--radius-sm);
  white-space: nowrap;
  position: relative;
}
.reason-chip::before {
  content: "·";
  margin-right: 4px;
  opacity: 0.5;
  font-weight: 700;
}

/* ========== 卡片展开区域 ========== */
.card-expansion {
  margin-top: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px dashed var(--color-border);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.exp-section-title {
  margin: 0 0 var(--space-2) 0;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  gap: 6px;
}

.exp-count {
  padding: 1px 6px;
  font-size: 10px;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
}

/* profile-preview */
.profile-grid {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 4px var(--space-3);
  margin: 0;
  font-size: var(--text-xs);
  line-height: 1.6;
}
.profile-grid dt {
  font-weight: 600;
  color: var(--color-text-muted);
}
.profile-grid dd {
  margin: 0;
  color: var(--color-text);
  word-break: break-word;
}
.profile-empty {
  color: var(--color-text-subtle);
  font-style: italic;
}

/* 关系子列表 */
.char-relations {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.rel-empty {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-style: italic;
}
.rel-sublist {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.rel-subitem {
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  overflow: hidden;
}
.rel-subitem.is-rel-expanded {
  border-color: var(--color-accent);
}

.rel-row {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  padding: 6px var(--space-2);
  background: transparent;
  border: none;
  cursor: pointer;
  text-align: left;
  font-size: var(--text-xs);
  color: var(--color-text);
  transition: background var(--duration-fast) var(--ease-out);
}
.rel-row:hover {
  background: var(--color-surface-hover);
}

.rel-arrow {
  flex-shrink: 0;
  color: var(--color-text-subtle);
}
.rel-other-name {
  flex: 1;
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.rel-type-chip {
  flex-shrink: 0;
  padding: 1px 6px;
  font-size: 10px;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
}
.rel-phases {
  flex-shrink: 0;
  font-size: 10px;
  color: var(--color-text-muted);
}
.rel-expand-icon {
  flex-shrink: 0;
  color: var(--color-text-muted);
  font-size: 10px;
}

.rel-timeline-wrap {
  padding: var(--space-2);
  background: var(--color-surface);
  border-top: 1px solid var(--color-border);
}

/* Sprint 6.A2 M1+++:"展开剩余 N 条 / 收起" 按钮 */
.rel-more-btn {
  margin-top: 6px;
  padding: 4px 10px;
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: transparent;
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  width: 100%;
  text-align: center;
  transition: all var(--duration-fast) var(--ease-out);
}
.rel-more-btn:hover {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
}

/* ========== UI 优化(2026-05-21 三轮):主角分页器 — 小而精致 ========== */
.wall-pager {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: 4px;
  margin-top: var(--space-4);
  padding: 4px 0;
}
.wall-pager-btn {
  min-width: 26px;
  height: 26px;
  padding: 0 6px;
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  line-height: 1;
}
.wall-pager-btn:hover:not(:disabled):not(.is-current) {
  background: var(--color-surface-hover);
  color: var(--color-text);
  border-color: var(--color-border-strong);
}
.wall-pager-btn:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
.wall-pager-btn.is-current {
  background: var(--color-accent);
  color: white;
  border-color: var(--color-accent);
  font-weight: 600;
  cursor: default;
}
.wall-pager-num {
  font-family: var(--font-mono, monospace);
}

/* Sprint 6.A2 #2 二期(2026-05-22):角色卡展开末尾 "情绪总览" 入口 */
.emotion-overview-cta {
  margin-top: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px dashed var(--color-border);
  display: flex;
  justify-content: flex-end;
}
.overview-btn {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
  transition: all var(--duration-fast) var(--ease-out);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.overview-btn:hover {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-color: var(--color-accent);
}
</style>
