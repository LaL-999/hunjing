<script setup lang="ts">
/**
 * CanonicalGuardianPanel — 正典守护者面板(Sprint 2.D 差异化王牌)。
 *
 * UI 结构(从上到下):
 *   - 标题 + 简介(自洽 vs 正典 的差异)
 *   - state 区:
 *     · idle      → [✦ 开始正典审计] CTA
 *     · running   → 转圈 + 进度提示(LLM 30-60s)
 *     · done      → 顶部统计 chip + 8 维度 issues 列表
 *     · failed    → 错误信息 + 重试
 *     · not_applicable → "初始态项目不适用"提示
 *   - 8 维度 issues 列表:每条显 dimension/severity/finding/evidence/canon_ref
 *     反事实豁免的条目高亮(浅绿 + ✓ 用户主动重塑 chip)
 *
 * Props:
 *   simulationId  目标推演 id
 *   isApplicable  父组件传入(基于 project.mode):initial → false,其它 → true
 */
import { computed, onMounted, onBeforeUnmount, ref, watch } from "vue";

import {
  CANONICAL_DIMENSION_META,
  CANONICAL_SEVERITY_META,
  type CanonicalIssue,
} from "../api/types";
import { useCanonicalGuardian } from "../composables/useCanonicalGuardian";

const props = defineProps<{
  simulationId: string;
  isApplicable: boolean;
}>();

// B6(2026-05-27):正典审计 done 后 emit 给 parent,
// parent 据此调 QualityScoreCard.reload() 让顶层评分卡刷新
const emit = defineEmits<{
  (e: "audit-done"): void;
}>();

const guardian = useCanonicalGuardian(props.simulationId);

// 折叠态:done 时默认折叠,显折叠 banner;点击展开完整面板。其他 state(idle/running/failed)
// 不需要折叠(需要 CTA / 转圈 / 错误反馈),永远展开。
const expanded = ref(false);

// B6(2026-05-27):正典审计 phase 从 running → done → emit
watch(
  () => guardian.phase.value,
  (newPhase, oldPhase) => {
    if (newPhase === "done" && oldPhase !== "done") {
      emit("audit-done");
    }
  },
);

function togglePanel() {
  expanded.value = !expanded.value;
}

const canonicalScore = computed<number | null>(() => {
  const s = guardian.audit.value?.overall_score;
  return typeof s === "number" ? s : null;
});
const canonicalIssuesCount = computed<number>(
  () => guardian.audit.value?.effective_issues_count ?? 0,
);
const canonicalExemptCount = computed<number>(() => guardian.stats.value?.exempt ?? 0);

function scoreLabel(score: number | null): string {
  if (score === null) return "—";
  if (score >= 90) return "优秀";
  if (score >= 70) return "良好";
  if (score >= 50) return "中等";
  return "待改进";
}

onMounted(() => {
  if (props.isApplicable) {
    void guardian.loadLatest();
  }
});

watch(
  () => props.simulationId,
  (newId, oldId) => {
    if (newId !== oldId) {
      guardian.reset();
      expanded.value = false;
      if (props.isApplicable) void guardian.loadLatest();
    }
  },
);

// Bug 修复(2026-05-23):用户报"退出作品再进入要重新跑正典"
// 根因:SimulationDetailView 异步拉 project.mode,首次挂载时 isApplicable=false(null !== null),
// onMounted 跳过 loadLatest;之后 projectMode 加载完 isApplicable 变 true 但无人补调 → 永远 idle
// 修法:watch isApplicable,从 false 翻 true 时补调一次 loadLatest
watch(
  () => props.isApplicable,
  (newApplicable, oldApplicable) => {
    if (newApplicable && !oldApplicable) {
      void guardian.loadLatest();
    }
  },
);

onBeforeUnmount(() => {
  guardian.unsubscribe();
});

function dimensionLabel(d: CanonicalIssue["dimension"]): string {
  return CANONICAL_DIMENSION_META[d]?.label ?? d;
}

function severityLabel(s: CanonicalIssue["severity"]): string {
  return CANONICAL_SEVERITY_META[s]?.label ?? s;
}

