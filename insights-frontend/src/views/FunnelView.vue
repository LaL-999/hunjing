<script setup lang="ts">
/**
 * FunnelView — 转化漏斗(INS-A9 + INS-B6 加深加宽,2026-05-27 末⁵²).
 *
 * INS-A9:PageHero / 步骤间 arrow-down SVG / 流失数突出.
 * INS-B6:9 步加深(访客 / 注册 / 上传 / 抽取 / 项目 / 推演 / 完成 / 审计 / 二次创作 / 付费)
 *         + 3 筛选器(window / mode / plan).漫创态默认不计入主漏斗(5.0a).
 */
import { computed, onMounted, ref, watch } from "vue";
import { fetchAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";

interface FunnelResp {
  window: string;
  mode: string;
  plan: string;
  huimeng_attached: boolean;
  steps: { name: string; count: number | null }[];
  note: string | null;
}

const window = ref<"all" | "this_week" | "this_month" | "quarter">("all");
const mode = ref<"all" | "initial" | "middle" | "end" | "cycle">("all");
const plan = ref<"all" | "free" | "pro" | "max" | "super_max">("all");

const data = ref<FunnelResp | null>(null);
const error = ref<string | null>(null);
const loading = ref(false);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    data.value = await fetchAdmin<FunnelResp>("/admin/funnel", {
      window: window.value,
      mode: mode.value,
      plan: plan.value,
    });
  } catch (e) {
    error.value = String(e);
  } finally {
    loading.value = false;
  }
}

function ratio(curr: number | null, prev: number | null): string {
  if (curr === null || prev === null || prev === 0) return "—";
  return `${Math.round((curr / prev) * 100)}%`;
}

function loss(curr: number | null, prev: number | null): number | null {
  if (curr === null || prev === null) return null;
  return prev - curr;
}

const maxCount = computed(() => {
  if (!data.value || data.value.steps.length === 0) return 1;
  return data.value.steps[0].count ?? 1;
});

onMounted(load);
watch([window, mode, plan], load);
</script>

