<script setup lang="ts">
/**
 * ExportSimulationDialog — 导出推演 modal(Sprint 2.E 去 IP 化导出)。
 *
 * 两版本:
 *   - 原版(留原作角色名,可自留;商用有 IP 风险)
 *   - 去 IP 版(走字典替换 + 法务 trail;同人圈商用可用)
 *
 * 首次导出去 IP 版会自动生成字典(LLM 调用 ~20s);后续导出复用缓存字典。
 * 用户可手动「重新生成字典」覆盖。
 *
 * UI 流程:
 *   ① 选版本(原版 / 去 IP)
 *   ② 选去 IP 时:若无字典 → 显「✦ AI 生成字典」按钮 → 点 → loading → 拿字典
 *                  若有字典 → 显字典预览(前 10 条 + 总数)+ [↻ 重新生成]
 *   ③ 点「导出」→ 后端拼 markdown → 浏览器下载 .md
 *   ④ 去 IP 版导出后弹"替换 trail"折叠展开(用户可看哪些被替换多少次)
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type DeIpDictionary,
  type ExportSimulationResponse,
} from "../api/types";
import { toast } from "../composables/useToast";

const props = defineProps<{
  open: boolean;
  simulationId: string;
  projectId: string;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

type Version = "original" | "de_ip";
const version = ref<Version>("original");

const dictionary = ref<DeIpDictionary | null>(null);
const dictionaryLoading = ref(false);
const generating = ref(false);
const exporting = ref(false);

const lastResult = ref<ExportSimulationResponse | null>(null);

// ============================================================
// open 时:重置 + 拉字典(若有)
// ============================================================

watch(
  () => props.open,
  async (isOpen) => {
    if (!isOpen) {
      lastResult.value = null;
      return;
    }
    version.value = "original";
    lastResult.value = null;
    await loadDictionary();
  },
);

async function loadDictionary() {
  dictionaryLoading.value = true;
  try {
    const dict = await api.get<DeIpDictionary>(
      `/projects/${props.projectId}/de_ip_dictionary`,
    );
    dictionary.value = dict;
  } catch (e) {
    if (e instanceof ApiError && e.code === "DE_IP_DICTIONARY_NOT_FOUND") {
      dictionary.value = null;   // 还没生成 — 正常
    } else if (e instanceof ApiError) {
      toast.error(`拉字典失败:${e.message}`);
    }
  } finally {
    dictionaryLoading.value = false;
  }
}

async function generateDictionary() {
  generating.value = true;
  try {
    const dict = await api.post<DeIpDictionary>(
      `/projects/${props.projectId}/de_ip_dictionary`,
    );
    dictionary.value = dict;
    const count = Object.keys(dict.mapping).length;
    toast.success(`字典已生成 — ${count} 条替换映射`);
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.code === "NO_CHARACTERS_TO_REPLACE") {
        toast.warning("项目还没角色,无法生成字典");
      } else {
        toast.error(`生成字典失败:${e.message}`);
      }
    } else {
      toast.error("生成字典失败,请稍后重试");
    }
  } finally {
    generating.value = false;
  }
}

async function regenerateDictionary() {
  // 重新生成 = 覆盖原字典
  generating.value = true;
  try {
    const dict = await api.post<DeIpDictionary>(
      `/projects/${props.projectId}/de_ip_dictionary`,
    );
    dictionary.value = dict;
    toast.success("字典已重新生成,新替换映射立即生效");
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "重新生成失败");
  } finally {
    generating.value = false;
  }
}

// ============================================================
// 导出 + 下载
// ============================================================

async function handleExport() {
  exporting.value = true;
  try {
    const resp = await api.post<ExportSimulationResponse>(
      `/simulations/${props.simulationId}/export`,
      { version: version.value },
    );
    lastResult.value = resp;
    triggerBrowserDownload(resp.filename, resp.content);
    if (resp.version === "de_ip") {
      toast.success(`已导出去 IP 版 — 共替换 ${resp.total_replacements} 处`);
    } else {
      toast.success("已导出原版");
    }
  } catch (e) {
    if (e instanceof ApiError) {
      if (e.code === "NO_DE_IP_DICTIONARY") {
        toast.warning("项目还没生成字典,请先点「✦ AI 生成字典」");
      } else if (e.code === "SIMULATION_NOT_EXPORTABLE") {
        toast.error("推演未完成或内容为空,无法导出");
      } else {
        toast.error(`导出失败:${e.message}`);
      }
    } else {
      toast.error("导出失败,请稍后重试");
    }
  } finally {
    exporting.value = false;
  }
}

function triggerBrowserDownload(filename: string, content: string) {
  const blob = new Blob([content], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

// ============================================================
// 派生
// ============================================================

const dictionaryCount = computed(() =>
  dictionary.value ? Object.keys(dictionary.value.mapping).length : 0,
);
const dictionaryPreview = computed<{ k: string; v: string }[]>(() => {
  if (!dictionary.value) return [];
  return Object.entries(dictionary.value.mapping)
    .slice(0, 10)
    .map(([k, v]) => ({ k, v }));
});
const hasDictionary = computed(() => dictionaryCount.value > 0);

const trailShown = ref(false);

// 全局 keydown 监听 — overlay 用 tabindex 不 focus 时,@keydown 不触发(Teleport
// 到 body 后无法靠 DOM 冒泡)。改 document 级监听后,modal 一打开就能按 Esc 关。
function onGlobalKey(e: KeyboardEvent) {
  if (props.open && e.key === "Escape") emit("close");
}
onMounted(() => document.addEventListener("keydown", onGlobalKey));
onBeforeUnmount(() => document.removeEventListener("keydown", onGlobalKey));
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="export-overlay"
        @click.self="emit('close')"
      >
        <transition name="export-pop">
          <section
            v-if="open"
            class="export-card"
            role="dialog"
            aria-label="导出推演"
          >
            <header class="export-header">
              <h2 class="export-title">
                <span class="export-icon">↓</span>
                导出推演
              </h2>
              <button class="close-btn" type="button" aria-label="关闭" @click="emit('close')">×</button>
            </header>

            <p class="export-sub">
              选择导出版本 — 原版保留角色原名,去 IP 版走字典替换(同人圈商用可用)
            </p>

            <!-- 版本选择 -->
            <div class="version-row">
              <label
                class="version-card"
                :class="{ 'is-active': version === 'original' }"
              >
                <input
                  type="radio"
                  v-model="version"
                  value="original"
                  class="visually-hidden"
                />
                <div class="version-head">
                  <span class="version-icon">📜</span>
                  <span class="version-name">原版</span>
                </div>
                <p class="version-desc">
                  保留原作角色名 — 自留 / 内部分享适用;商用有 IP 风险
                </p>
              </label>

              <label
                class="version-card"
                :class="{ 'is-active': version === 'de_ip' }"
              >
                <input
                  type="radio"
                  v-model="version"
                  value="de_ip"
                  class="visually-hidden"
                />
                <div class="version-head">
                  <span class="version-icon">🛡</span>
                  <span class="version-name">去 IP 版</span>
                  <span class="version-rec">推荐商用</span>
                </div>
                <p class="version-desc">
                  字典替换专有名词 — 同人圈发布 / 商用合规
                </p>
              </label>
            </div>

            <!-- 去 IP 版字典状态 -->
            <div v-if="version === 'de_ip'" class="dictionary-section">
              <header class="dict-header">
                <span class="dict-title">替换字典</span>
                <span v-if="hasDictionary" class="dict-count mono">{{ dictionaryCount }} 条</span>
              </header>

              <!-- 加载字典中 -->
              <p v-if="dictionaryLoading" class="dict-msg">加载字典…</p>

              <!-- 无字典 -->
              <div v-else-if="!hasDictionary" class="dict-empty">
                <p class="dict-empty-msg">
                  此项目还没生成字典 — 调 LLM 一次生成(约 20 秒,不扣配额)
                </p>
                <button
                  type="button"
                  class="btn-generate"
                  :disabled="generating"
                  @click="generateDictionary"
                >
                  <span v-if="generating">生成中…</span>
                  <span v-else>✦ AI 生成字典</span>
                </button>
              </div>

              <!-- 有字典 -->
              <div v-else class="dict-preview">
                <p v-if="dictionary?.notes" class="dict-notes">
                  <span class="dict-notes-label">设计:</span>{{ dictionary.notes }}
                </p>
                <ul class="dict-mapping">
                  <li
                    v-for="m in dictionaryPreview"
                    :key="m.k"
                    class="dict-row"
                  >
                    <span class="dict-orig">{{ m.k }}</span>
                    <span class="dict-arrow">→</span>
                    <span class="dict-new">{{ m.v }}</span>
                  </li>
                  <li v-if="dictionaryCount > 10" class="dict-more">
                    + {{ dictionaryCount - 10 }} 条更多
                  </li>
                </ul>
                <button
                  type="button"
                  class="btn-regenerate"
                  :disabled="generating"
                  @click="regenerateDictionary"
                  title="重新生成 — 覆盖现有字典"
                >
                  <span v-if="generating">生成中…</span>
                  <span v-else>↻ 重新生成字典</span>
                </button>
              </div>
            </div>

            <!-- 上次导出 trail(去 IP 版导出后展示) -->
            <div v-if="lastResult?.replacements && lastResult.replacements.length" class="trail-section">
              <button
                type="button"
                class="trail-toggle"
                @click="trailShown = !trailShown"
              >
                {{ trailShown ? '▾' : '▸' }}
                查看上次导出替换 trail({{ lastResult.total_replacements }} 处替换)
              </button>
              <ul v-if="trailShown" class="trail-list">
                <li
                  v-for="entry in lastResult.replacements"
                  :key="entry.original"
                  class="trail-row"
                >
                  <span class="trail-orig">{{ entry.original }}</span>
                  <span class="trail-arrow">→</span>
                  <span class="trail-new">{{ entry.replaced }}</span>
                  <span class="trail-count mono">× {{ entry.count }}</span>
                </li>
              </ul>
            </div>

            <!-- 导出按钮 -->
            <footer class="export-footer">
              <button type="button" class="btn-cancel" @click="emit('close')">
                取消
              </button>
              <button
                type="button"
                class="btn-export"
                :disabled="exporting || (version === 'de_ip' && !hasDictionary)"
                @click="handleExport"
              >
                <span v-if="exporting">导出中…</span>
                <span v-else>↓ 导出 .md</span>
              </button>
            </footer>
          </section>
        </transition>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.export-overlay {
  position: fixed;
  inset: 0;
  z-index: var(--z-modal-backdrop);
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(15, 23, 42, 0.45);
  backdrop-filter: blur(4px);
  padding: var(--space-4);
}

.export-card {
  position: relative;
  width: 100%;
  max-width: 560px;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  padding: var(--space-5);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.18);
  overflow-y: auto;
  z-index: var(--z-modal);
}

.export-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.export-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.export-icon {
  color: var(--color-accent);
  font-size: var(--text-xl);
}
.close-btn {
  width: 28px;
  height: 28px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: var(--text-xl);
  color: var(--color-text-subtle);
  background: transparent;
  border-radius: var(--radius-full);
  cursor: pointer;
}
.close-btn:hover {
  color: var(--color-text);
  background: var(--color-bg-subtle);
}

.export-sub {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.6;
  margin: 0;
}

/* 版本卡 */
.version-row {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-2);
}
.version-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--space-3);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.version-card:hover {
  border-color: var(--color-accent-border);
}
.version-card.is-active {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}
.visually-hidden {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0,0,0,0);
}
.version-head {
  display: flex;
  align-items: center;
  gap: 6px;
}
.version-icon {
  font-size: var(--text-md);
}
.version-name {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
}
.version-rec {
  font-size: 10px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: rgba(22, 163, 74, 0.14);
  color: #16A34A;
  font-weight: 600;
}
.version-desc {
  font-size: 11px;
  color: var(--color-text-muted);
  line-height: 1.5;
  margin: 0;
}

