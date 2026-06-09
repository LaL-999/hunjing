<script setup lang="ts">
/**
 * ScreenplayAnalyticsView — 剧创态(第 5 态)使用洞察(2026-06-09 新增)。
 *
 * 后端:GET /admin/screenplay?days=30
 *
 * 4 个板块:
 *   1. 转化漏斗 — dashboard 卡 → 上传 → compose → optimize → 多模型对比
 *   2. 9 个新事件 KPI 卡片(各事件计数 + unique users)
 *   3. 多模型对比胜出 vendor 分布(行业洞察)
 *   4. Dashboard 各卡片点击分布 + Optimize scope/focus 偏好
 *   5. 桥接资产价值(用了多少父平台 SP-2/3/7,转化率)
 */
import { onMounted, ref, computed } from "vue";
import { fetchAdmin } from "../api";
import PageHero from "../components/PageHero.vue";
import Icon from "../components/Icon.vue";

interface FunnelStep {
  event_type: string;
  label: string;
  unique_users: number;
  total_count: number;
}

interface VendorWin {
  provider: string;
  wins: number;
}

interface BridgeStats {
  total_screenplays: number;
  with_bridge: number;
  avg_drivers_injections: number;
  avg_knowledge_injections: number;
  avg_polarity_injections: number;
}

interface ScreenplayAnalyticsResp {
  window_days: number;
  now_ms: number;
  events_by_type: Record<string, { count: number; unique_users: number }>;
  funnel: FunnelStep[];
  winner_distribution: VendorWin[];
  optimize_scope_distribution: Record<string, number>;
  optimize_focus_distribution: Record<string, number>;
  dashboard_card_clicks: Record<string, number>;
  bridge_stats: BridgeStats;
}

const data = ref<ScreenplayAnalyticsResp | null>(null);
const error = ref<string | null>(null);
const windowDays = ref(30);

const EVENT_LABELS: Record<string, string> = {
  screenplay_novel_upload: "上传小说",
  screenplay_compose_start: "触发剧本生成",
  screenplay_compose_done: "剧本生成完成",
  screenplay_optimize: "AI 优化重排",
  screenplay_characters_view: "查看角色档案",
  screenplay_episodes_plan: "分集规划",
  model_compare_start: "多模型对比 · 入口",
  model_compare_run: "多模型对比 · 运行",
  model_compare_winner: "多模型对比 · 胜出",
};

const CARD_LABELS: Record<string, string> = {
  initial: "初始态",
  middle: "中间态",
  end: "末尾态",
  cycle: "漫创态",
  screenplay: "剧创态",
};

const SCOPE_LABELS: Record<string, string> = {
  full_screenplay: "全篇优化",
  single_scene: "单场优化",
};

const FOCUS_LABELS: Record<string, string> = {
  fidelity_and_structure: "保真度 + 结构",
  fidelity_only: "仅保真度",
  structure_only: "仅结构",
};

async function load() {
  error.value = null;
  try {
    data.value = await fetchAdmin<ScreenplayAnalyticsResp>(
      "/admin/screenplay",
      { days: windowDays.value },
    );
  } catch (e) {
    error.value = String(e);
  }
}

onMounted(load);

// 桥接率
const bridgeRate = computed(() => {
  const s = data.value?.bridge_stats;
  if (!s || s.total_screenplays === 0) return 0;
  return Math.round((s.with_bridge / s.total_screenplays) * 100);
});

// 漏斗 — 上一步留存率(给次步用)
function stepRate(step: FunnelStep, idx: number, funnel: FunnelStep[]): number {
  if (idx === 0) return 100;
  const prev = funnel[idx - 1].unique_users;
  if (prev === 0) return 0;
  return Math.round((step.unique_users / prev) * 100);
}

function changeWindow(days: number) {
  windowDays.value = days;
  void load();
}
</script>