function severityStyle(s: CanonicalIssue["severity"]): Record<string, string> {
  const meta = CANONICAL_SEVERITY_META[s];
  if (!meta) return {};
  return { color: meta.color, background: meta.bg };
}
</script>

<template>
  <section class="canonical-panel">
    <!-- 折叠态 banner — done 态且未展开时显
         视觉对齐自洽守护者的折叠 banner(audit-trigger--has-result) -->
    <button
      v-if="isApplicable && guardian.phase.value === 'done' && !expanded"
      type="button"
      class="cg-collapsed-banner"
      @click="togglePanel"
    >
      <span class="cg-collapsed-icon" aria-hidden="true">⚖</span>
      <span class="cg-collapsed-text">
        正典守护者:<strong>{{ canonicalScore }} 分</strong>
        <span class="cg-collapsed-grade">{{ scoreLabel(canonicalScore) }}</span>
        <span class="cg-collapsed-sep">·</span>
        {{ canonicalIssuesCount }} 偏离<span
          v-if="canonicalExemptCount > 0"
          class="cg-collapsed-exempt"
        >({{ canonicalExemptCount }} 豁免)</span>
      </span>
      <span class="cg-collapsed-arrow" aria-hidden="true">展开查看 ▸</span>
    </button>

    <!-- 标题区(done 折叠时隐藏;其他 state 始终显引导用户)-->
    <header
      v-if="isApplicable && (guardian.phase.value !== 'done' || expanded)"
      class="cg-header"
    >
      <h3 class="cg-title">
        <span class="cg-icon">⚖</span>
        正典守护者
        <span class="cg-beta">差异化王牌</span>
        <button
          v-if="guardian.phase.value === 'done' && expanded"
          type="button"
          class="cg-collapse-btn"
          aria-label="收起"
          title="收起"
          @click="togglePanel"
        >▾ 收起</button>
      </h3>
      <p class="cg-sub">
        审计产物对<strong>原作 canon</strong>的偏离 — 与「自洽守护者」互补:自洽看用户设定,正典看原作底色。
        <br />
        反事实改过的部分自动豁免(不算违规)。
      </p>
    </header>

    <!-- idle: 从未审计过(仅 isApplicable=true 时可达)-->
    <div v-if="isApplicable && guardian.phase.value === 'idle'" class="cg-state cg-state-idle">
      <span class="cg-state-icon">⚖</span>
      <p class="cg-state-main">还没审计过这次推演</p>
      <p class="cg-state-sub">点击下方,AI 会对 8 维度做正典对照审计(约 30-60 秒)</p>
      <button class="cg-trigger-btn" @click="guardian.trigger()">
        ✦ 开始正典审计
      </button>
    </div>

    <!-- loading: 拉 latest 中 -->
    <div v-if="isApplicable && guardian.phase.value === 'loading'" class="cg-state">
      <p class="cg-state-main">加载中…</p>
    </div>

    <!-- running: 审计中 -->
    <div v-if="isApplicable && guardian.phase.value === 'running'" class="cg-state cg-state-running">
      <span class="cg-spinner" aria-hidden="true"></span>
      <p class="cg-state-main">AI 在做 8 维度正典审计…</p>
      <p class="cg-state-sub">长产物 + 反事实豁免分析 = 30-60 秒,请稍候(可关闭页面,完成后回来看)</p>
    </div>

    <!-- failed: 错误 -->
    <div v-if="isApplicable && (guardian.phase.value === 'failed' || guardian.phase.value === 'error')" class="cg-state cg-state-failed">
      <span class="cg-state-icon">✗</span>
      <p class="cg-state-main">审计失败</p>
      <p class="cg-state-sub">{{ guardian.errorMessage.value ?? "未知错误" }}</p>
      <button class="cg-trigger-btn" @click="guardian.trigger()">↻ 重试</button>
    </div>

    <!-- done: 显示结果(仅展开态显完整内容)-->
    <template v-if="isApplicable && guardian.phase.value === 'done' && expanded">
      <!-- 顶部统计 -->
      <div class="cg-stats">
        <span class="cg-stat-item cg-stat-strict">
          <span class="cg-stat-num mono">{{ guardian.stats.value.strict }}</span>
          <span class="cg-stat-label">严格符合</span>
        </span>
        <span class="cg-stat-item cg-stat-drift">
          <span class="cg-stat-num mono">{{ guardian.stats.value.realDrift }}</span>
          <span class="cg-stat-label">真实偏离</span>
        </span>
        <span class="cg-stat-item cg-stat-exempt">
          <span class="cg-stat-num mono">{{ guardian.stats.value.exempt }}</span>
          <span class="cg-stat-label">反事实豁免</span>
        </span>
        <button class="cg-rerun-btn" @click="guardian.trigger()" title="重新审计(覆盖此次结果)">
          ↻ 重审
        </button>
      </div>

      <!-- 8 维度 issues 列表 -->
      <ul v-if="guardian.issues.value.length > 0" class="cg-issues">
        <li
          v-for="(issue, idx) in guardian.issues.value"
          :key="idx"
          class="cg-issue"
          :class="{ 'is-exempt': issue.counterfactual_exempt }"
        >
          <header class="issue-head">
            <span class="issue-dim">{{ dimensionLabel(issue.dimension) }}</span>
            <span class="issue-sev" :style="severityStyle(issue.severity)">
              {{ severityLabel(issue.severity) }}
            </span>
            <span v-if="issue.counterfactual_exempt" class="issue-exempt-chip">
              ✓ 用户重塑·豁免
            </span>
          </header>

          <p class="issue-finding">{{ issue.finding }}</p>

          <div class="issue-evidence">
            <div class="ev-row">
              <span class="ev-label mono">产物</span>
              <span class="ev-content">「{{ issue.evidence_excerpt }}」</span>
            </div>
            <div class="ev-row">
              <span class="ev-label mono ev-canon">原作</span>
              <span class="ev-content ev-canon">{{ issue.canon_reference }}</span>
            </div>
            <div v-if="issue.counterfactual_exempt && issue.exempt_reason" class="ev-row">
              <span class="ev-label mono ev-exempt">豁免</span>
              <span class="ev-content ev-exempt">{{ issue.exempt_reason }}</span>
            </div>
          </div>
        </li>
      </ul>
      <p v-else class="cg-empty">审计完成 — 8 维度全部严格符合原作正典</p>
    </template>
  </section>
