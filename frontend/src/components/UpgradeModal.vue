<script setup lang="ts">
/**
 * UpgradeModal — 升级订阅模态(Sprint D.1 重构,2026-05-12)。
 *
 * 4 档 Anthropic 风:Free / Pro / Max / 超级 Max,详见 项目记忆.md 配额表
 * 月/年付切换:年付 ≈ "省 1.2 个月"(行业 16-20% 折扣给不起,因 LLM 成本占比 70%)
 *
 * 阶段 1 暂无支付,所有档位"敬请期待"。点击只显示提示。
 * 触发场景:配额超限被 catch 时由调用方传入 reason 描述触发原因。
 *
 * 后续接入支付时:每张卡片"立即订阅"按钮换 stripe / 微信支付 / 支付宝。
 */
import { computed, ref } from "vue";
import { useRouter } from "vue-router";

import { useAuthStore } from "../stores/auth";
import { useUpgradeModal } from "../composables/useUpgradeModal";
import { usePayment } from "../composables/usePayment";
import { toast } from "../composables/useToast";
import type { Plan } from "../api/types";

const auth = useAuthStore();
const upgradeModal = useUpgradeModal();
const pay = usePayment();
const router = useRouter();

// v5 主打:开通自携密钥 —— 关升级弹窗,去 BYOK 配置页(内含开通 ¥5 月卡 + 配 key)
function handleByok() {
  upgradeModal.close();
  void router.push("/byok-config");
}

// 月付 / 年付切换 — 默认显月付(用户更易接受门槛低)
const billingCycle = ref<"monthly" | "yearly">("monthly");

interface PlanCard {
  key: Plan;
  name: string;
  /** 月付价 — 显示在"按月"模式 */
  monthlyPrice: string;
  /** 年付价 — 显示在"按年"模式 */
  yearlyPrice: string;
  /** 年付月均(给用户更直观的对比锚)*/
  yearlyMonthEq: string;
  /** 高亮标(推荐档,通常 Pro 或 Max)*/
  highlight?: boolean;
  /** 关键卖点 bullets */
  bullets: string[];
}

// v5 计费转向(2026-06-26,BYOK 主打):订阅激进降价约半,超级 Max 下架,Max 转正可购
//   - Pro ¥138→¥68 / Max ¥438→¥218;credit 数不变(600/2000)= 单 credit ¥0.11
//   - 年付 = 月付 × 12 × 0.85;漫创态对 Pro/Max 解锁(去漫画包门槛)
//   - 主推「自携密钥 ¥5/月」(见顶部 byok-hero),订阅为辅
const PLANS: PlanCard[] = [
  {
    key: "free",
    name: "免费",
    monthlyPrice: "¥0",
    yearlyPrice: "¥0",
    yearlyMonthEq: "—",
    bullets: [
      "**20 credit / 月**(尝鲜,约 3 次中等推演)",
      "项目 2 个 · 角色 10/项",
      "重塑度上限 30%",
      "体验 AI 对焦 / 短文本推演",
    ],
  },
  {
    key: "pro",
    name: "Pro",
    monthlyPrice: "¥68/月",
    yearlyPrice: "¥693.60/年",
    yearlyMonthEq: "¥57.80/月",
    highlight: true,
    bullets: [
      "**600 credit / 月**(单 credit ¥0.11)",
      "项目 5 个 · 角色 30/项",
      "重塑度上限 80%",
      "**漫创态解锁** · 约 90 次中等推演/月",
      "首次订阅 5 折 · 月末清零,加购 1 年有效",
    ],
  },
  {
    key: "max",
    name: "Max",
    monthlyPrice: "¥218/月",
    yearlyPrice: "¥2223.60/年",
    yearlyMonthEq: "¥185.30/月",
    bullets: [
      "**2000 credit / 月**(单 credit ¥0.11)",
      "项目 20 个 · 角色 50/项",
      "重塑度上限 90%(满档)",
      "**漫创态解锁** · 约 300 次中等推演",
      "大量素材库 / 守护者 / 工作室级",
    ],
  },
];

