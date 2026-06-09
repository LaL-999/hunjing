/**
 * useAnalytics — 浑晶平台前端埋点 SDK(2026-05-27 / INS-A2).
 *
 * 设计原则:
 *   - 零 UI:不渲染任何东西,只异步派发埋点
 *   - 零阻塞:track() 立刻返回,fetch 走 Promise.catch 静默吞
 *   - 零污染:本 composable 是唯一与 insights 系统耦合的前端代码
 *   - 容错优先:网络失败 / insights 服务挂 → 静默吞,绝不影响主平台
 *
 * 数据流:
 *   组件 → useAnalytics().track('event_type', {...})
 *        → 直发 POST http://localhost:8001/track
 *        → insights-backend.events 表
 *
 * 特殊场景:
 *   - 页面 unload(刷新 / 关闭 tab):
 *     · 用 navigator.sendBeacon(/track/batch) 把 buffer 一次性发出
 *     · sendBeacon 是浏览器原生 "fire and forget",unload 也能发成功
 *
 * session_id:
 *   - 浏览器 sessionStorage 存(关闭 tab 自动清)
 *   - 第一次调 track() 时若没有,自动生成
 *
 * 用户 ID:
 *   - 通过 setUser(userId) 主动设置(useUser 登录成功后调一次)
 *   - 未登录时所有事件 user_id=null(匿名访问也要埋点)
 */

// ============================================================
// 类型(对齐 insights-backend/app/models/event.py EVENT_TYPES)
// ============================================================

/** 27 个事件类型 — 加新类型必须同步 backend EVENT_TYPES tuple + migration CHECK
 * 2026-06-09 扩展:剧创态 6 个 + 多模型对比 3 个 + dashboard 卡点击 1 个
 */
export type AnalyticsEventType =
  // 页面级
  | "page_view"
  | "page_leave"
  | "page_refresh"
  // AI 调用级
  | "ai_call_start"
  | "ai_call_done"
  | "ai_call_failed"
  // 创作态切换
  | "mode_switch"
  // 业务关键操作(传统 4 态)
  | "project_create"
  | "project_delete"
  | "simulation_create"
  | "simulation_done"
  | "simulation_failed"
  | "audit_run"
  | "canonical_audit_run"
  // 异常 / 会话
  | "error"
  | "session_start"
  | "session_end"
  // 2026-06-09 新增 — 剧创态(第 5 态)6 类
  | "screenplay_novel_upload"      // 上传小说
  | "screenplay_compose_start"     // 触发剧本生成
  | "screenplay_compose_done"      // 剧本生成完成(含 bridge_used 元信息)
  | "screenplay_optimize"          // AI 优化重排(全篇 / 单场)
  | "screenplay_characters_view"   // 打开角色档案面板
  | "screenplay_episodes_plan"     // 调用分集规划
  // 2026-06-09 新增 — 多模型对比(BYOK B5)3 类
  | "model_compare_start"          // 打开多模型对比配置
  | "model_compare_run"            // 触发对比执行
  | "model_compare_winner"         // 用户选定胜出 vendor(导出 / 采用)
  // 2026-06-09 新增 — Dashboard 入口转化(新手教育价值)
  | "dashboard_card_click";        // 主页 6 张创作态卡片点击(meta.card = "initial"/"middle"/etc.)

/** 5 态枚举(对齐主平台 project.mode + screenplay 新增) */
export type AnalyticsMode = "initial" | "middle" | "tail" | "comic" | "screenplay";

/** 单条埋点 payload(对齐 insights-backend TrackRequest) */
export interface AnalyticsEvent {
  event_type: AnalyticsEventType;
  user_id?: string | null;
  session_id: string;
  timestamp_ms: number;
  project_id?: string | null;
  simulation_id?: string | null;
  mode?: AnalyticsMode | null;
  step?: string | null;
  path?: string | null;
  duration_ms?: number | null;
  meta?: Record<string, unknown>;
}

/** track() 的可选 props(简化调用) */
export interface TrackProps {
  project_id?: string;
  simulation_id?: string;
  mode?: AnalyticsMode;
  step?: string;
  path?: string;
  duration_ms?: number;
  meta?: Record<string, unknown>;
}

// ============================================================
// 配置
// ============================================================

/** insights-backend endpoint — dev 默认 localhost:8001,prod 通过 env 覆盖 */
const INSIGHTS_BASE =
  (import.meta.env.VITE_INSIGHTS_BASE as string | undefined) ||
  "http://localhost:8001";

