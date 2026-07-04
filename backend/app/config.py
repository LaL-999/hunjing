"""读项目根 .env + 提供 settings 单例。

对齐 simulate.py 的 load_env() 风格 — 与命令行脚本共享**同一个项目根 .env**。
不引入 pydantic-settings 以保持依赖最小化(对应 ADR 决议)。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# 项目根 = backend/app/config.py 往上 3 层
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ENV_PATH = PROJECT_ROOT / ".env"


def _load_env_file() -> None:
    """从项目根 .env 加载到 os.environ(setdefault,不覆盖系统已有变量)。"""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# 模块导入即加载(确保 settings 实例化时变量已就绪)
_load_env_file()


def _require(key: str) -> str:
    val = os.getenv(key)
    if not val:
        raise RuntimeError(
            f"必填环境变量缺失:{key}(检查项目根 .env;参考 backend/.env.example)"
        )
    return val


def _get_int(key: str, default: int) -> int:
    val = os.getenv(key)
    if not val:
        return default
    try:
        return int(val)
    except ValueError:
        raise RuntimeError(f"环境变量 {key} 必须是整数,实际:{val!r}")


@dataclass(frozen=True)
class Settings:
    # === 数据库 ===
    db_path: str

    # === 文件上传(Sprint 2.A) ===
    uploads_dir: str          # 项目根相对路径;默认 backend/data/uploads
    upload_max_bytes: int     # 默认 100 MB

    # === JWT ===
    jwt_secret: str
    jwt_ttl_seconds: int

    # === SMTP(OTP 邮箱通道,Sprint 1.B 启用) ===
    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_pass: str
    smtp_from: str

    # === 服务器 ===
    api_host: str
    api_port: int
    cors_origins: list[str]

    # === LLM(refine 服务用,沿用 simulate.py 风格;Sprint 1.D 启用) ===
    # Sprint D.8:这三个保留作为 DeepSeek 主路由的 OpenAI 兼容端点参数
    #   (DeepSeekTextAdapter 内部读取);新代码不要再直接读 llm_api_key,
    #   应该走 llm_routing.router.get_text_llm()。
    llm_api_key: str
    llm_api_base: str
    llm_model: str

    # === Sprint D.8 — 多 vendor 路由层选择 ===
    # 详见 docs/ADR_D.8_国产API路由层.md
    # 路由层根据这三个字段决定 get_text_llm / get_image_gen / get_vision_llm
    # 返回哪个 adapter。改这三个字段 → 重启后端生效。
    text_vendor: str          # deepseek(默认)/ qwen / moonshot — Sprint D.8 主路由
    image_vendor: str         # jimeng(默认)/ wanx / kolors — Sprint D.8 主路由
    vision_vendor: str        # qwen_vl(默认)/ glm_4v — Sprint D.8 主路由

    # === Sprint D.8 — 各 vendor 凭据(只填要用的,默认空字符串)===
    # 阿里灵积(DashScope)统一 key — Qwen-VL / Qwen-Max / 其他 Qwen 系列共用
    qwen_api_key: str
    # 阿里灵积视觉模型 ID — 用户在控制台核对精确字符串(常见 qwen-vl-max-latest /
    # qwen3-vl-plus 等);新模型出来时改这里就够,adapter 不动
    qwen_vl_model: str
    # 字节火山方舟(Volcano Ark)API_KEY — Doubao Seedream 等图像 / chat 模型走
    # OpenAI 兼容协议,统一走 sk-xxx 形式 API_KEY(不是 IAM AK/SK 对)
    jimeng_api_key: str
    # 火山方舟图像模型 ID(默认 Doubao Seedream 5.0,2026-01 发布)
    jimeng_model: str
    # 火山方舟 OpenAI 兼容端点(默认北京区,跨区可改 cn-shanghai 等)
    jimeng_api_base: str

    # === 创始人白名单(plan 运行时升 founder,绕过所有配额) ===
    # 不写库,只在 deps.get_current_user 内存改写。
    # 安全模型:邮箱凭 OTP 验证才能登录,别人即便知道这个邮箱也登不上。
    # 改这个列表 → 重启后端生效。
    founder_emails: frozenset[str]

    # === BYOK 支付(2026-06-05)— 个人收款码 + Vision LLM 自动审核 ===
    # PAYEE_NAME:收款账户的**真实姓名**,仅用于 Vision LLM 比对截图里的"收款方"
    # (防伪);**不展示给用户**。换收款账户时改这个为新账户真实姓名 → 重启后端生效。
    byok_payee_name: str
    # PAYEE_DISPLAY_NAME(2026-07-03):**展示给用户**的收款方名称(支付指引 / 弹窗里
    # "向【X】转账")。与 PAYEE_NAME 解耦:对外只露品牌名(如 Ever),真实姓名不外泄。
    byok_payee_display_name: str
    # 二维码图片相对 backend/data/payment_qrcodes/ 的文件名;
    # 在 backend/data/payment_qrcodes/ 下放对应文件即生效。
    # 不存在文件时前端会显示占位 + 提示去 setup。
    byok_wechat_qr_filename: str
    byok_alipay_qr_filename: str
    # 截图上传通知邮箱 — manual_review 状态时收件人
    byok_review_notify_email: str

    # === 洞察后台 admin token(2026-06-05)===
    # 与 insights-backend INSIGHTS_ADMIN_TOKEN 共用同一个 env,
    # 用于允许洞察后台 frontend 跨域调主平台 admin endpoint(BYOK 审核等)。
    # 生产环境必须改成强随机字符串;dev fallback 与 insights-backend 默认值一致。
    insights_admin_token: str

    @property
    def project_root(self) -> Path:
        return PROJECT_ROOT

    @property
    def db_abs_path(self) -> Path:
        """db_path 是项目根的相对路径,这里转绝对。"""
        p = Path(self.db_path)
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    @property
    def uploads_abs_dir(self) -> Path:
        """uploads_dir 同 db_path,允许相对项目根。运行时如不存在自动建。"""
        p = Path(self.uploads_dir)
        return p if p.is_absolute() else (PROJECT_ROOT / p)

    def smtp_configured(self) -> bool:
        """Sprint 1.B 发送 OTP 前用此判断 SMTP 凭据是否填好。"""
        return bool(self.smtp_user and self.smtp_pass and self.smtp_from)


def load_settings() -> Settings:
    return Settings(
        db_path=os.getenv("HUIMENG_DB_PATH", "backend/data/huimeng.db"),
        uploads_dir=os.getenv("HUIMENG_UPLOADS_DIR", "backend/data/uploads"),
        upload_max_bytes=_get_int(
            "HUIMENG_UPLOAD_MAX_BYTES", 100 * 1024 * 1024  # 100 MB
        ),
        jwt_secret=_require("HUIMENG_JWT_SECRET"),
        jwt_ttl_seconds=_get_int("HUIMENG_JWT_TTL_SECONDS", 604800),
        smtp_host=os.getenv("HUIMENG_SMTP_HOST", "smtp.qq.com"),
        smtp_port=_get_int("HUIMENG_SMTP_PORT", 465),
        smtp_user=os.getenv("HUIMENG_SMTP_USER", ""),
        smtp_pass=os.getenv("HUIMENG_SMTP_PASS", ""),
        smtp_from=os.getenv("HUIMENG_SMTP_FROM", ""),
        api_host=os.getenv("HUIMENG_API_HOST", "0.0.0.0"),
        api_port=_get_int("HUIMENG_API_PORT", 8000),
        cors_origins=[
            o.strip()
            for o in os.getenv(
                # 2026-06-05:洞察后台 frontend 调主平台 admin endpoint 用
                # 5173 主平台 / 5174 洞察后台默认 / 5175-5176 fallback(端口被占时 vite 自动跳)
                # 2026-06-09:加生产域名 shuangdayeye.cn(门户 + app 子域),
                #   门户跨域拉定价 / app 调 api 都要放行;env 可整体覆盖。
                #   桌面端 tauri:// origin 走 main.py 的 allow_origin_regex,不在这里。
                "HUIMENG_CORS_ORIGINS",
                "http://localhost:5173,http://localhost:5174,http://localhost:5175,http://localhost:5176"
                ",https://shuangdayeye.cn,https://www.shuangdayeye.cn,https://app.shuangdayeye.cn",
            ).split(",")
            if o.strip()
        ],
        llm_api_key=os.getenv("OPENAI_API_KEY", ""),
        llm_api_base=os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1"),
        llm_model=os.getenv("LLM_MODEL", "deepseek-chat"),
        # Sprint D.8 — 多 vendor 路由层
        text_vendor=os.getenv("HUIMENG_TEXT_VENDOR", "deepseek"),
        image_vendor=os.getenv("HUIMENG_IMAGE_VENDOR", "jimeng"),
        vision_vendor=os.getenv("HUIMENG_VISION_VENDOR", "qwen_vl"),
        qwen_api_key=os.getenv("HUIMENG_QWEN_API_KEY", ""),
        qwen_vl_model=os.getenv("HUIMENG_QWEN_VL_MODEL", "qwen-vl-max-latest"),
        jimeng_api_key=os.getenv("HUIMENG_JIMENG_API_KEY", ""),
        jimeng_model=os.getenv("HUIMENG_JIMENG_MODEL", "doubao-seedream-5-0-260128"),
        jimeng_api_base=os.getenv(
            "HUIMENG_JIMENG_API_BASE",
            "https://ark.cn-beijing.volces.com/api/v3",
        ),
        # 创始人邮箱:env HUIMENG_FOUNDER_EMAILS 逗号分隔,默认硬编码创始人本人
        founder_emails=frozenset(
            e.strip().lower()
            for e in os.getenv(
                "HUIMENG_FOUNDER_EMAILS",
                "javaspringcjiajia@foxmail.com",
            ).split(",")
            if e.strip()
        ),
        # BYOK 支付配置(2026-06-05)
        byok_payee_name=os.getenv("HUIMENG_BYOK_PAYEE_NAME", "李爽"),
        byok_payee_display_name=os.getenv("HUIMENG_BYOK_PAYEE_DISPLAY_NAME", "Ever"),
        byok_wechat_qr_filename=os.getenv(
            "HUIMENG_BYOK_WECHAT_QR_FILENAME", "wechat_qr.png",
        ),
        byok_alipay_qr_filename=os.getenv(
            "HUIMENG_BYOK_ALIPAY_QR_FILENAME", "alipay_qr.png",
        ),
        byok_review_notify_email=os.getenv(
            "HUIMENG_BYOK_REVIEW_NOTIFY_EMAIL",
            "javaspringcjiajia@foxmail.com",
        ),
        # 与 insights-backend INSIGHTS_ADMIN_TOKEN 同 env(两 service 共用一把钥匙)
        insights_admin_token=os.getenv(
            "INSIGHTS_ADMIN_TOKEN", "huimeng-insights-dev-token",
        ),
    )


# 模块单例(import 即加载;测试可以 monkey patch settings)
settings: Settings = load_settings()
