/**
 * useGlobalSearch — Sprint 6.A2 路线图 #6(2026-05-23)全局搜索单例 + 快捷键。
 *
 * 单例 state(类似 useConfirm / useToast):
 *   - isOpen:弹窗显示状态
 *   - initialScopeProjectId:打开时的"当前项目"context(在 /projects/:id 路由触发时自动设)
 *
 * 使用:
 *   // 任意组件
 *   const search = useGlobalSearch();
 *   search.open();                                    // 全平台搜索
 *   search.open({ scopeProjectId: "abc123" });        // 限当前项目搜
 *
 *   // App.vue setup() 调一次 — 注册全局 Cmd+K / Ctrl+K 监听
 *   registerGlobalSearchShortcut(() => router.currentRoute.value);
 */
import { onBeforeUnmount, onMounted, ref } from "vue";
import type { RouteLocationNormalized } from "vue-router";

const isOpen = ref(false);
const initialScopeProjectId = ref<string | null>(null);

export interface OpenSearchOptions {
  /** 打开时的"当前项目"限定;不传 → 全平台搜索 */
  scopeProjectId?: string | null;
}

export function useGlobalSearch() {
  function open(opts?: OpenSearchOptions) {
    initialScopeProjectId.value = opts?.scopeProjectId ?? null;
    isOpen.value = true;
  }
  function close() {
    isOpen.value = false;
  }
  function toggle(opts?: OpenSearchOptions) {
    if (isOpen.value) close();
    else open(opts);
  }
  return {
    isOpen,
    initialScopeProjectId,
    open,
    close,
    toggle,
  };
}

/**
 * 全局快捷键注册 — App.vue setup 调一次,自动在挂载 / 卸载时管理 listener。
 *
 * Cmd+K(Mac)/ Ctrl+K(Win) → 打开搜索;路由在 /projects/:id 时自动限定该项目。
 * 路由不在项目内时 → 全平台搜索。
 *
 * Args:
 *   getCurrentRoute: 返回当前 route(从 useRoute() 取一个 reactive ref)— 用于推断 scope
 */
export function registerGlobalSearchShortcut(
  getCurrentRoute: () => RouteLocationNormalized,
) {
  const search = useGlobalSearch();

  function onKeydown(e: KeyboardEvent) {
    // Cmd+K(Mac)或 Ctrl+K(Win/Linux)
    if (!(e.metaKey || e.ctrlKey)) return;
    if (e.key.toLowerCase() !== "k") return;
    e.preventDefault();
    // 推断 scope:当前路由是 /projects/:id 时,默认限该项目
    const route = getCurrentRoute();
    const m = route.path.match(/^\/projects\/([^/]+)/);
    const scopeProjectId = m ? m[1] : null;
    search.toggle({ scopeProjectId });
  }

  onMounted(() => document.addEventListener("keydown", onKeydown));
  onBeforeUnmount(() => document.removeEventListener("keydown", onKeydown));
}
