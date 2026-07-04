"""改编决策 API — PR#9(差异化创新)。

Endpoint:
  POST /scenes/propose-adaptation-decisions   —— 无状态,对内心独白返 3 备选 + 推荐
  POST /screenplays/{id}/apply-decisions       —— 有状态,把作者选择"确定生成"落到剧本

为什么独立 endpoint:
  - 改编决策**贵**(LLM 调用),只在用户主动请求时跑
  - demo 视频里这是核心画面:"AI 给你 3 选项,你来选"
  - PR#10 主流程串联时,内部调 service 不走 HTTP

apply-decisions(2026-07-03 修 bug):
  之前作者在面板里"选"了决策只存在前端本地 state,剧本正文纹丝不动 —— 选了等于没选。
  本 endpoint 把选择**确定性地**落到剧本元素上(用 compose 时已生成好的备选文本,
  无需再调 LLM),存为新版本(parent = 原剧本),前端切到新版即见正文变化。
"""
from __future__ import annotations

from datetime import datetime, timezone

import yaml as yamllib
from fastapi import Depends, APIRouter, HTTPException, status
from app.deps import get_current_user
from app.models.user import User
from pydantic import BaseModel, Field

from app.screenplay.services import screenplay_store
from app.screenplay.services.pipeline.adaptation_decision import (
    AdaptationDecisionError,
    propose_decisions,
)
from app.screenplay.services.pipeline.element_extractor import (
    CharacterRef,
    ScreenplayElement,
)

router = APIRouter(tags=["decisions"], dependencies=[Depends(get_current_user)])


# ============================================================
# Pydantic 模型
# ============================================================


class CharacterRefIn(BaseModel):
    id: str
    name: str
    aka: list[str] = Field(default_factory=list)


class SceneHeadingIn(BaseModel):
    int_ext: str
    location_name: str
    time_of_day: str


class ElementIn(BaseModel):
    type: str
    text: str
    character_name: str | None = None
    parenthetical: str | None = None
    is_inner_monologue: bool = False


class ProposeRequest(BaseModel):
    scene_summary: str
    scene_heading: SceneHeadingIn
    scene_text: str
    characters_in_scene: list[CharacterRefIn]
    elements: list[ElementIn]


class OptionOut(BaseModel):
    type: str
    text: str = ""
    pros: str = ""
    cons: str = ""
    rationale: str = ""


class DecisionOut(BaseModel):
    element_index: int
    original_text: str
    options: list[OptionOut]
    recommended: str


class ProposeResponse(BaseModel):
    decision_count: int
    decisions: list[DecisionOut]
    llm_usage: dict


# ============================================================
# Endpoint
# ============================================================


@router.post(
    "/scenes/propose-adaptation-decisions",
    response_model=ProposeResponse,
)
def api_propose_decisions(req: ProposeRequest) -> dict:
    """对场景中的内心独白生成 3 备选。

    Returns:
      - decision_count: 检出的内心独白条数(也是 decisions 数组长度)
      - decisions[]: 每条含 3 options(V.O./action_externalize/delete)+ recommended
    """
    if not req.elements:
        # 无元素 → 直接返空,不调 LLM
        return {"decision_count": 0, "decisions": [], "llm_usage": {"input_tokens": 0, "output_tokens": 0}}

    characters = [
        CharacterRef(id=c.id, name=c.name, aka=c.aka)
        for c in req.characters_in_scene
    ]
    elements = [
        ScreenplayElement(
            type=el.type,
            text=el.text,
            character_name=el.character_name,
            parenthetical=el.parenthetical,
            is_inner_monologue=el.is_inner_monologue,
        )
        for el in req.elements
    ]

    try:
        result = propose_decisions(
            scene_text=req.scene_text,
            scene_summary=req.scene_summary,
            scene_heading=req.scene_heading.model_dump(),
            characters_in_scene=characters,
            elements=elements,
        )
    except AdaptationDecisionError as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"code": "LLM_DECISION_FAILED", "message": str(e)},
        )

    return {
        "decision_count": result.decision_count(),
        "decisions": [
            {
                "element_index": d.element_index,
                "original_text": d.original_text,
                "options": [
                    {
                        "type": o.type,
                        "text": o.text,
                        "pros": o.pros,
                        "cons": o.cons,
                        "rationale": o.rationale,
                    }
                    for o in d.options
                ],
                "recommended": d.recommended,
            }
            for d in result.decisions
        ],
        "llm_usage": result.llm_usage,
    }


