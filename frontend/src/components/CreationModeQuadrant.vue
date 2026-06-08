<script setup lang="ts">
/**
 * CreationModeQuadrant — 4 态创作入口选择器(Sprint 2.A.F)。
 *
 * 浑晶四态心智锚定的核心 UI 组件。memory v6.1 line 41-50 标的"四态只是入口和约束不同"
 * 在前端落地为 2x2 卡片网格,让用户进首屏第一眼就理解"我是哪种创作者"。
 *
 * 视觉风格:浅色 + 简洁 + 专业感(Linear / Notion / Vercel 风),不用 emoji,
 * 用清晰类型层级 + minimal 几何符号 + 紫色 accent。
 *
 * Props:
 *   compact   boolean  默认 false(2x2 大卡);true 时横排紧凑(留扩展)
 *
 * Emits:
 *   select(mode)  用户点已可用态 → 父组件开 NewProjectModal
 *   unavailable(mode)  用户点未实现态 → 父组件 toast.info 解释
 */
import { type ProjectMode } from "../api/types";
import { toast } from "../composables/useToast";

interface ModeCard {
  mode: ProjectMode;
  index: number;            // 1-4 序号(角标用)
  title: string;
  description: string;
  status: "available" | "beta" | "experimental" | "soon";
  statusLabel: string;
  // UI 优化(2026-05-21 四轮):抽象几何图标,SVG path 数据(无填充,1.5 stroke,viewBox 24×24)
  // 用 path 的 d 属性数组方便组合,fragment 列表里每个 path 渲染为独立 <path>
  iconPaths: string[];
  // SP-S 阶段 6(2026-06-08):差异化卖点 tag,放在 description 下方
  // 不显示则不渲染 — 只有 screenplay / future modes 需要
  diffTag?: string;
}

/**
 * 4 态状态白名单 — **每完工一个 sprint 必须同步更新此表**(执行纪律 8)。
 *
 * 当前状态(Sprint 5.B 降级 — 2026-05-18):
 *   initial   ✅ 可用       B 阶段完工
 *   middle    ✅ 可用       C 阶段(2.A-2.E)完工
 *   end       ✅ 可用       D 阶段 3.A 完工
 *   cycle     ⚗ 实验中     **战略性降级**(原"内测"→"实验中") — 漫画态主流程虽通,
 *                          但 AI 生图模型(国产 Qwen-Image / CogView-4)对角色一致性 /
 *                          复杂镜头 / 画面对话对齐能力有上限,实测产物质量 6/10,
 *                          不符合产品 8/10 ship 标准。
 *                          路线:不主推 + 不删入口,等行业生图模型质变(预计 FLUX 2.0 /
 *                          SD 4.0 / Wan 2.5,18-24 月内)再开放主推。
 *                          商业模型变更(Sprint 5.B):漫画态不再消耗 credit,改为
 *                          订阅福利"免费次数":Free 0 / Pro 1 / Max 2 / 超级 Max 4 本/月
 *                          详 docs/ADR_漫画创作态架构.md v3 + 5.B 降级备忘
 *
 * status 取值 → statusLabel 映射:
 *   "available"    → "可用"     完工 + 上线,正常入口
 *   "beta"         → "内测"     完工 + 上线但有已知功能边界
 *   "experimental" → "实验中"   完工但效果未达 ship 标准,提供给愿尝鲜的用户(漫画态)
 *   "soon"         → "开发中"   未实现,点卡片只弹 toast 不进入流程
 */
