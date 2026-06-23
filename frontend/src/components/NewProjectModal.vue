<script setup lang="ts">
/**
 * NewProjectModal — 新建项目模态。
 *
 * Sprint 2.A.F:加 mode prop 让用户在创建瞬间锚定"哪种创作态"。
 * 顶部加态 chip + 一句话定义 + 流程提示;创建成功后中间态额外提示先上传文件。
 *
 * Props:
 *   open  boolean
 *   mode  'initial'|'middle'(默认 initial;ending/cyclic 由父端拦在 unavailable 不入这里)
 *
 * Emits:
 *   close
 *   created(project, mode)  父组件根据 mode 决定 router push 后的引导(中间态 toast 提示上传)
 */
import { computed, ref, watch } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type CreateProjectRequest,
  type Project,
  type ProjectMode,
  type QuotaExceededDetail,
} from "../api/types";
import { useUpgradeModal } from "../composables/useUpgradeModal";

const props = withDefaults(
  defineProps<{
    open: boolean;
    mode?: ProjectMode;
  }>(),
  { mode: "initial" },
);

const emit = defineEmits<{
  (e: "close"): void;
  (e: "created", project: Project, mode: ProjectMode): void;
}>();

const upgradeModal = useUpgradeModal();

const name = ref("");
const creating = ref(false);
const errorMessage = ref<string | null>(null);

// 2026-06-05 简化:删掉初始态的"小说类型" + "题材标签"字段 —
//   - 项目内 ProjectView 已有更专业的「故事脊柱 / 视角扩展 / 世界观 / 角色」等
//     真实影响创作的字段,且都有 AI 推断
//   - 这俩字段重复 + 不准确(用户随手选的"玄幻"和项目内 AI 推断的结果可能冲突)
//   - 4 个 mode(initial/middle/end/cycle)统一只填项目名,后续 AI 自动识别

watch(
  () => props.open,
  (open) => {
    if (open) {
      name.value = "";
      errorMessage.value = null;
    }
  },
);

const canCreate = computed(
  () => !creating.value && name.value.trim().length > 0,
);

async function handleCreate() {
  if (!canCreate.value) return;
  creating.value = true;
  errorMessage.value = null;
  try {
    // 4 mode 统一:type='generic',tags 留空,后续 AI 自动识别
    const body: CreateProjectRequest = {
      name: name.value.trim(),
      type: "generic",
      mode: props.mode,
    };
    const project = await api.post<Project>("/projects", body);
    emit("created", project, props.mode);
    // 2026-06-01:广播 project:created → 项目列表(AppSidebar)实时显示新项目
    import("../stores/events").then(({ useEventBus }) => {
      try {
        useEventBus().emit("project:created", { project_id: project.id });
      } catch (err) {
        if (import.meta.env.DEV) {
          // eslint-disable-next-line no-console
          console.warn("[NewProjectModal] emit project:created failed:", err);
        }
      }
    });
  } catch (e) {
    if (e instanceof ApiError && e.code === "QUOTA_EXCEEDED") {
      // 项目数超限 → 关闭新建 modal,弹升级 modal
      emit("close");
      upgradeModal.open(e.detail as QuotaExceededDetail);
      return;
    }
    errorMessage.value = e instanceof ApiError ? e.message : "创建失败";
  } finally {
    creating.value = false;
  }
}

function handleBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget && !creating.value) {
    emit("close");
  }
}

// UI 优化(2026-05-21 九轮):删除 tagline + flowHint — 首页 4 态卡已说明,modal 内重复就是噪音
const MODE_META: Record<ProjectMode, { label: string }> = {
  initial:    { label: "初始态" },
  middle:     { label: "中间态" },
  end:        { label: "末尾态" },
  cycle:      { label: "漫创态" },
  // 剧创态 / 更多有各自的入口,不经本 modal 创建;此处仅补全 ProjectMode 键。
  screenplay: { label: "剧创态" },
  more:       { label: "更多" },
};

const modeMeta = computed(() => MODE_META[props.mode]);
</script>

