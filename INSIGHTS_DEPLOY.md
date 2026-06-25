# 浑晶洞察后台(insights)上线保姆手册

> 把"你的运营后台"(看有多少用户、他们干了什么、转化漏斗、付费、风控)部署到公网。
> 写给**不是程序员**的你。看不懂的地方,把这段 + 报错截图发给 Claude。
>
> 关键信息:
> - 服务器:`139.196.85.46`(阿里云轻量,Ubuntu 24.04)
> - 新子域:**insights.shuangdayeye.cn**(本手册要新建它)
> - 代码在服务器:`/opt/huimeng/app` · 洞察后端跑在 `127.0.0.1:8001`
> - 配套文件(我已写好,随 git push 上去):`deploy/huimeng-insights.service`、`deploy/nginx-insights.conf`、`deploy/env.production.example`(已加 insights 段)、`insights-frontend/.env.production.example`

---

## ⚠️ 0. 先读这段:为什么洞察后台不能"裸奔上线"

洞察后台能一键导出**全平台用户的隐私**(邮箱、注册 IP、消费金额、风控记录)。它原本只为本机自用设计,**鉴权很弱**(只有一把会被打进前端文件、谁都能扒出来的 token)。

所以**直接公网暴露 = 用户隐私全裸奔**。本手册的做法是:在 nginx 最外层加一道**网页登录框(HTTP Basic Auth)**——打开 insights.shuangdayeye.cn 会先弹个"输入账号密码"的浏览器原生框,只有你知道密码。这是**硬红线,不做就别上线**。

**三条红线(缺一不可):**
1. **整站 Basic Auth**(`deploy/nginx-insights.conf` 已配好,你只需建密码文件)——挡住外网看数据。
2. **强随机管理 token**(`.env` 里 `INSIGHTS_ADMIN_TOKEN`,默认值会被拒绝启动)。
3. **埋点地址生效**——主前端要重 build 注入 `VITE_INSIGHTS_BASE`,否则线上行为数据一条都收不到(这是个老 bug,本次一并修)。

> 代码层我已经加固:生产环境用默认 token 会**拒绝启动**;关掉了 `/docs` 接口地图;`/health` 不再泄露主库表名。你只要照步骤把 nginx 密码 + 强 token 配上即可。

---

## 1. 你要亲自做的事(Claude 做不了)

| # | 事项 | 在哪做 |
|---|---|---|
| 1 | **加 DNS 解析**:`insights` → `139.196.85.46` | 你的域名控制台(阿里云/你买域名的地方),加一条 A 记录,主机记录填 `insights`,记录值填 `139.196.85.46` |
| 2 | **定 Basic Auth 账号 + 密码** | 自己想一个,部署时输入(建议存进密码管理器);**这就是你登录洞察后台的钥匙** |
| 3 | **生成强 token**:服务器跑 `openssl rand -hex 24`,记下结果 | 这串要填 3 个地方(见步骤),务必一致 |
| 4 | **备案确认**:中国大陆服务器新增子域,去阿里云备案系统看 `insights` 子域是否要补充(通常同主体子域沿用主域备案号,但**请你向阿里云核实**,别想当然) | 阿里云备案控制台 |
| 5 | 在服务器上跑下面给你的命令 + 上传 dist | SSH / Workbench |

---

## 2. 部署步骤(照着做)

### 步骤 0 — 加 DNS(你做)
域名控制台加 A 记录 `insights` → `139.196.85.46`。等几分钟生效,服务器上验证:
```bash
dig +short insights.shuangdayeye.cn      # 应返回 139.196.85.46
```
没装 dig 就 `nslookup insights.shuangdayeye.cn`。**没生效别往下走**(certbot 签证书会失败)。

### 步骤 1 — 拉新代码(服务器)
```bash
sudo -u huimeng git -C /opt/huimeng/app pull --ff-only
```
> 这步把我写的 insights 加固代码 + 配置文件拉到服务器。

