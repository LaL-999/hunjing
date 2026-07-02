<script setup lang="ts">
/**
 * PublishWorkModal — 把一部已完成的作品上架到作品广场。2026-06-25。
 *
 * 流程:选一部已完成推演 → 取名 → (可选)写简介 → 选封面(上传图 / 9 款渐变)→ 上架。
 * 正文由后端从源 simulation.narrative 快照(前端不传正文,杜绝伪造)。
 *
 * 黄金标准参照:CreateComicModal.vue(backdrop + blur + Esc + Teleport body + 列表多选)。
 * 这里是单选(一次上架一部)。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { apiAssetUrl, ApiError } from "../api/client";
import {
  plazaApi,
  type PublishableSim,
  type PublishableScreenplay,
  type PublishableComic,
  type ScreenplayPublishKind,
} from "../api/plaza";
import { toast } from "../composables/useToast";

const props = defineProps<{ open: boolean }>();
const emit = defineEmits<{
  (e: "close"): void;
  (e: "published"): void;
}>();

const MODE_LABELS: Record<string, string> = {
  initial: "初始态",
  middle: "中间态",
  end: "末尾态",
  cycle: "漫创态",
  screenplay: "剧创态",
};
const GRADIENTS = [1, 2, 3, 4, 5, 6, 7, 8, 9];

// 发布来源 —— 推演(simulation)/ 剧本(screenplay)/ 漫画(comic)
type SourceType = "sim" | "screenplay" | "comic";
const sourceType = ref<SourceType>("sim");

const sims = ref<PublishableSim[]>([]);
const loadingSims = ref(false);
const loadError = ref<string | null>(null);

// 剧创态可发布素材
const screenplays = ref<PublishableScreenplay[]>([]);
const selectedNovelId = ref<string | null>(null);
const screenplayKind = ref<ScreenplayPublishKind>("global");
const selectedPlanId = ref<string | null>(null);

// 漫画可发布素材
const comics = ref<PublishableComic[]>([]);
const selectedComicId = ref<string | null>(null);

// 作品权限(v5)
const isPublic = ref(true);
const allowDownload = ref(true);

const selectedNovel = computed(() =>
  screenplays.value.find((s) => s.novel_id === selectedNovelId.value) ?? null,
);

const selectedSimId = ref<string | null>(null);
const title = ref("");
const summary = ref("");
const coverGradient = ref(1);
/** 上传封面后的内部 URL(优先于渐变) */
const coverImagePath = ref<string | null>(null);
const uploadingCover = ref(false);

const submitting = ref(false);
const submitError = ref<string | null>(null);

const fileInput = ref<HTMLInputElement | null>(null);

/** 剧本发布:episodes/both 必须选中一个分集方案 */
const screenplayReady = computed(() => {
  if (!selectedNovelId.value) return false;
  if (screenplayKind.value !== "global" && !selectedPlanId.value) return false;
  return true;
});

const hasSelection = computed(() => {
  if (sourceType.value === "sim") return !!selectedSimId.value;
  if (sourceType.value === "comic") return !!selectedComicId.value;
  return screenplayReady.value;
});

const canSubmit = computed(
  () =>
    hasSelection.value
    && title.value.trim().length > 0
    && title.value.trim().length <= 60
    && !submitting.value
    && !uploadingCover.value,
);

async function loadPublishable(): Promise<void> {
  loadingSims.value = true;
  loadError.value = null;
  try {
    const resp = await plazaApi.publishable();
    sims.value = resp.items;
  } catch (e) {
    loadError.value = e instanceof ApiError ? e.message : "加载可上架作品失败";
  } finally {
    loadingSims.value = false;
  }
}

async function loadPublishableScreenplays(): Promise<void> {
  loadingSims.value = true;
  loadError.value = null;
  try {
    const resp = await plazaApi.publishableScreenplays();
    screenplays.value = resp.items;
  } catch (e) {
    loadError.value = e instanceof ApiError ? e.message : "加载可上架剧本失败";
  } finally {
    loadingSims.value = false;
  }
}

