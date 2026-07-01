<script setup lang="ts">
/**
 * PlazaReadView — 作品广场沉浸式在线阅读器。
 * v5(2026-07-02)item9 重设计:从"一长条竖排滚动"升级为全屏横向翻页阅读器
 * (对齐 SimulationReadView 质感)—— 返回广场按钮固定在视口左上角,不再淹没在正文里。
 *
 * 交互:
 *   键盘:← / PgUp 上一页 · → / PgDn / Space 下一页 · Esc 返回广场 ·
 *         + - 字号 · F 单/双页 · T 主题
 *   鼠标:点屏左 1/3 上一页 · 右 1/3 下一页 · 中 1/3 留给选中
 *   触摸:横滑翻页(|Δx| > 60px)
 *
 * 技术:CSS Multi-column 分栏 + scrollLeft 翻页(无 paginate 依赖,复刻推演阅读器)。
 * 正文渲染无 markdown 依赖(架构冻结闸门):按空行拆段 + # 识别标题(标题起新页)。
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { ApiError } from "../api/client";
import { plazaApi, type PlazaWork } from "../api/plaza";
import { useAuthStore } from "../stores/auth";
import { useLoginModal } from "../composables/useLoginModal";
import { toast } from "../composables/useToast";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const loginModal = useLoginModal();

const workId = computed(() => String(route.params.id ?? ""));

const MODE_LABELS: Record<string, string> = {
  initial: "初始态", middle: "中间态", end: "末尾态", cycle: "漫创态", screenplay: "剧创态",
};
function modeLabel(mode: string): string { return MODE_LABELS[mode] ?? "创作"; }

// ============================================================
// 阅读偏好(localStorage 持久化,与推演阅读器共用 key)
// ============================================================
const STORAGE_KEY = "huimeng:reader-prefs";
type ReaderTheme = "light" | "sepia" | "dark";
type ReaderSpread = "single" | "double";
interface ReaderPrefs { fontSize: number; spread: ReaderSpread; theme: ReaderTheme }
const FONT_SIZES = [16, 18, 22] as const;
const DEFAULT_PREFS: ReaderPrefs = { fontSize: 18, spread: "double", theme: "sepia" };

function loadPrefs(): ReaderPrefs {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_PREFS };
    const p = JSON.parse(raw) as Partial<ReaderPrefs>;
    return {
      fontSize: (FONT_SIZES as readonly number[]).includes(p.fontSize as number)
        ? (p.fontSize as number) : DEFAULT_PREFS.fontSize,
      spread: p.spread === "single" || p.spread === "double" ? p.spread : DEFAULT_PREFS.spread,
      theme: p.theme === "light" || p.theme === "sepia" || p.theme === "dark"
        ? p.theme : DEFAULT_PREFS.theme,
    };
  } catch { return { ...DEFAULT_PREFS }; }
}
const prefs = ref<ReaderPrefs>(loadPrefs());
watch(prefs, (p) => {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(p)); } catch { /* 忽略 */ }
}, { deep: true });

// ============================================================
// 数据加载
// ============================================================
const work = ref<PlazaWork | null>(null);
const loading = ref(true);
const errorMsg = ref<string | null>(null);
const likeBusy = ref(false);

async function load(): Promise<void> {
  loading.value = true;
  errorMsg.value = null;
  try {
    work.value = await plazaApi.read(workId.value);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) errorMsg.value = "作品不存在或已下架";
    else errorMsg.value = e instanceof ApiError ? e.message : "加载作品失败";
  } finally {
    loading.value = false;
  }
}

// ============================================================
// 内容拆块(# → 标题起新页;其余 → 段落)
// ============================================================
type RenderItem = { type: "h"; text: string } | { type: "p"; text: string };
const items = computed<RenderItem[]>(() => {
  const raw = work.value?.content ?? "";
  return raw
    .split(/\n{2,}/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s): RenderItem => {
      const m = s.match(/^#{1,6}\s+(.*)$/);
      if (m) return { type: "h", text: m[1].trim() };
      return { type: "p", text: s.replace(/\n/g, " ") };
    });
});
const isEmpty = computed(() => items.value.length === 0);

