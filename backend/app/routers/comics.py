"""漫画态路由 — Sprint D.9 Sprint 2.A。

POST   /api/comics                            创建漫画态项目(state='queued')
GET    /api/comics                            列出当前用户所有漫画
GET    /api/comics/{comic_id}                 单个漫画详情(含 is_alive zombie 检测)
DELETE /api/comics/{comic_id}                 删除漫画(级联删 character_cards / scenes / props /
                                                       character_visuals / comic_pages)
POST   /api/comics/{comic_id}/upload_references  用户上传 3 张参考图,触发画风定调员 v2
POST   /api/comics/{comic_id}/vote_style       用户 5 选 1,触发角色锚定员
POST   /api/comics/{comic_id}/cancel           用户取消运行中的漫画

设计:
  - 漫画态走独立路由(隔离铁律,ADR §5),不复用 /api/projects
  - state 异步推进,前端 polling /api/comics/{id} 看 state 变化
  - is_alive 派生字段对齐 canonical_audit / extract_job 模式
"""
from __future__ import annotations

import secrets
import sqlite3
import traceback

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Response,
    UploadFile,
    status,
)

from app.config import settings
from app.db import fetch_all
from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.comic import (
    ComicResponse,
    CreateComicRequest,
    PlanPreviewRequest,
    PlanPreviewResponse,
    UploadReferenceFileResponse,
    UploadReferenceImagesRequest,
    VoteStyleRequest,
)
from app.services.comic_service import (
    ComicNotFoundOrForbidden,
    ComicPlanRequired,
    ComicStillRunning,
    ExportNothingToDo,
    InvalidComicSource,
    _agent_character_anchor,
    _agent_planner,
    _agent_scripter,
    _agent_style_director_v2,
    _run_style_candidates_stage,
    _agent_visual_assets_extractor,
    _export_comic_pdf,
    _export_comic_zip,
    _load_source_text_from_dict,
    _sanitize_filename,
    _update_state,
    _validate_source,
    cancel_comic,
    create_comic,
    delete_comic,
    get_comic_or_404,
    is_comic_alive,
    list_comics_for_user,
)
from app.services.credit_service import InsufficientCredits


router = APIRouter()


# ============================================================
# Sprint C.4:AI Planner 创建前预览
# ============================================================

@router.post(
    "/comics/plan_preview",
    response_model=PlanPreviewResponse,
)
def api_plan_preview(
    req: PlanPreviewRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """AI Planner 预览 — 创建漫画前调,**不持久化任何数据**,只跑 DeepSeek 给推荐。

    用户在 CreateComicModal 选完源 + 点 "AI 推荐"按钮后调本端点。
    返回 recommended_total_pages / estimated_credits / reasoning。
    用户拿到推荐后可调整滑块再正式 POST /comics 创建。

    成本:planner LLM 调用 ~0.5 c(极轻量),失败不阻塞创建流程,前端兜底"用户手动选页数"。
    """
    # 1. 校验源合法
    source_dict = req.source.model_dump(exclude_none=True)
    try:
        _validate_source(conn, user.id, source_dict)
    except InvalidComicSource as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_COMIC_SOURCE", "message": str(e)},
        )

    # 2. 拉源文本 + 角色数(从源 simulations 拿)
    # Sprint 4.D+ bug fix(2026-05-13):原 _load_source_text_from_dict 没 try/except 包围,
    # 任何 db / SQL 错(如曾经 uploads.created_at 列错)会直接 500 让前端只看到含糊"请求失败"
    try:
        source_text = _load_source_text_from_dict(conn, source_dict)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "SOURCE_LOAD_FAILED",
                "message": f"加载源文本失败({type(e).__name__}):{str(e)[:200]}",
            },
        )
    if not source_text or len(source_text.strip()) < 100:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "SOURCE_TOO_SHORT",
                "message": f"输入源文本不足 100 字(实际 {len(source_text.strip())} 字),不足以生成漫画",
            },
        )

    # 角色数:从源 simulations / uploads 关联的 project 拉(用第一条 sim/upload 的 project_id)
    character_count = 0
    if source_dict.get("type") == "internal":
        sim_ids = source_dict.get("simulation_ids") or []
        if sim_ids:
            from app.db import fetch_one
            row = fetch_one(
                conn,
                "SELECT project_id FROM simulations WHERE id=? LIMIT 1",
                (sim_ids[0],),
            )
            if row:
                count_row = fetch_one(
                    conn,
                    "SELECT COUNT(*) AS cnt FROM characters WHERE project_id=?",
                    (row["project_id"],),
                )
                character_count = int(count_row["cnt"]) if count_row else 0

    # 3. 跑 planner
    try:
        plan = _agent_planner(
            source_text=source_text,
            character_count=character_count,
            major_event_count=0,   # Sprint C.4 简化:暂不接 events 表
            user_preference=req.user_preference,
        )
    except Exception as e:
        traceback.print_exc()
        # 失败兜底:返回保守默认值,不阻塞用户创建
        plan = {
            "recommended_total_pages": 12,
            "panels_per_page": 6,
            "estimated_credits": 260,
            "reasoning": f"AI 推荐暂不可用({type(e).__name__});已应用保守默认 12 页",
            "long_text_warning": len(source_text) > 14400,
        }

    return {
        **plan,
        "source_char_count": len(source_text),
        "character_count": character_count,
    }


