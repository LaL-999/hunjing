"""Sprint 6.A2 M6(2026-05-20)— Outline Generator(长篇 outline 生成器)。

核心职责:在 sim 真正"跑生成"之前,一次性 LLM 生成完整全篇 outline:
  - global_theme + global_arc
  - N 幕 × 每幕完整图纸(location / key_events / key_props 等)

落库:simulation_outlines + outline_scenes(N 行)
状态:outline state 走 drafting → awaiting_user(交给用户审核)

设计原则:
  - 一次 LLM 调用拿完整 outline(不分批,避免幕间漂移)
  - 失败 → state='failed' + error_message;主流程不阻塞 sim 本身(用户可重试)
  - 用户编辑由 outline_editor 服务负责;本服务只负责"首次生成"

Sprint 6.A2 M6-fix1(2026-05-20)— LlmJsonParseFailed 截断兜底:
  - max_tokens 从 6000 → 8000(DeepSeek V3 完成上限)
  - 调底层 call_llm_text 拿 raw,LlmJsonParseFailed 时尝试 partial extraction:
    用 regex 从被截断的 JSON 里抽出"已完成的 scenes",落 outline.state='awaiting_user'
    (附带 error_message 提示"已抽 N 幕,可编辑或重生")

API:
  - create_outline_draft(conn, sim_id) → tuple[str, dict]
    返回 (outline_id, llm_usage);outline state='awaiting_user' / 'failed'
  - get_outline_with_scenes(conn, sim_id) → tuple[SimulationOutline, list[OutlineScene]]
    给 OutlineReviewView API 用
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Optional

from app.db import execute, fetch_all, fetch_one
from app.models.outline_scene import OutlineScene
from app.models.simulation_outline import SimulationOutline
from app.services.llm_client import call_llm_json, call_llm_text
from app.services.project_service import iso_now

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


# ============================================================
# 主入口:生成 outline 草稿
# ============================================================

def create_outline_draft(
    conn: sqlite3.Connection,
    sim_id: str,
    strengthen_diversity: bool = False,
) -> tuple[str, dict]:
    """读 sim 配置 + 调 LLM → 落 simulation_outlines + N 行 outline_scenes。

    Args:
      sim_id: 已创建的 simulation id(应处于刚创建状态)
      strengthen_diversity: M8.D(2026-05-21)— 重新生成时若上一版场景过于单一,
        传 True 让 LLM 看到额外指令强化多样性

    Returns:
      (outline_id, llm_usage_dict)

    Raises:
      ValueError: sim 不存在 / 已有 outline / sim 配置异常
    """
    # 1. 拉 sim + 验证状态
    sim_row = fetch_one(conn, "SELECT * FROM simulations WHERE id=?", (sim_id,))
    if sim_row is None:
        raise ValueError(f"sim {sim_id} 不存在")
    existing = fetch_one(
        conn, "SELECT id FROM simulation_outlines WHERE simulation_id=?",
        (sim_id,),
    )
    if existing is not None:
        raise ValueError(
            f"sim {sim_id} 已有 outline ({existing['id']});outline_editor 处理编辑"
        )

    # 2. 准备 LLM 输入
    project_id = sim_row["project_id"]
    project_row = fetch_one(
        conn, "SELECT name, type, tags FROM projects WHERE id=?", (project_id,),
    )
    if project_row is None:
        raise ValueError(f"project {project_id} 不存在")

    char_rows = fetch_all(
        conn,
        """SELECT id, name, identity, personality, is_protagonist
           FROM characters WHERE project_id=?""",
        (project_id,),
    )
    characters_input = [
        {
            "id": r["id"],
            "name": r["name"],
            "identity": r["identity"] or "",
            "personality": r["personality"] or "",
            "is_protagonist": bool(r["is_protagonist"]),
        }
        for r in char_rows
    ]

    # ============================================================
    # 2026-06-05 CRITICAL BUG FIX:outline 生成必须读用户反事实变量!
    # ============================================================
    # 老 bug:create_outline_draft 完全不读 counterfactual_changes,
    #         outline LLM 看到的角色/事件全是原作,用户改的名字/性格/事件
    #         都不会体现在 outline 里 → sim 跟着 outline 跑 → narrative 也没反事实。
    #
    # 修复策略(三层):
    #   Layer A — 数据层:把反事实应用到 characters_input(改 name/identity/personality)
    #             LLM 看见的就是"改后"的角色,自然按改后的写
    #   Layer B — Prompt 层:user_input 加 counterfactual_section 文本块,
    #             显式告诉 LLM "用户做了这些反事实改动 + 用户意图,必须严格按此编排"
    #   Layer C — prompt 铁律(m6_outline_generator.md):反事实是用户硬指令,
    #             覆盖原作设定,优先级 > divergence > 原作
    from app.services.counterfactual_service import (
        build_director_context,
        get_linked_counterfactual_ids,
        render_counterfactual_section_text,
    )
    cf_section_text = ""
    cf_context_items: list[dict] = []
    try:
        # 拉本 sim 关联的反事实子集(combination_tree 用户勾选写入 link 表)
        # None = 老语义"全部 active";[id1,...] = 用户选的子集
        linked_ids = get_linked_counterfactual_ids(conn, sim_id)
        cf_context = build_director_context(conn, project_id, linked_ids)
        cf_context_items = cf_context.get("items", [])
        if cf_context["active_count"] > 0:
            cf_section_text = render_counterfactual_section_text(cf_context)

            # Layer A:把 character 反事实应用到 characters_input
            # 用户改了角色 name/identity/personality → outline LLM 看见的就是改后的版本
            char_field_map: dict[str, dict[str, str]] = {}
            for it in cf_context_items:
                if it["target_type"] != "character":
                    continue
                tid = it["target_id"]
                fld = it["field"]
                new_val = it["to"]
                if not tid or not fld:
                    continue
                char_field_map.setdefault(tid, {})[fld] = new_val
            if char_field_map:
                for c in characters_input:
                    overrides = char_field_map.get(c["id"])
                    if not overrides:
                        continue
                    # 仅覆盖 outline 关心的 3 字段
                    if "name" in overrides:
                        c["name"] = overrides["name"]
                    if "identity" in overrides:
                        c["identity"] = overrides["identity"]
                    if "personality" in overrides:
                        c["personality"] = overrides["personality"]
                    # 标记该角色被反事实改写过 — LLM 看到后会注意
                    c["_counterfactual_applied"] = True
    except Exception as e:  # noqa: BLE001
        # 反事实加载失败不阻断 outline — log + 走原作路径
        logger.warning(
            f"outline_generator 反事实加载失败 sim={sim_id}: {e}"
        )

    # 项目场景
    scene_rows = fetch_all(
        conn,
        """SELECT name, description, appearance_chunk_count
           FROM project_scenes WHERE project_id=?
           ORDER BY appearance_chunk_count DESC LIMIT 30""",
        (project_id,),
    )
    available_scenes_input = [
        {
            "name": r["name"],
            "description": r["description"] or "",
        }
        for r in scene_rows
    ]

    project_tags = []
    if project_row["tags"]:
        try:
            project_tags = json.loads(project_row["tags"])
        except (json.JSONDecodeError, TypeError):
            project_tags = []

    # Sprint 6.A2 FOCUS.2(2026-05-21):读项目叙述视角,注入 outline prompt
    # 让 outline_generator 设计大纲时按"first → 设计'我'视角连贯幕序" / "third → 多视角切换 OK"
    project_pov = None
    try:
        raw_pov = project_row["narrative_pov"]
        if isinstance(raw_pov, str) and raw_pov in {"first", "second", "third", "mixed"}:
            project_pov = raw_pov
    except (KeyError, IndexError):
        project_pov = None

    # 2026-06-02 Patch D:走向终章信号传给 outline_generator
    # 用户没勾"走向终章" → outline 必须保持开放结局,末尾几幕不许收束
    try:
        with_grand_finale = bool(sim_row["with_grand_finale"])
    except (KeyError, IndexError, TypeError):
        with_grand_finale = False

    # F1.4(2026-06-02)+ 阶段 3A:拉项目级未解伏笔(跨代继承) → outline 主动安排回收
    # 阶段 3A:支持用户在 SimulationDock 主动选择继承哪些伏笔(inherited_foreshadow_ids_json)
    #   NULL / 不存在  → 默认拉所有 open(F1.4 行为,向后兼容)
    #   '[]'           → 用户主动空选 → 一条不读
    #   '["id1","id2"]' → 只读用户选的子集
    # 上限 8 条防 prompt 过载.
    foreshadows_for_outline: list[dict] = []
    try:
        # 先看本 sim 是否记录了 inherited_foreshadow_ids
        inherited_ids: list[str] | None = None
        try:
            inh_raw = sim_row["inherited_foreshadow_ids_json"]
            if inh_raw:
                inherited_ids = json.loads(inh_raw)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            inherited_ids = None

        if inherited_ids == []:
            # 用户主动空选 — 不读任何伏笔
            pass
        elif inherited_ids is None:
            # 老路径 / 默认行为:拉所有 open(F1.4)
            fs_rows = fetch_all(
                conn,
                """SELECT content, priority, introduced_scene_index
                   FROM foreshadow_ledger
                   WHERE project_id=? AND status='open'
                   ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                            created_at ASC
                   LIMIT 8""",
                (project_row["id"],),
            )
            for r in fs_rows:
                content = (r["content"] or "").strip()
                if content:
                    foreshadows_for_outline.append({
                        "content": content[:200],
                        "priority": r["priority"] or "medium",
                        "introduced_at_prior_scene": int(r["introduced_scene_index"] or 0),
                    })
        else:
            # 阶段 3A:用户主动选了子集 → 用 IN 过滤
            if inherited_ids:
                placeholders = ",".join(["?"] * len(inherited_ids))
                fs_rows = fetch_all(
                    conn,
                    f"""SELECT content, priority, introduced_scene_index
                        FROM foreshadow_ledger
                        WHERE project_id=? AND status='open'
                          AND id IN ({placeholders})
                        ORDER BY CASE priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
                                 created_at ASC
                        LIMIT 8""",
                    (project_row["id"], *inherited_ids),
                )
                for r in fs_rows:
                    content = (r["content"] or "").strip()
                    if content:
                        foreshadows_for_outline.append({
                            "content": content[:200],
                            "priority": r["priority"] or "medium",
                            "introduced_at_prior_scene": int(r["introduced_scene_index"] or 0),
                        })
    except sqlite3.OperationalError:
        # foreshadow_ledger 表不存在 / inherited_foreshadow_ids_json 列不存在 — 老库降级
        pass
    except Exception as e:  # noqa: BLE001
        logger.warning(f"outline_generator pull foreshadows failed sim={sim_id}: {e}")

    # ============================================================
    # 2026-06-05 顶级压力:把 event 反事实自动拼进 divergence(强信号位)
    # ============================================================
    # 原因:cf_section_text 在 user_input 中段位置,LLM 对原作熟悉度强时会偷偷按原作 key_events 编排。
    # divergence 是"剧情大方向锚点",prompt 中位置靠前 + 语义强 — LLM 必定遵守。
    # 把 event 反事实(target_type='event')附加进 divergence,LLM 第一眼就看到事件改写指令。
    divergence_raw = sim_row["divergence"] or ""
    divergence_enhanced = divergence_raw
    evt_items_for_div = [it for it in cf_context_items if it["target_type"] == "event"]
    if evt_items_for_div:
        evt_block_lines = ["", "[⚠ 用户事件改写 — 必须按改后版本编排,绝对禁止用原作版本]"]
        for it in evt_items_for_div:
            evt_block_lines.append(
                f"- 原作版本:{it['from']}"
            )
            evt_block_lines.append(
                f"  改后版本:{it['to']}  ← outline 的 key_events / scene_summary 必须按此写"
            )
            if it.get("user_intent"):
                evt_block_lines.append(f"  ★ 用户意图:{it['user_intent']}")
        divergence_enhanced = divergence_raw + "\n".join(evt_block_lines)

    user_input = {
        "sim_id": sim_id,
        "project_name": project_row["name"],
        "project_genre": project_row["type"],
        "project_tags": project_tags,
        "divergence": divergence_enhanced,
        "characters": characters_input,
        "available_project_scenes": available_scenes_input,
        "previous_narrative_summary": "",  # 滚雪球时填,首期简化为空
        "original_tail_excerpt": sim_row["original_tail_excerpt"] or "",
        "target_total_chars": int(sim_row["target_chars"] or 4000),
        "total_scenes_target": int(sim_row["rounds_planned"] or 12),
        "reshape_percent": int(sim_row["reshape_percent"] or 50),
        "style": sim_row["style"] or "auto",
        # FOCUS.2:作品叙述视角 — 大纲按此设计视点幕序;null 时 prompt 兜底
        "narrative_pov": project_pov,
        # 2026-06-02 Patch D:走向终章信号(False = 开放结局 / True = 收束)
        "with_grand_finale": with_grand_finale,
        # F1.4(2026-06-02):跨代未解伏笔(给 LLM 列表;空 list 表示无前作伏笔)
        # prompt 见 m6_outline_generator.md 铁律段(本次同时更新)
        "inherited_foreshadows": foreshadows_for_outline,
        # 2026-06-05 CRITICAL:反事实变量(用户改写的角色/事件/关系/世界观)
        # 优先级:反事实 > divergence > 原作设定
        # active_count > 0 时 prompt 会在 outline 各幕中体现这些改动
        "counterfactual_section": cf_section_text,
        "counterfactual_count": len(cf_context_items),
    }
    # M8.D(2026-05-21):上一版场景过于单一 → 注入额外指令强化多样性
    if strengthen_diversity:
        total = user_input["total_scenes_target"]
        user_input["diversity_directive"] = (
            f"⚠️ 上一版本 outline 场景过于单一(用户反馈“清一色”),"
            f"本次必须**主动自创新场景**让整篇 outline 出现 ≥ {max(2, total // 2)} 个不同场所;"
            f"原作 available_project_scenes 数量少时,可大胆自创对齐时代风格的新场所"
            f"(如校园悬疑可自创 天台 / 地下室 / 小卖部 / 操场看台 / 走廊;"
            f"古典可自创 驿站 / 码头 / 郊外凉亭 / 废祠;"
            f"科幻可自创 舰桥 / 医疗舱 / 星图室)。"
            f"自创场景会被反向入库项目场景库,为故事注入新血液。"
        )

    # 3. 先插一行 outline state='drafting'
    now = iso_now()
    outline_id = uuid.uuid4().hex
    total_planned = user_input["total_scenes_target"]
    execute(
        conn,
        """INSERT INTO simulation_outlines
           (id, simulation_id, state, total_scenes_planned,
            global_theme, global_arc, user_approved_at, error_message,
            created_at, updated_at)
           VALUES (?, ?, 'drafting', ?, '', '', NULL, NULL, ?, ?)""",
        (outline_id, sim_id, total_planned, now, now),
    )
    conn.commit()

    # 4. 调 LLM(M6-fix1:用 call_llm_text 拿 raw,自己解析 — 截断时能 partial extract)
    system_prompt = _load_prompt("m6_outline_generator.md")
    raw_text = ""
    usage: dict = {"input_tokens": 0, "output_tokens": 0}
    try:
        raw_text, usage = call_llm_text(
            system_prompt, user_input,
            max_tokens=8000,  # DeepSeek V3 完成上限
            temperature=0.7,
        )
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"[:300]
        logger.warning(
            f"outline_generator LLM call failed sim={sim_id}: {err}"
        )
        _mark_failed(conn, outline_id, err)
        return outline_id, usage

    # 4.1 尝试完整 JSON 解析
    parsed = _try_parse_complete_json(raw_text)
    is_partial = False

    if parsed is None:
        # 4.2 JSON 截断 / 损坏 → partial extraction 兜底
        logger.warning(
            f"outline_generator JSON parse failed sim={sim_id}, "
            f"raw_len={len(raw_text)} chars;尝试 partial extraction…"
        )
        parsed = _partial_extract_outline(raw_text)
        is_partial = parsed is not None

    if parsed is None:
        _mark_failed(
            conn, outline_id,
            f"LLM JSON 解析全失败 + partial 抽取也失败 "
            f"(raw 前 200 字={raw_text[:200]!r})",
        )
        return outline_id, usage

    if not isinstance(parsed, dict):
        _mark_failed(conn, outline_id, "LLM 输出非 dict")
        return outline_id, usage

    global_theme = str(parsed.get("global_theme") or "").strip()[:200]
    global_arc = str(parsed.get("global_arc") or "").strip()[:1000]
    scenes_raw = parsed.get("scenes") or []
    if not isinstance(scenes_raw, list) or len(scenes_raw) == 0:
        _mark_failed(conn, outline_id, "LLM 未返 scenes 数组")
        return outline_id, usage

    # 落每幕
    actual_inserted = 0
    expected_scene_index = 0
    for scene_dict in scenes_raw:
        if not isinstance(scene_dict, dict):
            continue
        try:
            _insert_outline_scene(
                conn, outline_id, expected_scene_index, scene_dict, now,
            )
            actual_inserted += 1
            expected_scene_index += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"outline_generator skip malformed scene "
                f"{expected_scene_index}: {e}"
            )
            continue

    if actual_inserted == 0:
        _mark_failed(conn, outline_id, "无任何合法 scene 落库")
        return outline_id, usage

    # M6-fix1:partial extraction 时附 warning 给用户(state 仍是 awaiting_user,
    # 让用户决定:接受当前 N 幕重生剩余 / 直接重生 / 编辑后批准)
    # M6-fix4:**partial 时保留用户原始 total**(让"继续生成"按钮显 + 知道还差几幕)
    partial_warning = None
    expected_total = user_input["total_scenes_target"]
    if is_partial:
        partial_warning = (
            f"⚠️ LLM 输出被截断(已抽取 {actual_inserted}/{expected_total} 幕);"
            f"你可以:(1) 点'▶ 继续生成'补齐剩余 {expected_total - actual_inserted} 幕;"
            f"(2) 在审核界面补足缺失幕 / 调整后批准;"
            f"(3) 点'重新生成'让 LLM 重出完整 outline。"
        )

    # M6-fix4:
    #   partial 截断 → 保留 expected_total(让 continue_outline_generation 知道还要补几幕)
    #   完整生成   → 用 actual_inserted(LLM 可能多给少给,以实际为准)
    final_total = expected_total if is_partial else actual_inserted

    # 更新 outline 主表 → state='awaiting_user'
    execute(
        conn,
        """UPDATE simulation_outlines
           SET state='awaiting_user', global_theme=?, global_arc=?,
               total_scenes_planned=?, error_message=?, updated_at=?
           WHERE id=?""",
        (
            global_theme, global_arc, final_total,
            partial_warning, iso_now(), outline_id,
        ),
    )
    conn.commit()
    return outline_id, usage


def _insert_outline_scene(
    conn: sqlite3.Connection,
    outline_id: str,
    scene_index: int,
    scene_dict: dict[str, Any],
    now: str,
) -> None:
    """落一行 outline_scene。校验 + 默认值兜底。"""
    scene_summary = str(scene_dict.get("scene_summary") or "").strip()[:500]
    if not scene_summary:
        raise ValueError("scene_summary 为空")
    location = str(scene_dict.get("location") or "").strip()[:50]
    if not location:
        raise ValueError("location 为空")

    scene_purpose = str(scene_dict.get("scene_purpose") or "推进主线").strip()[:50]
    time_anchor = str(scene_dict.get("time_anchor") or "").strip()[:50]
    transition = str(scene_dict.get("transition_from_last") or "").strip()[:300]

    # characters_present 必填(防 narrator 找不到 agent)
    chars_present = scene_dict.get("characters_present") or []
    if not isinstance(chars_present, list):
        chars_present = []
    chars_present = [str(c) for c in chars_present if c][:8]

    # key_events 至少 1 条
    key_events = scene_dict.get("key_events") or []
    if not isinstance(key_events, list):
        key_events = []
    key_events = [str(e)[:300] for e in key_events if e][:8]
    if not key_events:
        raise ValueError("key_events 为空")

    # key_props 可空
    key_props_raw = scene_dict.get("key_props") or []
    key_props: list[dict] = []
    if isinstance(key_props_raw, list):
        for p in key_props_raw[:6]:
            if not isinstance(p, dict):
                continue
            name = str(p.get("name") or "").strip()[:50]
            if not name:
                continue
            action = str(p.get("action") or "referenced")[:30]
            props_val = p.get("properties") or {}
            if not isinstance(props_val, dict):
                props_val = {}
            key_props.append({
                "name": name,
                "action": action,
                "properties": {
                    str(k): str(v)[:200]
                    for k, v in list(props_val.items())[:10]
                },
            })

    execute(
        conn,
        """INSERT INTO outline_scenes
           (id, outline_id, scene_index,
            scene_summary, scene_purpose,
            location, time_anchor, characters_present_json,
            key_events_json, key_props_json, transition_from_last,
            user_edited, state, generated_simulation_scene_id, error_message,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                   0, 'pending', NULL, NULL, ?, ?)""",
        (
            uuid.uuid4().hex, outline_id, scene_index,
            scene_summary, scene_purpose,
            location, time_anchor,
            json.dumps(chars_present, ensure_ascii=False),
            json.dumps(key_events, ensure_ascii=False),
            json.dumps(key_props, ensure_ascii=False),
            transition,
            now, now,
        ),
    )


def _mark_failed(
    conn: sqlite3.Connection, outline_id: str, reason: str,
) -> None:
    execute(
        conn,
        """UPDATE simulation_outlines
           SET state='failed', error_message=?, updated_at=?
           WHERE id=?""",
        (reason[:300], iso_now(), outline_id),
    )
    conn.commit()


# ============================================================
# JSON 解析 + Partial extraction(M6-fix1,治 LLM 截断)
# ============================================================

def _strip_markdown_fence(raw: str) -> str:
    """剥 ```json ... ``` 围栏(常见 LLM 输出格式)。"""
    s = raw.strip()
    if s.startswith("```"):
        # 去开头 ```json 或 ```
        first_newline = s.find("\n")
        if first_newline > 0:
            s = s[first_newline + 1:]
    if s.endswith("```"):
        s = s[: -3]
    return s.strip()


def _try_parse_complete_json(raw: str) -> Optional[dict]:
    """尝试完整 JSON 解析(strip_fence + repair)。失败返 None。"""
    if not raw:
        return None
    cleaned = _strip_markdown_fence(raw)
    # 直接 parse
    try:
        result = json.loads(cleaned)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        pass
    # 尝试用 json_repair 库(若可用)
    try:
        import json_repair  # type: ignore[import-not-found]
        result = json_repair.loads(cleaned)
        return result if isinstance(result, dict) else None
    except (ImportError, Exception):  # noqa: BLE001
        pass
    return None


def _partial_extract_outline(raw: str) -> Optional[dict]:
    """LLM 输出被截断时的兜底:用正则从 raw 抽出已完成的 global + scenes。

    策略:
      1. 抽 global_theme / global_arc(简单 string 字段)
      2. 抽 scenes 数组里**已完整结束**的 scene 对象(花括号匹配 + 字段齐全)
      3. 至少抽到 ≥1 个 scene 才返回非 None(否则交给上层 mark_failed)

    Returns:
      None — 抽不到任何完整 scene
      dict — {"global_theme": ..., "global_arc": ..., "scenes": [...]}
    """
    if not raw:
        return None
    cleaned = _strip_markdown_fence(raw)

    # 抽 global_theme / global_arc(简单 string)
    global_theme = ""
    m = re.search(r'"global_theme"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', cleaned)
    if m:
        global_theme = m.group(1)[:200]
    global_arc = ""
    m = re.search(r'"global_arc"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)"', cleaned)
    if m:
        global_arc = m.group(1)[:1000]

    # 抽 scenes 数组里的完整对象
    # 找 "scenes": [ ... 的起始位置
    scenes_start = re.search(r'"scenes"\s*:\s*\[', cleaned)
    if not scenes_start:
        return None
    array_start = scenes_start.end()

    # 从 array_start 开始,逐个抽完整的 scene 对象(括号匹配)
    scenes: list[dict] = []
    i = array_start
    n = len(cleaned)
    while i < n:
        # 跳空白 / 逗号
        while i < n and cleaned[i] in " \t\n\r,":
            i += 1
        if i >= n or cleaned[i] != "{":
            break
        # 从 i 开始括号匹配 + 字符串感知
        scene_end = _match_balanced_brace(cleaned, i)
        if scene_end is None:
            # 不完整 scene 对象 → 停止
            break
        scene_str = cleaned[i:scene_end + 1]
        try:
            scene_obj = json.loads(scene_str)
            if isinstance(scene_obj, dict):
                scenes.append(scene_obj)
        except json.JSONDecodeError:
            # 这个 scene 本身坏了,跳过(往后找)
            pass
        i = scene_end + 1

    if not scenes:
        return None

    return {
        "global_theme": global_theme,
        "global_arc": global_arc,
        "scenes": scenes,
    }


def _match_balanced_brace(s: str, start: int) -> Optional[int]:
    """从 s[start] = '{' 开始,匹配到对应的 '}' 索引;字符串感知(不被 } in string 误导)。

    返回闭合 '}' 索引;若到 s 末尾仍未闭合 → None。
    """
    if s[start] != "{":
        return None
    depth = 0
    in_str = False
    escape = False
    for j in range(start, len(s)):
        c = s[j]
        if in_str:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return j
    return None


# ============================================================
# M6-fix3(2026-05-20)— 续生成(从断点接力)
# ============================================================

# 单次续生成的最大幕数(防再被截断;15 幕 ≈ 6000 tokens 输出)
CONTINUE_MAX_SCENES_PER_BATCH = 15


def continue_outline_generation(
    conn: sqlite3.Connection,
    sim_id: str,
) -> tuple[int, dict, bool]:
    """从当前 outline 的最后一幕之后继续生成,补完剩余幕。

    可被多次调用(若一次仍未补完)— 每次最多生成 CONTINUE_MAX_SCENES_PER_BATCH 幕。

    Returns:
      (new_scenes_inserted, llm_usage, fully_completed)
      fully_completed=True 表示已补齐到 total_scenes_planned;False 表示仍有缺

    Raises:
      ValueError: sim 无 outline / outline 已完整 / outline 状态不对
    """
    # 1. 拉 outline + 现有 scenes
    outline_row = fetch_one(
        conn,
        "SELECT * FROM simulation_outlines WHERE simulation_id=?",
        (sim_id,),
    )
    if outline_row is None:
        raise ValueError(f"sim {sim_id} 无 outline")
    if outline_row["state"] not in ("awaiting_user", "drafting"):
        raise ValueError(
            f"outline state={outline_row['state']},只有 awaiting_user / drafting 允许续生成"
        )

    outline_id = outline_row["id"]
    target_total = int(outline_row["total_scenes_planned"])
    global_theme = outline_row["global_theme"] or ""
    global_arc = outline_row["global_arc"] or ""

    scene_rows = fetch_all(
        conn,
        """SELECT * FROM outline_scenes
           WHERE outline_id=?
           ORDER BY scene_index ASC""",
        (outline_id,),
    )
    existing_count = len(scene_rows)
    if existing_count >= target_total:
        raise ValueError(
            f"outline 已有 {existing_count}/{target_total} 幕,无需续生成"
        )

    next_scene_index = existing_count
    remaining = target_total - existing_count
    max_this_batch = min(remaining, CONTINUE_MAX_SCENES_PER_BATCH)

    # 2. 拉 sim 信息 + 角色 + 项目场景(给 LLM 上下文)
    sim_row = fetch_one(conn, "SELECT * FROM simulations WHERE id=?", (sim_id,))
    if sim_row is None:
        raise ValueError(f"sim {sim_id} 不存在")
    project_id = sim_row["project_id"]
    project_row = fetch_one(
        conn, "SELECT name, type FROM projects WHERE id=?", (project_id,),
    )
    char_rows = fetch_all(
        conn,
        """SELECT id, name, identity, personality, is_protagonist
           FROM characters WHERE project_id=?""",
        (project_id,),
    )
    characters_input = [
        {
            "id": r["id"], "name": r["name"],
            "identity": (r["identity"] or "")[:200],
            "personality": (r["personality"] or "")[:200],
            "is_protagonist": bool(r["is_protagonist"]),
        }
        for r in char_rows
    ]
    scene_rows_project = fetch_all(
        conn,
        """SELECT name, description FROM project_scenes WHERE project_id=?
           ORDER BY appearance_chunk_count DESC LIMIT 30""",
        (project_id,),
    )
    available_scenes_input = [
        {"name": r["name"], "description": r["description"] or ""}
        for r in scene_rows_project
    ]

    # 3. 序列化已生成的幕作为 context(给 LLM 看以承接剧情)
    completed_scenes_payload = []
    for r in scene_rows:
        s = OutlineScene.from_row(r)
        completed_scenes_payload.append({
            "scene_index": s.scene_index,
            "scene_summary": s.scene_summary,
            "scene_purpose": s.scene_purpose,
            "location": s.location,
            "time_anchor": s.time_anchor,
            "characters_present": s.characters_present,
            "key_events": s.key_events,
            "key_props": s.key_props,
            "transition_from_last": s.transition_from_last,
        })

    user_input = {
        "sim_id": sim_id,
        "project_name": project_row["name"] if project_row else "",
        "project_genre": project_row["type"] if project_row else "",
        "divergence": (sim_row["divergence"] or "")[:500],
        "characters": characters_input,
        "available_project_scenes": available_scenes_input,
        "global_theme": global_theme,
        "global_arc": global_arc,
        "completed_scenes": completed_scenes_payload,
        "next_scene_index": next_scene_index,
        "target_total_scenes": target_total,
        "max_scenes_this_batch": max_this_batch,
    }

    # 4. 调 LLM
    system_prompt = _load_prompt("m6_outline_continue.md")
    try:
        raw_text, usage = call_llm_text(
            system_prompt, user_input,
            max_tokens=8000, temperature=0.7,
        )
    except Exception as e:  # noqa: BLE001
        err = f"continue LLM failed: {type(e).__name__}: {e}"[:300]
        logger.warning(f"continue_outline_generation sim={sim_id}: {err}")
        # 不 mark failed(用户仍可重试);只更新 error_message
        execute(
            conn,
            "UPDATE simulation_outlines SET error_message=?, updated_at=? WHERE id=?",
            (err, iso_now(), outline_id),
        )
        conn.commit()
        return 0, {"input_tokens": 0, "output_tokens": 0}, False

    # 5. 解析(完整 / partial 兜底,与首次生成同逻辑)
    parsed = _try_parse_complete_json(raw_text)
    if parsed is None:
        # partial extraction:这里只抽 scenes 数组(无 global_theme/arc)
        parsed = _partial_extract_continue_scenes(raw_text)

    if not isinstance(parsed, dict):
        execute(
            conn,
            "UPDATE simulation_outlines SET error_message=?, updated_at=? WHERE id=?",
            ("续生成解析失败", iso_now(), outline_id),
        )
        conn.commit()
        return 0, usage, False

    new_scenes_raw = parsed.get("scenes") or []
    if not isinstance(new_scenes_raw, list) or not new_scenes_raw:
        execute(
            conn,
            "UPDATE simulation_outlines SET error_message=?, updated_at=? WHERE id=?",
            ("续生成未返新 scenes", iso_now(), outline_id),
        )
        conn.commit()
        return 0, usage, False

    # 6. 落库(严格 scene_index 递增 + 重复跳过)
    now = iso_now()
    inserted = 0
    expected_idx = next_scene_index
    for scene_dict in new_scenes_raw:
        if not isinstance(scene_dict, dict):
            continue
        # 强制 scene_index 接续(LLM 可能给错号)
        scene_dict["scene_index"] = expected_idx
        try:
            _insert_outline_scene(conn, outline_id, expected_idx, scene_dict, now)
            inserted += 1
            expected_idx += 1
            if expected_idx >= target_total:
                # 已补齐到 target
                break
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"continue_outline_generation skip malformed scene "
                f"index={expected_idx}: {e}"
            )
            continue

    # 7. 更新 outline 主表
    final_count = existing_count + inserted
    fully_completed = final_count >= target_total
    new_error_msg = None
    if not fully_completed:
        new_error_msg = (
            f"⚠️ 已生成 {final_count}/{target_total} 幕;"
            f"可继续点'继续生成'再补,或直接编辑/批准"
        )
    execute(
        conn,
        """UPDATE simulation_outlines
           SET error_message=?, updated_at=?
           WHERE id=?""",
        (new_error_msg, iso_now(), outline_id),
    )
    conn.commit()
    return inserted, usage, fully_completed


def _partial_extract_continue_scenes(raw: str) -> Optional[dict]:
    """续生成的 partial extraction — 与 _partial_extract_outline 类似,
    但只抽 scenes 数组(续生成不再返 global_theme/arc)。
    """
    if not raw:
        return None
    cleaned = _strip_markdown_fence(raw)
    scenes_start = re.search(r'"scenes"\s*:\s*\[', cleaned)
    if not scenes_start:
        # 尝试直接以 [ 开头
        m = re.search(r'\[', cleaned)
        if not m:
            return None
        array_start = m.end()
    else:
        array_start = scenes_start.end()

    scenes: list[dict] = []
    i = array_start
    n = len(cleaned)
    while i < n:
        while i < n and cleaned[i] in " \t\n\r,":
            i += 1
        if i >= n or cleaned[i] != "{":
            break
        scene_end = _match_balanced_brace(cleaned, i)
        if scene_end is None:
            break
        scene_str = cleaned[i:scene_end + 1]
        try:
            obj = json.loads(scene_str)
            if isinstance(obj, dict):
                scenes.append(obj)
        except json.JSONDecodeError:
            pass
        i = scene_end + 1

    if not scenes:
        return None
    return {"scenes": scenes}


# ============================================================
# 查询接口
# ============================================================

def get_outline_with_scenes(
    conn: sqlite3.Connection,
    sim_id: str,
) -> tuple[SimulationOutline, list[OutlineScene]]:
    """拉某 sim 的 outline + 全部 outline_scenes(按 scene_index 升序)。

    Raises:
      ValueError: sim 没有 outline
    """
    outline_row = fetch_one(
        conn,
        "SELECT * FROM simulation_outlines WHERE simulation_id=?",
        (sim_id,),
    )
    if outline_row is None:
        raise ValueError(f"sim {sim_id} 无 outline")
    outline = SimulationOutline.from_row(outline_row)

    scene_rows = fetch_all(
        conn,
        """SELECT * FROM outline_scenes
           WHERE outline_id=?
           ORDER BY scene_index ASC""",
        (outline.id,),
    )
    scenes = [OutlineScene.from_row(r) for r in scene_rows]
    return outline, scenes


def get_outline_by_id(
    conn: sqlite3.Connection, outline_id: str,
) -> SimulationOutline | None:
    row = fetch_one(
        conn, "SELECT * FROM simulation_outlines WHERE id=?", (outline_id,),
    )
    if row is None:
        return None
    return SimulationOutline.from_row(row)
