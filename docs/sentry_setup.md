# Sentry 接入操作指南(给你自己看的步骤手册)

## 第一步:注册 Sentry 账号

1. 打开 https://sentry.io/signup
2. 用邮箱注册(免费档够用 — 每月 5000 条事件 + 7 天数据保留)
3. 注册时选**组织名**(随便填,比如 `huimeng` / `shuangdayeye`)

## 第二步:创建两个项目

Sentry 里**前端和后端是独立的项目**,要分开建。

### 后端项目
1. 控制台 → 左下角 `Projects` → 右上 `Create Project`
2. 选 **Platform**:Python → **FastAPI**
3. 项目名:`huimeng-backend`
4. 创建后会显示 **DSN**(像这样:`https://xxxxxxxxxx@oXXXXXX.ingest.sentry.io/YYYYYY`)
5. **复制这个 DSN**

### 前端项目
1. 再来一次 `Create Project`
2. 选 **Platform**:JavaScript → **Vue**
3. 项目名:`huimeng-frontend`
4. 复制它的 **DSN**(跟后端的不一样)

## 第三步:把 DSN 填到 .env

### 后端
打开**项目根目录**(`C:\Users\Administrator\Desktop\huimeng\`)的 `.env` 文件,加两行:
```bash
SENTRY_DSN=https://你刚才复制的后端 DSN
ENV=production
```
本地开发可以不填(代码会自动跳过 Sentry 初始化)。

### 前端
打开 `frontend/.env.production`(不存在就新建):
```bash
VITE_SENTRY_DSN=https://你刚才复制的前端 DSN
```
本地开发也不需要填。

## 第四步:安装 SDK

### 后端
```bash
cd backend
pip install "sentry-sdk[fastapi]>=2.0"
```
或者用我加好的 optional 依赖:
```bash
pip install -e ".[monitoring]"
```

### 前端
```bash
cd frontend
npm install @sentry/vue
```
(我已经加到 package.json 的 `optionalDependencies`,正常 `npm install` 也会装)

## 第五步:验证接入成功

### 后端测试
启动后端,访问任何一个**故意会 500** 的 URL(或者直接看 Sentry 控制台,启动时如果 DSN 配置成功,会看到一条 `release` 事件)。

简单测法:
```bash
# 把 SENTRY_DSN 临时设个**故意错的**,看 backend 日志
SENTRY_DSN=invalid_dsn python -m app.main
# 应该看到 "Sentry 初始化失败,降级运行" 类似日志(说明代码路径走通了)
```

更正式的测法:在后端任意路由临时写 `raise Exception("sentry test")`,触发 → Sentry 控制台应该 1 分钟内收到事件。

### 前端测试
启动 `npm run build && npm run preview`(必须 PROD 模式,DEV 模式不发 Sentry),在浏览器 console 跑:
```javascript
window.__SENTRY__ ? '已接入' : '未接入'
```
或者直接 `throw new Error("test")`,Sentry 控制台应该收到。

## 第六步:配置告警(可选但推荐)

Sentry 控制台 → 你的项目 → `Alerts` → `Create Alert`

**推荐 3 条告警**:
1. **5 分钟内有 10 条以上 error** → 邮件
2. **新出现的 error**(以前没见过的) → 邮件
3. **影响 ≥ 5 个用户的 error** → 邮件

## 常见问题

**Q: Sentry 免费档够用吗?**
A: 每月 5000 条事件 + 7 天保留。如果上线后日活 < 500,基本够。日活破 1000 后看是否需要升级 Team 档(每月 $26)。

**Q: SENTRY_DSN 暴露在前端 .env 里安全吗?**
A: 是安全的,DSN 是公开标识符不是 secret(就像 Google Analytics ID),Sentry 服务端有项目隔离。

**Q: 如何屏蔽某些不重要的错误?**
A: Sentry 控制台 → `Settings` → `Inbound Filters`,可以按 URL / browser / 错误类型过滤。

**Q: 用户隐私怎么办?**
A: 我已经设了 `send_default_pii=False`(后端 + 前端),Sentry 不会自动上传用户 IP / cookie / 请求 body。如果你想脱敏更深,看 Sentry 的 `beforeSend` hook 文档。