# ============================================================
# apply-decisions —— 把作者选择"确定生成"落到剧本(2026-07-03 修 bug)
# ============================================================


class ApplyDecisionsRequest(BaseModel):
    # decision_id → 选项 type(voiceover / action_externalize / subtext / symbolism / delete)
    choices: dict[str, str] = Field(default_factory=dict)


class ApplyChangeItem(BaseModel):
    scene_id: str = ""
    element_id: str = ""
    action: str = ""
    summary: str = ""


class ApplyDecisionsResponse(BaseModel):
    new_screenplay_id: str
    parent_screenplay_id: str
    applied_count: int
    skipped: list[str] = Field(default_factory=list)
    change_log: list[ApplyChangeItem] = Field(default_factory=list)
    yaml: str


# 每种手法落地后的一句话变更说明(供版本树 change_log 展示)
_CHANGE_SUMMARY: dict[str, str] = {
    "voiceover": "保留为画外音(V.O.),替换为打磨后的旁白文本",
    "action_externalize": "内心独白外化为可见动作",
    "subtext": "改写为含潜台词的台词",
    "symbolism": "以意象/空镜承载情绪",
    "delete": "删除该内心独白(留待后续场景体现)",
}


def _index_elements_by_id(screenplay: dict) -> dict[str, tuple[list, int]]:
    """element_id → (该 scene 的 elements 列表, 在列表中的下标)。"""
    lookup: dict[str, tuple[list, int]] = {}
    for scene in screenplay.get("scenes", []) or []:
        elements = scene.get("elements") or []
        for i, el in enumerate(elements):
            if isinstance(el, dict) and el.get("id"):
                lookup[el["id"]] = (elements, i)
    return lookup


def _rewrite_element(el: dict, option: dict, chosen_type: str) -> dict | None:
    """按选中的手法把 element 改写成新 dict;返回 None 表示应删除。

    只用 compose 时已生成好的 option['text'],**不调 LLM**(确定性、秒级)。
    无改写文本(subtext/symbolism 有时为空)→ 保持原元素不动,交由 skipped 记录。
    """
    eid = el.get("id")
    if chosen_type == "delete":
        return None

    text = (option.get("text") or "").strip()
    if not text:
        return el  # 无落地文本,保持原样(上层据此判 skip)

    if chosen_type == "voiceover":
        return {
            "type": "voiceover",
            "id": eid,
            "character_id": el.get("character_id") or "",
            "text": text,
            "adaptation_note": "作者选择:保留为画外音(V.O.)",
        }
    if chosen_type == "subtext":
        # 潜台词落为台词,需要说话人;沿用原 character_id,缺失则退化为动作行
        cid = el.get("character_id")
        if cid:
            return {"type": "dialogue", "id": eid, "character_id": cid, "text": text}
        return {"type": "action", "id": eid, "text": text}
    # action_externalize / symbolism → 动作行(不带说话人)
    return {"type": "action", "id": eid, "text": text}


