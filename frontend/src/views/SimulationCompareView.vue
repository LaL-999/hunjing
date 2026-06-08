<script setup lang="ts">
/**
 * SimulationCompareView — SP-9(2026-05-29)反事实分支并排对比.
 *
 * URL: /projects/:id/compare?a=simA&b=simB
 *
 * 产品目的:
 *   用户在同一项目下跑了多个 sim(不同反事实变量配置),很难肉眼看出
 *   "改了角色 X 性格后,第 5 幕变化在哪".此 view 把两个 sim:
 *     1. metadata 并列展示(divergence / reshape_percent / state)
 *     2. 反事实变量 diff(仅 A / 共享 / 仅 B 三栏)
 *     3. 按 scene_index 对齐 simulation_scenes,左右双 column 显幕级 narrative
 *     4. 每幕标"相同 / 差异 / 仅 A / 仅 B" chip
 *
 * 数据源:GET /api/simulations/{a}/compare/{b}
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api, ApiError } from "../api/client";
import type {
  CompareAnalysisReport,
  CompareCounterfactualEntry,
  CompareSceneAlignmentItem,
  SimulationCompareResponse,
  SimulationState,
} from "../api/types";
import Icon from "../components/Icon.vue";

const props = defineProps<{
  id: string; // project_id
}>();

const route = useRoute();
const router = useRouter();

const simAId = computed(() => String(route.query.a ?? ""));
const simBId = computed(() => String(route.query.b ?? ""));

const data = ref<SimulationCompareResponse | null>(null);
const loading = ref(false);
const errorMessage = ref<string | null>(null);

async function load() {
  if (!simAId.value || !simBId.value) {
    errorMessage.value = "缺少推演 id 参数(query.a / query.b)";
    return;
  }
  if (simAId.value === simBId.value) {
    errorMessage.value = "不能与自己对比,请选择两个不同推演";
    return;
  }
  loading.value = true;
  errorMessage.value = null;
  try {
    const resp = await api.get<SimulationCompareResponse>(
      `/simulations/${simAId.value}/compare/${simBId.value}`,
    );
    data.value = resp;
  } catch (e) {
    errorMessage.value = e instanceof ApiError ? e.message : "加载对比数据失败";
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch([simAId, simBId], load);

function goBack() {
  if (window.history.length > 1) {
    router.back();
  } else {
    router.push(`/projects/${props.id}`);
  }
}

function stateLabel(s: SimulationState | string): string {
  const m: Record<string, string> = {
    queued: "排队中",
    directing: "导演中",
    summoning: "召唤角色",
    agent_loop: "agent 推演",
    narrating: "narrator 合稿",
    done: "已完成",
    failed: "失败",
    cancelled: "已取消",
  };
  return m[s] ?? s;
}

function diffLabel(item: CompareSceneAlignmentItem): string {
  if (item.diff_kind === "only_a") return "仅分支 A";
  if (item.diff_kind === "only_b") return "仅分支 B";
  return item.similar ? "几乎相同" : "明显差异";
}

function diffClass(item: CompareSceneAlignmentItem): string {
  if (item.diff_kind === "only_a") return "scene-row--only-a";
  if (item.diff_kind === "only_b") return "scene-row--only-b";
  return item.similar ? "scene-row--similar" : "scene-row--differ";
}

function truncate(text: string, max: number): string {
  if (text.length <= max) return text;
  return text.slice(0, max) + "…";
}

function cfTargetLabel(cf: CompareCounterfactualEntry): string {
  const typeName: Record<string, string> = {
    character: "角色",
    event: "事件",
    relationship: "关系",
    world: "世界观",
  };
  const tn = typeName[cf.target_type] ?? cf.target_type;
  return cf.target_name ? `${tn} · ${cf.target_name}` : tn;
}

function formatValue(v: string | null): string {
  if (v === null || v === undefined) return "（空）";
  if (v.length > 80) return v.slice(0, 80) + "…";
  return v;
}

function gotoSim(simId: string) {
  router.push(`/simulations/${simId}`);
}

// ============================================================
// SP-9 enhancement(2026-05-29 末)— AI 因果分析
// ============================================================
const analysis = ref<CompareAnalysisReport | null>(null);
const analyzing = ref(false);
const analysisError = ref<string | null>(null);

async function analyzeCompare() {
  if (analyzing.value || !data.value) return;
  analyzing.value = true;
  analysisError.value = null;
  try {
    const resp = await api.post<CompareAnalysisReport>(
      `/simulations/${simAId.value}/compare/${simBId.value}/analyze`,
      {},
    );
    analysis.value = resp;
  } catch (e) {
    analysisError.value = e instanceof ApiError ? e.message : "AI 分析失败";
  } finally {
    analyzing.value = false;
  }
}

// 给 cf_impact_chains / turning_points 解析 cf_id 到具体反事实(以便前端高亮链接)
function cfById(cfId: string): CompareCounterfactualEntry | null {
  if (!data.value || !cfId) return null;
  const all = [
    ...data.value.counterfactual_diff.only_in_a,
    ...data.value.counterfactual_diff.common,
    ...data.value.counterfactual_diff.only_in_b,
  ];
  return all.find((c) => c.id === cfId) ?? null;
}

function cfLabelFromId(cfId: string): string {
  const cf = cfById(cfId);
  if (!cf) return cfId.slice(0, 8);
  const typeName: Record<string, string> = {
    character: "角色",
    event: "事件",
    relationship: "关系",
    world: "世界观",
  };
  const tn = typeName[cf.target_type] ?? cf.target_type;
  return cf.target_name ? `${tn}·${cf.target_name}·${cf.field}` : `${tn}·${cf.field}`;
}

function sideLabel(side: string): string {
  if (side === "only_in_a") return "仅 A";
  if (side === "only_in_b") return "仅 B";
  return "共享";
}
</script>

<template>
  <div class="compare-page">
    <header class="page-hero">
      <button class="back-btn" @click="goBack">
        <Icon name="arrow_left" :size="16" />
        <span>返回</span>
      </button>
      <h1 class="page-title">分支并排对比</h1>
      <p class="page-subtitle">
        看反事实变量改了什么之后,推演产物在哪里不一样
      </p>
    </header>

    <div v-if="loading && !data" class="state-block">加载对比数据中…</div>
    <div v-else-if="errorMessage" class="state-block state-error">
      {{ errorMessage }}
    </div>

    <template v-else-if="data">
      <!-- 两 sim metadata 卡片 -->
      <section class="meta-pair">
        <article class="meta-card meta-card--a">
          <header class="meta-header">
            <span class="meta-tag">分支 A</span>
            <span class="meta-state">{{ stateLabel(data.sim_a.state) }}</span>
            <button class="meta-link" @click="gotoSim(data.sim_a.id)">
              查看完整推演 →
            </button>
          </header>
          <p class="meta-divergence">{{ data.sim_a.divergence }}</p>
          <ul class="meta-stats">
            <li>重塑度 <strong>{{ data.sim_a.reshape_percent }}%</strong></li>
            <li>目标字数 <strong>{{ data.sim_a.target_chars }}</strong></li>
            <li>已跑 <strong>{{ data.sim_a.current_round }}</strong> / {{ data.sim_a.rounds_planned }} 幕</li>
            <li>成本 <strong>¥{{ data.sim_a.cost_yuan.toFixed(3) }}</strong></li>
          </ul>
        </article>
        <article class="meta-card meta-card--b">
          <header class="meta-header">
            <span class="meta-tag meta-tag--b">分支 B</span>
            <span class="meta-state">{{ stateLabel(data.sim_b.state) }}</span>
            <button class="meta-link" @click="gotoSim(data.sim_b.id)">
              查看完整推演 →
            </button>
          </header>
          <p class="meta-divergence">{{ data.sim_b.divergence }}</p>
          <ul class="meta-stats">
            <li>重塑度 <strong>{{ data.sim_b.reshape_percent }}%</strong></li>
            <li>目标字数 <strong>{{ data.sim_b.target_chars }}</strong></li>
            <li>已跑 <strong>{{ data.sim_b.current_round }}</strong> / {{ data.sim_b.rounds_planned }} 幕</li>
            <li>成本 <strong>¥{{ data.sim_b.cost_yuan.toFixed(3) }}</strong></li>
          </ul>
        </article>
      </section>

      <!-- SP-9 enhancement(2026-05-29 末):AI 因果分析面板 -->
      <section class="analysis-panel">
        <header class="panel-header analysis-header">
          <Icon name="sparkles" :size="16" class="panel-icon" />
          <h2 class="panel-title">AI 因果分析</h2>
          <p class="panel-subtitle">
            AI 找出"反事实变量 → narrative 走向"的因果链 — 帮你看懂为什么不同(约 30-60 秒)
          </p>
          <button
            v-if="!analysis && !analyzing"
            class="analyze-btn"
            @click="analyzeCompare"
          >
            <Icon name="sparkles" :size="14" />
            <span>✨ AI 分析差异</span>
          </button>
        </header>

        <div v-if="analyzing" class="analysis-loading">
          <div class="spinner-dot" />
          <div class="spinner-dot" />
          <div class="spinner-dot" />
          <span class="analysis-loading-text">AI 在阅读两分支 narrative 找因果链…</span>
        </div>

        <div v-else-if="analysisError" class="analysis-error">
          {{ analysisError }}
          <button class="retry-btn" @click="analyzeCompare">重试</button>
        </div>

        <div v-else-if="analysis" class="analysis-content">
          <div v-if="analysis.is_fallback" class="analysis-fallback-banner">
            AI 分析暂不可用,以下为程序级简要统计.
            <button class="retry-btn-inline" @click="analyzeCompare">重新分析</button>
          </div>

          <!-- summary 总览 -->
          <div class="analysis-summary">
            <h3 class="analysis-sub-title">总体差异</h3>
            <p class="analysis-text">{{ analysis.summary }}</p>
          </div>

          <!-- verdicts 两分支走向 -->
          <div v-if="analysis.verdict_a || analysis.verdict_b" class="analysis-verdicts">
            <div class="verdict-block verdict-block--a">
              <span class="verdict-label">分支 A 走向</span>
              <p class="analysis-text">{{ analysis.verdict_a || "见原始 narrative" }}</p>
            </div>
            <div class="verdict-block verdict-block--b">
              <span class="verdict-label">分支 B 走向</span>
              <p class="analysis-text">{{ analysis.verdict_b || "见原始 narrative" }}</p>
            </div>
          </div>

          <!-- key turning points -->
          <div v-if="analysis.key_turning_points.length" class="analysis-block">
            <h3 class="analysis-sub-title">关键转折幕({{ analysis.key_turning_points.length }})</h3>
            <ul class="turning-list">
              <li
                v-for="(tp, i) in analysis.key_turning_points"
                :key="`tp-${i}`"
                class="turning-item"
              >
                <span class="turning-idx">第 {{ tp.scene_index + 1 }} 幕</span>
                <p class="turning-text">{{ tp.what_diverged }}</p>
                <p v-if="tp.likely_cause_cf_id" class="turning-cause">
                  → 主因:<span class="cf-link">{{ cfLabelFromId(tp.likely_cause_cf_id) }}</span>
                </p>
                <p v-else class="turning-cause turning-cause--random">
                  → 无明显反事实主因(可能是 LLM 随机性差异)
                </p>
              </li>
            </ul>
          </div>

          <!-- cf impact chains -->
          <div v-if="analysis.cf_impact_chains.length" class="analysis-block">
            <h3 class="analysis-sub-title">反事实因果链({{ analysis.cf_impact_chains.length }})</h3>
            <ul class="chain-list">
              <li
                v-for="(ch, i) in analysis.cf_impact_chains"
                :key="`chain-${i}`"
                class="chain-item"
              >
                <div class="chain-head">
                  <span class="chain-side-chip" :class="`chain-side-${ch.side}`">{{ sideLabel(ch.side) }}</span>
                  <span class="chain-cf-label">{{ cfLabelFromId(ch.cf_id) }}</span>
                </div>
                <p class="chain-text">→ {{ ch.narrative_consequence }}</p>
              </li>
            </ul>
          </div>

          <!-- reasoning(折叠) -->
          <details v-if="analysis.reasoning" class="analysis-reasoning">
            <summary class="analysis-reasoning-summary">AI 推理过程</summary>
            <p class="analysis-text">{{ analysis.reasoning }}</p>
          </details>
        </div>
      </section>

      <!-- 反事实变量差异 -->
      <section class="cf-diff-panel">
        <header class="panel-header">
          <Icon name="compass" :size="16" class="panel-icon" />
          <h2 class="panel-title">反事实变量差异</h2>
          <p class="panel-subtitle">
            两分支应用了不同的"假如…"变量,这是它们走向不同产物的根本原因
          </p>
        </header>
        <div class="cf-cols">
          <div class="cf-col cf-col--only-a">
            <h3 class="cf-col-title">
              仅 A 应用
              <span class="cf-count">{{ data.counterfactual_diff.only_in_a.length }}</span>
            </h3>
            <ul v-if="data.counterfactual_diff.only_in_a.length" class="cf-list">
              <li v-for="cf in data.counterfactual_diff.only_in_a" :key="cf.id" class="cf-item">
                <div class="cf-target">{{ cfTargetLabel(cf) }}</div>
                <div class="cf-field">字段:{{ cf.field }}</div>
                <div class="cf-values">
                  <span class="cf-old">{{ formatValue(cf.old_value) }}</span>
                  <span class="cf-arrow">→</span>
                  <span class="cf-new">{{ formatValue(cf.new_value) }}</span>
                </div>
                <p v-if="cf.user_intent" class="cf-intent">"{{ cf.user_intent }}"</p>
              </li>
            </ul>
            <p v-else class="cf-empty">无</p>
          </div>

          <div class="cf-col cf-col--common">
            <h3 class="cf-col-title">
              双方共享
              <span class="cf-count">{{ data.counterfactual_diff.common.length }}</span>
            </h3>
            <ul v-if="data.counterfactual_diff.common.length" class="cf-list">
              <li v-for="cf in data.counterfactual_diff.common" :key="cf.id" class="cf-item">
                <div class="cf-target">{{ cfTargetLabel(cf) }}</div>
                <div class="cf-field">字段:{{ cf.field }}</div>
                <div class="cf-values">
                  <span class="cf-old">{{ formatValue(cf.old_value) }}</span>
                  <span class="cf-arrow">→</span>
                  <span class="cf-new">{{ formatValue(cf.new_value) }}</span>
                </div>
              </li>
            </ul>
            <p v-else class="cf-empty">无</p>
          </div>

          <div class="cf-col cf-col--only-b">
            <h3 class="cf-col-title">
              仅 B 应用
              <span class="cf-count">{{ data.counterfactual_diff.only_in_b.length }}</span>
            </h3>
            <ul v-if="data.counterfactual_diff.only_in_b.length" class="cf-list">
              <li v-for="cf in data.counterfactual_diff.only_in_b" :key="cf.id" class="cf-item">
                <div class="cf-target">{{ cfTargetLabel(cf) }}</div>
                <div class="cf-field">字段:{{ cf.field }}</div>
                <div class="cf-values">
                  <span class="cf-old">{{ formatValue(cf.old_value) }}</span>
                  <span class="cf-arrow">→</span>
                  <span class="cf-new">{{ formatValue(cf.new_value) }}</span>
                </div>
                <p v-if="cf.user_intent" class="cf-intent">"{{ cf.user_intent }}"</p>
              </li>
            </ul>
            <p v-else class="cf-empty">无</p>
          </div>
        </div>
      </section>

      <!-- 逐幕并排 -->
      <section class="scene-alignment">
        <header class="panel-header">
          <Icon name="scene" :size="16" class="panel-icon" />
          <h2 class="panel-title">逐幕并排对比</h2>
          <p class="panel-subtitle">
            按 scene_index 对齐.A 共 <strong>{{ data.stats.scenes_a_count }}</strong> 幕 ·
            B 共 <strong>{{ data.stats.scenes_b_count }}</strong> 幕 ·
            双方都有 <strong>{{ data.stats.common_scene_count }}</strong> 幕 ·
            其中相同 <strong>{{ data.stats.similar_scene_count }}</strong> 幕
          </p>
        </header>

        <ul v-if="data.scene_alignment.length" class="scene-list">
          <li
            v-for="item in data.scene_alignment"
            :key="`scene-${item.scene_index}`"
            class="scene-row"
            :class="diffClass(item)"
          >
            <header class="scene-row-head">
              <span class="scene-idx">第 {{ item.scene_index + 1 }} 幕</span>
              <span class="scene-diff-chip" :class="diffClass(item)">{{ diffLabel(item) }}</span>
            </header>
            <div class="scene-cols">
              <!-- A column -->
              <div class="scene-col scene-col--a">
                <template v-if="item.a">
                  <h4 class="scene-name">{{ item.a.scene_name || "（无名场景）" }}</h4>
                  <p class="scene-meta">
                    <span v-if="item.a.time_anchor">⏱ {{ item.a.time_anchor }}</span>
                    <span>· 在场 {{ item.a.characters_present.length }} 人</span>
                  </p>
                  <p class="scene-narrative">{{ truncate(item.a.narrative_segment, 800) }}</p>
                </template>
                <p v-else class="scene-empty">（A 无此幕）</p>
              </div>
              <!-- B column -->
              <div class="scene-col scene-col--b">
                <template v-if="item.b">
                  <h4 class="scene-name">{{ item.b.scene_name || "（无名场景）" }}</h4>
                  <p class="scene-meta">
                    <span v-if="item.b.time_anchor">⏱ {{ item.b.time_anchor }}</span>
                    <span>· 在场 {{ item.b.characters_present.length }} 人</span>
                  </p>
                  <p class="scene-narrative">{{ truncate(item.b.narrative_segment, 800) }}</p>
                </template>
                <p v-else class="scene-empty">（B 无此幕）</p>
              </div>
            </div>
          </li>
        </ul>
        <p v-else class="state-block">两分支都还没有幕级数据(simulation_scenes 表为空 / 推演未跑完)</p>
      </section>
    </template>
  </div>
</template>

<style scoped>
.compare-page {
  min-height: 100vh;
  background: var(--color-bg);
  padding: var(--space-6) var(--space-6) var(--space-8);
}

.page-hero {
  max-width: 1300px;
  margin: 0 auto var(--space-5);
  position: relative;
}
.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: var(--space-3);
}
.back-btn:hover { color: var(--color-text); background: var(--color-surface-hover); }
.page-title {
  font-size: var(--text-2xl);
  font-weight: 600;
  color: var(--color-text);
  margin: 0 0 var(--space-1);
}
.page-subtitle {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin: 0;
}

.state-block {
  max-width: 1300px;
  margin: var(--space-8) auto;
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.state-error {
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-radius: var(--radius-md);
}

/* ================= 两 sim metadata 卡片 ================= */
.meta-pair {
  max-width: 1300px;
  margin: 0 auto var(--space-5);
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-4);
}
.meta-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4) var(--space-5);
}
.meta-card--a { border-left: 3px solid #3b82f6; }
.meta-card--b { border-left: 3px solid #d97706; }

.meta-header {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-3);
}
.meta-tag {
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  font-weight: 600;
  color: #fff;
  background: #3b82f6;
  border-radius: var(--radius-sm);
}
.meta-tag--b { background: #d97706; }
.meta-state {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.meta-link {
  margin-left: auto;
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: transparent;
  border: 0;
  padding: 0;
  cursor: pointer;
}
.meta-link:hover { text-decoration: underline; }

.meta-divergence {
  font-size: var(--text-sm);
  color: var(--color-text);
  line-height: 1.6;
  margin: 0 0 var(--space-3);
  font-style: italic;
}
.meta-stats {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.meta-stats li strong { color: var(--color-text); font-variant-numeric: tabular-nums; }

/* ================= AI 因果分析面板(SP-9 enhancement,2026-05-29 末) ================= */
.analysis-panel {
  max-width: 1300px;
  margin: 0 auto var(--space-5);
  background: linear-gradient(
    135deg,
    var(--color-accent-soft) 0%,
    rgba(124, 58, 237, 0.04) 100%
  );
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4) var(--space-5);
}
.analysis-header { position: relative; }
.analyze-btn {
  position: absolute;
  top: 0;
  right: 0;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: 0;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out);
}
.analyze-btn:hover { background: var(--color-accent-hover); }

.analysis-loading {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-4);
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.analysis-loading .spinner-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--color-accent);
  animation: bounce 1.4s ease-in-out infinite both;
}
.analysis-loading .spinner-dot:nth-child(1) { animation-delay: -0.32s; }
.analysis-loading .spinner-dot:nth-child(2) { animation-delay: -0.16s; }
@keyframes bounce {
  0%, 80%, 100% { transform: scale(0.6); opacity: 0.5; }
  40% { transform: scale(1); opacity: 1; }
}
.analysis-loading-text { margin-left: var(--space-2); }

