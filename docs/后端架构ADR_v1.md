# 后端架构决策记录(ADR)v1

最后更新:2026-05-09 / Day 5(待用户批准 → 进入实施)

---

## 0. 决策摘要

| 项 | 决策 | 来源 |
|---|---|---|
| 目录结构 | `huimeng/backend/`(与 frontend/ 平级) | 2026-05-09 |
| 包管理 | **复用项目根 `.venv` + pip**(backend 与 simulate.py 共用同一 Python 环境) | 2026-05-09 Sprint 1.A 收尾修订(原 ADR "uv" 因机器未装 uv 暂搁置;阶段 2 多人或环境分裂时再上 uv) |
| Web 框架 | FastAPI | 项目记忆已定 |
| 数据库 | SQLite(阶段 1-2),阶段 3 切 PostgreSQL | 项目记忆已定 |
| **ORM** | **不引入,直接 SQL DDL + sqlite3 标准库** | 2026-05-09 用户决议(选项 ①A) |
| **认证 OTP** | **纯邮箱 OTP(SMTP)**;phone 字段保留 nullable 但不参与登录 | 2026-05-09 用户决议(②B 短信优先撤回 → 改纯邮箱) |
| **simulate.py 服务化** | **阶段 1 不改,留 Week 3** | 2026-05-09 用户决议(选项 ③B) |
| 配置加载 | 沿用 .env 手写解析(对齐 simulate.py) | ADR |
| API 风格 | RESTful | 角色对焦组件设计已用 |
| 测试框架 | pytest | 标准 |
| 部署形态 | 阶段 1:单进程 FastAPI + SQLite;阶段 3 拆分 | YAGNI |

---

## 1. 阶段 1 后端范围(显式定义,避免范围蔓延)

### 1.1 ✅ 阶段 1 做
- FastAPI 脚手架 + SQLite + JWT
- Auth(短信/邮箱 OTP 双通道 + 验证 + JWT 签发)
- Project / Character / Relationship / Event CRUD
- **Refine 服务**(对接 `prompts/character_focus.md` v3 + §16 三个兜底)
- Consent 记录(对接前端 UploadOverlay 的 consent phase)
- UsageLog 表(为后续配额系统铺路,阶段 1 只记录不限流)

### 1.2 ❌ 阶段 1 不做(YAGNI 强制执行)
- Generation 服务(simulate.py 服务化,留 Week 3)
- Subscription 完整流程(留 Week 4 + 接支付)
- 推送 / 通知系统
- 分布式 / 缓存(Redis) / 队列(Celery)
- Alembic 迁移工具(手工 SQL DDL + 版本号文件名足够)
- Docker Compose 多服务(单进程足够)
- 完整 OpenAPI 自定义文档(FastAPI 自带 /docs)
- 多语言 i18n(阶段 1 只中文)
- 限流中间件(用户量未到)
- 监控告警栈(留阶段 2 接 Sentry)

---

## 2. 目录结构

