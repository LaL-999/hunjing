<script setup lang="ts">
/**
 * MyScreenplaysView — 「我的剧本」列表页(2026-06-08)。
 *
 * 设计:
 *   - 跟 MyComicsView 平级,从 sidebar "我的剧本" tab 进入
 *   - 纯粹的书架体验:不含 ScreenplayHomeView 的 hero / 上传卡 / moat-hint
 *   - 信息密度更高:每张作品卡可显更多元数据(章数 / 字数 / 绑定状态 / 创建时间 / 是否已生成剧本)
 *   - 点击卡片 → /screenplay/novels/:id 直接进剧本编辑器
 *   - 顶部「新建作品」CTA → 跳 /screenplay(主页有上传卡)
 *
 * 路由:/my-screenplays(name: my-screenplays)
 */
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import {
  deleteNovel,
  listNovels,
} from "../screenplay/api/screenplay-client";
import type { NovelInfo } from "../screenplay/types/screenplay";
import { useAuthStore } from "../stores/auth";
import { useLoginModal } from "../composables/useLoginModal";
import { confirm } from "../composables/useConfirm";
import { toast } from "../composables/useToast";

const router = useRouter();
const auth = useAuthStore();
const loginModal = useLoginModal();

const novels = ref<NovelInfo[]>([]);
const loading = ref<boolean>(false);
const errorMsg = ref<string>("");

const hasNovels = computed(() => novels.value.length > 0);

async function load() {
  if (!auth.isAuthed) return;
  loading.value = true;
  errorMsg.value = "";
  try {
    novels.value = await listNovels();
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : "加载失败";
  } finally {
    loading.value = false;
  }
}

function openNovel(id: string) {
  router.push({ name: "screenplay-editor", params: { id } });
}

function gotoUpload() {
  router.push({ name: "screenplay-home" });
}

function backToDashboard() {
  router.push({ name: "dashboard" });
}

async function handleDelete(novel: NovelInfo, e: Event) {
  e.stopPropagation();
  const ok = await confirm({
    title: "删除作品",
    message: `确定删除《${novel.title}》及其所有剧本数据?`,
    confirmText: "删除",
    danger: true,
  });
  if (!ok) return;
  try {
    await deleteNovel(novel.id);
    novels.value = novels.value.filter((n) => n.id !== novel.id);
    toast.success("已删除");
  } catch (err) {
    toast.error(err instanceof Error ? err.message : "删除失败");
  }
}

function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  const now = new Date();
  const sameDay =
    d.getFullYear() === now.getFullYear() &&
    d.getMonth() === now.getMonth() &&
    d.getDate() === now.getDate();
  if (sameDay) {
    const hh = String(d.getHours()).padStart(2, "0");
    const mm = String(d.getMinutes()).padStart(2, "0");
    return `今天 ${hh}:${mm}`;
  }
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

onMounted(() => {
  if (!auth.isAuthed) {
    loginModal.open("/my-screenplays");
    return;
  }
  load();
});
</script>

