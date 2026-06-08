<template>
  <section class="ac-panel">
    <!-- ====== Header ====== -->
    <header class="ac-header">
      <div class="ac-title-row">
        <h2 class="ac-title">作者指南针</h2>
        <span class="ac-status-chip" :data-phase="phase">{{ phaseLabel }}</span>
      </div>
      <p v-if="!props.isInitialMode" class="ac-subtitle">
        双轨制 LLM 调研:外部研究作家学术定位 + 内部反推原作文风量化。
        续作生成时自动注入,让产物风格贴合该作家。
      </p>
      <p v-else class="ac-subtitle">
        初始态仅做<strong>外部研究</strong> — LLM 调研你指定的作家学术定位(主题偏好 / 风格 / 代表手法),
        续作生成时自动注入风骨。<br />
        <span class="ac-initial-hint">
          (内部反推需上传原作量化文风;初始态从零创作,此模块自动隐藏)
        </span>
      </p>
    </header>

    <!-- ====== 用户输入(作家名 + 作品名)+ 分析按钮 ====== -->
    <div class="ac-input-section">
      <div class="ac-input-row">
        <label class="ac-label">
          <span class="ac-label-text">作家</span>
          <input
            v-model="authorName"
            type="text"
            class="ac-input"
            placeholder="如:川端康成"
            :disabled="isAnalyzing || compass?.user_locked"
            maxlength="100"
          />
        </label>
        <label class="ac-label">
          <span class="ac-label-text">作品</span>
          <input
            v-model="workTitle"
            type="text"
            class="ac-input"
            placeholder="如:雪国"
            :disabled="isAnalyzing || compass?.user_locked"
            maxlength="200"
          />
        </label>
      </div>
      <div class="ac-action-row">
        <button
          v-if="!compass?.user_locked"
          type="button"
          class="ac-btn ac-btn--primary"
          :disabled="isAnalyzing || (!authorName.trim() && !workTitle.trim())"
          @click="onAnalyze"
        >
          <span v-if="isAnalyzing" class="ac-spinner" aria-hidden="true"></span>
          <span>{{ isAnalyzing ? "正在调研(~30s)..." : (compass ? "重新分析" : "开始分析") }}</span>
        </button>
        <span v-else class="ac-locked-hint">已锁定 — 先解锁才能重新分析</span>
      </div>
    </div>

    <!-- ====== 错误提示 ====== -->
    <div v-if="errorMessage && phase === 'error'" class="ac-error-banner">
      {{ errorMessage }}
    </div>

    <!-- ====== 双轨结果 ====== -->
    <div v-if="compass" class="ac-tracks">
      <!-- 外部研究 -->
      <div class="ac-card" :data-status="compass.external_status">
        <header class="ac-card-header">
          <h3 class="ac-card-title">外部研究(作家学术定位)</h3>
          <span class="ac-card-status">{{ statusLabel(compass.external_status) }}</span>
        </header>
        <div v-if="compass.external_status === 'failed'" class="ac-card-error">
          {{ compass.external_error || "外部研究失败" }}
        </div>
        <div v-else-if="compass.external_profile" class="ac-fields">
          <div v-if="compass.external_profile.流派 || compass.external_profile.年代" class="ac-field">
            <span class="ac-field-key">文学坐标</span>
            <span class="ac-field-value">{{ joinNonEmpty([compass.external_profile.流派, compass.external_profile.年代, compass.external_profile.文化背景]) }}</span>
          </div>
          <div v-if="listOrEmpty(compass.external_profile.主题偏好).length" class="ac-field">
            <span class="ac-field-key">主题偏好</span>
            <div class="ac-chips">
              <span
                v-for="(item, i) in listOrEmpty(compass.external_profile.主题偏好)"
                :key="`tm-${i}`"
                class="ac-chip"
              >{{ item }}</span>
            </div>
          </div>
          <div v-if="listOrEmpty(compass.external_profile.风格标签).length" class="ac-field">
            <span class="ac-field-key">风格标签</span>
            <div class="ac-chips">
              <span
                v-for="(item, i) in listOrEmpty(compass.external_profile.风格标签)"
                :key="`st-${i}`"
                class="ac-chip"
              >{{ item }}</span>
            </div>
          </div>
          <div v-if="listOrEmpty(compass.external_profile.代表手法).length" class="ac-field">
            <span class="ac-field-key">代表手法</span>
            <div class="ac-chips">
              <span
                v-for="(item, i) in listOrEmpty(compass.external_profile.代表手法)"
                :key="`mh-${i}`"
                class="ac-chip"
              >{{ item }}</span>
            </div>
          </div>
          <div v-if="listOrEmpty(compass.external_profile.雷区).length" class="ac-field">
            <span class="ac-field-key ac-field-key--danger">雷区</span>
            <div class="ac-chips">
              <span
                v-for="(item, i) in listOrEmpty(compass.external_profile.雷区)"
                :key="`lz-${i}`"
                class="ac-chip ac-chip--danger"
              >{{ item }}</span>
            </div>
          </div>
        </div>
        <div v-else class="ac-card-empty">尚未生成 — 点击「开始分析」</div>
      </div>

      <!-- 内部反推 — 初始态隐藏(无上传作品,反推必失败) -->
      <div
        v-if="!props.isInitialMode"
        class="ac-card"
        :data-status="compass.internal_status"
      >
        <header class="ac-card-header">
          <h3 class="ac-card-title">内部反推(原作文风量化)</h3>
          <span class="ac-card-status">{{ statusLabel(compass.internal_status) }}</span>
        </header>
        <div v-if="compass.internal_status === 'failed'" class="ac-card-error">
          {{ compass.internal_error || "内部反推失败" }}
        </div>
        <div v-else-if="compass.internal_metrics" class="ac-fields">
          <div v-if="compass.internal_metrics.句长" class="ac-field">
            <span class="ac-field-key">句长分布</span>
            <span class="ac-field-value">
              短 {{ percent(compass.internal_metrics.句长.短句占比) }} ·
              中 {{ percent(compass.internal_metrics.句长.中句占比) }} ·
              长 {{ percent(compass.internal_metrics.句长.长句占比) }}
            </span>
          </div>
          <div v-if="compass.internal_metrics.对白率" class="ac-field">
            <span class="ac-field-key">对白率</span>
            <span class="ac-field-value">{{ percent(compass.internal_metrics.对白率.比例) }}</span>
          </div>
          <div v-if="compass.internal_metrics.感官比例" class="ac-field">
            <span class="ac-field-key">感官主导</span>
            <span class="ac-field-value">
              <template v-for="key in ['视觉', '听觉', '嗅觉', '触觉', '味觉']" :key="key">
                <template v-if="getSenseValue(key) >= 0.15">
                  {{ key }} {{ percent(getSenseValue(key)) }}
                  <span class="ac-dot">·</span>
                </template>
              </template>
            </span>
          </div>
          <div v-if="listOrEmpty(compass.internal_metrics.意象偏好).length" class="ac-field">
            <span class="ac-field-key ac-field-key--star">推荐意象</span>
            <div class="ac-chips">
              <span
                v-for="(item, i) in listOrEmpty(compass.internal_metrics.意象偏好)"
                :key="`im-${i}`"
                class="ac-chip ac-chip--star"
              >{{ item }}</span>
            </div>
          </div>
          <div v-if="compass.internal_metrics.基调" class="ac-field">
            <span class="ac-field-key">整体基调</span>
            <span class="ac-field-value">
              {{ joinNonEmpty([compass.internal_metrics.基调.情感色彩, compass.internal_metrics.基调.节奏感]) }}
            </span>
          </div>
          <!-- P4(2026-05-27):身体描写尺度 — 决定续作是否保留原作肉体感风骨 -->
          <div v-if="compass.internal_metrics.身体描写尺度" class="ac-field ac-field--body">
            <span class="ac-field-key">身体描写尺度</span>
            <span class="ac-field-value">
              <span class="ac-body-pill">{{ formatBodyFrequency(compass.internal_metrics.身体描写尺度.频率) }}</span>
              <span class="ac-body-pill">{{ formatBodyExplicit(compass.internal_metrics.身体描写尺度.直白度) }}</span>
              <span v-if="compass.internal_metrics.身体描写尺度.评注" class="ac-body-note">
                {{ compass.internal_metrics.身体描写尺度.评注 }}
              </span>
            </span>
          </div>
        </div>
        <div v-else class="ac-card-empty">尚未生成 — 需先上传原作并点击「开始分析」</div>
      </div>
    </div>

    <!-- ====== 锁定 / 解锁 ====== -->
    <div v-if="compass" class="ac-lock-section">
      <p class="ac-lock-hint">
        锁定后,后续续作生成将固定使用此指南针,不会被 LLM 重新解读。
        <span v-if="compass.user_locked" class="ac-lock-state">当前:已锁定</span>
        <span v-else class="ac-lock-state ac-lock-state--unlocked">当前:未锁定</span>
      </p>
      <button
        v-if="!compass.user_locked && canLock"
        type="button"
        class="ac-btn ac-btn--lock"
        @click="onLock"
      >
        锁定为最终版本
      </button>
      <button
        v-else-if="compass.user_locked"
        type="button"
        class="ac-btn ac-btn--unlock"
        @click="onUnlock"
      >
        解锁
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";

