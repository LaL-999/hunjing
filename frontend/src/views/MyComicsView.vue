<script setup lang="ts">
/**
 * MyComicsView — D.9 Sprint 2.B 「我的漫画」列表
 *
 * 独立 view(不混入「我的剧情线」DashboardView,隔离铁律,详 ADR §5)。
 *
 * 功能:
 *   - 列出当前用户所有漫画(按 updated_at 倒序)
 *   - 每行卡片:封面(style_anchor_image_url)+ 名字 + state chip + 创建时间
 *   - 点卡片 → /comics/:id
 *   - "+ 新建漫画"按钮 → 弹 CreateComicModal 就地创建(Sprint 2.B+)
 *
 * Sprint 2.B+(2026-05-12):
 *   - 路由 requiresAuth: true → false:游客可进列表页(view 自管认证态)
 *     用意:让 sidebar 直接入口 + 4 态卡片任意路径都能进,而不是被 router.beforeEach
 *     静默 redirect 到 /dashboard(用户报告的"点了跳主页" bug)
 *   - 未登录态展示 hero CTA + loginModal 入口
 *   - 401 catch:不显示 raw error,而是清状态 + 走未登录 hero(token 过期场景)
 *
 * Sprint 2.B+ 二修(2026-05-12 致命 bug 修复):
 *   - **斩断死循环**:原 handleNewComic 用 toast + router.push("/dashboard") 把用户踢回去
 *     让走 4 态卡片路径,但 cycle 卡又跳回 /my-comics → 空态又被踢回 → 死锁
 *   - 改:就地弹 CreateComicModal,用户选已有 done 推演作输入源 + 输漫画名 → 创建成功
 *     → push /comics/:id 进画风上传流程
 */
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useRouter } from "vue-router";

import { api, apiAssetUrl } from "../api/client";
import { ApiError } from "../api/types";
import type {
  Comic,
  ComicCancelResponse,
  ComicState,
} from "../api/types";
import { COMIC_STATE_LABEL } from "../api/types";
import { useAuthStore } from "../stores/auth";
import { useEventBus } from "../stores/events";
import { useQuotaStore } from "../stores/quota";
import { useLoginModal } from "../composables/useLoginModal";
import { useUpgradeModal } from "../composables/useUpgradeModal";
import { confirm as confirmDialog } from "../composables/useConfirm";
import { toast } from "../composables/useToast";
// UI 优化(2026-05-21 六轮):CreateComicModal 已收归 DashboardView,这里不再 import

const router = useRouter();
const auth = useAuthStore();
const events = useEventBus();
const quota = useQuotaStore();
const loginModal = useLoginModal();
const upgradeModal = useUpgradeModal();

// 2026-06-02:漫创态仅限 Pro 及以上(产品决策 — 不让 Free 白嫖,激发订阅意愿)
// 注意:quota.status 未加载完成时,两者都返 false → 走 loading 分支,避免闪烁
const isFreeTier = computed(() => quota.status?.plan === "free");
const isPaidTier = computed(() => {
  const plan = quota.status?.plan;
  return plan === "pro" || plan === "max" || plan === "super_max" || plan === "founder";
});

function handleUpgradeCta() {
  upgradeModal.open();
}

const comics = ref<Comic[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);

/** Sprint 2.B+ 四修:卡片 hover ⋯ 菜单 open id(同时只能开一个) */
const openMenuId = ref<string | null>(null);

/** Sprint 2.B+ 四修:终态(不能再 cancel)*/
const TERMINAL_STATES: ComicState[] = ["done", "failed", "cancelled"];

function isTerminal(state: ComicState): boolean {
  return TERMINAL_STATES.includes(state);
}

async function loadComics() {
  // Sprint 2.B+:游客直接走未登录 hero,不调 API(防 401)
  if (!auth.isAuthed) {
    comics.value = [];
    loading.value = false;
    return;
  }
  loading.value = true;
  try {
    comics.value = await api.get<Comic[]>("/comics");
    error.value = null;
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      // token 过期已被 client 触发 logout;此处不显 raw 错,走未登录 hero
      comics.value = [];
      error.value = null;
    } else {
      error.value = e instanceof ApiError ? e.message : "加载漫画列表失败";
    }
  } finally {
    loading.value = false;
  }
}

