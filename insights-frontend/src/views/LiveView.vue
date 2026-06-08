<script setup lang="ts">
/**
 * LiveView — B8 实时事件流(INS-B Phase 1,2026-05-27 末⁵).
 *
 * 数据源:analytics.events + huimeng.simulations(进行中)
 * 自动 30 秒 refresh.漫创态进行中单列(用户偏好 5.0a).
 */
import { onMounted, onUnmounted, ref } from "vue";
import { fetchAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";
import { formatEventType, formatMode } from "../labels";

interface EventRow {
  id: number;
  user_id: string | null;
  event_type: string;
  mode: string | null;
  step: string | null;
  path: string | null;
  timestamp_ms: number;
  duration_ms: number | null;
  project_id: string | null;
}

interface SimRow {
  id: string;
  project_id: string;
  user_id: string;
  state: string;
  started_at: string | null;
  rounds_planned: number;
  current_round: number;
  target_chars: number;
}

interface ComicRow {
  id: string;
  user_id: string;
  name: string;
  state: string;
  progress_percent: number;
  created_at: string;
}

interface LiveResp {
  now_ms: number;
  huimeng_attached: boolean;
  online_count: number;
  recent_events: EventRow[];
  active_simulations: SimRow[];
  active_comics: ComicRow[];
}

const data = ref<LiveResp | null>(null);
const error = ref<string | null>(null);
const lastRefresh = ref<number>(0);
let timer: number | null = null;

async function load() {
  try {
    data.value = await fetchAdmin<LiveResp>("/admin/live");
    lastRefresh.value = Date.now();
    error.value = null;
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(() => {
  load();
  timer = window.setInterval(load, 30_000);
});

onUnmounted(() => {
  if (timer !== null) {
    clearInterval(timer);
  }
});

function fmtTime(ms: number): string {
  const d = new Date(ms);
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}
function fmtAgo(ms: number): string {
  if (!data.value) return "";
  const diff = data.value.now_ms - ms;
  if (diff < 60_000) return `${Math.floor(diff / 1000)} 秒前`;
  if (diff < 3600_000) return `${Math.floor(diff / 60_000)} 分前`;
  return `${Math.floor(diff / 3600_000)} 小时前`;
}
function fmtRefresh(): string {
  if (lastRefresh.value === 0) return "—";
  const diff = Math.floor((Date.now() - lastRefresh.value) / 1000);
  return `${diff} 秒前`;
}
</script>

<template>
  <section>
    <PageHero
      icon="activity"
      title="实时事件流"
      description="平台当前活动 — 在线人数、正在跑的推演、最近 50 条用户行为。30 秒自动刷新。看清现在谁在用、跑什么、有没有异常。"
      audience="运营值班视角"
    />

    <div v-if="error" class="err">{{ error }}</div>

    <div v-if="data">
      <!-- 顶部 -->
      <div class="num-grid">
        <div class="num-item accent">
          <div class="num-val-row">
            <Icon name="circle-dot" :size="20" />
            <span class="num-val">{{ data.online_count }}</span>
          </div>
          <div class="num-label">当前在线(15 分钟内)</div>
        </div>
        <div class="num-item">
          <div class="num-val">{{ data.active_simulations.length }}</div>
          <div class="num-label">进行中推演</div>
        </div>
        <div class="num-item secondary-item">
          <div class="num-val">{{ data.active_comics.length }}</div>
          <div class="num-label">进行中漫画(次要)</div>
        </div>
        <div class="num-item">
          <div class="num-val muted-num">{{ fmtRefresh() }}</div>
          <div class="num-label">数据更新</div>
        </div>
      </div>

      <!-- 进行中推演 -->
      <div class="block">
        <div class="block-head">
          <Icon name="play" :size="14" />
          <h3>进行中推演</h3>
        </div>
        <table v-if="data.active_simulations.length" class="data-table">
          <thead>
            <tr>
              <th>推演 ID</th>
              <th>用户</th>
              <th>状态</th>
              <th class="r">轮次</th>
              <th class="r">目标字数</th>
              <th>开始时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="s in data.active_simulations" :key="s.id">
              <td class="muted"><code>{{ s.id.slice(0, 10) }}…</code></td>
              <td>
                <router-link :to="`/user/${s.user_id}`" class="user-link">
                  {{ s.user_id.slice(0, 10) }}…
                </router-link>
              </td>
              <td>
                <span class="state-chip" :class="`state-${s.state}`">{{ s.state }}</span>
              </td>
              <td class="r">{{ s.current_round }}/{{ s.rounds_planned }}</td>
              <td class="r">{{ s.target_chars.toLocaleString() }}</td>
              <td class="muted">{{ s.started_at || "—" }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">当前无进行中推演</div>
      </div>

      <!-- 漫创态进行中(单列) -->
      <div v-if="data.active_comics.length" class="block secondary">
        <div class="block-head">
          <h3>进行中漫画(漫创态 · 次要功能)</h3>
        </div>
        <table class="data-table">
          <tr v-for="c in data.active_comics" :key="c.id">
            <td>{{ c.name }}</td>
            <td>
              <router-link :to="`/user/${c.user_id}`" class="user-link">
                {{ c.user_id.slice(0, 10) }}…
              </router-link>
            </td>
            <td><span class="state-chip">{{ c.state }}</span></td>
            <td class="r">{{ c.progress_percent }}%</td>
          </tr>
        </table>
      </div>

      <!-- 最近 50 条 events -->
      <div class="block">
        <div class="block-head">
          <Icon name="clock" :size="14" />
          <h3>最近 50 条行为</h3>
        </div>
        <table v-if="data.recent_events.length" class="data-table compact">
          <thead>
            <tr>
              <th>时间</th>
              <th>事件</th>
              <th>用户</th>
              <th>模式</th>
              <th>路径</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="ev in data.recent_events" :key="ev.id">
              <td class="muted">{{ fmtTime(ev.timestamp_ms) }}<br><span class="ago">{{ fmtAgo(ev.timestamp_ms) }}</span></td>
              <td>
                <span class="ev-chip">{{ formatEventType(ev.event_type) }}</span>
              </td>
              <td>
                <router-link
                  v-if="ev.user_id"
                  :to="`/user/${ev.user_id}`"
                  class="user-link"
                >
                  {{ ev.user_id.slice(0, 10) }}…
                </router-link>
                <span v-else class="muted">访客</span>
              </td>
              <td>{{ ev.mode ? formatMode(ev.mode) : "—" }}</td>
              <td class="muted path">{{ ev.path || "—" }}</td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">暂无事件</div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.err { color: #dc2626; padding: 0.75rem; background: #fee2e2; border-radius: 0.375rem; margin-bottom: 1rem; }
.empty { color: #9a968d; font-style: italic; padding: 1.5rem; text-align: center; font-size: 0.8125rem; }

.num-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(10rem, 1fr));
  gap: 0.75rem;
  margin-bottom: 1.25rem;
}
.num-item {
  background: white;
  border: 1px solid #e5e1d8;
  padding: 0.875rem 1rem;
  border-radius: 0.5rem;
}
.num-item.accent { background: #f3efff; border-color: #c4b5fd; color: #7c3aed; }
.num-item.secondary-item { background: #fafafa; opacity: 0.85; }
.num-val-row { display: flex; align-items: center; gap: 0.5rem; }
.num-val { font-size: 1.5rem; font-weight: 700; font-variant-numeric: tabular-nums; }
.num-val.muted-num { color: #9a968d; font-size: 1.125rem; }
.num-label { font-size: 0.6875rem; color: #6b6862; margin-top: 0.125rem; }

.block {
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.5rem;
  padding: 1.25rem 1.5rem;
  margin-bottom: 1rem;
}
.block.secondary { background: #fafafa; opacity: 0.85; }
.block-head { display: flex; align-items: center; gap: 0.5rem; color: #6b6862; margin-bottom: 0.75rem; }
.block-head h3 { font-size: 0.875rem; font-weight: 600; color: #1f1f1e; }

.data-table { width: 100%; border-collapse: collapse; font-size: 0.8125rem; }
.data-table.compact { font-size: 0.75rem; }
.data-table th, .data-table td {
  padding: 0.5rem 0.75rem;
  border-bottom: 1px solid #f5f5f0;
  text-align: left;
  vertical-align: middle;
}
.data-table.compact td { padding: 0.375rem 0.625rem; }
.data-table th { font-weight: 600; color: #6b6862; font-size: 0.6875rem; text-transform: uppercase; letter-spacing: 0.03125rem; }
.data-table td.r, .data-table th.r { text-align: right; font-variant-numeric: tabular-nums; }
.data-table code {
  background: #f5f5f0;
  padding: 0.125rem 0.375rem;
  border-radius: 0.1875rem;
  font-size: 0.6875rem;
}
.muted { color: #9a968d; }
.path { max-width: 12.5rem; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.ago { font-size: 0.625rem; color: #b8b3a8; }

.user-link {
  color: #7c3aed;
  text-decoration: none;
  font-family: monospace;
  font-size: 0.6875rem;
}
.user-link:hover { text-decoration: underline; }

.ev-chip {
  background: #f3efff;
  color: #5b21b6;
  padding: 0.125rem 0.5rem;
  border-radius: 0.1875rem;
  font-size: 0.6875rem;
}

.state-chip {
  font-size: 0.625rem;
  padding: 0.125rem 0.375rem;
  border-radius: 0.1875rem;
  background: #fef3c7;
  color: #d97706;
}
.state-chip.state-composing { background: #dbeafe; color: #1e40af; }
.state-chip.state-finalizing { background: #f3efff; color: #5b21b6; }
.state-chip.state-directing { background: #fce7f3; color: #be185d; }
</style>
