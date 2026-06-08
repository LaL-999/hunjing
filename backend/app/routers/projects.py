"""Project 路由 — 5 个 CRUD + 1 个组合 graph 接口。

序列化策略:dataclass → asdict → dict → Pydantic response_model 自动校验。
权限策略:所有单项目操作用 get_project_or_403 一次完成"存在 + 归属"双重检查。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, status

from app.db import execute, fetch_all
from app.deps import get_current_user, get_db
from app.models.character import Character
from app.models.event import Event
from app.models.project import Project
from app.models.relationship import Relationship
from app.models.user import User
from app.schemas.event import ProjectGraphResponse
from app.schemas.project import (
    CreateProjectRequest,
    ProjectResponse,
    UpdateProjectRequest,
)
from app.services.project_service import get_project_or_403, iso_now
from app.services.quota_service import (
    QuotaExceeded,
    enforce_project_create_quota,
)

router = APIRouter()


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def api_create_project(
    req: CreateProjectRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        enforce_project_create_quota(conn, user.id, user.plan)
    except QuotaExceeded as e:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "QUOTA_EXCEEDED",
                "kind": e.kind,
                "used": e.used,
                "limit": e.limit,
                "plan": e.plan,
                "message": str(e),
            },
        )

    project_id = str(uuid.uuid4())
    now = iso_now()
    execute(
        conn,
        "INSERT INTO projects "
        "(id, user_id, name, type, custom_type_name, tags, mode, "
        " created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            project_id, user.id, req.name, req.type, req.custom_type_name,
            json.dumps(req.tags, ensure_ascii=False), req.mode, now, now,
        ),
    )
    conn.commit()
    return asdict(Project(
        id=project_id, user_id=user.id, name=req.name, type=req.type,
        custom_type_name=req.custom_type_name,
        tags=req.tags, mode=req.mode, created_at=now, updated_at=now,
    ))


@router.get("", response_model=list[ProjectResponse])
def api_list_projects(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    rows = fetch_all(
        conn,
        "SELECT * FROM projects WHERE user_id=? ORDER BY updated_at DESC",
        (user.id,),
    )
    return [asdict(Project.from_row(r)) for r in rows]


@router.get("/{project_id}", response_model=ProjectResponse)
def api_get_project(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    project = get_project_or_403(conn, project_id, user.id)
    return asdict(project)


@router.patch("/{project_id}", response_model=ProjectResponse)
def api_update_project(
    project_id: str,
    req: UpdateProjectRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    project = get_project_or_403(conn, project_id, user.id)
    updates = req.model_dump(exclude_unset=True)
    if not updates:
        return asdict(project)

    set_parts = []
    values: list = []
    for field, value in updates.items():
        if field == "tags":
            value = json.dumps(value, ensure_ascii=False)
        elif field == "world_baseline":
            # INIT.5(2026-05-21):前端字段名 world_baseline,DB 列名 world_baseline_json
            # 6 维 dict 序列化为 JSON 落库;空字段过滤(用户不填的维度不存)
            cleaned = {
                k: str(v).strip()[:200]
                for k, v in (value or {}).items()
                if v and str(v).strip()
            }
            value = json.dumps(cleaned, ensure_ascii=False)
            field = "world_baseline_json"
        elif field == "narrative_pov":
            # Sprint 6.A2 FOCUS.2(2026-05-21):用户手动改 / extract 自动写
            # Pydantic Literal 已经校验过枚举值,这里只兜底空字符串 → NULL
            if value == "" or value is None:
                value = None
            elif value not in {"first", "second", "third", "mixed"}:
                # 防御层:理论不会到这里(Pydantic 拦截),但写库前再校一次
                continue
        set_parts.append(f"{field}=?")
        values.append(value)

    set_parts.append("updated_at=?")
    values.append(iso_now())
    values.append(project_id)

    execute(
        conn,
        f"UPDATE projects SET {', '.join(set_parts)} WHERE id=?",
        tuple(values),
    )
    conn.commit()

    return asdict(get_project_or_403(conn, project_id, user.id))


@router.post(
    "/{project_id}/infer_world_baseline",
    response_model=ProjectResponse,
)
def api_infer_world_baseline(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """老项目补识别世界观 baseline(2.C+ polish)。

    新中间态项目跑 extract 时已经自动识别(infer_meta v2 阶段 3 落 baseline);
    老项目 / extract 之前完成的项目 baseline 是空 → 用户在反事实工作台「世界观」
    tab 看到 6 个"原"字段空白,点 [✦ AI 识别] 按钮调本端点补识别。

    流程:
      1. 鉴权 + 找项目最新 ready/parsed upload
      2. 拉文本 → 调 infer_meta v2(只取 world_baseline 部分,不动 type/tags)
      3. 落 projects.world_baseline_json
      4. 返 ProjectResponse(含新 baseline)

    Raises:
      404                   project 不存在 / 不属于该用户
      422 NO_UPLOAD         项目无 ready/parsed upload(初始态项目无原作可识别)
      500                   LLM 调用失败
    """
    from app.config import settings
    from app.db import fetch_one, transaction
    from app.services.file_parser import parse_file
    from app.services.llm_extract import infer_meta

    project = get_project_or_403(conn, project_id, user.id)

    # 找该项目最新 ready/parsed upload(extract 完成的优先,否则解析完成的)
    upload_row = fetch_one(
        conn,
        "SELECT * FROM uploads WHERE project_id=? "
        "  AND state IN ('ready', 'parsed') "
        "ORDER BY CASE state WHEN 'ready' THEN 0 ELSE 1 END, uploaded_at DESC "
        "LIMIT 1",
        (project_id,),
    )
    if not upload_row:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "PROJECT_HAS_NO_UPLOAD",
                "message": "项目还没上传作品文件 — 世界观 baseline 需要原作文本来识别",
            },
        )

    abs_path = settings.uploads_abs_dir / upload_row["storage_path"]
    parse_result = parse_file(abs_path, upload_row["mime_type"])
    if not parse_result.success:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "FILE_PARSE_FAILED",
                "message": f"读取上传文件失败:{parse_result.error}",
            },
        )

    try:
        meta, _usage = infer_meta(project.name, parse_result.text)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INFER_META_FAILED",
                "message": f"AI 识别失败:{type(e).__name__}: {str(e)[:150]}",
            },
        )

    baseline = meta.get("world_baseline") if isinstance(meta.get("world_baseline"), dict) else {}
    baseline_json = json.dumps(baseline, ensure_ascii=False) if baseline else None

    with transaction(conn) as tx:
        execute(
            tx,
            "UPDATE projects SET world_baseline_json=?, updated_at=? WHERE id=?",
            (baseline_json, iso_now(), project_id),
        )

    return asdict(get_project_or_403(conn, project_id, user.id))


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,  # FastAPI 0.110 + Py3.13 把 `-> None` 推成 NoneType,触发 204 无 body 断言
)
def api_delete_project(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    get_project_or_403(conn, project_id, user.id)
    execute(conn, "DELETE FROM projects WHERE id=?", (project_id,))
    conn.commit()


# ==========================================================
# P2.B 升级(2026-05-24)— 触发 / 获取 AI 节奏推断
# ==========================================================
# POST /projects/{id}/ensure-pacing
#   前端 SimulationDock 打开时调用 — 若项目已缓存就立即返回,否则同步跑 LLM 推断
#   返回:{ inferred_pacing, inferred_pacing_reasoning, inferred_pacing_metrics, inferred_pacing_at }
#   失败:任何错误内部 fallback "standard",绝不返回 5xx 阻塞用户

@router.post("/{project_id}/ensure-pacing")
def api_ensure_project_pacing(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """触发 / 获取项目叙事节奏推断(P2.B 升级,2026-05-24).

    用户首次创建续作前,前端打开 SimulationDock 时主动调用。
    若已缓存(inferred_pacing 非 NULL)→ 立即返回缓存
    若 NULL → 同步跑 pacing_inferer(2-3s)+ 缓存 + 返回结果
    任何错误 → fallback 'standard',永不返回 5xx 阻塞用户体验
    """
    project = get_project_or_403(conn, project_id, user.id)

    # P0O(2026-05-24)— 自愈老 fallback 缓存:
    # P2.B 升级初版有 import bug → 所有项目缓存了 fallback 结果。
    # 检查 reasoning 含失败标记 → 视为无效,重跑推断
    cached_reasoning = project.inferred_pacing_reasoning or ""
    is_fallback_cache = (
        "AI 推断失败" in cached_reasoning
        or "推断失败" in cached_reasoning
        or "无可用原作" in cached_reasoning
    )

    # 已有有效缓存 → 直接返回
    if (
        project.inferred_pacing in ("slow", "standard", "fast")
        and not is_fallback_cache
    ):
        return {
            "inferred_pacing": project.inferred_pacing,
            "inferred_pacing_reasoning": project.inferred_pacing_reasoning,
            "inferred_pacing_metrics": project.inferred_pacing_metrics,
            "inferred_pacing_at": project.inferred_pacing_at,
            "cached": True,
        }

    # 未推断 — 同步触发
    from app.services.pacing_inferer import (
        infer_pacing_for_project,
        cache_pacing_to_project,
    )
    try:
        result = infer_pacing_for_project(conn, project_id)
        cache_pacing_to_project(conn, project_id, result)
        # 重新拉项目数据返回新缓存值
        refreshed = get_project_or_403(conn, project_id, user.id)
        return {
            "inferred_pacing": refreshed.inferred_pacing,
            "inferred_pacing_reasoning": refreshed.inferred_pacing_reasoning,
            "inferred_pacing_metrics": refreshed.inferred_pacing_metrics,
            "inferred_pacing_at": refreshed.inferred_pacing_at,
            "cached": False,
        }
    except Exception:  # noqa: BLE001 — 任何错误都返 standard,绝不 5xx
        return {
            "inferred_pacing": "standard",
            "inferred_pacing_reasoning": "AI 推断失败,默认标准节奏(你可手动改)",
            "inferred_pacing_metrics": {},
            "inferred_pacing_at": None,
            "cached": False,
        }


@router.get("/{project_id}/graph", response_model=ProjectGraphResponse)
def api_get_project_graph(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """组合接口 — 一次返回项目 + 全部 characters + relationships + events。

    前端图谱编辑器主入口,避免多次 round-trip。
    """
    project = get_project_or_403(conn, project_id, user.id)

    char_rows = fetch_all(
        conn,
        "SELECT * FROM characters WHERE project_id=? ORDER BY created_at ASC",
        (project_id,),
    )
    rel_rows = fetch_all(
        conn,
        "SELECT * FROM relationships WHERE project_id=? ORDER BY created_at ASC",
        (project_id,),
    )
    event_rows = fetch_all(
        conn,
        "SELECT * FROM events WHERE project_id=? ORDER BY created_at ASC",
        (project_id,),
    )

    return {
        "project": asdict(project),
        "characters": [asdict(Character.from_row(r)) for r in char_rows],
        "relationships": [asdict(Relationship.from_row(r)) for r in rel_rows],
        "events": [asdict(Event.from_row(r)) for r in event_rows],
    }


# ============================================================
# 阶段 3A(2026-06-02):项目可继承伏笔列表
# ============================================================

@router.get("/{project_id}/foreshadows")
def api_list_project_foreshadows(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """列项目的 open foreshadows — 给 SimulationDock 的"继承伏笔"面板用.

    返:open(未解 + 未废弃)伏笔列表,按 priority(high → medium → low)+ 创建时间.
    用户在创建新 sim 时,从这个列表勾选要继承到续作的伏笔.
    P1 主线(high)默认勾选(前端处理);P2/P3 默认不勾.

    老库 foreshadow_ledger 表不存在 → 返空 list(独立推演 / 无前作的项目).
    """
    get_project_or_403(conn, project_id, user.id)

    try:
        rows = fetch_all(
            conn,
            """SELECT id, content, priority, introduced_in_simulation_id,
                      introduced_scene_index, created_at
               FROM foreshadow_ledger
               WHERE project_id=? AND status='open'
               ORDER BY CASE priority
                          WHEN 'high' THEN 0
                          WHEN 'medium' THEN 1
                          ELSE 2
                        END,
                        created_at ASC""",
            (project_id,),
        )
    except sqlite3.OperationalError:
        # 老库无 foreshadow_ledger 表 → 返空
        return {"foreshadows": [], "total": 0}

    foreshadows = [
        {
            "id": r["id"],
            "content": r["content"] or "",
            "priority": r["priority"] or "medium",
            "introduced_in_simulation_id": r["introduced_in_simulation_id"],
            "introduced_scene_index": int(r["introduced_scene_index"] or 0),
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return {"foreshadows": foreshadows, "total": len(foreshadows)}


# ============================================================
# SP-1.5(2026-05-29):AI 推断故事内核三件套
# ============================================================

@router.post("/{project_id}/infer/story_core")
def api_infer_story_core(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """触发 LLM 推断故事内核三件套(core_dramatic_question / theme / ending_direction).

    设计:
      - 用户主动按钮触发(非懒触发) — 创作意图层应有主动权
      - 同步等待 LLM 完成(~10-30s)
      - 写入 projects 表(覆盖现有值;用户后续可手动改)
      - 失败 LLM 内部兜底返三字段空 + reasoning 解释 — 不返 5xx

    返回:更新后的完整 Project(含 reasoning 透传给前端展示) — 实际 reasoning
          以一次性 message 形式由 service 返回,前端可以从下一次 GET 拉到字段值,
          这里直接把更新后的 Project 透出.
    """
    get_project_or_403(conn, project_id, user.id)
    from app.services.story_core_inferer import (
        infer_story_core_for_project,
        cache_story_core_to_project,
    )
    result = infer_story_core_for_project(conn, project_id)
    cache_story_core_to_project(conn, project_id, result)
    refreshed = get_project_or_403(conn, project_id, user.id)
    out = asdict(refreshed)
    # 透传 reasoning 给前端 toast 显示(非持久化字段)
    out["_inference_reasoning"] = result.get("reasoning", "")
    return out


# ============================================================
# SP-8.1(2026-05-29):AI 推断视角扩展三件套
# ============================================================

@router.post("/{project_id}/infer/narrative_view")
def api_infer_narrative_view(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """触发 LLM 推断视角扩展三件套.

    focus_character 由 service 层从 LLM 输出 name 映射到 character_id;
    无匹配 → None,但 reliability / distance 仍可用.
    """
    get_project_or_403(conn, project_id, user.id)
    from app.services.narrative_view_inferer import (
        infer_narrative_view_for_project,
        cache_narrative_view_to_project,
    )
    result = infer_narrative_view_for_project(conn, project_id)
    cache_narrative_view_to_project(conn, project_id, result)
    refreshed = get_project_or_403(conn, project_id, user.id)
    out = asdict(refreshed)
    out["_inference_reasoning"] = result.get("reasoning", "")
    out["_inference_focus_name"] = result.get("narrative_focus_character_name", "")
    return out


# ============================================================
# 一键灌满北极星(2026-05-29 末)— 编排 4+N 个 inferer
# ============================================================

@router.post("/{project_id}/infer/all_boards")
def api_infer_all_boards(
    project_id: str,
    include_drivers: bool = True,
    force_knowledge: bool = False,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """一次性触发所有 inferer:故事脊柱 + 视角扩展 + 知识边界 + N 个角色驱动.

    串行执行,异常隔离 — 某个 stage 失败不影响其他.
    返完整汇总 report 给前端展示进度 + 部分成功提示.

    参数:
      include_drivers:是否给每个角色推 drivers(默认 True,角色多时较慢)
      force_knowledge:knowledge_boundaries 是否 overwrite=True(默认 False,
        已有 facts 时 skip;True 时清空重建)

    幂等:LLM 调用本身有随机性,但 cache 行为是确定的(overwrite or skip).
    用户重复点 → 已填字段保留(drivers overwrite=False),已有 facts 视 force_knowledge.
    """
    get_project_or_403(conn, project_id, user.id)
    from app.services.inferers_batch import infer_all_boards_for_project
    report = infer_all_boards_for_project(
        conn, project_id,
        include_drivers=include_drivers,
        force_knowledge=force_knowledge,
    )
    return report


# ============================================================
# SSE 长任务进度(2026-05-30²)
# 一键灌满总耗时 1-5 分钟,用户无进度反馈会担心是否在跑.
# 用 StreamingResponse + queue+thread 模式推送 stage 切换事件.
# ============================================================

@router.post("/{project_id}/infer/all_boards/stream")
def api_infer_all_boards_stream(
    project_id: str,
    include_drivers: bool = True,
    force_knowledge: bool = False,
    include_polarity: bool = True,
    force_polarity: bool = False,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
):
    """SSE 流式版一键灌满 — 实时推送 stage 进度.

    事件格式(text/event-stream):
      event: plan          data: {total_stages, include_drivers, include_polarity}
      event: stage_start   data: {stage, stage_index, total_stages, label}
      event: stage_done    data: {stage, stage_index, applied, reasoning, ...}
      event: stage_failed  data: {stage, stage_index, error}
      event: done          data: {stats}
      event: report        data: <完整 report dict>(最后)

    前端用 fetch + ReadableStream 拆 SSE 行(不用 EventSource 保留 POST + Bearer).

    实现:
      - inferer 同步运行在后台线程
      - 主请求线程从 queue 拉 progress event 转 SSE
      - 完成后 report 也通过 queue 推回主线程
    """
    get_project_or_403(conn, project_id, user.id)

    import json
    import queue
    import sqlite3 as _sqlite3
    import threading
    from fastapi.responses import StreamingResponse

    from app.db import get_connection
    from app.services.inferers_batch import infer_all_boards_for_project

    # 用 queue 在 worker 线程 → 主线程之间传 progress event
    event_queue: queue.Queue = queue.Queue()
    SENTINEL = object()

    def worker():
        # 工作线程开独立 sqlite connection(主请求 conn 不能跨线程用)
        worker_conn = get_connection()
        try:
            def on_progress(ev: dict) -> None:
                event_queue.put(ev)

            try:
                report = infer_all_boards_for_project(
                    worker_conn, project_id,
                    include_drivers=include_drivers,
                    force_knowledge=force_knowledge,
                    include_polarity=include_polarity,
                    force_polarity=force_polarity,
                    progress_callback=on_progress,
                )
                # 最后把完整 report 推一次(前端可拿到细节)
                event_queue.put({"event": "report", "report": report})
            except Exception as e:  # noqa: BLE001
                event_queue.put({
                    "event": "fatal",
                    "error": f"{type(e).__name__}: {str(e)[:200]}",
                })
        finally:
            try:
                worker_conn.close()
            except _sqlite3.Error:
                pass
            event_queue.put(SENTINEL)

    # 2026-06-05 BYOK:capture context 让 worker 内 LLM 调用拿到用户 user_id
    from app.services.byok_context import capture_current_context
    ctx = capture_current_context()
    t = threading.Thread(target=ctx.run, args=(worker,), daemon=True)
    t.start()

    def sse_generator():
        while True:
            ev = event_queue.get()
            if ev is SENTINEL:
                break
            event_name = ev.get("event", "message")
            # 排除 event 字段,其余作为 data
            data_payload = {k: v for k, v in ev.items() if k != "event"}
            data_json = json.dumps(data_payload, ensure_ascii=False)
            yield f"event: {event_name}\ndata: {data_json}\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # 防 nginx 缓冲(若用 nginx 反代)
        },
    )
