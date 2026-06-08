<script setup lang="ts">
/**
 * AppSidebar — 左侧固定 240px 导航(对齐 Claude / ChatGPT 风格)。
 *
 * 顶部:浑晶 logo
 * 中部:"+ 新建项目" + 项目列表
 *   - hover 项目 → 右侧 ⋯ 按钮 → 弹小菜单(重命名 / 删除)
 *   - 重命名:item 变 inline input(失焦/Enter 保存,ESC 取消)
 *   - 删除:confirm + DELETE,如果删的是当前路由项目,跳 /dashboard
 * 底部:游客 → 登录按钮;已登录 → 头像 + 菜单
 */
import { computed, nextTick, onMounted, onUnmounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { api } from "../api/client";
import { ApiError, type Project } from "../api/types";
import Icon from "./Icon.vue";
import QuotaIndicator from "./QuotaIndicator.vue";
import BYOKUnlockModal from "./BYOKUnlockModal.vue";
import { useAuthStore } from "../stores/auth";
import { useQuotaStore } from "../stores/quota";
import { useBYOKStore } from "../stores/byok";
import { useEventBus } from "../stores/events";
import { useGlobalSearch } from "../composables/useGlobalSearch";
import { useLoginModal } from "../composables/useLoginModal";
import { useDocumentViewer } from "../composables/useDocumentViewer";
import { useTheme, type ThemePreference } from "../composables/useTheme";
import { useSidebarLayout } from "../composables/useSidebarLayout";
import { confirm as confirmDialog } from "../composables/useConfirm";
import { toast } from "../composables/useToast";

const router = useRouter();
const route = useRoute();
const auth = useAuthStore();
const quota = useQuotaStore();
const byok = useBYOKStore();

// BYOK 解锁 modal 开关
const byokModalOpen = ref(false);
function openBYOKModal() {
  if (!auth.isAuthed) {
    loginModal.open();
    return;
  }
  byokModalOpen.value = true;
}
const events = useEventBus();
const loginModal = useLoginModal();
const docViewer = useDocumentViewer();
const theme = useTheme();
// 2026-06-08:sidebar 折叠展开
const { collapsed: sidebarCollapsed, toggle: toggleSidebar } = useSidebarLayout();

/** 用户菜单子菜单 — "切换主题"展开后用 */
const themeSubmenuOpen = ref(false);

function setThemeAndClose(pref: ThemePreference) {
  theme.setPreference(pref);
  themeSubmenuOpen.value = false;
  activeMenu.value = null;   // 关父菜单
}

/** 主题选项展示文案 */
const THEME_OPTIONS: Array<{ value: ThemePreference; label: string; icon: string }> = [
  { value: "light", label: "亮色", icon: "☀" },
  { value: "dark", label: "暗色", icon: "☾" },
  { value: "system", label: "跟随系统", icon: "⚙" },
];

const projects = ref<Project[]>([]);
const projectsLoading = ref(false);

// 项目搜索框已下线(2026-05-23 S1):全局 Cmd+K 已覆盖此功能 + 支持范围 tab,
// 旧搜索框 UI/逻辑作废。直接 v-for projects.value,无需 filteredProjects

// 菜单状态:同时只能有一个 ⋯ 菜单 OR user menu 打开
type ActiveMenu =
  | { kind: "user" }
  | { kind: "project"; id: string }
  | null;
const activeMenu = ref<ActiveMenu>(null);

// inline 重命名状态
const renamingId = ref<string | null>(null);
const renameValue = ref("");
const renameInputRef = ref<HTMLInputElement | null>(null);

const currentProjectId = computed(() => {
  if (typeof route.params.id === "string") return route.params.id;
  return null;
});

async function loadProjects() {
  if (!auth.isAuthed) {
    projects.value = [];
    return;
  }
  // Sprint 6.A2 polish(2026-05-22):stale-while-revalidate 避免切项目闪"加载中…"
  // - 首次加载(列表为空):projectsLoading=true 显示"加载中"
  // - refetch(切项目 / 重命名后刷新):保留旧列表无缝显示,reactive swap 平滑替换
  // - 失败时保留旧列表不清空,只有 401(logout)才清空避免泄露
  const isFirstLoad = projects.value.length === 0;
  if (isFirstLoad) projectsLoading.value = true;
  try {
    projects.value = await api.get<Project[]>("/projects");
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      projects.value = [];
    } else {
      // 2026-06-02 批次 2:DEV 守门,生产 console 干净
      if (import.meta.env.DEV) {
        console.warn("项目列表加载失败:", e);
      }
      // 保留旧列表,用户至少能看到上次的成功数据
    }
  } finally {
    if (isFirstLoad) projectsLoading.value = false;
  }
}

