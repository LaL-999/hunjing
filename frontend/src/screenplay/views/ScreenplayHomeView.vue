<script setup lang="ts">
/**
 * 剧创态主页(阶段 6 重写,2026-06-08)。
 *
 * 改造:
 *   - 用 useConfirm / useToast 替 window.confirm / alert(项目铁律)
 *   - 删 hard-coded localhost:8003 链接
 *   - 加每本小说的「绑定项目」状态指示 + 入口
 *   - Header 增加"接通浑晶 4 大资产"差异化卖点
 */
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";

import NovelUploadCard from "../components/NovelUploadCard.vue";
import {
  deleteNovel,
  getHealth,
  listNovels,
  linkNovelToProject,
  listProjectsForLink,
} from "../api/screenplay-client";
import type { NovelInfo } from "../types/screenplay";
import type { Project } from "../../api/types";
import { confirm } from "../../composables/useConfirm";
import { toast } from "../../composables/useToast";
import { track } from "../../composables/useAnalytics";

const router = useRouter();

const backendStatus = ref<"unknown" | "ok" | "error">("unknown");
const backendInfo = ref<{
  version?: string;
  llm_model?: string;
  llm_configured?: boolean;
}>({});
const errorMsg = ref<string>("");

const novels = ref<NovelInfo[]>([]);
const novelsLoading = ref<boolean>(false);

// 阶段 5.1:每本小说的 linked_project_id 缓存(取自 getNovel)
const linkedProjectByNovel = ref<Record<string, string | null>>({});

// 阶段 5.1:link 抽屉
const linkDrawerOpen = ref(false);
const linkTargetNovelId = ref<string>("");
const linkTargetNovelTitle = ref<string>("");
const projectsForLink = ref<Project[]>([]);
const projectsLoading = ref(false);
const linkSubmitting = ref(false);

async function checkBackend() {
  try {
    backendInfo.value = await getHealth();
    backendStatus.value = "ok";
  } catch (e) {
    backendStatus.value = "error";
    errorMsg.value = e instanceof Error ? e.message : String(e);
  }
}

async function loadNovels() {
  novelsLoading.value = true;
  try {
    novels.value = await listNovels();
    // 2026-06-08 bug fix:list_novels 后端现已直接返 linked_project_id,
    // 不再 Promise.all N+1 拉详情(100 本 = 100 请求性能灾难)
    const linkMap: Record<string, string | null> = {};
    for (const n of novels.value) {
      linkMap[n.id] = n.linked_project_id ?? null;
    }
    linkedProjectByNovel.value = linkMap;
  } catch {
    novels.value = [];
  } finally {
    novelsLoading.value = false;
  }
}

function backToDashboard() {
  // 2026-06-08 bug fix:首页路由名是 "dashboard" 不是 "home",
  // 之前写错导致按钮点击无反应(路由名不存在,vue-router silently no-op)。
  router.push({ name: "dashboard" });
}

function openEditor(novelId: string) {
  router.push({ name: "screenplay-editor", params: { id: novelId } });
}

async function handleUploaded(novelId: string) {
  // 2026-06-09 埋点 — 剧创态小说上传转化(用户决定进入剧创流水线)
  track("screenplay_novel_upload", {
    mode: "screenplay",
    meta: { novel_id: novelId },
  });
  await loadNovels();
  openEditor(novelId);
}

async function handleDelete(novelId: string, title: string, ev: MouseEvent) {
  ev.stopPropagation();
  const ok = await confirm({
    title: `删除《${title}》?`,
    message: "同时清除该作品的所有章节、剧本、改编决策。该操作不可恢复。",
    danger: true,
    confirmLabel: "删除",
  });
  if (!ok) return;
  try {
    await deleteNovel(novelId);
    await loadNovels();
    toast.success(`已删除《${title}》`);
  } catch (e) {
    toast.error("删除失败:" + (e instanceof Error ? e.message : String(e)));
  }
}

// === 阶段 5.1 桥接 — Link / Unlink Project ===

async function openLinkDrawer(
  novelId: string,
  novelTitle: string,
  ev: MouseEvent,
) {
  ev.stopPropagation();
  linkTargetNovelId.value = novelId;
  linkTargetNovelTitle.value = novelTitle;
  linkDrawerOpen.value = true;
  // 懒加载 projects
  if (projectsForLink.value.length === 0) {
    projectsLoading.value = true;
    try {
      projectsForLink.value = await listProjectsForLink();
    } catch (e) {
      toast.error("加载浑晶项目失败:" + (e instanceof Error ? e.message : String(e)));
    } finally {
      projectsLoading.value = false;
    }
  }
}

