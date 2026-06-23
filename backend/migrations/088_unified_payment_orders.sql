-- migration 088:统一订单中心 payment_orders(2026-06-09 商业化重塑 · 第一期)
--
-- 背景:平台原有 3 套分裂收费(订阅 / BYOK 月卡 / 配额包),只有 BYOK 那套
-- (byok_payment_orders)有真实收款。统一支付内核把所有收费场景收敛到一张
-- 订单表 + 一套履约路由。个人主体阶段渠道 = 微信扫码 + 截图人工复核。
--
-- 四层解耦:① SKU 目录(catalog.py 常量)② 本订单表 ③ 渠道适配 ④ 履约路由
-- 权益存储(user_plan_snapshots / byok_subscriptions / credit lots)不动,
-- 订单 paid 后由 fulfillment_service 路由到对应权益系统。
--
-- 用户拍板(2026-06-09):
--   - 个人主体扫码;审核 = 截图 + 人工复核(Vision LLM 仅辅助预填)
--   - byok_payment_orders 一次性迁移合并进本表(见文件末尾 INSERT)
--
-- ⚠ 迁移 runner 语义:整文件单事务 executescript,失败回滚全文件。
--   所以 DDL 必须严格 IF NOT EXISTS;迁移 INSERT 用确定性 id 防重跑重插。

CREATE TABLE IF NOT EXISTS payment_orders (
    id                       TEXT PRIMARY KEY,
    user_id                  TEXT NOT NULL,

    -- ===== 商品(下单时冻结)=====
    sku_code                 TEXT NOT NULL,          -- 'sub_pro_monthly' / 'byok_1m' / 'credit_medium'
    sku_title                TEXT NOT NULL,          -- 冻结的商品名(显示用,防 catalog 改名影响历史单)
    category                 TEXT NOT NULL,          -- 'subscription' / 'byok' / 'credit'(履约路由用)
    amount_cents             INTEGER NOT NULL,       -- 订单总额(分)
    quantity                 INTEGER NOT NULL DEFAULT 1,  -- months / packs 等倍率
    sku_meta_json            TEXT NOT NULL DEFAULT '{}',  -- 冻结的 SKU 参数(plan/cycle/credits…)

    -- ===== 渠道 + 状态 =====
    channel                  TEXT NOT NULL DEFAULT 'wechat_qr_manual',
    -- pending(待付)/ submitted(已传凭证待审)/ paid(已确认)/
    -- rejected(驳回)/ manual_review(需人工)/ expired / refunded
    status                   TEXT NOT NULL DEFAULT 'pending',

    -- ===== 凭证(扫码截图渠道)=====
    proof_image_path         TEXT,
    proof_image_hash         TEXT,                   -- SHA-256,防同图重复提交
    proof_submitted_at       TEXT,

    -- ===== Vision LLM 预审(辅助,非终审)=====
    detected_amount_cents    INTEGER,
    detected_payee_name      TEXT,
    detected_pay_time        TEXT,
    detected_transaction_id  TEXT,
    detection_raw_json       TEXT,
    detection_run_at         TEXT,
    detection_pass_reason    TEXT,

    -- ===== 人工审核(终审)=====
    reviewed_by              TEXT,                   -- 审核管理员标识
    reviewed_at              TEXT,
    rejected_reason          TEXT,

    -- ===== 履约 =====
    fulfilled_at             TEXT,
    fulfillment_ref          TEXT,                   -- 履约产物 id(snapshot_id / subscription_id / lot_id)
    fulfillment_meta_json    TEXT,

    -- ===== 时刻轴 =====
    created_at               TEXT NOT NULL,
    expires_at               TEXT NOT NULL,          -- 待付超时(默认 24h)

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_payment_orders_user
    ON payment_orders(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_payment_orders_status
    ON payment_orders(status);
CREATE INDEX IF NOT EXISTS idx_payment_orders_category
    ON payment_orders(category);
CREATE INDEX IF NOT EXISTS idx_payment_orders_proof_hash
    ON payment_orders(proof_image_hash);
-- 交易号全平台唯一(防一张截图激活多单)— NULL 不参与
CREATE UNIQUE INDEX IF NOT EXISTS idx_payment_orders_txid_uniq
    ON payment_orders(detected_transaction_id)
    WHERE detected_transaction_id IS NOT NULL;


-- ============================================================
-- 一次性迁移:byok_payment_orders → payment_orders
--
-- 确定性 id = 'legacy_byok_' || 原 id,重跑时 WHERE NOT EXISTS 命中已迁移
-- 行,不重插(幂等)。原 byok_payment_orders 表保留只读,不删。
--
-- 字段映射:
--   category        = 'byok'
--   sku_code        = 'byok_' || months || 'm'
--   sku_title       = '自携密钥 · ' || months || ' 个月'
--   quantity        = months
--   fulfillment_ref = activated_subscription_id
--   fulfilled_at    = status='paid' ? proof_submitted_at(近似): NULL
-- ============================================================
INSERT INTO payment_orders (
    id, user_id, sku_code, sku_title, category, amount_cents, quantity,
    sku_meta_json, channel, status,
    proof_image_path, proof_image_hash, proof_submitted_at,
    detected_amount_cents, detected_payee_name, detected_pay_time,
    detected_transaction_id, detection_raw_json, detection_run_at,
    detection_pass_reason,
    rejected_reason,
    fulfilled_at, fulfillment_ref,
    created_at, expires_at
)
SELECT
    'legacy_byok_' || b.id,
    b.user_id,
    'byok_' || b.months || 'm',
    '自携密钥 · ' || b.months || ' 个月',
    'byok',
    b.amount_cents,
    b.months,
    '{"months":' || b.months || '}',
    'wechat_qr_manual',
    b.status,
    b.proof_image_path, b.proof_image_hash, b.proof_submitted_at,
    b.detected_amount_cents, b.detected_payee_name, b.detected_pay_time,
    -- 迁移时给交易号加前缀避免跟新单 UNIQUE 冲突(历史号已用过)
    CASE WHEN b.detected_transaction_id IS NOT NULL
         THEN 'legacy:' || b.detected_transaction_id ELSE NULL END,
    b.detection_raw_json, b.detection_run_at, b.detection_pass_reason,
    b.rejected_reason,
    CASE WHEN b.status = 'paid' THEN b.proof_submitted_at ELSE NULL END,
    b.activated_subscription_id,
    b.created_at, b.expires_at
FROM byok_payment_orders b
WHERE NOT EXISTS (
    SELECT 1 FROM payment_orders po WHERE po.id = 'legacy_byok_' || b.id
);
