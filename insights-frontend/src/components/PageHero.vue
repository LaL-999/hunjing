<script setup lang="ts">
/**
 * PageHero — 每页顶部"用途卡"(INS-A9,2026-05-27 末³).
 *
 * 设计宗旨:管理员 10s 内看懂当前页是干什么的、给谁看、怎么用.
 * 用户拍板:每页用 PageHero 集中承担用途提示,view 内部不再加 hint / sub-label / 释义文案.
 *
 * 用法:
 * <PageHero
 *   icon="layout-dashboard"
 *   title="总览"
 *   description="一眼看懂平台用户规模、今日活跃和事件分布。数字异常时点用户画像下钻具体行为链。"
 *   audience="管理员视角"
 *   back-to="/users"  // 可选:detail page 加返回入口
 * />
 *
 * 返回按钮逻辑(2026-05-27 末⁵⁵):
 *   - 若有 backTo prop → 显示 < 按钮
 *   - 点击优先 router.back()(从哪来回哪,智能)
 *   - 浏览器无 history(直链 / 刷新)→ fallback 到 backTo 路径
 */
import { useRouter } from "vue-router";
import Icon from "./Icon.vue";

const props = defineProps<{
  icon: string;
  title: string;
  description: string;
  audience?: string;
  backTo?: string;
}>();

const router = useRouter();

function goBack() {
  // 优先浏览器 history,fallback 到固定路径
  if (window.history.length > 1) {
    router.back();
  } else if (props.backTo) {
    router.push(props.backTo);
  } else {
    router.push("/");
  }
}
</script>

<template>
  <header class="hero">
    <div class="title-row">
      <button
        v-if="backTo"
        class="back-btn"
        :aria-label="`返回 ${backTo}`"
        @click="goBack"
      >
        <Icon name="chevron-left" :size="16" />
      </button>
      <span class="title-icon">
        <Icon :name="icon" :size="20" />
      </span>
      <h2 class="title">{{ title }}</h2>
      <span v-if="audience" class="chip">{{ audience }}</span>
    </div>
    <p class="desc">{{ description }}</p>
  </header>
</template>

<style scoped>
.hero {
  background: white;
  border: 1px solid #e5e1d8;
  border-radius: 0.625rem;
  padding: 1.25rem 1.5rem;
  margin-bottom: 1.5rem;
}

.title-row {
  display: flex;
  align-items: center;
  gap: 0.625rem;
}

/* 返回按钮(2026-05-27 末⁵⁵)— detail page 智能返回上层列表 */
.back-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.75rem;
  height: 1.75rem;
  background: transparent;
  border: 1px solid #d6d1c4;
  border-radius: 0.375rem;
  color: #6b6862;
  transition: background 120ms, border-color 120ms, color 120ms;
}
.back-btn:hover {
  background: #f7f5f0;
  border-color: #9a968d;
  color: #1f1f1e;
}
.back-btn:active {
  background: #ede9e0;
}

.title-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2rem;
  height: 2rem;
  background: #f3efff;
  color: #7c3aed;
  border-radius: 0.5rem;
}

.title {
  font-size: 1.25rem;
  font-weight: 600;
  color: #1f1f1e;
  margin: 0;
  flex-shrink: 0;
}

.chip {
  font-size: 0.6875rem;
  color: #6b6862;
  background: #ede9e0;
  padding: 0.1875rem 0.625rem;
  border-radius: 62.4375rem;
  margin-left: auto;
  font-weight: 500;
}

.desc {
  margin: 0.625rem 0 0 2.625rem;
  font-size: 0.8125rem;
  line-height: 1.7;
  color: #6b6862;
}
</style>
