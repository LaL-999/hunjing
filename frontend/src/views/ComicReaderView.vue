<script setup lang="ts">
/**
 * ComicReaderView — D.9 Sprint 4.A 漫画阅读器(全屏沉浸式)
 *
 * 路由:/comics/:id/read(meta.fullscreen=true → App.vue 隐藏 sidebar)
 *
 * 设计原则:
 *   - 阅读器 ≠ 创作流程视图,独立 UX 范式;ComicProjectView 是创作页(多状态、上传、投票),
 *     此处是"读者翻阅"模式(单页全屏 + 翻页 + 缩放 + 平移)
 *   - state ≠ 'done' 时不渲染 panels,显占位 + "回创作视图"链(不在阅读器里推进流程)
 *   - panel.image_url=null(生成失败)时显占位格子,不阻塞翻阅其他正常页
 *   - 一致性 chip:对齐 ADR §10,Sprint 0 实测 95% 角色一致性
 *
 * 交互(MVP):
 *   - 键盘:← prev / → next / Esc 返回 / + - 缩放 / 0 重置 / Home End 首末页
 *   - 鼠标:滚轮缩放(viewport 中心)/ zoom > 1 时左键拖拽平移
 *   - 触摸:横滑翻页(|Δx| > 50px)
 *   - 沉浸:鼠标 idle ≥ 3s 隐藏顶 / 底 chrome,移动时立刻显
 *
 * 未做(留 Sprint 4 后续):
 *   - 双击重置(测试时大量误触,延后)
 *   - 双指 pinch 缩放(touch-action 复杂,留挂载点)
 *   - 翻页过渡动画(MVP 用 immediate 切换,后期加 CSS transition)
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { apiAssetUrl } from "../api/client";

import { type ComicPageRecord } from "../api/types";
import { useComic } from "../composables/useComic";
import { toast } from "../composables/useToast";

const route = useRoute();
const router = useRouter();

const comicId = computed<string | null>(() => {
  const v = route.params.id;
  return typeof v === "string" ? v : null;
});

const { comic, loading, error, dispose, fetchComicPages } = useComic(() => comicId.value);

// ============================================================
// Pages 拉取(独立于 useComic 的 comic state)
// ============================================================

const pages = ref<ComicPageRecord[]>([]);
const pagesLoading = ref<boolean>(false);
const pagesError = ref<string | null>(null);

async function loadPages() {
  if (!comicId.value) return;
  pagesLoading.value = true;
  pagesError.value = null;
  try {
    pages.value = await fetchComicPages();
  } catch (e) {
    pages.value = [];
    pagesError.value = e instanceof Error ? e.message : "加载漫画页面失败";
  } finally {
    pagesLoading.value = false;
  }
}

// comic state 变 done 时拉(初始 / 等推进完成)
watch(
  () => comic.value?.state,
  (newState) => {
    if (newState === "done" && pages.value.length === 0 && !pagesLoading.value) {
      void loadPages();
    }
  },
  { immediate: true },
);

// ============================================================
// 翻页 state
// ============================================================

/** 0-based 当前页索引(pages[i]) */
const currentPageIndex = ref<number>(0);

const totalPages = computed<number>(() => pages.value.length);
const currentPage = computed<ComicPageRecord | null>(() => {
  return pages.value[currentPageIndex.value] ?? null;
});

const atFirst = computed<boolean>(() => currentPageIndex.value <= 0);
const atLast = computed<boolean>(() => currentPageIndex.value >= totalPages.value - 1);

// ============================================================
// 视图模式(Sprint 4.C):整页 PNG(typesetter 排版结果)vs 格视图(2x3 panel grid)
// ============================================================

/** 用户偏好(localStorage 持久化跨会话) */
const VIEW_MODE_STORAGE_KEY = "huimeng:comic-reader:view-mode";
type ViewMode = "fullpage" | "grid";

function readStoredViewMode(): ViewMode {
  try {
    const v = localStorage.getItem(VIEW_MODE_STORAGE_KEY);
    if (v === "grid" || v === "fullpage") return v;
  } catch {
    // localStorage 不可用(隐私模式),无视
  }
  return "fullpage"; // 默认整页(更专业、更接近发布形态)
}

