<script setup lang="ts">
/**
 * PlazaReadView — 作品广场在线阅读页。2026-06-25。
 *
 * 免费阅读一部上架作品的正文 + 顶部封面信息 + 点赞。进入即 +1 阅读量(后端处理)。
 *
 * 正文渲染:无 markdown 依赖(架构冻结闸门),按空行拆段 + 简单识别 # 标题行,
 * 与 SimulationReadView 的 split(/\n{2,}/) 口径一致。
 */
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { apiAssetUrl, ApiError } from "../api/client";
import { plazaApi, type PlazaWork } from "../api/plaza";
import { useAuthStore } from "../stores/auth";
import { useLoginModal } from "../composables/useLoginModal";
import { toast } from "../composables/useToast";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const loginModal = useLoginModal();

const MODE_LABELS: Record<string, string> = {
  initial: "初始态", middle: "中间态", end: "末尾态", cycle: "漫创态", screenplay: "剧创态",
};

const work = ref<PlazaWork | null>(null);
const loading = ref(true);
const errorMsg = ref<string | null>(null);
const likeBusy = ref(false);

const workId = computed(() => String(route.params.id));

interface Block { type: "h" | "p"; text: string }

/** 正文 → 段落块(# 开头识别为标题) */
const blocks = computed<Block[]>(() => {
  const raw = work.value?.content ?? "";
  return raw
    .split(/\n{2,}/)
    .map((s) => s.trim())
    .filter(Boolean)
    .map((s): Block => {
      const m = s.match(/^#{1,6}\s+(.*)$/);
      if (m) return { type: "h", text: m[1].trim() };
      // 段内单换行折叠成空格(避免硬换行破坏排版)
      return { type: "p", text: s.replace(/\n/g, " ") };
    });
});

async function load(): Promise<void> {
  loading.value = true;
  errorMsg.value = null;
  try {
    work.value = await plazaApi.read(workId.value);
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) {
      errorMsg.value = "作品不存在或已下架";
    } else {
      errorMsg.value = e instanceof ApiError ? e.message : "加载作品失败";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(load);

async function toggleLike(): Promise<void> {
  if (!work.value) return;
  if (!auth.isAuthed) {
    loginModal.open(`/plaza/works/${workId.value}`);
    return;
  }
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

function modeLabel(mode: string): string { return MODE_LABELS[mode] ?? "创作"; }
function coverClass(): string {
  const g = work.value?.cover_gradient ?? 1;
  return `g${g >= 1 && g <= 9 ? g : 1}`;
}
function coverStyle(): Record<string, string> {
  if (work.value?.cover_image_path) {
    return { backgroundImage: `url(${apiAssetUrl(work.value.cover_image_path)})` };
  }
  return {};
}
function authorName(): string { return work.value?.author_nickname || "浑晶创作者"; }
function fmtCount(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}w`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
function fmtWords(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万字`;
  return `${n} 字`;
}
function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
  } catch { return iso; }
}
</script>

<template>
  <div class="reader">
    <div class="reader-bar">
      <button class="back-link" type="button" @click="router.push('/plaza')">← 返回广场</button>
    </div>

    <div v-if="loading" class="state-msg">加载中…</div>
    <div v-else-if="errorMsg" class="state-msg state-error">
      {{ errorMsg }}
      <button class="link-btn" type="button" @click="router.push('/plaza')">回到广场</button>
    </div>

    <template v-else-if="work">
      <!-- 封面头 -->
      <header class="work-hero" :class="coverClass()" :style="coverStyle()">
        <div class="hero-overlay">
          <span class="mode-badge">{{ modeLabel(work.mode) }}</span>
          <h1 class="work-title">{{ work.title }}</h1>
          <p class="work-orig">
            <template v-if="work.original_title">原著《{{ work.original_title }}》· 二创</template>
            <template v-else>原创世界</template>
          </p>
        </div>
      </header>

      <!-- 元信息条 -->
      <div class="work-meta">
        <span class="author">
          <span class="a-av">
            <img v-if="work.author_avatar_url" :src="apiAssetUrl(work.author_avatar_url)" alt="" />
            <template v-else>{{ authorName()[0]?.toUpperCase() }}</template>
          </span>
          {{ authorName() }}
        </span>
        <span class="dot-sep">·</span>
        <span>{{ fmtDate(work.published_at) }}</span>
        <span class="dot-sep">·</span>
        <span>{{ fmtWords(work.word_count) }}</span>
        <span class="dot-sep">·</span>
        <span class="reads">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none"
               stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" /><circle cx="12" cy="12" r="3" />
          </svg>
          {{ fmtCount(work.read_count) }} 阅读
        </span>
      </div>

      <p v-if="work.summary" class="work-summary">{{ work.summary }}</p>

      <!-- 正文 -->
      <article class="work-body">
        <template v-for="(b, i) in blocks" :key="i">
          <h2 v-if="b.type === 'h'" class="body-h">{{ b.text }}</h2>
          <p v-else class="body-p">{{ b.text }}</p>
        </template>
      </article>

      <!-- 底部点赞 -->
      <div class="reader-footer">
        <button type="button" class="like-big" :class="{ on: work.liked }" :disabled="likeBusy" @click="toggleLike">
          <svg viewBox="0 0 24 24" width="18" height="18"
               :fill="work.liked ? 'currentColor' : 'none'"
               stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z" />
          </svg>
          <span>{{ work.liked ? "已喜欢" : "喜欢这部作品" }}</span>
          <span class="like-num">{{ fmtCount(work.like_count) }}</span>
        </button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.reader {
  max-width: 760px;
  margin: 0 auto;
  padding: var(--space-4) var(--space-6) var(--space-8);
}
.reader-bar { padding: var(--space-2) 0 var(--space-4); }
.back-link {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 0;
  transition: color var(--duration-fast) var(--ease-out);
}
.back-link:hover { color: var(--color-text); }

.work-hero {
  height: 240px;
  border-radius: var(--radius-xl);
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: flex-end;
  background-size: cover;
  background-position: center;
  box-shadow: var(--shadow-md);
}
.work-hero::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.55), transparent 70%);
}
.hero-overlay {
  position: relative;
  z-index: 2;
  padding: var(--space-6);
  color: #fff;
}
.mode-badge {
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.94);
  color: var(--color-accent-text);
  padding: 4px 9px;
  border-radius: var(--radius-sm);
  margin-bottom: var(--space-3);
}
.work-title {
  font-size: var(--text-2xl);
  font-weight: 700;
  font-family: var(--font-serif, Georgia, serif);
  line-height: 1.3;
  margin: 0;
  text-shadow: 0 2px 14px rgba(0, 0, 0, 0.4);
}
.work-orig { font-size: var(--text-sm); opacity: 0.9; margin: var(--space-2) 0 0; }

