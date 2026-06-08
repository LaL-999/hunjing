"""author_compass_util — P3 Day 2(2026-05-26).

生成"作者指南针" prompt 注入段,evolution + quick 双模式共用。

与 P0X.2 physical_constraints_util 同构 — 把 author_compass DB 数据
翻译成 LLM 能消费的硬铁律 prompt 段。

注入点:
  - evolution mode → hard_constraints.py section 6
  - quick mode     → simulation_service.py composer prompt prepend

设计原则:
  - **0 schema 改动**(已用 P3 Day 1 的 author_compass 表)
  - **LLM-first**:把指南针数据翻译成具体写作建议 + 雷区
  - **双轨融合**:外部研究(流派/主题/标签)+ 内部反推(句长/对白率/感官/意象)
  - **锁定优先**:user_locked=1 → 读 final_compass_json(用户拍板版本)
                 user_locked=0 → 读 external + internal 合并

created 2026-05-26 / P3 Day 2
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _format_list(values: Any, max_items: int = 5) -> str:
    """把 list / str 格式化成 '· A · B · C' 形式;非 list 返空。"""
    if isinstance(values, str):
        return values
    if not isinstance(values, list):
        return ""
    parts = [str(v).strip() for v in values[:max_items] if str(v).strip()]
    return " · ".join(parts)


def _build_external_section(external: Optional[dict]) -> list[str]:
    """格式化外部研究轨段落。"""
    if not external or not isinstance(external, dict):
        return []
    lines: list[str] = []

    genre = external.get("流派")
    era = external.get("年代")
    culture = external.get("文化背景")
    if genre or era or culture:
        bits = [str(x) for x in (genre, era, culture) if x]
        lines.append(f"  • 文学坐标:{' / '.join(bits)}")

    if external.get("主题偏好"):
        lines.append(f"  • 主题偏好:{_format_list(external['主题偏好'])}")
    if external.get("风格标签"):
        lines.append(f"  • 风格标签:{_format_list(external['风格标签'])}")
    if external.get("代表手法"):
        lines.append(f"  • 代表手法:{_format_list(external['代表手法'])}")
    if external.get("雷区"):
        lines.append(f"  ✗ 雷区(本作家**绝对不写**):{_format_list(external['雷区'], max_items=6)}")
    return lines


def _build_internal_section(internal: Optional[dict]) -> list[str]:
    """格式化内部反推轨段落(量化参数)。"""
    if not internal or not isinstance(internal, dict):
        return []
    lines: list[str] = []

    # 句长分布
    sj = internal.get("句长")
    if isinstance(sj, dict):
        s = sj.get("短句占比")
        m = sj.get("中句占比")
        l = sj.get("长句占比")
        comment = sj.get("评注", "")
        if any(x is not None for x in (s, m, l)):
            bits = []
            if s is not None:
                bits.append(f"短句 {int(s * 100)}%")
            if m is not None:
                bits.append(f"中句 {int(m * 100)}%")
            if l is not None:
                bits.append(f"长句 {int(l * 100)}%")
            extra = f" — {comment}" if comment else ""
            lines.append(f"  • 句长分布:{' / '.join(bits)}{extra}")

    # 对白率
    dr = internal.get("对白率")
    if isinstance(dr, dict):
        ratio = dr.get("比例")
        comment = dr.get("评注", "")
        if ratio is not None:
            extra = f" — {comment}" if comment else ""
            lines.append(f"  • 对白率:约 {int(ratio * 100)}%{extra}")

    # 感官比例
    sense = internal.get("感官比例")
    if isinstance(sense, dict):
        bits = []
        for key in ("视觉", "听觉", "嗅觉", "触觉", "味觉"):
            v = sense.get(key)
            if v is not None and float(v) >= 0.15:
                bits.append(f"{key} {int(float(v) * 100)}%")
        comment = sense.get("评注", "")
        if bits:
            extra = f" — {comment}" if comment else ""
            lines.append(f"  • 感官主导:{' / '.join(bits)}{extra}")

    # 段落节奏
    para = internal.get("段落节奏")
    if isinstance(para, dict):
        avg = para.get("平均段长字数")
        ratio = para.get("短长段配比")
        comment = para.get("评注", "")
        bits = []
        if avg:
            bits.append(f"平均段长 {avg} 字")
        if ratio:
            bits.append(f"短长配比 {ratio}")
        if bits:
            extra = f" — {comment}" if comment else ""
            lines.append(f"  • 段落节奏:{' / '.join(bits)}{extra}")

    # 意象偏好(关键 — narrator 选意象时优先用这些)
    imgs = internal.get("意象偏好")
    if isinstance(imgs, list) and imgs:
        lines.append(
            f"  ★ 推荐意象(本作家高频用):{_format_list(imgs, max_items=8)}"
        )

    # 视角
    pov = internal.get("视角")
    if isinstance(pov, dict):
        person = pov.get("人称")
        scope = pov.get("全知或限知")
        comment = pov.get("评注", "")
        if person or scope:
            bits = []
            if person:
                bits.append(person)
            if scope:
                bits.append(scope)
            extra = f" — {comment}" if comment else ""
            lines.append(f"  • 视角:{' '.join(bits)}{extra}")

    # 基调
    mood = internal.get("基调")
    if isinstance(mood, dict):
        feel = mood.get("情感色彩")
        pace = mood.get("节奏感")
        comment = mood.get("评注", "")
        bits = [x for x in (feel, pace) if x]
        if bits:
            extra = f" — {comment}" if comment else ""
            lines.append(f"  • 整体基调:{' / '.join(bits)}{extra}")

    # P4(2026-05-27)— 身体描写尺度(灵魂续写关键 — 决定续作是否保留原作肉体感风骨)
    body = internal.get("身体描写尺度")
    if isinstance(body, dict):
        freq = body.get("频率")
        explicit = body.get("直白度")
        attitude = body.get("态度")
        function = body.get("功能")
        comment = body.get("评注", "")

        if freq or explicit or attitude or function:
            # 频率 → 中文友好描述
            freq_desc = {
                "none": "完全不写",
                "rare": "极少出现(全书 1-2 处)",
                "occasional": "偶尔出现(< 1 次 / 万字)",
                "frequent": "频繁出现(≥ 1 次 / 万字)",
            }.get(freq or "", freq or "")
            # 直白度 → 中文友好描述
            explicit_desc = {
                "absent": "完全不涉",
                "clothed-only": "只写穿着仪态",
                "metaphorical": "用比喻代替(如花似玉)",
                "clinical-detached": "客观冷峻(医学式)",
                "sensual-implicit": "感官隐晦(温度/气味/触觉)",
                "direct-detailed": "直接细节",
            }.get(explicit or "", explicit or "")
            # 态度 → 中文
            attitude_desc = {
                "absent": "—",
                "romantic": "浪漫化",
                "matter-of-fact": "平淡如实",
                "tragic": "悲剧色彩",
                "clinical": "冷峻客观",
                "voyeuristic": "窥视客体化",
            }.get(attitude or "", attitude or "")
            # 功能 → 中文
            function_desc = {
                "absent": "—",
                "character-development": "推进人物刻画",
                "plot-driving": "推进情节",
                "atmospheric": "营造氛围",
                "thematic-symbolic": "主题象征",
            }.get(function or "", function or "")

            extra = f" — {comment}" if comment else ""
            lines.append(
                f"  • 身体描写尺度:{freq_desc} / {explicit_desc} / "
                f"{attitude_desc} / {function_desc}{extra}"
            )

    return lines


def build_author_compass_block(
    conn: sqlite3.Connection,
    project_id: str,
) -> str:
    """生成 prompt 注入段,evolution / quick 双模式共用.

    Args:
      conn: sqlite 连接
      project_id: 项目 id

    Returns:
      完整 prompt 注入文本(可空 — 若没生成过指南针 / 两轨都失败)。
      格式:【作者指南针(本作风格基线)】+ 外部研究 + 内部反推 + 4 条铁律

    用法:
      block = build_author_compass_block(conn, sim.project_id)
      if block:
          system_prompt = block + "\\n\\n" + system_prompt   # prepend
    """
    try:
        from app.services.author_compass_service import get_compass
        compass = get_compass(conn, project_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"build_author_compass_block: get_compass failed: {e}")
        return ""

    if compass is None:
        return ""

    # 选数据源:锁定 → final_compass;否则用 external + internal
    if compass.user_locked and compass.final_compass:
        # 用户已拍板的最终版本(可能是合并 + 修改后的自定义结构)
        external_part = compass.final_compass.get("external_profile") or compass.final_compass.get("外部研究")
        internal_part = compass.final_compass.get("internal_metrics") or compass.final_compass.get("内部反推")
        # 如果 final 是扁平结构(用户直接改了 external_profile + internal_metrics 字段),
        # 也兼容:把整个 final 当 external_part
        if external_part is None and internal_part is None:
            external_part = compass.final_compass
            internal_part = None
    else:
        external_part = compass.external_profile
        internal_part = compass.internal_metrics

    ext_lines = _build_external_section(external_part)
    int_lines = _build_internal_section(internal_part)
    if not ext_lines and not int_lines:
        return ""

    # 标题 + 作家名 + 作品名
    header_bits = []
    if compass.author_name:
        header_bits.append(compass.author_name)
    if compass.work_title:
        header_bits.append(f"《{compass.work_title}》")
    header_suffix = f"({' '.join(header_bits)})" if header_bits else ""

    lines: list[str] = [
        f"【作者指南针 — 本作风格基线{header_suffix}】",
    ]
    if ext_lines:
        lines.append("· 外部研究(作家学术定位):")
        lines.extend(ext_lines)
    if int_lines:
        lines.append("· 内部反推(原作文风量化):")
        lines.extend(int_lines)

    lines.append(
        "\n  铁律 — 续作创作必须:\n"
        "  1. **风格贴合**:句长 / 对白率 / 感官主导 / 段落节奏 与上方量化参数误差 ≤ 15%\n"
        "     (短句占比 55% → 续作短句 40-70% 范围内合规)\n"
        "  2. **意象优先**:描写时**优先选用**「推荐意象」清单中的物件(雪 / 银河 / 镜 等),\n"
        "     避免引入与该作家风格冲突的现代意象(手机 / 霓虹 / 高架桥...)\n"
        "  3. **雷区严守**:外部研究的「雷区」条目**绝对不许写**(违反 = 风格漂移)\n"
        "  4. **基调一致**:整体情感色彩 / 节奏感与「内部反推 · 基调」保持一致;\n"
        "     不许突然变成「热烈快节奏」或「幽默调侃」等冲突基调\n"
        "  5. **身体描写尺度对齐**(P4):若上方有「身体描写尺度」,续作必须严守:\n"
        "     - 频率 none/rare → 几乎不写;频率 occasional/frequent → 允许相应密度\n"
        "     - 直白度 absent/clothed-only/metaphorical → 不许写直接身体细节\n"
        "     - 直白度 sensual-implicit → 用温度/气味/触觉氛围,避免直白\n"
        "     - 直白度 direct-detailed → 可坦率书写,但仍按原作态度\n"
        "     - 态度 + 功能 → 决定语气和作用,不许偏离\n"
        "     **铁律核心**:含蓄原作不许搞出露骨,直白原作不许回避绕过 — 二者都是"
        "灵魂错位\n"
        "  普适性:任何作家任何作品都适用 — 数据由 P3 双轨 LLM 实测得出"
    )
    return "\n".join(lines)


__all__ = ["build_author_compass_block"]