const viewMode = ref<ViewMode>(readStoredViewMode());

/** 当前页是否有 composed_url(typesetter 排版完了)*/
const currentHasComposed = computed<boolean>(() => {
  return Boolean(currentPage.value?.composed_url);
});

/**
 * 实际显示的模式 — 用户偏好 + 数据可用性的双闸门
 * - 用户选 'fullpage' 但当前页 composed_url=null(typesetter 失败 OR 老漫画 Sprint 4.C 前生成):
 *   自动降级到 grid;不切换用户偏好(下一页有 composed_url 时仍回 fullpage)
 */
const effectiveViewMode = computed<ViewMode>(() => {
  if (viewMode.value === "fullpage" && !currentHasComposed.value) {
    return "grid";
  }
  return viewMode.value;
});

function toggleViewMode() {
  const next: ViewMode = viewMode.value === "fullpage" ? "grid" : "fullpage";
  viewMode.value = next;
  try {
    localStorage.setItem(VIEW_MODE_STORAGE_KEY, next);
  } catch {
    // localStorage 失败时不阻塞
  }
  resetTransform();
}

function prevPage() {
  if (atFirst.value) return;
  currentPageIndex.value -= 1;
  resetTransform();
}

function nextPage() {
  if (atLast.value) return;
  currentPageIndex.value += 1;
  resetTransform();
}

function goToFirst() {
  currentPageIndex.value = 0;
  resetTransform();
}

function goToLast() {
  currentPageIndex.value = Math.max(0, totalPages.value - 1);
  resetTransform();
}

// ============================================================
// 缩放 / 平移 state
// ============================================================

const MIN_ZOOM = 1.0;
const MAX_ZOOM = 3.0;
const ZOOM_STEP = 0.25;
const WHEEL_ZOOM_FACTOR = 0.001;

const zoomLevel = ref<number>(MIN_ZOOM);
const panX = ref<number>(0);
const panY = ref<number>(0);

const isPanning = ref<boolean>(false);
const panStartX = ref<number>(0);
const panStartY = ref<number>(0);
const panStartOffsetX = ref<number>(0);
const panStartOffsetY = ref<number>(0);

/** transform 字符串,viewport 套在外层 */
const viewportTransform = computed<string>(() => {
  return `translate(${panX.value}px, ${panY.value}px) scale(${zoomLevel.value})`;
});

function clampZoom(z: number): number {
  return Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, z));
}

function zoomIn() {
  zoomLevel.value = clampZoom(zoomLevel.value + ZOOM_STEP);
  // 缩放下降时若回到 1,清平移
  if (zoomLevel.value === MIN_ZOOM) {
    panX.value = 0;
    panY.value = 0;
  }
}

function zoomOut() {
  zoomLevel.value = clampZoom(zoomLevel.value - ZOOM_STEP);
  if (zoomLevel.value === MIN_ZOOM) {
    panX.value = 0;
    panY.value = 0;
  }
}

function resetTransform() {
  zoomLevel.value = MIN_ZOOM;
  panX.value = 0;
  panY.value = 0;
}

function onWheel(e: WheelEvent) {
  e.preventDefault();
  const delta = -e.deltaY * WHEEL_ZOOM_FACTOR;
  const next = clampZoom(zoomLevel.value + delta);
  zoomLevel.value = next;
  if (next === MIN_ZOOM) {
    panX.value = 0;
    panY.value = 0;
  }
}

function onMouseDown(e: MouseEvent) {
  if (zoomLevel.value <= MIN_ZOOM) return;
  // 仅左键
  if (e.button !== 0) return;
  isPanning.value = true;
  panStartX.value = e.clientX;
  panStartY.value = e.clientY;
  panStartOffsetX.value = panX.value;
  panStartOffsetY.value = panY.value;
  e.preventDefault();
}

