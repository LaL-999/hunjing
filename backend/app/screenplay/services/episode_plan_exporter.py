"""分集方案导出器(2026-06-09)。

3 种格式:
  - txt        人读用 — 集标题 + 分隔线 + 该集场景列表 + 评分摘要
  - fountain   行业标准 — 在 scene heading 前插 `# Episode N · 标题` 注释
  - yaml       开发者/备份 — 直接 dump 完整方案数据

设计:
  - 复用 episode_plan_store.get_plan 拿 plan_data(含 3 视角完整数据)
  - 导出时只选**推荐视角**的 episodes,其他视角作为元信息附在文件尾部
  - fountain 的 episode 注释借用 Final Draft 兼容的 `#` 标记(不破坏剧本本体)
"""
from __future__ import annotations

import json
from typing import Any


# ============================================================
# TXT — 人读
# ============================================================

def export_to_txt(plan_summary: dict, plan_data: dict) -> str:
    """生成纯文本分集方案,中文排版,适合复制粘贴看。

    Args:
        plan_summary: EpisodePlanSummary.to_dict() 含 scheme_name / preset / created_at 等
        plan_data: MultiPerspectivePlan 完整数据(3 视角)
    """
    lines: list[str] = []
    sep_double = "═" * 60
    sep_single = "─" * 60

    # 头部
    lines.append(sep_double)
    lines.append(f"  分集方案 · {plan_summary.get('scheme_name', '未命名')}")
    lines.append(sep_double)
    lines.append("")
    lines.append(f"  预设档:{plan_summary.get('preset', '-')}")
    lines.append(f"  目标单集时长:{plan_summary.get('target_minutes', 0)} 分钟")
    lines.append(f"  推荐视角:{_persp_label(plan_summary.get('recommended_perspective'))}")
    lines.append(f"  集数:{plan_summary.get('episode_count', 0)} 集")
    lines.append(f"  场景总数:{plan_summary.get('scene_count', 0)} 场")
    lines.append(f"  创建时间:{plan_summary.get('created_at', '-')[:19].replace('T', ' ')}")
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
            lines.append(f"  【{idx}】 {title}")
            lines.append(f"      时长 {est_min} 分钟 · {scene_count} 场 · {ch_range}")
            if ep.get("logline"):
                lines.append(f"      故事概要:{ep['logline']}")
            if ep.get("cliffhanger_text"):
                lines.append(f"      集尾钩子:{ep['cliffhanger_text']}")
            if ep.get("next_episode_preview"):
                lines.append(f"      下集预告:{ep['next_episode_preview']}")
            # 场景列表(最多列前 5 个,过长截断)
            for sid in scene_ids[:5]:
                lines.append(f"        · {sid}")
            if len(scene_ids) > 5:
                lines.append(f"        … 还有 {len(scene_ids) - 5} 场")
            lines.append("")
    else:
        lines.append("(无可显示分集)")
        lines.append("")

    # 其他视角概要
    other_perspectives = [
        p for p in perspectives if p.get("perspective") != recommended
    ]
    if other_perspectives:
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

    lines.append(sep_double)
    lines.append("  浑晶剧创态 · 分集规划")
    lines.append(sep_double)
    return "\n".join(lines)


# ============================================================
# Fountain — 行业标准格式
# ============================================================

def export_to_fountain(plan_summary: dict, plan_data: dict) -> str:
    """生成 Fountain 格式分集大纲。

    Fountain 是 Hollywood spec script 标准,Final Draft / WriterDuet 等
    剧本软件都支持。这里不导出"场景+对白"(那个剧本本体已经在
    sp_screenplays 用 export.fountain 导出),只导出"分集大纲"。

    用 `#` 标记 act / sequence(Fountain 标准),适合作为
    项目大纲文档。
    """
    lines: list[str] = []
    title = plan_summary.get("scheme_name", "Untitled Episode Plan")
    preset = plan_summary.get("preset", "")

    # Title page
    lines.append(f"Title: {title}")
    lines.append("Source: 浑晶剧创态")
    lines.append(f"Notes: {preset} · 目标 {plan_summary.get('target_minutes', 0)} 分钟/集")
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
        # 元信息作为 Action 块
        est_min = ep.get("est_minutes", 0)
        scene_count = len(ep.get("scene_ids") or [])
        lines.append(f"_约 {est_min} 分钟 · {scene_count} 场_")
        lines.append("")
        if ep.get("logline"):
            lines.append(ep["logline"])
            lines.append("")
        if ep.get("cliffhanger_text"):
            lines.append(f"**集尾钩子:** {ep['cliffhanger_text']}")
            lines.append("")
        if ep.get("next_episode_preview"):
            lines.append(f"_下集预告:{ep['next_episode_preview']}_")
            lines.append("")
        # 场景 ID 列表(给后期对接剧本本体)
        scene_ids = ep.get("scene_ids") or []
        if scene_ids:
            lines.append("[[Scenes: " + ", ".join(scene_ids) + "]]")
            lines.append("")

    return "\n".join(lines)


# ============================================================
# YAML — 开发者 / 备份
# ============================================================

def export_to_yaml(plan_summary: dict, plan_data: dict) -> str:
    """生成 YAML 格式 — 完整 plan_data + meta。

    用 PyYAML 可读性 dump,不引依赖时手写嵌套。
    """
    try:
        import yaml  # 大多平台都装了 pyyaml,失败时回退 JSON
        return yaml.safe_dump(
            {
                "scheme_name": plan_summary.get("scheme_name"),
                "preset": plan_summary.get("preset"),
                "target_minutes": plan_summary.get("target_minutes"),
                "recommended_perspective": plan_summary.get("recommended_perspective"),
                "episode_count": plan_summary.get("episode_count"),
                "scene_count": plan_summary.get("scene_count"),
                "created_at": plan_summary.get("created_at"),
                "updated_at": plan_summary.get("updated_at"),
                "plan_data": plan_data,
            },
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
        )
    except ImportError:
        # pyyaml 不可用时回退 JSON(用户可改后缀)
        return json.dumps(
            {
                "summary": plan_summary,
                "plan_data": plan_data,
            },
            ensure_ascii=False,
            indent=2,
        )


# ============================================================
# 工具
# ============================================================

def _persp_label(perspective: Any) -> str:
    """rhythm / hook / arc → 中文 label"""
    return {
        "rhythm": "节奏视角",
        "hook": "钩子视角",
        "arc": "角色弧光视角",
    }.get(str(perspective), str(perspective or "未指定"))


def safe_filename(name: str) -> str:
    """把方案名转成文件名安全字符 — 去掉 /, \\, :, *, ?, ", <, >, |"""
    bad = '/\\:*?"<>|\n\r\t'
    return "".join("_" if c in bad else c for c in name).strip() or "episode_plan"


__all__ = [
    "export_to_txt",
    "export_to_fountain",
    "export_to_yaml",
    "safe_filename",
]
