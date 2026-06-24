# 浑晶 · 服务器部署指南(阿里云轻量 · Ubuntu 24.04)

> 把**门户 + 在线创作 + 后端**部署到 `shuangdayeye.cn`,让官网能下载客户端、网页能创作。
> 前提:服务器 IP `139.196.85.46`、域名 `shuangdayeye.cn` 备案已过、用 Let's Encrypt 免费证书。
> 配置文件在 `deploy/` 目录,本文一步步带你跑。命令在阿里云"远程连接"(SSH)里执行。

子域规划:`shuangdayeye.cn` 门户 · `app.` 在线创作 · `api.` 后端。

---

## 1. DNS 解析(阿里云 → 域名 → 解析)

加 4 条 **A 记录**,全部指向 `139.196.85.46`:

| 记录类型 | 主机记录 | 记录值 |
|---|---|---|
| A | `@` | 139.196.85.46 |
| A | `www` | 139.196.85.46 |
| A | `app` | 139.196.85.46 |
| A | `api` | 139.196.85.46 |

> 生效后 `ping app.shuangdayeye.cn` 能返回这个 IP 再往下走。

## 2. 防火墙(阿里云轻量 → 防火墙)

在【轻量应用服务器 → 防火墙】(**不是** ECS 安全组,这台是轻量)放行
**80 (HTTP)** 和 **443 (HTTPS)**(22 SSH 默认已开)。

> ⚠️ **别在系统里 `ufw enable`** —— 本套部署不用 OS 防火墙,开了反而多一层挡 80/443。
> 端口只在阿里云轻量控制台这一处开即可。

## 3. 服务器初始化(SSH 进去,以下都用 root / sudo)

```bash
# 3.1 加 4G swap(2G 内存建前端时不够,swap 兜底)— 幂等,可重复跑
[ -f /swapfile ] || { sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile; }
sudo swapon /swapfile 2>/dev/null || true
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

# 3.2 装依赖:nginx / python / git / certbot / node 22
sudo apt update
sudo apt install -y nginx python3-venv python3-pip git curl certbot python3-certbot-nginx
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash -
sudo apt install -y nodejs

# 3.3 建专用用户 + 目录
sudo useradd -r -m -d /home/huimeng -s /bin/bash huimeng || true
sudo mkdir -p /opt/huimeng && sudo chown huimeng:huimeng /opt/huimeng
```

## 4. 拉私有代码(deploy key,只读)

```bash
# 4.1 给 huimeng 用户生成 SSH key
sudo -u huimeng ssh-keygen -t ed25519 -C "huimeng-deploy" -f /home/huimeng/.ssh/id_ed25519 -N ""
sudo -u huimeng bash -c 'ssh-keyscan github.com >> /home/huimeng/.ssh/known_hosts'
# 4.2 打印公钥
sudo cat /home/huimeng/.ssh/id_ed25519.pub
```

把上面打印的公钥,加到私有仓库的 **Deploy keys**:
`github.com/LaL-999/hunjing` → Settings → Deploy keys → Add deploy key → 粘贴 →
**不要勾 Allow write access**(只读)→ Add。然后:

```bash
# 4.3 克隆到 /opt/huimeng/app
sudo -u huimeng git clone git@github.com:LaL-999/hunjing.git /opt/huimeng/app
```

## 5. 后端(venv + 依赖 + .env + systemd)

```bash
# 5.1 建 venv + 装后端(含监控)
sudo -u huimeng python3 -m venv /opt/huimeng/venv
sudo -u huimeng /opt/huimeng/venv/bin/pip install -U pip
sudo -u huimeng /opt/huimeng/venv/bin/pip install -e "/opt/huimeng/app/backend[monitoring]"

# 5.2 建数据目录(DB / 上传 / 收款码)
sudo -u huimeng mkdir -p /opt/huimeng/app/backend/data/uploads
sudo -u huimeng mkdir -p /opt/huimeng/app/backend/data/payment_qrcodes

# 5.3 生成生产 .env(放项目根)
sudo -u huimeng cp /opt/huimeng/app/deploy/env.production.example /opt/huimeng/app/.env
# 立刻收紧权限(含 JWT/DeepSeek key/SMTP 密码,绝不能 world-readable)
sudo chown huimeng:huimeng /opt/huimeng/app/.env && sudo chmod 600 /opt/huimeng/app/.env
# 生成强随机串,JWT_SECRET 和 INSIGHTS_ADMIN_TOKEN 各来一个:
openssl rand -hex 32   # → HUIMENG_JWT_SECRET
openssl rand -hex 32   # → INSIGHTS_ADMIN_TOKEN
sudo -u huimeng nano /opt/huimeng/app/.env
# 校验权限(期望 600 huimeng):
stat -c '%a %U' /opt/huimeng/app/.env
```

