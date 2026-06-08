<script setup lang="ts">
/**
 * RelationshipTimeline — Sprint 6.A2 M1(2026-05-18)关系时间轴组件
 *
 * 产品意图(用户拍板"关系演化"愿景的核心 UI):
 *   - 关系不再是"贴标签",而是有时间维度的"剧本笔记"
 *   - 暗恋→情侣→仇敌 显时间轴 chip + 每阶段卡(类型/强度/锚点/触发事件/备注)
 *   - 用户可加新阶段(自动 phase_index=max+1 + 自动 current_phase_id 切到新阶段)
 *   - 续写时 director 按当前 phase 拉关系类型,而不是死板的 relationships.type
 *
 * 父组件(NodeEditDrawer 关系编辑区)调用:
 *   <RelationshipTimeline :relationship-id="rel.id" @phases-changed="onPhasesChanged" />
 */
import { computed, onMounted, ref, watch } from "vue";

import { api } from "../api/client";
import {
  ApiError,
  type CreateRelationshipPhaseRequest,
  PRESET_RELATIONSHIP_TYPES,
  type RelationshipPhase,
  type RelationshipStrength,
  type RelationshipType,
} from "../api/types";
import { toast } from "../composables/useToast";
import { confirm as confirmDialog } from "../composables/useConfirm";

const props = defineProps<{
  relationshipId: string;
  /** 当前 current_phase_id(父传),用于高亮显示 */
  currentPhaseId?: string | null;
}>();

/**
 * Sprint 6.A2 M1+++ bug fix(2026-05-18 用户反馈"编辑后页面闪现到顶部"):
 * 父组件原本收到 "phases-changed" 后 loadAll() 全量重拉 → reactive 数组替换 → DOM 重建
 * → 滚动位置先跳顶部再被 nextTick 恢复 → 用户感知"闪烁"。
 *
 * 修法:emit 时带具体变更数据(新 phase 数 + 新 current_phase_id + relationship id),
 * 父组件只更新这一条对应的 cache,**不全量重拉** → 列表不重建 → 无闪烁。
 *
 * payload.currentPhaseId 语义:
 *   - string  → 新的 current(addPhase auto_set=true / setCurrent)
 *   - null    → 显式清空(deletePhase 删到只剩 0 个;或 setCurrent(null))
 *   - undefined → 不动(用户没改 current,只是某 phase 字段被 patch / addPhase auto_set=false)
 */
type PhasesChangedPayload = {
  relationshipId: string;
  phaseCount: number;
  currentPhaseId: string | null | undefined;
};
const emit = defineEmits<{
  (e: "phases-changed", payload: PhasesChangedPayload): void;
}>();

const phases = ref<RelationshipPhase[]>([]);
const loading = ref(false);
const error = ref<string | null>(null);
const addingPhase = ref(false);

// 新 phase 表单
const newPhaseType = ref<RelationshipType>("朋友");
const newPhaseStrength = ref<RelationshipStrength>("moderate");
const newPhaseStartAnchor = ref("");
const newPhaseEndAnchor = ref("");
const newPhaseNotes = ref("");

// M7.G(2026-05-20):用统一预设清单(api/types.ts 维护单一源)
const PHASE_TYPES = PRESET_RELATIONSHIP_TYPES;
const STRENGTH_LABELS: Record<RelationshipStrength, string> = {
  strong: "强",
  moderately_strong: "中强",
  moderate: "中",
  moderately_weak: "中弱",
  weak: "弱",
};

async function loadPhases() {
  loading.value = true;
  error.value = null;
  try {
    phases.value = await api.get<RelationshipPhase[]>(
      `/relationships/${props.relationshipId}/phases`,
    );
  } catch (e) {
    error.value = e instanceof ApiError ? e.message : "加载阶段失败";
    phases.value = [];
  } finally {
    loading.value = false;
  }
}

