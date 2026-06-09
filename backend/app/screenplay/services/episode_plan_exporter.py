"""分集方案导出器(2026-06-09,v2 加 mode + scene 内嵌入)。

3 种格式 × 3 种 mode = 9 种导出组合:

  格式:fountain / txt / yaml
  mode:
    - outline  仅大纲(集标题 / 钩子 / 元信息;scene_id 列表),文件最小
    - full     大纲 + 完整剧本(每集嵌入对应 scenes 的动作+对白)
    - script   仅剧本(每集 # Episode 标题下直接放 scene 内容,无 logline)

mode=full / script 需要传入 screenplay_dict(从 yaml_text 解析),
否则自动降级到 outline + 加 warning 注释。

设计:
  - 集间过渡:集头 logline(Action 块),集尾 cliffhanger + 下集预告(用户拍板)
  - scene 渲染规则:fountain 标准(INT./EXT. + 地点 + 时段 / Character 大写 / dialogue 缩进)
  - 跨用户隔离:导出函数本身无 user_id 概念,router 层校验
"""
from __future__ import annotations

import json
from typing import Any, Optional


# ============================================================
# Scene 渲染(fountain 格式 — 单 scene)
# ============================================================

def _render_scene_fountain(
    scene: dict,
    char_map: dict[str, str],
    loc_map: dict[str, str],
) -> str:
    """把单个 scene dict 渲染成 Fountain 文本片段。

    Args:
        scene: YAML 解析后的 scene 节点(heading + elements)
        char_map: character_id → name
        loc_map: location_id → name
    """
    out: list[str] = []

    # Scene Heading
    heading = scene.get("heading") or {}
    int_ext = str(heading.get("int_ext") or "INT").upper()
    loc_id = heading.get("location_id", "")
    loc_name = loc_map.get(loc_id, loc_id or "未命名")
    time_of_day = heading.get("time_of_day") or "日"
    out.append(f"{int_ext}. {loc_name} - {time_of_day}")
    out.append("")

    # 可选概要(boneyard 注释)
    summary = (scene.get("summary") or "").strip()
    if summary:
        out.append(f"[[ {summary} ]]")
        out.append("")

    # Elements
    elements = scene.get("elements") or []
    for el in elements:
        if not isinstance(el, dict):
            continue
        etype = el.get("type", "")
        text = (el.get("text") or "").strip()
        if not text:
            continue

        if etype == "action":
            out.append(text)
            out.append("")

        elif etype in ("dialogue", "voiceover"):
            char_id = el.get("character_id", "")
            char_name = char_map.get(char_id, char_id or "未知")
            voice_tag = ""
            if etype == "voiceover":
                vs = el.get("voice_source", "VO")
                voice_tag = " (V.O.)" if vs != "OS" else " (O.S.)"
            paren = (el.get("parenthetical") or "").strip()
            out.append(f"{char_name.upper()}{voice_tag}")
            if paren:
                out.append(f"({paren})")
            out.append(text)
            out.append("")

        elif etype == "transition":
            out.append(f"> {text} <")
            out.append("")

    return "\n".join(out)


def _render_scene_txt(
    scene: dict,
    char_map: dict[str, str],
    loc_map: dict[str, str],
) -> str:
    """把单个 scene dict 渲染成纯中文文本(给非剧本读者看)。"""
    out: list[str] = []

    heading = scene.get("heading") or {}
    loc_id = heading.get("location_id", "")
    loc_name = loc_map.get(loc_id, loc_id or "未命名地点")
    time_of_day = heading.get("time_of_day") or "日"
    int_ext = str(heading.get("int_ext") or "INT").upper()
    int_ext_cn = "内景" if int_ext == "INT" else "外景"
    out.append(f"  [{int_ext_cn} · {loc_name} · {time_of_day}]")
    out.append("")

    summary = (scene.get("summary") or "").strip()
    if summary:
        out.append(f"    {summary}")
        out.append("")

    elements = scene.get("elements") or []
    for el in elements:
        if not isinstance(el, dict):
            continue
        etype = el.get("type", "")
        text = (el.get("text") or "").strip()
        if not text:
            continue

        if etype == "action":
            out.append(f"    {text}")
        elif etype in ("dialogue", "voiceover"):
            char_id = el.get("character_id", "")
            char_name = char_map.get(char_id, char_id or "?")
            voice_tag = ""
            if etype == "voiceover":
                voice_tag = "(画外音)" if el.get("voice_source") != "OS" else "(场外音)"
            paren = (el.get("parenthetical") or "").strip()
            paren_text = f"({paren}) " if paren else ""
            out.append(f"    {char_name}{voice_tag}:{paren_text}{text}")
        elif etype == "transition":
            out.append(f"    → {text}")
        out.append("")

    return "\n".join(out)


