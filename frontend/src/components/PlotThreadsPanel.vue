<script setup lang="ts">
/**
 * PlotThreadsPanel — SP-5.1(2026-05-28).
 *
 * 暴露 plot_threads:后端 3 个月前就在 m4_plot_tracker 抽伏笔了,
 * 但前端从未显示 —— 用户根本不知道平台一直在追踪伏笔.
 *
 * 显示三类(active / resolved / abandoned),用户可:
 *   - 标废弃(is_abandoned)— 与 resolved 互斥
 *
 * 2026-06-02 cleanup:删除"预期回收幕"编辑 + "悬空预警"
 * (用户产品决策:哪幕回收伏笔是 LLM 导演的工作,不应该用户填).
 */
import { computed, onMounted, ref, watch } from "vue";

import { api, ApiError } from "../api/client";
import type { PlotThread, PlotThreadsResponse } from "../api/types";
import { toast } from "../composables/useToast";

const props = defineProps<{
  simulationId: string;
  currentScene?: number;
}>();

const data = ref<PlotThreadsResponse | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const activeTab = ref<"active" | "resolved" | "abandoned">("active");
const editingId = ref<string | null>(null);
// 2026-06-02 cleanup:editDraft 只剩 abandoned(expected 字段已删 — 用户产品决策)
const editDraft = ref<{ abandoned: boolean }>({
  abandoned: false,
});
const saving = ref(false);