/* 字典区 */
.dictionary-section {
  padding: var(--space-3);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.dict-header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.dict-title {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
}
.dict-count {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
}
.dict-msg {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  text-align: center;
  margin: 0;
}
.dict-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2);
}
.dict-empty-msg {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  text-align: center;
  margin: 0;
}
.btn-generate {
  padding: 6px var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: filter var(--duration-fast) var(--ease-out);
}
.btn-generate:hover:not(:disabled) {
  filter: brightness(1.05);
}
.btn-generate:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.dict-preview {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.dict-notes {
  font-size: 11px;
  color: var(--color-text-muted);
  line-height: 1.5;
  padding: 4px var(--space-2);
  background: var(--color-surface);
  border-radius: var(--radius-sm);
  margin: 0;
}
.dict-notes-label {
  font-weight: 600;
  color: var(--color-accent-text);
}
.dict-mapping {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 200px;
  overflow-y: auto;
}
.dict-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-xs);
  padding: 2px var(--space-2);
}
.dict-orig {
  color: var(--color-text-muted);
}
.dict-arrow {
  color: var(--color-text-subtle);
}
.dict-new {
  color: var(--color-accent-text);
  font-weight: 500;
}
.dict-more {
  font-size: 11px;
  color: var(--color-text-subtle);
  font-style: italic;
  padding: 2px var(--space-2);
}
.btn-regenerate {
  align-self: flex-start;
  padding: 3px var(--space-2);
  font-size: 11px;
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.btn-regenerate:hover:not(:disabled) {
  color: var(--color-accent-text);
  border-color: var(--color-accent);
}
.btn-regenerate:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* trail */
.trail-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-1);
}
.trail-toggle {
  align-self: flex-start;
  padding: 3px var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  cursor: pointer;
}
.trail-toggle:hover {
  color: var(--color-accent-text);
}
.trail-list {
  list-style: none;
  padding: var(--space-2);
  margin: 0;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: 2px;
  max-height: 220px;
  overflow-y: auto;
}
.trail-row {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-xs);
}
.trail-orig {
  color: var(--color-text-muted);
  text-decoration: line-through;
}
.trail-arrow {
  color: var(--color-text-subtle);
}
.trail-new {
  color: var(--color-accent-text);
  font-weight: 500;
}
.trail-count {
  margin-left: auto;
  color: var(--color-text-subtle);
  font-size: 10px;
}

/* 底部按钮 */
.export-footer {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
}
.btn-cancel {
  padding: 6px var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
}
.btn-cancel:hover {
  color: var(--color-text);
  background: var(--color-bg-subtle);
}
.btn-export {
  padding: 6px var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  transition: filter var(--duration-fast) var(--ease-out);
}
.btn-export:hover:not(:disabled) {
  filter: brightness(1.05);
}
.btn-export:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

/* transitions(export-pop 是内部子弹窗,保留独立 scale 节奏)*/
.export-pop-enter-active,
.export-pop-leave-active {
  transition:
    transform var(--duration-base) var(--ease-out),
    opacity var(--duration-fast) var(--ease-out);
}
.export-pop-enter-from,
.export-pop-leave-to {
  transform: scale(0.96) translateY(10px);
  opacity: 0;
}
</style>
