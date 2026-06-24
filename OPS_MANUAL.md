# 浑晶 · 运维保姆手册

> 写给**不是程序员**的你。这份手册不讲原理,只讲"遇到 X,照着做 Y"。
> 看不懂的地方,直接把这段 + 报错截图发给 Claude,让它帮你判。
>
> 你的平台关键信息:
> - 服务器(阿里云轻量):`139.196.85.46`,系统 Ubuntu 24.04
> - 门户:https://shuangdayeye.cn ・ 创作平台:https://app.shuangdayeye.cn ・ 后端:https://api.shuangdayeye.cn
> - 代码在服务器:`/opt/huimeng/app` ・ 数据在:`/opt/huimeng/app/backend/data`
> - 私有代码仓库:github.com/LaL-999/hunjing ・ 桌面客户端发布仓库:github.com/LaL-999/huimeng-desktop

---

## 0. 先记住一张图:代码在"三个地方",不会自动同步 ⭐

这是你最担心的事,先把它讲透。你的代码同时存在 **3 个地方**:

```
①你的电脑                ②GitHub(云端仓库)            ③服务器(线上真正在跑的)
C:\...\Desktop\huimeng  →  github.com/LaL-999/hunjing  →  /opt/huimeng/app
   (改代码的地方)            (中转 + 备份)                  (用户访问的)
        │                          ▲                              │
        └──── git push ────────────┘                              │
                                   └──────── git pull ────────────┘
```

**核心结论(记住这一句)**:
> 在电脑上改了代码 **≠** 网站就变了。必须走完 `push → pull → 重启/重建` 这一整条,线上才生效。**没有任何一步是自动的。**

- 我(Claude)帮你改代码 + `git push` → 只是把代码送到了 ②GitHub,**线上 ③ 还是旧的**。
- 要让线上变,你得在服务器上把新代码 `git pull` 下来,再**重启后端**(或**重新部署前端**)。

所以你完全不用担心"我改了会不会乱同步"——**恰恰相反,不主动部署,线上永远不变**,很安全。

---

## 1. 怎么让"改的代码"上线(最常用)

改动分三种,部署方式不同。**先判断改了哪类**(不确定就问 Claude:"这次改动要怎么部署?")。

### A. 只改了后端(`.py` 文件)→ 一条命令

SSH 进服务器,跑:
```bash
sudo bash /opt/huimeng/app/deploy/update.sh
```
它自动:拉新代码 → 装依赖 → 重启后端 → reload nginx。完事看一眼健康:
```bash
curl -s https://api.shuangdayeye.cn/api/health
```
返回 `{"ok":true,...}` 就成了。

### B. 改了前端(`.vue` / `.ts` / 界面)→ 本地构建 + 上传

⚠️ **不能在服务器上构建**(1.6G 内存会卡死)。流程:

1. **让 Claude 打包**(最省事):跟我说"重新构建前端",我在你电脑上构建 + 打好 `huimeng-dist.zip` 放你桌面。
2. **上传**:阿里云 Workbench 文件管理 → 把 `huimeng-dist.zip` 传到 `/home/admin/`。
3. **服务器解压**:
   ```bash
   sudo mv /home/admin/huimeng-dist.zip /opt/huimeng/app/frontend/
   cd /opt/huimeng/app/frontend
   sudo rm -rf dist && sudo mkdir dist
   sudo unzip -o huimeng-dist.zip -d dist
   sudo chown -R huimeng:huimeng dist
   sudo rm huimeng-dist.zip
   ```
4. 浏览器 **Ctrl+Shift+R** 强刷。前端不用重启任何服务。

### C. 改了 nginx 配置 → re-copy + 重跑 certbot

```bash
cd /opt/huimeng/app && sudo -u huimeng git pull
sudo cp deploy/nginx-huimeng.conf /etc/nginx/sites-available/huimeng.conf
sudo certbot --nginx -d shuangdayeye.cn -d www.shuangdayeye.cn -d app.shuangdayeye.cn -d api.shuangdayeye.cn \
  --agree-tos -m javaspringcjiajia@foxmail.com --redirect --non-interactive
sudo sed -i '/listen 443 ssl/a\    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;' /etc/nginx/sites-available/huimeng.conf
sudo nginx -t && sudo systemctl reload nginx
```

