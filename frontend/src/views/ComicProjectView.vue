<script setup lang="ts">
/**
 * ComicProjectView — D.9 Sprint 2.B 漫画态项目页
 *
 * 独立 view(不复用 ProjectView,隔离铁律,详 ADR §5)。按 comic.state 显示不同
 * 推进 UI:
 *   - queued / failed:显"开始 → 上传参考图"按钮 → 弹 RefImageUploadDialog
 *   - scripting / extracting_visuals / style_analyzing:loading 进度条
 *   - style_voting:显 3 张候选样张 → 弹 StyleVotingDialog(Sprint 2.B+ 七修:5→3)
 *   - character_anchoring:loading
 *   - designing / generating / composing:Sprint 3 实现,Sprint 2.B 显"等 Sprint 3 接通"
 *   - done:Sprint 3 实现漫画阅读器
 *   - cancelled:显"已取消"
 *
 * 路由:/comics/:id
 *
 * a11y:aria-busy / aria-live 状态变化让屏幕阅读器知道(对齐 D.5 收口)
 */
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import RefImageUploadDialog from "../components/RefImageUploadDialog.vue";
import StyleVotingDialog from "../components/StyleVotingDialog.vue";
import { COMIC_STATE_LABEL, type ComicPageRecord } from "../api/types";
import { useComic } from "../composables/useComic";
import { confirm as confirmDialog } from "../composables/useConfirm";
import { toast } from "../composables/useToast";

// Sprint 4.A(2026-05-13):ComicPagePanel / ComicPageRecord 已上提到 api/types.ts,
// 供 ComicReaderView 复用;此处仅 import 类型。

const route = useRoute();
const router = useRouter();

const comicId = computed<string | null>(() => {
  const v = route.params.id;
  return typeof v === "string" ? v : null;
});

const {
  comic, loading, error,
  isRunning, isTerminalState,
  uploadReferences, retryStyleCandidates, voteStyle,
  cancel, remove, fetchComicPages, exportComic,
  startPolling, stopPolling, dispose,
} = useComic(() => comicId.value);

onBeforeUnmount(dispose);

// Dialog 状态
const uploadDialogOpen = ref(false);
const votingDialogOpen = ref(false);
// LLM 调用期间的 dialog loading
const dialogLoading = ref(false);

function handleOpenUpload() {
  uploadDialogOpen.value = true;
}

async function handleUploadSubmit(imageUrls: string[]) {
  dialogLoading.value = true;
  // Sprint 5.4.1:imageUrls=[] 表示跳过模式(AI 从文本推画风)
  const isSkip = imageUrls.length === 0;
  // Sprint 5.x bug fix(2026-05-14):dialog 期间 router 内 sync agent 跑 30-60s,
  // 显式启动 polling 拉 comic.state + progress 变化,让 dialog loadingMessage 显示子步骤
  // (原 polling 依赖 is_alive,但 sync agent 不在 _RUNNING_COMICS → polling 永不启动)
  startPolling();
  try {
    await uploadReferences(imageUrls);
    uploadDialogOpen.value = false;
    toast.success(
      isSkip
        ? "AI 已从剧本题材推断画风,请选 1 张你喜欢的样张。"
        : "画风分析完成!请选 1 张你喜欢的画风。",
    );
  } catch {
    toast.error(isSkip ? "AI 推断画风失败,请重试或上传参考图" : "画风分析失败,请重试");
  } finally {
    dialogLoading.value = false;
    // 关闭强制 polling — router 返回后 load() 已经拉到最新 state
    // 若 state 仍 in_progress(generating),load() 内部 schedulePoll 自动接管
    stopPolling();
  }
}

function handleOpenVoting() {
  votingDialogOpen.value = true;
}

