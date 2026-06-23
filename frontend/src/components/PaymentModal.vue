<script setup lang="ts">
/**
 * PaymentModal — 全局统一支付弹窗(2026-06-09 商业化重塑第一期)。
 *
 * App.vue 挂一次。任何组件 usePayment().open(skuCode) 唤起。
 * 三阶段:
 *   confirm  确认商品 + 金额 → 下单
 *   pay      扫码二维码 + 上传付款截图 → 提交凭证
 *   review   等待人工审核(轮询)→ paid(发货成功)/ rejected(驳回)
 *
 * 个人主体扫码 + 截图 + 人工复核(用户拍板)。
 */
import { computed, ref, watch, onBeforeUnmount } from "vue";

import { usePayment } from "../composables/usePayment";
import { toast } from "../composables/useToast";
import {
  createOrder,
  fetchCatalog,
  fetchPayInfo,
  getOrder,
  submitProof,
  type PaymentOrder,
  type PaymentSku,
  type PayInfo,
} from "../api/payments";
import { ApiError } from "../api/client";
import Icon from "./Icon.vue";

const pay = usePayment();

type Stage = "confirm" | "pay" | "review";
const stage = ref<Stage>("confirm");

const sku = ref<PaymentSku | null>(null);
const payInfo = ref<PayInfo | null>(null);
const order = ref<PaymentOrder | null>(null);
const loading = ref(false);
const submitting = ref(false);
const fileInput = ref<HTMLInputElement | null>(null);

let pollTimer: number | null = null;

const API_ORIGIN = import.meta.env.VITE_API_BASE || "";

const qrUrl = computed(() => {
  const u = payInfo.value?.wechat_qr_url || "";
  if (!u) return "";
  // 后端返 /api/payment-qrcodes/xxx,需拼到 API origin
  return u.startsWith("http") ? u : `${API_ORIGIN}${u}`;
});

// 打开时:载入 SKU + 收款信息,重置到 confirm
watch(
  () => pay.isOpen.value,
  async (open) => {
    if (!open) {
      stopPoll();
      return;
    }
    stage.value = "confirm";
    sku.value = null;
    order.value = null;
    payInfo.value = null;
    const code = pay.currentSkuCode.value;
    if (!code) return;
    loading.value = true;
    try {
      const [catalog, info] = await Promise.all([fetchCatalog(), fetchPayInfo()]);
      sku.value = catalog.find((s) => s.code === code) ?? null;
      payInfo.value = info;
      if (!sku.value) {
        toast.error("商品不存在或已下架");
        pay.close();
      }
    } catch (e) {
      toast.error(errMsg(e, "加载商品信息失败"));
      pay.close();
    } finally {
      loading.value = false;
    }
  },
);

function errMsg(e: unknown, fallback: string): string {
  if (e instanceof ApiError) {
    const d = e.detail as { message?: string } | null;
    return d?.message || e.message || fallback;
  }
  return e instanceof Error ? e.message : fallback;
}

async function handleCreateOrder() {
  if (!sku.value) return;
  loading.value = true;
  try {
    order.value = await createOrder(sku.value.code);
    stage.value = "pay";
  } catch (e) {
    toast.error(errMsg(e, "下单失败"));
  } finally {
    loading.value = false;
  }
}

function triggerFilePick() {
  fileInput.value?.click();
}

async function handleProofSelected(ev: Event) {
  const input = ev.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file || !order.value) return;
  submitting.value = true;
  try {
    order.value = await submitProof(order.value.id, file);
    stage.value = "review";
    startPoll();
    toast.success("凭证已提交,正在人工核验");
  } catch (e) {
    toast.error(errMsg(e, "上传凭证失败"));
  } finally {
    submitting.value = false;
    if (input) input.value = "";
  }
}

