<script setup lang="ts">
/**
 * KnowledgeView — 知识库(INS-A9 整改,2026-05-27 末³).
 *
 * 用户拍板:PageHero 单一承担说明.view 内部区块不再有 sub-section 释义文案.
 * Emoji 清零(原 ▶ ✓ 8 处)→ Icon 组件.
 * 触发结果格式化(不再 raw JSON.stringify).
 */
import { computed, onMounted, ref } from "vue";
import { fetchAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";

// ============================================================
// Types
// ============================================================

interface AgentStatusResp {
  level1: {
    pending_dates: string[];
    pending_count: number;
    total_reports: number;
    skipped_reports: number;
    valid_reports: number;
    min_events_threshold: number;
    total_cost_yuan: number;
  };
  level2: {
    should_run: boolean;
    reason: string;
    total_summaries: number;
    min_reports_threshold: number;
    min_days_interval: number;
    total_cost_yuan: number;
  };
}

interface DailyReport {
  id: number;
  report_date: string;
  event_count: number;
  skipped: boolean;
  skip_reason: string | null;
  summary_md: string | null;
  metrics: Record<string, unknown>;
  cost_yuan: number;
  created_at: string;
}

interface SummaryItem {
  id: number;
  stage_number: number;
  period_start: string;
  period_end: string;
  daily_count: number;
  cost_yuan: number;
  created_at: string;
  read_at: string | null;
}

// ============================================================
// State
// ============================================================

const status = ref<AgentStatusResp | null>(null);
const dailies = ref<DailyReport[]>([]);
const summaries = ref<SummaryItem[]>([]);
const expandedDailyId = ref<number | null>(null);

const error = ref<string | null>(null);
const loading = ref(false);

const adminToken = (import.meta.env.VITE_ADMIN_TOKEN as string | undefined)
  || "huimeng-insights-dev-token";
const insightsBase = (import.meta.env.VITE_INSIGHTS_BASE as string | undefined)
  || "http://localhost:8001";

// ============================================================
// Load
// ============================================================

async function loadAll() {
  loading.value = true;
  error.value = null;
  try {
    const [s, d, sm] = await Promise.all([
      fetchAdmin<AgentStatusResp>("/agent/status"),
      fetchAdmin<{ reports: DailyReport[] }>("/agent/daily", { limit: 30 }),
      fetchAdmin<{ summaries: SummaryItem[] }>("/agent/summaries"),
    ]);
    status.value = s;
    dailies.value = d.reports;
    summaries.value = sm.summaries;
  } catch (e) {
    error.value = String(e);
  } finally {
    loading.value = false;
  }
}

onMounted(loadAll);

// ============================================================
// Trigger
// ============================================================

const triggering = ref<"daily" | "summary" | null>(null);
const triggerResult = ref<{ kind: "daily" | "summary"; data: Record<string, unknown> } | null>(null);

async function postTrigger(path: string, body: Record<string, unknown>): Promise<Record<string, unknown>> {
  const resp = await fetch(`${insightsBase}${path}`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Admin-Token": adminToken,
    },
    body: JSON.stringify(body),
  });
  if (!resp.ok) {
    throw new Error(`${path} → ${resp.status}: ${await resp.text()}`);
  }
  return await resp.json();
}

async function triggerDaily(force: boolean) {
  triggering.value = "daily";
  triggerResult.value = null;
  error.value = null;
  try {
    const result = await postTrigger("/agent/run-daily", { force });
    triggerResult.value = { kind: "daily", data: result };
    await loadAll();
  } catch (e) {
    error.value = String(e);
  } finally {
    triggering.value = null;
  }
}

async function triggerSummary(force: boolean) {
  triggering.value = "summary";
  triggerResult.value = null;
  error.value = null;
  try {
    const result = await postTrigger("/agent/run-summary", { force });
    triggerResult.value = { kind: "summary", data: result };
    await loadAll();
  } catch (e) {
    error.value = String(e);
  } finally {
    triggering.value = null;
  }
}

