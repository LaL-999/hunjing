"""Character 路由 — 嵌套创建/列出 + 顶层详情/更新/删除。

- POST   /api/projects/{project_id}/characters    嵌套(因需 project_id)
- GET    /api/projects/{project_id}/characters    嵌套(列出某项目所有)
- GET    /api/characters/{character_id}            顶层(详情)
- PATCH  /api/characters/{character_id}            顶层(部分更新)
- DELETE /api/characters/{character_id}            顶层(级联删 relationships)
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
from app.models.user import User
from app.schemas.character import (
    CharacterResponse,
    CreateCharacterRequest,
    UpdateCharacterRequest,
)
from app.schemas.simulation import CharacterEmotionsBySim
from app.services.emotional_state_tracker import (
    list_character_emotional_states_across_sims,
)
from app.services.project_service import (
    get_character_or_403,
    get_project_or_403,
    iso_now,
)
from app.services.quota_service import (
    QuotaExceeded,
    enforce_character_create_quota,
)

router = APIRouter()


@router.post(
    "/projects/{project_id}/characters",
    response_model=CharacterResponse,
    status_code=status.HTTP_201_CREATED,
)
def api_create_character(
    project_id: str,
    req: CreateCharacterRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    get_project_or_403(conn, project_id, user.id)  # 鉴权 + 确认项目存在

    try:
        # Sprint E.4:传 user.id 让闸门走快照优先(老用户老规则保护)
        enforce_character_create_quota(conn, project_id, user.plan, user_id=user.id)
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

    char_id = str(uuid.uuid4())
    now = iso_now()
    # Sprint 6.A2 INIT.3(2026-05-21):behavior_baseline → JSON;None → NULL(老数据兼容)
    baseline_json = (
        json.dumps(req.behavior_baseline.model_dump(), ensure_ascii=False)
        if req.behavior_baseline is not None
        else None
    )
    # SP-2(2026-05-28):角色驱动五件套 — secrets list[SecretItem] → JSON
    secrets_json = (
        json.dumps([s.model_dump() for s in req.secrets], ensure_ascii=False)
        if req.secrets
        else None
    )
    execute(
        conn,
        "INSERT INTO characters "
        "(id, project_id, name, identity, personality, quotes, no_go_list, "
        "position_x, position_y, position_z, color, created_at, updated_at, "
        "behavior_baseline_json, life_status, status_note, "
        "surface_goal, deep_need, fatal_blind_spot, arc_from_to, secret_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            char_id, project_id, req.name, req.identity, req.personality,
            json.dumps(req.quotes, ensure_ascii=False),
            json.dumps(req.no_go_list, ensure_ascii=False),
            req.position_x, req.position_y, req.position_z, req.color,
            now, now, baseline_json,
            req.life_status, req.status_note,  # P1.B
            req.surface_goal, req.deep_need, req.fatal_blind_spot,  # SP-2
            req.arc_from_to, secrets_json,
        ),
    )
    conn.commit()
    return asdict(Character(
        id=char_id, project_id=project_id, name=req.name,
        identity=req.identity, personality=req.personality,
        quotes=req.quotes, no_go_list=req.no_go_list,
        position_x=req.position_x, position_y=req.position_y, position_z=req.position_z,
        color=req.color, created_at=now, updated_at=now,
        behavior_baseline=(
            req.behavior_baseline.model_dump()
            if req.behavior_baseline is not None
            else None
        ),
        life_status=req.life_status,  # P1.B
        status_note=req.status_note,
        # SP-2:角色驱动五件套
        surface_goal=req.surface_goal,
        deep_need=req.deep_need,
        fatal_blind_spot=req.fatal_blind_spot,
        arc_from_to=req.arc_from_to,
        secrets=[s.model_dump() for s in (req.secrets or [])],
    ))


@router.get(
    "/projects/{project_id}/characters",
    response_model=list[CharacterResponse],
)
def api_list_characters(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    get_project_or_403(conn, project_id, user.id)
    rows = fetch_all(
        conn,
        "SELECT * FROM characters WHERE project_id=? ORDER BY created_at ASC",
        (project_id,),
    )
    return [asdict(Character.from_row(r)) for r in rows]


@router.get("/characters/{character_id}", response_model=CharacterResponse)
def api_get_character(
    character_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    return asdict(get_character_or_403(conn, character_id, user.id))


# Sprint 2.C 反事实变量:character PATCH 时跟踪 5 个推演影响字段
#   name / identity / personality / quotes / no_go_list 改 → 落 counterfactual_changes
#   position_x/y/z / color 改 → 不记(纯视觉,不影响 director / agent 推演)
#
# Sprint 6.A2 INIT.3(2026-05-21):behavior_baseline 故意**不**进 tracked 集合 —
#   它是 M4.3 引入的"硬性基线"(基础设定),不是 "what-if" 反事实变量;
#   改它 = 重新定义角色,不是假设;对齐 consistency_checker 语义。
_CHARACTER_TRACKED_FIELDS = {"name", "identity", "personality", "quotes", "no_go_list"}


@router.patch("/characters/{character_id}", response_model=CharacterResponse)
def api_update_character(
    character_id: str,
    req: UpdateCharacterRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    char = get_character_or_403(conn, character_id, user.id)
    updates = req.model_dump(exclude_unset=True)
    if not updates:
        return asdict(char)

    # === Sprint 2.C:先 record 反事实变量(track 5 个推演影响字段)===
    # 推演时 director 会读 active counterfactual_changes 编译成 prompt 上下文区
    from app.services.counterfactual_service import record_change
    char_dict = asdict(char)   # 已 parse 的 quotes/no_go_list 是 list
    for field, new_val in updates.items():
        if field not in _CHARACTER_TRACKED_FIELDS:
            continue
        old_val = char_dict.get(field)
        try:
            record_change(
                conn, char.project_id, "character", character_id,
                field, old_val, new_val, user.id,
            )
        except ValueError:
            # old == new(record_change 抛 "无需记录")— 静默跳过
            pass

    set_parts = []
    values: list = []
    for field, value in updates.items():
        if field in ("quotes", "no_go_list"):
            value = json.dumps(value, ensure_ascii=False)
        if field == "is_protagonist":
            value = 1 if value else 0
        # Sprint 6.A2 INIT.3(2026-05-21):behavior_baseline dict → JSON 列;None → SQL NULL
        if field == "behavior_baseline":
            db_field = "behavior_baseline_json"
            db_value = (
                json.dumps(value, ensure_ascii=False)
                if value is not None
                else None
            )
            set_parts.append(f"{db_field}=?")
            values.append(db_value)
            continue
        # B5.2(2026-05-27):aliases list → aliases_json 列(列名带 _json 后缀,migration 054)
        if field == "aliases":
            set_parts.append("aliases_json=?")
            values.append(json.dumps(value or [], ensure_ascii=False))
            continue
        # SP-2(2026-05-28):secrets list[dict] → secret_json TEXT 列
        if field == "secrets":
            set_parts.append("secret_json=?")
            if value is None:
                values.append(None)
            else:
                # Pydantic 已 model_dump,但安全起见再处理
                secrets_payload = [
                    s if isinstance(s, dict) else s.model_dump()
                    for s in value
                ]
                values.append(json.dumps(secrets_payload, ensure_ascii=False))
            continue
        set_parts.append(f"{field}=?")
        values.append(value)

    # Sprint 6.A1:用户手动改 is_protagonist 时,自动锁定 user_pinned=true
    # (后续 judger 重判不覆盖用户决定)
    if "is_protagonist" in updates:
        set_parts.append("protagonist_user_pinned=?")
        values.append(1)

    set_parts.append("updated_at=?")
    values.append(iso_now())
    values.append(character_id)

    execute(
        conn,
        f"UPDATE characters SET {', '.join(set_parts)} WHERE id=?",
        tuple(values),
    )
    conn.commit()

    # Sprint 6.A1:agent 档案 4 字段被改时,触发 counterfactual 联动 — 该角色 agent 档案
    # 修改后,enricher 不再自动重跑(改的是用户最终意志),但记 counterfactual_changes 已够
    # (反事实联动 hook 见 extract_service / counterfactual_service)

    return asdict(get_character_or_403(conn, character_id, user.id))


@router.delete(
    "/characters/{character_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,  # FastAPI 0.110 + Py3.13 把 `-> None` 推成 NoneType,触发 204 无 body 断言
)
def api_delete_character(
    character_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> None:
    """删角色 + 级联清理 events.participants 中此 id。

    why 不能靠 SQL 外键:events.participants 是 JSON 数组(`["c1","c2",...]`),
    SQLite 无法对 JSON 内的 id 加 ON DELETE CASCADE。必须 Python 层显式遍历清理,
    否则角色删了之后 events 还引用,前端 lookup 失败显 "?",LLM 输入也会带脏 id。

    relationships 表的 source_id / target_id 是 TEXT 列,已有 ON DELETE CASCADE
    自动级联(005 schema),无需手动处理。
    """
    char = get_character_or_403(conn, character_id, user.id)
    project_id = char.project_id

    # 1. 先把同项目下所有引用此角色的 events 的 participants 数组过滤掉这个 id
    event_rows = fetch_all(
        conn,
        "SELECT id, participants FROM events WHERE project_id=?",
        (project_id,),
    )
    for ev in event_rows:
        try:
            old_parts = json.loads(ev["participants"])
        except (TypeError, json.JSONDecodeError):
            old_parts = []
        new_parts = [pid for pid in old_parts if pid != character_id]
        if len(new_parts) != len(old_parts):
            execute(
                conn,
                "UPDATE events SET participants=? WHERE id=?",
                (json.dumps(new_parts, ensure_ascii=False), ev["id"]),
            )

    # 2. 删角色本身(relationships 走 SQL 外键自动级联)
    execute(conn, "DELETE FROM characters WHERE id=?", (character_id,))
    conn.commit()


# ============================================================
# Sprint 6.A2 FOCUS(2026-05-21):角色合并(给 AI 抽取归一错误兜底)
#   对齐 scene_extractor.merge_scenes 的语义
# ============================================================

from pydantic import BaseModel as _PydBM


class _MergeCharactersRequest(_PydBM):
    """body:把 source 合并到 target,返回 target 的最新 CharacterResponse。

    why body 而非 path:用户在 UI 上选两张卡(source = 当前选中 / target = 点击的另一张),
    都用 id 传更直观;同时校验同 project_id 也好做。
    """
    source_character_id: str
    target_character_id: str


@router.post(
    "/projects/{project_id}/characters/merge",
    response_model=CharacterResponse,
)
def api_merge_characters(
    project_id: str,
    req: _MergeCharactersRequest,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """把 source 角色合并到 target(主角面板的 ⊕ 合并入口)。

    使用场景:LLM 抽图谱时偶发把同一人物拆成两条 PERSON(如《挪威的森林》"我"
    vs "渡边" / 《雪国》"艺妓" vs "驹子")。用户在主角面板选源 + 目标 → 调本端点。

    错误码:
      403 PROJECT_FORBIDDEN — 项目不属于此用户
      404 CHARACTER_NOT_FOUND — source / target 不存在或不属于此项目
      409 MERGE_SELF — source == target
    """
    get_project_or_403(conn, project_id, user.id)   # 鉴权

    # 延迟 import:避免循环依赖(service 也可能在测试 path 之外)
    from app.services.character_merger import (
        merge_characters,
        CharacterNotFound,
        CharacterMergeConflict,
    )

    try:
        merged = merge_characters(
            conn,
            project_id_for_auth=project_id,
            source_character_id=req.source_character_id,
            target_character_id=req.target_character_id,
        )
    except CharacterMergeConflict as e:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail={"code": "MERGE_SELF", "message": str(e)},
        )
    except CharacterNotFound as e:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={"code": "CHARACTER_NOT_FOUND", "message": str(e)},
        )

    return asdict(merged)


# ============================================================
# Sprint 6.A1(2026-05-18):主角判定 + agent 档案补全端点
# ============================================================

@router.post("/projects/{project_id}/judge_protagonists")
def api_judge_protagonists(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """触发项目主角判定(4 维度评分 + 上限 PROTAGONIST_MAX)。

    使用场景:
      - 中间/末尾态:extract 完工后自动 hook 触发(用户也可手动重跑)
      - 初始态:用户手建角色后可手动触发(主要看候选评分,自己再勾选)

    幂等:可重复跑;protagonist_user_pinned=true 的角色不被覆盖。

    返回 judge_protagonists() 完整报告(含每个角色的 metrics_detail 给前端展示)。
    """
    get_project_or_403(conn, project_id, user.id)

    from app.services.protagonist_judger import judge_protagonists
    try:
        report = judge_protagonists(conn, project_id)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "JUDGE_FAILED",
                "message": f"{type(e).__name__}: {str(e)[:200]}",
            },
        )
    return report


@router.get(
    "/projects/{project_id}/protagonists",
    response_model=list[CharacterResponse],
)
def api_list_protagonists(
    project_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """拉项目主角列表(is_protagonist=1,按 score 倒序)。

    前端 ProtagonistWall 组件用此拉数据;空列表 = 还没 judge 或没人达标。
    """
    get_project_or_403(conn, project_id, user.id)
    from app.services.protagonist_judger import list_protagonists
    protagonists = list_protagonists(conn, project_id)
    return [asdict(p) for p in protagonists]


@router.post("/characters/{character_id}/enrich_agent_profile")
def api_enrich_agent_profile(
    character_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """对单个角色触发 agent 档案补全(LLM 补空字段)。

    使用场景:
      - 初始态:用户手建角色填了 name 后,点"AI 补全档案"按钮触发
      - 中间/末尾态:用户在 NodeEditDrawer 想重新补某角色档案时手动触发
      - 反事实改 character 属性时,前端可调此重新生成档案(单角色,不动其他)

    幂等:已填字段不会被覆盖。
    成本:每角色 ~¥0.001 DeepSeek V3 调用。
    """
    char = get_character_or_403(conn, character_id, user.id)

    from app.services.agent_profile_enricher import enrich_character
    try:
        report = enrich_character(conn, char)
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={
                "code": "ENRICH_FAILED",
                "message": f"{type(e).__name__}: {str(e)[:200]}",
            },
        )

    # 返回最新角色 + 补全报告
    refreshed = get_character_or_403(conn, character_id, user.id)
    return {
        "character": asdict(refreshed),
        "report": report,
    }


# ============================================================
# SP-2.1(2026-05-29):AI 推断单角色驱动五件套
# ============================================================
# 用户在角色 drawer 内"角色驱动"details 点"AI 推断驱动"触发
#   query ?overwrite=true → 覆盖用户已填字段;否则仅填入空字段(保留用户意志)
#   失败:service 内部兜底,绝不抛 5xx 给前端

@router.post("/characters/{character_id}/infer/drivers")
def api_infer_character_drivers(
    character_id: str,
    overwrite: bool = False,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> dict:
    """LLM 推断单角色驱动五件套 + 写入角色(默认不覆盖已填字段).

    overwrite=false(默认):AI 推断只填用户当前为空的字段,已填值保留
    overwrite=true:用户主动选"覆盖" → AI 推断结果全部替换
    """
    char = get_character_or_403(conn, character_id, user.id)
    from app.services.character_drivers_inferer import (
        infer_drivers_for_character,
        cache_drivers_to_character,
    )
    result = infer_drivers_for_character(conn, character_id)
    cache_drivers_to_character(conn, character_id, result, overwrite=overwrite)
    refreshed = get_character_or_403(conn, character_id, user.id)
    return {
        "character": asdict(refreshed),
        "reasoning": result.get("reasoning", ""),
        "applied_overwrite": overwrite,
        "raw_inferred": {
            "surface_goal": result.get("surface_goal", ""),
            "deep_need": result.get("deep_need", ""),
            "fatal_blind_spot": result.get("fatal_blind_spot", ""),
            "arc_from_to": result.get("arc_from_to", ""),
            "secrets": result.get("secrets", []),
        },
    }


# ============================================================
# Sprint 6.A2 路线图 #2 二期(2026-05-22)— 跨多次推演的角色情绪总览
# 给 ProjectView 角色卡展开 "情绪总览" 按钮用
# ============================================================

@router.get(
    "/projects/{project_id}/characters/{character_id}/emotional_states",
    response_model=list[CharacterEmotionsBySim],
)
def api_list_character_emotions_across_sims(
    project_id: str,
    character_id: str,
    user: User = Depends(get_current_user),
    conn: sqlite3.Connection = Depends(get_db),
) -> list[dict]:
    """列该角色在该项目所有 sim 的情绪记录,group by sim_id 输出。

    鉴权:project + character 都必须属于该用户(任一不属 → 404)
    返回顺序:sim 按 created_at DESC(最新推演在前)
    空数据返 [](该角色没参与过任何 evolution mode sim)
    """
    # 双重鉴权:project 属用户 + character 属该项目(get_character_or_403 内部 JOIN projects 校验)
    get_project_or_403(conn, project_id, user.id)
    char = get_character_or_403(conn, character_id, user.id)
    if char.project_id != project_id:
        # character 存在但不在该 project → 同样 404(不泄露其他项目的 character)
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail={
                "code": "NOT_FOUND",
                "resource": "character",
                "message": f"character {character_id} 不在项目 {project_id} 中",
            },
        )
    return list_character_emotional_states_across_sims(conn, project_id, character_id)
