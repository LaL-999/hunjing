# 浑晶 · 商业化重塑 — 需你亲自采集的资源清单

> 这份清单列出**只有你能提供**的资源/信息。代码我全包了;这些是钱、域名、
> 法律主体、品牌素材类的东西,我没法替你生成。交付时直接把对应项发我即可,
> 我会接进系统。按优先级排序。

---

## 🔴 P0 — 不给就收不到钱(统一支付上线必需)

### 1. 收款二维码图片
- **微信个人收款码**(必需)→ 截图/导出成图片文件(.png / .jpg)
- 支付宝个人收款码(可选,有就给)
- **放哪**:`backend/data/payment_qrcodes/` 目录下
- **怎么生效**:告诉我文件名,我配到 env `BYOK_WECHAT_QR_FILENAME` / `BYOK_ALIPAY_QR_FILENAME`
- 注:个人收款码有单笔/单日限额(微信通常 ¥3000/笔上限因人而异),大额订阅可能要拆单或走支付宝

### 2. 收款人真实姓名
- 就是**微信转账时对方看到的你的名字**(如"张三"或脱敏的"张*三")
- **用途**:审核截图时核对"收款方是不是你"(防伪造截图)
- 配到 env `BYOK_PAYEE_NAME`

### 3. 价格最终确认
- 我**沿用了你现有的定价**(订阅 Pro ¥138 / Max ¥438 / Super Max ¥1388 月付,年付 -15%;
  BYOK ¥30/月;配额包 小 ¥18 / 中 ¥85 / 大 ¥320)
- 如果要调价,告诉我新数字,我改 `billing_service.PLAN_PRICE_CENTS` / `credit_service.ADDON_PACKAGES`

---

## 🟡 P1 — 门户页 + 上线部署需要

### 4. 域名
- 你打算用的域名(如 `huimeng.com` / `huimeng.cn`)
- 门户页 + 在线创作平台都挂这个域名下

### 5. 服务器 / 部署环境
- 有没有云服务器(阿里云 / 腾讯云 ECS 等)?配置大概多少?
- 后端 FastAPI + 前端 + 门户 + 洞察后台都要部署上去
- 我会写部署脚本/说明,但服务器得你买

### 6. 备案
- 你已有 **黔ICP备2026004840号-1**(页脚已显示)✓
- 如果换域名,备案要重新关联;沿用现域名则无需动

### 7. 品牌素材(门户页用 — 没有我先用占位)
- **Logo**:高清 SVG 或 PNG(透明底最佳)
- 一句话**品牌 slogan**(如"让每个创作者都有 AI 创作搭子")— 没有我先拟一个你改
- 可选:产品截图 2-4 张(剧创态/3D 图谱/多模型对比这些好看的界面)
- 可选:创始人/团队故事(门户"关于"区,没有就不放)

### 8. 客服 / 联系邮箱
- 门户页脚 + 订单驳回引导 + 用户求助用
- 现在代码里是占位 `hi@huimeng.example`,给我真实邮箱我换掉

---

## 🟢 P2 — 桌面客户端(Phase 3,脚手架 + CI 已就位)

> 完整操作手册见 `DESKTOP_CLIENT.md`。下面只列**需要你提供的东西**。

### 9. ⭐ 后端基址 `PROD_API_BASE` —— ⚠️ 改放到【公开】发布仓库
- 闭源后构建改在公开仓库 `huimeng-desktop` 跑,所以这个变量要配在那边:
  `huimeng-desktop` → Settings → Secrets and variables → Actions → **Variables** 加
  `PROD_API_BASE = https://api.shuangdayeye.cn`(你之前加在私有 hunjing 的那个可删/留着没用)
- ⚠️ **备案核对**:确认 `黔ICP备2026004840号-1` 是否已绑定 shuangdayeye.cn;
  若该备案是别的域名,需在工信部把 shuangdayeye.cn 加进备案,否则国内打不开

