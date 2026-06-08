"""章节切分(2026-06-01)— 给 narrative 文本按区间切章.

核心:**运行时算,不入库**.字数变了永远算得准.

算法:
  1. 累加字符到 chapter_size_min → 开始扫段落分隔符 \\n\\n
  2. 找到 → 在那里切一章
  3. 累加到 chapter_size_max 还没找到段落分隔符 → 退而求其次找句末标点(。!?")
  4. 仍找不到 → 在 chapter_size_max 强切(极端情况,如全是单一长段)

输出每章:
  global_chapter_no:  全局章号(start_chapter_no + 在本 sim 内的索引)
  char_offset_start:  本 sim narrative 内偏移
  char_offset_end:    本 sim narrative 内偏移(开区间)
  char_count:         本章字数
  first_words:        前 20 字预览(跳章列表用)
"""
from __future__ import annotations

from typing import Optional, TypedDict


class ChapterInfo(TypedDict):
    global_chapter_no: int
    char_offset_start: int
    char_offset_end: int
    char_count: int
    first_words: str


# 段落分隔符候选 — 按优先级
_PARAGRAPH_BREAK = "\n\n"
# 句末标点 — 在 max 还没找到段落时,退而求其次
_SENTENCE_ENDS = ("。", "!", "?", ".", "!", "?", "...", "…")


def _scan_paragraph_break(text: str, search_start: int, search_end: int) -> Optional[int]:
    """在 text[search_start:search_end] 找最近一个 \\n\\n,返回切点位置(\\n\\n 之后)."""
    idx = text.find(_PARAGRAPH_BREAK, search_start, search_end)
    if idx == -1:
        return None
    return idx + len(_PARAGRAPH_BREAK)


def _scan_sentence_end(text: str, search_start: int, search_end: int) -> Optional[int]:
    """在 text[search_start:search_end] 倒着找最近一个句末标点 — 让本章在句子结尾收束."""
    # 从尾巴往前扫(更倾向于切在 max 附近,避免章节过短)
    best_idx = -1
    for end_token in _SENTENCE_ENDS:
        idx = text.rfind(end_token, search_start, search_end)
        if idx > best_idx:
            best_idx = idx + len(end_token)
    return best_idx if best_idx > 0 else None


def _extract_first_words(text: str, start: int, end: int, max_chars: int = 20) -> str:
    """取章节开头 N 字作为预览,跳过前导空白和段落分隔符."""
    chunk = text[start:end].lstrip(" \n\t\r")
    if len(chunk) <= max_chars:
        return chunk
    # 在 max_chars 字内找标点,优先在标点处截断
    cut = chunk[:max_chars]
    for end_token in ("。", ",", ",", "!", "?", " ", "、"):
        idx = cut.rfind(end_token)
        if idx > max_chars // 2:  # 别截太短
            return chunk[:idx + 1].strip()
    return cut.strip() + "…"


def chapterize(
    narrative: str,
    chapter_size_min: int,
    chapter_size_max: int,
    start_chapter_no: int = 1,
) -> list[ChapterInfo]:
    """切章主函数.

    Args:
      narrative: 本 sim 完整文本
      chapter_size_min: 章节最小字数(到此值才开始找段落切点)
      chapter_size_max: 章节最大字数(超过此值强切)
      start_chapter_no: 本 sim 第一章的全局章号(由继承链推导)

    Returns:
      章节列表(global_chapter_no 从 start_chapter_no 起递增)
      若文本极短(< chapter_size_min)→ 整篇作为一章
    """
    if not narrative or not narrative.strip():
        return []
    if chapter_size_min < 100:
        chapter_size_min = 100
    if chapter_size_max < chapter_size_min + 200:
        chapter_size_max = chapter_size_min + 200

    n = len(narrative)
    chapters: list[ChapterInfo] = []
    cursor = 0
    current_chapter_no = start_chapter_no

    while cursor < n:
        remaining = n - cursor
        if remaining <= chapter_size_max:
            # 剩余文本不够一章 + 容差 → 整段作为最后一章
            chapters.append({
                "global_chapter_no": current_chapter_no,
                "char_offset_start": cursor,
                "char_offset_end": n,
                "char_count": remaining,
                "first_words": _extract_first_words(narrative, cursor, n),
            })
            break

        # 切点候选窗口:[cursor + min, cursor + max]
        window_start = cursor + chapter_size_min
        window_end = cursor + chapter_size_max

        # 1. 优先找段落分隔符
        cut_at = _scan_paragraph_break(narrative, window_start, window_end)

        # 2. 没段落分隔符 → 找句末标点
        if cut_at is None:
            cut_at = _scan_sentence_end(narrative, window_start, window_end)

        # 3. 句末也没有 → 强切在 window_end
        if cut_at is None:
            cut_at = window_end

        # 防御:cut_at 必须严格大于 cursor 避免死循环
        if cut_at <= cursor:
            cut_at = window_end

        chapters.append({
            "global_chapter_no": current_chapter_no,
            "char_offset_start": cursor,
            "char_offset_end": cut_at,
            "char_count": cut_at - cursor,
            "first_words": _extract_first_words(narrative, cursor, cut_at),
        })
        cursor = cut_at
        current_chapter_no += 1

    return chapters


__all__ = ["chapterize", "ChapterInfo"]
