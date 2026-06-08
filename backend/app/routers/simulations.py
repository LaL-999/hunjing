"""Simulation 路由 — Sprint 1.G 续写引擎服务化的 5 个端点。

- POST   /api/projects/{project_id}/simulations    创建推演 + 异步 kick_off
- GET    /api/projects/{project_id}/simulations    项目下所有推演(列表 / summary)
- GET    /api/simulations/{simulation_id}          单条详情(含 timeline + narrative)
- GET    /api/simulations/{simulation_id}/stream   SSE 实时进度
- DELETE /api/simulations/{simulation_id}          204
"""
from __future__ import annotations

import sqlite3
import traceback

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from jose import JWTError

from app.db import get_connection
from app.deps import get_current_user, get_db
from app.models.user import User
from app.services.auth_service import decode_sse_token
from app.schemas.simulation import (
    CreateSimulationRequest,
    EmotionalStateResponse,
    SceneHintRequest,
    SceneHintResponse,
    SimulationCreatedResponse,
    SimulationFullResponse,
    SimulationSummaryResponse,
    SimulationSummaryWithProjectResponse,
)
from app.services.emotional_state_tracker import (
    list_emotional_states_for_visualization,
)
from app.services.counterfactual_service import ReshapeCharacterLimitExceeded
from app.services.project_service import ResourceNotFoundOrForbidden
from app.services.upload_service import NoReadyUploadForTailExcerpt
from app.services.credit_service import (
    InsufficientCredits,
    get_balance,
)
from app.services.quota_service import (
    QuotaExceeded,
    enforce_reshape_quota,
)
from app.services import simulation_service
from app.services.project_service import get_project_or_403
from app.services.simulation_service import (
    InvalidAnchorEvent,
    InvalidContextSimulations,
    SimulationNotResumable,
    SimulationStillRunning,
    TooFewCharactersForSimulation,
    build_characters_snapshot,
    create_simulation,
    delete_simulation,
    get_simulation_or_404,
    list_simulations_for_project,
    compute_inheritance_metadata_for_user,
    list_simulations_for_user,
    resume_simulation,
    stream_simulation_state,
    validate_context_simulation_ids,
)

router = APIRouter()


# ============================================================
# M6-fix2(2026-05-20):outline 生成异步化
# ============================================================
# 模块级 thread runner — 测试 monkeypatch 此变量为同步直跑,
# 让 test_outline_first.py 不必等线程结束就能断言
def _outline_async_runner(sim_id: str) -> None:
    """生产环境:起后台 daemon 线程跑 create_outline_draft,POST 立即返回 drafting。

    2026-06-05 BYOK:capture_current_context() 把当前 endpoint 的 user_id ContextVar
    捕获,带进新 thread 跑 — 否则 thread 里 get_current_user_id() 永远是 None,
    BYOK 路由会失效(误走平台默认 key)。
    """
    import threading
    from app.db import get_connection
    from app.services.byok_context import capture_current_context

    # ⚠ 必须在主 thread(即 endpoint 上下文)捕获,新 thread 里捕获就晚了
    ctx = capture_current_context()

    def _run():
        conn = get_connection()
        try:
            from app.services.outline_generator import create_outline_draft
            create_outline_draft(conn, sim_id)
        except Exception as e:  # noqa: BLE001
            import logging
            logging.warning(f"outline async runner failed sim={sim_id}: {e}")
            # 把 outline 状态标 failed,前端轮询会看到
            try:
                from app.services.outline_generator import (
                    _mark_failed, get_outline_by_id,
                )
                # 找 outline_id(可能还没插入 — create_outline_draft 头部插占位)
                row = conn.execute(
                    "SELECT id FROM simulation_outlines WHERE simulation_id=?",
                    (sim_id,),
                ).fetchone()
                if row:
                    _mark_failed(conn, row["id"], f"{type(e).__name__}: {e}")
            except Exception as _inner:  # noqa: BLE001
                # 2026-06-02:不再静默吞 — 写失败状态再次失败时记录(僵尸 outline)
                import logging
                logging.getLogger(__name__).error(
                    f"outline 写 failed 状态时再次失败 sim={sim_id}:"
                    f" {type(_inner).__name__}: {_inner}",
                    exc_info=True,
                )
        finally:
            try:
                conn.close()
            except Exception as _close_err:  # noqa: BLE001
                # close 异常一般是连接已关 / SQLite 损坏 — log 不抛
                import logging
                logging.getLogger(__name__).warning(
                    f"outline worker close conn 异常 sim={sim_id}:"
                    f" {type(_close_err).__name__}: {_close_err}"
                )

    threading.Thread(
        target=ctx.run,   # ⚠ ctx.run 包装 _run,把 ContextVar 带进新 thread
        args=(_run,),
        daemon=True,
        name=f"outline-{sim_id[:8]}",
    ).start()


def _kick_off_outline_async(sim_id: str) -> str:
    """POST 路径调用:先 INSERT outline 占位 row(state='drafting'),
    再后台线程跑 LLM,立即返回 'drafting' 状态。

    Returns:
      outline state(通常 'drafting');异常时 'failed'
    """
    from app.db import get_connection
    from app.services.project_service import iso_now
    import uuid
    conn = get_connection()
    try:
        # 预先 INSERT 占位 outline 行(state='drafting'),
        # 让前端 GET /api/simulations/{id}/outline 立即能看到 drafting 状态(2s 轮询)
        # 注:create_outline_draft 内部会判断"已有 outline"抛 ValueError,
        # 所以我们这里不预 INSERT — 直接由后台线程的 create_outline_draft 自己插
        _outline_async_runner(sim_id)
        return "drafting"
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


