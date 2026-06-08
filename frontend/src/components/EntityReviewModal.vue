<!--
  Sprint 6.A2 FOCUS.10(2026-05-22):人机协同审核 Modal

  抽完 graph 后,worker 暂停在 entities_pending_review,前端弹此 modal:
    - 展示 AI 抽出的所有 PERSON 列表(name / description / 出场次数)
    - 用户能 × 删除某个误抽的角色
    - 用户能 + 手动补漏抽的角色(name 必填,description 可选)
    - 显示后端检测到的"漏抽嫌疑" candidate(黄色提示),用户可一键补
    - 点"通过"→ POST approve_entities → 后端续跑 profile 阶段
    - 点"全部接受"→ 等同空 approve,不删不加

  设计:中央 modal + 背景虚化(对齐 ConfirmDialog 风格)。
-->
<script setup lang="ts">
import { ref, computed } from "vue";
import { api } from "../api/client";
import { ApiError } from "../api/types";
import { toast } from "../composables/useToast";
import type { PendingReviewPerson } from "../composables/useExtractJob";

interface Props {
  open: boolean;
  jobId: string;
  persons: PendingReviewPerson[];
  missedPersons: string[];
}

const props = defineProps<Props>();

const emit = defineEmits<{
  (e: "approved"): void;   // 用户批准成功,父组件该刷新 job 状态
  (e: "close"): void;
}>();

const submitting = ref(false);
// 用户标记为"删除"的角色 name 集合
const removedNames = ref<Set<string>>(new Set());
// 用户手动补的角色(name / description)
const addedPersons = ref<Array<{ name: string; description: string }>>([]);
// 新增 form 的临时输入
const newName = ref("");
const newDescription = ref("");

function isRemoved(name: string): boolean {
  return removedNames.value.has(name);
}

function toggleRemove(name: string) {
  if (removedNames.value.has(name)) {
    removedNames.value.delete(name);
  } else {
    removedNames.value.add(name);
  }
  // Vue 3 ref<Set> 不会自动响应 add/delete → 显式触发
  removedNames.value = new Set(removedNames.value);
}

function addPerson() {
  const name = newName.value.trim();
  if (!name) {
    toast.warning("角色名不能为空");
    return;
  }
  // 不允许与已有 / 已添加重名
  const existing = new Set([
    ...props.persons.map((p) => p.name),
    ...addedPersons.value.map((p) => p.name),
  ]);
  if (existing.has(name)) {
    toast.warning(`「${name}」已存在`);
    return;
  }
  addedPersons.value.push({ name, description: newDescription.value.trim() });
  newName.value = "";
  newDescription.value = "";
}

function removeAddedAt(idx: number) {
  addedPersons.value.splice(idx, 1);
}

/** 一键补漏抽 candidate */
function quickAddMissed(name: string) {
  const existing = new Set([
    ...props.persons.map((p) => p.name),
    ...addedPersons.value.map((p) => p.name),
  ]);
  if (existing.has(name)) return;
  addedPersons.value.push({
    name,
    description: "(从原作 description 反向扫出的漏抽嫌疑,待用户补充)",
  });
}

const finalCount = computed(() => {
  return props.persons.length - removedNames.value.size + addedPersons.value.length;
});

async function submit() {
  if (submitting.value) return;
  submitting.value = true;
  try {
    await api.post(`/extract_jobs/${props.jobId}/approve_entities`, {
      added_persons: addedPersons.value,
      removed_names: Array.from(removedNames.value),
    });
    toast.success(`已批准 — AI 开始为 ${finalCount.value} 位角色生成档案`);
    emit("approved");
    emit("close");
  } catch (e) {
    toast.error(
      e instanceof ApiError ? `批准失败:${e.message}` : "批准失败",
    );
  } finally {
    submitting.value = false;
  }
}

</script>

