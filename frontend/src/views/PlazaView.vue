<script setup lang="ts">
/**
 * PlazaView — 作品广场(社区发布画廊)。2026-06-25。
 *
 * 用户把浑晶创作的作品上架到这里,所有人免费在线阅读 + 点赞。
 * 三种排序:热门(时间衰减 + 新作保护)/ 最新 / 经典(累计赞)。
 *
 * 视觉:网易云风 — 渐变 / 上传封面 + 创作态徽标 + 原著标签 + 作者 + 点赞 + 元信息。
 * 设计稿:mockups/plaza.html(用户已批准)。
 *
 * stale-while-revalidate:切换排序 tab 时保留旧列表,新数据到了再替换(不闪白)。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";

import { apiAssetUrl, ApiError } from "../api/client";
import { plazaApi, type PlazaCard, type PlazaSort } from "../api/plaza";
import { useAuthStore } from "../stores/auth";
import { useLoginModal } from "../composables/useLoginModal";
import { toast } from "../composables/useToast";
import PublishWorkModal from "../components/PublishWorkModal.vue";

const router = useRouter();
const auth = useAuthStore();
const loginModal = useLoginModal();

type Tab = PlazaSort | "mine";

const SORT_TABS: { key: Tab; label: string }[] = [
  { key: "hot", label: "热门" },
  { key: "new", label: "最新" },
  { key: "classic", label: "经典" },
  { key: "mine", label: "我的发布" },
];

const MODE_LABELS: Record<string, string> = {
  initial: "初始态",
  middle: "中间态",
  end: "末尾态",
  cycle: "漫创态",
  screenplay: "剧创态",
};

const activeTab = ref<Tab>("hot");
const works = ref<PlazaCard[]>([]);
const loading = ref(false);
const errorMsg = ref<string | null>(null);
const publishOpen = ref(false);
/** 正在切换点赞的作品 id 集合(防抖 + 禁重复点击) */
const likeBusy = ref<Set<string>>(new Set());

async function load(tab: Tab, opts: { silent?: boolean } = {}): Promise<void> {
  if (!opts.silent) loading.value = true;
  errorMsg.value = null;
  try {
    if (tab === "mine") {
      if (!auth.isAuthed) {
        works.value = [];
        return;
      }
      const resp = await plazaApi.myWorks();
      works.value = resp.items;
    } else {
      const resp = await plazaApi.list(tab, 48, 0);
      works.value = resp.items;
    }
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : "加载作品广场失败";
  } finally {
    loading.value = false;
  }
}

function switchTab(tab: Tab): void {
  if (tab === activeTab.value) return;
  activeTab.value = tab;
  // stale-while-revalidate:保留旧列表(silent),新数据到了再替换
  void load(tab, { silent: works.value.length > 0 });
}

onMounted(() => void load(activeTab.value));
// 登录态变化(登录 / 登出)→ 重新拉(尤其影响 liked 标记 + 我的发布)
watch(() => auth.isAuthed, () => void load(activeTab.value, { silent: true }));

const isEmpty = computed(() => !loading.value && works.value.length === 0);

function openPublish(): void {
  if (!auth.isAuthed) {
    loginModal.open("/plaza");
    return;
  }
  publishOpen.value = true;
}

function onPublished(): void {
  publishOpen.value = false;
  toast.success("作品已上架到广场");
  // 切到「我的发布」让用户立刻看到
  activeTab.value = "mine";
  void load("mine");
}

function openWork(work: PlazaCard): void {
  router.push(`/plaza/works/${work.id}`);
}