</template>

<style scoped>
/* 折叠态 banner — 视觉对齐自洽守护者的 audit-trigger--has-result */
.cg-collapsed-banner {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  width: 100%;
  padding: var(--space-3) var(--space-4);
  background: var(--color-accent-soft);
  border: 1px dashed var(--color-accent-border);
  border-radius: var(--radius-lg);
  font-size: var(--text-sm);
  color: var(--color-accent-text);
  text-align: left;
  transition: background var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out);
}
.cg-collapsed-banner:hover {
  background: rgba(124, 58, 237, 0.12);
  border-color: var(--color-accent);
}
.cg-collapsed-icon {
  font-size: var(--text-lg);
  flex-shrink: 0;
}
.cg-collapsed-text {
  flex: 1;
}
.cg-collapsed-text strong {
  color: var(--color-text);
  font-weight: 600;
}
.cg-collapsed-grade {
  margin-left: 6px;
  padding: 1px 8px;
  font-size: 11px;
  font-weight: 500;
  background: var(--color-surface);
  border-radius: var(--radius-sm);
}
.cg-collapsed-sep {
  margin: 0 6px;
  color: var(--color-text-subtle);
}
.cg-collapsed-exempt {
  margin-left: 4px;
  color: var(--color-text-subtle);
  font-size: var(--text-xs);
}
.cg-collapsed-arrow {
  flex-shrink: 0;
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  font-weight: 500;
}

/* 展开时顶部"收起"按钮(在 title 行右侧) */
.cg-collapse-btn {
  margin-left: auto;
  padding: 2px 10px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition: all var(--duration-fast) var(--ease-out);
}
.cg-collapse-btn:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
  border-color: var(--color-border-strong);
}