**`.env` 必填**(漏了会出大问题,模板里都标了):
- `HUIMENG_JWT_SECRET` — 上面生成的强随机串
- `HUIMENG_FOUNDER_EMAILS` — **你审批支付订单要用的登录邮箱**,不填用别的邮箱注册会一直 403 审不了单
- `INSIGHTS_ADMIN_TOKEN` — 上面生成的;不填会用源码公开的弱默认 token(任何人可批单)
- `OPENAI_API_KEY`(DeepSeek)、SMTP 四项(注册发验证码)
- `HUIMENG_BYOK_PAYEE_NAME` — 你的收款人名(默认是真名「李爽」,**必改**)

**收款码图片**上传到 `/opt/huimeng/app/backend/data/payment_qrcodes/`,文件名填进
`HUIMENG_BYOK_WECHAT_QR_FILENAME`(注意全是 `HUIMENG_BYOK_` 前缀,写错前缀会被静默忽略)。

```bash
# 5.4 装 systemd 服务
sudo cp /opt/huimeng/app/deploy/huimeng-backend.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now huimeng-backend
# 看是否起来(应 active running;迁移会自动跑)
sudo systemctl status huimeng-backend --no-pager
curl -s http://127.0.0.1:8000/api/health   # 期望返回健康 JSON
```

## 6. 前端 + 门户(构建 + nginx)

```bash
# 6.1 构建前端(指向 api 子域;限堆 3G + swap 兜底)
cd /opt/huimeng/app/frontend
sudo -u huimeng npm ci
sudo -u huimeng env VITE_API_BASE=https://api.shuangdayeye.cn NODE_OPTIONS=--max-old-space-size=3072 npm run build
# 产物在 /opt/huimeng/app/frontend/dist;门户是 /opt/huimeng/app/portal/index.html(已就绪)

# 6.2 装 nginx 配置
sudo cp /opt/huimeng/app/deploy/nginx-huimeng.conf /etc/nginx/sites-available/huimeng.conf
sudo ln -sf /etc/nginx/sites-available/huimeng.conf /etc/nginx/sites-enabled/huimeng.conf
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t && sudo systemctl reload nginx
```

此时 `http://shuangdayeye.cn`(门户)、`http://app.shuangdayeye.cn`(创作)应能打开(还是 http)。

## 7. HTTPS(Let's Encrypt 自动)

certbot 一次签 4 个域名是 **all-or-nothing**:任一没解析好,整次失败。先校验 4 个全部解析到本机:

```bash
for d in shuangdayeye.cn www.shuangdayeye.cn app.shuangdayeye.cn api.shuangdayeye.cn; do
  echo -n "$d -> "; dig +short @8.8.8.8 "$d" | tail -1
done
# 四个都必须返回 139.196.85.46 才往下;没生效就等 DNS 传播(可能几分钟到几十分钟)
```

