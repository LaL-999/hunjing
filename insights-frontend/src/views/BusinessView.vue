<script setup lang="ts">
/**
 * BusinessView — B1 经营驾驶舱(INS-B Phase 3 + Phase 4 sub-tab 改造,2026-05-27 末⁵³).
 *
 * Phase 4 改造:
 *   - 顶部毛利三连卡始终可见(老板核心指标)
 *   - 明细分 sub-tab:成本 / 收入 / 漫创(次要)
 */
import { computed, onMounted, ref } from "vue";
import { fetchAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";
import TabBar from "../components/TabBar.vue";

interface BusinessResp {
  huimeng_attached: boolean;
  llm_cost: {
    total_yuan: number;
    today_yuan: number;
    this_week_yuan: number;
    this_month_yuan: number;
  } | null;
  cost_by_action: { action: string; cost_yuan: number; call_count: number }[];
  avg_sim_cost: {
    average_yuan: number;
    total_yuan: number;
    completed_sims: number;
  } | null;
  revenue: {
    total_yuan: number;
    subscription_yuan: number;
    comic_pack_yuan: number;
    addon_yuan: number;
    active_subscribers: number;
    comic_packs_sold: number;
    addon_packs_sold: number;
  } | null;
  plan_distribution: { plan: string; user_count: number }[];
  arpu: { paid_users: number; arpu_yuan: number } | null;
  gross_profit: {
    revenue_yuan: number;
    llm_cost_yuan: number;
    gross_profit_yuan: number;
    gross_margin: number;
  } | null;
  comic_economy: {
    llm_cost_yuan: number;
    revenue_yuan: number;
    packs_sold: number;
    completed_comics: number;
    note: string;
  } | null;
}

const data = ref<BusinessResp | null>(null);
const error = ref<string | null>(null);

async function load() {
  try {
    data.value = await fetchAdmin<BusinessResp>("/admin/business");
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(load);

const activeTab = ref<"cost" | "revenue" | "comic">("cost");

const tabs = computed(() => {
  const base = [
    { key: "cost", label: "成本归因", icon: "trending-up" },
    { key: "revenue", label: "收入分项", icon: "database" },
  ];
  if (data.value?.comic_economy) {
    base.push({ key: "comic", label: "漫创(次要)", icon: "book-open" });
  }
  return base;
});

const PLAN_LABELS: Record<string, string> = {
  free: "Free 免费",
  pro: "Pro 专业",
  max: "Max 高产",
  super_max: "SuperMax 专业",
  founder: "创始人",
};
const ACTION_LABELS: Record<string, string> = {
  refine: "AI 精修",
  continuation: "续作生成",
  extract: "文本抽取",
  comic_scripter: "漫画分镜",
  comic_visual_assets: "漫画视觉",
  comic_anchor_card: "漫画锚定卡",
  comic_anchor_descriptor: "漫画锚描述",
};

function fmtYuan(n: number): string {
  if (n >= 10000) return `¥${(n / 10000).toFixed(2)}万`;
  return `¥${n.toFixed(2)}`;
}
function planLabel(p: string): string { return PLAN_LABELS[p] || p; }
function actionLabel(a: string): string { return ACTION_LABELS[a] || a; }
</script>

<template>
  <section>
    <PageHero
      icon="line-chart"
      title="经营驾驶舱"
      description="平台真金白银的烧 vs 收 — LLM 实际成本 / 用户付费收入 / 毛利估算 / 付费分层。决定要不要调价、压成本、推哪档订阅。"
      audience="老板视角"
    />

    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="data && !data.huimeng_attached" class="warn">
      huimeng.db 未连接,无法读经营数据
    </div>

    <div v-if="data && data.huimeng_attached">
      <!-- 顶部:毛利核心三连(始终可见) -->
      <div v-if="data.gross_profit" class="hero-grid">
        <div class="hero-card revenue-card">
          <div class="hero-label">总收入</div>
          <div class="hero-val">{{ fmtYuan(data.gross_profit.revenue_yuan) }}</div>
        </div>
        <div class="hero-card cost-card">
          <div class="hero-label">LLM 成本</div>
          <div class="hero-val">{{ fmtYuan(data.gross_profit.llm_cost_yuan) }}</div>
        </div>
        <div class="hero-card profit-card" :class="{ loss: data.gross_profit.gross_profit_yuan < 0 }">
          <div class="hero-label">毛利</div>
          <div class="hero-val">{{ fmtYuan(data.gross_profit.gross_profit_yuan) }}</div>
          <div class="hero-sub">{{ (data.gross_profit.gross_margin * 100).toFixed(1) }}% 毛利率</div>
        </div>
      </div>

      <!-- sub-tab -->
      <TabBar v-model="activeTab" :tabs="tabs" />

      <!-- 1. 成本归因 -->
      <div v-if="activeTab === 'cost'" class="tab-content">
        <!-- 时间窗口 -->
        <div v-if="data.llm_cost" class="num-grid">
          <div class="num-item">
            <div class="num-val">{{ fmtYuan(data.llm_cost.total_yuan) }}</div>
            <div class="num-label">累计</div>
          </div>
          <div class="num-item">
            <div class="num-val">{{ fmtYuan(data.llm_cost.today_yuan) }}</div>
            <div class="num-label">今日</div>
          </div>
          <div class="num-item">
            <div class="num-val">{{ fmtYuan(data.llm_cost.this_week_yuan) }}</div>
            <div class="num-label">本周</div>
          </div>
          <div class="num-item">
            <div class="num-val">{{ fmtYuan(data.llm_cost.this_month_yuan) }}</div>
            <div class="num-label">本月</div>
          </div>
        </div>

        <!-- 单推演均价 -->
        <div v-if="data.avg_sim_cost" class="sub-section">
          <h4 class="sub-h">单推演成本</h4>
          <div class="num-grid">
            <div class="num-item">
              <div class="num-val">{{ fmtYuan(data.avg_sim_cost.average_yuan) }}</div>
              <div class="num-label">平均 / 推演</div>
            </div>
            <div class="num-item">
              <div class="num-val">{{ fmtYuan(data.avg_sim_cost.total_yuan) }}</div>
              <div class="num-label">已完成总成本</div>
            </div>
            <div class="num-item">
              <div class="num-val muted-num">{{ data.avg_sim_cost.completed_sims }}</div>
              <div class="num-label">完成推演数</div>
            </div>
          </div>
        </div>

        <!-- 按 action 分类成本 top 10 -->
        <h4 class="sub-h">业务动作成本归因 top 10</h4>
        <table v-if="data.cost_by_action.length" class="data-table">
          <thead>
            <tr>
              <th>业务动作</th>
              <th class="r">累计成本</th>
              <th class="r">调用次数</th>
              <th class="r">单次均价</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="a in data.cost_by_action" :key="a.action">
              <td>{{ actionLabel(a.action) }} <span class="muted">/ {{ a.action }}</span></td>
              <td class="r"><b>{{ fmtYuan(a.cost_yuan) }}</b></td>
              <td class="r">{{ a.call_count.toLocaleString() }}</td>
              <td class="r muted">¥{{ (a.cost_yuan / a.call_count).toFixed(4) }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">暂无消费数据</div>
      </div>

      <!-- 2. 收入分项 -->
      <div v-if="activeTab === 'revenue'" class="tab-content">
        <!-- 收入分项 -->
        <div v-if="data.revenue" class="num-grid">
          <div class="num-item accent">
            <div class="num-val">{{ fmtYuan(data.revenue.subscription_yuan) }}</div>
            <div class="num-label">订阅 ({{ data.revenue.active_subscribers }} 活跃)</div>
          </div>
          <div class="num-item">
            <div class="num-val">{{ fmtYuan(data.revenue.addon_yuan) }}</div>
            <div class="num-label">credit 包 ({{ data.revenue.addon_packs_sold }} 单)</div>
          </div>
          <div class="num-item secondary-item">
            <div class="num-val">{{ fmtYuan(data.revenue.comic_pack_yuan) }}</div>
            <div class="num-label">漫画包 ({{ data.revenue.comic_packs_sold }} 单)</div>
          </div>
        </div>

        <!-- 付费分层 -->
        <h4 class="sub-h">付费分层</h4>
        <table v-if="data.plan_distribution.length" class="data-table compact">
          <tr v-for="p in data.plan_distribution" :key="p.plan">
            <td>{{ planLabel(p.plan) }}</td>
            <td class="r"><b>{{ p.user_count }}</b> 人</td>
          </tr>
        </table>

        <!-- ARPU -->
        <h4 class="sub-h">ARPU 平均每用户付费</h4>
        <div v-if="data.arpu" class="num-grid">
          <div class="num-item">
            <div class="num-val">{{ fmtYuan(data.arpu.arpu_yuan) }}</div>
            <div class="num-label">ARPU</div>
          </div>
          <div class="num-item">
            <div class="num-val">{{ data.arpu.paid_users }}</div>
            <div class="num-label">付费用户数</div>
          </div>
        </div>
      </div>

      <!-- 3. 漫创(次要) -->
      <div v-if="activeTab === 'comic' && data.comic_economy" class="tab-content secondary">
        <p class="note">{{ data.comic_economy.note }}</p>
        <div class="num-grid">
          <div class="num-item secondary-item">
            <div class="num-val">{{ fmtYuan(data.comic_economy.revenue_yuan) }}</div>
            <div class="num-label">漫画包收入</div>
          </div>
          <div class="num-item secondary-item">
            <div class="num-val crit">{{ fmtYuan(data.comic_economy.llm_cost_yuan) }}</div>
            <div class="num-label">漫画 LLM 成本</div>
          </div>
          <div class="num-item secondary-item">
            <div class="num-val">{{ data.comic_economy.packs_sold }}</div>
            <div class="num-label">售出包数</div>
          </div>
          <div class="num-item secondary-item">
            <div class="num-val">{{ data.comic_economy.completed_comics }}</div>
            <div class="num-label">完成漫画数</div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.err { color: #dc2626; padding: 0.75rem; background: #fee2e2; border-radius: 0.375rem; margin-bottom: 1rem; }
.warn { color: #92400e; padding: 0.75rem; background: #fef3c7; border-radius: 0.375rem; margin-bottom: 1rem; font-size: 0.8125rem; }
.empty { color: #9a968d; font-style: italic; padding: 3rem 1.5rem; text-align: center; font-size: 0.8125rem; }

.hero-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.75rem;
  margin-bottom: 1.25rem;
}
.hero-card {
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.625rem;
  padding: 1.25rem 1.5rem;
}
.hero-card.revenue-card { background: linear-gradient(135deg, #f3efff 0%, white 100%); border-color: #c4b5fd; }
.hero-card.cost-card { background: linear-gradient(135deg, #fef3c7 0%, white 100%); border-color: #fcd34d; }
.hero-card.profit-card { background: linear-gradient(135deg, #d1fae5 0%, white 100%); border-color: #6ee7b7; }
.hero-card.profit-card.loss { background: linear-gradient(135deg, #fee2e2 0%, white 100%); border-color: #fca5a5; }
.hero-label { font-size: 0.75rem; color: #6b6862; }
.hero-val { font-size: 2rem; font-weight: 700; margin-top: 0.25rem; font-variant-numeric: tabular-nums; }
.hero-sub { font-size: 0.75rem; color: #6b6862; margin-top: 0.25rem; }

.tab-content {
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.5rem;
  padding: 1.25rem 1.5rem;
}
.tab-content.secondary { background: #fafafa; opacity: 0.85; }

.num-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(9.375rem, 1fr));
  gap: 0.75rem;
}
.num-item {
  background: #f7f5f0;
  padding: 0.875rem 1rem;
  border-radius: 0.375rem;
}
.num-item.accent { background: #f3efff; }
.num-item.secondary-item { background: #ede9e0; }
.num-val { font-size: 1.375rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.num-val.crit { color: #dc2626; }
.num-val.muted-num { color: #9a968d; font-size: 1.125rem; }
.num-label { font-size: 0.6875rem; color: #6b6862; margin-top: 0.25rem; }

.sub-section { margin-top: 1rem; }
.sub-h { font-size: 0.75rem; font-weight: 600; color: #6b6862; margin: 1.25rem 0 0.5rem; text-transform: uppercase; letter-spacing: 0.03125rem; }

.data-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 0.8125rem;
}
.data-table.compact tr td:first-child { padding-left: 0; }
.data-table th, .data-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #f5f5f0;
  text-align: left;
}
.data-table th { font-weight: 600; color: #6b6862; font-size: 0.6875rem; text-transform: uppercase; letter-spacing: 0.03125rem; }
.data-table td.r, .data-table th.r { text-align: right; font-variant-numeric: tabular-nums; }
.muted { color: #9a968d; font-size: 0.6875rem; }
.note { font-size: 0.75rem; color: #9a968d; font-style: italic; margin-bottom: 0.75rem; }
</style>