import { toast } from "../composables/useToast";
import { useAuthorCompass } from "../composables/useAuthorCompass";
import type { AuthorCompassStatus } from "../api/types";

const props = defineProps<{
  projectId: string;
  /** hotfix(2026-06-01):初始态无上传作品 → 隐藏"内部反推"卡 + 改文案 */
  isInitialMode?: boolean;
}>();

const compassApi = useAuthorCompass(props.projectId);
const { phase, compass, errorMessage, hasBothTracksDone, load, analyze, save } = compassApi;

const authorName = ref("");
const workTitle = ref("");

const isAnalyzing = ref(false);

const phaseLabel = computed(() => {
  switch (phase.value) {
    case "idle": return "未生成";
    case "loading": return "加载中...";
    case "running": return "调研中...";
    case "ready": return "已就绪";
    case "locked": return "已锁定";
    case "error": return "出错";
    default: return phase.value;
  }
});

// hotfix(2026-06-01):锁定条件 mode-aware
// - 非初始态:外部 + 内部双轨都 done(原有 hasBothTracksDone 逻辑)
// - 初始态:内部反推被隐藏(无上传作品),只要外部研究 done 即可锁定
const canLock = computed(() => {
  const c = compass.value;
  if (!c) return false;
  if (props.isInitialMode) {
    return c.external_status === "done";
  }
  return hasBothTracksDone.value;
});