async function loadPublishableComics(): Promise<void> {
  loadingSims.value = true;
  loadError.value = null;
  try {
    const resp = await plazaApi.publishableComics();
    comics.value = resp.items;
  } catch (e) {
    loadError.value = e instanceof ApiError ? e.message : "加载可上架漫画失败";
  } finally {
    loadingSims.value = false;
  }
}

function resetForm(): void {
  selectedSimId.value = null;
  selectedNovelId.value = null;
  selectedPlanId.value = null;
  selectedComicId.value = null;
  screenplayKind.value = "global";
  title.value = "";
  summary.value = "";
  coverGradient.value = 1;
  coverImagePath.value = null;
  isPublic.value = true;
  allowDownload.value = true;
  submitError.value = null;
}

function reloadCurrent(): void {
  if (sourceType.value === "sim") void loadPublishable();
  else if (sourceType.value === "screenplay") void loadPublishableScreenplays();
  else void loadPublishableComics();
}

function switchSource(t: SourceType): void {
  if (sourceType.value === t) return;
  sourceType.value = t;
  resetForm();
  reloadCurrent();
}

watch(
  () => props.open,
  (isOpen) => {
    if (isOpen) {
      sourceType.value = "sim";
      resetForm();
      void loadPublishable();
    }
  },
);

function pickSim(sim: PublishableSim): void {
  selectedSimId.value = sim.sim_id;
  // 自动用项目名 / 分歧点做标题草稿(用户可改)
  if (!title.value.trim()) {
    title.value = sim.original_title || sim.project_name || "我的作品";
  }
}

function pickNovel(sp: PublishableScreenplay): void {
  selectedNovelId.value = sp.novel_id;
  selectedPlanId.value = sp.episode_plans[0]?.plan_id ?? null;
  // 若没有全局剧本,默认切到分集
  screenplayKind.value = sp.has_global ? "global" : "episodes";
  if (!title.value.trim()) title.value = sp.novel_title || "我的剧本";
}

function pickComic(c: PublishableComic): void {
  selectedComicId.value = c.comic_id;
  if (!title.value.trim()) title.value = c.name || "我的漫画";
}

function triggerUpload(): void {
  fileInput.value?.click();
}

async function onFilePicked(ev: Event): Promise<void> {
  const input = ev.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";   // 允许重选同一文件
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) {
    toast.error("封面图最大 5 MB");
    return;
  }
  uploadingCover.value = true;
  try {
    const res = await plazaApi.uploadCover(file);
    coverImagePath.value = res.cover_image_path;
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "封面上传失败");
  } finally {
    uploadingCover.value = false;
  }
}

function clearCover(): void {
  coverImagePath.value = null;
}

async function handleSubmit(): Promise<void> {
  if (!canSubmit.value) return;
  submitting.value = true;
  submitError.value = null;
  const vis = {
    is_public: isPublic.value ? 1 : 0,
    allow_download: allowDownload.value ? 1 : 0,
  };
  try {
    if (sourceType.value === "sim") {
      if (!selectedSimId.value) return;
      await plazaApi.publish({
        sim_id: selectedSimId.value,
        title: title.value.trim(),
        summary: summary.value.trim() || null,
        cover_image_path: coverImagePath.value,
        cover_gradient: coverGradient.value,
        ...vis,
      });
    } else if (sourceType.value === "comic") {
      if (!selectedComicId.value) return;
      await plazaApi.publishComic({
        comic_id: selectedComicId.value,
        title: title.value.trim(),
        summary: summary.value.trim() || null,
        cover_image_path: coverImagePath.value,
        cover_gradient: coverGradient.value,
        ...vis,
      });
    } else {
      if (!selectedNovelId.value) return;
      await plazaApi.publishScreenplay({
        novel_id: selectedNovelId.value,
        kind: screenplayKind.value,
        plan_id: screenplayKind.value === "global" ? null : selectedPlanId.value,
        title: title.value.trim(),
        summary: summary.value.trim() || null,
        cover_image_path: coverImagePath.value,
        cover_gradient: coverGradient.value,
        ...vis,
      });
    }
    emit("published");
  } catch (e) {
    submitError.value =
      e instanceof ApiError ? e.message : "上架失败,请重试";
  } finally {
    submitting.value = false;
  }
}

