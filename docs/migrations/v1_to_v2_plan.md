# 订阅模式 v1 → v2 数据迁移(开发者本人业务 DB 手动跑)

**适用对象**:Sprint D.1 之前已有 `backend/data/huimeng.db` 业务库的开发者本人 / 早期内测用户。
**测试 DB**:不适用(每次 fresh,migration 001 直接用新 CHECK)。
**生产 DB**:目前还没有上线,本文档作为未来 v1→v2 升级路径参考。

## 背景

Sprint D.1(2026-05-12)用户拍板把订阅模式从 v1(`free`/`standard`/`super`)改为 v2(`free`/`pro`/`max`/`super_max`),覆盖 Anthropic 风 4 档。

`migration 001_users.sql` 的 `CHECK` 已直接更新为 v2 字面量。**fresh DB(测试)自动使用新 schema 无需迁移**。

但 **已存在的 v1 DB** 因为:
- `CREATE TABLE IF NOT EXISTS` 不会触发重建 → 老库的 `CHECK (plan IN ('free','standard','super'))` 仍生效
- 老 users 表里的 `plan='standard'` / `'super'` 数据需要转换

→ 需要**手动跑下面的 SQL**。

## 迁移 SQL(SQLite)

```sql
-- 1. 关外键,准备表重建
PRAGMA foreign_keys = OFF;

-- 2. 重命名老表(它的 CHECK 还是 v1)
ALTER TABLE users RENAME TO users_old_v1;

-- 3. 创建带新 CHECK 的 users 表(对齐 migration 001 v2 schema)
CREATE TABLE users (
    id              TEXT PRIMARY KEY,
    phone           TEXT UNIQUE,
    email           TEXT UNIQUE,
    plan            TEXT NOT NULL DEFAULT 'free'
                      CHECK (plan IN ('free', 'pro', 'max', 'super_max')),
    quota_reset_at  TEXT,
    register_ip     TEXT,
    register_ua     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    CHECK (phone IS NOT NULL OR email IS NOT NULL)
);

-- 4. 拷贝数据 + 转换 plan v1→v2
INSERT INTO users (
    id, phone, email, plan, quota_reset_at,
    register_ip, register_ua, created_at, updated_at
)
SELECT
    id, phone, email,
    CASE plan
        WHEN 'standard' THEN 'pro'
        WHEN 'super'    THEN 'max'
        WHEN 'free'     THEN 'free'
        ELSE 'free'   -- 兜底:未知值落 free
    END,
    quota_reset_at, register_ip, register_ua, created_at, updated_at
FROM users_old_v1;

-- 5. 删老表 + 重建索引
DROP TABLE users_old_v1;

CREATE INDEX IF NOT EXISTS idx_users_phone ON users(phone);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- 6. 验证
SELECT plan, COUNT(*) AS cnt FROM users GROUP BY plan;
-- 期望输出仅含 'free' / 'pro' / 'max' / 'super_max'

-- 7. 开外键
PRAGMA foreign_keys = ON;
```

## 怎么跑

```bash
cd /c/Users/Administrator/Desktop/huimeng/backend
sqlite3 data/huimeng.db < ../docs/migrations/v1_to_v2_plan.sql
```

或直接打开 sqlite shell 粘贴 SQL。

## 价格快照(老用户老规则)

协议第三章保证"老用户老规则":
- 老 `standard` (¥38/月) 用户**保留 ¥38 价格快照**,续费时维持 standard 配额 + ¥38 价格
- 老 `super` (¥268/月) 用户同理保留 ¥268

**E 阶段 E.4 sprint 加 `user_plan_snapshots` 表落地**(plan + price + grandfather_at 字段),本 migration 不动。当前价格快照逻辑还未实现,等真有付费用户前必须落地。