> **改了多类怎么办?** 按 A → B → C 顺序各做一遍即可。拿不准就把 Claude 给你的 commit 说明发我,我告诉你这次要走哪几步。

---

## 2. 服务器日常命令速查(复制粘贴)

SSH 进服务器(阿里云控制台「远程连接」)后:

| 我想…… | 命令 |
|---|---|
| 看后端是不是活着 | `sudo systemctl status huimeng-backend` |
| 重启后端 | `sudo systemctl restart huimeng-backend` |
| 看后端实时日志(排错最常用) | `sudo journalctl -u huimeng-backend -f`（Ctrl+C 退出) |
| 看后端最近 100 行日志 | `sudo journalctl -u huimeng-backend -n 100 --no-pager` |
| 看后端健康 | `curl -s https://api.shuangdayeye.cn/api/health` |
| 重启 / reload nginx | `sudo systemctl reload nginx` |
| 看磁盘满没满 | `df -h` （看 `/` 那行的「已用%」,>90% 要清理) |
| 看内存 | `free -h` |
| 看谁在吃内存 | `top`（按 q 退出） |

---

## 3. 出问题了怎么办 —— 故障速查表 🚑

**通用第一步**:先开着日志窗口 `sudo journalctl -u huimeng-backend -f`,然后去网站复现一次,看刷出什么红字。把那几行发 Claude 基本就能定位。

| 症状 | 大概率原因 | 先做什么 |
|---|---|---|
| **AI 全部用不了**(抽取/续写/对比失败) | DeepSeek 账户没余额了(最常见) | 去 platform.deepseek.com 看余额 → 充值。日志里看到 `402 Insufficient Balance` 就是它 |
| **网站打不开 / 502** | 后端挂了 | `sudo systemctl restart huimeng-backend`,再看 `status` 和日志 |
| **网站打不开 / 证书错误** | 证书过期 或 nginx 挂了 | `sudo systemctl reload nginx`;证书见第 5 节 |
| **实时进度条不动**(抽取转圈到结束才跳) | 前端没更新 或 nginx 没关缓冲 | 确认前端是最新版(第 1.B 节)+ nginx 是最新版(第 1.C 节) |
| **偶发 "database is locked"** | 并发写撞库 | 已修(busy_timeout)。若仍频繁,问 Claude 是否要迁 PostgreSQL |
| **点哪都慢** | 内存/CPU 吃紧 或 流量上来 | `free -h` / `top` 看负载;长期慢就升配服务器 |
| **付款弹窗没有收款码** | 收款码图没传 | 把微信码命名 `wechat_qr.png` 传到 `/opt/huimeng/app/backend/data/payment_qrcodes/` |
| **报了一堆我看不懂的红字** | —— | 截图 + `journalctl` 日志发 Claude,别自己硬扛 |

> **黄金法则**:任何报错,先 `journalctl -u huimeng-backend -n 100` 抓日志,连同你做了什么,一起发给 Claude。90% 的问题这样就能定位。

---

## 4. 数据备份 —— 你的命根子(务必做)

平台所有数据(用户、订单、项目、推演)都在**一个 SQLite 文件** `/opt/huimeng/app/backend/data/huimeng.db`。**这文件没了 = 全没了**,所以备份不是可选项。

### 装自动备份(只需做一次)
```bash
sudo -u huimeng crontab -e
# 在打开的编辑器里加这一行(每 6 小时备份一次),存盘退出:
0 */6 * * * bash /opt/huimeng/app/deploy/backup.sh >> /opt/huimeng/backups/backup.log 2>&1
```
立刻手动跑一次确认能用:
```bash
sudo -u huimeng bash /opt/huimeng/app/deploy/backup.sh
ls -la /opt/huimeng/backups/   # 应看到 huimeng-时间戳.db + data-时间戳.tar.gz
```

### ⚠️ 强烈建议:备份再传一份"离机"
本机磁盘坏了,本机的备份也一起没。建议在 `deploy/backup.sh` 末尾配阿里云 OSS 离机副本(文件里留了注释模板),或定期手动把 `/opt/huimeng/backups/` 下载到自己电脑。**有量了一定要做这步。**

