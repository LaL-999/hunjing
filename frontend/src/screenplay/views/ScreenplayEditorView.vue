<script setup lang="ts">
/**
 * 剧创态编辑器(占位实现 — Sprint SP-S 阶段 1)
 *
 * 路由:/screenplay/novels/:id
 * 阶段 2 会用 hunjing-screenplay 仓库的真实编辑器替换 — 双栏 + AI 优化 + 版本树
 */
import { useRouter } from "vue-router";

const router = useRouter();

function goBack() {
  router.push("/screenplay");
}
</script>

<template>
  <main class="editor-placeholder">
    <button class="back-btn" @click="goBack">← 返回剧创态首页</button>
    <p class="hint">编辑器视图正在迁入(阶段 2)…</p>
  </main>
</template>

<style scoped>
.editor-placeholder {
  min-height: 100%;
  padding: var(--space-7) var(--space-6);
  text-align: center;
}
.back-btn {
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-2) var(--space-4);
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
.back-btn:hover {
  background: var(--color-surface-hover);
}
.hint {
  margin-top: var(--space-6);
  color: var(--color-text-muted);
  font-size: var(--text-sm);
}
</style>
