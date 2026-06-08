"""Sprint 6.A1(2026-05-18)— Agent 档案补全员。

产品意图(对齐用户拍板"路径 C 多 agent 仿真"):
  把 characters 表升级为"统一 agent 档案"层。给空字段的角色用 LLM 自动补全
  identity / personality / quotes / no_go_list **+ behavior_baseline** 五字段,**已填内容不覆盖**。

  Sprint 6.A2 路线图 #1(2026-05-22):补完 behavior_baseline 维度 —
  FOCUS.7 已让 extract_service 主路径抽 baseline,但 agent_profile_enricher 是
  "手动补完"路径(初始态用户手建 / 配角 minimal_profile fallback / 反事实重跑),
  之前只补 4 个 string/list 字段,baseline 字段被遗漏。本次补齐让 baseline 抽取链路闭环。

3 态生效路径:
  - 初始态:用户手动建角色填了 name 但没填其他 → 用户可点"AI 补全档案" 触发
  - 中间/末尾态:extract 完工后 hook 自动跑(批量给所有 character 补全空字段)
  - 反事实改属性时:**仅重跑该角色**(单角色 enricher),不动其他角色

LLM 输入:
  - character_name: 角色名
  - existing: 当前 5 字段值(已填的保留;空字段才会被补)
  - source_context: 该角色在原作 chunk 里的出场片段(从 extract_chunk_results 抽)
                     若该角色无原作上下文(初始态用户手建),source_context=[]
                     → LLM 降级写更通用的档案 / 留空

成本:每角色一次 DeepSeek V3 调用 ~500 input + 400 output token = ~¥0.001
       30 角色批量补全 ~¥0.03(可忽略)

错误处理:
  - LLM 失败 / JSON 解析失败 → log warning,**该角色档案保持原状**,不抛
  - 单角色失败不阻塞批量(继续下一个)
  - LLM 输出 baseline 非法(非枚举 / 越界)→ 校验后 baseline 不入库,其他字段不受影响
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Optional

from app.db import execute, fetch_all, fetch_one
from app.models.character import Character
from app.services.extract_service import _extract_behavior_baseline_from_profile
from app.services.llm_client import call_llm_json
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)


# 每个角色给 LLM 看几段原作上下文(过多 = 浪费 token,过少 = 推不准)
# P0H.1(2026-05-24):6 → 10,因为大著作里 6 段全集中在前几章,死亡 / 异地章节扫不到
SOURCE_CONTEXT_SAMPLES = 10
# 单段上下文最大字数(防一段超长把 prompt 撑爆)
CONTEXT_SAMPLE_MAX_CHARS = 400


def _load_enricher_prompt() -> str:
    """从 prompts/agent_profile_enricher.md 加载系统 prompt。"""
    # 复用 comic_service 的 _load_prompt 模式(读 prompts/ 目录)
    from pathlib import Path
    prompt_path = Path(__file__).resolve().parent.parent.parent.parent / "prompts" / "agent_profile_enricher.md"
    return prompt_path.read_text(encoding="utf-8")


def _gather_source_context(
    conn: sqlite3.Connection, project_id: str, character_name: str,
) -> list[str]:
    """从 extract_chunk_results 抽该角色出现的原作片段(最多 SOURCE_CONTEXT_SAMPLES 段)。

    实现:扫所有 chunk 的 graph_json,找含该角色名的 chunk,从 chunk 原文(uploads
    表实际文本)截出现段。

    简化策略(MVP):graph_json 不存 chunk 原文,这里直接拉 upload 表的原文,
    用关键词位置 ± 200 字截窗。未来可优化为存 chunk 原文。
    """
    # 拉所有 ready upload 的解析文本(初始态可能没有 upload → 返 [])
    upload_rows = fetch_all(
        conn,
        """SELECT storage_path, mime_type
           FROM uploads
           WHERE project_id=? AND state='ready'
           ORDER BY uploaded_at ASC""",
        (project_id,),
    )
    if not upload_rows:
        return []

    # 拼接所有 upload 文本(可能很大,我们只用 grep 抽片段)
    # Bug 修复(2026-05-22):file_parser.parse_file 返回 ParseResult dataclass(success/text/error)
    # 不是 str;直接 append 会让 "\n\n".join() 抛 TypeError(sequence item 0: ...ParseResult found)。
    # 另:parse_file 签名收 Path 而非 str 相对路径 → 用 settings.uploads_abs_dir 拼绝对路径。
    all_text_parts: list[str] = []
    try:
        from app.config import settings
        from app.services import file_parser
        for r in upload_rows:
            try:
                abs_path = settings.uploads_abs_dir / r["storage_path"]
                result = file_parser.parse_file(abs_path, r["mime_type"])
                if result.success and result.text:
                    all_text_parts.append(result.text)
                elif not result.success:
                    logger.warning(
                        f"agent_profile_enricher: parse {r['storage_path']} fail: {result.error}"
                    )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"agent_profile_enricher: parse {r['storage_path']} exception: {e}"
                )
    except ImportError:
        logger.warning("agent_profile_enricher: file_parser unavailable")
        return []

    if not all_text_parts:
        return []
    full_text = "\n\n".join(all_text_parts)

    # P0H.1(2026-05-24)— 分布采样治"直子未死"根因
    # 原线性扫前 N 段,大著作 600+ 次出场全集中前几章,**死亡 / 异地章节扫不到**
    # 修复:先扫所有 character_name 出现位置,再前/中/末等距采样
    #
    # 关键策略:**末尾 2 段保底** — 角色的"最终状态"(死/活/离开)线索高度集中在原著末尾,
    # 死亡场景一定出现在角色最后几次提及里
    positions: list[int] = []
    cursor = 0
    while True:
        pos = full_text.find(character_name, cursor)
        if pos == -1:
            break
        positions.append(pos)
        cursor = pos + len(character_name)  # 下一个匹配开始点

    if not positions:
        return []

    # 选采样位置:
    #   - 角色出场 ≤ SAMPLES → 全取(短文本)
    #   - 出场 > SAMPLES → 等距分布(前/中/末覆盖)+ 末尾 2 段保底
    n = SOURCE_CONTEXT_SAMPLES
    if len(positions) <= n:
        sample_positions = positions
    else:
        # 末尾 2 段保底(治死亡判定漏掉关键证据)
        tail_positions = positions[-2:]
        # 剩余 n-2 段在前 80% 的范围内等距采样
        front_count = n - 2
        front_pool = positions[: int(len(positions) * 0.80)]
        if len(front_pool) <= front_count:
            front_positions = front_pool
        else:
            indices = [
                round(i * (len(front_pool) - 1) / (front_count - 1))
                for i in range(front_count)
            ]
            front_positions = [front_pool[i] for i in indices]
        sample_positions = front_positions + tail_positions

    # 截窗 + 防重叠
    samples: list[str] = []
    seen_starts: list[int] = []
    for pos in sample_positions:
        start = max(0, pos - 100)
        # 防与已采样窗口重叠(< 100 字距视为重叠)
        if any(abs(start - s) < 100 for s in seen_starts):
            continue
        end = min(len(full_text), pos + CONTEXT_SAMPLE_MAX_CHARS)
        snippet = full_text[start:end].strip()
        if snippet:
            samples.append(snippet)
            seen_starts.append(start)
    return samples


def enrich_character(
    conn: sqlite3.Connection, character: Character,
) -> dict:
    """给单个 character 补全空字段。

    Returns: dict 报告 {
      "character_id": str,
      "updated_fields": list[str],   # 实际被 LLM 补的字段名
      "skipped_fields": list[str],   # 用户已填,跳过不补的字段名
      "context_samples": int,        # 给 LLM 看了几段原文
      "usage": {"input_tokens": int, "output_tokens": int},
      "error": str | None,
    }
    """
    # Sprint 6.A2 #1(2026-05-22):existing 加 behavior_baseline(dict | None)
    # baseline 整体 None / 全空 dict → 视为待补;有任一子字段非空 → 视为"已填"保留
    # P0F.2(2026-05-24):加 life_status + status_note,LLM 基于原作判定
    # B5.2(2026-05-27):加 aliases — 原 P0Q.3 只让 narrator 透出 aliases 给 LLM 用本名,
    # 但 enricher 不补 aliases → 用户手建初始角色未填 aliases 时,反事实重跑产物仍
    # 缺别名词汇库。让 LLM 从原作上下文里发掘别名候选填上。
    existing = {
        "identity": character.identity or "",
        "personality": character.personality or "",
        "quotes": list(character.quotes or []),
        "no_go_list": list(character.no_go_list or []),
        "aliases": list(character.aliases or []),
        "behavior_baseline": (
            character.behavior_baseline
            if isinstance(character.behavior_baseline, dict)
            and any(
                v not in (None, "", [])
                for v in character.behavior_baseline.values()
            )
            else None
        ),
        # P0F.2:life_status 默认 alive 视为"未判定";已是 deceased/in_facility/absent
        # 等非默认值 → 视为"已填",不再覆盖(用户可能手动改过)
        "life_status": character.life_status or "alive",
        "status_note": character.status_note or "",
    }

    # 找出需要补的字段(空字符串 / 空数组 / None dict)
    fields_to_fill: list[str] = []
    for k, v in existing.items():
        if k == "behavior_baseline":
            if v is None:
                fields_to_fill.append(k)
        elif k == "life_status":
            # P0F.2:life_status='alive' + status_note='' 视为"未判定" → 让 LLM 补
            if v == "alive" and not (existing.get("status_note") or "").strip():
                fields_to_fill.append(k)
        elif k == "status_note":
            # status_note 跟随 life_status 一起补(下方 LLM 同时返回这两字段)
            # 此处不单独列入 fields_to_fill,避免重复
            pass
        elif isinstance(v, list):
            if not v:
                fields_to_fill.append(k)
        else:
            if not (v or "").strip():
                fields_to_fill.append(k)

    if not fields_to_fill:
        return {
            "character_id": character.id,
            "updated_fields": [],
            "skipped_fields": list(existing.keys()),
            "context_samples": 0,
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "error": "all_filled",
        }

    source_context = _gather_source_context(
        conn, character.project_id, character.name,
    )

    user_input = {
        "character_name": character.name,
        "existing": existing,
        "source_context": source_context,
    }

    try:
        system_prompt = _load_enricher_prompt()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"enricher prompt load failed: {e}")
        return {
            "character_id": character.id,
            "updated_fields": [],
            "skipped_fields": list(existing.keys()),
            "context_samples": len(source_context),
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "error": f"prompt_load_failed: {type(e).__name__}",
        }

    try:
        parsed, usage = call_llm_json(
            system_prompt=system_prompt,
            user_input=user_input,
            max_tokens=1500,
            temperature=0.7,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"enricher LLM failed for {character.name}: {e}")
        return {
            "character_id": character.id,
            "updated_fields": [],
            "skipped_fields": list(existing.keys()),
            "context_samples": len(source_context),
            "usage": {"input_tokens": 0, "output_tokens": 0},
            "error": f"llm_failed: {type(e).__name__}: {str(e)[:120]}",
        }

    if not isinstance(parsed, dict):
        logger.warning(
            f"enricher LLM returned non-dict for {character.name}: {type(parsed)}"
        )
        return {
            "character_id": character.id,
            "updated_fields": [],
            "skipped_fields": list(existing.keys()),
            "context_samples": len(source_context),
            "usage": usage,
            "error": "parse_failed",
        }

    # 只覆盖空字段(已填的保留),严格执行 prompt 铁律
    new_values: dict = {}
    updated_fields: list[str] = []
    skipped_fields: list[str] = []

    for field in fields_to_fill:
        llm_val = parsed.get(field)
        if field == "behavior_baseline":
            # Sprint 6.A2 #1(2026-05-22):复用 extract_service 的校验函数
            # 它会过滤枚举/范围非法值;全部非法 → 返 None,我们就不入库
            baseline_validated = _extract_behavior_baseline_from_profile(
                {"behavior_baseline": llm_val}
            )
            if baseline_validated is not None:
                new_values[field] = baseline_validated
                updated_fields.append(field)
        elif field == "life_status":
            # P0F.2(2026-05-24):严格枚举校验 + 配套 status_note
            if isinstance(llm_val, str) and llm_val in (
                "alive", "deceased", "in_facility", "absent", "unknown"
            ):
                new_values["life_status"] = llm_val
                updated_fields.append("life_status")
                # status_note 一起补,清洗 + 限长 200
                note_val = parsed.get("status_note")
                if isinstance(note_val, str):
                    new_values["status_note"] = note_val.strip()[:200]
                    updated_fields.append("status_note")
                else:
                    new_values["status_note"] = ""
        elif isinstance(existing[field], list):
            if isinstance(llm_val, list) and llm_val:
                # 过滤非字符串项 + 限长 + 去重保序
                cleaned = []
                seen = set()
                for item in llm_val:
                    s = str(item).strip()[:80]
                    if s and s not in seen:
                        cleaned.append(s)
                        seen.add(s)
                    if len(cleaned) >= 10:
                        break
                if cleaned:
                    new_values[field] = cleaned
                    updated_fields.append(field)
        else:
            if isinstance(llm_val, str) and llm_val.strip():
                # 长度限(对齐 schema:identity 200,personality 500)
                max_len = 200 if field == "identity" else 500
                new_values[field] = llm_val.strip()[:max_len]
                updated_fields.append(field)

    skipped_fields = [k for k in existing.keys() if k not in updated_fields]

    if not updated_fields:
        return {
            "character_id": character.id,
            "updated_fields": [],
            "skipped_fields": skipped_fields,
            "context_samples": len(source_context),
            "usage": usage,
            "error": "no_new_content",
        }

    # 写库 — field → DB column 映射:
    #   identity / personality / quotes / no_go_list → 同名列(quotes / no_go_list 无 _json 后缀,历史遗留)
    #   behavior_baseline → behavior_baseline_json 列(JSON dict 序列化)
    #   aliases → aliases_json 列(migration 054 起,后缀 _json 与早期 quotes 命名不一致)
    #   life_status / status_note → 同名列
    set_parts: list[str] = []
    values: list = []
    for field, val in new_values.items():
        if field == "behavior_baseline":
            # Sprint 6.A2 #1(2026-05-22):dict → JSON 写 behavior_baseline_json 列
            set_parts.append("behavior_baseline_json=?")
            values.append(json.dumps(val, ensure_ascii=False))
        elif field == "aliases":
            # B5.2(2026-05-27):list → JSON 写 aliases_json 列(migration 054 起的列名)
            set_parts.append("aliases_json=?")
            values.append(json.dumps(val, ensure_ascii=False))
        elif field in ("life_status", "status_note"):
            # P0F.2(2026-05-24):直接写同名列(TEXT NOT NULL DEFAULT)
            set_parts.append(f"{field}=?")
            values.append(val)
        elif isinstance(val, list):
            set_parts.append(f"{field}=?")
            values.append(json.dumps(val, ensure_ascii=False))
        else:
            set_parts.append(f"{field}=?")
            values.append(val)
    set_parts.append("updated_at=?")
    values.append(iso_now())
    values.append(character.id)

    execute(
        conn,
        f"UPDATE characters SET {', '.join(set_parts)} WHERE id=?",
        tuple(values),
    )
    conn.commit()

    return {
        "character_id": character.id,
        "updated_fields": updated_fields,
        "skipped_fields": skipped_fields,
        "context_samples": len(source_context),
        "usage": usage,
        "error": None,
    }


def enrich_all_characters_in_project(
    conn: sqlite3.Connection, project_id: str,
) -> dict:
    """批量补全项目所有角色的 agent 档案。

    用于 extract 完工 hook;单角色失败不阻塞下一个。
    """
    rows = fetch_all(
        conn,
        "SELECT * FROM characters WHERE project_id=?",
        (project_id,),
    )
    characters = [Character.from_row(r) for r in rows]
    if not characters:
        return {"enriched_count": 0, "skipped_count": 0, "results": []}

    enriched = 0
    skipped = 0
    errors = 0
    results: list[dict] = []

    for char in characters:
        try:
            result = enrich_character(conn, char)
            results.append(result)
            if result["error"] is None:
                enriched += 1
            elif result["error"] == "all_filled":
                skipped += 1
            else:
                errors += 1
        except Exception as e:  # noqa: BLE001
            logger.error(
                f"enrich_character unexpectedly raised for {char.name}: {e}"
            )
            errors += 1

    return {
        "enriched_count": enriched,
        "skipped_count": skipped,
        "error_count": errors,
        "results": results,
    }