watch(() => auth.isAuthed, loadProjects);

// 2026-06-04:登录态变化时同步刷新 BYOK status(决定底部 ● 绿点是否亮)
watch(() => auth.isAuthed, (isAuthed) => {
  if (isAuthed) {
    byok.refreshStatus();
  } else {
    byok.reset();
  }
}, { immediate: true });

// 路由变化时(进新项目 / 删完跳回 dashboard)同步刷新
watch(
  () => route.fullPath,
  () => {
    if (auth.isAuthed) loadProjects();
  },
);

function handleNewProject() {
  // Sprint 2.A.F:不再直接弹无态的 NewProjectModal,跳 dashboard 让用户先选态(四象限)
  if (!auth.isAuthed) {
    loginModal.open();
    return;
  }
  router.push("/dashboard");
}

function gotoProject(p: Project) {
  if (renamingId.value === p.id) return;   // 重命名态点击不触发跳转
  router.push(`/projects/${p.id}`);
}

function gotoHome() {
  router.push("/dashboard");
}

// Sprint 6.A2 路线图 #6(2026-05-23):全局搜索入口
const globalSearch = useGlobalSearch();
function openGlobalSearch() {
  // 当前在 /projects/:id 时,默认 scope 到该项目;其他路由 = 全平台搜
  const m = route.path.match(/^\/projects\/([^/]+)/);
  globalSearch.open({ scopeProjectId: m ? m[1] : null });
}

// 检测平台 — Mac 显 ⌘K,Win/Linux 显 Ctrl+K
const shortcutHint = computed(() => {
  if (typeof navigator === "undefined") return "Ctrl K";
  return /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘ K" : "Ctrl K";
});

/**
 * Sprint 2.B+(2026-05-12):侧栏直达「我的漫画」入口
 *
 * 设计意图:漫创态走独立列表(MyComicsView,与"我的剧情线"DashboardView 平级),
 * 用户从 4 态卡片绕一圈进入太累。侧栏加直达链接,常用入口扁平化。
 *
 * - 游客点击 → loginModal.open('/my-comics'),登录后落地我的漫画
 * - 已登录 → router.push('/my-comics')(/my-comics 已改 requiresAuth: false,不会被静默 redirect)
 */
function gotoMyComics() {
  if (!auth.isAuthed) {
    loginModal.open("/my-comics");
    return;
  }
  router.push("/my-comics");
}

const isMyComicsActive = computed(() => route.name === "my-comics");

function handleLogin() {
  activeMenu.value = null;
  loginModal.open();
}

function handleLogout() {
  auth.logout();
  activeMenu.value = null;
  router.push("/dashboard");
}

function handleViewDoc() {
  activeMenu.value = null;
  docViewer.open();
}

/** Sprint C.3:跳"我的 credit 消费记录"页 */
function gotoCreditHistory() {
  activeMenu.value = null;
  router.push("/credit-history");
}


// ============================================================
// ⋯ 菜单
// ============================================================

function toggleProjectMenu(p: Project, e: Event) {
  e.stopPropagation();
  if (activeMenu.value?.kind === "project" && activeMenu.value.id === p.id) {
    activeMenu.value = null;
  } else {
    activeMenu.value = { kind: "project", id: p.id };
  }
}

function toggleUserMenu(e: Event) {
  e.stopPropagation();
  if (activeMenu.value?.kind === "user") {
    activeMenu.value = null;
  } else {
    activeMenu.value = { kind: "user" };
    // 每次打开都收起主题二级菜单(免上次残留状态)
    themeSubmenuOpen.value = false;
  }
}

function isProjectMenuOpen(id: string): boolean {
  return activeMenu.value?.kind === "project" && activeMenu.value.id === id;
}
const isUserMenuOpen = computed(() => activeMenu.value?.kind === "user");