const KIND_LABEL: Record<string, string> = {
  // 资源容量类(QuotaExceeded)
  characters_per_project: "项目内角色数",
  projects_total: "项目总数",
  reshape_percent: "重塑度上限",
  // v5(2026-06-26):漫创态改为订阅(Pro/Max)或 BYOK 解锁,去漫画包门槛
  comics_per_month: "漫创态(升级 Pro/Max 或开自携密钥解锁)",
  // AI credit 不足类(InsufficientCredits)— action 字段映射
  refine: "AI 对焦",
  continuation: "AI 推演",
  extract: "AI 抽图谱",
  comic_create: "漫画创建",
  comic_batch: "漫画分批",
  planner: "AI 规划员",
  vote_style: "画风投票",
};

const reasonText = computed<string | null>(() => {
  const r = upgradeModal.reason.value;
  if (!r) return null;
  if (r.code === "INSUFFICIENT_CREDITS") {
    const label = KIND_LABEL[r.action] ?? r.action;
    return `「${label}」需要 ${r.needed} credit,当前余额 ${r.available} c,升档或加购解锁`;
  }
  // QuotaExceeded(资源容量类)
  const label = KIND_LABEL[r.kind] ?? r.kind;
  // v5(2026-06-26):漫创态改为 Pro/Max 订阅 或 自携密钥解锁,去漫画包门槛
  if (r.kind === "comics_per_month") {
    return "漫创态是 Pro / Max 会员功能。升级订阅,或开通「自携密钥 ¥5/月」即可解锁漫创态与全部功能。";
  }
  return `当前 ${r.plan} 档的「${label}」已用尽 (${r.used}/${r.limit}),升级解锁更多`;
});

function handleSubscribe(plan: PlanCard) {
  if (plan.key === auth.plan) return;
  if (plan.key === "free") return;   // 免费档无需"订阅"
  // 2026-06-09 商业化重塑:接入统一支付。SKU code = sub_<plan>_<cycle>
  // plan.key ∈ pro/max/super_max,billingCycle ∈ monthly/yearly
  const skuCode = `sub_${plan.key}_${billingCycle.value}`;
  upgradeModal.close();
  pay.open(skuCode, {
    onFulfilled: () => {
      // 订阅发放后刷新用户档位 + 配额
      void auth.fetchMe();
      toast.success(`已升级到「${plan.name}」`);
    },
  });
}

function handleBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) upgradeModal.close();
}
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="upgradeModal.isOpen.value"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        aria-label="升级订阅"
        @click="handleBackdrop"
        @keydown.esc="upgradeModal.close()"
      >
        <div class="modal-card surface">
          <header class="modal-header">
            <div>
              <h2 class="modal-title">选择订阅档位</h2>
              <p v-if="reasonText" class="modal-subtitle reason">{{ reasonText }}</p>
              <p v-else class="modal-subtitle">最超值:用「自携密钥」每月 ¥5 解锁全部功能;或选下方订阅</p>
            </div>
            <button class="close-btn" type="button" aria-label="关闭" @click="upgradeModal.close()">
              ×
            </button>
          </header>

          <!-- v5 主打:自携密钥 hero(¥5/月,解锁全部功能,不占平台额度)-->
          <section class="byok-hero" @click="handleByok" role="button" tabindex="0">
            <div class="byok-hero-text">
              <span class="byok-hero-badge">主打 · 最超值</span>
              <h3 class="byok-hero-title">自携密钥 · <strong>¥5/月</strong></h3>
              <p class="byok-hero-desc">
                接上你自己的大模型 key，<strong>五大创作态全解锁</strong>(含漫创态)、
                <strong>不再消耗平台额度</strong>。只付一点辛苦费，用到顶级平台的全部能力。
              </p>
            </div>
            <span class="byok-hero-cta">开通自携密钥 →</span>
          </section>

          <!-- 月付 / 年付切换 -->
          <div class="billing-toggle">
            <button
              type="button"
              class="cycle-btn"
              :class="{ 'cycle-btn--active': billingCycle === 'monthly' }"
              @click="billingCycle = 'monthly'"
            >按月</button>
            <button
              type="button"
              class="cycle-btn"
              :class="{ 'cycle-btn--active': billingCycle === 'yearly' }"
              @click="billingCycle = 'yearly'"
            >
              按年
              <span class="cycle-discount">省 ~1.8 月</span>
            </button>
          </div>

          <section class="plan-grid">
            <article
              v-for="plan in PLANS"
              :key="plan.key"
              class="plan-card"
              :class="{
                'plan-card--highlight': plan.highlight,
                'plan-card--current': auth.plan === plan.key,
              }"
            >
              <header class="plan-header">
                <h3 class="plan-name">{{ plan.name }}</h3>
                <p class="plan-price">
                  {{ billingCycle === "monthly" ? plan.monthlyPrice : plan.yearlyPrice }}
                </p>
                <p
                  v-if="billingCycle === 'yearly' && plan.key !== 'free'"
                  class="plan-price-eq"
                >折合 {{ plan.yearlyMonthEq }}</p>
              </header>

              <ul class="plan-bullets">
                <li v-for="b in plan.bullets" :key="b" v-html="b.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')" />
              </ul>

              <button
                class="plan-cta"
                :class="{
                  'plan-cta--current': auth.plan === plan.key,
                  'plan-cta--primary': plan.highlight && auth.plan !== plan.key,
                }"
                :disabled="auth.plan === plan.key || plan.key === 'free'"
                @click="handleSubscribe(plan)"
              >
                <template v-if="auth.plan === plan.key">当前档位</template>
                <template v-else-if="plan.key === 'free'">免费使用</template>
                <template v-else>立即订阅</template>
              </button>
            </article>
          </section>

          <footer class="modal-footer">
            <p class="footnote">
              <strong>推荐自携密钥 ¥5/月解锁全部</strong>
              · 订阅首次 5 折 · 年付额外 -15%
              · 老用户 6 个月价格保护(协议第三章 §价格快照)
            </p>
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
  background: rgba(31, 31, 30, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  /* v5:super_max 下架 → 3 档卡片,宽度收窄 */
  max-width: 900px;
  max-height: calc(100vh - var(--space-8));
  display: flex;
  flex-direction: column;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  overflow: hidden;
}

.modal-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-5) var(--space-6);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.modal-title {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: var(--space-1);
}

.modal-subtitle {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: var(--line-normal);
}
.modal-subtitle.reason {
  color: var(--color-accent-text);
}

.close-btn {
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-muted);
  border-radius: var(--radius-full);
  flex-shrink: 0;
  transition: all var(--duration-fast) var(--ease-out);
}

.close-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

