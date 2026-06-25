"""insights-backend 配置(2026-05-27).

设计:
  - 跟主平台 backend/app/config.py 模式一致(env 变量 + 默认值)
  - 主要配置:监听端口 / analytics.db 路径 / huimeng.db 路径(read-only)
  - 不读主平台 backend 的 config(完全独立,避免代码污染)
"""
from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass


# 主平台根目录(默认假设 insights-backend 与 backend 平级)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    """配置不可变;通过 env 变量覆盖默认值。"""

    # === 服务监听 ===
    HOST: str = "127.0.0.1"
    PORT: int = 8001                          # 主平台 backend 用 8000,本服务 8001

    # === 数据库 ===
    # 本服务独立的 analytics.db(写)
    ANALYTICS_DB_PATH: Path = _PROJECT_ROOT / "insights-backend" / "data" / "analytics.db"
    # 主平台 huimeng.db(只读 attach,用于关联用户画像 / 项目数据)
    # ★ 必须与主平台 backend/app/config.py 的 db_path 默认值保持一致.
    # 主平台默认是 backend/data/huimeng.db(不是 backend/huimeng.db).
    # 历史 bug:INS-A1 此处少写了一层 data/,导致 attach 到 0 字节空文件,
    # 表探针失败,UI 一直显示"huimeng.db 未连接"(INS-A9 末发现,2026-05-27 末³ 治本).
    HUIMENG_DB_PATH: Path = _PROJECT_ROOT / "backend" / "data" / "huimeng.db"

    # === CORS ===
    # 主平台 frontend 发埋点 → 本服务需要允许 CORS
    # insights-frontend 调 /admin/* 也需要
    # 2026-06-05:5174 被别项目占用时 vite 自动 fallback 5175 / 5176,这里都允许
    ALLOWED_ORIGINS: tuple[str, ...] = (
        "http://localhost:5173",      # 主平台 frontend dev
        "http://localhost:5174",      # insights-frontend dev(默认端口)
        "http://localhost:5175",      # insights-frontend fallback 1
        "http://localhost:5176",      # insights-frontend fallback 2
        "http://127.0.0.1:5173",
        "http://127.0.0.1:5174",
        "http://127.0.0.1:5175",
        "http://127.0.0.1:5176",
    )

    # === 鉴权 ===
    # 简单 admin token — 通过 env 设置,insights-frontend 调 /admin/* 时带在 header
    # 默认值仅 dev 用,生产环境必须 env 覆盖
    ADMIN_TOKEN: str = "huimeng-insights-dev-token"

    # === 运行环境 ===
    # "development"(默认)/ "production"。生产下做启动守卫:token 仍是默认值则拒启动。
    ENV: str = "development"

    # === 埋点限流 ===
    # 同 user_id 同 event_type 在 N ms 内的重复埋点视为去重(防 frontend 误派发)
    DEDUP_WINDOW_MS: int = 100


# 默认 dev token —— 生产绝不能用它(会被任何人从公开 bundle 提取)
_DEFAULT_DEV_TOKEN = "huimeng-insights-dev-token"


def _load() -> Settings:
    """从环境变量读配置,fall back 到默认值。"""
    kwargs: dict = {}
    if v := os.getenv("INSIGHTS_HOST"):
        kwargs["HOST"] = v
    if v := os.getenv("INSIGHTS_PORT"):
        kwargs["PORT"] = int(v)
    if v := os.getenv("INSIGHTS_ANALYTICS_DB"):
        kwargs["ANALYTICS_DB_PATH"] = Path(v)
    if v := os.getenv("INSIGHTS_HUIMENG_DB"):
        kwargs["HUIMENG_DB_PATH"] = Path(v)
    if v := os.getenv("INSIGHTS_ADMIN_TOKEN"):
        kwargs["ADMIN_TOKEN"] = v
    if v := os.getenv("INSIGHTS_ENV"):
        kwargs["ENV"] = v

    # CORS:在内置 dev 白名单基础上,叠加生产 origin(逗号分隔)。
    #   例:INSIGHTS_ALLOWED_ORIGINS=https://app.shuangdayeye.cn,https://insights.shuangdayeye.cn
    # 写 env 钩子(而非把生产域写死进 tuple),后续换域名不必改代码。
    if extra := os.getenv("INSIGHTS_ALLOWED_ORIGINS"):
        base = Settings.__dataclass_fields__["ALLOWED_ORIGINS"].default
        add = tuple(o.strip() for o in extra.split(",") if o.strip())
        kwargs["ALLOWED_ORIGINS"] = base + add

    s = Settings(**kwargs)

    # 启动守卫:生产环境若 admin token 仍是默认 dev 值 → 拒绝启动。
    #   token 会被 build 进可下载的前端 bundle,默认值 = 形同无鉴权。
    #   生产必须 env 覆盖 INSIGHTS_ADMIN_TOKEN(`openssl rand -hex 24`)。
    if s.ENV.lower() == "production" and s.ADMIN_TOKEN == _DEFAULT_DEV_TOKEN:
        raise RuntimeError(
            "拒绝启动:INSIGHTS_ENV=production 但 INSIGHTS_ADMIN_TOKEN 仍是默认 dev 值。"
            "请用 `openssl rand -hex 24` 生成强 token 并写入 .env。"
        )
    return s


settings = _load()
