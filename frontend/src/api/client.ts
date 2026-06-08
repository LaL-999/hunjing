/**
 * API client — fetch wrapper。
 *
 * 职责:
 *   - 自动加 Authorization: Bearer <token>(从 auth store 取)
 *   - 统一错误处理:HTTP 4xx/5xx 抛 ApiError(带 code + message)
 *   - 401 自动 logout(token 失效场景)
 *   - JSON 序列化/反序列化
 *
 * 不做:
 *   - 缓存 / 去重 / 重试(YAGNI)
 *   - 全局 loading 状态(各 view 自己管)
 */
import { ApiError, type ApiErrorBody } from "./types";

// re-export ApiError 让历史代码 `import { api, ApiError } from "./api/client"` 也工作.
// 注:运行时 ESM 严格只看本模块 export — 之前 client.ts 仅 import 不 re-export,
// 导致 StoryFactsPanel / PlotThreadsPanel / SimulationCompareView 三处运行时
// "does not provide an export named 'ApiError'" 致整条路由 navigation 失败.
// vue-tsc 漏抓:把 ApiError 当 type-only 引用通过(实际它是运行时 class).
export { ApiError };

const API_BASE = "/api";   // 走 vite proxy → http://localhost:8000

/** auth store 在 client 之后初始化,用 setter 注入,避免循环 import */
let getToken: () => string | null = () => null;
let onUnauthorized: () => void = () => {};

export function configureAuthAccessors(opts: {
  getToken: () => string | null;
  onUnauthorized: () => void;
}) {
  getToken = opts.getToken;
  onUnauthorized = opts.onUnauthorized;
}

interface RequestOptions {
  /** 跳过自动 Bearer header(登录接口用) */
  skipAuth?: boolean;
}

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: RequestOptions = {},
): Promise<T> {
  const headers: Record<string, string> = {};
  // Sprint 2.A:FormData 走 multipart,Content-Type 由浏览器拼(含 boundary),不能手设
  const isFormData = typeof FormData !== "undefined" && body instanceof FormData;
  if (body !== undefined && !isFormData) {
    headers["Content-Type"] = "application/json";
  }
  if (!opts.skipAuth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }

  const init: RequestInit = {
    method,
    headers,
    body:
      body === undefined
        ? undefined
        : isFormData
          ? (body as FormData)
          : JSON.stringify(body),
  };

  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}${path}`, init);
  } catch (e) {
    // 网络错误 / 后端没起
    throw new ApiError(0, "NETWORK_ERROR", "网络异常,请检查后端是否启动");
  }

  // 204 No Content
  if (resp.status === 204) {
    return undefined as T;
  }

  let parsed: unknown = null;
  const contentType = resp.headers.get("Content-Type") || "";
  if (contentType.includes("application/json")) {
    try {
      parsed = await resp.json();
    } catch {
      // JSON 解析失败仍走错误流程
    }
  }

  if (!resp.ok) {
    if (resp.status === 401 && !opts.skipAuth) {
      onUnauthorized();
    }
    const body = parsed as ApiErrorBody | null;
    let code = `HTTP_${resp.status}`;
    let message = `请求失败 (${resp.status})`;
    let detailObj: unknown = parsed;
    if (body && typeof body.detail === "object" && body.detail !== null) {
      const d = body.detail;
      code = d.code || code;
      message = d.message || message;
      detailObj = d;
    } else if (body && typeof body.detail === "string") {
      message = body.detail;
    }
    throw new ApiError(resp.status, code, message, detailObj);
  }

  return parsed as T;
}

// ========== 公共便捷方法 ==========

/**
 * Sprint 4.D(2026-05-13):二进制文件下载(PDF / ZIP / 未来其它格式)。
 *
 * 返回 blob + 选中的响应 headers(供 caller 拿 X-Missing-Pages 等)。
 * 错误处理对齐 request<T>:401 → onUnauthorized;非 2xx → ApiError(尝试解 JSON 错误体)。
 *
 * 用法:
 *   const { blob, headers } = await api.downloadFile("/comics/{id}/export.pdf");
 *   const missing = headers.get("X-Missing-Pages")?.split(",").map(Number) ?? [];
 *   const url = URL.createObjectURL(blob);
 *   // ...触发 <a download> 点击
 */
async function downloadFile(
  path: string,
  opts: RequestOptions = {},
): Promise<{ blob: Blob; headers: Headers }> {
  const headers: Record<string, string> = {};
  if (!opts.skipAuth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}${path}`, { method: "GET", headers });
  } catch (e) {
    throw new ApiError(0, "NETWORK_ERROR", "网络异常,请检查后端是否启动");
  }
  if (!resp.ok) {
    if (resp.status === 401 && !opts.skipAuth) {
      onUnauthorized();
    }
    // 二进制下载失败时,后端会返 JSON 错误体(FastAPI 默认行为)
    let code = `HTTP_${resp.status}`;
    let message = `下载失败 (${resp.status})`;
    let detailObj: unknown = null;
    try {
      const body = (await resp.json()) as ApiErrorBody;
      if (body.detail && typeof body.detail === "object") {
        code = body.detail.code || code;
        message = body.detail.message || message;
        detailObj = body.detail;
      } else if (typeof body.detail === "string") {
        message = body.detail;
      }
    } catch {
      // 非 JSON 错误体,沿用默认 message
    }
    throw new ApiError(resp.status, code, message, detailObj);
  }
  const blob = await resp.blob();
  return { blob, headers: resp.headers };
}