// 轮询订单状态(等审核结果)
function startPoll() {
  stopPoll();
  pollTimer = window.setInterval(async () => {
    if (!order.value) return;
    try {
      const fresh = await getOrder(order.value.id);
      order.value = fresh;
      if (fresh.status === "paid" && fresh.fulfilled_at) {
        stopPoll();
        pay.notifyFulfilled();
        toast.success("支付已确认,权益已发放");
      } else if (fresh.status === "rejected") {
        stopPoll();
      }
    } catch {
      /* 轮询失败静默,下次再试 */
    }
  }, 4000);
}

function stopPoll() {
  if (pollTimer !== null) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

onBeforeUnmount(stopPoll);

const isPaid = computed(
  () => order.value?.status === "paid" && !!order.value?.fulfilled_at,
);
const isRejected = computed(() => order.value?.status === "rejected");

function handleClose() {
  pay.close();
}
</script>

<template>
  <Teleport to="body">
    <div v-if="pay.isOpen.value" class="pm-overlay" @click.self="handleClose">
      <div class="pm-card screenplay-module">
        <header class="pm-hdr">
          <h2 class="pm-title">{{
            stage === "confirm" ? "确认订单"
            : stage === "pay" ? "扫码支付"
            : "支付核验"
          }}</h2>
          <button class="pm-close" @click="handleClose" title="关闭">
            <Icon name="close" :size="18" />
          </button>
        </header>

        <div class="pm-body">
          <!-- 加载 -->
          <div v-if="loading" class="pm-loading">加载中…</div>

          <!-- 阶段 1:确认 -->
          <template v-else-if="stage === 'confirm' && sku">
            <div class="pm-sku">
              <div class="pm-sku-title">{{ sku.title }}</div>
              <div class="pm-sku-price">¥{{ sku.amount_yuan.toFixed(2) }}</div>
            </div>
            <p class="pm-hint">{{ payInfo?.note }}</p>
            <div class="pm-ftr">
              <button class="pm-btn-cancel" @click="handleClose">取消</button>
              <button class="pm-btn-primary" @click="handleCreateOrder">去支付</button>
            </div>
          </template>

          <!-- 阶段 2:扫码 + 上传凭证 -->
          <template v-else-if="stage === 'pay' && order">
            <div class="pm-pay-amount">
              应付 <strong>¥{{ order.amount_yuan.toFixed(2) }}</strong>
              <span class="pm-order-id mono">订单 {{ order.id }}</span>
            </div>
            <div v-if="qrUrl" class="pm-qr-wrap">
              <img :src="qrUrl" alt="收款二维码" class="pm-qr" />
              <p class="pm-payee">微信扫码付给 <strong>{{ payInfo?.payee_name }}</strong></p>
            </div>
            <div v-else class="pm-qr-missing">
              收款码暂未配置,请联系管理员
            </div>
            <p class="pm-hint">付款后请上传付款成功截图,我们将尽快人工核验并发放权益。</p>
            <input
              ref="fileInput" type="file" accept="image/*"
              style="display:none" @change="handleProofSelected"
            />
            <div class="pm-ftr">
              <button class="pm-btn-cancel" @click="handleClose">稍后再说</button>
              <button class="pm-btn-primary" :disabled="submitting" @click="triggerFilePick">
                {{ submitting ? "上传中…" : "上传付款截图" }}
              </button>
            </div>
          </template>

          <!-- 阶段 3:审核结果 -->
          <template v-else-if="stage === 'review' && order">
            <div v-if="isPaid" class="pm-result pm-result--ok">
              <Icon name="check" :size="40" />
              <div class="pm-result-title">支付成功,权益已发放</div>
              <p class="pm-hint">{{ order.sku_title }} 已生效</p>
              <div class="pm-ftr pm-ftr--center">
                <button class="pm-btn-primary" @click="handleClose">完成</button>
              </div>
            </div>
            <div v-else-if="isRejected" class="pm-result pm-result--rejected">
              <Icon name="alert_triangle" :size="40" />
              <div class="pm-result-title">核验未通过</div>
              <p class="pm-hint">{{ order.rejected_reason || "请确认付款截图无误后重新提交" }}</p>
              <div class="pm-ftr pm-ftr--center">
                <button class="pm-btn-cancel" @click="handleClose">关闭</button>
                <button class="pm-btn-primary" @click="stage = 'pay'">重新上传</button>
              </div>
            </div>
            <div v-else class="pm-result pm-result--pending">
              <Icon name="spinner" :size="40" class="pm-spin" />
              <div class="pm-result-title">正在人工核验</div>
              <p class="pm-hint">通常几分钟内完成,你可以关闭窗口稍后在「我的订单」查看,发放后自动生效。</p>
              <div class="pm-ftr pm-ftr--center">
                <button class="pm-btn-cancel" @click="handleClose">关闭窗口</button>
              </div>
            </div>
          </template>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.pm-overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 16, 12, 0.5);
  backdrop-filter: blur(5px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 400;
}
.pm-card {
  width: 100%;
  max-width: 420px;
  background: var(--card-bg);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
  color: var(--text);
}
.pm-hdr {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-soft);
}
.pm-title {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  font-family: var(--font-serif);
}
.pm-close {
  background: transparent;
  border: none;
  color: var(--text-muted);
  cursor: pointer;
  display: inline-flex;
  padding: 4px;
  border-radius: var(--radius-sm);
}
.pm-close:hover { background: var(--hover-bg); color: var(--text); }
.pm-body { padding: 20px; }
.pm-loading { text-align: center; padding: 40px; color: var(--text-muted); }