const charCount = computed(
  () => work.value?.word_count ?? (work.value?.content?.match(/[一-鿿]/g) || []).length,
);

// ============================================================
// 翻页(CSS column + scrollLeft)—— 复刻 SimulationReadView
// ============================================================
const pagerEl = ref<HTMLElement | null>(null);
const currentPage = ref(1);
const totalPages = ref(1);

function recalcPagination() {
  const el = pagerEl.value;
  if (!el) return;
  const pageWidth = el.clientWidth;
  if (pageWidth <= 0) return;
  const total = Math.max(1, Math.round(el.scrollWidth / pageWidth));
  totalPages.value = total;
  currentPage.value = Math.min(total, Math.max(1, Math.round(el.scrollLeft / pageWidth) + 1));
}
function goToPage(page: number) {
  const el = pagerEl.value;
  if (!el) return;
  const clamped = Math.min(totalPages.value, Math.max(1, page));
  el.scrollTo({ left: (clamped - 1) * el.clientWidth, behavior: "smooth" });
}
function nextPage() { if (currentPage.value < totalPages.value) goToPage(currentPage.value + 1); }
function prevPage() { if (currentPage.value > 1) goToPage(currentPage.value - 1); }

const progressPct = computed(() =>
  totalPages.value <= 1 ? 100 : Math.round((currentPage.value / totalPages.value) * 100),
);

// ============================================================
// 字号 / 单双页 / 主题
// ============================================================
function adjustFontSize(delta: 1 | -1) {
  const idx = FONT_SIZES.indexOf(prefs.value.fontSize as typeof FONT_SIZES[number]);
  const ni = Math.min(FONT_SIZES.length - 1, Math.max(0, idx + delta));
  prefs.value = { ...prefs.value, fontSize: FONT_SIZES[ni] };
}
function toggleSpread() {
  prefs.value = { ...prefs.value, spread: prefs.value.spread === "double" ? "single" : "double" };
}
const THEME_ORDER: ReaderTheme[] = ["sepia", "light", "dark"];
function cycleTheme() {
  const idx = THEME_ORDER.indexOf(prefs.value.theme);
  prefs.value = { ...prefs.value, theme: THEME_ORDER[(idx + 1) % THEME_ORDER.length] };
}
const themeLabel = computed(() =>
  prefs.value.theme === "light" ? "浅" : prefs.value.theme === "sepia" ? "沙" : "暗",
);
const spreadLabel = computed(() => (prefs.value.spread === "double" ? "双页" : "单页"));
const fontSizeLabel = computed(() =>
  prefs.value.fontSize === 16 ? "小" : prefs.value.fontSize === 22 ? "大" : "中",
);

// ============================================================
// 返回广场
// ============================================================
function exitReader() { router.push("/plaza"); }

// ============================================================
// 点赞(plaza 专属)
// ============================================================
async function toggleLike(): Promise<void> {
  if (!work.value) return;
  if (!auth.isAuthed) { loginModal.open(`/plaza/works/${workId.value}`); return; }
  if (likeBusy.value) return;
  likeBusy.value = true;
  const next = !work.value.liked;
  work.value.liked = next;
  work.value.like_count = Math.max(0, work.value.like_count + (next ? 1 : -1));
  try {
    const res = await plazaApi.like(workId.value, next);
    work.value.liked = res.liked;
    work.value.like_count = res.like_count;
  } catch (e) {
    work.value.liked = !next;
    work.value.like_count = Math.max(0, work.value.like_count + (next ? -1 : 1));
    toast.error(e instanceof ApiError ? e.message : "操作失败");
  } finally {
    likeBusy.value = false;
  }
}

