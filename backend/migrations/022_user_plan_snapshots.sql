-- migration 022: user_plan_snapshots(订阅价格快照)
--
-- v1(2026-05-12 E.4):"老用户老规则"承诺 — 冻结订阅时点的全部 PlanLimits 字段
-- v2(2026-05-13 Sprint C.1):**重构为 credit 模式**
--   - 删除所有 *_per_month 次数字段
--   - 加 monthly_credits_quota(月度 credit 池)
--   - 加 single_credit_price_cents(单 credit 售价分)— 给加购包预备
--
-- 协议第三章承诺(不变):
--   "如未来调价,老用户将以订阅时点的价格快照与配额规则继续服务,
--    直至主动取消订阅或明确同意新规则。变更将提前 30 日站内公告。"
--
-- v2 翻译到 credit 数据层:
--   - 用户付费订阅 → 冻结当时的 plan + 价格 + 月度 credit 池
--   - 平台改默认 credit 池不影响老快照
--   - cron month_reset 走快照优先(快照 monthly_credits_quota → fallback PLAN_DEFAULTS)
--   - 用户取消 → state='cancelled'(当期 credit 仍可用至 current_period_end)
--   - 用户同意新规则 → 老快照 state='upgraded' + 新建 active 快照
--
-- 关键设计:
--   1. price_cents / single_credit_price_cents 用 INTEGER(分)— 避浮点
--   2. 冻结 monthly_credits_quota + 非 AI 类硬限 — 真正"冻结"
--   3. Free / Founder 档不写快照(无价格承诺)
--   4. 当前未接支付通道,本表仅 schema;真接入支付后调 service 写入

CREATE TABLE IF NOT EXISTS user_plan_snapshots (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- 订阅档位(只对付费档生效;free/founder 不写此表)
    plan            TEXT NOT NULL
                      CHECK (plan IN ('pro', 'max', 'super_max')),
    billing_cycle   TEXT NOT NULL
                      CHECK (billing_cycle IN ('monthly', 'yearly')),

    -- 冻结的价格(单位:分,避浮点)
    -- 当前(Sprint C.1 credit 模式)默认:
    --   pro       monthly =  13800 / yearly = 148800
    --   max       monthly =  43800 / yearly = 472800
    --   super_max monthly = 138800 / yearly = 1498800
    price_cents     INTEGER NOT NULL,

    -- v2 核心:冻结的月度 credit 池
    -- 当前默认:pro=970 / max=3400 / super_max=12000(每升一档 -10% 阶梯优惠后)
    monthly_credits_quota   INTEGER NOT NULL,

    -- 冻结的单 credit 售价(分,1c=¥0.10 成本基线 × 阶梯优惠 / 毛利倍率)
    -- 当前默认:pro=14(¥0.1423/c) / max=13(¥0.1288/c) / super_max=12(¥0.1157/c)
    single_credit_price_cents INTEGER NOT NULL,

    -- 非 AI 类硬限(保留 — 这些不消耗 credit,而是平台容量约束)
    characters_per_project      INTEGER NOT NULL,
    projects_total              INTEGER NOT NULL,
    reshape_max_percent         INTEGER NOT NULL,

    -- 时刻轴
    grandfather_at          TEXT NOT NULL,
    current_period_start    TEXT NOT NULL,
    current_period_end      TEXT NOT NULL,

    -- 状态机
    state           TEXT NOT NULL
                      CHECK (state IN ('active', 'cancelled', 'expired', 'upgraded')),

    notes           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_user_plan_snapshots_user_state
    ON user_plan_snapshots(user_id, state);
CREATE INDEX IF NOT EXISTS idx_user_plan_snapshots_updated
    ON user_plan_snapshots(updated_at);
