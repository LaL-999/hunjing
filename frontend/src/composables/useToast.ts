/**
 * useToast — 全平台轻提示 singleton(Sprint 1.M Polish)。
 *
 * 替代 window.alert():
 *   - 原生 alert 顶部弹出无背景且阻塞主线程
 *   - 这里是顶部居中 stack,不阻塞,自动消失,与浅色 token 体系一致
 *   - z-index 高于 modal,modal 内部触发也能正常显示
 *
 * 使用:
 *   import { toast } from "../composables/useToast";
 *
 *   toast.error("删除失败:网络错误");
 *   toast.info("AI 续写只能围绕角色,事件节点不参与");
 *   toast.warning("配额即将用完");
 *   toast.success("已保存");
 *
 *   // 自定义 duration(0 = sticky 不自动消失)
 *   toast.show({ kind: "info", message: "...", duration: 5000 });
 *
 * 由 App.vue 全局挂一次 ToastHost 渲染。
 */
import { ref } from "vue";

export type ToastKind = "info" | "success" | "warning" | "error";

export interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
  /** 毫秒;0 = sticky 永不自动消失 */
  duration: number;
}

const toasts = ref<ToastItem[]>([]);
const timers = new Map<number, ReturnType<typeof setTimeout>>();
let nextId = 1;

function _show(opts: Omit<ToastItem, "id">): number {
  const id = nextId++;
  const item: ToastItem = { id, ...opts };
  toasts.value.push(item);
  if (item.duration > 0) {
    const t = setTimeout(() => dismiss(id), item.duration);
    timers.set(id, t);
  }
  return id;
}

function dismiss(id: number) {
  toasts.value = toasts.value.filter((t) => t.id !== id);
  const t = timers.get(id);
  if (t) {
    clearTimeout(t);
    timers.delete(id);
  }
}

function dismissAll() {
  for (const t of timers.values()) clearTimeout(t);
  timers.clear();
  toasts.value = [];
}

/**
 * 直接调用的简洁 API。业务代码用 `toast.error("...")` 即可。
 */
export const toast = {
  show: _show,
  info(message: string, duration = 3500): number {
    return _show({ kind: "info", message, duration });
  },
  success(message: string, duration = 3000): number {
    return _show({ kind: "success", message, duration });
  },
  warning(message: string, duration = 4000): number {
    return _show({ kind: "warning", message, duration });
  },
  error(message: string, duration = 4500): number {
    return _show({ kind: "error", message, duration });
  },
  dismiss,
  dismissAll,
};

/**
 * ToastHost 组件用 — 拿响应式 toasts 列表 + dismiss 回调。
 */
export function useToast() {
  return {
    toasts,
    dismiss,
  };
}