function handleBackdrop(e: MouseEvent): void {
  if (submitting.value) return;
  if (e.target === e.currentTarget) emit("close");
}
function onGlobalKey(e: KeyboardEvent): void {
  if (!props.open) return;
  if (e.key === "Escape" && !submitting.value) {
    e.preventDefault();
    emit("close");
  }
}
onMounted(() => document.addEventListener("keydown", onGlobalKey));
onBeforeUnmount(() => document.removeEventListener("keydown", onGlobalKey));

function modeLabel(mode: string): string {
  return MODE_LABELS[mode] ?? "创作";
}
function fmtDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString("zh-CN", {
      year: "numeric", month: "2-digit", day: "2-digit",
    });
  } catch { return iso; }
}
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        aria-labelledby="publish-title"
        @click="handleBackdrop"
      >
        <div class="modal-card surface" role="document">
          <button
            class="close-btn"
            type="button"
            aria-label="关闭"
            :disabled="submitting"
            @click="emit('close')"
          >×</button>

          <header class="modal-header">
            <h2 id="publish-title" class="modal-title">上架我的作品</h2>
            <p class="modal-subtitle">选一部已完成的作品,分享到广场免费给大家阅读</p>
          </header>

          <!-- 发布来源 tab -->
          <div class="source-tabs" role="tablist">
            <button
              type="button" class="source-tab"
              :class="{ 'is-active': sourceType === 'sim' }"
              role="tab" :aria-selected="sourceType === 'sim'"
              @click="switchSource('sim')"
            >推演作品</button>
            <button
              type="button" class="source-tab"
              :class="{ 'is-active': sourceType === 'screenplay' }"
              role="tab" :aria-selected="sourceType === 'screenplay'"
              @click="switchSource('screenplay')"
            >剧本</button>
            <button
              type="button" class="source-tab"
              :class="{ 'is-active': sourceType === 'comic' }"
              role="tab" :aria-selected="sourceType === 'comic'"
              @click="switchSource('comic')"
            >漫画</button>
          </div>

          <!-- 选作品 -->
          <div class="field">
            <label class="field-label">选择作品 <span class="req">*</span></label>

            <div v-if="loadingSims" class="state-msg">加载可上架作品…</div>
            <div v-else-if="loadError" class="state-msg state-error">
              {{ loadError }}
              <button class="retry-btn" type="button" @click="reloadCurrent">重试</button>
            </div>

            <!-- 推演作品列表 -->
            <template v-else-if="sourceType === 'sim'">
              <div v-if="sims.length === 0" class="state-msg">
                还没有可上架的作品 — 请先在项目里完成一次 AI 推演。
              </div>
              <ul v-else class="sim-list" role="listbox">
                <li
                  v-for="sim in sims"
                  :key="sim.sim_id"
                  class="sim-row"
                  :class="{ 'is-selected': selectedSimId === sim.sim_id }"
                  role="option"
                  :aria-selected="selectedSimId === sim.sim_id"
                  tabindex="0"
                  @click="pickSim(sim)"
                  @keydown.enter.prevent="pickSim(sim)"
                >
                  <span class="sim-radio" aria-hidden="true">
                    <span v-if="selectedSimId === sim.sim_id" class="dot" />
                  </span>
                  <div class="sim-content">
                    <div class="sim-head">
                      <span class="sim-mode">{{ modeLabel(sim.mode) }}</span>
                      <span class="sim-project">
                        {{ sim.original_title ? `原著《${sim.original_title}》` : (sim.project_name || "原创世界") }}
                      </span>
                    </div>
                    <p v-if="sim.summary" class="sim-summary">{{ sim.summary }}</p>
                    <span class="sim-time mono">{{ fmtDate(sim.created_at) }}</span>
                  </div>
                </li>
              </ul>
            </template>

            <!-- 剧本列表 -->
            <template v-else-if="sourceType === 'screenplay'">
              <div v-if="screenplays.length === 0" class="state-msg">
                还没有可上架的剧本 — 请先在剧创态生成「全局剧本」或「分集方案」。
              </div>
              <ul v-else class="sim-list" role="listbox">
                <li
                  v-for="sp in screenplays"
                  :key="sp.novel_id"
                  class="sim-row"
                  :class="{ 'is-selected': selectedNovelId === sp.novel_id }"
                  role="option"
                  :aria-selected="selectedNovelId === sp.novel_id"
                  tabindex="0"
                  @click="pickNovel(sp)"
                  @keydown.enter.prevent="pickNovel(sp)"
                >
                  <span class="sim-radio" aria-hidden="true">
                    <span v-if="selectedNovelId === sp.novel_id" class="dot" />
                  </span>
                  <div class="sim-content">
                    <div class="sim-head">
                      <span class="sim-mode">剧创态</span>
                      <span class="sim-project">{{ sp.novel_title || "未命名剧本" }}</span>
                    </div>
                    <p class="sim-summary">
                      <template v-if="sp.has_global">含全局剧本 · </template>
                      {{ sp.episode_plans.length }} 套分集方案
                    </p>
                  </div>
                </li>
              </ul>
            </template>

            <!-- 漫画列表 -->
            <template v-else>
              <div v-if="comics.length === 0" class="state-msg">
                还没有可上架的漫画 — 请先在漫创态完成一部漫画(生成 + 排版)。
              </div>
              <ul v-else class="sim-list" role="listbox">
                <li
                  v-for="c in comics"
                  :key="c.comic_id"
                  class="sim-row"
                  :class="{ 'is-selected': selectedComicId === c.comic_id }"
                  role="option"
                  :aria-selected="selectedComicId === c.comic_id"
                  tabindex="0"
                  @click="pickComic(c)"
                  @keydown.enter.prevent="pickComic(c)"
                >
                  <span class="sim-radio" aria-hidden="true">
                    <span v-if="selectedComicId === c.comic_id" class="dot" />
                  </span>
                  <img
                    v-if="c.cover_url"
                    :src="apiAssetUrl(c.cover_url)"
                    class="comic-thumb"
                    alt=""
                  />
                  <div class="sim-content">
                    <div class="sim-head">
                      <span class="sim-mode">漫创态</span>
                      <span class="sim-project">{{ c.name || "未命名漫画" }}</span>
                    </div>
                    <p class="sim-summary">共 {{ c.page_count }} 页</p>
                  </div>
                </li>
              </ul>
            </template>
          </div>

          <!-- 剧本:全局 / 分集 / 两者 选择 -->
          <div v-if="sourceType === 'screenplay' && selectedNovel" class="field">
            <label class="field-label">发布内容 <span class="req">*</span></label>
            <div class="kind-row">
              <button
                type="button" class="kind-btn"
                :class="{ 'is-active': screenplayKind === 'global' }"
                :disabled="!selectedNovel.has_global"
                :title="selectedNovel.has_global ? '' : '该剧本还没有生成全局剧本'"
                @click="screenplayKind = 'global'"
              >全局剧本</button>
              <button
                type="button" class="kind-btn"
                :class="{ 'is-active': screenplayKind === 'episodes' }"
                :disabled="selectedNovel.episode_plans.length === 0"
                @click="screenplayKind = 'episodes'"
              >分集方案</button>
              <button
                type="button" class="kind-btn"
                :class="{ 'is-active': screenplayKind === 'both' }"
                :disabled="!selectedNovel.has_global || selectedNovel.episode_plans.length === 0"
                @click="screenplayKind = 'both'"
              >两者都要</button>
            </div>

            <!-- 分集方案下拉(episodes/both 时)-->
            <div v-if="screenplayKind !== 'global' && selectedNovel.episode_plans.length > 0" class="plan-select">
              <label class="field-sub">选择分集方案</label>
              <select v-model="selectedPlanId" class="text-input">
                <option
                  v-for="p in selectedNovel.episode_plans"
                  :key="p.plan_id"
                  :value="p.plan_id"
                >{{ p.scheme_name }} · {{ p.episode_count }} 集</option>
              </select>
            </div>
          </div>

          <template v-if="hasSelection">
            <!-- 作品名 -->
            <div class="field">
              <label class="field-label" for="pub-title">作品名 <span class="req">*</span></label>
              <input
                id="pub-title"
                v-model="title"
                type="text"
                maxlength="60"
                placeholder="给你的作品取个名字"
                class="text-input"
                :disabled="submitting"
                autocomplete="off"
              />
              <div class="field-hint mono">{{ title.trim().length }}/60</div>
            </div>

            <!-- 简介 -->
            <div class="field">
              <label class="field-label" for="pub-summary">简介(可选)</label>
              <textarea
                id="pub-summary"
                v-model="summary"
                maxlength="200"
                rows="2"
                placeholder="留空则自动截取正文开头"
                class="text-input textarea"
                :disabled="submitting"
              />
            </div>

            <!-- 封面 -->
            <div class="field">
              <label class="field-label">封面</label>

              <!-- 已上传图预览 -->
              <div v-if="coverImagePath" class="cover-preview">
                <img :src="apiAssetUrl(coverImagePath)" alt="封面预览" />
                <button type="button" class="cover-clear" @click="clearCover">移除图片,改用渐变</button>
              </div>

              <template v-else>
                <button
                  type="button"
                  class="upload-btn"
                  :disabled="uploadingCover || submitting"
                  @click="triggerUpload"
                >
                  <svg viewBox="0 0 24 24" width="15" height="15" fill="none"
                       stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                    <path d="M17 8l-5-5-5 5M12 3v12" />
                  </svg>
                  {{ uploadingCover ? "上传中…" : "上传自定义封面" }}
                </button>
                <p class="field-sub">不上传则用下面的渐变封面 · 支持 JPG/PNG/WebP,≤5MB</p>

                <!-- 渐变选择 -->
                <div class="grad-grid">
                  <button
                    v-for="g in GRADIENTS"
                    :key="g"
                    type="button"
                    class="grad"
                    :class="[`g${g}`, { 'is-active': coverGradient === g }]"
                    :aria-label="`渐变 ${g}`"
                    @click="coverGradient = g"
                  >
                    <span v-if="coverGradient === g" class="grad-check">✓</span>
                  </button>
                </div>
              </template>

              <input
                ref="fileInput"
                type="file"
                accept="image/*"
                class="hidden-file"
                @change="onFilePicked"
              />
            </div>

            <!-- 作品权限(v5)-->
            <div class="field">
              <label class="field-label">作品权限</label>
              <div class="vis-seg">
                <button
                  type="button" class="vis-opt"
                  :class="{ 'is-on': isPublic }"
                  @click="isPublic = true"
                >
                  <span class="vis-opt-name">公开</span>
                  <span class="vis-opt-desc">展示在广场,所有人可阅读</span>
                </button>
                <button
                  type="button" class="vis-opt"
                  :class="{ 'is-on': !isPublic }"
                  @click="isPublic = false"
                >
                  <span class="vis-opt-name">私人</span>
                  <span class="vis-opt-desc">仅自己可见,不在广场展示</span>
                </button>
              </div>
              <label
                v-if="isPublic && sourceType !== 'comic'"
                class="checkbox-row vis-dl"
              >
                <input v-model="allowDownload" type="checkbox" />
                <span>允许其他用户下载正文(Markdown)</span>
              </label>
            </div>
          </template>

          <p v-if="submitError" class="submit-error">{{ submitError }}</p>

          <footer class="modal-footer">
            <button type="button" class="btn btn-ghost" :disabled="submitting" @click="emit('close')">取消</button>
            <button type="button" class="btn btn-primary" :disabled="!canSubmit" @click="handleSubmit">
              <span v-if="submitting">上架中…</span>
              <span v-else>上架到广场</span>
            </button>
          </footer>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(31, 31, 30, 0.4);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}
