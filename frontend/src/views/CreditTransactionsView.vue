<script setup lang="ts">
/**
 * CreditTransactionsView — 我的 credit 消费记录(Sprint C.3,2026-05-13)。
 *
 * 数据源:GET /api/credit/transactions(默认本月,可切"近 50 条")
 *
 * UX:
 *   - 顶部 toolbar:本月 / 近 50 条 切换
 *   - 列表行:wallet chip + kind chip + delta(正绿负红)+ action + related_id + created_at
 *   - 空态:游客 / 无数据分别提示
 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import { api } from "../api/client";
import { ApiError, type CreditTransaction } from "../api/types";
import { useAuthStore } from "../stores/auth";
import { useEventBus } from "../stores/events";

const router = useRouter();
const auth = useAuthStore();

const transactions = ref<CreditTransaction[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const thisMonthOnly = ref(true);

async function loadTransactions() {
  if (!auth.isAuthed) return;
  loading.value = true;
  error.value = null;
  try {
    transactions.value = await api.get<CreditTransaction[]>(
      `/credit/transactions?this_month_only=${thisMonthOnly.value}&limit=200`,
    );
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : "加载交易记录失败";
  } finally {
    loading.value = false;
  }
}

onMounted(loadTransactions);

// 2026-06-02:实时更新 — 监听 credit:consumed + sim done + comic done 事件 → reload
// 治"sim 跑完用户来这页看不到新消费,得手刷新"
const events = useEventBus();
const _unsubCreditConsumed = events.on("credit:consumed", () => void loadTransactions());
const _unsubSimDone = events.on("sim:done", () => void loadTransactions());
const _unsubComicDone = events.on("comic:done", () => void loadTransactions());
function _onCreditVisibilityChange() {
  if (document.visibilityState === "visible") {
    void loadTransactions();
  }
}
document.addEventListener("visibilitychange", _onCreditVisibilityChange);
onBeforeUnmount(() => {
  _unsubCreditConsumed();
  _unsubSimDone();
  _unsubComicDone();
  document.removeEventListener("visibilitychange", _onCreditVisibilityChange);
});

function switchScope(monthOnly: boolean) {
  if (thisMonthOnly.value === monthOnly) return;
  thisMonthOnly.value = monthOnly;
  void loadTransactions();
}

function backToHome() {
  router.push("/dashboard");
}

const empty = computed<boolean>(
  () => !loading.value && transactions.value.length === 0,
);

// 派生:本月总消耗 / 总加购
const totalConsumed = computed<number>(() => {
  let total = 0;
  for (const tx of transactions.value) {
    if (tx.kind === "consume" && tx.delta < 0) total += -tx.delta;
  }
  return total;
});
const totalGranted = computed<number>(() => {
  let total = 0;
  for (const tx of transactions.value) {
    if (
      (tx.kind === "subscribe_grant" || tx.kind === "addon_purchase") &&
      tx.delta > 0
    ) {
      total += tx.delta;
    }
  }
  return total;
});

// ============================================================
// 显示工具
// ============================================================

const KIND_LABEL: Record<string, string> = {
  subscribe_grant: "订阅发放",
  addon_purchase: "加购包",
  consume: "消费",
  refund: "退款",
  month_reset: "月度重置",
  addon_expire: "加购过期",
};

const ACTION_LABEL: Record<string, string> = {
  initial_grant: "首次发放",
  cron_month_reset: "月初重置",
  new_subscription: "新订阅",
  upgrade: "升档",
  refine: "AI 对焦",
  continuation: "AI 推演",
  extract: "AI 抽图谱",
  comic_scripter: "漫画编剧",
  comic_visual_assets: "漫画素材库",
  comic_style_dna: "漫画画风 DNA",
  comic_style_synth: "漫画画风综合",
  comic_style_candidate: "漫画画风候选图",
  comic_anchor_descriptor: "角色描述符",
  comic_anchor_card: "角色立绘卡",
  buy_small: "加购 100c",
  buy_medium: "加购 500c",
  buy_large: "加购 2000c",
};

function actionLabel(action: string | null): string {
  if (!action) return "—";
  return ACTION_LABEL[action] ?? action;
}

function kindLabel(kind: string): string {
  return KIND_LABEL[kind] ?? kind;
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function formatDelta(delta: number): string {
  if (delta > 0) return `+${delta.toLocaleString()}`;
  return delta.toLocaleString();
}
</script>

<template>
  <main class="credit-tx-view">
    <header class="page-header">
      <button class="back-btn" type="button" @click="backToHome">
        <span class="back-arrow">←</span>
        <span>返回</span>
      </button>
      <h1 class="page-title">Credit 消费记录</h1>
    </header>

    <!-- 游客提示 -->
    <div v-if="!auth.isAuthed" class="empty-state">
      <p>请先登录查看消费记录</p>
    </div>

    <template v-else>
      <!-- 顶部汇总 + 切换 -->
      <section class="summary-row">
        <div class="summary-card">
          <span class="summary-label">本期发放</span>
          <span class="summary-value mono">+{{ totalGranted.toLocaleString() }} c</span>
        </div>
        <div class="summary-card summary-card--accent">
          <span class="summary-label">本期消费</span>
          <span class="summary-value mono">-{{ totalConsumed.toLocaleString() }} c</span>
        </div>
        <div class="scope-toggle" role="group" aria-label="时间范围">
          <button
            type="button"
            class="scope-btn"
            :class="{ 'is-active': thisMonthOnly }"
            @click="switchScope(true)"
          >本月</button>
          <button
            type="button"
            class="scope-btn"
            :class="{ 'is-active': !thisMonthOnly }"
            @click="switchScope(false)"
          >近 200 条</button>
        </div>
      </section>

      <!-- 列表 -->
      <section class="tx-list-wrap">
        <div v-if="loading" class="state-msg">加载中…</div>
        <div v-else-if="error" class="state-msg state-error">{{ error }}</div>
        <div v-else-if="empty" class="state-msg">
          {{ thisMonthOnly ? "本月还没有交易记录" : "没有任何交易记录" }}
        </div>
        <ul v-else class="tx-list">
          <li
            v-for="tx in transactions"
            :key="tx.id"
            class="tx-row"
            :class="{
              'is-consume': tx.delta < 0,
              'is-grant': tx.delta > 0,
            }"
          >
            <div class="tx-left">
              <div class="tx-line-1">
                <span class="kind-chip" :class="`kind-${tx.kind}`">{{ kindLabel(tx.kind) }}</span>
                <span class="wallet-chip">{{ tx.wallet === "subscription" ? "订阅" : "加购" }}</span>
                <span class="action-text">{{ actionLabel(tx.action) }}</span>
              </div>
              <div class="tx-line-2 mono">{{ formatTime(tx.created_at) }}</div>
            </div>
            <div class="tx-right">
              <span class="delta mono" :class="{ 'is-positive': tx.delta > 0 }">
                {{ formatDelta(tx.delta) }} c
              </span>
              <span v-if="tx.cost_yuan > 0" class="cost-yuan mono">
                ¥{{ tx.cost_yuan.toFixed(4) }}
              </span>
            </div>
          </li>
        </ul>
      </section>
    </template>
  </main>
</template>

<style scoped>
.credit-tx-view {
  max-width: 880px;
  margin: 0 auto;
  padding: var(--space-6) var(--space-5);
}

.page-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-5);
}

.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
}
.back-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.back-arrow { font-size: var(--text-base); line-height: 1; }

.page-title {
  font-size: var(--text-2xl);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}

/* ===== 汇总区 ===== */
.summary-row {
  display: flex;
  gap: var(--space-3);
  align-items: center;
  flex-wrap: wrap;
  margin-bottom: var(--space-4);
}
.summary-card {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: var(--space-3) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  min-width: 140px;
}
.summary-card--accent {
  background: var(--color-accent-soft);
  border-color: var(--color-accent-border);
}
.summary-label {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.summary-value {
  font-size: var(--text-lg);
  font-weight: 700;
  color: var(--color-text);
}
.summary-card--accent .summary-value {
  color: var(--color-accent-text);
}

.scope-toggle {
  margin-left: auto;
  display: inline-flex;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.scope-btn {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: transparent;
  cursor: pointer;
}
.scope-btn.is-active {
  color: var(--color-text-on-accent);
  background: var(--color-accent);
}
.scope-btn:hover:not(.is-active) {
  background: var(--color-surface-hover);
}

/* ===== 列表 ===== */
.state-msg {
  padding: var(--space-8) var(--space-4);
  text-align: center;
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.state-error {
  color: var(--color-danger);
}

.tx-list {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 1px;
  background: var(--color-border);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  overflow: hidden;
}

.tx-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  background: var(--color-surface);
}
.tx-row:hover {
  background: var(--color-surface-hover);
}

.tx-left {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  flex: 1;
}
.tx-line-1 {
  display: flex;
  gap: var(--space-2);
  align-items: center;
  flex-wrap: wrap;
}
.tx-line-2 {
  font-size: 11px;
  color: var(--color-text-subtle);
}

.kind-chip,
.wallet-chip {
  flex-shrink: 0;
  padding: 1px 6px;
  font-size: 10px;
  font-weight: 500;
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
}
.kind-chip {
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
}
.kind-chip.kind-consume { color: var(--color-danger); background: var(--color-danger-soft); }
.kind-chip.kind-subscribe_grant { color: var(--color-accent-text); background: var(--color-accent-soft); }
.kind-chip.kind-addon_purchase { color: #16A34A; background: rgba(22, 163, 74, 0.1); }
.kind-chip.kind-refund { color: #16A34A; background: rgba(22, 163, 74, 0.1); }

.wallet-chip {
  color: var(--color-text-subtle);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
}

.action-text {
  font-size: var(--text-sm);
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tx-right {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 2px;
}
.delta {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-danger);
}
.delta.is-positive {
  color: #16A34A;
}
.cost-yuan {
  font-size: 11px;
  color: var(--color-text-subtle);
}

.empty-state {
  padding: var(--space-12);
  text-align: center;
  color: var(--color-text-muted);
}

.mono { font-family: var(--font-mono); }
</style>