// 把 trigger 结果格式化成键值对显示(代替原 JSON.stringify).
const triggerResultEntries = computed(() => {
  if (!triggerResult.value) return [];
  return Object.entries(triggerResult.value.data).map(([k, v]) => ({
    key: k,
    value: typeof v === "object" && v !== null ? JSON.stringify(v) : String(v),
  }));
});

// ============================================================
// Computed
// ============================================================

const totalCost = computed(() => {
  if (!status.value) return "0";
  return (status.value.level1.total_cost_yuan + status.value.level2.total_cost_yuan).toFixed(4);
});

function toggleDaily(id: number) {
  expandedDailyId.value = expandedDailyId.value === id ? null : id;
}

function fmtDate(s: string) {
  return new Date(s).toLocaleString("zh-CN");
}
</script>

<template>
  <section>
    <PageHero
      icon="book-open"
      title="知识库"
      description="Agent 自动跑出的日报和阶段汇总报告 — 平台自我进化的核心。Level 1 每天提炼一份微观洞察,Level 2 在日报堆积够后浓缩出宏观趋势,人读后决定怎么改产品。"
      audience="管理员视角 · 知识库管理"
    />

    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="loading && !status" class="loading">加载中…</div>

    <!-- ============ Agent 状态 + 手动触发 ============ -->
    <div v-if="status">
      <h3>Agent 状态</h3>
      <div class="status-grid">
        <div class="card">
          <div class="card-head">
            <span class="card-title">Level 1 · 单日 agent</span>
            <button
              class="trigger-btn"
              :disabled="triggering !== null"
              @click="triggerDaily(false)"
            >
              <Icon v-if="triggering !== 'daily'" name="play" :size="12" />
              <Icon v-else name="refresh" :size="12" class="spin" />
              <span>{{ triggering === "daily" ? "运行中" : "触发待跑日期" }}</span>
            </button>
          </div>
          <ul class="stat-list">
            <li>
              <span>已生成报告</span>
              <b>{{ status.level1.valid_reports }} 份(有效) + {{ status.level1.skipped_reports }} 份(跳过)</b>
            </li>
            <li>
              <span>待跑日期</span>
              <b>{{ status.level1.pending_count }} 个</b>
            </li>
            <li>
              <span>事件阈值</span>
              <b>&lt; {{ status.level1.min_events_threshold }} 跳过</b>
            </li>
            <li>
              <span>累计成本</span>
              <b>¥ {{ status.level1.total_cost_yuan }}</b>
            </li>
          </ul>
        </div>

        <div class="card">
          <div class="card-head">
            <span class="card-title">Level 2 · 阶段汇总 agent</span>
            <button
              class="trigger-btn"
              :disabled="triggering !== null"
              @click="triggerSummary(false)"
            >
              <Icon v-if="triggering !== 'summary'" name="play" :size="12" />
              <Icon v-else name="refresh" :size="12" class="spin" />
              <span>{{ triggering === "summary" ? "运行中" : "触发汇总" }}</span>
            </button>
          </div>
          <ul class="stat-list">
            <li>
              <span>已生成汇总</span>
              <b>{{ status.level2.total_summaries }} 份</b>
            </li>
            <li>
              <span>触发判定</span>
              <b :class="{ ready: status.level2.should_run, 'not-ready': !status.level2.should_run }">
                <Icon
                  :name="status.level2.should_run ? 'check' : 'clock'"
                  :size="12"
                />
                <span>{{ status.level2.should_run ? "可触发" : "未达条件" }}</span>
              </b>
            </li>
            <li>
              <span>触发条件</span>
              <b class="reason">{{ status.level2.reason }}</b>
            </li>
            <li>
              <span>累计成本</span>
              <b>¥ {{ status.level2.total_cost_yuan }}</b>
            </li>
          </ul>
          <button
            class="force-btn"
            :disabled="triggering !== null"
            @click="triggerSummary(true)"
          >
            <Icon name="alert-triangle" :size="12" />
            <span>强制运行 Level 2(忽略触发条件)</span>
          </button>
        </div>
      </div>

      <!-- 触发结果(格式化键值对,非 JSON) -->
      <div v-if="triggerResult" class="trigger-result">
        <div class="trigger-result-head">
          <Icon name="check" :size="14" />
          <span>{{ triggerResult.kind === "daily" ? "Level 1" : "Level 2" }} 触发完成</span>
        </div>
        <ul class="trigger-result-list">
          <li v-for="entry in triggerResultEntries" :key="entry.key">
            <span class="rk">{{ entry.key }}</span>
            <span class="rv">{{ entry.value }}</span>
          </li>
        </ul>
      </div>

      <p class="total-cost">总 LLM 成本:¥ {{ totalCost }}</p>
    </div>

    <!-- ============ 阶段汇总报告 ============ -->
    <h3>阶段汇总报告 ({{ summaries.length }})</h3>
    <div v-if="summaries.length === 0" class="empty">
      暂无阶段汇总。需要累积 ≥ 30 份日报 或 距上次汇总 ≥ 30 天才会自动触发。
    </div>
    <ul v-else class="summary-list">
      <li v-for="s in summaries" :key="s.id">
        <router-link :to="`/knowledge/summary/${s.id}`" class="summary-link">
          <div class="summary-title">
            <span>第 {{ s.stage_number }} 阶段</span>
            <span v-if="!s.read_at" class="badge-new">未读</span>
          </div>
          <div class="summary-meta">
            {{ s.period_start }} → {{ s.period_end }} · 聚合 {{ s.daily_count }} 份日报 · ¥ {{ s.cost_yuan }}
          </div>
          <div class="summary-time">生成于 {{ fmtDate(s.created_at) }}</div>
        </router-link>
      </li>
    </ul>

    <!-- ============ 日报列表 ============ -->
    <h3>最近 30 份日报</h3>
    <div v-if="dailies.length === 0" class="empty">
      暂无日报。需要先有埋点事件 + 触发 Level 1 agent。
    </div>
    <ul v-else class="daily-list">
      <li
        v-for="d in dailies"
        :key="d.id"
        :class="{ skipped: d.skipped, expanded: expandedDailyId === d.id }"
      >
        <div class="daily-head" @click="toggleDaily(d.id)">
          <span class="daily-date">{{ d.report_date }}</span>
          <span class="daily-events">{{ d.event_count }} 事件</span>
          <span v-if="d.skipped" class="badge-skipped">跳过:{{ d.skip_reason }}</span>
          <span v-else class="daily-cost">¥ {{ d.cost_yuan }}</span>
        </div>
        <pre v-if="expandedDailyId === d.id && d.summary_md" class="daily-body">{{ d.summary_md }}</pre>
      </li>
    </ul>
  </section>