async function load() {
  if (!props.simulationId) return;
  loading.value = true;
  error.value = null;
  try {
    data.value = await api.get<PlotThreadsResponse>(
      `/simulations/${props.simulationId}/plot_threads`,
    );
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : "加载伏笔失败";
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(() => props.simulationId, load);

// 当前 tab 的全部伏笔(分页前)
const allInTab = computed<PlotThread[]>(() => {
  if (!data.value) return [];
  return data.value.threads.filter((t) => t.status === activeTab.value);
});

// F4.1(2026-06-02):分页 — 每页 10 条
const PAGE_SIZE = 10;
const currentPage = ref(1);

// 切 tab 时重置到第 1 页
watch(activeTab, () => {
  currentPage.value = 1;
});

const totalPages = computed(() => {
  const n = allInTab.value.length;
  return Math.max(1, Math.ceil(n / PAGE_SIZE));
});

const visibleThreads = computed<PlotThread[]>(() => {
  // 当前页 → slice
  const start = (currentPage.value - 1) * PAGE_SIZE;
  return allInTab.value.slice(start, start + PAGE_SIZE);
});

function goPrevPage() {
  if (currentPage.value > 1) currentPage.value -= 1;
}
function goNextPage() {
  if (currentPage.value < totalPages.value) currentPage.value += 1;
}

function priorityLabel(p: number): string {
  return p === 1 ? "主线" : p === 2 ? "次要" : "背景";
}

// 2026-06-02 cleanup:isOverdue() 删除 — 依赖已删的 expected_resolution_scene
// "悬空"概念由 must_advance(staleness 自动算)替代,无需用户介入

function startEdit(t: PlotThread) {
  editingId.value = t.id;
  editDraft.value = {
    abandoned: t.is_abandoned,
  };
}

function cancelEdit() {
  editingId.value = null;
}

async function saveEdit(t: PlotThread) {
  if (saving.value) return;
  saving.value = true;
  try {
    // 2026-06-02 cleanup:payload 只剩 is_abandoned(expected_resolution_scene 已删)
    const payload: Record<string, unknown> = {
      is_abandoned: editDraft.value.abandoned,
    };
    await api.patch<PlotThread>(
      `/simulations/${props.simulationId}/plot_threads/${t.id}`,
      payload,
    );
    editingId.value = null;
    toast.success("伏笔已更新");
    await load();
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "保存失败");
  } finally {
    saving.value = false;
  }
}

defineExpose({ reload: load });
</script>

<template>
  <section class="plot-panel">
    <header class="ph">
      <h3 class="title">伏笔账本</h3>
      <p class="subtitle">
        平台每幕自动追踪剧情伏笔(主线 / 支线 / 背景),标"预期回收幕"或"废弃"让 narrator 接近终点时优先收线。
      </p>
      <!-- F2.2(2026-06-02):告诉用户编辑真生效 + 跨代继承 -->
      <div class="ph-banner">
        <span class="ph-banner-icon">📌</span>
        <span class="ph-banner-text">
          <strong>编辑真生效</strong>:标"废弃"会让本推演的 LLM 立刻不再追;
          所有<strong>进行中</strong>的伏笔会在推演完成时自动升级到「项目跨代账本」,
          基于本篇续写的新作 outline 会主动安排回收。
        </span>
      </div>
    </header>

    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="loading && !data" class="empty">加载中…</div>

    <div v-if="data" class="content">
      <!-- 三类计数 tab -->
      <div class="tabs">
        <button
          v-for="key in (['active', 'resolved', 'abandoned'] as const)"
          :key="key"
          class="tab"
          :class="{ active: activeTab === key }"
          @click="activeTab = key"
        >
          <span>{{
            key === "active" ? "进行中" :
            key === "resolved" ? "已回收" : "已废弃"
          }}</span>
          <span class="count">{{ data.counts[key] }}</span>
        </button>
      </div>

      <ul v-if="visibleThreads.length" class="thread-list">
        <li
          v-for="t in visibleThreads"
          :key="t.id"
          class="thread"
          :class="{ 'must-advance': t.must_advance }"
        >
          <div class="thread-row">
            <span class="prio" :class="`prio-${t.priority}`">P{{ t.priority }} {{ priorityLabel(t.priority) }}</span>
            <span class="desc">{{ t.description }}</span>
            <button
              v-if="editingId !== t.id"
              class="edit-btn"
              @click="startEdit(t)"
            >编辑</button>
          </div>

          <div class="thread-meta">
            <span>引入于第 {{ t.introduced_at_scene_index + 1 }} 幕</span>
            <span v-if="t.resolved_at_scene_index !== null">
              · 回收于第 {{ t.resolved_at_scene_index + 1 }} 幕
            </span>
            <span v-if="t.staleness > 0">· 已停滞 {{ t.staleness }} 幕</span>
            <span v-if="t.must_advance" class="alert">⚠ 必须推进</span>
          </div>

          <!-- 2026-06-02 cleanup:编辑面板只剩"标为废弃"
               (预期回收幕字段删除 — 哪幕回收是 LLM 导演的工作) -->
          <div v-if="editingId === t.id" class="edit-row">
            <label class="ab-label">
              <input v-model="editDraft.abandoned" type="checkbox" />
              标为废弃(让 LLM 不再追这条线)
            </label>
            <button class="save-btn" :disabled="saving" @click="saveEdit(t)">
              {{ saving ? "保存中…" : "保存" }}
            </button>
            <button class="cancel-btn" :disabled="saving" @click="cancelEdit">
              取消
            </button>
          </div>
        </li>
      </ul>

      <!-- F4.1(2026-06-02):分页器 — 当前 tab 超过 1 页才显示 -->
      <div v-if="totalPages > 1" class="pager">
        <button
          type="button"
          class="pager-btn"
          :disabled="currentPage <= 1"
          @click="goPrevPage"
        >‹ 上一页</button>
        <span class="pager-meta mono">
          第 {{ currentPage }} / {{ totalPages }} 页 · 共 {{ allInTab.length }} 条
        </span>
        <button
          type="button"
          class="pager-btn"
          :disabled="currentPage >= totalPages"
          @click="goNextPage"
        >下一页 ›</button>
      </div>

      <div v-if="allInTab.length === 0" class="empty">
        {{
          activeTab === "active" ? "暂无进行中伏笔" :
          activeTab === "resolved" ? "暂无已回收伏笔" : "暂无已废弃伏笔"
        }}
      </div>
    </div>
  </section>
</template>

<style scoped>
.plot-panel {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4) var(--space-5);
}
.ph { margin-bottom: var(--space-3); }
.title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.subtitle {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin: var(--space-1) 0 0;
  line-height: 1.6;
}
/* F2.2(2026-06-02):跨代生效 banner */
.ph-banner {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  margin-top: var(--space-3);
  padding: var(--space-2) var(--space-3);
  background: #ecfdf5;
  border: 1px solid #6ee7b7;
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  line-height: 1.55;
  color: #065f46;
}
.ph-banner-icon {
  flex-shrink: 0;
}
.ph-banner-text strong {
  color: #047857;
  font-weight: 600;
}
.err {
  color: var(--color-danger);
  background: var(--color-danger-soft);
  padding: var(--space-2) var(--space-3);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
}
.empty {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-subtle);
  font-size: var(--text-sm);
}