async function handleVoteSubmit(selectedIndex: number) {
  dialogLoading.value = true;
  // Sprint 5.x bug fix(2026-05-14):vote 后 router 内同步跑 character_anchor(60-90s),
  // 期间 dialog 需要 polling 拉 progress 推进(80→84 每角色 +N%)
  startPolling();
  try {
    await voteStyle(selectedIndex);
    votingDialogOpen.value = false;
    toast.success("画风已锁定!正在锚定角色形象...");
  } catch {
    toast.error("投票失败,请重试");
  } finally {
    dialogLoading.value = false;
    // 关闭强制 polling — router 返回后 load() 已拉到 designing/generating,
    // 自动 schedulePoll 接管(state in_progress 永远继续 polling)
    stopPolling();
  }
}

/**
 * Sprint 5.10+(2026-05-14):3 张候选全部失败时重出 — 只重跑 Stage 3,
 * 复用已有 style_detailed_prompt / style_candidates_json variants(不消耗 vendor 调用)。
 *
 * 后端 endpoint:POST /comics/{id}/retry_style_candidates
 *   - 前置:state == style_voting(已跑完前两 Stage)
 *   - 4xx:title 缺失 / 不是 style_voting / 数据不完整
 *   - 5xx:vendor 全失败 → 仍保留旧 candidates
 *
 * UI 流:
 *   1. dialog 内点"重新出 3 张"→ emit retry
 *   2. dialogLoading=true(disable 全部按钮)+ startPolling(显 Stage 3 子步骤)
 *   3. 成功:toast.success + comic.value 刷成最新(candidates 全替换)
 *   4. 失败:toast.error(消息从 ApiError.detail.message 拿)+ dialog 仍开
 */
async function handleRetryStyleCandidates() {
  dialogLoading.value = true;
  startPolling();
  try {
    await retryStyleCandidates();
    toast.success("重新出图完成,请选 1 张你喜欢的画风。");
  } catch (e) {
    const msg = e instanceof Error ? e.message : "重试失败,请稍后再试";
    toast.error(msg);
  } finally {
    dialogLoading.value = false;
    stopPolling();
  }
}

/**
 * 取消漫画 — Sprint 2.B+ 四修按 progress 显示退款档位提示
 *
 * 退款规则(对齐后端 quota_service.compute_cancel_refund_units):
 *   progress < 10:全退(扣 0 本)
 *   progress < 80:半退(扣 0.5 本)
 *   progress >= 80:不退(扣 1 本)
 */
async function handleCancel() {
  if (!comic.value) return;
  const progress = comic.value.progress_percent;

  let predictedRefund: string;
  if (progress < 10) {
    predictedRefund = "进度 < 10%,预计配额全额退回(不扣本月)";
  } else if (progress < 80) {
    predictedRefund = `进度 ${progress}%,预计半额退回(扣半本)`;
  } else {
    predictedRefund = `进度 ${progress}%,已超 80%,不予退还(扣全本)`;
  }

  const ok = await confirmDialog({
    title: "取消这次漫画创作?",
    message: `${predictedRefund}\n\n已生成的内容会保留,但 state 落 cancelled,不能继续推进。`,
    danger: true,
    confirmLabel: "确认取消",
  });
  if (!ok) return;

  try {
    const refund = await cancel();
    if (refund.phase === "full") {
      toast.success(`已取消 · ${refund.label}`, 4500);
    } else if (refund.phase === "half") {
      toast.info(`已取消 · ${refund.label}`, 4500);
    } else if (refund.phase === "none") {
      toast.warning(`已取消 · ${refund.label}`, 4500);
    } else {
      toast.info("漫画已是终态,无需操作");
    }
  } catch {
    toast.error("取消失败,请重试");
  }
}

async function handleDelete() {
  if (!comic.value) return;
  const ok = await confirmDialog({
    title: `删除漫画「${comic.value.name}」?`,
    message: "所有相关数据(参考图、角色立绘卡、剧本)都会被删除,无法恢复。",
    danger: true,
    confirmLabel: "删除",
  });
  if (!ok) return;
  await remove();
  toast.success("漫画已删除");
  router.push("/my-comics");
}