const SESSION_STORAGE_KEY = "huimeng_session_id";

// ============================================================
// 模块级状态(单例)
// ============================================================

let _userId: string | null = null;
let _sessionId: string | null = null;

/** sendBeacon 的 buffer — 页面 unload 时一次性发 */
const _beaconBuffer: AnalyticsEvent[] = [];

// ============================================================
// 内部工具
// ============================================================

function getOrCreateSessionId(): string {
  if (_sessionId) return _sessionId;
  try {
    const stored = sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (stored) {
      _sessionId = stored;
      return stored;
    }
  } catch {
    // sessionStorage 不可用(无痕模式 / SSR)— fallback 用内存
  }
  // 生成新 session_id:时间戳 + 随机数
  const newId = `sess-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 9)}`;
  _sessionId = newId;
  try {
    sessionStorage.setItem(SESSION_STORAGE_KEY, newId);
  } catch {
    /* ignore */
  }
  return newId;
}

/** 向 /track 发单条 — 异步,失败静默 */
async function sendOne(ev: AnalyticsEvent): Promise<void> {
  try {
    await fetch(`${INSIGHTS_BASE}/track`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ev),
      // keepalive 让 fetch 也能在 unload 时尽量送达(类似 sendBeacon)
      keepalive: true,
      // 不带 credentials — insights 系统无 cookie 鉴权,纯接收
    });
  } catch {
    // 静默吞 — 网络失败 / CORS / insights 服务挂都不影响主平台
  }
}

/** 用 sendBeacon 发 buffer(unload 场景) */
function flushBuffer(): void {
  if (_beaconBuffer.length === 0) return;
  const payload = JSON.stringify({ events: _beaconBuffer.splice(0) });
  try {
    // sendBeacon 不接 application/json,要 Blob 包装
    const blob = new Blob([payload], { type: "application/json" });
    navigator.sendBeacon?.(`${INSIGHTS_BASE}/track/batch`, blob);
  } catch {
    /* 静默 */
  }
}

// ============================================================
// 公开 API
// ============================================================

/** 主入口:发一条埋点(异步,不阻塞调用方) */
export function track(
  eventType: AnalyticsEventType,
  props: TrackProps = {},
): void {
  const ev: AnalyticsEvent = {
    event_type: eventType,
    user_id: _userId,
    session_id: getOrCreateSessionId(),
    timestamp_ms: Date.now(),
    project_id: props.project_id ?? null,
    simulation_id: props.simulation_id ?? null,
    mode: props.mode ?? null,
    step: props.step ?? null,
    path: props.path ?? window.location.pathname,
    duration_ms: props.duration_ms ?? null,
    meta: props.meta ?? {},
  };
  // page_leave / session_end:进 buffer 等 unload 一次性发(更稳)
  // 其他事件:立刻 fetch 发
  if (eventType === "page_leave" || eventType === "session_end") {
    _beaconBuffer.push(ev);
  } else {
    void sendOne(ev);
  }
}

/** 登录后调一次,所有后续 event 自动带 user_id */
export function setAnalyticsUser(userId: string | null): void {
  _userId = userId;
}

/** 登出 / 切换账号 — 清 user_id + 重新生成 session_id */
export function resetAnalyticsSession(): void {
  _userId = null;
  _sessionId = null;
  try {
    sessionStorage.removeItem(SESSION_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

/** 当前 session_id(给业务代码偶尔需要时用,非常用) */
export function getSessionId(): string {
  return getOrCreateSessionId();
}

// ============================================================
// 全局生命周期 hook(main.ts 调一次)
// ============================================================

let _installed = false;

/** 安装全局 hook:beforeunload flush buffer + visibilitychange + 自动 session_start */
export function installAnalytics(): void {
  if (_installed) return;
  _installed = true;

  // 首次安装时发 session_start
  track("session_start");

  // 页面 unload(刷新 / 关闭 tab / 跳外站) — flush buffer
  window.addEventListener("beforeunload", () => {
    // 标记 session_end + page_leave 由 router 触发,这里只做 buffer flush 兜底
    flushBuffer();
  });

  // visibilitychange — 用户切到其他 tab 时也 flush(更稳)
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") {
      flushBuffer();
    }
  });
}

// ============================================================
// 默认导出(供 import default 使用)
// ============================================================

export default {
  track,
  setAnalyticsUser,
  resetAnalyticsSession,
  getSessionId,
  installAnalytics,
};
