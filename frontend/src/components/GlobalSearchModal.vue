<script setup lang="ts">
/**
 * GlobalSearchModal — Sprint 6.A2 路线图 #6(2026-05-23)全局搜索弹窗。
 *
 * 触发:Cmd+K / Ctrl+K(由 App.vue 注册的快捷键)+ 左侧栏搜索按钮
 *
 * 形态:
 *   - 顶部对齐居中弹窗(15vh from top),~680px 宽
 *   - 顶部:🔍 + 输入框 + 范围 tab(全部 / 当前项目)+ Esc 提示
 *   - 中部:分组结果(6 类),每条带 icon + 主名 + 项目面包屑 + 类型标签
 *   - 键盘:↑↓ 上下,Enter 跳转,Esc 关闭
 *   - debounce 250ms;1 字符开搜
 */
import { computed, nextTick, onBeforeUnmount, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api } from "../api/client";
import {
  ApiError,
  type GlobalSearchResponse,
  type SearchCharacterItem,
  type SearchEventItem,
  type SearchProjectItem,
  type SearchSceneItem,
  type SearchSimulationItem,
} from "../api/types";
import { useGlobalSearch } from "../composables/useGlobalSearch";
import { useAuthStore } from "../stores/auth";
import { useLoginModal } from "../composables/useLoginModal";

const router = useRouter();
const search = useGlobalSearch();
const auth = useAuthStore();
const loginModal = useLoginModal();

const query = ref("");
const scope = ref<"all" | "current">("all");
const results = ref<GlobalSearchResponse | null>(null);
const loading = ref(false);
const errorMessage = ref<string | null>(null);
const selectedIndex = ref(0);

let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let currentSeq = 0;   // 序列 token,防晚到的请求覆盖最新结果

const inputRef = ref<HTMLInputElement | null>(null);
const resultsAreaRef = ref<HTMLElement | null>(null);

// 弹窗打开时:autofocus 输入框 + 重置状态
// Bug 修复(2026-05-23):打开时默认 scope=all(原默认根据路由推断为 current,
// 导致在项目 A 内 Cmd+K 搜"渡边"(在项目 B)时,被限定到项目 A 内 → 0 结果,
// 用户感觉"切 tab 结果消失")。改为始终 all,用户主动切"当前项目"才限定。
watch(
  () => search.isOpen.value,
  (open) => {
    if (open) {
      query.value = "";
      results.value = null;
      errorMessage.value = null;
      selectedIndex.value = 0;
      scope.value = "all";
      typeFilter.value = "all";
      void nextTick(() => inputRef.value?.focus());
    }
  },
);

// query / scope 变化 → debounce 250ms 后搜
watch([query, scope], () => {
  if (debounceTimer !== null) {
    clearTimeout(debounceTimer);
    debounceTimer = null;
  }
  selectedIndex.value = 0;
  const q = query.value.trim();
  if (!q) {
    results.value = null;
    loading.value = false;
    errorMessage.value = null;
    return;
  }
  debounceTimer = setTimeout(() => {
    void runSearch();
  }, 250);
});

async function runSearch() {
  const q = query.value.trim();
  if (!q) return;
  // 2026-06-02 hotfix:未登录守门 — 触发登录 modal,不发请求(防"缺少 Authorization Bearer token"漏出)
  if (!auth.isAuthed) {
    search.close();
    loginModal.open();
    return;
  }
  const mySeq = ++currentSeq;
  loading.value = true;
  errorMessage.value = null;
  try {
    const params = new URLSearchParams({ q });
    if (scope.value === "current" && search.initialScopeProjectId.value) {
      params.set("project_id", search.initialScopeProjectId.value);
    }
    const resp = await api.get<GlobalSearchResponse>(
      `/search?${params.toString()}`,
    );
    if (mySeq !== currentSeq) return;  // 已被更新请求覆盖,丢弃
    results.value = resp;
    selectedIndex.value = 0;
  } catch (e) {
    if (mySeq !== currentSeq) return;
    // 2026-06-02 hotfix:401 / 鉴权类错误 → 友好文案,不漏后端原话
    if (e instanceof ApiError && (e.status === 401 || /token|auth/i.test(e.message))) {
      errorMessage.value = "请先登录后再使用搜索";
      results.value = null;
      // 顺手触发登录引导
      search.close();
      loginModal.open();
    } else {
      errorMessage.value = e instanceof ApiError ? e.message : "搜索失败";
      results.value = null;
    }
  } finally {
    if (mySeq === currentSeq) loading.value = false;
  }
}

