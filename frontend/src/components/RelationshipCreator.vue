<script setup lang="ts">
/**
 * RelationshipCreator — 3D 图谱内 Shift 连关系的创建对话框(Sprint 1.M.1.E)。
 *
 * 触发流程(由 ProjectGraphView 管理):
 *   1. 用户 Shift + 点角色 A → ProjectGraphView 记 source = A
 *   2. 用户 Shift + 点另一角色 B → 父组件弹此 dialog,from=A / to=B
 *   3. 用户填类型 + 描述 → 提交 → POST /relationships → 关 dialog + reload graph
 *
 * Props:
 *   open       boolean
 *   sourceId   string                   起始角色 id
 *   sourceName string                   起始角色名
 *   targetId   string                   目标角色 id
 *   targetName string                   目标角色名
 *   projectId  string                   POST 用
 *
 * Emits:
 *   close                                取消 / 提交后
 *   created                              创建成功 → 父组件 reload graph
 */
import { computed, ref, watch } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type CreateRelationshipRequest,
  PRESET_RELATIONSHIP_TYPES,
  type Relationship,
  type RelationshipType,
} from "../api/types";

const props = defineProps<{
  open: boolean;
  sourceId: string;
  sourceName: string;
  targetId: string;
  targetName: string;
  projectId: string;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "created"): void;
}>();

// Sprint 6.A2 M7.G(2026-05-20)关系类型自由化:
//   - dropdown 列 PRESET_RELATIONSHIP_TYPES 中 25 种常用预设
//   - 末尾加 "✎ 自定义…" 选项 → 切到 customMode + 显文本输入框
//   - 后端 DB CHECK 改为 length 1-20,前端校验 trim 后非空 + ≤ 20 字
const REL_TYPES: readonly string[] = PRESET_RELATIONSHIP_TYPES;

// type 字段当前选中值;customMode=true 时优先用 customType
const type = ref<RelationshipType>("朋友");
const customMode = ref(false);
const customType = ref("");
const description = ref("");
const creating = ref(false);
const errorMsg = ref<string | null>(null);

const sameNode = computed(() => props.sourceId === props.targetId);

/** 最终提交的 type 值:自定义模式用 customType,否则用 dropdown 选的 type */
const effectiveType = computed<string>(() =>
  customMode.value ? customType.value.trim() : type.value,
);

/** 自定义输入校验 — 1-20 字非空 */
const customTypeError = computed<string | null>(() => {
  if (!customMode.value) return null;
  const v = customType.value.trim();
  if (!v) return "请输入自定义关系名";
  if (v.length > 20) return "关系名最多 20 字";
  return null;
});

const submitDisabled = computed(
  () => creating.value || sameNode.value || !!customTypeError.value,
);

/** dropdown change handler — 用户选末尾"自定义"项时切到 customMode */
function onTypeChange(val: string) {
  if (val === "__custom__") {
    customMode.value = true;
    customType.value = "";
  } else {
    customMode.value = false;
    type.value = val;
  }
}

/** "回到预设"按钮 — 自定义输入旁的小链接,切回 dropdown 模式 */
function backToPreset() {
  customMode.value = false;
  customType.value = "";
  type.value = "朋友";
}

watch(
  () => props.open,
  (open) => {
    if (open) {
      type.value = "朋友";
      customMode.value = false;
      customType.value = "";
      description.value = "";
      errorMsg.value = null;
      creating.value = false;
    }
  },
);

async function handleCreate() {
  if (sameNode.value) {
    errorMsg.value = "不能给自己建立关系";
    return;
  }
  if (customTypeError.value) {
    errorMsg.value = customTypeError.value;
    return;
  }
  creating.value = true;
  errorMsg.value = null;
  try {
    const body: CreateRelationshipRequest = {
      source_id: props.sourceId,
      target_id: props.targetId,
      type: effectiveType.value,
      description: description.value.trim(),
    };
    await api.post<Relationship>(
      `/projects/${props.projectId}/relationships`,
      body,
    );
    emit("created");
    emit("close");
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : "创建关系失败";
  } finally {
    creating.value = false;
  }
}

function handleBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget && !creating.value) {
    emit("close");
  }
}

