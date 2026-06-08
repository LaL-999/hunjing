<script setup lang="ts">
/**
 * SimulationReadView — 推演产物沉浸式阅读器(Sprint 6.A2,2026-05-22)
 *
 * 路由:/simulations/:id/read(meta.fullscreen=true → App.vue 隐藏 sidebar)
 *
 * 设计原则(参考 Kindle / Apple Books / 微信读书):
 *   - 阅读体验 ≠ 详情产物预览:此处沉浸式、横向翻页、章页感
 *   - 沉浸 chrome:鼠标 idle 2.5s 顶 / 底 toolbar 自动淡出;移动立刻回归
 *   - localStorage 持久化偏好(字号 / 单双页 / 主题)
 *
 * 交互:
 *   键盘:← / PgUp 上一页 · → / PgDn / Space 下一页 · Esc 退出 ·
 *         + - 字号 · F 单/双页 · T 主题
 *   鼠标:点屏幕左 1/3 上一页 · 右 1/3 下一页 · 中 1/3 不动(留给文本选中)
 *   触摸:横滑翻页(|Δx| > 60px)
 *
 * 技术实现:
 *   - CSS Multi-column 自动分栏 + scrollLeft 翻页(无 paginate 库依赖)
 *   - column-fill: auto 让最后一栏不强制对齐(翻页语义对)
 *   - 字号 / 单双页变化时 scroll 复位 + 重算总页数
 *
 * 不做(YAGNI):
 *   - 章节书签(单篇产物无章节)
 *   - 高亮 / 批注(用户读自己产物)
 *   - 朗读 / 字体切换(超出范围)
 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "../api/client";
import { ApiError, type SimulationFull } from "../api/types";
import Icon from "../components/Icon.vue";
import ChapterJumpPopover from "../components/ChapterJumpPopover.vue";

const route = useRoute();
const router = useRouter();

const simId = computed(() => String(route.params.id ?? ""));

// ============================================================
// 阅读偏好(localStorage 持久化)
// ============================================================

const STORAGE_KEY = "huimeng:reader-prefs";

type ReaderTheme = "light" | "sepia" | "dark";
type ReaderSpread = "single" | "double";

interface ReaderPrefs {
  fontSize: number;        // 16 / 18 / 22(三档)
  spread: ReaderSpread;    // 单 / 双页
  theme: ReaderTheme;      // 浅 / 沙 / 暗
}

const FONT_SIZES = [16, 18, 22] as const;
const DEFAULT_PREFS: ReaderPrefs = {
  fontSize: 18,
  spread: "double",
  theme: "sepia",
};

function loadPrefs(): ReaderPrefs {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return { ...DEFAULT_PREFS };
    const parsed = JSON.parse(raw) as Partial<ReaderPrefs>;
    return {
      fontSize: (FONT_SIZES as readonly number[]).includes(parsed.fontSize as number)
        ? (parsed.fontSize as number)
        : DEFAULT_PREFS.fontSize,
      spread: parsed.spread === "single" || parsed.spread === "double"
        ? parsed.spread
        : DEFAULT_PREFS.spread,
      theme: parsed.theme === "light" || parsed.theme === "sepia" || parsed.theme === "dark"
        ? parsed.theme
        : DEFAULT_PREFS.theme,
    };
  } catch {
    return { ...DEFAULT_PREFS };
  }
}

const prefs = ref<ReaderPrefs>(loadPrefs());

watch(
  prefs,
  (p) => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
    } catch {
      // 无 localStorage 权限静默,不阻塞主流程
    }
  },
  { deep: true },
);

// ============================================================
// 数据加载(stale-while-revalidate 节奏)
// ============================================================

const narrative = ref<string>("");
const projectName = ref<string>("");
const loading = ref(true);
const errorMessage = ref<string | null>(null);
// P0H.2(2026-05-24):每章字数 — sim.chapter_size_chars,渲染时按此切章节
const chapterSize = ref<number>(2000);
// 2026-06-01:沿继承链锁定的起始章号(老 sim 无此字段时默认 1)
const startChapter = ref<number>(1);

/** 解析 narrative — 跳过首行"> 语体:..." 元数据 */
function stripMetaLine(raw: string): string {
  if (!raw) return "";
  const firstNewline = raw.indexOf("\n");
  if (firstNewline === -1) return raw;
  const first = raw.slice(0, firstNewline).trim();
  if (/^>\s*语体/.test(first)) {
    return raw.slice(firstNewline + 1).replace(/^\n+/, "");
  }
  return raw;
}