```
huimeng/
├── backend/                          ★ 新建,与 frontend/ 平级
│   ├── pyproject.toml                uv 项目
│   ├── README.md                     启动说明
│   ├── .env.example                  环境变量模板
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                   FastAPI 入口 + middleware + 路由挂载
│   │   ├── config.py                 .env 加载 + 配置 dataclass
│   │   ├── deps.py                   FastAPI Depends(get_db / get_current_user)
│   │   ├── db.py                     sqlite3 连接 + 事务 helper
│   │   ├── models/                   纯 dataclass(数据库表的 Python 表示)
│   │   │   ├── __init__.py
│   │   │   ├── user.py
│   │   │   ├── project.py
│   │   │   ├── character.py
│   │   │   ├── relationship.py
│   │   │   ├── event.py
│   │   │   ├── refinement.py
│   │   │   └── consent.py
│   │   ├── schemas/                  Pydantic(API 输入/输出 schema)
│   │   │   └── (同 models 命名)
│   │   ├── routers/                  FastAPI APIRouter,薄层
│   │   │   ├── auth.py
│   │   │   ├── projects.py
│   │   │   ├── characters.py
│   │   │   ├── refine.py
│   │   │   └── consent.py
│   │   ├── services/                 业务逻辑,不依赖 FastAPI,独立可测
│   │   │   ├── auth_service.py       OTP 流程 + JWT
│   │   │   ├── otp_sms_service.py    阿里云短信(主通道)
│   │   │   ├── otp_email_service.py  SMTP(fallback)
│   │   │   ├── refine_service.py     character_focus.md v3 LLM 调用 + 3 兜底
│   │   │   └── llm_client.py         DeepSeek 客户端(对齐 simulate.py)
│   │   └── middleware/
│   │       └── error_handler.py
│   ├── migrations/                   SQL DDL(手工版本号)
│   │   ├── 001_users.sql
│   │   ├── 002_consent.sql
│   │   ├── 003_projects.sql
│   │   ├── 004_characters.sql
│   │   ├── 005_relationships_events.sql
│   │   ├── 006_refine_tables.sql
│   │   └── 007_usage_log.sql
│   ├── data/                         SQLite 文件位置(.gitignore)
│   │   └── huimeng.db
│   ├── scripts/
│   │   ├── init_db.py                跑所有 migrations
│   │   └── seed_dev.py               生成开发期假数据(给前端联调用)
│   └── tests/
│       ├── conftest.py
│       ├── test_auth.py
│       ├── test_projects.py
│       └── test_refine.py
└── ...
```

### 2.1 关键设计取舍说明

- **`models/` 用 dataclass,不用 SQLAlchemy** — 阶段 1 schema 简单(7 张表),dataclass + 手写 sql 控制力最强。`models/` 只负责"表的 Python 表示",不带行为。
- **`schemas/` 用 Pydantic** — 负责 API 输入/输出 schema 校验。**与 models 解耦**:数据库 schema 变化不必同步动 API 契约(可演进性)。
- **`services/` 是业务逻辑核心,不 import FastAPI** — 这层独立可测;阶段 3 切 PG 时不会动 services。
- **`routers/` 只做"HTTP 进 → 调 service → 出 HTTP"** — 薄层,无业务逻辑。

---

## 3. 数据库设计(7 张表完整 DDL,阶段 1 ship)

合并自 `docs/MVP阶段1_初始态流程图.md` §数据模型 + `docs/MVP阶段1_角色对焦组件设计.md` §8。

