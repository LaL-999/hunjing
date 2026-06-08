"""dialogue_dedup — P0S.2 / P0T.2 / P0U.1(2026-05-24)对白 + 叙述复读后处理兜底.

起源:
  - P0S.2:L35 行男对白逐字复读
  - P0T.2:L61/L125 内心独白 70 字逐字复读 / L63/L127 嘴唇句逐字复读
  - P0U.1:雪国 13574 实测发现 12 字短转场套语"晨光在纸门上缓缓移动"复读 4 次,
          P0T.2 的 20 字阈值放过。加**短句 ≥ 3 次复读** 检测兜底。

工作流(retry 3 次都失败时,按序兜底):
  1. dedup_repeated_dialogues_in_segment(segment) — 删完全重复对白(≥ 8 字)
  2. dedup_repeated_narratives_in_segment(segment) — 删完全重复叙述句:
       - 长句(≥ 20 字): 2 次起即删后续(原 P0T.2)
       - 短句(8-19 字): ≥ 3 次起才删,保留首次(P0U.1 新增)

两层兜底,各自只删 **100% 字符匹配** 的复读项,**只删第 2 次及后续**,保留首次出现。

设计原则:
  - **只删 100% 字符匹配**:避免误伤(轻微复读 / 相似但有变化的句子不动)
  - **只删后一次**:保留首次出现保持剧情连贯
  - **双阈值**(P0U.1):
      长句 ≥ 20 字:2 次重复就异常(LLM 复读病)
      短句 8-19 字:2 次允许(诗化呼应 / 时间推进),3 次起异常(转场板复读)
  - **不重写**:LLM 重写已在 retry 阶段失败 3 次,后处理不再尝试

普适性:任何中文文本都适用,与作品风格无关。

created 2026-05-24 / P0S.2 / P0T.2 / P0U.1
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)


# 匹配对白模式:引号(中/英/直角)包围 + 可选前置"X 说/道/问/叹/低声:"等领句
# group(1) = 前置描述 (可空),group(2) = 引号包围内容
_DIALOGUE_PATTERN = re.compile(
    r"((?:[^。!?\n]{0,30}?(?:说|道|问|叹|喊|低声|低头|续道|续说|沉吟|苦笑|怒道|笑道|应道|答道):)?\s*)"
    r"([\"""「]([^\"""」\n]+)[\"""」])",
)


def dedup_repeated_dialogues_in_segment(segment: str) -> tuple[str, int]:
    """删除 segment 中完全重复的对白(只删第二次及后续).

    Args:
      segment: narrator 输出的 markdown narrative

    Returns:
      (cleaned_segment, removed_count)
      - cleaned_segment:去重后的文本
      - removed_count:实际删除的复读对白条数(0 = 无复读)

    安全性:
      - 只删完全字符匹配的对白(set 中重复出现)
      - 保留首次出现
      - 删第二次时,同时移除紧邻的"X 说:"前置描述(避免悬挂)
    """
    if not segment or "\"" not in segment + "「":  # 简单预过滤
        # 上面的 +"「" 是 hack,等价于"segment 里没引号"
        pass

    # 1. 扫所有对白 match,记录 (dialogue_inner_text, full_match_with_lead, span)
    matches: list[tuple[str, re.Match]] = []
    for m in _DIALOGUE_PATTERN.finditer(segment):
        inner = m.group(3).strip()  # 引号内的纯对白
        if len(inner) < 8:
            continue   # 太短(如"嗯""好")不算
        matches.append((inner, m))

    if len(matches) < 2:
        return segment, 0

    # 2. 找重复:第一次出现保留,后续删除
    seen_inner: set[str] = set()
    spans_to_remove: list[tuple[int, int]] = []
    for inner, m in matches:
        if inner in seen_inner:
            # 重复对白 — 标记删除区间(含前置 lead + 引号包围)
            spans_to_remove.append(m.span())
        else:
            seen_inner.add(inner)

    if not spans_to_remove:
        return segment, 0

    # 3. 倒序删除(防止位置 shift)
    cleaned = segment
    spans_to_remove.sort(key=lambda s: -s[0])
    for start, end in spans_to_remove:
        # 删整个匹配区间(lead + 引号)
        # 注:可能留下空段落或前后双空格,简单规整一下
        cleaned = cleaned[:start] + cleaned[end:]

    # 4. 规整:连续空白合并成 1 个换行(避免空段)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)

    logger.info(
        f"dialogue_dedup: 删 {len(spans_to_remove)} 处复读对白,"
        f"原长 {len(segment)} → {len(cleaned)} 字"
    )
    return cleaned, len(spans_to_remove)


def _mask_dialogue_spans(segment: str) -> str:
    """把所有引号包围的对白替换成等长占位符(保留字符位置).

    对白内常含句号(如 "我累了。你回去吧。"),如果直接 split 句子边界,
    会被切成 4 段,把后续叙述句的位置全打乱。先把对白原位替换为 \\x00,
    保持字符索引不变,后续按句号切时不会切进对白里。
    """
    masked_chars = list(segment)
    patterns = [
        r'["""][^"""\n]*["""]',  # 中文 / 英文双引号
        r'「[^」\n]*」',                 # 直角引号
    ]
    for pat in patterns:
        for m in re.finditer(pat, segment):
            for i in range(m.start(), m.end()):
                masked_chars[i] = "\x00"
    return "".join(masked_chars)


