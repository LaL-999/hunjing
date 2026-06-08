<script setup lang="ts">
/**
 * 剧创态主页(占位实现 — Sprint SP-S 阶段 1,2026-06-07)
 *
 * 当前为占位,显示"功能上线中"友好页 + GitHub 仓库链接。
 * 阶段 2 会用 hunjing-screenplay 仓库的真实 HomeView 替换:
 *   - 小说上传卡(.txt/.epub/.docx)
 *   - 已上传小说书架(用户隔离)
 *   - 后端 prefix /api/screenplay/* 已规划,见 INTEGRATION_NOTES_SCREENPLAY.md
 *
 * 入口路由:/screenplay
 * 父入口:Dashboard 6 象限 → 剧创态卡
 */
import { useRouter } from "vue-router";

const router = useRouter();

function goBack() {
  router.push("/dashboard");
}
</script>

<template>
  <main class="screenplay-home">
    <header class="hdr">
      <button class="back-btn" @click="goBack" aria-label="返回 Dashboard">
        <svg
          width="16"
          height="16"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.5"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M19 12H5M12 19l-7-7 7-7" />
        </svg>
      </button>
      <div class="hdr-text">
        <div class="brand">浑晶</div>
        <h1 class="title">剧创态</h1>
        <p class="tagline">由小说,至剧本。</p>
      </div>
    </header>

    <section class="placeholder-card">
      <div class="placeholder-icon">
        <svg
          width="32"
          height="32"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          stroke-width="1.2"
          stroke-linecap="round"
          stroke-linejoin="round"
        >
          <path d="M3 6h18" />
          <path d="M6 3v6" />
          <path d="M12 3v6" />
          <path d="M18 3v6" />
          <path d="M3 10h18v11H3z" />
        </svg>
      </div>
      <h2>剧创态正在并入浑晶</h2>
      <p class="ph-body">
        第 5 创作态由七牛云 1024 暑期实训营窗口内独立开发,正在迁入浑晶平台。
        <br />
        预计本周完成与中间态(角色 / 关系 / 事件)的深度协同。
      </p>
      <div class="features">
        <div class="feat">
          <strong>5 种专业改编手法</strong>
          <span>V.O. / 动作外化 / 潜台词 / 意象化 / 删除 — 作者拍板</span>
        </div>
        <div class="feat">
          <strong>人机协作优化引擎</strong>
          <span>单场精修 + 整本重排,自动入版本树可回滚</span>
        </div>
        <div class="feat">
          <strong>fidelity 4 维 + 张力曲线</strong>
          <span>对白覆盖度 / 角色一致性 / 三幕分布 自动评分</span>
        </div>
        <div class="feat">
          <strong>V.O. / O.S. 工业级声音区分</strong>
          <span>导演看了立刻知道现场录还是后期配</span>
        </div>
      </div>
      <a
        class="repo-link"
        href="https://github.com/LaL-999/hunjing-screenplay"
        target="_blank"
        rel="noopener"
      >
        查看比赛仓库 →
      </a>
    </section>
  </main>
</template>

<style scoped>
.screenplay-home {
  min-height: 100%;
  padding: var(--space-8) var(--space-6);
  max-width: 720px;
  margin: 0 auto;
}

.hdr {
  display: flex;
  align-items: flex-start;
  gap: var(--space-4);
  margin-bottom: var(--space-7);
}
.back-btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: var(--radius-md);
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text-muted);
  flex-shrink: 0;
  margin-top: 4px;
}
.back-btn:hover {
  background: var(--color-surface-hover);
  color: var(--color-text);
}
.hdr-text {
  flex: 1;
}
.brand {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  letter-spacing: 0.24em;
  margin-bottom: var(--space-2);
}
.title {
  font-size: var(--text-3xl);
  font-weight: 600;
  color: var(--color-text);
  margin: 0;
  letter-spacing: 0.02em;
}
.tagline {
  font-size: var(--text-base);
  color: var(--color-text-muted);
  margin: var(--space-2) 0 0;
}

.placeholder-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-xl);
  padding: var(--space-7) var(--space-6);
  text-align: center;
}
.placeholder-icon {
  color: var(--color-accent);
  margin-bottom: var(--space-4);
  opacity: 0.7;
}
.placeholder-card h2 {
  font-size: var(--text-xl);
  color: var(--color-text);
  margin: 0 0 var(--space-3);
  font-weight: 600;
}
.ph-body {
  font-size: var(--text-sm);
  color: var(--color-text-muted);
  line-height: 1.8;
  margin: 0 0 var(--space-6);
}

.features {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-3);
  text-align: left;
  margin-bottom: var(--space-6);
}
@media (max-width: 640px) {
  .features {
    grid-template-columns: 1fr;
  }
}
.feat {
  padding: var(--space-3) var(--space-4);
  background: var(--color-bg);
  border-radius: var(--radius-md);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.feat strong {
  font-size: var(--text-sm);
  color: var(--color-text);
  font-weight: 600;
}
.feat span {
  font-size: var(--text-xs);
  color: var(--color-text-muted);
  line-height: 1.6;
}

.repo-link {
  display: inline-block;
  font-size: var(--text-sm);
  color: var(--color-accent);
  border-bottom: 1px solid currentColor;
  padding-bottom: 2px;
}
.repo-link:hover {
  color: var(--color-accent-hover);
}
</style>
