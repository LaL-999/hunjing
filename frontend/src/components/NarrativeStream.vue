<script setup lang="ts">
/**
 * NarrativeStream — 推演产物的阅读体验组件(Sprint 1.I)。
 *
 * Props:
 *   narrative      composer 产物 markdown(≈ 4000 字)
 *   projectName    项目名,显示在标题
 *   reshapePercent done 屏 meta 行展示
 *   roundsPlanned  meta 行展示
 *   costYuan       meta 行展示
 *   autoStream     true 时自动开始打字渐显;false 时立即全显(默认 true)
 *
 * Emits:
 *   close          关闭按钮
 *
 * UX:
 *   - 打字效果:setInterval 每 ~44ms 渐显 10-14 字,~20s 完整呈现 4000 字
 *   - 跳过按钮:streaming 中点击立即显完整
 *   - 末尾光标:streaming 中闪烁竖线表"正在写"
 *   - 完成后底部出现导出区:复制 / 下载 md
 *   - markdown 简单解析:# / ## / **bold** / 段落首行缩进 2em
 *
 * 跨作品复用:本组件与具体业务无关,只接 narrative + meta props。
 * 中间态 / 周期态产物若复用 markdown 格式,可直接挂载本组件。
 */
import { computed, onBeforeUnmount, ref, watch } from "vue";

const props = withDefaults(
  defineProps<{
    narrative: string;
    projectName: string;
    reshapePercent: number;
    roundsPlanned: number;
    costYuan: number;
    autoStream?: boolean;
    /** Sprint 6.A2 M3.D-fix4(2026-05-19):用户配置的目标字数 —
     *  传入后底部 meta 行展示"实际 / 目标"并算偏差。0 / undefined 不展示。 */
    targetChars?: number;
    /** Sprint 6.A2(2026-05-22):是否显示"在线阅读"按钮替代"关闭"。
     *  SimulationDetailView 场景设 true(顶部已有"返回项目"按钮,"关闭"冗余 →
     *  改为进沉浸式阅读器);SimulationDock 等场景默认 false 保留"关闭"语义 */
    showReadButton?: boolean;
  }>(),
  {
    autoStream: true,
    targetChars: 0,
    showReadButton: false,
  },
);

defineEmits<{
  (e: "close"): void;
  (e: "enter-reader"): void;
}>();

// ============================================================
// 元数据行解析(Sprint 1.Q)— composer.md 输出格式:
//   首行:`> 语体:<语体名> · <来源>`
//   第二行起:正文 Markdown
// 解析后:meta 行不进打字渐显的 displayedText,从 footer meta 行展示
// ============================================================

interface NarrativeMeta {
  styleLabel: string;   // 例:"现代都市言情" / "古典含蓄" / "赛博朋克冷峻"
  source: string;       // 例:"由 AI 根据角色 + 题材自适应" / "由用户自定义"
}

/** 把 narrative 拆为 (meta, body)。无元数据行 → meta=null,body=原文。 */
function parseMeta(raw: string): { meta: NarrativeMeta | null; body: string } {
  if (!raw) return { meta: null, body: "" };
  const firstNewline = raw.indexOf("\n");
  if (firstNewline === -1) return { meta: null, body: raw };
  const firstLine = raw.slice(0, firstNewline).trim();
  // 形如:> 语体:现代都市言情 · 由 AI 根据角色 + 题材自适应
  const m = firstLine.match(/^>\s*语体[::]\s*([^·]+?)\s*[··]\s*(.+)$/);
  if (!m) return { meta: null, body: raw };
  return {
    meta: { styleLabel: m[1].trim(), source: m[2].trim() },
    body: raw.slice(firstNewline + 1).replace(/^\n+/, ""),
  };
}

const parsed = computed(() => parseMeta(props.narrative));
const narrativeBody = computed(() => parsed.value.body);
const narrativeMeta = computed<NarrativeMeta | null>(() => parsed.value.meta);

// ============================================================
// 打字渐显状态(基于 narrativeBody,不含元数据行)
// ============================================================

/** 当前已显示的字符数(0 → narrativeBody.length) */
const displayedLen = ref(0);
const isStreaming = ref(false);
let streamTimer: ReturnType<typeof setInterval> | null = null;

const displayedText = computed(() => narrativeBody.value.slice(0, displayedLen.value));

/** 中文字符计数(参照老版,小说体重要 metric) */
const totalCnChars = computed(
  () => (narrativeBody.value.match(/[一-鿿]/g) || []).length,
);
const displayedCnChars = computed(
  () => (displayedText.value.match(/[一-鿿]/g) || []).length,
);