/* ===== 月/年付切换 ===== */
.billing-toggle {
  display: inline-flex;
  align-self: center;
  margin: var(--space-4) auto 0;
  padding: 4px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-full);
  gap: 2px;
}
.cycle-btn {
  padding: 6px var(--space-5);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.cycle-btn:hover {
  color: var(--color-text);
}
.cycle-btn--active {
  background: var(--color-surface);
  color: var(--color-accent-text);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
  font-weight: 500;
}
.cycle-discount {
  font-size: 10px;
  padding: 1px 6px;
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-radius: var(--radius-sm);
  font-weight: 600;
}
.cycle-btn:not(.cycle-btn--active) .cycle-discount {
  opacity: 0.6;
}

/* ===== v5 主打:自携密钥 hero ===== */
.byok-hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  margin: var(--space-4) var(--space-5) 0;
  padding: var(--space-4) var(--space-5);
  border-radius: var(--radius-lg);
  background: radial-gradient(120% 160% at 0% 0%, #8B5CF6 0%, #6D28D9 55%, #3B1F8B 100%);
  color: #fff;
  cursor: pointer;
  box-shadow: 0 10px 28px rgba(124, 58, 237, 0.28);
  transition: transform var(--duration-base) var(--ease-out), box-shadow var(--duration-base) var(--ease-out);
}
.byok-hero:hover { transform: translateY(-2px); box-shadow: 0 14px 36px rgba(124, 58, 237, 0.34); }
.byok-hero-text { min-width: 0; }
.byok-hero-badge {
  display: inline-block;
  font-size: 11px;
  letter-spacing: 1px;
  background: rgba(255, 255, 255, 0.2);
  padding: 3px 10px;
  border-radius: var(--radius-full);
  margin-bottom: 6px;
}
.byok-hero-title { font-size: var(--text-lg); font-weight: 700; margin: 0 0 4px; }
.byok-hero-title strong { font-size: var(--text-xl); }
.byok-hero-desc { font-size: var(--text-xs); line-height: 1.6; opacity: 0.9; margin: 0; max-width: 46em; }
.byok-hero-desc strong { color: #fff; font-weight: 700; }
.byok-hero-cta {
  flex-shrink: 0;
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-accent-text);
  background: #fff;
  border-radius: var(--radius-md);
  white-space: nowrap;
}
@media (max-width: 640px) {
  .byok-hero { flex-direction: column; align-items: flex-start; }
}

/* ===== 3 档卡片 ===== */
.plan-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-3);
  padding: var(--space-5) var(--space-5);
  overflow-y: auto;
}

@media (max-width: 760px) {
  .plan-grid {
    grid-template-columns: 1fr;
  }
}
@media (max-width: 560px) {
  .plan-grid {
    grid-template-columns: 1fr;
  }
}

.plan-card {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  transition: all var(--duration-fast) var(--ease-out);
}

.plan-card--highlight {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.plan-card--current {
  border-color: #16A34A;
  border-width: 2px;
}

.plan-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}

.plan-name {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
}

.plan-price {
  font-size: var(--text-xl);
  font-weight: 700;
  color: var(--color-accent-text);
  letter-spacing: -0.01em;
}
.plan-price-eq {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin-top: 2px;
}

.plan-card--highlight .plan-price {
  color: var(--color-accent);
}

.plan-bullets {
  list-style: none;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  flex: 1;
}

.plan-bullets li {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  padding-left: var(--space-3);
  position: relative;
  line-height: var(--line-relaxed);
}
.plan-bullets li :deep(strong) {
  color: var(--color-accent-text);
  font-weight: 600;
}

.plan-bullets li::before {
  content: "·";
  position: absolute;
  left: 4px;
  color: var(--color-accent);
  font-weight: 700;
}

.plan-cta {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  transition: all var(--duration-fast) var(--ease-out);
}

.plan-cta:hover:not(:disabled) {
  border-color: var(--color-accent);
  color: var(--color-accent);
}

.plan-cta--primary {
  background: var(--color-accent);
  border-color: var(--color-accent);
  color: var(--color-text-on-accent);
}

.plan-cta--primary:hover:not(:disabled) {
  filter: brightness(1.05);
  color: var(--color-text-on-accent);
}

.plan-cta--current,
.plan-cta:disabled {
  background: var(--color-bg-subtle);
  color: var(--color-text-subtle);
  border-color: var(--color-border);
  cursor: not-allowed;
}

.coming-soon {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  padding: 2px var(--space-2);
  font-size: 10px;
  color: var(--color-text-muted);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font-weight: 500;
  letter-spacing: 0.02em;
}

.modal-footer {
  padding: var(--space-4) var(--space-6);
  border-top: 1px solid var(--color-border);
  text-align: center;
  flex-shrink: 0;
}

.footnote {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

</style>