// 2026-06-01:实时更新 — 监听漫画事件 → reload(治"返回不刷新")
// 2026-06-02 hotfix:订阅 + visibility 注册全部移到 onMounted,加 try/catch 防 setup 中断
function _onComicsVisibilityChange() {
  if (document.visibilityState === "visible") {
    void loadComics();
  }
}
const _comicUnsubs: Array<() => void> = [];
onMounted(() => {
  void loadComics();
  try {
    _comicUnsubs.push(events.on("comic:created", () => void loadComics()));
    _comicUnsubs.push(events.on("comic:done", () => void loadComics()));
    _comicUnsubs.push(events.on("comic:deleted", () => void loadComics()));
  } catch (e) {
    if (import.meta.env.DEV) {
      // eslint-disable-next-line no-console
      console.warn("[MyComicsView] event bus subscription failed:", e);
    }
  }
  document.addEventListener("visibilitychange", _onComicsVisibilityChange);
});
onUnmounted(() => {
  for (const unsub of _comicUnsubs) {
    try { unsub(); } catch { /* noop */ }
  }
  document.removeEventListener("visibilitychange", _onComicsVisibilityChange);
});

function openComic(comic: Comic) {
  router.push(`/comics/${comic.id}`);
}

// UI 优化(2026-05-21 六轮):handleNewComic / onComicCreated 删除 —
// 新建漫画入口收归首页"漫创态"卡片,MyComicsView 不再承担创建职责。
// loadComics 在 onMounted 自动跑;用户从首页 CreateComicModal 创建后
// push 到 /comics/:id 进详情页,返回 /my-comics 时 onMounted 重拉到新数据

function handleLoginCta() {
  loginModal.open("/my-comics");
}

// ============================================================
// Sprint 2.B+ 四修:卡片 ⋯ 菜单 + 取消 + 删除
// ============================================================

function toggleMenu(e: Event, comicId: string) {
  e.stopPropagation();   // 防触发卡片点击进详情
  openMenuId.value = openMenuId.value === comicId ? null : comicId;
}

function closeMenuOnDocClick() {
  openMenuId.value = null;
}
onMounted(() => document.addEventListener("click", closeMenuOnDocClick));
onUnmounted(() => document.removeEventListener("click", closeMenuOnDocClick));

/**
 * 取消漫画 — 显示按进度档位的退款提示
 *
 * 后端按 progress_percent 算退款:
 *   < 10%:全退 / 10-80%:半退 / >= 80%:不退
 * 前端 confirm 提前预告"按当前进度,预计 X 退款"让用户知情决策。
 */
async function handleCancel(e: Event, comic: Comic) {
  e.stopPropagation();
  openMenuId.value = null;

  // 预告退款规则给用户
  let predictedRefund = "";
  if (comic.progress_percent < 10) {
    predictedRefund = "进度 < 10%,预计配额全额退回(不扣本月)";
  } else if (comic.progress_percent < 80) {
    predictedRefund = `进度 ${comic.progress_percent}%,预计半额退回(扣半本)`;
  } else {
    predictedRefund = `进度 ${comic.progress_percent}%,已超 80%,不予退还(扣全本)`;
  }

  const ok = await confirmDialog({
    title: `取消漫画《${comic.name}》?`,
    message: `${predictedRefund}\n\n已生成内容会保留,但 state 落 cancelled,不能继续推进。`,
    danger: true,
    confirmLabel: "确认取消",
  });
  if (!ok) return;

  try {
    const resp = await api.post<ComicCancelResponse>(
      `/comics/${comic.id}/cancel`, {},
    );
    // 局部更新该卡(避免整列表 reload 闪)
    const idx = comics.value.findIndex((c) => c.id === comic.id);
    if (idx >= 0) comics.value[idx] = resp.comic;
    // 按退款档位 toast 反馈
    if (resp.refund.phase === "full") {
      toast.success(`已取消 · ${resp.refund.label}`, 4500);
    } else if (resp.refund.phase === "half") {
      toast.info(`已取消 · ${resp.refund.label}`, 4500);
    } else if (resp.refund.phase === "none") {
      toast.warning(`已取消 · ${resp.refund.label}`, 4500);
    } else {
      toast.info("漫画已是终态,无需操作");
    }
  } catch (err) {
    const msg = err instanceof ApiError ? err.message : "取消失败";
    toast.error(msg);
  }
}