# ============================================================
# 基础 CRUD
# ============================================================

@router.post(
    "/comics",
    response_model=ComicResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_create_comic(
    req: CreateComicRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """创建漫画态项目 — Sprint 2.A 落 state='queued';
    用户后续上传参考图触发实际 pipeline。

    Sprint 5.B 商业模型变更(2026-05-18):
      - 漫画态从"按 credit 消耗"改为"订阅福利免费次数池"
      - 创建入口先 enforce_comic_count_quota(查本月已占用 vs 配额)
      - 超额 → 429 QUOTA_EXCEEDED + kind='comics_per_month'(前端弹 UpgradeModal)
      - 通过后落 state='queued',pipeline 跑完只 log cost audit,**不扣 credit**
      - 失败 / 取消的漫画不占用次数(count 时 NOT IN failed/cancelled)
    """
    # v5(2026-07-02)漫创态解锁重构(item4 + item6):
    #   - 漫画包 / comics_per_month 次数闸门**全部下线**(不再 enforce_comic_count_quota)
    #   - 准入改为二选一:
    #       a) 订阅 Pro / Max / founder → 用平台图像 key(订阅费已覆盖)
    #       b) 开通自携密钥(BYOK)且配了『图像模型』→ 用自带 key,不占平台图像额度(主打路径)
    #   - 防白嫖:免费无订阅、又没配 BYOK 图像 key 的用户拦在门外(否则生图会回落平台 key = 白嫖创始人)
    from app.services.byok_service import (
        get_active_image_config,
        get_active_llm_config,
    )

    # super_max:v5 已下架不可售,但历史订阅用户老规则保护 —— 仍解锁漫创态
    has_subscription = user.plan in ("pro", "max", "super_max", "founder")
    has_byok_image = get_active_image_config(conn, user.id) is not None
    if not (has_subscription or has_byok_image):
        # 已开通 BYOK 文本但缺图像模型 → 精准引导去补;否则引导开通 BYOK(主打)/ 订阅
        has_byok_active = get_active_llm_config(conn, user.id) is not None
        if has_byok_active:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "COMIC_IMAGE_KEY_REQUIRED",
                    "message": (
                        "漫创态还差一个图像模型:你已开通自携密钥,请到「自携密钥」里再加配一个"
                        "『图像模型』(硅基流动 / 火山 Seedream / 智谱 CogView 等),即可开始创作漫画。"
                    ),
                },
            )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={
                "code": "COMIC_ACCESS_REQUIRED",
                "message": (
                    "漫创态需先开通「自携密钥」(¥5/月,配上你自己的图像模型 key 即可无限用),"
                    "或订阅 Pro / Max。点击主页左下角个人头像卡片即可开通自携密钥。"
                ),
            },
        )

    try:
        comic = create_comic(
            conn, user.id, req.name,
            req.source.model_dump(exclude_none=True),
            target_pages=req.target_pages,
        )
    except InvalidComicSource as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_COMIC_SOURCE", "message": str(e)},
        )
    except ComicPlanRequired as e:
        # 2026-06-02:Free 档不能创建漫画,需升级 Pro
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail={"code": "COMIC_PLAN_REQUIRED", "message": str(e)},
        )
    except InsufficientCredits as e:
        # Sprint 5.B:理论上漫画态不再扣 credit,此 except 保留作健壮性兜底
        # (若 create_comic 内部仍有未清理的扣 credit 路径,降级为提示而非 500)
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "INSUFFICIENT_CREDITS",
                "needed": e.needed,
                "available": e.available,
                "action": e.action,
                "message": str(e),
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_ERROR", "message": f"{type(e).__name__}: {e}"[:300]},
        )

    return comic.to_response()


