<script setup lang="ts">
/**
 * 剧本编辑器主视图 — PR#11 commit 1 骨架。
 *
 * 后续 commit 会填充:
 *  - commit 2:左栏原文 + 右栏剧本面板 + scene 联动
 *  - commit 3:改编决策 3 选项面板(差异化)
 *  - commit 4:触发 compose + 进度对话框
 */
import { computed, onMounted, provide, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import AdaptationDecisionPanel from "../components/AdaptationDecisionPanel.vue";
import BridgeGainBanner from "../components/BridgeGainBanner.vue";
import CharacterProfilesPanel from "../components/CharacterProfilesPanel.vue";
import ComparisonPanel from "../components/ComparisonPanel.vue";
import ComposeDialog from "../components/ComposeDialog.vue";
import EpisodePlanPanel from "../components/EpisodePlanPanel.vue";
import ExportMenu from "../components/ExportMenu.vue";
import NovelTextPanel from "../components/NovelTextPanel.vue";
import OptimizationModal from "../components/OptimizationModal.vue";
import ScreenplayPanel from "../components/ScreenplayPanel.vue";
import VersionDiffPanel from "../components/VersionDiffPanel.vue";
import VersionSwitcher from "../components/VersionSwitcher.vue";
import { useScreenplayStore } from "../stores/screenplay";
import type { OptimizeScope } from "../types/screenplay";
import { track } from "../../composables/useAnalytics";

const props = defineProps<{ id: string }>();
const router = useRouter();
const route = useRoute();
const store = useScreenplayStore();

/**
 * 2026-06-08 UI 升级:status 优化
 *
 * 用户反馈:左上角"错误:该作品尚未生成剧本 — 先 POST /novels/{id}/compose-screenplay"
 * 太"报错感",还带技术细节(POST 路径)。其实"尚未生成"是 expected initial state,
 * 不是真错误 — 右边主视窗已显"剧本待生成"占位卡,这里不需要重复且显眼地报错。
 *
 * 治理:
 *   1. 识别"尚未生成剧本"类错误 → 转 muted "待生成"提示(与初始 idle 态合并)
 *   2. 真错误(网络 / LLM 失败 / yaml 解析)→ 显示但去掉技术细节后缀(`— 先 POST ...`)
 */
function _stripTechSuffix(msg: string): string {
  // 去掉"—"或"-"之后的技术提示("先 POST /...")
  const cut = msg.split(/\s+[—-]\s+/)[0];
  return cut.trim();
}

const status = computed(() => {
  if (store.loadingState === "loading") return { label: "加载中...", tone: "muted" };
  if (store.loadingState === "composing")
    return { label: "AI 编排中(约 1-2 分钟)", tone: "muted" };
  if (store.loadingState === "error") {
    const raw = store.lastError || "";
    // "尚未生成"是 expected initial state,不是错误,合并到 idle 提示
    if (raw.includes("尚未生成") || raw.includes("未生成剧本")) {
      return { label: "待生成 — 点击右上「生成剧本」", tone: "muted" };
    }
    // 真错误:去技术细节后缀
    return { label: _stripTechSuffix(raw) || "加载失败", tone: "danger" };
  }
  if (store.loadingState === "ready") return { label: "已生成", tone: "success" };
  return { label: "待生成 — 点击右上「生成剧本」", tone: "muted" };
});

/**
 * 2026-06-08 用户精修:返回按钮智能识别来源(from query)
 *
 * 用户报告:从「我的剧本」点进剧本编辑器,返回时却跳到剧创态主页,
 * 不是回到「我的剧本」。
 *
 * 治理:openNovel 时带 `?from=my-screenplays` query 标识入口,
 * 返回按钮按 from 跳回对应来源(白名单防开放重定向)。
 *
 * 已知合法 from:
 *   - my-screenplays(我的剧本书架)
 *   - 默认:screenplay-home(剧创态主页)
 */
const ALLOWED_BACK_ROUTES: Record<string, string> = {
  "my-screenplays": "my-screenplays",
};

function goHome() {
  const from = route.query.from;
  if (typeof from === "string" && ALLOWED_BACK_ROUTES[from]) {
    router.push({ name: ALLOWED_BACK_ROUTES[from] });
    return;
  }
  // 默认退到剧创态主页
  router.push({ name: "screenplay-home" });
}

const composeDialogVisible = ref<boolean>(false);
function openComposeDialog() {
  // 2026-06-09 埋点 — 剧本生成入口触发(转化前)
  track("screenplay_compose_start", {
    mode: "screenplay",
    meta: { novel_id: props.id, has_screenplay: store.hasScreenplay },
  });
  composeDialogVisible.value = true;
}
function closeComposeDialog() {
  composeDialogVisible.value = false;
}

// 阶段 8.2:角色档案 + 关系图 全屏 modal
const characterPanelVisible = ref<boolean>(false);
function openCharacterPanel() {
  // 2026-06-09 埋点 — 角色档案使用率(差异化卖点)
  track("screenplay_characters_view", {
    mode: "screenplay",
    meta: { novel_id: props.id },
  });
  characterPanelVisible.value = true;
}
function closeCharacterPanel() { characterPanelVisible.value = false; }

// 阶段 8.3 第 3b 张牌:版本树 diff modal
const versionDiffVisible = ref<boolean>(false);
function openVersionDiff() { versionDiffVisible.value = true; }
function closeVersionDiff() { versionDiffVisible.value = false; }

// 阶段 8.4:分集规划 MVP modal
const episodePanelVisible = ref<boolean>(false);
function openEpisodePanel() {
  // 2026-06-09 埋点 — 分集规划功能使用
  track("screenplay_episodes_plan", {
    mode: "screenplay",
    meta: { novel_id: props.id },
  });
  episodePanelVisible.value = true;
}
function closeEpisodePanel() { episodePanelVisible.value = false; }

// 阶段 8.5:多模型对比 modal
const comparisonPanelVisible = ref<boolean>(false);
function openComparisonPanel() {
  // 2026-06-09 埋点 — 多模型对比入口(用户触发"想看不同 LLM 谁更强")
  track("model_compare_start", {
    mode: "screenplay",
    meta: { novel_id: props.id, screenplay_id: store.screenplayId ?? null },
  });
  comparisonPanelVisible.value = true;
}
function closeComparisonPanel() { comparisonPanelVisible.value = false; }

// AI 优化弹窗(A + B 入口共用)
const optimizationModalVisible = ref<boolean>(false);
const optimizationScope = ref<OptimizeScope>("full_screenplay");
const optimizationTargetScene = ref<string | undefined>(undefined);

function openOptimizationModal(
  scope: OptimizeScope,
  targetSceneId?: string,
) {
  optimizationScope.value = scope;
  optimizationTargetScene.value = targetSceneId;
  optimizationModalVisible.value = true;
}
function closeOptimizationModal() {
  optimizationModalVisible.value = false;
}

// 把 open 函数 provide 给子组件(StructureReportPanel / ScreenplayPanel 用)
provide("openOptimizationModal", openOptimizationModal);

// 路由变化时重新加载
watch(
  () => props.id,
  (id) => {
    if (id) store.loadLatestForNovel(id);
  },
  { immediate: false },
);

onMounted(() => {
  store.loadLatestForNovel(props.id);
});
</script>

<template>
  <div class="editor screenplay-module">
    <!-- ===== 顶栏 ===== -->
    <header class="topbar">
      <div class="topbar-left">
        <button class="back-btn" @click="goHome" title="返回主页">
          <svg
            width="16"
            height="16"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            stroke-width="2"
            stroke-linecap="round"
            stroke-linejoin="round"
          >
            <path d="M19 12H5M12 19l-7-7 7-7" />
          </svg>
        </button>
        <div class="title-block">
          <div class="novel-title">
            {{
              store.novelTitle ||
              (store.loadingState === "error" ? "—" : "加载中...")
            }}
          </div>
          <div class="status-line">
            <span :class="['status-pill', `status-pill--${status.tone}`]">
              {{ status.label }}
            </span>
            <span v-if="store.hasScreenplay" class="stats">
              · {{ store.totalScenes }} 场 ·
              {{ store.screenplay?.characters.length ?? 0 }} 角色 ·
              {{ store.screenplay?.locations.length ?? 0 }} 地点
            </span>
          </div>
        </div>
      </div>
      <div class="topbar-right">
        <!-- 阶段 8.2:角色档案 + 关系图入口 -->
        <button
          class="secondary-btn"
          @click="openCharacterPanel"
          :disabled="store.loadingState === 'loading'"
          title="角色档案 + 关系图 + 桥接资产"
        >
          <svg
            width="14" height="14" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
          >
            <circle cx="9" cy="7" r="3" />
            <path d="M14.5 9.5a2.5 2.5 0 1 0 0-5" />
            <path d="M2 21v-2a4 4 0 0 1 4-4h6a4 4 0 0 1 4 4v2" />
            <path d="M17 21v-2a4 4 0 0 0-2.5-3.7" />
          </svg>
          角色
        </button>
        <!-- 阶段 8.4:分集规划 MVP 入口(仅在 hasScreenplay 时) -->
        <button
          v-if="store.hasScreenplay"
          class="secondary-btn"
          @click="openEpisodePanel"
          title="按目标单集时长贪心分集"
        >
          <svg
            width="14" height="14" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
          >
            <rect x="3" y="3" width="7" height="7" rx="1" />
            <rect x="14" y="3" width="7" height="7" rx="1" />
            <rect x="3" y="14" width="7" height="7" rx="1" />
            <rect x="14" y="14" width="7" height="7" rx="1" />
          </svg>
          分集
        </button>
        <!-- 阶段 8.5:多模型对比 入口 -->
        <button
          v-if="store.hasScreenplay"
          class="secondary-btn"
          @click="openComparisonPanel"
          title="多 vendor 并行跑同一场,4 维可解释打分"
        >
          <svg
            width="14" height="14" viewBox="0 0 24 24" fill="none"
            stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
          >
            <path d="M12 2v6" />
            <circle cx="12" cy="14" r="6" />
            <path d="M9 14h6" />
            <path d="M12 11v6" />
          </svg>
          对比
        </button>
        <VersionSwitcher v-if="store.hasScreenplay" @open-diff="openVersionDiff" />
        <ExportMenu v-if="store.hasScreenplay" />
        <button
          class="primary-btn"
          :disabled="
            store.loadingState === 'loading' ||
            store.loadingState === 'composing'
          "
          @click="openComposeDialog"
        >
          {{ store.hasScreenplay ? "重新生成" : "生成剧本" }}
        </button>
      </div>
    </header>

    <!-- ===== 双栏主体 ===== -->
    <div class="panes">
      <!-- 左栏:原文 -->
      <section class="pane pane-left">
        <div class="pane-header">
          <h3>
            原文
            <span class="pane-stat">{{ store.novelChapters.length }} 章</span>
          </h3>
        </div>
        <div class="pane-body">
          <NovelTextPanel />
        </div>
      </section>

      <!-- 分隔线 -->
      <div class="divider"></div>

      <!-- 右栏:剧本 -->
      <section class="pane pane-right">
        <div class="pane-header">
          <h3>
            剧本
            <span v-if="store.hasScreenplay" class="pane-stat">
              {{ store.totalScenes }} 场
            </span>
          </h3>
        </div>
        <div class="pane-body">
          <!-- 2026-06-08 用户精修:桥接增益面板挪进 pane-body,跟"结构报告"
               一样随主内容滚动,不再 sticky 占用顶部空间。 -->
          <div v-if="store.hasScreenplay" class="bridge-banner-wrap">
            <BridgeGainBanner />
          </div>
          <div v-if="!store.hasScreenplay" class="placeholder">
            <div class="placeholder-icon">
              <svg
                width="36"
                height="36"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                stroke-width="1.5"
                stroke-linecap="round"
                stroke-linejoin="round"
              >
                <rect x="3" y="3" width="18" height="18" rx="2" />
                <path d="M9 7h6M9 12h6M9 17h6" />
              </svg>
            </div>
            <div class="placeholder-text">
              剧本待生成<br />
              <span class="placeholder-hint">点击右上「生成剧本」运行 LLM 流水线</span>
            </div>
          </div>
          <ScreenplayPanel v-else />
        </div>
      </section>
    </div>

    <!-- ===== 改编决策浮窗(差异化创新核心)===== -->
    <AdaptationDecisionPanel />

    <!-- ===== 编排对话框 ===== -->
    <ComposeDialog
      :visible="composeDialogVisible"
      @close="closeComposeDialog"
    />

    <!-- ===== AI 优化弹窗(A + B 共用,PR#16) ===== -->
    <OptimizationModal
      :visible="optimizationModalVisible"
      :scope="optimizationScope"
      :target-scene-id="optimizationTargetScene"
      @close="closeOptimizationModal"
    />

    <!-- ===== 阶段 8.2:角色档案 + 关系图 ===== -->
    <CharacterProfilesPanel
      :novel-id="props.id"
      :visible="characterPanelVisible"
      @close="closeCharacterPanel"
    />

    <!-- ===== 阶段 8.3 第 3b:版本树 diff + 回滚 ===== -->
    <VersionDiffPanel
      :visible="versionDiffVisible"
      @close="closeVersionDiff"
    />

    <!-- ===== 阶段 8.4:分集规划 MVP ===== -->
    <EpisodePlanPanel
      :novel-id="props.id"
      :visible="episodePanelVisible"
      @close="closeEpisodePanel"
    />

    <!-- ===== 阶段 8.5:多模型对比 ===== -->
    <ComparisonPanel
      :visible="comparisonPanelVisible"
      @close="closeComparisonPanel"
    />
  </div>
</template>

<style scoped>
/* 2026-06-08 用户精修:用户反馈"右侧主视窗能轻微滑动遮挡上部"。
 * 根因:height: 100vh 包含浏览器整高,但父平台 App.vue 的 main 容器只占
 * vh - topbar/sidebar 部分。100vh 超过父高 → 整个 .editor 在父容器内
 * 产生溢出,顶部被 chrome 遮住一部分。
 * 修复:height: 100% 跟随父容器实际高度,overflow: hidden 拒绝任何外溢。
 */
.editor {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  background: var(--bg);
  color: var(--text);
  overflow: hidden;
}

/* ===== 顶栏 — 更轻、更文气 ===== */
.topbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: var(--space-4) var(--space-6);
  background: var(--card-bg);
  border-bottom: 1px solid var(--border-soft);
  flex-shrink: 0;
}
.topbar-left {
  display: flex;
  align-items: center;
  gap: var(--space-4);
}
.topbar-right {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}
.back-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  border-radius: var(--radius-md);
  border: 1px solid transparent;
  background: transparent;
  color: var(--text-muted);
  transition: all var(--transition-fast);
}
.back-btn:hover {
  background: var(--hover-bg);
  color: var(--text);
}
.title-block {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.novel-title {
  font-family: var(--font-serif);
  font-size: 18px;
  font-weight: 500;
  color: var(--text-strong);
  letter-spacing: 0.01em;
}
.status-line {
  font-size: 11px;
  color: var(--text-muted);
  display: flex;
  gap: var(--space-2);
  align-items: center;
  letter-spacing: 0.04em;
}
.status-pill {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  font-size: 10.5px;
  font-weight: 500;
  letter-spacing: 0.02em;
}
.status-pill--muted {
  background: var(--code-bg);
  color: var(--text-muted);
}
.status-pill--success {
  background: var(--success-soft);
  color: var(--success);
}
.status-pill--danger {
  background: var(--danger-soft);
  color: var(--danger);
}

.primary-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 18px;
  border-radius: var(--radius-md);
  background: var(--accent);
  color: white;
  border: none;
  font-size: 12.5px;
  font-weight: 500;
  letter-spacing: 0.04em;
  transition: all var(--transition-fast);
}
.primary-btn:hover:not(:disabled) {
  background: var(--accent-hover);
}
.primary-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* 阶段 8.2:secondary-btn 用于"角色"等次级操作 */
.secondary-btn {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 7px 14px;
  border-radius: var(--radius-md);
  background: var(--card-bg);
  color: var(--text);
  border: 1px solid var(--border);
  font-size: 12.5px;
  font-weight: 500;
  letter-spacing: 0.04em;
  cursor: pointer;
  transition: all var(--transition-fast);
}
.secondary-btn:hover:not(:disabled) {
  background: var(--hover-bg);
  border-color: var(--accent);
  color: var(--accent);
}
.secondary-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

