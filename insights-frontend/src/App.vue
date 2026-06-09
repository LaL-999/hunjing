<script setup lang="ts">
// 顶层 shell — 侧边栏导航(对齐主平台 AppSidebar 风格)+ 主区 router-view.
// 2026-05-27 末⁵⁵:对齐主平台样式 + 全局 zoom 80%(用户实测视觉最优).
import Icon from "./components/Icon.vue";
</script>

<template>
  <div class="layout">
    <aside class="sidebar">
      <!-- 顶部 logo:点击返回首页(对齐主平台 AppSidebar.sidebar-header) -->
      <router-link to="/" class="sidebar-header">
        <span class="logo-mark">◆</span>
        <span class="logo-text">浑晶 · 洞察</span>
      </router-link>

      <nav>
        <div class="nav-group">
          <div class="group-label">数据看板</div>
          <router-link to="/" exact-active-class="active" class="nav-link">
            <Icon name="layout-dashboard" :size="16" />
            <span>总览</span>
          </router-link>
          <router-link to="/funnel" active-class="active" class="nav-link">
            <Icon name="funnel" :size="16" />
            <span>转化漏斗</span>
          </router-link>
          <router-link to="/retention" active-class="active" class="nav-link">
            <Icon name="line-chart" :size="16" />
            <span>留存</span>
          </router-link>
        </div>
        <!-- INS-B Phase 4(2026-05-27 末⁵³):用户画像升级为独立大 tab -->
        <div class="nav-group">
          <div class="group-label">用户</div>
          <router-link to="/users" active-class="active" class="nav-link">
            <Icon name="users" :size="16" />
            <span>用户画像</span>
          </router-link>
        </div>
        <!-- INS-B Phase 1(2026-05-27 末⁵):经营 / 安全 / 指南针 -->
        <div class="nav-group">
          <div class="group-label">经营 · 风控</div>
          <router-link to="/business" active-class="active" class="nav-link">
            <Icon name="trending-up" :size="16" />
            <span>经营驾驶舱</span>
          </router-link>
          <router-link to="/quality" active-class="active" class="nav-link">
            <Icon name="bar-chart-3" :size="16" />
            <span>质量观察</span>
          </router-link>
          <router-link to="/safety" active-class="active" class="nav-link">
            <Icon name="shield" :size="16" />
            <span>合规风控</span>
          </router-link>
          <router-link to="/compass" active-class="active" class="nav-link">
            <Icon name="compass" :size="16" />
            <span>作者指南针</span>
          </router-link>
          <!-- 2026-06-09:剧创态使用洞察 -->
          <router-link to="/screenplay" active-class="active" class="nav-link">
            <Icon name="film" :size="16" />
            <span>剧创态洞察</span>
          </router-link>
        </div>
        <div class="nav-group">
          <div class="group-label">实时</div>
          <router-link to="/live" active-class="active" class="nav-link">
            <Icon name="activity" :size="16" />
            <span>事件流</span>
          </router-link>
        </div>
        <!-- BYOK 审核(2026-06-05):自携密钥订单人工审核 -->
        <div class="nav-group">
          <div class="group-label">订单审核</div>
          <router-link to="/byok-review" active-class="active" class="nav-link">
            <Icon name="key" :size="16" />
            <span>BYOK 审核</span>
          </router-link>
        </div>
        <div class="nav-group">
          <div class="group-label">知识库</div>
          <router-link to="/knowledge" active-class="active" class="nav-link">
            <Icon name="book-open" :size="16" />
            <span>Agent 日报 + 阶段汇总</span>
          </router-link>
        </div>
      </nav>
    </aside>
    <main class="main">
      <router-view />
    </main>
  </div>
</template>

<style>
:root {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  color-scheme: light;
}

/* 末⁵⁹ + 末⁶⁰ 重写:
 *   - 抛弃 html { zoom: 0.8 } — zoom 跟 flex/grid overflow 有 Chrome 实现层兼容 bug
 *   - 改 flex 布局 — 单行 sidebar+main 用 flex 更直观
 *   - 滚动 3 件套:flex:1 + min-height:0 + overflow-y:auto
 *   - 80% 视觉:走主平台 VD-1 同套路 — root font-size 80% + 全套 px → rem
 *     1rem = 12.8px(浏览器 default 16px × 80%)
 *     所有用 rem 的尺寸自动缩 80%;border 等保留 px(细线防亚像素丢失) */

html {
  font-size: 80%;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
html, body, #app { height: 100%; }
body {
  background: #faf7f2;
  color: #1f1f1e;
  font-size: 0.9375rem;
  overflow: hidden;
}

.layout {
  display: flex;
  height: 100vh;
  width: 100vw;
  overflow: hidden;
}

.sidebar {
  width: 15rem;
  flex-shrink: 0;
  background: #f4f0e8;
  border-right: 1px solid #e5e1d8;
  display: flex;
  flex-direction: column;
  user-select: none;
  overflow-y: auto;
  min-height: 0;
}

/* ===== 顶部 logo(对齐主平台 AppSidebar.sidebar-header)===== */
.sidebar-header {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 1rem 1.25rem;
  cursor: pointer;
  border-bottom: 1px solid #e5e1d8;
  text-decoration: none;
  transition: background 150ms ease-out;
}
.sidebar-header:hover {
  background: #f7f5f0;
}
.logo-mark {
  font-size: 1.125rem;
  color: #7c3aed;
  line-height: 1;
}
.logo-text {
  font-size: 1.125rem;
  font-weight: 600;
  color: #1f1f1e;
  letter-spacing: 0.02em;
}

/* ===== nav ===== */
nav {
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  padding: 0.75rem 0.75rem 1rem;
}
.nav-group {
  display: flex;
  flex-direction: column;
  gap: 0.0625rem;
}
.group-label {
  font-size: 0.625rem;
  color: #9a968d;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.03125rem;
  padding: 0.625rem 0.75rem 0.25rem;
}
.nav-link {
  display: flex;
  align-items: center;
  gap: 0.625rem;
  padding: 0.4375rem 0.75rem;
  border-radius: 0.375rem;
  color: #1f1f1e;
  text-decoration: none;
  font-size: 0.8125rem;
  transition: background 120ms ease-out, color 120ms ease-out;
}
.nav-link:hover {
  background: rgba(0, 0, 0, 0.04);
}
.nav-link.active {
  background: #7c3aed;
  color: white;
}
.main {
  flex: 1;
  min-width: 0;
  min-height: 0;
  overflow-y: auto;
  padding: 2rem;
  /* flex 子项滚动黄金组合(末⁵⁹):
   *   - flex:1   占据剩余空间(sidebar 15rem 之外)
   *   - min-*:0  允许子项小于内容(否则 flex 子项默认 min-content,撑大父容器)
   *   - overflow-y: auto 内容溢出时滚动 */
}
</style>
