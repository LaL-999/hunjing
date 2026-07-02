<script setup lang="ts">
/**
 * PlazaWorkDetailView — 作品广场「作品详情落地页」。v5(2026-07-02)。
 *
 * 点作品先进这里(不再直接进阅读器):封面 + 元信息 + 试读预览 + 动作
 * (在线阅读 / 下载 / 点赞),作者本人还能设权限(公开/私人 · 允许下载)+ 下架。
 * 漫画作品:内联首页预览 + 「在线阅读漫画」进全屏图阅读器。
 */
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { apiAssetUrl, ApiError } from "../api/client";
import { plazaApi, type PlazaWorkDetail, type PlazaComment } from "../api/plaza";
import { useAuthStore } from "../stores/auth";
import { useLoginModal } from "../composables/useLoginModal";
import { confirm as confirmDialog } from "../composables/useConfirm";
import { toast } from "../composables/useToast";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const loginModal = useLoginModal();

const MODE_LABELS: Record<string, string> = {
  initial: "初始态", middle: "中间态", end: "末尾态", cycle: "漫创态", screenplay: "剧创态",
};
function modeLabel(m: string): string { return MODE_LABELS[m] ?? "创作"; }

const workId = computed(() => String(route.params.id));
const work = ref<PlazaWorkDetail | null>(null);
const loading = ref(true);
const errorMsg = ref<string | null>(null);
const likeBusy = ref(false);
const dlBusy = ref(false);
const visBusy = ref(false);

const isComic = computed(() => work.value?.source_type === "comic");

// ===== 评论区 =====
const comments = ref<PlazaComment[]>([]);
const commentText = ref("");
const commentsLoading = ref(false);
const posting = ref(false);

async function load(): Promise<void> {
  loading.value = true;
  errorMsg.value = null;
  try {
    work.value = await plazaApi.detail(workId.value);
    void loadComments();
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) errorMsg.value = "作品不存在或已下架";
    else errorMsg.value = e instanceof ApiError ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
}
onMounted(load);

async function loadComments(): Promise<void> {
  commentsLoading.value = true;
  try {
    comments.value = (await plazaApi.listComments(workId.value)).items;
  } catch {
    /* 评论加载失败不阻塞详情 */
  } finally {
    commentsLoading.value = false;
  }
}

async function postComment(): Promise<void> {
  const text = commentText.value.trim();
  if (!text || posting.value) return;
  if (!auth.isAuthed) { loginModal.open(`/plaza/works/${workId.value}`); return; }
  posting.value = true;
  try {
    const c = await plazaApi.addComment(workId.value, text);
    comments.value.unshift(c);
    commentText.value = "";
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "评论失败");
  } finally {
    posting.value = false;
  }
}

async function removeComment(c: PlazaComment): Promise<void> {
  const ok = await confirmDialog({
    title: "删除这条评论?", message: "删除后无法恢复。", danger: true, confirmLabel: "删除",
  });
  if (!ok) return;
  try {
    await plazaApi.deleteComment(c.id);
    comments.value = comments.value.filter((x) => x.id !== c.id);
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "删除失败");
  }
}

/** 能否删这条评论:本人 或 作品作者 */
function canDelete(c: PlazaComment): boolean {
  return c.is_mine || !!work.value?.is_owner;
}
function commentAuthorName(c: PlazaComment): string { return c.author_nickname || "浑晶用户"; }
function fmtRelTime(iso: string): string {
  try {
    const d = new Date(iso).getTime();
    const diff = Date.now() - d;
    const min = Math.floor(diff / 60000);
    if (min < 1) return "刚刚";
    if (min < 60) return `${min} 分钟前`;
    const h = Math.floor(min / 60);
    if (h < 24) return `${h} 小时前`;
    const day = Math.floor(h / 24);
    if (day < 30) return `${day} 天前`;
    return new Date(iso).toLocaleDateString("zh-CN");
  } catch { return iso; }
}

function goRead(): void {
  router.push(`/plaza/works/${workId.value}/read`);
}

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

async function download(): Promise<void> {
  if (!work.value || dlBusy.value) return;
  dlBusy.value = true;
  try {
    await plazaApi.download(workId.value, work.value.title);
    toast.success("已开始下载");
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "下载失败");
  } finally {
    dlBusy.value = false;
  }
}

