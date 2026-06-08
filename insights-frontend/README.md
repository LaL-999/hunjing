# insights-frontend — 浑晶洞察后台前端

独立 Vue 项目,独立部署(用户看不见的内部管理面板)。

## 启动

```bash
cd insights-frontend
npm install
npm run dev      # → http://localhost:5174
```

## 配置

`.env.local`(自建):
```
VITE_INSIGHTS_BASE=http://localhost:8001
VITE_ADMIN_TOKEN=huimeng-insights-dev-token
```

## 鉴权

所有 API 调用带 `X-Admin-Token` header(从 `.env` 读)。无 token → 401。

## 页面(A4 sprint)

- `/` 总览 — 用户数 / 项目数 / 事件分布
- `/funnel` 转化漏斗
- `/retention` 留存
- `/user/:id` 单用户画像

## 设计

- **独立 URL**(`localhost:5174`),用户访问主平台时**完全看不到入口**
- 主平台 frontend 任何地方都不引用本项目
- 通过 `VITE_INSIGHTS_BASE` 配 insights-backend 地址
