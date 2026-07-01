<script setup lang="ts">
/**
 * AddonPurchaseModal — 加购 credit 包弹窗(Sprint C.1)。
 *
 * 触发:QuotaIndicator "+ 加购" 按钮 / SimulationDock credit 不足时引导 / 用户菜单
 * 后端:POST /api/credit/addon/purchase(Sprint C.5 接真支付前 mock 模拟成功)
 *
 * UX:
 *   - 3 个加购包卡片(小 / 中 / 大),价格 + credit + 单价 + 比订阅贵 X% 标签
 *   - 选中卡片紫色边框 + 角标
 *   - 大包"最划算"高亮(单 c 最低)
 *   - 1 年有效期提示
 *   - 购买按钮 → 调 API → toast.success + 刷新 quota → 关 modal
 *
 * a11y:role="dialog" + aria-modal + Esc 关闭
 */
import { onBeforeUnmount, onMounted, ref } from "vue";

import {
  ADDON_PACKAGES,
  type AddonPackageDef,
  type AddonPackageSize,
} from "../api/types";
import { useAddonModal } from "../composables/useAddonModal";
import { useQuotaStore } from "../stores/quota";
import { useUpgradeModal } from "../composables/useUpgradeModal";
import { usePayment } from "../composables/usePayment";
import { toast } from "../composables/useToast";

const addonModal = useAddonModal();
const upgradeModal = useUpgradeModal();
const quota = useQuotaStore();
const pay = usePayment();

const selectedSize = ref<AddonPackageSize | null>(null);
const purchasing = ref(false);

function selectPackage(pkg: AddonPackageDef) {
  if (purchasing.value) return;
  selectedSize.value = pkg.size;
}

function handlePurchase() {
  if (!selectedSize.value || purchasing.value) return;
  // 2026-06-09 商业化重塑:加购走统一支付(原 mock 即时发放 → 真实订单 + 人工核验)
  // SKU code = credit_<size>(small/medium/large)
  const skuCode = `credit_${selectedSize.value}`;
  addonModal.close();
  selectedSize.value = null;
  pay.open(skuCode, {
    onFulfilled: () => {
      void quota.refresh();
      toast.success("配额已到账");
    },
  });
}

function handleClose() {
  if (purchasing.value) {
    toast.info("加购处理中,请等待完成...");
    return;
  }
  addonModal.close();
  selectedSize.value = null;
}

function handleBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) handleClose();
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape" && addonModal.isOpen.value) {
    e.preventDefault();
    handleClose();
  }
}

onMounted(() => document.addEventListener("keydown", onKeydown));
onBeforeUnmount(() => document.removeEventListener("keydown", onKeydown));

function formatPrice(cents: number): string {
  return `¥${(cents / 100).toFixed(0)}`;
}