async function loadNarrative() {
  if (!simId.value) return;
  loading.value = true;
  errorMessage.value = null;
  try {
    const sim = await api.get<SimulationFull>(`/simulations/${simId.value}`);
    if (sim.state !== "done") {
      errorMessage.value = "此推演尚未完成,无法阅读";
      return;
    }
    // 2026-06-01 v2:合并最终作品现在是独立 sim 行(is_final_compilation=1),
    // 不再走 ?final=1 query — 用户从作品列表点合并卡片直接跳本 view 即可
    narrative.value = stripMetaLine(sim.narrative ?? "");
    // 沿继承链锁定的起始章号(合并 sim 起始章号 = 1,普通 sim 用前篇累计)
    startChapter.value = sim.start_chapter_locked && sim.start_chapter_locked > 0
      ? sim.start_chapter_locked
      : 1;
    // P0H.2:拉用户设的每章字数(老 sim 无此字段时默认 2000)
    chapterSize.value = sim.chapter_size_chars ?? 2000;
    // 静默拉项目名(失败不阻塞)
    if (sim.project_id) {
      try {
        const proj = await api.get<{ name: string }>(`/projects/${sim.project_id}`);
        projectName.value = proj.name;
      } catch {
        projectName.value = "";
      }
    }
  } catch (e) {
    errorMessage.value = e instanceof ApiError ? e.message : "加载产物失败";
  } finally {
    loading.value = false;
  }
}

// ============================================================
// 内容拆段(简单按双换行分段,首行缩进 2em)
// ============================================================

const paragraphs = computed(() =>
  narrative.value
    .split(/\n{2,}/)
    .map((p) => p.trim())
    .filter((p) => p.length > 0),
);

/**
 * P0H.2(2026-05-24):按 chapter_size_chars 切章节
 *
 * 算法:
 *   - 遍历 paragraphs,累积字符数
 *   - 每章开头插入 { type: 'chapter', n: N } 项
 *   - 当累计字数达到 chapter_size 时,**下一段开头**新开一章
 *   - 字符数只数中文字符(避免 markdown 噪音)
 *
 * 输出:Array<{ type: 'chapter' | 'para', text: string, chapter?: number }>
 * 模板用 v-for 渲染,type='chapter' 用 h2,type='para' 用 p
 */
type RenderItem =
  | { type: "chapter"; chapter: number }
  | { type: "para"; text: string };

const chunkedItems = computed<RenderItem[]>(() => {
  const paras = paragraphs.value;
  if (paras.length === 0) return [];

  const size = Math.max(1000, Math.min(10000, chapterSize.value));
  const items: RenderItem[] = [];
  // 2026-06-01:章号 = 起始章号 + 本 sim 内局部偏移
  let chapterIndex = 0;  // 本 sim 内第几章(0-based)
  let chapterChars = 0;

  // 第一章开头
  items.push({ type: "chapter", chapter: startChapter.value + chapterIndex });

  for (const p of paras) {
    // 数中文字符(去 markdown 标点)
    const cnCount = (p.match(/[一-鿿]/g) || []).length;
    // 若当前章已达字数阈值,**这一段开新章**
    if (chapterChars >= size) {
      chapterIndex += 1;
      chapterChars = 0;
      items.push({ type: "chapter", chapter: startChapter.value + chapterIndex });
    }
    items.push({ type: "para", text: p });
    chapterChars += cnCount;
  }
  return items;
});

// 2026-06-01:章节跳转 — 抽取所有章节标题 + 首段前 20 字预览,给 popover 用
interface ChapterEntry {
  chapter: number;       // 全局章号(startChapter + 偏移)
  firstWords: string;    // 章节首段前 20 字
  charCount: number;     // 本章字符数(中文计)
  itemIndex: number;     // 在 chunkedItems 中的索引,用于定位 DOM
}
const chapterEntries = computed<ChapterEntry[]>(() => {
  const items = chunkedItems.value;
  const out: ChapterEntry[] = [];
  let buf: ChapterEntry | null = null;
  let firstPara: string | null = null;

  function flush() {
    if (buf) {
      // 在 push 时已经把 firstWords / charCount 写好(若有)
      out.push(buf);
    }
  }

  items.forEach((it, idx) => {
    if (it.type === "chapter") {
      flush();
      buf = {
        chapter: it.chapter,
        firstWords: "",
        charCount: 0,
        itemIndex: idx,
      };
      firstPara = null;
    } else {
      if (buf) {
        if (firstPara === null) {
          firstPara = it.text;
          // 截前 20 字 + 标点优化
          const head = it.text.slice(0, 24);
          const cutAt = (() => {
            for (const tok of ["。", ",", ",", "!", "?", "、"]) {
              const i = head.indexOf(tok, 8);
              if (i > 0) return i + 1;
            }
            return Math.min(20, head.length);
          })();
          buf.firstWords = head.slice(0, cutAt).trim() +
            (head.length > cutAt ? "…" : "");
        }
        buf.charCount += (it.text.match(/[一-鿿]/g) || []).length;
      }
    }
  });
  flush();
  return out;
});

