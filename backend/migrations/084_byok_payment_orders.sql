-- BYOK 个人收款码支付订单 + 智能审核记录
--
-- 流程:
--   1. 用户点购买 → create_order 生成 pending 订单 + 显示二维码
--   2. 用户微信扫码付款 → 上传截图 → submit_proof
--   3. 后台 Vision LLM 审核截图 → 自动校验金额/收款方/时间/单号
--   4. 通过 → status=paid + 自动激活 BYOK 订阅
--      不通过 → status=manual_review 通知管理员
--
-- 字段说明:
--   id:订单 ID(同时充当用户备注号,UUID 前 8 位人话化)
--   amount_cents:订单总额(分),= 3000 * months
--   months:订阅月数(1-12)
--   status:pending / submitted / paid / rejected / manual_review / expired
--
--   proof_image_path:用户上传截图本地相对路径(data/payment_proofs/...)
--   proof_image_hash:SHA-256 16 进制(防同图重复提交)
--   proof_submitted_at:用户上传截图的时间
--
--   detected_*:Vision LLM 识别出的 4 项字段
--   detection_raw_json:LLM 原始输出全量(审计 / debug 用)
--   detection_pass_reason:通过原因 OR 不通过原因
--
--   activated_subscription_id:审核通过后挂载的 BYOK 订阅 ID
--   rejected_reason:rejected / manual_review 时的具体原因(给前端友好提示)
--
--   created_at:订单创建时间
--   expires_at:24h 后(用户必须在此前上传截图)
--
-- 唯一约束:
--   detected_transaction_id 全平台唯一(防一张截图激活多个订单)— 注:NULL 不参与 UNIQUE 计数

CREATE TABLE IF NOT EXISTS byok_payment_orders (
    id                            TEXT PRIMARY KEY,
    user_id                       TEXT NOT NULL,
    amount_cents                  INTEGER NOT NULL,
    months                        INTEGER NOT NULL DEFAULT 1,
    status                        TEXT NOT NULL DEFAULT 'pending',

    proof_image_path              TEXT,
    proof_image_hash              TEXT,
    proof_submitted_at            TEXT,

    detected_amount_cents         INTEGER,
    detected_payee_name           TEXT,
    detected_pay_time             TEXT,
    detected_transaction_id       TEXT,
    detection_raw_json            TEXT,
    detection_run_at              TEXT,
    detection_pass_reason         TEXT,

    activated_subscription_id     TEXT,

    rejected_reason               TEXT,

    created_at                    TEXT NOT NULL,
    expires_at                    TEXT NOT NULL,

    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_byok_orders_user_id ON byok_payment_orders(user_id);
CREATE INDEX IF NOT EXISTS idx_byok_orders_status ON byok_payment_orders(status);
CREATE INDEX IF NOT EXISTS idx_byok_orders_proof_hash ON byok_payment_orders(proof_image_hash);
CREATE UNIQUE INDEX IF NOT EXISTS idx_byok_orders_transaction_id_uniq
    ON byok_payment_orders(detected_transaction_id)
    WHERE detected_transaction_id IS NOT NULL;