def _apply_decisions_to_screenplay(
    screenplay: dict,
    choices: dict[str, str],
) -> tuple[int, list[dict], list[str]]:
    """就地把 choices 落到 screenplay。返回 (applied_count, change_log, skipped)。"""
    lookup = _index_elements_by_id(screenplay)
    now = datetime.now(timezone.utc).isoformat()
    applied = 0
    change_log: list[dict] = []
    skipped: list[str] = []
    delete_ids: set[str] = set()
    seen_elements: set[str] = set()   # 防同一元素被多条决策重复处理

    for dec in screenplay.get("adaptation_decisions", []) or []:
        did = dec.get("id")
        if not did or did not in choices:
            continue
        ctype = choices[did]
        option = next(
            (o for o in (dec.get("options") or []) if o.get("type") == ctype),
            None,
        )
        if option is None:
            skipped.append(f"{did}:该决策无 {ctype} 选项")
            continue
        eid = dec.get("element_id")
        loc = lookup.get(eid) if eid else None
        if loc is None:
            skipped.append(f"{did}:元素 {eid} 未找到(可能已被删除)")
            continue
        # 同一元素被多条决策命中 → 只认第一条(防重复计数 / 矛盾改写)
        if eid in seen_elements:
            skipped.append(f"{did}:元素 {eid} 已被另一条决策处理,跳过")
            continue

        elements, idx = loc
        new_el = _rewrite_element(elements[idx], option, ctype)

        # 无落地文本 → _rewrite_element 原样返回;不算 applied,记 skip
        if new_el is not None and new_el is elements[idx] and ctype != "delete":
            skipped.append(f"{did}:{ctype} 无具体改写文本,未落地")
            continue

        # 删除不能把本场删空(schema 要求 scene.elements ≥ 1)
        if new_el is None:
            remaining = [
                e for e in elements
                if isinstance(e, dict)
                and e.get("id") not in delete_ids
                and e.get("id") != eid
            ]
            if not remaining:
                skipped.append(f"{did}:删除会清空本场(至少留 1 个元素),已跳过")
                continue

        seen_elements.add(eid)
        dec["chosen"] = ctype
        dec["chosen_at"] = now
        scene_id = dec.get("scene_id", "") or ""
        if new_el is None:
            delete_ids.add(eid)
            change_log.append({
                "scene_id": scene_id, "element_id": eid,
                "action": "delete", "summary": _CHANGE_SUMMARY["delete"],
            })
        else:
            elements[idx] = new_el
            change_log.append({
                "scene_id": scene_id, "element_id": eid,
                "action": ctype, "summary": _CHANGE_SUMMARY.get(ctype, ctype),
            })
        applied += 1

    # 统一执行删除(element_id 全局唯一,扫所有 scene 过滤)
    if delete_ids:
        for scene in screenplay.get("scenes", []) or []:
            els = scene.get("elements")
            if els:
                scene["elements"] = [
                    e for e in els
                    if not (isinstance(e, dict) and e.get("id") in delete_ids)
                ]

    return applied, change_log, skipped


@router.post(
    "/screenplays/{screenplay_id}/apply-decisions",
    response_model=ApplyDecisionsResponse,
)
def api_apply_decisions(
    screenplay_id: str,
    body: ApplyDecisionsRequest,
    user: User = Depends(get_current_user),
) -> dict:
    """把作者在改编决策面板里的选择**确定性地**落到剧本正文,存为新版本。

    与 /optimize 的区别:optimize 是"把选择当参考再跑一遍 LLM 重排"(贵、可能改动全局);
    本 endpoint 只做**精准替换**已选元素(用 compose 阶段就生成好的备选文本),秒级、可预期。
    """
    if not body.choices:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "NO_CHOICES", "message": "未提交任何改编选择"},
        )

    record = screenplay_store.get_screenplay_by_id(screenplay_id, user_id=user.id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SCREENPLAY_NOT_FOUND", "message": "剧本不存在"},
        )

    try:
        screenplay = yamllib.safe_load(record["yaml_text"])
    except yamllib.YAMLError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "YAML_PARSE_FAILED", "message": str(e)},
        )
    if not isinstance(screenplay, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"code": "INVALID_SCREENPLAY", "message": "yaml 解析非 dict"},
        )

    applied, change_log, skipped = _apply_decisions_to_screenplay(
        screenplay, body.choices,
    )
    if applied == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "NOTHING_APPLIED",
                "message": "所选决策均无法落地(元素已删或无改写文本)",
                "skipped": skipped,
            },
        )

    new_yaml = yamllib.safe_dump(
        screenplay,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=120,
    )

    try:
        new_id = screenplay_store.save_screenplay(
            novel_id=record["novel_id"],
            user_id=user.id,
            yaml_text=new_yaml,
            stats=record.get("stats") or {},
            warnings=record.get("warnings") or [],
            failed_chapters=record.get("failed_chapters") or [],
            schema_version=record.get("schema_version") or "1.0",
            model_name=record.get("model_name"),
            parent_screenplay_id=screenplay_id,
            optimization_origin="apply_decisions",
            optimization_log={
                "change_log": change_log,
                "reasoning": f"作者手动应用了 {applied} 项改编决策(确定性落地,未调 LLM)。",
                "skipped": skipped,
            },
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SCREENPLAY_NOT_FOUND", "message": "剧本不存在"},
        )

    return {
        "new_screenplay_id": new_id,
        "parent_screenplay_id": screenplay_id,
        "applied_count": applied,
        "skipped": skipped,
        "change_log": change_log,
        "yaml": new_yaml,
    }
