"""FastAPI 入口。

Sprint 进度:
  1.A:meta(/, /healthz)
  1.B:auth + consent
  1.C:projects + characters + relationships + events(共 19 个 endpoints)
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.config import settings
from app.db import healthcheck as db_healthcheck
from app.services.credit_service import InsufficientCredits
from app.services.project_service import ResourceNotFoundOrForbidden

logger = logging.getLogger(__name__)


def _init_sentry() -> None:
    """2026-06-02 上线监控:接入 Sentry 错误追踪.

    SENTRY_DSN 未设置 → 静默跳过(本地开发 / 测试 / 未配置 DSN 时不报错).
    SENTRY_DSN 设置 → 初始化 sdk,自动捕获:
      - FastAPI 路由内未捕获的异常
      - logger.warning / error 级别的 log
      - 慢请求(10% 采样,看 traces_sample_rate)

    需要先 pip 装:
      pip install "sentry-sdk[fastapi]>=2.0"

    配置 env:
      SENTRY_DSN=https://xxxx@xxx.ingest.sentry.io/yyyy
      ENV=production  (可选,默认 production)
    """
    dsn = os.getenv("SENTRY_DSN", "").strip()
    if not dsn:
        logger.info("SENTRY_DSN 未设置,跳过 Sentry 初始化(本地开发场景正常)")
        return

    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.logging import LoggingIntegration

        sentry_sdk.init(
            dsn=dsn,
            integrations=[
                FastApiIntegration(),
                # logger.warning/error 自动上报
                LoggingIntegration(level=logging.INFO, event_level=logging.WARNING),
            ],
            traces_sample_rate=0.1,  # 10% 请求采样(省 quota,够看趋势)
            profiles_sample_rate=0.0,  # 暂关 profile(占 quota 大)
            environment=os.getenv("ENV", "production"),
            release=__version__,
            # 不上传请求体里的敏感信息(JWT / OTP 等)
            send_default_pii=False,
        )
        logger.info(f"Sentry 已初始化:env={os.getenv('ENV', 'production')}, release={__version__}")
    except ImportError:
        logger.warning(
            "SENTRY_DSN 已设置但 sentry-sdk 未安装,跳过.运行:\n"
            '  pip install "sentry-sdk[fastapi]>=2.0"'
        )
    except Exception as e:  # noqa: BLE001
        # 监控本身不应阻塞应用启动
        logger.warning(f"Sentry 初始化失败,降级运行:{e}")


def create_app() -> FastAPI:
    # 先初始化 Sentry,然后再创建 FastAPI app
    # 这样后续 import / 路由注册 / startup hooks 的错都能被捕获
    _init_sentry()

    app = FastAPI(
        title="浑晶 (HunJing) Backend",
        description="MVP 阶段 1 后端 API。详细架构见 docs/后端架构ADR_v1.md。",
        version=__version__,
    )

    # CORS
    # 2026-06-05:加 allow_origin_regex 兜底 localhost / 127.0.0.1 任意端口
    # —— 主平台 frontend / 洞察后台 frontend / vite fallback 端口都自动通过
    # 与 allow_origins 是 OR 关系,任一匹配即放行
    # 生产环境 origin 走 settings.cors_origins(env 配置),regex 仅 dev 兜底
    # 2026-06-09:加 tauri 壳化客户端 origin —— 桌面端打包后 SPA 跑在
    #   tauri://localhost(mac/Linux WKWebView)或 http://tauri.localhost
    #   (Windows WebView2),不放行则桌面端所有跨域 API 被浏览器内核拦。
    #   这两个是固定字面量,Web 攻击者无法伪造,安全。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=(
            r"^(http://(localhost|127\.0\.0\.1):\d+"
            r"|tauri://localhost"
            r"|http://tauri\.localhost)$"
        ),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---------- 全局异常 handler ----------
    @app.exception_handler(ResourceNotFoundOrForbidden)
    async def handle_not_found(
        request: Request, exc: ResourceNotFoundOrForbidden
    ) -> JSONResponse:
        """资源不存在或不属于当前用户 → 404。

        统一 404 而非 403,避免暴露资源存在性(防探测攻击)。
        """
        return JSONResponse(
            status_code=404,
            content={
                "detail": {
                    "code": "NOT_FOUND",
                    "resource": exc.resource,
                    "message": str(exc),
                }
            },
        )

    @app.exception_handler(InsufficientCredits)
    async def handle_insufficient_credits(
        request: Request, exc: InsufficientCredits
    ) -> JSONResponse:
        """余额不足(含 item7:AI 调用前余额闸)→ 统一 429。

        任何入口(推演/续写/剧创态/漫创/对比…)调 AI 前若 0 余额且非 BYOK,
        LLM 客户端抛 InsufficientCredits,这里统一转 429 + 引导升级/开 BYOK,
        前端 client 拦 429 弹加购/升档/自携密钥 modal。省去每个 router 各写 try/except。
        """
        return JSONResponse(
            status_code=429,
            content={
                "detail": {
                    "code": "INSUFFICIENT_CREDITS",
                    "needed": getattr(exc, "needed", 1),
                    "available": getattr(exc, "available", 0),
                    "action": getattr(exc, "action", "ai_call"),
                    "message": str(exc),
                }
            },
        )

    # ---------- meta 路由 ----------

    @app.get("/", tags=["meta"])
    def root() -> dict:
        return {
            "name": "浑晶 HunJing Backend",
            "version": __version__,
            "docs": "/docs",
            "healthz": "/healthz",
        }

    def _health_payload() -> dict:
        """探活端点共用 payload — 给 UptimeRobot / Sentry release health 等用."""
        db_ok = db_healthcheck()
        return {
            "ok": db_ok,
            "status": "ok" if db_ok else "degraded",
            "db": "ok" if db_ok else "fail",
            "version": __version__,
            "smtp_configured": settings.smtp_configured(),
        }

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict:
        """兼容旧路径 — 仍可用,但 nginx 默认只转发 /api/*,生产推荐用 /api/health."""
        return _health_payload()

    @app.get("/api/health", tags=["meta"])
    def api_health() -> dict:
        """探活端点 — 上线后给 UptimeRobot / 类似服务监控用.

        无需鉴权(不暴露任何敏感信息).每分钟探活一次,连续 2 次 ok=false 触发告警.
        """
        return _health_payload()

    # ---------- 业务路由 ----------
    # Sprint 1.B
    from app.routers import auth, consent
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(consent.router, prefix="/api/consent", tags=["consent"])

    # Sprint 1.C
    from app.routers import characters, events, projects, relationships
    app.include_router(projects.router, prefix="/api/projects", tags=["projects"])
    # characters / relationships / events 因为路径混合(嵌套 + 顶层),用 /api 前缀
    app.include_router(characters.router, prefix="/api", tags=["characters"])
    app.include_router(relationships.router, prefix="/api", tags=["relationships"])
    app.include_router(events.router, prefix="/api", tags=["events"])

    # Sprint 1.D
    from app.routers import refine
    app.include_router(refine.router, prefix="/api", tags=["refine"])

    # Sprint 1.E.5(配额闸门)
    from app.routers import quota
    app.include_router(quota.router, prefix="/api/quota", tags=["quota"])

    # Sprint 1.G(续写引擎服务化)
    from app.routers import outlines, simulations
    app.include_router(simulations.router, prefix="/api", tags=["simulations"])
    # Sprint 6.A2 M6:outline-first 长篇生成路由
    app.include_router(outlines.router, prefix="", tags=["outlines"])

    # Sprint 1.R(自洽守护者 — 推演产物诊断)
    from app.routers import audits
    app.include_router(audits.router, prefix="/api", tags=["audits"])

    # SP-5(2026-05-28):伏笔账本前端面板 — plot_threads 后端齐但 0 endpoint,补齐
    from app.routers import plot_threads
    app.include_router(plot_threads.router, prefix="/api", tags=["plot_threads"])

    # SP-3(2026-05-28):知识边界前端面板 — character_knowledge service 齐但 0 endpoint
    from app.routers import story_facts
    app.include_router(story_facts.router, prefix="/api", tags=["story_facts"])

    # Sprint 6.A2 路线图 #6(2026-05-23):全局搜索
    from app.routers import search
    app.include_router(search.router, prefix="/api", tags=["search"])

    # Sprint 2.A(中间态文件上传 + 红旗指纹拦截)
    from app.routers import uploads
    app.include_router(uploads.router, prefix="/api", tags=["uploads"])

    # Sprint 2.B(自动图谱抽取 — 上传文件 → AI 抽人物/关系/事件 → 落项目)
    from app.routers import extract_jobs
    app.include_router(extract_jobs.router, prefix="/api", tags=["extract_jobs"])

    # Sprint 6.A2 M2(场景图谱 — project_scenes + character_affinity)
    from app.routers import scenes
    app.include_router(scenes.router, prefix="/api", tags=["scenes"])

    # Sprint 2.C(反事实变量 + 重塑度三维度)
    from app.routers import counterfactuals
    app.include_router(counterfactuals.router, prefix="/api", tags=["counterfactuals"])

    # Sprint 6.A2 CT(反事实组合树 — 2026-05-21)
    from app.routers import counterfactual_combinations
    app.include_router(
        counterfactual_combinations.router,
        prefix="/api",
        tags=["counterfactual_combinations"],
    )

    # Sprint 2.D(正典守护者 — 差异化王牌)
    from app.routers import canonical_audits
    app.include_router(canonical_audits.router, prefix="/api", tags=["canonical_audits"])

    # Sprint 2.E(去 IP 化导出 — doc 5 四层版权防护核心层)
    from app.routers import de_ip
    app.include_router(de_ip.router, prefix="/api", tags=["de_ip"])

    # Sprint E.4(订阅 / 价格快照 — 协议第三章"老用户老规则"承诺)
    from app.routers import billing
    app.include_router(billing.router, prefix="/api", tags=["billing"])

    # Sprint C.1 credit 重构(2026-05-13):加购包 / 交易明细
    from app.routers import credit
    app.include_router(credit.router, prefix="/api", tags=["credit"])

    # Sprint D.9 Sprint 2.A(漫画态 — comic_projects + agent #2/#3/#4/#5)
    from app.routers import comics
    app.include_router(comics.router, prefix="/api", tags=["comics"])

    # P3 作者指南针 Agent(2026-05-26):双轨制 LLM 调研 + 用户审阅锁定
    from app.routers import author_compass
    app.include_router(author_compass.router, prefix="/api", tags=["author_compass"])

    # BYOK 自携密钥(2026-06-04):用户输入激活码解锁,可配自己的 LLM API key
    from app.routers import byok
    app.include_router(byok.router, prefix="/api", tags=["byok"])

    # BYOK 个人收款码支付(2026-06-05):用户上传付款截图 + Vision LLM 自动审核
    from app.routers import byok_payment
    app.include_router(byok_payment.router, prefix="/api", tags=["byok_payment"])

    # 商业化重塑 第一期(2026-06-09):统一支付内核 — 订单中心 + SKU 目录 + 履约路由
    # 订阅 / BYOK / 配额包 全收敛到一套订单 + 审核 + 发货流程
    from app.routers import payments
    app.include_router(payments.router, prefix="/api", tags=["payments"])

    # BYOK admin 后台(2026-06-05):founder 邮箱可审批 manual_review 订单 + 看截图
    from app.routers import byok_admin
    app.include_router(byok_admin.router, prefix="/api", tags=["byok_admin"])

    # 作品广场(2026-06-25):用户把创作上架到社区 + 免费在线阅读 + 点赞 + 阅读量 + 智能排序
    from app.routers import plaza
    app.include_router(plaza.router, prefix="/api", tags=["plaza"])

    # 用户资料(2026-06-25):改昵称 + 改头像(配合广场作者展示)
    from app.routers import profile
    app.include_router(profile.router, prefix="/api", tags=["profile"])

    # BYOK 支付:收款码图片静态服务 + 用户上传截图存储目录
    from fastapi.staticfiles import StaticFiles
    payment_qrcodes_dir = settings.uploads_abs_dir.parent / "payment_qrcodes"
    payment_qrcodes_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/api/payment-qrcodes",
        StaticFiles(directory=str(payment_qrcodes_dir)),
        name="payment-qrcodes",
    )
    # 用户上传的付款截图(私有,不 mount 静态服务 — 仅后端 Vision LLM 读)
    payment_proofs_dir = settings.uploads_abs_dir.parent / "payment_proofs"
    payment_proofs_dir.mkdir(parents=True, exist_ok=True)

    # Sprint D.9 Sprint 2.B+ 六修(2026-05-12):漫画参考图本地上传静态服务
    #   POST /api/comics/{id}/upload_reference_file 落到 backend/data/comic_refs/{user_id}/{token}.{ext}
    #   GET  /api/comic-files/{user_id}/{filename}  公开读取(filename 24 字节随机 token,不可猜)
    # 不做 auth — 行业图床惯例:文件名本身就是 token,泄漏风险低于让 Qwen-VL 拉公网 URL
    from fastapi.staticfiles import StaticFiles

    comic_refs_dir = settings.uploads_abs_dir.parent / "comic_refs"
    comic_refs_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/api/comic-files",
        StaticFiles(directory=str(comic_refs_dir)),
        name="comic-files",
    )

    # Sprint D.9 Sprint 4.C(2026-05-13):漫画 Typesetter 整页 PNG 静态服务
    #   _agent_typesetter 输出落 backend/data/composed/<comic_id>/page_<N>.png
    #   GET /api/comic-composed/{comic_id}/page_<N>.png — 阅读器整页模式拉
    # 安全:comic_id 是 uuid hex(不可枚举),与上面 comic_refs 同模式;读不到他人 comic
    composed_dir = settings.uploads_abs_dir.parent / "composed"
    composed_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/api/comic-composed",
        StaticFiles(directory=str(composed_dir)),
        name="comic-composed",
    )

    # 作品广场(2026-06-25):用户上传的封面图 + 头像静态服务
    #   POST /api/plaza/cover  → backend/data/plaza_covers/{user_id}/{token}.{ext}
    #   POST /api/me/avatar    → backend/data/avatars/{user_id}/{token}.{ext}
    # 文件名 24 字节随机 token,不可枚举(同 comic_refs 图床惯例)
    plaza_covers_dir = settings.uploads_abs_dir.parent / "plaza_covers"
    plaza_covers_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/api/plaza-covers",
        StaticFiles(directory=str(plaza_covers_dir)),
        name="plaza-covers",
    )
    avatars_dir = settings.uploads_abs_dir.parent / "avatars"
    avatars_dir.mkdir(parents=True, exist_ok=True)
    app.mount(
        "/api/avatars",
        StaticFiles(directory=str(avatars_dir)),
        name="avatars",
    )

    # ============================================================
    # 剧创态(第 5 创作态)— 阶段 3 后端代码迁入
    # ============================================================
    # 所有剧创态 router 统一挂在 /api/screenplay/* 前缀下,与父平台路由解耦
    # 每个 endpoint 内部已加 Depends(get_current_user) JWT 鉴权
    # 失败兜底:剧创态 import 失败不阻塞父平台启动(log warning 即可)
    try:
        from app.screenplay.routers import (
            attributions as sp_attributions,
            character_profiles as sp_character_profiles,
            compare as sp_compare,
            compose as sp_compose,
            decisions as sp_decisions,
            elements as sp_elements,
            episodes as sp_episodes,
            export as sp_export,
            novels as sp_novels_router,
            optimize as sp_optimize,
            scenes as sp_scenes_router,
            story_bibles as sp_story_bibles,
        )
        app.include_router(sp_novels_router.router, prefix="/api/screenplay", tags=["screenplay"])
        app.include_router(sp_story_bibles.router, prefix="/api/screenplay", tags=["screenplay"])
        app.include_router(sp_scenes_router.router, prefix="/api/screenplay", tags=["screenplay"])
        app.include_router(sp_elements.router, prefix="/api/screenplay", tags=["screenplay"])
        app.include_router(sp_attributions.router, prefix="/api/screenplay", tags=["screenplay"])
        app.include_router(sp_decisions.router, prefix="/api/screenplay", tags=["screenplay"])
        app.include_router(sp_compose.router, prefix="/api/screenplay", tags=["screenplay"])
        app.include_router(sp_optimize.router, prefix="/api/screenplay", tags=["screenplay"])
        app.include_router(sp_export.router, prefix="/api/screenplay", tags=["screenplay"])
        # 阶段 8.2:角色页 + 关系图 + 桥接资产可视化
        app.include_router(sp_character_profiles.router, prefix="/api/screenplay", tags=["screenplay"])
        # 阶段 8.4:分集规划 MVP
        app.include_router(sp_episodes.router, prefix="/api/screenplay", tags=["screenplay"])
        # 阶段 8.5:多模型对比(基于 BYOK)
        app.include_router(sp_compare.router, prefix="/api/screenplay", tags=["screenplay"])
    except Exception:  # noqa: BLE001
        # 2026-06-25:原来只 warn → 剧创态路由一旦注册失败就全静默消失,前端
        # 「我的剧本」拿到 404 却无从查因(用户线上实测)。改 logging.exception
        # 打全栈,prod 日志能直接看到真因(多半是 prod-only 缺依赖 / DB 迁移状态),
        # 而不是降级成请求时 404。仍不阻塞父平台启动(漫画等其他态照常)。
        import logging
        logging.exception(
            "剧创态 router 注册失败(不阻塞父平台启动,但 /api/screenplay/* 将全部 404,"
            "请看上面全栈定位真因)"
        )

    return app


def _auto_apply_migrations() -> None:
    """Sprint 6.A2 FOCUS.10(2026-05-22):启动时自动跑未应用的 migration。

    背景:之前的流程需用户手动跑 `python scripts/init_db.py`,常被忘掉 →
    新 migration(如 056 entities_pending_review state)未应用 → 抽取走到新 state 时
    SQLite CHECK 约束抛 IntegrityError → 用户看到"抽取失败"莫名其妙。
    解决:每次启动调用 init_db 的 migration loop,DDL 已 IF NOT EXISTS,幂等可重跑。

    失败时只 log warning 不阻塞启动 — 用户可能有手动管理 DB 的特殊场景。
    """
    import logging
    import sqlite3 as _sqlite3
    from pathlib import Path
    from app.db import get_connection, transaction

    try:
        migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
        if not migrations_dir.exists():
            return
        sql_files = sorted(migrations_dir.glob("*.sql"))
        conn = get_connection()
        try:
            for sql_file in sql_files:
                sql = sql_file.read_text(encoding="utf-8")
                try:
                    with transaction(conn) as tx:
                        tx.executescript(sql)
                except _sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if "duplicate column name" in msg:
                        # SQLite ALTER ADD COLUMN 非幂等,正常跳过
                        continue
                    if "no such column" in msg:
                        # 2026-06-02:SQLite ALTER DROP COLUMN 不幂等
                        # 列已不存在(老 DB 从未创建过 / 之前已 DROP 过)→ 正常跳过
                        continue
                    logging.warning(
                        "auto-migration %s 失败(可能已应用):%s",
                        sql_file.name, e,
                    )
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logging.warning("auto-migration 启动钩子失败: %s", e)


_auto_apply_migrations()

# 阶段 4(2026-06-08):剧创态 schema 已规整进 migration 085,
# 由 _auto_apply_migrations 统一接管 — 不再需要 _init_screenplay_schema 钩子。


def _fix_sp_novels_user_id_type() -> None:
    """阶段 4.5(2026-06-08)— 修阶段 3/4 留下的 sp_novels.user_id 类型 bug。

    背景:阶段 3 在 schema.sql 里把 sp_novels.user_id 写成 INTEGER,
    但父平台 users.id 是 TEXT(UUID 字符串)。阶段 5 huimeng_bridge 要 JOIN
    父平台 characters / projects 时,INTEGER vs TEXT 比较走 SQLite 类型亲和
    隐式转换 — 大部分场景能用,但 JOIN 行为不可预测,且 FK 约束在严格模式下报错。

    幂等修复:检测 sp_novels.user_id 列类型;
      - 若 INTEGER 或 历史 RENAME trick 残留(sp_novels_old_int / sp_chapters FK 悬挂)
        → DROP 整套 sp_ 表 + 让 migration runner 重建(sp_ 家族无产品数据,安全)
      - 若 TEXT 且 无残留 → noop

    为什么走 Python 而不是 migration 文件:
      迁徙 runner 每次启动跑所有 .sql,没有"已应用"标记。SQL 没法条件执行
      "如果列类型是 INTEGER 才动",而 ALTER TABLE 在 SQLite 无 ALTER COLUMN TYPE 操作。
      所以这种"补丁式条件迁徙"走 Python 一次性脚本是最干净的。

    2026-06-08 用户上传 500 修复:
      原版用 ALTER RENAME → CREATE → INSERT → DROP _old 的 trick。
      但 SQLite ALTER RENAME 会**自动重写所有子表 FK** 指向 _old 名;后续
      DROP _old 让子表 FK 悬挂,INSERT 子表时 SQLite 验 FK 报
      "no such table: sp_novels_old_int" → 500。
      新版改用整套 DROP + 重建,避开悬挂 FK 问题。
    """
    import logging
    from pathlib import Path
    from app.db import get_connection, transaction

    try:
        conn = get_connection()
        try:
            # 检测 1:sp_novels 存在么?
            info = conn.execute("PRAGMA table_info(sp_novels)").fetchall()
            if not info:
                return  # 表还没建,等 migration 085 跑

            # 检测 2:user_id 是 INTEGER 还是 TEXT?
            user_id_col = next((c for c in info if c[1] == "user_id"), None)
            if user_id_col is None:
                logging.warning("sp_novels 缺 user_id 列,跳过类型修复")
                return
            col_type = (user_id_col[2] or "").upper()
            is_integer = "INT" in col_type

            # 检测 3:历史 rename trick 残留 sp_novels_old_int?
            old_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='sp_novels_old_int'"
            ).fetchone()

            # 检测 4:sp_chapters 的 FK 是不是指向 sp_novels_old_int?
            chapters_fk_broken = False
            try:
                fk_info = conn.execute("PRAGMA foreign_key_list(sp_chapters)").fetchall()
                chapters_fk_broken = any(
                    row[2] == "sp_novels_old_int" for row in fk_info
                )
            except Exception:  # noqa: BLE001
                pass

            if not (is_integer or old_table or chapters_fk_broken):
                return  # 健康 — noop

            logging.warning(
                "阶段 4.5 修复触发:user_id=%s / 残留_old=%s / 子表 FK 悬挂=%s — "
                "DROP 整套 sp_ 表 + 重建(sp_ 家族无产品数据,安全)",
                col_type or "(未声明)", bool(old_table), chapters_fk_broken,
            )

            # PRAGMA foreign_keys 必须在 transaction 外设置
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                # 按 FK 依赖反向 DROP,即使 PRAGMA OFF 也保险些
                tables_in_order = [
                    "sp_screenplays",
                    "sp_bible_events",
                    "sp_bible_relationships",
                    "sp_bible_locations",
                    "sp_bible_characters",
                    "sp_story_bibles",
                    "sp_paragraphs",
                    "sp_chapters",
                    "sp_novels",
                    "sp_novels_old_int",
                ]
                with transaction(conn) as tx:
                    for tbl in tables_in_order:
                        tx.execute(f"DROP TABLE IF EXISTS {tbl}")
                # 重新跑 migration 085 + 086 内容(SQL 文件本身就是 IF NOT EXISTS)
                migrations_dir = Path(__file__).resolve().parent.parent / "migrations"
                for mig_name in (
                    "085_screenplay_sp_tables.sql",
                    "086_sp_novels_linked_project.sql",
                ):
                    sql_path = migrations_dir / mig_name
                    if not sql_path.exists():
                        logging.warning("阶段 4.5 缺 migration: %s", mig_name)
                        continue
                    sql = sql_path.read_text(encoding="utf-8")
                    with transaction(conn) as tx:
                        tx.executescript(sql)
            finally:
                conn.execute("PRAGMA foreign_keys = ON")

            logging.warning("阶段 4.5 修复完成:sp_ 表家族已重建,user_id=TEXT")
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logging.warning("sp_novels user_id 类型修复失败(不阻塞启动): %s", e)


_fix_sp_novels_user_id_type()

app = create_app()
