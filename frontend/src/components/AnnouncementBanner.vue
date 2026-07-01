<!--
  AnnouncementBanner.vue — 平台公告条(v5 item5:创始人致辞)

  形态:内容区顶部一条紧凑 bar;可「展开全文」看完整信;可「¥5 开通」直达自携密钥;可关闭。
  关闭状态存 localStorage(按公告 id),换新公告(bump id)会重新出现。
  仅对已登录用户展示(公告内容是"如何用满平台",与登录态强相关)。
-->
<template>
  <transition name="announce-fade">
    <section v-if="visible" class="announce" :class="{ 'is-open': expanded }">
      <div class="announce-bar">
        <svg
          class="announce-icon" width="16" height="16" viewBox="0 0 24 24"
          fill="none" stroke="currentColor" stroke-width="1.7"
          stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"
        >
          <path d="M3 11l18-5v12L3 14v-3z" />
          <path d="M11.6 16.8a3 3 0 0 1-5.8-1.6" />
        </svg>

        <span class="announce-teaser">{{ ann.teaser }}</span>

        <div class="announce-actions">
          <button type="button" class="announce-link" @click="expanded = !expanded">
            {{ expanded ? "收起" : "展开全文" }}
          </button>
          <button type="button" class="announce-cta" @click="handleCta">
            {{ ann.ctaLabel }}
          </button>
          <button
            type="button" class="announce-close" aria-label="关闭公告" @click="dismiss"
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
                 stroke="currentColor" stroke-width="1.8" stroke-linecap="round">
              <path d="M18 6L6 18M6 6l12 12" />
            </svg>
          </button>
        </div>
      </div>

      <!-- 展开态:完整创始人信 -->
      <transition name="announce-expand">
        <div v-if="expanded" class="announce-letter">
          <h3 class="announce-title">{{ ann.title }}</h3>
          <p v-for="(p, i) in ann.paragraphs" :key="i" class="announce-para">{{ p }}</p>
          <p class="announce-sign">{{ ann.signature }}</p>
        </div>
      </transition>
    </section>
  </transition>
</template>

<script setup lang="ts">
import { computed, ref } from "vue";
import { useRouter } from "vue-router";

import {
  PLATFORM_ANNOUNCEMENT,
  announcementDismissKey,
} from "../constants/announcement";
import { useAuthStore } from "../stores/auth";
import { useBYOKStore } from "../stores/byok";

const router = useRouter();
const auth = useAuthStore();
const byok = useBYOKStore();

const ann = PLATFORM_ANNOUNCEMENT;
const expanded = ref(false);

const dismissed = ref<boolean>(readDismissed());

function readDismissed(): boolean {
  try {
    return localStorage.getItem(announcementDismissKey(ann.id)) === "1";
  } catch {
    return false;
  }
}

// 仅登录 + 未开通 BYOK + 未 dismiss 时显示(已开通的用户不用再被劝)
const visible = computed(
  () => auth.isAuthed && !byok.isActive && !dismissed.value,
);

function dismiss(): void {
  dismissed.value = true;
  try {
    localStorage.setItem(announcementDismissKey(ann.id), "1");
  } catch {
    /* localStorage 不可用则本次会话内关闭即可 */
  }
}

function handleCta(): void {
  router.push("/byok-config");
}
</script>

<style scoped>
.announce {
  margin: 0 0 var(--space-4) 0;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-lg);
  background:
    linear-gradient(0deg, var(--color-surface), var(--color-surface)),
    radial-gradient(120% 200% at 0% 0%, rgba(124, 58, 237, 0.08), transparent 60%);
  background-blend-mode: normal;
  overflow: hidden;
}

.announce-bar {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 10px var(--space-4);
}

.announce-icon {
  color: var(--color-accent);
  flex-shrink: 0;
}

.announce-teaser {
  flex: 1;
  min-width: 0;
  font-size: var(--text-sm);
  color: var(--color-text-primary);
  line-height: 1.5;
}

.announce-actions {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  flex-shrink: 0;
}

.announce-link {
  font-size: 12px;
  color: var(--color-text-secondary);
  background: none;
  border: none;
  cursor: pointer;
  white-space: nowrap;
}
.announce-link:hover { color: var(--color-text-primary); text-decoration: underline; }

.announce-cta {
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  white-space: nowrap;
  padding: 6px 14px;
  border: none;
  border-radius: var(--radius-md);
  cursor: pointer;
  background: linear-gradient(135deg, #8b5cf6, #6d28d9);
  transition: opacity var(--duration-fast) var(--ease-out);
}
.announce-cta:hover { opacity: 0.9; }

.announce-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  color: var(--color-text-muted);
  background: none;
  border: none;
  border-radius: var(--radius-sm);
  cursor: pointer;
}
.announce-close:hover {
  color: var(--color-text-primary);
  background: var(--color-surface-hover);
}

.announce-letter {
  padding: 4px var(--space-4) var(--space-4);
  border-top: 1px solid var(--color-border);
}

.announce-title {
  font-size: var(--text-base);
  font-weight: 700;
  color: var(--color-text-primary);
  margin: var(--space-3) 0 var(--space-3);
}

.announce-para {
  font-size: var(--text-sm);
  line-height: 1.85;
  color: var(--color-text-secondary);
  margin: 0 0 var(--space-2);
}

.announce-sign {
  font-size: var(--text-sm);
  color: var(--color-text-primary);
  text-align: right;
  margin: var(--space-3) 0 0;
}

/* transitions */
.announce-fade-enter-active,
.announce-fade-leave-active {
  transition: opacity var(--duration-base) var(--ease-out),
              transform var(--duration-base) var(--ease-out);
}
.announce-fade-enter-from,
.announce-fade-leave-to {
  opacity: 0;
  transform: translateY(-6px);
}

.announce-expand-enter-active,
.announce-expand-leave-active {
  transition: opacity var(--duration-fast) var(--ease-out);
}
.announce-expand-enter-from,
.announce-expand-leave-to { opacity: 0; }
</style>