// 全局点击关菜单
function closeMenuOnDocClick() {
  activeMenu.value = null;
}
onMounted(() => document.addEventListener("click", closeMenuOnDocClick));
onUnmounted(() => document.removeEventListener("click", closeMenuOnDocClick));

// ============================================================
// inline 重命名
// ============================================================

async function startRename(p: Project) {
  activeMenu.value = null;
  renamingId.value = p.id;
  renameValue.value = p.name;
  await nextTick();
  renameInputRef.value?.focus();
  renameInputRef.value?.select();
}

async function commitRename(p: Project) {
  if (renamingId.value !== p.id) return;
  const newName = renameValue.value.trim();
  if (!newName || newName === p.name) {
    cancelRename();
    return;
  }
  try {
    const updated = await api.patch<Project>(`/projects/${p.id}`, { name: newName });
    // 同步本地
    const idx = projects.value.findIndex((x) => x.id === p.id);
    if (idx >= 0) projects.value[idx] = updated;
    // Bug 修复(2026-05-22):通知打开此项目的 ProjectView 即时更新 header 名字
    // (sidebar 和 ProjectView 是路由兄弟,无共享 store / props,用 window CustomEvent 跨组件)
    window.dispatchEvent(
      new CustomEvent("huimeng:project-renamed", {
        detail: { id: updated.id, name: updated.name },
      }),
    );
  } catch (e) {
    // 2026-06-02 批次 2:DEV 守门 + toast 提示用户
    if (import.meta.env.DEV) {
      console.warn("重命名失败:", e);
    }
    toast.error(e instanceof ApiError ? `重命名失败:${e.message}` : "重命名失败");
  } finally {
    cancelRename();
  }
}

function cancelRename() {
  renamingId.value = null;
  renameValue.value = "";
}

function onRenameKey(p: Project, e: KeyboardEvent) {
  if (e.key === "Enter") {
    e.preventDefault();
    commitRename(p);
  } else if (e.key === "Escape") {
    e.preventDefault();
    cancelRename();
  }
}

// ============================================================
// 删除
// ============================================================

async function deleteProject(p: Project) {
  activeMenu.value = null;
  const ok = await confirmDialog({
    title: `删除项目「${p.name}」?`,
    message: "所有角色 / 关系 / 事件 / 推演记录都会被一起删除,无法恢复。",
    danger: true,
    confirmLabel: "删除",
  });
  if (!ok) return;
  try {
    await api.delete(`/projects/${p.id}`);
    projects.value = projects.value.filter((x) => x.id !== p.id);
    quota.refresh();   // 项目数 -1,刷新配额展示
    // 2026-06-01:广播 project:deleted → 其它依赖项目列表的 view(MyComicsView 等)同步
    events.emit("project:deleted", { project_id: p.id });
    // 如果删的就是当前查看的项目,跳回 dashboard
    if (currentProjectId.value === p.id) {
      router.push("/dashboard");
    }
  } catch (e) {
    toast.error(e instanceof ApiError ? `删除失败:${e.message}` : "删除失败");
  }
}

// 2026-06-01:实时更新 — 监听项目级事件 → 重拉列表
// 2026-06-02 hotfix:订阅移到 onMounted + try/catch,避免 setup 边界异常
const _sidebarUnsubs: Array<() => void> = [];
onMounted(() => {
  void loadProjects();
  try {
    _sidebarUnsubs.push(events.on("project:created", () => void loadProjects()));
    _sidebarUnsubs.push(events.on("project:updated", () => void loadProjects()));
    // 不监听 project:deleted(本组件就是 delete 触发者,自己已 filter 移除)
  } catch (e) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn("[AppSidebar] event bus subscription failed:", e);
    }
  }
});
onUnmounted(() => {
  for (const unsub of _sidebarUnsubs) {
    try { unsub(); } catch { /* noop */ }
  }
});
</script>

