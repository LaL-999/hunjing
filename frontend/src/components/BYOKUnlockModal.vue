<!--
  BYOKUnlockModal.vue — "开挂开关" / 自携密钥解锁弹窗

  入口:AppSidebar 左下角"自携密钥"按钮 → 触发本 modal
  内容:
    - 用户当前状态显示(未购买 / 已购买未激活 / 已激活)
    - 状态 1:未购买 → 显示购买按钮(30 元/月)
    - 状态 2:已购买未激活 → 显示激活码 + "复制" + 输入框(其实是粘贴自己复制的码)
    - 状态 3:已激活 → 显示"已激活,有效期至 XX",底部"前往配置" + "停用"

  注意:激活码输入框允许用户填入自己保存的码(支持跨设备同步)
-->

<template>
  <transition name="modal-fade">
    <div v-if="isOpen" class="modal-backdrop" @click.self="close">
      <div class="modal-card">
        <header class="modal-header">
          <div class="title-wrap">
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="20" height="20" viewBox="0 0 24 24"
              fill="none" stroke="currentColor" stroke-width="1.8"
              stroke-linecap="round" stroke-linejoin="round"
              class="title-icon"
            >
              <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
            </svg>
            <h2 class="modal-title">自携密钥</h2>
          </div>
          <button class="close-btn" aria-label="关闭" @click="close">×</button>
        </header>

        <div class="modal-body">
          <!-- 状态:已激活 -->
          <template v-if="status?.has_active_subscription">
            <div class="state-card state-card--active">
              <div class="state-icon">●</div>
              <div class="state-content">
                <div class="state-title">自携密钥已激活</div>
                <div class="state-sub">
                  有效期至 {{ formatDate(status.active_subscription_expires_at) }}
                  · 当前配置 {{ status.configs_count }} 个模型
                </div>
              </div>
            </div>

            <div class="action-row">
              <button class="btn btn--primary" @click="gotoConfig">前往配置 →</button>
              <button class="btn btn--ghost" @click="handleDeactivate" :disabled="deactivating">
                {{ deactivating ? "停用中…" : "暂时停用" }}
              </button>
            </div>
          </template>

          <!-- 状态:有未激活的月卡 -->
          <template v-else-if="status?.has_unused_subscription">
            <div class="state-card state-card--ready">
              <div class="state-icon">◐</div>
              <div class="state-content">
                <div class="state-title">已购月卡,等待激活</div>
                <div class="state-sub">输入激活码 = 解锁开关 = 启用自己的 API key</div>
              </div>
            </div>

            <div class="form-block">
              <label class="form-label">激活码</label>
              <input
                v-model="codeInput"
                type="text"
                placeholder="BYOK-XXXX-XXXX-XXXX"
                class="code-input"
                :disabled="activating"
                @keyup.enter="handleActivate"
              />
              <p v-if="activateError" class="error-msg">{{ activateError }}</p>
            </div>

            <div class="action-row">
              <button
                class="btn btn--primary"
                :disabled="!canActivate || activating"
                @click="handleActivate"
              >
                {{ activating ? "激活中…" : "激活" }}
              </button>
            </div>

            <div class="hint-block hint-block--small">
              没保存激活码?<button class="link-btn" @click="handleOpenPayment">再买一张(30 元/月)</button>
            </div>
          </template>

          <!-- 状态:未购买 -->
          <template v-else>
            <div class="state-card state-card--idle">
              <div class="state-icon">○</div>
              <div class="state-content">
                <div class="state-title">未开通自携密钥</div>
                <div class="state-sub">
                  解锁后可在平台用你自己的 API key 跑模型,不消耗平台 credit
                </div>
              </div>
            </div>

            <ul class="perk-list">
              <li>使用你自己账号的 DeepSeek / Qwen / GLM / Doubao / Kimi 等 API</li>
              <li>支持完全自定义模型(任意 OpenAI 兼容接口)</li>
              <li>开挂期间平台不扣 credit,LLM 算力费用由你的厂商账单结算</li>
              <li>30 元/月,过期自动回到平台默认模型</li>
            </ul>

            <div class="form-block">
              <label class="form-label">已有激活码?直接输入</label>
              <input
                v-model="codeInput"
                type="text"
                placeholder="BYOK-XXXX-XXXX-XXXX"
                class="code-input"
                :disabled="activating"
                @keyup.enter="handleActivate"
              />
              <p v-if="activateError" class="error-msg">{{ activateError }}</p>
            </div>

            <div class="action-row">
              <button class="btn btn--primary" @click="handleOpenPayment">
                购买月卡 ¥30
              </button>
              <button
                class="btn btn--ghost"
                :disabled="!canActivate || activating"
                @click="handleActivate"
              >
                {{ activating ? "激活中…" : "激活" }}
              </button>
            </div>
          </template>

          <!-- 刚购买后的临时显示 -->
          <div v-if="latestPurchasedCode" class="purchased-code-card">
            <div class="purchased-code-label">本次生成的激活码(已自动激活下面用)</div>
            <div class="purchased-code-row">
              <code class="purchased-code">{{ latestPurchasedCode }}</code>
              <button class="copy-btn" @click="copyCode(latestPurchasedCode)">
                {{ copied ? "已复制" : "复制" }}
              </button>
            </div>
            <p class="purchased-code-hint">建议复制保存 — 跨设备登录可再次输入此码激活</p>
          </div>
        </div>
      </div>
    </div>
  </transition>

  <!-- 2026-06-05 真支付流程:点购买 → 打开收款码支付流程 -->
  <BYOKPaymentModal
    :is-open="paymentModalOpen"
    @close="paymentModalOpen = false"
    @activated="handlePaymentActivated"
  />
