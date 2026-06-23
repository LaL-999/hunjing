<script setup lang="ts">
/**
 * OrderReviewView — 统一订单审核(商业化重塑 P1h,2026-06-09)。
 *
 * 取代分散审核:订阅 / BYOK / 配额 全部走统一 payment_orders,这里一处审完。
 * 数据源:主平台 backend /api/payments/admin/orders(X-Admin-Token 鉴权)。
 *
 * 流程:用户扫码付款 → 上传截图 → status='submitted' → 这里看截图 →
 *   通过(→ paid → 自动履约发货)/ 驳回(填原因)。
 */
import { computed, onMounted, ref } from "vue";

import { fetchPlatformAdmin, fetchPlatformBlob, postPlatformAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";

type OrderStatus =
  | "pending" | "submitted" | "paid" | "rejected" | "manual_review" | "expired" | "refunded";

interface AdminOrder {
  id: string;
  user_id: string;
  sku_code: string;
  sku_title: string;
  category: "subscription" | "byok" | "credit";
  amount_yuan: number;
  status: OrderStatus;
  proof_image_path: string | null;
  proof_submitted_at: string | null;
  rejected_reason: string | null;
  fulfilled_at: string | null;
  created_at: string;
}

const CATEGORY_LABEL: Record<string, string> = {
  subscription: "订阅",
  byok: "自携密钥",
  credit: "配额包",
};
const STATUS_LABEL: Record<string, string> = {
  pending: "待付款",
  submitted: "待审核",
  paid: "已通过",
  rejected: "已驳回",
  manual_review: "需人工",
  expired: "已过期",
  refunded: "已退款",
};

const orders = ref<AdminOrder[]>([]);
const error = ref<string | null>(null);
const loading = ref(false);
const statusFilter = ref<string>("");      // 空 = 待审(submitted+manual_review)
const busyId = ref<string | null>(null);

// 截图预览
const previewUrl = ref<string | null>(null);
// 驳回原因(行内)
const rejectingId = ref<string | null>(null);
const rejectReason = ref<string>("");

const pendingCount = computed(
  () => orders.value.filter((o) => o.status === "submitted" || o.status === "manual_review").length,
);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const q = statusFilter.value ? { status_filter: statusFilter.value } : undefined;
    const resp = await fetchPlatformAdmin<{ items: AdminOrder[] }>(
      "/api/payments/admin/orders", q,
    );
    orders.value = resp.items ?? [];
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

onMounted(load);

function setFilter(v: string) {
  statusFilter.value = v;
  void load();
}

async function approve(o: AdminOrder) {
  busyId.value = o.id;
  try {
    await postPlatformAdmin(`/api/payments/admin/orders/${o.id}/approve`, {});
    await load();
  } catch (e) {
    error.value = `通过失败:${e instanceof Error ? e.message : String(e)}`;
  } finally {
    busyId.value = null;
  }
}

function startReject(o: AdminOrder) {
  rejectingId.value = o.id;
  rejectReason.value = "";
}
function cancelReject() {
  rejectingId.value = null;
  rejectReason.value = "";
}
async function confirmReject(o: AdminOrder) {
  const reason = rejectReason.value.trim();
  if (!reason) return;
  busyId.value = o.id;
  try {
    await postPlatformAdmin(`/api/payments/admin/orders/${o.id}/reject`, { reason });
    cancelReject();
    await load();
  } catch (e) {
    error.value = `驳回失败:${e instanceof Error ? e.message : String(e)}`;
  } finally {
    busyId.value = null;
  }
}

async function openProof(o: AdminOrder) {
  if (!o.proof_image_path) return;
  try {
    const blob = await fetchPlatformBlob(`/api/payments/admin/orders/${o.id}/proof-image`);
    previewUrl.value = URL.createObjectURL(blob);
  } catch (e) {
    error.value = `加载截图失败:${e instanceof Error ? e.message : String(e)}`;
  }
}
function closeProof() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
  previewUrl.value = null;
}

function fmtTime(iso: string | null): string {
  if (!iso) return "—";
  return iso.slice(0, 19).replace("T", " ");
}
function canReview(s: OrderStatus): boolean {
  return s === "submitted" || s === "manual_review";
}
</script>

