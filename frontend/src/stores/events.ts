/**
 * 全局事件总线(2026-06-01)— 治"用户操作完后列表/卡片不实时更新需手动刷新页"
 *
 * 设计:
 *   - 轻量 mitt-like:Map<event_name, Set<callback>>
 *   - Pinia store 包装,提供 reactive subscription lifecycle 管理
 *   - 不持久化,纯运行时事件管道
 *
 * 用法:
 *   // 触发端(SimulationDetailView 收到 SSE done 后)
 *   const events = useEventBus();
 *   events.emit("sim:done", { sim_id, project_id });
 *
 *   // 监听端(SimulationsListPanel)
 *   const events = useEventBus();
 *   const unsub = events.on("sim:done", (payload) => {
 *     if (payload.project_id === currentProjectId.value) {
 *       reload();
 *     }
 *   });
 *   onBeforeUnmount(unsub);
 *
 * 标准事件名(命名空间:domain:action):
 *   sim:created / sim:done / sim:deleted / sim:failed / sim:cancelled
 *   project:created / project:deleted / project:updated
 *   comic:created / comic:done / comic:deleted
 *   credit:consumed(留位,credit balance 实时刷新用)
 */
import { defineStore } from "pinia";

export type AppEventName =
  | "sim:created"
  | "sim:done"
  | "sim:deleted"
  | "sim:failed"
  | "sim:cancelled"
  | "project:created"
  | "project:deleted"
  | "project:updated"
  | "comic:created"
  | "comic:done"
  | "comic:deleted"
  | "credit:consumed";

export interface SimEventPayload {
  sim_id: string;
  project_id?: string | null;
}
export interface ProjectEventPayload {
  project_id: string;
}
export interface ComicEventPayload {
  comic_id: string;
  project_id?: string | null;
}
export interface CreditEventPayload {
  amount_yuan?: number;
  reason?: string;
}

// 联合体 — 类型推导按 event 名分发
export type EventPayload<T extends AppEventName> =
  T extends "sim:created" | "sim:done" | "sim:deleted" | "sim:failed" | "sim:cancelled"
    ? SimEventPayload
    : T extends "project:created" | "project:deleted" | "project:updated"
    ? ProjectEventPayload
    : T extends "comic:created" | "comic:done" | "comic:deleted"
    ? ComicEventPayload
    : T extends "credit:consumed"
    ? CreditEventPayload
    : never;

type AnyCallback = (payload: unknown) => void;

export const useEventBus = defineStore("eventBus", () => {
  // Map<event_name, Set<callback>> — 非 reactive(订阅本身不用响应式)
  const handlers = new Map<AppEventName, Set<AnyCallback>>();

  /**
   * 订阅事件;返回 unsubscribe 函数.
   * 务必在 onBeforeUnmount 调 unsub() 避免内存泄漏 + zombie reload.
   */
  function on<T extends AppEventName>(
    event: T,
    callback: (payload: EventPayload<T>) => void,
  ): () => void {
    if (!handlers.has(event)) {
      handlers.set(event, new Set());
    }
    const set = handlers.get(event)!;
    const cb = callback as AnyCallback;
    set.add(cb);
    return () => {
      set.delete(cb);
      if (set.size === 0) {
        handlers.delete(event);
      }
    };
  }

  /**
   * 触发事件;同步派发到所有订阅者.
   * 单个 callback 抛错不影响其它订阅者(隔离 try/catch).
   */
  function emit<T extends AppEventName>(
    event: T,
    payload: EventPayload<T>,
  ): void {
    const set = handlers.get(event);
    if (!set || set.size === 0) return;
    for (const cb of set) {
      try {
        cb(payload as unknown);
      } catch (e) {
        if (import.meta.env.DEV) {
          // eslint-disable-next-line no-console
          console.error(`[eventBus] handler for "${event}" threw:`, e);
        }
      }
    }
  }

  /** 调试用 — 列出当前所有订阅 */
  function debugSnapshot(): Record<string, number> {
    const out: Record<string, number> = {};
    for (const [name, set] of handlers.entries()) {
      out[name] = set.size;
    }
    return out;
  }

  return { on, emit, debugSnapshot };
});
