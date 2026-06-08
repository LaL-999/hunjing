<script setup lang="ts">
/**
 * SceneHintInput — Sprint 6.A2 路线图 #5(2026-05-23)边写边干预输入框
 *
 * 产品意图:
 *   AI 灵魂续写一气呵成 5-15 分钟,用户看进度只能干瞪眼;
 *   想干预只能等跑完后看产物不满意 → 重新推演(浪费 token)。
 *   本组件让用户在续写过程中给"下一幕"塞一条 hint(比如"让主角这里要爆发")
 *   → AI 下一幕开始生成时把这条 hint 纳入考虑。
 *
 * 行为:
 *   - 仅 evolution 模式 + sim 非终态显示(quick 30-60s 无干预必要)
 *   - 文本框 + 提交按钮;提交后 toast "将在下一幕生效" + 清空文本框
 *   - 后端可能返回 accepted=false(sim 状态已变为终态)→ toast info 提示
 *   - 失败 → toast error
 *
 * Props:
 *   simId: 必填
 *
 * Emits:
 *   submitted: 提交成功后 emit(父组件可决定是否埋点 / 反馈)
 */
import { computed, ref } from "vue";

import { api } from "../api/client";
import { ApiError } from "../api/types";
import { toast } from "../composables/useToast";
import HintHistoryModal from "./HintHistoryModal.vue";

interface SceneHintResponse {
  accepted: boolean;
  will_apply_to_scene: number | null;
  hint_preview: string | null;
}

const props = defineProps<{
  simId: string;
}>();

const emit = defineEmits<{
  (e: "submitted", hint: string): void;
}>();

const hintText = ref("");
const submitting = ref(false);

// hotfix(2026-06-01):提交记录 modal
const historyOpen = ref(false);

const MAX_LEN = 300;

const charCount = computed(() => hintText.value.length);
const overLimit = computed(() => charCount.value > MAX_LEN);
const canSubmit = computed(
  () => !submitting.value && hintText.value.trim().length > 0 && !overLimit.value,
);

async function handleSubmit() {
  if (!canSubmit.value) return;
  const text = hintText.value.trim();
  submitting.value = true;
  try {
    const resp = await api.post<SceneHintResponse>(
      `/simulations/${props.simId}/inject_scene_hint`,
      { hint: text },
    );
    if (resp.accepted) {
      const sceneNum = resp.will_apply_to_scene;
      toast.success(
        sceneNum != null
          ? `已收到,将在第 ${sceneNum} 幕生效`
          : "已收到,将在下一幕生效",
      );
      hintText.value = "";
      emit("submitted", text);
    } else {
      toast.info("推演已结束,无法干预");
    }
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : "提交失败,请稍后重试";
    toast.error(msg);
  } finally {
    submitting.value = false;
  }
}

function onKeydown(e: KeyboardEvent) {
  // Cmd/Ctrl + Enter 快捷提交(textarea 普通 Enter 应为换行)
  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
    e.preventDefault();
    void handleSubmit();
  }
}
</script>

<template>
  <section class="hint-input surface" aria-label="边写边干预输入框">
    <header class="hint-header">
      <span class="hint-icon" aria-hidden="true">✦</span>
      <div class="hint-titles">
        <h4 class="hint-title">边写边干预 — 给下一幕塞一句话</h4>
        <p class="hint-sub">
          比如"让主角这里要爆发" / "转向悲剧" / "下幕到酒馆冲突"。
          <strong>只影响下一幕</strong>,用完即清空。
        </p>
      </div>
    </header>

    <textarea
      v-model="hintText"
      class="hint-textarea"
      :maxlength="MAX_LEN + 50"
      placeholder="给 AI 一句话,影响下一幕怎么写..."
      rows="3"
      :disabled="submitting"
      @keydown="onKeydown"
    />

    <footer class="hint-footer">
      <span
        class="char-count mono"
        :class="{ 'is-over': overLimit }"
      >{{ charCount }} / {{ MAX_LEN }}</span>
      <!-- hotfix(2026-06-01):Ctrl+Enter 文案改成"提交记录"按钮,
           点击弹 modal 看完整 hint 历史(带"第几幕"标签) -->
      <button
        type="button"
        class="history-btn"
        title="查看本次创作中你提交过的所有干预指令(按幕排序)"
        @click="historyOpen = true"
      >提交记录</button>
      <button
        type="button"
        class="submit-btn"
        :disabled="!canSubmit"
        @click="handleSubmit"
      >
        {{ submitting ? "提交中…" : "提交 →" }}
      </button>
    </footer>
    <HintHistoryModal
      v-if="historyOpen"
      :sim-id="props.simId"
      @close="historyOpen = false"
    />
  </section>
</template>

<style scoped>
.hint-input {
  margin-top: var(--space-4);
  padding: var(--space-4);
  border-radius: var(--radius-lg);
  display: flex;
  flex-direction: column;
  gap: var(--space-3);
  border: 1px solid var(--color-accent-border);
}

.hint-header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.hint-icon {
  font-size: var(--text-lg);
  color: var(--color-accent-text);
  line-height: 1.2;
  flex-shrink: 0;
}

.hint-titles {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.hint-title {
  margin: 0;
  font-size: var(--text-base);
  font-weight: 600;
  color: var(--color-text);
}

.hint-sub {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.5;
}

.hint-sub strong {
  color: var(--color-text);
  font-weight: 600;
}

/* hotfix(2026-06-01):提交记录按钮 — 灰色不抢眼,只是入口 */
.history-btn {
  background: transparent;
  border: 0;
  padding: 2px 6px;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  cursor: pointer;
  text-decoration: underline;
  text-decoration-style: dotted;
  text-underline-offset: 3px;
  border-radius: var(--radius-sm);
}
.history-btn:hover {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
}

.hint-textarea {
  width: 100%;
  padding: var(--space-3);
  font-family: var(--font-sans);
  font-size: var(--text-sm);
  line-height: var(--line-relaxed);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  resize: vertical;
  min-height: 72px;
  transition: border-color var(--duration-fast) var(--ease-out),
              box-shadow var(--duration-fast) var(--ease-out);
}

.hint-textarea:focus {
  outline: none;
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.12);
}

.hint-textarea:disabled {
  background: var(--color-bg-subtle);
  color: var(--color-text-muted);
}

.hint-footer {
  display: flex;
  align-items: center;
  gap: var(--space-3);
}

.char-count {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}

.char-count.is-over {
  color: var(--color-danger);
}

.kbd-hint {
  font-size: 11px;
  color: var(--color-text-subtle);
  margin-left: auto;
}

.submit-btn {
  padding: 6px var(--space-4);
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: none;
  border-radius: var(--radius-md);
  transition: background var(--duration-fast) var(--ease-out);
}

.submit-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.submit-btn:disabled {
  opacity: 0.5;
}
</style>
