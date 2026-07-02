"""作品广场路由 — 2026-06-25。

用户把自己创作的作品上架到社区广场,免费在线阅读 + 点赞 + 阅读量 + 智能排序。

端点:
  GET    /api/plaza/works                 列出广场公开作品(sort=hot|new|classic)
  GET    /api/plaza/my-works              我发布的作品(含已下架)
  GET    /api/plaza/publishable           我可上架的素材(已完成 simulation,未上架)
  POST   /api/plaza/cover                 上传封面图 → 返回内部 URL
  POST   /api/plaza/publish               发布一个作品到广场(快照正文)
  GET    /api/plaza/works/{work_id}       在线阅读(返回正文 + 元信息,阅读量 +1)
  POST   /api/plaza/works/{work_id}/like  点赞 / 取消点赞(幂等)
  DELETE /api/plaza/works/{work_id}       作者下架自己的作品

设计:
  - 封面图本地落盘 backend/data/plaza_covers/{user_id}/{token}.{ext},mount /api/plaza-covers 静态服务
  - 文件名 24 字节随机 token,不可枚举(对齐 comic_refs 图床惯例)
  - 正文权威来自源 simulation.narrative,杜绝前端伪造(见 plaza_service)
"""
from __future__ import annotations

import secrets
import sqlite3
import traceback
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field

from app.config import settings
from app.deps import get_current_user, get_db
from app.models.user import User
from app.services import plaza_service
from app.services.plaza_service import (
    PlazaError,
    PublishComicInput,
    PublishInput,
    PublishScreenplayInput,
)
from app.services.project_service import ResourceNotFoundOrForbidden

router = APIRouter()

# 封面图与漫画参考图同款约束(复用行业图床惯例)
_ALLOWED_IMAGE_MIMES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/gif", "image/bmp",
}
_ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
_COVER_MAX_BYTES = 5 * 1024 * 1024   # 5 MB


def _map_plaza_error(exc: PlazaError) -> HTTPException:
    """业务异常 code → HTTP 状态码。"""
    not_found = {"SOURCE_NOT_FOUND"}
    code = exc.code
    http = status.HTTP_404_NOT_FOUND if code in not_found else status.HTTP_400_BAD_REQUEST
    return HTTPException(http, detail={"code": code, "message": exc.message})


async def _save_image(
    file: UploadFile, user_id: str, subdir: str, url_prefix: str,
) -> str:
    """通用图片落盘 → 返回内部 URL 路径 /{url_prefix}/{user_id}/{filename}。

    subdir      落盘目录名(plaza_covers / avatars),位于 backend/data/ 下。
    url_prefix  对外 URL 前缀(/api/plaza-covers / /api/avatars)。
    """
    declared_mime = (file.content_type or "").lower()
    filename_orig = file.filename or ""
    ext = ""
    if "." in filename_orig:
        ext = "." + filename_orig.rsplit(".", 1)[-1].lower()

    if declared_mime not in _ALLOWED_IMAGE_MIMES and ext not in _ALLOWED_IMAGE_EXTS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "IMAGE_BAD_TYPE", "message": "图片必须是 JPG / PNG / WebP / GIF / BMP"},
        )
    if not ext:
        ext = {
            "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
            "image/webp": ".webp", "image/gif": ".gif", "image/bmp": ".bmp",
        }.get(declared_mime, ".jpg")

    raw = await file.read()
    if len(raw) > _COVER_MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "IMAGE_TOO_LARGE",
                "message": f"图片最大 {_COVER_MAX_BYTES // 1024 // 1024} MB,当前 {len(raw) // 1024 // 1024} MB",
            },
        )
    if len(raw) == 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "IMAGE_EMPTY", "message": "图片文件为空"},
        )

    token = secrets.token_urlsafe(24)
    filename = f"{token}{ext}"
    target_dir = settings.uploads_abs_dir.parent / subdir / user_id
    target_dir.mkdir(parents=True, exist_ok=True)
    file_path = target_dir / filename
    try:
        file_path.write_bytes(raw)
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "IMAGE_SAVE_FAILED", "message": str(e)[:200]},
        )
    return f"{url_prefix}/{user_id}/{filename}"


# ---------- 请求体 ----------