const MODE_CARDS: ModeCard[] = [
  {
    mode: "initial",
    index: 1,
    title: "初始态",
    description: "我从零创造一个世界 — 角色、关系、事件全自己定。",
    status: "available",
    statusLabel: "可用",
    // 种子 / 萌芽 — 圆形 + 朝上的小芽叶
    iconPaths: [
      "M12 22V13",                            // 茎(向下到根部)
      "M12 13a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z", // 圆形种子
      "M12 13c0-3 2-5 4-5",                  // 右侧嫩芽叶
    ],
  },
  {
    mode: "middle",
    index: 2,
    title: "中间态",
    description: "导入已有作品 — AI 自动拆解人物、关系、事件,你在 3D 图谱里改属性触发剧情重塑。",
    status: "available",
    statusLabel: "可用",
    // 树状分支 — 1 根节点 + 3 个子节点连线
    iconPaths: [
      "M12 5V2",                              // 顶部端点
      "M12 5a3 3 0 1 0 0 0Z",                 // 顶部圆心
      "M12 5v6",                              // 主茎
      "M6 19a3 3 0 1 0 0 0Z",                 // 左子节点圆心
      "M18 19a3 3 0 1 0 0 0Z",                // 右子节点圆心
      "M12 19a3 3 0 1 0 0 0Z",                // 中子节点圆心
      "M12 11l-6 5",                          // 主茎 → 左
      "M12 11v5",                             // 主茎 → 中
      "M12 11l6 5",                           // 主茎 → 右
    ],
  },
  {
    mode: "end",
    index: 3,
    title: "末尾态",
    description: "导入已有作品 — 不改原剧情,从末尾一键续写。",
    status: "available",
    statusLabel: "可用",
    // 终点旗 — 旗杆 + 三角旗 + 底座
    iconPaths: [
      "M5 22V4",                              // 旗杆
      "M5 4h12l-3 4 3 4H5",                   // 三角旗(顶部)
      "M3 22h6",                              // 底座
    ],
  },
  {
    // Sprint 3.A polish 5(2026-05-11):第 4 态重定义,原"周期态"语义并入中间态
    // Sprint 2.B(2026-05-12):后端 4 agent + 前端 UI 编码完成,状态 'soon' → 'beta'
    // Sprint 2.B+(2026-05-12):UI 改名"漫画创作态" → "漫创态"(短名;架构层保留全称)
    // **Sprint 5.B 降级(2026-05-18)**:'beta' → 'experimental' — 实测产物质量未达
    //   ship 标准,降级保留入口等行业生图模型质变。商业模型同步从 credit 改订阅次数福利。
    mode: "cycle",
    index: 4,
    title: "漫创态",
    description: "已有文本 → AI 漫画分格(实验中;Pro 及以上会员专属)。",
    status: "experimental",
    statusLabel: "⚗ 实验中",
    // 漫画分格 — 2×2 frames
    iconPaths: [
      "M3 3h8v8H3z",                          // 左上格
      "M13 3h8v8h-8z",                        // 右上格
      "M3 13h8v8H3z",                         // 左下格
      "M13 13h8v8h-8z",                       // 右下格
    ],
  },
  {
    // Sprint SP-S(2026-06-07)新增第 5 态:剧创态
    //   位置:末尾态下方(2×3 / 3×2 布局时第 5 格)
    //   入口:点击 → router.push('/screenplay'),不走 NewProjectModal
    //   关系:复用浑晶中间态已抽的 character_drivers / character_knowledge /
    //         relationship_polarity(huimeng_bridge 桥接,见 services/screenplay/)
    mode: "screenplay",
    index: 5,
    title: "剧创态",
    description: "导入小说 → AI 转剧本 YAML;5 种改编手法 + 版本树 + 张力曲线。",
    status: "available",
    statusLabel: "可用",
    // 胶片 / 场记板抽象 — 顶部条带 + 下方 frame
    iconPaths: [
      "M3 6h18",                              // 顶部胶片孔条
      "M6 3v6",                               // 左侧打孔
      "M12 3v6",                              // 中打孔
      "M18 3v6",                              // 右打孔
      "M3 10h18v11H3z",                       // 主 frame(剧本页)
      "M7 14h10",                             // 内文 line 1
      "M7 17h7",                              // 内文 line 2
    ],
    // 阶段 6:差异化卖点 — 仅剧创态接通父平台 SP-2/3/4/7 资产
    diffTag: "可接通 浑晶角色驱动力 · 知识边界 · 状态时间线 · 关系正负极",
  },
  {
    // Sprint SP-S 占位卡 — 给未来的第 6/7 态留个 hint,UI 上的"我们还没停下来"承诺
    // 触发 toast 提示,不进入流程
    mode: "more",
    index: 6,
    title: "更多玩法",
    description: "我们还在思考下一种创作姿势 — 欢迎在群里告诉我们你想要的。",
    status: "soon",
    statusLabel: "开发中",
    // 三个点 — 暗示"待续"
    iconPaths: [
      "M5 12h.01",
      "M12 12h.01",
      "M19 12h.01",
      "M3 12a9 9 0 1 0 18 0 9 9 0 0 0 -18 0",
    ],
  },
];

