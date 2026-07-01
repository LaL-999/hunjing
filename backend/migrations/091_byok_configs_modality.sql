-- BYOK 生图模型配置(item2,v5 2026-07-02)—— byok_configs 加 modality 区分文本/图像
--
-- 背景:
--   原 byok_configs 只存文本 LLM 配置(deepseek-chat / qwen-plus ...)。
--   v5 把「自携密钥」升为主打(¥5/月解锁全功能),漫创态需要用户自己的图像模型 key,
--   否则 BYOK 用户生图会回落到平台图像 key = 白嫖创始人图像 API(与 item7 同源风险)。
--
-- 设计:
--   加 modality 列('text' | 'image'),一个用户可各配一套:
--     - 一个 default 文本模型(推演 / 抽图谱 / 剧本 / 续写 用)
--     - 一个 default 图像模型(漫创态生图 用)
--   is_default 语义改为「每 modality 一个默认」(service 层清默认时按 modality 隔离)。
--   老数据无 modality → 默认 'text',完全向后兼容。
--
-- 幂等:ALTER ADD COLUMN 在 SQLite 非幂等,但 app/main.py::_auto_apply_migrations
--   捕获 "duplicate column name" OperationalError 后跳过整个文件(第 2 次启动起 noop);
--   NOT NULL + DEFAULT 'text' 保证既有行回填,首启原子应用 ALTER + INDEX。

ALTER TABLE byok_configs ADD COLUMN modality TEXT NOT NULL DEFAULT 'text';

CREATE INDEX IF NOT EXISTS idx_byok_configs_user_modality_default
    ON byok_configs(user_id, modality, is_default);