/** 作者:切公开/私人 或 允许下载 */
async function setVis(patch: { is_public?: number; allow_download?: number }): Promise<void> {
  if (!work.value || visBusy.value) return;
  visBusy.value = true;
  try {
    const updated = await plazaApi.setVisibility(workId.value, patch);
    if (work.value) {
      work.value.is_public = updated.is_public;
      work.value.allow_download = updated.allow_download;
    }
    toast.success("已更新");
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "更新失败");
  } finally {
    visBusy.value = false;
  }
}

async function unpublish(): Promise<void> {
  const ok = await confirmDialog({
    title: "下架这部作品?",
    message: "下架后其他用户将看不到它,你仍可在「我的发布」找到。",
    danger: true,
    confirmLabel: "下架",
  });
  if (!ok) return;
  try {
    await plazaApi.unpublish(workId.value);
    toast.success("已下架");
    router.push("/plaza");
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "下架失败");
  }
}

function authorName(): string { return work.value?.author_nickname || "浑晶创作者"; }
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
function fmtCount(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}w`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
function fmtAmount(): string {
  const w = work.value;
  if (!w) return "";
  if (w.source_type === "comic") return `${w.word_count} 页`;
  if (w.word_count >= 10000) return `${(w.word_count / 10000).toFixed(1)} 万字`;
  return `${w.word_count} 字`;
}
function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit" });
  } catch { return iso; }
}
</script>

<template>
  <div class="detail-page">
    <div class="topbar">
      <button class="back-link" type="button" @click="router.push('/plaza')">← 返回广场</button>
    </div>

    <div v-if="loading" class="state">加载中…</div>
    <div v-else-if="errorMsg" class="state state--err">
      {{ errorMsg }}
      <button class="ghost-btn" @click="router.push('/plaza')">回到广场</button>
    </div>

    <template v-else-if="work">
      <!-- 封面头 -->
      <header class="hero" :class="coverClass()" :style="coverStyle()">
        <div class="hero-shade"></div>
        <div class="hero-inner">
          <span class="mode-badge">{{ modeLabel(work.mode) }}</span>
          <h1 class="title">{{ work.title }}</h1>
          <p class="orig">
            <template v-if="work.original_title">原著《{{ work.original_title }}》· 二创</template>
            <template v-else>原创世界</template>
          </p>
        </div>
      </header>

      <!-- 元信息条 -->
      <div class="meta">
        <span class="author">
          <span class="av">
            <img v-if="work.author_avatar_url" :src="apiAssetUrl(work.author_avatar_url)" alt="" />
            <template v-else>{{ authorName()[0]?.toUpperCase() }}</template>
          </span>
          {{ authorName() }}
        </span>
        <span class="dot">·</span><span>{{ fmtDate(work.published_at) }}</span>
        <span class="dot">·</span><span>{{ fmtAmount() }}</span>
        <span class="dot">·</span>
        <span class="stat">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor"
               stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" /><circle cx="12" cy="12" r="3" />
          </svg>{{ fmtCount(work.read_count) }}
        </span>
        <span class="dot">·</span>
        <span class="stat">♥ {{ fmtCount(work.like_count) }}</span>
      </div>

      <p v-if="work.summary" class="summary">{{ work.summary }}</p>

      <!-- 漫画:首页预览 -->
      <section v-if="isComic" class="comic-preview">
        <img
          v-if="work.comic_pages && work.comic_pages.length"
          :src="apiAssetUrl(work.comic_pages[0])"
          class="comic-cover-page"
          alt="漫画首页"
        />
        <p class="comic-hint">共 {{ work.word_count }} 页 · 点击「在线阅读漫画」翻看全部</p>
      </section>

      <!-- 文本:试读预览 -->
      <section v-else-if="work.preview" class="text-preview">
        <div class="tp-label">试读</div>
        <p class="tp-body">{{ work.preview }}</p>
        <div class="tp-fade"></div>
      </section>

      <!-- 主动作 -->
      <div class="actions">
        <button class="btn btn-primary" @click="goRead">
          {{ isComic ? "在线阅读漫画 →" : "在线阅读全文 →" }}
        </button>
        <button
          v-if="!isComic && work.can_download"
          class="btn btn-ghost"
          :disabled="dlBusy"
          @click="download"
        >{{ dlBusy ? "下载中…" : "下载 Markdown" }}</button>
        <button class="btn btn-ghost like-btn" :class="{ on: work.liked }" :disabled="likeBusy" @click="toggleLike">
          <svg viewBox="0 0 24 24" width="15" height="15"
               :fill="work.liked ? 'currentColor' : 'none'"
               stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20.8 4.6a5.5 5.5 0 0 0-7.8 0L12 5.6l-1-1a5.5 5.5 0 0 0-7.8 7.8l1 1L12 21l7.8-7.6 1-1a5.5 5.5 0 0 0 0-7.8z" />
          </svg>
          {{ work.liked ? "已喜欢" : "喜欢" }}
        </button>
      </div>

      <!-- 作者:权限设置 -->
      <section v-if="work.is_owner" class="owner-box">
        <div class="ob-title">作品权限(仅你可见此设置)</div>
        <div class="ob-rows">
          <div class="ob-row">
            <div class="ob-info">
              <span class="ob-name">{{ work.is_public ? "公开" : "私人" }}</span>
              <span class="ob-desc">{{ work.is_public ? "展示在广场,所有人可阅读" : "仅你可见,不在广场展示" }}</span>
            </div>
            <button class="toggle" :class="{ on: work.is_public }" :disabled="visBusy"
              @click="setVis({ is_public: work.is_public ? 0 : 1 })" aria-label="切换公开/私人">
              <span class="knob"></span>
            </button>
          </div>
          <div class="ob-row" :class="{ 'is-disabled': !work.is_public || isComic }">
            <div class="ob-info">
              <span class="ob-name">允许他人下载</span>
              <span class="ob-desc">
                {{ isComic ? "漫画不提供文本下载" : "公开时,其他用户可下载正文 Markdown" }}
              </span>
            </div>
            <button class="toggle" :class="{ on: work.allow_download && work.is_public && !isComic }"
              :disabled="visBusy || !work.is_public || isComic"
              @click="setVis({ allow_download: work.allow_download ? 0 : 1 })" aria-label="切换允许下载">
              <span class="knob"></span>
            </button>
          </div>
        </div>
        <button class="unpub-btn" @click="unpublish">下架作品</button>
      </section>

      <!-- 评论区 -->
      <section class="comments">
        <h3 class="cm-title">评论 <span class="cm-count">{{ comments.length }}</span></h3>

        <!-- 发评论 -->
        <div class="cm-editor">
          <textarea
            v-model="commentText"
            class="cm-input"
            rows="3"
            maxlength="1000"
            :placeholder="auth.isAuthed ? '说点什么…' : '登录后即可评论'"
          />
          <div class="cm-editor-foot">
            <span class="cm-len">{{ commentText.length }}/1000</span>
            <button
              class="cm-post"
              :disabled="!commentText.trim() || posting"
              @click="postComment"
            >{{ posting ? "发布中…" : "发表评论" }}</button>
          </div>
        </div>

        <!-- 列表 -->
        <div v-if="commentsLoading" class="cm-state">加载评论中…</div>
        <div v-else-if="comments.length === 0" class="cm-state">还没有评论,来做第一个吧。</div>
        <ul v-else class="cm-list">
          <li v-for="c in comments" :key="c.id" class="cm-item">
            <span class="cm-av">
              <img v-if="c.author_avatar_url" :src="apiAssetUrl(c.author_avatar_url)" alt="" />
              <template v-else>{{ commentAuthorName(c)[0]?.toUpperCase() }}</template>
            </span>
            <div class="cm-body">
              <div class="cm-meta">
                <span class="cm-name">{{ commentAuthorName(c) }}</span>
                <span class="cm-time">{{ fmtRelTime(c.created_at) }}</span>
                <button v-if="canDelete(c)" class="cm-del" title="删除" @click="removeComment(c)">删除</button>
              </div>
              <p class="cm-text">{{ c.content }}</p>
            </div>
          </li>
        </ul>
      </section>
    </template>
  </div>
</template>

<style scoped>
.detail-page {
  max-width: 760px;
  margin: 0 auto;
  padding: var(--space-3) var(--space-6) var(--space-8);
}
.topbar { padding: var(--space-2) 0 var(--space-3); }
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

.hero {
  height: 220px;
  border-radius: var(--radius-xl);
  position: relative;
  overflow: hidden;
  display: flex;
  align-items: flex-end;
  background-size: cover;
  background-position: center;
  box-shadow: var(--shadow-md);
}
.hero-shade { position: absolute; inset: 0; background: linear-gradient(to top, rgba(0,0,0,0.6), transparent 72%); }
.hero-inner { position: relative; z-index: 2; padding: var(--space-6); color: #fff; }
.mode-badge {
  display: inline-block; font-size: 11px; font-weight: 600;
  background: rgba(255,255,255,0.94); color: var(--color-accent-text);
  padding: 4px 9px; border-radius: var(--radius-sm); margin-bottom: var(--space-3);
}
.title {
  font-size: var(--text-2xl); font-weight: 700;
  font-family: var(--font-serif, Georgia, serif); line-height: 1.3; margin: 0;
  text-shadow: 0 2px 14px rgba(0,0,0,0.4);
}
.orig { font-size: var(--text-sm); opacity: 0.92; margin: var(--space-2) 0 0; }

.meta {
  display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap;
  font-size: var(--text-sm); color: var(--color-text-muted);
  padding: var(--space-4) 0 var(--space-2);
}
.author { display: inline-flex; align-items: center; gap: 7px; color: var(--color-text); }
.av {
  width: 24px; height: 24px; border-radius: 50%;
  background: linear-gradient(135deg, #7C3AED, #22D3A8);
  display: inline-flex; align-items: center; justify-content: center;
  color: #fff; font-size: 11px; font-weight: 600; overflow: hidden;
}
.av img { width: 100%; height: 100%; object-fit: cover; }
.dot { color: var(--color-text-subtle); }
.stat { display: inline-flex; align-items: center; gap: 4px; }

.summary {
  font-size: var(--text-sm); color: var(--color-text-muted); line-height: 1.7;
  padding: var(--space-2) 0 var(--space-4); margin: 0;
  border-bottom: 1px solid var(--color-border);
}

.comic-preview { padding: var(--space-5) 0 var(--space-2); text-align: center; }
.comic-cover-page {
  max-width: 100%; max-height: 460px; border-radius: var(--radius-lg);
  border: 1px solid var(--color-border); box-shadow: var(--shadow-sm);
}
.comic-hint { font-size: var(--text-xs); color: var(--color-text-muted); margin: var(--space-3) 0 0; }

.text-preview { position: relative; padding: var(--space-5) 0 var(--space-2); }
.tp-label {
  font-size: 11px; font-weight: 600; letter-spacing: 2px;
  color: var(--color-accent-text); margin-bottom: var(--space-2);
}
.tp-body {
  font-size: 16px; line-height: 2; color: var(--color-text);
  font-family: var(--font-serif, Georgia, serif);
  margin: 0; max-height: 260px; overflow: hidden; white-space: pre-wrap;
}
.tp-fade {
  position: absolute; left: 0; right: 0; bottom: 0; height: 90px;
  background: linear-gradient(to bottom, transparent, var(--color-bg));
  pointer-events: none;
}

.actions {
  display: flex; flex-wrap: wrap; gap: var(--space-2);
  padding: var(--space-4) 0; border-top: 1px solid var(--color-border);
  margin-top: var(--space-2);
}
.btn {
  padding: 10px 20px; font-size: var(--text-sm); font-weight: 600;
  border-radius: var(--radius-md); cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
  display: inline-flex; align-items: center; gap: 6px;
}
.btn:disabled { opacity: 0.55; cursor: default; }
.btn-primary { color: #fff; background: linear-gradient(135deg, #8b5cf6, #6d28d9); border: none; }
.btn-primary:hover:not(:disabled) { opacity: 0.92; }
.btn-ghost { color: var(--color-text); background: var(--color-surface); border: 1px solid var(--color-border); }
.btn-ghost:hover:not(:disabled) { border-color: var(--color-border-strong); background: var(--color-surface-hover); }
.like-btn.on { color: #D9534F; border-color: #E8B4B4; background: rgba(217,83,79,0.06); }

.owner-box {
  margin-top: var(--space-5); padding: var(--space-4) var(--space-5);
  background: var(--color-bg-subtle); border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}
.ob-title { font-size: var(--text-sm); font-weight: 600; color: var(--color-text); margin-bottom: var(--space-3); }
.ob-rows { display: flex; flex-direction: column; gap: var(--space-3); }
.ob-row { display: flex; align-items: center; justify-content: space-between; gap: var(--space-4); }
.ob-row.is-disabled { opacity: 0.5; }
.ob-info { display: flex; flex-direction: column; gap: 2px; }
.ob-name { font-size: var(--text-sm); font-weight: 500; color: var(--color-text); }
.ob-desc { font-size: var(--text-xs); color: var(--color-text-muted); }
.toggle {
  position: relative; width: 42px; height: 24px; flex-shrink: 0;
  border-radius: 999px; border: none; cursor: pointer;
  background: var(--color-border-strong);
  transition: background var(--duration-fast) var(--ease-out);
}
.toggle.on { background: var(--color-accent); }
.toggle:disabled { cursor: default; }
.knob {
  position: absolute; top: 3px; left: 3px; width: 18px; height: 18px;
  border-radius: 50%; background: #fff;
  transition: transform var(--duration-fast) var(--ease-out);
}
.toggle.on .knob { transform: translateX(18px); }
.unpub-btn {
  margin-top: var(--space-4); font-size: var(--text-xs);
  color: var(--color-danger); background: transparent; border: none;
  cursor: pointer; text-decoration: underline;
}
.unpub-btn:hover { opacity: 0.8; }

/* 评论区 */
.comments { margin-top: var(--space-6); padding-top: var(--space-5); border-top: 1px solid var(--color-border); }
.cm-title { font-size: var(--text-base); font-weight: 600; color: var(--color-text); margin: 0 0 var(--space-4); }
.cm-count { color: var(--color-text-muted); font-weight: 400; margin-left: 4px; }
.cm-editor {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-3);
  margin-bottom: var(--space-5);
}
.cm-input {
  width: 100%;
  border: none;
  background: transparent;
  resize: vertical;
  font-size: var(--text-sm);
  line-height: 1.6;
  color: var(--color-text);
  outline: none;
  font-family: inherit;
}
.cm-editor-foot { display: flex; align-items: center; justify-content: space-between; margin-top: var(--space-2); }
.cm-len { font-size: var(--text-xs); color: var(--color-text-subtle); }
.cm-post {
  padding: 6px 16px; font-size: var(--text-sm); font-weight: 600;
  color: #fff; background: linear-gradient(135deg, #8b5cf6, #6d28d9);
  border: none; border-radius: var(--radius-md); cursor: pointer;
  transition: opacity var(--duration-fast) var(--ease-out);
}
.cm-post:hover:not(:disabled) { opacity: 0.9; }
.cm-post:disabled { opacity: 0.5; cursor: default; }
.cm-state { padding: var(--space-5); text-align: center; font-size: var(--text-sm); color: var(--color-text-muted); }
.cm-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-4); }
.cm-item { display: flex; gap: var(--space-3); }
.cm-av {
  width: 34px; height: 34px; flex-shrink: 0; border-radius: 50%;
  background: linear-gradient(135deg, #7C3AED, #22D3A8);
  display: inline-flex; align-items: center; justify-content: center;
  color: #fff; font-size: 13px; font-weight: 600; overflow: hidden;
}
.cm-av img { width: 100%; height: 100%; object-fit: cover; }
.cm-body { flex: 1; min-width: 0; }
.cm-meta { display: flex; align-items: center; gap: var(--space-2); }
.cm-name { font-size: var(--text-sm); font-weight: 500; color: var(--color-text); }
.cm-time { font-size: var(--text-xs); color: var(--color-text-subtle); }
.cm-del {
  margin-left: auto; font-size: var(--text-xs); color: var(--color-text-subtle);
  background: transparent; border: none; cursor: pointer;
}
.cm-del:hover { color: var(--color-danger); }
.cm-text {
  font-size: var(--text-sm); line-height: 1.7; color: var(--color-text);
  margin: 4px 0 0; white-space: pre-wrap; word-break: break-word;
}

.state { padding: var(--space-8); text-align: center; font-size: var(--text-sm); color: var(--color-text-muted); }
.state--err { color: var(--color-danger); }
.ghost-btn {
  margin-left: var(--space-2); padding: 4px 12px; font-size: var(--text-xs);
  color: var(--color-text); background: var(--color-surface);
  border: 1px solid var(--color-border); border-radius: var(--radius-sm); cursor: pointer;
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