<template>
  <aside class="sidebar" :class="{ 'sidebar--collapsed': sidebarCollapsed }">
    <!-- 顶部 logo — 2026-06-08:toggle 按钮已挪到 App.vue 贴边浮动,
         此处只保留 logo,header 回归简洁 -->
    <header class="sidebar-header" @click="gotoHome">
      <span class="logo-mark">◆</span>
      <span class="logo-text">浑晶</span>
    </header>

    <!-- Sprint 6.A2 路线图 #6(2026-05-23):全局搜索入口
         点击或 Cmd+K / Ctrl+K 触发同一弹窗 -->
    <button
      type="button"
      class="global-search-btn"
      @click="openGlobalSearch"
      :title="`全局搜索(${shortcutHint})`"
    >
      <span class="gs-btn-icon" aria-hidden="true">
        <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <circle cx="11" cy="11" r="7" />
          <path d="m21 21-4.3-4.3" />
        </svg>
      </span>
      <span class="gs-btn-text">搜索</span>
      <kbd class="gs-btn-kbd mono">{{ shortcutHint }}</kbd>
    </button>

    <!-- 新建项目 -->
    <button class="new-project-btn" @click="handleNewProject">
      <span class="plus-icon">+</span>
      <span>新建项目</span>
    </button>

    <!--
      UI 优化(2026-05-21 七轮):"我的漫画"从顶部移至底部用户区上方
      原因:漫创态是独立产品线(独立流水线/计费/列表),与项目(前 3 态)是不同心智域。
      放顶部时项目列表加 ↳ 副层级图标会让用户误判"项目是漫画的子项",一刀切到底部根治。
    -->

    <!-- 项目列表(项目搜索已迁移到全局 Cmd+K,下线旧的 sidebar 搜索框)-->
    <nav class="project-list" aria-label="项目列表">
      <div v-if="!auth.isAuthed" class="empty-hint">
        登录后可在此处看到你的项目
      </div>
      <div v-else-if="projectsLoading" class="empty-hint">加载中…</div>
      <div v-else-if="projects.length === 0" class="empty-hint">
        还没有项目
      </div>

      <ul v-else class="project-ul">
        <li
          v-for="p in projects"
          :key="p.id"
          class="project-li"
          :class="{
            'is-active': currentProjectId === p.id,
            'is-renaming': renamingId === p.id,
            'is-menu-open': isProjectMenuOpen(p.id),
          }"
        >
          <!-- 重命名态 -->
          <input
            v-if="renamingId === p.id"
            ref="renameInputRef"
            v-model="renameValue"
            type="text"
            maxlength="30"
            class="rename-input"
            @blur="commitRename(p)"
            @keydown="onRenameKey(p, $event)"
            @click.stop
          />

          <!-- 普通态 — UI 优化(2026-05-21 七轮):去除 ↳ 副层级图标(我的漫画已移底部,无父级误导) -->
          <template v-else>
            <button class="project-item" :title="p.name" @click="gotoProject(p)">
              <span class="project-name">{{ p.name }}</span>
            </button>

            <button
              class="more-btn"
              aria-label="项目操作"
              @click="toggleProjectMenu(p, $event)"
            >
              ⋯
            </button>

            <transition name="menu-fade">
              <div
                v-if="isProjectMenuOpen(p.id)"
                class="project-menu"
                @click.stop
              >
                <button class="menu-item" @click="startRename(p)">
                  重命名
                </button>
                <button class="menu-item menu-item--danger" @click="deleteProject(p)">
                  删除项目
                </button>
              </div>
            </transition>
          </template>
        </li>
      </ul>
    </nav>

    <!-- 2026-05-12 重构:配额卡片从 sidebar 主区挪到用户菜单内(用户拍板) -->

    <!--
      UI 优化(2026-05-21 七轮):"我的漫画"独立到底部 — 漫创态是独立产品线,
      不应混在项目列表中误导用户。分隔线 + 间距明确两区切割。
    -->
    <div class="sidebar-bottom-nav">
      <button
        class="nav-btn"
        :class="{ 'is-active': isMyComicsActive }"
        :aria-label="auth.isAuthed ? '前往我的漫画' : '登录后前往我的漫画'"
        :aria-current="isMyComicsActive ? 'page' : undefined"
        @click="gotoMyComics"
      >
        <span class="nav-icon" aria-hidden="true">⚭</span>
        <span class="nav-label">我的漫画</span>
        <span class="nav-chip">内测</span>
      </button>

    </div>

    <!-- 底部用户区(游客/已登录都用同一个 btn 弹菜单,菜单内容因登录态而异) -->
    <div class="user-area">
      <div class="user-wrap">
        <transition name="menu-fade">
          <div v-if="isUserMenuOpen" class="user-menu" @click.stop>
            <!-- 配额可视化(已登录,塞菜单顶部;4 tab + ⚭ 共用标识) -->
            <QuotaIndicator v-if="auth.isAuthed" />

            <!-- 游客:只有"登录账户" -->
            <template v-if="!auth.isAuthed">
              <button class="menu-item menu-item--accent" @click="handleLogin">
                登录账户
              </button>
            </template>

            <!-- 已登录:消费记录 + 协议 + 主题 + 退出
                 2026-06-02 重设计:统一 line SVG icon,去掉每行分隔线,只用底部一条隔退出 -->
            <template v-else>
              <button class="menu-item" @click="gotoCreditHistory">
                <Icon name="clock" :size="14" class="menu-icon" />
                <span>消费记录</span>
              </button>

              <button class="menu-item" @click="handleViewDoc">
                <Icon name="file_text" :size="14" class="menu-icon" />
                <span>用户协议</span>
              </button>

              <!-- 2026-06-05:自携密钥(BYOK)挪进用户菜单(原在 nav 区,现归到此处) -->
              <button class="menu-item menu-item--byok" @click="openBYOKModal">
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  width="14" height="14" viewBox="0 0 24 24"
                  fill="none" stroke="currentColor" stroke-width="1.8"
                  stroke-linecap="round" stroke-linejoin="round"
                  class="menu-icon"
                >
                  <path d="M21 2l-2 2m-7.61 7.61a5.5 5.5 0 1 1-7.778 7.778 5.5 5.5 0 0 1 7.777-7.777zm0 0L15.5 7.5m0 0l3 3L22 7l-3-3m-3.5 3.5L19 4" />
                </svg>
                <span>自携密钥</span>
                <span v-if="byok.isActive" class="menu-byok-dot" aria-label="已激活"></span>
              </button>


              <!-- 2026-06-02:主题切换 — 改为右侧弹出小卡片(原 inline 展开会让"主题"两字居中,跟其它菜单项不齐) -->
              <div class="theme-trigger-wrap">
                <button
                  class="menu-item menu-item--with-sub"
                  @click.stop="themeSubmenuOpen = !themeSubmenuOpen"
                >
                  <Icon name="moon" :size="14" class="menu-icon" />
                  <span>主题</span>
                  <span class="menu-chev-tiny">{{ themeSubmenuOpen ? '▾' : '▸' }}</span>
                </button>
                <div v-if="themeSubmenuOpen" class="theme-popup">
                  <button
                    v-for="opt in THEME_OPTIONS"
                    :key="opt.value"
                    class="menu-item menu-item--sub"
                    :class="{ 'menu-item--sub-active': theme.preference.value === opt.value }"
                    @click="setThemeAndClose(opt.value)"
                  >
                    <span class="theme-icon">{{ opt.icon }}</span>
                    <span>{{ opt.label }}</span>
                    <span
                      v-if="theme.preference.value === opt.value"
                      class="theme-check"
                    >✓</span>
                  </button>
                </div>
              </div>

              <div class="menu-divider" />
              <button class="menu-item menu-item--danger" @click="handleLogout">
                <Icon name="log_out" :size="14" class="menu-icon" />
                <span>退出登录</span>
              </button>
            </template>
          </div>
        </transition>

        <button class="user-btn" @click="toggleUserMenu($event)">
          <span class="avatar" :class="{ 'avatar-guest': !auth.isAuthed }">
            {{ auth.isAuthed
              ? (auth.currentUser?.email?.[0]?.toUpperCase() ?? "?")
              : "游"
            }}
          </span>
          <div class="user-text">
            <span class="user-line-1">
              {{ auth.isAuthed
                ? (auth.currentUser?.email ?? "已登录")
                : "游客"
              }}
            </span>
            <span class="user-line-2" :class="{ mono: auth.isAuthed }">
              {{ auth.isAuthed ? `${auth.plan} 档` : "点击查看更多" }}
            </span>
          </div>
          <span class="menu-chev">⋯</span>
        </button>
      </div>
    </div>

    <!-- 2026-06-05:ICP 备案号已挪到 App.vue 主内容区底部居中(原侧栏底端显得局促) -->

    <!-- 2026-06-04:BYOK 自携密钥解锁 modal — sidebar 永驻,任意页面可触发 -->
    <BYOKUnlockModal
      :is-open="byokModalOpen"
      @close="byokModalOpen = false"
    />
  </aside>