async function handleDelete(e: Event, comic: Comic) {
  e.stopPropagation();
  openMenuId.value = null;

  const ok = await confirmDialog({
    title: `删除漫画《${comic.name}》?`,
    message: "所有参考图、角色立绘卡、剧本数据都会被删除,无法恢复。",
    danger: true,
    confirmLabel: "删除",
  });
  if (!ok) return;

  try {
    await api.delete(`/comics/${comic.id}`);
    comics.value = comics.value.filter((c) => c.id !== comic.id);
    // 2026-06-01:广播 comic:deleted → ProjectView 等关联 view 同步
    events.emit("comic:deleted", { comic_id: comic.id });
    toast.success(`漫画《${comic.name}》已删除`);
  } catch (err) {
    if (err instanceof ApiError && err.code === "COMIC_STILL_RUNNING") {
      toast.error("漫画正在推进中,请先取消再删除");
    } else {
      toast.error(err instanceof ApiError ? err.message : "删除失败");
    }
  }
}

function formatTime(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

const empty = computed<boolean>(() => !loading.value && comics.value.length === 0);
</script>

<template>
  <main class="my-comics">
    <!-- 2026-06-08 用户精修:加返回主页按钮(之前缺) -->
    <div class="page-toolbar">
      <button
        type="button"
        class="back-btn"
        @click="router.push('/dashboard')"
        title="返回主页"
      >
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
             stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">
          <path d="M19 12H5M12 19l-7-7 7-7" />
        </svg>
        <span>返回主页</span>
      </button>
    </div>

    <!--
      UI 优化(2026-05-21 七轮):标题置于左上角,横幅下沉
        - 原顺序:横幅 → 标题(主次不分,首屏被次要信息占据)
        - 新顺序:标题(主)→ 横幅(辅助说明,贴在标题下方)
        - 删除"文本一键变漫画"副标题(纯废话)
        - 删除右上角"+新建漫画"按钮 — 新建入口统一收归首页"漫创态"卡片
    -->
    <header class="page-header">
      <h1 class="page-title">我的漫画</h1>
    </header>

    <!--
      漫创态尝鲜版说明 — 仅对已登录付费档显示(Free 走下方升级引导,不重复说明)
    -->
    <div
      v-if="auth.isAuthed && isPaidTier"
      class="experimental-banner"
      role="note"
    >
      <span class="banner-icon" aria-hidden="true">⚗</span>
      <div class="banner-body">
        <strong class="banner-title">漫创态尝鲜版</strong>
        <p class="banner-text">
          AI 生图风格仍在迭代,产物会逐步惊艳。
          按订阅福利使用,不消耗 credit:Pro 1 · Max 2 · 超级 Max 4 本/月。
        </p>
      </div>
    </div>

    <!-- 游客 hero -->
    <div v-if="!auth.isAuthed" class="empty-state">
      <p class="empty-title">登录后查看你的漫画</p>
      <button class="primary-btn" @click="handleLoginCta">登录</button>
    </div>

    <!-- 2026-06-02:Free 档升级引导(漫创态仅限付费档) -->
    <div v-else-if="isFreeTier" class="upgrade-gate">
      <div class="upgrade-gate-icon" aria-hidden="true">⚭</div>
      <h2 class="upgrade-gate-title">漫创态是会员专属功能</h2>
      <p class="upgrade-gate-text">
        升级到 Pro 及以上档位,即可解锁<strong> AI 漫画创作 </strong>—
        从文本一键生成漫画分镜 / 立绘 / 排版。
      </p>
      <ul class="upgrade-gate-perks">
        <li><span class="perk-tier">Pro</span> 每月 1 本</li>
        <li><span class="perk-tier">Max</span> 每月 2 本</li>
        <li><span class="perk-tier">超级 Max</span> 每月 4 本</li>
      </ul>
      <button class="primary-btn" @click="handleUpgradeCta">升级解锁 →</button>
      <p class="upgrade-gate-hint">订阅福利使用,不额外消耗 credit</p>
    </div>

    <!-- Loading 骨架 -->
    <div
      v-else-if="loading"
      class="skeleton-grid"
      aria-busy="true"
      aria-live="polite"
    >
      <div
        v-for="i in 3"
        :key="i"
        class="comic-card comic-card--skeleton"
      >
        <div class="card-cover skeleton"></div>
        <div class="card-meta">
          <span class="skeleton skeleton-line" style="width: 60%; height: 16px;"></span>
          <span class="skeleton skeleton-line" style="width: 40%; height: 12px; margin-top: 6px;"></span>
        </div>
      </div>
    </div>

    <!-- Error -->
    <div v-else-if="error" class="state-msg state-error">{{ error }}</div>

    <!--
      Empty — UI 优化(2026-05-21 六轮):
        - 删除按钮(新建漫画入口已统一到首页"漫创态"卡片)
        - 占满首屏垂直空间,居中,有抽象插画(漫画格子)+ 引导文案
        - 提示用户回首页选漫创态
    -->
    <div v-else-if="empty" class="empty-state empty-state--hero">
      <svg
        class="empty-illustration"
        viewBox="0 0 80 80"
        width="80"
        height="80"
        fill="none"
        stroke="currentColor"
        stroke-width="1.5"
        stroke-linecap="round"
        stroke-linejoin="round"
        aria-hidden="true"
      >
        <rect x="8" y="8" width="28" height="28" rx="3" />
        <rect x="44" y="8" width="28" height="28" rx="3" />
        <rect x="8" y="44" width="28" height="28" rx="3" />
        <rect x="44" y="44" width="28" height="28" rx="3" />
      </svg>
      <p class="empty-title-large">第一格漫画,正在等你</p>
      <p class="empty-sub">回首页选「漫创态」开始创作</p>
    </div>

    <!-- 漫画卡片网格 -->
    <ul v-else class="comics-grid" role="list">
      <li
        v-for="comic in comics"
        :key="comic.id"
        class="comic-card"
        :class="[
          `comic-card--${comic.state}`,
          { 'is-menu-open': openMenuId === comic.id },
        ]"
        tabindex="0"
        role="button"
        :aria-label="`查看漫画《${comic.name}》,状态 ${COMIC_STATE_LABEL[comic.state]}`"
        @click="openComic(comic)"
        @keydown.enter="openComic(comic)"
        @keydown.space.prevent="openComic(comic)"
      >
        <div class="card-cover">
          <img
            v-if="comic.style_anchor_image_url"
            :src="apiAssetUrl(comic.style_anchor_image_url)"
            :alt="`${comic.name} 封面`"
            loading="lazy"
          />
          <div v-else class="card-cover-placeholder">
            <span class="placeholder-text">{{ comic.style_tag || "尚未定调" }}</span>
          </div>

          <!-- Sprint 2.B+ 四修:卡片 hover ⋯ 操作菜单 -->
          <button
            class="card-more-btn"
            :aria-label="`《${comic.name}》操作菜单`"
            :aria-expanded="openMenuId === comic.id"
            @click.stop="toggleMenu($event, comic.id)"
          >⋯</button>

          <transition name="menu-fade">
            <div
              v-if="openMenuId === comic.id"
              class="card-menu"
              role="menu"
              @click.stop
            >
              <!-- 非终态可取消 -->
              <button
                v-if="!isTerminal(comic.state)"
                class="menu-item"
                role="menuitem"
                @click="handleCancel($event, comic)"
              >取消创作</button>
              <!-- 终态(done/failed/cancelled)或 queued 显示删除 -->
              <button
                v-if="isTerminal(comic.state) || comic.state === 'queued'"
                class="menu-item menu-item--danger"
                role="menuitem"
                @click="handleDelete($event, comic)"
              >删除漫画</button>
            </div>
          </transition>
        </div>
        <div class="card-meta">
          <h2 class="card-name">{{ comic.name }}</h2>
          <div class="card-info-row">
            <span class="state-chip" :class="`state-${comic.state}`">
              {{ COMIC_STATE_LABEL[comic.state] }}
            </span>
            <span class="card-time mono">{{ formatTime(comic.updated_at) }}</span>
          </div>
          <div v-if="comic.state !== 'done' && comic.state !== 'cancelled'" class="card-progress">
            <div class="progress-bar">
              <div
                class="progress-fill"
                :class="{ 'is-failed': comic.state === 'failed' }"
                :style="{ width: comic.progress_percent + '%' }"
              ></div>
            </div>
            <span class="progress-text mono">{{ comic.progress_percent }}%</span>
          </div>
          <!-- Sprint 2.B+ 四修:queued 态显示"下一步"hint(用户从列表看不知道要点进去上传参考图)-->
          <p
            v-if="comic.state === 'queued'"
            class="card-next-hint"
          >点击卡片 → 上传 3 张参考图触发创作</p>
        </div>
      </li>
    </ul>

    <!--
      UI 优化(2026-05-21 六轮):删除 CreateComicModal 局部嵌入 — 新建漫画入口已收归首页"漫创态"卡片,
      MyComicsView 纯作为产物列表页,不再承担创建职责
    -->
  </main>