async function toggleLike(work: PlazaCard, ev: Event): Promise<void> {
  ev.stopPropagation();
  if (!auth.isAuthed) {
    loginModal.open("/plaza");
    return;
  }
  if (likeBusy.value.has(work.id)) return;
  const next = !work.liked;
  // 乐观更新
  work.liked = next;
  work.like_count = Math.max(0, work.like_count + (next ? 1 : -1));
  const busy = new Set(likeBusy.value);
  busy.add(work.id);
  likeBusy.value = busy;
  try {
    const res = await plazaApi.like(work.id, next);
    work.liked = res.liked;
    work.like_count = res.like_count;
  } catch (e) {
    // 回滚
    work.liked = !next;
    work.like_count = Math.max(0, work.like_count + (next ? -1 : 1));
    toast.error(e instanceof ApiError ? e.message : "操作失败,请重试");
  } finally {
    const b = new Set(likeBusy.value);
    b.delete(work.id);
    likeBusy.value = b;
  }
}

// ========== 显示工具 ==========

function modeLabel(mode: string): string {
  return MODE_LABELS[mode] ?? "创作";
}

function coverClass(work: PlazaCard): string {
  const g = work.cover_gradient >= 1 && work.cover_gradient <= 9 ? work.cover_gradient : 1;
  return `g${g}`;
}

function coverStyle(work: PlazaCard): Record<string, string> {
  if (work.cover_image_path) {
    return { backgroundImage: `url(${apiAssetUrl(work.cover_image_path)})` };
  }
  return {};
}

function authorName(work: PlazaCard): string {
  return work.author_nickname || "浑晶创作者";
}

function authorInitial(work: PlazaCard): string {
  const n = authorName(work);
  return n[0]?.toUpperCase() ?? "?";
}

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
    return new Date(iso).toLocaleDateString("zh-CN", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
  } catch {
    return iso;
  }
}
</script>

<template>
  <div class="plaza">
    <!-- 标题区 -->
    <header class="plaza-head">
      <div class="head-left">
        <button class="back-link" type="button" @click="router.push('/playground')">
          ← 更多玩法
        </button>
        <h1 class="plaza-title">作品广场</h1>
        <p class="plaza-sub">大家用浑晶写出来的故事,都在这里 · 免费在线阅读</p>
      </div>
      <button class="pub-btn" type="button" @click="openPublish">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none"
             stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M12 5v14M5 12h14" />
        </svg>
        上架我的作品
      </button>
    </header>

    <!-- 排序 tab -->
    <nav class="tabs" role="tablist">
      <button
        v-for="t in SORT_TABS"
        :key="t.key"
        type="button"
        role="tab"
        class="tab"
        :class="{ 'is-active': activeTab === t.key }"
        :aria-selected="activeTab === t.key"
        @click="switchTab(t.key)"
      >{{ t.label }}</button>
    </nav>

    <!-- 列表 -->
    <div v-if="loading && works.length === 0" class="grid">
      <div v-for="i in 6" :key="i" class="card card--skeleton">
        <div class="cover cover--skeleton" />
        <div class="info">
          <div class="sk-line" style="width: 60%" />
          <div class="sk-line" style="width: 90%" />
        </div>
      </div>
    </div>

    <div v-else-if="errorMsg" class="state-msg state-error">
      {{ errorMsg }}
      <button class="retry-btn" type="button" @click="load(activeTab)">重试</button>
    </div>

    <div v-else-if="isEmpty" class="state-msg">
      <template v-if="activeTab === 'mine'">
        你还没有上架任何作品 —
        <button class="link-btn" type="button" @click="openPublish">现在上架一部</button>
      </template>
      <template v-else>
        广场还很安静 — 成为第一个上架作品的人吧。
      </template>
    </div>

    <div v-else class="grid">
      <article
        v-for="work in works"
        :key="work.id"
        class="card"
        @click="openWork(work)"
      >
        <div class="cover" :class="coverClass(work)" :style="coverStyle(work)">
          <span class="mode-badge">{{ modeLabel(work.mode) }}</span>
          <span
            v-if="activeTab === 'mine' && work.is_public === 0"
            class="offline-badge"
          >已下架</span>
          <span class="cover-title">{{ work.title }}</span>
        </div>
        <div class="info">
          <div class="orig">
            <template v-if="work.original_title">
              原著《<b>{{ work.original_title }}</b>》· 二创
            </template>
            <template v-else>原创世界</template>
          </div>
          <div class="row">
            <span class="author">
              <span class="a-av" :class="`av${(authorInitial(work).charCodeAt(0) % 4) + 1}`">
                <img
                  v-if="work.author_avatar_url"
                  :src="apiAssetUrl(work.author_avatar_url)"
                  alt=""
                  class="a-av-img"
                />
                <template v-else>{{ authorInitial(work) }}</template>
              </span>
              {{ authorName(work) }}
            </span>
            <button
              type="button"
              class="like"
              :class="{ on: work.liked }"
              :disabled="likeBusy.has(work.id)"
              @click="toggleLike(work, $event)"
            >
              <svg viewBox="0 0 24 24" width="14" height="14"
                   :fill="work.liked ? 'currentColor' : 'none'"
                   stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z" />
              </svg>
              {{ fmtCount(work.like_count) }}
            </button>
          </div>
          <div class="meta">
            <span>{{ fmtDate(work.published_at) }}</span>
            <span>·</span>
            <span>{{ fmtWords(work.word_count) }}</span>
            <span>·</span>
            <span class="meta-reads">
              <svg viewBox="0 0 24 24" width="12" height="12" fill="none"
                   stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
              {{ fmtCount(work.read_count) }}
            </span>
          </div>
        </div>
      </article>
    </div>

    <PublishWorkModal
      :open="publishOpen"
      @close="publishOpen = false"
      @published="onPublished"
    />
  </div>
