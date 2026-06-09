"""分集规划 API — 阶段 8.4(MVP 规则版)+ 阶段 8.4+(多视角完整版,2026-06-08)。

Endpoints:
  POST /novels/{novel_id}/plan-episodes       (MVP)
    body: { target_minutes_per_ep? }
    → EpisodePlan(纯规则版,向后兼容旧前端)

  POST /novels/{novel_id}/plan-episodes-multi (完整版)
    body: { preset?, target_minutes_per_ep?, with_llm_titles? }
    → MultiPerspectivePlan + 4 维评分 + LLM logline 标题

跨用户隔离:user_id 经 Depends(get_current_user) 注入,透传 service。

完整版流水线(plan-episodes-multi):
  ① dramatic_curve_analyzer → 算每场张力 / cliffhanger / 候选切点
  ② multi_perspective_planner → 3 视角并行切集(rhythm / hook / arc)
  ③ episode_quality_scorer    → 每方案 4 维评分
  ④ episode_title_writer       → 推荐方案的标题 + 下集预告(LLM, BYOK)
  ⑤ 用评分重选 recommended_perspective(替代 Phase 3 启发式)
"""
from __future__ import annotations

import logging
from typing import Optional

import yaml as yamllib
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.deps import get_current_user, get_db
from app.models.user import User
from app.screenplay.services import (
    episode_planner,
    episode_plan_exporter,
    episode_plan_store,
    episode_quality_scorer,
    episode_title_writer,
    multi_perspective_planner,
    screenplay_store,
)
from fastapi.responses import Response
from urllib.parse import quote as urlquote

logger = logging.getLogger(__name__)

router = APIRouter(tags=["episodes"], dependencies=[Depends(get_current_user)])


# ============================================================
# MVP 规则版(阶段 8.4)— 保留不动,向后兼容
# ============================================================


class PlanEpisodesBody(BaseModel):
    target_minutes_per_ep: float = Field(
        default=3.0,
        ge=0.5,
        le=30.0,
        description="单集目标时长(分钟);短剧 2-3,长剧 8-12",
    )


@router.post("/novels/{novel_id}/plan-episodes")
def api_plan_episodes(
    novel_id: str,
    body: PlanEpisodesBody = PlanEpisodesBody(),
    user: User = Depends(get_current_user),
) -> dict:
    """对该 novel 的最新剧本做分集规划(MVP 规则版)。

    Errors:
      404 SCREENPLAY_NOT_FOUND  作品未生成剧本
      400 PLAN_FAILED           规划失败(yaml 解析 / 场景缺失)
    """
    try:
        plan = episode_planner.plan_episodes(
            novel_id,
            user_id=user.id,
            target_minutes_per_ep=body.target_minutes_per_ep,
        )
    except episode_planner.EpisodePlanError as e:
        msg = str(e)
        if "尚未生成剧本" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "SCREENPLAY_NOT_FOUND", "message": msg},
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PLAN_FAILED", "message": msg},
        )
    return episode_planner.to_dict(plan)


# ============================================================
# 完整版(阶段 8.4+ Phase 6)— 多视角 + 4 维评分 + LLM logline
# ============================================================


class PlanEpisodesMultiBody(BaseModel):
    preset: str = Field(
        default="short_drama",
        description=(
            "预设档:short_drama(2-3 min)/ long_drama(8-12 min)/"
            " anime(22-30 min)/ custom"
        ),
    )
    target_minutes_per_ep: Optional[float] = Field(
        default=None,
        ge=0.5,
        le=60.0,
        description="若 preset=custom 必填;其他档可选覆盖默认",
    )
    with_llm_titles: bool = Field(
        default=True,
        description=(
            "true=对推荐方案的 episodes 调 LLM 写 logline 标题 + 下集预告;"
            "false=保持规则标题(节省 token)"
        ),
    )
    apply_llm_titles_to_all_perspectives: bool = Field(
        default=False,
        description=(
            "true=对 3 个视角都调 LLM 写标题(3× LLM 成本);"
            "false=只对推荐方案(默认,经济)"
        ),
    )