</template>

<style scoped>
.my-comics {
  max-width: 1080px;
  margin: 0 auto;
  padding: var(--space-6) var(--space-5);
}

/* 2026-06-08:返回主页 toolbar(顶部独立行) */
.page-toolbar {
  margin-bottom: var(--space-3);
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
  color: var(--color-text-muted);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.back-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
  border-color: var(--color-border);
}

/* Sprint 5.B 降级横幅(2026-05-18):警示色调,占顶部一行,不抢卡片视觉 */
.experimental-banner {
  display: flex;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  margin-bottom: var(--space-5);
  background: rgba(245, 158, 11, 0.08);
  border: 1px solid rgba(245, 158, 11, 0.3);
  border-radius: var(--radius-md);
  align-items: flex-start;
}

/* 2026-06-02:Free 档升级引导 — 漫创态会员专属 */
.upgrade-gate {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  max-width: 480px;
  margin: var(--space-12) auto 0;
  padding: var(--space-8) var(--space-6);
  text-align: center;
}
.upgrade-gate-icon {
  font-size: 48px;
  line-height: 1;
  color: var(--color-accent);
  margin-bottom: var(--space-2);
}
.upgrade-gate-title {
  margin: 0;
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--color-text);
}
.upgrade-gate-text {
  margin: 0;
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: 1.6;
}
.upgrade-gate-text strong {
  color: var(--color-text);
  font-weight: 600;
}
.upgrade-gate-perks {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
  margin: var(--space-2) 0;
  padding: var(--space-3) var(--space-5);
  list-style: none;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
}
.upgrade-gate-perks .perk-tier {
  display: inline-block;
  min-width: 80px;
  font-weight: 600;
  color: var(--color-accent-text);
}
.upgrade-gate-hint {
  margin: var(--space-1) 0 0;
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.upgrade-gate .primary-btn {
  padding: var(--space-2) var(--space-6);
  margin-top: var(--space-2);
  font-size: var(--text-sm);
  font-weight: 500;
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border-radius: var(--radius-md);
  border: 0;
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out);
}
.upgrade-gate .primary-btn:hover {
  background: var(--color-accent-hover);
}
.banner-icon {
  font-size: var(--text-xl);
  line-height: 1;
  color: #B45309;
  flex-shrink: 0;
  margin-top: 2px;
}
.banner-body {
  flex: 1;
  min-width: 0;
}
.banner-title {
  display: block;
  font-size: var(--text-sm);
  color: #B45309;
  font-weight: 600;
  margin-bottom: 4px;
}
.banner-text {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--color-text);
  line-height: 1.6;
}