/**
 * 2026-05-27(批次 4):显式 Api interface — 防 silent failure。
 * 起源:P3 D3.1 时 useAuthorCompass.ts 调 api.put 但 client 漏了 put 方法,
 * inline const 类型推断未拦下,运行时才崩 TypeError。
 * 显式 interface 后,调任何不在此列的方法 → vue-tsc 立刻报"Property does not exist"。
 */
export interface Api {
  get<T>(path: string, opts?: RequestOptions): Promise<T>;
  post<T>(path: string, body?: unknown, opts?: RequestOptions): Promise<T>;
  put<T>(path: string, body?: unknown, opts?: RequestOptions): Promise<T>;
  patch<T>(path: string, body?: unknown, opts?: RequestOptions): Promise<T>;
  delete<T = void>(path: string, opts?: RequestOptions): Promise<T>;
  downloadFile: typeof downloadFile;
  streamSSE(
    path: string,
    body: unknown,
    handlers: {
      onEvent: (ev: { event: string; data: Record<string, unknown> }) => void;
      onDone?: () => void;
      onError?: (e: Error) => void;
    },
    opts?: RequestOptions,
  ): Promise<void>;
}

/**
 * SSE 流式 POST 实现(2026-05-30²).
 * 用 fetch+ReadableStream 而非 EventSource,保留 POST + Bearer.
 */
async function streamSSE(
  path: string,
  body: unknown,
  handlers: {
    onEvent: (ev: { event: string; data: Record<string, unknown> }) => void;
    onDone?: () => void;
    onError?: (e: Error) => void;
  },
  opts: RequestOptions = {},
): Promise<void> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "Accept": "text/event-stream",
  };
  if (!opts.skipAuth) {
    const token = getToken();
    if (token) headers["Authorization"] = `Bearer ${token}`;
  }
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(body ?? {}),
    });
  } catch (e) {
    handlers.onError?.(new ApiError(0, "NETWORK_ERROR", "网络异常"));
    return;
  }
  if (!resp.ok || !resp.body) {
    if (resp.status === 401) onUnauthorized();
    handlers.onError?.(
      new ApiError(resp.status, "STREAM_FAILED",
        `SSE 流建立失败 (${resp.status})`),
    );
    return;
  }
  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      // SSE 帧由 \n\n 分隔
      let idx: number;
      while ((idx = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        // 单帧可能有多行:event: X / data: Y
        let evName = "message";
        const dataLines: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith("event:")) {
            evName = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            dataLines.push(line.slice(5).trim());
          }
        }
        if (dataLines.length === 0) continue;
        const dataStr = dataLines.join("\n");
        try {
          const data = JSON.parse(dataStr) as Record<string, unknown>;
          handlers.onEvent({ event: evName, data });
        } catch {
          // 非 JSON 数据帧,忽略
        }
      }
    }
    handlers.onDone?.();
  } catch (e) {
    handlers.onError?.(e instanceof Error ? e : new Error(String(e)));
  }
}

export const api: Api = {
  get<T>(path: string, opts?: RequestOptions): Promise<T> {
    return request<T>("GET", path, undefined, opts);
  },
  post<T>(path: string, body?: unknown, opts?: RequestOptions): Promise<T> {
    return request<T>("POST", path, body, opts);
  },
  put<T>(path: string, body?: unknown, opts?: RequestOptions): Promise<T> {
    return request<T>("PUT", path, body, opts);
  },
  patch<T>(path: string, body?: unknown, opts?: RequestOptions): Promise<T> {
    return request<T>("PATCH", path, body, opts);
  },
  delete<T = void>(path: string, opts?: RequestOptions): Promise<T> {
    return request<T>("DELETE", path, undefined, opts);
  },
  downloadFile,
  /**
   * SSE 流式 POST(2026-05-30²):用于长任务推送 stage 进度.
   *
   * 后端返 text/event-stream(StreamingResponse),前端用 fetch+ReadableStream
   * 拆 SSE 帧.每个事件 onEvent({event, data}) 调一次.最后 onDone() / onError(e).
   *
   * 为什么不用 EventSource:
   *   EventSource 只支持 GET + 无法设 Bearer header.
   *   我们用 fetch + reader,可保留 POST + Authorization.
   */
  streamSSE(
    path: string,
    body: unknown,
    handlers: {
      onEvent: (ev: { event: string; data: Record<string, unknown> }) => void;
      onDone?: () => void;
      onError?: (e: Error) => void;
    },
    opts: RequestOptions = {},
  ): Promise<void> {
    return streamSSE(path, body, handlers, opts);
  },
};