</template>

<style scoped>
.plaza {
  max-width: 1180px;
  margin: 0 auto;
  padding: var(--space-6) var(--space-6) var(--space-8);
}

/* ===== 标题区 ===== */
.plaza-head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: var(--space-4);
  padding: var(--space-4) 0 var(--space-5);
}
.head-left {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.back-link {
  align-self: flex-start;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 0;
  transition: color var(--duration-fast) var(--ease-out);
}
.back-link:hover { color: var(--color-text); }
.plaza-title {
  font-size: var(--text-2xl);
  font-weight: 700;
  color: var(--color-text);
  font-family: var(--font-serif, Georgia, serif);
  letter-spacing: 0.04em;
  margin: 0;
}
.plaza-sub {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin: 0;
}
.pub-btn {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5);
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: none;
  border-radius: var(--radius-lg);
  cursor: pointer;
  box-shadow: 0 6px 18px rgba(124, 58, 237, 0.24);
  transition: background var(--duration-fast) var(--ease-out),
              transform var(--duration-base) var(--ease-out);
}
.pub-btn:hover {
  background: var(--color-accent-hover);
  transform: translateY(-1px);
}

/* ===== 排序 tab ===== */
.tabs {
  display: flex;
  gap: var(--space-1);
  border-bottom: 1px solid var(--color-border);
  margin-bottom: var(--space-5);
}
.tab {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  cursor: pointer;
  transition: color var(--duration-fast) var(--ease-out),
              border-color var(--duration-fast) var(--ease-out);
}
.tab:hover { color: var(--color-text); }
.tab.is-active {
  color: var(--color-accent);
  border-bottom-color: var(--color-accent);
  font-weight: 600;
}

/* ===== 网格 ===== */
.grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-5);
}
@media (max-width: 920px) {
  .grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 560px) {
  .grid { grid-template-columns: 1fr; }
}

.card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  overflow: hidden;
  box-shadow: var(--shadow-sm);
  cursor: pointer;
  display: flex;
  flex-direction: column;
  transition: transform var(--duration-base) var(--ease-out),
              box-shadow var(--duration-base) var(--ease-out),
              border-color var(--duration-base) var(--ease-out);
}
.card:hover {
  transform: translateY(-4px);
  box-shadow: var(--shadow-lg);
  border-color: var(--color-accent-border);
}