.canonical-panel {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

.cg-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.cg-title {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
}
.cg-icon {
  color: var(--color-accent);
  font-size: var(--text-lg);
}
.cg-beta {
  font-size: 10px;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
  font-weight: 500;
}
.cg-sub {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.6;
  margin: 0;
}
.cg-sub strong {
  color: var(--color-accent-text);
}

/* state 通用 */
.cg-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: var(--space-5) var(--space-4);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  gap: 6px;
}
.cg-state-icon {
  font-size: 28px;
  color: var(--color-text-muted);
  margin-bottom: var(--space-1);
}
.cg-state-na .cg-state-icon { color: var(--color-text-subtle); }
.cg-state-idle .cg-state-icon { color: var(--color-accent); }
.cg-state-failed .cg-state-icon { color: var(--color-danger); }
.cg-state-main {
  font-size: var(--text-md);
  color: var(--color-text);
  margin: 0;
}
.cg-state-sub {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  max-width: 480px;
  line-height: 1.6;
  margin: 0;
}

.cg-trigger-btn {
  margin-top: var(--space-3);
  padding: 8px var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: filter var(--duration-fast) var(--ease-out);
}
.cg-trigger-btn:hover {
  filter: brightness(1.05);
}

/* running spinner */
.cg-state-running .cg-state-main {
  color: var(--color-accent-text);
}
.cg-spinner {
  width: 24px;
  height: 24px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: 50%;
  animation: spin var(--duration-spin) linear infinite;
  margin-bottom: var(--space-2);
}

/* 统计 chip 区 */
.cg-stats {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  flex-wrap: wrap;
}
.cg-stat-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: var(--text-xs);
  padding: 2px var(--space-2);
  border-radius: var(--radius-sm);
}
.cg-stat-num {
  font-weight: 600;
  font-size: var(--text-sm);
}
.cg-stat-label {
  color: var(--color-text-muted);
}
.cg-stat-strict {
  color: #16A34A;
  background: rgba(22, 163, 74, 0.1);
}
.cg-stat-drift {
  color: #B45309;
  background: rgba(245, 158, 11, 0.12);
}
.cg-stat-exempt {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
}
.cg-rerun-btn {
  margin-left: auto;
  padding: 3px var(--space-2);
  font-size: 11px;
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.cg-rerun-btn:hover {
  color: var(--color-accent-text);
  border-color: var(--color-accent);
}

/* issues 列表 */
.cg-issues {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.cg-issue {
  padding: var(--space-3);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition: all var(--duration-fast) var(--ease-out);
}
.cg-issue.is-exempt {
  border-color: rgba(22, 163, 74, 0.3);
  background: rgba(22, 163, 74, 0.04);
}

.issue-head {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: 6px;
  flex-wrap: wrap;
}
.issue-dim {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
}
.issue-sev {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
}
.issue-exempt-chip {
  font-size: 10px;
  font-weight: 600;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
  color: #16A34A;
  background: rgba(22, 163, 74, 0.14);
}

.issue-finding {
  font-size: var(--text-xs);
  color: var(--color-text);
  line-height: 1.6;
  margin: 0 0 var(--space-2) 0;
}

.issue-evidence {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--space-2);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font-size: 11px;
  line-height: 1.6;
}
.ev-row {
  display: flex;
  gap: 6px;
  align-items: flex-start;
}
.ev-label {
  flex-shrink: 0;
  width: 30px;
  text-align: center;
  padding: 1px 4px;
  font-size: 10px;
  font-weight: 600;
  border-radius: var(--radius-sm);
  background: rgba(148, 163, 184, 0.15);
  color: var(--color-text-muted);
}
.ev-label.ev-canon {
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
}
.ev-label.ev-exempt {
  background: rgba(22, 163, 74, 0.14);
  color: #16A34A;
}
.ev-content {
  flex: 1;
  color: var(--color-text);
  word-break: break-word;
}
.ev-content.ev-canon {
  color: var(--color-accent-text);
}
.ev-content.ev-exempt {
  color: #16A34A;
  font-style: italic;
}

.cg-empty {
  text-align: center;
  padding: var(--space-4);
  font-size: var(--text-sm);
  color: #16A34A;
  background: rgba(22, 163, 74, 0.06);
  border-radius: var(--radius-md);
  margin: 0;
}
</style>
