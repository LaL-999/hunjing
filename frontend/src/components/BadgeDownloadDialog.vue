<script setup lang="ts">
/**
 * BadgeDownloadDialog — 创作徽章下载 modal(Sprint D.3)。
 *
 * 产品语义(项目记忆 §AI 协作认证徽章):
 *   把 AI 使用去污名化,转换同人圈手心态 — 让用户**自豪地承认 + 透明** AI 协作。
 *
 * 用户使用流程:
 *   ① SimulationDetailView done 屏点「✨ 创作徽章」按钮
 *   ② 弹此 dialog → 预览浅 / 深两种风格 + 一键下载 SVG
 *   ③ 用户把 SVG 贴到豆瓣 / 晋江 / B 站等同人圈作品发布页
 *
 * 为什么不做 markdown 嵌入代码:
 *   `![](https://huimeng.example/badge.svg)` 需要公网 URL 托管,但浑晶 E 阶段
 *   部署后才有公网 CDN。**现阶段最务实路径**:用户下载 SVG 到本地,自己贴。
 *   E.1 部署后再加 CDN URL + markdown 嵌入代码(D.3 polish)。
 *
 * 不复用 ConfirmDialog:此 dialog 是预览 + 选择 + 下载三步交互,语义跟"确认/取消"
 * 二元 dialog 不同;复用反而把 ConfirmDialog API 撑得过载。
 */
import { ref } from "vue";

import { toast } from "../composables/useToast";

const props = defineProps<{
  open: boolean;
  /** 用户产物归属项目名,显在 dialog 头部副标(可选)*/
  projectName?: string;
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

/** 选中的变体(浅 / 深),驱动预览 + 下载 */
const variant = ref<"light" | "dark">("light");

const badgeUrl = (v: "light" | "dark") => `/badges/huimeng-badge-${v}.svg`;

async function handleDownload() {
  try {
    const url = badgeUrl(variant.value);
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const blob = await resp.blob();
    triggerBrowserDownload(
      `huimeng-badge-${variant.value}.svg`,
      blob,
    );
    toast.success("徽章已下载 — 把它贴到你的作品发布页吧 ✨", 5000);
  } catch (e) {
    toast.error(
      e instanceof Error ? `下载失败:${e.message}` : "下载失败,请稍后重试",
    );
  }
}

function triggerBrowserDownload(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function handleBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) emit("close");
}
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="badge-overlay"
        role="dialog"
        aria-modal="true"
        aria-label="下载创作徽章"
        @click="handleBackdrop"
        @keydown.esc="emit('close')"
        tabindex="-1"
      >
        <section class="badge-card surface">
          <header class="badge-header">
            <div class="header-text">
              <h2 class="badge-title">
                <span class="title-icon">✨</span>
                创作徽章
              </h2>
              <p class="badge-sub">
                透明创作 · 把它贴到你发布作品的地方<span v-if="projectName"> ·《{{ projectName }}》</span>
              </p>
            </div>
            <button class="close-btn" type="button" aria-label="关闭" @click="emit('close')">×</button>
          </header>

          <!-- 变体切换 -->
          <div class="variant-toggle">
            <button
              type="button"
              class="variant-btn"
              :class="{ 'variant-btn--active': variant === 'light' }"
              @click="variant = 'light'"
            >
              浅色
            </button>
            <button
              type="button"
              class="variant-btn"
              :class="{ 'variant-btn--active': variant === 'dark' }"
              @click="variant = 'dark'"
            >
              深色
            </button>
          </div>

          <!-- 徽章预览(浅 / 深背景区分) -->
          <div
            class="badge-preview"
            :class="`badge-preview--${variant}`"
          >
            <img
              :src="badgeUrl(variant)"
              alt="浑晶创作徽章预览"
              class="badge-img"
            />
          </div>

          <!-- 说明文字 -->
          <p class="badge-explain">
            <strong>为什么贴这个徽章</strong>:让读者一眼看到这是 AI 协作创作 —
            <strong>透明</strong>不藏拙,<strong>版权</strong>归你本人(浑晶不索取),
            同人圈最容易接受的"AI 协作"姿态。
          </p>

          <!-- 操作按钮 -->
          <footer class="badge-footer">
            <button type="button" class="btn-cancel" @click="emit('close')">关闭</button>
            <button type="button" class="btn-download" @click="handleDownload">
              ↓ 下载 SVG
            </button>
          </footer>

          <!-- 未来扩展提示 -->
          <p class="future-hint">
            部署上线后会加 markdown 嵌入代码 / PNG 导出 / 多尺寸变体
          </p>
        </section>
      </div>
    </transition>
  </Teleport>
</template>

<style scoped>
.badge-overlay {
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

.badge-card {
  position: relative;
  width: 100%;
  max-width: 500px;
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  padding: var(--space-5);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  box-shadow: 0 16px 48px rgba(0, 0, 0, 0.18);
  z-index: var(--z-modal);
}

.badge-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
}
.header-text {
  flex: 1;
  min-width: 0;
}
.badge-title {
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}
.title-icon {
  font-size: var(--text-lg);
}
.badge-sub {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  margin: 4px 0 0;
  line-height: 1.6;
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
  flex-shrink: 0;
}
.close-btn:hover {
  color: var(--color-text);
  background: var(--color-bg-subtle);
}

/* 浅 / 深 切换 */
.variant-toggle {
  display: inline-flex;
  align-self: center;
  padding: 3px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-full);
  gap: 2px;
}
.variant-btn {
  padding: 4px var(--space-4);
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  border-radius: var(--radius-full);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.variant-btn:hover { color: var(--color-text); }
.variant-btn--active {
  background: var(--color-surface);
  color: var(--color-accent-text);
  font-weight: 500;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.06);
}

/* 徽章预览区 */
.badge-preview {
  padding: var(--space-5) var(--space-3);
  border-radius: var(--radius-md);
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100px;
  transition: background var(--duration-base) var(--ease-out);
}
.badge-preview--light {
  background: linear-gradient(135deg, #FAF8F5 0%, #EDE9FE 100%);
}
.badge-preview--dark {
  background: linear-gradient(135deg, #1F1F1E 0%, #2D1B69 100%);
}
.badge-img {
  max-width: 100%;
  height: auto;
  filter: drop-shadow(0 2px 8px rgba(0, 0, 0, 0.08));
}

.badge-explain {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.65;
  padding: var(--space-3) var(--space-4);
  background: var(--color-bg-subtle);
  border-radius: var(--radius-md);
  margin: 0;
}
.badge-explain strong {
  color: var(--color-accent-text);
  font-weight: 600;
}

.badge-footer {
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
.btn-download {
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
.btn-download:hover { filter: brightness(1.05); }

.future-hint {
  font-size: 10px;
  color: var(--color-text-subtle);
  text-align: center;
  margin: 0;
  font-style: italic;
}

</style>
