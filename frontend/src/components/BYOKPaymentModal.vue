<!--
  BYOKPaymentModal.vue — BYOK 个人收款码支付流程

  4 阶段 UI:
    1. show_qr     显示订单号 + 微信收款码 + 提示
    2. uploading   用户选/拖文件 + 上传截图
    3. reviewing   截图已传,自动审核中(轮询订单状态)
    4. paid        激活码已到手 + 自动 activate + 关闭

  失败分支:
    rejected       金额/收款方/时间不符 → 显示原因 + 让用户重新下单
    manual_review  自动审核犹豫 → 显示"等待人工 24h" + 已收到工单提示
    expired        24h 内未上传 → 重新下单
-->

<template>
  <!-- 2026-06-24 fix:Teleport 到 body —— 本弹窗挂在 AppSidebar 内,sidebar 折叠用了
       translateX transform,会让内部 position:fixed 相对 sidebar 定位(弹窗被困在左侧栏)。
       传送到 body 即脱离该 transform 上下文,fixed 才真正铺满全屏居中。 -->
  <Teleport to="body">
    <transition name="modal-fade">
      <div v-if="isOpen" class="modal-backdrop" @click.self="handleClose">
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
              <rect x="3" y="6" width="18" height="13" rx="2" />
              <path d="M3 10h18M7 15h2" />
            </svg>
            <h2 class="modal-title">购买自携密钥月卡</h2>
            <span class="amount-badge">¥{{ (currentOrder?.amount_cents ?? 3000) / 100 }}</span>
          </div>
          <button class="close-btn" aria-label="关闭" @click="handleClose">×</button>
        </header>

        <div class="modal-body">
          <!-- Step 1: 显示二维码 -->
          <template v-if="stage === 'show_qr' && currentOrder">
            <div class="order-banner">
              <div class="order-label">订单号</div>
              <code class="order-id">{{ currentOrder.order_id }}</code>
            </div>

            <pre class="instruction-text">{{ currentOrder.instruction_text }}</pre>

            <div class="qr-wrap">
              <div v-if="currentOrder.wechat_qr_url" class="qr-block">
                <div class="qr-label">微信收款码</div>
                <img :src="currentOrder.wechat_qr_url" alt="微信收款码" class="qr-image" />
                <div class="qr-payee">收款人:{{ currentOrder.payee_name }}</div>
              </div>
              <div v-else class="qr-block qr-block--missing">
                <div class="qr-missing-icon">⚠</div>
                <div class="qr-missing-text">
                  平台管理员还未上传微信收款码<br/>
                  请把收款码图片放到<br/>
                  <code>backend/data/payment_qrcodes/wechat_qr.png</code>
                </div>
              </div>

              <div v-if="currentOrder.alipay_qr_url" class="qr-block">
                <div class="qr-label">支付宝收款码</div>
                <img :src="currentOrder.alipay_qr_url" alt="支付宝收款码" class="qr-image" />
              </div>
            </div>

            <div class="action-row">
              <button
                class="btn btn--primary"
                @click="stage = 'uploading'"
                :disabled="!currentOrder.wechat_qr_url && !currentOrder.alipay_qr_url"
              >
                已付款,上传截图 →
              </button>
              <button class="btn btn--ghost" @click="handleClose">稍后</button>
            </div>
          </template>

          <!-- Step 2: 上传截图 -->
          <template v-else-if="stage === 'uploading' && currentOrder">
            <div class="upload-zone-wrap">
              <label
                class="upload-zone"
                :class="{ 'is-dragover': isDragover, 'has-file': !!selectedFile }"
                @dragover.prevent="isDragover = true"
                @dragleave.prevent="isDragover = false"
                @drop.prevent="handleFileDrop"
              >
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/jpg,image/webp"
                  class="hidden-file-input"
                  @change="handleFileSelect"
                />
                <template v-if="!selectedFile">
                  <svg
                    xmlns="http://www.w3.org/2000/svg"
                    width="32" height="32" viewBox="0 0 24 24"
                    fill="none" stroke="currentColor" stroke-width="1.5"
                    stroke-linecap="round" stroke-linejoin="round"
                  >
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4M17 8l-5-5-5 5M12 3v12" />
                  </svg>
                  <div class="upload-hint-title">点击或拖入付款截图</div>
                  <div class="upload-hint-sub">支持 PNG / JPG / WEBP,最大 10 MB</div>
                </template>
                <template v-else>
                  <img :src="selectedFilePreview" class="preview-img" />
                  <div class="preview-name">{{ selectedFile.name }} · {{ formatBytes(selectedFile.size) }}</div>
                </template>
              </label>
            </div>

            <div class="checklist">
              <div class="checklist-title">截图必须清晰显示这 4 项:</div>
              <ul>
                <li>付款金额:<strong>¥{{ currentOrder.amount_cents / 100 }}</strong></li>
                <li>收款方:<strong>{{ currentOrder.payee_name }}</strong>(可带 * 脱敏)</li>
                <li>付款时间(必须在订单创建后 24h 内)</li>
                <li>交易单号(微信底部"交易单号"长串)</li>
              </ul>
            </div>

            <p v-if="uploadError" class="error-msg">{{ uploadError }}</p>

            <div class="action-row">
              <button
                class="btn btn--primary"
                :disabled="!selectedFile || uploading"
                @click="handleSubmitProof"
              >
                {{ uploading ? "上传中…" : "提交审核" }}
              </button>
              <button class="btn btn--ghost" @click="stage = 'show_qr'">返回二维码</button>
            </div>
          </template>

          <!-- Step 3: 审核中 -->
          <template v-else-if="stage === 'reviewing' && currentOrder">
            <div class="state-block state-block--info">
              <div class="state-icon spinning">◐</div>
              <div class="state-content">
                <div class="state-title">系统正在自动审核…</div>
                <div class="state-sub">
                  Vision LLM 识别截图中:金额 / 收款方 / 时间 / 单号<br/>
                  通常 30 秒内完成,通过后自动激活
                </div>
              </div>
            </div>

            <div v-if="polledStatus" class="poll-detail">
              <div class="poll-row">
                <span class="poll-label">状态</span>
                <span class="poll-value">{{ statusLabel(polledStatus.status) }}</span>
              </div>
              <div v-if="polledStatus.detected_amount_cents !== null" class="poll-row">
                <span class="poll-label">识别金额</span>
                <span class="poll-value">¥{{ polledStatus.detected_amount_cents / 100 }}</span>
              </div>
              <div v-if="polledStatus.detected_payee_name" class="poll-row">
                <span class="poll-label">收款方</span>
                <span class="poll-value">{{ polledStatus.detected_payee_name }}</span>
              </div>
              <div v-if="polledStatus.detected_pay_time" class="poll-row">
                <span class="poll-label">付款时间</span>
                <span class="poll-value">{{ formatTime(polledStatus.detected_pay_time) }}</span>
              </div>
            </div>

            <div class="action-row">
              <button class="btn btn--ghost" @click="handleClose">后台等待</button>
            </div>
          </template>

          <!-- Step 4: 通过 + 已激活 -->
          <template v-else-if="stage === 'paid' && polledStatus?.activated_code">
            <div class="state-block state-block--ok">
              <div class="state-icon">✓</div>
              <div class="state-content">
                <div class="state-title">支付成功,自携密钥已自动激活!</div>
                <div class="state-sub">
                  有效期至 {{ formatDate(polledStatus.activated_expires_at) }}
                </div>
              </div>
            </div>

            <div class="purchased-code-card">
              <div class="purchased-code-label">激活码(已自动激活,可复制保存)</div>
              <div class="purchased-code-row">
                <code class="purchased-code">{{ polledStatus.activated_code }}</code>
                <button class="copy-btn" @click="copyCode(polledStatus.activated_code!)">
                  {{ copied ? "已复制" : "复制" }}
                </button>
              </div>
            </div>

            <div class="action-row">
              <button class="btn btn--primary" @click="handleGoConfig">前往配置 →</button>
              <button class="btn btn--ghost" @click="handleClose">完成</button>
            </div>
          </template>

          <!-- 失败:rejected -->
          <template v-else-if="stage === 'rejected' && polledStatus">
            <div class="state-block state-block--error">
              <div class="state-icon">×</div>
              <div class="state-content">
                <div class="state-title">审核未通过</div>
                <div class="state-sub">{{ polledStatus.rejected_reason || "截图信息与订单不符" }}</div>
              </div>
            </div>

            <div class="hint-block">
              请检查:① 金额是否 ¥30 ② 收款方是否本平台 ③ 截图是否清晰<br/>
              如确认无误,请联系客服(微信:Everovernever)申诉
            </div>

            <div class="action-row">
              <button class="btn btn--primary" @click="restartOrder">重新下单</button>
              <button class="btn btn--ghost" @click="handleClose">关闭</button>
            </div>
          </template>

          <!-- 失败:manual_review -->
          <template v-else-if="stage === 'manual_review' && polledStatus">
            <div class="state-block state-block--warn">
              <div class="state-icon">!</div>
              <div class="state-content">
                <div class="state-title">已提交人工审核</div>
                <div class="state-sub">
                  自动识别结果不够确定,我们会在 24 小时内人工核对<br/>
                  通过后激活码会发到你的注册邮箱
                </div>
              </div>
            </div>

            <div class="hint-block">
              原因:{{ polledStatus.rejected_reason || "需要人工确认" }}<br/>
              急用?联系客服微信:Everovernever
            </div>

            <div class="action-row">
              <button class="btn btn--primary" @click="handleClose">知道了</button>
            </div>
          </template>

          <!-- 初始 loading -->
          <template v-else>
            <div class="loading-state">正在准备订单…</div>
          </template>
        </div>
      </div>
    </div>
    </transition>
  </Teleport>