@router.get("/comics", response_model=list[ComicResponse])
def api_list_comics(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """当前用户的全部漫画(按 updated_at 倒序)。"""
    comics = list_comics_for_user(conn, user.id)
    return [c.to_response(is_alive=is_comic_alive(c.id)) for c in comics]


@router.get("/comics/{comic_id}", response_model=ComicResponse)
def api_get_comic(
    comic_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """单个漫画详情(含 is_alive 派生字段)。"""
    try:
        comic = get_comic_or_404(conn, comic_id, user.id)
    except ComicNotFoundOrForbidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="漫画不存在或不属于你")
    return comic.to_response(is_alive=is_comic_alive(comic.id))


@router.get("/comics/{comic_id}/pages")
def api_list_comic_pages(
    comic_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """Sprint 3:拉本漫画所有 page(按 page_index 升序)— 前端阅读器用。

    返回 [{id, page_index, panels: [{panel_index, image_url, dialogues, narrator, sfx}],
          composed_url, state, regenerated_count, ...}]
    """
    try:
        get_comic_or_404(conn, comic_id, user.id)   # 鉴权
    except ComicNotFoundOrForbidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="漫画不存在或不属于你")

    import json as _json
    rows = fetch_all(
        conn,
        """SELECT id, page_index, panels_json, composed_url, state,
                  regenerated_count, created_at, updated_at
           FROM comic_pages
           WHERE comic_id=?
           ORDER BY page_index ASC""",
        (comic_id,),
    )

    result: list[dict] = []
    for r in rows:
        try:
            panels = _json.loads(r["panels_json"]) if r["panels_json"] else []
        except _json.JSONDecodeError:
            panels = []
        result.append({
            "id": r["id"],
            "page_index": int(r["page_index"]),
            "panels": panels,
            "composed_url": r["composed_url"],
            "state": r["state"],
            "regenerated_count": int(r["regenerated_count"]),
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        })
    return result


@router.delete("/comics/{comic_id}")
def api_delete_comic(
    comic_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """删除漫画 — 级联删所有关联表(migration 定义的 ON DELETE CASCADE)。"""
    try:
        delete_comic(conn, comic_id, user.id)
    except ComicNotFoundOrForbidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="漫画不存在或不属于你")
    except ComicStillRunning as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "COMIC_STILL_RUNNING", "message": str(e)},
        )
    return {"deleted": True}


# ============================================================
# 推进 pipeline 端点(用户操作驱动 + 后台 agent 跑)
# ============================================================

# Sprint 2.B+ 六修(2026-05-12):漫画参考图本地上传
# ─────────────────────────────────────────────────
# 流程:
#   1. 前端选图 → POST multipart 到本端点(每次单文件)
#   2. 后端落 backend/data/comic_refs/{user_id}/{token24}.{ext}
#   3. 返回 {url, filename} → 前端缩略图预览
#   4. 用户上传 3 张后,把 3 个 url 提交给 /upload_references 触发画风定调员
#
# 安全:
#   - filename = secrets.token_urlsafe(24)(32 字符,~1.4e57 组合,不可猜)
#   - 静态服务公开 — 行业图床惯例,filename 本身就是 token
#   - 大小限 5MB / 类型限 image/*
#   - 路径需校验 comic 归属当前 user(防越权:别人 comic_id 给自己存图)

