<script setup lang="ts">
/**
 * LoginModal — 邮箱 OTP 登录(协议合并版)。
 *
 * 流程:
 *   1. phase=email: 输邮箱 + 协议 checkbox → 发送验证码(必须勾选才能发)
 *      - 协议链接点击 → 弹本地 DocumentViewer
 *      - DocumentViewer 点"我已阅读" → @read → 自动勾选 checkbox + 关闭 viewer
 *      - 用户也可直接勾 checkbox(快路径,等价于"我已阅读")
 *   2. phase=code: 输 6 位码 → 验证 → 拿 token → 静默 POST /api/consent 落证据链 → 关闭
 */
import { computed, onUnmounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { ApiError } from "../api/types";
import { useAuthStore } from "../stores/auth";
import { useConsentStore } from "../stores/consent";
import { useLoginModal } from "../composables/useLoginModal";
import { LEGAL_DOCUMENT } from "../constants/legal-docs";
import DocumentViewer from "./DocumentViewer.vue";
import BeianFooter from "./BeianFooter.vue";

const router = useRouter();
const auth = useAuthStore();
const consent = useConsentStore();
const loginModal = useLoginModal();

type Phase = "email" | "code";
const phase = ref<Phase>("email");

const email = ref("");
const code = ref("");
const sending = ref(false);
const verifying = ref(false);
const errorMessage = ref<string | null>(null);

// 协议同意 + 查看器状态
const protocolAgreed = ref(false);
const viewerOpen = ref(false);

// 60s 重发倒计时
const cooldown = ref(0);
let cooldownTimer: number | null = null;

function startCooldown(seconds: number) {
  cooldown.value = seconds;
  if (cooldownTimer) clearInterval(cooldownTimer);
  cooldownTimer = window.setInterval(() => {
    cooldown.value--;
    if (cooldown.value <= 0) {
      if (cooldownTimer) {
        clearInterval(cooldownTimer);
        cooldownTimer = null;
      }
    }
  }, 1000);
}

onUnmounted(() => {
  if (cooldownTimer) clearInterval(cooldownTimer);
});

// 模态打开时重置状态
watch(
  () => loginModal.isOpen.value,
  (isOpen) => {
    if (isOpen) {
      phase.value = "email";
      email.value = "";
      code.value = "";
      protocolAgreed.value = false;
      viewerOpen.value = false;
      errorMessage.value = null;
    }
  },
);

const emailValid = computed(() =>
  /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.value.trim()),
);
const codeValid = computed(() => /^\d{6}$/.test(code.value.trim()));
const canSendOtp = computed(
  () => emailValid.value && protocolAgreed.value && !sending.value,
);

function openViewer() {
  viewerOpen.value = true;
}
function handleViewerRead() {
  protocolAgreed.value = true;
}
function handleViewerClose() {
  viewerOpen.value = false;
}

async function handleSendOtp() {
  errorMessage.value = null;
  if (!emailValid.value) {
    errorMessage.value = "邮箱格式不对";
    return;
  }
  if (!protocolAgreed.value) {
    errorMessage.value = "请先勾选协议";
    return;
  }
  sending.value = true;
  try {
    await auth.sendOtp(email.value.trim());
    phase.value = "code";
    startCooldown(60);
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.code === "RATE_LIMITED") {
        const wait =
          (e.detail as { retry_after_seconds?: number })?.retry_after_seconds ??
          60;
        errorMessage.value = `操作太频繁,请 ${wait} 秒后再试`;
        startCooldown(wait);
        phase.value = "code";
      } else if (e.code === "SMTP_NOT_CONFIGURED") {
        // 2026-06-02:不暴露后端配置细节,改友好文案
        errorMessage.value = "邮件服务暂时不可用,请稍后再试";
      } else {
        errorMessage.value = e.message || "登录失败,请稍后再试";
      }
    } else {
      errorMessage.value = "未知错误,请稍后重试";
    }
  } finally {
    sending.value = false;
  }
}

