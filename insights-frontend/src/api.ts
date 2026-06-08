/**
 * insights-frontend API client.
 *
 * 两套调用对象(2026-06-05):
 *   1. fetchAdmin / postAdmin → insights-backend(port 8001)— 洞察数据(read-only attach 主库)
 *   2. fetchPlatformAdmin / postPlatformAdmin → 主平台 backend(port 8000)— 需要写主库的操作
 *      典型:BYOK 订单审核(approve/reject 要 UPDATE byok_payment_orders)
 *
 * 鉴权:全部用同一个 X-Admin-Token(主平台 backend 已加双轨鉴权,接受此 header)。
 *
 * 为什么主平台 backend 也接 X-Admin-Token?
 *   - 洞察后台是 service-to-service,不持有用户 JWT
 *   - read-only attach 主库的设计原则铁律 → 不能在 insights-backend 直接 UPDATE huimeng.db
 *   - 解法:让主平台 backend 兼容 X-Admin-Token,洞察后台前端跨域调即可
 */

const INSIGHTS_BASE = (import.meta.env.VITE_INSIGHTS_BASE as string | undefined)
  || "http://localhost:8001";
const PLATFORM_BASE = (import.meta.env.VITE_PLATFORM_BASE as string | undefined)
  || "http://localhost:8000";
const ADMIN_TOKEN = (import.meta.env.VITE_ADMIN_TOKEN as string | undefined)
  || "huimeng-insights-dev-token";


async function _doRequest<T>(
  baseUrl: string,
  path: string,
  method: "GET" | "POST",
  query?: Record<string, string | number>,
  body?: unknown,
): Promise<T> {
  const url = new URL(baseUrl + path);
  if (query) {
    for (const [k, v] of Object.entries(query)) {
      url.searchParams.set(k, String(v));
    }
  }
  const headers: Record<string, string> = {
    "X-Admin-Token": ADMIN_TOKEN,
  };
  const init: RequestInit = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  const resp = await fetch(url.toString(), init);
  if (!resp.ok) {
    let errMsg = `${path} → ${resp.status}`;
    try {
      const j = await resp.json();
      // FastAPI HTTPException { detail: { code, message } }
      if (j?.detail?.message) errMsg = `${errMsg}: ${j.detail.message}`;
      else if (typeof j?.detail === "string") errMsg = `${errMsg}: ${j.detail}`;
      else errMsg = `${errMsg}: ${JSON.stringify(j)}`;
    } catch {
      errMsg = `${errMsg}: ${await resp.text()}`;
    }
    throw new Error(errMsg);
  }
  // 204 / 空响应防护
  const ct = resp.headers.get("Content-Type") || "";
  if (!ct.includes("application/json")) {
    return undefined as T;
  }
  return resp.json();
}


// ============================================================
// insights-backend(port 8001)
// ============================================================

export async function fetchAdmin<T>(
  path: string,
  query?: Record<string, string | number>,
): Promise<T> {
  return _doRequest<T>(INSIGHTS_BASE, path, "GET", query);
}


// ============================================================
// 主平台 backend(port 8000)— 需要写主库的 admin 操作走这条线
// ============================================================

export async function fetchPlatformAdmin<T>(
  path: string,
  query?: Record<string, string | number>,
): Promise<T> {
  return _doRequest<T>(PLATFORM_BASE, path, "GET", query);
}

export async function postPlatformAdmin<T>(
  path: string,
  body?: unknown,
): Promise<T> {
  return _doRequest<T>(PLATFORM_BASE, path, "POST", undefined, body);
}

/**
 * 拿主平台 backend 的二进制资源(如付款截图)。
 * 返 blob,前端用 URL.createObjectURL 转 src.
 */
export async function fetchPlatformBlob(path: string): Promise<Blob> {
  const resp = await fetch(PLATFORM_BASE + path, {
    method: "GET",
    headers: { "X-Admin-Token": ADMIN_TOKEN },
  });
  if (!resp.ok) {
    throw new Error(`fetchPlatformBlob ${path} → ${resp.status}`);
  }
  return resp.blob();
}