### 步骤 2 — 确认依赖(服务器)
洞察后端依赖(fastapi/uvicorn/pydantic)主 backend 已装,跑一次兜底确认能 import:
```bash
sudo -u huimeng /opt/huimeng/venv/bin/pip install -e /opt/huimeng/app/insights-backend -q
```

### 步骤 3 — 配 .env(服务器)
生成强 token 并记下:
```bash
openssl rand -hex 24      # 复制结果,下面三处都用它
```
编辑 `/opt/huimeng/app/.env`(`sudo nano /opt/huimeng/app/.env`),确认/填好这几行(模板见 `deploy/env.production.example`):
```bash
INSIGHTS_ADMIN_TOKEN=<上面生成的强 token>
INSIGHTS_ALLOWED_ORIGINS=https://app.shuangdayeye.cn,https://insights.shuangdayeye.cn
INSIGHTS_LLM_API_KEY=<你的 DeepSeek key,给 AI 每日总结用;不填则总结功能降级,看板照常>
INSIGHTS_LLM_BASE_URL=https://api.deepseek.com/v1
INSIGHTS_LLM_MODEL=deepseek-chat
# 并确认这行已含 insights 子域(insights 前端跨域调主后端审单要用):
HUIMENG_CORS_ORIGINS=https://shuangdayeye.cn,https://www.shuangdayeye.cn,https://app.shuangdayeye.cn,https://insights.shuangdayeye.cn
```
存盘退出。锁权限:
```bash
sudo chmod 600 /opt/huimeng/app/.env && sudo chown huimeng:huimeng /opt/huimeng/app/.env
```

### 步骤 4 — 建 Basic Auth 密码文件(服务器)
```bash
sudo apt-get install -y apache2-utils                      # 提供 htpasswd
sudo htpasswd -c /etc/nginx/.htpasswd-insights <你的账号>   # 回车后输两遍密码(账号密码你定)
sudo chown root:www-data /etc/nginx/.htpasswd-insights
sudo chmod 640 /etc/nginx/.htpasswd-insights
```
> 这个账号密码 = 以后打开洞察后台时浏览器弹框要输的。记牢。

### 步骤 5 — 装 systemd 服务(服务器)
```bash
sudo cp /opt/huimeng/app/deploy/huimeng-insights.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now huimeng-insights
sudo systemctl status huimeng-insights        # 看到 active (running) 就成
curl -s 127.0.0.1:8001/track/health           # 本机直连应返 {"ok":true,...}
```
> 若 status 报错且日志写"拒绝启动:...默认 dev 值",说明步骤 3 的 token 没填好——回去补。这是**故意的安全守卫**。

### 步骤 6 — 本地 build 两份前端(在你电脑,或让 Claude 做)

⚠️ 服务器内存小,**不能在服务器 build**。两份都在本地构建:

**(a) 洞察前端**:先填 `insights-frontend/.env.production`(模板 `.env.production.example`),`VITE_ADMIN_TOKEN` 填步骤 3 那把**同一个 token**,然后:
```
cd C:\Users\Administrator\Desktop\huimeng\insights-frontend
npm run build          # 产物 insights-frontend\dist
```

**(b) 主前端**(为让线上埋点生效,必须重 build 一次):确认 `frontend/.env.production` 里有
`VITE_INSIGHTS_BASE=https://insights.shuangdayeye.cn`,然后 `cd ..\frontend && npm run build`。

> 跟我说"帮我打包洞察后台 + 重打主前端",我直接在你电脑上 build + 打 zip。

### 步骤 7 — 上传两份 dist(你做)
阿里云 Workbench 把两个 dist 传上去,服务器上就位 + 修权限:
```bash
# 洞察前端 dist → /opt/huimeng/app/insights-frontend/dist
sudo chown -R huimeng:huimeng /opt/huimeng/app/insights-frontend/dist
sudo chmod -R u=rwX,go=rX     /opt/huimeng/app/insights-frontend/dist
# 主前端 dist → /opt/huimeng/app/frontend/dist(同你平时更新前端的流程,权限同样修)
```

