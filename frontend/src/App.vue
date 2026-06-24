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
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import AddonPurchaseModal from "./components/AddonPurchaseModal.vue";
import AppSidebar from "./components/AppSidebar.vue";
import BeianFooter from "./components/BeianFooter.vue";
import ConfirmDialog from "./components/ConfirmDialog.vue";
import DocumentViewer from "./components/DocumentViewer.vue";
import GlobalSearchModal from "./components/GlobalSearchModal.vue";
import LoginModal from "./components/LoginModal.vue";
import NewProjectModal from "./components/NewProjectModal.vue";
import PaymentModal from "./components/PaymentModal.vue";
import ToastHost from "./components/ToastHost.vue";
import UpgradeModal from "./components/UpgradeModal.vue";
import { confirm } from "./composables/useConfirm";
import { useDocumentViewer } from "./composables/useDocumentViewer";
import { registerGlobalSearchShortcut } from "./composables/useGlobalSearch";
import { useNewProjectModal } from "./composables/useNewProjectModal";
import { useSidebarLayout } from "./composables/useSidebarLayout";
import { toast } from "./composables/useToast";
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

// 2026-06-24:桌面客户端(Tauri 壳)自动更新检查 —— Web 版完全跳过。
// 仅当运行在 Tauri 内(window.__TAURI_INTERNALS__ 存在)才执行;updater/process
// 插件走动态 import,不进 Web 包。任何失败静默吞掉,绝不阻断使用。
onMounted(async () => {
  if (!("__TAURI_INTERNALS__" in window)) return;
  try {
    const { check } = await import("@tauri-apps/plugin-updater");
    const update = await check();
    if (!update) return;
    toast.info(`正在下载新版本 ${update.version}…`);
    await update.downloadAndInstall();
    const ok = await confirm({
      title: "更新已就绪",
      message: `新版本 ${update.version} 已安装,重启后生效。现在重启?`,
      confirmLabel: "立即重启",
    });
    if (ok) {
      const { relaunch } = await import("@tauri-apps/plugin-process");
      await relaunch();
    }
  } catch (e) {
    if (import.meta.env.DEV) console.warn("[updater] 检查更新失败", e);
  }
});

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

    <!-- 2026-06-08 用户精修 v2:统一 toggle 按钮,贴在 sidebar 右边缘
         展开态:left = sidebar-width 沿 sidebar 右沿
         折叠态:left = 0 贴屏幕最左
         CSS transition 同步滑动,永远在同一视觉锚点(屏幕中线),不破坏 sidebar header -->
    <button
      v-if="!isFullscreen"
      type="button"
      class="sidebar-toggle-edge"
      :class="{ 'is-collapsed': sidebarCollapsed }"
      :title="sidebarCollapsed ? '展开侧栏' : '收起侧栏'"
      :aria-label="sidebarCollapsed ? '展开侧栏' : '收起侧栏'"
      @click="toggleSidebar"
    >
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
           stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <polyline v-if="sidebarCollapsed" points="9 18 15 12 9 6" />
        <polyline v-else points="15 18 9 12 15 6" />
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
    <!-- 商业化重塑 P1(2026-06-09):全局统一支付弹窗 — 任意购买按钮 usePayment().open() 唤起 -->
    <PaymentModal />
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

/* 2026-06-08 用户精修 v4:bug 修复 — 去 transform 治"点击后弹一下"
 *
 *   v3 bug:用 transform: translateY(-50%) 做垂直居中,click 时浏览器
 *   对 button 的 :active 默认行为短暂影响 transform layer 合成,
 *   按钮视觉上"往下偏一下又弹回" — Chrome / Edge 都复现。
 *
 *   治理:用 top: calc(50% - height/2) 静态居中,彻底去掉 transform。
 *   按钮位置稳定不依赖 GPU 合成层,click 时绝对不会偏移。
 */
.sidebar-toggle-edge {
  position: fixed;
  /* 56 / 2 = 28px,精准垂直居中,不靠 transform */
  top: calc(50% - 28px);
  left: var(--sidebar-width);
  z-index: 100;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 18px;
  height: 56px;
  margin: 0;
  padding: 0;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-left: none;          /* 跟 sidebar 右边沿贴齐,视觉融合 */
  border-radius: 0 6px 6px 0;  /* 仅右圆角,像突出的把手 */
  color: var(--color-text-subtle);
  cursor: pointer;
  opacity: 0.55;
  box-shadow: 2px 0 6px rgba(0, 0, 0, 0.04);
  transition:
    left var(--duration-base) var(--ease-out-soft),
    opacity var(--duration-fast) var(--ease-out),
    background var(--duration-fast) var(--ease-out),
    color var(--duration-fast) var(--ease-out);
}
.sidebar-toggle-edge.is-collapsed {
  left: 0;
}
.sidebar-toggle-edge:hover {
  opacity: 1;
  background: var(--color-accent-soft);
  color: var(--color-accent);
  border-color: var(--color-accent-border);
}
/* 显式锁 active 态 — 按下不要任何位置变化,只允许微弱 scale 给反馈 */
.sidebar-toggle-edge:active {
  background: var(--color-accent-soft);
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