</template>

<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRouter } from "vue-router";

import BYOKPaymentModal from "./BYOKPaymentModal.vue";

import { useBYOKStore } from "../stores/byok";
import { useAuthStore } from "../stores/auth";
import { toast } from "../composables/useToast";
import { confirm as confirmDialog } from "../composables/useConfirm";
import { ApiError } from "../api/client";

const props = defineProps<{
  isOpen: boolean;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

const byok = useBYOKStore();
const auth = useAuthStore();
const router = useRouter();

const codeInput = ref("");
const activating = ref(false);
const activateError = ref("");
const deactivating = ref(false);
const latestPurchasedCode = ref<string | null>(null);
const copied = ref(false);
const paymentModalOpen = ref(false);

const status = computed(() => byok.status);

const canActivate = computed(
  () => codeInput.value.trim().length >= 4,
);

// 打开 modal 时自动 refresh 一次
watch(
  () => props.isOpen,
  (open) => {
    if (open) {
      // 必须登录
      if (!auth.isAuthed) {
        toast.warning("请先登录后再使用自携密钥");
        emit("close");
        return;
      }
      byok.refreshStatus();
      // 重置临时状态
      codeInput.value = "";
      activateError.value = "";
      latestPurchasedCode.value = null;
      copied.value = false;
    }
  },
);

function close() {
  emit("close");
}

function formatDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

function handleOpenPayment() {
  paymentModalOpen.value = true;
}

async function handlePaymentActivated() {
  paymentModalOpen.value = false;
  // 支付通过后已经自动激活 → 关掉本 modal,提示成功
  toast.success("自携密钥已激活,前往配置");
  await byok.refreshStatus();
  router.push("/byok-config");
  emit("close");
}

async function handleActivate() {
  if (activating.value || !canActivate.value) return;
  activating.value = true;
  activateError.value = "";
  try {
    await byok.activate(codeInput.value);
    toast.success("自携密钥已激活,前往配置你的 API key");
    // 自动跳到配置页
    router.push("/byok-config");
    emit("close");
  } catch (e) {
    if (e instanceof ApiError) {
      activateError.value = e.message || "激活失败";
    } else {
      activateError.value = "激活失败,请检查激活码是否正确";
    }
  } finally {
    activating.value = false;
  }
}

async function handleDeactivate() {
  const ok = await confirmDialog({
    title: "暂时停用自携密钥?",
    message: "停用后平台 LLM 调用回到默认 key,月卡仍在有效期内,可随时再激活。",
    confirmLabel: "停用",
  });
  if (!ok) return;
  deactivating.value = true;
  try {
    await byok.deactivate();
    toast.success("已停用,LLM 调用已切回平台默认 key");
  } catch (e) {
    toast.error(e instanceof ApiError ? `停用失败:${e.message}` : "停用失败");
  } finally {
    deactivating.value = false;
  }
}

function gotoConfig() {
  router.push("/byok-config");
  emit("close");
}

async function copyCode(code: string) {
  try {
    await navigator.clipboard.writeText(code);
    copied.value = true;
    toast.success("激活码已复制");
    setTimeout(() => (copied.value = false), 2000);
  } catch {
    toast.warning("复制失败,请手动选中复制");
  }
}
</script>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(31, 31, 30, 0.4);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  /* 2026-06-06 fix:用 token 体系而不是硬编码 1000 — 否则会盖住 ConfirmDialog(z=301),
   * 导致点"暂时停用"弹出的二次确认被 BYOK 卡盖住,用户看不见 */
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  max-width: 480px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.15);
  display: flex;
  flex-direction: column;
  max-height: 90vh;
  position: relative;
  z-index: var(--z-modal);   /* 卡片在 backdrop 之上 */
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border);
}