function statusLabel(s: AuthorCompassStatus): string {
  switch (s) {
    case "pending": return "未跑";
    case "running": return "进行中";
    case "done": return "完成";
    case "failed": return "失败";
  }
}

function listOrEmpty(v: unknown): string[] {
  if (Array.isArray(v)) return v.filter((x): x is string => typeof x === "string" && x.trim().length > 0);
  return [];
}

function joinNonEmpty(values: unknown[]): string {
  return values.filter((v) => typeof v === "string" && v.trim()).join(" / ");
}

function percent(v: unknown): string {
  if (typeof v !== "number" || isNaN(v)) return "—";
  return `${Math.round(v * 100)}%`;
}

function getSenseValue(key: string): number {
  const sense = compass.value?.internal_metrics?.感官比例;
  if (!sense || typeof sense !== "object") return 0;
  const v = (sense as Record<string, unknown>)[key];
  return typeof v === "number" ? v : 0;
}

// P4(2026-05-27):身体描写尺度的英文枚举 → 中文友好标签
function formatBodyFrequency(v: unknown): string {
  const map: Record<string, string> = {
    none: "完全不写",
    rare: "极少",
    occasional: "偶尔",
    frequent: "频繁",
  };
  return (typeof v === "string" && map[v]) || "—";
}

function formatBodyExplicit(v: unknown): string {
  const map: Record<string, string> = {
    absent: "不涉",
    "clothed-only": "只写仪态",
    metaphorical: "比喻代替",
    "clinical-detached": "客观冷峻",
    "sensual-implicit": "感官隐晦",
    "direct-detailed": "直接细节",
  };
  return (typeof v === "string" && map[v]) || "—";
}

