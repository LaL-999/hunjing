"""剧创态配置 — 复用父平台 settings,只保留剧创态特有参数。

设计:
  - 父平台 settings(app.config.settings)管 DB 路径、CORS、host/port、JWT 等
  - 这里仅暴露剧创态需要的"路径辅助"(prompts / schemas 目录)
  - LLM 调用阶段 5 接通父平台 llm_routing,届时再从父平台 settings 读 key
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings as platform_settings


_THIS_DIR = Path(__file__).resolve().parent     # backend/app/screenplay/
_PROMPTS_DIR = _THIS_DIR / "prompts"
_SCHEMAS_DIR = _THIS_DIR / "schemas"


@dataclass(frozen=True)
class ScreenplaySettings:
    """剧创态特有配置(只读)。"""

    prompts_dir: Path = field(default_factory=lambda: _PROMPTS_DIR)
    schemas_dir: Path = field(default_factory=lambda: _SCHEMAS_DIR)

    # 上传:复用父平台 uploads
    upload_dir: Path = field(
        default_factory=lambda: platform_settings.upload_dir
        if hasattr(platform_settings, "upload_dir")
        else Path(__file__).resolve().parents[2] / "data" / "uploads",
    )
    max_upload_size_mb: int = 20

    # LLM:阶段 5 全接通父平台 llm_routing 之前先读 .env 中 DEEPSEEK_* 兼容字段
    # 父平台 platform_settings.deepseek_api_key 或环境变量
    @property
    def deepseek_api_key(self) -> str:
        return getattr(platform_settings, "deepseek_api_key", "") or ""

    @property
    def deepseek_api_base(self) -> str:
        return getattr(platform_settings, "deepseek_api_base", "https://api.deepseek.com/v1")

    @property
    def deepseek_model(self) -> str:
        return getattr(platform_settings, "deepseek_model", "deepseek-chat")

    @property
    def llm_timeout_seconds(self) -> float:
        return getattr(platform_settings, "llm_timeout_seconds", 60.0)

    @property
    def llm_max_retries(self) -> int:
        return getattr(platform_settings, "llm_max_retries", 2)

    @property
    def llm_default_temperature(self) -> float:
        return getattr(platform_settings, "llm_default_temperature", 0.6)

    # === 父平台 settings 透传 ===
    @property
    def database_path(self) -> Path:
        # 父平台 settings.db_abs_path 是 huimeng.db 完整路径
        # 剧创态 sp_* 表与父平台原表共存于同一个 SQLite 文件
        return platform_settings.db_abs_path


settings = ScreenplaySettings()

# 确保 prompts / schemas 目录存在(已存在;若被误删 init 时报清晰错)
if not settings.prompts_dir.exists():
    raise FileNotFoundError(f"剧创态 prompts 目录缺失: {settings.prompts_dir}")
if not settings.schemas_dir.exists():
    raise FileNotFoundError(f"剧创态 schemas 目录缺失: {settings.schemas_dir}")
