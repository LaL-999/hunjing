<script setup lang="ts">
/**
 * ChooseModeModal — 弹「选你的创作态」中央模态。
 *
 * 触发场景:DashboardView 老用户(已有推演)点顶部"+ 创建新作"按钮时弹此 modal。
 * 新用户(无推演)在 DashboardView 直接 inline 看 4 象限,不走这条路径。
 *
 * UX 铁律对齐:中央 + 背景虚化 + 200ms fade + Esc/点 backdrop 关。
 */
import { onBeforeUnmount, onMounted } from "vue";

import { type ProjectMode } from "../api/types";
import CreationModeQuadrant from "./CreationModeQuadrant.vue";

const props = defineProps<{
  open: boolean;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "select", mode: ProjectMode): void;
}>();

function handleBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) emit("close");
}

function onSelect(mode: ProjectMode) {
  emit("select", mode);
}

function onGlobalKey(e: KeyboardEvent) {
  if (props.open && e.key === "Escape") {
    e.preventDefault();
    emit("close");
  }
}

onMounted(() => document.addEventListener("keydown", onGlobalKey));
onBeforeUnmount(() => document.removeEventListener("keydown", onGlobalKey));
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        aria-label="选择创作态"
        @click="handleBackdrop"
      >
        <div class="modal-card surface">
          <button
            class="close-btn"
            type="button"
            aria-label="关闭"
            @click="emit('close')"
          >×</button>

          <header class="modal-header">
            <h2 class="modal-title">开始一段新创作</h2>
            <p class="modal-subtitle">先选一个创作态 — 决定你接下来怎么和 AI 协作</p>
          </header>

          <CreationModeQuadrant @select="onSelect" />
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
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  max-width: 920px;
  max-height: calc(100vh - var(--space-8));
  /* Sprint 3.A polish:原本写的 var(--space-7) 在 tokens.css 里没定义(只有 1-6 / 8 / 10 / 12 / 16),
     整条 padding shorthand 因此被 CSS 视为无效 → 回落到初值 0 → 末尾态/周期态卡贴边 modal 框。
     改用已定义 token:上 6 / 左右 6 / 下 10(底部多留一档,让 2x2 下排有呼吸感) */
  padding: var(--space-6) var(--space-6) var(--space-10);
  position: relative;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-modal);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-6);    /* header 与 quadrant 之间间距 */
}

.close-btn {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.close-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.modal-header {
  text-align: center;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.modal-title {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.modal-subtitle {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin: 0;
}

</style>