function closeLinkDrawer() {
  linkDrawerOpen.value = false;
}

async function submitLink(projectId: string | null) {
  if (linkSubmitting.value) return;
  linkSubmitting.value = true;
  try {
    await linkNovelToProject(linkTargetNovelId.value, projectId);
    linkedProjectByNovel.value = {
      ...linkedProjectByNovel.value,
      [linkTargetNovelId.value]: projectId,
    };
    if (projectId) {
      toast.success("已绑定 — 后续 AI 会读取该项目的角色驱动力、知识边界、关系正负极");
    } else {
      toast.info("已解绑 — 接下来的 AI 调用不再依赖浑晶项目数据");
    }
    closeLinkDrawer();
  } catch (e) {
    toast.error("操作失败:" + (e instanceof Error ? e.message : String(e)));
  } finally {
    linkSubmitting.value = false;
  }
}

function linkedProjectName(novelId: string): string | null {
  const projectId = linkedProjectByNovel.value[novelId];
  if (!projectId) return null;
  const proj = projectsForLink.value.find((p) => p.id === projectId);
  return proj?.name ?? null;
}

onMounted(() => {
  checkBackend();
  loadNovels();
});
</script>

<template>
  <main class="home screenplay-module">
    <!-- 2026-06-08 用户精修 v2:返回按钮提到全宽 toolbar,不再嵌在居中 720px 内 -->
    <div class="home-toolbar">
      <button
        type="button"
        class="home-back-btn"
        @click="backToDashboard"
        title="返回主页"
      >
        <svg
          width="14" height="14" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
        >
          <path d="M19 12H5M12 19l-7-7 7-7" />
        </svg>
        <span>返回主页</span>
      </button>
    </div>

    <!-- 居中内容区,可滚 -->
    <div class="home-scroll">
      <div class="home-inner">
        <header class="hdr">
      <!-- 2026-06-08 用户精修:删 .brand 小字 + .sub-tagline,
           hero 区只留大标题 + tagline 主轴线 + moat-hint 浅灰提示 -->
      <h1 class="title literary-heading">剧创态</h1>
      <p class="tagline">小说 <span class="arrow">→</span> 剧本</p>

      <p class="moat-hint">
        绑定项目可接通 角色 · 知识 · 状态 · 关系
      </p>
    </header>

    <!-- 上传卡 — 直接醒目放最上 -->
    <NovelUploadCard @uploaded="handleUploaded" />

    <!-- 小说书架 -->
    <section v-if="!novelsLoading && novels.length > 0" class="shelf">
      <div class="shelf-title literary-heading">书架</div>
      <ul class="novel-list">
        <li
          v-for="n in novels"
          :key="n.id"
          class="novel-item"
          @click="openEditor(n.id)"
        >
          <div class="novel-main">
            <div class="novel-title literary">{{ n.title }}</div>
            <div class="novel-meta">
              <span>{{ n.total_chapters }} 章</span>
              <span class="meta-sep">·</span>
              <span>{{ n.total_chars.toLocaleString() }} 字</span>
              <span class="meta-sep">·</span>
              <span class="meta-format">{{ n.source_format }}</span>
              <!-- 阶段 5.1 桥接状态指示 -->
              <span class="meta-sep">·</span>
              <span
                v-if="linkedProjectByNovel[n.id]"
                class="link-status link-status--bound"
                :title="linkedProjectName(n.id) || '已绑定浑晶项目'"
              >
                <svg
                  width="11"
                  height="11"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  stroke-width="2"
                  stroke-linecap="round"
                  stroke-linejoin="round"
                  aria-hidden="true"
                >
                  <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
                  <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
                </svg>
                已绑定浑晶项目
              </span>
              <span v-else class="link-status link-status--unbound">未绑定</span>
            </div>
          </div>
          <button
            class="link-btn"
            :title="linkedProjectByNovel[n.id] ? '改绑 / 解绑' : '绑定浑晶项目'"
            @click="(e) => openLinkDrawer(n.id, n.title, e)"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
              <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
            </svg>
          </button>
          <button
            class="delete-btn"
            title="删除"
            @click="(e) => handleDelete(n.id, n.title, e)"
          >
            <svg
              width="14"
              height="14"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.5"
              stroke-linecap="round"
              stroke-linejoin="round"
            >
              <polyline points="3 6 5 6 21 6" />
              <path
                d="M19 6l-2 14a2 2 0 0 1-2 2H9a2 2 0 0 1-2-2L5 6m5 0V4a2 2 0 0 1 2-2h0a2 2 0 0 1 2 2v2"
              />
            </svg>
          </button>
          <span class="open-arrow">›</span>
        </li>
      </ul>
    </section>

    <p v-if="!novelsLoading && novels.length === 0" class="shelf-empty literary">
      书架尚空 — 拖一份小说试试。
    </p>

        <!-- 系统状态:2026-06-08 用户精修 — 只在异常时显示
             正常态隐藏,不再占据"父平台路由"占位行 -->
        <section
          v-if="backendStatus === 'error' || (backendStatus === 'ok' && backendInfo.llm_configured === false)"
          class="status-line"
        >
          <template v-if="backendStatus === 'ok' && backendInfo.llm_configured === false">
            <span class="dot dot--warn"></span>
            <span class="status-text status-warn">LLM 未配置 — 请先去配置 BYOK</span>
          </template>
          <template v-else-if="backendStatus === 'error'">
            <span class="dot dot--err"></span>
            <span class="status-text status-err">服务未连接 · {{ errorMsg }}</span>
          </template>
        </section>
      </div>
    </div>

    <!-- 阶段 5.1 link 抽屉 — 中央卡片,backdrop 虚化 -->
    <Teleport to="body">
      <div v-if="linkDrawerOpen" class="link-overlay" @click.self="closeLinkDrawer">
        <div class="link-drawer screenplay-module">
          <header class="drawer-hdr">
            <h3>绑定浑晶项目</h3>
            <p class="drawer-sub">
              《{{ linkTargetNovelTitle }}》绑定后,6 个 AI agent 会读取该项目的
              SP-2 / 3 / 4 / 7 资产用于生成 + 优化剧本
            </p>
          </header>

          <div v-if="projectsLoading" class="drawer-loading">加载项目列表…</div>

          <ul v-else-if="projectsForLink.length > 0" class="project-list">
            <li
              v-for="p in projectsForLink"
              :key="p.id"
              class="project-item"
              :class="{
                'project-item--current':
                  linkedProjectByNovel[linkTargetNovelId] === p.id,
              }"
              @click="submitLink(p.id)"
            >
              <div class="project-main">
                <div class="project-name">{{ p.name }}</div>
                <div class="project-meta">
                  <span class="mode-chip">{{ p.mode }}</span>
                  <span class="type-chip">{{ p.type }}</span>
                </div>
              </div>
              <span
                v-if="linkedProjectByNovel[linkTargetNovelId] === p.id"
                class="bound-mark"
              >当前绑定</span>
            </li>
          </ul>

          <p v-else class="drawer-empty">
            你还没在浑晶建过项目。先去
            <a href="#" @click.prevent="router.push({ name: 'dashboard' })"
              >Dashboard</a
            >
            选「初始态 / 中间态 / 末尾态」建一个。
          </p>

          <footer class="drawer-footer">
            <button
              v-if="linkedProjectByNovel[linkTargetNovelId]"
              class="btn-unlink"
              :disabled="linkSubmitting"
              @click="submitLink(null)"
            >
              解绑
            </button>
            <button class="btn-cancel" :disabled="linkSubmitting" @click="closeLinkDrawer">
              取消
            </button>
          </footer>
        </div>
      </div>
    </Teleport>
  </main>
