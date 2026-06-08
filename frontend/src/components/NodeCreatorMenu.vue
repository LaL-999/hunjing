<script setup lang="ts">
/**
 * NodeCreatorMenu — 3D 图谱右键画布弹的浮动小菜单(Sprint 1.M.1.D)。
 *
 * 对应 docs/MVP阶段1_初始态流程图.md 节点 6 的"右键画布 → 弹新建小卡"。
 * 与 docs pitch deck 段落 3 demo 描述的"3D 即编辑器"对齐。
 *
 * Props:
 *   open      boolean
 *   x / y     菜单浮动位置(屏幕坐标,from CrystalGraph background-rclick)
 *
 * Emits:
 *   close                                          外部点击 / Esc
 *   request-create  ({ type: 'character'|'event' })  用户选了类型 — 父组件据此打开
 *                                                     NodeEditDrawer create 模式让用户填完整字段;
 *                                                     菜单本身不创建任何节点(POST 由 drawer 决定)
 *
 * UX:
 *   - 菜单在 (x, y) 弹出;边缘溢出时自动调向(右下/左下/右上/左上)
 *   - 选项点击 → emit request-create + emit close;用户在 drawer 不填名关掉 → 完全没节点
 *   - 点菜单外或 Esc 关闭
 *
 * 动画(优化"黑屏闪烁"):
 *   时长用 --duration-base(200ms),与 App.vue page-fade 同步;
 *   opacity + transform: translateY 同步过渡,告别突兀感
 */
import { computed, onBeforeUnmount, onMounted } from "vue";

type CreateType = "character" | "event";

const props = defineProps<{
  open: boolean;
  x: number;
  y: number;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "request-create", payload: { type: CreateType }): void;
}>();

// 菜单宽度 / 高度估算(用于边缘检测)
const MENU_W = 180;
const MENU_H = 110;

const positionStyle = computed(() => {
  const padding = 8;
  const vw = typeof window !== "undefined" ? window.innerWidth : 1024;
  const vh = typeof window !== "undefined" ? window.innerHeight : 768;
  // 默认放右下;碰右边缘 → 改放左下;碰下边缘 → 改放右上 / 左上
  const overflowRight = props.x + MENU_W + padding > vw;
  const overflowBottom = props.y + MENU_H + padding > vh;
  const left = overflowRight ? props.x - MENU_W : props.x;
  const top = overflowBottom ? props.y - MENU_H : props.y;
  return { left: `${left}px`, top: `${top}px` };
});

function pickType(t: CreateType) {
  emit("request-create", { type: t });
  emit("close");
}

// 全局点击 / Esc 关闭(不冒泡到自身菜单内)
function onGlobalClick(e: MouseEvent) {
  if (!props.open) return;
  const target = e.target as HTMLElement;
  if (target.closest(".node-creator-menu")) return;   // 点菜单内不关
  emit("close");
}
function onGlobalKey(e: KeyboardEvent) {
  if (props.open && e.key === "Escape") emit("close");
}

onMounted(() => {
  document.addEventListener("click", onGlobalClick);
  document.addEventListener("keydown", onGlobalKey);
});
onBeforeUnmount(() => {
  document.removeEventListener("click", onGlobalClick);
  document.removeEventListener("keydown", onGlobalKey);
});
</script>

<template>
  <Teleport to="body">
    <transition name="menu-pop">
      <div
        v-if="open"
        class="node-creator-menu surface"
        :style="positionStyle"
        role="menu"
      >
        <button
          type="button"
          class="menu-item"
          @click.stop="pickType('character')"
        >
          <span class="menu-icon">+</span>
          <span class="menu-label">
            新角色
            <span class="menu-sub">在此处加一个角色节点</span>
          </span>
        </button>
        <div class="menu-divider"></div>
        <button
          type="button"
          class="menu-item"
          @click.stop="pickType('event')"
        >
          <span class="menu-icon">+</span>
          <span class="menu-label">
            新事件
            <span class="menu-sub">在此处加一个事件节点</span>
          </span>
        </button>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.node-creator-menu {
  position: fixed;
  width: 180px;
  z-index: var(--z-modal);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  border: 1px solid var(--color-border);
  padding: var(--space-1);
  display: flex;
  flex-direction: column;
}

.menu-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  text-align: left;
  width: 100%;
  transition: background var(--duration-fast) var(--ease-out);
}
.menu-item:hover {
  background: var(--color-surface-hover);
}

.menu-icon {
  width: 20px;
  height: 20px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-accent);
  flex-shrink: 0;
}

.menu-label {
  display: flex;
  flex-direction: column;
  gap: 1px;
  font-size: var(--text-sm);
  color: var(--color-text);
}

.menu-sub {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-weight: 400;
}

.menu-divider {
  height: 1px;
  background: var(--color-border);
  margin: 2px var(--space-1);
}

/* 优化"黑屏闪烁":时长升到 --duration-base,与 App.vue page-fade 同步 */
.menu-pop-enter-active,
.menu-pop-leave-active {
  transition: opacity var(--duration-base) var(--ease-out),
              transform var(--duration-base) var(--ease-out);
}
.menu-pop-enter-from,
.menu-pop-leave-to {
  opacity: 0;
  transform: translateY(-4px) scale(0.96);
}
</style>
