-- migration 031: credit_transactions(credit 变更明细审计 — 取代老 usage_logs)
-- Sprint D.9 C.1(2026-05-13)— ADR_credit_quota_重构.md 落地
--
-- 取代 usage_logs(已删):
--   - 老 usage_logs 记"次数"模式的消耗(action=continuation/refine/extract/comic, quota_consumed=N)
--   - 新 credit_transactions 记"credit"模式的余额变更,精确到 credit 单位
--
-- 设计:
--   - delta 字段:正数 = 充值/退款,负数 = 消耗。所有变更一行一条
--   - wallet 区分扣的是订阅 wallet 还是加购 wallet(消耗顺序铁律:优先订阅)
--   - kind 表达事件类型(grant / purchase / consume / refund / month_reset / addon_expire)
--   - action 表达业务动作(continuation / extract / refine / comic_create / comic_batch /
--                          vote_style / planner / vision_describe / image_gen 等)
--   - related_id 关联到业务记录(sim_id / comic_id / comic_batch_id / addon_lot_id 等)
--   - cost_yuan 记真实 LLM 成本(为运营 audit / 季度调价评估保留)
--
-- 查询入口:
--   - 月度消耗汇总:WHERE user_id=? AND kind='consume' AND created_at > month_start
--   - 单业务全周期消耗:WHERE related_id=? AND kind IN ('consume', 'refund')
--   - 退款审计:WHERE kind='refund'

CREATE TABLE IF NOT EXISTS credit_transactions (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- credit 变更:正数 = 充值/退款,负数 = 消耗
    delta       INTEGER NOT NULL,

    -- 钱包来源(subscription / addon)
    -- consume 时:用了 subscription 多少 + addon 多少(2 条独立记录,优先扣 subscription)
    -- grant / month_reset:wallet=subscription
    -- purchase:wallet=addon
    -- refund:回到原 wallet
    -- addon_expire:wallet=addon
    wallet      TEXT NOT NULL
                  CHECK (wallet IN ('subscription', 'addon')),

    -- 事件类型
    kind        TEXT NOT NULL
                  CHECK (kind IN (
                    'subscribe_grant',   -- 月订阅发放(month_reset / 首次订阅)
                    'addon_purchase',    -- 加购包购买
                    'consume',           -- 用户消费(AI 调用扣)
                    'refund',            -- 平台退款(失败 / 用户取消按进度退等)
                    'month_reset',       -- 月度清零(subscription 钱包清零事件,delta = -剩余)
                    'addon_expire'       -- 加购包过期(delta = -剩余 c)
                  )),

    -- 业务动作分类(便于运营 audit / 用户透明计量 UI)
    -- 取值开放(refine / continuation / extract / comic_create / comic_planner /
    --        comic_scripter / comic_visual_assets / comic_style_director / comic_anchor /
    --        comic_director / comic_image_gen / comic_visual_qa / comic_inpainter /
    --        comic_typesetter / etc.)
    action      TEXT,

    -- 关联业务 ID(sim_id / comic_id / comic_batch_id / addon_credit_lot_id 等)
    related_id  TEXT,

    -- 真实 LLM 成本(元,REAL)— 运营财务 audit 用
    -- consume 时记 LLM 真实账单成本;grant / purchase / refund 通常为 0
    cost_yuan   REAL NOT NULL DEFAULT 0,

    -- 扩展元数据(JSON 自由字段)
    -- 典型:input_tokens / output_tokens / vendor / model / batch_index / refund_phase 等
    metadata    TEXT,

    created_at  TEXT NOT NULL
);

-- 查询索引
-- 1. 用户视角:本月所有 transaction(列出"我花了哪些 credit")
CREATE INDEX IF NOT EXISTS idx_credit_tx_user_time
    ON credit_transactions(user_id, created_at);

-- 2. 业务视角:某个漫画 / 推演的全部消耗(中途取消时算退款金额)
CREATE INDEX IF NOT EXISTS idx_credit_tx_related
    ON credit_transactions(related_id);

-- 3. 运营审计:某 kind 全平台变更(如月度 grant 总额 / 加购总收入)
CREATE INDEX IF NOT EXISTS idx_credit_tx_kind_time
    ON credit_transactions(kind, created_at);
