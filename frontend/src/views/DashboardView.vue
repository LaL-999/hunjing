<script setup lang="ts">
/**
 * DashboardView v4 — 极简首页(Sprint 6.A2 M7.D-fix,2026-05-20)。
 *
 * IA 重构最终方案(用户拍板):
 *   - 首页主区域不再展示项目列表 / 推演列表 — 侧栏的项目列表已经是"项目总览"
 *   - 主区域始终展示「开始一段新创作」4 象限选态 — 高级简洁,创作入口零跳转
 *   - 用户管理项目 → 走侧栏;管理某项目的推演 → 进入该项目页(默认看作品列表 Tab)
 *
 * 状态分屏:
 *   游客              → hero + CTA(同 v1)
 *   已登录            → 标题 + 副标题 + 4 象限(无论有无项目都一样)
 *
 * 删掉了:
 *   - 项目卡片网格(冗余,侧栏已经是项目总览)
 *   - "+ 创建新作"按钮(4 象限本身就是创建入口)
 *   - ChooseModeModal(主区域直接是 quadrant,不需要二级弹窗)
 *
 * 保留:
 *   - 永久保留承诺 hint(产品对用户的核心承诺,信任锚点)
 *   - 4 象限创作态选择(模式分发逻辑)
 */
import { ref } from "vue";
import { useRouter } from "vue-router";

import {
  type ProjectMode,
} from "../api/types";
import CreationModeQuadrant from "../components/CreationModeQuadrant.vue";
// UI 优化(2026-05-21 六轮):漫创态卡片点击 → 弹 CreateComicModal,与前 3 态逻辑一致
import CreateComicModal from "../components/CreateComicModal.vue";
import { useAuthStore } from "../stores/auth";
import { useQuotaStore } from "../stores/quota";
import { useLoginModal } from "../composables/useLoginModal";
import { useNewProjectModal } from "../composables/useNewProjectModal";
import { useUpgradeModal } from "../composables/useUpgradeModal";
import { toast } from "../composables/useToast";

const auth = useAuthStore();
const quota = useQuotaStore();
const loginModal = useLoginModal();
const newProjectModal = useNewProjectModal();
const upgradeModal = useUpgradeModal();
const router = useRouter();
// 漫创态 modal 本地开关(对齐 NewProjectModal 的 singleton 风格)
const createComicOpen = ref(false);

// ============================================================
// 数据初始化 — 仅刷新当前用户(若已登录但未拉取 me)
// ============================================================

const ready = ref(false);

async function init() {
  if (auth.isAuthed && !auth.currentUser) {
    await auth.fetchMe();
  }
  ready.value = true;
}
init();

// ============================================================
// 操作
// ============================================================

function handleHeroStart() {
  if (!auth.isAuthed) {
    loginModal.open();
    return;
  }
  // 已登录但点 hero CTA 罕见 — 此时主区域已经是 4 象限,直接交互即可
}

function onModeSelected(mode: ProjectMode) {
  if (mode === "initial" || mode === "middle" || mode === "end") {
    newProjectModal.open(mode);
  } else if (mode === "cycle") {
    // UI 优化(2026-05-21 六轮):漫创态点击 = 弹"新建漫画"对话框
    if (!auth.isAuthed) {
      loginModal.open("/my-comics");
      return;
    }
    // 2026-06-02:漫创态仅限 Pro 及以上(产品决策)— Free 弹升级
    const plan = quota.status?.plan;
    if (plan === "free") {
      toast.info("漫创态是会员专属功能,升级 Pro 即可解锁");
      upgradeModal.open();
      return;
    }
    createComicOpen.value = true;
  } else if (mode === "screenplay") {
    // Sprint SP-S(2026-06-07)第 5 态 — 剧创态
    // 不走 NewProjectModal,直接跳独立路由(剧创态有自己的小说上传 UI)
    if (!auth.isAuthed) {
      loginModal.open("/screenplay");
      return;
    }
    router.push("/screenplay");
  }
  // mode === "more" 不会走到这里 — CreationModeQuadrant 内部 toast 已拦截
}

/** 漫画创建成功 — CreateComicModal 内部已自管 router.push 到详情页,这里只 close */
function onComicCreated(_comicId: string) {
  createComicOpen.value = false;
}
</script>

<template>
  <div class="dashboard">
    <!-- 游客:极简 hero -->
    <template v-if="!auth.isAuthed">
      <div class="hero-stack">
        <h1 class="hero-title">让每一个意难平,都有一个版本</h1>
        <button class="primary-btn" @click="handleHeroStart">开始</button>
      </div>
    </template>

    <!-- 已登录:直接展示「开始一段新创作」4 象限(无论有无项目都一样)-->
    <template v-else-if="ready">
      <section class="create-hero">
        <h1 class="hero-title">开始一段新创作</h1>
        <p class="hero-sub">先选一个创作态 — 决定你接下来怎么和 AI 协作</p>
      </section>

      <CreationModeQuadrant @select="onModeSelected" />
      <!-- Sprint SP-S(2026-06-07):移除"永久保留"承诺 hint,把 4 象限扩为 6 卡;
           保留承诺仍是产品政策,但 UI 上不再每页重复 — 用户预期已锚定。 -->
    </template>

    <!--
      UI 优化(2026-05-21 六轮):漫创态卡片 → 弹 CreateComicModal,逻辑统一
      创建成功后 modal 内部自管 router.push("/comics/:id") 进画风上传流程
    -->
    <CreateComicModal
      v-if="auth.isAuthed"
      :open="createComicOpen"
      @close="createComicOpen = false"
      @created="onComicCreated"
    />
  </div>
</template>

<style scoped>
.dashboard {
  min-height: 100%;
  padding: var(--space-6);
}

/* ===== 游客 hero ===== */
.hero-stack {
  height: 80vh;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-6);
  text-align: center;
}

/* ===== 已登录创作 hero(顶部小标题,留主视野给 quadrant)===== */
.create-hero {
  text-align: center;
  padding: var(--space-8) var(--space-4) var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.hero-title {
  font-size: var(--text-2xl);
  font-weight: 600;
  color: var(--color-text);
  letter-spacing: 0;
  margin: 0;
}
.hero-sub {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin: 0;
}

.primary-btn {
  padding: var(--space-3) var(--space-6);
  font-size: var(--text-base);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-lg);
  transition: background var(--duration-fast) var(--ease-out),
              box-shadow var(--duration-base) var(--ease-out),
              transform var(--duration-base) var(--ease-out);
}
.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
  /* Sprint 6.A2 polish:hover 加紫色 glow + 微抬,主入口按钮"凸起"感 */
  box-shadow: 0 4px 16px rgba(124, 58, 237, 0.28);
  transform: translateY(-1px);
}

/* Sprint SP-S(2026-06-07):.forever-hint 已从模板移除(6 卡布局后视觉过满)
   保留承诺政策不变,只是不在首页每次出现 */
</style>