# ============================================================
# 从 screenplay_dict 抽 scene + character / location maps
# ============================================================

def _build_lookup_maps(screenplay_dict: dict) -> tuple[dict, dict, dict]:
    """从完整 screenplay 解析返:
    - scene_map: scene_id → scene dict(有完整 heading + elements)
    - char_map: character_id → name
    - loc_map: location_id → name
    """
    scene_map: dict[str, dict] = {}
    for s in (screenplay_dict.get("scenes") or []):
        if isinstance(s, dict) and s.get("id"):
            scene_map[str(s["id"])] = s

    char_map: dict[str, str] = {}
    for c in (screenplay_dict.get("characters") or []):
        if isinstance(c, dict) and c.get("id"):
            char_map[str(c["id"])] = str(c.get("name", c["id"]))

    loc_map: dict[str, str] = {}
    for l in (screenplay_dict.get("locations") or []):
        if isinstance(l, dict) and l.get("id"):
            loc_map[str(l["id"])] = str(l.get("name", l["id"]))

    return scene_map, char_map, loc_map


# ============================================================
# TXT — 人读
# ============================================================

def export_to_txt(
    plan_summary: dict,
    plan_data: dict,
    mode: str = "outline",
    screenplay_dict: Optional[dict] = None,
) -> str:
    """生成纯文本分集方案。

    mode:
      - outline  仅大纲(scene_id 列表)
      - full     大纲 + 每集嵌入剧本内容
      - script   仅剧本(集标题 + 集头 logline + scenes + 集尾钩子,无大纲元信息)
    """
    lines: list[str] = []
    sep_double = "═" * 60
    sep_single = "─" * 60

    # 如果用户要 full / script 但没传剧本数据,降级
    if mode in ("full", "script") and not screenplay_dict:
        mode = "outline"
        lines.append("⚠ 注:剧本尚未生成,本次仅导出大纲。")
        lines.append("  请先在编辑器跑「生成剧本」流水线,再重新导出。")
        lines.append("")

    scene_map, char_map, loc_map = (
        _build_lookup_maps(screenplay_dict) if screenplay_dict else ({}, {}, {})
    )

    # 头部(script 模式简化)
    if mode != "script":
        lines.append(sep_double)
        lines.append(f"  分集方案 · {plan_summary.get('scheme_name', '未命名')}")
        lines.append(sep_double)
        lines.append("")
        lines.append(f"  预设档:{plan_summary.get('preset', '-')}")
        lines.append(f"  目标单集时长:{plan_summary.get('target_minutes', 0)} 分钟")
        lines.append(f"  推荐视角:{_persp_label(plan_summary.get('recommended_perspective'))}")
        lines.append(f"  集数:{plan_summary.get('episode_count', 0)} 集")
        lines.append(f"  场景总数:{plan_summary.get('scene_count', 0)} 场")
        ct = (plan_summary.get('created_at') or '-')[:19].replace('T', ' ')
        lines.append(f"  创建时间:{ct}")
        lines.append("")
    else:
        lines.append(f"《{plan_summary.get('scheme_name', '未命名')}》")
        lines.append("")

    # 推荐视角分集详情
    recommended = plan_summary.get("recommended_perspective") or "rhythm"
    perspectives = plan_data.get("perspectives") or []
    target = next(
        (p for p in perspectives if p.get("perspective") == recommended),
        perspectives[0] if perspectives else None,
    )

    if target:
        episodes = target.get("episodes") or []
        if mode != "script":
            lines.append(sep_single)
            lines.append(f"  视角:{target.get('label', recommended)}")
            if target.get("rationale"):
                lines.append(f"  说明:{target['rationale']}")
            lines.append(sep_single)
            lines.append("")

        for idx, ep in enumerate(episodes, start=1):
            title = ep.get("title") or f"第 {idx} 集"
            est_min = ep.get("est_minutes", 0)
            scene_ids = ep.get("scene_ids") or []
            scene_count = len(scene_ids)
            ch_first = ep.get("first_chapter")
            ch_last = ep.get("last_chapter")
            ch_range = (
                f"第 {ch_first}-{ch_last} 章"
                if ch_first is not None and ch_last is not None
                else "(未知章节)"
            )

            # 集标题
            lines.append("")
            lines.append(f"  ━━━ 【第 {idx} 集】 {title} ━━━")
            lines.append(f"      时长 {est_min} 分钟 · {scene_count} 场 · {ch_range}")
            lines.append("")

            # 集头 logline(用户拍板)
            if ep.get("logline"):
                lines.append(f"  📖 本集梗概:{ep['logline']}")
                lines.append("")

            if mode == "outline":
                # 只列 scene_id
                lines.append(f"  场景:")
                for sid in scene_ids[:5]:
                    lines.append(f"    · {sid}")
                if len(scene_ids) > 5:
                    lines.append(f"    … 还有 {len(scene_ids) - 5} 场")
            else:
                # full / script:嵌入 scene 完整内容
                for sid in scene_ids:
                    scene = scene_map.get(sid)
                    if scene:
                        lines.append(_render_scene_txt(scene, char_map, loc_map))
                    else:
                        lines.append(f"    (场景 {sid} 在剧本中未找到)")
                        lines.append("")

            # 集尾钩子 + 下集预告(用户拍板)
            if ep.get("cliffhanger_text"):
                lines.append(f"  🎬 集尾钩子:{ep['cliffhanger_text']}")
                lines.append("")
            if ep.get("next_episode_preview"):
                lines.append(f"  📺 下集预告:{ep['next_episode_preview']}")
                lines.append("")

    # 其他视角参考(script 模式不要)
    if mode != "script" and target:
        other_perspectives = [
            p for p in perspectives if p.get("perspective") != recommended
        ]
        if other_perspectives:
            lines.append("")
            lines.append(sep_single)
            lines.append("  其他视角参考")
            lines.append(sep_single)
            for p in other_perspectives:
                label = p.get("label", p.get("perspective", "?"))
                ep_count = len(p.get("episodes") or [])
                lines.append(f"    · {label}:{ep_count} 集")
                if p.get("rationale"):
                    lines.append(f"      ({p['rationale']})")
            lines.append("")

    lines.append("")
    lines.append(sep_double)
    lines.append(f"  浑晶剧创态 · 分集 {_mode_label(mode)}")
    lines.append(sep_double)
    return "\n".join(lines)


