<script setup lang="ts">
/**
 * QualityScoreCard — Sprint 6.A2 路线图 #7(2026-05-23)续写质量评分卡(简版)。
 *
 * 独立组件 — 跟两个守护者(自洽 / 正典)平级,**不被任一面板包裹**。
 * 内部消费已有 audit 数据(零 LLM 成本):
 *   - 自洽 audit:GET /api/simulations/:id/audit/latest
 *   - 正典 audit:GET /api/simulations/:id/canonical_audit/latest
 * 没历史 → 404 静默,该栏显"未审计"占位。
 *
 * 视觉:
 *   - 初始态项目:单栏(只显自洽)— 正典不适用
 *   - 中间/末尾/漫创态:双栏(自洽 + 正典)
 *   - 任一审计有 done 数据才显示组件;两个都没数据时整个不显
 *   - 4 档分数色:90+绿 / 70+青 / 50+黄 / <50 红
 */
import { computed, onMounted, ref, watch } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type AuditResponse,
  type CanonicalAuditResponse,
} from "../api/types";

const props = defineProps<{
  simulationId: string;
  /** 中间/末尾/漫创态 = true(双栏);初始态 = false(单栏) */
  isApplicable: boolean;
}>();

const selfAudit = ref<AuditResponse | null>(null);
const canonicalAudit = ref<CanonicalAuditResponse | null>(null);

// Bug 修复(2026-05-23 B2):序列 token — 防 sim A 的晚到响应覆盖 sim B 的新数据
// 触发场景:网络慢时切 sim,A 请求未回来时 B 已发出 → A 后到会污染 B 的展示
// B7.1(2026-05-27)修:**两个 load 必须用各自独立的 seq token**。
// 之前共用 _loadSeq 时,reloadAll() 里先调 loadSelfAudit(seq=N) 后调 loadCanonicalAudit(seq=N+1),
// 自洽 fetch 完成时检查 mySeq(N) !== _loadSeq(N+1) → 错误地丢弃了刚拉到的有效结果!
// 现象:用户跑完自洽后,QualityScoreCard 的自洽分数永远显示"未审计"。
let _selfLoadSeq = 0;
let _canonicalLoadSeq = 0;

async function loadSelfAudit() {
  const mySeq = ++_selfLoadSeq;
  selfAudit.value = null;
  try {
    const resp = await api.get<AuditResponse>(
      `/simulations/${props.simulationId}/audit/latest`,
    );
    if (mySeq !== _selfLoadSeq) return;   // 已被新请求覆盖,丢弃
    selfAudit.value = resp;
  } catch (e) {
    if (mySeq !== _selfLoadSeq) return;
    if (!(e instanceof ApiError && e.status === 404)) {
      if (import.meta.env.DEV) {
        console.warn("[QualityScoreCard] 自洽 audit 拉取失败:", e);
      }
    }
  }
}

async function loadCanonicalAudit() {
  const mySeq = ++_canonicalLoadSeq;
  canonicalAudit.value = null;
  if (!props.isApplicable) return;
  try {
    const resp = await api.get<CanonicalAuditResponse>(
      `/simulations/${props.simulationId}/canonical_audit/latest`,
    );
    if (mySeq !== _canonicalLoadSeq) return;
    canonicalAudit.value = resp;
  } catch (e) {
    if (mySeq !== _canonicalLoadSeq) return;
    if (!(e instanceof ApiError && e.code === "CANONICAL_AUDIT_NOT_FOUND")) {
      if (import.meta.env.DEV) {
        console.warn("[QualityScoreCard] 正典 audit 拉取失败:", e);
      }
    }
  }
}

function reloadAll() {
  void loadSelfAudit();
  void loadCanonicalAudit();
}

onMounted(reloadAll);

// 切 sim 时重拉
watch(() => props.simulationId, reloadAll);

// Bug 修复(2026-05-23):isApplicable 异步从 false 变 true 时补拉正典 audit
// 原因:SimulationDetailView 异步拉 project.mode,首次 mount 时 isApplicable 可能还是 false
watch(
  () => props.isApplicable,
  (newApplicable, oldApplicable) => {
    if (newApplicable && !oldApplicable) {
      void loadCanonicalAudit();
    }
  },
);

const selfScore = computed<number | null>(() => {
  return selfAudit.value?.overall_score ?? null;
});
const selfIssuesCount = computed<number>(() => selfAudit.value?.issues?.length ?? 0);

const canonicalScore = computed<number | null>(() => {
  const s = canonicalAudit.value?.overall_score;
  return typeof s === "number" ? s : null;
});
const canonicalIssuesCount = computed<number>(
  () => canonicalAudit.value?.effective_issues_count ?? 0,
);
const canonicalExemptCount = computed<number>(() => {
  return (canonicalAudit.value?.issues ?? []).filter(
    (i) => i.counterfactual_exempt,
  ).length;
});

// B6(2026-05-27):改为始终显示组件 — 即使两个分都未审计,也显示"未审计"占位卡片,
// 让用户一进推演详情页就看到"质量评分"这个标志性的产物层,而不是只在跑过审计后才浮现。