先用 **staging** 演练(避免失败次数撞 Let's Encrypt 限频):

```bash
sudo certbot --nginx --staging \
  -d shuangdayeye.cn -d www.shuangdayeye.cn -d app.shuangdayeye.cn -d api.shuangdayeye.cn \
  --agree-tos -m 你的邮箱@example.com --redirect --non-interactive
```

演练成功(看到 "The dry run was successful" 或证书签发)后,**正式签**:

```bash
# 先删掉 staging 测试证书,再签正式
sudo certbot delete --cert-name shuangdayeye.cn 2>/dev/null || true
sudo certbot --nginx \
  -d shuangdayeye.cn -d www.shuangdayeye.cn -d app.shuangdayeye.cn -d api.shuangdayeye.cn \
  --agree-tos -m 你的邮箱@example.com --redirect --non-interactive
# certbot 自动改写 nginx 加 443 + http→https 跳转;续期自动(certbot.timer)
```

**加 HSTS**(certbot 只加跳转,不加 HSTS;带 token + 支付的站需要防 SSL-strip):

```bash
# 在 certbot 生成的 3 个 443 server 块里各加一行;最简单是在 server_name 行后插入:
sudo sed -i '/listen 443 ssl/a\    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;' /etc/nginx/sites-available/huimeng.conf
sudo nginx -t && sudo systemctl reload nginx
```

> 桌面 APP 和门户都要 https —— 这步过了 `https://api.shuangdayeye.cn` 才通,客户端才连得上。
> 验证(从**本机**,非服务器):`curl -I https://api.shuangdayeye.cn/api/health` 应 200 + 带 HSTS 头。

## 8. 先发布桌面端 Release(否则门户下载是死链)

到 [huimeng-desktop Releases](https://github.com/LaL-999/huimeng-desktop/releases) 把
`desktop-v0.1.0` 草稿 **Publish**(放在烟雾测试前,这样门户"下载"才不是死链)。
门户靠 GitHub API 自动发现最新【已发布】Release —— 草稿不算。验证:

```bash
curl -s https://api.github.com/repos/LaL-999/huimeng-desktop/releases/latest | grep -E '"tag_name"|"name".*setup'
```

## 9. 烟雾测试

- [ ] `https://shuangdayeye.cn` 门户打开,点「下载桌面客户端」能下到 `.exe`
- [ ] `https://app.shuangdayeye.cn` 创作平台打开
- [ ] 注册 → 收到 OTP 邮件 → 登录 → 进 dashboard
- [ ] 建项目 → AI 续写跑一篇短文
- [ ] 收款配置生效:`curl -s https://api.shuangdayeye.cn/api/payments/pay-info`
      应显示**你的**收款人名(不是「李爽」)+ 非空二维码 URL
- [ ] 桌面客户端装好后能登录(连 `https://api.shuangdayeye.cn`)

## 10. 数据备份(必做 —— 单库 SQLite,丢了全没)

```bash
# 装定时备份(每 6h):用 cron
sudo -u huimeng crontab -e
# 加一行:
# 0 */6 * * * bash /opt/huimeng/app/deploy/backup.sh >> /opt/huimeng/backups/backup.log 2>&1

# 先手动跑一次验证 + 演练 restore
sudo -u huimeng bash /opt/huimeng/app/deploy/backup.sh
ls -la /opt/huimeng/backups/
```

> ⚠️ 本机磁盘坏了备份也没用 —— 在 `deploy/backup.sh` 末尾配 OSS 离机副本(已留注释)。

---

## 以后更新代码

```bash
sudo bash /opt/huimeng/app/deploy/update.sh
```
(拉代码 → 装依赖 → 重建前端 → 重启后端 + reload nginx,一条龙)

## 怎么审批支付订单(洞察后台未部署前的临时办法)

可视化审核后台(`insights-frontend`)本指南未部署。在它上线前,你用**创始人账号**
(`HUIMENG_FOUNDER_EMAILS` 里的邮箱)登录后,订单审批走后端 admin API:

```bash
# 1. 登录拿 JWT(或从浏览器 localStorage 复制 token)
TOKEN=<你的创始人 JWT>
# 2. 看待审订单
curl -s https://api.shuangdayeye.cn/api/payments/admin/orders?status=submitted \
  -H "Authorization: Bearer $TOKEN"
# 3. 看支付凭证截图(浏览器带 token 打开)
#    https://api.shuangdayeye.cn/api/payments/admin/orders/<订单id>/proof-image
# 4. 通过 → 自动履约发货
curl -s -X POST https://api.shuangdayeye.cn/api/payments/admin/orders/<订单id>/approve \
  -H "Authorization: Bearer $TOKEN"
```

> 量大了我再帮你把 `insights-frontend` 部成一个可视化审核后台。

## 注意事项 / 已知约束

- **SQLite 单机**:验证期足够;日活破千再考虑 Postgres。
- **2G 内存**:前端构建靠 swap 兜底。若 build 仍 OOM,改在本机 `npm run build` 后
  `rsync frontend/dist` 上传到服务器同路径。
- **无全局限流**:`/api/auth/*`(发 OTP 邮件)和 LLM 重端点暂无速率限制。开放注册前
  建议给 `/api/auth/*` 加 nginx `limit_req` 挡邮件洪水 + DeepSeek 设月度消费上限/告警。
- **8192 那个数别信**:`docs/deploy_checklist.md` 写的 `--max-old-space-size=8192` 是旧值,
  2G 机上会 OOM;以本指南的 3072 为准。

---

_最后更新:2026-06-24 · 配置文件见 `deploy/` · 经 5 维对抗审查修正_