# ============================================================
# Fountain — 行业标准格式
# ============================================================

def export_to_fountain(
    plan_summary: dict,
    plan_data: dict,
    mode: str = "outline",
    screenplay_dict: Optional[dict] = None,
) -> str:
    """生成 Fountain 格式分集大纲 / 完整剧本。

    mode:
      - outline  仅大纲(section heading + logline + cliffhanger,无 scene 内容)
      - full     大纲 + 每集嵌入对应 scenes 完整 fountain
      - script   仅剧本(无 logline / cliffhanger 等大纲注释)
    """
    lines: list[str] = []

    # 降级
    if mode in ("full", "script") and not screenplay_dict:
        mode = "outline"

    scene_map, char_map, loc_map = (
        _build_lookup_maps(screenplay_dict) if screenplay_dict else ({}, {}, {})
    )

    title = plan_summary.get("scheme_name", "Untitled Episode Plan")
    preset = plan_summary.get("preset", "")

    # Title page
    lines.append(f"Title: {title}")
    lines.append("Source: 浑晶剧创态")
    lines.append(f"Notes: {preset} · 目标 {plan_summary.get('target_minutes', 0)} 分钟/集 · 模式:{_mode_label(mode)}")
    lines.append(f"Episodes: {plan_summary.get('episode_count', 0)}")
    lines.append("")
    lines.append("====")
    lines.append("")

    recommended = plan_summary.get("recommended_perspective") or "rhythm"
    perspectives = plan_data.get("perspectives") or []
    target = next(
        (p for p in perspectives if p.get("perspective") == recommended),
        perspectives[0] if perspectives else None,
    )

    if not target:
        lines.append(".NO_EPISODES")
        return "\n".join(lines)

    episodes = target.get("episodes") or []
    for idx, ep in enumerate(episodes, start=1):
        ep_title = ep.get("title") or f"Episode {idx}"
        # Fountain section heading
        lines.append(f"# Episode {idx} · {ep_title}")
        lines.append("")

        # 元信息(script 模式跳过)
        if mode != "script":
            est_min = ep.get("est_minutes", 0)
            scene_count = len(ep.get("scene_ids") or [])
            lines.append(f"_约 {est_min} 分钟 · {scene_count} 场_")
            lines.append("")

        # 集头 logline(集间过渡 — 用户拍板)
        if ep.get("logline"):
            lines.append(ep["logline"])
            lines.append("")

        # 主体
        scene_ids = ep.get("scene_ids") or []
        if mode == "outline":
            # 只列 scene id(boneyard 注释格式)
            if scene_ids:
                lines.append("[[Scenes: " + ", ".join(scene_ids) + "]]")
                lines.append("")
        else:
            # full / script:嵌入 fountain scenes
            for sid in scene_ids:
                scene = scene_map.get(sid)
                if scene:
                    lines.append(_render_scene_fountain(scene, char_map, loc_map))
                else:
                    lines.append(f"[[ MISSING SCENE: {sid} ]]")
                    lines.append("")

        # 集尾过渡(集间过渡 — 用户拍板)
        if ep.get("cliffhanger_text"):
            lines.append(f"**集尾钩子:** {ep['cliffhanger_text']}")
            lines.append("")
        if ep.get("next_episode_preview"):
            lines.append(f"_下集预告:{ep['next_episode_preview']}_")
            lines.append("")

    return "\n".join(lines)


