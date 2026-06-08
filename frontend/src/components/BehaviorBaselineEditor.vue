<!--
  Sprint 6.A2 FOCUS.6(2026-05-22):BehaviorBaselineEditor — behavior_baseline 4 维独立编辑器。

  背景:M4.3 / INIT.3 加的 behavior_baseline 4 维(speech_register / emotional_intensity /
  moral_compass / out_of_baseline_examples)只在初始态 ProjectView 角色 drawer 显示。中间态 /
  末尾态的 ProtagonistWall 卡片 + 3D 图谱 drawer 不显示,但 AI 自检 + AI 角色对焦都依赖这 4 维 →
  "后端在跑,UI 看不到"严重违背"透明 AI 协作"价值观。本组件抽出可在 3 处复用。

  Props / Events:
    v-model:baseline   BehaviorBaseline | null   (input + emit('update:baseline', ...))
    @save              用户改完(失焦 / change)时触发,父组件用来调 PATCH /characters/:id
    :disabled?         禁用所有输入(loading 状态用)
    :default-open?     <details> 是否默认展开,默认 false(中间/末尾态展开较占空间,折叠更合适)
    :variant?          'drawer' | 'card'   drawer = ProjectView 抽屉风格(留 padding);card = 主角墙卡片紧凑
-->
<script setup lang="ts">
import { computed } from "vue";
import type { BehaviorBaseline, SpeechRegister, MoralCompass } from "../api/types";
import Icon from "./Icon.vue";

interface Props {
  baseline: BehaviorBaseline | null | undefined;
  disabled?: boolean;
  defaultOpen?: boolean;
  variant?: "drawer" | "card";
}

const props = withDefaults(defineProps<Props>(), {
  disabled: false,
  defaultOpen: false,
  variant: "drawer",
});

const emit = defineEmits<{
  (e: "update:baseline", value: BehaviorBaseline | null): void;
  (e: "save"): void;
}>();

/** Internal: derive 4 个字段的本地 v-model,每次改动后构造新 baseline 并 emit update */
const speechRegister = computed({
  get: () => (props.baseline?.speech_register ?? "") as SpeechRegister | "",
  set: (v) => updateField("speech_register", v || null),
});
const emotionalIntensity = computed({
  get: () => props.baseline?.emotional_intensity ?? null,
  set: (v) => updateField("emotional_intensity", v),
});
const moralCompass = computed({
  get: () => (props.baseline?.moral_compass ?? "") as MoralCompass | "",
  set: (v) => updateField("moral_compass", v || null),
});

// P0G.2(2026-05-24):outRaw computed 已移除 — out_of_baseline_examples 字段彻底删除
// 原内容已通过 migration 062 合并到 no_go_list(参见后端 schemas/character.py)

function updateField<K extends keyof BehaviorBaseline>(
  key: K,
  value: BehaviorBaseline[K] | null,
) {
  const next: BehaviorBaseline = {
    speech_register: props.baseline?.speech_register ?? null,
    emotional_intensity: props.baseline?.emotional_intensity ?? null,
    moral_compass: props.baseline?.moral_compass ?? null,
    out_of_baseline_examples: props.baseline?.out_of_baseline_examples ?? [],
  };
  (next as any)[key] = value;
  // 4 字段全空 → 整体置 null(对齐后端"老数据"语义,consistency_checker fallback 用 personality)
  const allEmpty =
    !next.speech_register &&
    next.emotional_intensity == null &&
    !next.moral_compass &&
    (!next.out_of_baseline_examples || next.out_of_baseline_examples.length === 0);
  emit("update:baseline", allEmpty ? null : next);
}

function onChangeSave() {
  emit("save");
}
</script>