ALLOWED_IMAGE_MIMES = {
    "image/jpeg", "image/jpg", "image/png", "image/webp",
    "image/gif", "image/bmp",
}
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
COMIC_REF_MAX_BYTES = 5 * 1024 * 1024   # 5 MB / 张


@router.post(
    "/comics/{comic_id}/upload_reference_file",
    response_model=UploadReferenceFileResponse,
)
async def api_upload_reference_file(
    comic_id: str,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """漫画参考图本地上传 — Sprint 2.B+ 六修(2026-05-12)。

    取代原"用户粘公网 URL"流程。前端选本地文件 → 后端落盘 → 返回内部 URL。
    """
    # 1. 校验 comic 归属
    try:
        get_comic_or_404(conn, comic_id, user.id)
    except ComicNotFoundOrForbidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="漫画不存在或不属于你")

    # 2. 校验 MIME / 扩展名
    declared_mime = (file.content_type or "").lower()
    filename_orig = file.filename or ""
    ext = ""
    if "." in filename_orig:
        ext = "." + filename_orig.rsplit(".", 1)[-1].lower()

    if declared_mime not in ALLOWED_IMAGE_MIMES and ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "REF_IMAGE_BAD_TYPE",
                "message": "参考图必须是 JPG / PNG / WebP / GIF / BMP",
            },
        )

    # ext 兜底(MIME OK 但无扩展名时按 MIME 推断)
    if not ext:
        ext = {
            "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
            "image/webp": ".webp", "image/gif": ".gif", "image/bmp": ".bmp",
        }.get(declared_mime, ".jpg")

    # 3. 读字节 + 校验大小
    raw = await file.read()
    if len(raw) > COMIC_REF_MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "code": "REF_IMAGE_TOO_LARGE",
                "message": f"参考图最大 {COMIC_REF_MAX_BYTES // 1024 // 1024} MB,当前 {len(raw) // 1024 // 1024} MB",
            },
        )
    if len(raw) == 0:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail={"code": "REF_IMAGE_EMPTY", "message": "参考图文件为空"},
        )

    # 4. 生成不可猜 filename + 落盘
    token = secrets.token_urlsafe(24)   # 32 字符,~1.4e57 组合
    filename = f"{token}{ext}"

    comic_refs_dir = settings.uploads_abs_dir.parent / "comic_refs" / user.id
    comic_refs_dir.mkdir(parents=True, exist_ok=True)

    file_path = comic_refs_dir / filename
    try:
        file_path.write_bytes(raw)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "REF_IMAGE_SAVE_FAILED", "message": str(e)[:200]},
        )

    # 5. 返回内部 URL(对齐 main.py StaticFiles mount)
    url = f"/api/comic-files/{user.id}/{filename}"
    return {"url": url, "filename": filename}