function onMouseMove(e: MouseEvent) {
  notifyMouseActivity();
  if (!isPanning.value) return;
  panX.value = panStartOffsetX.value + (e.clientX - panStartX.value);
  panY.value = panStartOffsetY.value + (e.clientY - panStartY.value);
}

function onMouseUp() {
  isPanning.value = false;
}

// ============================================================
// 触摸滑动翻页(zoom = 1 时启用;zoom > 1 时让用户用拖拽看局部)
// ============================================================

const SWIPE_THRESHOLD_PX = 50;
const touchStartX = ref<number | null>(null);
const touchStartY = ref<number | null>(null);

function onTouchStart(e: TouchEvent) {
  if (e.touches.length !== 1) return;
  if (zoomLevel.value > MIN_ZOOM) return; // 缩放态不接管手势
  touchStartX.value = e.touches[0].clientX;
  touchStartY.value = e.touches[0].clientY;
}

function onTouchEnd(e: TouchEvent) {
  if (touchStartX.value === null || touchStartY.value === null) return;
  if (e.changedTouches.length !== 1) {
    touchStartX.value = null;
    touchStartY.value = null;
    return;
  }
  const dx = e.changedTouches[0].clientX - touchStartX.value;
  const dy = e.changedTouches[0].clientY - touchStartY.value;
  touchStartX.value = null;
  touchStartY.value = null;

  // 水平位移显著大于垂直 → 视为翻页;反之忽略(可能是上下滚动)
  if (Math.abs(dx) > SWIPE_THRESHOLD_PX && Math.abs(dx) > Math.abs(dy)) {
    if (dx > 0) {
      prevPage();
    } else {
      nextPage();
    }
  }
}

// ============================================================
// 键盘快捷键
// ============================================================

function onKeydown(e: KeyboardEvent) {
  // 防输入框内触发
  const target = e.target as HTMLElement | null;
  if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA")) {
    return;
  }

  switch (e.key) {
    case "ArrowLeft":
      e.preventDefault();
      prevPage();
      break;
    case "ArrowRight":
    case " ": // 空格也翻页(漫画阅读器约定俗成)
      e.preventDefault();
      nextPage();
      break;
    case "Escape":
      e.preventDefault();
      exitReader();
      break;
    case "+":
    case "=": // shift+= 是 +,= 单独也支持
      e.preventDefault();
      zoomIn();
      break;
    case "-":
    case "_":
      e.preventDefault();
      zoomOut();
      break;
    case "0":
      e.preventDefault();
      resetTransform();
      break;
    case "Home":
      e.preventDefault();
      goToFirst();
      break;
    case "End":
      e.preventDefault();
      goToLast();
      break;
  }
}

// ============================================================
// 沉浸式 chrome auto-hide(鼠标 idle ≥ 3s 隐藏 topbar/footer)
// ============================================================

const CHROME_HIDE_DELAY_MS = 3000;
const chromeVisible = ref<boolean>(true);
let chromeHideTimer: ReturnType<typeof setTimeout> | null = null;

function notifyMouseActivity() {
  chromeVisible.value = true;
  if (chromeHideTimer) clearTimeout(chromeHideTimer);
  chromeHideTimer = setTimeout(() => {
    chromeVisible.value = false;
  }, CHROME_HIDE_DELAY_MS);
}

// ============================================================
// 返回 / 退出
// ============================================================

function exitReader() {
  if (!comicId.value) {
    router.push("/my-comics");
    return;
  }
  router.push(`/comics/${comicId.value}`);
}

// ============================================================
// 状态判断
// ============================================================

const isReadyToRead = computed<boolean>(() => {
  return comic.value?.state === "done" && pages.value.length > 0;
});

const notDoneHint = computed<string>(() => {
  const s = comic.value?.state;
  if (!s) return "";
  if (s === "failed") return "此漫画生成失败,请回创作视图查看错误";
  if (s === "cancelled") return "此漫画已被取消";
  if (
    s === "scripting" || s === "extracting_visuals" || s === "style_analyzing"
    || s === "character_anchoring" || s === "designing" || s === "generating"
    || s === "composing"
  ) {
    return "此漫画仍在生成中,请回创作视图等待完成";
  }
  if (s === "style_uploading" || s === "style_voting" || s === "queued") {
    return "此漫画尚未完成创作流程,请回创作视图继续";
  }
  return "";
});