<template>
  <details
    class="bb-editor"
    :class="[`bb-editor--${variant}`]"
    :open="defaultOpen"
  >
    <summary class="bb-summary">
      <Icon name="settings" :size="14" class="bb-icon" />
      <span class="bb-title">AI 自检 · 行为基线</span>
      <span class="bb-hint">高级配置 · 防角色越级</span>
    </summary>

    <div class="bb-grid">
      <label class="bb-field">
        <span class="bb-label">
          语气登记
          <span class="bb-sub">日常说话基调</span>
        </span>
        <select
          v-model="speechRegister"
          class="bb-select"
          :disabled="disabled"
          @change="onChangeSave"
        >
          <option value="">— 未设(AI 自由发挥)—</option>
          <option value="卑微">卑微(讨好型,常自贬)</option>
          <option value="平和">平和(理性,情绪平稳)</option>
          <option value="强硬">强硬(果断,不让步)</option>
          <option value="恶意">恶意(敌意,主动攻击)</option>
        </select>
      </label>

      <label class="bb-field">
        <span class="bb-label">
          情绪强度基线
          <span class="bb-sub">
            {{ emotionalIntensity === null ? "未设" : `${emotionalIntensity} / 10` }}
            · 本轮发言 ±2 内浮动
          </span>
        </span>
        <div class="bb-slider-row">
          <input
            type="range"
            min="0"
            max="10"
            step="1"
            :value="emotionalIntensity ?? 5"
            class="bb-slider"
            :disabled="disabled"
            @input="emotionalIntensity = Number(($event.target as HTMLInputElement).value)"
            @change="onChangeSave"
          />
          <button
            v-if="emotionalIntensity !== null"
            type="button"
            class="bb-clear-btn"
            title="清空(让 AI 自由判断)"
            :disabled="disabled"
            @click.stop="() => { emotionalIntensity = null; onChangeSave(); }"
          >×</button>
        </div>
      </label>

      <label class="bb-field">
        <span class="bb-label">
          道德罗盘
          <span class="bb-sub">价值取向</span>
        </span>
        <select
          v-model="moralCompass"
          class="bb-select"
          :disabled="disabled"
          @change="onChangeSave"
        >
          <option value="">— 未设(AI 自由发挥)—</option>
          <option value="善">善(利他,守原则)</option>
          <option value="灰">灰(实用,情境而定)</option>
          <option value="恶">恶(自私,可越界)</option>
        </select>
      </label>

      <!--
        P0F.3(2026-05-24)— "雷区" UI 字段已隐藏。
        原因:与上层"禁忌(我绝对不会做的事)"(no_go_list)字段语义完全重叠,
            两个字段都是"角色绝不会做的事",用户看着混乱。
        现状:out_of_baseline_examples schema 字段保留(向后兼容已存数据 + 旧 sim 引用),
            但 UI 不再让用户编辑;新建/AI 补全档案统一写到 no_go_list。
        consistency_checker 同时读 no_go_list + out_of_baseline_examples,行为不变。
      -->
    </div>
  </details>
</template>

<style scoped>
.bb-editor {
  background: var(--color-bg-subtle);
  border: 1px dashed var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
}
.bb-editor--card {
  /* 主角墙卡片内紧凑变体 */
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
}

.bb-summary {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  cursor: pointer;
  list-style: none;
  user-select: none;
}
.bb-summary::-webkit-details-marker {
  display: none;
}
.bb-icon {
  color: var(--color-text-muted);
}
.bb-title {
  font-weight: 600;
  color: var(--color-text);
}
.bb-hint {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.bb-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: var(--space-3);
  margin-top: var(--space-3);
}

.bb-field {
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
}
.bb-field--full {
  grid-column: 1 / -1;
}

.bb-label {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: var(--text-sm);
  font-weight: 500;
  color: var(--color-text);
}
.bb-sub {
  font-size: var(--text-xs);
  font-weight: 400;
  color: var(--color-text-muted);
}

.bb-select,
.bb-textarea {
  width: 100%;
  padding: var(--space-2) var(--space-3);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  font-size: var(--text-sm);
  color: var(--color-text);
}
.bb-select:focus,
.bb-textarea:focus {
  outline: 2px solid var(--color-accent-border);
  outline-offset: -1px;
  border-color: var(--color-accent);
}
.bb-textarea {
  resize: vertical;
  min-height: 56px;
  font-family: inherit;
  line-height: 1.6;
}
.bb-select:disabled,
.bb-textarea:disabled {
  background: var(--color-bg-subtle);
  color: var(--color-text-muted);
  cursor: not-allowed;
}

.bb-slider-row {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}
.bb-slider {
  flex: 1;
  accent-color: var(--color-accent);
}
.bb-clear-btn {
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  color: var(--color-text-muted);
  cursor: pointer;
  font-size: var(--text-sm);
  line-height: 1;
}
.bb-clear-btn:hover:not(:disabled) {
  color: var(--color-danger);
  border-color: var(--color-danger);
}
.bb-clear-btn:disabled {
  cursor: not-allowed;
  opacity: 0.5;
}
</style>
