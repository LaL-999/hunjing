<script setup lang="ts">
/**
 * TabBar — 通用 sub-tab 组件(INS-B Phase 4,2026-05-27 末⁵³).
 *
 * 设计宗旨:大 tab 下的多块内容(合规风控 4 块 / 质量观察 4 块 / 经营驾驶舱 4 块)
 * 拆 sub-tab 渲染,避免单页信息密度过高 + 海量数据时一次性加载卡顿.
 *
 * 用法:
 * <TabBar v-model="activeTab" :tabs="[{ key: 'overview', label: '总览', icon: 'bar-chart-3' }, ...]" />
 *
 * 视觉:line-bottom 风格(line 在底部,选中变紫色),不竖色条(2.6 偏好).
 */
import Icon from "./Icon.vue";

interface Tab {
  key: string;
  label: string;
  icon?: string;
  badge?: number | string;
}

defineProps<{
  modelValue: string;
  tabs: Tab[];
}>();

defineEmits<{
  (e: "update:modelValue", value: string): void;
}>();
</script>

<template>
  <div class="tab-bar">
    <button
      v-for="tab in tabs"
      :key="tab.key"
      class="tab-item"
      :class="{ active: modelValue === tab.key }"
      @click="$emit('update:modelValue', tab.key)"
    >
      <Icon v-if="tab.icon" :name="tab.icon" :size="14" />
      <span>{{ tab.label }}</span>
      <span v-if="tab.badge !== undefined && tab.badge !== null && tab.badge !== 0" class="badge">
        {{ tab.badge }}
      </span>
    </button>
  </div>
</template>

<style scoped>
.tab-bar {
  display: flex;
  gap: 0;
  border-bottom: 1px solid #e5e1d8;
  margin-bottom: 1.25rem;
}

.tab-item {
  display: inline-flex;
  align-items: center;
  gap: 0.375rem;
  padding: 0.625rem 1.125rem;
  background: transparent;
  border: 0;
  border-bottom: 2px solid transparent;
  font-size: 0.8125rem;
  color: #6b6862;
  cursor: pointer;
  transition: color 120ms, border-color 120ms;
  margin-bottom: -0.0625rem;
  font-weight: 500;
}

.tab-item:hover {
  color: #1f1f1e;
}

.tab-item.active {
  color: #7c3aed;
  border-bottom-color: #7c3aed;
}

.badge {
  background: #ede9e0;
  color: #6b6862;
  font-size: 0.6875rem;
  padding: 0.0625rem 0.5rem;
  border-radius: 62.4375rem;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.tab-item.active .badge {
  background: #f3efff;
  color: #7c3aed;
}
</style>