/* ===== 双栏 ===== */
.panes {
  display: flex;
  flex: 1;
  overflow: hidden;
}
.pane {
  flex: 1;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  min-width: 0;
}
.pane-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: var(--space-4) var(--space-6);
  border-bottom: 1px solid var(--border-soft);
  background: var(--card-bg);
  flex-shrink: 0;
}
.pane-header h3 {
  margin: 0;
  font-family: var(--font-serif);
  font-size: 13px;
  font-weight: 500;
  color: var(--text-muted);
  letter-spacing: 0.24em;
  display: inline-flex;
  align-items: baseline;
  gap: var(--space-3);
}
.pane-stat {
  font-family: var(--font-mono);
  font-size: 10.5px;
  color: var(--text-muted);
  letter-spacing: 0.06em;
  font-weight: 400;
  text-transform: lowercase;
  opacity: 0.7;
}
.pane-body {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-5) var(--space-6);
}

/* 2026-06-08 用户精修:挪到 pane-body 内,容器 padding 不再外延,
   靠 pane-body 自己的 padding;只用 margin-bottom 给下方剧本一点空气 */
.bridge-banner-wrap {
  margin-bottom: var(--space-3);
}

.divider {
  width: 1px;
  background: var(--border-soft);
  flex-shrink: 0;
}

/* 占位符 */
.placeholder {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  min-height: 100%;
  text-align: center;
  color: var(--text-muted);
}
.placeholder-icon {
  margin-bottom: var(--space-3);
  opacity: 0.3;
}
.placeholder-text {
  font-family: var(--font-serif);
  font-size: 15px;
  line-height: 1.8;
  color: var(--text-secondary);
}
.placeholder-hint {
  font-size: 11.5px;
  color: var(--text-muted);
  margin-top: var(--space-2);
  letter-spacing: 0.04em;
}
</style>