.analysis-error {
  padding: var(--space-3);
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
}
.retry-btn,
.retry-btn-inline {
  margin-left: var(--space-2);
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
}

.analysis-fallback-banner {
  padding: var(--space-2) var(--space-3);
  margin-bottom: var(--space-3);
  background: rgba(245, 158, 11, 0.1);
  border-left: 3px solid #f59e0b;
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  color: #92400e;
}

.analysis-content {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.analysis-summary,
.analysis-block {
  background: var(--color-surface);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
}
.analysis-sub-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
  margin: 0 0 var(--space-2);
}
.analysis-text {
  font-size: var(--text-sm);
  color: var(--color-text);
  line-height: 1.7;
  margin: 0;
  white-space: pre-wrap;
}

.analysis-verdicts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}
.verdict-block {
  background: var(--color-surface);
  border-radius: var(--radius-md);
  padding: var(--space-3);
}
.verdict-block--a { border-left: 3px solid #3b82f6; }
.verdict-block--b { border-left: 3px solid #d97706; }
.verdict-label {
  display: block;
  font-size: var(--text-xs);
  font-weight: 600;
  color: var(--color-text-muted);
  margin-bottom: 4px;
}

.turning-list,
.chain-list { list-style: none; margin: 0; padding: 0; }
.turning-item,
.chain-item {
  padding: var(--space-2) var(--space-3);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  margin-bottom: var(--space-2);
  font-size: var(--text-sm);
}
.turning-idx {
  display: inline-block;
  font-weight: 600;
  color: var(--color-text);
  margin-right: var(--space-2);
}
.turning-text { margin: var(--space-1) 0; color: var(--color-text); }
.turning-cause {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.cf-link {
  color: var(--color-accent-text);
  font-weight: 500;
}
.turning-cause--random { color: var(--color-text-subtle); font-style: italic; }

.chain-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: 4px;
}
.chain-side-chip {
  padding: 1px var(--space-2);
  font-size: var(--text-xs);
  font-weight: 600;
  border-radius: var(--radius-sm);
}
.chain-side-only_in_a { color: #1d4ed8; background: #dbeafe; }
.chain-side-only_in_b { color: #b45309; background: #fed7aa; }
.chain-side-common { color: var(--color-text-muted); background: var(--color-surface-sunken); }
.chain-cf-label {
  font-weight: 500;
  color: var(--color-accent-text);
  font-size: var(--text-xs);
}
.chain-text { margin: 0; color: var(--color-text); }

.analysis-reasoning {
  background: var(--color-surface);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-3);
}
.analysis-reasoning-summary {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  cursor: pointer;
  user-select: none;
}
.analysis-reasoning[open] .analysis-reasoning-summary {
  margin-bottom: var(--space-2);
}

/* ================= 反事实差异面板 ================= */
.cf-diff-panel,
.scene-alignment {
  max-width: 1300px;
  margin: 0 auto var(--space-5);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4) var(--space-5);
}

.panel-header {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin-bottom: var(--space-4);
}
.panel-icon {
  color: var(--color-text-muted);
  transform: translateY(2px);
}
.panel-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}
.panel-subtitle {
  width: 100%;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: 1.6;
  margin: var(--space-1) 0 0;
}
.panel-subtitle strong {
  color: var(--color-text);
  font-variant-numeric: tabular-nums;
}

.cf-cols {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: var(--space-3);
}
.cf-col {
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  padding: var(--space-3);
  border: 1px solid var(--color-border);
}
.cf-col--only-a { border-top: 2px solid #3b82f6; }
.cf-col--common { border-top: 2px solid #6b7280; }
.cf-col--only-b { border-top: 2px solid #d97706; }

.cf-col-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
  margin: 0 0 var(--space-2);
}
.cf-count {
  font-size: var(--text-xs);
  padding: 1px var(--space-2);
  background: var(--color-surface);
  border-radius: var(--radius-full);
  color: var(--color-text-muted);
  font-variant-numeric: tabular-nums;
}
.cf-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.cf-item {
  padding: var(--space-2);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  margin-bottom: var(--space-2);
  font-size: var(--text-xs);
}
.cf-target {
  font-weight: 600;
  color: var(--color-text);
}
.cf-field {
  color: var(--color-text-muted);
  margin: 2px 0;
}
.cf-values {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-wrap: wrap;
  line-height: 1.5;
}
.cf-old {
  color: var(--color-text-muted);
  text-decoration: line-through;
  text-decoration-color: var(--color-text-subtle);
}
.cf-arrow { color: var(--color-text-subtle); }
.cf-new {
  color: var(--color-accent-text);
  font-weight: 500;
}
.cf-intent {
  margin: var(--space-1) 0 0;
  padding-top: var(--space-1);
  border-top: 1px dashed var(--color-border);
  color: var(--color-text-muted);
  font-style: italic;
}
.cf-empty {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  text-align: center;
  padding: var(--space-3) 0;
  margin: 0;
}

/* ================= 逐幕对比 ================= */
.scene-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.scene-row {
  padding: var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: var(--space-3);
  background: var(--color-surface);
}
.scene-row--similar {
  background: rgba(34, 197, 94, 0.04);
  border-color: rgba(34, 197, 94, 0.3);
}
.scene-row--differ {
  background: rgba(245, 158, 11, 0.04);
  border-color: rgba(245, 158, 11, 0.3);
}
.scene-row--only-a {
  background: rgba(59, 130, 246, 0.04);
  border-color: rgba(59, 130, 246, 0.3);
}
.scene-row--only-b {
  background: rgba(217, 119, 6, 0.04);
  border-color: rgba(217, 119, 6, 0.3);
}

.scene-row-head {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
}
.scene-idx {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
}
.scene-diff-chip {
  font-size: var(--text-xs);
  padding: 1px var(--space-2);
  border-radius: var(--radius-sm);
  font-weight: 500;
}
.scene-diff-chip.scene-row--similar { color: #15803d; background: #dcfce7; }
.scene-diff-chip.scene-row--differ { color: #b45309; background: #fef3c7; }
.scene-diff-chip.scene-row--only-a { color: #1d4ed8; background: #dbeafe; }
.scene-diff-chip.scene-row--only-b { color: #b45309; background: #fed7aa; }

.scene-cols {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}
.scene-col {
  padding: var(--space-3);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
}
.scene-col--a { border-left: 2px solid #3b82f6; }
.scene-col--b { border-left: 2px solid #d97706; }

.scene-name {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
  margin: 0 0 var(--space-1);
}
.scene-meta {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin: 0 0 var(--space-2);
  display: inline-flex;
  gap: var(--space-2);
}
.scene-narrative {
  font-size: var(--text-sm);
  color: var(--color-text);
  line-height: 1.7;
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
}
.scene-empty {
  font-size: var(--text-sm);
  color: var(--color-text-subtle);
  text-align: center;
  padding: var(--space-4) 0;
  margin: 0;
  font-style: italic;
}
</style>