```sql
-- 001_users.sql
CREATE TABLE users (
    id              TEXT PRIMARY KEY,                     -- uuid
    phone           TEXT UNIQUE,                          -- 国内 11 位
    email           TEXT UNIQUE,                          -- 海外用户 / fallback
    plan            TEXT NOT NULL DEFAULT 'free'          -- free / standard / super
                      CHECK (plan IN ('free', 'standard', 'super')),
    quota_reset_at  TEXT,                                  -- 下次配额重置时间(月初)
    register_ip     TEXT,
    register_ua     TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    CHECK (phone IS NOT NULL OR email IS NOT NULL)        -- 至少一个联系方式
);
CREATE INDEX idx_users_phone ON users(phone);
CREATE INDEX idx_users_email ON users(email);

-- 002_consent.sql
CREATE TABLE consent_records (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    version         TEXT NOT NULL,                         -- "v1" 等
    checks          TEXT NOT NULL,                         -- JSON: {adult,terms,privacy,pricing}
    accepted_at     TEXT NOT NULL,
    ip              TEXT,
    ua              TEXT
);
CREATE INDEX idx_consent_user ON consent_records(user_id);

-- 003_projects.sql
CREATE TABLE projects (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    type            TEXT NOT NULL                          -- novel / comic / anime / generic
                      CHECK (type IN ('novel','comic','anime','generic')),
    tags            TEXT NOT NULL DEFAULT '[]',            -- JSON 数组
    mode            TEXT NOT NULL DEFAULT 'initial'        -- initial / middle / end / cycle
                      CHECK (mode IN ('initial','middle','end','cycle')),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX idx_projects_user ON projects(user_id);

-- 004_characters.sql
CREATE TABLE characters (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,                         -- 必填
    identity        TEXT NOT NULL DEFAULT '',
    personality     TEXT NOT NULL DEFAULT '',
    quotes          TEXT NOT NULL DEFAULT '[]',            -- JSON 数组
    no_go_list      TEXT NOT NULL DEFAULT '[]',            -- JSON 数组
    position_x      REAL NOT NULL DEFAULT 0,
    position_y      REAL NOT NULL DEFAULT 0,
    position_z      REAL NOT NULL DEFAULT 0,
    color           TEXT,                                   -- hex
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX idx_characters_project ON characters(project_id);

-- 005_relationships_events.sql
CREATE TABLE relationships (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_id       TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    target_id       TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    type            TEXT NOT NULL                          -- 亲属/敌对/朋友/情侣/师徒/同事/其他
                      CHECK (type IN ('亲属','敌对','朋友','情侣','师徒','同事','其他')),
    description     TEXT NOT NULL DEFAULT '',
    color           TEXT,
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_relationships_project ON relationships(project_id);

CREATE TABLE events (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    description     TEXT NOT NULL,
    participants    TEXT NOT NULL DEFAULT '[]',            -- JSON: [character_id]
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_events_project ON events(project_id);

-- 006_refine_tables.sql(从角色对焦组件设计 §8 直接搬)
CREATE TABLE refine_sessions (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_id         TEXT NOT NULL REFERENCES users(id),
    triggered_at    TEXT NOT NULL,
    completed_at    TEXT,
    skip_reason     TEXT,
    tokens_input    INTEGER,
    tokens_output   INTEGER,
    cost_yuan       REAL,
    duration_ms     INTEGER
);
CREATE INDEX idx_refine_session_project ON refine_sessions(project_id);

CREATE TABLE character_refinements (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES refine_sessions(id) ON DELETE CASCADE,
    character_id        TEXT NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    suggestion_kind     TEXT NOT NULL CHECK (suggestion_kind IN
                          ('identity_补全','personality_补充','quote_补充',
                           'no_go_补充','consistency_警告')),
    suggestion_text     TEXT NOT NULL,
    suggestion_payload  TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','accepted','rejected','edited','skipped')),
    user_edit           TEXT,
    actioned_at         TEXT
);
CREATE INDEX idx_refine_status ON character_refinements(session_id, status);

-- 007_usage_log.sql(配额追踪基础设施,阶段 1 只记录不限流)
CREATE TABLE usage_logs (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL REFERENCES users(id),
    action          TEXT NOT NULL,                         -- refine / generate / export / ...
    quota_consumed  INTEGER NOT NULL DEFAULT 0,
    cost_yuan       REAL NOT NULL DEFAULT 0,
    metadata        TEXT,                                   -- JSON
    created_at      TEXT NOT NULL
);
CREATE INDEX idx_usage_user_time ON usage_logs(user_id, created_at);
```

### 3.1 SQLite 配置

- 文件:`backend/data/huimeng.db`(.gitignore)
- WAL 模式:`PRAGMA journal_mode=WAL`(并发友好)
- 强制外键:`PRAGMA foreign_keys=ON`(默认关,必须显式开)
- 连接管理:每个请求一个 connection(走 FastAPI Depends),不需要连接池

---

## 4. 邮箱 OTP 通道设计(2026-05-09 撤回短信方案后定锚)

### 4.1 为什么撤回短信优先

短信通道在创始人当前处境下有三层硬约束,综合判断成本远超收益:
- **企业认证阻塞**:正式签名 + 模板需要 ICP 备案 + 营业执照,公司未注册前只能用阿里云个人测试号(每天 100 条)
- **审核窗口阻塞**:正式签名审核 7-14 天,期间不能上线
- **持续成本**:0.045 元/条 + 防刷码运维成本

撤回意味着"阶段 1 上线不挂在公司注册之上",项目节奏更自由。

### 4.2 邮箱 OTP 设计

- **单通道,无 fallback**(YAGNI;避免"通道可热切换"的接口复杂度)
- **服务**:SMTP(标准库 `smtplib`),阶段 1 推荐 QQ 邮箱授权码或 SMTP2GO 免费档
- **OTP 规范**:6 位数字,5 分钟有效,同一邮箱 60 秒内只能重发 1 次
- **存储**:`otp_codes` 表(短期,定时清理过期记录)
- **防刷**:同一 IP 24 小时内最多 10 次发码请求