function backToList() {
  router.push("/my-comics");
}

const stateLabel = computed<string>(() => {
  if (!comic.value) return "";
  return COMIC_STATE_LABEL[comic.value.state] || comic.value.state;
});

/**
 * Sprint 5.x bug fix(2026-05-14):按 comic.state + progress_percent 算出"AI 当前正在做什么"
 * 子步骤文案,传给 RefImageUploadDialog / StyleVotingDialog 显示。
 *
 * 后端 progress 区间(_update_progress 累积):
 *   - scripting        10-24:编剧分批承接
 *   - extracting_visuals 25-44:素材库抽取
 *   - style_analyzing  50-68:画风定调员 v3
 *       - Stage 1 Qwen-VL × 3       50-55
 *       - Stage 2 DeepSeek 综合     55-60
 *       - Stage 3 Seedream × 3      60-68
 *   - character_anchoring 80-84:角色锚定员
 *   - generating       85-95:导演 + 主循环出图
 *   - composing        95-100:PIL 排版
 */
const subStepMessage = computed<string>(() => {
  const c = comic.value;
  if (!c) return "";
  const p = c.progress_percent || 0;
  const s = c.state;
  if (s === "scripting") {
    return `编剧 agent 正在把原文转成漫画分镜剧本(${p}%)...`;
  }
  if (s === "extracting_visuals") {
    return `素材库抽取员正在扫原文 → character_visuals / scenes / props(${p}%)...`;
  }
  if (s === "style_analyzing") {
    if (p < 55) return `画风定调 Stage 1:Qwen-VL 正在提取 3 张参考图的视觉 DNA(${p}%)...`;
    if (p < 60) return `画风定调 Stage 2:DeepSeek 综合视觉 DNA + 题材 → 详细 prompt(${p}%)...`;
    return `画风定调 Stage 3:Seedream 出 3 张多样化候选样张(${p}%)...`;
  }
  if (s === "character_anchoring") {
    return `角色锚定员正在为每个出场角色生成「身份证级」描述符 + 立绘卡(${p}%)...`;
  }
  if (s === "designing" || s === "generating") {
    return `导演正给每格组装详细 prompt + 生图模型 2 并发出图(${p}%)...`;
  }
  if (s === "composing") {
    return `排版员 PIL 本地合成整页 PNG(${p}%)...`;
  }
  return "请稍候...";
});

// Sprint 3:done 后拉漫画页面网格
const pages = ref<ComicPageRecord[]>([]);
const loadingPages = ref<boolean>(false);

async function loadPages() {
  if (!comicId.value) return;
  loadingPages.value = true;
  try {
    pages.value = await fetchComicPages();
  } catch {
    pages.value = [];
  } finally {
    loadingPages.value = false;
  }
}

/** 进入阅读模式 — Sprint 4.A 全屏沉浸式阅读器 */
function enterReader() {
  if (!comicId.value) return;
  router.push(`/comics/${comicId.value}/read`);
}

/** Sprint 4.D 导出 — 触发浏览器下载 + toast 缺页提示 */
const exporting = ref<"pdf" | "zip" | null>(null);

