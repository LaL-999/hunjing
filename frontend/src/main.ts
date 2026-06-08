/**
 * 浑晶 Frontend Entry
 *
 * 启动顺序:
 *   1. **applyInitialTheme** — DOM `<html data-theme>` 在 Vue mount 前就挂好,防 FOUC 白闪
 *   2. 创建 app
 *   3. 装 pinia(stores 才能 use)
 *   4. wireAuthIntoApiClient — 把 auth store 的 token getter 注入 fetch wrapper
 *   5. 装 router(beforeEach 守卫会读 auth store,所以必须在 wire 之后)
 *   6. mount
 */
import { createApp } from "vue";
import { createPinia } from "pinia";

import "./styles/tokens.css";
import "./styles/global.css";

import App from "./App.vue";
import { router } from "./router";
import { applyInitialTheme } from "./composables/useTheme";
import { wireAuthIntoApiClient } from "./stores/auth";
// INS-A2(2026-05-27):洞察子系统埋点 SDK — 安装全局 hook(session_start / unload flush)
// 这是主平台前端唯一与 insights 系统耦合的代码点(零 UI 痕迹)
import { installAnalytics } from "./composables/useAnalytics";

// Sprint D.4:必须在 createApp 之前应用主题 — 否则 Vue mount 第一帧
// 用默认浅色 token 渲染,mount 后才切换 → 用户看到一帧白闪
applyInitialTheme();

const app = createApp(App);
app.use(createPinia());

// 2026-06-02 上线监控:Sentry 错误追踪(VITE_SENTRY_DSN 未设置 → 跳过)
// 需要先 `npm install @sentry/vue`(已加进 package.json optionalDependencies)
// 配置 .env.production:VITE_SENTRY_DSN=https://xxxx@xxx.ingest.sentry.io/yyyy
//
// 关键 hotfix(2026-06-02):
//   即使 import 放在 if (import.meta.env.PROD) 守门内,vite dev 仍会**静态扫描**
//   import("@sentry/vue") 字符串并尝试解析,SDK 未装时报"Failed to resolve import".
//   解决:① 用 /* @vite-ignore */ 注释跳过分析 ② 用动态字符串变量绕开静态扫描
const _sentryDsn = (import.meta.env.VITE_SENTRY_DSN as string | undefined)?.trim();
if (import.meta.env.PROD && _sentryDsn) {
  // 用变量保存模块名 + /* @vite-ignore */ 双保险,让 vite 不静态扫描
  const _sentryModulePath = "@sentry/vue";
  import(/* @vite-ignore */ _sentryModulePath)
    .then((Sentry) => {
      Sentry.init({
        app,
        dsn: _sentryDsn,
        tracesSampleRate: 0.1,  // 10% 请求采样
        environment: "production",
        // 不上传 token / 表单 / 用户输入等敏感信息
        sendDefaultPii: false,
      });
    })
    .catch((err) => {
      // 监控不应阻塞业务
      if (import.meta.env.DEV) {
        // eslint-disable-next-line no-console
        console.warn("[main] Sentry 初始化跳过(SDK 未装):", err);
      }
    });
}

// 必须在 router 之前 wire,因为 router 守卫会读 auth store
wireAuthIntoApiClient();

app.use(router);
app.mount("#app");

// INS-A2(2026-05-27):安装洞察埋点全局 hook(session_start + unload flush)
// 故意放在 mount 之后 — 让首屏渲染优先,埋点稍后启动
installAnalytics();
