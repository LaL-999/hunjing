/**
 * Auth Pinia store — token + 当前用户。
 *
 * 持久化:
 *   token / userId / plan / expiresAt 写入 localStorage(key: huimeng:auth:v1)
 *   refresh 时从 localStorage 恢复
 *
 * 接口:
 *   sendOtp(email)     发码
 *   verifyOtp(email, code)  验码 + 拿 token + 持久化
 *   logout()           清 token + 跳 /login
 *   isAuthed (computed)
 */
import { computed, ref } from "vue";
import { defineStore } from "pinia";

import { api, configureAuthAccessors } from "../api/client";
import type {
  CurrentUser,
  Plan,
  SendOtpResponse,
  VerifyOtpResponse,
} from "../api/types";

// Sprint D.1(2026-05-12):storage 版本 bumpv1 → v2(老 'standard'/'super' 字面量
// 已废弃,migration 021 后老 token 失效;localStorage 老快照在 plan 字段反序列化时
// 会落到 free fallback。bump key 是稳妥的做法 — 让所有老 token 重登录,plan 字段
// 与后端 v2 严格对齐)
const STORAGE_KEY = "huimeng:auth:v2";

interface AuthSnapshot {
  token: string;
  user_id: string;
  plan: Plan;
  expires_at: string;
}

function loadFromStorage(): AuthSnapshot | null {
  if (typeof window === "undefined" || !window.localStorage) return null;
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const snap = JSON.parse(raw) as AuthSnapshot;
    // 过期检查
    if (new Date(snap.expires_at).getTime() < Date.now()) {
      window.localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return snap;
  } catch {
    return null;
  }
}

function saveToStorage(snap: AuthSnapshot) {
  if (typeof window === "undefined" || !window.localStorage) return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(snap));
  } catch {
    /* 隐私模式 / 配额满 — 不阻塞登录 */
  }
}

function clearStorage() {
  if (typeof window === "undefined" || !window.localStorage) return;
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* noop */
  }
}

export const useAuthStore = defineStore("auth", () => {
  // === state ===
  const initial = loadFromStorage();
  const token = ref<string | null>(initial?.token ?? null);
  const userId = ref<string | null>(initial?.user_id ?? null);
  const plan = ref<Plan>(initial?.plan ?? "free");
  const expiresAt = ref<string | null>(initial?.expires_at ?? null);
  const currentUser = ref<CurrentUser | null>(null);

  // === computed ===
  const isAuthed = computed(() => !!token.value);

  // === actions ===

  async function sendOtp(email: string): Promise<SendOtpResponse> {
    return api.post<SendOtpResponse>(
      "/auth/send_otp",
      { email },
      { skipAuth: true },
    );
  }

  async function verifyOtp(email: string, code: string): Promise<void> {
    const resp = await api.post<VerifyOtpResponse>(
      "/auth/verify",
      { email, code },
      { skipAuth: true },
    );
    token.value = resp.token;
    userId.value = resp.user_id;
    plan.value = resp.plan;
    expiresAt.value = resp.expires_at;
    saveToStorage({
      token: resp.token,
      user_id: resp.user_id,
      plan: resp.plan,
      expires_at: resp.expires_at,
    });
  }

  async function fetchMe(): Promise<void> {
    if (!token.value) return;
    try {
      currentUser.value = await api.get<CurrentUser>("/auth/me");
      // INS-A3(2026-05-27):登录成功后同步 user_id 到洞察 SDK
      if (currentUser.value?.id) {
        import("../composables/useAnalytics").then(({ setAnalyticsUser }) => {
          setAnalyticsUser(currentUser.value?.id ?? null);
        });
      }
    } catch {
      // 401 已经被 client 触发 onUnauthorized → logout
    }
  }

  function logout() {
    token.value = null;
    userId.value = null;
    plan.value = "free";
    expiresAt.value = null;
    currentUser.value = null;
    clearStorage();
    // INS-A3(2026-05-27):登出清空 user_id + 重置 session(后续埋点匿名)
    import("../composables/useAnalytics").then(({ resetAnalyticsSession }) => {
      resetAnalyticsSession();
    });
  }

  return {
    token,
    userId,
    plan,
    expiresAt,
    currentUser,
    isAuthed,
    sendOtp,
    verifyOtp,
    fetchMe,
    logout,
  };
});

/**
 * 在 main.ts 调用一次,把 auth store 的 token getter 注入 api client。
 * 必须在 createPinia + use 之后调用。
 */
export function wireAuthIntoApiClient() {
  const auth = useAuthStore();
  configureAuthAccessors({
    getToken: () => auth.token,
    onUnauthorized: () => auth.logout(),
  });
}
