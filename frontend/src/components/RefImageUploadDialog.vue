<script setup lang="ts">
/**
 * RefImageUploadDialog — D.9 Sprint 2.B+ 六修(2026-05-12)漫画态参考图本地上传
 *
 * 历史:Sprint 2.B 让用户粘 3 张公网 URL,用户体验差(要找图床)。六修改为本地上传 —
 * 文件选择器 + 自动上传到后端 → 拿到内部 URL → 提交时一起传给 upload_references。
 *
 * 流程:
 *   1. 3 个 file input(单文件 / image/* / max 5MB)
 *   2. 用户选某张 → 立即 POST /api/comics/{id}/upload_reference_file 上传(per-row)
 *   3. 成功后 row 状态 'ok' + 显缩略图;失败 'error' + retry 按钮
 *   4. 3 张全 ok → "开始分析"按钮可点
 *   5. 点提交 → emit('submit', [url1, url2, url3]) → 父调 upload_references 触发画风定调员
 *
 * 后端契约:
 *   - 上传单张:POST /api/comics/{id}/upload_reference_file (multipart, field=file)
 *     → {url: "/api/comic-files/{uid}/{token}.ext", filename: "..."}
 *   - 触发 pipeline:POST /api/comics/{id}/upload_references {image_urls: [3 URLs]}
 *
 * a11y:role="dialog" + aria-modal + aria-busy(分析中)+ Esc 关
 */
import { computed, ref, watch } from "vue";

import { api } from "../api/client";
import { ApiError } from "../api/types";
import { confirm as confirmDialog } from "../composables/useConfirm";
import { toast } from "../composables/useToast";

const props = defineProps<{
  open: boolean;
  loading?: boolean;       // 父组件控制(后端 LLM 调用期间)
  comicId: string | null;  // 漫画 ID — 上传时拼路径用
  /**
   * Sprint 5.x bug fix(2026-05-14):动态 loading 文案(按 comic.state + progress 显示具体子步骤)
   * 父组件计算(详 ComicProjectView.styleAnalyzingMessage),不传时用兜底静态文案
   */
  loadingMessage?: string;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "submit", imageUrls: string[]): void;
}>();

// ============================================================
// 每张图的本地状态(3 槽)
// ============================================================

interface RefSlot {
  /** 已选的本地文件(预览用,object URL)*/
  previewUrl: string | null;
  /** 后端返的内部 URL(/api/comic-files/...)— 提交给 upload_references 用 */
  serverUrl: string | null;
  /** 'idle' / 'uploading' / 'ok' / 'error' */
  status: "idle" | "uploading" | "ok" | "error";
  errorMsg: string | null;
}

function freshSlot(): RefSlot {
  return { previewUrl: null, serverUrl: null, status: "idle", errorMsg: null };
}

const slots = ref<RefSlot[]>([freshSlot(), freshSlot(), freshSlot()]);

/** 隐藏 file input refs(每槽 1 个),用于"重选"时触发 click */
const fileInputs = ref<Array<HTMLInputElement | null>>([null, null, null]);

// ============================================================
// 重置(modal 关闭时)
// ============================================================

watch(
  () => props.open,
  (isOpen) => {
    if (!isOpen) {
      // 释放 object URL 防内存泄漏
      slots.value.forEach((s) => {
        if (s.previewUrl) URL.revokeObjectURL(s.previewUrl);
      });
      slots.value = [freshSlot(), freshSlot(), freshSlot()];
    }
  },
);

// ============================================================
// 文件选择 → 立即上传
// ============================================================