// Sprint 6.A2 M3.D-fix4(2026-05-19):用户目标 vs 实际偏差
// 灵魂续写 / 快速续写都是 LLM 创作过程,平台不硬截断 — 实际 ±20% 内属正常,
// 超过则用 chip 提示"略短于 / 略长于目标"。
const targetCharsValid = computed(
  () => props.targetChars > 0,
);
const charsDeltaInfo = computed(() => {
  if (!targetCharsValid.value || totalCnChars.value === 0) return null;
  const actual = totalCnChars.value;
  const target = props.targetChars;
  const diff = actual - target;
  const ratio = diff / target;
  if (Math.abs(ratio) <= 0.15) {
    return { tone: "ok", label: "贴近目标" };
  }
  if (ratio > 0) {
    return { tone: "over", label: `略长 +${Math.round(ratio * 100)}%` };
  }
  return { tone: "under", label: `略短 ${Math.round(ratio * 100)}%` };
});

function stopStream() {
  if (streamTimer !== null) {
    clearInterval(streamTimer);
    streamTimer = null;
  }
  isStreaming.value = false;
}

function startStream() {
  stopStream();
  if (!narrativeBody.value) return;
  displayedLen.value = 0;
  isStreaming.value = true;

  // 速率参数:~44ms × 10-14 字 ≈ 20s 全部呈现 4000 字
  // 段落 \n\n 处暂停 80ms 让"换段呼吸"
  let pauseUntilMs = 0;
  streamTimer = setInterval(() => {
    if (performance.now() < pauseUntilMs) return;
    if (displayedLen.value >= narrativeBody.value.length) {
      stopStream();
      return;
    }
    const chunk = 10 + Math.floor(Math.random() * 5);
    const next = displayedLen.value + chunk;
    const newSlice = narrativeBody.value.slice(displayedLen.value, next);
    displayedLen.value = Math.min(next, narrativeBody.value.length);
    if (newSlice.includes("\n\n")) {
      pauseUntilMs = performance.now() + 80;
    }
  }, 44);
}

function skipToEnd() {
  stopStream();
  displayedLen.value = narrativeBody.value.length;
}

// 挂载 / props 变化时启动(autoStream=false 直接全显)
watch(
  () => props.narrative,
  (md) => {
    if (!md) {
      stopStream();
      displayedLen.value = 0;
      return;
    }
    if (props.autoStream) {
      startStream();
    } else {
      stopStream();
      displayedLen.value = narrativeBody.value.length;
    }
  },
  { immediate: true },
);

onBeforeUnmount(stopStream);

// ============================================================
// markdown 渲染(简单版,#  ## **bold** + 段落)
// ============================================================

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** 段落内 **bold** → <strong>,其它字符 escape */
function renderInline(s: string): string {
  // 把 **xxx** 替成 <strong>xxx</strong>;先 escape,再做替换避免 escape 把标签自己也吃了
  const escaped = escapeHtml(s);
  return escaped.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
}

const renderedHtml = computed(() => {
  const lines = displayedText.value.split("\n");
  const out: string[] = [];
  for (const line of lines) {
    if (line.startsWith("# ")) {
      out.push(`<h1 class="md-h1">${renderInline(line.slice(2))}</h1>`);
    } else if (line.startsWith("## ")) {
      out.push(`<h2 class="md-h2">${renderInline(line.slice(3))}</h2>`);
    } else if (line.startsWith("### ")) {
      out.push(`<h3 class="md-h3">${renderInline(line.slice(4))}</h3>`);
    } else if (line.trim() === "") {
      out.push(`<div class="md-spacer"></div>`);
    } else {
      out.push(`<p class="md-p">${renderInline(line)}</p>`);
    }
  }
  // 末尾光标(streaming 时)
  if (isStreaming.value) {
    out.push(`<span class="md-caret"></span>`);
  }
  return out.join("");
});

// ============================================================
// 阅读容器自动滚到底部(streaming 时)
// ============================================================

const bodyEl = ref<HTMLDivElement | null>(null);
watch(displayedLen, () => {
  if (!isStreaming.value) return;
  requestAnimationFrame(() => {
    const el = bodyEl.value;
    if (el) el.scrollTop = el.scrollHeight;
  });
});

// ============================================================
// 导出 — 复制 + 下载 markdown
// ============================================================

const copyState = ref<"idle" | "ok" | "fail">("idle");