const charCount = computed(
  () => (narrative.value.match(/[一-鿿]/g) || []).length,
);

// ============================================================
// 翻页(CSS column + scrollLeft)
// ============================================================

const pagerEl = ref<HTMLElement | null>(null);
const currentPage = ref(1);
const totalPages = ref(1);

function recalcPagination() {
  const el = pagerEl.value;
  if (!el) return;
  // clientWidth = viewport 一屏宽;scrollWidth = 多栏总宽
  const pageWidth = el.clientWidth;
  if (pageWidth <= 0) return;
  const total = Math.max(1, Math.round(el.scrollWidth / pageWidth));
  totalPages.value = total;
  const cur = Math.min(
    total,
    Math.max(1, Math.round(el.scrollLeft / pageWidth) + 1),
  );
  currentPage.value = cur;
}

function goToPage(page: number) {
  const el = pagerEl.value;
  if (!el) return;
  const clamped = Math.min(totalPages.value, Math.max(1, page));
  el.scrollTo({
    left: (clamped - 1) * el.clientWidth,
    behavior: "smooth",
  });
}

function nextPage() {
  if (currentPage.value < totalPages.value) goToPage(currentPage.value + 1);
}

function prevPage() {
  if (currentPage.value > 1) goToPage(currentPage.value - 1);
}

// 2026-06-01:跳到指定章节
// 实现:在 pager DOM 里按章号找 h2.reader-chapter-title 元素 → 算其 offsetLeft → 换算页
function jumpToChapter(chapterNo: number) {
  const el = pagerEl.value;
  if (!el) return;
  // 找所有章标题 DOM(顺序与 chunkedItems 中 'chapter' 顺序一致)
  const titles = el.querySelectorAll<HTMLElement>(".reader-chapter-title");
  // 在 chapterEntries 里找 chapter === chapterNo,拿索引(就是 titles 的索引)
  const idx = chapterEntries.value.findIndex((e) => e.chapter === chapterNo);
  if (idx === -1) return;
  const target = titles[idx];
  if (!target) return;
  const pageWidth = el.clientWidth;
  if (pageWidth <= 0) return;
  // multi-column 布局:offsetLeft 是元素在容器内的横向偏移 = (page - 1) * pageWidth
  const targetPage = Math.max(1, Math.round(target.offsetLeft / pageWidth) + 1);
  goToPage(targetPage);
}

// 计算当前页所在的"章号"— popover 高亮当前章用
const currentChapterNo = computed<number>(() => {
  const entries = chapterEntries.value;
  if (entries.length === 0) return startChapter.value;
  // 找最后一个 itemIndex <= "当前页起始 item index" 的章节
  const el = pagerEl.value;
  if (!el || totalPages.value <= 1) return entries[0].chapter;
  // 简化:取所有 .reader-chapter-title DOM,看哪个 offsetLeft 落在当前页内
  const titles = el.querySelectorAll<HTMLElement>(".reader-chapter-title");
  const pageWidth = el.clientWidth;
  if (pageWidth <= 0) return entries[0].chapter;
  const curScroll = (currentPage.value - 1) * pageWidth;
  let lastChapterIdx = 0;
  for (let i = 0; i < titles.length; i++) {
    const t = titles[i];
    if (t.offsetLeft <= curScroll + pageWidth - 1) {
      lastChapterIdx = i;
    } else {
      break;
    }
  }
  return entries[lastChapterIdx]?.chapter ?? entries[0].chapter;
});

// ============================================================
// 字号 / 单双页 / 主题 切换
// ============================================================

function adjustFontSize(delta: 1 | -1) {
  const idx = FONT_SIZES.indexOf(prefs.value.fontSize as typeof FONT_SIZES[number]);
  const newIdx = Math.min(FONT_SIZES.length - 1, Math.max(0, idx + delta));
  prefs.value = { ...prefs.value, fontSize: FONT_SIZES[newIdx] };
}

function toggleSpread() {
  prefs.value = {
    ...prefs.value,
    spread: prefs.value.spread === "double" ? "single" : "double",
  };
}