@router.post("/novels/{novel_id}/plan-episodes-multi")
def api_plan_episodes_multi(
    novel_id: str,
    body: PlanEpisodesMultiBody = PlanEpisodesMultiBody(),
    user: User = Depends(get_current_user),
) -> dict:
    """对该 novel 跑完整版多视角分集规划。

    流水线:
      ① 多视角生成(rhythm + hook + arc)
      ② 每方案 4 维评分
      ③ 用评分选最佳方案
      ④ 对推荐方案的 episodes 调 LLM 写 logline + teaser(可选)
      ⑤ 返完整 plan + 评分

    Errors:
      404 SCREENPLAY_NOT_FOUND  作品未生成剧本
      400 PLAN_FAILED           规划失败(yaml 解析 / 场景缺失 / preset 无效)
    """
    try:
        plan = multi_perspective_planner.plan_with_perspectives(
            novel_id,
            user_id=user.id,
            preset=body.preset,
            target_minutes_per_ep=body.target_minutes_per_ep,
        )
    except multi_perspective_planner.MultiPerspectivePlanError as e:
        msg = str(e)
        if "尚未生成剧本" in msg:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "SCREENPLAY_NOT_FOUND", "message": msg},
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "PLAN_FAILED", "message": msg},
        )

    # 拉一次 yaml,后面 scorer / title_writer 都要用
    record = screenplay_store.get_latest_screenplay(novel_id, user_id=user.id)
    if record is None:
        # 上面 plan_with_perspectives 已校验过,这里防御
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "SCREENPLAY_NOT_FOUND", "message": "剧本不存在"},
        )
    try:
        parsed = yamllib.safe_load(record["yaml_text"]) or {}
    except yamllib.YAMLError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "YAML_PARSE_FAILED", "message": f"剧本 yaml 解析失败:{e}"},
        )
    scenes_yaml = parsed.get("scenes") or []
    characters_yaml = parsed.get("characters") or []

    # ② 每方案评分(用评分填回 plan)
    perspective_scores: dict[str, episode_quality_scorer.PlanQualityScores] = {}
    for persp in plan.perspectives:
        if not persp.episodes:
            continue
        try:
            scores = episode_quality_scorer.score_plan(
                persp.episodes,
                target_minutes_per_ep=plan.target_minutes_per_ep,
                cuts=persp.cuts,
                scenes_yaml=scenes_yaml,
                characters_yaml=characters_yaml,
            )
            perspective_scores[persp.perspective] = scores
            persp.aggregate_quality = scores.aggregate
            # 填每集 quality_score
            ep_score_map = {es.episode_number: es for es in scores.episode_scores}
            for ep in persp.episodes:
                es = ep_score_map.get(ep.episode_number)
                if es is not None:
                    ep.quality_score = es.quality
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "scoring perspective=%s failed: %s", persp.perspective, e,
            )

    # ③ 用评分重选 recommended_perspective(替代 Phase 3 启发式)
    if perspective_scores:
        best = episode_quality_scorer.pick_best_perspective(perspective_scores)
        if best is not None:
            plan.recommended_perspective = best

    # ④ LLM 标题(可选,只对推荐方案 或 全部视角)
    is_short_drama = body.preset == "short_drama"
    if body.with_llm_titles:
        if body.apply_llm_titles_to_all_perspectives:
            target_perspectives = plan.perspectives
        else:
            target_perspectives = [
                p for p in plan.perspectives
                if p.perspective == plan.recommended_perspective and p.episodes
            ]

        for persp in target_perspectives:
            if not persp.episodes:
                continue
            try:
                title_map = episode_title_writer.write_titles_and_teasers(
                    persp.episodes,
                    user_id=user.id,
                    novel_id=novel_id,
                    scenes_yaml=scenes_yaml,
                    characters_yaml=characters_yaml,
                    is_short_drama=is_short_drama,
                )
                if title_map:
                    episode_title_writer.apply_to_episodes(
                        persp.episodes, title_map, keep_rule_prefix=True,
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "title_writer perspective=%s failed: %s", persp.perspective, e,
                )

    # ⑤ 组装响应:plan.to_dict() + perspective_scores(给前端展示分数详情)
    response = plan.to_dict()
    response["perspective_scores"] = {
        name: scores.to_dict() for name, scores in perspective_scores.items()
    }
    return response


# ============================================================
# 元数据:可选档案 + 视角说明(给前端 hard-coded 备份)
# ============================================================


