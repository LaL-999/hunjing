<script setup lang="ts">
/**
 * QualityView — B2 质量观察(INS-B Phase 1 + Phase 4 sub-tab 改造,2026-05-27 末⁵³).
 *
 * Phase 4 改造:5 块明细拆 sub-tab(评分/维度/失败/时长/漫创).
 * 漫创态降权:质量数据单 tab(用户偏好 5.0a).
 */
import { computed, onMounted, ref } from "vue";
import { fetchAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";
import TabBar from "../components/TabBar.vue";

interface QualityResp {
  huimeng_attached: boolean;
  audit_score: {
    total: number;
    average: number;
    median: number;
    buckets: { range: string; count: number }[];
  } | null;
  canonical_dimensions: {
    dimension: string;
    total_issues: number;
    by_severity: Record<string, number>;
  }[];
  failure_rate: { total: number; failed: number; rate: number } | null;
  failure_reasons: { reason: string; count: number }[];
  completion_time: {
    samples: number;
    p50_sec: number;
    p95_sec: number;
    avg_sec: number;
  } | null;
  comic_quality: {
    total: number;
    failed: number;
    done: number;
    note: string;
  } | null;
}

const data = ref<QualityResp | null>(null);
const error = ref<string | null>(null);

async function load() {
  try {
    data.value = await fetchAdmin<QualityResp>("/admin/quality");
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(load);

const activeTab = ref<"score" | "dimensions" | "failure" | "time" | "comic">("score");

const tabs = computed(() => {
  const base = [
    { key: "score", label: "评分分布", icon: "check" },
    { key: "dimensions", label: "维度命中", icon: "alert-triangle" },
    { key: "failure", label: "失败诊断", icon: "x" },
    { key: "time", label: "完成时长", icon: "clock" },
  ];
  if (data.value?.comic_quality) {
    base.push({ key: "comic", label: "漫创(次要)", icon: "book-open" });
  }
  return base;
});

const DIM_LABELS: Record<string, string> = {
  character_consistency: "角色一致",
  worldview: "世界观",
  tone: "基调",
  value_orientation: "价值取向",
  event_causality: "事件因果",
  relationship_network: "关系网",
  era_physics: "时代物理",
  detail_authenticity: "细节真实",
  body_register_alignment: "身体描写尺度",
  outline_execution: "剧情执行率",
};
const SEV_LABELS: Record<string, string> = {
  minor_drift: "轻微",
  obvious_drift: "明显",
  severe_breach: "严重",
};
function dimLabel(d: string): string { return DIM_LABELS[d] || d; }

function fmtSec(s: number): string {
  if (s < 60) return `${s.toFixed(0)} 秒`;
  if (s < 3600) return `${(s / 60).toFixed(1)} 分`;
  return `${(s / 3600).toFixed(1)} 小时`;
}

function maxBucket(buckets: { range: string; count: number }[]): number {
  return Math.max(1, ...buckets.map((b) => b.count));
}
</script>

<template>
  <section>
    <PageHero
      icon="bar-chart-3"
      title="质量观察"
      description="续作产物的实测质量分布 — 自洽评分直方图、正典守护者 9 维度命中、失败率与原因、完成时长。看清平台'生产的内容好不好',定位质量瓶颈。"
      audience="管理员视角"
    />

    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="data && !data.huimeng_attached" class="warn">
      huimeng.db 未连接,无法读主平台审计数据
    </div>

    <div v-if="data && data.huimeng_attached">
      <!-- sub-tab -->
      <TabBar v-model="activeTab" :tabs="tabs" />

      <!-- 1. 评分分布 -->
      <div v-if="activeTab === 'score'" class="tab-content">
        <div v-if="data.audit_score" class="score-card">
          <div class="num-grid">
            <div class="num-item">
              <div class="num-val">{{ data.audit_score.total }}</div>
              <div class="num-label">总审计次数</div>
            </div>
            <div class="num-item">
              <div class="num-val">{{ data.audit_score.average }}</div>
              <div class="num-label">平均分</div>
            </div>
            <div class="num-item">
              <div class="num-val">{{ data.audit_score.median }}</div>
              <div class="num-label">中位数</div>
            </div>
          </div>
          <div class="histogram">
            <div v-for="b in data.audit_score.buckets" :key="b.range" class="bar-row">
              <span class="bar-label">{{ b.range }} 分</span>
              <div class="bar-track">
                <div class="bar-fill" :style="{ width: `${(b.count / maxBucket(data.audit_score.buckets)) * 100}%` }" />
              </div>
              <span class="bar-count">{{ b.count }}</span>
            </div>
          </div>
        </div>
        <div v-else class="empty">暂无自洽审计数据</div>
      </div>

      <!-- 2. 维度命中 -->
      <div v-if="activeTab === 'dimensions'" class="tab-content">
        <table v-if="data.canonical_dimensions.length" class="dim-table">
          <thead>
            <tr>
              <th>维度</th>
              <th class="r">总命中</th>
              <th class="r">轻微</th>
              <th class="r">明显</th>
              <th class="r">严重</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="d in data.canonical_dimensions" :key="d.dimension">
              <td>{{ dimLabel(d.dimension) }}</td>
              <td class="r"><b>{{ d.total_issues }}</b></td>
              <td class="r muted">{{ d.by_severity.minor_drift ?? "—" }}</td>
              <td class="r warn-num">{{ d.by_severity.obvious_drift ?? "—" }}</td>
              <td class="r crit-num">{{ d.by_severity.severe_breach ?? "—" }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">暂无正典审计数据</div>
      </div>

      <!-- 3. 失败诊断 -->
      <div v-if="activeTab === 'failure'" class="tab-content">
        <div v-if="data.failure_rate" class="num-grid">
          <div class="num-item">
            <div class="num-val">{{ data.failure_rate.total }}</div>
            <div class="num-label">推演总数</div>
          </div>
          <div class="num-item">
            <div class="num-val crit">{{ data.failure_rate.failed }}</div>
            <div class="num-label">失败数</div>
          </div>
          <div class="num-item">
            <div class="num-val crit">{{ (data.failure_rate.rate * 100).toFixed(1) }}%</div>
            <div class="num-label">失败率</div>
          </div>
        </div>
        <div v-if="data.failure_reasons.length">
          <h4 class="sub-h">失败原因 top 5</h4>
          <table class="reason-table">
            <tr v-for="r in data.failure_reasons" :key="r.reason">
              <td>{{ r.reason }}</td>
              <td class="r"><b>{{ r.count }}</b></td>
            </tr>
          </table>
        </div>
        <div v-else-if="!data.failure_rate" class="empty">暂无失败诊断数据</div>
      </div>

      <!-- 4. 完成时长 -->
      <div v-if="activeTab === 'time'" class="tab-content">
        <div v-if="data.completion_time" class="num-grid">
          <div class="num-item">
            <div class="num-val">{{ fmtSec(data.completion_time.p50_sec) }}</div>
            <div class="num-label">P50 中位数</div>
          </div>
          <div class="num-item">
            <div class="num-val">{{ fmtSec(data.completion_time.p95_sec) }}</div>
            <div class="num-label">P95</div>
          </div>
          <div class="num-item">
            <div class="num-val">{{ fmtSec(data.completion_time.avg_sec) }}</div>
            <div class="num-label">平均</div>
          </div>
          <div class="num-item">
            <div class="num-val muted-num">{{ data.completion_time.samples }}</div>
            <div class="num-label">样本数</div>
          </div>
        </div>
        <div v-else class="empty">暂无完成时长数据</div>
      </div>

      <!-- 5. 漫创(次要) -->
      <div v-if="activeTab === 'comic' && data.comic_quality" class="tab-content secondary">
        <p class="note">{{ data.comic_quality.note }}</p>
        <div class="num-grid">
          <div class="num-item secondary-item">
            <div class="num-val">{{ data.comic_quality.total }}</div>
            <div class="num-label">总数</div>
          </div>
          <div class="num-item secondary-item">
            <div class="num-val">{{ data.comic_quality.done }}</div>
            <div class="num-label">完成</div>
          </div>
          <div class="num-item secondary-item">
            <div class="num-val">{{ data.comic_quality.failed }}</div>
            <div class="num-label">失败</div>
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

.tab-content {
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.5rem;
  padding: 1.25rem 1.5rem;
}
.tab-content.secondary { background: #fafafa; opacity: 0.85; }

.num-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8.75rem, 1fr));
  gap: 0.75rem;
  margin-bottom: 0.75rem;
}
.num-item {
  background: #f7f5f0;
  padding: 0.75rem 1rem;
  border-radius: 0.375rem;
}
.secondary-item { background: #ede9e0; }
.num-val { font-size: 1.5rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.num-val.crit { color: #dc2626; }
.num-val.muted-num { color: #9a968d; font-size: 1.25rem; }
.num-label { font-size: 0.6875rem; color: #6b6862; margin-top: 0.125rem; }

.histogram { display: flex; flex-direction: column; gap: 0.375rem; margin-top: 0.75rem; }
.bar-row { display: grid; grid-template-columns: 3.75rem 1fr 2.5rem; gap: 0.5rem; align-items: center; }
.bar-label { font-size: 0.75rem; color: #6b6862; font-variant-numeric: tabular-nums; }
.bar-track { background: #ede9e0; height: 1.125rem; border-radius: 0.1875rem; overflow: hidden; }
.bar-fill { background: #7c3aed; height: 100%; transition: width 200ms; }
.bar-count { font-size: 0.75rem; text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }

.dim-table, .reason-table { width: 100%; border-collapse: collapse; font-size: 0.8125rem; }
.dim-table th, .dim-table td, .reason-table td { padding: 0.5rem 0.75rem; border-bottom: 0.0625rem solid #f5f5f0; }
.dim-table th { font-weight: 600; color: #6b6862; font-size: 0.6875rem; text-transform: uppercase; letter-spacing: 0.03125rem; }
.dim-table td.r, .dim-table th.r, .reason-table td.r { text-align: right; font-variant-numeric: tabular-nums; }
.muted { color: #9a968d; }
.warn-num { color: #d97706; }
.crit-num { color: #dc2626; }

.sub-h { font-size: 0.75rem; font-weight: 600; color: #6b6862; margin: 1rem 0 0.5rem; text-transform: uppercase; letter-spacing: 0.03125rem; }
.note { font-size: 0.75rem; color: #9a968d; font-style: italic; margin-bottom: 0.75rem; }
</style>
