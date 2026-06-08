<script setup lang="ts">
/**
 * StyleVotingDialog — D.9 Sprint 2.B 漫画态画风 3 选 1 投票弹窗
 *  (Sprint 2.B+ 七修 2026-05-12:5 张候选 → 3 张,控成本 + 减选择疲劳)
 *
 * 用户从画风定调员 v2 出的 3 张候选样张中选 1 张,提交触发 _agent_character_anchor
 * (角色锚定员)。
 *
 * UX:
 *   - 网格 3 张图(响应式:大屏 3 列,中小屏 1-2 列)
 *   - 点图卡选中 → 主紫色边框 + 角标
 *   - 单张生成失败显错误提示,该张不可选
 *   - 全部失败 → 显"重新分析"提示
 *
 * a11y:role="dialog" + aria-modal + Tab 切候选 + Enter 选中
 */
import { computed, ref, watch } from "vue";

import type { ComicCandidateImage } from "../api/types";
import { toast } from "../composables/useToast";

const props = defineProps<{
  open: boolean;
  candidates: ComicCandidateImage[];
  styleTag: string | null;     // LLM 自由命名的简短 tag
  loading?: boolean;
  /**
   * Sprint 5.x bug fix(2026-05-14):动态 loading 文案
   * 父按 comic.state + progress_percent 算出"当前正在做什么"传进来,空时用静态兜底
   */
  loadingMessage?: string;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "vote", selectedIndex: number): void;
  /** Sprint 5.10+(2026-05-14):重试 Stage 3 出新的 3 张候选(不取消漫画) */
  (e: "retry"): void;
}>();

const selectedIndex = ref<number | null>(null);

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) selectedIndex.value = null;
  },
);

function selectCandidate(index: number, hasImage: boolean) {
  if (!hasImage) {
    toast.warning("该候选生成失败,请选其他张");
    return;
  }
  selectedIndex.value = index;
}

const canSubmit = computed<boolean>(() => {
  if (props.loading) return false;
  return selectedIndex.value !== null;
});

const validCount = computed<number>(() =>
  props.candidates.filter((c) => c.image_url).length,
);

/** Sprint 5.10+:全失败时,提取候选 error 字段供 dialog 显示具体错误原因 */
const allFailedErrors = computed<string[]>(() => {
  if (validCount.value > 0) return [];
  // 去重 + 限 3 条(避免 dialog 撑爆)
  const errs = props.candidates
    .map((c) => c.error || "")
    .filter((e) => e);
  return Array.from(new Set(errs)).slice(0, 3);
});

function handleRetry() {
  if (props.loading) return;
  emit("retry");
}

function handleVote() {
  if (selectedIndex.value === null) {
    toast.warning("请先选 1 张画风");
    return;
  }
  emit("vote", selectedIndex.value);
}

function handleClose() {
  if (props.loading) {
    toast.info("正在锚定角色形象,请等待完成...");
    return;
  }
  emit("close");
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape" && props.open) handleClose();
}
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="dialog-backdrop"
        role="presentation"
        @click.self="handleClose"
        @keydown="onKeydown"
      >
        <div
          class="dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="vote-title"
        >
          <header class="dialog-header">
            <div>
              <h2 id="vote-title" class="dialog-title">选 1 张画风</h2>
              <p v-if="styleTag" class="dialog-subtitle">画风:{{ styleTag }}</p>
            </div>
            <button
              type="button"
              class="close-btn"
              aria-label="关闭"
              :disabled="loading"
              @click="handleClose"
            >×</button>
          </header>

          <div class="dialog-body">
            <!-- Sprint 5.10+(2026-05-14):3 张全失败时显示具体错误 + "重试"按钮 -->
            <div v-if="validCount === 0" class="empty-state">
              <p class="empty-title">⚠️ 3 张候选全部生成失败</p>
              <div v-if="allFailedErrors.length > 0" class="empty-errors">
                <p class="empty-errors-label">vendor 返回的具体错误:</p>
                <ul>
                  <li v-for="(err, idx) in allFailedErrors" :key="idx">
                    <code>{{ err }}</code>
                  </li>
                </ul>
              </div>
              <p class="empty-action-hint">
                可能原因:vendor 限流 / 内容审核 / prompt 含敏感词。
                点下方「🔄 重新出 3 张候选」**只重画样张**,不重做画风分析(省时间)。
              </p>
            </div>

            <div v-else class="candidates-grid">
              <button
                v-for="cand in candidates"
                :key="cand.index"
                type="button"
                class="candidate-card"
                :class="{
                  'is-selected': selectedIndex === cand.index,
                  'is-failed': !cand.image_url,
                }"
                :disabled="!cand.image_url || loading"
                :aria-label="`选 ${cand.variant_hint}`"
                :aria-pressed="selectedIndex === cand.index"
                @click="selectCandidate(cand.index, !!cand.image_url)"
              >
                <div class="card-image-box">
                  <img
                    v-if="cand.image_url"
                    :src="cand.image_url"
                    :alt="`候选 ${cand.index}`"
                    class="card-image"
                    loading="lazy"
                  />
                  <div v-else class="card-image-error">
                    生成失败
                  </div>
                  <span
                    v-if="selectedIndex === cand.index"
                    class="select-badge"
                    aria-hidden="true"
                  >✓ 选中</span>
                </div>
                <div class="card-meta">
                  <span class="card-index mono">{{ cand.index }}/3</span>
                  <span class="card-hint">{{ cand.variant_hint }}</span>
                </div>
              </button>
            </div>

            <p v-if="loading" class="loading-note">
              <span class="hourglass" aria-hidden="true">⏳</span>
              {{ loadingMessage || "正在用所选画风出角色立绘卡(预计 30-60 秒)..." }}
            </p>
          </div>

          <footer class="dialog-footer">
            <!-- Sprint 5.10+(2026-05-14):全失败时右下显主 CTA "重新出 3 张" 替代灰色"确定" -->
            <button
              type="button"
              class="ghost-btn"
              :disabled="loading"
              @click="handleClose"
            >稍后再选</button>
            <button
              v-if="validCount === 0"
              type="button"
              class="primary-btn retry-btn"
              :disabled="loading"
              @click="handleRetry"
            >
              <span aria-hidden="true">🔄</span>
              {{ loading ? "重新出图中…" : "重新出 3 张候选" }}
            </button>
            <button
              v-else
              type="button"
              class="primary-btn"
              :disabled="!canSubmit"
              @click="handleVote"
            >
              {{ loading ? "锚定中…" : "确定使用这个画风" }}
            </button>
          </footer>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.dialog-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal);
  padding: var(--space-4);
}