class PublishBody(BaseModel):
    sim_id: str = Field(..., description="要上架的 simulation id")
    title: str = Field(..., description="作品名(1-60 字)")
    summary: Optional[str] = Field(None, description="简介,空则自动截取正文前 80 字")
    cover_image_path: Optional[str] = Field(None, description="封面图内部 URL(先调 /plaza/cover 上传)")
    cover_gradient: int = Field(1, ge=1, le=9, description="无封面图时用的默认渐变编号 1-9")
    is_public: int = Field(1, ge=0, le=1, description="1 公开(上广场)/ 0 私人(仅作者)")
    allow_download: int = Field(1, ge=0, le=1, description="公开时是否允许他人下载正文")


class PublishScreenplayBody(BaseModel):
    novel_id: str = Field(..., description="剧创态 novel id")
    kind: str = Field("global", pattern="^(global|episodes|both)$",
                      description="global=全局剧本 / episodes=分集方案 / both=两者")
    plan_id: Optional[str] = Field(None, description="kind=episodes/both 时指定的分集方案 id")
    title: str = Field(..., description="作品名(1-60 字)")
    summary: Optional[str] = Field(None, description="简介,空则自动截取正文前 80 字")
    cover_image_path: Optional[str] = Field(None, description="封面图内部 URL")
    cover_gradient: int = Field(1, ge=1, le=9, description="无封面图时用的默认渐变 1-9")
    is_public: int = Field(1, ge=0, le=1)
    allow_download: int = Field(1, ge=0, le=1)


class PublishComicBody(BaseModel):
    comic_id: str = Field(..., description="要上架的漫画 comic id(需 state=done + 已排版)")
    title: str = Field(..., description="作品名(1-60 字)")
    summary: Optional[str] = Field(None, description="简介,空则自动生成")
    cover_image_path: Optional[str] = Field(None, description="封面图内部 URL,空则用第一页")
    cover_gradient: int = Field(1, ge=1, le=9)
    is_public: int = Field(1, ge=0, le=1)
    allow_download: int = Field(1, ge=0, le=1)


class VisibilityBody(BaseModel):
    is_public: Optional[int] = Field(None, ge=0, le=1)
    allow_download: Optional[int] = Field(None, ge=0, le=1)


class LikeBody(BaseModel):
    liked: bool = Field(..., description="true=点赞 false=取消")


# ---------- 列表 / 素材 ----------

