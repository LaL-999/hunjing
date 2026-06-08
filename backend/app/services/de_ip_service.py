"""去 IP 化导出服务 — Sprint 2.E。

战略锚(doc 5 四层版权防护核心层):
  用户主动选导出原版 / 去 IP 版 → 平台不强制,提供工具

3 个核心能力:
  1. generate_de_ip_dictionary — 调 LLM 生成项目专属字典(per project 缓存)
  2. apply_de_ip — longest-match-first 替换算法(防"贾母"被"贾"前缀先吃)
  3. export_simulation — 拼 markdown + 可选 de_ip 替换 + 法务 logger trail

trail 设计(简化):
  当前仅 logger.info 写一行"用户 X 在时刻 Y 导出 sim Z 去 IP 版,替换 N 处"
  未来法务诉求 db 落表 → 加新 migration `de_ip_export_logs`(不复用 violation_logs,
  因为 violation_logs 语义是"违规命中红旗",去 IP 是用户主动合规化,不是违规)
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from app.db import execute, fetch_all, fetch_one, transaction
from app.models.de_ip_dictionary import DeIpDictionary
from app.services.llm_client import call_llm_json, estimate_cost_yuan
from app.services.project_service import (
    ResourceNotFoundOrForbidden,
    get_project_or_403,
    iso_now,
)
from app.services.simulation_service import get_simulation_or_404


log = logging.getLogger("de_ip_export")

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"


# === 异常 ===

class DictionaryNotFound(Exception):
    """项目还没生成去 IP 字典(用户未主动 POST /de_ip_dictionary)。"""


class SimulationNotExportable(Exception):
    """sim 非 done / narrative 空,不可导出。"""


class NoCharactersToReplace(Exception):
    """项目没角色 / 全是空名 → 无法生成字典。"""


# ======================================================================
# 字典生成
# ======================================================================

def _load_prompt() -> str:
    return (PROMPTS_DIR / "de_ip_dictionary.md").read_text(encoding="utf-8")


def _collect_input_names(
    conn: sqlite3.Connection, project_id: str,
) -> tuple[list[dict], list[str]]:
    """收集项目所有 character names + aliases + misc names(从 events.description 提取)。

    Returns (characters_list, misc_names_list)
    """
    char_rows = fetch_all(
        conn,
        "SELECT id, name FROM characters WHERE project_id=? ORDER BY created_at",
        (project_id,),
    )
    if not char_rows:
        raise NoCharactersToReplace("项目没角色,无法生成去 IP 字典")

    characters: list[dict] = []
    for r in char_rows:
        # characters 表的 aliases 在 character_refinements 里;简化只取 name
        # (如要 aliases:JOIN character_refinements 或 characters.aliases 字段)
        characters.append({"name": r["name"]})

    # misc 名字:从 events.description + relationships.description 里抓"专有名词"
    # 简化:全部 description 拼一起,让 LLM 自己抽
    # 更精确做法:NER 但 over-engineer
    misc_set: set[str] = set()
    for ev in fetch_all(
        conn, "SELECT description FROM events WHERE project_id=?", (project_id,),
    ):
        # 短描述里取前 60 字(防过长)
        text = (ev["description"] or "").strip()[:60]
        if text:
            misc_set.add(text)
    # 注:misc 不一定是名字,LLM 在 prompt 里被告知"只替换专有名词"
    misc_names = sorted(misc_set)[:30]   # 截 30 个防 prompt 过长

    return characters, misc_names


def _build_world_baseline_block(project_row: sqlite3.Row) -> str:
    raw = project_row["world_baseline_json"] if "world_baseline_json" in project_row.keys() else None
    if not raw:
        return "  (未识别 — LLM 自由判断语体气质)"
    try:
        baseline = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return "  (baseline 损坏)"
    if not isinstance(baseline, dict):
        return "  (baseline 格式错)"
    labels = {
        "genre": "体裁", "setting": "背景设定", "magic_system": "超能力体系",
        "time_axis": "时间轴", "tone": "整体基调", "free_form": "自由描述",
    }
    return "\n".join(
        f"  - {labels[k]}:{baseline.get(k) or '(未识别)'}"
        for k in labels
    )


def _build_user_prompt(
    work_name: str,
    world_baseline_block: str,
    characters: list[dict],
    misc_names: list[str],
) -> str:
    chars_block = "\n".join(
        f"  - {c['name']}" for c in characters
    ) or "  (无)"
    misc_block = "\n".join(f"  - {m}" for m in misc_names) or "  (无)"
    return (
        "请为下面的原作生成「去 IP」化替换字典。\n\n"
        f"## 原作信息\n- 作品名:{work_name}\n- 世界观 baseline(语体气质参考)\n"
        f"{world_baseline_block}\n\n"
        f"## 待替换的专有名词\n\n### 人名(含别名)\n{chars_block}\n\n"
        f"### 地名 / 事件名 / 物名(从事件描述提取 — LLM 自行筛专有名词)\n{misc_block}\n\n"
        "请严格按 system prompt 的 schema 输出 JSON。"
    )


def generate_de_ip_dictionary(
    conn: sqlite3.Connection, project_id: str, user_id: str,
) -> DeIpDictionary:
    """调 LLM 生成项目的去 IP 字典。

    Raises:
      ResourceNotFoundOrForbidden     project 不属于用户
      NoCharactersToReplace           项目无角色
      LlmCallFailed / LlmJsonParseFailed   LLM 失败
    """
    project = get_project_or_403(conn, project_id, user_id)
    project_row = fetch_one(
        conn, "SELECT * FROM projects WHERE id=?", (project_id,),
    )
    if project_row is None:
        raise ResourceNotFoundOrForbidden("project", project_id)

    characters, misc_names = _collect_input_names(conn, project_id)
    world_baseline_block = _build_world_baseline_block(project_row)

    system_prompt = _load_prompt()
    user_prompt = _build_user_prompt(
        work_name=project.name,
        world_baseline_block=world_baseline_block,
        characters=characters,
        misc_names=misc_names,
    )
    parsed, usage = call_llm_json(
        system_prompt, user_prompt,
        max_tokens=3000, timeout=90.0, retries=2,
    )

    raw_mapping = parsed.get("mapping") if isinstance(parsed, dict) else None
    if not isinstance(raw_mapping, dict):
        raw_mapping = {}
    # 清洗:key/value 必须 str + 非空 + 不等(不允许"a→a"空替换)
    mapping: dict[str, str] = {}
    for k, v in raw_mapping.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        k = k.strip()
        v = v.strip()
        if not k or not v or k == v:
            continue
        mapping[k] = v
    notes = parsed.get("notes") if isinstance(parsed, dict) else None
    if notes is not None and not isinstance(notes, str):
        notes = None

    cost = estimate_cost_yuan(usage["input_tokens"], usage["output_tokens"])
    now = iso_now()

    # INSERT OR REPLACE:per project 1 个;重新生成覆盖
    with transaction(conn) as tx:
        execute(
            tx,
            "INSERT OR REPLACE INTO de_ip_dictionaries "
            "(project_id, user_id, mapping_json, notes, "
            " tokens_input, tokens_output, cost_yuan, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, "
            "  COALESCE((SELECT created_at FROM de_ip_dictionaries WHERE project_id=?), ?), "
            "  ?)",
            (
                project_id, user_id, json.dumps(mapping, ensure_ascii=False), notes,
                usage["input_tokens"], usage["output_tokens"], cost,
                project_id, now,   # COALESCE 兜底 created_at
                now,
            ),
        )

    row = fetch_one(
        conn, "SELECT * FROM de_ip_dictionaries WHERE project_id=?", (project_id,),
    )
    return DeIpDictionary.from_row(row)


def get_dictionary_or_404(
    conn: sqlite3.Connection, project_id: str, user_id: str,
) -> DeIpDictionary:
    """拿项目当前字典。无 → DictionaryNotFound(router 转 404)。"""
    get_project_or_403(conn, project_id, user_id)   # 鉴权
    row = fetch_one(
        conn, "SELECT * FROM de_ip_dictionaries WHERE project_id=?", (project_id,),
    )
    if not row:
        raise DictionaryNotFound(f"项目 {project_id} 还没生成去 IP 字典")
    return DeIpDictionary.from_row(row)


# ======================================================================
# 替换算法 — longest-match-first(关键防"贾母"被"贾"前缀先吃)
# ======================================================================

def apply_de_ip(text: str, mapping: dict[str, str]) -> tuple[str, dict[str, int]]:
    """对文本应用字典替换。

    算法:按 key 长度倒序遍历替换 → "贾母"(长)先替换为"仁母","贾"(短)后替换
    剩余的"贾"。这样"贾母"不会被先把"贾"替换成"仁"导致变成"仁母"(但 mapping
    里"贾母"如果映射到别的,会被前缀吃 → 长 key 优先确保正确)。

    Returns:
      (replaced_text, count_dict)
        count_dict: {"林黛玉": 12, ...} 该字典 key 在原文出现并被替换的次数
        (按替换前文本统计,不计被前面替换"产生"的新文本)
    """
    if not mapping or not text:
        return text, {}

    # 按 key 长度倒序;长度相同按字典序稳定(防 dict 迭代序不稳)
    sorted_keys = sorted(mapping.keys(), key=lambda k: (-len(k), k))

    counts: dict[str, int] = {}
    result = text
    for orig in sorted_keys:
        new = mapping[orig]
        # count 当前 result 中出现次数(关键:基于已替换后的 result,因为长 key 先吃)
        c = result.count(orig)
        if c > 0:
            result = result.replace(orig, new)
            counts[orig] = c
    return result, counts


# ======================================================================
# 导出 markdown
# ======================================================================

def _render_markdown(
    title: str,
    sim_row: sqlite3.Row,
    narrative_text: str,
    version: str,
) -> str:
    """拼 markdown 头部 meta + narrative 主体。"""
    divergence = sim_row["divergence"] or ""
    reshape = sim_row["reshape_percent"]
    rounds = sim_row["rounds_planned"]
    created_at = sim_row["created_at"]
    completed_at = sim_row["completed_at"] or "(未完成)"
    version_label = "原版" if version == "original" else "去 IP 版"

    header = (
        f"# {title}\n\n"
        f"> {version_label} · 由浑晶协助创作\n>\n"
        f"> 反事实锚点:{divergence}\n>\n"
        f"> 重塑度:{reshape}% · 推演 {rounds} 轮\n>\n"
        f"> 创建:{created_at} · 完成:{completed_at}\n\n---\n\n"
    )
    return header + narrative_text


def export_simulation(
    conn: sqlite3.Connection, sim_id: str, user_id: str, version: str,
) -> dict[str, Any]:
    """导出推演 markdown。

    Args:
      version: 'original' / 'de_ip'

    Raises:
      ResourceNotFoundOrForbidden     sim 不属于用户
      SimulationNotExportable         sim 非 done / narrative 空
      DictionaryNotFound              version='de_ip' 但项目无字典
    """
    sim = get_simulation_or_404(conn, sim_id, user_id)
    if sim.state != "done":
        raise SimulationNotExportable(
            f"仅 done 推演可导出(当前 state={sim.state})"
        )
    narrative = sim.narrative or ""
    if not narrative.strip():
        raise SimulationNotExportable("推演 narrative 为空,无内容可导出")

    project = get_project_or_403(conn, sim.project_id, user_id)
    sim_row = fetch_one(
        conn, "SELECT * FROM simulations WHERE id=?", (sim_id,),
    )

    if version == "de_ip":
        dictionary = get_dictionary_or_404(conn, sim.project_id, user_id)
        replaced_narrative, counts = apply_de_ip(narrative, dictionary.mapping)
        # title 也替换(若项目名含 IP 角色 — 实际罕见但完整起见)
        replaced_title, _ = apply_de_ip(project.name, dictionary.mapping)

        total = sum(counts.values())
        # 法务 trail — logger.info 写一行(未来加 db 表再升级)
        log.info(
            "DE_IP_EXPORT user=%s sim=%s project=%s total_replacements=%d "
            "replacements=%s",
            user_id, sim_id, sim.project_id, total,
            json.dumps(counts, ensure_ascii=False),
        )

        md = _render_markdown(replaced_title, sim_row, replaced_narrative, version)
        filename = f"narrative_{sim_id[:8]}_deip.md"
        # 按 count 降序
        replacements_list = [
            {"original": k, "replaced": dictionary.mapping[k], "count": v}
            for k, v in sorted(counts.items(), key=lambda x: -x[1])
        ]
        return {
            "filename": filename,
            "content": md,
            "version": "de_ip",
            "replacements": replacements_list,
            "total_replacements": total,
        }
    else:   # original
        md = _render_markdown(project.name, sim_row, narrative, "original")
        filename = f"narrative_{sim_id[:8]}.md"
        return {
            "filename": filename,
            "content": md,
            "version": "original",
            "replacements": None,
            "total_replacements": 0,
        }


__all__ = [
    "DictionaryNotFound",
    "SimulationNotExportable",
    "NoCharactersToReplace",
    "generate_de_ip_dictionary",
    "get_dictionary_or_404",
    "apply_de_ip",
    "export_simulation",
]