const THEME_ORDER: ReaderTheme[] = ["sepia", "light", "dark"];
function cycleTheme() {
  const idx = THEME_ORDER.indexOf(prefs.value.theme);
  const newIdx = (idx + 1) % THEME_ORDER.length;
  prefs.value = { ...prefs.value, theme: THEME_ORDER[newIdx] };
}

const themeLabel = computed(() => {
  if (prefs.value.theme === "light") return "浅";
  if (prefs.value.theme === "sepia") return "沙";
  return "暗";
});

const spreadLabel = computed(() => (prefs.value.spread === "double" ? "双页" : "单页"));

// ============================================================
// 退出
// ============================================================

function exitReader() {
  router.push(`/simulations/${simId.value}`);
}

// ============================================================
// 键盘
// ============================================================

function onGlobalKey(e: KeyboardEvent) {
  // 排除编辑框焦点(本视图无输入框,但兜底防误触)
  const t = e.target as HTMLElement | null;
  if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;

  if (e.key === "ArrowRight" || e.key === "PageDown" || e.key === " ") {
    e.preventDefault();
    nextPage();
  } else if (e.key === "ArrowLeft" || e.key === "PageUp") {
    e.preventDefault();
    prevPage();
  } else if (e.key === "Escape") {
    exitReader();
  } else if (e.key === "+" || e.key === "=") {
    adjustFontSize(1);
  } else if (e.key === "-" || e.key === "_") {
    adjustFontSize(-1);
  } else if (e.key === "f" || e.key === "F") {
    toggleSpread();
  } else if (e.key === "t" || e.key === "T") {
    cycleTheme();
  } else if (e.key === "Home") {
    e.preventDefault();
    goToPage(1);
  } else if (e.key === "End") {
    e.preventDefault();
    goToPage(totalPages.value);
  }
}

// ============================================================
// 鼠标点击三区翻页 + 触摸滑动翻页
// ============================================================

function onPagerClick(e: MouseEvent) {
  const el = pagerEl.value;
  if (!el) return;
  // 排除文本选中(用户在划字)
  const sel = window.getSelection();
  if (sel && sel.toString().length > 0) return;
  const rect = el.getBoundingClientRect();
  const x = e.clientX - rect.left;
  if (x < rect.width / 3) prevPage();
  else if (x > (rect.width * 2) / 3) nextPage();
}

let _touchStartX: number | null = null;
function onTouchStart(e: TouchEvent) {
  _touchStartX = e.touches[0]?.clientX ?? null;
}
function onTouchEnd(e: TouchEvent) {
  if (_touchStartX === null) return;
  const endX = e.changedTouches[0]?.clientX ?? _touchStartX;
  const dx = endX - _touchStartX;
  _touchStartX = null;
  if (Math.abs(dx) < 60) return;
  if (dx < 0) nextPage();
  else prevPage();
}

// ============================================================
// 沉浸 chrome:鼠标 idle 2.5s 自动隐藏 toolbar(类似 ComicReaderView)
// ============================================================

const chromeVisible = ref(true);
let _idleTimer: ReturnType<typeof setTimeout> | null = null;
const IDLE_MS = 2500;

function pingChrome() {
  chromeVisible.value = true;
  if (_idleTimer !== null) clearTimeout(_idleTimer);
  _idleTimer = setTimeout(() => {
    chromeVisible.value = false;
  }, IDLE_MS);
}

// ============================================================
// 重算分页 — 稳定布局后再算(Sprint 6.A2 fix 2026-05-22)
//
// 原 bug:
//   1. watch(paragraphs) 在 loading 切到 false 同一 tick 触发,但模板此时还在
//      "加载产物中…"(v-if="loading" 时 <main ref="pagerEl"> 未挂载)→ pagerEl=null
//      → recalc early return → totalPages 卡在初始 1 → 用户改字号才"激活"
//   2. 衬线字体 fallback 切换瞬间 scrollWidth 跳变 → 算的页数失效
//   3. 单帧 rAF 不够让浏览器完成 column layout
//
// 修法:
//   - nextTick 等 v-if 模板切换 + DOM 挂载
//   - document.fonts.ready 等 @font-face 加载完
//   - double rAF 等浏览器 layout + paint 完成
//   - ResizeObserver 兜底监听容器尺寸变化(主题切换 / 浏览器缩放 / DPR 变化)
// ============================================================

