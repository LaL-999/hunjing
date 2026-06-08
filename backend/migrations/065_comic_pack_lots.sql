-- migration 065: comic_pack_lots(漫画包批次 + 6 月有效期跟踪)
-- Sprint ECON-2(2026-05-27 末⁴⁴)— 用户拍板 ECON-1.3 时定的"漫画态改单买漫画包"
--
-- 背景:
--   ECON-1 把 PLAN_LIMITS.comics_per_month 全档清零(原 Free 0 / Pro 1 / Max 2 / 超级 4)
--   理由:Pro 套餐免费送 1 次漫画,真实图像生成成本 ¥18-30,平台贴钱送
--   重设:漫画态从订阅福利改为"用户单买漫画包,¥30/次,有效期 6 月"
--
-- 设计(对齐 addon_credit_lots,migration 032 范本):
--   每次用户买 1 个漫画包 → INSERT 1 行 comic_pack_lots(price / expires_at)
--   创建漫画时 FIFO 扣 1 个最早购买的有效未用 lot(is_used=1 + used_comic_id)
--   漫画失败 / 取消时(ECON-2.1 留待下个 sprint)→ 退还(is_used=0)
--   cron 每天扫 expires_at < now AND is_used=0 → 设 is_expired=1
--
-- 与 addon_credit_lots 区别:
--   - addon 是"剩余 credits 数",可拆分消耗;comic_pack 是"1 次授权",原子消耗
--   - addon 1 年有效;comic_pack 6 月有效(用户原话拍板)
--
-- 与 PLAN_LIMITS.comics_per_month 关系:
--   - 双轨:enforce_comic_count_quota 优先看漫画包(有 ≥ 1 个有效未用 → 放过)
--   - 否则查 PLAN_LIMITS.comics_per_month(目前全 0,仅 founder=999999 仍走老路径)

CREATE TABLE IF NOT EXISTS comic_pack_lots (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,

    -- 购买信息(冻结)
    price_cents     INTEGER NOT NULL,    -- 购买时实付金额(分,默认 3000 = ¥30)

    -- 时刻轴
    purchased_at    TEXT NOT NULL,
    expires_at      TEXT NOT NULL,        -- purchased_at + 180 天

    -- 使用跟踪(原子,1 次授权)
    is_used         INTEGER NOT NULL DEFAULT 0,   -- 0=未用 / 1=已用
    used_at         TEXT,                          -- 实际使用时间
    used_comic_id   TEXT,                          -- 用在哪个漫画上(comic_projects.id)

    -- 过期跟踪
    is_expired      INTEGER NOT NULL DEFAULT 0,   -- 0=有效 / 1=已过期(cron 设)
    expired_at      TEXT                           -- 实际过期时间(cron 设)
);

-- 用户视角:数有效未用漫画包(创建漫画前 enforce 查这个)
CREATE INDEX IF NOT EXISTS idx_comic_pack_lots_user_avail
    ON comic_pack_lots(user_id, is_used, is_expired);

-- cron 扫描入口:今天该过期的 lot
CREATE INDEX IF NOT EXISTS idx_comic_pack_lots_expire
    ON comic_pack_lots(expires_at, is_expired);