// ============================================================
// 生命周期
// ============================================================

onMounted(() => {
  window.addEventListener("keydown", onKeydown);
  window.addEventListener("mouseup", onMouseUp);
  notifyMouseActivity();
});

onBeforeUnmount(() => {
  window.removeEventListener("keydown", onKeydown);
  window.removeEventListener("mouseup", onMouseUp);
  if (chromeHideTimer) clearTimeout(chromeHideTimer);
  dispose();
});

// 进入时给一次性提示(键盘快捷键)
let hintShown = false;
watch(isReadyToRead, (ready) => {
  if (ready && !hintShown) {
    hintShown = true;
    toast.info("← → 翻页 · + - 缩放 · Esc 退出", 3500);
  }
});
</script>

<template>
  <div
    class="reader-shell"
    @mousemove="notifyMouseActivity"
    :class="{ 'is-panning': isPanning }"
  >
    <!-- 顶栏 overlay(auto-hide) -->
    <header class="reader-topbar" :class="{ 'is-hidden': !chromeVisible }">
      <button class="topbar-btn" @click="exitReader" aria-label="退出阅读器">
        <span class="back-arrow" aria-hidden="true">←</span>
        <span>退出</span>
      </button>
      <div class="topbar-center">
        <span v-if="comic" class="comic-title">{{ comic.name }}</span>
        <span class="consistency-chip" title="ADR §10:Sprint 0 实测角色一致性 95%">
          AI 协作创作 · 角色一致性约 95%
        </span>
      </div>
      <div class="topbar-right">
        <span v-if="isReadyToRead" class="page-indicator mono">
          P {{ currentPageIndex + 1 }} / {{ totalPages }}
        </span>
      </div>
    </header>

    <!-- 主画布 -->
    <main
      class="reader-canvas"
      @wheel.prevent="onWheel"
      @mousedown="onMouseDown"
      @mousemove="onMouseMove"
      @touchstart.passive="onTouchStart"
      @touchend.passive="onTouchEnd"
      aria-live="polite"
    >
      <!-- 加载态(初始拉 comic + pages) -->
      <div v-if="loading && !comic" class="reader-overlay">
        <p>加载中…</p>
      </div>

      <!-- comic 加载失败 -->
      <div v-else-if="error && !comic" class="reader-overlay">
        <p class="error-text">{{ error }}</p>
        <button class="ghost-btn" @click="exitReader">返回</button>
      </div>

      <!-- comic ok 但 state 不是 done -->
      <div v-else-if="comic && !isReadyToRead" class="reader-overlay">
        <p>{{ notDoneHint }}</p>
        <button class="ghost-btn" @click="exitReader">返回创作视图</button>
      </div>

      <!-- pages 加载中 -->
      <div v-else-if="pagesLoading && pages.length === 0" class="reader-overlay">
        <p>加载漫画页面中…</p>
      </div>

      <!-- pages 拉失败 -->
      <div v-else-if="pagesError && pages.length === 0" class="reader-overlay">
        <p class="error-text">{{ pagesError }}</p>
        <button class="ghost-btn" @click="loadPages">重试</button>
        <button class="ghost-btn" @click="exitReader">返回</button>
      </div>

      <!-- 主阅读区 -->
      <div
        v-else-if="currentPage"
        class="page-viewport"
        :style="{ transform: viewportTransform }"
      >
        <!-- 模式 A:整页 PNG(Sprint 4.C typesetter 合成,推荐) -->
        <article
          v-if="effectiveViewMode === 'fullpage' && currentPage.composed_url"
          class="comic-page comic-page--fullpage"
          :data-page-index="currentPage.page_index"
        >
          <img
            :src="apiAssetUrl(currentPage.composed_url)"
            :alt="`第 ${currentPage.page_index} 页(整页排版)`"
            class="composed-page-img"
            draggable="false"
          />
        </article>

        <!-- 模式 B:格视图(2x3 panel grid;composed_url 缺失时自动降级到这里) -->
        <article
          v-else
          class="comic-page comic-page--grid"
          :data-page-index="currentPage.page_index"
        >
          <div class="page-panels">
            <div
              v-for="panel in currentPage.panels"
              :key="`${currentPage.id}-${panel.panel_index}`"
              class="panel"
              :class="{ 'panel--failed': !panel.image_url }"
            >
              <img
                v-if="panel.image_url"
                :src="apiAssetUrl(panel.image_url)"
                :alt="`第 ${currentPage.page_index} 页第 ${panel.panel_index} 格`"
                draggable="false"
              />
              <div v-else class="panel-failed-text">
                <span>格 {{ panel.panel_index }}</span>
                <span class="failed-hint">生成失败</span>
              </div>

              <!-- 旁白条(顶部) -->
              <div v-if="panel.narrator" class="narrator-strip">
                {{ panel.narrator }}
              </div>

              <!-- 对话气泡叠层 -->
              <div
                v-if="panel.dialogues && panel.dialogues.length > 0"
                class="dialogue-stack"
              >
                <div
                  v-for="(d, di) in panel.dialogues"
                  :key="di"
                  class="dialogue-bubble"
                >
                  <strong>{{ d.speaker }}:</strong>{{ d.text }}
                </div>
              </div>

              <!-- 拟声词角标 -->
              <div
                v-if="panel.sfx && panel.sfx.length > 0"
                class="sfx-tag"
              >
                {{ panel.sfx.join(" ") }}
              </div>
            </div>
          </div>
        </article>
      </div>

      <!-- 边缘点击区(zoom = 1 时显示;zoom > 1 时让位给拖拽) -->
      <button
        v-if="isReadyToRead && zoomLevel === MIN_ZOOM && !atFirst"
        class="edge-btn edge-btn--left"
        @click="prevPage"
        aria-label="上一页"
      >
        <span aria-hidden="true">‹</span>
      </button>
      <button
        v-if="isReadyToRead && zoomLevel === MIN_ZOOM && !atLast"
        class="edge-btn edge-btn--right"
        @click="nextPage"
        aria-label="下一页"
      >
        <span aria-hidden="true">›</span>
      </button>
    </main>

    <!-- 底栏 overlay(auto-hide) -->
    <footer
      v-if="isReadyToRead"
      class="reader-footer"
      :class="{ 'is-hidden': !chromeVisible }"
    >
      <div class="footer-left">
        <button
          class="footer-btn"
          @click="prevPage"
          :disabled="atFirst"
          aria-label="上一页"
        >
          ‹ 上一页
        </button>
        <button
          class="footer-btn"
          @click="nextPage"
          :disabled="atLast"
          aria-label="下一页"
        >
          下一页 ›
        </button>
      </div>

      <div class="footer-center">
        <div class="progress-track" :title="`P ${currentPageIndex + 1} / ${totalPages}`">
          <div
            class="progress-fill"
            :style="{
              width: totalPages > 0
                ? ((currentPageIndex + 1) / totalPages * 100) + '%'
                : '0%'
            }"
          ></div>
        </div>
      </div>

      <div class="footer-right">
        <!-- Sprint 4.C 视图模式切换 -->
        <button
          class="footer-btn view-mode-btn"
          @click="toggleViewMode"
          :title="effectiveViewMode === 'fullpage' ? '当前:整页排版 · 点击切到格视图' : '当前:格视图 · 点击切到整页(若已排版)'"
          :aria-label="effectiveViewMode === 'fullpage' ? '切到格视图' : '切到整页视图'"
        >
          <span aria-hidden="true">{{ effectiveViewMode === 'fullpage' ? '⊟' : '⊞' }}</span>
          <span class="view-mode-label">{{ effectiveViewMode === 'fullpage' ? '整页' : '格视图' }}</span>
        </button>
        <button
          class="footer-btn footer-btn--icon"
          @click="zoomOut"
          :disabled="zoomLevel <= MIN_ZOOM"
          aria-label="缩小"
          title="缩小(快捷键:-)"
        >−</button>
        <span class="zoom-label mono">{{ Math.round(zoomLevel * 100) }}%</span>
        <button
          class="footer-btn footer-btn--icon"
          @click="zoomIn"
          :disabled="zoomLevel >= MAX_ZOOM"
          aria-label="放大"
          title="放大(快捷键:+)"
        >+</button>
        <button
          class="footer-btn footer-btn--icon"
          @click="resetTransform"
          :disabled="zoomLevel === MIN_ZOOM && panX === 0 && panY === 0"
          aria-label="重置缩放"
          title="重置(快捷键:0)"
        >⤢</button>
      </div>
    </footer>

    <!-- 屏读器实时播报(a11y) -->
    <div class="sr-only" aria-live="polite" aria-atomic="true">
      <template v-if="isReadyToRead">
        第 {{ currentPageIndex + 1 }} 页,共 {{ totalPages }} 页
      </template>
    </div>
  </div>