async function addPhase() {
  if (addingPhase.value) return;
  addingPhase.value = true;
  try {
    const body: CreateRelationshipPhaseRequest = {
      type: newPhaseType.value,
      strength: newPhaseStrength.value,
      start_anchor: newPhaseStartAnchor.value.trim() || null,
      end_anchor: newPhaseEndAnchor.value.trim() || null,
      notes: newPhaseNotes.value.trim(),
      auto_set_current: true,
    };
    // 后端返回新创建的 phase(含 id)— 用它的 id 作 new current
    const newPhase = await api.post<RelationshipPhase>(
      `/relationships/${props.relationshipId}/phases`, body,
    );
    toast.success(`已加新阶段:${newPhaseType.value}(自动设为当前)`);
    // 重置表单
    newPhaseStartAnchor.value = "";
    newPhaseEndAnchor.value = "";
    newPhaseNotes.value = "";
    await loadPhases();
    // auto_set_current=true → new current = 刚加的 phase id
    emit("phases-changed", {
      relationshipId: props.relationshipId,
      phaseCount: phases.value.length,
      currentPhaseId: newPhase.id,
    });
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "添加阶段失败");
  } finally {
    addingPhase.value = false;
  }
}

async function deletePhase(phase: RelationshipPhase) {
  // Sprint 6.A2 FOCUS.8(2026-05-22):用 useConfirm 统一中央 modal 替代 native confirm
  // (.claude/skills 规则禁用 window.confirm,UI 风格对齐 ConfirmDialog)
  const ok = await confirmDialog({
    title: `删除阶段「${phase.type}」?`,
    message: phase.id === props.currentPhaseId
      ? `phase_index=${phase.phase_index} · 这是当前生效阶段,删除后会自动切到剩余最大 index 的阶段。`
      : `phase_index=${phase.phase_index}`,
    danger: true,
    confirmLabel: "删除",
  });
  if (!ok) return;
  const wasCurrent = phase.id === props.currentPhaseId;
  try {
    await api.delete(`/relationship_phases/${phase.id}`);
    toast.success("已删除");
    await loadPhases();
    // 后端 delete_phase 行为(对齐 relationship_phase_service.delete_phase):
    //   - 若被删的不是 current → current_phase_id 不变(传 undefined)
    //   - 若被删的是 current → 后端切到剩余最大 index 的 phase id;无剩余 → null
    let newCurrent: string | null | undefined = undefined;
    if (wasCurrent) {
      const remaining = phases.value;   // 已 loadPhases 刷新
      newCurrent = remaining.length > 0
        ? remaining[remaining.length - 1].id   // phase_index 最大的(列表已按 phase_index 升序)
        : null;
    }
    emit("phases-changed", {
      relationshipId: props.relationshipId,
      phaseCount: phases.value.length,
      currentPhaseId: newCurrent,
    });
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "删除失败");
  }
}

async function setCurrent(phase: RelationshipPhase) {
  if (phase.id === props.currentPhaseId) return;
  try {
    await api.patch(
      `/relationships/${props.relationshipId}`,
      { current_phase_id: phase.id },
    );
    toast.success(`已切换当前阶段:${phase.type}`);
    emit("phases-changed", {
      relationshipId: props.relationshipId,
      phaseCount: phases.value.length,
      currentPhaseId: phase.id,
    });
  } catch (e) {
    toast.error(e instanceof ApiError ? e.message : "切换失败");
  }
}

const isEmpty = computed(() => !loading.value && phases.value.length === 0);

onMounted(loadPhases);
watch(() => props.relationshipId, loadPhases);
</script>

