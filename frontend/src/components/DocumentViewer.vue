<script setup lang="ts">
/**
 * DocumentViewer — 浑晶用户协议查看器(浅色 modal,Claude 风)。
 *
 * Props:
 *   open: boolean         父组件控制开关
 * Emits:
 *   close                 用户主动关闭(× / Esc / 点蒙层)
 *   read                  用户点底部"我已阅读"按钮 — 父组件可据此设 agreed=true
 *
 * Teleport to body — 解决嵌套 stacking context 导致的 z-index 失效
 * (之前 ConsentGate 内嵌 DocumentViewer 时,DocumentViewer 被父级遮住的 bug)
 */
import { LEGAL_DOCUMENT } from "../constants/legal-docs";

defineProps<{ open: boolean }>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "read"): void;
}>();

function handleBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) emit("close");
}

function handleConfirmRead() {
  emit("read");
  emit("close");
}
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        :aria-label="LEGAL_DOCUMENT.title"
        @click="handleBackdrop"
        @keydown.esc="emit('close')"
      >
        <div class="modal-card surface">
          <header class="modal-header">
            <div class="header-text">
              <h2 class="modal-title">
                {{ LEGAL_DOCUMENT.title }}
                <span v-if="LEGAL_DOCUMENT.status" class="status-badge">{{ LEGAL_DOCUMENT.status }}</span>
              </h2>
              <p class="modal-subtitle">
                版本 <span class="mono">{{ LEGAL_DOCUMENT.version }}</span>
                · 含用户协议 · 隐私政策 · 服务等级与价格说明
              </p>
            </div>
            <button class="close-btn" type="button" aria-label="关闭" @click="emit('close')">
              ×
            </button>
          </header>

          <section class="modal-body">
            <article
              v-for="chapter in LEGAL_DOCUMENT.chapters"
              :key="chapter.title"
              class="chapter"
            >
              <h3 class="chapter-title">{{ chapter.title }}</h3>
              <div
                v-for="sec in chapter.sections"
                :key="sec.heading"
                class="doc-section"
              >
                <h4 class="doc-heading">{{ sec.heading }}</h4>
                <p class="doc-body">{{ sec.body }}</p>
              </div>
            </article>
          </section>

          <footer class="modal-footer">
            <button class="primary-btn" @click="handleConfirmRead">
              我已阅读
            </button>
          </footer>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(31, 31, 30, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-toast);   /* 高于其他所有 modal */
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  max-width: 680px;
  max-height: calc(100vh - var(--space-8));
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
}

.modal-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-5) var(--space-6);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.header-text {
  flex: 1;
  min-width: 0;
}

.modal-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-1);
}

.status-badge {
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-warning);
  background: var(--color-warning-soft);
  padding: 1px 8px;
  border-radius: var(--radius-sm);
}

.modal-subtitle {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.close-btn {
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-muted);
  border-radius: var(--radius-full);
  transition: all var(--duration-fast) var(--ease-out);
  flex-shrink: 0;
}

.close-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.modal-body {
  padding: var(--space-5) var(--space-6);
  overflow-y: auto;
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.chapter {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.chapter-title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-accent-text);
  padding-bottom: var(--space-2);
  border-bottom: 1px solid var(--color-border);
}

.doc-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.doc-heading {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
}

.doc-body {
  font-size: var(--text-sm);
  line-height: var(--line-relaxed);
  color: var(--color-text-muted);
}

.modal-footer {
  padding: var(--space-4) var(--space-6);
  border-top: 1px solid var(--color-border);
  display: flex;
  justify-content: flex-end;
  flex-shrink: 0;
}

.primary-btn {
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) var(--ease-out);
}

.primary-btn:hover {
  background: var(--color-accent-hover);
}

</style>
