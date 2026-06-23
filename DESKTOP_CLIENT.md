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
| `.github/workflows/desktop-release.yml` | 云端出三平台安装包的 CI |
| `frontend/src/api/client.ts` | `API_BASE = (VITE_API_BASE \|\| "") + "/api"` |
| `portal/index.html` | 门户下载区(自动发现最新 Release) |

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

## 4. 发布(推荐路径 —— 零本地 Rust,云端编译)

### 4.1 一次性配置

GitHub 仓库 → **Settings → Secrets and variables → Actions → Variables** 新建:

| 变量名 | 值 | 说明 |
|---|---|---|
| `PROD_API_BASE` | `https://api.<你的域名>` | **必填**。壳化版后端基址,不填桌面端连不上服务器 |

> 后端已放行 Tauri origin 的 CORS(`tauri://localhost` / `http://tauri.localhost`),
> 见 `backend/app/main.py` 的 `allow_origin_regex`。无需额外配置。

### 4.2 出包

```bash
# 改版本号(可选):frontend/src-tauri/tauri.conf.json 的 "version"
git tag desktop-v0.1.0
git push origin desktop-v0.1.0
```

CI 自动在 Windows / macOS / Linux runner 上编译,产物上传到一个**草稿 Release**。

### 4.3 发布

进 GitHub **Releases**,检查草稿里的安装包(Windows `.msi`/`.exe`、macOS `.dmg`、
Linux `.AppImage`/`.deb`),点 **Publish release**。

发布后,**门户下载按钮自动生效** —— `portal/index.html` 会调 GitHub API 发现最新
Release,按访客系统推荐对应安装包。无需改门户代码。

---

## 5. 替换应用图标

当前用的是 Tauri 默认图标。拿到品牌 logo 后:

```bash
cd frontend
npx tauri icon path/to/logo-1024.png   # 1024×1024 PNG,透明底最佳
```

会自动生成 `src-tauri/icons/` 下全套尺寸(.ico/.icns/.png),提交即可。

---

## 6. 代码签名(强烈建议,否则用户被吓退)

| 平台 | 不签名的后果 | 怎么签 |
|---|---|---|
| **Windows** | SmartScreen 拦"未知发布者",用户要点"仍要运行" | EV 代码签名证书(DigiCert 等,~$300+/年);短期可先不签 |
| **macOS** | **直接打不开**(Gatekeeper 拦) | Apple Developer($99/年)+ 公证;要出 Mac 版绕不开 |
| **Linux** | 无影响 | 不需要 |

签名证书配好后,在 CI 里加对应 secrets(参见 tauri-action 文档的 Windows/macOS 签名节)。
**建议先只出 Windows 版**(自签或让用户点"仍要运行"),验证 workflow 跑通,再上 Mac。

---

## 7. 开启自动更新(需要一次性生成签名密钥)

更新器需要一对 **minisign 密钥**:私钥是机密(进 GitHub Secrets),公钥进配置。
代码里**暂未启用**(保证首个版本零配置即可出包)。要开启:

### 7.1 生成密钥对

```bash
cd frontend
npx tauri signer generate -w ~/.tauri/huimeng-updater.key
# 输出公钥(一长串 base64)。私钥写到 ~/.tauri/huimeng-updater.key
```

### 7.2 加 GitHub Secrets

| Secret | 值 |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | `~/.tauri/huimeng-updater.key` 文件内容 |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | 生成时设的口令(没设留空) |

> CI workflow 已预留这两个 env,配好即生效。**私钥永不入库**。

### 7.3 改 `frontend/src-tauri/Cargo.toml` 加依赖

```toml
[dependencies]
tauri-plugin-updater = "2"
```

### 7.4 改 `frontend/src-tauri/src/lib.rs` 注册插件

```rust
tauri::Builder::default()
    .plugin(tauri_plugin_updater::Builder::new().build())   // ← 加这行
    .setup(|app| { /* ... 原样 ... */ })
```

### 7.5 改 `frontend/src-tauri/tauri.conf.json` 加 updater 配置块

```jsonc
"plugins": {
  "updater": {
    "endpoints": [
      "https://github.com/LaL-999/hunjing/releases/latest/download/latest.json"
    ],
    "pubkey": "<第 7.1 步输出的公钥>"
  }
}
```

`tauri-action` 发布时会自动生成 `latest.json` 并签名。之后客户端启动会静默检查更新。
前端如需「有新版」提示弹窗,装 `@tauri-apps/plugin-updater` 调 `check()`(可后做)。

---

## 8. 常见问题

- **桌面端白屏 / 接口 401 或 CORS 报错**:`PROD_API_BASE` 没配,或配的域名后端没起。
- **macOS 打不开("已损坏")**:没签名/没公证,见第 6 节。
- **门户下载还是"即将上线"**:还没 Publish 任何 Release,或 GitHub API 限流(等会儿)。
- **改了价格门户没变**:门户优先拉 `/api/payments/catalog`,拉不到才用兜底常量。

---

_最后更新:2026-06-09 · 由 Claude(项目负责人)维护_
