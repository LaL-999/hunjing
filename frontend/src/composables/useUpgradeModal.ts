/**
 * useUpgradeModal — 全局 singleton 控制升级订阅 modal。
 *
 * Sprint C.1 重构(2026-05-13):接两种触发原因
 *   1. QuotaExceededDetail:资源容量类硬限超出
 *      (reshape_percent / projects_total / characters_per_project)
 *   2. InsufficientCreditsDetail:AI credit 不足
 *      (refine / continuation / extract / comic_* 等)
 *
 * open(reason) 接两种 detail union,modal 内据此显示不同提示文案。
 */
import { ref } from "vue";

import type {
  InsufficientCreditsDetail,
  QuotaExceededDetail,
} from "../api/types";

export type UpgradeReason = QuotaExceededDetail | InsufficientCreditsDetail | null;

const isOpen = ref(false);
const reason = ref<UpgradeReason>(null);

export function useUpgradeModal() {
  return {
    isOpen,
    reason,
    open(detail?: UpgradeReason) {
      reason.value = detail ?? null;
      isOpen.value = true;
    },
    close() {
      isOpen.value = false;
      reason.value = null;
    },
  };
}