<template>
  <section>
    <PageHero
      icon="funnel"
      title="转化漏斗"
      description="从访客到付费的 10 步链路 + 3 个维度筛选(时间窗口 / 创作态 / 付费档)。每步流失数突出显示,定位平台留存关键拐点。漫创态默认不计入主漏斗(可独立筛选)。"
      audience="增长视角"
    />

    <div v-if="error" class="err">{{ error }}</div>

    <!-- 3 筛选器 -->
    <div class="filter-bar">
      <div class="filter">
        <label>时间窗口</label>
        <select v-model="window">
          <option value="all">全历史</option>
          <option value="this_week">本周</option>
          <option value="this_month">本月</option>
          <option value="quarter">近 90 天</option>
        </select>
      </div>
      <div class="filter">
        <label>创作态</label>
        <select v-model="mode">
          <option value="all">前 3 态(主漏斗)</option>
          <option value="initial">初创态</option>
          <option value="middle">中段态</option>
          <option value="end">收尾态</option>
          <option value="cycle">漫创态(单独)</option>
        </select>
      </div>
      <div class="filter">
        <label>付费档位</label>
        <select v-model="plan">
          <option value="all">全部</option>
          <option value="free">Free</option>
          <option value="pro">Pro</option>
          <option value="max">Max</option>
          <option value="super_max">SuperMax</option>
        </select>
      </div>
      <button class="refresh-btn" :disabled="loading" @click="load">
        <Icon name="refresh" :size="14" /> 刷新
      </button>
    </div>

    <div v-if="data && data.note" class="note">{{ data.note }}</div>
    <div v-if="data && !data.huimeng_attached" class="warn">
      huimeng.db 未连接,无法计算完整漏斗
    </div>

    <div v-if="data && data.steps.length" class="funnel">
      <template v-for="(step, i) in data.steps" :key="step.name">
        <div class="step">
          <div class="step-name">
            <span class="step-num">{{ i + 1 }}</span>
            {{ step.name }}
          </div>
          <div class="bar-wrap">
            <div
              class="bar"
              :style="{
                width: maxCount > 0 ? `${((step.count ?? 0) / maxCount) * 100}%` : '0%',
              }"
            />
          </div>
          <div class="count">{{ step.count?.toLocaleString() ?? "—" }}</div>
          <div class="ratio">
            {{ i > 0 ? ratio(step.count, data.steps[i - 1].count) : "100%" }}
          </div>
        </div>

        <div v-if="i < data.steps.length - 1" class="connector">
          <Icon name="arrow-down" :size="14" />
          <span v-if="loss(data.steps[i + 1].count, step.count) !== null" class="loss-tag">
            流失 {{ loss(data.steps[i + 1].count, step.count) }}
          </span>
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.err { color: #dc2626; padding: 0.75rem; background: #fee2e2; border-radius: 0.375rem; margin-bottom: 1rem; }
.warn { color: #92400e; padding: 0.75rem; background: #fef3c7; border-radius: 0.375rem; margin-bottom: 1rem; font-size: 0.8125rem; }
.note { color: #5b21b6; font-size: 0.75rem; padding: 0.5rem 0.75rem; background: #f3efff; border: 0.0625rem solid #c4b5fd; border-radius: 0.25rem; margin-bottom: 1rem; }

.filter-bar {
  display: flex;
  align-items: flex-end;
  gap: 0.75rem;
  margin-bottom: 1.25rem;
  background: white;
  padding: 0.875rem 1.125rem;
  border: 1px solid #e5e1d8;
  border-radius: 0.625rem;
}
.filter { display: flex; flex-direction: column; gap: 0.25rem; }
.filter label { font-size: 0.6875rem; color: #6b6862; }
.filter select {
  font-size: 0.8125rem;
  padding: 0.375rem 0.625rem;
  border: 1px solid #d6d1c4;
  border-radius: 0.25rem;
  background: white;
  min-width: 8.75rem;
}
.refresh-btn {
  font-size: 0.75rem;
  padding: 0.4375rem 0.875rem;
  border: 1px solid #c4b5fd;
  background: white;
  color: #7c3aed;
  border-radius: 0.25rem;
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
}
.refresh-btn:hover:not(:disabled) { background: #f3efff; }
.refresh-btn:disabled { opacity: 0.5; }

.funnel { display: flex; flex-direction: column; gap: 0.25rem; }
.step {
  display: grid;
  grid-template-columns: 10rem 1fr 5rem 3.75rem;
  gap: 1rem;
  align-items: center;
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.5rem;
  padding: 0.875rem 1.125rem;
}
.step-name {
  font-size: 0.875rem;
  font-weight: 500;
  display: flex;
  align-items: center;
  gap: 0.625rem;
}
.step-num {
  background: #ede9e0;
  color: #6b6862;
  font-size: 0.6875rem;
  width: 1.375rem;
  height: 1.375rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 62.4375rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}
.bar-wrap {
  background: #ede9e0;
  height: 1.5rem;
  border-radius: 0.25rem;
  overflow: hidden;
}
.bar {
  background: linear-gradient(90deg, #7c3aed 0%, #a78bfa 100%);
  height: 100%;
  transition: width 200ms ease;
}
.count {
  font-variant-numeric: tabular-nums;
  font-weight: 700;
  font-size: 1rem;
}
.ratio {
  font-size: 0.75rem;
  color: #6b6862;
  text-align: right;
  font-weight: 500;
}

.connector {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.25rem 1.125rem 0.25rem 10.875rem;
  color: #9a968d;
  font-size: 0.6875rem;
  height: 1.5rem;
}
.loss-tag {
  color: #d97706;
  font-weight: 500;
  font-variant-numeric: tabular-nums;
}
</style>