async function waitForStableLayout() {
  // 等 Vue DOM patch
  await nextTick();
  // 等字体加载完(无 @font-face 时立刻 resolve)
  const docFonts = (document as Document & { fonts?: FontFaceSet }).fonts;
  if (docFonts && typeof docFonts.ready?.then === "function") {
    try {
      await docFonts.ready;
    } catch {
      // 兜底:某些浏览器对 fonts.ready 实现不全
    }
  }
  // 两帧 rAF 让浏览器完成 layout + paint
  await new Promise<void>((resolve) => {
    requestAnimationFrame(() => {
      requestAnimationFrame(() => resolve());
    });
  });
}

async function recalcStable(resetScroll = false) {
  await waitForStableLayout();
  if (resetScroll && pagerEl.value) {
    pagerEl.value.scrollLeft = 0;
  }
  recalcPagination();
}

/** loading 切到 false 时 — DOM 此时才挂上 <main ref="pagerEl">,必须等之后再算 */
watch(loading, (isLoading) => {
  if (!isLoading && narrative.value) {
    void recalcStable(true);
  }
});

/** 切换字号 / 单双页 — 重置滚动到第 1 页(新 layout 总页数可能变,保持位置无意义) */
watch(
  () => [prefs.value.fontSize, prefs.value.spread],
  () => {
    void recalcStable(true);
  },
);

/** ResizeObserver 兜底 — 容器尺寸变化(resize / 缩放 / 主题切换引起的微调)自动重算
 *  pagerEl 在 v-if 切换时挂载/卸载,用 watch 跟踪 ref 变化绑定/解绑 observer */
let _resizeObs: ResizeObserver | null = null;
watch(pagerEl, (el, oldEl) => {
  if (_resizeObs) {
    if (oldEl) _resizeObs.unobserve(oldEl);
    _resizeObs.disconnect();
    _resizeObs = null;
  }
  if (el && typeof ResizeObserver !== "undefined") {
    _resizeObs = new ResizeObserver(() => {
      // 不重置 scrollLeft — resize 时尽量保持用户位置,但页数可能变
      recalcPagination();
    });
    _resizeObs.observe(el);
  }
});

onMounted(() => {
  document.addEventListener("keydown", onGlobalKey);
  window.addEventListener("mousemove", pingChrome, { passive: true });
  pingChrome();
  void loadNarrative();
});

onBeforeUnmount(() => {
  document.removeEventListener("keydown", onGlobalKey);
  window.removeEventListener("mousemove", pingChrome);
  if (_idleTimer !== null) clearTimeout(_idleTimer);
  if (_resizeObs) {
    _resizeObs.disconnect();
    _resizeObs = null;
  }
});

// ============================================================
// 进度
// ============================================================

const progressPct = computed(() => {
  if (totalPages.value <= 1) return 100;
  return Math.round((currentPage.value / totalPages.value) * 100);
});

// 2026-06-01:章节跳转弹窗状态(用户拍板 — 不动 6/6 页码区域)
const chapterPopoverOpen = ref(false);
function toggleChapterPopover() {
  chapterPopoverOpen.value = !chapterPopoverOpen.value;
}
function closeChapterPopover() {
  chapterPopoverOpen.value = false;
}
function onChapterJumpFromPopover(chapterNo: number) {
  jumpToChapter(chapterNo);
  closeChapterPopover();
}

const fontSizeLabel = computed(() => {
  const f = prefs.value.fontSize;
  if (f === 16) return "小";
  if (f === 22) return "大";
  return "中";
});
</script>