async function handleResend() {
  if (cooldown.value > 0 || sending.value) return;
  await handleSendOtp();
}

async function handleVerify() {
  errorMessage.value = null;
  if (!codeValid.value) {
    errorMessage.value = "请输入 6 位数字验证码";
    return;
  }
  verifying.value = true;
  try {
    await auth.verifyOtp(email.value.trim(), code.value.trim());

    // 静默落 consent record(证据链 + 法务追偿用)
    // 失败不阻塞登录(用户已拿到 token,后续可以补)
    try {
      await consent.submit({
        adult: true,
        terms: true,
        privacy: true,
        pricing: true,
      });
    } catch (consentErr) {
      if (import.meta.env.DEV) {
        console.warn("consent 落库失败,但不阻塞登录:", consentErr);
      }
    }

    const redirect = loginModal.consumeRedirect();
    loginModal.close();
    if (redirect) {
      router.push(redirect);
    }
  } catch (e) {
    errorMessage.value = e instanceof ApiError ? e.message : "验证失败";
  } finally {
    verifying.value = false;
  }
}

function handleClose() {
  loginModal.close();
}

function handleBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) handleClose();
}

function backToEmail() {
  phase.value = "email";
  code.value = "";
  errorMessage.value = null;
}
</script>

<template>
  <transition name="modal-fade">
    <div
      v-if="loginModal.isOpen.value"
      class="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="登录"
      @click="handleBackdrop"
      @keydown.esc="handleClose"
    >
      <div class="modal-card surface">
        <button class="close-btn" type="button" aria-label="关闭" @click="handleClose">
          ×
        </button>

        <header class="modal-header">
          <h2 class="modal-title">登录浑晶</h2>
          <p class="modal-subtitle">
            <template v-if="phase === 'email'">
              输入邮箱,我们会发一个 6 位验证码给你
            </template>
            <template v-else>
              验证码已发送至 <span class="email-mono">{{ email }}</span>
            </template>
          </p>
        </header>

        <!-- Phase 1:邮箱 + 协议 -->
        <form v-if="phase === 'email'" @submit.prevent="handleSendOtp" class="form">
          <label for="login-email" class="field-label">邮箱地址</label>
          <input
            id="login-email"
            v-model="email"
            type="email"
            placeholder="your@email.com"
            autocomplete="email"
            autofocus
            class="text-input"
            :disabled="sending"
          />

          <!-- 协议勾选行 -->
          <label class="protocol-row">
            <input
              v-model="protocolAgreed"
              type="checkbox"
              class="cb"
              :disabled="sending"
            />
            <span class="cb-mark" aria-hidden="true" />
            <span class="cb-text">
              我已阅读并同意
              <button
                type="button"
                class="doc-link"
                @click.prevent="openViewer"
              >《{{ LEGAL_DOCUMENT.shortName }}》</button>
            </span>
          </label>

          <button
            type="submit"
            class="primary-btn"
            :disabled="!canSendOtp"
          >
            {{ sending ? "发送中…" : "发送验证码" }}
          </button>
        </form>

        <!-- Phase 2:验证码 -->
        <form v-else @submit.prevent="handleVerify" class="form">
          <label for="login-code" class="field-label">6 位验证码</label>
          <input
            id="login-code"
            v-model="code"
            type="text"
            inputmode="numeric"
            maxlength="6"
            autocomplete="one-time-code"
            autofocus
            class="text-input code-input mono"
            :disabled="verifying"
            placeholder="······"
          />

          <div class="resend-row">
            <button
              type="button"
              class="link-btn"
              :disabled="cooldown > 0 || sending"
              @click="handleResend"
            >
              <span v-if="cooldown > 0">{{ cooldown }}s 后可重发</span>
              <span v-else>重新发送</span>
            </button>
            <button type="button" class="link-btn" @click="backToEmail">
              改邮箱
            </button>
          </div>

          <button
            type="submit"
            class="primary-btn"
            :disabled="!codeValid || verifying"
          >
            {{ verifying ? "验证中…" : "验证并进入" }}
          </button>
        </form>

        <p v-if="errorMessage" class="error-msg">{{ errorMessage }}</p>

        <p class="footer-hint">
          没收到?检查垃圾邮件,或 60 秒后重发
        </p>

        <!-- 2026-06-04:ICP 备案号(工信部合规)— 登录卡底部,新用户首屏建立信任 -->
        <BeianFooter variant="modal" />
      </div>

      <!-- 协议查看器(本地,绑 @read 自动勾选) -->
      <DocumentViewer
        :open="viewerOpen"
        @close="handleViewerClose"
        @read="handleViewerRead"
      />
    </div>
  </transition>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(31, 31, 30, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  max-width: 420px;
  padding: var(--space-8);
  position: relative;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-modal);
}