</template>

<script setup lang="ts">
import { onUnmounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { api } from "../api/client";
import { ApiError } from "../api/client";
import type {
  BYOKOrderStatus,
  CreatePaymentOrderRequest,
  CreatePaymentOrderResponse,
  PaymentOrderStatusResponse,
} from "../api/types";
import { useBYOKStore } from "../stores/byok";
import { toast } from "../composables/useToast";

const props = defineProps<{
  isOpen: boolean;
}>();
const emit = defineEmits<{
  (e: "close"): void;
  (e: "activated"): void;
}>();

const router = useRouter();
const byok = useBYOKStore();

type Stage =
  | "init"
  | "show_qr"
  | "uploading"
  | "reviewing"
  | "paid"
  | "rejected"
  | "manual_review";

const stage = ref<Stage>("init");
const currentOrder = ref<CreatePaymentOrderResponse | null>(null);
const polledStatus = ref<PaymentOrderStatusResponse | null>(null);

const selectedFile = ref<File | null>(null);
const selectedFilePreview = ref<string>("");
const isDragover = ref(false);
const uploading = ref(false);
const uploadError = ref("");
const copied = ref(false);

let pollTimer: ReturnType<typeof setInterval> | null = null;

watch(
  () => props.isOpen,
  async (open) => {
    if (open) {
      // 打开 → 创建订单
      stage.value = "init";
      currentOrder.value = null;
      polledStatus.value = null;
      selectedFile.value = null;
      selectedFilePreview.value = "";
      uploadError.value = "";
      copied.value = false;
      stopPolling();
      await createOrder();
    } else {
      stopPolling();
    }
  },
);

onUnmounted(() => stopPolling());

async function createOrder() {
  try {
    const req: CreatePaymentOrderRequest = { months: 1 };
    currentOrder.value = await api.post<CreatePaymentOrderResponse>(
      "/byok/payment/orders",
      req,
    );
    stage.value = "show_qr";
  } catch (e) {
    toast.error(e instanceof ApiError ? `下单失败:${e.message}` : "下单失败");
    emit("close");
  }
}

function handleFileSelect(ev: Event) {
  const input = ev.target as HTMLInputElement;
  const file = input.files?.[0];
  if (file) attachFile(file);
}

function handleFileDrop(ev: DragEvent) {
  isDragover.value = false;
  const file = ev.dataTransfer?.files[0];
  if (file) attachFile(file);
}

function attachFile(file: File) {
  if (!file.type.startsWith("image/")) {
    uploadError.value = "请选图片文件(png / jpg / webp)";
    return;
  }
  if (file.size > 10 * 1024 * 1024) {
    uploadError.value = "图片超过 10 MB";
    return;
  }
  uploadError.value = "";
  selectedFile.value = file;
  selectedFilePreview.value = URL.createObjectURL(file);
}

async function handleSubmitProof() {
  if (!selectedFile.value || !currentOrder.value || uploading.value) return;
  uploading.value = true;
  uploadError.value = "";
  try {
    const formData = new FormData();
    formData.append("file", selectedFile.value);

    // 用原生 fetch — api 客户端可能不支持 multipart
    const token = localStorage.getItem("auth_token") || "";
    const resp = await fetch(
      `/api/byok/payment/orders/${currentOrder.value.order_id}/submit_proof`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      },
    );
    if (!resp.ok) {
      const body = await resp.json().catch(() => ({}));
      const msg = body?.detail?.message || `HTTP ${resp.status}`;
      throw new Error(msg);
    }

    stage.value = "reviewing";
    startPolling();
  } catch (e) {
    uploadError.value = e instanceof Error ? e.message : "上传失败";
  } finally {
    uploading.value = false;
  }
}