.work-meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-wrap: wrap;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  padding: var(--space-4) 0 var(--space-2);
}
.author { display: inline-flex; align-items: center; gap: 7px; color: var(--color-text); }
.a-av {
  width: 24px; height: 24px; border-radius: 50%;
  background: linear-gradient(135deg, #7C3AED, #22D3A8);
  display: inline-flex; align-items: center; justify-content: center;
  color: #fff; font-size: 11px; font-weight: 600; overflow: hidden;
}
.a-av img { width: 100%; height: 100%; object-fit: cover; }
.dot-sep { color: var(--color-text-subtle); }
.reads { display: inline-flex; align-items: center; gap: 4px; }

.work-summary {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: 1.7;
  padding: var(--space-2) 0 var(--space-3);
  margin: 0;
  border-bottom: 1px solid var(--color-border);
}

.work-body {
  padding: var(--space-6) 0 var(--space-4);
}
.body-h {
  font-size: var(--text-xl);
  font-weight: 700;
  color: var(--color-text);
  font-family: var(--font-serif, Georgia, serif);
  margin: var(--space-6) 0 var(--space-3);
}
.body-p {
  font-size: 17px;
  line-height: 2;
  color: var(--color-text);
  margin: 0 0 var(--space-4);
  font-family: var(--font-serif, Georgia, serif);
}

.reader-footer {
  display: flex;
  justify-content: center;
  padding: var(--space-6) 0 var(--space-4);
  border-top: 1px solid var(--color-border);
}
.like-big {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-6);
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.like-big:hover:not(:disabled) { border-color: #E8B4B4; color: #D9534F; }
.like-big.on { border-color: #E8B4B4; color: #D9534F; background: rgba(217, 83, 79, 0.06); }
.like-big:disabled { opacity: 0.6; cursor: default; }
.like-num { font-family: var(--font-mono); opacity: 0.8; }

.state-msg {
  padding: var(--space-8);
  text-align: center;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}
.state-error { color: var(--color-danger); }
.link-btn {
  margin-left: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: transparent;
  border: none;
  text-decoration: underline;
  cursor: pointer;
}

.g1 { background-image: linear-gradient(135deg, #5B6CF0, #9B5CF0); }
.g2 { background-image: linear-gradient(135deg, #0FB5A8, #1E6FE0); }
.g3 { background-image: linear-gradient(135deg, #E0792F, #C13E6A); }
.g4 { background-image: linear-gradient(135deg, #7C3AED, #3B1F8B); }
.g5 { background-image: linear-gradient(135deg, #2D9E6F, #16607A); }
.g6 { background-image: linear-gradient(135deg, #C2456A, #7A2E8E); }
.g7 { background-image: linear-gradient(135deg, #4661C9, #22324F); }
.g8 { background-image: linear-gradient(135deg, #B8843E, #7A4E2E); }
.g9 { background-image: linear-gradient(135deg, #6E59C2, #9686C2); }
</style>