async function handleExport(format: "pdf" | "zip") {
  if (exporting.value) return;   // 防重入
  exporting.value = format;
  try {
    const { blob, filename, missingPages } = await exportComic(format);
    // 触发下载:CreateObjectURL → <a download> → click → revoke
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    if (missingPages.length > 0) {
      toast.warning(
        `${format.toUpperCase()} 已导出,但缺 ${missingPages.length} 页(排版失败:第 ${missingPages.join("、")} 页)`,
      );
    } else {
      toast.success(`${format.toUpperCase()} 已导出`);
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : "导出失败";
    toast.error(msg);
  } finally {
    exporting.value = null;
  }
}

// 当 comic.state 变为 done(从其他 state 切过来)→ 拉漫画页
watch(
  () => comic.value?.state,
  (newState, oldState) => {
    if (newState === "done" && oldState !== "done") {
      void loadPages();
    }
  },
  { immediate: true },
);
</script>

<template>
  <div class="comic-page">
    <!-- 顶栏 -->
    <header class="topbar">
      <button class="back-btn" @click="backToList">
        <span class="back-arrow">←</span>
        <span>我的漫画</span>
      </button>
      <div v-if="comic" class="comic-meta">
        <span class="comic-name">{{ comic.name }}</span>
        <span class="state-chip" :class="`state-${comic.state}`">{{ stateLabel }}</span>
      </div>
      <div class="topbar-actions">
        <button
          v-if="comic && !isTerminalState && !isRunning"
          class="ghost-btn"
          @click="handleCancel"
        >取消</button>
        <button
          v-if="comic && (isTerminalState || comic.state === 'queued' || comic.state === 'failed')"
          class="ghost-btn ghost-btn--danger"
          @click="handleDelete"
        >删除</button>
      </div>
    </header>

    <!-- 主区 -->
    <main class="main" :aria-busy="isRunning" aria-live="polite">
      <!-- Loading 态(初始拉) -->
      <div v-if="loading && !comic" class="state-overlay">
        <p class="state-msg">加载中…</p>
      </div>

      <!-- Error 态 -->
      <div v-else-if="error && !comic" class="state-overlay">
        <p class="state-msg state-error">{{ error }}</p>
        <button class="ghost-btn" @click="backToList">返回列表</button>
      </div>

      <!-- comic 数据就位 -->
      <div v-else-if="comic" class="comic-body">
        <!-- 进度条 -->
        <section class="progress-section">
          <div class="progress-bar">
            <div
              class="progress-fill"
              :style="{ width: comic.progress_percent + '%' }"
              :class="{ 'is-failed': comic.state === 'failed' }"
            ></div>
          </div>
          <p class="progress-text">
            {{ comic.progress_percent }}% · {{ stateLabel }}
          </p>
        </section>

        <!-- 失败态 -->
        <section v-if="comic.state === 'failed'" class="action-section action-section--error">
          <h2>创作失败</h2>
          <p class="error-detail">{{ comic.error_message || "创作过程中断,请稍后重试" }}</p>
          <button class="primary-btn" @click="handleOpenUpload">重新上传参考图</button>
        </section>

        <!-- queued / style_uploading:CTA 上传参考图 -->
        <section
          v-else-if="comic.state === 'queued' || comic.state === 'style_uploading'"
          class="action-section"
        >
          <h2>下一步:上传 3 张同画风参考图</h2>
          <p class="action-hint">
            选 3 张你喜欢的画风作品(网漫/同人/喜欢的画师作品)。AI 会综合它们的视觉 DNA
            锚定你的整本漫画画风。
          </p>
          <button class="primary-btn primary-btn--lg" @click="handleOpenUpload">
            开始 →
          </button>
        </section>

        <!-- 正在跑(scripting / extracting_visuals / style_analyzing / character_anchoring / designing / generating / composing) -->
        <section v-else-if="isRunning" class="action-section action-section--loading">
          <div class="loading-spinner" aria-hidden="true"></div>
          <h2>{{ stateLabel }}</h2>
          <p class="action-hint">
            <template v-if="comic.state === 'style_analyzing'">
              Qwen-VL 正在提取 3 张参考图的视觉 DNA,DeepSeek 综合后由 Seedream 出 3 张定调样张(预计 40-60 秒)...
            </template>
            <template v-else-if="comic.state === 'character_anchoring'">
              正在为前 6 个主角出"身份证级"立绘卡(预计 30-60 秒)...
            </template>
            <template v-else-if="comic.state === 'scripting'">
              编剧 agent 正在把原文转成漫画分镜剧本...
            </template>
            <template v-else-if="comic.state === 'extracting_visuals'">
              素材库抽取员正在扫原文 → character_visuals / scenes / props...
            </template>
            <template v-else-if="comic.state === 'designing' || comic.state === 'generating'">
              导演正在给每格组装详细 prompt,生图模型按同 seed 出图
              <template v-if="comic.target_pages">
                ({{ comic.target_pages }} 页 × 6 格 = {{ comic.target_pages * 6 }} 格,
                预计 {{ Math.ceil(comic.target_pages * 6 * 5 / 2) }} 秒,2 并发)...
              </template>
              <template v-else>...</template>
            </template>
            <template v-else-if="comic.state === 'composing'">
              排版员正在用 PIL 本地合成整页 PNG(对话气泡 + 旁白 + SFX)...
            </template>
            <template v-else>
              请稍候...
            </template>
          </p>
        </section>

        <!-- style_voting:3 选 1(Sprint 2.B+ 七修) -->
        <section v-else-if="comic.state === 'style_voting'" class="action-section">
          <h2>下一步:从 3 张候选画风中选 1 张</h2>
          <p class="action-hint">
            画风:<strong>{{ comic.style_tag || "(无 tag)" }}</strong>。
            选定后整本漫画都用这个画风生成。
          </p>
          <button class="primary-btn primary-btn--lg" @click="handleOpenVoting">
            打开候选画风 →
          </button>
        </section>

        <!-- designing(Sprint 3 接通后才有真实推进)-->
        <section v-else-if="comic.state === 'designing'" class="action-section">
          <h2>角色锚定完成 ✓</h2>
          <p class="action-hint">
            画风、剧本、角色立绘卡都已就位。**下一步:Agent #6 导演 + 图像生成**留待
            Sprint 3 接通。当前漫画整本生成功能未启用。
          </p>
          <div class="anchored-preview">
            <div class="preview-row">
              <span class="preview-label">画风 tag:</span>
              <span>{{ comic.style_tag || "—" }}</span>
            </div>
            <div v-if="comic.style_anchor_image_url" class="preview-row">
              <span class="preview-label">画风锚:</span>
              <a :href="comic.style_anchor_image_url" target="_blank" rel="noopener">查看</a>
            </div>
            <div class="preview-row">
              <span class="preview-label">详细 prompt:</span>
              <span class="prompt-preview">
                {{ comic.style_detailed_prompt
                    ? comic.style_detailed_prompt.slice(0, 100) + "..."
                    : "—" }}
              </span>
            </div>
          </div>
        </section>

        <!-- done — Sprint 3 接通后显漫画页面网格 + Sprint 4.A 阅读器入口 -->
        <section v-else-if="comic.state === 'done'" class="action-section action-section--done">
          <header class="done-header">
            <div class="done-header-text">
              <h2>漫画完成 ✓</h2>
              <p class="action-hint">
                共 {{ pages.length }} 页 ·
                {{ pages.reduce((sum, p) => sum + p.panels.filter(pa => pa.image_url).length, 0) }}
                格图
              </p>
            </div>
            <div v-if="pages.length > 0" class="done-actions">
              <button
                class="primary-btn primary-btn--lg reader-cta"
                @click="enterReader"
                aria-label="进入全屏阅读模式"
              >
                <span class="reader-icon" aria-hidden="true">📖</span>
                <span>阅读全本</span>
              </button>
              <button
                class="ghost-btn export-btn"
                :disabled="exporting !== null"
                @click="handleExport('pdf')"
                aria-label="导出 PDF"
                title="导出 PDF(整本一个文件,适合打印 / 分享)"
              >
                <span aria-hidden="true">📄</span>
                <span>{{ exporting === 'pdf' ? '正在导出...' : '导出 PDF' }}</span>
              </button>
              <button
                class="ghost-btn export-btn"
                :disabled="exporting !== null"
                @click="handleExport('zip')"
                aria-label="下载 PNG zip"
                title="下载 PNG ZIP(每页一张图,适合二次编辑)"
              >
                <span aria-hidden="true">🗂</span>
                <span>{{ exporting === 'zip' ? '正在打包...' : '下载 ZIP' }}</span>
              </button>
            </div>
          </header>

          <div v-if="loadingPages" class="state-msg">加载漫画页面中…</div>
          <div v-else-if="pages.length === 0" class="state-msg">暂无生成页面</div>
          <div v-else class="comic-grid">
            <div v-for="page in pages" :key="page.id" class="comic-page-block">
              <h3 class="comic-page-title">第 {{ page.page_index }} 页</h3>
              <div class="comic-panels">
                <div
                  v-for="panel in page.panels"
                  :key="`${page.id}-${panel.panel_index}`"
                  class="comic-panel"
                  :class="{ 'is-failed': !panel.image_url }"
                >
                  <img
                    v-if="panel.image_url"
                    :src="panel.image_url"
                    :alt="`第 ${page.page_index} 页第 ${panel.panel_index} 格`"
                    loading="lazy"
                  />
                  <div v-else class="comic-panel-failed">
                    格 {{ panel.panel_index }} 生成失败
                  </div>
                  <!-- 对白气泡(Sprint 3 简版,Sprint 4 PIL 排版精修) -->
                  <div
                    v-if="panel.dialogues && panel.dialogues.length > 0"
                    class="dialogue-overlay"
                  >
                    <div
                      v-for="(d, di) in panel.dialogues"
                      :key="di"
                      class="dialogue-bubble"
                    >
                      <strong>{{ d.speaker }}:</strong>{{ d.text }}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section v-else-if="comic.state === 'cancelled'" class="action-section">
          <h2>已取消</h2>
          <p class="action-hint">这次漫画已被取消。</p>
        </section>
      </div>
    </main>

    <!-- Dialogs -->
    <RefImageUploadDialog
      :open="uploadDialogOpen"
      :loading="dialogLoading"
      :comic-id="comicId"
      :loading-message="subStepMessage"
      @close="uploadDialogOpen = false"
      @submit="handleUploadSubmit"
    />
    <StyleVotingDialog
      :open="votingDialogOpen"
      :loading="dialogLoading"
      :candidates="comic?.style_candidates || []"
      :style-tag="comic?.style_tag || null"
      :loading-message="subStepMessage"
      @close="votingDialogOpen = false"
      @vote="handleVoteSubmit"
      @retry="handleRetryStyleCandidates"
    />
  </div>
</template>

<style scoped>
.comic-page {
  display: flex;
  flex-direction: column;
  height: 100vh;
  background: var(--color-bg);
}

.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-3) var(--space-5);
  background: var(--color-surface);
  border-bottom: 1px solid var(--color-border);
  flex-shrink: 0;
}

