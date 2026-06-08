<script setup lang="ts">
/**
 * QuotaIndicator — Credit 余额卡(v2 重设计,2026-06-02)
 *
 * 重设计目标(用户反馈"很缭乱"):
 *   - 一眼能看完(去掉大红色 alert banner / 双 wallet 卡 / 项目数等次要信息)
 *   - 收起态紧凑一行,展开态 4-5 行就完
 *   - 月末提示用 inline 黄字弱化,不再用整块红框压迫
 *   - "30 c" 不再被截断换行
 *
 * 显示位置:AppSidebar 用户菜单展开时塞在菜单顶部
 */
import { computed, ref } from "vue";

import { useQuotaStore } from "../stores/quota";
import { useUpgradeModal } from "../composables/useUpgradeModal";
import { useAddonModal } from "../composables/useAddonModal";

const quota = useQuotaStore();
const upgradeModal = useUpgradeModal();
const addonModal = useAddonModal();

const expanded = ref(false);
function toggle() {
  expanded.value = !expanded.value;
}

const status = computed(() => quota.status);

const planLabel = computed<string>(() => {
  if (!status.value) return "—";
  const map: Record<string, string> = {
    free: "Free",
    pro: "Pro",
    max: "Max",
    super_max: "超级 Max",
    founder: "创始人",
  };
  return map[status.value.plan] ?? status.value.plan;
});

const subscriptionCredits = computed<number>(
  () => status.value?.credit_balance.subscription_credits ?? 0,
);
const addonCredits = computed<number>(
  () => status.value?.credit_balance.addon_credits ?? 0,
);
const totalCredits = computed<number>(
  () => status.value?.credit_balance.total_credits ?? 0,
);
const monthlyQuota = computed<number>(
  () => status.value?.limits.monthly_credits_quota ?? 0,
);

/** 进度 = 订阅已用 / 月度池 */
const usedPct = computed<number>(() => {
  const q = monthlyQuota.value;
  if (q <= 0) return 0;
  const used = q - subscriptionCredits.value;
  return Math.max(0, Math.min(100, Math.round((used / q) * 100)));
});

/** 月末清零的"还剩 N 天" */
const daysUntilReset = computed<number>(() => {
  const start = status.value?.credit_balance.month_start;
  if (!start) return 0;
  try {
    const startDate = new Date(start);
    const now = new Date();
    const reset = new Date(startDate);
    reset.setDate(reset.getDate() + 30);
    const diffMs = reset.getTime() - now.getTime();
    return Math.max(0, Math.ceil(diffMs / (1000 * 60 * 60 * 24)));
  } catch {
    return 0;
  }
});

const isUrgent = computed(
  () => status.value?.plan !== "founder"
    && daysUntilReset.value <= 3
    && subscriptionCredits.value > 0,
);

const showUpgrade = computed(() => status.value && status.value.plan !== "founder");
const showAddon = computed(() => status.value && status.value.plan !== "founder");

function openUpgrade() {
  upgradeModal.open();
}
function openAddon() {
  addonModal.open();
}
</script>