</template>

<style scoped>
h3 {
  font-size: 0.9375rem;
  font-weight: 600;
  margin: 2rem 0 0.75rem;
  color: #1f1f1e;
}

.err { color: #dc2626; padding: 0.75rem; background: #fee2e2; border-radius: 0.375rem; margin-bottom: 1rem; }
.loading { color: #9a968d; font-size: 0.8125rem; }

.empty {
  padding: 1.5rem;
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.5rem;
  text-align: center;
  font-size: 0.8125rem;
  color: #9a968d;
}

/* === Agent 状态卡片 === */
.status-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0.75rem;
}
.card {
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.625rem;
  padding: 1.125rem 1.25rem;
}
.card-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 0.875rem;
  gap: 0.75rem;
}
.card-title {
  font-size: 0.8125rem;
  font-weight: 600;
  color: #1f1f1e;
}
.stat-list {
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: 0.75rem;
}
.stat-list li {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.4375rem 0;
  border-bottom: 1px solid #f5f5f0;
  gap: 0.75rem;
}
.stat-list li:last-child { border: 0; }
.stat-list span { color: #6b6862; }
.stat-list b {
  font-weight: 600;
  color: #1f1f1e;
  display: inline-flex;
  align-items: center;
  gap: 0.25rem;
}
.stat-list b.ready { color: #16A34A; }
.stat-list b.not-ready { color: #9a968d; }
.stat-list b.reason {
  font-size: 0.6875rem;
  font-weight: 400;
  color: #9a968d;
  text-align: right;
  max-width: 16.25rem;
}

/* === 按钮 === */
.trigger-btn, .force-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  padding: 0.3125rem 0.75rem;
  border: 1px solid #7c3aed;
  background: white;
  color: #7c3aed;
  border-radius: 0.3125rem;
  cursor: default;
  transition: background 120ms;
}
.trigger-btn:hover, .force-btn:hover { background: #f3efff; }
.trigger-btn:disabled, .force-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.force-btn {
  border-color: #d97706;
  color: #d97706;
  margin-top: 0.625rem;
  width: 100%;
  justify-content: center;
}
.force-btn:hover { background: #fef3c7; }
.spin { animation: spin 1s linear infinite; }
@keyframes spin {
  to { transform: rotate(360deg); }
}

/* === 触发结果 === */
.trigger-result {
  margin-top: 0.875rem;
  padding: 0.75rem 1rem;
  background: #f3efff;
  border: 1px solid #c4b5fd;
  border-radius: 0.5rem;
}
.trigger-result-head {
  display: flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  font-weight: 600;
  color: #5b21b6;
  margin-bottom: 0.5rem;
}
.trigger-result-list {
  list-style: none;
  margin: 0;
  padding: 0;
  font-size: 0.75rem;
}
.trigger-result-list li {
  display: flex;
  gap: 0.75rem;
  padding: 0.1875rem 0;
}
.rk {
  color: #6b6862;
  min-width: 6.25rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.6875rem;
}
.rv {
  color: #1f1f1e;
  word-break: break-all;
  font-variant-numeric: tabular-nums;
}

.total-cost {
  margin-top: 0.875rem;
  font-size: 0.75rem;
  color: #6b6862;
  text-align: right;
}

/* === 阶段汇总列表 === */
.summary-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}
.summary-link {
  display: block;
  padding: 0.875rem 1.125rem;
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.5rem;
  text-decoration: none;
  color: #1f1f1e;
  transition: border-color 120ms;
}
.summary-link:hover { border-color: #7c3aed; }
.summary-title {
  font-size: 0.875rem;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.badge-new {
  background: #7c3aed;
  color: white;
  font-size: 0.625rem;
  font-weight: 500;
  padding: 0.125rem 0.5rem;
  border-radius: 62.4375rem;
}
.summary-meta { font-size: 0.75rem; color: #6b6862; margin-top: 0.25rem; }
.summary-time { font-size: 0.6875rem; color: #9a968d; margin-top: 0.125rem; }

/* === 日报列表 === */
.daily-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.daily-list li {
  border: 1px solid #e5e1d8;
  border-radius: 0.375rem;
  margin-bottom: 0.25rem;
  background: white;
  overflow: hidden;
}
.daily-list li.skipped { opacity: 0.6; background: #fafafa; }
.daily-list li.expanded { border-color: #7c3aed; }
.daily-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 0.625rem 1rem;
  font-size: 0.75rem;
  cursor: default;
  user-select: none;
}
.daily-head:hover { background: #f7f5f0; }
.daily-date { font-weight: 600; font-variant-numeric: tabular-nums; }
.daily-events { color: #6b6862; }
.badge-skipped {
  font-size: 0.625rem;
  color: #d97706;
  background: #fef3c7;
  padding: 0.125rem 0.5rem;
  border-radius: 62.4375rem;
}
.daily-cost { color: #6b6862; font-variant-numeric: tabular-nums; }
.daily-body {
  margin: 0;
  padding: 0.875rem 1rem;
  background: #fafafa;
  border-top: 1px solid #e5e1d8;
  white-space: pre-wrap;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
  font-size: 0.75rem;
  line-height: 1.7;
  color: #1f1f1e;
}
</style>