</template>

<style scoped>
/* 2026-06-08 用户精修 v2:三层布局
 *   .home          全宽 flex column,锁高 + overflow hidden
 *     .home-toolbar  全宽 toolbar,返回按钮挂真正左上角(不嵌在 720 内)
 *     .home-scroll   flex 1 + 内部滚(剩余空间全占,撑开舒展感)
 *       .home-inner    max-width 720 居中,padding 控制呼吸
 */
.home {
  height: 100%;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  color: var(--text);
  background: var(--bg);
}

.home-toolbar {
  flex-shrink: 0;
  padding: var(--space-4) var(--space-5);
  display: flex;
  align-items: center;
}

/* 返回按钮 — ghost 风格,贴在真正的左上角(全宽 toolbar 内) */
.home-back-btn {
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
.home-back-btn:hover {
  background: var(--hover-bg);
  color: var(--text);
  border-color: var(--border);
}

/* 中央可滚区 — flex 1 占满 toolbar 之外的高度 */
.home-scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  /* 顶部留出空气,让 toolbar 和 hdr 之间不挤,但比之前 space-8 收紧 */
  padding: var(--space-5) var(--space-5) var(--space-7);
}

/* 居中容器 720px,负责 max-width + 视觉呼吸 */
.home-inner {
  max-width: 720px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
}