// ============================================================
// 键盘 / 鼠标 / 触摸
// ============================================================
function onGlobalKey(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
  if (e.key === "ArrowRight" || e.key === "PageDown" || e.key === " ") { e.preventDefault(); nextPage(); }
  else if (e.key === "ArrowLeft" || e.key === "PageUp") { e.preventDefault(); prevPage(); }
  else if (e.key === "Escape") exitReader();
  else if (e.key === "+" || e.key === "=") adjustFontSize(1);
  else if (e.key === "-" || e.key === "_") adjustFontSize(-1);
  else if (e.key === "f" || e.key === "F") toggleSpread();
  else if (e.key === "t" || e.key === "T") cycleTheme();
  else if (e.key === "Home") { e.preventDefault(); goToPage(1); }
  else if (e.key === "End") { e.preventDefault(); goToPage(totalPages.value); }
}
function onPagerClick(e: MouseEvent) {
  const el = pagerEl.value;
  if (!el) return;
  const sel = window.getSelection();
  if (sel && sel.toString().length > 0) return;
  const rect = el.getBoundingClientRect();
  const x = e.clientX - rect.left;
  if (x < rect.width / 3) prevPage();
  else if (x > (rect.width * 2) / 3) nextPage();
}
let _touchStartX: number | null = null;
function onTouchStart(e: TouchEvent) { _touchStartX = e.touches[0]?.clientX ?? null; }
function onTouchEnd(e: TouchEvent) {
  if (_touchStartX === null) return;
  const endX = e.changedTouches[0]?.clientX ?? _touchStartX;
  const dx = endX - _touchStartX;
  _touchStartX = null;
  if (Math.abs(dx) < 60) return;
  if (dx < 0) nextPage(); else prevPage();
}

// ============================================================
// 沉浸 chrome:鼠标 idle 2.5s 自动隐藏 toolbar
// ============================================================
const chromeVisible = ref(true);
let _idleTimer: ReturnType<typeof setTimeout> | null = null;
function pingChrome() {
  chromeVisible.value = true;
  if (_idleTimer !== null) clearTimeout(_idleTimer);
  _idleTimer = setTimeout(() => { chromeVisible.value = false; }, 2500);
}

