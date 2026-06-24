# 浑晶桌面客户端(Tauri 壳化)— 构建 / 发布 / 签名 / 自动更新指南

> 商业化重塑 Phase 3。本文是给你(项目所有者)的操作手册。代码侧已全部就位,
> 你按这里的步骤即可出包、发布、让官网自动提供下载。

---

## 1. 它是什么

把现有 Vue 创作平台**壳化**成桌面客户端:

- 平台仍以 **Web 服务器部署**为主(门户 + 在线创作 + 洞察后台)。
- 桌面端是一层"壳"——打包**编译后的 SPA**(`frontend/dist`),所有 `/api`
  请求走**远程后端**(由 `VITE_API_BASE` 指定)。
- 技术栈:**Tauri v2**(Rust 壳 + 系统 WebView,包体 ~3–10MB,远小于 Electron)。

```
用户机器                          你的服务器
┌─────────────────┐              ┌──────────────────┐
│ 浑晶.exe (Tauri)│   HTTPS      │ FastAPI 后端     │
│  └ 内置 SPA     │ ───────────▶ │  /api/*          │
│     fetch /api  │   JWT 鉴权   │ SQLite           │
└─────────────────┘              └──────────────────┘
```

---

## 2. 目录 / 文件

| 路径 | 作用 |
|---|---|
| `frontend/src-tauri/` | Tauri 工程(Cargo + 配置 + 图标 + Rust 入口) |
| `frontend/src-tauri/tauri.conf.json` | 应用配置(窗口 / 包元数据 / 标识符) |
| `frontend/src/api/client.ts` | `API_BASE = (VITE_API_BASE \|\| "") + "/api"` |
| `portal/index.html` | 门户下载区(自动发现最新 Release) |
| **公开仓库** `LaL-999/huimeng-desktop` | 发布流水线 + 安装包托管(源码不入此仓) |

> **两仓库架构(代码闭源)**:源码全在私有 `LaL-999/hunjing`;公开
> `LaL-999/huimeng-desktop` 只放编译产物。CI 在公开仓库里 sparse-checkout 私有
> 仓库的 `frontend/`(用只读 PAT)来编译,产物发布到公开仓库的 Release。
> 门户 + 自动更新都指向公开仓库,用户免登录直接下载;源码一行不外泄。

---

## 3. 本地开发(可选 —— 需要 Rust)

本机若没装 Rust,**跳过这节**,直接用云端 CI 出包(第 4 节)。

```bash
# 1) 装 Rust:https://rustup.rs
# 2) Windows 还需 WebView2 Runtime(Win11 自带)+ MSVC Build Tools
# 3) 跑桌面 dev(自动起 vite + 开 Tauri 窗口)
cd frontend
npm install
npm run tauri:dev
```

出本地安装包:`npm run tauri:build`(产物在 `frontend/src-tauri/target/release/bundle/`)。

---

## 4. 发布(零本地 Rust,云端编译 —— 在公开发布仓库操作)

### 4.1 一次性配置(都在【公开】仓库 `huimeng-desktop` 里设)

进 `LaL-999/huimeng-desktop` → **Settings → Secrets and variables → Actions**:

**Secrets**:

| 名称 | 值 | 必填? |
|---|---|---|
| `SOURCE_REPO_TOKEN` | 细粒度 PAT,对私有仓库 `hunjing` 仅 **Contents: Read-only** | ✅ 必填(否则拉不到源码) |
| `TAURI_SIGNING_PRIVATE_KEY` | 更新器私钥内容 | 开自动更新后才需 |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 私钥口令(没设留空) | 开自动更新后才需 |

**Variables**:

| 名称 | 值 |
|---|---|
| `PROD_API_BASE` | `https://api.shuangdayeye.cn` |

> **PAT 怎么建**:GitHub 头像 → Settings → Developer settings → Fine-grained tokens
> → Generate new token → Repository access 选 `hunjing` → Permissions 只勾
> **Contents: Read-only** → 生成 → 复制,存成上面的 `SOURCE_REPO_TOKEN`。
>
> 后端已放行 Tauri origin 的 CORS + 门户/app 子域;部署时把生产
> `HUIMENG_CORS_ORIGINS` 设成 shuangdayeye.cn 三域名(见 `backend/.env.example`)。

### 4.2 出包(版本号在私有仓库改,标签推到【公开】仓库)

```bash
# 1) 版本号在私有仓库改:frontend/src-tauri/tauri.conf.json 的 "version",push 私有 main
# 2) 到公开仓库推标签触发构建:
git clone https://github.com/LaL-999/huimeng-desktop.git
cd huimeng-desktop
git tag desktop-v0.1.0
git push origin desktop-v0.1.0
```

CI 在公开仓库里拉私有源码编译,产物上传到公开仓库的**草稿 Release**。

> **当前验证阶段只出 Windows 版**。要加 Mac/Linux:在公开仓库的
> `desktop-release.yml` 把 `runs-on` 改成矩阵(模板在 git 历史里)。

