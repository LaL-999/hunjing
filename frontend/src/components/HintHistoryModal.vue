<script setup lang="ts">
/**
 * HintHistoryModal — 用户干预指令历史(2026-06-01).
 *
 * 拉 GET /api/simulations/{sid}/hint_history → 按 scene_index 升序列出.
 * 每条:
 *   [第 N 幕] 场景名 · 提交时间
 *   "用户当时输入的 hint 完整文本"
 *
 * 加载失败 → 提示一行,不阻塞用户继续创作.
 */
import { onMounted, ref } from "vue";

import { api } from "../api/client";
import { ApiError } from "../api/types";

interface HintHistoryItem {
  scene_index: number;
  scene_label: string;
  scene_name: string;
  hint: string;
  applied_at: string;
}

interface HintHistoryResponse {
  hints: HintHistoryItem[];
  total: number;
}

const props = defineProps<{
  simId: string;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

const loading = ref(true);
const error = ref<string | null>(null);
const items = ref<HintHistoryItem[]>([]);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const resp = await api.get<HintHistoryResponse>(
      `/simulations/${props.simId}/hint_history`,
    );
    items.value = resp.hints;
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : "加载干预记录失败";
  } finally {
    loading.value = false;
  }
}

onMounted(load);

function handleBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) emit("close");
}

function fmtTime(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}`;
}
</script>

<template>
  <Teleport to="body">
    <div
      class="hint-history-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="干预记录"
      @click="handleBackdrop"
      @keydown.esc="emit('close')"
    >
      <div class="hint-history-card surface">
        <header class="hh-header">
          <h2 class="hh-title">干预记录</h2>
          <button
            type="button"
            class="hh-close"
            aria-label="关闭"
            @click="emit('close')"
          >×</button>
        </header>

        <p class="hh-sub">
          本次推演中你提交过的所有干预指令,按幕排序.每条 hint 都已在对应幕生效.
        </p>

        <div v-if="loading" class="hh-state">加载中…</div>
        <div v-else-if="error" class="hh-state hh-state--err">{{ error }}</div>
        <div v-else-if="items.length === 0" class="hh-state hh-state--empty">
          本次推演还没有提交过干预指令.
          <br />
          <span class="hh-empty-hint">
            可以在主进度页下方的"边写边干预"输入框给下一幕塞一句话,提交后 AI 会承接全文走向落实.
          </span>
        </div>
        <ul v-else class="hh-list">
          <li v-for="it in items" :key="it.scene_index" class="hh-item">
            <div class="hh-meta">
              <span class="hh-scene-tag">{{ it.scene_label }}</span>
              <span class="hh-scene-name">{{ it.scene_name }}</span>
              <span class="hh-time mono">{{ fmtTime(it.applied_at) }}</span>
            </div>
            <p class="hh-hint">{{ it.hint }}</p>
          </li>
        </ul>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.hint-history-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(31, 31, 30, 0.45);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}
.hint-history-card {
  width: 100%;
  max-width: 620px;
  max-height: calc(100vh - var(--space-8));
  padding: var(--space-6);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.hh-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.hh-title {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
}
.hh-close {
  width: 28px;
  height: 28px;
  font-size: var(--text-lg);
  line-height: 1;
  background: transparent;
  border: 0;
  color: var(--color-text-muted);
  border-radius: var(--radius-full);
  cursor: pointer;
}
.hh-close:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.hh-sub {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.6;
}
.hh-state {
  padding: var(--space-6);
  text-align: center;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.hh-state--err { color: var(--color-danger); }
.hh-state--empty { color: var(--color-text-subtle); }
.hh-empty-hint {
  display: inline-block;
  margin-top: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.hh-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.hh-item {
  padding: var(--space-3) var(--space-4);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.hh-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-1);
  flex-wrap: wrap;
}
.hh-scene-tag {
  padding: 1px var(--space-2);
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-sm);
  white-space: nowrap;
}
.hh-scene-name {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.hh-time {
  margin-left: auto;
  font-size: 11px;
  color: var(--color-text-subtle);
}
.hh-hint {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--color-text);
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
