<script setup lang="ts">
/**
 * PlaygroundView — 「更多玩法」主视图(网易云式大矩形 banner 入口)。2026-06-25。
 *
 * Dashboard 第 6 张卡「更多玩法」点击 → 进入此独立右主视图(非弹窗)。
 * 大广告屏式 banner 往下排列,每个 banner 是一种"玩法"。当前:
 *   - 作品广场(已上线)→ /plaza
 *   - 创作大赛 · 赛季榜(敬请期待)
 *
 * 设计稿:mockups/plaza.html 顶部 banner 区(用户已批准)。
 */
import { useRouter } from "vue-router";
import { toast } from "../composables/useToast";

const router = useRouter();

function enterPlaza(): void {
  router.push("/plaza");
}
function comingSoon(): void {
  toast.info("创作大赛 · 赛季榜正在路上 — 敬请期待", 3500);
}
</script>

<template>
  <div class="playground">
    <header class="pg-head">
      <h1 class="pg-title">更多玩法</h1>
      <p class="pg-sub">浑晶不只是创作工具 — 这里是好故事被看见、被比拼、被记住的地方。</p>
    </header>

    <div class="banners">
      <!-- 作品广场 -->
      <button class="banner plaza" type="button" @click="enterPlaza">
        <span class="b-tag">✦ 全新上线</span>
        <span class="b-art" aria-hidden="true" />
        <span class="b-grid" aria-hidden="true">
          <i v-for="n in 4" :key="n" />
        </span>
        <span class="b-content">
          <span class="b-name">作品广场</span>
          <span class="b-desc">把你在浑晶创作的故事上架到广场,免费分享给所有人在线阅读。好作品,值得被看见。</span>
        </span>
        <span class="b-go">进入 →</span>
      </button>

      <!-- 创作大赛(敬请期待) -->
      <button class="banner soon" type="button" @click="comingSoon">
        <span class="b-tag">敬请期待</span>
        <span class="b-content">
          <span class="b-name b-name--sm">创作大赛 · 赛季榜</span>
          <span class="b-desc">主题征文 + 读者投票 + 奖金池 —— 下一个玩法正在路上。</span>
        </span>
        <span class="b-go b-go--dim">开发中</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.playground {
  max-width: 1100px;
  margin: 0 auto;
  padding: var(--space-7) var(--space-6) var(--space-8);
}
.pg-head {
  padding: var(--space-2) 0 var(--space-6);
}
.pg-title {
  font-size: var(--text-2xl);
  font-weight: 700;
  color: var(--color-text);
  font-family: var(--font-serif, Georgia, serif);
  letter-spacing: 0.04em;
  margin: 0;
}
.pg-sub {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  margin: var(--space-2) 0 0;
}

.banners {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.banner {
  position: relative;
  height: 178px;
  border-radius: var(--radius-xl);
  overflow: hidden;
  cursor: pointer;
  display: flex;
  align-items: center;
  padding: 0 46px;
  color: #fff;
  text-align: left;
  box-shadow: var(--shadow-lg);
  transition: transform var(--duration-base) var(--ease-out),
              box-shadow var(--duration-base) var(--ease-out);
}
.banner:hover { transform: translateY(-3px); box-shadow: 0 18px 44px rgba(50, 40, 20, 0.16); }

.banner.plaza {
  background: radial-gradient(120% 160% at 0% 0%, #8B5CF6 0%, #6D28D9 45%, #3B1F8B 100%);
}
.banner.soon {
  background: linear-gradient(110deg, #2A2540, #3A3357);
  opacity: 0.94;
}

.b-tag {
  position: absolute;
  top: 18px;
  left: 46px;
  font-size: 11px;
  letter-spacing: 1px;
  background: rgba(255, 255, 255, 0.18);
  padding: 4px 10px;
  border-radius: var(--radius-full);
}
.banner.soon .b-tag { background: rgba(255, 255, 255, 0.1); color: #C9C3DE; }

.b-content {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  max-width: 560px;
}
.b-name {
  font-size: 30px;
  font-weight: 700;
  font-family: var(--font-serif, Georgia, serif);
  letter-spacing: 0.02em;
}
.b-name--sm { font-size: 25px; }
.b-desc {
  font-size: var(--text-sm);
  opacity: 0.86;
  line-height: 1.6;
}

.b-go {
  position: absolute;
  right: 46px;
  bottom: 26px;
  z-index: 2;
  font-size: var(--text-sm);
  opacity: 0.92;
}
.b-go--dim { opacity: 0.55; }

/* 装饰:右侧光晕 + frame 网格(纯视觉) */
.b-art {
  position: absolute;
  right: -10px;
  top: -30px;
  width: 280px;
  height: 280px;
  border-radius: 50%;
  background: radial-gradient(circle at 40% 40%, rgba(255, 255, 255, 0.22), transparent 60%);
  z-index: 1;
}
.b-grid {
  position: absolute;
  right: 46px;
  top: 50%;
  transform: translateY(-50%);
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 8px;
  opacity: 0.85;
  z-index: 1;
}
.b-grid i {
  width: 46px;
  height: 46px;
  border-radius: 10px;
  background: rgba(255, 255, 255, 0.16);
  display: block;
}

@media (max-width: 720px) {
  .banner { padding: 0 26px; height: 200px; }
  .b-tag { left: 26px; }
  .b-go { right: 26px; }
  .b-grid { display: none; }
  .b-name { font-size: 25px; }
}
</style>