</template>

<style scoped>
/* 2026-06-08 性能优化 v3:GPU 加速折叠(根治剧创态卡顿)
 *
 *   v2 用 transition: width — 触发 layout 重排,剧创态 DOM 节点多(剧本 +
 *   SVG + 桥接卡 + 结构报告)逐帧 layout 极慢 → 用户感知掉帧。
 *
 *   v3 改为 transform: translateX + margin-left 同步收 — transform 走 GPU
 *   合成层,完全跳过 layout/paint,内部 DOM 不重排。margin-left 仅触发
 *   主区域的 flex 重排(主区域是 flex 1,只要 sidebar 占位变 0 就自动填满,
 *   不会逐帧重排子元素)。
 *
 *   原理参考 CSS Triggers:transform = composite only,width = layout+paint+composite。
 */
.sidebar {
  width: var(--sidebar-width);
  height: 100vh;
  background: var(--color-bg-subtle);
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  flex-shrink: 0;
  user-select: none;
  /* 关键:transform 不重排,margin-left 单纯控制占位 */
  transition:
    transform var(--duration-base) var(--ease-out-soft),
    margin-left var(--duration-base) var(--ease-out-soft);
  will-change: transform, margin-left;
}

/* 折叠态 — translateX 离开视野,margin-left 负值让主区域填满 */
.sidebar--collapsed {
  transform: translateX(-100%);
  margin-left: calc(-1 * var(--sidebar-width));
  border-right: none;
}

