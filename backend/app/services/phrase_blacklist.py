"""Sprint 6.A2 M4.2(2026-05-19)— 套语去重(治瑕疵 4 复读机)。

Gemini 评测灵魂续写产物,报"刘飞专属套装"瑕疵:
  - 每次出现刘飞都"低着头""攥着衣角""指节发白""眼眶泛红""声音发颤"
  - narrator LLM 路径依赖 + 缺"上幕用过什么"信号 → 复读模板套语
  - 读者一眼识破 AI 模板化产物

本服务统计 sim 内所有已落库 narrative_segment 的 3-4 字 n-gram 高频短语,
建立"本幕禁用清单",注入 narrator prompt 让 LLM 自律避免复读。

设计原则:
  - **零新依赖**:复用 RAG rag_retrieval._tokenize 思路,但 ngram=3/4(套语级)
  - **约束式而非强制式**:用 prompt 让 LLM 自律,**不重生不截断**(对齐用户铁律)
  - **本 sim 范围**:跨 sim 不共享(不同作品的套语合法)
  - **freq ≥ MIN_FREQ_FOR_BLACKLIST 才计入**:低频短语属正常重复

API:
  - build_phrase_blacklist(conn, simulation_id) → list[tuple[str, int]]
    返回 [(短语, 频次), ...] 按 freq 倒序,top 20
"""
from __future__ import annotations

import re
import sqlite3
from collections import Counter

from app.db import fetch_all

# n-gram 大小(套语典型 3-4 字;P0Q.4 加 2 字治"湿衣裳/空屋/火盆/灰烬"反复)
NGRAM_SIZES = (2, 3, 4)
# 不同 n-gram 阈值
# P0Q.4(2026-05-24):2 字名词常作意象/道具(空屋/火盆)
# P0R.3(2026-05-24):雪国实测"热水/干衣"出现 5 次但阈值 8 没触发 → 降到 5
#   误伤率上升风险,但 _STOP_FILLER_CHARS 已扩充 + retry 会触发不可逆改写
MIN_FREQ_BY_NGRAM: dict[int, int] = {
    2: 5,   # 2-gram(意象/道具)— P0R.3 从 8 降到 5,提高敏感度
    3: 5,   # 3-gram(套语)
    4: 5,   # 4-gram(成语级套语)
}
# 兼容旧调用:默认阈值(若 ngram size 不在表中)
MIN_FREQ_FOR_BLACKLIST = 5
# 禁用清单最多返多少条(防 prompt 过载)— P0Q.4 加 2-gram 后稍微提高
MAX_BLACKLIST_ENTRIES = 25

# 极常见短语永远不入清单(就是常用搭配,不属于套语)
_STOP_PHRASES = frozenset([
    "他说道", "她说道", "他说,", "她说,",
    "他点了点头", "她点了点头",
    "突然之间", "与此同时",
    "此时此刻", "那一瞬间",
])

# 极常见单字组合(避免把"的他""了他"等噪声当套语)
# P0Q.4(2026-05-24):2-gram 加入后必须扩充,排除"的人 / 是他 / 不会 / 这是"等高频噪声
_STOP_FILLER_CHARS = frozenset(
    "的了是和也都就还在又把被让"
    "不没有就会要能可以这那一个上下里外前后中"
    "我你他她它们着过来去到从向对于"
)


def _normalize_segment(text: str) -> str:
    """规范化:去标点、统一空白。

    保留:中文字符 + 英数
    去除:所有非字母数字的标点 + 空白
    """
    if not text:
        return ""
    return re.sub(r"[^一-鿿\w]+", "", text)


def _extract_ngrams(text: str, ngram_size: int) -> list[str]:
    """滑窗取 ngram_size 字短语;过滤纯 filler 字组成的噪声。"""
    if not text or len(text) < ngram_size:
        return []
    out: list[str] = []
    for i in range(len(text) - ngram_size + 1):
        g = text[i:i + ngram_size]
        # 跳过整个 gram 全是 filler 字的(无意义)
        if all(c in _STOP_FILLER_CHARS for c in g):
            continue
        # 跳过停用短语
        if g in _STOP_PHRASES:
            continue
        out.append(g)
    return out


def build_phrase_blacklist(
    conn: sqlite3.Connection,
    simulation_id: str,
) -> list[tuple[str, int]]:
    """统计某 sim 已落库 narrative_segment 中的高频套语,返回禁用清单。

    Args:
      simulation_id: 当前 sim id;只统计本 sim 的 narrative_segment

    Returns:
      [(phrase, frequency), ...] 按 frequency 倒序;最多 MAX_BLACKLIST_ENTRIES 条
      若总 segment 字数 < 100(刚开始,数据不足) → 返空清单
    """
    rows = fetch_all(
        conn,
        """SELECT narrative_segment FROM simulation_scenes
           WHERE simulation_id=? AND narrative_segment != ''""",
        (simulation_id,),
    )
    if not rows:
        return []

    # 合并所有 segments,规范化
    all_text = " ".join(
        _normalize_segment(r["narrative_segment"]) for r in rows
    )
    # 字数太少 → 没足够数据判定套语
    if len(all_text) < 100:
        return []

    # 2/3/4-gram 各自统计,但用不同阈值过滤(P0Q.4)
    # 用 counter_by_n 分别存,因为不同 n-gram 用不同阈值,合并 Counter 后没法分辨
    counter_by_n: dict[int, Counter[str]] = {}
    for n in NGRAM_SIZES:
        c: Counter[str] = Counter()
        c.update(_extract_ngrams(all_text, n))
        counter_by_n[n] = c

    # 按 n-gram 阈值过滤
    candidates: list[tuple[str, int]] = []
    for n, c in counter_by_n.items():
        min_freq = MIN_FREQ_BY_NGRAM.get(n, MIN_FREQ_FOR_BLACKLIST)
        for phrase, freq in c.most_common():
            if freq >= min_freq:
                candidates.append((phrase, freq))

    # 去重子串:仅在"更长短语频次接近子串(diff < 30%)" 时,保留更长的
    # 因为:
    #   - 若"低着头"(freq=8)+ "飞低着头"(freq=2,巧合包含)→ 保留"低着头"
    #     (子串频次 8 远高于包含它的更长短语频次 2,说明"低着头"才是套语)
    #   - 若"低着头"(freq=8)+ "他低着头"(freq=7)→ 保留"他低着头"
    #     (频次接近,后者更具体,代表性更强)
    # 实现:按长度倒序排;每个新短语和已纳入清单的更长短语对比 — 若被某个频次接近的
    # 更长短语包含,则跳过(让更长的代表)
    candidates.sort(key=lambda x: (-len(x[0]), -x[1]))
    deduped: list[tuple[str, int]] = []
    for phrase, freq in candidates:
        skip = False
        for longer, longer_freq in deduped:
            if len(longer) <= len(phrase):
                continue
            if phrase not in longer:
                continue
            # 仅当更长短语频次 ≥ 子串频次 × 0.7 时,认为它是真"更具体套语"
            if longer_freq >= freq * 0.7:
                skip = True
                break
        if not skip:
            deduped.append((phrase, freq))

    # 按频次倒排,取 top
    deduped.sort(key=lambda x: -x[1])
    return deduped[:MAX_BLACKLIST_ENTRIES]