@router.post("/comics/{comic_id}/upload_references", response_model=ComicResponse)
def api_upload_references(
    comic_id: str,
    req: UploadReferenceImagesRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """用户上传 3 张参考图 → 触发 Agent #2 编剧 + Agent #5 素材库 + Agent #3 v2 画风定调员。

    Sprint 2.A 同步执行(LLM 调用耗时,前端要 loading 30-90s);Sprint 2.B 改成后台
    worker + SSE 推送(对齐 simulation_service.kick_off 模式)。

    流程:
      1. 编剧 _agent_scripter:扫 source → script_json
      2. 素材库 _agent_visual_assets_extractor:扫 source → character_visuals / scenes / props
      3. 画风定调员 _agent_style_director_v2:Qwen-VL × 3 + DeepSeek + Seedream × 5
      4. state → 'style_voting' 等用户 5 选 1
    """
    try:
        comic = get_comic_or_404(conn, comic_id, user.id)
    except ComicNotFoundOrForbidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="漫画不存在或不属于你")

    if comic.state not in ("queued", "style_uploading", "failed"):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "INVALID_STATE_FOR_UPLOAD",
                "message": f"当前 state={comic.state},不接受参考图上传",
            },
        )

    try:
        # Step 1:编剧
        _update_state(conn, comic.id, "scripting", progress=10)
        _agent_scripter(comic, conn)

        # Step 2:素材库抽取(实际可与编剧并行,Sprint 2.A 简化为串行;
        # Sprint 2.B 改 ThreadPoolExecutor 并发)
        _update_state(conn, comic.id, "extracting_visuals", progress=25)
        # 重新拉 comic(scripter 已更新 script 字段)
        comic = get_comic_or_404(conn, comic_id, user.id)
        _agent_visual_assets_extractor(comic, conn)

        # Step 3:画风定调员 v2
        _update_state(conn, comic.id, "style_analyzing", progress=50)
        comic = get_comic_or_404(conn, comic_id, user.id)
        _agent_style_director_v2(comic, conn, req.image_urls)

        # Step 4:状态推到 style_voting,等用户 5 选 1
        _update_state(conn, comic.id, "style_voting", progress=70)
    except ValueError as e:
        _update_state(conn, comic.id, "failed", error_message=str(e)[:300])
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "AGENT_FAILED", "message": str(e)},
        )
    except Exception as e:
        traceback.print_exc()
        _update_state(conn, comic.id, "failed", error_message=f"{type(e).__name__}: {e}"[:300])
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_ERROR", "message": str(e)[:300]},
        )

    comic = get_comic_or_404(conn, comic_id, user.id)
    return comic.to_response(is_alive=False)


@router.post("/comics/{comic_id}/retry_style_candidates", response_model=ComicResponse)
def api_retry_style_candidates(
    comic_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """Sprint 5.10+(2026-05-14):用户 dialog 看到"3 张候选全失败"时,**只重跑 Stage 3**
    出新的 3 张候选,不重做 Stage 1 + 2(已花了 ¥0.05 Qwen-VL DNA + DeepSeek 综合)。

    解决用户痛点:原流程让用户"取消漫画 → 重新上传参考图"重走全流程 30-60s,体验差。
    本端点只重画 3 张图(~10-30s,SiliconFlow Qwen-Image 6 并发 / 串行视 vendor)。

    错误码:
      404 COMIC_NOT_FOUND       漫画不存在或不属于你
      409 INVALID_STATE_FOR_RETRY  state ≠ style_voting(不在 5 选 1 阶段)
      409 NO_STYLE_PROMPT       style_detailed_prompt 缺失(Stage 1+2 未完成)
      503 RETRY_STILL_FAILED    重试仍全失败(vendor 限流 / 内容审核),错误详情在 detail
    """
    try:
        comic = get_comic_or_404(conn, comic_id, user.id)
    except ComicNotFoundOrForbidden:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "COMIC_NOT_FOUND", "message": f"漫画 {comic_id} 不存在"},
        )

    if comic.state != "style_voting":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "INVALID_STATE_FOR_RETRY",
                "message": f"当前 state={comic.state},不在 style_voting 阶段无法重试候选",
            },
        )
    if not comic.style_detailed_prompt:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "NO_STYLE_PROMPT",
                "message": "style_detailed_prompt 缺失(Stage 1+2 未完成),无法重试 Stage 3",
            },
        )

    try:
        result = _run_style_candidates_stage(comic, conn)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "RETRY_INTERNAL_ERROR", "message": str(e)[:200]},
        )

    # 全失败 → 503 + 详情
    if result["created"] == 0:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "RETRY_STILL_FAILED",
                "message": (
                    "重试 3 张候选仍全失败,vendor 可能限流 / 内容审核拦截。"
                    "建议:稍候 1 分钟再试;若仍失败,考虑切换生图 vendor"
                ),
                "errors": result["errors"][:3],   # 前 3 个错误详情
            },
        )

    # 重新拉最新 comic 返回(已含新 candidates)
    comic = get_comic_or_404(conn, comic_id, user.id)
    return comic.to_response(is_alive=False)