async function onAnalyze(): Promise<void> {
  if (!authorName.value.trim() && !workTitle.value.trim()) {
    toast.warning("请至少填写作家名或作品名");
    return;
  }
  isAnalyzing.value = true;
  try {
    await analyze({
      author_name: authorName.value.trim() || undefined,
      work_title: workTitle.value.trim() || undefined,
    });
    if (phase.value === "error") {
      toast.error(errorMessage.value || "分析失败");
    } else {
      toast.success("作者指南针分析完成");
    }
  } finally {
    isAnalyzing.value = false;
  }
}

async function onLock(): Promise<void> {
  // 合并双轨结果为 final_compass
  const final: Record<string, unknown> = {};
  if (compass.value?.external_profile) {
    final.external_profile = compass.value.external_profile;
  }
  if (compass.value?.internal_metrics) {
    final.internal_metrics = compass.value.internal_metrics;
  }
  try {
    await save({ final_compass: final, user_locked: true });
    toast.success("已锁定 — 续作将固定使用此指南针");
  } catch {
    toast.error("锁定失败,请稍后再试");
  }
}

async function onUnlock(): Promise<void> {
  try {
    await save({ user_locked: false });
    toast.success("已解锁");
  } catch {
    toast.error("解锁失败,请稍后再试");
  }
}

// 同步 compass.author_name / work_title 到 input
watch(compass, (c) => {
  if (c) {
    authorName.value = c.author_name ?? "";
    workTitle.value = c.work_title ?? "";
  }
}, { immediate: true });

onMounted(() => {
  void load();
});
</script>

<style scoped>
.ac-panel {
  display: flex;
  flex-direction: column;
  gap: 20px;
  padding: 24px;
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: 12px;
}