def _normalize_for_compare(masked_sentence: str) -> str:
    """把句子规范化成可比较的形式.

    1. 去掉对白占位符(\\x00) — 比较只针对叙述层文本
    2. 去掉所有空白
    3. 去掉首尾的标点 / 顿号 / 省略号 / 破折号
    """
    cleaned = masked_sentence.replace("\x00", "")
    cleaned = re.sub(r"\s+", "", cleaned)
    cleaned = cleaned.strip(" ,;:、…—。!?\n")
    return cleaned


def dedup_repeated_narratives_in_segment(
    segment: str,
    min_chars: int = 20,
    short_min_chars: int = 8,
    short_repeat_threshold: int = 3,
) -> tuple[str, int]:
    """P0T.2 / P0U.1(2026-05-24)— 删除 segment 中复读的叙述句.

    双阈值策略:
      - **长句**(≥ min_chars,默认 20 字):2 次重复即删第 2 次起(P0T.2 原版)
        起源:L61/L125 70 字独白逐字复读 / L63/L127 24 字嘴唇句复读
      - **短句**(short_min_chars 到 min_chars-1 字,默认 8-19 字):
        必须 ≥ short_repeat_threshold(默认 3) 次完全相同才删,保留首次,删后续(P0U.1)
        起源:13574 "晨光在纸门上缓缓移动"(12 字)复读 4 次

    流程:
      1. 把所有对白 mask 成 \\x00 占位符(保字符位置,防对白里句号被误切)
      2. 按句号 / 感叹号 / 问号 / 换行切句
      3. 规范化(去对白占位符 + 空白 + 首尾标点)后比较
      4. 按长度走双分支:
         - ≥ min_chars 长句:出现第 2 次即标记
         - short_min_chars 到 min_chars-1 短句:统计频次,≥ threshold 时除首次外全标记
      5. 倒序删除标记 span(含尾随标点)+ 规整空白

    Args:
      segment: narrator 输出的 markdown narrative
      min_chars: 长句阈值(默认 20),≥ 即按"2 次起删"严判
      short_min_chars: 短句下限(默认 8),< 即完全免疫(短叠句"她笑了"允许)
      short_repeat_threshold: 短句触发阈值(默认 3),3 次起删后续

    Returns:
      (cleaned_segment, removed_count)
      - cleaned_segment:去重后的文本
      - removed_count:实际删除的复读句条数(0 = 无复读)

    安全性:
      - 只删 **100% 字符匹配**(规范化后)的句子,不动相似句
      - 保留首次出现,只删第 2 次(长句)/ 第 2 次起(短句 ≥ 3 次的情况)
      - 对白完全不动(由 dedup_repeated_dialogues_in_segment 负责)
    """
    if not segment or len(segment) < short_min_chars * 2:
        return segment, 0

    # 1. 对白原位 mask(保字符位置)
    masked = _mask_dialogue_spans(segment)

    # 2. 按句号 / 感叹号 / 问号 / 换行切句 — 标点跟句子在一起
    #    例:"她抬头。她笑了。" → [(0,4), (4,8)]
    sentence_spans: list[tuple[int, int]] = []
    start = 0
    for m in re.finditer(r"[。!?\n]+", masked):
        end = m.end()
        sentence_spans.append((start, end))
        start = end
    if start < len(segment):
        sentence_spans.append((start, len(segment)))

    if len(sentence_spans) < 2:
        return segment, 0

    # 3. 把所有候选句子按规范化形式归类(同时记录原始 span)
    #    norm_to_spans: { 规范化句子 -> [(span_start, span_end), ...] 按出现顺序 }
    norm_to_spans: dict[str, list[tuple[int, int]]] = {}
    for st, ed in sentence_spans:
        norm = _normalize_for_compare(masked[st:ed])
        if len(norm) < short_min_chars:
            continue   # 太短(<8 字)完全免疫,如"她笑了""屋里静"
        norm_to_spans.setdefault(norm, []).append((st, ed))

    # 4. 按长度走双分支决定要删的 span
    spans_to_remove: list[tuple[int, int]] = []
    for norm, occurrences in norm_to_spans.items():
        if len(occurrences) < 2:
            continue   # 只出现 1 次,跳过
        if len(norm) >= min_chars:
            # 长句:出现 ≥ 2 次,删除第 2 次起
            spans_to_remove.extend(occurrences[1:])
        else:
            # 短句(8-19 字):必须 ≥ short_repeat_threshold 次才删
            if len(occurrences) >= short_repeat_threshold:
                spans_to_remove.extend(occurrences[1:])

    if not spans_to_remove:
        return segment, 0

    # 5. 倒序删除(防 shift)
    cleaned = segment
    spans_to_remove.sort(key=lambda s: -s[0])
    for st, ed in spans_to_remove:
        cleaned = cleaned[:st] + cleaned[ed:]

    # 6. 规整空白
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)

    logger.info(
        f"narrative_dedup: 删 {len(spans_to_remove)} 处复读叙述句,"
        f"原长 {len(segment)} → {len(cleaned)} 字"
    )
    return cleaned, len(spans_to_remove)