/* ===== 顶部 logo ===== */
.sidebar-header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-5);
  cursor: pointer;
  border-bottom: 1px solid var(--color-border);
  transition: background var(--duration-fast) var(--ease-out);
  flex-shrink: 0;
  /* 关键:内容固定 240px 不被宽度动画期间挤压(性能 + 视觉双重保护) */
  min-width: var(--sidebar-width);
}

.sidebar-header:hover {
  background: var(--color-surface-hover);
}

.logo-mark {
  font-size: var(--text-lg);
  color: var(--color-accent);
  line-height: 1;
}

.logo-text {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  letter-spacing: 0.02em;
}

/* ===== Sprint 6.A2 路线图 #6(2026-05-23)全局搜索按钮 ===== */
.global-search-btn {
  margin: var(--space-3) var(--space-3) 0;
  padding: var(--space-2) var(--space-3);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  text-align: left;
  transition: all var(--duration-fast) var(--ease-out);
}
.global-search-btn:hover {
  border-color: var(--color-accent-border);
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.gs-btn-icon {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  color: var(--color-text-muted);
}
.gs-btn-text {
  flex: 1;
}
.gs-btn-kbd {
  flex-shrink: 0;
  padding: 1px 6px;
  font-size: 10px;
  color: var(--color-text-subtle);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
}

/* ===== 新建项目按钮 ===== */
.new-project-btn {
  margin: var(--space-2) var(--space-3) var(--space-2);
  padding: var(--space-2) var(--space-3);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  color: var(--color-text);
  text-align: left;
  transition: all var(--duration-fast) var(--ease-out);
}

.new-project-btn:hover {
  border-color: var(--color-accent-border);
  background: var(--color-surface-hover);
}

.plus-icon {
  font-size: var(--text-md);
  color: var(--color-text-muted);
  font-weight: 400;
  line-height: 1;
  width: 16px;
  text-align: center;
}

/* ===== Sprint 2.B+:我的漫画直达入口 =====
 * 视觉:按钮横跨整个 sidebar(整行就是 tab),无内部小矩形.
 *
 * hotfix(2026-05-27 末⁵⁴):用户原话"直接拿外面的大矩形框作为'我的漫画'tab,
 * 重新设计选中效果".去 border / 去圆角 / 去 margin,按钮宽度 100% 撑满 sidebar.
 * 选中态用整行背景色(accent-soft)+ icon/文字变 accent,不加边框框(对齐 2.6 不竖色条).
 */
.nav-btn {
  width: 100%;
  margin: 0;
  padding: var(--space-2) var(--space-3);
  display: flex;
  align-items: center;
  gap: var(--space-2);
  background: transparent;
  border: 0;
  border-radius: 0;
  font-size: var(--text-sm);
  color: var(--color-text);
  text-align: left;
  transition: background var(--duration-fast) var(--ease-out),
              color var(--duration-fast) var(--ease-out);
}
.nav-btn:hover {
  background: var(--color-surface-hover);
}
.nav-btn.is-active {
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
}

/* 2026-06-05:BYOK 入口已挪进用户菜单 — 旧 nav-btn--byok 样式删,新 menu-item--byok 见下 */
.menu-item--byok {
  position: relative;
}
.menu-byok-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #10b981;
  margin-left: auto;
  box-shadow: 0 0 0 2px rgba(16, 185, 129, 0.2);
  flex-shrink: 0;
}