.modal-card {
  width: 100%;
  max-width: 600px;
  max-height: calc(100vh - var(--space-8));
  padding: var(--space-6) var(--space-6) var(--space-5);
  position: relative;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-modal);
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}
.close-btn {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-subtle);
  background: transparent;
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.close-btn:hover:not(:disabled) { background: var(--color-surface-hover); color: var(--color-text); }
.close-btn:disabled { opacity: 0.4; cursor: not-allowed; }

.modal-header {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-bottom: var(--space-3);
  border-bottom: 1px solid var(--color-border);
}
.modal-title { font-size: var(--text-xl); font-weight: 600; color: var(--color-text); margin: 0; }
.modal-subtitle { font-size: var(--text-sm); color: var(--color-text-muted); margin: 0; line-height: 1.55; }

/* v5 item8:来源 tab + 剧本发布内容选择 */
.source-tabs {
  display: inline-flex;
  gap: 4px;
  padding: 4px;
  align-self: flex-start;
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}
.source-tab {
  padding: 6px 16px;
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-secondary);
  background: transparent;
  border: none;
  border-radius: calc(var(--radius-md) - 3px);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.source-tab:hover { color: var(--color-text); }
.source-tab.is-active {
  background: var(--color-surface);
  color: var(--color-accent-text);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}