/* F4.1(2026-06-02):分页器 */
.pager {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  margin-top: var(--space-3);
  padding: var(--space-2) 0;
  border-top: 1px dashed var(--color-border);
}
.pager-btn {
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text);
  transition: all var(--duration-fast) var(--ease-out);
}
.pager-btn:hover:not(:disabled) {
  border-color: var(--color-accent-border);
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
}
.pager-btn:disabled {
  opacity: 0.4;
}
.pager-meta {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  white-space: nowrap;
}
.mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}

.tabs {
  display: flex;
  gap: var(--space-1);
  border-bottom: 1px solid var(--color-border);
  margin-bottom: var(--space-3);
}
.tab {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: transparent;
  border: 0;
  border-bottom: 2px solid transparent;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin-bottom: -1px;
}
.tab:hover { color: var(--color-text); }
.tab.active {
  color: var(--color-accent);
  border-bottom-color: var(--color-accent);
}
.count {
  background: var(--color-bg-subtle);
  padding: 1px var(--space-2);
  border-radius: var(--radius-full);
  font-size: var(--text-xs);
  font-variant-numeric: tabular-nums;
}
.tab.active .count {
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
}

.thread-list { list-style: none; margin: 0; padding: 0; }
.thread {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: var(--space-2);
  background: var(--color-surface);
}
/* 2026-06-02 cleanup:.thread.overdue 删除 — isOverdue 依赖的 expected_resolution_scene 已删 */
.thread.must-advance {
  background: rgba(217, 119, 6, 0.04);
  border-color: rgba(217, 119, 6, 0.3);
}
.thread-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
}
.prio {
  font-size: var(--text-xs);
  padding: 1px var(--space-2);
  border-radius: var(--radius-sm);
  font-weight: 600;
  flex-shrink: 0;
  font-variant-numeric: tabular-nums;
}
.prio-1 { background: #fef3c7; color: #92400e; }
.prio-2 { background: #f3efff; color: #5b21b6; }
.prio-3 { background: var(--color-bg-subtle); color: var(--color-text-muted); }
.desc {
  flex: 1;
  font-size: var(--text-sm);
  color: var(--color-text);
  line-height: 1.6;
}
.edit-btn {
  font-size: var(--text-xs);
  padding: 2px var(--space-2);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
}
.edit-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.thread-meta {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  margin-top: var(--space-2);
}
.alert {
  color: var(--color-danger);
  font-weight: 500;
}

.edit-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  align-items: center;
  margin-top: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.edit-row label {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.edit-row .ab-label {
  flex-direction: row;
  gap: var(--space-1);
  align-items: center;
}
/* 2026-06-02 cleanup:.num-input 删除 — 预期回收幕输入框已删 */
.save-btn {
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border: 0;
  border-radius: var(--radius-sm);
}
.save-btn:hover:not(:disabled) { background: var(--color-accent-hover); }
.save-btn:disabled { opacity: 0.5; }
.cancel-btn {
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-xs);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text-muted);
}
</style>