</template>

<style scoped>
/* ============================================================
 * 沉浸式阅读器 — 深色 chrome,主画布跟随主题
 * ============================================================ */

.reader-shell {
  position: fixed;
  inset: 0;
  background: var(--color-bg);
  color: var(--color-text);
  overflow: hidden;
  display: flex;
  flex-direction: column;
  user-select: none;
}
.reader-shell.is-panning {
  cursor: grabbing;
}

/* 顶栏 / 底栏 — 半透明 overlay,auto-hide */
.reader-topbar,
.reader-footer {
  position: absolute;
  left: 0;
  right: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  padding: var(--space-3) var(--space-5);
  background: color-mix(in srgb, var(--color-surface) 88%, transparent);
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  border-color: var(--color-border);
  transition: opacity 0.25s ease, transform 0.25s ease;
}
.reader-topbar {
  top: 0;
  border-bottom: 1px solid var(--color-border);
  justify-content: space-between;
  gap: var(--space-4);
}
.reader-topbar.is-hidden {
  opacity: 0;
  transform: translateY(-8px);
  pointer-events: none;
}

.reader-footer {
  bottom: 0;
  border-top: 1px solid var(--color-border);
  justify-content: space-between;
  gap: var(--space-4);
}
.reader-footer.is-hidden {
  opacity: 0;
  transform: translateY(8px);
  pointer-events: none;
}