// ============================================================
// flat list — 用于键盘上下导航 + Enter 跳转
// ============================================================

type EntityType = "project" | "character" | "event" | "scene" | "simulation";

interface FlatItem {
  type: EntityType;
  raw: SearchProjectItem | SearchCharacterItem
       | SearchEventItem | SearchSceneItem | SearchSimulationItem;
}

// 类型筛选 tab(默认"全部" — 显所有 5 类)
const typeFilter = ref<"all" | EntityType>("all");

const flatList = computed<FlatItem[]>(() => {
  const r = results.value;
  if (!r) return [];
  const out: FlatItem[] = [];
  r.projects.forEach((p) => out.push({ type: "project", raw: p }));
  r.characters.forEach((c) => out.push({ type: "character", raw: c }));
  r.events.forEach((e) => out.push({ type: "event", raw: e }));
  r.scenes.forEach((s) => out.push({ type: "scene", raw: s }));
  r.simulations.forEach((s) => out.push({ type: "simulation", raw: s }));
  // 按 typeFilter 过滤
  if (typeFilter.value === "all") return out;
  return out.filter((f) => f.type === typeFilter.value);
});

const totalCount = computed(() => flatList.value.length);
const hasResults = computed(() => totalCount.value > 0);

// 显示用:6 类分组(空组不渲染)
interface DisplayGroup {
  type: FlatItem["type"];
  title: string;
  items: FlatItem[];
}

const TYPE_META: Record<EntityType, { title: string; icon: string; label: string }> = {
  project:      { title: "项目",      icon: "📁", label: "项目" },
  character:    { title: "角色",      icon: "👤", label: "角色" },
  event:        { title: "事件",      icon: "📅", label: "事件" },
  scene:        { title: "场景",      icon: "🎬", label: "场景" },
  simulation:   { title: "推演产物",  icon: "📚", label: "推演" },
};

// 类型筛选 tab 的 chip 选项(动态计数,让用户一眼看每类多少命中)
const typeTabOptions = computed<Array<{ value: "all" | EntityType; label: string; count: number }>>(() => {
  const r = results.value;
  const totalAll = r
    ? r.projects.length + r.characters.length + r.events.length
      + r.scenes.length + r.simulations.length
    : 0;
  return [
    { value: "all",        label: "全部", count: totalAll },
    { value: "project",    label: "项目", count: r?.projects.length ?? 0 },
    { value: "character",  label: "角色", count: r?.characters.length ?? 0 },
    { value: "event",      label: "事件", count: r?.events.length ?? 0 },
    { value: "scene",      label: "场景", count: r?.scenes.length ?? 0 },
    { value: "simulation", label: "推演", count: r?.simulations.length ?? 0 },
  ];
});

const displayGroups = computed<DisplayGroup[]>(() => {
  const r = results.value;
  if (!r) return [];
  const order: EntityType[] = [
    "project", "character", "event", "scene", "simulation",
  ];
  return order.map((t) => ({
    type: t,
    title: TYPE_META[t].title,
    items: flatList.value.filter((f) => f.type === t),
  })).filter((g) => g.items.length > 0);
});

// flat index 映射 — displayGroups 顺序与 flatList 一致,直接 indexOf
function flatIndexOf(item: FlatItem): number {
  return flatList.value.indexOf(item);
}

// ============================================================
// 键盘导航
// ============================================================

// Bug 修复(2026-05-23 B1):切 type tab 时,记住"上次选中的实体 id+type",在
// 新过滤后的 flatList 中重新定位回去;找不到才回 0。原实现暴力 selectedIndex=0,
// 用户多次切 tab 会困惑"刚才看中的那条没了"
function switchTypeFilter(newFilter: "all" | EntityType) {
  if (typeFilter.value === newFilter) return;
  // 记住当前选中的实体(切换前)
  const before = flatList.value[selectedIndex.value];
  const beforeKey = before
    ? `${before.type}-${(before.raw as { id: string }).id}`
    : null;
  typeFilter.value = newFilter;
  // 切换后 flatList 重算 → 找回之前那条;找不到就 0
  if (beforeKey) {
    const idx = flatList.value.findIndex(
      (f) => `${f.type}-${(f.raw as { id: string }).id}` === beforeKey,
    );
    selectedIndex.value = idx >= 0 ? idx : 0;
  } else {
    selectedIndex.value = 0;
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") {
    e.preventDefault();
    search.close();
    return;
  }
  if (!hasResults.value) return;
  if (e.key === "ArrowDown") {
    e.preventDefault();
    selectedIndex.value = (selectedIndex.value + 1) % totalCount.value;
    void scrollSelectedIntoView();
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    selectedIndex.value =
      (selectedIndex.value - 1 + totalCount.value) % totalCount.value;
    void scrollSelectedIntoView();
  } else if (e.key === "Enter") {
    e.preventDefault();
    const item = flatList.value[selectedIndex.value];
    if (item) jumpTo(item);
  }
}

