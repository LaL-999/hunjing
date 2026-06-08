-- BYOK(自携密钥)— 用户配置的 API key 加密存储
--
-- 设计:
--   一个用户可配多个 provider(DeepSeek / Qwen / GLM / Doubao / Kimi / custom)
--   每个 provider 一行记录,is_default 标记当前 LLM 调用默认走哪个
--   api_key 用 Fernet AES-128 对称加密(密钥来自 env BYOK_ENCRYPTION_KEY)
--   后端永远不返回明文 key 给前端 — 只返脱敏 mask("sk-xxxx...xxx")
--
-- 字段:
--   provider:deepseek / qwen / zhipu / doubao / moonshot / custom
--   display_name:展示名(用户填,如"工作号 DeepSeek")
--   base_url:接口地址(预设 5 家固定值,custom 用户填)
--   model_name:具体模型(deepseek-chat / qwen-plus / glm-4-plus / ...)
--   api_key_encrypted:Fernet 密文(base64 string)
--   api_key_mask:展示用脱敏("sk-12....xy89")
--   is_default:当前 BYOK 激活时用哪个(单 user 仅一行 = 1)
--   last_test_ok:上次连通性测试是否成功
--   last_test_at:上次测试时间
--   created_at / updated_at:审计
--
-- 业务约束:
--   user_id + provider + model_name 唯一(同模型重配会覆盖)
--   BYOK 订阅过期后 configs 保留(用户下次激活直接生效)— 这是产品决定,不删

CREATE TABLE IF NOT EXISTS byok_configs (
    id                 TEXT PRIMARY KEY,
    user_id            TEXT NOT NULL,
    provider           TEXT NOT NULL,
    display_name       TEXT,
    base_url           TEXT NOT NULL,
    model_name         TEXT NOT NULL,
    api_key_encrypted  TEXT NOT NULL,
    api_key_mask       TEXT NOT NULL,
    is_default         INTEGER NOT NULL DEFAULT 0,
    last_test_ok       INTEGER,
    last_test_at       TEXT,
    last_test_error    TEXT,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    UNIQUE (user_id, provider, model_name)
);

CREATE INDEX IF NOT EXISTS idx_byok_configs_user_id ON byok_configs(user_id);
CREATE INDEX IF NOT EXISTS idx_byok_configs_user_default ON byok_configs(user_id, is_default);
