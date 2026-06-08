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

import { useAuthStore } from "../stores/auth";
import { useUpgradeModal } from "../composables/useUpgradeModal";
import { toast } from "../composables/useToast";
import type { Plan } from "../api/types";

const auth = useAuthStore();
const upgradeModal = useUpgradeModal();

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

// ECON-1 订阅重设(2026-05-27 末⁴):4 档配额按真实毛利目标重算
//   - 单 credit 真实成本 ¥0.13(token×0.013/0.026 反推),旧定价毛利仅 7%
//   - 新定价:Pro ¥0.23/c / Max ¥0.22/c / 超级 Max ¥0.21/c(满配额毛利 39-44%)
//   - 年付从 -10% 升到 -15%(月付 × 12 × 0.85)
//   - 漫创态从订阅福利改为"单买漫画包 ¥30/次"(各档 comics_per_month 清零,功能临时禁用至 ECON-2)
//   - 首次订阅 5 折优惠(billing_service.subscribe 检查 snapshot 历史 == 0)
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
      "不开放漫画态(付费档亦不送,需单买漫画包)",
    ],
  },
  {
    key: "pro",
    name: "Pro",
    monthlyPrice: "¥138/月",
    yearlyPrice: "¥1407.60/年",
    yearlyMonthEq: "¥117.30/月",
    highlight: true,
    bullets: [
      "**600 credit / 月**(基准单价 ¥0.23/c)",
      "项目 5 个 · 角色 30/项",
      "重塑度上限 80%",
      "约 90 次中等推演 / 月(token 加权计费)",
      "首次订阅 5 折 · 月末清零,加购 1 年有效",
    ],
  },
  {
    key: "max",
    name: "Max",
    monthlyPrice: "¥438/月",
    yearlyPrice: "¥4467.60/年",
    yearlyMonthEq: "¥372.30/月",
    bullets: [
      "**2000 credit / 月**(单价 ¥0.22/c,**比 Pro 便宜 4.3%**)",
      "项目 20 个 · 角色 50/项",
      "重塑度上限 90%(满档)",
      "约 300 次中等推演 + 大量素材库 / 守护者",
      "升档专享:credit 单价更低",
    ],
  },
  {
    key: "super_max",
    name: "超级 Max",
    monthlyPrice: "¥1388/月",
    yearlyPrice: "¥14157.60/年",
    yearlyMonthEq: "¥1179.80/月",
    bullets: [
      "**6500 credit / 月**(单价 ¥0.21/c,**比 Max 再便宜 4.5%**)",
      "项目无限 · 角色 100/项",
      "重塑度上限 90%(满档)",
      "约 1000 次中等推演 + 工作室级",
      "升档双重优惠:credit 多 3.25 倍 + 单价累计 -8.7%",
    ],
  },
];

const KIND_LABEL: Record<string, string> = {
  // 资源容量类(QuotaExceeded)
  characters_per_project: "项目内角色数",
  projects_total: "项目总数",
  reshape_percent: "重塑度上限",
  // ECON-2(2026-05-27 末⁴⁴):漫创态改为单买漫画包,¥30/次,有效期 6 月
  comics_per_month: "漫画功能(需购买漫画包)",
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
  // ECON-2(2026-05-27 末⁴⁴):漫画态采用单买漫画包制 ¥30/次,有效期 6 月.
  // 用户没有可用漫画包时引导购买(后续接通"购买"按钮 → /api/credit/comic_pack/purchase).
  if (r.kind === "comics_per_month") {
    return (
      "需要购买漫画包才能创建漫画。漫画态采用单买制:¥30/次,有效期 6 个月。" +
      "请到「账号 / 加购」购买漫画包后再创建。"
    );
  }
  return `当前 ${r.plan} 档的「${label}」已用尽 (${r.used}/${r.limit}),升级解锁更多`;
});

function handleSubscribe(plan: PlanCard) {
  if (plan.key === auth.plan) return;
  if (plan.key === "free") return;   // 免费档无需"订阅"
  toast.info(
    `「${plan.name}」${billingCycle.value === "yearly" ? "年付" : "月付"}订阅功能正在开发,敬请期待 · 早鸟内测联系 hi@huimeng.example`,
    6000,
  );
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
              <p v-else class="modal-subtitle">解锁完整推演 / 漫画态 / 大字数 / 重塑度上限</p>
            </div>
            <button class="close-btn" type="button" aria-label="关闭" @click="upgradeModal.close()">
              ×
            </button>
          </header>

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

              <p v-if="plan.key !== 'free' && auth.plan !== plan.key" class="coming-soon">敬请期待</p>
            </article>
          </section>

          <footer class="modal-footer">
            <p class="footnote">
              支付通道接入中。早鸟内测可联系 <span class="mono">hi@huimeng.example</span>
              · <strong>首次订阅享 5 折</strong>
              · 年付额外 -15%
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
  /* Sprint D.1:从 3 卡到 4 卡,宽度需要 + 130-150px 才不挤 */
  max-width: 1080px;
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

/* ===== 4 档卡片 ===== */
.plan-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: var(--space-3);
  padding: var(--space-5) var(--space-5);
  overflow-y: auto;
}

@media (max-width: 1000px) {
  .plan-grid {
    grid-template-columns: repeat(2, 1fr);
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