defineProps<{
  compact?: boolean;
}>();

const emit = defineEmits<{
  (e: "select", mode: ProjectMode): void;
  (e: "unavailable", mode: ProjectMode): void;
}>();

function handleClick(card: ModeCard) {
  if (card.status === "soon") {
    // 漫创态有特殊触发条件:公司注册 + AIGC 备案 + 国内绘图 API 路由 + 读者层验证
    // 都达成后才开发(详见 docs/ADR_漫画创作态架构.md)。toast 给用户清晰预期。
    // 注:Sprint 2.B 起漫创态状态已升为 'beta',此 toast 分支保留以防未来 cycle 再降级
    // 或新增 'soon' 态(执行纪律 7 "mode 白名单要同步")。
    if (card.mode === "cycle") {
      toast.info(
        "漫创态规划中 — 10 agent 流水线(编剧 / 画风定调员 / 角色锚定员 / 素材库抽取员 / 导演 / 生图 / 重绘 / 质检 / 排版 + 兜底),需等公司备案 + 国内绘图 API 路由完成后正式开放,详见架构 ADR。",
        6000,
      );
    } else {
      toast.info(
        `${card.title}还在开发中,敬请期待。当前可先体验初始态 / 中间态 / 末尾态。`,
        4500,
      );
    }
    emit("unavailable", card.mode);
    return;
  }
  emit("select", card.mode);
}
</script>

<template>
  <div class="quadrant" :class="{ 'is-compact': compact }">
    <button
      v-for="card in MODE_CARDS"
      :key="card.mode"
      type="button"
      class="card"
      :class="`card--${card.status}`"
      :aria-label="`选择 ${card.title}`"
      @click="handleClick(card)"
    >
      <header class="card-header">
        <!-- UI 优化(2026-05-21 四轮):card-index + 抽象几何 icon 同列 -->
        <span class="card-index-wrap">
          <svg
            class="card-icon"
            viewBox="0 0 24 24"
            width="20"
            height="20"
            fill="none"
            stroke="currentColor"
            stroke-width="1.6"
            stroke-linecap="round"
            stroke-linejoin="round"
            aria-hidden="true"
          >
            <path v-for="(d, i) in card.iconPaths" :key="i" :d="d" />
          </svg>
          <span class="card-index mono">{{ card.index.toString().padStart(2, "0") }}</span>
        </span>
        <!-- UI 优化(2026-05-21 四轮):去掉"可用"标签,只显示特殊状态(实验中/内测/开发中) -->
        <span
          v-if="card.status !== 'available'"
          class="status-chip"
          :class="`status-${card.status}`"
        >{{ card.statusLabel }}</span>
      </header>

      <div class="card-body">
        <h3 class="card-title">{{ card.title }}</h3>
        <p class="card-desc">{{ card.description }}</p>
        <p v-if="card.diffTag" class="card-diff-tag" :title="card.diffTag">
          {{ card.diffTag }}
        </p>
      </div>

      <footer class="card-footer">
        <span class="card-cta">
          <template v-if="card.status === 'soon'">查看 →</template>
          <template v-else>开始 →</template>
        </span>
      </footer>
    </button>
  </div>
</template>

<style scoped>
.quadrant {
  display: grid;
  /* SP-S(2026-06-07):4 → 6 卡布局,3×2 大屏 / 2×3 中屏 / 1col 小屏 */
  grid-template-columns: 1fr 1fr 1fr;
  gap: var(--space-4);
  width: 100%;
  max-width: 1200px;
  margin: 0 auto;
}

@media (max-width: 1080px) {
  .quadrant {
    grid-template-columns: 1fr 1fr;
    max-width: 880px;
  }
}
@media (max-width: 720px) {
  .quadrant {
    grid-template-columns: 1fr;
  }
}

.card {
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5) var(--space-5) var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  cursor: pointer;
  text-align: left;
  min-height: 180px;
  transition:
    border-color var(--duration-base) var(--ease-out),
    background var(--duration-base) var(--ease-out),
    transform var(--duration-base) var(--ease-out),
    box-shadow var(--duration-base) var(--ease-out);
}

