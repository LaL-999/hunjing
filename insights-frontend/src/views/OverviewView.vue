<script setup lang="ts">
/**
 * OverviewView — 平台总览(INS-A9 整改 + INS-B4 加 3 卡,2026-05-27 末⁵²).
 *
 * INS-A9:PageHero 承担文案 / 事件类型翻译.
 * INS-B4:加 LLM 累计成本 / 本月失败推演 / 高风险用户 3 卡(经营信号).
 */
import { onMounted, ref } from "vue";
import { fetchAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";
import { formatEventType } from "../labels";

interface OverviewResp {
  total_events: number;
  today_events: number;
  active_users_7d: number;
  registered_users: number | null;
  projects_count: number | null;
  simulations_count: number | null;
  huimeng_attached: boolean;
  type_distribution: { type: string; count: number }[];
  // INS-B4 新加 3 卡
  llm_cost_total_yuan: number | null;
  failed_simulations_this_month: number | null;
  high_risk_users_count: number | null;
}

const data = ref<OverviewResp | null>(null);
const error = ref<string | null>(null);

async function load() {
  try {
    data.value = await fetchAdmin<OverviewResp>("/admin/overview");
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(load);

function fmtYuan(n: number): string {
  if (n >= 10000) return `¥${(n / 10000).toFixed(2)}万`;
  return `¥${n.toFixed(2)}`;
}
</script>

<template>
  <section>
    <PageHero
      icon="layout-dashboard"
      title="总览"
      description="实时看平台用户规模、今日活跃、事件类型分布,以及本月经营核心信号(LLM 成本/失败推演/高风险用户)。数字异常时,从「用户画像」下钻定位原因。"
      audience="管理员视角"
    />

    <div v-if="error" class="err">{{ error }}</div>

    <div v-if="data" class="grid">
      <div class="card">
        <div class="label">总事件数</div>
        <div class="num">{{ data.total_events.toLocaleString() }}</div>
      </div>
      <div class="card">
        <div class="label">今日事件</div>
        <div class="num">{{ data.today_events.toLocaleString() }}</div>
      </div>
      <div class="card">
        <div class="label">7 日活跃用户</div>
        <div class="num">{{ data.active_users_7d }}</div>
      </div>
      <div class="card">
        <div class="label">注册用户</div>
        <div class="num">{{ data.registered_users ?? "—" }}</div>
        <div v-if="!data.huimeng_attached" class="attach-warn">huimeng.db 未连接</div>
      </div>
      <div class="card">
        <div class="label">项目数</div>
        <div class="num">{{ data.projects_count ?? "—" }}</div>
      </div>
      <div class="card">
        <div class="label">推演数</div>
        <div class="num">{{ data.simulations_count ?? "—" }}</div>
      </div>

      <!-- INS-B4 新加 3 卡:经营信号 -->
      <div class="card accent">
        <div class="label">
          <Icon name="trending-up" :size="12" />
          <span>LLM 累计成本</span>
        </div>
        <div class="num">{{ data.llm_cost_total_yuan === null ? "—" : fmtYuan(data.llm_cost_total_yuan) }}</div>
      </div>
      <div class="card card-warn">
        <div class="label">
          <Icon name="x" :size="12" />
          <span>本月失败推演</span>
        </div>
        <div class="num">{{ data.failed_simulations_this_month ?? "—" }}</div>
      </div>
      <div class="card card-crit">
        <div class="label">
          <Icon name="shield" :size="12" />
          <span>高风险用户</span>
        </div>
        <div class="num">{{ data.high_risk_users_count ?? "—" }}</div>
      </div>
    </div>

    <h3 v-if="data">事件类型分布</h3>
    <table v-if="data && data.type_distribution.length" class="dist">
      <tr v-for="row in data.type_distribution" :key="row.type">
        <td>{{ formatEventType(row.type) }}</td>
        <td class="raw">{{ row.type }}</td>
        <td class="r">{{ row.count.toLocaleString() }}</td>
      </tr>
    </table>
    <div v-else-if="data" class="empty">
      暂无事件。请打开主平台产生用户行为后回来查看。
    </div>
  </section>
</template>

<style scoped>
h3 { font-size: 0.9375rem; font-weight: 600; margin-top: 2rem; }

.grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 0.75rem;
}
.card {
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.625rem;
  padding: 1.125rem 1.25rem;
}
.card.accent {
  background: linear-gradient(135deg, #f3efff 0%, white 100%);
  border-color: #c4b5fd;
}
.card.card-warn {
  background: linear-gradient(135deg, #fef3c7 0%, white 100%);
  border-color: #fcd34d;
}
.card.card-crit {
  background: linear-gradient(135deg, #fee2e2 0%, white 100%);
  border-color: #fca5a5;
}
.label {
  font-size: 0.75rem;
  color: #6b6862;
  display: flex;
  align-items: center;
  gap: 0.25rem;
}
.num {
  font-size: 1.75rem;
  font-weight: 700;
  margin-top: 0.25rem;
  font-variant-numeric: tabular-nums;
  color: #1f1f1e;
}
.attach-warn {
  font-size: 0.6875rem;
  color: #d97706;
  margin-top: 0.375rem;
  font-weight: 500;
}

.err { color: #dc2626; padding: 0.75rem; background: #fee2e2; border-radius: 0.375rem; }

.dist {
  width: 100%;
  margin-top: 0.75rem;
  border-collapse: collapse;
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.5rem;
  overflow: hidden;
}
.dist tr:not(:last-child) td { border-bottom: 0.0625rem solid #f0ece3; }
.dist td {
  padding: 0.625rem 1rem;
  font-size: 0.8125rem;
}
.dist td.raw {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.6875rem;
  color: #9a968d;
}
.dist td.r {
  text-align: right;
  font-variant-numeric: tabular-nums;
  font-weight: 600;
}

.empty {
  margin-top: 0.75rem;
  padding: 2rem 1.5rem;
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.5rem;
  text-align: center;
  font-size: 0.8125rem;
  color: #9a968d;
}
</style>