@router.get("/plaza/works")
def api_list_works(
    sort: str = Query("hot", pattern="^(hot|new|classic)$"),
    limit: int = Query(24, ge=1, le=60),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """广场作品列表。带当前用户的 liked 标记。"""
    return plaza_service.list_works(conn, user.id, sort=sort, limit=limit, offset=offset)


@router.get("/plaza/my-works")
def api_list_my_works(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """我发布过的作品(含已下架)。"""
    return {"items": plaza_service.list_my_works(conn, user.id)}


@router.get("/plaza/publishable")
def api_list_publishable(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """我可上架的素材(已完成且有正文、尚未上架的 simulation)。"""
    return {"items": plaza_service.list_publishable_sims(conn, user.id)}


@router.get("/plaza/publishable-screenplays")
def api_list_publishable_screenplays(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """我可上架的剧创态素材(有全局剧本或分集方案的 novel)。item8。"""
    return {"items": plaza_service.list_publishable_screenplays(conn, user.id)}


# ---------- 封面上传 ----------

@router.post("/plaza/cover")
async def api_upload_cover(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
) -> dict:
    """上传封面图 → 返回内部 URL(发布时填入 cover_image_path)。"""
    url = await _save_image(file, user.id, "plaza_covers", "/api/plaza-covers")
    return {"cover_image_path": url}


# ---------- 发布 / 阅读 / 点赞 / 下架 ----------

@router.post("/plaza/publish", status_code=status.HTTP_201_CREATED)
def api_publish(
    body: PublishBody,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """把一个 simulation 作品上架到广场(快照正文)。"""
    try:
        work = plaza_service.publish_work(
            conn,
            user.id,
            PublishInput(
                sim_id=body.sim_id,
                title=body.title,
                summary=body.summary,
                cover_image_path=body.cover_image_path,
                cover_gradient=body.cover_gradient,
                is_public=body.is_public,
                allow_download=body.allow_download,
            ),
        )
    except PlazaError as exc:
        raise _map_plaza_error(exc)
    conn.commit()
    return work


@router.post("/plaza/publish-screenplay", status_code=status.HTTP_201_CREATED)
def api_publish_screenplay(
    body: PublishScreenplayBody,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """把剧创态作品(全局 / 分集 / 两者)上架到广场(item8,快照正文)。"""
    try:
        work = plaza_service.publish_screenplay(
            conn,
            user.id,
            PublishScreenplayInput(
                novel_id=body.novel_id,
                kind=body.kind,
                plan_id=body.plan_id,
                title=body.title,
                summary=body.summary,
                cover_image_path=body.cover_image_path,
                cover_gradient=body.cover_gradient,
                is_public=body.is_public,
                allow_download=body.allow_download,
            ),
        )
    except PlazaError as exc:
        raise _map_plaza_error(exc)
    conn.commit()
    return work


@router.get("/plaza/publishable-comics")
def api_list_publishable_comics(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """我可上架的漫画(state=done 且有已排版整页)。"""
    return {"items": plaza_service.list_publishable_comics(conn, user.id)}


@router.post("/plaza/publish-comic", status_code=status.HTTP_201_CREATED)
def api_publish_comic(
    body: PublishComicBody,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """把已完成的漫画上架到广场(快照整页 PNG 稳定 URL)。"""
    try:
        work = plaza_service.publish_comic(
            conn,
            user.id,
            PublishComicInput(
                comic_id=body.comic_id,
                title=body.title,
                summary=body.summary,
                cover_image_path=body.cover_image_path,
                cover_gradient=body.cover_gradient,
                is_public=body.is_public,
                allow_download=body.allow_download,
            ),
        )
    except PlazaError as exc:
        raise _map_plaza_error(exc)
    conn.commit()
    return work


@router.get("/plaza/works/{work_id}")
def api_work_detail(
    work_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """作品详情落地页:元信息 + 预览 + 权限(**不 +阅读量、不返全文**)。"""
    try:
        return plaza_service.get_work_detail(conn, work_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="作品不存在或已下架")


@router.get("/plaza/works/{work_id}/content")
def api_read_work(
    work_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """在线阅读:返回正文 + 元信息,阅读量 +1。"""
    try:
        work = plaza_service.read_work(conn, work_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="作品不存在或已下架")
    conn.commit()
    return work


@router.get("/plaza/works/{work_id}/download")
def api_download_work(
    work_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """下载作品正文(Markdown)。权限:作者本人,或 公开+允许下载。漫画不支持文本下载。"""
    from fastapi.responses import Response as _Response
    from urllib.parse import quote

    try:
        data = plaza_service.get_work_for_download(conn, work_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="作品不存在")
    except PlazaError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail={"code": exc.code, "message": exc.message})
    if data["source_type"] == "comic":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "COMIC_NO_TEXT_DOWNLOAD", "message": "漫画作品请用在线阅读浏览整页"},
        )
    title = (data["title"] or "作品").strip()
    md = f"# {title}\n\n{data['content'] or ''}\n"
    fname = quote(f"{title}.md")
    return _Response(
        content=md.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"},
    )


@router.patch("/plaza/works/{work_id}/visibility")
def api_set_visibility(
    work_id: str,
    body: VisibilityBody,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """作者改作品权限(公开/私人 + 是否允许下载)。"""
    try:
        work = plaza_service.set_work_visibility(
            conn, work_id, user.id,
            is_public=body.is_public, allow_download=body.allow_download,
        )
    except ResourceNotFoundOrForbidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="作品不存在或无权修改")
    conn.commit()
    return work


@router.post("/plaza/works/{work_id}/like")
def api_like(
    work_id: str,
    body: LikeBody,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """点赞 / 取消点赞(幂等)。"""
    try:
        result = plaza_service.set_like(conn, work_id, user.id, body.liked)
    except ResourceNotFoundOrForbidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="作品不存在")
    conn.commit()
    return result


@router.delete("/plaza/works/{work_id}")
def api_unpublish(
    work_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """作者下架自己的作品。"""
    try:
        plaza_service.unpublish(conn, work_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="作品不存在或无权下架")
    conn.commit()
    return {"unpublished": True}
