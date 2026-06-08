"""快速模式反复读检测 + retry 闸门(2026-06-02).

起源:用户给的《网恋风云》4520 字快速模式产物实测:
  - "喉结上下滚动" / "喉结动了动" 累计 ≥ 10 次
  - "指尖发抖" / "指节发白" / "指甲掐进掌心" 累计 ≥ 15 次
  - "路灯昏黄" / "夜风卷起碎纸片" 累计 ≥ 6 次
  - "同一焦点问题"(梁淼排第几 / 你心里装多少人)反复 10+ 轮

  快速模式不走 consistency_checker(只灵魂续写跑),所以全篇通过 → 用户读起来像复读机.

设计:
  - 预定义"高风险词族"分类(身体动作 / 环境意象 / 情绪句式)
  - 计算"族级总频次"(同族不同短语合并计)
  - 超阈值 → critical violation
  - 触发 composer 一次 retry(加 retry hint 告知 LLM 哪些族超限,要求换近义)

阈值哲学:
  - 短篇 4000-6000 字:同族 ≤ 4 次(用户报告"7-8 次明显")
  - 中长 6000-15000 字:同族 ≤ 6 次
  - 单一具体短语 ≤ 2 次(无论篇幅多长)

retry 决策:
  - 任一 critical → retry 1 次(快速模式只 retry 1 次,避免成本翻倍)
  - retry hint 告诉 LLM 具体哪些词超频 + 替代库(从 composer.md 铁律 12 引用)
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


# ============================================================
# 高风险词族 — 中文身体动作 / 环境意象 / 情绪句式
# ============================================================

# 同族内不同短语合并计频次(LLM 常在同一族内反复,如喉结滚 + 喉咙紧 + 咽口唾沫)
WORD_FAMILIES: dict[str, list[str]] = {
    # 喉咙/吞咽族
    "喉咙紧绷": [
        "喉结上下滚动", "喉结滚", "喉结动了动", "咽了口唾沫",
        "喉咙发紧", "嗓子干哑", "喉间发涩", "干咽一下",
    ],
    # 手部紧张族
    "手部紧张": [
        "指节发白", "指尖发抖", "指尖颤抖",
        "攥紧拳头", "指甲掐进", "指甲嵌进", "指甲抠进", "指甲扣进",
        "手指蜷缩", "手指蜷起", "手心冒汗", "掌心冒汗", "手指紧紧",
        "指尖收紧", "手指收紧",
    ],
    # 视线躲避族
    "视线躲避": [
        "目光躲闪", "眼神游移", "眼神躲闪", "视线躲闪",
        "不敢直视", "不敢看", "目光低垂", "垂下眼睑",
        "目光朝", "目光移开",
    ],
    # 情绪体感族
    "情绪体感": [
        "心里咯噔", "胸口发闷", "胸口起伏", "胸口剧烈",
        "心里揪了", "心里像被", "心里堵着", "后背发凉",
        "耳根烧", "耳根红",
    ],
    # 夜晚/路灯族
    "夜晚路灯": [
        "路灯昏黄", "路灯", "灯光昏黄", "昏黄的光",
        "夜风卷", "夜风裹", "夜风吹",
        "树影摇晃", "树影",
    ],
    # 走廊/光线变暗族
    "光线变暗": [
        "走廊光线", "光线暗", "光线渐暗",
        "夕阳沉入", "夕阳沉落", "天色暗",
        "暗下来",
    ],
    # 嘴唇/发声动作族
    "嘴唇动作": [
        "嘴唇翕动", "嘴唇微张", "张了张嘴",
        "嘴唇抿紧", "咬住下唇", "咬下唇", "咬唇",
    ],
}


# 关键单一短语阈值(无论篇幅都不许多用)
SPECIFIC_PHRASE_MAX = 2

# 词族阈值(按 narrative 总字数浮动)
def _family_threshold(narrative_chars: int) -> int:
    """根据字数算同族阈值.

    短篇 < 6000:阈值 4(用户实测 "7-8 次明显" 这里要更严)
    中长 6000-15000:阈值 6
    长篇 ≥ 15000:阈值 9
    """
    if narrative_chars < 6000:
        return 4
    if narrative_chars < 15000:
        return 6
    return 9


@dataclass
class RepetitionViolation:
    family: str                        # 词族名(如"喉咙紧绷")
    total_count: int                   # 族级总频次
    threshold: int                     # 触发阈值
    top_phrases: list[tuple[str, int]] # 族内 top-3 短语 + 频次
    severity: str                      # "critical" 触发 retry


def _count_phrase_occurrences(text: str, phrase: str) -> int:
    """统计 phrase 在 text 中(非重叠)出现次数."""
    if not phrase or not text:
        return 0
    return text.count(phrase)


def check_full_narrative_repetition(
    narrative: str,
) -> list[RepetitionViolation]:
    """扫描整篇 narrative,返回所有超阈值的词族违规.

    用法:
        violations = check_full_narrative_repetition(narrative)
        if violations:
            # 触发 retry,把 violations 转成 retry hint
            ...
    """
    if not narrative or len(narrative) < 200:
        return []

    threshold = _family_threshold(len(narrative))
    violations: list[RepetitionViolation] = []

    for family, phrases in WORD_FAMILIES.items():
        # 1. 算族级总频次(各 phrase 单独 count 后相加)
        phrase_counts: list[tuple[str, int]] = []
        total = 0
        for ph in phrases:
            cnt = _count_phrase_occurrences(narrative, ph)
            if cnt > 0:
                phrase_counts.append((ph, cnt))
                total += cnt

        # 2. 单短语超 SPECIFIC_PHRASE_MAX 也触发(即使族级未超)
        single_phrase_violation = False
        for ph, cnt in phrase_counts:
            if cnt > SPECIFIC_PHRASE_MAX:
                single_phrase_violation = True
                break

        if total > threshold or single_phrase_violation:
            # 排序 top-3
            phrase_counts.sort(key=lambda x: -x[1])
            violations.append(RepetitionViolation(
                family=family,
                total_count=total,
                threshold=threshold,
                top_phrases=phrase_counts[:3],
                severity="critical",
            ))

    return violations


def build_retry_hint_from_violations(
    violations: list[RepetitionViolation],
) -> str:
    """把 violations 转成 LLM 能消费的 retry 提示文本.

    塞到 composer user_prompt 末尾,告知 LLM "上一次产出哪里超频,这次必须换".
    """
    if not violations:
        return ""

    lines = [
        "",
        "【⚠ 上次产出反复读检测 — 必须修正(治「全篇反复用同一组动作」瑕疵)】",
        "",
    ]
    for v in violations:
        top_phrases_text = " / ".join(
            f"「{ph}」×{cnt}" for ph, cnt in v.top_phrases
        )
        lines.append(
            f"  · **{v.family}**族超频:共 {v.total_count} 次(上限 {v.threshold} 次)"
        )
        lines.append(f"    高频短语:{top_phrases_text}")

    lines.extend([
        "",
        "**重写要求**:",
        "  ① 上述每一族,**最多保留 2 次**(整篇内统计),其余必须换近义不同形式",
        "  ② 替代库参考 composer.md 铁律 12 — 喉咙紧族 / 手部族 / 视线族 / 情绪族 各有 6-8 个不同表达",
        "  ③ 同一情绪持续 N 段时,**轮换**使用不同表达,绝不允许「喉结滚」每段都出现",
        "  ④ 这是**最优先级修正**,即使略降「情绪密度感」也要换 — 读者要丰富不要复读",
    ])
    return "\n".join(lines)


__all__ = [
    "check_full_narrative_repetition",
    "build_retry_hint_from_violations",
    "RepetitionViolation",
    "WORD_FAMILIES",
    "SPECIFIC_PHRASE_MAX",
]
