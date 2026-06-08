# insights-backend — 浑晶洞察子系统

**用户行为追踪 + 数据 agent + 知识库**(2026-05-27 启动)

## 设计原则

**3 层隔离**(零污染主平台):
1. **代码隔离**:主平台 `backend/` 完全不改;`frontend/` 只加 1 个 `useAnalytics` composable
2. **DB 隔离**:本服务用独立 `analytics.db`,只读 attach 主平台 `huimeng.db`
3. **后台隔离**:本服务独立端口,管理面板用独立 `insights-frontend/` Vue 项目(用户看不见)

**数据流方向**(永不反向):
- 主平台 → 洞察:单向写(埋点)
- 洞察 → 主平台:单向只读(SQLite ATTACH)
- agent 永远不能改主平台数据 / 配置,只产报告供人读

## 目录结构

```
insights-backend/
├── app/
│   ├── main.py            FastAPI 入口
│   ├── config.py          配置(端口 / DB 路径 / huimeng.db 路径)
│   ├── db.py              SQLite 连接 + huimeng.db read-only attach
│   ├── deps.py            FastAPI Depends(get_db / get_admin_user)
│   ├── models/
│   │   └── event.py       Event dataclass + EVENT_TYPES 枚举
│   ├── schemas/
│   │   └── track.py       Pydantic 请求 / 响应
│   ├── routers/
│   │   ├── track.py       POST /track 接收埋点
│   │   └── admin.py       GET /admin/* 给 insights-frontend 调
│   └── services/
│       ├── analytics_query.py  聚合查询(漏斗 / 留存 / 用户画像)
│       └── agent.py        Level 1/2 数据 agent(后期 A5/A6)
├── migrations/
│   └── 001_events.sql      初始 schema
├── tests/
└── pyproject.toml
```

## 端口约定

- **insights-backend**:8001(主平台 backend 用 8000)
- 主平台 frontend 埋点直发 `http://localhost:8001/track`
- insights-frontend 通过 `http://localhost:8001/admin/*` 调

## 启动

```bash
cd insights-backend
uvicorn app.main:app --port 8001 --reload
```