.back-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: transparent;
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) var(--ease-out);
}

.back-btn:hover {
  background: var(--color-surface-hover);
}

.back-arrow {
  font-size: var(--text-md);
}

.comic-meta {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.comic-name {
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text);
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

.topbar-actions {
  display: flex;
  gap: var(--space-2);
}

.main {
  flex: 1;
  overflow-y: auto;
  padding: var(--space-6) var(--space-5);
}

.state-overlay {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: var(--space-3);
  padding: var(--space-8);
  color: var(--color-text-muted);
}

.state-msg {
  font-size: var(--text-base);
}

.state-error {
  color: var(--color-danger);
}

.comic-body {
  max-width: 880px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-5);
}

.progress-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.progress-bar {
  height: 6px;
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
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin: 0;
}

.action-section {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-6);
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--space-3);
}

.action-section h2 {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}

.action-hint {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: 1.6;
  max-width: 540px;
  margin: 0;
}

.action-section--error {
  border-color: var(--color-danger-soft);
  background: var(--color-danger-soft);
}

.error-detail {
  font-size: var(--text-sm);
  color: var(--color-danger);
  max-width: 540px;
  margin: 0;
}

.action-section--loading {
  padding: var(--space-8);
}

/* ===== Sprint 3:done 漫画网格 ===== */
.action-section--done {
  align-items: stretch;
  max-width: none;
  width: 100%;
}
.done-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-4);
  margin-bottom: var(--space-4);
  flex-wrap: wrap;
}
.done-header-text {
  flex: 1 1 auto;
  min-width: 0;
}
.done-header-text h2 {
  margin: 0 0 var(--space-1) 0;
}
.done-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
  flex-wrap: wrap;
}
.reader-cta {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
}
.reader-icon {
  font-size: 1.2em;
  line-height: 1;
}
.export-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: 0.9em;
}
.export-btn:disabled {
  opacity: 0.55;
  cursor: progress;
}
.state-msg {
  text-align: center;
  padding: var(--space-6);
  color: var(--color-text-muted);
}
.comic-grid {
  display: flex;
  flex-direction: column;
  gap: var(--space-6);
  width: 100%;
}
.comic-page-block {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-4);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
}
.comic-page-title {
  margin: 0;
  font-size: var(--text-md);
  font-weight: 600;
  color: var(--color-text-muted);
}
.comic-panels {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: var(--space-2);
}
@media (max-width: 720px) {
  .comic-panels { grid-template-columns: repeat(2, 1fr); }
}
@media (max-width: 480px) {
  .comic-panels { grid-template-columns: 1fr; }
}
.comic-panel {
  position: relative;
  aspect-ratio: 3 / 4;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.comic-panel img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.comic-panel.is-failed {
  border: 1px dashed var(--color-danger-soft);
}
.comic-panel-failed {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-xs);
  color: var(--color-danger);
}
/* 对白气泡简版(Sprint 4 PIL 排版后此叠层可关)*/
.dialogue-overlay {
  position: absolute;
  left: var(--space-2);
  right: var(--space-2);
  bottom: var(--space-2);
  display: flex;
  flex-direction: column;
  gap: 4px;
  pointer-events: none;
}
.dialogue-bubble {
  padding: 4px 8px;
  font-size: 11px;
  line-height: 1.45;
  color: var(--color-text);
  background: rgba(255, 255, 255, 0.92);
  border-radius: var(--radius-sm);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.12);
  backdrop-filter: blur(2px);
}

