<script setup lang="ts">
/**
 * ChapterJumpPopover — 阅读器章节跳转弹窗(2026-06-01)
 *
 * 设计:
 *   - 不居中弹出 — 锚定在汉堡按钮右下,与 toolbar 邻接
 *   - 宽 240px,最多显示 6 行(超出滚动)
 *   - 每行:"第 N 章 · 首段 ...(~X 字)" — 当前章高亮
 *   - 点击外部 / Esc 关闭
 *   - **鼠标不变手掌**:整个 popover 不用 cursor:pointer,用色差引导点击
 *
 * Props:
 *   chapters: ChapterEntry[]            章节列表
 *   currentChapter: number               高亮章号
 *   readerTheme: 'light' | 'sepia' | 'dark'  跟随阅读器主题
 *
 * Emits:
 *   jump(chapter): 用户点了某一章
 *   close():       用户点外部 / Esc / 选完了
 */
import { onBeforeUnmount, onMounted, ref, watch, nextTick } from "vue";

interface ChapterEntry {
  chapter: number;
  firstWords: string;
  charCount: number;
  itemIndex: number;
}

const props = defineProps<{
  chapters: ChapterEntry[];
  currentChapter: number;
  readerTheme: "light" | "sepia" | "dark";
}>();

const emit = defineEmits<{
  (e: "jump", chapter: number): void;
  (e: "close"): void;
}>();

const rootEl = ref<HTMLElement | null>(null);
const currentItemEl = ref<HTMLElement | null>(null);

function onJump(chapter: number) {
  emit("jump", chapter);
}

function onOutsideClick(e: MouseEvent) {
  if (!rootEl.value) return;
  if (!rootEl.value.contains(e.target as Node)) {
    emit("close");
  }
}

function onEscape(e: KeyboardEvent) {
  if (e.key === "Escape") emit("close");
}

onMounted(() => {
  // 延迟挂载 outside 监听,避免触发 popover 的那次点击立即关掉自己
  setTimeout(() => {
    document.addEventListener("mousedown", onOutsideClick);
  }, 0);
  document.addEventListener("keydown", onEscape);
  // 滚动到当前章节
  void nextTick(() => {
    currentItemEl.value?.scrollIntoView({ block: "nearest" });
  });
});

onBeforeUnmount(() => {
  document.removeEventListener("mousedown", onOutsideClick);
  document.removeEventListener("keydown", onEscape);
});

watch(() => props.currentChapter, () => {
  void nextTick(() => {
    currentItemEl.value?.scrollIntoView({ block: "nearest" });
  });
});
</script>

<template>
  <div
    ref="rootEl"
    class="chapter-jump-popover"
    :data-theme="readerTheme"
    role="dialog"
    aria-label="章节跳转"
  >
    <div class="popover-header">
      <span class="popover-title">章节列表</span>
      <span class="popover-count">{{ chapters.length }} 章</span>
    </div>
    <div v-if="chapters.length === 0" class="popover-empty">
      暂无章节(内容过短)
    </div>
    <ul v-else class="popover-list" role="listbox">
      <li
        v-for="c in chapters"
        :key="c.chapter"
        :ref="(el) => { if (c.chapter === currentChapter) currentItemEl = el as HTMLElement }"
        class="popover-item"
        :class="{ 'is-current': c.chapter === currentChapter }"
        role="option"
        :aria-selected="c.chapter === currentChapter"
        :title="`跳到第 ${c.chapter} 章(约 ${c.charCount} 字)`"
        tabindex="0"
        @click="onJump(c.chapter)"
        @keydown.enter="onJump(c.chapter)"
      >
        <span class="popover-item-no mono">第 {{ c.chapter }} 章</span>
        <span class="popover-item-preview">{{ c.firstWords || "—" }}</span>
        <span class="popover-item-chars mono">~{{ c.charCount }} 字</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
/* 阅读器三主题 token(与 SimulationReadView 对齐) */
.chapter-jump-popover {
  /* 默认 sepia */
  --cp-bg: #f7efe1;
  --cp-text: #3a2f24;
  --cp-muted: #8a7a62;
  --cp-border: #d9c7a8;
  --cp-hover: #ece0c7;
  --cp-current-bg: #cca25d22;
  --cp-current-text: #6b4b1f;
  --cp-shadow: rgba(60, 40, 16, 0.18);
}
.chapter-jump-popover[data-theme="light"] {
  --cp-bg: #ffffff;
  --cp-text: #1a1a1a;
  --cp-muted: #757575;
  --cp-border: #e2e2e2;
  --cp-hover: #f4f4f6;
  --cp-current-bg: #7c3aed14;
  --cp-current-text: #5b21b6;
  --cp-shadow: rgba(20, 20, 20, 0.15);
}
.chapter-jump-popover[data-theme="dark"] {
  --cp-bg: #1f2025;
  --cp-text: #e2e3e6;
  --cp-muted: #8b8d94;
  --cp-border: #34363c;
  --cp-hover: #2a2c33;
  --cp-current-bg: #7c3aed22;
  --cp-current-text: #c4b5fd;
  --cp-shadow: rgba(0, 0, 0, 0.4);
}

.chapter-jump-popover {
  position: absolute;
  top: calc(100% + 6px);
  right: 0;
  width: 260px;
  max-height: 360px;
  background: var(--cp-bg);
  color: var(--cp-text);
  border: 1px solid var(--cp-border);
  border-radius: 8px;
  box-shadow: 0 8px 24px var(--cp-shadow);
  z-index: 50;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  animation: cp-fade-in 160ms ease-out;
}
@keyframes cp-fade-in {
  from { opacity: 0; transform: translateY(-4px); }
  to   { opacity: 1; transform: translateY(0);    }
}

.popover-header {
  padding: 10px 14px;
  border-bottom: 1px solid var(--cp-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-shrink: 0;
}
.popover-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--cp-text);
}
.popover-count {
  font-size: 11px;
  color: var(--cp-muted);
}

.popover-empty {
  padding: 16px 14px;
  font-size: 13px;
  color: var(--cp-muted);
  text-align: center;
}

.popover-list {
  list-style: none;
  margin: 0;
  padding: 4px 0;
  overflow-y: auto;
  flex: 1;
}
.popover-list::-webkit-scrollbar {
  width: 6px;
}
.popover-list::-webkit-scrollbar-thumb {
  background: var(--cp-border);
  border-radius: 3px;
}

.popover-item {
  padding: 8px 14px;
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: baseline;
  gap: 8px;
  font-size: 13px;
  transition: background 80ms ease;
  outline: none;
  /* 鼠标不变手掌 — 用 default,色差引导点击 */
}
.popover-item:hover,
.popover-item:focus-visible {
  background: var(--cp-hover);
}
.popover-item.is-current {
  background: var(--cp-current-bg);
  color: var(--cp-current-text);
  font-weight: 500;
}
.popover-item-no {
  font-size: 12px;
  color: var(--cp-muted);
  white-space: nowrap;
}
.popover-item.is-current .popover-item-no {
  color: var(--cp-current-text);
}
.popover-item-preview {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 13px;
}
.popover-item-chars {
  font-size: 11px;
  color: var(--cp-muted);
  white-space: nowrap;
}

.mono {
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
}
</style>