### 10. ✅ 应用图标 —— 已完成(从你的 Hunjing.png 抠水晶球)
- 你重放的 Hunjing.png 已收到;我从中抠出绿色水晶球图标,生成了全套尺寸,
  替掉了临时占位图。装好后任务栏/开始菜单就是这颗水晶。无需你再做什么。

### 11. 代码签名证书 —— 我已查证 2026 现状(你说"能帮就帮":我帮你定方案,买证要你本人)
> 证书绑你**真实身份**(护照/身份证 + 视频核验)+ 要付费,只能你本人申请;我把
> 最省钱的正路定好,你照着买,买完证书发我我接进 CI。完整版见 `DESKTOP_CLIENT.md` 第 6 节。
- **验证期:别买,直接发未签名包**(给用户"更多信息 → 仍要运行"的指引,我已写好)
- **千万别买 EV** —— 2024 起 EV 也不再免 SmartScreen,贵 2-3 倍纯浪费(已查证微软官方)
- **Azure Trusted Signing(最便宜 $9.99/月)对中国个人资格出局**,别考虑
- 要扩量了再买:**Certum 开源代码签名云版(首年 ~$58–104,若浑晶开源最划算)**
  或 **SSL.com IV + eSigner 云签(~$129/年,闭源/想 CI 直签选它)** —— 都走**云签名**
  (2023 起私钥必须存硬件,云签名免 USB token 寄中国的清关麻烦)
- **macOS**:要出 Mac 版必须 Apple Developer($99/年)+ 公证,所以先只出 Windows

### 12. 自动更新签名密钥(你说"教我操作":步骤已写全,跑 1 条命令 + 填 2 个 Secret)
- 完整 7 步教学在 `DESKTOP_CLIENT.md` 第 7 节(已按 Tauri v2 现状查证更正)
- 核心:① `cd frontend && npm run tauri signer generate -- -w ~/.tauri/huimeng.key`
  ② 私钥内容 → GitHub Secret `TAURI_SIGNING_PRIVATE_KEY` ③ 公钥内容 → 我填进 conf
- ⚠️ 头号坑我已在文档标红:`bundle.createUpdaterArtifacts: true` 漏了更新永远装不上
- 不开自动更新也能正常发版,只是用户得手动来官网下新版(门户已自动发现最新版)

### 13. ✅ 客户端分发托管 —— 已建好(两仓库,代码闭源)
- 我已建公开发布仓库 **`LaL-999/huimeng-desktop`**(只放安装包,源码不入)
- CI 在公开仓库里 sparse-checkout 私有 `hunjing/frontend` 编译,产物发布到公开仓库
- 门户 + 自动更新已指向它;用户免登录直接下载,源码一行不外泄
- **你只需做一次**(详见 `DESKTOP_CLIENT.md` 4.1):在 `huimeng-desktop` 仓库加
  ① Secret `SOURCE_REPO_TOKEN`(细粒度 PAT,对 hunjing 仅 Contents: Read)
  ② Variable `PROD_API_BASE`(见第 9 项)③ 推 `desktop-v0.1.0` 标签触发首次出包

---

## 我这边已经/即将自动完成的(你不用管)

- ✅ 统一支付内核(订单中心 + SKU + 履约 + 审核)— 已上线
- ✅ 订阅/配额接入统一支付 — 已上线
- ✅ insights 统一订单审核页 — 已上线
- ✅ 门户页(静态高级营销站,自动拉定价 + 自动发现下载)— 已接 shuangdayeye.cn
- ✅ Tauri 桌面客户端脚手架 + 云端 CI(已收窄为 **Windows-only 验证版**)— 已就位
- ✅ 域名 shuangdayeye.cn 接线(门户/CORS/文档)+ 临时品牌图标 + 签名/更新器查证 — 已做
  (你只剩:配 `PROD_API_BASE` 变量 + 重放 `Hunjing.png` + 推 `desktop-v0.1.0` 标签)

---

_最后更新:2026-06-09 · 由 Claude(你的项目负责人)维护_
