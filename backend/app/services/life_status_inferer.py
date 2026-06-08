"""life_status_inferer — 角色生命/物理状态判定专用 agent(P0J,2026-05-24).

起源:之前把 life_status 判定塞进 build_graph.md 里(P0I.1),让 LLM 在每个 chunk 视野内
判定。**两个副作用**:
  1. prompt 变长 → LLM 注意力被分散 → **漏抽边缘 PERSON**(用户实测反馈)
  2. 单 chunk 视野判 life_status 不可靠 — chunk 1 看到角色活着,chunk 8 看到死亡,
     虽然 _merge_graphs 取最严重,但 chunk 内**死亡线索高度集中**才有效;实战
     LLM 容易在单 chunk 视野内填 alive(因为该 chunk 只看到部分对话场景)

P0J 拆分方案(用户拍板 2026-05-24,"成本翻倍没关系,要求几乎全部角色不漏抽"):
  - build_graph 回归纯实体抽取(P0J.1 已删 life_status 相关段)
  - 本 service 独立跑:**每个 PERSON 按角色名扫全文,前/中/末分布采样,LLM 专门判生死**
  - 末尾 5 段保底,确保看到死亡章节
  - LLM 看到的是"该角色的最终状态采样",而非"某 chunk 的局部视野"

工作流:
  1. extract_service 跑完 build_graph + 写库 characters 表后,调本 service
  2. 对每个 PERSON 角色:
     a. 扫全文找角色名出现的所有位置(positions[])
     b. 前 / 中 / 末分布采样(末尾 5 段保底)
     c. 喂 LLM prompts/life_status_inferer.md
     d. LLM 输出 {life_status, status_note, reasoning}
     e. 更新 characters 表(只更非默认值,避免覆盖用户手改)
  3. 失败不阻塞:单角色失败 log warning + 该角色保 alive,继续下一个

成本:每角色 1 次 LLM,~5-10 分钱;30-50 角色项目共 ¥2-5。用户已批准。

普适性:任何作品(物哀/硬科幻/武侠/现代)都用同一逻辑,只看 samples 实际证据。

created 2026-05-24 / P0J.3
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Callable, Optional

from app.db import execute, fetch_all, fetch_one
from app.services.llm_client import (
    LlmCallFailed,
    LlmJsonParseFailed,
    call_llm_json,
)
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)

# 采样分布(总 9 段;末尾 5 段保底覆盖死亡章节)
_FRONT_SAMPLES = 2
_MIDDLE_SAMPLES = 2
_TAIL_SAMPLES = 5
# 单段采样字数(前/中各 ±300 字窗口,末段 ±500 字窗口)
_SAMPLE_WINDOW_FRONT = 300
_SAMPLE_WINDOW_TAIL = 500


def _load_prompt() -> str:
    """读取 prompts/life_status_inferer.md。"""
    prompt_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "prompts" / "life_status_inferer.md"
    )
    return prompt_path.read_text(encoding="utf-8")


def _load_full_text(conn: sqlite3.Connection, project_id: str) -> Optional[str]:
    """拉项目所有 ready upload 的全文(拼接)。无 upload 返 None."""
    from app.config import settings
    from app.services import file_parser

    rows = fetch_all(
        conn,
        "SELECT storage_path, mime_type FROM uploads "
        "WHERE project_id=? AND state='ready' "
        "ORDER BY uploaded_at ASC",
        (project_id,),
    )
    if not rows:
        return None

    parts: list[str] = []
    for r in rows:
        try:
            abs_path = settings.uploads_abs_dir / r["storage_path"]
            result = file_parser.parse_file(abs_path, r["mime_type"])
            if result.success and result.text:
                parts.append(result.text)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"life_status_inferer: parse {r['storage_path']}: {e}")

    if not parts:
        return None
    return "\n\n".join(parts)


def _build_samples(
    full_text: str, character_name: str,
) -> list[dict]:
    """从全文按角色名扫所有出现位置,前/中/末分布采样.

    返回:list of {position_index, stage, text}
    - stage:"front" | "middle" | "tail"
    - 末尾 5 段保底
    - 总段数最多 9(2 前 + 2 中 + 5 末);角色出场少时按实际数量返回
    """
    # 1. 找所有 character_name 出现位置
    positions: list[int] = []
    cursor = 0
    while True:
        pos = full_text.find(character_name, cursor)
        if pos == -1:
            break
        positions.append(pos)
        cursor = pos + len(character_name)

    if not positions:
        return []  # 完全没出现 → 上游会判 unknown

    n = len(positions)

    # 2. 决定采样位置 — 末尾 5 段保底,前/中各 2 段
    if n <= _FRONT_SAMPLES + _MIDDLE_SAMPLES + _TAIL_SAMPLES:
        # 出场少 → 全取(可能不足 9 段)
        # 简化:全数标 tail(信息更重)
        sampled = [(i, "tail" if i >= n - _TAIL_SAMPLES else "front") for i in range(n)]
    else:
        # 末尾 5 段
        tail_indices = list(range(n - _TAIL_SAMPLES, n))
        # 前 2 段
        front_indices = list(range(_FRONT_SAMPLES))
        # 中 2 段(在 (前段后, 末段前) 之间均匀取)
        mid_start = _FRONT_SAMPLES
        mid_end = n - _TAIL_SAMPLES
        mid_range = mid_end - mid_start
        if mid_range >= _MIDDLE_SAMPLES:
            mid_indices = [
                mid_start + round(i * (mid_range - 1) / (_MIDDLE_SAMPLES - 1))
                for i in range(_MIDDLE_SAMPLES)
            ]
        else:
            mid_indices = list(range(mid_start, mid_end))

        sampled = (
            [(i, "front") for i in front_indices]
            + [(i, "middle") for i in mid_indices]
            + [(i, "tail") for i in tail_indices]
        )

    # 3. 截窗 + 防重叠
    samples: list[dict] = []
    seen_starts: list[int] = []
    for pos_idx, stage in sampled:
        pos = positions[pos_idx]
        window = _SAMPLE_WINDOW_TAIL if stage == "tail" else _SAMPLE_WINDOW_FRONT
        start = max(0, pos - 100)
        # 防重叠(<100 字窗距视为重叠)
        if any(abs(start - s) < 100 for s in seen_starts):
            continue
        end = min(len(full_text), pos + window)
        snippet = full_text[start:end].strip()
        if snippet:
            samples.append({
                "position_index": pos_idx,
                "stage": stage,
                "text": snippet,
            })
            seen_starts.append(start)
    return samples


def _validate_llm_result(raw: object) -> Optional[dict]:
    """校验 LLM 输出 — 不合法返 None,合法返 {life_status, status_note, reasoning}."""
    if not isinstance(raw, dict):
        return None
    ls = raw.get("life_status")
    if ls not in ("alive", "deceased", "in_facility", "absent", "unknown"):
        return None
    note = raw.get("status_note", "") or ""
    if not isinstance(note, str):
        note = ""
    reasoning = raw.get("reasoning", "") or ""
    if not isinstance(reasoning, str):
        reasoning = ""
    return {
        "life_status": ls,
        "status_note": note.strip()[:200],
        "reasoning": reasoning.strip()[:500],
    }


def infer_one_character(
    conn: sqlite3.Connection,
    project_id: str,
    character_name: str,
    full_text: Optional[str] = None,
) -> dict:
    """对单个角色跑生命状态判定 LLM.

    Args:
      character_name: 角色名(用于扫全文找出场位置)
      full_text: 可选预拉全文(批量调用时复用)

    Returns: {life_status, status_note, reasoning, error}
      失败时 error 非 None,life_status 默认 'unknown'
    """
    if full_text is None:
        full_text = _load_full_text(conn, project_id)
    if not full_text:
        return {
            "life_status": "unknown",
            "status_note": "",
            "reasoning": "项目无可用原作上传,无法判定",
            "error": "no_upload",
        }

    samples = _build_samples(full_text, character_name)
    if not samples:
        return {
            "life_status": "unknown",
            "status_note": "",
            "reasoning": f"角色 '{character_name}' 在原文中未出现,可能仅在 description 中被提及",
            "error": None,
        }

    try:
        system_prompt = _load_prompt()
    except Exception as e:  # noqa: BLE001
        return {
            "life_status": "unknown",
            "status_note": "",
            "reasoning": f"prompt 加载失败:{e}",
            "error": f"prompt_load: {type(e).__name__}",
        }

    user_input = {
        "character_name": character_name,
        "samples": samples,
    }

    try:
        parsed, _usage = call_llm_json(
            system_prompt=system_prompt,
            user_input=user_input,
            max_tokens=600,
            temperature=0.3,  # 判生死要稳定,降温
            retries=2,
            timeout=45.0,
        )
    except (LlmCallFailed, LlmJsonParseFailed) as e:
        logger.warning(f"life_status_inferer: LLM 失败 for {character_name}: {e}")
        return {
            "life_status": "unknown",
            "status_note": "",
            "reasoning": f"LLM 调用失败,默认 unknown:{type(e).__name__}",
            "error": f"llm_failed: {type(e).__name__}",
        }

    validated = _validate_llm_result(parsed)
    if validated is None:
        logger.warning(
            f"life_status_inferer: LLM 输出非法 for {character_name}: {parsed}"
        )
        return {
            "life_status": "unknown",
            "status_note": "",
            "reasoning": "LLM 输出格式不合法,默认 unknown",
            "error": "invalid_output",
        }

    return {**validated, "error": None}


def infer_all_characters_in_project(
    conn: sqlite3.Connection,
    project_id: str,
    *,
    force_overwrite: bool = False,
    emit_progress: Optional[Callable[[str, str, int, int], None]] = None,
) -> dict:
    """批量给项目所有 PERSON 角色判定 life_status.

    Args:
      force_overwrite: True = 覆盖已有 life_status(用户手动重抽);
                       False(默认)= 只补 life_status='alive' 且 status_note='' 的(初始抽取后的默认值)
                       — 避免覆盖用户已手改的状态
      emit_progress: P0K.1(2026-05-24)进度回调,每完成 1 个角色触发一次。
                     签名:(character_name, life_status, current_idx, total_count) -> None
                     用途:extract_service hook 传入 _emit 来打破 SSE 死寂,
                     防止前端"卡 UI"

    返回:{inferred_count, skipped_count, errors_count, results: [...]}
    """
    rows = fetch_all(
        conn,
        "SELECT id, name, life_status, status_note FROM characters "
        "WHERE project_id=?",
        (project_id,),
    )
    if not rows:
        return {"inferred_count": 0, "skipped_count": 0, "errors_count": 0, "results": []}

    # 预拉全文(批量复用,避免重复 parse)
    full_text = _load_full_text(conn, project_id)
    if not full_text:
        return {
            "inferred_count": 0,
            "skipped_count": len(rows),
            "errors_count": 0,
            "results": [],
            "warning": "no_upload",
        }

    inferred = 0
    skipped = 0
    errors = 0
    results: list[dict] = []
    total = len(rows)  # P0K.1:总数(给 emit_progress 用)

    for idx, r in enumerate(rows):
        char_id = r["id"]
        name = r["name"]
        cur_status = r["life_status"] or "alive"
        cur_note = r["status_note"] or ""

        # 跳过已被用户改过的(非默认值)
        if not force_overwrite:
            if cur_status != "alive" or cur_note != "":
                skipped += 1
                results.append({
                    "character_id": char_id,
                    "name": name,
                    "skipped": True,
                    "reason": "已有非默认 life_status,跳过",
                })
                # P0K.1:即便跳过也发进度事件(保持 SSE 通道活跃)
                if emit_progress:
                    try:
                        emit_progress(name, cur_status, idx + 1, total)
                    except Exception:  # noqa: BLE001
                        pass
                continue

        try:
            result = infer_one_character(conn, project_id, name, full_text=full_text)
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"life_status_inferer: 角色 {name} 异常: {e}",
                exc_info=True,
            )
            errors += 1
            # P0K.1:错误也发进度,防 SSE 死寂
            if emit_progress:
                try:
                    emit_progress(name, "unknown", idx + 1, total)
                except Exception:  # noqa: BLE001
                    pass
            continue

        if result.get("error") and result["error"] not in (None, "no_upload"):
            errors += 1

        # 写库
        try:
            execute(
                conn,
                "UPDATE characters SET life_status=?, status_note=?, updated_at=? "
                "WHERE id=?",
                (
                    result["life_status"],
                    result["status_note"],
                    iso_now(),
                    char_id,
                ),
            )
            conn.commit()
            inferred += 1
            results.append({
                "character_id": char_id,
                "name": name,
                **result,
            })
            # P0K.1:成功 emit 进度事件 — 保持 SSE 通道活跃 + UI 可视化
            if emit_progress:
                try:
                    emit_progress(name, result["life_status"], idx + 1, total)
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:  # noqa: BLE001
            logger.error(f"life_status_inferer: 写库失败 for {name}: {e}")
            errors += 1

    logger.info(
        f"life_status_inferer: project {project_id} "
        f"推断 {inferred} 个 / 跳过 {skipped} 个 / 错 {errors} 个"
    )
    return {
        "inferred_count": inferred,
        "skipped_count": skipped,
        "errors_count": errors,
        "results": results,
    }


__all__ = [
    "infer_one_character",
    "infer_all_characters_in_project",
]
