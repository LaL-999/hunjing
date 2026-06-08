"""Comic API schema — Sprint D.9 Sprint 2.A 漫画态路由。

routers/comics.py 对应的请求 / 响应 Pydantic 校验。
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator


# ============================================================
# 输入源 schema(POST /comics 时用户提交)
# ============================================================

class ComicSourceRequest(BaseModel):
    """漫画输入源 —
    - internal:从「我的剧情线」选 simulation_ids
    - external:从已上传文件选 upload_ids
    """
    type: Literal["internal", "external"]
    simulation_ids: Optional[list[str]] = None
    upload_ids: Optional[list[str]] = None


# ============================================================
# 漫画创建 / 更新 / 删除
# ============================================================

class CreateComicRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=30)
    source: ComicSourceRequest
    target_pages: int = Field(
        default=12, ge=6, le=18,
        description=(
            "目标页数(6-18)。Sprint 3 Phase 2(2026-05-13)接通 _agent_scripter "
            "真分批承接(每批 6 页 + tail_context),彻底解除 DeepSeek V3 ~8K output "
            "token 单次输出硬限,从 Sprint C.4 临时回退的 12 重新放开到 18。"
        ),
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name 不能为空白")
        return v


# ============================================================
# Sprint C.4:AI Planner 创建前预览
# ============================================================

class PlanPreviewRequest(BaseModel):
    """POST /api/comics/plan_preview — 用户选输入源后调,**不创建漫画**,只跑 planner 给推荐。

    用户拿到推荐后可调滑块再正式创建。
    """
    source: ComicSourceRequest
    user_preference: Literal["auto", "short", "long"] = Field(
        default="auto",
        description="用户偏好:auto / short(倾向短篇)/ long(倾向长篇)",
    )


class PlanPreviewResponse(BaseModel):
    """AI Planner 输出 + 客户端可信预估。"""
    recommended_total_pages: int    # 6-18
    panels_per_page: int            # 通常 6
    estimated_credits: int          # ≥ 100
    reasoning: str                  # 给用户看的一句话
    long_text_warning: bool         # 源 > 14400 字时为 true
    # 元数据(给前端滑块限制 + 显示用)
    source_char_count: int
    character_count: int


# ============================================================
# 用户上传 3 张参考图(触发画风定调员 v2)
# ============================================================

class UploadReferenceImagesRequest(BaseModel):
    """用户上传参考图后 POST 此端点触发 _agent_style_director_v2。

    Sprint 5.4.1(2026-05-13):接受 0 张或 3 张:
      - 3 张:有图模式(原 v2)—— Qwen-VL × 3 提 DNA + DeepSeek 综合
      - 0 张:跳过模式(用户没现成参考图)—— 跳过 Qwen-VL,DeepSeek 从原文题材推画风
      - 其他数量:422(防 1/2 张半残状态)
    """
    image_urls: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("image_urls")
    @classmethod
    def _validate_count(cls, v: list[str]) -> list[str]:
        if len(v) not in (0, 3):
            raise ValueError(
                "image_urls 必须是 0 张(跳过模式,AI 从文本推画风)"
                "或 3 张(有图模式)"
            )
        return v


class UploadReferenceFileResponse(BaseModel):
    """Sprint 2.B+ 六修:漫画参考图本地上传响应。

    前端逐张上传图片 → 后端落 backend/data/comic_refs/{user_id}/{token}.{ext}
    → 返回此 URL(浏览器可 <img src> 渲染 + Qwen-VL 可拉)。
    用户上完 3 张后,用 image_urls 列表调 upload_references 触发画风定调员。
    """
    url: str         # /api/comic-files/{user_id}/{filename}
    filename: str    # 仅 filename 部分(token.ext),用户删图时定位用


# ============================================================
# 用户 5 选 1(投票选定画风样张)
# ============================================================

class VoteStyleRequest(BaseModel):
    """用户从 3 张定调候选中选 1 张(Sprint 2.B+ 七修:5 → 3)。

    候选 index 1-3(对齐 Agent #3 v2 输出的 candidates 索引)。
    """
    selected_index: int = Field(..., ge=1, le=3)


# ============================================================
# 响应 schema(POST 返回 + GET 返回)
# ============================================================

class ComicCandidateImage(BaseModel):
    index: int
    variant_hint: str
    image_url: Optional[str] = None
    error: Optional[str] = None


class ComicResponse(BaseModel):
    """漫画态项目完整状态(对齐 Comic.to_response)。"""
    id: str
    user_id: str
    name: str
    source: dict
    # 画风定调员 v2 产物
    style_tag: Optional[str] = None
    style_anchor_image_url: Optional[str] = None
    style_candidates: list[ComicCandidateImage] = []
    style_reference_image_urls: list[str] = []
    style_visual_dna: Optional[list[dict[str, Any]]] = None
    style_detailed_prompt: Optional[str] = None
    # 编剧产物
    script: Optional[dict] = None
    # 一致性 L4
    generation_seed: Optional[int] = None
    # 状态机
    state: str
    progress_percent: int
    # 元数据
    cost_yuan: float
    error_message: Optional[str] = None
    created_at: str
    updated_at: str
    completed_at: Optional[str] = None
    # zombie 检测派生字段(对齐 canonical_audit / extract_job)
    is_alive: bool = False
