# 浑晶 上线部署 checklist

**日期**:2026-06-02 准备
**状态**:首版上线
**目标环境**:VPS(Linux,推荐 4C8G 起,SQLite 单机)

---

## A. 上线前(t-24h)

### 代码冻结
- [ ] git tag 当前 commit 为 `v1.0.0-launch`,后续 patch 走 `v1.0.x`
- [ ] frontend 跑 `npm run build`(注意:vite build 需要 8GB heap,先 `export NODE_OPTIONS=--max-old-space-size=8192`)
- [ ] 全量 pytest 902 passed 验证通过(最后一次)
- [ ] vue-tsc 0 错验证通过

### 配置审计
- [ ] `.env` 检查:
  - `DEEPSEEK_API_KEY` 生产 key(不是 mock / 测试 key)
  - `JWT_SECRET` 是**随机生成的强 secret**(不是 dev 默认值)
  - `OTP_SENDER_EMAIL` / `SMTP_*` 配生产邮箱
  - `CORS_ORIGINS` 只包含正式域名,不含 `localhost`
- [ ] `users` 表清空 dev 测试账号(或迁移到测试库)
- [ ] `simulations` / `projects` 测试数据清空(可选,看是否要保留作 demo)

### 域名 + HTTPS
- [ ] 域名 DNS 解析到 VPS IP
- [ ] Let's Encrypt SSL 证书申请(`certbot --nginx -d shuangdayeye.cn -d www.shuangdayeye.cn`)
- [ ] nginx 配 `force HTTPS` redirect
- [ ] HSTS header 加上(`add_header Strict-Transport-Security "max-age=31536000"`)

### 备份
- [ ] SQLite 数据库定时备份脚本(每 6h 一份到独立磁盘 / 对象存储)
- [ ] 备份脚本经测试:能完整 restore 出来

---

## B. 上线日(t=0)

### 部署
1. `git pull` 最新代码 / `git checkout v1.0.0-launch`
2. backend:
   ```bash
   cd backend && python -m pip install --upgrade -e .
   # 跑迁移
   python -m scripts.run_migrations  # 或手动 sqlite3 db < migrations/*.sql
   ```
3. frontend:
   ```bash
   cd frontend
   export NODE_OPTIONS=--max-old-space-size=8192
   npm ci && npm run build
   # build 产物在 dist/,nginx 配 root 指向它
   ```
4. nginx reload:`nginx -t && systemctl reload nginx`
5. backend 启动:
   ```bash
   # 用 systemd 起 uvicorn,不要直接 nohup
   systemctl restart huimeng-backend
   ```

### 烟雾测试(上线后 10 分钟内做完)
- [ ] 访问首页能加载
- [ ] 注册新账号 → 收到 OTP 邮件
- [ ] 登录 → 跳 dashboard
- [ ] 创建项目 → 进入 ProjectView
- [ ] 点 AI 续写 → 跑一篇短文(reshape 10% / quick mode)→ done
- [ ] 阅读器能打开
- [ ] 退出登录正常

### 监控接入
- [ ] Sentry / 类似的 error tracking 已接(见 `docs/monitoring.md`)
- [ ] uptime 监控(UptimeRobot / 类似)— 每分钟探活
- [ ] 关键指标看板(见 `docs/monitoring.md`)

---

## C. 上线后(t+24h)

- [ ] 看 Sentry / log 是否有 5xx 飙升
- [ ] credit 消耗看板看是否有用户疯狂消耗(防恶意)
- [ ] 邮件发送配额 / SMTP 健康度
- [ ] 备份脚本第一份成功
- [ ] 跑一遍备份 restore 演练(在 staging)

---

## D. 回滚方案

如果上线后 1 小时内发现严重问题:

1. 立即 `git checkout v0.9.x`(上次稳定版)
2. **如果有 migration 改动**:先备份数据库 → 跑 down migration → 切代码
3. nginx reload + backend restart
4. 不要慌:用户最近的操作可能丢,但平台先恢复重要

---

## E. 已知约束(运维人员需知)

- **SQLite 单机**:并发写入受限,日活 ≤ 1000 应该够用。日活破 2000 需考虑切 Postgres。
- **LLM 调用没有限流**:用户疯狂连点可能爆 DeepSeek API quota。前端已做提交按钮防重复(R1 修复),但后端**没有**速率限制。上线后看情况是否需要加。
- **comic_service 3815 行**:技术债已标(见 `docs/refactor_comic_service.md`),下个 sprint 拆。
- **漫创态 chip "⚗ 实验中"**:Sprint 5.B 战略性降级,功能保留但**不推用户用**。

---

## F. 应急联系

- VPS 提供商支持:_(填)_
- 域名注册商:_(填)_
- DeepSeek API 备用账号:_(填)_
- 邮箱 SMTP 备用账号:_(填)_

填好这份后,部署前再 review 一遍。
