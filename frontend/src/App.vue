<script setup lang="ts">
/**
 * App.vue — sidebar + main 主框架。
 *
 * 全局挂载:
 *   - LoginModal(登录,内含本地 DocumentViewer 给协议勾选用)
 *   - NewProjectModal(新建项目)
 *   - DocumentViewer(全局协议查看,sidebar 用户菜单触发)
 *   - UpgradeModal(配额超限触发)
 *
 * 路由 meta.fullscreen=true 时不渲染 sidebar(给 3D 图谱全屏)。
 *
 * Quota 联动:auth 变化时自动 refresh quota,确保 sidebar QuotaIndicator 数据新鲜。
 */
import { computed, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import AddonPurchaseModal from "./components/AddonPurchaseModal.vue";
import AppSidebar from "./components/AppSidebar.vue";
import BeianFooter from "./components/BeianFooter.vue";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import DocumentViewer from "./components/DocumentViewer.vue";
import GlobalSearchModal from "./components/GlobalSearchModal.vue";
import LoginModal from "./components/LoginModal.vue";
import NewProjectModal from "./components/NewProjectModal.vue";
import ToastHost from "./components/ToastHost.vue";
import UpgradeModal from "./components/UpgradeModal.vue";
import { useDocumentViewer } from "./composables/useDocumentViewer";
import { registerGlobalSearchShortcut } from "./composables/useGlobalSearch";
import { useNewProjectModal } from "./composables/useNewProjectModal";
import { useSidebarLayout } from "./composables/useSidebarLayout";
import { useAuthStore } from "./stores/auth";
import { useQuotaStore } from "./stores/quota";
import type { Project, ProjectMode } from "./api/types";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const quota = useQuotaStore();
const newProjectModal = useNewProjectModal();
const docViewer = useDocumentViewer();
// 2026-06-08:sidebar 折叠展开全局状态(类 Claude 客户端)
const { collapsed: sidebarCollapsed, toggle: toggleSidebar } = useSidebarLayout();

// Sprint 6.A2 路线图 #6(2026-05-23):注册全局 Cmd+K / Ctrl+K 快捷键
// 路由在 /projects/:id 时自动 scope 到该项目;其他路由 = 全平台搜
registerGlobalSearchShortcut(() => route);

const isFullscreen = computed(() => route.meta.fullscreen === true);

// Sprint 6.A2 polish(2026-05-22):路由方向感
// 比较前后 route 的 meta.depth → slide-left(深入)/ slide-right(返回)/ page-fade(同级)
// 比起单调 fade,方向感让用户对"在层级中位置"有直觉
const transitionName = ref<"slide-left" | "slide-right" | "page-fade">("page-fade");
let lastDepth = (route.meta.depth as number | undefined) ?? 0;
watch(
  () => route.fullPath,
  () => {
    const newDepth = (route.meta.depth as number | undefined) ?? 0;
    if (newDepth > lastDepth) transitionName.value = "slide-left";
    else if (newDepth < lastDepth) transitionName.value = "slide-right";
    else transitionName.value = "page-fade";
    lastDepth = newDepth;
  },
);

// 登录 / 登出 触发 quota refresh / reset
watch(
  () => auth.isAuthed,
  (isAuthed) => {
    if (isAuthed) quota.refresh();
    else quota.reset();
  },
  { immediate: true },
);

function handleProjectCreated(p: Project, mode: ProjectMode) {
  newProjectModal.close();
  // 新建项目后刷一次配额(项目数 +1)
  quota.refresh();
  router.push(`/projects/${p.id}`);
  // mode 入参仍保留(向下兼容 + 未来 toast / 埋点拓展点);
  // 中间态 / 末尾态的"先上传作品文件"引导已在 ProjectView 内通过 ProjectUploadsPanel
  // + aiActionsDisabledReason("请先在「作品文件」区上传文件...")承担,无需重复 toast。
  void mode;
}
</script>

<template>
  <div
    class="app-shell"
    :class="{
      'app-shell--fullscreen': isFullscreen,
      'app-shell--sidebar-collapsed': sidebarCollapsed && !isFullscreen,
    }"
  >
    <AppSidebar v-if="!isFullscreen" />

    <!-- 2026-06-08:sidebar 折叠后的"展开"浮动按钮 — fixed 在屏幕左上 -->
    <button
      v-if="!isFullscreen && sidebarCollapsed"
      type="button"
      class="sidebar-expand-fab"
      title="展开侧栏"
      aria-label="展开侧栏"
      @click="toggleSidebar"
    >
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
        <rect x="3" y="3" width="18" height="18" rx="2" />
        <line x1="9" y1="3" x2="9" y2="21" />
      </svg>
    </button>

    <main class="app-main">
      <div class="app-main-content">
        <router-view v-slot="{ Component }">
          <transition :name="transitionName" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </div>
      <!-- 2026-06-05:ICP 备案号(工信部合规)— 主内容区底部居中,游客与登录态都见 -->
      <BeianFooter v-if="!isFullscreen" class="app-main-beian" variant="subtle" />
    </main>

    <!-- 全局模态 -->
    <LoginModal />
    <NewProjectModal
      :open="newProjectModal.isOpen.value"
      :mode="newProjectModal.mode.value"
      @close="newProjectModal.close()"
      @created="handleProjectCreated"
    />
    <DocumentViewer
      :open="docViewer.isOpen.value"
      @close="docViewer.close()"
    />
    <UpgradeModal />
    <!-- Sprint C.1:加购包独立 modal(QuotaIndicator "+ 加购" 触发)-->
    <AddonPurchaseModal />
    <!-- 1.M Polish:全平台二次确认弹窗(替代原生 confirm,中央 + 背景虚化)-->
    <ConfirmDialog />
    <!-- 1.M Polish:全平台轻提示(替代原生 alert,顶部居中 stack 自动消失)-->
    <ToastHost />
    <!-- Sprint 6.A2 路线图 #6(2026-05-23):全局 Cmd+K 搜索弹窗 -->
    <GlobalSearchModal />
  </div>