function handleKey(e: KeyboardEvent) {
  if (e.key === "Escape" && !creating.value) emit("close");
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
        aria-label="创建关系"
        @click="handleBackdrop"
        @keydown="handleKey"
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
            <h2 class="modal-title">连一条关系</h2>
            <p class="modal-subtitle">
              <strong>{{ sourceName }}</strong>
              <span class="arrow">→</span>
              <strong>{{ targetName }}</strong>
            </p>
          </header>

          <p v-if="sameNode" class="banner-warn">
            起点和终点是同一个角色,改选另一个节点
          </p>

          <form v-else class="form" @submit.prevent="handleCreate">
            <div class="field">
              <label class="field-label">
                关系类型
                <span class="field-hint">— 选预设或自定义</span>
              </label>

              <!-- M7.G(2026-05-20)预设 + 自定义 双模式 -->
              <template v-if="!customMode">
                <div class="type-row">
                  <label
                    v-for="t in REL_TYPES"
                    :key="t"
                    class="type-chip"
                    :class="{ 'is-active': type === t }"
                  >
                    <input
                      type="radio"
                      :value="t"
                      v-model="type"
                      class="sr-only"
                      :disabled="creating"
                    />
                    {{ t }}
                  </label>
                  <!-- 切到自定义模式的入口 -->
                  <button
                    type="button"
                    class="type-chip type-chip--custom"
                    :disabled="creating"
                    @click="onTypeChange('__custom__')"
                  >✎ 自定义…</button>
                </div>
              </template>

              <template v-else>
                <div class="custom-row">
                  <input
                    v-model="customType"
                    type="text"
                    maxlength="20"
                    placeholder="输入自定义关系,1-20 字(如「忘年交」「革命战友」「笔友」)"
                    class="text-input"
                    :disabled="creating"
                  />
                  <button
                    type="button"
                    class="ghost-link"
                    :disabled="creating"
                    @click="backToPreset"
                  >← 回到预设</button>
                </div>
                <p v-if="customTypeError" class="banner-warn">{{ customTypeError }}</p>
              </template>
            </div>

            <div class="field">
              <label for="rel-desc" class="field-label">
                关系描述 <span class="opt-mark">(可选)</span>
              </label>
              <input
                id="rel-desc"
                v-model="description"
                type="text"
                maxlength="200"
                placeholder="例:青梅竹马 / 师承一脉 / 江湖宿敌"
                class="text-input"
                autofocus
                :disabled="creating"
              />
            </div>

            <p v-if="errorMsg" class="banner-error">{{ errorMsg }}</p>

            <div class="actions">
              <button
                type="button"
                class="ghost-btn"
                :disabled="creating"
                @click="emit('close')"
              >取消</button>
              <button
                type="submit"
                class="primary-btn"
                :disabled="submitDisabled"
              >{{ creating ? "创建中…" : "创建关系" }}</button>
            </div>
          </form>
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
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}

.modal-card {
  width: 100%;
  max-width: 480px;
  padding: var(--space-8);
  position: relative;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-modal);
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
  background: transparent;
  border-radius: var(--radius-full);
}
.close-btn:hover:not(:disabled) {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.modal-header {
  margin-bottom: var(--space-5);
}
.modal-title {
  font-size: var(--text-xl);
  font-weight: 600;
  margin-bottom: var(--space-2);
}
.modal-subtitle {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.modal-subtitle strong {
  color: var(--color-text);
  font-weight: 500;
}
.arrow {
  color: var(--color-accent);
  font-size: var(--text-md);
}

.form {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
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
.opt-mark {
  font-weight: 400;
  color: var(--color-text-subtle);
  font-size: var(--text-xs);
}

.type-row {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-2);
}
.type-chip {
  padding: var(--space-2) var(--space-4);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  cursor: pointer;
  user-select: none;
  transition: all var(--duration-fast) var(--ease-out);
}
.type-chip:hover {
  border-color: var(--color-accent-border);
}
.type-chip.is-active {
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-color: var(--color-accent);
}

/* M7.G(2026-05-20)自定义入口 chip + 输入区 */
.type-chip--custom {
  font-weight: 500;
  color: var(--color-accent-text);
  background: transparent;
  border-style: dashed;
  border-color: var(--color-accent-border);
}
.type-chip--custom:hover:not(:disabled) {
  background: var(--color-accent-soft);
}
.type-chip--custom:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.field-hint {
  margin-left: var(--space-2);
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
  font-weight: 400;
}
.custom-row {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.ghost-link {
  align-self: flex-start;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  background: transparent;
  border: none;
  cursor: pointer;
  padding: 0;
}
.ghost-link:hover:not(:disabled) {
  color: var(--color-accent-text);
}
.ghost-link:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.text-input {
  width: 100%;
  padding: var(--space-3) var(--space-4);
  font-size: var(--text-base);
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  outline: none;
  transition: border-color var(--duration-fast) var(--ease-out);
}
.text-input:focus {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 3px rgba(124, 58, 237, 0.12);
}

.banner-warn {
  padding: var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-radius: var(--radius-md);
}
.banner-error {
  padding: var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-danger);
  background: var(--color-danger-soft);
  border-radius: var(--radius-md);
}

.actions {
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  margin-top: var(--space-2);
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
}
.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}
.primary-btn:disabled {
  background: var(--color-text-subtle);
  cursor: not-allowed;
}

.sr-only {
  position: absolute;
  width: 1px; height: 1px;
  padding: 0; margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

</style>