### 4.3 限制(明确告知用户体验权衡)

- **到达率**:邮箱 OTP 到达率 92-95%(短信 99%);用户可能因为邮件进了垃圾箱拿不到码 → 前端在"未收到?"按钮里提示"检查垃圾邮件 + 60 秒后重试"
- **收码体验**:用户需切换应用到邮箱客户端拿验证码 → 流失率比短信高(行业数据约 +5-15%)
- **海外用户优势**:邮箱 OTP 天然支持海外用户,无国内手机号要求

### 4.4 OTP 服务接口(代码草稿)

```python
# app/services/otp_email_service.py
import smtplib
from email.message import EmailMessage
from app.config import settings

def send_email_otp(target_email: str, code: str) -> None:
    msg = EmailMessage()
    msg["Subject"] = f"浑晶登录验证码:{code}"
    msg["From"] = settings.smtp_from
    msg["To"] = target_email
    msg.set_content(
        f"你正在登录浑晶。\n\n验证码:{code}\n\n"
        f"有效期 5 分钟。如果不是你本人操作,请忽略此邮件。"
    )
    with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port) as s:
        s.login(settings.smtp_user, settings.smtp_pass)
        s.send_message(msg)


# app/services/auth_service.py(简化伪代码)
import re

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

def is_email(target: str) -> bool:
    return bool(EMAIL_RE.fullmatch(target))

def send_otp(target_email: str) -> None:
    if not is_email(target_email):
        raise InvalidTarget("不是合法邮箱")
    if db.recently_sent(target_email, within_seconds=60):
        raise RateLimited("60 秒内只能发一次,请稍后重试")
    code = gen_code()
    send_email_otp(target_email, code)
    db.store_otp(target=target_email, code=code, expires_at=now() + 300)


def verify_otp(target_email: str, input_code: str) -> User:
    record = db.get_active_otp(target_email)
    if not record or record.code != input_code:
        raise InvalidCode()
    db.delete_otp(target_email)
    user = db.get_or_create_user(email=target_email)
    return user
```

### 4.5 users 表的 phone 字段处理

users 表保留 `phone` 字段(nullable),理由:
- 阶段 2 / 3 升级到双通道(短信 + 邮箱)时,数据库 schema 不需要 migration 加列
- nullable 字段对当前邮箱 OTP 流程零影响
- 删掉 phone 字段后再加回来,要写 migration 脚本 + 数据回填,不如保留

代码层面 phone 字段**完全不参与登录流程**,登录链路只走 email。

---

## 5. JWT 认证设计

- 算法:HS256
- Secret:走 `.env` 的 `HUIMENG_JWT_SECRET`(必须 32+ 字节随机串,我提供生成命令)
- **单 token 模式**:无 refresh token(降低复杂度)
- TTL:7 天(过期重发 OTP)
- payload:`{"user_id": "<uuid>", "exp": <ts>, "plan": "free|standard|super"}`
- 前端:localStorage 存 token,自动加 `Authorization: Bearer xxx` header

**为什么不用 refresh token**:阶段 1 用户量小,7 天 TTL 足够;refresh token 增加双 token 同步逻辑、撤销列表等复杂度,YAGNI。

---

## 6. .env 新增变量清单

```bash
# === 已有(保留,对齐 simulate.py)===
OPENAI_API_KEY=...
OPENAI_API_BASE=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat

# === 新增 ===
# 数据库
HUIMENG_DB_PATH=backend/data/huimeng.db

# JWT
HUIMENG_JWT_SECRET=<32+ 字节随机串,用 python -c "import secrets; print(secrets.token_urlsafe(48))" 生成>
HUIMENG_JWT_TTL_SECONDS=604800

# OTP 邮箱(唯一通道,SMTP)
HUIMENG_SMTP_HOST=smtp.qq.com               # 或 SMTP2GO / Gmail 等
HUIMENG_SMTP_PORT=465
HUIMENG_SMTP_USER=
HUIMENG_SMTP_PASS=                          # QQ 邮箱用授权码,不是登录密码
HUIMENG_SMTP_FROM=noreply@huimeng.example.com

# 服务器
HUIMENG_API_HOST=0.0.0.0
HUIMENG_API_PORT=8000
HUIMENG_CORS_ORIGINS=http://localhost:5173   # 前端 Vite 开发服务器
```

