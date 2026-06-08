/**
 * Quota Pinia store — 当前用户的配额状态。
 *
 * 设计:
 *   - 后端单一事实源,前端不缓存复杂逻辑
 *   - refresh 触发时机:登录后、创建项目后、refine 后(成功 / 失败都 refresh)
 *   - 不持久化到 localStorage(每次刷新走 GET /api/quota)
 */
import { defineStore } from "pinia";
import { ref } from "vue";

import { api } from "../api/client";
import type { QuotaStatus } from "../api/types";

export const useQuotaStore = defineStore("quota", () => {
  const status = ref<QuotaStatus | null>(null);
  const refreshing = ref(false);

  async function refresh(): Promise<void> {
    if (refreshing.value) return;
    refreshing.value = true;
    try {
      status.value = await api.get<QuotaStatus>("/quota");
    } catch {
      // 401 已被 client 处理(logout);其他错 → 静默失败,不阻塞 UI
      status.value = null;
    } finally {
      refreshing.value = false;
    }
  }

  function reset() {
    status.value = null;
  }

  return { status, refreshing, refresh, reset };
});
