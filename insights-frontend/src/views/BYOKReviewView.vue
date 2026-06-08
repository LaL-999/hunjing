<script setup lang="ts">
/**
 * BYOKReviewView — BYOK 订单审核后台(2026-06-05)
 *
 * 数据源:主平台 backend(port 8000)/api/admin/byok/orders
 * 鉴权:X-Admin-Token(主平台 backend 已加双轨鉴权,接受洞察后台 token)
 *
 * 流程:
 *   - 用户购买 → Vision LLM 自动审 → 失败 → status=manual_review + 邮件通知
 *   - 管理员来这页看 → 看截图 → 通过(自动激活 BYOK 订阅)/ 拒绝(写明原因)
 *
 * 注:洞察后台 read-only attach 主库铁律不破 — 写操作通过 HTTP 调主平台 backend 完成
 */
import { computed, onMounted, ref } from "vue";

import { fetchPlatformAdmin, fetchPlatformBlob, postPlatformAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";

type BYOKOrderStatus =
  | "pending"
  | "submitted"
  | "paid"
  | "rejected"
  | "manual_review"
  | "expired";

interface AdminPaymentOrderResponse {
  order_id: string;
  user_id: string;
  user_email: string | null;
  status: BYOKOrderStatus;
  amount_cents: number;
  months: number;
  created_at: string;
  expires_at: string;
  proof_submitted_at: string | null;
  proof_image_url: string | null;

  detected_amount_cents: number | null;
  detected_payee_name: string | null;
  detected_pay_time: string | null;
  detected_transaction_id: string | null;
  detection_pass_reason: string | null;
  detection_run_at: string | null;

  rejected_reason: string | null;
  activated_subscription_id: string | null;
  activated_code: string | null;
  activated_expires_at: string | null;
}

interface AdminPaymentOrdersListResponse {
  orders: AdminPaymentOrderResponse[];
  total: number;
  pending_review_count: number;
}

type StatusFilter =
  | "manual_review"
  | "submitted"
  | "pending"
  | "paid"
  | "rejected"
  | "expired"
  | "all";

const STATUS_TABS: Array<{ value: StatusFilter; label: string; tone: "warn" | "info" | "ok" | "danger" | "muted" }> = [
  { value: "manual_review", label: "待人工审核", tone: "warn" },
  { value: "submitted", label: "自动审核中", tone: "info" },
  { value: "pending", label: "等用户上传", tone: "muted" },
  { value: "paid", label: "已通过", tone: "ok" },
  { value: "rejected", label: "已拒绝", tone: "danger" },
  { value: "expired", label: "已过期", tone: "muted" },
  { value: "all", label: "全部", tone: "muted" },
];

// ============================================================
// State
// ============================================================

const activeStatus = ref<StatusFilter>("manual_review");
const orders = ref<AdminPaymentOrderResponse[]>([]);
const total = ref(0);
const pendingReviewCount = ref(0);
const loading = ref(false);
const errorMsg = ref<string | null>(null);

// 操作锁
const processingIds = ref<Set<string>>(new Set());

// 截图预览
const previewImageUrl = ref<string | null>(null);

const headerBadge = computed(() => {
  if (pendingReviewCount.value === 0) return null;
  return `${pendingReviewCount.value} 单待审`;
});


// ============================================================
// 加载
// ============================================================

async function load() {
  loading.value = true;
  errorMsg.value = null;
  try {
    const resp = await fetchPlatformAdmin<AdminPaymentOrdersListResponse>(
      `/api/admin/byok/orders`,
      { status_filter: activeStatus.value, limit: 200 },
    );
    orders.value = resp.orders;
    total.value = resp.total;
    pendingReviewCount.value = resp.pending_review_count;
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
}

function setStatusFilter(s: StatusFilter) {
  activeStatus.value = s;
  load();
}

onMounted(load);


// ============================================================
// 审批 / 拒绝
// ============================================================

async function approveOrder(order: AdminPaymentOrderResponse) {
  if (processingIds.value.has(order.order_id)) return;

  const note = window.prompt(
    `审批通过订单 ${order.order_id} ?\n\n用户:${order.user_email ?? order.user_id}\n金额:${order.amount_cents / 100} 元\n月数:${order.months}\n\n可选备注(留空也行):`,
    "",
  );
  if (note === null) return;

  processingIds.value.add(order.order_id);
  try {
    const updated = await postPlatformAdmin<AdminPaymentOrderResponse>(
      `/api/admin/byok/orders/${order.order_id}/approve`,
      { note: note.trim() || null },
    );
    window.alert(
      `已通过 — 激活码:${updated.activated_code ?? ''}\n\n用户下次输入激活码即生效。`,
    );
    const idx = orders.value.findIndex((o) => o.order_id === order.order_id);
    if (idx >= 0) orders.value[idx] = updated;
    if (order.status === "manual_review" && pendingReviewCount.value > 0) {
      pendingReviewCount.value -= 1;
    }
  } catch (e) {
    window.alert(`审批失败:${e instanceof Error ? e.message : String(e)}`);
  } finally {
    processingIds.value.delete(order.order_id);
  }
}

async function rejectOrder(order: AdminPaymentOrderResponse) {
  if (processingIds.value.has(order.order_id)) return;

  const reason = window.prompt(
    `拒绝订单 ${order.order_id} ?\n\n用户:${order.user_email ?? order.user_id}\n\n请填拒绝原因(用户会看到):`,
    "",
  );
  if (reason === null) return;
  if (!reason.trim()) {
    window.alert("拒绝原因不能为空");
    return;
  }

  // 二次确认
  if (!window.confirm(`确认拒绝该订单?\n\n用户将看到:${reason.trim()}\n\n拒绝后状态变为 rejected,无法再审批。`)) {
    return;
  }

  processingIds.value.add(order.order_id);
  try {
    const updated = await postPlatformAdmin<AdminPaymentOrderResponse>(
      `/api/admin/byok/orders/${order.order_id}/reject`,
      { reason: reason.trim() },
    );
    const idx = orders.value.findIndex((o) => o.order_id === order.order_id);
    if (idx >= 0) orders.value[idx] = updated;
    if (order.status === "manual_review" && pendingReviewCount.value > 0) {
      pendingReviewCount.value -= 1;
    }
  } catch (e) {
    window.alert(`拒绝失败:${e instanceof Error ? e.message : String(e)}`);
  } finally {
    processingIds.value.delete(order.order_id);
  }
}


// ============================================================
// 工具
// ============================================================

function formatYuan(cents: number | null | undefined): string {
  if (cents == null) return "—";
  return `¥ ${(cents / 100).toFixed(2)}`;
}

function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

function statusLabel(s: BYOKOrderStatus): string {
  const map: Record<BYOKOrderStatus, string> = {
    pending: "等用户上传",
    submitted: "自动审核中",
    paid: "已通过",
    rejected: "已拒绝",
    manual_review: "待人工审核",
    expired: "已过期",
  };
  return map[s] ?? s;
}

function canApprove(s: BYOKOrderStatus): boolean {
  return s === "manual_review" || s === "submitted" || s === "pending";
}
function canReject(s: BYOKOrderStatus): boolean {
  return s !== "paid" && s !== "rejected" && s !== "expired";
}

async function openProofPreview(order: AdminPaymentOrderResponse) {
  if (!order.proof_image_url) {
    window.alert("该订单未上传截图");
    return;
  }
  try {
    const blob = await fetchPlatformBlob(order.proof_image_url);
    previewImageUrl.value = URL.createObjectURL(blob);
  } catch (e) {
    window.alert(`加载截图失败:${e instanceof Error ? e.message : String(e)}`);
  }
}

function closeProofPreview() {
  if (previewImageUrl.value) {
    URL.revokeObjectURL(previewImageUrl.value);
  }
  previewImageUrl.value = null;
}
</script>

<template>
  <div class="byok-review-view">
    <PageHero
      icon="key"
      title="BYOK 订单审核"
      :description="`自动审核未通过的订单进入「待人工审核」。看截图后通过(自动激活订阅)或拒绝(填明原因)。当前 ${pendingReviewCount} 单待你处理。`"
      audience="管理员视角"
    />

    <!-- 状态 tab -->
    <div class="status-tabs">
      <button
        v-for="tab in STATUS_TABS"
        :key="tab.value"
        class="status-tab"
        :class="[`status-tab--${tab.tone}`, { 'is-active': activeStatus === tab.value }]"
        @click="setStatusFilter(tab.value)"
      >
        {{ tab.label }}
        <span
          v-if="tab.value === 'manual_review' && pendingReviewCount > 0"
          class="tab-badge"
        >{{ pendingReviewCount }}</span>
      </button>
    </div>

    <!-- 错误 -->
    <div v-if="errorMsg" class="error-banner">{{ errorMsg }}</div>

    <!-- loading -->
    <div v-else-if="loading" class="loading-state">加载中…</div>

    <!-- 空 -->
    <div v-else-if="orders.length === 0" class="empty-state">
      <p>这个状态下没有订单</p>
    </div>

    <!-- 列表 -->
    <div v-else class="order-list">
      <article
        v-for="order in orders"
        :key="order.order_id"
        class="order-card"
        :class="`order-card--${order.status}`"
      >
        <!-- 左:基础 -->
        <div class="card-left">
          <div class="card-id">
            <span class="id-prefix">订单</span>
            <code class="id-value">{{ order.order_id }}</code>
            <span :class="`status-chip status-chip--${order.status}`">
              {{ statusLabel(order.status) }}
            </span>
          </div>
          <div class="card-user">
            <Icon name="user" :size="13" class="card-icon" />
            {{ order.user_email ?? order.user_id }}
          </div>
          <div class="card-meta">
            <span class="meta-item">{{ formatYuan(order.amount_cents) }} × {{ order.months }} 月</span>
            <span class="meta-sep">·</span>
            <span class="meta-item">{{ formatTime(order.created_at) }} 下单</span>
            <template v-if="order.proof_submitted_at">
              <span class="meta-sep">·</span>
              <span class="meta-item">{{ formatTime(order.proof_submitted_at) }} 上传截图</span>
            </template>
          </div>
        </div>

        <!-- 中:Vision LLM 识别 -->
        <div v-if="order.detection_run_at" class="card-detection">
          <div class="detection-title">
            <Icon name="eye" :size="13" />
            自动识别
          </div>
          <div class="detection-rows">
            <div class="det-row">
              <span class="det-label">金额</span>
              <span class="det-value">{{ formatYuan(order.detected_amount_cents) }}</span>
            </div>
            <div class="det-row">
              <span class="det-label">收款方</span>
              <span class="det-value">{{ order.detected_payee_name ?? "—" }}</span>
            </div>
            <div class="det-row">
              <span class="det-label">付款时间</span>
              <span class="det-value">{{ formatTime(order.detected_pay_time) }}</span>
            </div>
            <div class="det-row">
              <span class="det-label">交易号</span>
              <span class="det-value mono">{{ order.detected_transaction_id ?? "—" }}</span>
            </div>
          </div>
          <div
            v-if="order.detection_pass_reason"
            class="detection-note"
            :class="{ 'detection-note--fail': order.status === 'manual_review' || order.status === 'rejected' }"
          >
            {{ order.detection_pass_reason }}
          </div>
        </div>

        <!-- 右:操作 -->
        <div class="card-actions">
          <button
            v-if="order.proof_image_url"
            class="action-btn action-btn--neutral"
            @click="openProofPreview(order)"
          >
            <Icon name="eye" :size="13" />
            看截图
          </button>
          <button
            v-if="canApprove(order.status)"
            class="action-btn action-btn--approve"
            :disabled="processingIds.has(order.order_id)"
            @click="approveOrder(order)"
          >
            <Icon name="check" :size="13" />
            通过
          </button>
          <button
            v-if="canReject(order.status)"
            class="action-btn action-btn--reject"
            :disabled="processingIds.has(order.order_id)"
            @click="rejectOrder(order)"
          >
            <Icon name="x" :size="13" />
            拒绝
          </button>
          <div v-if="order.activated_code" class="action-code">
            <span class="code-label">激活码</span>
            <code class="code-value">{{ order.activated_code }}</code>
          </div>
          <div v-if="order.rejected_reason && order.status === 'rejected'" class="action-reason">
            <span class="reason-label">拒绝原因</span>
            <span>{{ order.rejected_reason }}</span>
          </div>
        </div>
      </article>
    </div>

    <!-- 截图预览 modal -->
    <div v-if="previewImageUrl" class="proof-modal" @click.self="closeProofPreview">
      <div class="proof-modal-content">
        <button class="proof-modal-close" @click="closeProofPreview">
          <Icon name="x" :size="18" />
        </button>
        <img :src="previewImageUrl" alt="付款截图" class="proof-modal-img" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.byok-review-view {
  max-width: 80rem;
  margin: 0 auto;
}

/* tab */
.status-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 0.375rem;
  margin: 1.25rem 0 1.125rem;
  padding: 0.25rem;
  background: #f3f0e8;
  border-radius: 0.625rem;
  width: fit-content;
}
.status-tab {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.375rem 0.875rem;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 0.4375rem;
  font-size: 0.8125rem;
  cursor: pointer;
  color: #6a665e;
  transition: all 120ms ease-out;
}
.status-tab:hover { color: #1f1f1e; }
.status-tab.is-active {
  background: #faf7f2;
  color: #1f1f1e;
  border-color: #e5e1d8;
  font-weight: 500;
}
.status-tab--warn.is-active { color: #92400e; border-color: #fcd34d; background: #fffbeb; }
.status-tab--ok.is-active { color: #065f46; border-color: #6ee7b7; background: #f0fdf4; }
.status-tab--danger.is-active { color: #991b1b; border-color: #fca5a5; background: #fef2f2; }
.status-tab--info.is-active { color: #1e40af; border-color: #93c5fd; background: #eff6ff; }

.tab-badge {
  padding: 0.0625rem 0.375rem;
  background: #f59e0b;
  color: white;
  font-size: 0.6875rem;
  border-radius: 999px;
  font-weight: 600;
}

/* 错误 / loading / 空 */
.error-banner {
  padding: 0.875rem 1.125rem;
  background: #fef2f2;
  color: #991b1b;
  border: 1px solid #fca5a5;
  border-radius: 0.5rem;
  font-size: 0.8125rem;
  margin-bottom: 1rem;
}
.loading-state,
.empty-state {
  padding: 3rem 0;
  text-align: center;
  color: #6a665e;
  font-size: 0.875rem;
}

/* 订单卡片 */
.order-list {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}
.order-card {
  display: grid;
  grid-template-columns: 1.5fr 1.2fr auto;
  gap: 1.5rem;
  padding: 1.125rem 1.25rem;
  background: #faf7f2;
  border: 1px solid #e5e1d8;
  border-radius: 0.75rem;
  transition: box-shadow 150ms;
}
.order-card--manual_review {
  border-left: 0.25rem solid #f59e0b;
  background: linear-gradient(to right, #fffbeb 0%, #faf7f2 30%);
}
.order-card--paid {
  border-left: 0.25rem solid #10b981;
}
.order-card--rejected {
  border-left: 0.25rem solid #ef4444;
  opacity: 0.85;
}
.order-card:hover {
  box-shadow: 0 0.125rem 0.5rem rgba(0, 0, 0, 0.04);
}

.card-left { min-width: 0; }
.card-id {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.375rem;
  flex-wrap: wrap;
}
.id-prefix {
  font-size: 0.6875rem;
  color: #9a968d;
}
.id-value {
  font-family: ui-monospace, monospace;
  font-size: 0.75rem;
  color: #1f1f1e;
  background: #f3f0e8;
  padding: 0.125rem 0.375rem;
  border-radius: 0.25rem;
}
.status-chip {
  font-size: 0.6875rem;
  padding: 0.125rem 0.5rem;
  border-radius: 999px;
  font-weight: 500;
}
.status-chip--manual_review { background: #fef3c7; color: #92400e; }
.status-chip--paid { background: #d1fae5; color: #065f46; }
.status-chip--rejected { background: #fee2e2; color: #991b1b; }
.status-chip--submitted { background: #dbeafe; color: #1e40af; }
.status-chip--pending { background: #f3f4f6; color: #4b5563; }
.status-chip--expired { background: #e5e7eb; color: #6b7280; }

.card-user {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.8125rem;
  color: #1f1f1e;
  margin-bottom: 0.375rem;
}
.card-icon { color: #9a968d; flex-shrink: 0; }
.card-meta {
  font-size: 0.75rem;
  color: #6a665e;
  display: flex;
  flex-wrap: wrap;
  gap: 0 0.375rem;
}
.meta-sep { color: #c4bfb3; }

/* detection */
.card-detection {
  padding: 0.75rem 0.875rem;
  background: #f7f5f0;
  border-radius: 0.5rem;
  font-size: 0.75rem;
}
.detection-title {
  display: flex;
  align-items: center;
  gap: 0.3125rem;
  font-weight: 500;
  color: #6a665e;
  margin-bottom: 0.5rem;
}
.detection-rows { display: flex; flex-direction: column; gap: 0.25rem; }
.det-row { display: flex; gap: 0.5rem; }
.det-label { color: #9a968d; width: 3.5rem; flex-shrink: 0; }
.det-value { color: #1f1f1e; }
.mono { font-family: ui-monospace, monospace; font-size: 0.6875rem; word-break: break-all; }
.detection-note {
  margin-top: 0.5rem;
  padding-top: 0.5rem;
  border-top: 1px dashed #e5e1d8;
  color: #6a665e;
  line-height: 1.5;
  font-size: 0.7187rem;
}
.detection-note--fail { color: #b45309; }

/* actions */
.card-actions {
  display: flex;
  flex-direction: column;
  gap: 0.375rem;
  min-width: 8.125rem;
  align-items: stretch;
}
.action-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.3125rem;
  padding: 0.5rem 0.75rem;
  border-radius: 0.4375rem;
  font-size: 0.8125rem;
  font-weight: 500;
  cursor: pointer;
  border: 1px solid;
  transition: all 150ms;
}
.action-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.action-btn--neutral {
  background: #faf7f2;
  color: #1f1f1e;
  border-color: #e5e1d8;
}
.action-btn--neutral:hover:not(:disabled) { background: #f3f0e8; }
.action-btn--approve {
  background: #10b981;
  color: white;
  border-color: #10b981;
}
.action-btn--approve:hover:not(:disabled) { background: #059669; }
.action-btn--reject {
  background: #faf7f2;
  color: #b91c1c;
  border-color: #fca5a5;
}
.action-btn--reject:hover:not(:disabled) { background: #fef2f2; }

.action-code,
.action-reason {
  margin-top: 0.375rem;
  padding: 0.375rem 0.5rem;
  background: #f3f0e8;
  border-radius: 0.375rem;
  font-size: 0.6875rem;
  display: flex;
  flex-direction: column;
  gap: 0.125rem;
}
.code-label,
.reason-label {
  color: #9a968d;
  font-size: 0.625rem;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.code-value {
  font-family: ui-monospace, monospace;
  font-size: 0.6875rem;
  color: #1f1f1e;
  word-break: break-all;
}
.action-reason span:last-child { color: #b91c1c; }

/* modal */
.proof-modal {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.75);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 9999;
  backdrop-filter: blur(4px);
  padding: 2.5rem;
}
.proof-modal-content {
  position: relative;
  max-width: 90vw;
  max-height: 90vh;
}
.proof-modal-img {
  max-width: 100%;
  max-height: 90vh;
  border-radius: 0.5rem;
  box-shadow: 0 0.5rem 2rem rgba(0, 0, 0, 0.3);
}
.proof-modal-close {
  position: absolute;
  top: -2.75rem;
  right: 0;
  width: 2.25rem;
  height: 2.25rem;
  background: rgba(255, 255, 255, 0.15);
  color: white;
  border: 1px solid rgba(255, 255, 255, 0.3);
  border-radius: 50%;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
}
.proof-modal-close:hover { background: rgba(255, 255, 255, 0.25); }

/* 响应式 */
@media (max-width: 64rem) {
  .order-card {
    grid-template-columns: 1fr;
  }
  .card-actions {
    flex-direction: row;
    flex-wrap: wrap;
  }
}
</style>
