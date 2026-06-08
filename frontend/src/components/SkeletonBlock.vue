<script setup lang="ts">
/**
 * SkeletonBlock — 单一灰块占位(Sprint D.6)。
 *
 * 用法:
 *   <SkeletonBlock height="24px" />                  默认 100% 宽,指定高
 *   <SkeletonBlock height="20px" width="60%" />      自定义宽高
 *   <SkeletonBlock height="40px" rounded="full" />   圆角形(头像位)
 *
 * 视觉细节走 global.css `.skeleton` 类(灰底 + shimmer + prefers-reduced-motion 兼容)。
 * 这里只是个 props 化壳子,方便 template 里写。
 */
import { computed } from "vue";

const props = defineProps<{
  /** CSS 高度,默认 16px(单行文字高)*/
  height?: string;
  /** CSS 宽度,默认 100% */
  width?: string;
  /** 圆角风格 — "default"(4px tokens-sm)/ "md"(6px)/ "full"(圆形)*/
  rounded?: "default" | "md" | "full";
}>();

// computed 让 styles 响应 props 变化(父组件传 ref 高度时挂载后能正确刷新)
const styles = computed(() => ({
  height: props.height ?? "16px",
  width: props.width ?? "100%",
  borderRadius:
    props.rounded === "full"
      ? "9999px"
      : props.rounded === "md"
        ? "var(--radius-md)"
        : undefined,    // default 用 .skeleton 自带 var(--radius-sm)
}));
</script>

<template>
  <div class="skeleton skeleton-block" :style="styles" aria-hidden="true" />
</template>

<style scoped>
.skeleton-block {
  display: block;
}
</style>
