/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_INSIGHTS_BASE?: string;
  readonly VITE_ADMIN_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
