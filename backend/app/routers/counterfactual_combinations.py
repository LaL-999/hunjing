"""反事实组合树路由 — Sprint 6.A2 CT(2026-05-21)。

POST /api/projects/{project_id}/counterfactual-combinations/preview
       预估批次成本(token / 时长 / 积分) — 给前端展示用户确认前用

POST /api/projects/{project_id}/counterfactual-combinations
       创建批次 + fanout 启动 2^N 个 sim

GET  /api/counterfactual-combinations/{combo_id}/tree
       拉决策树 + 叶 sim 状态列表(供前端 SVG 渲染)

设计:
  - 创建批次会复用 simulation_service.create_simulation(每个组合一个 sim)
  - 启动 sim 时复用现有 simulation_service.kick_off / SSE 链路(无新增)
  - sim 关联 combination_run_id 后,前端拉树时一次性返所有 N 个 sim 状态
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.counterfactual_combination import (
    CombinationLeafSim,
    CombinationRunResponse,
    CombinationTreeResponse,
    CreateCombinationRunRequest,
    PreviewRequest,
    PreviewResponse,
    SelectedVariableResponse,
)
from app.services.counterfactual_combination_service import (
    CombinationLimitExceeded,
    CombinationNotFound,
    CombinationVariableInvalid,
    get_combination_leaves,
    get_combination_run,
    preview_cost,
    start_combination_run,
)
from app.models.counterfactual_combination_run import SelectedVariable
from app.services.project_service import (
    ResourceNotFoundOrForbidden,
    get_project_or_403,
)

router = APIRouter()


@router.post(
    "/projects/{project_id}/counterfactual-combinations/preview",
    response_model=PreviewResponse,
)
def api_preview_combination(
    project_id: str,
    req: PreviewRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """预估批次成本(给前端展示用户确认前)。

    返回 total_combinations / 预估 token / 预估时长(串行)/ 预估积分。
    """
    try:
        get_project_or_403(conn, project_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise
    try:
        cost = preview_cost(
            variable_count=req.selected_variable_count,
            reshape_percent=req.reshape_percent,
            target_chars=req.target_chars,
        )
    except CombinationLimitExceeded as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "COMBINATION_LIMIT_EXCEEDED", "message": str(e)},
        )
    return {
        "total_combinations": cost.total_combinations,
        "estimated_token_per_sim": cost.estimated_token_per_sim,
        "estimated_total_tokens": cost.estimated_total_tokens,
        "estimated_minutes_per_sim": cost.estimated_minutes_per_sim,
        "estimated_total_minutes": cost.estimated_total_minutes,
        "estimated_credits": cost.estimated_credits,
    }


@router.post(
    "/projects/{project_id}/counterfactual-combinations",
    response_model=CombinationRunResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_create_combination_run(
    project_id: str,
    req: CreateCombinationRunRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """创建批次 + fanout 启动 2^N 个 sim。返回批次记录(state='generating')。

    Flow:
      1. 校验 variables 全部存在 + 属于该项目 + 未 reverted
      2. 落 combination_run 行(state='pending')
      3. 循环 enumerate_combinations 创建 2^N 个 sim
      4. 全部 sim 创建成功 → state='generating';部分失败 → err_msg
      5. 调用方拿 combo_id 后,前端跳树视图,并依赖 SSE 推送各 sim 状态

    Note:
      - sim 的实际跑批由 simulation_service 后台 worker 接管(此处不阻塞)
      - 前端跳树视图后调 /tree 端点查全部 sim 状态
    """
    try:
        get_project_or_403(conn, project_id, user.id)
    except ResourceNotFoundOrForbidden:
        raise

    variables = [
        SelectedVariable(
            counterfactual_id=v.counterfactual_id,
            label_a=v.label_a,
            label_b=v.label_b,
        )
        for v in req.selected_variables
    ]

    try:
        combo_run_id, sim_ids = start_combination_run(
            conn,
            project_id=project_id,
            user_id=user.id,
            variables=variables,
            divergence=req.divergence,
            reshape_percent=req.reshape_percent,
            target_chars=req.target_chars,
            style=req.style,
            custom_style_hint=req.custom_style_hint,
            use_outline_first=req.use_outline_first,
        )
    except CombinationLimitExceeded as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "COMBINATION_LIMIT_EXCEEDED", "message": str(e)},
        )
    except CombinationVariableInvalid as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "COMBINATION_VARIABLE_INVALID", "message": str(e)},
        )

    # 启动各 sim 的后台生成 — outline-first 走 outline 异步生成,否则走 sim kick_off
    # 与单 sim 创建路径(routers/simulations.py)一致
    #
    # CT-OPT.2(2026-05-21):用 ThreadPoolExecutor 替代裸 for 循环裸调用
    #   - 显式 max_workers 限并发数(防 API rate limit 把 8 个全炸)
    #   - 当 N=4 时 max_workers=4;N=8 时 max_workers=COMBINATION_MAX_CONCURRENCY(默认 4)
    #   - 单 sim kick_off 内部仍 spawn daemon Thread,executor 只是限并发提交速度
    #   - submit 后立即返回,不等执行结果(executor 内的任务都是非阻塞 kick_off)
    from concurrent.futures import ThreadPoolExecutor
    import os
    max_concurrency = int(
        os.getenv("COMBINATION_MAX_CONCURRENCY", "4")
    )
    max_concurrency = max(1, min(max_concurrency, len(sim_ids)))

    def _safe_kick_off(sid: str, use_outline: bool) -> None:
        try:
            if use_outline:
                from app.routers.simulations import _kick_off_outline_async
                _kick_off_outline_async(sid)
            else:
                from app.services import simulation_service
                simulation_service.kick_off(sid)
        except Exception:  # noqa: BLE001
            # 单 sim 启动失败不阻塞其他,用户可在树视图查看失败状态后重试
            pass

    try:
        # 用 ThreadPoolExecutor 限并发提交;executor 自身关闭时不等任务(daemon 任务)
        with ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix=f"ct-fanout-{combo_run_id[:8]}",
        ) as pool:
            for sim_id in sim_ids:
                pool.submit(_safe_kick_off, sim_id, req.use_outline_first)
            # with 块退出会等所有 submit 的任务完成 — 但任务体只是触发 daemon
            # 线程立即返回,所以这里"等"几乎是瞬时的(<100ms)
    except Exception:  # noqa: BLE001
        # 整体失败也不阻塞 — 用户可在树视图重试
        pass

    # 返回批次详情
    run = get_combination_run(conn, combo_run_id, user.id)
    return _serialize_combination_run(run)


@router.get(
    "/projects/{project_id}/counterfactual-combinations/latest",
)
def api_get_latest_combination_for_project(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """返该项目最新 combination_run 的 id;无则返 {"combo_id": None}。

    2026-06-05:让前端"组合树"入口能智能跳转 — 项目有最近批次时直接进进度页,
    无则进配置页(创建新批次)。
    """
    from app.services.counterfactual_combination_service import (
        get_latest_combination_run_for_project,
    )
    combo_id = get_latest_combination_run_for_project(conn, project_id, user.id)
    return {"combo_id": combo_id}


@router.get(
    "/projects/{project_id}/counterfactual-combinations",
)
def api_list_combinations_for_project(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """返该项目所有 combination_runs 的列表(给"切批次"下拉用)。

    2026-06-05:用户可能跑了多个批次想回去复盘 — UI 用此接口渲染历史下拉。
    """
    from app.services.counterfactual_combination_service import (
        list_combination_runs_for_project,
    )
    items = list_combination_runs_for_project(conn, project_id, user.id)
    return {"items": items}


@router.delete(
    "/counterfactual-combinations/{combo_id}",
)
def api_delete_combination(
    combo_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """删除批次 + 级联删所有下属 sim(narrative + outline + scenes 全清)。

    2026-06-05:用户清理历史批次用 — 不可恢复操作,前端需 confirm。
    """
    from app.services.counterfactual_combination_service import (
        delete_combination_run, CombinationNotFound,
    )
    try:
        deleted_sim_count = delete_combination_run(conn, combo_id, user.id)
    except CombinationNotFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "COMBINATION_NOT_FOUND", "message": str(e)},
        )
    return {"deleted_sim_count": deleted_sim_count}


@router.get(
    "/counterfactual-combinations/{combo_id}/tree",
    response_model=CombinationTreeResponse,
)
def api_get_combination_tree(
    combo_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """拉决策树 + 所有叶 sim 当前状态。供前端渲染。

    返回:
      combination_run: 批次本身(selected_variables / state / ...)
      leaves: 所有叶 sim 列表,每个含 tree_path + sim_state + 字数 + 时间
    """
    try:
        run = get_combination_run(conn, combo_id, user.id)
    except CombinationNotFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "COMBINATION_NOT_FOUND", "message": str(e)},
        )

    leaves = get_combination_leaves(conn, combo_id)
    return {
        "combination_run": _serialize_combination_run(run),
        "leaves": leaves,
    }


# ============================================================
# helpers
# ============================================================

def _serialize_combination_run(run) -> dict:
    """CounterfactualCombinationRun → API dict。"""
    return {
        "id": run.id,
        "project_id": run.project_id,
        "user_id": run.user_id,
        "selected_variables": [
            {
                "counterfactual_id": v.counterfactual_id,
                "label_a": v.label_a,
                "label_b": v.label_b,
            }
            for v in run.selected_variables
        ],
        "total_combinations": run.total_combinations,
        "state": run.state,
        "error_message": run.error_message,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
    }