@router.get("/episodes/presets")
def api_get_presets() -> dict:
    """返预设档列表 + 视角说明。前端启动时拉一次。"""
    return {
        "presets": [
            {
                "key": key,
                "label": cfg["label"],
                "description": cfg["description"],
                "default_minutes": cfg["target_minutes"],
            }
            for key, cfg in multi_perspective_planner.PRESETS.items()
        ],
        "perspectives": [
            {
                "key": key,
                "label": multi_perspective_planner.PERSPECTIVE_LABELS[key],
                "description": multi_perspective_planner.PERSPECTIVE_DESCRIPTIONS[key],
            }
            for key in ("rhythm", "hook", "arc")
        ],
    }


# ============================================================
# 分集方案持久化(2026-06-09 新增)
#
# 用户报告:分集结果不持久化,退出 modal 全丢。升级为有状态业务实体。
#
#   POST   /novels/{novel_id}/episode-plans       保存方案
#   GET    /novels/{novel_id}/episode-plans       列出该小说所有方案
#   GET    /episode-plans/{plan_id}               单个方案详情
#   PATCH  /episode-plans/{plan_id}               改名
#   DELETE /episode-plans/{plan_id}               删除
# ============================================================


class SaveEpisodePlanBody(BaseModel):
    scheme_name: str = Field(..., min_length=1, max_length=80)
    preset: str = Field(..., min_length=1, max_length=32)
    target_minutes: float = Field(..., gt=0)
    # 完整 MultiPerspectivePlan 序列化结构 — 前端传刚跑完的 plan
    plan_data: dict = Field(...)


class RenameEpisodePlanBody(BaseModel):
    scheme_name: str = Field(..., min_length=1, max_length=80)


@router.post("/novels/{novel_id}/episode-plans")
def api_save_episode_plan(
    novel_id: str,
    body: SaveEpisodePlanBody,
    user: User = Depends(get_current_user),
    conn=Depends(get_db),
) -> dict:
    """保存一个分集方案 — 用户跑完一次分集后命名保存。

    Errors:
      403 NOVEL_NOT_OWNED       小说不属于该用户
      400 INVALID_NAME          方案名空 / 过长
    """
    try:
        plan_id = episode_plan_store.save_plan(
            conn,
            novel_id=novel_id,
            user_id=user.id,
            scheme_name=body.scheme_name,
            preset=body.preset,
            target_minutes=body.target_minutes,
            plan_data=body.plan_data,
        )
    except PermissionError:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NOVEL_NOT_OWNED", "message": "小说不存在或无权访问"},
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_NAME", "message": str(exc)},
        )
    return {"plan_id": plan_id}


@router.get("/novels/{novel_id}/episode-plans")
def api_list_episode_plans(
    novel_id: str,
    user: User = Depends(get_current_user),
    conn=Depends(get_db),
) -> dict:
    """列出该 novel 下该用户所有方案,按创建时间倒序。跨用户返空。"""
    plans = episode_plan_store.list_plans(
        conn, novel_id=novel_id, user_id=user.id,
    )
    return {
        "items": [episode_plan_store.to_summary_dict(p) for p in plans],
    }


@router.get("/episode-plans/{plan_id}")
def api_get_episode_plan(
    plan_id: str,
    user: User = Depends(get_current_user),
    conn=Depends(get_db),
) -> dict:
    """拿单个方案完整数据(含 plan_data 完整快照)。"""
    p = episode_plan_store.get_plan(conn, plan_id=plan_id, user_id=user.id)
    if not p:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "方案不存在或无权访问"},
        )
    return episode_plan_store.to_full_dict(p)


@router.patch("/episode-plans/{plan_id}")
def api_rename_episode_plan(
    plan_id: str,
    body: RenameEpisodePlanBody,
    user: User = Depends(get_current_user),
    conn=Depends(get_db),
) -> dict:
    """改方案名。"""
    try:
        ok = episode_plan_store.rename_plan(
            conn, plan_id=plan_id, user_id=user.id,
            new_name=body.scheme_name,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "INVALID_NAME", "message": str(exc)},
        )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "方案不存在或无权访问"},
        )
    return {"ok": True}