/* topbar 内容 */
.topbar-btn,
.footer-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-2) var(--space-3);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text);
  font: inherit;
  cursor: pointer;
  transition: background 0.15s ease;
}
.topbar-btn:hover,
.footer-btn:hover:not(:disabled) {
  background: var(--color-bg-hover);
}
.footer-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.footer-btn--icon {
  padding: var(--space-2);
  min-width: 36px;
  justify-content: center;
  font-size: 1.1em;
}
.view-mode-btn {
  padding: var(--space-2) var(--space-3);
  gap: 4px;
  font-size: 0.85em;
}
.view-mode-label {
  white-space: nowrap;
}

.back-arrow {
  font-size: 1.1em;
  line-height: 1;
}

.topbar-center {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  flex: 1 1 auto;
  min-width: 0;
  justify-content: center;
}
.comic-title {
  font-weight: 600;
  font-size: 0.95em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 30ch;
}
.consistency-chip {
  display: inline-block;
  padding: 2px 10px;
  border-radius: 999px;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  font-size: 0.78em;
  white-space: nowrap;
}
.topbar-right {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}
.page-indicator {
  font-size: 0.9em;
  color: var(--color-text-muted);
  padding: 2px 10px;
  border-radius: var(--radius-sm);
  background: var(--color-bg-subtle);
}
.mono {
  font-family: var(--font-mono, ui-monospace, "SF Mono", Menlo, monospace);
}

