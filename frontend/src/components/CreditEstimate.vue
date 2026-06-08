<script setup lang="ts">
/**
 * CreditEstimate — 通用 credit 消耗预估卡(Sprint C.3,2026-05-13)。
 *
 * 显示位置:任何 AI 动作的「提交按钮上方」— 给用户透明计量决策依据。
 *
 * UX:
 *   - 余额够:浅紫卡 + 「本次预计消耗 X c · 你余额 Y c → 跑完约 Z c」
 *   - 余额不够:红色警示卡 + 「本次预计 X c · 余额仅 Y c · 升档 / 加购」CTA
 *   - founder 档:绿色卡 + 「创始人通行 · 不扣 credit」
 *
 * a11y:role="status" + aria-live="polite"
 */
import { computed } from "vue";

import { useQuotaStore } from "../stores/quota";
import { useAddonModal } from "../composables/useAddonModal";
import { useUpgradeModal } from "../composables/useUpgradeModal";

const props = defineProps<{
  /** 预估 credit 数 */
  estimated: number;
  /** 给用户看的"基于 X 轮 / X 字"解释 */
  basis: string;
  /** 动作类型(给 modal action 上下文)*/
  action?: string;
}>();

const quota = useQuotaStore();
const addonModal = useAddonModal();
const upgradeModal = useUpgradeModal();

const balance = computed<number>(
  () => quota.status?.credit_balance.total_credits ?? 0,
);
const isFounder = computed<boolean>(
  () => quota.status?.plan === "founder",
);

const afterEstimate = computed<number>(() => balance.value - props.estimated);
const insufficient = computed<boolean>(
  () => !isFounder.value && afterEstimate.value < 0,
);

function openAddon() {
  addonModal.open();
}
function openUpgrade() {
  upgradeModal.open();
}
</script>

<template>
  <div
    class="credit-estimate"
    :class="{
      'is-founder': isFounder,
      'is-insufficient': insufficient,
    }"
    role="status"
    aria-live="polite"
  >
    <!-- founder:绿色通行 -->
    <template v-if="isFounder">
      <span class="badge badge-founder">★ 创始人</span>
      <span class="text">通行 · 本次 AI 调用不扣 credit(仍记账)</span>
    </template>

    <!-- 余额不足:红色警示 + 双 CTA -->
    <template v-else-if="insufficient">
      <div class="line">
        <span class="badge badge-danger">⚠ credit 不足</span>
        <span class="text">
          预计消耗 <strong class="mono">{{ estimated }}</strong> c ·
          余额仅 <strong class="mono">{{ balance.toLocaleString() }}</strong> c
          <span class="basis">({{ basis }})</span>
        </span>
      </div>
      <div class="cta-row">
        <button type="button" class="cta-btn cta-btn--primary" @click="openAddon">
          立即加购
        </button>
        <button type="button" class="cta-btn cta-btn--ghost" @click="openUpgrade">
          或升档 →
        </button>
      </div>
    </template>

    <!-- 正常:紫色提示 -->
    <template v-else>
      <span class="badge badge-info">ⓘ 预计</span>
      <span class="text">
        消耗 <strong class="mono">{{ estimated }}</strong> c ·
        跑完约剩 <strong class="mono">{{ afterEstimate.toLocaleString() }}</strong> c
        <span class="basis">({{ basis }})</span>
      </span>
    </template>
  </div>
</template>

<style scoped>
.credit-estimate {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: var(--color-accent-soft);
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-md);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  line-height: 1.5;
}

.credit-estimate.is-founder {
  background: rgba(22, 163, 74, 0.08);
  border-color: rgba(22, 163, 74, 0.3);
  color: #16A34A;
}

.credit-estimate.is-insufficient {
  background: var(--color-danger-soft);
  border-color: var(--color-danger);
  color: var(--color-danger);
}

.line {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.badge {
  flex-shrink: 0;
  padding: 1px var(--space-2);
  font-size: 10px;
  font-weight: 600;
  letter-spacing: 0.04em;
  border-radius: var(--radius-sm);
}
.badge-info {
  color: var(--color-accent-text);
  background: var(--color-surface);
}
.badge-founder {
  color: #fff;
  background: #16A34A;
}
.badge-danger {
  color: #fff;
  background: var(--color-danger);
}

.text {
  flex: 1;
  min-width: 0;
}

.basis {
  margin-left: 4px;
  font-size: 11px;
  opacity: 0.75;
}

.mono {
  font-family: var(--font-mono);
  font-weight: 700;
}

.cta-row {
  display: flex;
  gap: var(--space-2);
  margin-top: 2px;
}

.cta-btn {
  padding: 2px var(--space-2);
  font-size: 11px;
  font-weight: 500;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.cta-btn--primary {
  color: #fff;
  background: var(--color-danger);
  border: 1px solid var(--color-danger);
}
.cta-btn--primary:hover {
  filter: brightness(0.92);
}
.cta-btn--ghost {
  color: var(--color-danger);
  background: transparent;
  border: 1px solid var(--color-danger-soft);
}
.cta-btn--ghost:hover {
  background: rgba(220, 38, 38, 0.08);
}
</style>