<template>
  <section>
    <PageHero
      icon="film"
      title="剧创态洞察"
      description="第 5 创作态(小说 → AI 剧本)上线后的使用率 / 转化率 / 桥接资产价值。多模型对比胜出 vendor 分布是 BYOK 战略最核心信号。"
      audience="管理员视角"
    />

    <!-- 时间窗口切换 -->
    <div class="window-switcher">
      <button
        v-for="d in [7, 30, 90]"
        :key="d"
        :class="{ active: windowDays === d }"
        @click="changeWindow(d)"
      >
        近 {{ d }} 天
      </button>
    </div>

    <div v-if="error" class="err">{{ error }}</div>

    <div v-if="data">
      <!-- ① 转化漏斗 -->
      <section class="block">
        <h3 class="block-title">
          <Icon name="funnel" :size="14" /> 转化漏斗
        </h3>
        <div class="funnel">
          <div
            v-for="(step, idx) in data.funnel"
            :key="step.event_type"
            class="funnel-row"
          >
            <span class="funnel-step">{{ idx + 1 }}</span>
            <span class="funnel-label">{{ step.label }}</span>
            <div class="funnel-bar-wrap">
              <div
                class="funnel-bar"
                :style="`width: ${data.funnel[0].unique_users ?
                  Math.round(step.unique_users / data.funnel[0].unique_users * 100) : 0}%`"
              ></div>
            </div>
            <span class="funnel-count">{{ step.unique_users }} 用户</span>
            <span class="funnel-rate">
              {{ stepRate(step, idx, data.funnel) }}%
              <span v-if="idx > 0" class="rate-from">↓ 上步</span>
            </span>
          </div>
        </div>
      </section>

      <!-- ② 9 事件 KPI -->
      <section class="block">
        <h3 class="block-title">
          <Icon name="bar-chart-3" :size="14" /> 各事件计数(近 {{ data.window_days }} 天)
        </h3>
        <div class="kpi-grid">
          <div
            v-for="(stats, et) in data.events_by_type"
            :key="et"
            class="kpi-card"
          >
            <div class="kpi-label">{{ EVENT_LABELS[et] ?? et }}</div>
            <div class="kpi-value">{{ stats.count }}</div>
            <div class="kpi-sub">{{ stats.unique_users }} 用户</div>
          </div>
        </div>
      </section>

      <!-- ③ 多模型对比胜出 vendor -->
      <section v-if="data.winner_distribution.length > 0" class="block">
        <h3 class="block-title">
          <Icon name="trending-up" :size="14" /> 多模型对比胜出 vendor 分布
        </h3>
        <p class="block-desc">
          BYOK 战略洞察 — 用户实测哪家 LLM 写剧本更强(可解释 4 维评分推荐)
        </p>
        <ul class="vendor-list">
          <li
            v-for="(v, idx) in data.winner_distribution"
            :key="v.provider"
            class="vendor-row"
          >
            <span class="vendor-rank">#{{ idx + 1 }}</span>
            <span class="vendor-name">{{ v.provider }}</span>
            <div class="vendor-bar-wrap">
              <div
                class="vendor-bar"
                :style="`width: ${Math.round(v.wins / data.winner_distribution[0].wins * 100)}%`"
              ></div>
            </div>
            <span class="vendor-wins">{{ v.wins }} 胜</span>
          </li>
        </ul>
      </section>

      <!-- ④ Dashboard 各卡片点击分布 + Optimize 偏好 -->
      <section class="block split-2">
        <div>
          <h3 class="block-title">
            <Icon name="layout-dashboard" :size="14" /> Dashboard 各卡点击
          </h3>
          <ul class="bar-list">
            <li
              v-for="(c, card) in data.dashboard_card_clicks"
              :key="card"
              class="bar-row"
            >
              <span class="bar-label">{{ CARD_LABELS[card] ?? card }}</span>
              <span class="bar-count">{{ c }}</span>
            </li>
            <li v-if="Object.keys(data.dashboard_card_clicks).length === 0" class="empty">
              暂无数据
            </li>
          </ul>
        </div>
        <div>
          <h3 class="block-title">
            <Icon name="settings" :size="14" /> Optimize 偏好
          </h3>
          <p class="micro">Scope</p>
          <ul class="bar-list">
            <li
              v-for="(c, scope) in data.optimize_scope_distribution"
              :key="`s-${scope}`"
              class="bar-row"
            >
              <span class="bar-label">{{ SCOPE_LABELS[scope] ?? scope }}</span>
              <span class="bar-count">{{ c }}</span>
            </li>
          </ul>
          <p class="micro">Focus</p>
          <ul class="bar-list">
            <li
              v-for="(c, focus) in data.optimize_focus_distribution"
              :key="`f-${focus}`"
              class="bar-row"
            >
              <span class="bar-label">{{ FOCUS_LABELS[focus] ?? focus }}</span>
              <span class="bar-count">{{ c }}</span>
            </li>
          </ul>
        </div>
      </section>

      <!-- ⑤ 桥接资产价值 -->
      <section class="block">
        <h3 class="block-title">
          <Icon name="link" :size="14" /> 浑晶桥接资产价值
        </h3>
        <p class="block-desc">
          剧创态接通父平台 SP-2/3/7 资产(角色驱动力 / 知识边界 / 关系正负极)的使用率
        </p>
        <div class="kpi-grid">
          <div class="kpi-card">
            <div class="kpi-label">总剧本数</div>
            <div class="kpi-value">{{ data.bridge_stats.total_screenplays }}</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">绑定项目 + 桥接率</div>
            <div class="kpi-value">{{ bridgeRate }}%</div>
            <div class="kpi-sub">
              {{ data.bridge_stats.with_bridge }} / {{ data.bridge_stats.total_screenplays }}
            </div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">平均 SP-2 注入</div>
            <div class="kpi-value">{{ data.bridge_stats.avg_drivers_injections }}</div>
            <div class="kpi-sub">每剧本</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">平均 SP-3 注入</div>
            <div class="kpi-value">{{ data.bridge_stats.avg_knowledge_injections }}</div>
            <div class="kpi-sub">每剧本</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">平均 SP-7 注入</div>
            <div class="kpi-value">{{ data.bridge_stats.avg_polarity_injections }}</div>
            <div class="kpi-sub">每剧本</div>
          </div>
        </div>
      </section>
    </div>
  </section>
</template>

<style scoped>
.err {
  color: #c0392b;
  background: #fdecec;
  padding: 10px 14px;
  border-radius: 6px;
  margin-bottom: 14px;
}

.window-switcher {
  display: flex;
  gap: 6px;
  margin-bottom: 18px;
}
.window-switcher button {
  padding: 4px 12px;
  background: #f4f4f5;
  border: 1px solid #e4e4e7;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
}
.window-switcher button.active {
  background: #7c3aed;
  color: white;
  border-color: #7c3aed;
}

.block {
  margin-bottom: 28px;
  padding: 16px 18px;
  background: white;
  border: 1px solid #e4e4e7;
  border-radius: 8px;
}
.block-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0 0 4px;
  font-size: 14px;
  font-weight: 600;
  color: #1f2937;
}
.block-desc {
  margin: 0 0 12px;
  font-size: 12px;
  color: #6b7280;
}
.split-2 {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}
.split-2 > div {
  min-width: 0;
}

/* 漏斗 */
.funnel {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.funnel-row {
  display: grid;
  grid-template-columns: 24px 1fr 2fr 80px 110px;
  align-items: center;
  gap: 10px;
  font-size: 13px;
}
.funnel-step {
  color: #9ca3af;
  text-align: center;
  font-family: ui-monospace, monospace;
  font-size: 11px;
}
.funnel-label {
  color: #1f2937;
}
.funnel-bar-wrap {
  height: 10px;
  background: #f4f4f5;
  border-radius: 5px;
  overflow: hidden;
}
.funnel-bar {
  height: 100%;
  background: linear-gradient(90deg, #7c3aed, #a78bfa);
  border-radius: 5px;
  transition: width 600ms ease;
}
.funnel-count {
  color: #4b5563;
  font-family: ui-monospace, monospace;
  font-size: 12px;
  text-align: right;
}
.funnel-rate {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: #5b21b6;
}
.rate-from {
  font-size: 10px;
  color: #9ca3af;
  font-weight: 400;
}

/* KPI 卡片 */
.kpi-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(160px, 1fr));
  gap: 10px;
}
.kpi-card {
  padding: 12px 14px;
  background: #fafafa;
  border: 1px solid #e4e4e7;
  border-radius: 6px;
}
.kpi-label {
  font-size: 11.5px;
  color: #6b7280;
  margin-bottom: 4px;
}
.kpi-value {
  font-size: 20px;
  font-weight: 600;
  color: #1f2937;
  font-family: ui-monospace, monospace;
}
.kpi-sub {
  font-size: 10.5px;
  color: #9ca3af;
  margin-top: 2px;
}

/* Vendor 排行 */
.vendor-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.vendor-row {
  display: grid;
  grid-template-columns: 32px 160px 1fr 70px;
  align-items: center;
  gap: 10px;
  padding: 6px 0;
  font-size: 13px;
}
.vendor-rank {
  color: #9ca3af;
  font-family: ui-monospace, monospace;
  font-weight: 600;
  text-align: center;
}
.vendor-name {
  color: #1f2937;
}
.vendor-bar-wrap {
  height: 8px;
  background: #f4f4f5;
  border-radius: 4px;
  overflow: hidden;
}
.vendor-bar {
  height: 100%;
  background: linear-gradient(90deg, #7c3aed, #a78bfa);
  border-radius: 4px;
}
.vendor-wins {
  font-family: ui-monospace, monospace;
  font-size: 12px;
  font-weight: 600;
  color: #5b21b6;
  text-align: right;
}

/* Bar 列表(dashboard / optimize 子板)*/
.bar-list {
  list-style: none;
  margin: 0 0 12px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 5px;
}
.bar-row {
  display: flex;
  justify-content: space-between;
  font-size: 12.5px;
  padding: 4px 8px;
  background: #fafafa;
  border-radius: 4px;
}
.bar-label {
  color: #4b5563;
}
.bar-count {
  font-family: ui-monospace, monospace;
  color: #5b21b6;
  font-weight: 600;
}
.empty {
  font-size: 12px;
  color: #9ca3af;
  font-style: italic;
}
.micro {
  margin: 8px 0 4px;
  font-size: 11px;
  color: #6b7280;
  text-transform: uppercase;
  letter-spacing: 0.05em;
}
</style>
