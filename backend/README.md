# 浑晶后端 (huimeng-backend)

MVP 阶段 1 后端 API。架构详见 `docs/后端架构ADR_v1.md`。

## 启动顺序

### 1. 配置

`backend/.env.example` 中需要的项,补充到**项目根 `.env`**(与 `simulate.py` 共享一份):

```bash
# 必填
HUIMENG_JWT_SECRET=<已自动生成,见项目根 .env>

# Sprint 1.B 启用 OTP 邮件登录前必填(在 QQ 邮箱设置里拿授权码)
HUIMENG_SMTP_USER=<你的 QQ 邮箱>
HUIMENG_SMTP_PASS=<QQ 邮箱授权码,16 位字母>
HUIMENG_SMTP_FROM=<通常等于 SMTP_USER>
```

### 2. 安装依赖

**包管理:复用项目根 `.venv`**(2026-05-09 ADR 修订,详见 ADR §10.1)。

依赖已装(Sprint 1.A 时一次性安装),后续装新依赖:
```bash
.\.venv\Scripts\pip.exe install <package>
```

如果是新机器拉代码,初装命令:
```bash
.\.venv\Scripts\pip.exe install "fastapi>=0.110" "uvicorn[standard]>=0.27" `
    "pydantic[email]>=2.6" "python-jose[cryptography]>=3.3" "openai>=1.30"
```

### 3. 初始化数据库

```bash
.\.venv\Scripts\python.exe backend\scripts\init_db.py
```

应输出"已创建 N 张业务表"(Sprint 1.A 是 9 张,Sprint 1.B 起 10 张含 otp_codes)。
**幂等**:可重复跑,IF NOT EXISTS 保护。

### 4. 启动服务

```bash
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --app-dir backend --port 8000
```

访问点:
- http://localhost:8000/        — 元信息
- http://localhost:8000/healthz — 健康检查(应返回 `db: ok`)
- http://localhost:8000/docs    — FastAPI 自动 OpenAPI 文档

## Sprint 进度(对齐 ADR §7)

- ✅ **Sprint 1.A**:脚手架 + SQLite + 7 张 migration DDL
- ⏳ Sprint 1.B:Auth(纯邮箱 OTP)+ JWT + Consent
- ⏳ Sprint 1.C:Project / Character / Relationship / Event CRUD
- ⏳ Sprint 1.D:Refine 服务 + 角色对焦 §16 三个兜底
- ⏳ Sprint 1.E:前后端联调 + 部署

## 目录约定

```
backend/
├── pyproject.toml        uv 项目
├── .env.example          模板(实际 .env 在项目根)
├── README.md             本文件
├── app/                  FastAPI 应用
│   ├── __init__.py
│   ├── main.py           FastAPI 入口
│   ├── config.py         读项目根 .env
│   ├── db.py             sqlite3 连接 + 事务上下文
│   ├── deps.py           Sprint 1.B+ 加(get_db / get_current_user)
│   ├── models/           Sprint 1.B+ 加(数据库表的 Python 表示,纯 dataclass)
│   ├── schemas/          Sprint 1.B+ 加(Pydantic API 输入输出)
│   ├── routers/          Sprint 1.B+ 加(FastAPI APIRouter)
│   ├── services/         Sprint 1.B+ 加(业务逻辑,不依赖 FastAPI)
│   └── middleware/
├── migrations/           7 个 SQL DDL 文件(手工版本号,无 Alembic)
├── data/                 SQLite 数据库文件位置(.gitignore)
├── scripts/
│   └── init_db.py        跑 migrations 创建数据库
└── tests/                pytest(Sprint 1.B+ 起加测试)
```

## 不做的事(YAGNI,详见 ADR §9)

- ORM(用 sqlite3 直接写 SQL,简单 + 可控)
- Alembic(手工版本号文件名足够阶段 1)
- Refresh Token(单 token 7 天 TTL)
- Celery / Redis / Docker Compose
- Sentry(留阶段 2)
- 多语言 / i18n