function startPolling() {
  if (!currentOrder.value) return;
  const orderId = currentOrder.value.order_id;
  let pollCount = 0;
  const MAX_POLLS = 60; // 60 × 3s = 3 分钟

  const tick = async () => {
    pollCount += 1;
    try {
      const status = await api.get<PaymentOrderStatusResponse>(
        `/byok/payment/orders/${orderId}`,
      );
      polledStatus.value = status;

      if (status.status === "paid") {
        stage.value = "paid";
        stopPolling();
        await byok.refreshStatus();
        emit("activated");
        return;
      }
      if (status.status === "rejected") {
        stage.value = "rejected";
        stopPolling();
        return;
      }
      if (status.status === "manual_review") {
        stage.value = "manual_review";
        stopPolling();
        return;
      }
      if (status.status === "expired") {
        toast.warning("订单已过期,请重新下单");
        stopPolling();
        emit("close");
        return;
      }
    } catch {
      // 网络抖动 — 继续轮询
    }

    if (pollCount >= MAX_POLLS) {
      stopPolling();
      // 没拿到结果但也不报错 — 用户可关闭后再回来
    }
  };

  tick(); // 立即第 1 次
  pollTimer = setInterval(tick, 3000);
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

function statusLabel(s: BYOKOrderStatus): string {
  const map: Record<BYOKOrderStatus, string> = {
    pending: "等待付款",
    submitted: "已提交,审核中",
    paid: "已通过 ✓",
    rejected: "审核拒",
    manual_review: "待人工审核",
    expired: "已过期",
  };
  return map[s] || s;
}

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

function formatTime(iso: string): string {
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return `${d.getMonth() + 1}-${d.getDate()} ${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

async function copyCode(code: string) {
  try {
    await navigator.clipboard.writeText(code);
    copied.value = true;
    toast.success("已复制");
    setTimeout(() => (copied.value = false), 2000);
  } catch {
    toast.warning("复制失败,请手动选中复制");
  }
}

function handleGoConfig() {
  router.push("/byok-config");
  emit("close");
}

function restartOrder() {
  stage.value = "init";
  currentOrder.value = null;
  polledStatus.value = null;
  selectedFile.value = null;
  selectedFilePreview.value = "";
  createOrder();
}

function handleClose() {
  stopPolling();
  emit("close");
}
</script>

<style scoped>
.modal-backdrop {
  position: fixed; inset: 0;
  background: rgba(31, 31, 30, 0.45);
  backdrop-filter: blur(4px);
  display: flex; align-items: center; justify-content: center;
  /* 2026-06-06 fix:从硬编码 1010 改 token,否则压住 ConfirmDialog(z=301)*/
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  max-width: 560px;
  max-height: 90vh;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  position: relative;
  z-index: var(--z-modal);
  border-radius: var(--radius-lg);
  box-shadow: 0 20px 50px rgba(0, 0, 0, 0.15);
  display: flex; flex-direction: column;
}

.modal-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border);
}

.title-wrap {
  display: flex; align-items: center; gap: var(--space-2);
}

.title-icon { color: var(--color-accent); }
.modal-title { font-size: var(--text-base); font-weight: 600; margin: 0; color: var(--color-text-primary); }

.amount-badge {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  margin-left: var(--space-2);
}

.close-btn {
  width: 28px; height: 28px; border-radius: var(--radius-sm);
  background: transparent; border: 0;
  color: var(--color-text-muted); font-size: 22px; cursor: pointer;
}
.close-btn:hover { background: var(--color-surface-hover); color: var(--color-text-primary); }

.modal-body {
  padding: var(--space-5);
  overflow-y: auto;
  display: flex; flex-direction: column; gap: var(--space-4);
}

.order-banner {
  display: flex; flex-direction: column; gap: 4px;
  padding: var(--space-3);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
}
.order-label { font-size: 11px; color: var(--color-text-secondary); }
.order-id {
  font-family: var(--font-mono, monospace);
  font-size: 13px;
  color: var(--color-text-primary);
  letter-spacing: 0.05em;
}

.instruction-text {
  font-family: inherit;
  font-size: 13px;
  color: var(--color-text-secondary);
  line-height: 1.7;
  white-space: pre-wrap;
  background: var(--color-bg-subtle);
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  margin: 0;
}

.qr-wrap {
  display: flex; gap: var(--space-4); justify-content: center;
  flex-wrap: wrap;
}

.qr-block {
  display: flex; flex-direction: column; align-items: center; gap: var(--space-2);
  padding: var(--space-3);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  min-width: 180px;
}

.qr-block--missing {
  border: 1px dashed #f59e0b;
  background: rgba(245, 158, 11, 0.05);
}

.qr-missing-icon { font-size: 32px; color: #f59e0b; }
.qr-missing-text {
  font-size: 12px; color: var(--color-text-secondary); text-align: center; line-height: 1.6;
}
.qr-missing-text code {
  background: var(--color-surface);
  padding: 1px 4px;
  border-radius: 3px;
  font-size: 11px;
}

.qr-label { font-size: 12px; color: var(--color-text-secondary); }
.qr-image { width: 160px; height: 160px; object-fit: contain; background: white; padding: 8px; border-radius: 6px; }
.qr-payee { font-size: 12px; color: var(--color-text-primary); }

.action-row { display: flex; gap: var(--space-3); }

.btn {
  flex: 1;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm); font-weight: 500;
  cursor: pointer; border: 1px solid transparent;
  transition: all var(--duration-fast) var(--ease-out);
}
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn--primary { background: var(--color-accent); color: var(--color-text-on-accent); }
.btn--primary:hover:not(:disabled) { filter: brightness(1.1); }
.btn--ghost { background: transparent; color: var(--color-text-primary); border-color: var(--color-border); }
.btn--ghost:hover:not(:disabled) { background: var(--color-surface-hover); }

.upload-zone-wrap { position: relative; }
.upload-zone {
  display: flex; flex-direction: column; align-items: center; gap: var(--space-3);
  padding: var(--space-6);
  border: 2px dashed var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  color: var(--color-text-secondary);
  transition: all var(--duration-fast) var(--ease-out);
}
.upload-zone:hover, .upload-zone.is-dragover {
  border-color: var(--color-accent);
  background: rgba(124, 58, 237, 0.04);
}
.upload-zone.has-file { padding: var(--space-3); }
.hidden-file-input { display: none; }
.upload-hint-title { font-size: 14px; font-weight: 500; color: var(--color-text-primary); }
.upload-hint-sub { font-size: 12px; color: var(--color-text-muted); }
.preview-img { max-width: 200px; max-height: 200px; border-radius: var(--radius-sm); }
.preview-name { font-size: 12px; color: var(--color-text-secondary); }

.checklist {
  background: var(--color-bg-subtle);
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-sm);
}
.checklist-title { font-size: 12px; color: var(--color-text-secondary); margin-bottom: var(--space-2); }
.checklist ul {
  list-style: none; padding: 0; margin: 0;
  display: flex; flex-direction: column; gap: 4px;
  font-size: 13px; color: var(--color-text-primary);
}
.checklist li::before { content: "• "; color: var(--color-accent); margin-right: 4px; }

.error-msg { font-size: 12px; color: #dc2626; margin: 0; }

.state-block {
  display: flex; align-items: flex-start; gap: var(--space-3);
  padding: var(--space-4);
  border-radius: var(--radius-md);
  border-left: 3px solid;
}
.state-block--info { border-color: var(--color-accent); background: rgba(124, 58, 237, 0.04); }
.state-block--ok   { border-color: #10b981; background: rgba(16, 185, 129, 0.06); }
.state-block--warn { border-color: #f59e0b; background: rgba(245, 158, 11, 0.06); }
.state-block--error{ border-color: #dc2626; background: rgba(220, 38, 38, 0.06); }

.state-icon { font-size: 22px; line-height: 1; }
.state-block--info .state-icon { color: var(--color-accent); }
.state-block--ok   .state-icon { color: #10b981; }
.state-block--warn .state-icon { color: #f59e0b; }
.state-block--error .state-icon { color: #dc2626; }

.state-content { flex: 1; }
.state-title { font-size: var(--text-sm); font-weight: 600; color: var(--color-text-primary); }
.state-sub { font-size: 12px; color: var(--color-text-secondary); margin-top: 4px; line-height: 1.6; }

.spinning {
  animation: spin 1.5s linear infinite;
  display: inline-block;
}
@keyframes spin {
  from { transform: rotate(0); }
  to { transform: rotate(360deg); }
}

.poll-detail {
  background: var(--color-bg-subtle);
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  display: flex; flex-direction: column; gap: 4px;
}
.poll-row { display: flex; justify-content: space-between; font-size: 12px; }
.poll-label { color: var(--color-text-secondary); }
.poll-value { color: var(--color-text-primary); font-family: var(--font-mono, monospace); }

.hint-block {
  font-size: 12px;
  color: var(--color-text-secondary);
  background: var(--color-bg-subtle);
  padding: var(--space-3);
  border-radius: var(--radius-sm);
  line-height: 1.6;
}

.purchased-code-card {
  padding: var(--space-3) var(--space-4);
  background: rgba(124, 58, 237, 0.06);
  border: 1px dashed rgba(124, 58, 237, 0.3);
  border-radius: var(--radius-md);
  display: flex; flex-direction: column; gap: var(--space-2);
}
.purchased-code-label { font-size: 12px; color: var(--color-text-secondary); }
.purchased-code-row { display: flex; align-items: center; gap: var(--space-3); }
.purchased-code {
  flex: 1; font-family: var(--font-mono, monospace); font-size: 14px;
  color: var(--color-text-primary); background: var(--color-surface);
  padding: 6px 10px; border-radius: var(--radius-sm); border: 1px solid var(--color-border);
}
.copy-btn {
  padding: 6px 12px; border-radius: var(--radius-sm);
  background: var(--color-accent); color: var(--color-text-on-accent);
  border: 0; font-size: 12px; cursor: pointer;
}

.loading-state {
  text-align: center; color: var(--color-text-secondary);
  padding: var(--space-6) 0;
}

.modal-fade-enter-active, .modal-fade-leave-active { transition: opacity var(--duration-fast) var(--ease-out); }
.modal-fade-enter-from, .modal-fade-leave-to { opacity: 0; }
</style>