.loading-spinner {
  width: 40px;
  height: 40px;
  border: 3px solid var(--color-border);
  border-top-color: var(--color-accent);
  border-radius: var(--radius-full);
  animation: spin var(--duration-spin) linear infinite;
}

@media (prefers-reduced-motion: reduce) {
  .loading-spinner { animation: none; }
}

.primary-btn {
  padding: var(--space-2) var(--space-5);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) var(--ease-out);
}

.primary-btn:hover {
  background: var(--color-accent-hover);
}

.primary-btn--lg {
  font-size: var(--text-md);
  padding: var(--space-3) var(--space-6);
}

.ghost-btn {
  padding: var(--space-1) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.ghost-btn:hover {
  background: var(--color-surface-hover);
}

.ghost-btn--danger {
  color: var(--color-danger);
  border-color: var(--color-danger-soft);
}

.ghost-btn--danger:hover {
  background: var(--color-danger-soft);
}

.anchored-preview {
  width: 100%;
  max-width: 600px;
  margin-top: var(--space-3);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  text-align: left;
}

.preview-row {
  display: flex;
  gap: var(--space-2);
  font-size: var(--text-sm);
}

.preview-label {
  flex-shrink: 0;
  color: var(--color-text-muted);
  font-weight: 500;
  min-width: 80px;
}

.prompt-preview {
  color: var(--color-text);
  word-break: break-all;
}
</style>