---

## 7. 第一波待建文件清单(给开工的优先级)

### Sprint 1.A · Day 1-2:脚手架 + 数据库
- `backend/pyproject.toml`(uv 项目,依赖 fastapi / uvicorn / pydantic / python-jose 等)
- `backend/.env.example`
- `backend/app/main.py`(FastAPI 入口 + CORS + 错误中间件)
- `backend/app/config.py`
- `backend/app/db.py`(sqlite3 连接 + 事务上下文管理器)
- `backend/migrations/001-007.sql`(全部 7 个 DDL)
- `backend/scripts/init_db.py`(跑 migrations)
- `backend/README.md`(启动说明)

**验收**:`uv run uvicorn app.main:app --reload` 启动成功,`init_db.py` 跑完 7 张表创建成功。

### Sprint 1.B · Day 3-4:Auth(纯邮箱 OTP)+ Consent
- `backend/app/models/user.py` + `backend/app/models/consent.py`
- `backend/app/schemas/auth.py`
- `backend/app/services/otp_email_service.py`(SMTP)
- `backend/app/services/auth_service.py`(OTP 生成 / 校验 / JWT 签发 / 60s 限频 / IP 限流)
- `backend/app/routers/auth.py`(`/auth/send_otp`, `/auth/verify`)
- `backend/app/routers/consent.py`(`/consent` POST/GET)
- `backend/app/deps.py`(`get_current_user`)
- `backend/tests/test_auth.py`

**验收**:邮箱 OTP 跑通,前端能用 OTP 拿到 JWT;同一邮箱 60s 内重发被拒;同一 IP 24h 内 > 10 次发码被拒。

### Sprint 1.C · Day 5-7:Project / Character / Relationship / Event CRUD
- `backend/app/models/{project,character,relationship,event}.py`
- `backend/app/schemas/{project,character,relationship,event}.py`
- `backend/app/routers/projects.py` + `characters.py`
- `backend/tests/test_projects.py`

**验收**:前端能创建项目 / 角色 / 关系 / 事件 + 持久化。

### Sprint 1.D · Day 8-10:Refine 服务(对接 character_focus.md v3 + 3 兜底)
- `backend/app/services/llm_client.py`(对齐 simulate.py)
- `backend/app/services/refine_service.py`(LLM 调用 + 3 兜底完整实现,见角色对焦组件设计 §16)
- `backend/app/models/refinement.py` + `backend/app/schemas/refinement.py`
- `backend/app/routers/refine.py`(3 个 endpoint:refine / action / skip)
- `backend/tests/test_refine.py`(**必须包含 §16 三类漂移的回归测试**)

**验收**:跑 `scripts/verify_prompt.py` 的 4 个场景,通过 refine API 流程 → 落库 → 兜底拦截违规 → action 应用到 character。

### Sprint 1.E · Week 2(待):前后端联调 + 部署准备
- `backend/scripts/seed_dev.py`(假数据)
- 前后端联调
- Dockerfile / nginx 配置示例
- 公网部署(等域名 + ICP 备案完成)

---

## 8. 待确认风险

| ⚠ 风险 | 影响 | 缓解 |
|---|---|---|
| DeepSeek API key 是否企业版 | refine 服务可能撞 RPS 限流 | 阶段 1 用户量小,撞限流概率低;若撞了则降级为顺序排队 |
| JWT secret 现在为空 | 阻塞 Sprint 1.B auth | 我在 ADR §6 给了生成命令,你自己跑一次粘到 .env |
| SMTP 邮箱选哪家 | 阻塞 Sprint 1.B | 推荐 QQ 邮箱(国内,免费,授权码 5 分钟开通)或 SMTP2GO 免费档(海外服务,1000 封/月) |
| 邮箱 OTP 到达率 92-95% < 短信 99% | 5-15% 用户因垃圾箱拿不到码 → 流失 | 前端"未收到?"按钮提示"检查垃圾邮件 + 60 秒后重试";后续观察转化数据,严重时再考虑加短信 |
| 邮箱 OTP 被恶意刷码 | 成本 / 用户体验 / 触发免费 SMTP 上限 | 同一邮箱 60s 内只能发一次 + 同一 IP 24h 内最多 10 次,后端硬实施 |