/* 封面 */
.cover {
  height: 172px;
  position: relative;
  display: flex;
  align-items: flex-end;
  padding: var(--space-4);
  color: #fff;
  overflow: hidden;
  background-size: cover;
  background-position: center;
}
.cover::after {
  content: "";
  position: absolute;
  inset: 0;
  background: linear-gradient(to top, rgba(0, 0, 0, 0.46), transparent 64%);
}
.cover-title {
  position: relative;
  z-index: 2;
  font-size: var(--text-lg);
  font-weight: 700;
  line-height: 1.3;
  font-family: var(--font-serif, Georgia, serif);
  text-shadow: 0 2px 12px rgba(0, 0, 0, 0.4);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.mode-badge {
  position: absolute;
  top: 13px;
  left: 13px;
  z-index: 3;
  font-size: 11px;
  font-weight: 600;
  background: rgba(255, 255, 255, 0.94);
  color: var(--color-accent-text);
  padding: 4px 9px;
  border-radius: var(--radius-sm);
}
.offline-badge {
  position: absolute;
  top: 13px;
  right: 13px;
  z-index: 3;
  font-size: 11px;
  font-weight: 600;
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  padding: 4px 9px;
  border-radius: var(--radius-sm);
}

/* 封面渐变(对齐 mockup g1-g9) */
.g1 { background-image: linear-gradient(135deg, #5B6CF0, #9B5CF0); }
.g2 { background-image: linear-gradient(135deg, #0FB5A8, #1E6FE0); }
.g3 { background-image: linear-gradient(135deg, #E0792F, #C13E6A); }
.g4 { background-image: linear-gradient(135deg, #7C3AED, #3B1F8B); }
.g5 { background-image: linear-gradient(135deg, #2D9E6F, #16607A); }
.g6 { background-image: linear-gradient(135deg, #C2456A, #7A2E8E); }
.g7 { background-image: linear-gradient(135deg, #4661C9, #22324F); }
.g8 { background-image: linear-gradient(135deg, #B8843E, #7A4E2E); }
.g9 { background-image: linear-gradient(135deg, #6E59C2, #9686C2); }

/* info */
.info {
  padding: var(--space-4);
  display: flex;
  flex-direction: column;
}
.orig {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin-bottom: var(--space-3);
}
.orig b { color: var(--color-text); font-weight: 600; }
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}
.author {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  min-width: 0;
}
.a-av {
  width: 22px;
  height: 22px;
  border-radius: 50%;
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 11px;
  font-weight: 600;
  color: #fff;
  overflow: hidden;
}
.a-av-img { width: 100%; height: 100%; object-fit: cover; }
.av1 { background: linear-gradient(135deg, #7C3AED, #22D3A8); }
.av2 { background: linear-gradient(135deg, #E0792F, #C13E6A); }
.av3 { background: linear-gradient(135deg, #0FB5A8, #1E6FE0); }
.av4 { background: linear-gradient(135deg, #C2456A, #7A2E8E); }

.like {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  padding: 4px 10px;
  background: var(--color-surface);
  cursor: pointer;
  flex-shrink: 0;
  transition: all var(--duration-fast) var(--ease-out);
}
.like:hover:not(:disabled) { border-color: #E8B4B4; color: #D9534F; }
.like.on { border-color: #E8B4B4; color: #D9534F; background: rgba(217, 83, 79, 0.06); }
.like:disabled { opacity: 0.6; cursor: default; }

.meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 11px;
  color: var(--color-text-subtle);
  margin-top: var(--space-3);
}
.meta-reads {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

/* ===== 状态 ===== */
.state-msg {
  padding: var(--space-8) var(--space-4);
  text-align: center;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}
.state-error { color: var(--color-danger); }
.retry-btn,
.link-btn {
  margin-left: var(--space-2);
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: transparent;
  border: 1px solid var(--color-accent-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.link-btn { border: none; text-decoration: underline; }

/* ===== skeleton ===== */
.card--skeleton { pointer-events: none; }
.cover--skeleton {
  height: 172px;
  background: var(--color-bg-subtle);
}
.sk-line {
  height: 12px;
  margin-bottom: var(--space-2);
  border-radius: var(--radius-sm);
  background: var(--color-bg-subtle);
}
</style>