.hdr {
  text-align: center;
  margin: 0;
  padding: var(--space-3) 0 0;
  flex-shrink: 0;
}
.brand {
  font-family: var(--font-serif);
  font-size: 13px;
  color: var(--text-muted);
  letter-spacing: 0.32em;
  margin-bottom: var(--space-3);
}
.title {
  font-size: 38px;
  margin: 0 0 var(--space-3);
  font-weight: 500;
  color: var(--text-strong);
  letter-spacing: 0.04em;
  line-height: 1.2;
}
.tagline {
  font-family: var(--font-serif);
  color: var(--text-secondary);
  font-size: 16px;
  margin: 0 0 var(--space-2);
  letter-spacing: 0.12em;
}
.tagline .arrow {
  display: inline-block;
  margin: 0 var(--space-2);
  color: var(--accent);
  font-family: var(--font-sans);
  font-weight: 300;
  font-size: 18px;
  vertical-align: -1px;
}
.sub-tagline {
  font-family: var(--font-sans);
  color: var(--text-muted);
  font-size: 11.5px;
  margin: 0;
  letter-spacing: 0.16em;
}

/* === 阶段 6 桥接差异化卖点 banner === */
/* 2026-06-08 用户精修 v2:moat-hint — 跟 tagline / sub-tagline 同视觉层级
 * 衬线斜体浅灰小字,inline 跟随 hero 区,不再框 + chip 抢焦点 */
.moat-hint {
  margin: var(--space-3) 0 0;
  font-size: 12px;
  color: var(--text-subtle, var(--text-muted));
  font-family: var(--font-serif);
  font-style: italic;
  letter-spacing: 0.04em;
  opacity: 0.75;
}

/* === 书架 — 2026-06-08 用户精修 v2:
 * 不再 flex 1 + overflow auto(滚动已上提到 .home-scroll)
 * 让 shelf 自然撑开,父 .home-inner 的 gap 控制与上方间距
 */
.shelf {
  padding: 0;
  margin: 0;
}
.shelf-title {
  font-size: 12px;
  color: var(--text-muted);
  letter-spacing: 0.24em;
  margin-bottom: var(--space-4);
  padding-left: var(--space-2);
  text-transform: uppercase;
}
.shelf-empty {
  text-align: center;
  color: var(--text-muted);
  font-size: 14px;
  padding: var(--space-7) 0;
  margin: 0;
  font-style: italic;
}

.novel-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.novel-item {
  display: flex;
  align-items: center;
  padding: var(--space-4) var(--space-3);
  cursor: pointer;
  transition: all var(--transition-fast);
  border-radius: var(--radius-md);
}
.novel-item + .novel-item {
  border-top: 1px solid var(--border-soft);
}
.novel-item:hover {
  background: var(--hover-bg);
}
.novel-main {
  flex: 1;
}
.novel-title {
  font-size: 18px;
  font-weight: 500;
  color: var(--text-strong);
  margin-bottom: var(--space-1);
}
.novel-meta {
  font-size: 11.5px;
  color: var(--text-muted);
  letter-spacing: 0.04em;
  display: flex;
  gap: 6px;
  align-items: center;
  flex-wrap: wrap;
}
.meta-sep {
  color: var(--border);
}
.meta-format {
  font-family: var(--font-mono);
  font-size: 10.5px;
  text-transform: uppercase;
  padding: 1px 6px;
  background: var(--code-bg);
  border-radius: var(--radius-sm);
}
.link-status {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10.5px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
}
.link-status--bound {
  color: var(--accent-text);
  background: var(--accent-soft);
}
.link-status--unbound {
  color: var(--text-muted);
  background: var(--code-bg);
}
.open-arrow {
  color: var(--text-muted);
  font-size: 24px;
  padding-right: var(--space-2);
  font-family: var(--font-serif);
  transition: all var(--transition-fast);
}
.novel-item:hover .open-arrow {
  color: var(--accent);
  transform: translateX(2px);
}