<template>
  <Teleport to="body">
    <Transition name="modal-fade">
      <div v-if="open" class="erm-overlay" @click.self="emit('close')">
        <div class="erm-modal" role="dialog" aria-modal="true" aria-labelledby="erm-title">
          <header class="erm-header">
            <h3 id="erm-title" class="erm-title">
              人机协同审核 · AI 抽出 {{ persons.length }} 位角色
            </h3>
            <button type="button" class="erm-close" @click="emit('close')" aria-label="关闭">×</button>
          </header>

          <p class="erm-hint">
            AI 可能漏抽 / 误抽角色。审核后 AI 才会继续生成详细档案 —
            点 <span class="dim-tag">×</span> 删掉误抽,点 <span class="dim-tag">+</span> 补漏抽。
          </p>

          <!-- 漏抽嫌疑(后端 description 反向扫描)-->
          <div v-if="missedPersons.length > 0" class="erm-missed">
            <span class="erm-missed-icon">⚠</span>
            <span class="erm-missed-label">可能漏抽:</span>
            <button
              v-for="name in missedPersons"
              :key="`missed-${name}`"
              type="button"
              class="erm-missed-chip"
              :title="`点击 + 添加「${name}」`"
              @click="quickAddMissed(name)"
            >+ {{ name }}</button>
          </div>

          <!-- AI 抽出的 PERSON 列表 -->
          <ul class="erm-list">
            <li
              v-for="p in persons"
              :key="`p-${p.name}`"
              class="erm-item"
              :class="{ 'erm-item--removed': isRemoved(p.name) }"
            >
              <div class="erm-item-main">
                <span class="erm-name">{{ p.name }}</span>
                <span class="erm-meta">出场 {{ p.text_occurrences }} 次</span>
                <span v-if="p.aliases.length > 0" class="erm-aliases">
                  别名:{{ p.aliases.join(" / ") }}
                </span>
              </div>
              <div v-if="p.description" class="erm-desc">{{ p.description }}</div>
              <button
                type="button"
                class="erm-remove-btn"
                :title="isRemoved(p.name) ? '撤销删除' : '标记删除'"
                @click="toggleRemove(p.name)"
              >
                {{ isRemoved(p.name) ? '↩' : '×' }}
              </button>
            </li>
          </ul>

          <!-- 用户添加的角色 — 浅紫色背景就够区分,不需要 + flag / "手动添加"文案 -->
          <ul v-if="addedPersons.length > 0" class="erm-list erm-added">
            <li
              v-for="(ap, idx) in addedPersons"
              :key="`added-${idx}-${ap.name}`"
              class="erm-item erm-item--added"
            >
              <div class="erm-item-main">
                <span class="erm-name">{{ ap.name }}</span>
              </div>
              <div v-if="ap.description" class="erm-desc">{{ ap.description }}</div>
              <button
                type="button"
                class="erm-remove-btn"
                title="移除这个添加"
                @click="removeAddedAt(idx)"
              >×</button>
            </li>
          </ul>

          <!-- 手动添加 form -->
          <div class="erm-add-form">
            <input
              v-model="newName"
              class="erm-input erm-input-name"
              placeholder="补漏抽角色名(必填)"
              :disabled="submitting"
              @keydown.enter="addPerson"
            />
            <input
              v-model="newDescription"
              class="erm-input erm-input-desc"
              placeholder="角色描述(可选)"
              :disabled="submitting"
              @keydown.enter="addPerson"
            />
            <button
              type="button"
              class="erm-add-btn"
              :disabled="submitting || !newName.trim()"
              @click="addPerson"
            >+ 添加</button>
          </div>

          <footer class="erm-footer">
            <span class="erm-summary mono">
              最终 {{ finalCount }} 角色(原 {{ persons.length }} · 删 {{ removedNames.size }} · 加 {{ addedPersons.length }})
            </span>
            <div class="erm-actions">
              <button
                type="button"
                class="erm-btn erm-btn-primary"
                :disabled="submitting"
                @click="submit"
              >{{ submitting ? "批准中…" : "✓ 通过,继续生成档案" }}</button>
            </div>
          </footer>
        </div>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.erm-overlay {
  position: fixed;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(0, 0, 0, 0.4);
  backdrop-filter: blur(4px);
  z-index: var(--z-modal, 100);
  padding: var(--space-4);
}
.erm-modal {
  width: 100%;
  max-width: 640px;
  max-height: 85vh;
  display: flex;
  flex-direction: column;
  background: var(--color-surface);
  border-radius: var(--radius-lg);
  box-shadow: 0 10px 40px rgba(0, 0, 0, 0.18);
  padding: var(--space-5) var(--space-5) var(--space-4);
  overflow: hidden;
}
.erm-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  margin-bottom: var(--space-2);
}
.erm-title {
  margin: 0;
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
}
.erm-close {
  width: 28px;
  height: 28px;
  border: none;
  background: none;
  font-size: var(--text-xl);
  color: var(--color-text-muted);
  cursor: pointer;
  border-radius: var(--radius-sm);
  line-height: 1;
}
.erm-close:hover {
  background: var(--color-bg-subtle);
  color: var(--color-text);
}
.erm-hint {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: 1.6;
  margin: 0 0 var(--space-3);
}
.dim-tag {
  display: inline-block;
  padding: 0 6px;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  font-family: monospace;
}