async function onFileSelected(idx: number, e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  // 重置 input value 让用户能重选同一张
  input.value = "";

  if (!file) return;
  if (!props.comicId) {
    toast.error("漫画 ID 缺失,请刷新页面重试");
    return;
  }

  // 客户端预校验(大小 + 类型)— 后端会再校验一遍,这里给即时反馈
  const MAX_BYTES = 5 * 1024 * 1024;
  if (file.size > MAX_BYTES) {
    toast.warning(`第 ${idx + 1} 张图超过 5MB(当前 ${(file.size / 1024 / 1024).toFixed(1)}MB)`);
    return;
  }
  if (!file.type.startsWith("image/")) {
    toast.warning(`第 ${idx + 1} 张图不是图片格式`);
    return;
  }

  // 释放之前的 object URL
  if (slots.value[idx].previewUrl) {
    URL.revokeObjectURL(slots.value[idx].previewUrl!);
  }

  // 创建预览
  const previewUrl = URL.createObjectURL(file);
  slots.value[idx] = {
    previewUrl,
    serverUrl: null,
    status: "uploading",
    errorMsg: null,
  };

  // 上传到后端
  const formData = new FormData();
  formData.append("file", file);

  try {
    const resp = await api.post<{ url: string; filename: string }>(
      `/comics/${props.comicId}/upload_reference_file`,
      formData,
    );
    slots.value[idx] = {
      previewUrl,
      serverUrl: resp.url,
      status: "ok",
      errorMsg: null,
    };
  } catch (err) {
    let msg = "上传失败";
    if (err instanceof ApiError) {
      const detail = (err.detail as { message?: string } | null)?.message;
      msg = detail || err.message;
    }
    slots.value[idx] = {
      previewUrl,
      serverUrl: null,
      status: "error",
      errorMsg: msg,
    };
    toast.error(`第 ${idx + 1} 张上传失败:${msg}`);
  }
}

function retryUpload(idx: number) {
  // 触发对应 file input 重新弹文件选择器
  fileInputs.value[idx]?.click();
}

function clearSlot(idx: number) {
  if (slots.value[idx].previewUrl) {
    URL.revokeObjectURL(slots.value[idx].previewUrl!);
  }
  slots.value[idx] = freshSlot();
}

// ============================================================
// 提交
// ============================================================

const canSubmit = computed<boolean>(() => {
  if (props.loading) return false;
  return slots.value.every((s) => s.status === "ok" && s.serverUrl);
});

function handleSubmit() {
  if (!canSubmit.value) return;
  const urls = slots.value.map((s) => s.serverUrl!) as string[];
  emit("submit", urls);
}

/**
 * Sprint 5.4.1(2026-05-13):跳过上传 — AI 仅从剧本题材推画风
 *
 * 用户场景:没有现成参考图 / 不想花时间找;愿意接受 AI 自动推断
 * 后端契约:emit('submit', [])  → 父调 upload_references({ image_urls: [] })
 *           → Schema 接受 0 张(min_length=0,validator 卡 0 或 3)
 *           → _agent_style_director_v2 跳过 Stage 1 Qwen-VL,Stage 2 用题材推画风
 */
async function handleSkip() {
  if (props.loading) return;
  const ok = await confirmDialog({
    title: "跳过上传参考图?",
    message:
      "AI 将仅根据剧本题材(如悬疑/校园/惊悚/治愈)推断合适的画风,"
      + "出图风格可能不如有参考图准确。\n\n"
      + "推荐:有心仪画风(如某网漫/同人作品截图)→ 上传;"
      + "完全没头绪 → 跳过,让 AI 推荐。",
    confirmLabel: "跳过,让 AI 推画风",
    cancelLabel: "返回上传",
    danger: false,
  });
  if (!ok) return;
  emit("submit", []);   // 空数组 = 跳过模式,后端走 text_only 路径
}