/* footer 内容 */
.footer-left,
.footer-right {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.footer-center {
  flex: 1 1 auto;
  display: flex;
  justify-content: center;
  align-items: center;
  padding: 0 var(--space-4);
  min-width: 0;
}
.progress-track {
  position: relative;
  width: min(100%, 320px);
  height: 4px;
  background: var(--color-bg-subtle);
  border-radius: 2px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: var(--color-accent);
  transition: width 0.2s ease;
}
.zoom-label {
  min-width: 4ch;
  text-align: center;
  font-size: 0.85em;
  color: var(--color-text-muted);
}

/* ============================================================
 * 主画布 + viewport
 * ============================================================ */

.reader-canvas {
  flex: 1 1 auto;
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: default;
  /* 启用滚轮 + 触摸 */
  touch-action: pan-y; /* 允许垂直滚动作为页面级 fallback;水平由 onTouchEnd 接管 */
}

.page-viewport {
  display: flex;
  align-items: center;
  justify-content: center;
  transform-origin: center center;
  transition: transform 0.05s linear;
  will-change: transform;
}

.reader-shell.is-panning .page-viewport {
  transition: none;
}

/* ============================================================
 * 漫画页 / 格(2x3 grid,与 ComicProjectView 视觉一致但放大)
 * ============================================================ */

.comic-page {
  background: var(--color-surface);
  border-radius: var(--radius-md);
  box-shadow: 0 8px 32px rgba(0, 0, 0, 0.18);
  /* Sprint 4.D+ fix:严格不溢出 viewport(原 max-height calc 在 chrome 隐藏时空间反而增加,
     用 vh 单位 + 留 chrome 余量更稳) */
  max-width: 90vw;
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

/* 模式 A:整页 PNG(typesetter 输出) — 容器贴边,不加 padding */
.comic-page--fullpage {
  padding: 0;
}
.composed-page-img {
  display: block;
  /* Sprint 4.D+ 整页比例 800:1200 = 2:3(原 800:1500 用户实测后调,对齐 1:1 panel 生图)
     viewport 88vh × 2/3 = 58.6vh 宽,所以 max-width 用 vh 反推(`88vh × 0.667`),
     避免 max-width: 800px 时高度溢出 */
  max-width: min(90vw, calc(88vh * 0.667));
  max-height: 88vh;
  width: auto;
  height: auto;
  object-fit: contain;
  border-radius: var(--radius-md);
}

/* 模式 B:格视图 — Sprint 4.D+ 关键修复:2x3 grid 整体 fit viewport
   原 bug:.panel aspect-ratio: 3/4 强制每格 388×517,3 行 = 1657h 远超 88vh = 920h,
   reader-canvas overflow:hidden + center align → 只见上 2 行的部分(用户报告)
   修法:容器固定 2:3 整体比例 + max-height 88vh + grid fr 让 panel 自适应,
        panel 取消 aspect-ratio 让 grid 决定其大小(本质上变成接近 1:1 正方形格,
        与 manga 4-koma / 6-panel 排版更接近,且各格 image 用 object-fit:cover 仍正常) */
.comic-page--grid {
  padding: var(--space-2);
  /* 整体 2:3(宽 800:高 1200 类似 manga B5),与 viewport 较小者 fit */
  aspect-ratio: 2 / 3;
  width: min(70vh, 90vw);
  max-height: 88vh;
}
.comic-page--grid .page-panels {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  grid-template-rows: repeat(3, 1fr);
  gap: var(--space-2);
  width: 100%;
  height: 100%;
}

/* Legacy .page-panels(已被 .comic-page--grid .page-panels 替代,保留兜底响应式) */
.page-panels {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-2);
  flex: 1 1 auto;
  width: 100%;
}

.panel {
  position: relative;
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  overflow: hidden;
  /* 防 grid 子元素溢出导致 overflow:hidden 失效 */
  min-width: 0;
  min-height: 0;
}
.panel img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.panel--failed {
  background: color-mix(in srgb, var(--color-danger) 12%, var(--color-bg-subtle));
  border-color: color-mix(in srgb, var(--color-danger) 40%, transparent);
}
.panel-failed-text {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-1);
  color: var(--color-danger);
  font-size: 0.85em;
}
.failed-hint {
  font-size: 0.85em;
  color: var(--color-text-muted);
}