.title-wrap {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.title-icon {
  color: var(--color-accent);
}

.modal-title {
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--color-text-primary);
  margin: 0;
}

.close-btn {
  width: 28px;
  height: 28px;
  border-radius: var(--radius-sm);
  background: transparent;
  border: none;
  color: var(--color-text-muted);
  font-size: 22px;
  line-height: 1;
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out);
}

.close-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text-primary);
}

.modal-body {
  padding: var(--space-5);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.state-card {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  border-left: 3px solid var(--color-border);
  background: var(--color-bg-subtle);
}

.state-card--active {
  border-left-color: #10b981;
  background: rgba(16, 185, 129, 0.06);
}

.state-card--ready {
  border-left-color: #f59e0b;
  background: rgba(245, 158, 11, 0.06);
}

.state-card--idle {
  border-left-color: var(--color-text-muted);
}

.state-icon {
  font-size: 18px;
  line-height: 1;
  margin-top: 1px;
}

.state-card--active .state-icon { color: #10b981; }
.state-card--ready .state-icon { color: #f59e0b; }
.state-card--idle .state-icon { color: var(--color-text-muted); }

.state-content {
  flex: 1;
}

.state-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-primary);
}

.state-sub {
  font-size: 12px;
  color: var(--color-text-secondary);
  margin-top: 2px;
}

.hint-block {
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.6;
  padding: var(--space-3);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
}

.hint-block strong {
  color: var(--color-text-primary);
}

.hint-block--small {
  font-size: 12px;
  text-align: center;
  background: transparent;
  padding: 0;
}

.perk-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.perk-list li {
  position: relative;
  padding-left: var(--space-4);
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.5;
}

.perk-list li::before {
  content: "✓";
  position: absolute;
  left: 0;
  color: var(--color-accent);
  font-weight: 600;
}

.form-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.form-label {
  font-size: 12px;
  color: var(--color-text-secondary);
  letter-spacing: 0.02em;
}

.code-input {
  width: 100%;
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-primary);
  font-family: var(--font-mono, monospace);
  font-size: 14px;
  letter-spacing: 0.05em;
  text-transform: uppercase;
}

.code-input:focus {
  outline: none;
  border-color: var(--color-accent);
  box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.15);
}

.error-msg {
  font-size: 12px;
  color: #dc2626;
  margin: 0;
}

.action-row {
  display: flex;
  gap: var(--space-3);
}

.btn {
  flex: 1;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  font-weight: 500;
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
  border: 1px solid transparent;
}

.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.btn--primary {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
}

.btn--primary:hover:not(:disabled) {
  background: var(--color-accent-hover, var(--color-accent));
  filter: brightness(1.1);
}

.btn--ghost {
  background: transparent;
  color: var(--color-text-primary);
  border-color: var(--color-border);
}

.btn--ghost:hover:not(:disabled) {
  background: var(--color-surface-hover);
}

.link-btn {
  background: none;
  border: none;
  color: var(--color-accent);
  cursor: pointer;
  text-decoration: underline;
  font: inherit;
  padding: 0;
}

.purchased-code-card {
  padding: var(--space-3) var(--space-4);
  background: rgba(124, 58, 237, 0.06);
  border: 1px dashed rgba(124, 58, 237, 0.3);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.purchased-code-label {
  font-size: 12px;
  color: var(--color-text-secondary);
}

.purchased-code-row {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.purchased-code {
  flex: 1;
  font-family: var(--font-mono, monospace);
  font-size: 14px;
  letter-spacing: 0.04em;
  color: var(--color-text-primary);
  background: var(--color-surface);
  padding: 6px 10px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
}

.copy-btn {
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border: none;
  font-size: 12px;
  cursor: pointer;
}

.purchased-code-hint {
  font-size: 11px;
  color: var(--color-text-muted);
  margin: 0;
}

.modal-fade-enter-active,
.modal-fade-leave-active {
  transition: opacity var(--duration-fast) var(--ease-out);
}

.modal-fade-enter-from,
.modal-fade-leave-to {
  opacity: 0;
}
</style>