<template>
  <main class="myss screenplay-module">
    <div class="myss-toolbar">
      <button
        type="button"
        class="back-btn"
        @click="backToDashboard"
        title="返回主页"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M19 12H5M12 19l-7-7 7-7" />
        </svg>
        <span>返回主页</span>
      </button>
    </div>

    <div class="myss-scroll">
      <div class="myss-inner">
        <!-- 顶部标题区 -->
        <header class="myss-hdr">
          <h1 class="myss-title literary-heading">我的剧本</h1>
          <p class="myss-sub">
            <span v-if="hasNovels">共 {{ novels.length }} 部作品</span>
            <span v-else-if="!loading && auth.isAuthed">还没有剧本作品</span>
          </p>
          <button
            type="button"
            class="new-btn"
            @click="gotoUpload"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 5v14 M5 12h14" />
            </svg>
            <span>新建作品</span>
          </button>
        </header>

        <!-- 未登录态 -->
        <section v-if="!auth.isAuthed" class="myss-guest">
          <p class="guest-text">登录后查看你的剧本作品</p>
          <button class="guest-cta" @click="loginModal.open('/my-screenplays')">登录</button>
        </section>

        <!-- 错误 -->
        <p v-else-if="errorMsg" class="myss-err">
          {{ errorMsg }}
          <button class="link-retry" @click="load">重试</button>
        </p>

        <!-- 加载中 -->
        <div v-else-if="loading" class="myss-loading">
          <div class="skeleton-card" v-for="i in 3" :key="i" />
        </div>

        <!-- 空态 -->
        <section v-else-if="!hasNovels" class="myss-empty">
          <svg class="empty-icon" width="44" height="44" viewBox="0 0 24 24"
               fill="none" stroke="currentColor" stroke-width="1.2"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
            <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
          </svg>
          <p class="empty-title">书架尚空</p>
          <p class="empty-hint">点击「新建作品」上传小说,AI 开始把它编成剧本</p>
        </section>

        <!-- 作品列表 -->
        <ul v-else class="myss-list">
          <li
            v-for="n in novels"
            :key="n.id"
            class="myss-card"
            @click="openNovel(n.id)"
          >
            <div class="myss-card-main">
              <h3 class="card-title literary">{{ n.title }}</h3>
              <div class="card-meta">
                <span class="meta-num mono">{{ n.total_chapters }}</span>
                <span class="meta-lbl">章</span>
                <span class="meta-dot">·</span>
                <span class="meta-num mono">{{ n.total_chars.toLocaleString() }}</span>
                <span class="meta-lbl">字</span>
                <span class="meta-dot">·</span>
                <span class="format-pill">{{ n.source_format }}</span>
                <span v-if="n.linked_project_id" class="meta-dot">·</span>
                <span v-if="n.linked_project_id" class="link-pill">
                  <svg width="10" height="10" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
                  </svg>
                  已绑定
                </span>
              </div>
              <p v-if="n.uploaded_at" class="card-time">{{ formatDate(n.uploaded_at) }}</p>
            </div>
            <div class="myss-card-actions">
              <button
                type="button"
                class="card-action card-action--danger"
                title="删除作品"
                @click="handleDelete(n, $event)"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                     stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
                  <path d="M3 6h18" />
                  <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6" />
                  <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                </svg>
              </button>
              <svg class="card-chev" width="16" height="16" viewBox="0 0 24 24"
                   fill="none" stroke="currentColor" stroke-width="1.5"
                   stroke-linecap="round" stroke-linejoin="round">
                <polyline points="9 18 15 12 9 6" />
              </svg>
            </div>
          </li>
        </ul>
      </div>
    </div>
  </main>
</template>

<style scoped>
.myss {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg);
  color: var(--text);
}

.myss-toolbar {
  flex-shrink: 0;
  padding: var(--space-4) var(--space-5);
}
.back-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px 6px 10px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  font-size: 12px;
  color: var(--text-muted);
  cursor: pointer;
  transition: all var(--transition-fast);
}
.back-btn:hover {
  background: var(--hover-bg);
  color: var(--text);
  border-color: var(--border);
}

.myss-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: var(--space-5) var(--space-5) var(--space-7);
}
.myss-inner {
  max-width: 880px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

/* 头部 */
.myss-hdr {
  display: flex;
  align-items: baseline;
  gap: var(--space-3);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--border-soft);
}
.myss-title {
  margin: 0;
  font-size: 26px;
  font-weight: 500;
  color: var(--text);
  font-family: var(--font-serif);
  letter-spacing: 0.02em;
}
.myss-sub {
  margin: 0;
  flex: 1;
  font-size: 12px;
  color: var(--text-muted);
  letter-spacing: 0.04em;
}
.new-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 7px 14px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 12.5px;
  font-weight: 500;
  cursor: pointer;
  transition: background var(--transition-fast);
}
.new-btn:hover {
  background: var(--accent-hover);
}

