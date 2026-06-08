"""红旗词过滤服务 — Sprint 2.A。

设计原则(用户拍板):
  **宽松文学优先 — 误杀文学用户的成本远高于漏放某个边缘词。**
  只拦真正极端三类(政治敏感 / 恐怖暴力 / 猎奇极端色情)+ 隐私模式(身份证/银行卡号正则)。
  一般文学正常元素绝不拦:
    - 武侠流血 / 战争场景 / 死亡描写(violence 类不拦"杀" "血" "刀剑")
    - 言情亲密 / 情爱描写(sexual 类不拦"亲吻" "做爱" "情欲")
    - 历史事件提及 / 一般政治词(political 类不拦"政府" "国家" "战争")
    - 普通脏话(完全不拦)

匹配机制:
  - 默认 substring(性能 + 安全)
  - is_regex=1 时按 re.search 匹配(用于 PII 这种格式规则)
  - 全文喂进来,逐条扫;命中第一条 block 即返回(不继续扫,省时间)

API:
  filter_text(conn, text) -> FilterVerdict
    成功通过 → FilterVerdict.passed()
    命中 block → FilterVerdict.blocked(matched_flag, snippet)
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Optional

from app.db import fetch_all
from app.models.red_flag import RedFlag

# 命中片段截取上下文,< 80 字脱敏存
SNIPPET_BEFORE = 30
SNIPPET_AFTER = 30


@dataclass
class FilterVerdict:
    """扫描结果。passed=True 表示通过;命中 block 时 matched_flag + snippet 填。"""

    passed: bool
    matched_flag: Optional[RedFlag] = None
    snippet: Optional[str] = None    # 命中片段 + 上下文(80 字内)

    @classmethod
    def ok(cls) -> "FilterVerdict":
        return cls(passed=True)

    @classmethod
    def block(cls, flag: RedFlag, snippet: str) -> "FilterVerdict":
        return cls(passed=False, matched_flag=flag, snippet=snippet)


def filter_text(conn: sqlite3.Connection, text: str) -> FilterVerdict:
    """扫文本,命中第一条 block 词典就返。

    warn 级别记录但不阻断(v1 暂不实现 warn 路径,留扩展)。
    """
    rows = fetch_all(
        conn,
        "SELECT * FROM red_flag_dictionary WHERE enabled=1 ORDER BY severity",
        (),
    )
    flags = [RedFlag.from_row(r) for r in rows]

    for flag in flags:
        if flag.severity != "block":
            continue   # warn 级别在 v1 不实施
        snippet = _try_match(text, flag)
        if snippet is not None:
            return FilterVerdict.block(flag, snippet)

    return FilterVerdict.ok()


def _try_match(text: str, flag: RedFlag) -> Optional[str]:
    """尝试匹配单条 flag。命中返回脱敏 snippet,未命中返回 None。"""
    if flag.is_regex:
        try:
            m = re.search(flag.pattern, text)
        except re.error:
            return None     # 词典写错正则不阻塞业务
        if m is None:
            return None
        return _make_snippet(text, m.start(), m.end())
    # substring 匹配
    idx = text.find(flag.pattern)
    if idx < 0:
        return None
    return _make_snippet(text, idx, idx + len(flag.pattern))


def _make_snippet(text: str, start: int, end: int) -> str:
    """从命中位置截取 < 80 字上下文(法务证据 + 给运营调词典看)。"""
    s = max(0, start - SNIPPET_BEFORE)
    e = min(len(text), end + SNIPPET_AFTER)
    snippet = text[s:e].replace("\n", " ").strip()
    # 控制总长不超 80
    if len(snippet) > 80:
        snippet = snippet[:77] + "..."
    return snippet


__all__ = ["FilterVerdict", "filter_text"]
