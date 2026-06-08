import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
  plugins: [vue()],
  server: {
    // 2026-06-05:5174 已被别的项目占用 → 改 5175 避开,不抢别人端口
    // 不开 strictPort,继续允许 vite 自动 fallback(再被占就跳 5176)
    port: 5175,
  },
});
