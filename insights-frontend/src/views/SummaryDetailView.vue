<script setup lang="ts">
/**
 * SummaryDetailView — 阶段汇总报告详情(INS-A9 整改,2026-05-27 末³).
 *
 * 用户拍板:PageHero 单一承担说明.
 * Emoji 清零(原 📈 ⚠️ 🔍 💡 ↑↓→ ✓ 7 处)→ Icon 组件.
 */
import { computed, onMounted, ref, watch } from "vue";
import { fetchAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";

const props = defineProps<{ id: string }>();

interface SummaryDetailResp {
  id: number;
  stage_number: number;
  period_start: string;
  period_end: string;
  daily_count: number;
  summary_md: string | null;
  insights: {
    trends?: { name: string; direction: string; detail: string }[];
    anomalies?: { date?: string; type?: string; detail: string }[];
    patterns?: { name?: string; detail: string }[];
    recommendations?: {
      title?: string;
      rationale?: string;
      specific_action_for_human?: string;
      priority?: "high" | "medium" | "low";
    }[];
    tags?: string[];
  };
  cost_yuan: number;
  tokens_input: number;
  tokens_output: number;
  created_at: string;
  read_at: string | null;
}

const data = ref<SummaryDetailResp | null>(null);
const error = ref<string | null>(null);
const marking = ref(false);

const adminToken = (import.meta.env.VITE_ADMIN_TOKEN as string | undefined)
  || "huimeng-insights-dev-token";
const insightsBase = (import.meta.env.VITE_INSIGHTS_BASE as string | undefined)
  || "http://localhost:8001";

async function load() {
  try {
    data.value = await fetchAdmin<SummaryDetailResp>(`/agent/summaries/${props.id}`);
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(load);
watch(() => props.id, load);

async function markRead() {
  if (!data.value || marking.value) return;
  marking.value = true;
  try {
    const resp = await fetch(
      `${insightsBase}/agent/summaries/${data.value.id}/mark-read`,
      {
        method: "POST",
        headers: { "X-Admin-Token": adminToken },
      },
    );
    if (resp.ok) {
      await load();
    } else {
      error.value = `mark-read 失败: ${resp.status}`;
    }
  } catch (e) {
    error.value = String(e);
  } finally {
    marking.value = false;
  }
}

function priorityClass(p?: string) {
  return `prio-${p || "medium"}`;
}

function directionIcon(direction: string): string {
  if (direction === "up") return "arrow-up";
  if (direction === "down") return "arrow-down";
  return "arrow-right";
}

const heroDescription = computed(() => {
  if (!data.value) {
    return "Level 2 agent 从一堆日报中浓缩的宏观洞察(趋势/异常/模式/改进建议),标已读防遗漏。";
  }
  return `${data.value.period_start} → ${data.value.period_end},聚合 ${data.value.daily_count} 份日报。读完决定是否改产品,标已读防遗漏。`;
});
</script>

<template>
  <section>
    <router-link to="/knowledge" class="back">
      <Icon name="arrow-left" :size="14" />
      <span>返回知识库</span>
    </router-link>

    <div v-if="error" class="err">{{ error }}</div>

    <div v-if="data">
      <PageHero
        icon="book-open"
        :title="`第 ${data.stage_number} 阶段汇总`"
        :description="heroDescription"
        :audience="data.read_at ? `已读 · ${new Date(data.read_at).toLocaleString('zh-CN')}` : '未读'"
      />

      <div class="meta-row">
        <span>成本 ¥ {{ data.cost_yuan }}({{ data.tokens_input + data.tokens_output }} tokens)</span>
        <button
          v-if="!data.read_at"
          class="mark-btn"
          :disabled="marking"
          @click="markRead"
        >
          <Icon name="check" :size="12" />
          <span>{{ marking ? "标记中…" : "标已读" }}</span>
        </button>
      </div>

      <div v-if="data.insights?.tags?.length" class="tags">
        <span v-for="t in data.insights.tags" :key="t" class="tag">#{{ t }}</span>
      </div>

      <!-- 主体 markdown -->
      <article class="report-body">
        <pre>{{ data.summary_md || "(空报告)" }}</pre>
      </article>

      <!-- 趋势 -->
      <div v-if="data.insights?.trends?.length" class="insights-block">
        <h3>
          <Icon name="trending-up" :size="16" />
          <span>趋势</span>
        </h3>
        <ul>
          <li v-for="(t, i) in data.insights.trends" :key="i">
            <span class="ins-name">{{ t.name }}</span>
            <span class="ins-dir" :class="`dir-${t.direction}`">
              <Icon :name="directionIcon(t.direction)" :size="12" />
            </span>
            <span class="ins-detail">{{ t.detail }}</span>
          </li>
        </ul>
      </div>

      <!-- 异常 -->
      <div v-if="data.insights?.anomalies?.length" class="insights-block">
        <h3>
          <Icon name="alert-triangle" :size="16" />
          <span>异常</span>
        </h3>
        <ul>
          <li v-for="(a, i) in data.insights.anomalies" :key="i">
            <span v-if="a.date" class="ins-name">{{ a.date }}</span>
            <span v-if="a.type" class="ins-type">{{ a.type }}</span>
            <span class="ins-detail">{{ a.detail }}</span>
          </li>
        </ul>
      </div>

      <!-- 模式 -->
      <div v-if="data.insights?.patterns?.length" class="insights-block">
        <h3>
          <Icon name="search" :size="16" />
          <span>用户行为模式</span>
        </h3>
        <ul>
          <li v-for="(p, i) in data.insights.patterns" :key="i">
            <span v-if="p.name" class="ins-name">{{ p.name }}</span>
            <span class="ins-detail">{{ p.detail }}</span>
          </li>
        </ul>
      </div>

      <!-- 改进建议 -->
      <div v-if="data.insights?.recommendations?.length" class="insights-block">
        <h3>
          <Icon name="lightbulb" :size="16" />
          <span>改进建议(给开发者)</span>
        </h3>
        <ol>
          <li
            v-for="(r, i) in data.insights.recommendations"
            :key="i"
            :class="priorityClass(r.priority)"
          >
            <div class="rec-title">
              <b>{{ r.title || `建议 ${i + 1}` }}</b>
              <span class="rec-prio">{{ r.priority || "medium" }}</span>
            </div>
            <p v-if="r.rationale" class="rec-rat"><b>依据:</b>{{ r.rationale }}</p>
            <p v-if="r.specific_action_for_human" class="rec-act">
              <b>具体动作:</b>{{ r.specific_action_for_human }}
            </p>
          </li>
        </ol>
      </div>
    </div>
  </section>
</template>

<style scoped>
.back {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  color: #6b6862;
  text-decoration: none;
  margin-bottom: 1rem;
}
.back:hover { color: #7c3aed; }

.meta-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 0.75rem;
  color: #6b6862;
  margin-bottom: 0.75rem;
}
.mark-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  font-size: 0.75rem;
  padding: 0.375rem 0.875rem;
  background: #7c3aed;
  color: white;
  border: 0;
  border-radius: 0.3125rem;
  cursor: default;
}
.mark-btn:disabled { opacity: 0.5; cursor: not-allowed; }

.tags {
  display: flex;
  gap: 0.375rem;
  flex-wrap: wrap;
  margin: 0.5rem 0 1rem;
}
.tag {
  background: #f3efff;
  color: #5b21b6;
  padding: 0.1875rem 0.625rem;
  border-radius: 62.4375rem;
  font-size: 0.6875rem;
}

.err { color: #dc2626; padding: 0.75rem; background: #fee2e2; border-radius: 0.375rem; }

.report-body {
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.625rem;
  padding: 1.5rem 1.75rem;
  margin: 1rem 0 1.5rem;
}
.report-body pre {
  margin: 0;
  white-space: pre-wrap;
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif;
  font-size: 0.875rem;
  line-height: 1.8;
  color: #1f1f1e;
}

.insights-block {
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.625rem;
  padding: 1rem 1.5rem;
  margin-bottom: 0.75rem;
}
.insights-block h3 {
  font-size: 0.875rem;
  font-weight: 600;
  margin: 0.25rem 0 0.75rem;
  color: #1f1f1e;
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.insights-block ul, .insights-block ol {
  margin: 0;
  padding-left: 1.25rem;
  font-size: 0.8125rem;
  line-height: 1.7;
}
.insights-block li { margin-bottom: 0.5rem; }
.ins-name {
  font-weight: 600;
  margin-right: 0.375rem;
}
.ins-type {
  background: #fef3c7;
  color: #b45309;
  font-size: 0.6875rem;
  padding: 0.0625rem 0.5rem;
  border-radius: 62.4375rem;
  margin-right: 0.375rem;
}
.ins-dir {
  display: inline-flex;
  align-items: center;
  margin-right: 0.375rem;
}
.dir-up { color: #16A34A; }
.dir-down { color: #DC2626; }
.dir-stable { color: #6b6862; }
.ins-detail { color: #1f1f1e; }

.rec-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}
.rec-prio {
  font-size: 0.625rem;
  padding: 0.125rem 0.5rem;
  border-radius: 62.4375rem;
  background: #ede9e0;
  color: #6b6862;
  text-transform: uppercase;
  font-weight: 600;
  letter-spacing: 0.03125rem;
}
.prio-high .rec-prio { background: #fee2e2; color: #DC2626; }
.prio-medium .rec-prio { background: #fef3c7; color: #d97706; }
.prio-low .rec-prio { background: #e0e7ff; color: #5b21b6; }
.rec-rat, .rec-act { margin: 0.25rem 0; font-size: 0.75rem; color: #6b6862; }
.rec-rat b, .rec-act b { color: #1f1f1e; }
</style>