// 暴露 reload 方法,parent 监听 audit / canonicalGuardian phase 变 done 时主动调,
// 让评分卡随守护者完成后自动刷新分数,无需用户手动重载页面。
defineExpose({ reload: reloadAll });

/** 0-100 → 4 档颜色 */
function scoreColorClass(score: number | null): string {
  if (score === null) return "score-na";
  if (score >= 90) return "score-great";
  if (score >= 70) return "score-good";
  if (score >= 50) return "score-mid";
  return "score-bad";
}

function scoreLabel(score: number | null): string {
  if (score === null) return "—";
  if (score >= 90) return "优秀";
  if (score >= 70) return "良好";
  if (score >= 50) return "中等";
  return "待改进";
}
</script>

<template>
  <section class="quality-score-card surface">
    <header class="qsc-header">
      <span class="qsc-icon" aria-hidden="true">⭐</span>
      <h3 class="qsc-title">这次推演的质量评分</h3>
    </header>
    <div class="qsc-row" :class="{ 'qsc-row--single': !isApplicable }">
      <!-- 自洽分(全 mode 都显)-->
      <div class="qsc-cell" :class="scoreColorClass(selfScore)">
        <div class="qsc-cell-head">
          <span class="qsc-cell-label">自洽</span>
          <span class="qsc-cell-sub">用户设定层</span>
        </div>
        <div v-if="selfScore !== null" class="qsc-cell-body">
          <span class="qsc-score mono">{{ selfScore }}</span>
          <span class="qsc-score-max mono">/ 100</span>
          <span class="qsc-grade">{{ scoreLabel(selfScore) }}</span>
        </div>
        <div v-else class="qsc-cell-na">未审计</div>
        <div v-if="selfScore !== null" class="qsc-cell-foot mono">
          {{ selfIssuesCount }} 个问题
        </div>
      </div>

      <!-- 正典分(仅中间/末尾/漫创态显)-->
      <div
        v-if="isApplicable"
        class="qsc-cell"
        :class="scoreColorClass(canonicalScore)"
      >
        <div class="qsc-cell-head">
          <span class="qsc-cell-label">正典</span>
          <span class="qsc-cell-sub">原作底色层</span>
        </div>
        <div v-if="canonicalScore !== null" class="qsc-cell-body">
          <span class="qsc-score mono">{{ canonicalScore }}</span>
          <span class="qsc-score-max mono">/ 100</span>
          <span class="qsc-grade">{{ scoreLabel(canonicalScore) }}</span>
        </div>
        <div v-else class="qsc-cell-na">未审计</div>
        <div v-if="canonicalScore !== null" class="qsc-cell-foot mono">
          {{ canonicalIssuesCount }} 个偏离
          <span v-if="canonicalExemptCount > 0" class="qsc-exempt-hint">
            · {{ canonicalExemptCount }} 个豁免
          </span>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.quality-score-card {
  padding: var(--space-4) var(--space-5);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  border: 1px solid var(--color-border);
}

.qsc-header {
  display: flex;
  align-items: center;
  gap: 6px;
}
.qsc-icon {
  font-size: var(--text-md);
}
.qsc-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
}

.qsc-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
}
/* 初始态:单栏占满宽度 */
.qsc-row--single {
  grid-template-columns: 1fr;
}

.qsc-cell {
  padding: var(--space-3) var(--space-4);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  transition: border-color var(--duration-fast) var(--ease-out);
}
.qsc-cell-head {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.qsc-cell-label {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
}
.qsc-cell-sub {
  font-size: 11px;
  color: var(--color-text-subtle);
}
.qsc-cell-body {
  display: flex;
  align-items: baseline;
  gap: 4px;
  flex-wrap: wrap;
}
.qsc-score {
  font-size: 32px;
  font-weight: 700;
  line-height: 1;
  color: var(--color-text);
}
.qsc-score-max {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.qsc-grade {
  margin-left: auto;
  padding: 2px 8px;
  font-size: 11px;
  font-weight: 500;
  border-radius: var(--radius-sm);
}
.qsc-cell-na {
  font-size: var(--text-sm);
  color: var(--color-text-subtle);
  font-style: italic;
  padding: 6px 0;
}
.qsc-cell-foot {
  font-size: 11px;
  color: var(--color-text-muted);
}
.qsc-exempt-hint {
  color: var(--color-text-subtle);
}

/* 4 档分数色 — border-color + grade chip 颜色双信号(无左侧色条) */
.qsc-cell.score-great { border-color: #16A34A; }
.qsc-cell.score-great .qsc-grade { color: #16A34A; background: rgba(22, 163, 74, 0.10); }
.qsc-cell.score-good { border-color: #06B6D4; }
.qsc-cell.score-good .qsc-grade { color: #0891B2; background: rgba(6, 182, 212, 0.10); }
.qsc-cell.score-mid { border-color: #F59E0B; }
.qsc-cell.score-mid .qsc-grade { color: #B45309; background: rgba(245, 158, 11, 0.12); }
.qsc-cell.score-bad { border-color: var(--color-danger); }
.qsc-cell.score-bad .qsc-grade { color: var(--color-danger); background: var(--color-danger-soft); }
</style>