---

## 9. 不做的事(再次显式列出 YAGNI)

- ❌ Celery / Redis 队列 — refine 走 FastAPI BackgroundTasks 同步够用
- ❌ Alembic — 7 张 SQL DDL 文件 + 手工版本号足够阶段 1-2
- ❌ Docker Compose 多服务 — 单进程 SQLite + FastAPI 阶段 1 足够
- ❌ Refresh Token — 单 token 7 天 TTL 足够
- ❌ 完整角色权限系统 — 阶段 1 只有"用户拥有自己的项目",不做共享/团队
- ❌ 限流中间件 — 阶段 1 用户少,IP 限流足够
- ❌ Sentry / Datadog — 留阶段 2

---

## 10. ADR 修订流程

- 任何修改本 ADR 的决策(比如阶段 2 引入 Redis、阶段 3 切 PostgreSQL),需要在本文件**追加**新章节,标注日期 + 修订理由
- **不允许悄悄改 ADR**(即使是 AI),所有架构决策必须留痕
- 阶段 2 / 3 的重大架构变更,需要再走一遍"暂停代码 → 输出 ADR → 等批准"流程

### 10.1 修订记录

**2026-05-09 / Sprint 1.A 收尾**:包管理决策从"uv"改为"**复用项目根 `.venv` + pip**"
- **理由**:用户机器未装 uv;装 uv 增加一次性认知负担,且阶段 1 单人开发,backend 与 simulate.py 共享 venv 没有隔离痛点
- **影响**:① backend/pyproject.toml 仍保留(给未来切回 uv 用) ② backend/README.md 启动步骤删 `uv sync`,改为"依赖已在项目根 .venv" ③ 后续装新依赖统一用 `.venv\Scripts\pip.exe install`
- **回滚条件**:阶段 2 多人协作 OR 后端依赖与 simulate.py 出现冲突 → 装 uv,backend 走独立 backend/.venv

**2026-05-09 / Sprint 1.B 启动**:Sprint 1.B 实施过程中发现 OTP 需要持久化表,新增 migration 008(otp_codes 表)
- **理由**:60s 限频 + 24h IP 限流 + OTP 验证都需要查询历史发码记录
- **设计**:code 存 SHA-256 hash 不存明文(防数据库泄漏时 OTP 全曝光)
- **影响**:数据库表数量 9 → 10

**2026-05-09 / Sprint 1.C 启动**:routers 目录从 ADR §7 的"projects.py + characters.py" 拆为 4 个独立文件
- **理由**:Sprint 1.C 实施时发现 19 个 endpoints 全塞 2 个文件会让单文件超 400 行,违反"宜居代码"原则
- **新结构**:routers/projects.py(5 + /graph) + characters.py(5) + relationships.py(4) + events.py(4)
- **不引入新概念**:仍是薄层 router + service 权限检查;routers/__init__.py 不变

**2026-05-09 / Sprint 1.C 设计决策**:不写 seed_dev.py,不在生产 DB 塞示例数据
- **理由**:用户明确要求"清除 mock 数据麻烦,能不用就不用"
- **影响**:① 取消 ADR §7 Sprint 1.E 中 `backend/scripts/seed_dev.py` 的产出 ② 自动化测试用真实场景数据(继承 verify_prompt.py 的"江湖夜雨"等)
- **替代**:开发期想看数据效果?直接前端走完一遍 OTP → 创建项目 → 添加角色 流程

---

## 11. 等批准 → 进入 Sprint 1.A

ADR 写完。等用户批准:
- "按 ADR 开工"  → 我开始 Sprint 1.A(Day 1-2 脚手架 + 数据库)
- "改 X 项再开工" → 我修改对应章节,重新等批准
