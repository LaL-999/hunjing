import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// https://vite.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      // 同源避 CORS:前端 fetch('/api/...') 实际打到 backend :8000
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
});