.nav-icon {
  width: 16px;
  text-align: center;
  font-size: var(--text-md);
  color: var(--color-text-muted);
  line-height: 1;
  flex-shrink: 0;
}
.nav-btn.is-active .nav-icon {
  color: var(--color-accent-text);
}
.nav-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.nav-chip {
  flex-shrink: 0;
  padding: 1px 6px;
  font-size: 10px;
  font-weight: 500;
  letter-spacing: 0.04em;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
}
.nav-btn.is-active .nav-chip {
  /* active 态时整行已是 accent-soft 背景,chip 用白底 + accent 边框区分 */
  background: var(--color-surface);
  border: 1px solid var(--color-accent-border);
}

/* ===== 项目列表 ===== */
.project-list {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-2) var(--space-3);
}

.empty-hint {
  padding: var(--space-3) var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  text-align: center;
  line-height: var(--line-relaxed);
}

.project-ul {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.project-li {
  position: relative;
  display: flex;
  align-items: stretch;
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) var(--ease-out);
}

.project-li:hover {
  background: var(--color-surface-hover);
}

.project-li.is-active {
  background: var(--color-accent-soft);
}

.project-li.is-menu-open {
  background: var(--color-surface-hover);
}

/* UI 优化(2026-05-21 七轮):"我的漫画"已移底部,项目 item 不再需要 ↳ 副层级图标 */
.project-item {
  flex: 1;
  min-width: 0;
  padding: var(--space-2) var(--space-3);
  text-align: left;
  font-size: var(--text-sm);
  color: var(--color-text);
  background: transparent;
}

.project-li.is-active .project-item {
  color: var(--color-accent-text);
  font-weight: 500;
}

.project-name {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

/*
  UI 优化(2026-05-21 七轮):"我的漫画"独立底部区
    - 顶部细线分隔,与项目列表视觉切开
    - 与用户区紧贴(中间不再加分隔线避免线条堆积)

  hotfix v2(2026-05-27 末⁵⁴):用户拍板"外部大矩形即 tab 本身,选中用背景色,
  不要内部小矩形框".本容器无任何 padding / margin,按钮 width: 100% 直接横跨
  整个 sidebar 宽度,选中态整行 accent-soft 背景.
*/
.sidebar-bottom-nav {
  border-top: 1px solid var(--color-border);
}

/* 旧的 .project-search 已下线(2026-05-23 S1)— 全局 Cmd+K 已覆盖 */

.more-btn {
  width: 28px;
  font-size: var(--text-md);
  line-height: 1;
  color: var(--color-text-muted);
  background: transparent;
  border-radius: var(--radius-sm);
  margin-right: 4px;
  opacity: 0;
  transition: all var(--duration-fast) var(--ease-out);
  flex-shrink: 0;
}

.project-li:hover .more-btn,
.project-li.is-menu-open .more-btn {
  opacity: 1;
}

.more-btn:hover {
  background: var(--color-bg-subtle);
  color: var(--color-text);
}

/* 项目菜单(下拉) */
.project-menu {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 2px;
  min-width: 140px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  padding: var(--space-1);
  z-index: var(--z-sticky);
}

/* ===== inline 重命名 input ===== */
.rename-input {
  flex: 1;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-accent);
  border-radius: var(--radius-md);
  outline: none;
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.12);
  margin: 0;
  min-width: 0;
}