### 4.3 发布

进公开仓库 **Releases**,检查草稿里的安装包(Windows `.msi` / `.exe` setup),
点 **Publish release**。发布后门户下载按钮自动生效。

发布后,**门户下载按钮自动生效** —— `portal/index.html` 会调 GitHub API 发现最新
Release,按访客系统推荐对应安装包。无需改门户代码。

---

## 5. 替换应用图标

当前用的是**临时品牌图标**(暖紫圆角 + 白「晶」字,源图
`frontend/src-tauri/icon-source-placeholder.png`)。你的 `Hunjing.png` 之前没落到
仓库能访问的位置,所以先用了占位图。拿到真 logo 后一条命令替换:

```bash
cd frontend
npx tauri icon path/to/Hunjing.png   # 1024×1024 PNG,方形,透明或实底都行
```

会自动重生成 `src-tauri/icons/` 全套尺寸(.ico/.icns/.png),提交即可。
(我跑这条命令时顺手删了它生成的 `android/` `ios/` 目录,桌面端用不到。)

---

## 6. 代码签名(2026 现状 —— 已查证,别踩旧坑)

> 这节的结论经过联网核验(微软官方文档 2026-05 + CA/B Forum 规则 + 多家 CA),
> 推翻了网上大量过期教程。**先读"一句话":验证期别买证书。**

### 一句话

**验证期直接发"未签名"包,先别买证书。** 等种子用户跑通、要扩量了,再买
**Certum 开源代码签名(云 SimplySign 版,首年约 $58–104)**。
**绝对不要为了过 SmartScreen 去买 EV** —— 这条 2026 年已经失效。

### ⚠️ 三个最容易踩的旧坑(都已查证)

1. **EV 证书不再免 SmartScreen。** 微软官方(learn.microsoft.com,2026-05 更新)
   原话:"EV 证书不再绕过 SmartScreen……这个行为已不存在。" 大约 2024-03 生效。
   现在 OV 和 EV 在 SmartScreen 面前一样,都靠下载量慢慢攒信誉。EV 贵 2-3 倍纯浪费。
2. **Azure Trusted Signing(2026 改名 Azure Artifact Signing)对你出局。** 价格最香
   ($9.99/月、无硬件 token、原生 GitHub Action),但官方 FAQ:个人开发者**仅限美/加**,
   组织仅限美/加/欧/英。**中国个人两条路都走不通**,别把 CI 架在它上面。
3. **便宜的可下载 .pfx 证书已绝迹。** 2023-06 起 CA/B 规定私钥必须存硬件(FIPS),
   交付只有 USB token(寄中国清关慢)或**云 HSM / 云签名**。→ 选云签名,绕开物流。
   另:2026-03-01 起证书有效期上限砍到 ~458 天,多年期要每 400 多天用同一身份重签。

### 个人主体到底买得到什么 + 怎么选

可以买。证书以 **IV(个人验证)/ Sole-Proprietor** 形式签发在你**真实姓名**下,
等同 OV 信任。身份核验:护照/身份证 + 手持自拍 + 视频(护照适合中国申请)。

| 方案 | 价格 | 中国个人可用 | 适合 |
|---|---|---|---|
| **Certum 开源代码签名(云)** ⭐ | 首年 ~$58–104,续费 ~$32 | 是 | **若浑晶开源**,最便宜正路(需仓库+license+一张水电账单) |
| **SSL.com IV + eSigner 云签** | ~$129/年 | 是 | 闭源 / 想在 CI 里免 USB 直接签 |
| Sectigo/Comodo IV | ~$210+/年 | 是 | 同效更贵,不推荐 |

