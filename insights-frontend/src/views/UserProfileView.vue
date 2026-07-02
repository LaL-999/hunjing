<script setup lang="ts">
/**
 * UserProfileView — 单用户画像(INS-A9 + INS-B5 加厚,2026-05-27 末⁵²).
 *
 * INS-A9:PageHero / labels.ts 翻译 / AI 调用细分 start/done/failed.
 * INS-B5:加 account / balance / 累计消费 ¥ / works / uploads / violation_count / 评分时间线.
 *        漫创态作品 / 模式分布单独标灰(5.0a).
 */
import { computed, onMounted, ref, watch } from "vue";
import { fetchAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";
import { formatEventType, formatMode, formatStep } from "../labels";

const props = defineProps<{ id: string }>();

interface Work {
  id: string;
  name: string;
  mode: string;
  type: string;
  created_at: string;
  sim_count: number;
}

interface Upload {
  id: string;
  filename: string;
  state: string;
  parsed_text_chars: number | null;
  size_bytes: number;
}

interface UserProfileResp {
  user_id: string;
  events: {
    id: number;
    event_type: string;
    timestamp_ms: number;
    mode: string | null;
    step: string | null;
    path: string | null;
    duration_ms: number | null;
  }[];
  ai_call_stats: Record<string, number>;
  mode_distribution: Record<string, number>;
  projects_count: number | null;
  simulations_count: number | null;
  // INS-B5 加厚
  account: {
    email: string;
    plan: string;
    register_ip: string | null;
    register_ua: string | null;
    created_at: string;
  } | null;
  balance: {
    subscription_credits: number;
    addon_credits: number;
    byok_active?: boolean;
  } | null;
  total_consumed_yuan: number | null;
  works: Work[];
  uploads: Upload[];
  violation_count: number | null;
  audit_scores_timeline: { score: number; at: string }[];
}

const data = ref<UserProfileResp | null>(null);
const error = ref<string | null>(null);

async function load() {
  try {
    data.value = await fetchAdmin<UserProfileResp>(`/admin/user/${props.id}`);
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(load);
watch(() => props.id, load);

function fmtTs(ms: number): string {
  return new Date(ms).toLocaleString("zh-CN");
}
function fmtBytes(n: number): string {
  if (n >= 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  if (n >= 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${n} B`;
}

const aiStats = computed(() => {
  if (!data.value) return { start: 0, done: 0, failed: 0 };
  const s = data.value.ai_call_stats || {};
  return {
    start: s.ai_call_start ?? 0,
    done: s.ai_call_done ?? 0,
    failed: (s.ai_call_failed ?? 0) + (s.ai_call_error ?? 0),
  };
});

const PLAN_LABELS: Record<string, string> = {
  free: "Free 免费",
  pro: "Pro",
  max: "Max",
  super_max: "SuperMax",
  founder: "创始人",
};
function planLabel(p: string): string {
  return PLAN_LABELS[p] || p;
}
</script>

<template>
  <section>
    <PageHero
      icon="user"
      title="用户画像"
      :description="`单个用户的全维度画像 — 账户/余额/累计消费/作品/上传/红旗/评分时间线/最近行为。审查可疑用户或重度用户的完整使用路径。`"
      :audience="`管理员视角 · user_id ${id}`"
      back-to="/users"
    />

    <div v-if="error" class="err">{{ error }}</div>

    <div v-if="data">
      <!-- INS-B5 账户 -->
      <div v-if="data.account" class="block">
        <div class="block-head"><Icon name="user" :size="14" /><h3>账户</h3></div>
        <div class="account-grid">
          <div><span class="kvl">邮箱</span><b>{{ data.account.email }}</b></div>
          <div>
            <span class="kvl">订阅档位</span>
            <span class="plan-chip">{{ planLabel(data.account.plan) }}</span>
          </div>
          <div><span class="kvl">注册时间</span><b>{{ data.account.created_at }}</b></div>
          <div><span class="kvl">注册 IP</span><b class="muted">{{ data.account.register_ip || "—" }}</b></div>
        </div>
      </div>

      <!-- INS-B5 余额 + 累计消费 -->
      <div v-if="data.balance" class="block">
        <div class="block-head"><Icon name="database" :size="14" /><h3>余额 · 消费</h3></div>
        <div class="num-grid">
          <div class="num-item">
            <div class="num-val">{{ data.balance.subscription_credits }}</div>
            <div class="num-label">订阅 credits</div>
          </div>
          <div class="num-item">
            <div class="num-val">{{ data.balance.addon_credits }}</div>
            <div class="num-label">加购 credits</div>
          </div>
          <div class="num-item">
            <div class="num-val">{{ data.balance.byok_active ? "是" : "否" }}</div>
            <div class="num-label">自携密钥激活</div>
          </div>
          <div class="num-item accent">
            <div class="num-val">{{ data.total_consumed_yuan === null ? "—" : `¥${data.total_consumed_yuan.toFixed(2)}` }}</div>
            <div class="num-label">累计 LLM 真实成本</div>
          </div>
        </div>
      </div>

      <!-- INS-B5 风险信号 -->
      <div v-if="data.violation_count !== null && data.violation_count > 0" class="block warn-block">
        <div class="block-head"><Icon name="alert-triangle" :size="14" /><h3>风险信号</h3></div>
        <div class="warn-text">
          红旗词典累计命中 <b>{{ data.violation_count }}</b> 次
          <router-link to="/safety" class="link-btn">查看合规风控</router-link>
        </div>
      </div>

      <!-- 原:量级统计 -->
      <div class="block">
        <div class="block-head"><Icon name="bar-chart-3" :size="14" /><h3>创作量级</h3></div>
        <div class="num-grid">
          <div class="num-item">
            <div class="num-val">{{ data.projects_count ?? "—" }}</div>
            <div class="num-label">项目数</div>
          </div>
          <div class="num-item">
            <div class="num-val">{{ data.simulations_count ?? "—" }}</div>
            <div class="num-label">推演数</div>
          </div>
          <div class="num-item">
            <div class="num-val">{{ aiStats.done }}</div>
            <div class="num-label">AI 完成 · 发起 {{ aiStats.start }} · 失败 {{ aiStats.failed }}</div>
          </div>
        </div>
      </div>

      <!-- INS-B5 作品列表 -->
      <div v-if="data.works.length" class="block">
        <div class="block-head"><Icon name="book-open" :size="14" /><h3>作品列表</h3></div>
        <table class="data-table">
          <thead>
            <tr>
              <th>项目名</th>
              <th>创作态</th>
              <th>类型</th>
              <th>创建时间</th>
              <th class="r">推演数</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="w in data.works" :key="w.id" :class="{ secondary: w.mode === 'cycle' }">
              <td>{{ w.name }}</td>
              <td>
                <span class="mode-chip" :class="{ secondary: w.mode === 'cycle' }">
                  {{ formatMode(w.mode) }}
                </span>
              </td>
              <td class="muted">{{ w.type }}</td>
              <td class="muted">{{ w.created_at }}</td>
              <td class="r"><b>{{ w.sim_count }}</b></td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- INS-B5 上传 -->
      <div v-if="data.uploads.length" class="block">
        <div class="block-head"><Icon name="database" :size="14" /><h3>上传记录</h3></div>
        <table class="data-table">
          <thead>
            <tr>
              <th>文件名</th>
              <th>状态</th>
              <th class="r">字数</th>
              <th class="r">大小</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="u in data.uploads" :key="u.id">
              <td>{{ u.filename }}</td>
              <td><span class="state-chip" :class="`state-${u.state}`">{{ u.state }}</span></td>
              <td class="r">{{ u.parsed_text_chars?.toLocaleString() ?? "—" }}</td>
              <td class="r muted">{{ fmtBytes(u.size_bytes) }}</td>
            </tr>
          </tbody>
        </table>
      </div>

      <!-- INS-B5 评分时间线 -->
      <div v-if="data.audit_scores_timeline.length" class="block">
        <div class="block-head"><Icon name="trending-up" :size="14" /><h3>自洽审计评分时间线(最近 10 次)</h3></div>
        <div class="timeline">
          <div
            v-for="(t, i) in [...data.audit_scores_timeline].reverse()"
            :key="i"
            class="tl-bar"
            :title="`${t.score} 分 @ ${t.at}`"
          >
            <div class="tl-bar-fill" :style="{ height: `${t.score}%` }">
              <span class="tl-score">{{ t.score }}</span>
            </div>
          </div>
        </div>
      </div>

      <!-- 4 态分布(漫创单列) -->
      <div class="block">
        <div class="block-head"><Icon name="layout-dashboard" :size="14" /><h3>创作态分布</h3></div>
        <ul class="mode-list">
          <li
            v-for="(count, mode) in data.mode_distribution"
            :key="mode"
            :class="{ secondary: mode === 'cycle' }"
          >
            <span class="mode-name">
              {{ formatMode(mode) }}
              <span v-if="mode === 'cycle'" class="muted">(次要)</span>
            </span>
            <span class="mode-count">{{ count }}</span>
          </li>
        </ul>
      </div>

      <!-- 最近行为 -->
      <div class="block">
        <div class="block-head"><Icon name="clock" :size="14" /><h3>最近 100 条行为</h3></div>
        <table class="events">
          <thead>
            <tr>
              <th>时间</th>
              <th>事件</th>
              <th>页面路径</th>
              <th>模式 / 步骤</th>
              <th class="r">时长</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ev in data.events" :key="ev.id">
              <td>{{ fmtTs(ev.timestamp_ms) }}</td>
              <td>
                <span class="event-label">{{ formatEventType(ev.event_type) }}</span>
                <code class="event-raw">{{ ev.event_type }}</code>
              </td>
              <td class="muted path">{{ ev.path ?? "—" }}</td>
              <td>
                <span v-if="ev.mode">{{ formatMode(ev.mode) }}</span>
                <span v-if="ev.mode && ev.step"> / </span>
                <span v-if="ev.step">{{ formatStep(ev.step) }}</span>
                <span v-if="!ev.mode && !ev.step" class="muted">—</span>
              </td>
              <td class="r">{{ ev.duration_ms !== null ? `${ev.duration_ms} ms` : "—" }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<style scoped>
.err { color: #dc2626; padding: 0.75rem; background: #fee2e2; border-radius: 0.375rem; }

.block {
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.625rem;
  padding: 1.125rem 1.375rem;
  margin-bottom: 0.875rem;
}
.block.warn-block { background: #fef3c7; border-color: #fcd34d; }
.block-head { display: flex; align-items: center; gap: 0.5rem; color: #6b6862; margin-bottom: 0.75rem; }
.block-head h3 { font-size: 0.875rem; font-weight: 600; color: #1f1f1e; }

.account-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 0.75rem;
  font-size: 0.8125rem;
}
.kvl {
  color: #6b6862;
  font-size: 0.6875rem;
  display: block;
  margin-bottom: 0.125rem;
}
.plan-chip {
  background: #f3efff;
  color: #5b21b6;
  padding: 0.125rem 0.625rem;
  border-radius: 0.1875rem;
  font-size: 0.75rem;
  font-weight: 600;
}

.num-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(8.75rem, 1fr));
  gap: 0.75rem;
}
.num-item {
  background: #f7f5f0;
  padding: 0.75rem 1rem;
  border-radius: 0.375rem;
}
.num-item.accent { background: #f3efff; }
.num-val { font-size: 1.375rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.num-label { font-size: 0.6875rem; color: #6b6862; margin-top: 0.125rem; }

.warn-text {
  font-size: 0.8125rem;
  color: #92400e;
  display: flex;
  align-items: center;
  gap: 0.75rem;
}
.warn-text b { color: #dc2626; font-size: 1.125rem; }

.data-table { width: 100%; border-collapse: collapse; font-size: 0.8125rem; }
.data-table th, .data-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #f0ece3;
  text-align: left;
}
.data-table th { font-weight: 600; color: #6b6862; font-size: 0.6875rem; text-transform: uppercase; letter-spacing: 0.03125rem; }
.data-table td.r, .data-table th.r { text-align: right; font-variant-numeric: tabular-nums; }
.data-table tr.secondary { opacity: 0.6; }
.muted { color: #9a968d; }
.path { max-width: 13.75rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }

.mode-chip {
  background: #f3efff;
  color: #5b21b6;
  padding: 0.125rem 0.5rem;
  border-radius: 0.1875rem;
  font-size: 0.6875rem;
}
.mode-chip.secondary { background: #ede9e0; color: #9a968d; }

.state-chip {
  font-size: 0.6875rem;
  padding: 0.125rem 0.375rem;
  border-radius: 0.1875rem;
  background: #ede9e0;
  color: #6b6862;
}
.state-chip.state-ready { background: #d1fae5; color: #065f46; }
.state-chip.state-failed,
.state-chip.state-rejected { background: #fee2e2; color: #dc2626; }
.state-chip.state-extracting,
.state-chip.state-uploaded { background: #fef3c7; color: #d97706; }

.mode-list { list-style: none; margin: 0; padding: 0; font-size: 0.8125rem; }
.mode-list li {
  display: flex;
  justify-content: space-between;
  padding: 0.5rem 0;
  border-bottom: 1px solid #f0ece3;
}
.mode-list li:last-child { border-bottom: 0; }
.mode-list li.secondary { opacity: 0.6; }
.mode-name { color: #1f1f1e; }
.mode-count {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: #7c3aed;
}

.timeline {
  display: flex;
  align-items: flex-end;
  gap: 0.375rem;
  height: 7.5rem;
  padding: 0.5rem 0;
}
.tl-bar {
  flex: 1;
  height: 100%;
  background: #f7f5f0;
  border-radius: 0.1875rem;
  position: relative;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
.tl-bar-fill {
  width: 100%;
  background: linear-gradient(180deg, #a78bfa 0%, #7c3aed 100%);
  border-radius: 0.1875rem;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding-top: 0.25rem;
  min-height: 10%;
}
.tl-score {
  font-size: 0.625rem;
  color: white;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.link-btn {
  font-size: 0.6875rem;
  color: #7c3aed;
  text-decoration: none;
  padding: 0.125rem 0.625rem;
  border: 1px solid #c4b5fd;
  border-radius: 0.1875rem;
  margin-left: auto;
}
.link-btn:hover { background: #f3efff; }

.events {
  border-collapse: collapse;
  width: 100%;
  font-size: 0.75rem;
  background: white;
}
.events thead { background: #f7f5f0; }
.events th, .events td {
  padding: 0.5rem 0.75rem;
  text-align: left;
  border-bottom: 1px solid #f0ece3;
}
.events th { font-weight: 600; color: #6b6862; }
.events td.r, .events th.r {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.event-label { font-weight: 500; }
.event-raw {
  display: inline-block;
  margin-left: 0.375rem;
  background: #f7f5f0;
  padding: 0.0625rem 0.375rem;
  border-radius: 0.1875rem;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 0.625rem;
  color: #9a968d;
}
</style>
