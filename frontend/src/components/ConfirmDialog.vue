<script setup lang="ts">
/**
 * ConfirmDialog — 全平台二次确认对话框(Sprint 1.M Polish)。
 *
 * 替代 window.confirm() 的统一中央弹窗:
 *   - 中央定位(原生 confirm 顶部割裂)
 *   - 背景虚化(backdrop-filter: blur)
 *   - 浅色 token 与其它 modal 对齐
 *   - danger 模式红色确定按钮(删除场景)
 *   - Esc / 点 backdrop / 点取消都视为取消
 *   - Enter 触发确定(键盘可达)
 *
 * 由 App.vue 全局挂一次,通过 useConfirm() 触发,不直接被业务组件 import。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { useConfirm } from "../composables/useConfirm";

const { isOpen, state, onConfirm, onCancel } = useConfirm();

const confirmBtnRef = ref<HTMLButtonElement | null>(null);

/** 多行 message 拆段渲染(原生 confirm 用 \n,这里保留兼容)*/
const messageLines = computed(() => state.value.message.split("\n"));

const danger = computed(() => state.value.danger ?? false);
const titleText = computed(() => state.value.title ?? "");
const confirmLabel = computed(() => state.value.confirmLabel ?? "确定");
const cancelLabel = computed(() => state.value.cancelLabel ?? "取消");

function handleBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) onCancel();
}

function onGlobalKey(e: KeyboardEvent) {
  if (!isOpen.value) return;
  if (e.key === "Escape") {
    e.preventDefault();
    onCancel();
  } else if (e.key === "Enter") {
    e.preventDefault();
    onConfirm();
  }
}

// 弹出后下一帧把焦点丢到「确定」按钮上,Enter 即提交,无需鼠标
watch(isOpen, async (open) => {
  if (open) {
    await nextTick();
    confirmBtnRef.value?.focus();
  }
});

onMounted(() => {
  document.addEventListener("keydown", onGlobalKey);
});
onBeforeUnmount(() => {
  document.removeEventListener("keydown", onGlobalKey);
});
</script>

<template>
  <Teleport to="body">
    <transition name="confirm-fade">
      <div
        v-if="isOpen"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        :aria-label="titleText || '确认'"
        @click="handleBackdrop"
      >
        <div class="modal-card surface" :class="{ 'is-danger': danger }">
          <header v-if="titleText" class="modal-header">
            <h2 class="modal-title">{{ titleText }}</h2>
          </header>

          <div class="modal-body">
            <p
              v-for="(line, i) in messageLines"
              :key="i"
              class="message-line"
            >{{ line }}</p>
          </div>

          <footer class="modal-actions">
            <button
              type="button"
              class="ghost-btn"
              @click="onCancel"
            >{{ cancelLabel }}</button>
            <button
              ref="confirmBtnRef"
              type="button"
              class="primary-btn"
              :class="{ 'is-danger': danger }"
              @click="onConfirm"
            >{{ confirmLabel }}</button>
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
  background: rgba(31, 31, 30, 0.4);
  /* 背景虚化(用户点名要的)— 浏览器降级时 background 颜色仍生效不会无视觉 */
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: flex;
  align-items: center;     /* 中央对齐(原生 confirm 顶部割裂的根因) */
  justify-content: center;
  z-index: var(--z-confirm-backdrop);   /* 2.C+ polish: 永远高于业务 modal */
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  max-width: 420px;
  padding: var(--space-6) var(--space-6) var(--space-5);
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-confirm);
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.modal-header {
  /* 标题区无下边框,与 message 之间靠 gap 区分 */
}

.modal-title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
  line-height: 1.4;
}

.modal-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.message-line {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: 1.6;
  margin: 0;
}

.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-2);
}

.ghost-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border-radius: var(--radius-md);
  transition: all var(--duration-fast) var(--ease-out);
}
.ghost-btn:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
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
.primary-btn:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
}

/* danger 模式:确定按钮变红(删除场景)*/
.primary-btn.is-danger {
  background: var(--color-danger);
}
.primary-btn.is-danger:hover {
  background: var(--color-danger-hover, #B91C1C);
}
.primary-btn.is-danger:focus-visible {
  outline-color: var(--color-danger);
}

/* 进出动画:背景虚化与卡片同步,200ms 与 page-fade 同节奏 */
.confirm-fade-enter-active,
.confirm-fade-leave-active {
  transition: opacity var(--duration-base) var(--ease-out);
}
.confirm-fade-enter-active .modal-card,
.confirm-fade-leave-active .modal-card {
  transition: transform var(--duration-base) var(--ease-out),
              opacity var(--duration-base) var(--ease-out);
}
.confirm-fade-enter-from,
.confirm-fade-leave-to {
  opacity: 0;
}
.confirm-fade-enter-from .modal-card,
.confirm-fade-leave-to .modal-card {
  transform: scale(0.96) translateY(-4px);
  opacity: 0;
}
</style>
