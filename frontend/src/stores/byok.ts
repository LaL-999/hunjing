/**
 * BYOK Pinia store — 自携密钥状态 + configs 全集
 *
 * 设计:
 *   - status:全局状态(已激活 / 有未用月卡 / 配置数量),sidebar 入口判断用
 *   - configs:用户全部 provider 配置,配置页用
 *   - 单一事实源 = 后端;前端缓存 status,configs 由配置页主动 fetch
 *
 * refresh 时机:
 *   - 登录后(App.vue / auth store)
 *   - 激活码激活后
 *   - 主动停用后
 *   - 配置 upsert / delete 后
 */
import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { api } from "../api/client";
import type {
  BYOKActivateRequest,
  BYOKActivateResponse,
  BYOKConfigResponse,
  BYOKConfigsListResponse,
  BYOKConfigUpsertRequest,
  BYOKPurchaseRequest,
  BYOKPurchaseResponse,
  BYOKStatusResponse,
  BYOKTestResponse,
} from "../api/types";

export const useBYOKStore = defineStore("byok", () => {
  const status = ref<BYOKStatusResponse | null>(null);
  const configs = ref<BYOKConfigResponse[]>([]);
  const loading = ref(false);

  // ============================================================
  // Status
  // ============================================================

  async function refreshStatus(): Promise<void> {
    try {
      status.value = await api.get<BYOKStatusResponse>("/byok/status");
    } catch {
      status.value = null;
    }
  }

  // ============================================================
  // 购买 + 激活
  // ============================================================

  async function purchase(months = 1): Promise<BYOKPurchaseResponse> {
    const req: BYOKPurchaseRequest = { months };
    const resp = await api.post<BYOKPurchaseResponse>("/byok/purchase", req);
    await refreshStatus();
    return resp;
  }

  async function activate(code: string): Promise<BYOKActivateResponse> {
    const req: BYOKActivateRequest = { code: code.trim().toUpperCase() };
    const resp = await api.post<BYOKActivateResponse>("/byok/activate", req);
    await refreshStatus();
    return resp;
  }

  async function deactivate(): Promise<void> {
    await api.post<{ deactivated: boolean }>("/byok/deactivate", {});
    await refreshStatus();
  }

  // ============================================================
  // 配置 CRUD
  // ============================================================

  async function fetchConfigs(): Promise<void> {
    loading.value = true;
    try {
      const resp = await api.get<BYOKConfigsListResponse>("/byok/configs");
      configs.value = resp.configs;
    } finally {
      loading.value = false;
    }
  }

  async function upsertConfig(
    req: BYOKConfigUpsertRequest,
  ): Promise<BYOKConfigResponse> {
    const resp = await api.put<BYOKConfigResponse>("/byok/configs", req);
    await fetchConfigs();
    await refreshStatus();
    return resp;
  }

  async function setDefault(configId: string): Promise<BYOKConfigResponse> {
    const resp = await api.post<BYOKConfigResponse>(
      `/byok/configs/${configId}/default`,
      {},
    );
    await fetchConfigs();
    await refreshStatus();
    return resp;
  }

  async function deleteConfig(configId: string): Promise<void> {
    await api.delete<{ deleted: boolean }>(`/byok/configs/${configId}`);
    await fetchConfigs();
    await refreshStatus();
  }

  async function testConfig(configId: string): Promise<BYOKTestResponse> {
    const resp = await api.post<BYOKTestResponse>(
      `/byok/configs/${configId}/test`,
      {},
    );
    // 测试结果反映到 configs(latency / last_test_ok),刷新
    await fetchConfigs();
    return resp;
  }

  // ============================================================
  // 派生
  // ============================================================

  const isActive = computed(() => status.value?.has_active_subscription ?? false);
  const hasUnusedCode = computed(
    () => status.value?.has_unused_subscription ?? false,
  );

  // ============================================================
  // Reset(登出时调)
  // ============================================================

  function reset() {
    status.value = null;
    configs.value = [];
  }

  return {
    status,
    configs,
    loading,
    isActive,
    hasUnusedCode,
    refreshStatus,
    purchase,
    activate,
    deactivate,
    fetchConfigs,
    upsertConfig,
    setDefault,
    deleteConfig,
    testConfig,
    reset,
  };
});
