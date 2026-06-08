<script setup lang="ts">
/**
 * WorldBaselineEditor — INIT.5(2026-05-21)初始态世界观 6 维编辑
 *
 * 给"从零创造"的用户一个明确的世界观锚点:
 *   - genre 体裁(如"东方玄幻"/"现代都市悬疑")
 *   - setting 背景设定(如"修仙九大宗门时代"/"赛博 2087")
 *   - magic_system 力量体系(如"灵气修炼分九境"/"无超能力")
 *   - time_axis 时间轴(如"架空古代"/"近未来 2050")
 *   - tone 基调(如"热血燃情"/"冷峻悬疑")
 *   - free_form 自由(其他设定细节)
 *
 * 数据流:
 *   - props.initial: 父组件传入的 project.world_baseline(Record<string, string>)
 *   - 用户改了某维 + blur → emit('save', { [dim]: newValue })
 *   - 父组件调 PATCH /projects/:id 落库;响应回来更新 project.world_baseline
 *
 * 折叠态默认收起,与 ProtagonistWall / SceneGraph 一致
 */
import { computed, ref, watch } from "vue";

import Icon from "./Icon.vue";

const props = defineProps<{
  initial: Record<string, string>;
}>();

const emit = defineEmits<{
  (e: "save", patch: Record<string, string>): void;
}>();

const sectionCollapsed = ref(true);

interface DimDef {
  key: string;
  label: string;
  placeholder: string;
}
const DIMS: DimDef[] = [
  { key: "genre", label: "体裁", placeholder: "如:东方玄幻 / 现代都市悬疑 / 赛博朋克" },
  { key: "setting", label: "背景设定", placeholder: "如:修仙九大宗门时代 / 末日核冬 / 民国军阀混战" },
  { key: "magic_system", label: "力量体系", placeholder: "如:灵气修炼分九境 / 无超能力 / 异能觉醒概率 1%" },
  { key: "time_axis", label: "时间轴", placeholder: "如:架空古代 / 近未来 2050 / 平行 1920s" },
  { key: "tone", label: "基调", placeholder: "如:热血燃情 / 冷峻悬疑 / 苍凉惆怅" },
  { key: "free_form", label: "其他设定", placeholder: "其他你想锁定的世界规则,自由文本" },
];

// 各维度本地 editing 值(基于 props.initial 初始化)
const draft = ref<Record<string, string>>({});
function syncFromProps() {
  const next: Record<string, string> = {};
  for (const d of DIMS) {
    next[d.key] = props.initial?.[d.key] ?? "";
  }
  draft.value = next;
}
syncFromProps();
watch(() => props.initial, syncFromProps, { deep: true });

function onBlur(dim: string) {
  const value = (draft.value[dim] || "").trim().slice(0, 200);
  const oldValue = props.initial?.[dim] ?? "";
  if (value === oldValue) return;
  // 把当前所有 6 维都发出去(后端 dict 全替换语义);后端 router 已 strip empty
  const patch: Record<string, string> = {};
  for (const d of DIMS) {
    patch[d.key] = (draft.value[d.key] || "").trim().slice(0, 200);
  }
  emit("save", patch);
}

const filledCount = computed(() => {
  return DIMS.filter((d) => (draft.value[d.key] || "").trim()).length;
});

function toggleSection() {
  sectionCollapsed.value = !sectionCollapsed.value;
}
</script>

<template>
  <section class="wb-section" :class="{ 'is-collapsed': sectionCollapsed }">
    <header class="wb-header">
      <button
        type="button"
        class="collapse-btn"
        :aria-expanded="!sectionCollapsed"
        :aria-label="sectionCollapsed ? '展开世界观' : '折叠世界观'"
        @click="toggleSection"
      >
        {{ sectionCollapsed ? "▸" : "▾" }}
      </button>
      <div class="wb-title-block">
        <h3 class="wb-title">
          <Icon name="world" :size="18" class="title-icon" />
          世界观
          <span class="title-count mono">{{ filledCount }} / {{ DIMS.length }}</span>
        </h3>
        <p v-if="!sectionCollapsed" class="wb-sub">
          6 维设定决定 LLM 推演的世界规则,推演时自动遵守
        </p>
      </div>
    </header>

    <template v-if="!sectionCollapsed">
      <div class="wb-grid">
        <label
          v-for="dim in DIMS"
          :key="dim.key"
          class="wb-field"
        >
          <span class="wb-field-label">{{ dim.label }}</span>
          <input
            v-model="draft[dim.key]"
            type="text"
            maxlength="200"
            :placeholder="dim.placeholder"
            class="wb-input"
            @blur="onBlur(dim.key)"
            @keydown.enter.prevent="(($event.target as HTMLInputElement)?.blur())"
          />
        </label>
      </div>
    </template>
  </section>
</template>

<style scoped>
.wb-section {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  padding: var(--space-4) var(--space-5);
  margin-bottom: var(--space-4);
}

.wb-header {
  display: flex;
  align-items: flex-start;
  gap: var(--space-3);
}

.collapse-btn {
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  font-size: var(--text-base);
  line-height: 1;
  color: var(--color-text-muted);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  cursor: pointer;
  transition: all var(--duration-fast) var(--ease-out);
}
.collapse-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.wb-title-block {
  flex: 1;
  min-width: 0;
}

.wb-title {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: var(--text-lg);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
}

.title-icon {
  font-size: var(--text-lg);
}

.title-count {
  font-size: var(--text-xs);
  color: var(--color-accent-text);
  background: var(--color-accent-soft);
  padding: 1px 6px;
  border-radius: var(--radius-sm);
  font-weight: 500;
}

.wb-sub {
  margin: 4px 0 0;
  font-size: var(--text-xs);
  color: var(--color-text-muted);
}

.wb-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3) var(--space-4);
  margin-top: var(--space-4);
}

@media (max-width: 640px) {
  .wb-grid {
    grid-template-columns: 1fr;
  }
}

.wb-field {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.wb-field-label {
  font-size: var(--text-xs);
  font-weight: 500;
  color: var(--color-text-muted);
}

.wb-input {
  padding: var(--space-2) var(--space-3);
  font-size: var(--text-sm);
  color: var(--color-text);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  transition: border-color var(--duration-fast) var(--ease-out);
}

.wb-input:focus {
  outline: none;
  border-color: var(--color-accent-border);
  background: var(--color-surface);
}

.mono {
  font-family: var(--font-mono, monospace);
}
</style>