/* Header */
.ac-header {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.ac-title-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.ac-title {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text, #1f2937);
}
.ac-status-chip {
  padding: 2px 10px;
  border-radius: 999px;
  font-size: var(--text-xs);
  font-weight: 500;
  background: var(--color-bg-muted, #f3f4f6);
  color: var(--color-text-muted, #6b7280);
}
.ac-status-chip[data-phase="running"] {
  background: var(--color-accent-soft, #ede9fe);
  color: var(--color-accent, #7c3aed);
}
.ac-status-chip[data-phase="ready"] {
  background: #d1fae5;
  color: #065f46;
}
.ac-status-chip[data-phase="locked"] {
  background: #fef3c7;
  color: #92400e;
}
.ac-status-chip[data-phase="error"] {
  background: #fee2e2;
  color: #991b1b;
}
.ac-subtitle {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--color-text-muted, #6b7280);
  line-height: 1.5;
}
/* hotfix(2026-06-01):初始态隐藏内部反推说明 */
.ac-initial-hint {
  display: inline-block;
  margin-top: 4px;
  font-size: var(--text-xs);
  color: var(--color-text-subtle, #9ca3af);
  font-style: italic;
}

/* 输入区 */
.ac-input-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.ac-input-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}
.ac-label {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ac-label-text {
  font-size: var(--text-xs);
  color: var(--color-text-muted, #6b7280);
}
.ac-input {
  padding: 8px 10px;
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: 6px;
  font-size: var(--text-base);
  background: var(--color-surface, #fff);
  color: var(--color-text, #1f2937);
}
.ac-input:focus {
  outline: 2px solid var(--color-accent-soft, #ede9fe);
  border-color: var(--color-accent, #7c3aed);
}
.ac-input:disabled {
  background: var(--color-bg-muted, #f3f4f6);
  color: var(--color-text-muted, #6b7280);
}

.ac-action-row {
  display: flex;
  gap: 12px;
  align-items: center;
}
.ac-btn {
  padding: 8px 18px;
  border-radius: 6px;
  font-size: var(--text-base);
  font-weight: 500;
  border: 1px solid var(--color-border, #e5e7eb);
  background: var(--color-surface, #fff);
  color: var(--color-text, #1f2937);
  cursor: pointer;
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.ac-btn:hover:not(:disabled) {
  border-color: var(--color-accent, #7c3aed);
  color: var(--color-accent, #7c3aed);
}
.ac-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
.ac-btn--primary {
  background: var(--color-accent, #7c3aed);
  color: white;
  border-color: var(--color-accent, #7c3aed);
}
.ac-btn--primary:hover:not(:disabled) {
  background: var(--color-accent-hover, #6d28d9);
  color: white;
}
.ac-btn--lock {
  background: #fef3c7;
  color: #92400e;
  border-color: #fcd34d;
}
.ac-btn--lock:hover:not(:disabled) {
  background: #fde68a;
  color: #92400e;
  border-color: #f59e0b;
}
.ac-btn--unlock {
  background: var(--color-bg-muted, #f3f4f6);
}

.ac-spinner {
  width: 14px;
  height: 14px;
  border: 2px solid transparent;
  border-top-color: currentColor;
  border-radius: 50%;
  animation: ac-spin 0.8s linear infinite;
}
@keyframes ac-spin {
  to { transform: rotate(360deg); }
}

.ac-locked-hint {
  font-size: var(--text-sm);
  color: var(--color-text-muted, #6b7280);
}

/* 错误条 */
.ac-error-banner {
  padding: 10px 12px;
  background: #fee2e2;
  border: 1px solid #fecaca;
  border-radius: 6px;
  color: #991b1b;
  font-size: var(--text-sm);
}

/* 双轨卡片 */
.ac-tracks {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
@media (max-width: 760px) {
  .ac-tracks { grid-template-columns: 1fr; }
  .ac-input-row { grid-template-columns: 1fr; }
}
.ac-card {
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px;
  background: var(--color-bg-muted, #f9fafb);
  border: 1px solid var(--color-border, #e5e7eb);
  border-radius: 8px;
}
.ac-card[data-status="failed"] {
  background: #fef2f2;
  border-color: #fecaca;
}
.ac-card-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
}
.ac-card-title {
  margin: 0;
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--color-text, #1f2937);
}
.ac-card-status {
  font-size: var(--text-xs);
  color: var(--color-text-muted, #6b7280);
}
.ac-card-error {
  font-size: var(--text-sm);
  color: #991b1b;
  padding: 8px;
  background: #fee2e2;
  border-radius: 4px;
}
.ac-card-empty {
  font-size: var(--text-sm);
  color: var(--color-text-muted, #6b7280);
  padding: 20px;
  text-align: center;
  font-style: italic;
}

/* 字段列表 */
.ac-fields {
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.ac-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.ac-field-key {
  font-size: 11px;
  color: var(--color-text-muted, #6b7280);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.ac-field-key--danger {
  color: #991b1b;
}
.ac-field-key--star {
  color: #b45309;
}
.ac-field-value {
  font-size: var(--text-sm);
  color: var(--color-text, #1f2937);
  line-height: 1.5;
}
.ac-dot {
  color: var(--color-text-muted, #9ca3af);
  margin: 0 2px;
}

.ac-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.ac-chip {
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #e5e7eb);
  font-size: var(--text-xs);
  color: var(--color-text, #1f2937);
}
.ac-chip--danger {
  background: #fef2f2;
  border-color: #fecaca;
  color: #991b1b;
}
.ac-chip--star {
  background: #fffbeb;
  border-color: #fde68a;
  color: #92400e;
}

/* P4(2026-05-27):身体描写尺度的内嵌 pill + 评注 */
.ac-field--body .ac-field-value {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
  align-items: center;
}
.ac-body-pill {
  padding: 2px 8px;
  border-radius: var(--radius-md);
  background: var(--color-surface, #fff);
  border: 1px solid var(--color-border, #e5e7eb);
  font-size: var(--text-xs);
  color: var(--color-text-muted, #6b7280);
}
.ac-body-note {
  font-size: var(--text-xs);
  color: var(--color-text-muted, #6b7280);
  font-style: italic;
}

/* 锁定区 */
.ac-lock-section {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding-top: 12px;
  border-top: 1px solid var(--color-border, #e5e7eb);
  gap: 12px;
  flex-wrap: wrap;
}
.ac-lock-hint {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--color-text-muted, #6b7280);
  line-height: 1.5;
}
.ac-lock-state {
  margin-left: 8px;
  font-weight: 500;
  color: #92400e;
}
.ac-lock-state--unlocked {
  color: var(--color-text, #1f2937);
}
</style>