### 步骤 8 — 装 nginx 站点 + 签证书(服务器)
```bash
sudo cp /opt/huimeng/app/deploy/nginx-insights.conf /etc/nginx/sites-available/huimeng-insights.conf
sudo ln -sf /etc/nginx/sites-available/huimeng-insights.conf /etc/nginx/sites-enabled/
sudo nginx -t                                   # 必须 syntax ok（报 limit_req_zone 重复见下方排错）
sudo systemctl reload nginx

# certbot 把 insights 子域加进证书(必须把原来 4 个域一起列,否则会换证书)
sudo certbot --nginx \
  -d shuangdayeye.cn -d www.shuangdayeye.cn \
  -d app.shuangdayeye.cn -d api.shuangdayeye.cn \
  -d insights.shuangdayeye.cn \
  --agree-tos -m javaspringcjiajia@foxmail.com --redirect --non-interactive

sudo nginx -t && sudo systemctl reload nginx
```

### 步骤 9 — 验证(服务器 / 浏览器)
```bash
# 1) 没账号密码应被挡(401)
curl -I https://insights.shuangdayeye.cn/
# 2) 带账号密码能进(200)
curl -I -u <账号>:<密码> https://insights.shuangdayeye.cn/
# 3) 埋点端点公开可达(不需账号密码,返回 {"ok":true})
curl -i -X POST https://insights.shuangdayeye.cn/track \
  -H "Content-Type: application/json" \
  -d '{"event_type":"session_start","session_id":"t1","timestamp_ms":1}'
```
**浏览器**:打开 `https://insights.shuangdayeye.cn` → 弹账号密码框 → 输入 → 看到洞察看板 = 成功。
再打开 `https://app.shuangdayeye.cn` 操作几下,回洞察后台看"实时/今日事件"有没有涨(主前端重 build 后埋点才会进来)。

---

## 3. 排错速查

| 症状 | 原因 | 处理 |
|---|---|---|
| `huimeng-insights` 起不来,日志写"拒绝启动...默认 dev 值" | `INSIGHTS_ADMIN_TOKEN` 没填 / 仍是默认 | 步骤 3 填强 token,`systemctl restart huimeng-insights` |
| `nginx -t` 报 `limit_req_zone "insights_track" already defined` | 主 conf 已有同名限流区 | 编辑 `nginx-insights.conf` 把 `insights_track` 改个名(两处:zone 定义 + location 里的 `limit_req zone=`) |
| 打开洞察后台不弹密码框、直接能看 | Basic Auth 没生效 | 确认 `.htpasswd-insights` 已建 + nginx 已 reload + 访问的是 https 子域 |
| 洞察看板能开,但"事件总数"一直 0 | 主前端没重 build / `VITE_INSIGHTS_BASE` 没注入 | 重 build 主前端 dist 再传(步骤 6b/7) |
| 看板里 admin 接口 401/403 | 洞察前端 build 时 `VITE_ADMIN_TOKEN` 与 `.env` 的 `INSIGHTS_ADMIN_TOKEN` 不一致 | 两处对齐,重 build 洞察前端 |
| AI 每日总结报错 | `INSIGHTS_LLM_API_KEY` 没配 | 填 DeepSeek key(或忽略,不影响看板) |
| certbot 失败 | DNS 没生效 | 等 DNS,`dig +short insights.shuangdayeye.cn` 返对 IP 再签 |

---

## 4. 日常维护

```bash
# 看洞察后端活着没 / 重启 / 看日志
sudo systemctl status huimeng-insights
sudo systemctl restart huimeng-insights
sudo journalctl -u huimeng-insights -f
```
- 洞察数据存 `insights-backend/data/analytics.db`(与主库 `huimeng.db` 分开;主库它只读)。
- 想把 analytics.db 也纳入备份,在 `deploy/backup.sh` 里加一行拷它即可(让 Claude 帮你加)。
- 以后改了洞察代码:`git pull` →（若改了 .py）`sudo systemctl restart huimeng-insights`;改了洞察前端则重 build + 重传 dist。

---

_最后更新:2026-06-25 · 配套:OPS_MANUAL.md(平台日常)/ DEPLOY.md(首次部署)/ deploy/(配置 + 脚本)_