<template>
  <section class="rel-timeline" :aria-busy="loading">
    <header class="tl-header">
      <h4 class="tl-title">
        <span class="tl-icon" aria-hidden="true">🕒</span>
        关系时间轴
        <span class="tl-count mono" v-if="!loading">
          {{ phases.length }} 阶段
        </span>
      </h4>
      <p class="tl-sub">
        关系会随剧情演化(暗恋→情侣→仇敌)。续写时 AI 按当前阶段拉关系类型。
      </p>
    </header>

    <!-- Loading -->
    <div v-if="loading" class="tl-state">加载中…</div>

    <!-- Error -->
    <div v-else-if="error" class="tl-state tl-error">
      {{ error }}
      <button class="ghost-btn" @click="loadPhases">重试</button>
    </div>

    <!-- 阶段列表(chip 时间轴 + 详情卡)-->
    <template v-else-if="!isEmpty">
      <!-- Chip 时间轴(顶部一行)-->
      <div class="tl-chips" role="list">
        <div
          v-for="phase in phases"
          :key="phase.id"
          class="tl-chip"
          :class="{ 'is-current': phase.id === currentPhaseId }"
          role="listitem"
          :title="
            `phase ${phase.phase_index}: ${phase.type}\n` +
            `${phase.start_anchor || '?'} → ${phase.end_anchor || '至今'}\n` +
            (phase.notes ? '备注:' + phase.notes : '')
          "
        >
          <span class="chip-idx mono">{{ phase.phase_index }}</span>
          <span class="chip-type">{{ phase.type }}</span>
          <span v-if="phase.id === currentPhaseId" class="chip-current">●</span>
        </div>
      </div>

      <!-- 阶段详情列表 -->
      <ul class="tl-list">
        <li
          v-for="phase in phases"
          :key="phase.id"
          class="tl-phase"
          :class="{ 'is-current': phase.id === currentPhaseId }"
        >
          <header class="phase-head">
            <span class="phase-index mono">phase {{ phase.phase_index }}</span>
            <span class="phase-type">{{ phase.type }}</span>
            <span class="phase-strength">
              [{{ STRENGTH_LABELS[phase.strength] }}]
            </span>
            <span v-if="phase.id === currentPhaseId" class="phase-badge">
              ● 当前生效
            </span>
          </header>
          <p class="phase-anchor">
            <span class="anchor-from">{{ phase.start_anchor || "未指定" }}</span>
            <span class="anchor-arrow" aria-hidden="true">→</span>
            <span class="anchor-to">{{ phase.end_anchor || "至今" }}</span>
          </p>
          <p v-if="phase.notes" class="phase-notes">备注:{{ phase.notes }}</p>
          <footer class="phase-foot">
            <button
              v-if="phase.id !== currentPhaseId"
              type="button"
              class="link-btn"
              @click="setCurrent(phase)"
            >设为当前</button>
            <button
              type="button"
              class="link-btn link-btn-danger"
              @click="deletePhase(phase)"
            >删除</button>
          </footer>
        </li>
      </ul>
    </template>

    <!-- Empty(理论上不会触发,因为后端老数据自动迁移成 phase[0])-->
    <div v-else class="tl-state tl-empty">
      <p>暂无阶段。这通常只在关系刚创建尚未首次访问时出现。</p>
    </div>

    <!-- 加新阶段表单 -->
    <details class="add-phase-section">
      <summary class="add-summary">+ 加新阶段</summary>
      <div class="add-form">
        <label class="form-row">
          <span class="form-label">类型</span>
          <select v-model="newPhaseType" class="form-input">
            <option v-for="t in PHASE_TYPES" :key="t" :value="t">{{ t }}</option>
            <!-- M7.G(2026-05-20)兜底:若当前 newPhaseType 是自定义不在预设列表,显示它 -->
            <option
              v-if="newPhaseType && !PHASE_TYPES.includes(newPhaseType)"
              :value="newPhaseType"
            >{{ newPhaseType }}(自定义)</option>
          </select>
        </label>

        <label class="form-row">
          <span class="form-label">强度</span>
          <select v-model="newPhaseStrength" class="form-input">
            <option
              v-for="(label, val) in STRENGTH_LABELS"
              :key="val"
              :value="val"
            >{{ label }}</option>
          </select>
        </label>

        <label class="form-row">
          <span class="form-label">起始锚点</span>
          <input
            v-model="newPhaseStartAnchor"
            type="text"
            class="form-input"
            maxlength="80"
            placeholder="例:第 8 章 / T2 / 自由文本"
          />
        </label>

        <label class="form-row">
          <span class="form-label">结束锚点</span>
          <input
            v-model="newPhaseEndAnchor"
            type="text"
            class="form-input"
            maxlength="80"
            placeholder="留空 = 持续到现在"
          />
        </label>

        <label class="form-row">
          <span class="form-label">备注</span>
          <textarea
            v-model="newPhaseNotes"
            class="form-input form-textarea"
            maxlength="500"
            rows="2"
            placeholder="为什么从前一阶段转到这一阶段?"
          ></textarea>
        </label>

        <div class="add-actions">
          <button
            type="button"
            class="primary-btn"
            :disabled="addingPhase"
            @click="addPhase"
          >
            {{ addingPhase ? "添加中…" : "✓ 添加(并设为当前)" }}
          </button>
        </div>
      </div>
    </details>
  </section>