.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-6);
}

.page-title {
  font-size: var(--text-2xl);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}

/* 代码屎山清理(2026-05-21):.page-subtitle 已删 — 副标题"文本一键变漫画"删除 */

.primary-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) var(--ease-out);
  flex-shrink: 0;
}

.primary-btn:hover {
  background: var(--color-accent-hover);
}

/* 代码屎山清理(2026-05-21):.plus-icon 已删 — "+ 新建漫画"按钮已收归首页 4 态卡 */

.state-msg {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-muted);
}

.state-error {
  color: var(--color-danger);
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-8) var(--space-5);
  text-align: center;
}

.empty-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}

.empty-hint {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  max-width: 480px;
  margin: 0;
  line-height: 1.6;
}

/*
  UI 优化(2026-05-21 六轮):.empty-state--hero — 占满首屏垂直空间的大空态
  抽象插画(漫画格子)+ 大标题 + 引导文案,无 CTA(入口在首页"漫创态")
*/
.empty-state--hero {
  min-height: 60vh;
  justify-content: center;
  gap: var(--space-5);
  padding: var(--space-8) var(--space-5);
}
.empty-illustration {
  color: var(--color-text-subtle);
  opacity: 0.45;
  transition: opacity var(--duration-base) var(--ease-out);
}
.empty-state--hero:hover .empty-illustration {
  opacity: 0.6;
}
.empty-title-large {
  font-size: var(--text-xl);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
  letter-spacing: 0.02em;
}
.empty-sub {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin: 0;
}