### 怎么恢复(万一出事)
```bash
sudo systemctl stop huimeng-backend
sudo -u huimeng cp /opt/huimeng/backups/huimeng-<某个时间戳>.db /opt/huimeng/app/backend/data/huimeng.db
sudo systemctl start huimeng-backend
```

---

## 5. 证书 / 域名 / 服务器到期 —— 日历上记三个日子 📅

| 事项 | 现状 | 你要做的 |
|---|---|---|
| **HTTPS 证书**(Let's Encrypt) | 90 天有效,**自动续期** | 一般不用管。确认续期任务在:`sudo systemctl status certbot.timer`(active 就行)。手动测续期:`sudo certbot renew --dry-run` |
| **服务器到期** | **2026-09-18 到期** | 到期前去阿里云续费,别让它停机(停机 = 全站挂) |
| **域名 shuangdayeye.cn** | 看你买的年限 | 域名注册商续费,别过期 |
| **备案** | 黔ICP备2026004840号-1 | 一般不用动;换域名才要重新备案 |

> 建议:在手机日历上设 **2026-09-01**(服务器到期前两周)+ 域名到期前两周的提醒。

---

## 6. 钱要盯着的两笔 💰

1. **DeepSeek 余额** —— AI 功能全靠它。平台默认用你的 key(`.env` 里 `OPENAI_API_KEY`),用户用得多你烧得多。余额 0 → AI 全挂(报 402)。
   - 省钱招:引导重度用户开 **BYOK(自带密钥)**,他们用自己的 key,你零成本。
   - 上限保护:量大了让 Claude 帮你加"每月消费上限告警"。
2. **服务器 + 域名续费**(见第 5 节)。

---

## 7. 桌面客户端发新版本

改完代码、想发桌面端新版(比如 v0.2.0):
```bash
# 在你电脑上(或让 Claude 做):
cd C:\Users\Administrator\Desktop\huimeng
git pull
# 改 frontend/src-tauri/tauri.conf.json 里的 "version" 为新版本号
git add -A && git commit -m "bump desktop v0.2.0" && git push
# 去【公开发布仓库】打标签触发云端出包:
#   clone github.com/LaL-999/huimeng-desktop → git tag desktop-v0.2.0 → git push origin desktop-v0.2.0
```
CI 自动编译 → 公开仓库出**草稿 Release** → 你去 Releases 页面 **Publish**。
老用户下次打开桌面端会自动检查更新(已接通自动更新器)。门户下载也自动指向最新版。

> 这套有点绕,**发新版直接让 Claude 全程带你**,它知道每一步。

---

## 8. 什么时候别硬扛,直接找 Claude 🤝

你**不需要**会写代码 / 会运维。你的角色是"老板 + 操作员",Claude 是"工程师"。明确分工:

**Claude 能直接做的(尽管让它做)**:
- 改代码 / 加功能 / 修 bug
- 诊断报错(你把日志贴给它)
- 在你这台电脑上构建前端 + 打包 zip
- 写部署命令、给你一步步带

**只有你能做的(Claude 做不了,会列清单给你)**:
- 上传文件到服务器(Workbench / SSH)
- 在服务器上跑 Claude 给你的命令
- 花钱的事:充值 DeepSeek、续费服务器/域名、买证书
- 把收款码图、logo 这类素材给它
- 在网页上点"确认/发布"这类不可逆操作

**最佳协作姿势**:遇到任何情况 → 截图 + 复制报错/日志 → 发给 Claude → 它告诉你"跑这条命令"或"我来改,改完给你打包"。你照做即可。

---

## 9. 一页纸应急卡(网站突然挂了,先做这三件)

```bash
# ① 后端还活着吗?不活就重启
sudo systemctl restart huimeng-backend
sudo systemctl status huimeng-backend     # 看是不是 active (running)

# ② 看最近日志有没有红字
sudo journalctl -u huimeng-backend -n 80 --no-pager

# ③ nginx 还正常吗?
sudo nginx -t && sudo systemctl reload nginx
```
做完还不行 → 把上面 ② 的日志截图发 Claude。**别慌,数据在 SQLite 文件里,重启不会丢。**

---

_最后更新:2026-06-24 · 配套:DEPLOY.md(首次部署)/ DESKTOP_CLIENT.md(桌面端)/ deploy/(配置 + 脚本)_