@router.post("/comics/{comic_id}/vote_style", response_model=ComicResponse)
def api_vote_style(
    comic_id: str,
    req: VoteStyleRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """用户 5 选 1 → 触发 Agent #4 角色锚定员。

    Sprint 2.A 简化:vote 后同步跑角色锚定;Sprint 2.B 改后台 worker + SSE。
    """
    try:
        comic = get_comic_or_404(conn, comic_id, user.id)
    except ComicNotFoundOrForbidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="漫画不存在或不属于你")

    if comic.state != "style_voting":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={
                "code": "INVALID_STATE_FOR_VOTE",
                "message": f"当前 state={comic.state},不在 style_voting 阶段",
            },
        )
    if not comic.style_candidates or len(comic.style_candidates) < req.selected_index:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "NO_CANDIDATE_AT_INDEX",
                "message": f"selected_index={req.selected_index} 超出 candidates 数量",
            },
        )

    selected = comic.style_candidates[req.selected_index - 1]
    selected_url = selected.get("image_url")
    if not selected_url:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "CANDIDATE_FAILED", "message": "该候选生成失败,请选其他张"},
        )

    # 落选定 anchor URL
    from app.db import execute
    from app.services.comic_service import _now_iso
    execute(
        conn,
        "UPDATE comic_projects SET style_anchor_image_url=?, updated_at=? WHERE id=?",
        (selected_url, _now_iso(), comic.id),
    )
    conn.commit()

    try:
        _update_state(conn, comic.id, "character_anchoring", progress=80)
        comic = get_comic_or_404(conn, comic_id, user.id)
        _agent_character_anchor(comic, conn)
        # Sprint 3(2026-05-13):character_anchoring 完成 → designing → 异步启动主循环
        # state designing 仅瞬时,kick_off 内部翻 generating + 跑 director/image_gen
        _update_state(conn, comic.id, "designing", progress=85)
    except ValueError as e:
        _update_state(conn, comic.id, "failed", error_message=str(e)[:300])
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "AGENT_FAILED", "message": str(e)},
        )
    except Exception as e:
        traceback.print_exc()
        _update_state(conn, comic.id, "failed", error_message=f"{type(e).__name__}: {e}"[:300])
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INTERNAL_ERROR", "message": str(e)[:300]},
        )

    # Sprint 3:异步启动主循环(designing → generating → done)— 用户不阻塞,返回后
    # 前端 polling /api/comics/{id} 看 progress 推进
    from app.services.comic_service import kick_off
    kick_off(comic.id)

    comic = get_comic_or_404(conn, comic_id, user.id)
    return comic.to_response(is_alive=is_comic_alive(comic.id))


@router.post("/comics/{comic_id}/cancel")
def api_cancel_comic(
    comic_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """用户取消漫画(state → 'cancelled')+ Sprint 2.B+ 四修退款。

    Returns:
        {
          "comic": ComicResponse,
          "refund": {
            "phase": "full" / "half" / "none" / "noop",
            "units": int,  # 0 / -50 / -100
            "label": str,  # 中文文案,前端 toast 直接用
          }
        }

    refund.phase 语义:
      full  — 进度 < 10%,全额退回(不扣本月配额)
      half  — 进度 10-80%,半额退回(扣 0.5 本,月度计数仍占 1 slot)
      none  — 进度 >= 80%,不退(扣 1 本)
      noop  — 漫画已是终态,无操作(refund_units=0)
    """
    try:
        refund_units, refund_phase = cancel_comic(conn, comic_id, user.id)
    except ComicNotFoundOrForbidden:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="漫画不存在或不属于你")

    comic = get_comic_or_404(conn, comic_id, user.id)

    # 退款文案映射(给前端 toast 用)
    REFUND_LABELS = {
        "full": "全额退回(进度 < 10%,本月配额不扣)",
        "half": "半额退回(进度 10-80%,扣半本配额)",
        "none": "未退回(进度 ≥ 80%,扣全额配额)",
        "noop": "漫画已是终态,无需操作",
    }
    return {
        "comic": comic.to_response(is_alive=is_comic_alive(comic.id)),
        "refund": {
            "phase": refund_phase,
            "units": refund_units,
            "label": REFUND_LABELS.get(refund_phase, ""),
        },
    }