async function handleCopy() {
  try {
    // 复制纯正文(不带元数据 `> 语体:...` 行)— 用户分享出去更干净
    await navigator.clipboard.writeText(narrativeBody.value);
    copyState.value = "ok";
    setTimeout(() => (copyState.value = "idle"), 1800);
  } catch {
    copyState.value = "fail";
    setTimeout(() => (copyState.value = "idle"), 2400);
  }
}

const copyLabel = computed(() => {
  if (copyState.value === "ok") return "已复制 ✓";
  if (copyState.value === "fail") return "复制失败,请手动选";
  return "复制全文";
});

function safeFilename(s: string): string {
  // 保留中文,去掉 Windows 文件名禁止字符
  return s.replace(/[\\/:*?"<>|]/g, "_").trim() || "推演";
}

function handleDownloadMd() {
  const stamp = new Date()
    .toISOString()
    .slice(0, 19)
    .replace(/[-:T]/g, "")
    .slice(0, 13);   // YYYYMMDD_HHMM
  const filename = `${safeFilename(props.projectName)}_${stamp}.md`;
  // 下载纯正文(不带元数据)— 与复制行为一致
  const blob = new Blob([narrativeBody.value], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}
</script>

<template>
  <article class="narrative" role="article" aria-label="推演产物">
    <header class="narr-header">
      <h2 class="narr-title">《{{ projectName }}》</h2>
    </header>

    <!-- D.5 a11y:streaming 内容用 aria-live="polite" + aria-busy 让屏幕阅读器
         在打字渐显时读出新增片段(polite = 等用户当前任务结束后读,不打断);
         非 streaming(autoStream=false 立即全显)时 aria-busy=false 让 SR 直接全读。 -->
    <div
      ref="bodyEl"
      class="narr-body"
      aria-live="polite"
      :aria-busy="isStreaming ? 'true' : 'false'"
    >
      <div class="md-content" v-html="renderedHtml"></div>
    </div>

    <footer class="narr-footer">
      <!-- 语体行(Sprint 1.Q):composer 元数据;旧产物没元数据时不显 -->
      <p
        v-if="narrativeMeta"
        class="style-line"
        :title="narrativeMeta.source"
      >
        ✦ 语体:<strong>{{ narrativeMeta.styleLabel }}</strong>
        <span class="style-source">· {{ narrativeMeta.source }}</span>
      </p>

      <div class="meta-row">
        <span class="meta-item">
          <span class="meta-num mono">{{ displayedCnChars }}</span>
          <span class="meta-unit">/ {{ totalCnChars }} 中文字</span>
        </span>
        <!-- Sprint 6.A2 M3.D-fix4(2026-05-19):目标字数对比 chip(传入 targetChars 时显) -->
        <template v-if="targetCharsValid && charsDeltaInfo">
          <span class="meta-sep">·</span>
          <span class="meta-item">
            <span class="meta-unit">目标</span>
            <span class="meta-num mono">{{ targetChars.toLocaleString() }}</span>
            <span :class="['chars-delta-chip', `delta-${charsDeltaInfo.tone}`]">
              {{ charsDeltaInfo.label }}
            </span>
          </span>
        </template>
        <span class="meta-sep">·</span>
        <span class="meta-item">
          <span class="meta-num mono">{{ roundsPlanned }}</span>
          <span class="meta-unit">轮</span>
        </span>
        <span class="meta-sep">·</span>
        <span class="meta-item">
          <span class="meta-num mono">{{ reshapePercent }}%</span>
          <span class="meta-unit">重塑度</span>
        </span>
        <span class="meta-sep">·</span>
        <span class="meta-item">
          <span class="meta-unit">¥</span>
          <span class="meta-num mono">{{ costYuan.toFixed(4) }}</span>
        </span>
      </div>

      <div class="actions">
        <button v-if="isStreaming" class="ghost-btn" @click="skipToEnd">
          跳过流式 · 直接看完
        </button>
        <template v-else>
          <button
            class="ghost-btn"
            :class="{ 'ok-flash': copyState === 'ok' }"
            @click="handleCopy"
          >{{ copyLabel }}</button>
          <button class="ghost-btn" @click="handleDownloadMd">
            下载 Markdown
          </button>
          <!-- Sprint 6.A2(2026-05-22):detail view 场景换"在线阅读"入沉浸式阅读器;
               dock 场景仍走"关闭"语义 -->
          <button
            v-if="showReadButton"
            class="primary-btn"
            @click="$emit('enter-reader')"
          >📖 在线阅读</button>
          <button
            v-else
            class="primary-btn"
            @click="$emit('close')"
          >关闭</button>
        </template>
      </div>
    </footer>
  </article>
</template>

<style scoped>
.narrative {
  display: flex;
  flex-direction: column;
  width: 100%;
  /* 阅读容器自身不带 max-width — 由父容器(modal-card)限宽 */
}

/* ===== Header ===== */
.narr-header {
  text-align: center;
  margin-bottom: var(--space-4);
}
.narr-title {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--color-text);
  margin-bottom: var(--space-1);
}
.narr-subtitle {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-style: italic;
}

/* ===== Body ===== */
.narr-body {
  flex: 1;
  max-height: 480px;
  overflow-y: auto;
  padding: var(--space-5) var(--space-6);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  margin-bottom: var(--space-4);
}

/* 内容排版 — 中文小说体首选:正文 serif,标题略大,行距宽,段落首行缩进 */
.md-content {
  font-family: "Noto Serif SC", "Source Han Serif SC", Georgia, "Cambria", serif;
  font-size: var(--text-base);
  line-height: 1.95;
  color: var(--color-text);
  letter-spacing: 0.01em;
}

.md-content :deep(.md-h1) {
  font-family: inherit;
  font-size: var(--text-xl);
  font-weight: 600;
  margin: 0 0 var(--space-4) 0;
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
  text-align: center;
  letter-spacing: 0.04em;
  color: var(--color-text);
}

.md-content :deep(.md-h2) {
  font-family: inherit;
  font-size: var(--text-lg);
  font-weight: 600;
  margin: var(--space-5) 0 var(--space-3) 0;
  color: var(--color-accent-text);
  letter-spacing: 0.04em;
}

.md-content :deep(.md-h3) {
  font-family: inherit;
  font-size: var(--text-base);
  font-weight: 600;
  margin: var(--space-4) 0 var(--space-2) 0;
  color: var(--color-text);
}

.md-content :deep(.md-p) {
  margin: 0 0 var(--space-3) 0;
  text-indent: 2em;        /* 中文小说体段首缩进 */
  word-break: break-word;
}

.md-content :deep(strong) {
  color: var(--color-accent-text);
  font-weight: 600;
}

.md-content :deep(.md-spacer) {
  height: var(--space-2);
}

/* 流式光标:闪烁竖线 */
.md-content :deep(.md-caret) {
  display: inline-block;
  width: 2px;
  height: 1.05em;
  vertical-align: text-bottom;
  background: var(--color-accent);
  margin-left: 2px;
  animation: caret-blink 0.9s steps(2, end) infinite;
}

@keyframes caret-blink {
  0%, 49% { opacity: 1; }
  50%, 100% { opacity: 0; }
}

/* ===== Footer ===== */
.narr-footer {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}

/* 语体行(Sprint 1.Q):composer 自评估出的语体名称,让用户事后看到"AI 选了什么风" */
.style-line {
  text-align: center;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin-bottom: var(--space-2);
  cursor: help;
}
.style-line strong {
  color: var(--color-accent-text);
  font-weight: 600;
}
.style-source {
  color: var(--color-text-subtle);
  font-size: var(--text-xs);
  margin-left: 4px;
}

.meta-row {
  display: flex;
  flex-wrap: wrap;
  justify-content: center;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}
.meta-item {
  display: inline-flex;
  align-items: baseline;
  gap: 2px;
}
.meta-num {
  color: var(--color-text);
  font-weight: 500;
}
.meta-unit {
  color: var(--color-text-subtle);
  font-size: var(--text-xs);
}
.meta-sep {
  color: var(--color-text-subtle);
}

/* Sprint 6.A2 M3.D-fix4(2026-05-19):目标字数偏差 chip */
.chars-delta-chip {
  margin-left: 6px;
  padding: 1px 6px;
  font-size: 11px;
  border-radius: var(--radius-sm);
  font-weight: 500;
}
.chars-delta-chip.delta-ok {
  background: rgba(16, 185, 129, 0.1);
  color: rgb(5, 122, 85);
}
.chars-delta-chip.delta-over {
  background: rgba(245, 158, 11, 0.12);
  color: rgb(180, 83, 9);
}
.chars-delta-chip.delta-under {
  background: rgba(124, 58, 237, 0.1);
  color: var(--color-accent-text);
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  flex-wrap: wrap;
}

.ghost-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition: all var(--duration-fast) var(--ease-out);
}
.ghost-btn:hover:not(:disabled) {
  color: var(--color-text);
  background: var(--color-surface-hover);
  border-color: var(--color-border-strong);
}
.ghost-btn.ok-flash {
  color: var(--color-accent-text);
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}

.primary-btn {
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) var(--ease-out);
}
.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.mono {
  font-family: var(--font-mono);
}
</style>