@router.delete("/episode-plans/{plan_id}")
def api_delete_episode_plan(
    plan_id: str,
    user: User = Depends(get_current_user),
    conn=Depends(get_db),
) -> dict:
    """删方案。"""
    ok = episode_plan_store.delete_plan(
        conn, plan_id=plan_id, user_id=user.id,
    )
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "方案不存在或无权访问"},
        )
    return {"ok": True}


# ============================================================
# 2026-06-09 P4 — 分集方案导出(fountain / txt / yaml)
#
#   GET /episode-plans/{plan_id}/export.{format}
#
# 复用现有导出 endpoint 模式(同 /screenplays/{id}/export.{format})。
# Content-Disposition 用 RFC 5987 UTF-8 编码 filename*= 支持中文。
# ============================================================


_EXPORT_FORMATS = {
    "fountain": ("application/octet-stream", "fountain"),
    "txt": ("text/plain; charset=utf-8", "txt"),
    "yaml": ("application/x-yaml; charset=utf-8", "yaml"),
}

_EXPORT_MODES = ("outline", "full", "script")


@router.get("/episode-plans/{plan_id}/export.{fmt}")
def api_export_episode_plan(
    plan_id: str,
    fmt: str,
    mode: str = "outline",
    user: User = Depends(get_current_user),
    conn=Depends(get_db),
):
    """导出分集方案为指定格式。

    Query:
      mode = outline | full | script
        - outline:仅大纲(集标题 + 钩子 + scene_id 列表)— 文件最小
        - full:大纲 + 每集嵌入完整剧本内容(动作 + 对白)
        - script:仅剧本(每集 # Episode 标题下直接放 scene 内容,无元信息)

    Errors:
      400 UNSUPPORTED_FORMAT  fmt 不在 fountain/txt/yaml 内
      400 UNSUPPORTED_MODE    mode 不在 outline/full/script 内
      404 NOT_FOUND           方案不存在或无权访问
    """
    if fmt not in _EXPORT_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "UNSUPPORTED_FORMAT",
                "message": f"不支持的格式: {fmt}(支持:fountain / txt / yaml)",
            },
        )
    if mode not in _EXPORT_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "code": "UNSUPPORTED_MODE",
                "message": f"不支持的导出模式: {mode}(支持:outline / full / script)",
            },
        )

    p = episode_plan_store.get_plan(conn, plan_id=plan_id, user_id=user.id)
    if not p:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "NOT_FOUND", "message": "方案不存在或无权访问"},
        )

    summary_dict = episode_plan_store.to_full_dict(p)
    plan_data = p.plan_data

    # mode=full/script 需要剧本本体 — 拉该 novel 最新一份剧本
    screenplay_dict = None
    if mode in ("full", "script"):
        try:
            sp = screenplay_store.get_latest_screenplay(p.novel_id, user.id)
        except Exception as exc:
            logger.warning("拉剧本失败 %s: %s", p.novel_id, exc)
            sp = None
        if sp and sp.get("yaml_text"):
            try:
                import yaml
                parsed = yaml.safe_load(sp["yaml_text"]) or {}
                if isinstance(parsed, dict):
                    screenplay_dict = parsed
            except Exception as exc:
                logger.warning("解析剧本 yaml 失败 %s: %s", p.novel_id, exc)
        # 没拿到 → exporter 自动降级 outline + 加 warning

    if fmt == "fountain":
        content = episode_plan_exporter.export_to_fountain(
            summary_dict, plan_data, mode=mode, screenplay_dict=screenplay_dict,
        )
    elif fmt == "txt":
        content = episode_plan_exporter.export_to_txt(
            summary_dict, plan_data, mode=mode, screenplay_dict=screenplay_dict,
        )
    else:  # yaml
        content = episode_plan_exporter.export_to_yaml(
            summary_dict, plan_data, mode=mode, screenplay_dict=screenplay_dict,
        )

    mime, ext = _EXPORT_FORMATS[fmt]
    safe_name = episode_plan_exporter.safe_filename(p.scheme_name, mode=mode)
    filename_utf8 = urlquote(f"{safe_name}.{ext}")
    disposition = f"attachment; filename*=UTF-8''{filename_utf8}"

    return Response(
        content=content.encode("utf-8"),
        media_type=mime,
        headers={"Content-Disposition": disposition},
    )
