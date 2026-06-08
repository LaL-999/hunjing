/**
 * Consent Pinia store — 当前用户的协议同意状态。
 *
 * 三态:
 *   未检查         checked=false           等 refresh
 *   已检查未同意   checked=true, hasConsented=false  → ConsentGate 弹出
 *   已检查已同意   checked=true, hasConsented=true   → 正常使用
 *
 * 行为:
 *   refresh()  调 GET /api/consent,根据是否含 LEGAL_DOC_VERSION 决定 hasConsented
 *   submit()   POST /api/consent,成功后 hasConsented=true
 *   reset()    清状态(用户登出时)
 *
 * 持久化:不持久化(每次刷新都 refresh,后端是单一事实源)。
 */
import { defineStore } from "pinia";
import { ref } from "vue";

import { api } from "../api/client";
import type {
  ConsentChecks,
  ConsentRecord,
  CreateConsentRequest,
} from "../api/types";
import { LEGAL_DOC_VERSION } from "../constants/legal-docs";

export const useConsentStore = defineStore("consent", () => {
  const checked = ref(false);
  const hasConsented = ref(false);
  const records = ref<ConsentRecord[]>([]);
  const refreshing = ref(false);
  const submitting = ref(false);

  async function refresh(): Promise<void> {
    if (refreshing.value) return;
    refreshing.value = true;
    try {
      const list = await api.get<ConsentRecord[]>("/consent");
      records.value = list;
      hasConsented.value = list.some(
        (r) => r.version === LEGAL_DOC_VERSION,
      );
      checked.value = true;
    } catch {
      // 401 已经被 client 处理(logout);其他错误 → 视为未检查,不阻塞
      checked.value = false;
      hasConsented.value = false;
      records.value = [];
    } finally {
      refreshing.value = false;
    }
  }

  async function submit(checks: ConsentChecks): Promise<ConsentRecord> {
    submitting.value = true;
    try {
      const body: CreateConsentRequest = {
        version: LEGAL_DOC_VERSION,
        checks,
      };
      const created = await api.post<ConsentRecord>("/consent", body);
      records.value.unshift(created);
      hasConsented.value = true;
      checked.value = true;
      return created;
    } finally {
      submitting.value = false;
    }
  }

  function reset() {
    checked.value = false;
    hasConsented.value = false;
    records.value = [];
  }

  return {
    checked,
    hasConsented,
    records,
    refreshing,
    submitting,
    refresh,
    submit,
    reset,
  };
});