<template>
  <section>
    <PageHero
      icon="key"
      title="订单审核"
      :description="`订阅 / 自携密钥 / 配额包 全部走统一订单。用户扫码付款上传截图后进入待审,看截图后通过(自动发放权益)或驳回(填原因)。当前 ${pendingCount} 单待处理。`"
      audience="管理员视角"
    />

    <div class="filter-row">
      <button :class="{ active: statusFilter === '' }" @click="setFilter('')">待审</button>
      <button :class="{ active: statusFilter === 'paid' }" @click="setFilter('paid')">已通过</button>
      <button :class="{ active: statusFilter === 'rejected' }" @click="setFilter('rejected')">已驳回</button>
      <button :class="{ active: statusFilter === 'pending' }" @click="setFilter('pending')">待付款</button>
      <button class="refresh" @click="load">刷新</button>
    </div>

    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="loading" class="hint">加载中…</div>
    <div v-else-if="orders.length === 0" class="hint">暂无订单</div>

    <ul v-else class="order-list">
      <li v-for="o in orders" :key="o.id" class="order-row">
        <div class="order-main">
          <div class="order-head">
            <span class="cat-chip" :class="`cat-${o.category}`">{{ CATEGORY_LABEL[o.category] }}</span>
            <span class="order-title">{{ o.sku_title }}</span>
            <span class="order-amount mono">¥{{ o.amount_yuan.toFixed(2) }}</span>
            <span class="status-chip" :class="`st-${o.status}`">{{ STATUS_LABEL[o.status] }}</span>
          </div>
          <div class="order-meta mono">
            <span>{{ o.id }}</span>
            <span class="sep">·</span>
            <span>用户 {{ o.user_id.slice(0, 12) }}</span>
            <span class="sep">·</span>
            <span>下单 {{ fmtTime(o.created_at) }}</span>
            <span v-if="o.proof_submitted_at" class="sep">·</span>
            <span v-if="o.proof_submitted_at">传图 {{ fmtTime(o.proof_submitted_at) }}</span>
          </div>
          <div v-if="o.rejected_reason" class="reject-note">驳回原因:{{ o.rejected_reason }}</div>

          <!-- 行内驳回输入 -->
          <div v-if="rejectingId === o.id" class="reject-box">
            <input
              v-model="rejectReason"
              class="reject-input"
              placeholder="填写驳回原因(用户可见)"
              @keyup.enter="confirmReject(o)"
            />
            <button class="btn-danger" :disabled="!rejectReason.trim() || busyId === o.id" @click="confirmReject(o)">确认驳回</button>
            <button class="btn-ghost" @click="cancelReject">取消</button>
          </div>
        </div>

        <div class="order-actions">
          <button v-if="o.proof_image_path" class="btn-ghost" @click="openProof(o)">
            <Icon name="image" :size="14" /> 看截图
          </button>
          <template v-if="canReview(o.status) && rejectingId !== o.id">
            <button class="btn-primary" :disabled="busyId === o.id" @click="approve(o)">
              {{ busyId === o.id ? "处理中…" : "通过" }}
            </button>
            <button class="btn-danger-ghost" @click="startReject(o)">驳回</button>
          </template>
        </div>
      </li>
    </ul>

    <!-- 截图预览遮罩 -->
    <div v-if="previewUrl" class="proof-overlay" @click.self="closeProof">
      <img :src="previewUrl" alt="付款截图" class="proof-img" />
      <button class="proof-close" @click="closeProof">关闭</button>
    </div>
  </section>
</template>

<style scoped>
.filter-row { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
.filter-row button {
  padding: 5px 14px; border: 1px solid #e4e4e7; background: #f4f4f5;
  border-radius: 6px; font-size: 12.5px; cursor: pointer;
}
.filter-row button.active { background: #7c3aed; color: #fff; border-color: #7c3aed; }
.filter-row .refresh { margin-left: auto; }
.err { color: #c0392b; background: #fdecec; padding: 10px 14px; border-radius: 6px; margin-bottom: 12px; }
.hint { color: #6b7280; padding: 40px; text-align: center; }

.order-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 10px; }
.order-row {
  display: flex; align-items: center; gap: 14px;
  padding: 14px 16px; background: #fff; border: 1px solid #e4e4e7; border-radius: 8px;
}
.order-main { flex: 1; min-width: 0; }
.order-head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.cat-chip { font-size: 11px; padding: 2px 8px; border-radius: 4px; font-weight: 600; }
.cat-subscription { background: #ede9fe; color: #6d28d9; }
.cat-byok { background: #dcfce7; color: #15803d; }
.cat-credit { background: #fef3c7; color: #b45309; }
.order-title { font-size: 14px; font-weight: 500; color: #1f2937; }
.order-amount { font-size: 14px; font-weight: 700; color: #5b21b6; }
.status-chip { font-size: 11px; padding: 2px 8px; border-radius: 10px; background: #f4f4f5; color: #6b7280; }
.st-submitted, .st-manual_review { background: #fef9c3; color: #a16207; }
.st-paid { background: #dcfce7; color: #15803d; }
.st-rejected { background: #fee2e2; color: #b91c1c; }
.order-meta { font-size: 11px; color: #9ca3af; margin-top: 5px; display: flex; gap: 6px; flex-wrap: wrap; }
.sep { color: #d1d5db; }
.reject-note { font-size: 12px; color: #b91c1c; margin-top: 6px; }
.reject-box { display: flex; gap: 8px; margin-top: 8px; align-items: center; }
.reject-input { flex: 1; padding: 6px 10px; border: 1px solid #e4e4e7; border-radius: 6px; font-size: 12.5px; }

.order-actions { display: flex; gap: 6px; flex-shrink: 0; align-items: center; }
.btn-primary { padding: 6px 16px; background: #7c3aed; color: #fff; border: none; border-radius: 6px; font-size: 12.5px; cursor: pointer; }
.btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-danger { padding: 6px 14px; background: #dc2626; color: #fff; border: none; border-radius: 6px; font-size: 12.5px; cursor: pointer; }
.btn-danger:disabled { opacity: 0.5; }
.btn-danger-ghost { padding: 6px 12px; background: #fff; color: #dc2626; border: 1px solid #fca5a5; border-radius: 6px; font-size: 12.5px; cursor: pointer; }
.btn-ghost { display: inline-flex; align-items: center; gap: 4px; padding: 6px 12px; background: #fff; color: #4b5563; border: 1px solid #e4e4e7; border-radius: 6px; font-size: 12.5px; cursor: pointer; }

.proof-overlay {
  position: fixed; inset: 0; background: rgba(0,0,0,0.7);
  display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 14px; z-index: 999;
}
.proof-img { max-width: 80vw; max-height: 80vh; border-radius: 8px; background: #fff; }
.proof-close { padding: 8px 20px; background: #fff; border: none; border-radius: 6px; cursor: pointer; }
.mono { font-family: ui-monospace, monospace; }
</style>