> 买了 OV/IV 也**不会立刻免警告**:首批下载仍弹一次,但显示你的**真实姓名**(不再"未知
> 发布者"),信誉随下载量积累(通常几周 + 数百次干净安装)。唯一零首警告路径是上架
> Microsoft Store(微软重签)。

### macOS / Linux

- **macOS**:不公证**直接打不开**(Gatekeeper)。必须 Apple Developer($99/年)+ 公证。
  → 出 Mac 版前绕不开,**这也是先只出 Windows 的原因**。
- **Linux**:无需签名。

### 验证期未签名包 —— 把这段写进 Release notes 给种子用户

```
1. 双击 Huimeng_<版本>_x64-setup.exe
2. 弹"Windows 已保护你的电脑" → 点左下角"更多信息(More info)"
3. 显示"未知发布者" → 点"仍要运行(Run anyway)"→ 开始安装
（备选:右键 .exe → 属性 → 勾"解除锁定(Unblock)" → 确定,再运行）
```

> - SmartScreen **没有任何 tauri.conf 开关能关掉**,别浪费时间找。
> - Win11 的 **Smart App Control** 更狠,可能连"仍要运行"都不给 —— 但只影响开了 SAC
>   的设备(默认很多是关的),验证期可接受。

---

## 7. 开启自动更新(教学 —— 已查证 Tauri v2 确切步骤)

更新器需要一对 **minisign 密钥**:私钥是机密(进 GitHub Secrets),公钥进配置。
代码里**暂未启用**(保证首版零配置出包)。下面 7 步开启。

> 🔴 **头号坑(漏了白忙)**:`bundle.createUpdaterArtifacts: true` 是 v2 新增硬要求
> (v1 没有)。漏了它 → 不生成 `.sig` 签名产物 → 构建照样成功,但**更新永远装不上且
> 不报错**。紧随其后两个运行时坑:`pubkey` 缺/写错 → 运行时 `InvalidSignature`
> (v2 签名校验无法关闭;pubkey 要粘**内容**不是路径);capabilities 少权限 →
> `check()`/`relaunch()` 运行时抛错。

### 7.1 生成密钥对(在 frontend 目录跑)

```bash
cd frontend
npm run tauri signer generate -- -w ~/.tauri/huimeng.key
# 私钥 → ~/.tauri/huimeng.key(喂 CI,绝不外泄 + 务必备份)
# 公钥 → ~/.tauri/huimeng.key.pub(把内容填进 7.4 的 pubkey)
```

> ⚠️ 重新生成密钥 = 所有已发出去的客户端(持旧 pubkey)会**拒绝**新版本,永久失联。
> 密钥对要稳定 + 备份。

### 7.2 加 GitHub Secrets

| Secret | 值 |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | `~/.tauri/huimeng.key` 私钥**全部内容** |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 生成时设的口令(没设留空,但 CI 里仍要传这个 env) |

> ⚠️ 私钥**不能放 `.env`**,官方明确说对签名 key 无效 —— 必须是真环境变量(CI 里就是
> 上面 secrets,放 job 的 `env:` 下)。CI workflow 已预留这两个 env。

### 7.3 加 Rust 插件依赖(在 frontend/src-tauri 跑)

```bash
cargo add tauri-plugin-updater --target 'cfg(any(target_os = "macos", windows, target_os = "linux"))'
```

### 7.4 改 `frontend/src-tauri/src/lib.rs` 注册(必须 `#[cfg(desktop)]` 守卫)

```rust
#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            #[cfg(desktop)]
            app.handle().plugin(tauri_plugin_updater::Builder::new().build());
            if cfg!(debug_assertions) { /* ...原有 log 插件保留... */ }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
```

> 不加 `#[cfg(desktop)]` 会破坏 mobile 构建。

### 7.5 改 `frontend/src-tauri/tauri.conf.json`

`createUpdaterArtifacts` 在 **bundle** 下;`pubkey` 放**内容**;`endpoints` 是**数组**:

```jsonc
{
  "bundle": {
    "createUpdaterArtifacts": true
  },
  "plugins": {
    "updater": {
      "pubkey": "把 ~/.tauri/huimeng.key.pub 的全部内容粘到这里",
      "endpoints": [
        "https://github.com/LaL-999/huimeng-desktop/releases/latest/download/latest.json"
      ]
    }
  }
}
```

### 7.6 加 capabilities 权限 + JS 包

`frontend/src-tauri/capabilities/default.json` 的 `permissions` 加两项:
`"updater:default"`、`"process:allow-restart"`。然后在 frontend 装 JS 包:

```bash
npm install @tauri-apps/plugin-updater @tauri-apps/plugin-process
```

最小调用(可放 App.vue onMounted):

```javascript
import { check } from "@tauri-apps/plugin-updater";
import { relaunch } from "@tauri-apps/plugin-process";

const update = await check();
if (update) {
  await update.downloadAndInstall();
  await relaunch();
}
```

### 7.7 发布

照常推 `desktop-v*` 标签即可。**只要 `TAURI_SIGNING_PRIVATE_KEY` 在,`tauri-action`
就自动构建 updater 产物、签 `.sig`、生成带各平台 url+signature 的 `latest.json` 并上传
到 Release** —— 你不用手写 latest.json。

> 📌 网上很多写 `includeUpdaterJson: true` —— **这个 input 不存在**,会被静默忽略。
> 真名是 `uploadUpdaterJson`,而且**默认就是 true**,所以根本不用写。endpoint 那条静态
> URL 只在 latest.json 真上传后才有效,否则 `check()` 会 404。
>
> 📌 updater 签名密钥(`TAURI_SIGNING_PRIVATE_KEY`)和第 6 节的 Authenticode 代码签名
> **是两码事**:前者保证更新包完整性,后者是 Windows 信任。两者独立,可分别上。

---

## 8. 常见问题

- **桌面端白屏 / 接口 401 或 CORS 报错**:`PROD_API_BASE` 没配,或配的域名后端没起。
- **macOS 打不开("已损坏")**:没签名/没公证,见第 6 节。
- **门户下载还是"即将上线"**:还没 Publish 任何 Release,或 GitHub API 限流(等会儿)。
- **改了价格门户没变**:门户优先拉 `/api/payments/catalog`,拉不到才用兜底常量。

---

_最后更新:2026-06-09 · 由 Claude(项目负责人)维护_