</template>

<style scoped>
.app-shell {
  display: flex;
  width: 100vw;
  height: 100vh;
  background: var(--color-bg);
}

.app-shell--fullscreen {
  display: block;
}

.app-main {
  flex: 1;
  min-width: 0;
  height: 100vh;
  /* 2026-06-05:改 flex column — 内容区滚动下放到 .app-main-content,Beian 永远贴底 */
  display: flex;
  flex-direction: column;
}

/* 2026-06-08:sidebar 折叠浮动展开按钮 — fixed 左上,跟 sidebar header 同高 */
.sidebar-expand-fab {
  position: fixed;
  top: 12px;
  left: 12px;
  z-index: var(--z-sticky);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-text-muted);
  cursor: pointer;
  box-shadow: var(--shadow-sm);
  transition: all var(--duration-fast) var(--ease-out);
}
.sidebar-expand-fab:hover {
  color: var(--color-accent);
  border-color: var(--color-accent-border);
  background: var(--color-surface-hover);
}

.app-main-content {
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}

.app-main-beian {
  flex-shrink: 0;
  padding: 10px 0 14px;
  display: flex;
  justify-content: center;
  /* 主内容区宽,subtle 默认的 border-top 全宽分隔线显得重,去掉留白净 */
  border-top: none !important;
}
.app-main-beian :deep(.beian-footer) {
  border-top: none !important;
  padding: 0 !important;
}

/* 同级页面切换:纯 fade(无方向感)*/
.page-fade-enter-active,
.page-fade-leave-active {
  transition: opacity var(--duration-page) var(--ease-out-soft);
}
.page-fade-enter-from,
.page-fade-leave-to {
  opacity: 0;
}

/* 深入(dashboard → project → graph 等):新页从右进入 + 旧页向左退出 */
.slide-left-enter-active,
.slide-left-leave-active {
  transition: transform var(--duration-page) var(--ease-out-soft),
              opacity var(--duration-page) var(--ease-out-soft);
}
.slide-left-enter-from {
  opacity: 0;
  transform: translateX(24px);
}
.slide-left-leave-to {
  opacity: 0;
  transform: translateX(-24px);
}

/* 返回(graph → project → dashboard 等):新页从左进入 + 旧页向右退出 */
.slide-right-enter-active,
.slide-right-leave-active {
  transition: transform var(--duration-page) var(--ease-out-soft),
              opacity var(--duration-page) var(--ease-out-soft);
}
.slide-right-enter-from {
  opacity: 0;
  transform: translateX(-24px);
}
.slide-right-leave-to {
  opacity: 0;
  transform: translateX(24px);
}
</style>