/* hover:轻微抬升 + accent 边框 + 微阴影 */
.card:hover {
  border-color: var(--color-accent-border);
  transform: translateY(-2px);
  box-shadow: var(--shadow-md);
}
.card--soon:hover {
  /* 开发中卡仍可 hover 但抬升幅度小,暗示"看一看就好"*/
  transform: translateY(-1px);
  border-color: var(--color-border-strong);
  box-shadow: none;
}

.card--soon {
  background: var(--color-bg-subtle);
  cursor: pointer;   /* 仍可点(弹 toast),不用 not-allowed */
}

/* Sprint D.4 polish 2(2026-05-12 用户反馈"暗色下 4 张卡都该用第 4 张那种纯黑感"):
 * 暗色主题下,所有卡片(available + soon)统一用 bg-subtle(#08070A 极暗值)做底,
 * 让卡片在暗色背景中**凝重突出**,而不是当前浅深灰那种"漂浮"感。
 * 亮色主题不动 — 亮色下 surface(白)卡片浮在米色底已经够突出。 */
:root[data-theme="dark"] .card {
  background: var(--color-bg-subtle);
  /* 边框微调亮一点,让卡片轮廓在纯黑底上仍清晰 */
  border-color: var(--color-border);
}
:root[data-theme="dark"] .card:hover {
  /* 暗色 hover:边框升 accent-border + 轻微 lift,跟亮色等价 */
  border-color: var(--color-accent-border);
  background: var(--color-surface);   /* hover 时露一点 surface 让用户感知"被 focus" */
}
:root[data-theme="dark"] .card--soon:hover {
  background: var(--color-bg-subtle); /* 开发中卡 hover 不变背景 — 用户"看看就好"语义 */
  border-color: var(--color-border-strong);
}

/* card header:序号 + 状态 chip */
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: var(--space-2);
}
.card-index {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  letter-spacing: 0.1em;
}

/* UI 优化(2026-05-21 四轮):图标 + 序号同列,图标用 accent 色 */
.card-index-wrap {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.card-icon {
  flex-shrink: 0;
  color: var(--color-accent);
  opacity: 0.85;
  transition: transform var(--duration-fast) var(--ease-out), opacity var(--duration-fast) var(--ease-out);
}
.card:hover .card-icon {
  opacity: 1;
  transform: scale(1.08);
}
.card--soon .card-icon {
  opacity: 0.4;
}

.status-chip {
  padding: 2px var(--space-2);
  font-size: 11px;
  font-weight: 500;
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
}
.status-available {
  color: #16A34A;
  background: rgba(22, 163, 74, 0.08);
}
.status-beta {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
}
/* Sprint 5.B 降级(2026-05-18):'实验中' — 警示色,告诉用户产物质量不稳 */
.status-experimental {
  color: #B45309;
  background: rgba(245, 158, 11, 0.12);
  border: 1px solid rgba(245, 158, 11, 0.3);
}
.status-soon {
  color: var(--color-text-subtle);
  background: transparent;
  border: 1px dashed var(--color-border-strong);
}

/* card body:标题 + 描述 */
.card-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.card-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  line-height: 1.3;
  margin: 0;
}
.card--soon .card-title {
  color: var(--color-text-muted);
}

.card-desc {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: 1.7;     /* UI 优化(2026-05-21 四轮):行高 1.6 → 1.7 阅读更舒适 */
  margin: 0;
  /* UI 优化(2026-05-21 四轮):宽屏下限制描述最大宽度,避免单行过长 */
  max-width: 32em;
}

/* 阶段 6:剧创态差异化卖点 tag — 接通父平台资产 */
.card-diff-tag {
  margin: var(--space-2) 0 0;
  padding: 4px 10px;
  font-size: 11px;
  line-height: 1.5;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
  font-weight: 500;
  /* 单行省略避免破坏卡片高度 */
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  max-width: 100%;
}

/* card footer:CTA */
.card-footer {
  display: flex;
  justify-content: flex-end;
}
.card-cta {
  font-size: var(--text-sm);
  color: var(--color-accent-text);
  font-weight: 500;
  transition: transform var(--duration-fast) var(--ease-out);
}
.card--soon .card-cta {
  color: var(--color-text-subtle);
}
.card:hover .card-cta {
  transform: translateX(2px);
}

.mono {
  font-family: var(--font-mono);
}
</style>
