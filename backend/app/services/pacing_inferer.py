"""pacing_inferer — AI 推断项目叙事节奏档位(P2.B 升级,2026-05-24).

起源:用户反馈"叙事节奏应该 AI 根据原作分析自动决定,用户可改"。
旧版让用户手填 select(慢/标准/紧凑)— 不诚实(用户不懂原作节奏 metric)。
新版:项目首次创建续作时,**懒触发**本 service,LLM 分析原作头中尾三段采样,
推断 recommended_pacing(slow/standard/fast)+ reasoning + metrics,
落库到 projects 表 4 字段缓存,后续 sim 创建直接读用。

触发时机:simulation_service.create_simulation 顶部 hook 调用。
失败兜底:任何异常都 fallback "standard" + reasoning="AI 推断失败,默认标准节奏"
        — 绝不阻塞用户创建续作。

普适性:任何作品风格(物哀/硬科幻/武侠/古风)都靠采样实测指标判,
       不依赖 LLM 训练数据中对作品的预存印象。

created 2026-05-24 / P2.B 升级
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Optional

from app.db import execute, fetch_all, fetch_one
from app.services.llm_client import call_llm_json, LlmCallFailed, LlmJsonParseFailed
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)

# 采样大小(头/中/尾各取这么多字符)
_SAMPLE_CHARS = 1500

# 失败兜底返回值
_FALLBACK_RESULT = {
    "recommended_pacing": "standard",
    "reasoning": "AI 推断失败,默认标准节奏(你可在续作创建表单中手动改)",
    "metrics": {},
}


def _load_prompt() -> str:
    """读取 pacing_inferer prompt 文件。"""
    prompt_path = Path(__file__).parent.parent.parent.parent / "prompts" / "pacing_inferer.md"
    return prompt_path.read_text(encoding="utf-8")


def _sample_text(full_text: str, sample_chars: int = _SAMPLE_CHARS) -> dict[str, str]:
    """从全文切头/中/尾三段采样。

    若全文 < sample_chars * 3:三段拼起来就是全文(可能重叠,无害)。
    """
    n = len(full_text)
    if n <= sample_chars * 3:
        # 文本短,直接全给
        return {
            "head_excerpt": full_text[:sample_chars],
            "middle_excerpt": full_text[sample_chars : sample_chars * 2] if n > sample_chars else "",
            "tail_excerpt": full_text[-sample_chars:] if n > sample_chars * 2 else "",
        }

    head = full_text[:sample_chars]
    middle_start = (n - sample_chars) // 2
    middle = full_text[middle_start : middle_start + sample_chars]
    tail = full_text[-sample_chars:]
    return {
        "head_excerpt": head,
        "middle_excerpt": middle,
        "tail_excerpt": tail,
    }


def _get_full_text_for_project(
    conn: sqlite3.Connection,
    project_id: str,
) -> Optional[str]:
    """从项目最近 ready upload 取全文(解析后)。

    返回 None 表示无可用 upload(初始态项目可能空文件,或老项目 upload 被清理)。
    """
    # P2.B(修 import 路径,2026-05-24):
    #   parse_file 在 file_parser 模块,不在 upload_service
    #   settings 在 app.config,不在 app.core.config
    # 之前路径错 → ImportError 被外层 except Exception 吞掉 → 一直 fallback
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


def _validate_llm_result(raw: Any) -> dict:
    """校验 LLM 返回结构 — 不合法则抛 ValueError。

    必需字段:recommended_pacing(三档之一)+ reasoning(非空字符串)
    可选:metrics(dict)
    """
    if not isinstance(raw, dict):
        raise ValueError(f"LLM result not a dict: {type(raw)}")
    pacing = raw.get("recommended_pacing")
    if pacing not in ("slow", "standard", "fast"):
        raise ValueError(f"invalid recommended_pacing: {pacing!r}")
    reasoning = raw.get("reasoning", "")
    if not isinstance(reasoning, str) or not reasoning.strip():
        raise ValueError("reasoning empty or invalid")
    metrics = raw.get("metrics")
    if metrics is not None and not isinstance(metrics, dict):
        metrics = {}
    return {
        "recommended_pacing": pacing,
        "reasoning": reasoning.strip(),
        "metrics": metrics or {},
    }


def infer_pacing_for_project(
    conn: sqlite3.Connection,
    project_id: str,
) -> dict:
    """LLM 推断项目叙事节奏档位.

    流程:
      1. 从最近 ready upload 取全文 → 切头中尾采样
      2. 读 world_baseline(genre / setting / tone)作为辅助上下文
      3. 调 LLM(call_llm_json,DeepSeek 默认)→ JSON 输出
      4. 校验结构 → 返回 dict {recommended_pacing, reasoning, metrics}

    失败路径(任何阶段)→ 返回 _FALLBACK_RESULT,绝不抛异常给上游。

    返回:dict 必含 3 个 key:recommended_pacing / reasoning / metrics
    """
    try:
        full_text = _get_full_text_for_project(conn, project_id)
        if not full_text or len(full_text.strip()) < 200:
            logger.info(
                f"pacing_inferer: project {project_id} no usable upload "
                f"(text len={len(full_text or '')}), fallback standard"
            )
            return {
                **_FALLBACK_RESULT,
                "reasoning": "项目尚无可用原作上传或文本过短,默认标准节奏(上传作品后可重跑)",
            }

        samples = _sample_text(full_text)

        # 拉 world_baseline(可能 NULL / 部分字段缺)
        proj_row = fetch_one(
            conn,
            "SELECT world_baseline_json FROM projects WHERE id=?",
            (project_id,),
        )
        baseline_dict: dict[str, str] = {}
        if proj_row and proj_row["world_baseline_json"]:
            try:
                parsed = json.loads(proj_row["world_baseline_json"])
                if isinstance(parsed, dict):
                    # 只挑 3 个对节奏判定有用的字段
                    for key in ("genre", "setting", "tone"):
                        val = parsed.get(key)
                        if isinstance(val, str):
                            baseline_dict[key] = val
            except (json.JSONDecodeError, TypeError):
                pass

        user_input = {
            **samples,
            "world_baseline": baseline_dict,
        }

        system_prompt = _load_prompt()

        result, _usage = call_llm_json(
            system_prompt=system_prompt,
            user_input=user_input,
            max_tokens=1500,
            temperature=0.4,  # 节奏判定要稳定,降温
            retries=2,
            timeout=60.0,
        )

        validated = _validate_llm_result(result)
        logger.info(
            f"pacing_inferer: project {project_id} → "
            f"{validated['recommended_pacing']} "
            f"(reasoning {len(validated['reasoning'])} chars)"
        )
        return validated

    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"pacing_inferer: LLM call/parse failed for {project_id}: {e}")
        return _FALLBACK_RESULT
    except ValueError as e:
        logger.warning(f"pacing_inferer: LLM result invalid for {project_id}: {e}")
        return _FALLBACK_RESULT
    except Exception as e:  # noqa: BLE001 — 兜底任何异常
        logger.warning(f"pacing_inferer: unexpected error for {project_id}: {e}")
        return _FALLBACK_RESULT


def cache_pacing_to_project(
    conn: sqlite3.Connection,
    project_id: str,
    result: dict,
) -> None:
    """把 infer_pacing_for_project 返回结果写入 projects 表 4 字段。"""
    metrics_json = json.dumps(result.get("metrics") or {}, ensure_ascii=False)
    execute(
        conn,
        "UPDATE projects SET "
        "  inferred_pacing = ?, "
        "  inferred_pacing_reasoning = ?, "
        "  inferred_pacing_metrics_json = ?, "
        "  inferred_pacing_at = ? "
        "WHERE id = ?",
        (
            result["recommended_pacing"],
            result["reasoning"],
            metrics_json,
            iso_now(),
            project_id,
        ),
    )
    conn.commit()


def ensure_project_pacing_inferred(
    conn: sqlite3.Connection,
    project_id: str,
) -> str:
    """懒触发入口 — 给 simulation_service.create_simulation 顶部调用.

    流程:
      1. 查 project.inferred_pacing
      2. 已有值 → 直接返回(快路径,无 LLM 调用)
      3. NULL → 同步跑 infer_pacing_for_project + 缓存 → 返回新值

    返回:最终的 pacing 字符串('slow' / 'standard' / 'fast')
    任何错误下都返回 'standard'(失败兜底)
    """
    try:
        row = fetch_one(
            conn,
            "SELECT inferred_pacing, inferred_pacing_reasoning FROM projects WHERE id=?",
            (project_id,),
        )
        # P0O(2026-05-24):自愈缓存 — 老 fallback 数据(reasoning 含失败标记)视为无效
        # 因为 P2.B 升级初版有 import 路径 bug,所有项目都被缓存了 fallback 结果
        # 修复后重启时,这些缓存应该被识别为"无效"并重跑
        cached_pacing = row["inferred_pacing"] if row else None
        cached_reasoning = (row["inferred_pacing_reasoning"] if row else "") or ""
        is_fallback_cache = (
            "AI 推断失败" in cached_reasoning
            or "推断失败" in cached_reasoning
            or "无可用原作" in cached_reasoning
        )
        if (
            cached_pacing in ("slow", "standard", "fast")
            and not is_fallback_cache
        ):
            return cached_pacing

        # 首次,或老缓存是 fallback,跑推断
        if is_fallback_cache:
            logger.info(
                f"pacing_inferer: project {project_id} 老缓存是 fallback,重跑推断"
            )
        result = infer_pacing_for_project(conn, project_id)
        cache_pacing_to_project(conn, project_id, result)
        return result["recommended_pacing"]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"ensure_project_pacing_inferred failed: {e}")
        return "standard"


__all__ = [
    "infer_pacing_for_project",
    "cache_pacing_to_project",
    "ensure_project_pacing_inferred",
]