/* confirm */
.pm-sku {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 14px 16px;
  background: var(--bg-deep);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  margin-bottom: 12px;
}
.pm-sku-title { font-size: 14px; font-weight: 500; }
.pm-sku-price { font-size: 22px; font-weight: 700; color: var(--accent-text); font-family: var(--font-mono); }

.pm-hint { font-size: 12px; color: var(--text-muted); line-height: 1.6; margin: 10px 0 0; }

/* pay */
.pm-pay-amount {
  text-align: center;
  font-size: 13px;
  color: var(--text-muted);
  margin-bottom: 14px;
}
.pm-pay-amount strong { font-size: 20px; color: var(--accent-text); font-family: var(--font-mono); }
.pm-order-id { display: block; font-size: 10.5px; color: var(--text-subtle); margin-top: 4px; }
.pm-qr-wrap { text-align: center; margin-bottom: 12px; }
.pm-qr {
  width: 200px; height: 200px; object-fit: contain;
  border: 1px solid var(--border-soft); border-radius: var(--radius-md);
  background: white; padding: 8px;
}
.pm-payee { font-size: 12.5px; color: var(--text); margin: 8px 0 0; }
.pm-qr-missing {
  text-align: center; padding: 30px; color: var(--danger);
  background: var(--danger-soft); border-radius: var(--radius-md);
  font-size: 13px; margin-bottom: 12px;
}

/* result */
.pm-result { text-align: center; padding: 16px 0; }
.pm-result--ok { color: var(--success); }
.pm-result--rejected { color: var(--danger); }
.pm-result--pending { color: var(--accent); }
.pm-result-title { font-size: 16px; font-weight: 600; margin: 10px 0 0; color: var(--text); }
.pm-spin { animation: pm-spin 0.8s linear infinite; }
@keyframes pm-spin { to { transform: rotate(360deg); } }

/* footer */
.pm-ftr {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
}
.pm-ftr--center { justify-content: center; }
.pm-btn-cancel, .pm-btn-primary {
  padding: 8px 18px;
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
  border: 1px solid var(--border);
}
.pm-btn-cancel { background: var(--card-bg); color: var(--text); }
.pm-btn-cancel:hover { background: var(--hover-bg); }
.pm-btn-primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.pm-btn-primary:hover:not(:disabled) { background: var(--accent-hover); }
.pm-btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.mono { font-family: var(--font-mono); }
</style>