async function scrollSelectedIntoView() {
  await nextTick();
  const el = resultsAreaRef.value?.querySelector(
    `[data-flat-idx="${selectedIndex.value}"]`,
  );
  if (el instanceof HTMLElement) {
    el.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

// ============================================================
// 跳转
// ============================================================

function jumpTo(item: FlatItem) {
  let url = "";
  switch (item.type) {
    case "project":
      url = `/projects/${(item.raw as SearchProjectItem).id}`;
      break;
    case "character":
    case "event":
    case "scene":
      // 角色/事件/场景都在图谱编辑 tab 里 → 跳项目 + tab=graph
      url = `/projects/${(item.raw as SearchCharacterItem).project_id}?tab=graph`;
      break;
    case "simulation":
      url = `/simulations/${(item.raw as SearchSimulationItem).id}`;
      break;
  }
  search.close();
  void router.push(url);
}

function handleBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) search.close();
}

// 组件卸载时清 debounce timer 防内存泄漏
onBeforeUnmount(() => {
  if (debounceTimer !== null) {
    clearTimeout(debounceTimer);
    debounceTimer = null;
  }
});

// ============================================================
// 主名 / 副名(面包屑)— 不同类型字段不同,统一抽取
// ============================================================

function getItemTitle(item: FlatItem): string {
  switch (item.type) {
    case "project":
      return (item.raw as SearchProjectItem).name;
    case "character":
      return (item.raw as SearchCharacterItem).name;
    case "event":
      return (item.raw as SearchEventItem).description || "(无描述)";
    case "scene":
      return (item.raw as SearchSceneItem).name;
    case "simulation":
      return (item.raw as SearchSimulationItem).divergence || "(无锚点)";
    default:
      return "?";
  }
}

function getItemBreadcrumb(item: FlatItem): string {
  if (item.type === "project") {
    return ""; // 项目本身就是"顶层",无面包屑
  }
  const r = item.raw as { project_name?: string };
  return r.project_name ?? "";
}

function getItemSubText(item: FlatItem): string {
  // 次要信息(浅灰 — 不抢主名视觉权重)
  if (item.type === "character") {
    return (item.raw as SearchCharacterItem).identity_excerpt;
  }
  if (item.type === "event") {
    const e = item.raw as SearchEventItem;
    return e.time_anchor ?? "";
  }
  if (item.type === "scene") {
    return (item.raw as SearchSceneItem).description;
  }
  if (item.type === "simulation") {
    const s = item.raw as SearchSimulationItem;
    const stateLabel = s.state === "done" ? "✓ 完成" : s.state;
    return stateLabel;
  }
  return "";
}