/* 漫画卡片 grid:大屏 3 列,中屏 2 列,小屏 1 列 */
.comics-grid,
.skeleton-grid {
  list-style: none;
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-4);
  padding: 0;
}

@media (max-width: 960px) {
  .comics-grid,
  .skeleton-grid { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 600px) {
  .comics-grid,
  .skeleton-grid { grid-template-columns: 1fr; }
}

.comic-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  overflow: hidden;
  cursor: pointer;
  transition:
    border-color var(--duration-fast) var(--ease-out),
    transform var(--duration-fast) var(--ease-out),
    box-shadow var(--duration-fast) var(--ease-out);
  outline: none;
}

.comic-card:hover,
.comic-card:focus-visible {
  border-color: var(--color-accent-border);
  transform: translateY(-2px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.06);
}

.card-cover {
  aspect-ratio: 4 / 3;
  background: var(--color-bg-subtle);
  overflow: hidden;
  position: relative;   /* Sprint 2.B+ 四修:让 .card-more-btn + .card-menu 绝对定位 */
}

/* ===== Sprint 2.B+ 四修:卡片 hover ⋯ 菜单 ===== */
.card-more-btn {
  position: absolute;
  top: var(--space-2);
  right: var(--space-2);
  width: 32px;
  height: 32px;
  border-radius: var(--radius-full);
  background: rgba(0, 0, 0, 0.55);
  color: #fff;
  font-size: var(--text-lg);
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  opacity: 0;
  transition: opacity var(--duration-fast) var(--ease-out),
              background var(--duration-fast) var(--ease-out);
  z-index: 2;
}
.comic-card:hover .card-more-btn,
.comic-card:focus-within .card-more-btn,
.comic-card.is-menu-open .card-more-btn {
  opacity: 1;
}
.card-more-btn:hover {
  background: rgba(0, 0, 0, 0.8);
}

.card-menu {
  position: absolute;
  top: calc(var(--space-2) + 36px);   /* 32px ⋯ btn + 4px gap */
  right: var(--space-2);
  min-width: 130px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-md);
  padding: var(--space-1);
  z-index: 3;
}
.menu-item {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  text-align: left;
  font-size: var(--text-sm);
  color: var(--color-text);
  background: transparent;
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: background var(--duration-fast) var(--ease-out);
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

/* queued 态"下一步"提示 — 不喧宾夺主 */
.card-next-hint {
  margin: 0;
  padding: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
  line-height: 1.4;
  text-align: center;
}

.card-cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.card-cover-placeholder {
  display: flex;
  align-items: center;
  justify-content: center;
  height: 100%;
  background: var(--color-bg-subtle);
}

.placeholder-text {
  font-size: var(--text-sm);
  color: var(--color-text-subtle);
}

.card-meta {
  padding: var(--space-3) var(--space-4) var(--space-4);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.card-name {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.card-info-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-2);
}

.state-chip {
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  font-weight: 500;
  border-radius: var(--radius-sm);
  background: var(--color-bg-subtle);
  color: var(--color-text-muted);
}

.state-chip.state-done {
  color: #16A34A;
  background: rgba(22, 163, 74, 0.1);
}
.state-chip.state-failed {
  color: var(--color-danger);
  background: var(--color-danger-soft);
}
.state-chip.state-style_voting,
.state-chip.state-style_uploading {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
}

.card-time {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

.card-progress {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.progress-bar {
  flex: 1;
  height: 4px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.progress-fill {
  height: 100%;
  background: var(--color-accent);
  border-radius: var(--radius-full);
  transition: width var(--duration-base) var(--ease-out);
}

.progress-fill.is-failed {
  background: var(--color-danger);
}

.progress-text {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  flex-shrink: 0;
}

.mono {
  font-family: var(--font-mono);
}

/* 骨架占位 */
.comic-card--skeleton {
  cursor: default;
}

.comic-card--skeleton:hover {
  transform: none;
  box-shadow: none;
  border-color: var(--color-border);
}

.skeleton-line {
  display: block;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
}
</style>