/* ===== 底部用户区 ===== */
.user-area {
  border-top: 1px solid var(--color-border);
  padding: var(--space-2);
  position: relative;
}

.user-wrap {
  position: relative;
}

.user-btn {
  width: 100%;
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-2);
  border-radius: var(--radius-md);
  background: transparent;
  text-align: left;
  transition: background var(--duration-fast) var(--ease-out);
}

.user-btn:hover {
  background: var(--color-surface-hover);
}

.avatar {
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  flex-shrink: 0;
}

.avatar-guest {
  background: var(--color-text-muted);
}

.user-text {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.user-line-1 {
  font-size: var(--text-sm);
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1.3;
}

.user-line-2 {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin-top: 2px;
  line-height: 1.3;
}

.menu-chev {
  font-size: var(--text-md);
  color: var(--color-text-muted);
  flex-shrink: 0;
}

/* 用户菜单(向上弹) */
.user-menu {
  position: absolute;
  bottom: calc(100% + 4px);
  left: 0;
  right: 0;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  padding: var(--space-1);
  z-index: var(--z-sticky);
}

/* ===== 通用菜单 item ===== */
.menu-item {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  padding: var(--space-2) var(--space-3);
  text-align: left;
  font-size: var(--text-sm);
  color: var(--color-text);
  border-radius: var(--radius-sm);
  background: transparent;
  transition: background var(--duration-fast) var(--ease-out);
}

/* 2026-06-02:菜单 icon — line SVG,统一灰色 */
.menu-icon {
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.menu-item:hover {
  background: var(--color-surface-hover);
}

.menu-item--danger {
  color: var(--color-danger);
}

.menu-item--danger:hover {
  background: var(--color-danger-soft);
}

.menu-item--accent {
  color: var(--color-accent-text);
  font-weight: 500;
}

.menu-item--accent:hover {
  background: var(--color-accent-soft);
}

.menu-divider {
  height: 1px;
  background: var(--color-border);
  margin: var(--space-1) 0;
}

/* Sprint C.3 polish:更淡的分隔线,用于 4 个一级菜单项之间(视觉分组不抢注意力)*/
.menu-separator {
  height: 1px;
  background: var(--color-border);
  opacity: 0.45;
  margin: 2px var(--space-2);
}

/* 2026-06-02:主题切换器
   - "主题"行跟其它菜单项一样左对齐(继承 .menu-item 的 flex)
   - chevron 用 margin-left: auto 推到最右
   - 子选项改为右侧弹出 popup(不再 inline 展开,避免文字被居中) */
.menu-item-text {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.menu-chev-tiny {
  font-size: 10px;
  color: var(--color-text-subtle);
  margin-left: auto;  /* 关键:把 chevron 推到最右,让"主题"文字紧贴 icon 左对齐 */
}

/* 主题 popup wrapper(让 popup 相对于此定位) */
.theme-trigger-wrap {
  position: relative;
}
.theme-popup {
  position: absolute;
  left: calc(100% + 4px);
  top: 0;
  min-width: 140px;
  padding: 4px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  z-index: 20;
  display: flex;
  flex-direction: column;
  gap: 1px;
}

.menu-item--sub {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  padding: var(--space-2) var(--space-3);
}
.menu-item--sub:hover {
  color: var(--color-text);
  background: var(--color-surface-hover);
}
.menu-item--sub-active {
  color: var(--color-accent-text);
  font-weight: 500;
  background: var(--color-accent-soft);
}
.theme-icon {
  width: 14px;
  text-align: center;
  font-size: var(--text-sm);
  display: inline-block;
  flex-shrink: 0;
}
.theme-check {
  margin-left: auto;
  color: var(--color-accent);
  font-weight: 700;
}

/* ===== 菜单淡入 ===== */
.menu-fade-enter-active,
.menu-fade-leave-active {
  transition: opacity var(--duration-fast) var(--ease-out),
              transform var(--duration-fast) var(--ease-out);
}

.menu-fade-enter-from,
.menu-fade-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