function switchToUpgrade() {
  // Sprint C.3:用户偏向升档(单价更低)→ 关 addon modal 开 upgrade modal
  addonModal.close();
  upgradeModal.open();
}
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="addonModal.isOpen.value"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        aria-labelledby="addon-title"
        @click="handleBackdrop"
      >
        <div class="modal-card surface" role="document">
          <button
            class="close-btn"
            type="button"
            aria-label="关闭"
            :disabled="purchasing"
            @click="handleClose"
          >×</button>

          <header class="modal-header">
            <h2 id="addon-title" class="modal-title">加购 Credit</h2>
            <p class="modal-subtitle">
              当月订阅 credit 不够用?加购包独立钱包,**从购买日起 1 年有效**,
              月末不清零。
            </p>
          </header>

          <div class="packages-grid">
            <button
              v-for="pkg in ADDON_PACKAGES"
              :key="pkg.size"
              type="button"
              class="package-card"
              :class="{
                'is-selected': selectedSize === pkg.size,
                'is-best-deal': pkg.size === 'large',
              }"
              :disabled="purchasing"
              :aria-pressed="selectedSize === pkg.size"
              @click="selectPackage(pkg)"
            >
              <span
                v-if="pkg.size === 'large'"
                class="best-deal-badge"
                aria-hidden="true"
              >最划算</span>

              <div class="package-name">{{ pkg.label }}</div>
              <div class="package-credits mono">
                {{ pkg.credits.toLocaleString() }} <span class="c-unit">c</span>
              </div>
              <div class="package-price mono">{{ formatPrice(pkg.price_cents) }}</div>

              <div class="package-unit-price">
                ¥{{ pkg.unit_price_yuan.toFixed(2) }} / credit
              </div>

              <div class="package-meta">
                比 Pro 订阅贵 {{ pkg.premium_over_pro_pct }}%
              </div>

              <span
                v-if="selectedSize === pkg.size"
                class="selected-badge"
                aria-hidden="true"
              >✓ 选中</span>
            </button>
          </div>

          <p class="footer-hint">
            ⓘ 加购 credit 与订阅 credit 是独立钱包。消耗时优先扣订阅(月末清零),
            订阅用完才扣加购,**让你的 1 年加购最划算**。
          </p>

          <p class="upgrade-hint">
            💡 更划算:每月 5 元开通「自携密钥」即可用满全部功能、不占额度;
            或升 Max 拿更大 credit 池,
            <button type="button" class="upgrade-link" @click="switchToUpgrade">
              查看方案 →
            </button>
          </p>

          <footer class="modal-footer">
            <button
              type="button"
              class="btn btn-ghost"
              :disabled="purchasing"
              @click="handleClose"
            >取消</button>
            <button
              type="button"
              class="btn btn-primary"
              :disabled="!selectedSize || purchasing"
              @click="handlePurchase"
            >
              <span v-if="purchasing">支付中…</span>
              <span v-else-if="selectedSize">
                立即加购 {{
                  formatPrice(
                    ADDON_PACKAGES.find((p) => p.size === selectedSize)?.price_cents ?? 0
                  )
                }}
              </span>
              <span v-else>请选择加购包</span>
            </button>
          </footer>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(31, 31, 30, 0.4);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  max-width: 720px;
  max-height: calc(100vh - var(--space-8));
  padding: var(--space-6) var(--space-6) var(--space-5);
  position: relative;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-modal);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.close-btn {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  color: var(--color-text-subtle);
  background: transparent;
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.close-btn:hover:not(:disabled) {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.close-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.modal-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.modal-title {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.modal-subtitle {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin: 0;
  line-height: 1.6;
}

/* ===== 3 包网格 ===== */
.packages-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: var(--space-3);
}
@media (max-width: 640px) {
  .packages-grid { grid-template-columns: 1fr; }
}

.package-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  padding: var(--space-4) var(--space-3);
  background: var(--color-bg-subtle);
  border: 2px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  text-align: center;
  transition: all var(--duration-fast) var(--ease-out);
  outline: none;
}
.package-card:hover:not(:disabled) {
  border-color: var(--color-accent-border);
  transform: translateY(-1px);
}
.package-card.is-selected {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}
.package-card.is-best-deal {
  border-color: var(--color-accent-border);
}
.package-card:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.best-deal-badge {
  position: absolute;
  top: -8px;
  left: 50%;
  transform: translateX(-50%);
  padding: 2px var(--space-2);
  font-size: 10px;
  font-weight: 600;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-full);
  letter-spacing: 0.04em;
}

.package-name {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-muted);
}
.package-credits {
  font-size: var(--text-2xl);
  font-weight: 700;
  color: var(--color-accent-text);
  line-height: 1.1;
  margin-top: var(--space-1);
}
.c-unit {
  font-size: var(--text-md);
  color: var(--color-text-muted);
  font-weight: 500;
}
.package-price {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin-top: var(--space-2);
}
.package-unit-price {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.package-meta {
  font-size: 11px;
  color: var(--color-text-subtle);
  margin-top: var(--space-1);
}

.selected-badge {
  position: absolute;
  bottom: var(--space-2);
  right: var(--space-2);
  padding: 1px 6px;
  font-size: 10px;
  color: #fff;
  background: var(--color-accent);
  border-radius: var(--radius-sm);
}

.footer-hint {
  margin: 0;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  line-height: 1.6;
}

.upgrade-hint {
  margin: var(--space-2) 0 0;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
  line-height: 1.6;
}
.upgrade-link {
  margin-left: 4px;
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-accent-text);
  background: transparent;
  cursor: pointer;
  text-decoration: underline;
}
.upgrade-link:hover {
  filter: brightness(0.85);
}

/* ===== footer ===== */
.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
}
.btn {
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
  font-weight: 500;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.btn-ghost {
  color: var(--color-text);
  background: transparent;
  border: 1px solid var(--color-border);
}
.btn-ghost:hover:not(:disabled) {
  background: var(--color-surface-hover);
  border-color: var(--color-border-strong);
}
.btn-primary {
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: 1px solid var(--color-accent);
}
.btn-primary:hover:not(:disabled) {
  background: var(--color-accent-hover);
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.mono { font-family: var(--font-mono); }

</style>
