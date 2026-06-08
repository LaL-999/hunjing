<!--
  BeianFooter.vue — ICP 备案号展示组件(全平台通用)

  合规要求:工信部规定网站底部必须展示 ICP 备案号 + 链接到 beian.miit.gov.cn
  视觉规范:小字号、低饱和、不抢眼,但保证可读

  使用位置(2026-06-04):
    - AppSidebar 底部用户区下方(已登录 + 游客都显)
    - LoginModal 卡片底部(未登录首屏建立信任)
    - 后续可在任意 view 底部 mount

  variant:
    - "subtle"  小字浅灰,左侧栏 / 法律页用(默认)
    - "modal"   modal 底部,稍微醒目一点
-->

<template>
  <div class="beian-footer" :class="`beian-footer--${variant}`">
    <a
      :href="ICP_VERIFY_URL"
      target="_blank"
      rel="noopener noreferrer nofollow"
      class="beian-link"
      :aria-label="`查询备案号 ${ICP_RECORD_NUMBER}`"
    >
      <!-- 小盾牌 icon 暗示「已经备案 / 合规」 -->
      <svg
        xmlns="http://www.w3.org/2000/svg"
        :width="iconSize"
        :height="iconSize"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        stroke-width="1.8"
        stroke-linecap="round"
        stroke-linejoin="round"
        class="beian-icon"
        aria-hidden="true"
      >
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
      </svg>
      <span class="beian-text">{{ ICP_RECORD_NUMBER }}</span>
    </a>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue";

import { ICP_RECORD_NUMBER, ICP_VERIFY_URL } from "../constants/beian-info";

interface Props {
  /** 视觉风格 — subtle 给侧栏 / footer,modal 给登录卡 */
  variant?: "subtle" | "modal";
}

const props = withDefaults(defineProps<Props>(), {
  variant: "subtle",
});

const iconSize = computed(() => (props.variant === "modal" ? 12 : 11));
</script>

<style scoped>
.beian-footer {
  display: flex;
  justify-content: center;
  align-items: center;
  user-select: none;
}

.beian-footer--subtle {
  padding: var(--space-2) var(--space-3);
  border-top: 1px solid var(--color-border);
}

.beian-footer--modal {
  padding: var(--space-3) 0 var(--space-2);
}

.beian-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  line-height: 1;
  color: var(--color-text-tertiary, var(--color-text-secondary));
  text-decoration: none;
  letter-spacing: 0.02em;
  transition: color var(--duration-fast, 0.15s) var(--ease-out, ease);
}

.beian-link:hover {
  color: var(--color-text-secondary);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.beian-footer--modal .beian-link {
  font-size: 12px;
}

.beian-icon {
  flex-shrink: 0;
  opacity: 0.7;
}

.beian-link:hover .beian-icon {
  opacity: 1;
}

.beian-text {
  /* 数字与中文混排:数字部分用等宽更对齐 */
  font-feature-settings: "tnum" 1;
}
</style>