.kind-row { display: flex; gap: var(--space-2); flex-wrap: wrap; }
.kind-btn {
  padding: 6px 14px;
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.kind-btn:hover:not(:disabled) { border-color: var(--color-accent-border); }
.kind-btn.is-active {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
  font-weight: 600;
}
.kind-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.plan-select { display: flex; flex-direction: column; gap: 4px; margin-top: var(--space-2); }

/* v5:作品权限 */
.vis-seg { display: flex; gap: var(--space-2); }
.vis-opt {
  flex: 1;
  display: flex; flex-direction: column; gap: 2px;
  padding: var(--space-2) var(--space-3);
  text-align: left;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.vis-opt:hover { border-color: var(--color-accent-border); }
.vis-opt.is-on {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}
.vis-opt-name { font-size: var(--text-sm); font-weight: 600; color: var(--color-text); }
.vis-opt.is-on .vis-opt-name { color: var(--color-accent-text); }
.vis-opt-desc { font-size: var(--text-xs); color: var(--color-text-muted); }
.checkbox-row {
  display: flex; align-items: center; gap: var(--space-2);
  font-size: var(--text-sm); color: var(--color-text); cursor: pointer;
}
.vis-dl { margin-top: var(--space-2); }
.comic-thumb {
  width: 38px; height: 52px; flex-shrink: 0;
  object-fit: cover; border-radius: var(--radius-sm);
  border: 1px solid var(--color-border);
}

.field { display: flex; flex-direction: column; gap: var(--space-2); }
.field-label { font-size: var(--text-sm); font-weight: 500; color: var(--color-text); }
.req { color: var(--color-danger); margin-left: 2px; }
.field-sub { font-size: var(--text-xs); color: var(--color-text-muted); margin: 0; }
.field-hint { display: flex; justify-content: flex-end; font-size: var(--text-xs); color: var(--color-text-subtle); }

.text-input {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  outline: none;
  transition: border-color var(--duration-fast) var(--ease-out), box-shadow var(--duration-fast) var(--ease-out);
}
.text-input:focus { border-color: var(--color-accent); box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.12); }
.textarea { resize: vertical; line-height: 1.6; font-family: inherit; }

/* sim 列表 */
.sim-list { list-style: none; display: flex; flex-direction: column; gap: var(--space-2); max-height: 260px; overflow-y: auto; padding: 0; margin: 0; }
.sim-row {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
  padding: var(--space-3);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  outline: none;
  transition: border-color var(--duration-fast) var(--ease-out), background var(--duration-fast) var(--ease-out);
}
.sim-row:hover, .sim-row:focus-visible { border-color: var(--color-accent-border); background: var(--color-surface-hover); }
.sim-row.is-selected { border-color: var(--color-accent); background: var(--color-accent-soft); }
.sim-radio {
  flex-shrink: 0;
  width: 18px; height: 18px;
  border: 1.5px solid var(--color-border-strong);
  border-radius: 50%;
  display: flex; align-items: center; justify-content: center;
  margin-top: 2px;
}
.sim-row.is-selected .sim-radio { border-color: var(--color-accent); }
.dot { width: 10px; height: 10px; border-radius: 50%; background: var(--color-accent); }
.sim-content { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 4px; }
.sim-head { display: flex; align-items: center; gap: var(--space-2); }
.sim-mode {
  flex-shrink: 0;
  font-size: 11px; font-weight: 600;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  padding: 2px 7px;
  border-radius: var(--radius-sm);
}
.sim-project { font-size: var(--text-sm); color: var(--color-text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.sim-summary {
  font-size: var(--text-xs); color: var(--color-text-muted); margin: 0; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.sim-time { font-size: 11px; color: var(--color-text-subtle); }

/* 上传 + 渐变 */
.upload-btn {
  display: inline-flex; align-items: center; gap: var(--space-2);
  align-self: flex-start;
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm); color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.upload-btn:hover:not(:disabled) { border-color: var(--color-accent-border); background: var(--color-surface-hover); }
.upload-btn:disabled { opacity: 0.5; cursor: not-allowed; }
.hidden-file { display: none; }

.grad-grid { display: grid; grid-template-columns: repeat(9, 1fr); gap: var(--space-2); margin-top: var(--space-1); }
@media (max-width: 480px) { .grad-grid { grid-template-columns: repeat(5, 1fr); } }
.grad {
  aspect-ratio: 1;
  border-radius: var(--radius-md);
  border: 2px solid transparent;
  cursor: pointer;
  position: relative;
  display: flex; align-items: center; justify-content: center;
  transition: transform var(--duration-fast) var(--ease-out);
}
.grad:hover { transform: scale(1.06); }
.grad.is-active { border-color: var(--color-text); }
.grad-check { color: #fff; font-size: 13px; font-weight: 700; text-shadow: 0 1px 3px rgba(0,0,0,0.4); }
.g1 { background: linear-gradient(135deg, #5B6CF0, #9B5CF0); }
.g2 { background: linear-gradient(135deg, #0FB5A8, #1E6FE0); }
.g3 { background: linear-gradient(135deg, #E0792F, #C13E6A); }
.g4 { background: linear-gradient(135deg, #7C3AED, #3B1F8B); }
.g5 { background: linear-gradient(135deg, #2D9E6F, #16607A); }
.g6 { background: linear-gradient(135deg, #C2456A, #7A2E8E); }
.g7 { background: linear-gradient(135deg, #4661C9, #22324F); }
.g8 { background: linear-gradient(135deg, #B8843E, #7A4E2E); }
.g9 { background: linear-gradient(135deg, #6E59C2, #9686C2); }

.cover-preview { display: flex; flex-direction: column; gap: var(--space-2); }
.cover-preview img {
  width: 100%; max-height: 180px; object-fit: cover;
  border-radius: var(--radius-md); border: 1px solid var(--color-border);
}
.cover-clear {
  align-self: flex-start;
  font-size: var(--text-xs); color: var(--color-text-muted);
  background: transparent; border: none; cursor: pointer; text-decoration: underline;
}
.cover-clear:hover { color: var(--color-text); }

.state-msg {
  padding: var(--space-4); text-align: center;
  font-size: var(--text-sm); color: var(--color-text-muted);
  background: var(--color-bg-subtle); border-radius: var(--radius-md); line-height: 1.6;
}
.state-error { color: var(--color-danger); background: var(--color-danger-soft); }
.retry-btn {
  margin-left: var(--space-2); padding: 2px var(--space-2);
  font-size: var(--text-xs); color: var(--color-accent-text);
  background: transparent; border: 1px solid var(--color-accent-border); border-radius: var(--radius-sm); cursor: pointer;
}

.submit-error {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm); color: var(--color-danger);
  background: var(--color-danger-soft); border-radius: var(--radius-md); margin: 0;
}

.modal-footer {
  display: flex; justify-content: flex-end; gap: var(--space-2);
  padding-top: var(--space-3); border-top: 1px solid var(--color-border);
}
.btn { padding: var(--space-2) var(--space-5); font-size: var(--text-sm); font-weight: 500; border-radius: var(--radius-md); cursor: pointer; transition: all var(--duration-fast) var(--ease-out); }
.btn-ghost { color: var(--color-text); background: transparent; border: 1px solid var(--color-border); }
.btn-ghost:hover:not(:disabled) { background: var(--color-surface-hover); border-color: var(--color-border-strong); }
.btn-primary { color: var(--color-text-on-accent); background: var(--color-accent); border: 1px solid var(--color-accent); }
.btn-primary:hover:not(:disabled) { background: var(--color-accent-hover); }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.mono { font-family: var(--font-mono); }
</style>