.erm-missed {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-2) var(--space-3);
  margin-bottom: var(--space-3);
  background: rgba(217, 119, 6, 0.06);
  border: 1px dashed var(--color-warning);
  border-radius: var(--radius-md);
  font-size: var(--text-sm);
}
.erm-missed-icon {
  color: var(--color-warning);
  font-weight: 600;
}
.erm-missed-label {
  color: var(--color-text);
  font-weight: 500;
}
.erm-missed-chip {
  padding: 2px 8px;
  background: var(--color-warning-soft);
  color: var(--color-warning);
  border: 1px solid var(--color-warning);
  border-radius: var(--radius-sm);
  font-size: var(--text-xs);
  cursor: pointer;
}
.erm-missed-chip:hover {
  background: var(--color-warning);
  color: var(--color-surface);
}

.erm-list {
  list-style: none;
  margin: 0;
  padding: 0;
  overflow-y: auto;
  flex: 1;
  min-height: 80px;
  max-height: 40vh;
}
.erm-added {
  flex: 0 0 auto;
  border-top: 1px dashed var(--color-border);
  margin-top: var(--space-2);
  padding-top: var(--space-2);
}
.erm-item {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: var(--space-2) var(--space-3);
  padding-right: 40px;
  border-bottom: 1px solid var(--color-border);
  transition: background 120ms, opacity 120ms;
}
.erm-item:last-child {
  border-bottom: none;
}
.erm-item--removed {
  background: var(--color-danger-soft);
  opacity: 0.55;
  text-decoration: line-through;
}
.erm-item--added {
  background: rgba(124, 58, 237, 0.04);
}
.erm-item-main {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: var(--space-2);
  font-size: var(--text-sm);
}
.erm-name {
  font-weight: 600;
  color: var(--color-text);
}
.erm-meta {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.erm-aliases {
  font-size: var(--text-xs);
  color: var(--color-text-subtle);
}
.erm-desc {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.5;
}
.erm-remove-btn {
  position: absolute;
  top: 50%;
  right: var(--space-2);
  transform: translateY(-50%);
  width: 24px;
  height: 24px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text-muted);
  font-size: var(--text-base);
  cursor: pointer;
  border-radius: var(--radius-sm);
  line-height: 1;
}
.erm-remove-btn:hover {
  color: var(--color-danger);
  border-color: var(--color-danger);
}
.erm-item--removed .erm-remove-btn {
  color: var(--color-accent);
}

.erm-add-form {
  display: flex;
  gap: var(--space-2);
  padding: var(--space-3) 0 var(--space-2);
  border-top: 1px solid var(--color-border);
  margin-top: var(--space-2);
}
.erm-input {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  background: var(--color-surface);
}
.erm-input:focus {
  outline: 2px solid var(--color-accent-border);
  outline-offset: -1px;
  border-color: var(--color-accent);
}
.erm-input-name { flex: 0 0 35%; }
.erm-input-desc { flex: 1; }
.erm-add-btn {
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-accent-border);
  background: var(--color-accent-soft);
  color: var(--color-accent-text);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  cursor: pointer;
  font-weight: 500;
}
.erm-add-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.erm-add-btn:not(:disabled):hover {
  background: var(--color-accent);
  color: var(--color-surface);
}

.erm-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding-top: var(--space-3);
  border-top: 1px solid var(--color-border);
  margin-top: var(--space-2);
}
.erm-summary {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.erm-actions {
  display: flex;
  gap: var(--space-2);
}
.erm-btn {
  padding: var(--space-2) var(--space-4);
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text);
  border-radius: var(--radius-sm);
  font-size: var(--text-sm);
  cursor: pointer;
  font-weight: 500;
}
.erm-btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.erm-btn-secondary:hover:not(:disabled) {
  background: var(--color-bg-subtle);
}
.erm-btn-primary {
  border-color: var(--color-accent);
  background: var(--color-accent);
  color: var(--color-surface);
}
.erm-btn-primary:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

</style>