const canSwitchToCurrent = computed(
  () => !!search.initialScopeProjectId.value,
);
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="search.isOpen.value"
        class="gs-backdrop"
        @click="handleBackdrop"
        @keydown="onKeydown"
        tabindex="-1"
      >
        <div class="gs-card surface">
          <!-- 输入行 -->
          <div class="gs-input-row">
            <span class="gs-icon" aria-hidden="true">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">
                <circle cx="11" cy="11" r="7.5" />
                <path d="m20.5 20.5-4.2-4.2" />
              </svg>
            </span>
            <input
              ref="inputRef"
              v-model="query"
              type="text"
              class="gs-input"
              autocomplete="off"
              spellcheck="false"
            />
            <!-- 范围 tab(像 PyCharm 那种)— 只在有当前项目 context 时才显 -->
            <div v-if="canSwitchToCurrent" class="gs-scope-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                :class="['gs-scope-tab', { 'is-active': scope === 'all' }]"
                :aria-selected="scope === 'all'"
                @click="scope = 'all'"
              >全部项目</button>
              <button
                type="button"
                role="tab"
                :class="['gs-scope-tab', { 'is-active': scope === 'current' }]"
                :aria-selected="scope === 'current'"
                @click="scope = 'current'"
              >当前项目</button>
            </div>
            <kbd class="gs-esc-hint mono">Esc</kbd>
          </div>

          <!-- 类型筛选 tab(只在有结果时显示,带每类计数) -->
          <div v-if="results && hasResults" class="gs-type-tabs" role="tablist">
            <button
              v-for="opt in typeTabOptions"
              :key="opt.value"
              type="button"
              role="tab"
              :class="[
                'gs-type-tab',
                { 'is-active': typeFilter === opt.value, 'is-empty': opt.count === 0 },
              ]"
              :aria-selected="typeFilter === opt.value"
              :disabled="opt.count === 0 && opt.value !== 'all'"
              @click="switchTypeFilter(opt.value)"
            >
              {{ opt.label }}
              <span class="gs-type-tab-count mono">{{ opt.count }}</span>
            </button>
          </div>

          <!-- 结果区 -->
          <div ref="resultsAreaRef" class="gs-results">
            <!-- 空 query 引导 -->
            <div v-if="!query.trim()" class="gs-empty">
              <p class="gs-empty-text">输入关键字开始搜索</p>
              <p class="gs-empty-hint">
                可搜:项目名 · 角色名 · 事件 · 场景 · 推演锚点
              </p>
            </div>

            <!-- loading -->
            <div v-else-if="loading && !results" class="gs-empty">
              <p class="gs-empty-text">搜索中…</p>
            </div>

            <!-- error -->
            <div v-else-if="errorMessage" class="gs-empty gs-empty-error">
              <p class="gs-empty-text">{{ errorMessage }}</p>
            </div>

            <!-- no results -->
            <div v-else-if="results && !hasResults" class="gs-empty">
              <p class="gs-empty-text">没有匹配的结果</p>
              <p class="gs-empty-hint">试试其它关键字</p>
            </div>

            <!-- 分组结果 -->
            <template v-else-if="hasResults">
              <section
                v-for="group in displayGroups"
                :key="group.type"
                class="gs-group"
              >
                <h4 class="gs-group-title">
                  <span class="gs-group-icon">{{ TYPE_META[group.type].icon }}</span>
                  {{ group.title }}
                  <span class="gs-group-count mono">{{ group.items.length }}</span>
                </h4>
                <ul class="gs-item-list">
                  <li
                    v-for="item in group.items"
                    :key="`${item.type}-${(item.raw as { id: string }).id}`"
                    class="gs-item"
                    :class="{ 'is-selected': flatIndexOf(item) === selectedIndex }"
                    :data-flat-idx="flatIndexOf(item)"
                    @click="jumpTo(item)"
                    @mouseenter="selectedIndex = flatIndexOf(item)"
                  >
                    <div class="gs-item-main">
                      <div class="gs-item-title">{{ getItemTitle(item) }}</div>
                      <div class="gs-item-meta">
                        <span
                          v-if="getItemBreadcrumb(item)"
                          class="gs-item-breadcrumb"
                        >《{{ getItemBreadcrumb(item) }}》</span>
                        <span
                          v-if="getItemSubText(item)"
                          class="gs-item-sub"
                        >{{ getItemSubText(item) }}</span>
                      </div>
                    </div>
                    <span class="gs-item-type-label">
                      {{ TYPE_META[item.type].label }}
                    </span>
                  </li>
                </ul>
              </section>
            </template>
          </div>

          <!-- 底部提示 -->
          <footer v-if="hasResults" class="gs-footer mono">
            <span><kbd>↑</kbd><kbd>↓</kbd> 选择</span>
            <span><kbd>↵</kbd> 跳转</span>
            <span><kbd>Esc</kbd> 关闭</span>
          </footer>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.gs-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(31, 31, 30, 0.45);
  backdrop-filter: blur(4px);
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 15vh;
  padding-left: var(--space-4);
  padding-right: var(--space-4);
  z-index: var(--z-modal-backdrop);
  outline: none;
}

.gs-card {
  width: 100%;
  max-width: 680px;
  max-height: 70vh;
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  border: 1px solid var(--color-border);
}

/* ============= 输入行 ============= */
.gs-input-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--color-border);
  background: var(--color-surface);
}
.gs-icon {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-subtle);
}
.gs-input {
  flex: 1;
  min-width: 0;
  border: none;
  outline: none;
  background: transparent;
  font-size: var(--text-md);
  color: var(--color-text);
  font-family: var(--font-sans);
}
.gs-input::placeholder {
  color: var(--color-text-subtle);
}