<template>
  <div
    class="reader"
    :data-theme="prefs.theme"
    :data-spread="prefs.spread"
    :class="{ 'is-chrome-hidden': !chromeVisible }"
  >
    <!-- 顶部 toolbar -->
    <header class="reader-toolbar reader-toolbar--top">
      <button class="tool-btn tool-btn--icon" @click="exitReader" title="返回详情 (Esc)">
        <span aria-hidden="true">←</span>
        <span>返回</span>
      </button>

      <h1 class="reader-title">
        <template v-if="projectName">《{{ projectName }}》</template>
        <template v-else>推演产物</template>
      </h1>

      <div class="reader-tools">
        <div class="tool-group">
          <button class="tool-btn" @click="adjustFontSize(-1)" title="缩小字号 (-)">A−</button>
          <span class="tool-label mono">{{ fontSizeLabel }}</span>
          <button class="tool-btn" @click="adjustFontSize(1)" title="放大字号 (+)">A+</button>
        </div>
        <span class="tool-sep" aria-hidden="true">·</span>
        <button class="tool-btn" @click="toggleSpread" :title="'单 / 双页切换 (F)'">
          {{ spreadLabel }}
        </button>
        <span class="tool-sep" aria-hidden="true">·</span>
        <button class="tool-btn" @click="cycleTheme" title="主题切换 (T)">
          {{ themeLabel }}
        </button>
        <span class="tool-sep" aria-hidden="true">·</span>
        <!-- 2026-06-01:章节跳转 — 汉堡 SVG 按钮 + 右下小窗 -->
        <div class="chapter-jump-wrap">
          <button
            class="tool-btn tool-btn--icon-only"
            :class="{ 'is-active': chapterPopoverOpen }"
            :title="`章节跳转(共 ${chapterEntries.length} 章)`"
            aria-label="章节跳转"
            @click="toggleChapterPopover"
          >
            <Icon name="menu" :size="16" />
          </button>
          <ChapterJumpPopover
            v-if="chapterPopoverOpen"
            :chapters="chapterEntries"
            :current-chapter="currentChapterNo"
            :reader-theme="prefs.theme"
            @jump="onChapterJumpFromPopover"
            @close="closeChapterPopover"
          />
        </div>
      </div>
    </header>

    <!-- loading / error -->
    <div v-if="loading" class="reader-state" aria-busy="true" aria-live="polite">加载产物中…</div>
    <div v-else-if="errorMessage" class="reader-state reader-state--error">
      {{ errorMessage }}
      <button class="ghost-btn" @click="exitReader">返回详情</button>
    </div>

    <!-- P-4 修复(2026-05-23):narrative 为空兜底(LLM 异常 / DB 损坏 罕见场景) -->
    <div
      v-else-if="paragraphs.length === 0"
      class="reader-state reader-state--empty"
    >
      <p>产物正文为空 — 可能 LLM 生成异常或数据已损坏。</p>
      <button class="ghost-btn" @click="exitReader">返回详情</button>
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
        <!-- P0H.2(2026-05-24):章节系统统一 — 两种模式都按 chapter_size 切 ## 章节 N -->
        <template v-for="(item, i) in chunkedItems" :key="i">
          <h2 v-if="item.type === 'chapter'" class="reader-chapter-title">
            章节 {{ item.chapter }}
          </h2>
          <p v-else class="reader-para">{{ item.text }}</p>
        </template>
      </div>

      <!-- 鼠标点击翻页提示区(透明 overlay,仅 hover 时显示左右箭头) -->
      <div class="reader-tap-hint reader-tap-hint--left" aria-hidden="true">
        <span v-if="currentPage > 1" class="tap-arrow">‹</span>
      </div>
      <div class="reader-tap-hint reader-tap-hint--right" aria-hidden="true">
        <span v-if="currentPage < totalPages" class="tap-arrow">›</span>
      </div>
    </main>

    <!-- 底部 footer:翻页 + 进度 + 字数 -->
    <footer v-if="!loading && !errorMessage" class="reader-toolbar reader-toolbar--bottom">
      <button
        class="page-btn"
        :disabled="currentPage <= 1"
        @click="prevPage"
        aria-label="上一页 (← / PgUp)"
      >‹</button>
      <span class="page-meta mono">{{ currentPage }} / {{ totalPages }}</span>
      <div class="progress-track" role="progressbar"
        :aria-valuenow="progressPct" aria-valuemin="0" aria-valuemax="100">
        <div class="progress-fill" :style="{ width: `${progressPct}%` }"></div>
      </div>
      <span class="char-count mono">{{ charCount.toLocaleString() }} 字</span>
      <button
        class="page-btn"
        :disabled="currentPage >= totalPages"
        @click="nextPage"
        aria-label="下一页 (→ / PgDn / Space)"
      >›</button>
    </footer>
  </div>
</template>

<style scoped>
/* ============================================================
 * 阅读器主题(独立 token 集,不混入项目浅色 token)
 *   sepia(默认):米黄底,纸质阅读最舒适
 *   light:纯白底
 *   dark:深灰底,夜读护眼
 * ============================================================ */
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
  --r-bg: #F4ECD8;
  --r-text: #3C3528;
  --r-chrome-bg: rgba(232, 223, 202, 0.92);
  --r-border: rgba(213, 201, 176, 0.6);
  --r-accent: #8B5A2B;
  --r-muted: rgba(60, 53, 40, 0.55);
}
.reader[data-theme="light"] {
  --r-bg: #FFFFFF;
  --r-text: #1F1F1E;
  --r-chrome-bg: rgba(244, 240, 232, 0.92);
  --r-border: rgba(229, 225, 216, 0.7);
  --r-accent: #7C3AED;
  --r-muted: rgba(107, 104, 98, 0.7);
}
.reader[data-theme="dark"] {
  --r-bg: #1A1A19;
  --r-text: #DCD8CF;
  --r-chrome-bg: rgba(38, 36, 31, 0.92);
  --r-border: rgba(56, 53, 48, 0.6);
  --r-accent: #A78BFA;
  --r-muted: rgba(220, 216, 207, 0.5);
}