</template>

<style scoped>
.rel-timeline {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding: var(--space-3);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
}

.tl-header {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.tl-title {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
}
.tl-icon { font-size: var(--text-base); }
.tl-count {
  padding: 2px 6px;
  font-size: 10px;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
  margin-left: 4px;
}
.tl-sub {
  margin: 0;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.5;
}

.tl-state {
  padding: var(--space-3);
  text-align: center;
  color: var(--color-text-muted);
  font-size: var(--text-xs);
}
.tl-error { color: var(--color-danger); }

/* Chip 时间轴 */
.tl-chips {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  padding: var(--space-2) 0;
}
.tl-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  font-size: var(--text-xs);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}
.tl-chip.is-current {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}
.chip-idx {
  font-size: 10px;
  color: var(--color-text-subtle);
}
.chip-type {
  font-weight: 500;
}
.chip-current {
  color: var(--color-accent);
}

/* 阶段详情列表 */
.tl-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.tl-phase {
  padding: var(--space-2) var(--space-3);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
}
.tl-phase.is-current {
  border-color: var(--color-accent);
  background: var(--color-accent-soft);
}
.phase-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.phase-index {
  font-size: 10px;
  color: var(--color-text-subtle);
}
.phase-type {
  font-size: var(--text-sm);
  font-weight: 600;
  color: var(--color-text);
}
.phase-strength {
  font-size: 10px;
  color: var(--color-text-muted);
}
.phase-badge {
  margin-left: auto;
  padding: 2px 6px;
  font-size: 10px;
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  border-radius: var(--radius-sm);
}
.phase-anchor {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 4px 0;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}
.anchor-arrow {
  color: var(--color-text-subtle);
}
.phase-notes {
  margin: 4px 0;
  font-size: var(--text-xs);
  color: var(--color-text);
  font-style: italic;
}
.phase-foot {
  display: flex;
  gap: var(--space-2);
  margin-top: 4px;
}
.link-btn {
  font-size: var(--text-xs);
  background: none;
  border: none;
  color: var(--color-accent-text);
  cursor: pointer;
  padding: 0;
  text-decoration: underline;
}
.link-btn:hover { color: var(--color-accent); }
.link-btn-danger { color: var(--color-danger); }
.link-btn-danger:hover { color: var(--color-danger); }

/* 加新阶段表单 */
.add-phase-section {
  margin-top: var(--space-2);
  padding-top: var(--space-2);
  border-top: 1px dashed var(--color-border);
}
.add-summary {
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  cursor: pointer;
  padding: 4px 0;
}
.add-summary:hover { color: var(--color-accent); }

.add-form {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  padding-top: var(--space-2);
}
.form-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.form-label {
  font-size: 10px;
  color: var(--color-text-muted);
}
.form-input {
  padding: 4px 8px;
  font-size: var(--text-xs);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  color: var(--color-text);
}
.form-textarea {
  resize: vertical;
  min-height: 40px;
  font-family: inherit;
}
.add-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 4px;
}
.primary-btn {
  padding: 4px var(--space-3);
  font-size: var(--text-xs);
  color: var(--color-text-on-accent);
  background: var(--color-accent);
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.primary-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.primary-btn:hover:not(:disabled) {
  background: var(--color-accent-hover);
}

.ghost-btn {
  padding: 2px 8px;
  font-size: var(--text-xs);
  color: var(--color-text);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  margin-left: var(--space-2);
}
</style>
