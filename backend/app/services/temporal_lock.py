"""Sprint 6.A2 M5.5(2026-05-20)— Temporal Lock(时间链锁,治时间倒流)。

把灵魂续写每幕的 time_anchor(自由文本如"次日下午""黄昏"等)解析成 numeric
相对时间戳(单位:小时),让 scene_picker 能强制校验 new ≥ last。

Gemini 第二轮评测瑕疵 3 体现:
  "刚在教室唱完歌,没过两段,刘美佳的台词突然变成'哎,昨天那歌唱得我嗓子都有点哑了'"
  → 时间无过渡强行注入"第二天"

设计:
  - **离线 LLM 解析**:每幕 narrator 后调一次,把 time_anchor 转 hour offset
    (相对 sim 起点,float)
  - **校验**:scene_picker 调用前,从 simulation_scenes 拉历史 time_anchor 解析过的最大值
    + 把"new_time_offset 必须 ≥ last_time_offset"作硬约束 prepend 进 prompt
  - **consistency_checker** 增加 TEMPORAL_REGRESSION 违规判定

零依赖:不用日历库 / dateutil。LLM 直接给 float 偏移(单位小时)。

API:
  - parse_time_anchor(text) → tuple[float, dict] LLM 调:文本 → 小时偏移
  - get_last_time_offset_for_sim(conn, simulation_id) → float
  - format_temporal_constraint_line(last_offset, last_anchor_text) → str
    生成给 narrator/scene_picker prompt prepend 的硬铁律单行
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from app.db import fetch_one
from app.services.llm_client import call_llm_json

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"


# 简单 fast-path 规则:常见时间锚直接映射,避免每幕都调 LLM
# (LLM 是兜底,这层减少 80% LLM 调用)
_FAST_RULES = [
    # (正则, 解析函数)
    (re.compile(r"^([一二三四五六七八九十]|\d+)\s*分钟[后之]?$"), lambda m: 0.05),
    (re.compile(r"^半小时[后之]?$"), lambda m: 0.5),
    (re.compile(r"^一?小时[后之]?$"), lambda m: 1.0),
    (re.compile(r"^数小时[后之]?$"), lambda m: 4.0),
    (re.compile(r"^当晚|当夜|入夜$"), lambda m: 6.0),
    (re.compile(r"^次日清晨|次日早晨|翌日清晨$"), lambda m: 16.0),
    (re.compile(r"^次日(?:上午|早上)$"), lambda m: 20.0),
    (re.compile(r"^次日(?:中午|正午)$"), lambda m: 24.0),
    (re.compile(r"^次日(?:下午|午后)$"), lambda m: 28.0),
    (re.compile(r"^次日(?:傍晚|黄昏|入夜)$"), lambda m: 30.0),
    (re.compile(r"^次日(?:深夜|夜里|夜)$"), lambda m: 32.0),
    (re.compile(r"^翌日$"), lambda m: 24.0),
    (re.compile(r"^第三日|两日后$"), lambda m: 48.0),
    (re.compile(r"^一周后$"), lambda m: 168.0),
    (re.compile(r"^数日(?:后|过去)?$"), lambda m: 72.0),
]


def parse_time_anchor_fastpath(text: str, prev_offset: float = 0.0) -> tuple[float, bool]:
    """规则 fast-path 解析(0 LLM 调用)。

    Returns:
      (offset_hours, matched)
      matched=False 时 caller 应转 LLM 解析
    """
    if not text:
        return prev_offset, True  # 空 = 视为同时刻
    text = text.strip()
    for pattern, fn in _FAST_RULES:
        m = pattern.match(text)
        if m:
            delta = fn(m)
            return prev_offset + delta, True
    return prev_offset, False


def parse_time_anchor_llm(
    text: str, prev_offset_hours: float,
) -> tuple[float, dict]:
    """LLM 解析自由文本时间锚 → 相对 sim 起点的小时偏移。

    Returns: (new_offset_hours, llm_usage)
    """
    if not text:
        return prev_offset_hours, {"input_tokens": 0, "output_tokens": 0}

    user_input = {
        "previous_offset_hours": prev_offset_hours,
        "time_anchor_text": text,
    }
    system_prompt = (
        "你是时间锚解析器。把中文自由文本时间锚(如'次日下午''黄昏''数日后')"
        "解析成**相对推演起点**的小时偏移(float,单位小时)。\n\n"
        "规则:\n"
        "- previous_offset_hours 是上一幕的偏移,你给出的新偏移必须 **≥ 此值**\n"
        "- '次日下午' 通常对应 28h(假设起点为 0 点);'当晚' 对应 6h\n"
        "- 不确定时,在上一幕的基础上加合理增量(如 1-2 小时表示同日内推进)\n\n"
        "输出严格 JSON:{\"new_offset_hours\": float}"
    )
    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input,
            max_tokens=80, temperature=0.1,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"temporal_lock LLM failed for {text!r}: {e}")
        return prev_offset_hours, {"input_tokens": 0, "output_tokens": 0}

    if not isinstance(parsed, dict):
        return prev_offset_hours, usage
    new_offset = parsed.get("new_offset_hours")
    if not isinstance(new_offset, (int, float)):
        return prev_offset_hours, usage
    # 强制不可倒流
    return max(float(new_offset), prev_offset_hours), usage


def get_last_time_offset_for_sim(
    conn: sqlite3.Connection,
    simulation_id: str,
) -> tuple[float, str]:
    """拉某 sim 最新一幕的 time_anchor 文本 + 已落库的最大 offset。

    本服务不落 offset 到表(走 in-memory + 主循环每次计算),所以这里只返
    最近一幕的 time_anchor 原文,offset 由 caller 用 parse_* 计算。

    Returns:
      (last_offset_hours, last_anchor_text)
      若 sim 还没有幕 → (0.0, "")
    """
    row = fetch_one(
        conn,
        """SELECT time_anchor FROM simulation_scenes
           WHERE simulation_id=? AND time_anchor != ''
           ORDER BY scene_index DESC LIMIT 1""",
        (simulation_id,),
    )
    if row is None:
        return 0.0, ""
    last_text = row["time_anchor"] or ""
    # 用 fast-path 估算;失败时返 0(caller 决定是否调 LLM)
    last_offset, matched = parse_time_anchor_fastpath(last_text, prev_offset=0.0)
    return last_offset if matched else 0.0, last_text


def format_temporal_constraint_line(
    last_offset_hours: float, last_anchor_text: str,
) -> str:
    """生成给 narrator/scene_picker 的硬铁律单行。"""
    if not last_anchor_text:
        return ""
    return (
        f"上幕时间:【{last_anchor_text}】(累计偏移 ~{last_offset_hours:.1f} 小时)— "
        f"本幕 time_anchor 必须 **≥** 此时刻,严禁出现'昨天''几天前''上一周'等倒流表达"
    )