/* 旁白条 — 顶部黄色长条 */
.narrator-strip {
  position: absolute;
  top: 6px;
  left: 6px;
  right: 6px;
  padding: 4px 8px;
  background: color-mix(in srgb, #f5d06a 85%, transparent);
  color: #4a3a00;
  font-size: 0.75em;
  border-radius: 3px;
  line-height: 1.3;
  z-index: 2;
  pointer-events: none;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.15);
}

/* 对话气泡 stack */
.dialogue-stack {
  position: absolute;
  bottom: 6px;
  left: 6px;
  right: 6px;
  display: flex;
  flex-direction: column;
  gap: 3px;
  z-index: 2;
  pointer-events: none;
}
.dialogue-bubble {
  background: rgba(255, 255, 255, 0.96);
  color: #1a1a1a;
  padding: 4px 8px;
  border-radius: 10px;
  font-size: 0.8em;
  line-height: 1.35;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.18);
  border: 1px solid rgba(0, 0, 0, 0.06);
}
.dialogue-bubble strong {
  color: var(--color-accent);
}

/* 拟声词角标 */
.sfx-tag {
  position: absolute;
  top: 6px;
  right: 6px;
  padding: 2px 6px;
  background: color-mix(in srgb, var(--color-warning, #e09a2f) 90%, transparent);
  color: #fff;
  font-weight: 700;
  font-size: 0.75em;
  border-radius: 3px;
  z-index: 2;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  pointer-events: none;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.2);
}

/* ============================================================
 * 边缘点击翻页区(zoom=1 时显)
 * ============================================================ */

.edge-btn {
  position: absolute;
  top: 50%;
  transform: translateY(-50%);
  width: 56px;
  height: 80px;
  background: color-mix(in srgb, var(--color-surface) 70%, transparent);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-muted);
  font-size: 2em;
  line-height: 1;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0.55;
  transition: opacity 0.15s ease, background 0.15s ease;
  backdrop-filter: blur(6px);
  -webkit-backdrop-filter: blur(6px);
  z-index: 5;
}
.edge-btn:hover {
  opacity: 1;
  background: var(--color-surface);
}
.edge-btn--left {
  left: var(--space-4);
}
.edge-btn--right {
  right: var(--space-4);
}

/* ============================================================
 * Overlay 信息(加载 / 错误 / 占位)
 * ============================================================ */

.reader-overlay {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-6);
  text-align: center;
  color: var(--color-text-muted);
}
.error-text {
  color: var(--color-danger);
}
.ghost-btn {
  padding: var(--space-2) var(--space-4);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text);
  font: inherit;
  cursor: pointer;
}
.ghost-btn:hover {
  background: var(--color-bg-hover);
}

/* ============================================================
 * 屏读器 only
 * ============================================================ */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

/* ============================================================
 * 响应式:小屏单列 panel
 * ============================================================ */

@media (max-width: 640px) {
  /* 小屏:grid 保留 2 列 3 行(单列布局会让漫画体验跌出工具感),
     但让容器整体 fit 屏幕(width 100% + max-height 85vh) */
  .comic-page--grid {
    aspect-ratio: 2 / 3;
    width: calc(100vw - 16px);
    max-height: 85vh;
    padding: var(--space-1);
  }
  .comic-page--grid .page-panels {
    gap: var(--space-1);
  }
  .composed-page-img {
    max-width: calc(100vw - 16px);
    max-height: 85vh;
  }
  .topbar-center .comic-title {
    display: none; /* 小屏隐藏标题省空间 */
  }
  .consistency-chip {
    font-size: 0.7em;
  }
  .edge-btn {
    display: none; /* 小屏靠触摸翻页 */
  }
  .footer-btn span {
    display: none; /* 仅图标 */
  }
  /* 例外:view-mode-btn 的标签太短,保留;但 icon 也能识别就好 */
  .view-mode-btn .view-mode-label {
    display: none;
  }
}
</style>