<template>
  <transition name="modal-fade">
    <div
      v-if="open"
      class="modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="新建项目"
      @click="handleBackdrop"
      @keydown.esc="emit('close')"
    >
      <div class="modal-card surface">
        <button
          class="close-btn"
          type="button"
          aria-label="关闭"
          :disabled="creating"
          @click="emit('close')"
        >×</button>

        <header class="modal-header">
          <div class="header-top">
            <h2 class="modal-title">新建项目</h2>
            <span class="mode-chip">{{ modeMeta.label }}</span>
          </div>
          <!-- UI 优化(2026-05-21 九轮):删除 tagline + flowHint — 首页 4 态卡已说明,寸土寸金 -->
        </header>

        <form class="form" @submit.prevent="handleCreate">
          <div class="field">
            <label for="np-name" class="field-label">项目名</label>
            <input
              id="np-name"
              v-model="name"
              type="text"
              maxlength="30"
              placeholder="如:江湖夜雨"
              autofocus
              class="text-input"
              :disabled="creating"
            />
          </div>

          <!-- 2026-06-05:统一提示 — 作品类型 / 题材交给 AI 推断,
               用户在项目内的「故事脊柱 / 视角扩展 / 世界观」更精准影响创作 -->
          <p class="ai-fill-hint">
            <span class="hint-mark">✦</span>
            <span>作品类型 / 题材标签将由 AI 解析自动识别 — 这样推演结果更精准</span>
          </p>

          <p v-if="errorMessage" class="error-msg">{{ errorMessage }}</p>

          <div class="actions">
            <button
              type="button"
              class="ghost-btn"
              :disabled="creating"
              @click="emit('close')"
            >
              取消
            </button>
            <button
              type="submit"
              class="primary-btn"
              :disabled="!canCreate"
            >
              {{ creating ? "创建中…" : "创建并进入" }}
            </button>
          </div>
        </form>
      </div>
    </div>
  </transition>
</template>

<style scoped>
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(31, 31, 30, 0.4);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  max-width: 480px;
  /* min-height 撑住短内容,form 用 flex 自然居中(2026-06-05 简化后所有 mode 只填项目名) */
  min-height: 360px;
  padding: var(--space-8);
  position: relative;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-modal);
  display: flex;
  flex-direction: column;
}

.close-btn {
  position: absolute;
  top: var(--space-3);
  right: var(--space-3);
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-muted);
  border-radius: var(--radius-full);
  transition: all var(--duration-fast) var(--ease-out);
}

.close-btn:hover:not(:disabled) {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.modal-header {
  margin-bottom: var(--space-6);
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.header-top {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.modal-title {
  font-size: var(--text-xl);
  font-weight: 600;
  margin: 0;
}

.mode-chip {
  padding: 2px var(--space-2);
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
  letter-spacing: 0.04em;
}

/* UI 优化(2026-05-21 九轮):删除 .modal-subtitle / .flow-hint 死 CSS(对应模板已删) */

/* 非初始态:AI 自动识别 type/tags 的说明条(替代手填表单)*/
.ai-fill-hint {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-md);
  line-height: 1.6;
  margin: 0;
}
.hint-mark {
  font-size: var(--text-sm);
  flex-shrink: 0;
}

.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
  /* UI 优化(2026-05-21 九轮):form 填满 modal-card 剩余空间;actions 自然推到底部 */
  flex: 1;
}

.field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}

.field-label {
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
}

.text-input {
  width: 100%;
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-base);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  outline: none;
  transition: border-color var(--duration-fast) var(--ease-out),
              box-shadow var(--duration-fast) var(--ease-out);
}

.text-input:focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.12);
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  /* UI 优化(2026-05-21 九轮):用 margin-top: auto 把按钮推到 modal 底部,内容少时不挤在中间 */
  margin-top: auto;
  padding-top: var(--space-4);
}

.ghost-btn {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  background: transparent;
  border-radius: var(--radius-md);
}

.ghost-btn:hover:not(:disabled) {
  color: var(--color-text);
  background: var(--color-surface-hover);
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

.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.primary-btn:disabled {
  background: var(--color-text-subtle);
  cursor: not-allowed;
}

.error-msg {
  padding: var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-radius: var(--radius-md);
}

</style>