def dedup_cross_segment_short_repeats(
    segment: str,
    history_segments: list[str],
    min_chars: int = 10,
    history_threshold: int = 2,
) -> tuple[str, int]:
    """P0V.2(2026-05-24)— 跨幕短句完全相同兜底删除.

    起源:雪国 14345 实测发现 "远处传来雪块滚落的闷响" 11 字短句在
    L27 / L43 / L59 三幕中完全字符相同出现。
    - P0U.2 consistency_checker 应该 catch 这种**跨幕累计 ≥ 3 次**,触发 retry
    - 但 LLM retry 可能改不掉(它就想用这个转场套语)
    - P0U.1 的 dedup_repeated_narratives_in_segment **只看本 segment 内部**,
      不能删跨幕复读 — 本函数补这个洞

    工作流(narrator retry 用尽后兜底):
      1. 拉历史 N-1 个 segment(本 sim 已落库的)
      2. 统计每个完整长度 ≥ min_chars 的短句在历史中出现的字符完全相同的次数
      3. 扫本 segment 短句,若在历史出现次数 ≥ history_threshold,
         **删本 segment 这一处**(保留历史首次出现的"原文"地位)

    Args:
      segment: 本 segment(刚生成 / retry 完的 narrative)
      history_segments: 同 sim 之前已落库的 narrative_segment 列表
      min_chars: 短句长度下限(默认 12)— 太短的免疫
      history_threshold: 历史出现次数阈值(默认 2)— 在历史出现 ≥ 2 次的句子,
                         本幕再写就该删

    Returns:
      (cleaned_segment, removed_count)
    """
    if not segment or not history_segments:
        return segment, 0
    if len(segment) < min_chars:
        return segment, 0

    # 1. 收集历史所有短句频次(按规范化形式计 key)
    from collections import Counter
    history_short_counter: Counter[str] = Counter()
    for hist_seg in history_segments:
        if not hist_seg:
            continue
        masked_h = _mask_dialogue_spans(hist_seg)
        start = 0
        for m in re.finditer(r"[。!?\n]+", masked_h):
            end = m.end()
            norm = _normalize_for_compare(masked_h[start:end])
            if len(norm) >= min_chars:
                history_short_counter[norm] += 1
            start = end
        if start < len(hist_seg):
            norm = _normalize_for_compare(masked_h[start:])
            if len(norm) >= min_chars:
                history_short_counter[norm] += 1

    # 找出历史里频次 ≥ threshold 的短句集合
    saturated_norms: set[str] = {
        n for n, c in history_short_counter.items() if c >= history_threshold
    }
    if not saturated_norms:
        return segment, 0

    # 2. 扫本 segment 短句,若 norm 在 saturated_norms 中,删该 span
    masked = _mask_dialogue_spans(segment)
    spans_to_remove: list[tuple[int, int]] = []
    start = 0
    for m in re.finditer(r"[。!?\n]+", masked):
        end = m.end()
        norm = _normalize_for_compare(masked[start:end])
        if norm in saturated_norms:
            spans_to_remove.append((start, end))
        start = end
    if start < len(segment):
        norm = _normalize_for_compare(masked[start:])
        if norm in saturated_norms:
            spans_to_remove.append((start, len(segment)))

    if not spans_to_remove:
        return segment, 0

    # 3. 倒序删除
    cleaned = segment
    spans_to_remove.sort(key=lambda s: -s[0])
    for st, ed in spans_to_remove:
        cleaned = cleaned[:st] + cleaned[ed:]

    # 4. 规整空白
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)

    logger.info(
        f"cross_segment_dedup: 删 {len(spans_to_remove)} 处跨幕短句复读,"
        f"原长 {len(segment)} → {len(cleaned)} 字"
    )
    return cleaned, len(spans_to_remove)


__all__ = [
    "dedup_repeated_dialogues_in_segment",
    "dedup_repeated_narratives_in_segment",
    "dedup_cross_segment_short_repeats",
]
