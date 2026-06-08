<script setup lang="ts">
/**
 * ToastHost — 全平台轻提示渲染器(Sprint 1.M Polish)。
 *
 * 顶部居中 stack(z-index 200,高于 modal 101,modal 内触发也能盖上层显示)。
 * 自动消失或手动 ×。TransitionGroup 做进出动画。
 *
 * 由 App.vue 全局挂一次,业务代码不直接 import,通过 toast.* API 触发。
 */
import { useToast, type ToastKind } from "../composables/useToast";

const { toasts, dismiss } = useToast();

const ICON: Record<ToastKind, string> = {
  info: "ⓘ",
  success: "✓",
  warning: "⚠",
  error: "✕",
};
</script>

<template>
  <Teleport to="body">
    <div class="toast-stack" role="status" aria-live="polite">
      <transition-group name="toast-slide">
        <div
          v-for="t in toasts"
          :key="t.id"
          class="toast"
          :class="`toast--${t.kind}`"
        >
          <span class="toast-icon" aria-hidden="true">{{ ICON[t.kind] }}</span>
          <span class="toast-msg">{{ t.message }}</span>
          <button
            type="button"
            class="toast-close"
            aria-label="关闭"
            @click="dismiss(t.id)"
          >×</button>
        </div>
      </transition-group>
    </div>
  </Teleport>
</template>

<style scoped>
.toast-stack {
  position: fixed;
  top: var(--space-5);
  left: 50%;
  transform: translateX(-50%);
  display: flex;
  flex-direction: column;
  align-items: center;        /* 单条 toast 居中(420px 固定宽度)*/
  gap: var(--space-2);
  z-index: var(--z-toast);
  pointer-events: none;
  /* 容器不限宽,toast 自己 width: 420px;小屏靠 toast 内部 min(420px, 100vw - X)兜底 */
}

/* 小屏适配:< 480px 时 toast 顶住屏宽留 16px 边 */
@media (max-width: 480px) {
  .toast {
    width: calc(100vw - var(--space-4) * 2);
  }
}

.toast {
  pointer-events: auto;        /* 单条 toast 可点 × */
  display: flex;               /* flex(非 inline-flex)+ 固定宽度 → 防长内容撑超宽 */
  align-items: flex-start;     /* 多行时图标顶对齐,视觉更稳 */
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-sm);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  width: 420px;                /* 固定宽度,不再 min/max-width 让 flex 子元素撑爆 */
}

.toast-icon {
  font-size: var(--text-md);
  line-height: 1.4;            /* 与 toast-msg line-height 对齐,顶对齐时不偏 */
  flex-shrink: 0;
}

.toast-msg {
  /* min-width:0 是关键 — flex 子元素默认 min-width:auto 会跟随内容尺寸,
     这里强制收缩到 0,让 word-wrap 能真正生效 */
  flex: 1;
  min-width: 0;
  color: var(--color-text);
  line-height: 1.5;
  /* 中英文长串都强制换行 */
  word-wrap: break-word;
  word-break: break-word;
  overflow-wrap: anywhere;
  /* 单条 message 不超 6 行(防异常超长 message 把整屏占满)*/
  display: -webkit-box;
  -webkit-line-clamp: 6;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.toast-close {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-lg);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border-radius: var(--radius-full);
  cursor: pointer;
  flex-shrink: 0;
  transition: all var(--duration-fast) var(--ease-out);
}
.toast-close:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}

/* 4 类 kind 配色 — 用整圈边框颜色区分(不再用左侧 4px 色条),icon 颜色双重视觉信号 */
.toast--info {
  border-color: var(--color-accent);
}
.toast--info .toast-icon {
  color: var(--color-accent);
}

.toast--success {
  border-color: #16A34A;
}
.toast--success .toast-icon {
  color: #16A34A;
}

.toast--warning {
  border-color: #D97706;
}
.toast--warning .toast-icon {
  color: #D97706;
}

.toast--error {
  border-color: var(--color-danger);
}
.toast--error .toast-icon {
  color: var(--color-danger);
}

/* 进出动画:从顶部滑入 + 淡入,200ms 与 page-fade 同节奏。
 * B4 fix(2026-05-22):
 *   - leave 时 position: absolute 脱离文档流,下方 toast 通过 .toast-slide-move 平滑上移
 *   - 旧版 leave-to 仅 translateY 不 absolute → 下方 toast 突然位移(注释撒谎)
 *   - leave-to 改 translateX(-50%) + translateY(-12px) + scale(0.98),与 enter 对称且微缩"飞走"感更自然 */
.toast-slide-enter-active,
.toast-slide-leave-active {
  transition: transform var(--duration-base) var(--ease-out),
              opacity var(--duration-base) var(--ease-out);
}
.toast-slide-leave-active {
  position: absolute;
  left: 50%;
  transform: translateX(-50%);
}
.toast-slide-enter-from {
  opacity: 0;
  transform: translateY(-12px);
}
.toast-slide-leave-to {
  opacity: 0;
  transform: translateX(-50%) translateY(-12px) scale(0.98);
}
.toast-slide-move {
  transition: transform var(--duration-base) var(--ease-out);
}
</style>