# ============================================================
# Sprint 4.D 导出 — PDF + PNG zip(2026-05-13)
# ============================================================

def _check_comic_exportable(comic) -> None:
    """统一校验 comic 是否可导出 — state 必须 'done'。

    raises HTTPException 422 with code COMIC_NOT_EXPORTABLE。
    """
    if comic.state != "done":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "COMIC_NOT_EXPORTABLE",
                "message": f"漫画状态 {comic.state} 不支持导出(需 'done')",
            },
        )


@router.get("/comics/{comic_id}/export.pdf")
def api_export_comic_pdf(
    comic_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """整本漫画导出 PDF — 复用 Sprint 4.C typesetter 整页 PNG 合成多页 PDF。

    返回:application/pdf bytes;Content-Disposition attachment + 文件名(漫画名 sanitize)。
    Header X-Missing-Pages 含缺页号(typesetter 失败页),前端 toast 提示用户。

    错误码:
      404 COMIC_NOT_FOUND       漫画不存在或不属于当前用户
      422 COMIC_NOT_EXPORTABLE  state ≠ 'done'
      422 EXPORT_NOTHING_TO_DO  所有 composed_url 都缺失(typesetter 全失败 / 老漫画)
    """
    try:
        comic = get_comic_or_404(conn, comic_id, user.id)
    except ComicNotFoundOrForbidden:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "COMIC_NOT_FOUND", "message": f"漫画 {comic_id} 不存在"},
        )

    _check_comic_exportable(comic)

    try:
        pdf_bytes, missing_pages = _export_comic_pdf(conn, comic)
    except ExportNothingToDo as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "EXPORT_NOTHING_TO_DO", "message": str(e)},
        )

    safe_name = _sanitize_filename(comic.name)
    # RFC 5987 filename* 编码以支持中文文件名(浏览器 UTF-8);保留 ASCII filename 兜底
    from urllib.parse import quote
    encoded = quote(f"{safe_name}.pdf")
    headers = {
        "Content-Disposition": f"attachment; filename=\"comic.pdf\"; filename*=UTF-8''{encoded}",
    }
    if missing_pages:
        headers["X-Missing-Pages"] = ",".join(str(p) for p in missing_pages)
    return Response(content=pdf_bytes, media_type="application/pdf", headers=headers)


@router.get("/comics/{comic_id}/export.zip")
def api_export_comic_zip(
    comic_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> Response:
    """整本漫画导出 PNG zip — 每页一张 PNG,按 page_index 排序(zero-padded)。

    错误码同 export.pdf。
    """
    try:
        comic = get_comic_or_404(conn, comic_id, user.id)
    except ComicNotFoundOrForbidden:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "COMIC_NOT_FOUND", "message": f"漫画 {comic_id} 不存在"},
        )

    _check_comic_exportable(comic)

    try:
        zip_bytes, missing_pages = _export_comic_zip(conn, comic)
    except ExportNothingToDo as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "EXPORT_NOTHING_TO_DO", "message": str(e)},
        )

    safe_name = _sanitize_filename(comic.name)
    from urllib.parse import quote
    encoded = quote(f"{safe_name}.zip")
    headers = {
        "Content-Disposition": f"attachment; filename=\"comic.zip\"; filename*=UTF-8''{encoded}",
    }
    if missing_pages:
        headers["X-Missing-Pages"] = ",".join(str(p) for p in missing_pages)
    return Response(content=zip_bytes, media_type="application/zip", headers=headers)