.link-btn,
.delete-btn {
  background: transparent;
  border: 1px solid transparent;
  border-radius: var(--radius-md);
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--text-muted);
  cursor: pointer;
  margin-right: var(--space-2);
  opacity: 0;
  transition: all var(--transition-fast);
}
.novel-item:hover .link-btn,
.novel-item:hover .delete-btn {
  opacity: 1;
}
.link-btn:hover {
  color: var(--accent);
  background: var(--accent-soft);
}
.delete-btn:hover {
  color: var(--danger);
  background: var(--danger-soft);
}

/* === 状态行 === */
.status-line {
  margin-top: var(--space-7);
  padding-top: var(--space-4);
  border-top: 1px solid var(--border-soft);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  font-size: 11px;
  color: var(--text-muted);
  letter-spacing: 0.04em;
}
.status-text {
  display: flex;
  align-items: center;
  gap: 4px;
}
.status-meta {
  color: var(--text-muted);
  font-family: var(--font-mono);
  font-size: 10.5px;
}
.status-warn {
  color: var(--warning);
}
.status-err {
  color: var(--danger);
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex-shrink: 0;
}
.dot--ok { background: var(--success); }
.dot--err { background: var(--danger); }
.dot--warn { background: var(--warning); }
.dot--pending {
  background: var(--text-muted);
  animation: blink 1.6s ease-in-out infinite;
}
@keyframes blink {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.35; }
}

/* === Link 抽屉 === */
.link-overlay {
  position: fixed;
  inset: 0;
  background: rgba(20, 16, 12, 0.45);
  backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.link-drawer {
  background: var(--card-bg);
  border-radius: var(--radius-lg);
  padding: var(--space-6) var(--space-6) var(--space-5);
  max-width: 480px;
  width: 90vw;
  max-height: 80vh;
  display: flex;
  flex-direction: column;
  box-shadow: var(--shadow-lg);
  color: var(--text);
}
.drawer-hdr h3 {
  margin: 0 0 var(--space-2);
  font-size: 18px;
  font-weight: 600;
  color: var(--text-strong);
}
.drawer-sub {
  font-size: 12.5px;
  color: var(--text-muted);
  line-height: 1.6;
  margin: 0 0 var(--space-5);
}
.drawer-loading,
.drawer-empty {
  text-align: center;
  color: var(--text-muted);
  font-size: 13px;
  padding: var(--space-7) 0;
  margin: 0;
}
.drawer-empty a {
  color: var(--accent);
}
.project-list {
  list-style: none;
  margin: 0 0 var(--space-4);
  padding: 0;
  overflow-y: auto;
  flex: 1;
}
.project-item {
  display: flex;
  align-items: center;
  padding: var(--space-3) var(--space-4);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--transition-fast);
  border: 1px solid var(--border-soft);
  margin-bottom: var(--space-2);
}
.project-item:hover {
  background: var(--hover-bg);
  border-color: var(--accent);
}
.project-item--current {
  background: var(--accent-soft);
  border-color: var(--accent);
}
.project-main {
  flex: 1;
}
.project-name {
  font-size: 14.5px;
  font-weight: 500;
  color: var(--text-strong);
  margin-bottom: 3px;
}
.project-meta {
  display: flex;
  gap: var(--space-2);
}
.mode-chip,
.type-chip {
  font-size: 10.5px;
  padding: 1px 6px;
  background: var(--code-bg);
  color: var(--text-muted);
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
  font-family: var(--font-mono);
}
.bound-mark {
  font-size: 11px;
  color: var(--accent-text);
  padding: 2px 8px;
  background: var(--card-bg);
  border-radius: var(--radius-sm);
  font-weight: 500;
}
.drawer-footer {
  display: flex;
  gap: var(--space-3);
  justify-content: flex-end;
  margin-top: var(--space-4);
  padding-top: var(--space-3);
  border-top: 1px solid var(--border-soft);
}
.btn-unlink,
.btn-cancel {
  padding: var(--space-2) var(--space-5);
  border-radius: var(--radius-md);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: all var(--transition-fast);
  border: 1px solid var(--border);
}
.btn-unlink {
  color: var(--danger);
  background: var(--card-bg);
  border-color: var(--danger);
}
.btn-unlink:hover:not(:disabled) {
  background: var(--danger-soft);
}
.btn-cancel {
  color: var(--text);
  background: var(--card-bg);
}
.btn-cancel:hover:not(:disabled) {
  background: var(--hover-bg);
}
.btn-unlink:disabled,
.btn-cancel:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
</style>