/* 未登录态 */
.myss-guest {
  text-align: center;
  padding: var(--space-12) 0;
}
.guest-text {
  margin: 0 0 var(--space-4);
  color: var(--text-muted);
  font-size: 14px;
}
.guest-cta {
  padding: 8px 24px;
  background: var(--accent);
  color: white;
  border: none;
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
}
.guest-cta:hover { background: var(--accent-hover); }

/* 错误 */
.myss-err {
  padding: 10px 14px;
  background: var(--danger-soft);
  border-left: 3px solid var(--danger);
  color: var(--danger);
  border-radius: var(--radius-sm);
  font-size: 13px;
}
.link-retry {
  margin-left: 8px;
  background: transparent;
  border: none;
  color: var(--accent);
  cursor: pointer;
  font-size: 12px;
  text-decoration: underline;
}

/* 加载骨架 */
.myss-loading {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
}
.skeleton-card {
  height: 76px;
  border-radius: var(--radius-md);
  background: linear-gradient(90deg,
    var(--card-bg) 0%,
    var(--bg-deep) 50%,
    var(--card-bg) 100%);
  background-size: 200% 100%;
  animation: shimmer 1.4s ease-in-out infinite;
}
@keyframes shimmer {
  0% { background-position: 200% 0; }
  100% { background-position: -200% 0; }
}

/* 空态 */
.myss-empty {
  text-align: center;
  padding: var(--space-12) 0;
  color: var(--text-subtle);
}
.empty-icon {
  opacity: 0.3;
  margin-bottom: var(--space-3);
}
.empty-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--text-muted);
  margin: 0 0 6px;
  font-family: var(--font-serif);
}
.empty-hint {
  font-size: 12px;
  margin: 0;
}

/* 作品列表 */
.myss-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.myss-card {
  display: flex;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-4) var(--space-5);
  background: var(--card-bg);
  border: 1px solid var(--border-soft);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
}
.myss-card:hover {
  border-color: var(--accent-border, var(--accent));
  box-shadow: var(--shadow-sm);
}
.myss-card-main {
  flex: 1;
  min-width: 0;
}
.card-title {
  margin: 0 0 4px;
  font-size: 15px;
  font-weight: 500;
  color: var(--text);
  font-family: var(--font-serif);
  letter-spacing: 0.01em;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.card-meta {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: 3px;
  font-size: 11.5px;
  letter-spacing: 0.04em;
  color: var(--text-subtle);
}
.meta-num {
  color: var(--text);
  font-weight: 500;
}
.meta-lbl {
  color: var(--text-muted);
}
.meta-dot {
  color: var(--text-subtle);
  opacity: 0.6;
  margin: 0 4px;
}
.format-pill {
  font-family: var(--font-mono);
  font-size: 10.5px;
  text-transform: uppercase;
  padding: 1px 6px;
  background: var(--bg-deep);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
}
.link-pill {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 10.5px;
  padding: 1px 6px;
  background: var(--accent-soft);
  color: var(--accent-text);
  border-radius: var(--radius-sm);
}
.card-time {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--text-subtle);
  font-family: var(--font-mono);
}

.myss-card-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  color: var(--text-subtle);
}
.card-action {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  color: var(--text-subtle);
  cursor: pointer;
  opacity: 0;
  transition: all var(--transition-fast);
}
.myss-card:hover .card-action {
  opacity: 1;
}
.card-action--danger:hover {
  background: var(--danger-soft);
  color: var(--danger);
}
.card-chev {
  color: var(--text-subtle);
  transition: transform var(--transition-fast);
}
.myss-card:hover .card-chev {
  transform: translateX(2px);
  color: var(--accent);
}

.mono { font-family: var(--font-mono); }
</style>