/* ============================================================
 * 沉浸 chrome(顶 / 底 toolbar)
 *   - chromeVisible=false 时整条平滑滑出 + 淡出
 *   - backdrop-filter 模糊背景,文字仍可见
 * ============================================================ */
.reader-toolbar {
  position: absolute;
  left: 0;
  right: 0;
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
.reader-toolbar--top {
  top: 0;
  border-bottom: 1px solid var(--r-border);
}
.reader-toolbar--bottom {
  bottom: 0;
  border-top: 1px solid var(--r-border);
}
.reader.is-chrome-hidden .reader-toolbar--top {
  transform: translateY(-100%);
  opacity: 0;
  pointer-events: none;
}
.reader.is-chrome-hidden .reader-toolbar--bottom {
  transform: translateY(100%);
  opacity: 0;
  pointer-events: none;
}

.reader-title {
  flex: 1;
  text-align: center;
  font-size: var(--text-base);
  font-weight: 500;
  letter-spacing: 0.5px;
  margin: 0;
  color: var(--r-text);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

/* ============================================================
 * 工具按钮(顶部)
 * ============================================================ */
.reader-tools {
  display: flex;
  align-items: center;
  gap: 8px;
}
.tool-group {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
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
}
.tool-btn:hover:not(:disabled) {
  background: var(--r-border);
}
.tool-btn--icon {
  padding-left: 8px;
}
.tool-btn--icon-only {
  padding: 5px 7px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.tool-btn--icon-only.is-active {
  background: var(--r-border);
  border-color: var(--r-border-strong, var(--r-border));
}
.chapter-jump-wrap {
  position: relative;
  display: inline-flex;
  align-items: center;
}
.tool-label {
  font-size: var(--text-xs);
  color: var(--r-muted);
  min-width: 16px;
  text-align: center;
}
.tool-sep {
  color: var(--r-border);
  font-size: var(--text-xs);
}

/* ============================================================
 * loading / error
 * ============================================================ */
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
.reader-state--error {
  color: #C24555;
}
.reader-state .ghost-btn {
  padding: 6px 16px;
  font-size: var(--text-sm);
  color: var(--r-text);
  background: transparent;
  border: 1px solid var(--r-border);
  border-radius: 6px;
  transition: all 120ms ease;
}
.reader-state .ghost-btn:hover {
  background: var(--r-border);
}

/* ============================================================
 * 内容区 — CSS Multi-column 横向分页
 *   column-fill: auto 让最后一栏不强制对齐 → 翻页语义对(不像 balance 让短文均分)
 *   break-inside: avoid 让段落尽量不跨栏
 * ============================================================ */
/* .reader-pager:纯滚动容器,viewport 满尺寸,不加 padding
 * 关键:padding 放在 .reader-pager 上会让 column-fill: auto 的"页高"与翻页 scrollLeft 跨度
 * 失配 → 文字布局错乱。padding 必须在 column 容器自身(box-sizing: border-box 挤压 column) */
.reader-pager {
  flex: 1;
  position: relative;
  overflow: hidden;
  cursor: default;
}

/* .reader-content:column 容器,占满父尺寸,padding 在自身上(border-box 挤压列)
 *
 * 关键决策 — column-gap: 0 + 每段 margin 模拟列间距:
 *   - 原 column-gap: 80 让横向延伸列之间也有 80gap → col_count*col_width + col_count*gap
 *     (每组左侧也算 1 gap)≠ viewport.width → 翻页每页累积 80px 错位 → "3 列残缺"
 *   - 新方案:column 紧贴(gap 0)→ col_width = viewport / col_count → scrollLeft 步长
 *     精确 = viewport.width → 完美对齐
 *   - 视觉列间距由 .reader-para 的 margin-left/right 40 模拟(左列右 margin + 右列左 margin = 80)
 *
 * 禁用 `contain: layout`:它会创建独立 layout 边界,封死 column-fill: auto 的横向 fragments */
.reader-content {
  width: 100%;
  height: 100%;
  box-sizing: border-box;
  column-gap: 0;
  column-fill: auto;
  column-rule: none;
  /* 上下留白(给顶 / 底 toolbar 视觉缓冲);左右留白由 spread 分别控制 */
  padding-top: 56px;
  padding-bottom: 56px;
  font-size: var(--reader-font-size);
  line-height: 1.95;
  /* 衬线字体序列:长文阅读舒适 — 优先用户系统已有的高质量字体 */
  font-family:
    "Songti SC",
    "STSong",
    "霞鹜文楷",
    "Source Han Serif SC",
    "Noto Serif CJK SC",
    "PingFang SC",
    "Hiragino Sans GB",
    "Microsoft YaHei",
    serif;
  letter-spacing: 0.5px;
  color: var(--r-text);
}

/* 双页 / 单页都不在 .reader-content 加 padding-L/R
 * 原因:column 横向延伸时,padding 只在 content-area 内生效,横向延伸列起点 = content-area
 * 末端,不等于 viewport 整数倍 → 翻页错位。padding 必须为 0,左右留白用 paragraph margin 处理 */
.reader[data-spread="double"] .reader-content {
  column-count: 2;
}
.reader[data-spread="single"] .reader-content {
  column-count: 1;
}
/* 段落 margin 模拟视觉留白(column-gap 设 0 后所有留白都在 paragraph 上)
 * 双页:左右各 40px(左列右margin + 右列左margin = 80 中缝)
 * 单页:左右各 max(40, (vw-720)/2) — 字段居中 720px 宽 */
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

/* P0H.2(2026-05-24)— 章节标题样式
 * 在 column 流里 break-before: column 让每章节起新列(双页时左右两列,单页时新页)
 * 字号略大 + 居中 + 上下留白,与正文形成视觉对比 */
.reader-chapter-title {
  font-size: calc(var(--reader-font-size, 17px) * 1.4);
  font-weight: 600;
  color: var(--text-1, #1a1a1a);
  text-align: center;
  margin: 1.5em 40px 1em 40px;
  break-before: column;
  break-after: avoid;
  letter-spacing: 0.1em;
}
.reader-chapter-title:first-child {
  break-before: avoid;  /* 第一章不强制起新列 */
  margin-top: 0.5em;
}
.reader[data-spread="single"] .reader-chapter-title {
  margin-left: max(40px, calc((100vw - 720px) / 2));
  margin-right: max(40px, calc((100vw - 720px) / 2));
}

/* ============================================================
 * 鼠标点击翻页提示区(左 / 右 1/3 hover 显箭头)
 * ============================================================ */
.reader-tap-hint {
  position: absolute;
  top: 0;
  bottom: 0;
  width: 33.33%;
  display: flex;
  align-items: center;
  pointer-events: none;     /* 点击事件由父 pager 接管 */
  opacity: 0;
  transition: opacity 180ms ease;
}
.reader-tap-hint--left {
  left: 0;
  justify-content: flex-start;
  padding-left: 24px;
}
.reader-tap-hint--right {
  right: 0;
  justify-content: flex-end;
  padding-right: 24px;
}
.reader-pager:hover .reader-tap-hint {
  opacity: 0.35;
}
.reader-pager:hover .reader-tap-hint:hover {
  opacity: 0.7;
}
.tap-arrow {
  font-size: 48px;
  color: var(--r-muted);
  line-height: 1;
  user-select: none;
}

/* ============================================================
 * 底部 footer 翻页 + 进度 + 字数
 * ============================================================ */
.page-btn {
  width: 36px;
  height: 36px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--r-text);
  background: transparent;
  border: 1px solid var(--r-border);
  border-radius: 6px;
  transition: all 120ms ease;
}
.page-btn:hover:not(:disabled) {
  background: var(--r-border);
}
.page-btn:disabled {
  opacity: 0.3;
  cursor: not-allowed;
}
.page-meta {
  min-width: 72px;
  text-align: center;
  font-size: var(--text-sm);
  color: var(--r-muted);
}
.progress-track {
  flex: 1;
  height: 4px;
  background: var(--r-border);
  border-radius: 2px;
  overflow: hidden;
  min-width: 120px;
}
.progress-fill {
  height: 100%;
  background: var(--r-accent);
  transition: width 220ms ease;
}
.char-count {
  font-size: var(--text-xs);
  color: var(--r-muted);
  white-space: nowrap;
}

/* ============================================================
 * 减弱动效用户偏好兼容
 * ============================================================ */
@media (prefers-reduced-motion: reduce) {
  .reader,
  .reader-toolbar,
  .progress-fill {
    transition: none !important;
  }
}
</style>