function handleClose() {
  if (props.loading) {
    toast.info("正在分析,请等待完成...");
    return;
  }
  emit("close");
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape" && props.open) handleClose();
}
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="dialog-backdrop"
        role="presentation"
        @click.self="handleClose"
        @keydown="onKeydown"
      >
        <div
          class="dialog"
          role="dialog"
          aria-modal="true"
          aria-labelledby="ref-upload-title"
          :aria-busy="loading"
        >
          <header class="dialog-header">
            <h2 id="ref-upload-title" class="dialog-title">上传 3 张同画风参考图</h2>
            <button
              type="button"
              class="close-btn"
              aria-label="关闭"
              :disabled="loading"
              @click="handleClose"
            >×</button>
          </header>

          <div class="dialog-body">
            <p class="hint">
              选 <strong>3 张同画风</strong>本地图片(网漫截图 / 同人作品 / 你喜欢的画师作品)。
              AI 会综合 3 张图的视觉 DNA 锚定你的整本漫画画风。
            </p>
            <p class="hint hint--meta">
              支持 JPG / PNG / WebP / GIF / BMP,单张 ≤ 5MB。
            </p>

            <div class="slot-grid">
              <div
                v-for="(slot, idx) in slots"
                :key="idx"
                class="slot"
                :class="`slot-status-${slot.status}`"
              >
                <div class="slot-label-row">
                  <span class="slot-label">参考图 {{ idx + 1 }}</span>
                  <span v-if="slot.status === 'ok'" class="slot-badge ok">✓ 已上传</span>
                  <span v-else-if="slot.status === 'uploading'" class="slot-badge uploading">上传中…</span>
                  <span v-else-if="slot.status === 'error'" class="slot-badge error">失败</span>
                  <span v-else class="slot-badge idle">未选</span>
                </div>

                <div class="preview-area">
                  <!-- 已选 / 上传中 / OK -->
                  <template v-if="slot.previewUrl">
                    <img
                      :src="slot.previewUrl"
                      :alt="`参考图 ${idx + 1} 预览`"
                      class="preview-img"
                    />
                    <div v-if="slot.status === 'uploading'" class="upload-overlay">
                      <div class="spinner" aria-hidden="true"></div>
                    </div>
                    <button
                      v-if="slot.status === 'ok' && !loading"
                      type="button"
                      class="slot-clear"
                      :aria-label="`移除参考图 ${idx + 1}`"
                      @click="clearSlot(idx)"
                    >×</button>
                  </template>
                  <!-- 空 / 失败 -->
                  <template v-else>
                    <span class="preview-empty">点击下方"选择文件"</span>
                  </template>
                </div>

                <!-- 失败错误 -->
                <p v-if="slot.status === 'error'" class="slot-error-msg">
                  {{ slot.errorMsg }}
                </p>

                <!-- 文件选择按钮(隐藏的 input + 自定义 button)-->
                <input
                  :ref="(el) => { fileInputs[idx] = el as HTMLInputElement | null; }"
                  type="file"
                  accept="image/*"
                  class="hidden-input"
                  :disabled="loading || slot.status === 'uploading'"
                  @change="(e) => onFileSelected(idx, e)"
                />
                <button
                  v-if="slot.status === 'idle'"
                  type="button"
                  class="select-btn"
                  :disabled="loading || !comicId"
                  @click="fileInputs[idx]?.click()"
                >选择文件</button>
                <button
                  v-else-if="slot.status === 'error'"
                  type="button"
                  class="select-btn select-btn--retry"
                  :disabled="loading"
                  @click="retryUpload(idx)"
                >重选</button>
              </div>
            </div>

            <p v-if="loading" class="loading-note">
              <span class="hourglass" aria-hidden="true">⏳</span>
              {{ loadingMessage || "AI 正在分析画风 + 出 3 张定调样张(预计 40-60 秒)..." }}
            </p>
          </div>

          <footer class="dialog-footer">
            <!-- Sprint 5.4.1:跳过按钮放左侧,与右侧 取消/开始分析 形成"选择左 / 主行右"的视觉分组 -->
            <button
              type="button"
              class="ghost-btn skip-btn"
              :disabled="loading"
              @click="handleSkip"
              title="没有现成参考图?让 AI 仅从剧本题材推画风"
            >
              <span aria-hidden="true">⏭</span>
              <span>跳过,AI 从文本推画风</span>
            </button>
            <div class="footer-right">
              <button
                type="button"
                class="ghost-btn"
                :disabled="loading"
                @click="handleClose"
              >取消</button>
              <button
                type="button"
                class="primary-btn"
                :disabled="!canSubmit"
                @click="handleSubmit"
              >
                {{ loading ? "分析中…" : "开始分析" }}
              </button>
            </div>
          </footer>
        </div>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.dialog-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal);
  padding: var(--space-4);
}