.close-btn {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-muted);
  border-radius: var(--radius-full);
  transition: all var(--duration-fast) var(--ease-out);
}

.close-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.modal-header {
  margin-bottom: var(--space-6);
}

.modal-title {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: var(--space-2);
}

.modal-subtitle {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: var(--line-normal);
}

.email-mono {
  font-family: var(--font-mono);
  color: var(--color-text);
  font-size: var(--text-xs);
}

.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.field-label {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
  margin-bottom: 2px;
}

.text-input {
  width: 100%;
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-base);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  outline: none;
  transition: border-color var(--duration-fast) var(--ease-out),
              box-shadow var(--duration-fast) var(--ease-out);
}

.text-input:focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.12);
}

.text-input:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.code-input {
  font-size: var(--text-2xl);
  letter-spacing: 0.4em;
  text-align: center;
  padding-left: 0.4em;
}

/* ===== 协议勾选行 ===== */
.protocol-row {
  display: grid;
  grid-template-columns: auto 1fr;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  position: relative;
  padding: var(--space-2) 0;
  user-select: none;
}

.cb {
  position: absolute;
  opacity: 0;
  pointer-events: none;
}

.cb-mark {
  width: 16px;
  height: 16px;
  border: 1.5px solid var(--color-border-strong);
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--color-surface);
  transition: all var(--duration-fast) var(--ease-out);
  flex-shrink: 0;
}

.cb:checked + .cb-mark {
  background: var(--color-accent);
  border-color: var(--color-accent);
}

.cb:checked + .cb-mark::after {
  content: "✓";
  color: var(--color-text-on-accent);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
}

.cb:focus-visible + .cb-mark {
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.2);
}

.cb-text {
  font-size: var(--text-sm);
  color: var(--color-text);
  line-height: var(--line-normal);
}

.doc-link {
  background: none;
  border: none;
  padding: 0;
  font: inherit;
  color: var(--color-accent);
  cursor: pointer;
  text-decoration: underline;
  text-underline-offset: 2px;
  text-decoration-color: rgba(124, 58, 237, 0.4);
}

.doc-link:hover {
  color: var(--color-accent-hover);
  text-decoration-color: var(--color-accent);
}

.resend-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: var(--text-sm);
  margin-top: -4px;
}

.primary-btn {
  width: 100%;
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-base);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) var(--ease-out);
  margin-top: var(--space-2);
}

.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.primary-btn:disabled {
  background: var(--color-text-subtle);
  cursor: not-allowed;
}

.link-btn {
  font-size: var(--text-sm);
  color: var(--color-accent);
  background: none;
  border: none;
  padding: 0;
}

.link-btn:hover:not(:disabled) {
  text-decoration: underline;
  text-underline-offset: 2px;
}

.link-btn:disabled {
  color: var(--color-text-subtle);
  cursor: not-allowed;
}

.error-msg {
  margin-top: var(--space-3);
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-radius: var(--radius-md);
}

.footer-hint {
  margin-top: var(--space-5);
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  text-align: center;
}

</style>