/* 范围 tab */
.gs-scope-tabs {
  display: inline-flex;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  padding: 2px;
  flex-shrink: 0;
}
.gs-scope-tab {
  padding: 4px 10px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  border-radius: var(--radius-sm);
  transition: all var(--duration-fast) var(--ease-out);
}
.gs-scope-tab:hover {
  color: var(--color-text);
}
.gs-scope-tab.is-active {
  background: var(--color-surface);
  color: var(--color-accent-text);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.06);
}

.gs-esc-hint {
  flex-shrink: 0;
  padding: 2px 6px;
  font-size: 11px;
  color: var(--color-text-subtle);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
}

/* ============= 类型筛选 tab(小型 chip 风) ============= */
.gs-type-tabs {
  display: flex;
  gap: 4px;
  padding: 6px var(--space-4);
  border-bottom: 1px solid var(--color-border-subtle);
  background: var(--color-surface);
  overflow-x: auto;
}
.gs-type-tab {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 12px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  border-radius: 999px;
  transition: all var(--duration-fast) var(--ease-out);
  white-space: nowrap;
}
.gs-type-tab:hover:not(:disabled) {
  color: var(--color-text);
  background: var(--color-bg-subtle);
}
.gs-type-tab.is-active {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  font-weight: 500;
}
.gs-type-tab.is-empty {
  opacity: 0.45;
}
.gs-type-tab:disabled {
  cursor: not-allowed;
}
.gs-type-tab-count {
  font-size: 10px;
  padding: 0 5px;
  border-radius: 999px;
  background: var(--color-bg-subtle);
  color: var(--color-text-subtle);
  min-width: 16px;
  text-align: center;
}
.gs-type-tab.is-active .gs-type-tab-count {
  background: var(--color-surface);
  color: var(--color-accent-text);
}

/* ============= 结果区 ============= */
.gs-results {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-2) 0;
}

.gs-empty {
  padding: var(--space-8) var(--space-4);
  text-align: center;
}
.gs-empty-text {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}
.gs-empty-hint {
  margin: var(--space-2) 0 0;
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.gs-empty-error .gs-empty-text {
  color: var(--color-danger);
}

.gs-group {
  display: flex;
  flex-direction: column;
}
.gs-group + .gs-group {
  border-top: 1px solid var(--color-border-subtle);
}

.gs-group-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  padding: var(--space-2) var(--space-4);
  font-size: 11px;
  font-weight: 600;
  color: var(--color-text-subtle);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}
.gs-group-icon {
  font-size: var(--text-xs);
}
.gs-group-count {
  color: var(--color-text-subtle);
  font-weight: 400;
}

.gs-item-list {
  list-style: none;
  margin: 0;
  padding: 0 var(--space-2);
  display: flex;
  flex-direction: column;
}

.gs-item {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 8px var(--space-3);
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) var(--ease-out);
  min-width: 0;
}
.gs-item.is-selected {
  background: var(--color-accent-soft);
}

.gs-item-main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.gs-item-title {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.gs-item-meta {
  display: flex;
  align-items: baseline;
  gap: 6px;
  font-size: 11px;
  color: var(--color-text-subtle);
  min-width: 0;
}
.gs-item-breadcrumb {
  flex-shrink: 0;
  color: var(--color-accent-text);
}
.gs-item-sub {
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  min-width: 0;
}
.gs-item-type-label {
  flex-shrink: 0;
  padding: 2px 6px;
  font-size: 10px;
  color: var(--color-text-subtle);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
}

/* ============= 底部 ============= */
.gs-footer {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: var(--space-4);
  padding: 6px var(--space-4);
  font-size: 11px;
  color: var(--color-text-subtle);
  border-top: 1px solid var(--color-border-subtle);
  background: var(--color-bg-subtle);
}
.gs-footer kbd {
  display: inline-block;
  padding: 1px 5px;
  margin-right: 3px;
  font-size: 10px;
  color: var(--color-text-muted);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 3px;
  font-family: var(--font-mono);
}

/* ============= 进入 / 退出动画 ============= */
.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity var(--duration-base) var(--ease-out);
}
.modal-fade-enter-active .gs-card,
.modal-fade-leave-active .gs-card {
  transition: transform var(--duration-base) var(--ease-out);
}
.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}
.modal-fade-enter-from .gs-card,
.modal-fade-leave-to .gs-card {
  transform: translateY(-8px);
}
</style>