# ============================================================
# YAML — 开发者 / 备份
# ============================================================

def export_to_yaml(
    plan_summary: dict,
    plan_data: dict,
    mode: str = "outline",
    screenplay_dict: Optional[dict] = None,
) -> str:
    """生成 YAML 格式。

    mode 在 YAML 模式下控制是否嵌入 screenplay_data。outline 不嵌,full/script 嵌。
    """
    payload: dict[str, Any] = {
        "scheme_name": plan_summary.get("scheme_name"),
        "preset": plan_summary.get("preset"),
        "target_minutes": plan_summary.get("target_minutes"),
        "recommended_perspective": plan_summary.get("recommended_perspective"),
        "episode_count": plan_summary.get("episode_count"),
        "scene_count": plan_summary.get("scene_count"),
        "created_at": plan_summary.get("created_at"),
        "updated_at": plan_summary.get("updated_at"),
        "export_mode": mode,
        "plan_data": plan_data,
    }
    if mode in ("full", "script") and screenplay_dict:
        # 嵌入完整剧本数据(scenes + characters + locations)
        payload["screenplay_data"] = {
            "scenes": screenplay_dict.get("scenes") or [],
            "characters": screenplay_dict.get("characters") or [],
            "locations": screenplay_dict.get("locations") or [],
        }

    try:
        import yaml
        return yaml.safe_dump(
            payload,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    except ImportError:
        return json.dumps(payload, ensure_ascii=False, indent=2)


# ============================================================
# 工具
# ============================================================

def _persp_label(perspective: Any) -> str:
    return {
        "rhythm": "节奏视角",
        "hook": "钩子视角",
        "arc": "角色弧光视角",
    }.get(str(perspective), str(perspective or "未指定"))


def _mode_label(mode: str) -> str:
    """mode 中文标签(给文件头部显示用)"""
    return {
        "outline": "仅大纲",
        "full": "大纲 + 完整剧本",
        "script": "仅剧本",
    }.get(mode, mode)


def safe_filename(name: str, mode: str = "") -> str:
    """安全文件名 — 去掉跨平台敏感字符,可选加 mode 后缀"""
    bad = '/\\:*?"<>|\n\r\t'
    clean = "".join("_" if c in bad else c for c in name).strip() or "episode_plan"
    if mode and mode != "outline":
        clean = f"{clean}-{mode}"
    return clean


__all__ = [
    "export_to_txt",
    "export_to_fountain",
    "export_to_yaml",
    "safe_filename",
]
