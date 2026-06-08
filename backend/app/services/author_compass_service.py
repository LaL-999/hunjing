"""author_compass_service — P3 作者指南针 Agent(2026-05-26).

让续作真正"像川端 / 像村上",从抽象"风格 = literary"升级到作者级元信息。

双轨制(LLM-first):
  - 外部研究轨(analyze_external_for_project):
      输入:作家名 + 作品名
      LLM 用世界知识吐结构化作者画像(流派/年代/主题/风格标签/雷区)
  - 内部反推轨(analyze_internal_for_project):
      输入:原作文本分布采样(头中尾 + 中段 2 处 ~6000 字)
      LLM 反推"虚拟作者"量化参数(句长/对白率/感官比例/段落节奏/意象 top10/视角/基调)

设计同 pacing_inferer:
  - 失败兜底不阻塞主流程 — 任何 LLM 错误都返回 None / 写 failed status
  - 懒触发入口 ensure_compass_inferred 给 simulation_service / API 复用
  - 锁定后续作读 final_compass_json(用户最终拍板版本)

普适性:任何作品都适用 — 作家画像与作品类型 / 节奏档位正交。

created 2026-05-26 / P3 Day 1
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional

from app.db import execute, fetch_one
from app.models.author_compass import AuthorCompass
from app.services.llm_client import (
    LlmCallFailed,
    LlmJsonParseFailed,
    call_llm_json,
)
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)


# 采样大小(每段)— 5 段总共 ~7500 字,够 LLM 看出文风全貌
_INTERNAL_SAMPLE_CHARS = 1500
_INTERNAL_SAMPLE_COUNT = 5    # 头 / 1/4 / 中 / 3/4 / 尾


_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


# =============================================================================
# 数据库存取
# =============================================================================

def get_compass(
    conn: sqlite3.Connection,
    project_id: str,
) -> Optional[AuthorCompass]:
    """读项目的 author_compass 记录,不存在返 None。"""
    row = fetch_one(
        conn,
        "SELECT * FROM author_compass WHERE project_id=?",
        (project_id,),
    )
    if not row:
        return None
    return AuthorCompass.from_row(row)


def _create_compass_row(
    conn: sqlite3.Connection,
    project_id: str,
    author_name: Optional[str] = None,
    work_title: Optional[str] = None,
) -> AuthorCompass:
    """新建一条 author_compass 记录(pending 状态)。"""
    now = iso_now()
    compass_id = uuid.uuid4().hex
    execute(
        conn,
        """INSERT INTO author_compass (
            id, project_id, author_name, work_title,
            external_status, internal_status,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, 'pending', 'pending', ?, ?)""",
        (compass_id, project_id, author_name, work_title, now, now),
    )
    conn.commit()
    row = fetch_one(
        conn,
        "SELECT * FROM author_compass WHERE id=?",
        (compass_id,),
    )
    return AuthorCompass.from_row(row)


def _update_external(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    status: str,
    profile: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    """写入外部研究轨结果。"""
    profile_json = (
        json.dumps(profile, ensure_ascii=False) if profile is not None else None
    )
    execute(
        conn,
        """UPDATE author_compass SET
              external_profile_json = COALESCE(?, external_profile_json),
              external_status = ?,
              external_error = ?,
              external_at = ?,
              updated_at = ?
           WHERE project_id = ?""",
        (
            profile_json, status, error,
            iso_now() if status in ("done", "failed") else None,
            iso_now(), project_id,
        ),
    )
    conn.commit()


def _update_internal(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    status: str,
    metrics: Optional[dict] = None,
    error: Optional[str] = None,
) -> None:
    """写入内部反推轨结果。"""
    metrics_json = (
        json.dumps(metrics, ensure_ascii=False) if metrics is not None else None
    )
    execute(
        conn,
        """UPDATE author_compass SET
              internal_metrics_json = COALESCE(?, internal_metrics_json),
              internal_status = ?,
              internal_error = ?,
              internal_at = ?,
              updated_at = ?
           WHERE project_id = ?""",
        (
            metrics_json, status, error,
            iso_now() if status in ("done", "failed") else None,
            iso_now(), project_id,
        ),
    )
    conn.commit()


def update_compass_user_fields(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    author_name: Optional[str] = None,
    work_title: Optional[str] = None,
    final_compass: Optional[dict] = None,
    user_locked: Optional[bool] = None,
) -> Optional[AuthorCompass]:
    """用户改字段 / 锁定 — 只更新提供的字段。

    锁定时 final_compass 必须有值(API 层会校验),这里假设已校验过。
    """
    sets: list[str] = []
    params: list[Any] = []

    if author_name is not None:
        sets.append("author_name = ?")
        params.append(author_name)
    if work_title is not None:
        sets.append("work_title = ?")
        params.append(work_title)
    if final_compass is not None:
        sets.append("final_compass_json = ?")
        params.append(json.dumps(final_compass, ensure_ascii=False))
    if user_locked is not None:
        sets.append("user_locked = ?")
        params.append(1 if user_locked else 0)

    if not sets:
        return get_compass(conn, project_id)   # nothing to update

    sets.append("updated_at = ?")
    params.append(iso_now())
    params.append(project_id)

    execute(
        conn,
        f"UPDATE author_compass SET {', '.join(sets)} WHERE project_id = ?",
        params,
    )
    conn.commit()
    return get_compass(conn, project_id)


# =============================================================================
# 文本采样(内部反推用)
# =============================================================================

def _sample_text_distributed(
    full_text: str,
    sample_chars: int = _INTERNAL_SAMPLE_CHARS,
    count: int = _INTERNAL_SAMPLE_COUNT,
) -> list[str]:
    """从全文按均匀位置采样 count 段。

    位置:0%, 25%, 50%, 75%, 100%(头/四分之一/中/四分之三/尾)
    若全文 < sample_chars * count → 全文当一段返回。
    """
    n = len(full_text)
    if n <= sample_chars * count:
        return [full_text]

    positions = [int(n * i / (count - 1)) if count > 1 else 0 for i in range(count)]
    # 修正尾段防越界
    samples: list[str] = []
    for pos in positions:
        start = max(0, min(pos, n - sample_chars))
        samples.append(full_text[start:start + sample_chars])
    return samples


def _get_full_text_for_project(
    conn: sqlite3.Connection,
    project_id: str,
) -> Optional[str]:
    """从项目最近 ready upload 取全文(复用 pacing_inferer 同一逻辑)。"""
    from app.config import settings
    from app.services import file_parser

    row = fetch_one(
        conn,
        "SELECT storage_path, mime_type FROM uploads "
        "WHERE project_id=? AND state='ready' "
        "ORDER BY uploaded_at DESC LIMIT 1",
        (project_id,),
    )
    if not row:
        return None
    abs_path = settings.uploads_abs_dir / row["storage_path"]
    if not abs_path.exists():
        return None
    parse_result = file_parser.parse_file(abs_path, row["mime_type"])
    if not parse_result.success or not parse_result.text:
        return None
    return parse_result.text


# =============================================================================
# 双轨制 LLM 调用
# =============================================================================

def analyze_external_for_project(
    conn: sqlite3.Connection,
    project_id: str,
    author_name: Optional[str],
    work_title: Optional[str],
) -> tuple[Optional[dict], Optional[str]]:
    """外部研究轨 — LLM 用世界知识吐作家结构化画像.

    Returns:
      (profile_dict, error_msg)
      成功:(dict, None)
      失败:(None, error_msg)
    """
    if not author_name and not work_title:
        return None, "缺少作家名 / 作品名 — 至少填一项"

    try:
        system_prompt = _load_prompt("author_compass_external.md")
        user_input = {
            "author_name": author_name or "",
            "work_title": work_title or "",
        }
        result, _usage = call_llm_json(
            system_prompt=system_prompt,
            user_input=user_input,
            max_tokens=2500,
            temperature=0.3,   # 调研要稳定,降温
            retries=2,
            timeout=60.0,
        )
        if not isinstance(result, dict):
            return None, f"LLM 返回非 dict: {type(result).__name__}"
        # 简单校验有任一关键字段(不强求全有 — prompt 已说"不确定空着")
        keys = ("流派", "风格标签", "主题偏好", "雷区")
        if not any(k in result for k in keys):
            return None, "LLM 返回缺少必要字段(流派/风格标签/主题偏好/雷区 至少一个)"
        return result, None
    except (LlmCallFailed, LlmJsonParseFailed) as e:
        return None, f"LLM 调用失败:{e}"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"analyze_external failed sim project={project_id}: {e}")
        return None, f"未预期错误:{e}"


def analyze_internal_for_project(
    conn: sqlite3.Connection,
    project_id: str,
) -> tuple[Optional[dict], Optional[str]]:
    """内部反推轨 — LLM 读原作分布采样反推"虚拟作者"量化参数.

    Returns:
      (metrics_dict, error_msg)
    """
    try:
        full_text = _get_full_text_for_project(conn, project_id)
        if not full_text or len(full_text.strip()) < 500:
            return None, (
                f"原作文本过短(< 500 字)— 至少上传 500 字原作才能反推文风。"
                f"当前文本长度 {len(full_text or '')} 字。"
            )

        samples = _sample_text_distributed(full_text)
        system_prompt = _load_prompt("author_compass_internal.md")
        user_input = {
            "sample_excerpts": samples,
            "total_chars": len(full_text),
            "sample_count": len(samples),
        }
        result, _usage = call_llm_json(
            system_prompt=system_prompt,
            user_input=user_input,
            max_tokens=2500,
            temperature=0.3,
            retries=2,
            timeout=90.0,
        )
        if not isinstance(result, dict):
            return None, f"LLM 返回非 dict: {type(result).__name__}"
        # P4(2026-05-27)— 加"身体描写尺度"维度(灵魂续写关键 — 决定续作是否保留作家肉体感风骨)
        keys = (
            "句长", "对白率", "感官比例", "段落节奏",
            "意象偏好", "视角", "基调",
            "身体描写尺度",
        )
        if not any(k in result for k in keys):
            return None, (
                "LLM 返回缺少必要字段"
                "(句长/对白率/感官/段落/意象/视角/基调/身体描写尺度 至少一个)"
            )
        return result, None
    except (LlmCallFailed, LlmJsonParseFailed) as e:
        return None, f"LLM 调用失败:{e}"
    except Exception as e:  # noqa: BLE001
        logger.warning(f"analyze_internal failed project={project_id}: {e}")
        return None, f"未预期错误:{e}"


# =============================================================================
# 主入口
# =============================================================================

def analyze_compass_for_project(
    conn: sqlite3.Connection,
    project_id: str,
    author_name: Optional[str] = None,
    work_title: Optional[str] = None,
    *,
    force: bool = False,
) -> AuthorCompass:
    """双轨制主入口 — 两轨并行(实际同步串行)各跑,落库.

    Args:
      author_name / work_title: 用户填的元信息(可空,LLM 也能从原作猜)
      force: True = 即使已存在 done 记录也重跑;False = 已 done 就跳过

    锁定后(user_locked=1)即使 force=True 也不会重跑 — 用户已拍板,不应被覆盖。

    Returns:
      AuthorCompass 当前状态(可能某一轨成功 / 失败)
    """
    # 1. 拿或建记录
    compass = get_compass(conn, project_id)
    if compass is None:
        compass = _create_compass_row(
            conn, project_id, author_name, work_title,
        )
    else:
        # 已锁定 → 拒绝重跑
        if compass.user_locked:
            logger.info(
                f"analyze_compass: project {project_id} 已锁定,跳过重跑"
            )
            return compass
        # 已 done 且 not force → 跳过
        if (
            not force
            and compass.external_status == "done"
            and compass.internal_status == "done"
        ):
            return compass
        # 更新用户输入(可能用户改了名)
        if author_name is not None or work_title is not None:
            update_compass_user_fields(
                conn, project_id,
                author_name=author_name,
                work_title=work_title,
            )

    # 2. 外部研究轨
    _update_external(conn, project_id, status="running")
    profile, ext_err = analyze_external_for_project(
        conn, project_id, author_name or compass.author_name,
        work_title or compass.work_title,
    )
    if profile is not None:
        _update_external(conn, project_id, status="done", profile=profile)
    else:
        _update_external(conn, project_id, status="failed", error=ext_err)

    # 3. 内部反推轨
    _update_internal(conn, project_id, status="running")
    metrics, int_err = analyze_internal_for_project(conn, project_id)
    if metrics is not None:
        _update_internal(conn, project_id, status="done", metrics=metrics)
    else:
        _update_internal(conn, project_id, status="failed", error=int_err)

    return get_compass(conn, project_id)


def ensure_compass_inferred(
    conn: sqlite3.Connection,
    project_id: str,
) -> Optional[AuthorCompass]:
    """懒触发入口 — 给 simulation_service.create_simulation 顶部 hook 调用.

    流程:
      1. 查 author_compass.user_locked → True 直接返回(快路径)
      2. 双轨任一 done → 返回当前状态(不重跑)
      3. 双轨都未跑 → 同步跑 analyze_compass_for_project
    任何错误都不抛 — 返回当前状态或 None,绝不阻塞主流程。
    """
    try:
        compass = get_compass(conn, project_id)
        if compass and (
            compass.user_locked
            or compass.external_status in ("done", "failed")
            or compass.internal_status in ("done", "failed")
        ):
            # 已有记录(成功 / 失败 / 用户锁定)→ 不重跑
            return compass
        # 首次,跑一遍
        return analyze_compass_for_project(conn, project_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ensure_compass_inferred failed: {e}")
        return None


__all__ = [
    "get_compass",
    "update_compass_user_fields",
    "analyze_external_for_project",
    "analyze_internal_for_project",
    "analyze_compass_for_project",
    "ensure_compass_inferred",
]