// ============================================================
// 稳定布局后重算分页(等 DOM + 字体 + 双 rAF)
// ============================================================
async function waitForStableLayout() {
  await nextTick();
  const docFonts = (document as Document & { fonts?: FontFaceSet }).fonts;
  if (docFonts && typeof docFonts.ready?.then === "function") {
    try { await docFonts.ready; } catch { /* 兜底 */ }
  }
  await new Promise<void>((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}
async function recalcStable(resetScroll = false) {
  await waitForStableLayout();
  if (resetScroll && pagerEl.value) pagerEl.value.scrollLeft = 0;
  recalcPagination();
}
watch(loading, (isLoading) => {
  if (!isLoading && work.value && !isEmpty.value) void recalcStable(true);
});
watch(() => [prefs.value.fontSize, prefs.value.spread], () => { void recalcStable(true); });

let _resizeObs: ResizeObserver | null = null;
watch(pagerEl, (el, oldEl) => {
  if (_resizeObs) {
    if (oldEl) _resizeObs.unobserve(oldEl);
    _resizeObs.disconnect();
    _resizeObs = null;
  }
  if (el && typeof ResizeObserver !== "undefined") {
    _resizeObs = new ResizeObserver(() => recalcPagination());
    _resizeObs.observe(el);
  }
});

onMounted(() => {
  document.addEventListener("keydown", onGlobalKey);
  window.addEventListener("mousemove", pingChrome, { passive: true });
  pingChrome();
  void load();
});
onBeforeUnmount(() => {
  document.removeEventListener("keydown", onGlobalKey);
  window.removeEventListener("mousemove", pingChrome);
  if (_idleTimer !== null) clearTimeout(_idleTimer);
  if (_resizeObs) { _resizeObs.disconnect(); _resizeObs = null; }
});

function authorName(): string { return work.value?.author_nickname || "浑晶创作者"; }
function fmtCount(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}w`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
</script>

<template>
  <div
    class="reader"
    :data-theme="prefs.theme"
    :data-spread="prefs.spread"
    :class="{ 'is-chrome-hidden': !chromeVisible }"
  >
    <!-- 顶部 toolbar:返回广场固定在左上角 -->
    <header class="reader-toolbar reader-toolbar--top">
      <button class="tool-btn tool-btn--icon" @click="exitReader" title="返回广场 (Esc)">
        <span aria-hidden="true">←</span>
        <span>返回广场</span>
      </button>

      <h1 class="reader-title">
        <span v-if="work" class="title-mode">{{ modeLabel(work.mode) }}</span>
        <span class="title-text">{{ work?.title || "在线阅读" }}</span>
      </h1>

      <div class="reader-tools">
        <div class="tool-group">
          <button class="tool-btn" @click="adjustFontSize(-1)" title="缩小字号 (-)">A−</button>
          <span class="tool-label">{{ fontSizeLabel }}</span>
          <button class="tool-btn" @click="adjustFontSize(1)" title="放大字号 (+)">A+</button>
        </div>
        <span class="tool-sep" aria-hidden="true">·</span>
        <button class="tool-btn" @click="toggleSpread" title="单 / 双页切换 (F)">{{ spreadLabel }}</button>
        <span class="tool-sep" aria-hidden="true">·</span>
        <button class="tool-btn" @click="cycleTheme" title="主题切换 (T)">{{ themeLabel }}</button>
      </div>
    </header>

    <!-- loading / error / empty -->
    <div v-if="loading" class="reader-state" aria-busy="true">加载作品中…</div>
    <div v-else-if="errorMsg" class="reader-state reader-state--error">
      {{ errorMsg }}
      <button class="ghost-btn" @click="exitReader">返回广场</button>
    </div>
    <div v-else-if="isEmpty" class="reader-state reader-state--empty">
      <p>作品正文为空。</p>
      <button class="ghost-btn" @click="exitReader">返回广场</button>
    </div>

    <!-- 内容区(CSS column 分页) -->
    <main
      v-else
      ref="pagerEl"
      class="reader-pager"
      :style="{ '--reader-font-size': `${prefs.fontSize}px` }"
      @click="onPagerClick"
      @scroll.passive="recalcPagination"
      @touchstart.passive="onTouchStart"
      @touchend.passive="onTouchEnd"
    >
      <div class="reader-content">
        <!-- 卷首:标题 + 作者(作为第一页的题头)-->
        <div class="reader-frontmatter">
          <h2 class="fm-title">{{ work?.title }}</h2>
          <p class="fm-orig">
            <template v-if="work?.original_title">原著《{{ work.original_title }}》· 二创</template>
            <template v-else>原创世界</template>
          </p>
          <p class="fm-author">{{ authorName() }} · {{ modeLabel(work?.mode || "") }}</p>
        </div>

        <template v-for="(item, i) in items" :key="i">
          <h3 v-if="item.type === 'h'" class="reader-chapter-title">{{ item.text }}</h3>
          <p v-else class="reader-para">{{ item.text }}</p>
        </template>
      </div>

      <!-- 点击翻页提示 -->
      <div class="reader-tap-hint reader-tap-hint--left" aria-hidden="true">
        <span v-if="currentPage > 1" class="tap-arrow">‹</span>
      </div>
      <div class="reader-tap-hint reader-tap-hint--right" aria-hidden="true">
        <span v-if="currentPage < totalPages" class="tap-arrow">›</span>
      </div>
    </main>

    <!-- 底部 footer:翻页 + 进度 + 点赞 -->
    <footer v-if="!loading && !errorMsg && !isEmpty" class="reader-toolbar reader-toolbar--bottom">
      <button class="page-btn" :disabled="currentPage <= 1" @click="prevPage" aria-label="上一页">‹</button>
      <span class="page-meta">{{ currentPage }} / {{ totalPages }}</span>
      <div class="progress-track" role="progressbar"
        :aria-valuenow="progressPct" aria-valuemin="0" aria-valuemax="100">
        <div class="progress-fill" :style="{ width: `${progressPct}%` }"></div>
      </div>
      <button
        v-if="work"
        type="button"
        class="like-pill"
        :class="{ on: work.liked }"
        :disabled="likeBusy"
        @click="toggleLike"
      >
        <svg viewBox="0 0 24 24" width="15" height="15"
             :fill="work.liked ? 'currentColor' : 'none'"
             stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
          <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z" />
        </svg>
        <span>{{ fmtCount(work.like_count) }}</span>
      </button>
      <span class="char-count">{{ charCount.toLocaleString() }} 字</span>
      <button class="page-btn" :disabled="currentPage >= totalPages" @click="nextPage" aria-label="下一页">›</button>
    </footer>
  </div>
</template>

<style scoped>
.reader {
  position: fixed;
  inset: 0;
  z-index: 100;
  display: flex;
  flex-direction: column;
  background: var(--r-bg);
  color: var(--r-text);
  transition: background 220ms ease, color 220ms ease;
}
.reader[data-theme="sepia"] {
  --r-bg: #F4ECD8; --r-text: #3C3528;
  --r-chrome-bg: rgba(232, 223, 202, 0.92); --r-border: rgba(213, 201, 176, 0.6);
  --r-accent: #8B5A2B; --r-muted: rgba(60, 53, 40, 0.55);
}
.reader[data-theme="light"] {
  --r-bg: #FFFFFF; --r-text: #1F1F1E;
  --r-chrome-bg: rgba(244, 240, 232, 0.92); --r-border: rgba(229, 225, 216, 0.7);
  --r-accent: #7C3AED; --r-muted: rgba(107, 104, 98, 0.7);
}
.reader[data-theme="dark"] {
  --r-bg: #1A1A19; --r-text: #DCD8CF;
  --r-chrome-bg: rgba(38, 36, 31, 0.92); --r-border: rgba(56, 53, 48, 0.6);
  --r-accent: #A78BFA; --r-muted: rgba(220, 216, 207, 0.5);
}

.reader-toolbar {
  position: absolute;
  left: 0; right: 0;
  display: flex;
  align-items: center;
  gap: 16px;
  height: 52px;
  padding: 0 24px;
  background: var(--r-chrome-bg);
  border-color: var(--r-border);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  transition: transform 220ms ease, opacity 220ms ease;
  z-index: 2;
}
.reader-toolbar--top { top: 0; border-bottom: 1px solid var(--r-border); }
.reader-toolbar--bottom { bottom: 0; border-top: 1px solid var(--r-border); }
.reader.is-chrome-hidden .reader-toolbar--top {
  transform: translateY(-100%); opacity: 0; pointer-events: none;
}
.reader.is-chrome-hidden .reader-toolbar--bottom {
  transform: translateY(100%); opacity: 0; pointer-events: none;
}

.reader-title {
  flex: 1;
  text-align: center;
  font-size: var(--text-base);
  font-weight: 500;
  margin: 0;
  color: var(--r-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-width: 0;
}
.title-mode {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 6px;
  background: var(--r-border);
  color: var(--r-accent);
  flex-shrink: 0;
}
.title-text { overflow: hidden; text-overflow: ellipsis; }

.reader-tools { display: flex; align-items: center; gap: 8px; }
.tool-group { display: inline-flex; align-items: center; gap: 4px; }
.tool-btn {
  padding: 5px 10px;
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--r-text);
  background: transparent;
  border: 1px solid transparent;
  border-radius: 6px;
  transition: all 120ms ease;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  line-height: 1.2;
  cursor: pointer;
}
.tool-btn:hover:not(:disabled) { background: var(--r-border); }
.tool-btn--icon { padding-left: 8px; }
.tool-label { font-size: var(--text-xs); color: var(--r-muted); min-width: 16px; text-align: center; }
.tool-sep { color: var(--r-border); font-size: var(--text-xs); }

.reader-state {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  font-size: var(--text-base);
  color: var(--r-muted);
}
.reader-state--error { color: #C24555; }
.ghost-btn {
  padding: 6px 16px;
  font-size: var(--text-sm);
  color: var(--r-text);
  background: transparent;
  border: 1px solid var(--r-border);
  border-radius: 6px;
  cursor: pointer;
  transition: all 120ms ease;
}
.ghost-btn:hover { background: var(--r-border); }

.reader-pager { flex: 1; position: relative; overflow: hidden; cursor: default; }
.reader-content {
  width: 100%;
  height: 100%;
  box-sizing: border-box;
  column-gap: 0;
  column-fill: auto;
  padding-top: 56px;
  padding-bottom: 56px;
  font-size: var(--reader-font-size);
  line-height: 1.95;
  font-family:
    "Songti SC", "STSong", "霞鹜文楷", "Source Han Serif SC",
    "Noto Serif CJK SC", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", serif;
  letter-spacing: 0.5px;
  color: var(--r-text);
}
.reader[data-spread="double"] .reader-content { column-count: 2; }
.reader[data-spread="single"] .reader-content { column-count: 1; }

.reader-frontmatter {
  margin: 0 40px 1.5em 40px;
  text-align: center;
  break-after: column;
}
.reader[data-spread="single"] .reader-frontmatter {
  margin-left: max(40px, calc((100vw - 720px) / 2));
  margin-right: max(40px, calc((100vw - 720px) / 2));
}
.fm-title {
  font-size: calc(var(--reader-font-size, 18px) * 1.7);
  font-weight: 700;
  margin: 0.4em 0 0.4em;
  line-height: 1.3;
}
.fm-orig { font-size: var(--text-sm); color: var(--r-muted); margin: 0 0 0.3em; }
.fm-author { font-size: var(--text-sm); color: var(--r-muted); margin: 0; }

.reader-para {
  margin: 0 40px 1em 40px;
  text-indent: 2em;
  break-inside: avoid;
  hyphens: auto;
}
.reader[data-spread="single"] .reader-para {
  margin-left: max(40px, calc((100vw - 720px) / 2));
  margin-right: max(40px, calc((100vw - 720px) / 2));
}
.reader-chapter-title {
  font-size: calc(var(--reader-font-size, 18px) * 1.35);
  font-weight: 600;
  text-align: center;
  margin: 1.5em 40px 1em 40px;
  break-before: column;
  break-after: avoid;
  letter-spacing: 0.08em;
}
.reader[data-spread="single"] .reader-chapter-title {
  margin-left: max(40px, calc((100vw - 720px) / 2));
  margin-right: max(40px, calc((100vw - 720px) / 2));
}

.reader-tap-hint {
  position: absolute; top: 0; bottom: 0;
  width: 33.33%;
  display: flex; align-items: center;
  pointer-events: none;
  opacity: 0;
  transition: opacity 180ms ease;
}
.reader-tap-hint--left { left: 0; justify-content: flex-start; padding-left: 24px; }
.reader-tap-hint--right { right: 0; justify-content: flex-end; padding-right: 24px; }
.reader-pager:hover .reader-tap-hint { opacity: 0.35; }
.reader-pager:hover .reader-tap-hint:hover { opacity: 0.7; }
.tap-arrow { font-size: 48px; color: var(--r-muted); line-height: 1; user-select: none; }

.page-btn {
  width: 36px; height: 36px;
  font-size: var(--text-xl); line-height: 1;
  color: var(--r-text);
  background: transparent;
  border: 1px solid var(--r-border);
  border-radius: 6px;
  cursor: pointer;
  transition: all 120ms ease;
}
.page-btn:hover:not(:disabled) { background: var(--r-border); }
.page-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.page-meta { min-width: 66px; text-align: center; font-size: var(--text-sm); color: var(--r-muted); }
.progress-track {
  flex: 1; height: 4px;
  background: var(--r-border);
  border-radius: 2px;
  overflow: hidden;
  min-width: 100px;
}
.progress-fill { height: 100%; background: var(--r-accent); transition: width 220ms ease; }
.char-count { font-size: var(--text-xs); color: var(--r-muted); white-space: nowrap; }

.like-pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 5px 12px;
  font-size: var(--text-sm);
  color: var(--r-text);
  background: transparent;
  border: 1px solid var(--r-border);
  border-radius: var(--radius-full, 999px);
  cursor: pointer;
  transition: all 120ms ease;
}
.like-pill:hover:not(:disabled) { border-color: #E8B4B4; color: #D9534F; }
.like-pill.on { border-color: #E8B4B4; color: #D9534F; background: rgba(217, 83, 79, 0.08); }
.like-pill:disabled { opacity: 0.6; cursor: default; }

@media (prefers-reduced-motion: reduce) {
  .reader, .reader-toolbar, .progress-fill { transition: none !important; }
}
</style>