<template>
  <div class="quota-indicator" :class="{ 'is-expanded': expanded }">
    <!-- 顶部:可点击的紧凑行(始终可见)-->
    <button
      type="button"
      class="quota-header"
      :aria-expanded="expanded"
      @click="toggle"
    >
      <span class="chevron" aria-hidden="true">{{ expanded ? "▾" : "▸" }}</span>
      <span class="header-label">本月余额</span>
      <span class="header-value mono">
        <strong>{{ totalCredits.toLocaleString() }}</strong>
        <span class="header-unit">c</span>
      </span>
      <span
        v-if="showUpgrade"
        class="upgrade-link"
        role="button"
        tabindex="0"
        @click.stop="openUpgrade"
        @keydown.enter.stop="openUpgrade"
      >升级 →</span>
    </button>

    <!-- 月末倒数提示(始终可见,弱化为 inline 文字)-->
    <p
      v-if="status?.plan !== 'founder' && monthlyQuota > 0"
      class="reset-hint"
      :class="{ 'is-urgent': isUrgent }"
    >
      <span v-if="isUrgent">⚠</span>
      <span>{{ planLabel }} 档 · {{ daysUntilReset }} 天后刷新</span>
    </p>

    <!-- 展开态:进度条 + 加购按钮 -->
    <div v-if="expanded" class="quota-body">
      <!-- 进度条(单条,显订阅 wallet 使用率)-->
      <div v-if="monthlyQuota > 0" class="progress-section">
        <div class="progress-meta">
          <span>已用 {{ usedPct }}%</span>
          <span class="mono">
            {{ subscriptionCredits.toLocaleString() }} / {{ monthlyQuota.toLocaleString() }}
          </span>
        </div>
        <div class="progress-bar">
          <div
            class="progress-fill"
            :class="{
              'is-warning': usedPct >= 70 && usedPct < 90,
              'is-danger': usedPct >= 90,
            }"
            :style="{ width: usedPct + '%' }"
          ></div>
        </div>
      </div>

      <!-- 加购 credit 显示(永久,1 年有效)-->
      <div v-if="addonCredits > 0" class="addon-line">
        <span class="addon-label">永久 credit</span>
        <span class="addon-value mono">{{ addonCredits.toLocaleString() }} c</span>
      </div>

      <!-- 加购按钮 -->
      <button
        v-if="showAddon"
        type="button"
        class="addon-btn"
        @click="openAddon"
      >+ 加购永久 credit</button>
    </div>
  </div>
</template>

<style scoped>
.quota-indicator {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--color-surface);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
}

/* ===== 顶部 header(可点击展开)===== */
.quota-header {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 100%;
  padding: 0;
  background: transparent;
  border: 0;
  text-align: left;
  cursor: pointer;
  font-size: inherit;
  color: inherit;
}
.quota-header:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

.chevron {
  font-size: 10px;
  color: var(--color-text-subtle);
  width: 10px;
  flex-shrink: 0;
}

.header-label {
  font-weight: 600;
  color: var(--color-text);
  flex-shrink: 0;
}

.header-value {
  margin-left: auto;
  display: inline-flex;
  align-items: baseline;
  gap: 2px;
  /* 关键修复:数字 + 单位不允许换行 */
  white-space: nowrap;
}
.header-value strong {
  font-size: var(--text-md);
  font-weight: 700;
  color: var(--color-text);
}
.header-unit {
  font-size: 11px;
  color: var(--color-text-subtle);
}

.upgrade-link {
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  cursor: pointer;
  font-weight: 500;
  white-space: nowrap;
  padding-left: var(--space-2);
}
.upgrade-link:hover {
  text-decoration: underline;
}
.upgrade-link:focus-visible {
  outline: 2px solid var(--color-accent);
  outline-offset: 2px;
  border-radius: var(--radius-sm);
}

/* ===== 月末倒数提示(inline 弱化)===== */
.reset-hint {
  margin: 0;
  font-size: 11px;
  color: var(--color-text-subtle);
  padding-left: 18px;  /* 跟 chevron 对齐 */
  display: flex;
  align-items: center;
  gap: 4px;
}
.reset-hint.is-urgent {
  color: #b45309;  /* amber-700,温和的警告色 */
  font-weight: 500;
}

/* ===== body(展开态)===== */
.quota-body {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px solid var(--color-border);
}

/* 进度条 */
.progress-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.progress-meta {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  color: var(--color-text-muted);
}
.progress-bar {
  height: 4px;
  background: var(--color-border);
  border-radius: var(--radius-full);
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: var(--color-accent);
  border-radius: var(--radius-full);
  transition: width var(--duration-base) var(--ease-out);
}
.progress-fill.is-warning { background: #F59E0B; }
.progress-fill.is-danger { background: var(--color-danger); }

/* 加购 credit 单独一行(永久) */
.addon-line {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: var(--text-xs);
}
.addon-label {
  color: var(--color-text-muted);
}
.addon-value {
  color: var(--color-text);
  font-weight: 500;
  white-space: nowrap;
}

/* 加购按钮 */
.addon-btn {
  padding: 5px var(--space-2);
  font-size: 11px;
  color: var(--color-accent-text);
  background: transparent;
  border: 1px dashed var(--color-accent-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.addon-btn:hover {
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
}

.mono {
  font-family: var(--font-mono);
}
</style>
