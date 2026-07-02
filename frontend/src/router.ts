/**
 * 路由配置 + 全局守卫(v2 重构,移除 /login 路由)。
 *
 * 路由表(MVP 阶段 1,Claude/ChatGPT 风游客优先):
 *   /                       重定向 /dashboard
 *   /dashboard              所有人可访问(游客看欢迎页 + CTA)
 *   /projects/:id           需登录,未登录 → 弹 LoginModal,redirect /dashboard
 *   /projects/:id/graph     需登录,fullscreen 布局(无 sidebar)
 *
 * 没有独立 /login 路由 — 登录走全局 LoginModal(useLoginModal())。
 *
 * Sprint 6.A2 polish(2026-05-22):每个 route 加 meta.depth 数字,
 * App.vue 根据 from→to depth 差驱动 slide-left / slide-right / fade 过渡方向感。
 *   - depth 0:首页 dashboard
 *   - depth 1:一级列表 / 详情(/my-comics / /credit-history / /projects/:id)
 *   - depth 2:二级深入(/projects/:id/graph / /simulations/:id / /comics/:id / counterfactual-tree)
 *   - depth 3:三级深入(/simulations/:id/outline / /comics/:id/read)
 */
import {
  createRouter,
  createWebHistory,
  type RouteRecordRaw,
} from "vue-router";

import { useAuthStore } from "./stores/auth";
import { useLoginModal } from "./composables/useLoginModal";

