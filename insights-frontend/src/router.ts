import { createRouter, createWebHistory } from "vue-router";

import OverviewView from "./views/OverviewView.vue";
import FunnelView from "./views/FunnelView.vue";
import RetentionView from "./views/RetentionView.vue";
import UserProfileView from "./views/UserProfileView.vue";
import KnowledgeView from "./views/KnowledgeView.vue";
import SummaryDetailView from "./views/SummaryDetailView.vue";
// INS-B Phase 1(2026-05-27 末⁵):4 个经营 dashboard
import QualityView from "./views/QualityView.vue";
import SafetyView from "./views/SafetyView.vue";
import CompassView from "./views/CompassView.vue";
import LiveView from "./views/LiveView.vue";
// INS-B Phase 3(2026-05-27 末⁵²):经营驾驶舱
import BusinessView from "./views/BusinessView.vue";
// INS-B Phase 4(2026-05-27 末⁵³):用户画像独立大 tab
import UsersListView from "./views/UsersListView.vue";
// BYOK 审核后台(2026-06-05):自携密钥订单人工审核 — 调主平台 backend admin endpoint
import BYOKReviewView from "./views/BYOKReviewView.vue";
// 2026-06-09 — 剧创态(第 5 态)使用洞察
import ScreenplayAnalyticsView from "./views/ScreenplayAnalyticsView.vue";
// 2026-06-09 — 统一订单审核(商业化重塑 P1)
import OrderReviewView from "./views/OrderReviewView.vue";

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", name: "overview", component: OverviewView },
    { path: "/funnel", name: "funnel", component: FunnelView },
    { path: "/retention", name: "retention", component: RetentionView },
    { path: "/user/:id", name: "user", component: UserProfileView, props: true },
    // INS-A8(2026-05-27):知识库 — agent 状态 + 日报 + 阶段汇总
    { path: "/knowledge", name: "knowledge", component: KnowledgeView },
    {
      path: "/knowledge/summary/:id",
      name: "summary-detail",
      component: SummaryDetailView,
      props: true,
    },
    // INS-B Phase 1(2026-05-27 末⁵):经营 / 安全 / 指南针 / 实时流
    { path: "/quality", name: "quality", component: QualityView },
    { path: "/safety", name: "safety", component: SafetyView },
    { path: "/compass", name: "compass", component: CompassView },
    { path: "/live", name: "live", component: LiveView },
    // INS-B Phase 3(2026-05-27 末⁵²):经营驾驶舱(老板视角)
    { path: "/business", name: "business", component: BusinessView },
    // INS-B Phase 4(2026-05-27 末⁵³):用户画像独立大 tab
    { path: "/users", name: "users", component: UsersListView },
    // BYOK 审核后台(2026-06-05)
    { path: "/byok-review", name: "byok-review", component: BYOKReviewView },
    // 2026-06-09:剧创态使用洞察
    { path: "/screenplay", name: "screenplay-analytics", component: ScreenplayAnalyticsView },
    // 2026-06-09:统一订单审核(订阅 / BYOK / 配额)
    { path: "/orders", name: "order-review", component: OrderReviewView },
  ],
});
