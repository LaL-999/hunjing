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

放行 **80 (HTTP)** 和 **443 (HTTPS)**(22 SSH 默认已开)。

## 3. 服务器初始化(SSH 进去,以下都用 root / sudo)

```bash
# 3.1 加 4G swap(2G 内存建前端时不够,swap 兜底)
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

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
# 生成一个强 JWT 密钥,填进去:
openssl rand -hex 32
sudo -u huimeng nano /opt/huimeng/app/.env   # 填 JWT / DeepSeek key / SMTP / 收款人名 等
```

`.env` 必填:`HUIMENG_JWT_SECRET`(上面生成的)、`OPENAI_API_KEY`(DeepSeek)、
SMTP 四项(注册发验证码)、`BYOK_PAYEE_NAME`。**收款码图片**上传到
`/opt/huimeng/app/backend/data/payment_qrcodes/`,文件名填进 `BYOK_WECHAT_QR_FILENAME`。

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

```bash
sudo certbot --nginx \
  -d shuangdayeye.cn -d www.shuangdayeye.cn \
  -d app.shuangdayeye.cn -d api.shuangdayeye.cn \
  --agree-tos -m 你的邮箱@example.com --redirect --non-interactive
# certbot 自动改写 nginx 加 443 + http→https 跳转;续期自动(systemd timer)
```

> 桌面 APP 和门户都要 https —— 这步过了 `https://api.shuangdayeye.cn` 才通,客户端才连得上。

## 8. 烟雾测试

- [ ] `https://shuangdayeye.cn` 门户打开,点「下载桌面客户端」能跳到 Release(需先 Publish 草稿)
- [ ] `https://app.shuangdayeye.cn` 创作平台打开
- [ ] 注册 → 收到 OTP 邮件 → 登录 → 进 dashboard
- [ ] 建项目 → AI 续写跑一篇短文
- [ ] 桌面客户端装好后能登录(连 `https://api.shuangdayeye.cn`)

## 9. 让官网下载真正生效

到 [huimeng-desktop Releases](https://github.com/LaL-999/huimeng-desktop/releases) 把
`desktop-v0.1.0` 草稿 **Publish**。门户的"下载客户端"会自动发现它、按系统推 `.exe`。

---

## 以后更新代码

```bash
sudo bash /opt/huimeng/app/deploy/update.sh
```
(拉代码 → 装依赖 → 重建前端 → 重启后端 + reload nginx,一条龙)

## 注意事项

- **SQLite 单机**:验证期足够;日活破千再考虑 Postgres。每天备份 `backend/data/huimeng.db`。
- **2G 内存**:跑后端 + nginx 够;前端构建靠 swap 兜底。若 build 仍 OOM,改在本机
  `npm run build` 后把 `frontend/dist` 上传(`rsync`)。
- **洞察后台(订单审核)**:`insights-backend` / `insights-frontend` 是独立的运营后台,
  本指南未含。你审核支付订单暂时可直接调后端 admin API;要可视化后台再单独部署一份。
- **备份**:`backend/data/`(DB + 上传 + 收款码)是唯一有状态目录,务必定时备份。

---

_最后更新:2026-06-24 · 配置文件见 `deploy/`_