# ============================================================
# POST 创建推演 + 异步 kick_off
# ============================================================

@router.post(
    "/projects/{project_id}/simulations",
    response_model=SimulationCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_create_simulation(
    project_id: str,
    req: CreateSimulationRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    try:
        # 顺序很重要:鉴权/资源前置条件 → 配额闸门 → 实际创建。
        # 这样跨用户(404)/角色不足(422)的语义错误优先于配额(429),
        # 避免"项目根本不属于你 / 你都没填够角色"还被告知"重塑度配额超限"的怪体验。

        # 1. 鉴权 + 资源存在性(404 if cross-user / not exist)
        get_project_or_403(conn, project_id, user.id)

        # 2. 业务前置条件:角色 ≥ 3,顺手把 snapshot 算出来(create_simulation 仍会再算一次,
        #    可接受 — 索引查询毫秒级,换路由代码可读性)
        build_characters_snapshot(conn, project_id)

        # 3. 滚雪球前文 ids 校验(Sprint 1.O):必须同项目 + done + 属于当前用户
        validate_context_simulation_ids(
            conn, project_id, user.id, req.context_simulation_ids or [],
        )

        # 4. 配额闸门:reshape_max_percent(资源容量类硬限,非 credit)
        enforce_reshape_quota(
            user.plan, req.reshape_percent,
            conn=conn, user_id=user.id,
        )

        # Sprint C.2(2026-05-13):前置 credit 余额粗检
        #   不预估具体多少(LLM 调用前 token 数未知),只防"明显跑不动"
        #   真实扣费在 simulation done 时按总 token 算(simulation_service.run_simulation)
        #   founder 档跳过(consume_credits 内部短路,但前置防止 free 用户跑不动还烧 LLM)
        if user.plan != "founder":
            balance = get_balance(conn, user.id)
            if balance.total <= 0:
                raise InsufficientCredits(
                    needed=1,
                    available=balance.total,
                    action="continuation",
                )

        # 5. 落库(Sprint 6.A2 M3.B:透传 mode 字段 / M6:透传 use_outline_first)
        sim_id = create_simulation(
            conn=conn,
            project_id=project_id,
            user_id=user.id,
            divergence=req.divergence,
            reshape_percent=req.reshape_percent,
            target_chars=req.target_chars,
            style=req.style,
            custom_style_hint=req.custom_style_hint,
            context_simulation_ids=req.context_simulation_ids,
            selected_counterfactual_ids=req.selected_counterfactual_ids,
            mode=req.mode,
            use_outline_first=req.use_outline_first,
            # Sprint 6.A2 M7.J(2026-05-20):中间态起点锚点(可选)
            anchor_event_id=req.anchor_event_id,
            # P2.A(2026-05-24):走向终章开关
            with_grand_finale=req.with_grand_finale,
            # P2.B(2026-05-24):叙事节奏档位
            narrative_pacing=req.narrative_pacing,
            # P0H.2(2026-05-24):每章字数
            chapter_size_chars=req.chapter_size_chars,
            # 阶段 3A(2026-06-02):用户选的伏笔继承列表(可空 / null = 默认全继承)
            inherited_foreshadow_ids=req.inherited_foreshadow_ids,
        )

        # Sprint C.1:credit 计费在 simulation 完成时由 simulation_service 内部触发
        # consume_credits(action="continuation", units=按 token 真扣);
        # 创建时不预扣(LLM 调用异步,token 数未知;走"完成时扣"模式)

        # Sprint 6.A2 M6(2026-05-20):outline-first 路径分支
        #   走 outline-first:**不立即 kick_off**,先生成 outline 草稿 → 用户审核 → 用户批准后再 kick_off
        #   走原路径(quick / evolution 无 outline):立即 kick_off
        # Sprint 6.A2 M6-fix2(2026-05-20):outline 生成异步化
        #   原来阻塞 ~30-60s 让前端"创建中..."卡半天;现在 INSERT 占位 row + 后台
        #   threading 跑 create_outline_draft,POST 立即返回 outline_state='drafting',
        #   前端立即跳转 OutlineReviewView,在那里看 2s 轮询的"AI 正在生成 outline"转圈页。
        outline_state_value = None
        if req.use_outline_first and req.mode == "evolution":
            outline_state_value = _kick_off_outline_async(sim_id)
        else:
            # 启动 worker(生产:asyncio task / 测试:同步 monkeypatch 直跑)
            simulation_service.kick_off(sim_id)

        # kick_off 后再读一遍状态:测试模式下可能已 done
        sim = get_simulation_or_404(conn, sim_id, user.id)
        return {
            "simulation_id": sim_id,
            "state": sim.state,
            "use_outline_first": req.use_outline_first and req.mode == "evolution",
            "outline_state": outline_state_value,
        }

    except QuotaExceeded as e:
        # 资源容量类硬限超限(reshape_percent 仅,continuation 已转 credit)
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
    except InsufficientCredits as e:
        # AI credit 不足(理论 simulation 走完成扣模式,这里 catch 兜底)
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
    except TooFewCharactersForSimulation as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "TOO_FEW_CHARACTERS", "message": str(e)},
        )
    except InvalidAnchorEvent as e:
        # Sprint 6.A2 M7.J(2026-05-20):起点锚点 event_id 不属于本项目 / 不存在
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "INVALID_ANCHOR_EVENT", "message": str(e)},
        )
    except ReshapeCharacterLimitExceeded as e:
        # Sprint 2.C 反事实第 1 维:已改角色数 > 当前 reshape % 上限
        # 前端据此提示用户:撤销一些反事实 / 提高 reshape /(若已 plan max)升级
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "RESHAPE_CHARACTER_LIMIT_EXCEEDED",
                "current": e.current,
                "limit": e.limit,
                "reshape_percent": e.reshape_percent,
                "message": str(e),
            },
        )
    except InvalidContextSimulations as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "code": "INVALID_CONTEXT_SIMULATIONS",
                "message": str(e),
                "invalid_ids": e.invalid_ids,
            },
        )
    except NoReadyUploadForTailExcerpt as e:
        # Sprint 3.A:末尾态创建推演前必须先有 ready upload(上传 + 抽图谱完成)。
        # 前端 toast:"请先在「作品文件」区上传作品并完成 AI 抽图谱"。
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "END_MODE_NO_UPLOAD", "message": str(e)},
        )
    except HTTPException:
        raise
    except ResourceNotFoundOrForbidden:
        raise   # 全局 handler → 404
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"create_simulation 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )


# ============================================================
# GET 列表(项目下所有推演)
# ============================================================

@router.get(
    "/projects/{project_id}/simulations",
    response_model=list[SimulationSummaryResponse],
)
def api_list_simulations(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    sims = list_simulations_for_project(conn, project_id, user.id)
    # M7.D-fix(2026-05-20):per-project 端点也注入 inheritance 字段,
    # 让 ProjectView 的"作品列表"Tab 也能展示"第 N 代接续"badge
    inheritance = compute_inheritance_metadata_for_user(sims)
    return [
        {
            **s.to_summary(),
            "inheritance_depth": inheritance.get(s.id, {}).get("depth", 0),
            "ancestors_chain": inheritance.get(s.id, {}).get("ancestors_chain", []),
        }
        for s in sims
    ]


# ============================================================
# GET 列表(当前用户跨项目所有推演 — Sprint 1.J 我的剧情线)
# ============================================================

@router.get(
    "/simulations",
    response_model=list[SimulationSummaryWithProjectResponse],
)
def api_list_user_simulations(
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """跨项目列出当前用户所有推演,按 created_at DESC。

    返回字段含 project_name(JOIN projects 一次查到),前端时间轴卡片
    直接渲染无需二次请求。无分页(YAGNI:super 一年最多 96 条)。
    """
    items = list_simulations_for_user(conn, user.id)
    # Sprint 6.A2 M7.D(2026-05-20)— 一次性算继承深度 + 祖先链,给前端 badge 用
    # helper 接受 list[(sim, name)] 形态(name 不参与算法)
    inheritance = compute_inheritance_metadata_for_user(items)
    return [
        {
            **sim.to_summary(),
            "project_name": project_name,
            "inheritance_depth": inheritance.get(sim.id, {}).get("depth", 0),
            "ancestors_chain": inheritance.get(sim.id, {}).get("ancestors_chain", []),
        }
        for sim, project_name in items
    ]


# ============================================================
# 续作家族树(2026-06-06)
# ============================================================

@router.get("/projects/{project_id}/simulation_family_tree")
def api_get_simulation_family_tree(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """项目下所有 sim 的家族树视图 — 滚雪球链 + 反事实组合批次。

    Returns:
      {
        "nodes": [
          {
            "id": str,                    # simulation_id
            "state": str,                 # queued/generating/completed/failed
            "mode": str,                  # quick / evolution
            "divergence": str,            # 用户的分歧点描述(节点 label)
            "narrative_chars": int,       # 字数(节点大小 metric)
            "created_at": str,            # ISO
            "completed_at": str | None,
            "parent_ids": list[str],      # context_simulation_ids
            "combination_run_id": str | None,  # 同批次的 sim 兄弟分组
            "tree_path": list[str] | None,     # 反事实组合树叶子路径
            "is_final_compilation": bool, # 是否独立合并产物
            "compiled_from_sim_id": str | None,  # 合并来源
            "with_grand_finale": bool,    # 走向终章标记
            "use_outline_first": bool,
            "inheritance_depth": int,     # 沿继承链推算(根=0)
          }
        ],
        "combination_runs": [             # 反事实组合批次元信息(给节点分组着色)
          {
            "id": str,
            "total_combinations": int,
            "state": str,
            "created_at": str,
            "selected_variables_count": int,
          }
        ],
        "stats": {
          "total_sims": int,
          "roots": int,                   # context_simulation_ids 为空的 sim 数
          "max_depth": int,               # 最深继承深度
          "combo_batches": int,
        }
      }
    """
    from app.services.simulation_service import list_simulations_for_project
    # list_simulations_for_project 内部已 get_project_or_403(项目不属于 user → 403)
    sims = list_simulations_for_project(conn, project_id, user.id)

    # 直接 SQL 拉 combination_run_id / tree_path_json — Simulation dataclass 没暴露这俩列
    combo_lookup_rows = conn.execute(
        "SELECT id, combination_run_id, tree_path_json FROM simulations WHERE project_id=?",
        (project_id,),
    ).fetchall()
    combo_lookup = {r["id"]: r for r in combo_lookup_rows}

    # 算 inheritance_depth(根=0)
    sim_by_id = {s.id: s for s in sims}
    depth_cache: dict[str, int] = {}

    def _depth(sid: str, seen: set[str]) -> int:
        if sid in depth_cache:
            return depth_cache[sid]
        if sid in seen:
            return 0  # 循环兜底(理论上不应存在)
        s = sim_by_id.get(sid)
        if not s:
            return 0
        parents = s.context_simulation_ids or []
        if not parents:
            depth_cache[sid] = 0
            return 0
        # 取最后一个 parent(直接父辈,见 经验值 35)
        direct_parent = parents[-1]
        d = _depth(direct_parent, seen | {sid}) + 1
        depth_cache[sid] = d
        return d

    import json as _json_local

    nodes: list[dict] = []
    for s in sims:
        # combination_run_id / tree_path_json 不在 Simulation dataclass,从 lookup 拿
        lookup_row = combo_lookup.get(s.id)
        combo_run_id_val = lookup_row["combination_run_id"] if lookup_row else None
        tree_path = None
        if lookup_row:
            try:
                tp_raw = _json_local.loads(lookup_row["tree_path_json"] or "null")
                if isinstance(tp_raw, list):
                    tree_path = tp_raw
            except (_json_local.JSONDecodeError, TypeError):
                pass

        nodes.append({
            "id": s.id,
            "state": s.state,
            "mode": s.mode,
            "divergence": (s.divergence or "")[:120],
            "narrative_chars": len(s.narrative or ""),
            "created_at": s.created_at,
            "completed_at": s.completed_at,
            "parent_ids": list(s.context_simulation_ids or []),
            "combination_run_id": combo_run_id_val,
            "tree_path": tree_path,
            "is_final_compilation": bool(s.is_final_compilation),
            "compiled_from_sim_id": s.compiled_from_sim_id,
            "with_grand_finale": bool(s.with_grand_finale),
            "use_outline_first": bool(s.use_outline_first),
            "inheritance_depth": _depth(s.id, set()),
        })

    # 拉同项目的所有 combination_runs(给前端按 batch 分组着色用)
    combo_rows = conn.execute(
        """SELECT id, total_combinations, state, created_at,
                  selected_variables_json
             FROM counterfactual_combination_runs
            WHERE project_id = ? AND user_id = ?
         ORDER BY created_at DESC""",
        (project_id, user.id),
    ).fetchall()
    combination_runs: list[dict] = []
    for r in combo_rows:
        try:
            sv = _json_local.loads(r["selected_variables_json"] or "[]")
            sv_count = len(sv) if isinstance(sv, list) else 0
        except (_json_local.JSONDecodeError, TypeError):
            sv_count = 0
        combination_runs.append({
            "id": r["id"],
            "total_combinations": r["total_combinations"],
            "state": r["state"],
            "created_at": r["created_at"],
            "selected_variables_count": sv_count,
        })

    max_depth = max((n["inheritance_depth"] for n in nodes), default=0)
    roots = sum(1 for n in nodes if not n["parent_ids"])

    return {
        "nodes": nodes,
        "combination_runs": combination_runs,
        "stats": {
            "total_sims": len(nodes),
            "roots": roots,
            "max_depth": max_depth,
            "combo_batches": len(combination_runs),
        },
    }


# ============================================================
# GET 单条详情
# ============================================================

@router.get(
    "/simulations/{simulation_id}",
    response_model=SimulationFullResponse,
)
def api_get_simulation(
    simulation_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    sim = get_simulation_or_404(conn, simulation_id, user.id)
    return sim.to_full()


@router.get(
    "/simulations/{simulation_id}/emotional_states",
    response_model=list[EmotionalStateResponse],
)
def api_list_emotional_states(
    simulation_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """Sprint 6.A2 路线图 #2(2026-05-22):前端"角色情绪曲线"可视化数据源。

    返回该 sim 全部 (character, scene) 情绪记录,前端按 character_id group + 8 色折线图。
    跨用户访问 → 404(复用 get_simulation_or_404,不暴露资源存在性)。
    数据为空时返 [] —— 不存在的 sim 与 evolution mode 还没跑的 sim 都返空,
    前端据此隐藏 section,不强出空态。
    """
    get_simulation_or_404(conn, simulation_id, user.id)
    return list_emotional_states_for_visualization(conn, simulation_id)


@router.get("/simulations/{simulation_id}/state_timeline")
def api_get_state_timeline(
    simulation_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """SP-4.1(2026-06-02):角色状态时间线 — 前端可视化数据源.

    返回:
      timelines: dict[character_id, list[snapshot dict]]
        每个 snapshot 含 scene_index / position / hp_status / status_note /
        emotion_vec / inventory / known_fact_ids
      characters: list[{id, name}] — 该 sim 出现过的角色

    数据源:character_state_snapshots 表(SP-4,每幕末写).
    无数据 → 返 timelines={}, characters=[](老 sim / 非 evolution mode).
    """
    sim = get_simulation_or_404(conn, simulation_id, user.id)
    from app.db import fetch_all as _fa
    from app.services.character_snapshot_service import (
        list_snapshots_for_character,
    )
    # 拉本 sim 出现过的所有角色 id + name
    try:
        char_rows = _fa(
            conn,
            "SELECT DISTINCT character_id, character_name "
            "FROM character_state_snapshots "
            "WHERE simulation_id=? "
            "ORDER BY character_name ASC",
            (simulation_id,),
        )
    except Exception:  # noqa: BLE001
        # 老库无表 → 降级返空
        return {"timelines": {}, "characters": [], "scene_count": 0}

    timelines: dict[str, list] = {}
    characters: list[dict] = []
    max_scene = -1
    for cr in char_rows:
        cid = cr["character_id"]
        cname = cr["character_name"]
        snaps = list_snapshots_for_character(conn, simulation_id, cid)
        timelines[cid] = snaps
        characters.append({"id": cid, "name": cname})
        for s in snaps:
            si = s.get("scene_index", -1)
            if isinstance(si, int) and si > max_scene:
                max_scene = si

    return {
        "timelines": timelines,
        "characters": characters,
        "scene_count": max_scene + 1 if max_scene >= 0 else 0,
        "simulation_id": simulation_id,
        "project_id": sim.project_id,
    }


# ============================================================
# SSE 流式进度
# ============================================================

@router.get("/simulations/{simulation_id}/stream")
def api_stream_simulation(
    simulation_id: str,
    token: str = Query(
        ...,
        description="短期 SSE token,通过 POST /api/auth/sse_token 换取",
    ),
) -> StreamingResponse:
    """SSE 流(Sprint 1.L 真推送 + token-in-URL)。

    why query token 而非 Bearer header:
      浏览器 EventSource API 不支持自定义 header,只能把 token 写网址。
      用 POST /api/auth/sse_token 换发的短期(15 分钟)token,带 aud='sse'。

    事件 schema(详见 simulation_service):
      snapshot / round_start / director_start / director_done /
      agent_start / agent_done / round_done / composing_start /
      composing_done / done / error / state_change / heartbeat(`:` 注释行)

    终态(done / error / cancelled)推完后服务端关闭连接。

    headers:
      Cache-Control: no-cache  —— 防代理缓存
      X-Accel-Buffering: no    —— 防 nginx 默认 buffer 把流憋住
    """
    # 1. 验 SSE token
    try:
        user_id = decode_sse_token(token)
    except JWTError:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_SSE_TOKEN", "message": "SSE token 无效或已过期"},
        )

    # 2. 鉴权 + 资源验证(用一次性短连接 conn,不放 dependency 因为 stream 长连)
    conn = get_connection()
    try:
        get_simulation_or_404(conn, simulation_id, user_id)
    finally:
        conn.close()

    # 3. 开流(stream_simulation_state 内部自己管 conn)
    return StreamingResponse(
        stream_simulation_state(simulation_id, user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ============================================================
# POST /api/simulations/{id}/resume — 断点续推(Sprint 1.P)
# ============================================================

@router.post(
    "/simulations/{simulation_id}/resume",
    response_model=SimulationCreatedResponse,
)
def api_resume_simulation(
    simulation_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """让 failed / 中断的 sim 接着第 N+1 轮跑。

    不扣额外 continuation 配额(原 sim 创建时已扣)。
    LLM 新调用产生的 cost 累积进 sim.cost_yuan。

    错误码:
      404 NOT_FOUND                  sim 不存在 / 跨用户
      409 SIMULATION_STILL_RUNNING   sim 正在 worker 跑中,等其结束
      422 SIMULATION_NOT_RESUMABLE   state=done(无需) / state=queued(等启动) / 无进度
    """
    try:
        resume_simulation(conn, simulation_id, user.id)
        # resume 后立刻读最新 state(测试 sync runner 模式下可能已 done)
        sim = get_simulation_or_404(conn, simulation_id, user.id)
        return {"simulation_id": simulation_id, "state": sim.state}
    except SimulationStillRunning as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "SIMULATION_STILL_RUNNING", "message": str(e)},
        )
    except SimulationNotResumable as e:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "SIMULATION_NOT_RESUMABLE", "message": str(e)},
        )
    except HTTPException:
        raise
    except ResourceNotFoundOrForbidden:
        raise
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "INTERNAL_ERROR",
                "message": f"resume 内部错误:{type(e).__name__}: {e}"[:300],
            },
        )


# ============================================================
# Sprint 6.A2 路线图 #5(2026-05-23):边写边干预 — 用户给下一幕塞 hint
# ============================================================

@router.post(
    "/simulations/{simulation_id}/inject_scene_hint",
    response_model=SceneHintResponse,
)
def api_inject_scene_hint(
    simulation_id: str,
    body: SceneHintRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """续写过程中给下一幕塞一条即时 hint(比如"让主角这里要爆发")。

    后端写入 simulations.pending_scene_hint;续写主循环下一幕开始前读取并清空
    (消耗式,只影响下一幕)。

    规则:
      - sim 必须存在且属于当前用户(404 跨用户)
      - sim 状态必须是非终态(queued / directing / composing);终态 done/failed/cancelled
        返回 accepted=False(不报错,前端 toast 提示"已结束无法干预")
      - hint 长度 1-300 字(schema 层验证)
      - 同时刻 sim 已有 pending_scene_hint → **后写覆盖前写**(用户最新输入优先)
        理由:用户主动改主意是常见场景;若想保留所有 hint,会让用户困惑"为啥我改了还是按旧 hint 走"

    错误码:
      404 NOT_FOUND          sim 不存在 / 跨用户
      422 VALIDATION_ERROR   hint 长度违反(schema 层抛)
    """
    # 验证 sim 存在 + 归属
    sim = get_simulation_or_404(conn, simulation_id, user.id)

    # 终态拒绝(不报错,返 accepted=False 让前端 toast 提示)
    if sim.state in ("done", "failed", "cancelled"):
        return {
            "accepted": False,
            "will_apply_to_scene": None,
            "hint_preview": None,
        }

    # 写入 pending_scene_hint(后写覆盖前写)
    from app.db import execute as db_execute
    db_execute(
        conn,
        "UPDATE simulations SET pending_scene_hint=? WHERE id=?",
        (body.hint.strip(), simulation_id),
    )
    conn.commit()

    # hotfix(2026-06-01):错位修复 — 之前 +1 实际上"当前正在跑的那幕"已 consume hint,
    # 用户提交时 hint 只能被下一幕 consume(主循环消费在 scene_start 之后立刻发生).
    # 正确数学:
    #   current_round = 已完成幕数(刚跑完 N 幕 → current_round=N → 即将开始第 N+1 幕)
    #   前端"AI 正在演第 N+1 幕"提示时,scene_picker 已 consume → 用户 hint 影响第 N+2 幕
    next_scene = (sim.current_round or 0) + 2

    return {
        "accepted": True,
        "will_apply_to_scene": next_scene,
        "hint_preview": body.hint.strip()[:60],
    }


# hotfix(2026-06-01):拉用户干预 hint 历史(给前端"提交记录"用)
@router.get("/simulations/{simulation_id}/chapters")
def api_get_simulation_chapters(
    simulation_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """运行时切章 — 不入库,每次按当前 narrative + 项目 chapter_size 区间算.

    返回:
      chapters: list[ChapterInfo],每条 {global_chapter_no, char_offset_start, char_offset_end, char_count, first_words}
      start_chapter_no: 本 sim 起始章号(沿继承链锁定)
      chapter_size_min/max: 项目级章节字数区间
      narrative_length: 本 sim narrative 字符数(供前端校验)

    sim 未跑完 / narrative 为空 → 返空 chapters list.
    """
    sim = get_simulation_or_404(conn, simulation_id, user.id)
    from app.services.narrative_chapterizer import chapterize
    from app.services.sim_chapter_helper import (
        _get_chapter_size_range_for_project,
        get_effective_start_chapter,
    )
    start_chap = get_effective_start_chapter(sim)
    cmin, cmax = _get_chapter_size_range_for_project(conn, sim.project_id)
    narrative = sim.narrative or ""
    chapters = chapterize(narrative, cmin, cmax, start_chapter_no=start_chap)
    return {
        "chapters": chapters,
        "start_chapter_no": start_chap,
        "chapter_size_min": cmin,
        "chapter_size_max": cmax,
        "narrative_length": len(narrative),
    }


@router.get("/simulations/{simulation_id}/final_work")
def api_get_simulation_final_work(
    simulation_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """读"走向终章 + 滚雪球合并"产物.

    2026-06-01 v2:合并产物现在是独立 sim 行(is_final_compilation=1).
    本 endpoint 兼容三种调用:
      1. 传入 source sim id → 查它是否有关联的独立合并 sim → 重定向语义(返合并 sim 内容)
      2. 传入合并 sim id 本身 → 直接返其 narrative
      3. 老 sim 的 final_compiled_narrative 字段(v1) → 降级兼容

    返回:
      compilation_sim_id: 独立合并 sim 的 id(供前端跳转到详情页)
      narrative / chapters / total_chars / ancestor_count / chapter_size_min/max
    """
    sim = get_simulation_or_404(conn, simulation_id, user.id)

    from app.services.narrative_chapterizer import chapterize
    from app.services.sim_chapter_helper import (
        _get_chapter_size_range_for_project,
    )

    compilation_sim: "Simulation | None" = None  # type: ignore[name-defined]
    if sim.is_final_compilation:
        # 调用者直接传的就是合并 sim
        compilation_sim = sim
    else:
        # 调用者传的是 source sim — 反查独立合并 sim
        try:
            from app.db import fetch_one as _fo
            row = _fo(
                conn,
                "SELECT id FROM simulations "
                "WHERE is_final_compilation=1 AND compiled_from_sim_id=? "
                "AND user_id=? LIMIT 1",
                (sim.id, user.id),
            )
            if row:
                compilation_sim = get_simulation_or_404(conn, row["id"], user.id)
        except sqlite3.OperationalError:
            # 老库无此列 — 走 v1 降级
            pass

    # v1 降级:合并 sim 找不到 → 看 source 的 final_compiled_narrative 字段
    narrative: str = ""
    compilation_id: str | None = None
    if compilation_sim:
        narrative = compilation_sim.narrative or ""
        compilation_id = compilation_sim.id
    elif sim.final_compiled_narrative:
        narrative = sim.final_compiled_narrative

    if not narrative:
        raise HTTPException(
            status_code=404,
            detail={
                "error_code": "FINAL_WORK_NOT_AVAILABLE",
                "message": "本推演未生成最终作品.最终作品需 ① 选择 走向终章 ② 是滚雪球续作(有前篇).",
            },
        )

    cmin, cmax = _get_chapter_size_range_for_project(conn, sim.project_id)
    chapters = chapterize(narrative, cmin, cmax, start_chapter_no=1)
    return {
        "compilation_sim_id": compilation_id,
        "narrative": narrative,
        "chapters": chapters,
        "total_chars": len(narrative),
        "ancestor_count": len(sim.context_simulation_ids or []),
        "chapter_size_min": cmin,
        "chapter_size_max": cmax,
    }


@router.get("/simulations/{simulation_id}/hint_history")
def api_get_hint_history(
    simulation_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """返回本 sim 所有已落地的用户干预 hint,按 scene_index 升序.

    数据源:simulation_scenes.user_hint_applied(消费时落库).
    无干预 → 返空 list,不报错.
    """
    get_simulation_or_404(conn, simulation_id, user.id)
    rows = fetch_all(
        conn,
        "SELECT scene_index, scene_name, user_hint_applied, created_at "
        "FROM simulation_scenes "
        "WHERE simulation_id=? AND user_hint_applied IS NOT NULL AND user_hint_applied != '' "
        "ORDER BY scene_index ASC",
        (simulation_id,),
    )
    hints = [
        {
            "scene_index": int(r["scene_index"]),
            "scene_label": f"第 {int(r['scene_index']) + 1} 幕",
            "scene_name": r["scene_name"],
            "hint": r["user_hint_applied"],
            "applied_at": r["created_at"],
        }
        for r in rows
    ]
    return {"hints": hints, "total": len(hints)}


# 把上面 endpoint 用到的 fetch_all 显式 import(本文件其他地方用 _fetch_all 别名)
from app.db import fetch_all  # noqa: E402


# ============================================================
# DELETE
# ============================================================

@router.delete(
    "/simulations/{simulation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,  # FastAPI 0.110 + Py3.13 把 `-> None` 推成 NoneType
)
def api_delete_simulation(
    simulation_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    delete_simulation(conn, simulation_id, user.id)


# ============================================================
# SP-9(2026-05-29):反事实分支并排对比
# ============================================================
#   GET /api/simulations/{a}/compare/{b}
#     返:两 sim metadata + counterfactual diff(common / only_a / only_b)
#         + scene_alignment(按 scene_index 对齐 simulation_scenes 行)
#     用途:用户对比"改了反事实 X 后,第 5 幕变了什么" — 灵魂续写差异化可视化

import json as _json
from app.db import fetch_all as _fetch_all
from app.models.counterfactual_change import CounterfactualChange


def _get_sim_meta_or_404(
    conn: sqlite3.Connection, sim_id: str, user_id: str,
) -> dict:
    """拉 sim 基本 metadata,鉴权(必属于该用户的项目)."""
    row = conn.execute(
        "SELECT s.id, s.project_id, s.state, s.divergence, s.reshape_percent, "
        "       s.rounds_planned, s.current_round, s.target_chars, s.style, "
        "       s.created_at, s.completed_at, s.cost_yuan, "
        "       p.name AS project_name "
        "FROM simulations s "
        "JOIN projects p ON p.id = s.project_id "
        "WHERE s.id=? AND p.user_id=?",
        (sim_id, user_id),
    ).fetchone()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "SIMULATION_NOT_FOUND",
                "message": f"推演 {sim_id} 不存在或无权访问",
            },
        )
    return dict(row)


def _list_scenes_for_sim(
    conn: sqlite3.Connection, sim_id: str,
) -> list[dict]:
    """拉 sim 全部 simulation_scenes,按 scene_index 升序."""
    rows = conn.execute(
        "SELECT scene_index, scene_name, scene_source, time_anchor, "
        "       characters_present_json, narrative_segment "
        "FROM simulation_scenes WHERE simulation_id=? ORDER BY scene_index ASC",
        (sim_id,),
    ).fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            chars = _json.loads(r["characters_present_json"] or "[]")
            if not isinstance(chars, list):
                chars = []
        except _json.JSONDecodeError:
            chars = []
        out.append({
            "scene_index": int(r["scene_index"]),
            "scene_name": r["scene_name"] or "",
            "scene_source": r["scene_source"] or "",
            "time_anchor": r["time_anchor"] or "",
            "characters_present": chars,
            "narrative_segment": r["narrative_segment"] or "",
        })
    return out


def _compute_counterfactual_diff(
    conn: sqlite3.Connection, project_id: str,
    sim_a_id: str, sim_b_id: str,
) -> dict:
    """对比两 sim 应用的反事实变量(基于 counterfactual_changes.applied_in_simulations_json).

    返回 {common, only_in_a, only_in_b},每条用 CounterfactualChange.to_response().
    """
    rows = _fetch_all(
        conn,
        "SELECT * FROM counterfactual_changes WHERE project_id=?",
        (project_id,),
    )
    common: list[dict] = []
    only_a: list[dict] = []
    only_b: list[dict] = []
    for r in rows:
        cf = CounterfactualChange.from_row(r)
        applied = cf.applied_simulation_ids
        in_a = sim_a_id in applied
        in_b = sim_b_id in applied
        if not (in_a or in_b):
            continue
        cf_dict = cf.to_response()
        # 加 target_name(character / event / relationship 的 name)— 给前端显示用
        cf_dict["target_name"] = _resolve_target_name(conn, cf.target_type, cf.target_id)
        if in_a and in_b:
            common.append(cf_dict)
        elif in_a:
            only_a.append(cf_dict)
        else:
            only_b.append(cf_dict)
    return {
        "common": common,
        "only_in_a": only_a,
        "only_in_b": only_b,
    }


def _resolve_target_name(
    conn: sqlite3.Connection, target_type: str, target_id: str,
) -> str:
    """character / event / relationship → name / description.world → ''."""
    if target_type == "character":
        row = conn.execute(
            "SELECT name FROM characters WHERE id=?", (target_id,),
        ).fetchone()
        return row["name"] if row else ""
    if target_type == "event":
        row = conn.execute(
            "SELECT description FROM events WHERE id=?", (target_id,),
        ).fetchone()
        return (row["description"] or "")[:60] if row else ""
    if target_type == "relationship":
        row = conn.execute(
            "SELECT source_id, target_id, type FROM relationships WHERE id=?",
            (target_id,),
        ).fetchone()
        if not row:
            return ""
        s_row = conn.execute(
            "SELECT name FROM characters WHERE id=?", (row["source_id"],),
        ).fetchone()
        t_row = conn.execute(
            "SELECT name FROM characters WHERE id=?", (row["target_id"],),
        ).fetchone()
        s = s_row["name"] if s_row else "?"
        t = t_row["name"] if t_row else "?"
        return f"{s} → {t}({row['type']})"
    return ""


def _align_scenes(
    scenes_a: list[dict], scenes_b: list[dict],
) -> list[dict]:
    """按 scene_index 对齐两 sim 的幕.

    Returns list of {scene_index, a, b, diff_kind}:
      diff_kind = "both" 两侧都有 | "only_a" 仅 a | "only_b" 仅 b
      若 both → 简单 similarity:narrative_segment len 差超 30% → "differ" 否则 "similar"
    """
    a_by_idx = {s["scene_index"]: s for s in scenes_a}
    b_by_idx = {s["scene_index"]: s for s in scenes_b}
    all_indices = sorted(set(a_by_idx.keys()) | set(b_by_idx.keys()))

    out: list[dict] = []
    for idx in all_indices:
        a = a_by_idx.get(idx)
        b = b_by_idx.get(idx)
        if a and b:
            len_a = len(a["narrative_segment"])
            len_b = len(b["narrative_segment"])
            similar = False
            if len_a and len_b:
                ratio = min(len_a, len_b) / max(len_a, len_b)
                # 双方都有内容 + scene_name 相同 + 字数比 ≥ 0.7 → 视为 similar
                similar = ratio >= 0.7 and a["scene_name"] == b["scene_name"]
            out.append({
                "scene_index": idx,
                "a": a,
                "b": b,
                "diff_kind": "both",
                "similar": similar,
            })
        elif a:
            out.append({
                "scene_index": idx, "a": a, "b": None,
                "diff_kind": "only_a", "similar": False,
            })
        else:
            out.append({
                "scene_index": idx, "a": None, "b": b,
                "diff_kind": "only_b", "similar": False,
            })
    return out


@router.get("/simulations/{sim_a_id}/compare/{sim_b_id}")
def api_compare_simulations(
    sim_a_id: str,
    sim_b_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """SP-9 反事实分支并排对比.

    流程:
      1. 鉴权两 sim(必须都属于该用户的项目)
      2. 校验同项目(跨项目无意义,400)
      3. 校验非自比(同 sim 无意义,400)
      4. 计算反事实变量 diff(基于 counterfactual_changes.applied_in_simulations_json)
      5. 拉两 sim 的 simulation_scenes 按 scene_index 对齐
      6. 返结构化对比数据
    """
    sim_a = _get_sim_meta_or_404(conn, sim_a_id, user.id)
    sim_b = _get_sim_meta_or_404(conn, sim_b_id, user.id)

    if sim_a["project_id"] != sim_b["project_id"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "COMPARE_CROSS_PROJECT",
                "message": "不能跨项目对比推演",
            },
        )

    if sim_a_id == sim_b_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "COMPARE_SELF",
                "message": "不能与自己对比",
            },
        )

    cf_diff = _compute_counterfactual_diff(
        conn, sim_a["project_id"], sim_a_id, sim_b_id,
    )
    scenes_a = _list_scenes_for_sim(conn, sim_a_id)
    scenes_b = _list_scenes_for_sim(conn, sim_b_id)
    alignment = _align_scenes(scenes_a, scenes_b)

    return {
        "sim_a": sim_a,
        "sim_b": sim_b,
        "counterfactual_diff": cf_diff,
        "scene_alignment": alignment,
        "stats": {
            "scenes_a_count": len(scenes_a),
            "scenes_b_count": len(scenes_b),
            "common_scene_count": sum(1 for x in alignment if x["diff_kind"] == "both"),
            "similar_scene_count": sum(1 for x in alignment if x.get("similar")),
        },
    }


# ============================================================
# SP-9 enhancement(2026-05-29 末)— LLM 因果分析对比
# ============================================================

@router.post("/simulations/{sim_a_id}/compare/{sim_b_id}/analyze")
def api_analyze_compare(
    sim_a_id: str,
    sim_b_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """LLM 因果分析两分支对比 — 复用 compare endpoint 内部 helper.

    流程:
      1. 复用 api_compare_simulations 的鉴权 + 数据组装(同函数)
      2. 把 compare_data 喂给 comparison_analyzer
      3. 返结构化分析报告

    失败兜底:LLM 抛 / 返非法 → service 内部返程序级 fallback,绝不 5xx.
    """
    # 复用现有 compare 函数(完全相同的鉴权 + 数据组装)
    compare_data = api_compare_simulations(
        sim_a_id, sim_b_id, user=user, conn=conn,
    )
    from app.services.comparison_analyzer import analyze_compare
    analysis = analyze_compare(compare_data)
    return analysis
