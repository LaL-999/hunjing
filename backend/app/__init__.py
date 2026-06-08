"""浑晶后端 (huimeng-backend) — MVP 阶段 1。

架构对齐 docs/后端架构ADR_v1.md。

模块:
  config   — .env 加载 + 配置 dataclass
  db       — sqlite3 连接 + 事务 helper
  main     — FastAPI 入口 + middleware + 路由挂载
  deps     — FastAPI Depends(get_db / get_current_user)
  models/  — 数据库表的 Python 表示(纯 dataclass,Sprint 1.B+ 起逐步加)
  schemas/ — Pydantic API 输入/输出 schema(Sprint 1.B+ 起逐步加)
  routers/ — FastAPI APIRouter,薄层(Sprint 1.B+ 起逐步加)
  services/— 业务逻辑,不依赖 FastAPI(Sprint 1.B+ 起逐步加)
"""

__version__ = "0.1.0"
