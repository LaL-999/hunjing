/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_INSIGHTS_BASE?: string;
  readonly VITE_ADMIN_TOKEN?: string;
  // 2026-06-25:insights 前端跨域调主平台(BYOK 审核等)的 base
  readonly VITE_PLATFORM_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
