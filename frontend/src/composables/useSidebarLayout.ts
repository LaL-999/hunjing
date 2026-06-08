/**
 * useSidebarLayout — sidebar 折叠展开全局状态(2026-06-08)。
 *
 * 设计:
 *   - 单例 ref(模块顶层),组件共享同一份 state
 *   - localStorage 持久化(刷新保留用户的折叠偏好)
 *   - 默认展开(新用户首次进入)
 *   - 简洁 API:collapsed / toggle / expand / collapse
 *
 * 用法:
 *   import { useSidebarLayout } from "@/composables/useSidebarLayout";
 *   const { collapsed, toggle } = useSidebarLayout();
 */
import { ref, watch } from "vue";

const STORAGE_KEY = "huimeng_sidebar_collapsed";

function readInitial(): boolean {
  try {
    if (typeof localStorage === "undefined") return false;
    return localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    return false;
  }
}

// 模块级单例 — 所有组件 useSidebarLayout() 共享同一份 state
const collapsedRef = ref<boolean>(readInitial());

// 持久化到 localStorage
watch(collapsedRef, (v) => {
  try {
    if (typeof localStorage !== "undefined") {
      localStorage.setItem(STORAGE_KEY, v ? "1" : "0");
    }
  } catch {
    // 隐私模式 localStorage 可能不可用 — 静默忽略
  }
});

export function useSidebarLayout() {
  return {
    collapsed: collapsedRef,
    toggle: (): void => {
      collapsedRef.value = !collapsedRef.value;
    },
    expand: (): void => {
      collapsedRef.value = false;
    },
    collapse: (): void => {
      collapsedRef.value = true;
    },
  };
}