.dialog {
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  max-width: 780px;
  width: 100%;
  max-height: 90vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.dialog-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--color-border);
}

.dialog-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}

.close-btn {
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  color: var(--color-text-muted);
  background: transparent;
  border-radius: var(--radius-sm);
  transition: background var(--duration-fast) var(--ease-out);
}
.close-btn:hover:not(:disabled) {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.close-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.dialog-body {
  padding: var(--space-5);
  overflow-y: auto;
  flex: 1;
}

.hint {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: 1.6;
  margin: 0 0 var(--space-2);
}
.hint--meta {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

/* ===== 3 槽网格 ===== */
.slot-grid {
  display: grid;
  grid-template-columns: 1fr 1fr 1fr;
  gap: var(--space-3);
  margin-top: var(--space-4);
}
@media (max-width: 640px) {
  .slot-grid { grid-template-columns: 1fr; }
}

.slot {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition: border-color var(--duration-fast) var(--ease-out);
}
.slot.slot-status-ok {
  border-color: var(--color-accent);
}
.slot.slot-status-error {
  border-color: var(--color-danger);
}

.slot-label-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.slot-label {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
}
.slot-badge {
  padding: 1px 6px;
  font-size: 10px;
  letter-spacing: 0.04em;
  border-radius: var(--radius-sm);
}
.slot-badge.idle { color: var(--color-text-subtle); background: var(--color-surface); border: 1px dashed var(--color-border-strong); }
.slot-badge.uploading { color: var(--color-accent-text); background: var(--color-accent-soft); }
.slot-badge.ok { color: #fff; background: var(--color-accent); }
.slot-badge.error { color: #fff; background: var(--color-danger); }

.preview-area {
  position: relative;
  aspect-ratio: 1 / 1;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}
.preview-img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}
.preview-empty {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  text-align: center;
  padding: var(--space-2);
}

.upload-overlay {
  position: absolute;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
}
.spinner {
  width: 32px;
  height: 32px;
  border: 3px solid rgba(255, 255, 255, 0.3);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin var(--duration-spin) linear infinite;
}

.slot-clear {
  position: absolute;
  top: var(--space-1);
  right: var(--space-1);
  width: 24px;
  height: 24px;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  border-radius: 50%;
  font-size: var(--text-base);
  line-height: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
}
.slot-clear:hover {
  background: rgba(0, 0, 0, 0.85);
}

.slot-error-msg {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--color-danger);
  line-height: 1.4;
}

.hidden-input {
  display: none;
}

.select-btn {
  padding: var(--space-2);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.select-btn:hover:not(:disabled) {
  border-color: var(--color-accent-border);
  background: var(--color-surface-hover);
}
.select-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.select-btn--retry {
  color: var(--color-danger);
  border-color: var(--color-danger-soft);
}
.select-btn--retry:hover:not(:disabled) {
  background: var(--color-danger-soft);
}

.loading-note {
  margin-top: var(--space-4);
  padding: var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-md);
  text-align: center;
}

.dialog-footer {
  display: flex;
  /* Sprint 5.4.1:跳过按钮左 / 取消+开始分析 右 */
  justify-content: space-between;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-4) var(--space-5);
  border-top: 1px solid var(--color-border);
  flex-wrap: wrap;
}
.dialog-footer .footer-right {
  display: flex;
  gap: var(--space-2);
}
.dialog-footer .skip-btn {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1);
  font-size: var(--text-sm);
}

.ghost-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
}
.ghost-btn:hover:not(:disabled) {
  background: var(--color-surface-hover);
}
.ghost-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.primary-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border-radius: var(--radius-md);
}
.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}
.primary-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

</style>