const routes: RouteRecordRaw[] = [
  {
    path: "/",
    redirect: "/dashboard",
  },
  {
    path: "/dashboard",
    name: "dashboard",
    component: () => import("./views/DashboardView.vue"),
    meta: { requiresAuth: false, depth: 0 },   // 游客也能进
  },
  {
    // Sprint SP-S(2026-06-07)新增第 5 态 — 剧创态主页 + 编辑器
    //   入口:Dashboard 6 象限 → 点击"剧创态"卡 → 进入此路由
    //   /screenplay              小说书架 + 上传卡
    //   /screenplay/novels/:id   双栏编辑器(原文 + 剧本)
    path: "/screenplay",
    name: "screenplay-home",
    component: () => import("./screenplay/views/ScreenplayHomeView.vue"),
    meta: { requiresAuth: true, depth: 1 },
  },
  {
    path: "/screenplay/novels/:id",
    name: "screenplay-editor",
    component: () => import("./screenplay/views/ScreenplayEditorView.vue"),
    meta: { requiresAuth: true, depth: 2 },
    props: true,
  },
  {
    path: "/projects/:id",
    name: "project",
    component: () => import("./views/ProjectView.vue"),
    meta: { requiresAuth: true, depth: 1 },
    props: true,
  },
  {
    path: "/projects/:id/graph",
    name: "project-graph",
    component: () => import("./views/ProjectGraphView.vue"),
    meta: { requiresAuth: true, fullscreen: true, depth: 2 },
    props: true,
  },
  {
    // Sprint 1.J 我的剧情线 — 单条推演详情
    path: "/simulations/:id",
    name: "simulation-detail",
    component: () => import("./views/SimulationDetailView.vue"),
    meta: { requiresAuth: true, depth: 2 },
    props: true,
  },
  {
    // Sprint 6.A2(2026-05-22):推演产物沉浸式阅读器
    //   横向翻页 / 字号 / 单双页 / 三主题 / 键盘交互 / 沉浸 chrome
    //   入口:SimulationDetailView 的"📖 在线阅读"按钮
    path: "/simulations/:id/read",
    name: "simulation-read",
    component: () => import("./views/SimulationReadView.vue"),
    meta: { requiresAuth: true, fullscreen: true, depth: 3 },
    props: true,
  },
  {
    // Sprint 6.A2 M6(2026-05-20):Outline-first 长篇生成 — 用户审核 / 编辑 outline
    // SimulationDock 创建 sim 后 use_outline_first=true → 跳此路由
    // 用户在此编辑 outline + 批准 → 启动逐幕生成 → 跳 /simulations/:id 看进度
    path: "/simulations/:id/outline",
    name: "simulation-outline",
    component: () => import("./views/OutlineReviewView.vue"),
    meta: { requiresAuth: true, depth: 3 },
    props: true,
  },
  {
    // D.9 Sprint 2.B 我的漫画(漫画态项目列表)
    // Sprint 2.B+(2026-05-12):requiresAuth: true → false
    //   bug 修复 — 用户从 sidebar 直接入口 / 4 态卡片点击若 token 失效,beforeEach 会
    //   静默 redirect 到 /dashboard(行为像"点了没反应跳主页")。改为对游客开放,view
    //   内部自管未登录态(显登录 hero CTA),避免静默跳转。漫画详情仍 requiresAuth: true。
    path: "/my-comics",
    name: "my-comics",
    component: () => import("./views/MyComicsView.vue"),
    meta: { requiresAuth: false, depth: 1 },
  },
  {
    // 2026-06-08:「我的剧本」专属书架页(跟我的漫画平级)
    // 跟 /my-comics 同样设计:requiresAuth false,view 自管未登录态
    path: "/my-screenplays",
    name: "my-screenplays",
    component: () => import("./views/MyScreenplaysView.vue"),
    meta: { requiresAuth: false, depth: 1 },
  },
  {
    // D.9 Sprint 2.B 漫画态项目详情(独立 view,不复用 ProjectView)
    path: "/comics/:id",
    name: "comic-project",
    component: () => import("./views/ComicProjectView.vue"),
    meta: { requiresAuth: true, fullscreen: true, depth: 2 },
    props: true,
  },
  {
    // D.9 Sprint 4.A 漫画阅读器(全屏沉浸式翻页 + 缩放 + 平移)
    // 入口:ComicProjectView done 状态的"📖 阅读全本"CTA;state ≠ done 时显占位 + 返回链
    path: "/comics/:id/read",
    name: "comic-reader",
    component: () => import("./views/ComicReaderView.vue"),
    meta: { requiresAuth: true, fullscreen: true, depth: 3 },
    props: true,
  },
  {
    // Sprint C.3 我的 credit 消费记录
    path: "/credit-history",
    name: "credit-history",
    component: () => import("./views/CreditTransactionsView.vue"),
    meta: { requiresAuth: true, depth: 1 },
  },
  {
    // 2026-06-04:BYOK 自携密钥独立配置页
    path: "/byok-config",
    name: "byok-config",
    component: () => import("./views/BYOKConfigView.vue"),
    meta: { requiresAuth: true, depth: 1 },
  },
  // 2026-06-05:BYOK 审核后台已挪到洞察后台(insights-frontend),
  // 此处不再有路由 — admin 不应混在用户平台里。
  {
    // Sprint 6.A2 CT(2026-05-21)— 反事实组合树
    // 无 combo_id 时显示配置页(选 1-3 反事实变量 + 配置批次)
    // 有 combo_id 时显示决策树视图(SVG 渲染 2^N 叶 sim 状态)
    path: "/projects/:id/counterfactual-tree/:combo_id?",
    name: "counterfactual-tree",
    component: () => import("./views/CounterfactualTreeView.vue"),
    meta: { requiresAuth: true, depth: 2 },
    props: true,
  },
  {
    // SP-9(2026-05-29)— 反事实分支并排对比
    // ?a=simA&b=simB 两 query 参数指定要对比的 2 个 sim
    path: "/projects/:id/compare",
    name: "simulation-compare",
    component: () => import("./views/SimulationCompareView.vue"),
    meta: { requiresAuth: true, depth: 2 },
    props: true,
  },
  {
    // 2026-06-06:续作家族树(滚雪球链 + 反事实组合批次的可视化)
    path: "/projects/:id/family-tree",
    name: "simulation-family-tree",
    component: () => import("./views/SimulationFamilyTreeView.vue"),
    meta: { requiresAuth: true, depth: 2 },
    props: true,
  },
  {
    // 2026-06-25:「更多玩法」主视图(网易云式 banner 入口)
    // Dashboard 第 6 张卡「更多玩法」点击 → 进此独立右主视图(非弹窗)
    path: "/playground",
    name: "playground",
    component: () => import("./views/PlaygroundView.vue"),
    meta: { requiresAuth: false, depth: 1 },
  },
  {
    // 2026-06-25:作品广场(社区发布画廊)
    // 游客也能逛 + 阅读;上架 / 点赞需登录(view 内部触发 LoginModal)
    path: "/plaza",
    name: "plaza",
    component: () => import("./views/PlazaView.vue"),
    meta: { requiresAuth: false, depth: 2 },
  },
  {
    // v5(2026-07-02):作品详情落地页(预览 + 元信息 + 在线阅读/下载/权限)
    path: "/plaza/works/:id",
    name: "plaza-detail",
    component: () => import("./views/PlazaWorkDetailView.vue"),
    meta: { requiresAuth: false, depth: 3 },
    props: true,
  },
  {
    // v5:沉浸式全屏阅读器(从详情页点"在线阅读"进入)
    path: "/plaza/works/:id/read",
    name: "plaza-read",
    component: () => import("./views/PlazaReadView.vue"),
    meta: { requiresAuth: false, fullscreen: true, depth: 4 },
    props: true,
  },
  {
    // 兜底:未匹配的 → /dashboard
    path: "/:pathMatch(.*)*",
    redirect: "/",
  },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to) => {
  const auth = useAuthStore();
  const loginModal = useLoginModal();
  const requiresAuth = to.meta.requiresAuth === true;

  if (requiresAuth && !auth.isAuthed) {
    // 触发全局登录模态,redirect 回原目标
    loginModal.open(to.fullPath);
    return { name: "dashboard" };
  }
});

// INS-A3(2026-05-27):洞察埋点 — page_view / page_leave 自动派发
// 设计:每次路由切换,旧页发 page_leave + duration,新页发 page_view
//   - 零侵入:不动业务代码,纯 router 钩子
//   - 零阻塞:埋点静默吞,绝不影响导航
import { track as analyticsTrack } from "./composables/useAnalytics";

let _lastPath: string | null = null;
let _lastEnterMs: number | null = null;

router.afterEach((to, _from) => {
  const nowMs = Date.now();
  // 离开旧页面:发 page_leave + duration
  if (_lastPath && _lastEnterMs !== null) {
    analyticsTrack("page_leave", {
      path: _lastPath,
      duration_ms: nowMs - _lastEnterMs,
    });
  }
  // 进入新页面:发 page_view
  analyticsTrack("page_view", { path: to.fullPath });
  _lastPath = to.fullPath;
  _lastEnterMs = nowMs;
});
