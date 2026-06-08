<script setup lang="ts">
/**
 * CharacterEmotionModal — 单角色情绪曲线居中模态(Sprint 6.A2 路线图 #2 v2,2026-05-22)
 *
 * 设计原因:首版默认渲染全部角色图表占满屏 → 用户反馈"应做成 chip 列表 + 点击弹卡片"。
 * 此组件是薄壳:复用全局 .modal-fade transition + .modal-backdrop(blur 4px),
 * 内嵌 CharacterEmotionChart;关闭走 Esc / 点 backdrop / 右上角 ×。
 *
 * Props:
 *   open           boolean                    控制显示
 *   characterName  string                     角色名(传给 chart)
 *   records        EmotionalStateRecord[]     该角色排序后情绪记录(传给 chart)
 *
 * Emits:
 *   close   用户关闭(Esc / 点 backdrop / 点 ×)
 *
 * 与 LoginModal / DocumentViewer 范式一致:Teleport to body + transition name="modal-fade"
 * → 全局 modal-fade-* class 自动接管节奏(scale 0.96 + translateY -4px + opacity)
 */
import { onBeforeUnmount, onMounted } from "vue";

import CharacterEmotionChart from "./CharacterEmotionChart.vue";
import type { EmotionalStateRecord } from "../api/types";

const props = defineProps<{
  open: boolean;
  characterName: string;
  records: EmotionalStateRecord[];
}>();

const emit = defineEmits<{
  (e: "close"): void;
}>();

function onBackdrop(e: MouseEvent) {
  if (e.target === e.currentTarget) emit("close");
}

function onGlobalKey(e: KeyboardEvent) {
  if (!props.open) return;
  if (e.key === "Escape") {
    e.preventDefault();
    emit("close");
  }
}

onMounted(() => document.addEventListener("keydown", onGlobalKey));
onBeforeUnmount(() => document.removeEventListener("keydown", onGlobalKey));
</script>

<template>
  <Teleport to="body">
    <transition name="modal-fade">
      <div
        v-if="open"
        class="modal-backdrop"
        role="dialog"
        aria-modal="true"
        :aria-label="`${characterName} 角色情绪曲线`"
        @click="onBackdrop"
      >
        <div class="modal-card emotion-modal-card">
          <button
            type="button"
            class="close-btn"
            aria-label="关闭"
            @click="emit('close')"
          >×</button>
          <CharacterEmotionChart
            :character-name="characterName"
            :records="records"
          />
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
  backdrop-filter: blur(4px);
  -webkit-backdrop-filter: blur(4px);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: var(--z-modal-backdrop);
  padding: var(--space-4);
}

/* modal-card 自身透明 + 无边框 — chart 组件自带 .surface 卡片视觉(白底 + border + radius)
 * 避免 double card 视觉冗余 */
.emotion-modal-card {
  width: 100%;
  max-width: 720px;
  position: relative;
  z-index: var(--z-modal);
}

/* close button 浮在 chart 右上外角 — 经典 modal 范式,不挡 chart-header */
.close-btn {
  position: absolute;
  top: -10px;
  right: -10px;
  width: 32px;
  height: 32px;
  font-size: var(--text-xl);
  line-height: 1;
  color: var(--color-text-muted);
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-full);
  box-shadow: var(--shadow-md);
  transition: all var(--duration-fast) var(--ease-out);
  z-index: 1;
}
.close-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
  border-color: var(--color-border-strong);
}
</style>