.dialog {
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  max-width: 1080px;
  width: 100%;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.dialog-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border);
}

.dialog-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}

.dialog-subtitle {
  margin: 4px 0 0;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}

.close-btn {
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  color: var(--color-text-muted);
  background: transparent;
  border-radius: var(--radius-sm);
  flex-shrink: 0;
}

.close-btn:hover:not(:disabled) {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.close-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.dialog-body {
  padding: var(--space-5);
  overflow-y: auto;
  flex: 1;
}

.empty-state {
  padding: var(--space-5) var(--space-6);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: color-mix(in srgb, var(--color-warning, #d97706) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--color-warning, #d97706) 30%, transparent);
  border-radius: var(--radius-md);
}
.empty-state .empty-title {
  font-weight: 600;
  color: var(--color-warning, #d97706);
  margin: 0 0 var(--space-3) 0;
  font-size: var(--text-base);
}
.empty-state .empty-errors {
  margin: var(--space-3) 0;
  padding: var(--space-3);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
}
.empty-state .empty-errors-label {
  margin: 0 0 var(--space-2) 0;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.empty-state .empty-errors ul {
  margin: 0;
  padding-left: var(--space-5);
}
.empty-state .empty-errors li {
  margin: var(--space-1) 0;
  word-break: break-all;
}
.empty-state .empty-errors code {
  font-family: var(--font-mono, monospace);
  font-size: 0.85em;
  color: var(--color-danger);
}
.empty-state .empty-action-hint {
  margin: var(--space-3) 0 0 0;
  color: var(--color-text-muted);
  line-height: 1.5;
}
.retry-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
}

/* Sprint 2.B+ 七修(2026-05-12):3 张候选(原 5 张)
 * - 大屏 3 列(每张更大,选择更清晰)
 * - 中屏 2 列(平板)
 * - 小屏 1 列(手机) */
.candidates-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-4);
}

@media (max-width: 720px) {
  .candidates-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 480px) {
  .candidates-grid {
    grid-template-columns: 1fr;
  }
}

.candidate-card {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2);
  background: var(--color-bg-subtle);
  border: 2px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition:
    border-color var(--duration-fast) var(--ease-out),
    transform var(--duration-fast) var(--ease-out);
  text-align: left;
}

.candidate-card:hover:not(:disabled) {
  transform: translateY(-2px);
  border-color: var(--color-accent-border);
}

.candidate-card.is-selected {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.candidate-card.is-failed {
  opacity: 0.5;
  cursor: not-allowed;
}

.card-image-box {
  position: relative;
  aspect-ratio: 3 / 4;
  background: var(--color-bg);
  border-radius: var(--radius-sm);
  overflow: hidden;
}

.card-image {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.card-image-error {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-xs);
  color: var(--color-danger);
}

.select-badge {
  position: absolute;
  top: var(--space-1);
  right: var(--space-1);
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-sm);
}

.card-meta {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 0 var(--space-1);
}

.card-index {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

.card-hint {
  font-size: var(--text-xs);
  color: var(--color-text);
  line-height: 1.4;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.loading-note {
  margin-top: var(--space-4);
  padding: var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-md);
  text-align: center;
}

.dialog-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid var(--color-border);
}

.ghost-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
}

.ghost-btn:hover:not(:disabled) {
  background: var(--color-surface-hover);
}

.ghost-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.primary-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
}

.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.mono {
  font-family: var(--font-mono);
}

</style>
