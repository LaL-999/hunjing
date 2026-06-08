"""Sprint 6.A2 M4.3(2026-05-20)— Reflexion 一致性自检(治瑕疵 3 行为漂移 + 兜底)。

每幕 narrator 合稿完成后调用,LLM 检测 narrative_segment 是否违反:
  - world_facts(ACTIVE + LOCKED)— 时间线 / 反派规则不一致
  - plot_threads(ACTIVE)— 漠视主线 / 引入太多新分支
  - character behavior_baseline(或 personality + no_go_list fallback)— 行为漂移

输出 violations 列表,每条含:
  - severity: 'critical' / 'warning' / 'info'
  - category: 'world_fact' / 'plot_thread' / 'character_baseline'
  - evidence: narrative 中的违规片段
  - suggestion: LLM 给出的改写建议

主循环消费:
  - severity='critical' 数量 ≥ 1 → 触发 narrator 重生本幕(max 1 retry)
  - 'warning' / 'info' 仅 log,不重生(避免无限循环 + 成本可控)

设计原则(对齐"做最好的产品"铁律 + "不强行截断"既有铁律):
  - 用 LLM 做语义级判定(规则匹配无法识别"行为漂移"这种语义违规)
  - 单次 critical 重生上限(防 LLM 反复重写卡死)
  - LLM 调失败 → 默认无违规(降级容错,主流程继续)
  - **不修改 narrative_segment**:只输出违规清单,改写交给 narrator retry

API:
  - check_segment(conn, sim, scene_index, narrative_segment, agents) →
      tuple[CheckResult, dict]
  - CheckResult.has_critical_violation 给 caller 决定是否 retry
  - CheckResult.to_retry_hint() 生成给 narrator 的 retry hint 文本
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from app.db import fetch_all, fetch_one
from app.models.character import Character
from app.models.simulation import Simulation
from app.services.llm_client import call_llm_json

logger = logging.getLogger(__name__)

ViolationSeverity = Literal["critical", "warning", "info"]
# M4.3 3 类 + M5 新增 4 类:
#   identity_invented        瑕疵 1 — 造出与已注册实体语义重合的新身份
#   action_repeated          瑕疵 2 — 重复了 action_ledger 中标 is_repeatable=0 的动作
#   temporal_regression      瑕疵 3.B — 时间倒流("昨天""上一周"在本幕出现)
#   emotional_discontinuity  瑕疵 4 — 角色情绪跨幕突变 ≥5 档且无铺垫
ViolationCategory = Literal[
    "world_fact", "plot_thread", "character_baseline",
    "identity_invented", "action_repeated",
    "temporal_regression", "emotional_discontinuity",
    # Sprint 6.A2 FOCUS.2(2026-05-21):叙述视角漂移(纯程序检测,不走 LLM)
    "narrative_pov_drift",
    # P0Q.1(2026-05-24):对白复读(纯程序检测,字符 n-gram 包含率)
    "dialogue_repetition",
    # P0T.1(2026-05-24):叙述句复读(纯程序检测,排除对白后的长句互比)
    "narrative_repetition",
    # P0V.3(2026-05-24):意象主题词刷屏(累计频次 ≥ 12 次)
    "phrase_density",
    # P0V.4(2026-05-24):对白主题词反复(同 actor 同子串 ≥ K 次)
    "dialogue_phrase_repetition",
    # P0W.1(2026-05-24):受限角色物理 / 语言能力违规(治 15070 师傅"半身不遂"仍拄杖步行)
    "physical_constraint_violation",
    # M10.A(2026-05-27):文风滤镜 — 实测 vs author_compass 目标偏差(句长/对白率/感官/段长)
    "style_drift",
    # M10.B(2026-05-27):意象库 — 雷区命中 / 推荐意象缺位
    "imagery_violation",
    # P5.1(2026-05-27):outline 执行率 — key_events 是否被实际完成(治"剧情空心化")
    "outline_execution",
]

_PROMPTS_DIR = Path(__file__).parents[3] / "prompts"


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8")


@dataclass
class Violation:
    severity: ViolationSeverity
    category: ViolationCategory
    evidence: str       # narrative 中的违规片段
    suggestion: str     # 改写建议(给 narrator retry 用)
    subject_name: str = ""    # 涉及的角色 / 事实主体名(可空)


@dataclass
class CheckResult:
    violations: list[Violation] = field(default_factory=list)

    @property
    def has_critical_violation(self) -> bool:
        return any(v.severity == "critical" for v in self.violations)

    @property
    def critical_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "critical")

    def to_retry_hint(self) -> str:
        """生成给 narrator retry 的 prompt 提示文本(只含 critical 违规)。"""
        crits = [v for v in self.violations if v.severity == "critical"]
        if not crits:
            return ""
        lines = ["上一次合稿被一致性自检检测出严重违规,需要改写。具体违规清单:"]
        for i, v in enumerate(crits, 1):
            subj = f"({v.subject_name})" if v.subject_name else ""
            lines.append(
                f"{i}. [{v.category}{subj}] 证据:\"{v.evidence}\";"
                f"建议:{v.suggestion}"
            )
        lines.append("\n请重写本幕 narrative_segment,严格规避以上违规。")
        return "\n".join(lines)


def _strip_dialogue_quotes(text: str) -> str:
    """剥离中文引号内的对话,只留叙述层文本。

    Sprint 6.A2 FOCUS.2(2026-05-21):人称漂移检测前置步骤。
    "<对话>" / 「<对话>」 内的"我"是角色自报,不算叙述层漂移。
    """
    import re
    # 中文双引号 "..." / 直角引号 「...」 / 英文双引号 "..."
    cleaned = re.sub(r'["""].*?["""]', '', text)
    cleaned = re.sub(r'「.*?」', '', cleaned)
    cleaned = re.sub(r'"[^"]*"', '', cleaned)
    return cleaned


def _check_narrative_pov_drift(
    narrative_segment: str,
    narrative_pov: Optional[str],
    agents: list[Character],
) -> list[Violation]:
    """Sprint 6.A2 FOCUS.2(2026-05-21)— 程序级人称漂移检测。

    纯字符串扫描,不调 LLM(便宜 + 确定性高)。

    检测逻辑:
      - 去除对话(引号内)后扫叙述层代词频次
      - pov='first' 时:叙述层"我"应频繁出现;若"我"次数 < 1/500 字 → 漂移到三人称
      - pov='third' 时:叙述层不该出现"我"作主语;若出现 ≥ 3 次 → 漂移到一人称
      - pov='second' 时:类似检查"你"作主语
      - pov='mixed' / null → 跳过检测(无基线)

    返回违规列表(severity='critical' 会触发 narrator retry)。
    """
    if narrative_pov not in {"first", "second", "third"}:
        return []   # mixed / null 不检测
    if not narrative_segment or len(narrative_segment.strip()) < 100:
        return []   # 段落太短,统计无意义

    import re
    narrative_layer = _strip_dialogue_quotes(narrative_segment)
    seg_len = max(len(narrative_layer), 1)

    # 统计"我"/"你"出现次数(简单字符 count,不区分语法角色)
    # 接受少量误报(如对话里"我"被对话过滤掉了还残留),目标是抓**大量漂移**
    count_wo = narrative_layer.count("我")   # "我"
    count_ni = narrative_layer.count("你")   # "你"

    violations: list[Violation] = []

    if narrative_pov == "first":
        # 第一人称:叙述层"我"该频繁出现;太少 = 漂移到三人称
        # 阈值:每 500 字至少 1 次"我"(保守);否则 critical
        expected_min = max(1, seg_len // 500)
        if count_wo < expected_min:
            violations.append(Violation(
                severity="critical",
                category="narrative_pov_drift",
                evidence=(
                    f"原作为第一人称叙述,但本幕叙述层只出现 {count_wo} 次「我」"
                    f"(预期 ≥ {expected_min} 次,基于段落长度 {seg_len} 字)"
                ),
                suggestion="把主角的所有叙述层动作 / 心理 / 感受改回「我」开头,避免漂移到第三人称",
            ))
    elif narrative_pov == "third":
        # 第三人称:叙述层"我"不该出现;出现 ≥ 3 次 = 漂移
        # 但允许 1-2 次(短引语 / 误剥离)
        if count_wo >= 3:
            violations.append(Violation(
                severity="critical",
                category="narrative_pov_drift",
                evidence=(
                    f"原作为第三人称叙述,但本幕叙述层出现 {count_wo} 次「我」"
                    f"(预期 < 3 次)"
                ),
                suggestion="把所有叙述层的「我」改为对应角色名 / 他 / 她,避免漂移到第一人称",
            ))
    elif narrative_pov == "second":
        # 第二人称:叙述层"你"该频繁出现;太少 = 漂移
        expected_min = max(1, seg_len // 500)
        if count_ni < expected_min:
            violations.append(Violation(
                severity="critical",
                category="narrative_pov_drift",
                evidence=(
                    f"原作为第二人称叙述,但本幕叙述层只出现 {count_ni} 次「你」"
                ),
                suggestion="把所有叙述层指代主体改回「你」,避免漂移",
            ))

    return violations


def _extract_dialogues_from_narrative(text: str) -> list[str]:
    """从 narrative 文本里提取所有对白(引号包裹的内容)。

    支持中文双引号 "..." / 直角引号 「...」 / 英文双引号 "..."
    返回:对白字符串 list,每个 ≥ 8 字符才入选(过短的"嗯""好"无意义)
    """
    import re
    dialogues: list[str] = []
    # 中文 / 英文 / 直角引号
    patterns = [
        r'["""]([^""""\n]+)["""]',  # 中文 / 英文双引号
        r'「([^」\n]+)」',              # 直角引号
    ]
    for pat in patterns:
        for m in re.finditer(pat, text):
            d = m.group(1).strip()
            if len(d) >= 8:
                dialogues.append(d)
    return dialogues


def _char_ngram_set(text: str, n: int = 3) -> set:
    """生成字符级 n-gram 集合(用于 Jaccard 相似度)。"""
    text = text.strip()
    if len(text) < n:
        return set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def _jaccard_similarity(text_a: str, text_b: str, n: int = 3) -> float:
    """字符 n-gram Jaccard 相似度(0.0-1.0)。

    注意:Jaccard 对"包含 vs 完全等同"区分弱 — d2 几乎全是 d1 的子集时,Jaccard ~ 0.6
    若要检测"包含/复读",用 _ngram_containment_score 更合适。
    """
    set_a = _char_ngram_set(text_a, n)
    set_b = _char_ngram_set(text_b, n)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    return intersection / union if union > 0 else 0.0


def _ngram_containment_score(text_a: str, text_b: str, n: int = 3) -> float:
    """P0Q.1(2026-05-24)— 字符 n-gram 包含率(0.0-1.0).

    Jaccard 检测"两段相同"较灵敏,但"短句 d2 几乎是 d1 子集"时只能给 ~ 0.6。
    复读检测应用 containment:短句在长句里的覆盖率。
    例:d1="驹子,跟我回客栈去。衣裳都湿透了,着了凉可怎么好。"
       d2="驹子,跟我回客栈去。衣裳都湿透了。"(明显复读 d1 前段)
       Jaccard:0.58(过低)
       Containment:18/18=1.0(d2 全部 3-gram 都在 d1 里)
    """
    set_a = _char_ngram_set(text_a, n)
    set_b = _char_ngram_set(text_b, n)
    if not set_a or not set_b:
        return 0.0
    intersection = len(set_a & set_b)
    min_size = min(len(set_a), len(set_b))
    return intersection / min_size if min_size > 0 else 0.0


def _check_dialogue_repetition(
    conn: sqlite3.Connection,
    sim_id: str,
    new_segment: str,
    threshold: float = 0.7,
) -> list[Violation]:
    """P0Q.1(2026-05-24)— 对白复读检测.

    扫本幕 narrative_segment 提取的每个对白,与历史幕已落库的所有对白做 3-gram
    Jaccard 相似度对比。≥ 阈值 → 加 Violation。

    阈值 0.7:经验值。70% 字符 n-gram 重叠 = 显著复读
    (例:"驹子,跟我回客栈去。衣裳都湿透了" vs 同一句重复 → 重叠 ~95%)
    """
    new_dialogues = _extract_dialogues_from_narrative(new_segment)
    if not new_dialogues:
        return []

    # 拉本 sim 所有历史 narrative_segment
    rows = fetch_all(
        conn,
        """SELECT scene_index, narrative_segment FROM simulation_scenes
           WHERE simulation_id=? AND narrative_segment != ''""",
        (sim_id,),
    )
    if not rows:
        return []

    # 提取历史所有对白(带 scene_index)
    history_dialogues: list[tuple[int, str]] = []
    for r in rows:
        for d in _extract_dialogues_from_narrative(r["narrative_segment"]):
            history_dialogues.append((r["scene_index"], d))

    if not history_dialogues:
        return []

    violations: list[Violation] = []

    # P0R.1(2026-05-24)— 同幕内对白互比(治"驹子'她还穿着这个呢'在本幕重复 2 次")
    # 历史检测在跑时,本 segment 还没落库 → history_dialogues 不含本幕对白
    # 这里先做"本幕内两两互比",catch 同 segment 内的逐字复读
    for i, new_d in enumerate(new_dialogues):
        for j in range(i + 1, len(new_dialogues)):
            other_d = new_dialogues[j]
            if new_d == other_d:
                # 完全相同 → 必报
                violations.append(Violation(
                    severity="critical",
                    category="dialogue_repetition",
                    evidence=new_d[:80],
                    suggestion=(
                        f"本幕内**逐字重复**对白(出现 2 次)。"
                        f"删除其中 1 处,或改写为不同表达。"
                    ),
                    subject_name="",
                ))
                continue
            sim_score = _ngram_containment_score(new_d, other_d, n=3)
            if sim_score >= threshold:
                violations.append(Violation(
                    severity="critical",
                    category="dialogue_repetition",
                    evidence=new_d[:80],
                    suggestion=(
                        f"本幕内对白与另一句高度相似 "
                        f"({int(sim_score * 100)}%):\"{other_d[:60]}\"。"
                        f"请改写其中 1 处为不同措辞。"
                    ),
                    subject_name="",
                ))

    # 历史对白互比(P0Q.1 原版)
    for new_d in new_dialogues:
        for prev_idx, prev_d in history_dialogues:
            # P0Q.1 用 containment 替代 Jaccard — 对"复读"模式更灵敏
            sim_score = _ngram_containment_score(new_d, prev_d, n=3)
            if sim_score >= threshold:
                violations.append(Violation(
                    severity="critical",
                    category="dialogue_repetition",
                    evidence=new_d[:80],
                    suggestion=(
                        f"对白与第 {prev_idx + 1} 幕的某句重复 "
                        f"({int(sim_score * 100)}% 相似度):\"{prev_d[:60]}\"。"
                        f"请改写为不同措辞或换角色表达。"
                    ),
                    subject_name="",
                ))
                break  # 同一新对白只报一次最高匹配
    return violations


def _extract_narrative_sentences(text: str, min_chars: int = 20) -> list[str]:
    """P0T.1(2026-05-24)— 提取叙述句(排除引号内对白).

    流程:
      1. 先 mask 所有引号内对白(替换为占位符)
      2. 按中文句号 / 感叹号 / 问号 / 换行切分
      3. 过滤短句(< min_chars,默认 20) — 短句重复合理(如"她笑了""屋里很静"),
         ≥ 20 字的长句重复才是 LLM 复读病

    Returns: 长叙述句 list(不含对白引号内文本)
    """
    import re
    # mask 各种引号内容(中文 / 英文 / 直角)
    masked = re.sub(r'["""][^""""\n]*["""]', '_DLG_', text)
    masked = re.sub(r'「[^」\n]*」', '_DLG_', masked)
    # 切分句子
    raw_sentences = re.split(r"[。!?\n]+", masked)
    out: list[str] = []
    for s in raw_sentences:
        # 去掉占位符 + 首尾空白
        cleaned = s.replace("_DLG_", "").strip(" ,;:、…—")
        if len(cleaned) >= min_chars:
            out.append(cleaned)
    return out


def _extract_short_narrative_sentences(
    text: str, min_chars: int = 8, max_chars: int = 19,
) -> list[str]:
    """P0U.2(2026-05-24)— 提取 8-19 字的中等长度叙述句.

    用于检测"晨光在纸门上缓缓移动"(12 字)这类**转场套语**复读 ≥ 3 次的情况。
    流程同 _extract_narrative_sentences,但长度窗 [min_chars, max_chars]。
    """
    import re
    masked = re.sub(r'["""][^""""\n]*["""]', '_DLG_', text)
    masked = re.sub(r'「[^」\n]*」', '_DLG_', masked)
    raw_sentences = re.split(r"[。!?\n]+", masked)
    out: list[str] = []
    for s in raw_sentences:
        cleaned = s.replace("_DLG_", "").strip(" ,;:、…—")
        if min_chars <= len(cleaned) <= max_chars:
            out.append(cleaned)
    return out


def _check_narrative_repetition(
    conn: sqlite3.Connection,
    sim_id: str,
    new_segment: str,
    threshold: float = 0.7,
    min_sentence_chars: int = 20,
    short_min_chars: int = 8,
    short_repeat_threshold: int = 3,
) -> list[Violation]:
    """P0T.1 / P0U.2(2026-05-24)— 叙述句复读检测(双阈值).

    治两类盲区:
      - **长句**(≥ min_sentence_chars 默认 20):L61/L125 70 字独白逐字复读、
        L63/L127 24 字嘴唇句复读 — 2 次出现就该 catch
      - **短转场套语**(P0U.2 新加,short_min_chars 到 min_sentence_chars-1 字):
        13574 实测 "晨光在纸门上缓缓移动" 12 字复读 4 次 —
        必须 ≥ short_repeat_threshold(默认 3)次完全相同才报

    流程:
      1. 长句检测(原 P0T.1):
         - 同幕内两两互比 containment ≥ threshold → critical
         - 跨幕跟历史叙述句互比 → critical
      2. 短句检测(P0U.2 新加):
         - 同幕内 + 跨幕统计同字符短句出现频次,≥ short_repeat_threshold → critical

    阈值 0.7 + 4-gram(对白用 3-gram,叙述用 4-gram 更严防短句误伤)
    """
    violations: list[Violation] = []

    # === 长句检测(P0T.1 原版) ===
    new_sentences = _extract_narrative_sentences(new_segment, min_chars=min_sentence_chars)

    # 1. 同幕内两两互比(长句)
    for i, s1 in enumerate(new_sentences):
        for j in range(i + 1, len(new_sentences)):
            s2 = new_sentences[j]
            if s1 == s2:
                violations.append(Violation(
                    severity="critical",
                    category="narrative_repetition",
                    evidence=s1[:80],
                    suggestion=(
                        f"本幕内叙述句**完全逐字重复**(出现 2 次)。"
                        f"必须删除其中 1 处,或改写为不同视角的描写。"
                    ),
                    subject_name="",
                ))
                continue
            score = _ngram_containment_score(s1, s2, n=4)
            if score >= threshold:
                violations.append(Violation(
                    severity="critical",
                    category="narrative_repetition",
                    evidence=s1[:80],
                    suggestion=(
                        f"本幕内叙述句高度相似 ({int(score * 100)}%):\"{s2[:60]}\"。"
                        f"请改写其中 1 处为不同表达。"
                    ),
                    subject_name="",
                ))

    # 2. 跨幕互比(长句):与历史 segment 的叙述句对比
    rows = fetch_all(
        conn,
        """SELECT scene_index, narrative_segment FROM simulation_scenes
           WHERE simulation_id=? AND narrative_segment != ''""",
        (sim_id,),
    )

    history_sentences: list[tuple[int, str]] = []
    history_short_sentences: list[tuple[int, str]] = []
    if rows:
        for r in rows:
            for s in _extract_narrative_sentences(
                r["narrative_segment"], min_chars=min_sentence_chars,
            ):
                history_sentences.append((r["scene_index"], s))
            for s in _extract_short_narrative_sentences(
                r["narrative_segment"],
                min_chars=short_min_chars,
                max_chars=min_sentence_chars - 1,
            ):
                history_short_sentences.append((r["scene_index"], s))

    for new_s in new_sentences:
        for prev_idx, prev_s in history_sentences:
            score = _ngram_containment_score(new_s, prev_s, n=4)
            if score >= threshold:
                violations.append(Violation(
                    severity="critical",
                    category="narrative_repetition",
                    evidence=new_s[:80],
                    suggestion=(
                        f"叙述句与第 {prev_idx + 1} 幕的某句高度重复 "
                        f"({int(score * 100)}% 相似度):\"{prev_s[:60]}\"。"
                        f"内心独白 / 环境描写**绝不能跨幕逐字复用**,必须用不同视角或细节改写。"
                    ),
                    subject_name="",
                ))
                break

    # === 短转场套语检测(P0U.2 新加) ===
    # 统计同字符短句的总频次(本幕 + 跨幕),≥ short_repeat_threshold 报 critical
    new_short_sentences = _extract_short_narrative_sentences(
        new_segment,
        min_chars=short_min_chars,
        max_chars=min_sentence_chars - 1,
    )
    if new_short_sentences:
        # 频次表:同字符短句 -> 总出现次数(本幕 + 历史)
        from collections import Counter
        # 历史出现频次
        history_short_counter: Counter[str] = Counter(
            s for _, s in history_short_sentences
        )
        # 本幕出现频次(去重前,看本幕内出现几次)
        local_short_counter: Counter[str] = Counter(new_short_sentences)

        reported: set[str] = set()  # 同句只报一次
        for short_s, local_count in local_short_counter.items():
            total_count = local_count + history_short_counter.get(short_s, 0)
            if total_count >= short_repeat_threshold and short_s not in reported:
                reported.add(short_s)
                hist_n = history_short_counter.get(short_s, 0)
                breakdown = (
                    f"本幕 {local_count} 次 + 历史 {hist_n} 次 = 共 {total_count} 次"
                    if hist_n > 0 else f"本幕 {local_count} 次"
                )
                violations.append(Violation(
                    severity="critical",
                    category="narrative_repetition",
                    evidence=short_s[:80],
                    suggestion=(
                        f"短转场套语「{short_s[:30]}」复读 {breakdown},"
                        f"≥ {short_repeat_threshold} 次属 LLM 机械模板病。"
                        f"必须改写其中至少 {total_count - 1} 处为不同表达 "
                        f"(换意象 / 换主语 / 换视角,不要每次幕换都用同一句)。"
                    ),
                    subject_name="",
                ))

    return violations


# P0X.2(2026-05-26)— 受限角色违禁动词清单 + flag 推断,抽到共享 module.
# 避免 3 处独立维护漂移(consistency_checker / hard_constraints / 未来 sequel)。
from app.services.physical_constraints_util import (
    PHYSICAL_FORBIDDEN_VERBS as _PHYSICAL_FORBIDDEN_VERBS,
    infer_physical_flags as _infer_physical_flags,
)


def _check_physical_constraints_violation(
    conn: sqlite3.Connection,
    sim: Simulation,
    narrative_segment: str,
) -> list[Violation]:
    """P0W.1(2026-05-24)— 受限角色物理 / 语言能力违规事后检测.

    起源:15070 实测发现 P0V.1 的 hard_constraints 注入只让 LLM "看见"铁律,
    但**LLM 创作冲动压倒约束**,仍写出:
      L79: "她拄着木杖,缓缓走到驹子面前"(半身不遂禁止拄杖步行)
      L87: "师傅松开木杖,缓缓蹲下身,用粗糙的手掌抹去驹子脸上的雪水"(禁止蹲下+伸手)
      L91: "师傅站直身子" + 说 4 完整分句(禁止站立 + 整句对白)
      L99: "师傅握住驹子发抖的手腕,自己伸手掀开白布一角"

    本函数事后扫描:
      1. 拉项目所有非 deceased 角色,推出 physical_flags
      2. 对每个受限角色,扫 narrative 句子:
         - 找角色名(或 aliases)出现位置
         - 在角色名后 0-25 字窗口内找违禁动词 → critical
      3. 扫该角色的引号对白,若含 ≥ 2 个完整分句(。!? 计数)→ critical
    """
    if not narrative_segment or len(narrative_segment.strip()) < 50:
        return []

    # 1. 拉项目受限角色 (name, aliases_json, flags)
    try:
        char_rows = fetch_all(
            conn,
            "SELECT name, aliases_json, status_note FROM characters "
            "WHERE project_id=? AND life_status != 'deceased'",
            (sim.project_id,),
        )
    except Exception as e:  # noqa: BLE001 — schema 可能缺 aliases_json 列(老 DB)
        # B1-C(2026-05-27):加 log 防 silent failure
        logger.warning(
            f"_check_physical_constraints_violation: SELECT with aliases_json "
            f"failed sim={sim.id} project={sim.project_id}, fallback to no-aliases query: {e}"
        )
        try:
            char_rows = fetch_all(
                conn,
                "SELECT name, status_note FROM characters "
                "WHERE project_id=? AND life_status != 'deceased'",
                (sim.project_id,),
            )
        except Exception as e2:  # noqa: BLE001 — schema 也缺 life_status 列(更老 DB)
            logger.warning(
                f"_check_physical_constraints_violation: fallback SELECT also failed "
                f"sim={sim.id} project={sim.project_id} — returning empty: {e2}"
            )
            return []

    restricted: list[tuple[str, list[str], list[str]]] = []
    for r in char_rows:
        flags = _infer_physical_flags(r["status_note"] or "")
        if not flags:
            continue
        name = r["name"]
        try:
            aliases_raw = r["aliases_json"] or "[]"
            aliases = json.loads(aliases_raw) if isinstance(aliases_raw, str) else []
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            # B1-C(2026-05-27):aliases_json 格式错误(损坏 / 截断 / 非 string)→ 加 log
            logger.warning(
                f"_check_physical_constraints_violation: aliases_json parse failed "
                f"for character={name!r} sim={sim.id} — falling back to []: {e}"
            )
            aliases = []
        # 合并所有名字(本名 + aliases)
        all_names = [name] + [a for a in aliases if isinstance(a, str) and a]
        restricted.append((name, all_names, flags))

    if not restricted:
        return []

    violations: list[Violation] = []

    # 2. 切句(简单按 。!?\n 切)
    sentences = re.split(r"[。!?\n]+", narrative_segment)

    for char_name, all_names, flags in restricted:
        # 合并所有受限动词
        forbidden_verbs: set[str] = set()
        for f in flags:
            forbidden_verbs.update(_PHYSICAL_FORBIDDEN_VERBS.get(f, []))
        if not forbidden_verbs:
            continue

        # 2a. 扫叙述句:角色名 + 后置违禁动词(主语在前的简单启发)
        violated_actions: list[tuple[str, str]] = []   # (verb, sentence)
        for s in sentences:
            for n in all_names:
                idx = s.find(n)
                if idx < 0:
                    continue
                # 在角色名后 0-25 字窗口找违禁动词
                window = s[idx + len(n): idx + len(n) + 25]
                for verb in forbidden_verbs:
                    if verb in window:
                        violated_actions.append((verb, s.strip()))
                        break
                if violated_actions and violated_actions[-1][1] == s.strip():
                    break   # 同一句只报一次

        for verb, sent in violated_actions[:3]:   # 每个角色最多报 3 条
            flag_str = " / ".join(flags)
            violations.append(Violation(
                severity="critical",
                category="physical_constraint_violation",
                evidence=sent[:80],
                suggestion=(
                    f"角色「{char_name}」({flag_str})做了违禁动作「{verb}」。"
                    f"按设定,该角色不能完成此类动作。请改写为合规姿态:"
                    f"半身不遂 → 卧床 / 倚靠 / 被他人搀扶;"
                    f"卧床 → 别人到她床前来,她不下床;"
                    f"失明 → 改写为听 / 摸 / 凭声音判断;"
                    f"失语 → 改写为含混嘟囔 / 单字 / 比划。"
                ),
                subject_name=char_name,
            ))

        # 2b. 扫该角色的对白:看是否含 ≥ 2 个完整分句(违反语言能力锁定)
        # 仅适用于"半身不遂/卧床/昏迷/失语" — 这些状态下角色应只能含混断词
        if not any(f in flags for f in ("半身不遂", "卧床", "昏迷", "失语")):
            continue
        # 找角色对白:扫"<角色名>...':<引号>...<引号>" 或 "<引号>...<引号>...<角色名>...说/道"
        # 简化:扫所有引号对白,看引号前 30 字内是否有该角色名
        dialogue_pat = re.compile(r'(["""「])([^"""」\n]+)([""」"])')
        for m in dialogue_pat.finditer(narrative_segment):
            dialogue_text = m.group(2)
            if len(dialogue_text) < 10:
                continue
            # 引号前 50 字内是否含该角色名
            lead_start = max(0, m.start() - 50)
            lead_text = narrative_segment[lead_start: m.start()]
            if not any(n in lead_text for n in all_names):
                # 引号后 30 字内也找一下("<对白>",师傅说)
                tail_text = narrative_segment[m.end(): m.end() + 30]
                if not any(n in tail_text for n in all_names):
                    continue
            # 统计完整分句数:。!? 数量
            terminator_count = sum(1 for c in dialogue_text if c in "。!?")
            if terminator_count >= 2:
                violations.append(Violation(
                    severity="critical",
                    category="physical_constraint_violation",
                    evidence=dialogue_text[:80],
                    suggestion=(
                        f"角色「{char_name}」({' / '.join(flags)})说了**连续 "
                        f"{terminator_count + 1} 个完整分句**的整句对白。"
                        f"按设定,该角色只能含混断词 / 单字呼唤 / 5 字以内短句。"
                        f"反例(本对白):「{dialogue_text[:60]}」。"
                        f"正例:「驹……粥……让那丫头……进来」"
                    ),
                    subject_name=char_name,
                ))
                if sum(1 for v in violations if v.subject_name == char_name) >= 5:
                    break   # 每角色最多 5 条违规,避免 retry hint 过长

    return violations


# =============================================================================
# M10.A 文风滤镜 + M10.B 意象库 — 消费 P3 author_compass(2026-05-27)
# =============================================================================

def _check_style_drift(
    conn: sqlite3.Connection,
    project_id: str,
    narrative_segment: str,
    *,
    length_tolerance: float = 0.15,
    dialogue_tolerance: float = 0.15,
    paragraph_tolerance_ratio: float = 0.30,
) -> list[Violation]:
    """M10.A(2026-05-27)— 文风滤镜:实测 vs author_compass 目标偏差.

    P3 作者指南针把"目标文风"(句长/对白率/感官/段长)注入到 prompt 是软提示。
    本函数事后实测产物各项指标,与画像目标对比,误差超阈值 → critical violation。

    阈值:
      - 句长/对白率/感官比例:绝对差 > length_tolerance(默认 0.15)
      - 段长字数:相对差 > paragraph_tolerance_ratio(默认 30%)

    若项目没有 author_compass(用户没跑 P3)→ 返空列表(本函数完全跳过)。
    若 author_compass 的 internal_status != 'done' → 返空(目标尚未确认)。
    """
    if not narrative_segment or len(narrative_segment.strip()) < 200:
        return []   # 产物太短统计无意义

    # 拉 author_compass(若 user_locked=True 用 final_compass.internal_metrics,否则用原始 internal_metrics)
    try:
        from app.services.author_compass_service import get_compass
        compass = get_compass(conn, project_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"_check_style_drift: get_compass failed: {e}")
        return []
    if compass is None:
        return []

    target_metrics: Optional[dict] = None
    if compass.user_locked and compass.final_compass:
        # final_compass 可能是扁平结构(直接 internal_metrics) 或 包了一层
        if "internal_metrics" in compass.final_compass:
            target_metrics = compass.final_compass.get("internal_metrics")
        elif "句长" in compass.final_compass:
            target_metrics = compass.final_compass
    else:
        if compass.internal_status == "done":
            target_metrics = compass.internal_metrics
    if not isinstance(target_metrics, dict):
        return []

    # 实测
    from app.services.style_filter_service import (
        compute_drift, measure_style_metrics, top_sense,
    )
    measured = measure_style_metrics(narrative_segment)

    violations: list[Violation] = []

    # --- 1. 句长分布 ---(数据充足 ≥ 3 句才比较)
    target_sl = target_metrics.get("句长")
    if isinstance(target_sl, dict) and measured["_meta"]["sentence_count"] >= 3:
        for key in ("短句占比", "中句占比", "长句占比"):
            t_val = target_sl.get(key)
            m_val = measured["句长"].get(key)
            if isinstance(t_val, (int, float)) and isinstance(m_val, (int, float)):
                is_drift, diff = compute_drift(m_val, t_val, length_tolerance)
                if is_drift:
                    violations.append(Violation(
                        severity="critical",
                        category="style_drift",
                        evidence=f"{key} 实测 {int(m_val * 100)}% vs 目标 {int(t_val * 100)}%(差 {int(diff * 100)} 个百分点)",
                        suggestion=(
                            f"作者画像目标:{key} = {int(t_val * 100)}%;"
                            f"本幕实测 = {int(m_val * 100)}%,偏差超 {int(length_tolerance * 100)} 个百分点。"
                            f"请调整本幕**句子粒度** — 增加/减少 ≤15 字的短句、16-40 中句、>40 长句的比例,贴合作者实际文风。"
                        ),
                        subject_name="",
                    ))

    # --- 2. 对白率 ---
    target_dr = target_metrics.get("对白率")
    if isinstance(target_dr, dict):
        t_val = target_dr.get("比例")
        m_val = measured["对白率"]["比例"]
        if isinstance(t_val, (int, float)) and m_val > 0:
            is_drift, diff = compute_drift(m_val, t_val, dialogue_tolerance)
            if is_drift:
                violations.append(Violation(
                    severity="critical",
                    category="style_drift",
                    evidence=f"对白率 实测 {int(m_val * 100)}% vs 目标 {int(t_val * 100)}%",
                    suggestion=(
                        f"作者画像目标:对白率 = {int(t_val * 100)}%;"
                        f"本幕实测 = {int(m_val * 100)}%(差 {int(diff * 100)} 个百分点)。"
                        f"对白多了减对白,改为叙述层动作/景物/心理;"
                        f"对白少了加对白,让角色互动外显化。"
                    ),
                    subject_name="",
                ))

    # --- 3. 感官比例(主导感官应一致)---
    target_sense = target_metrics.get("感官比例")
    if isinstance(target_sense, dict):
        # 排除 _meta / 评注 字段
        target_pure = {k: v for k, v in target_sense.items()
                       if k in ("视觉", "听觉", "嗅觉", "触觉", "味觉") and isinstance(v, (int, float))}
        if target_pure:
            target_top = top_sense(target_pure)
            measured_top = top_sense(measured["感官比例"])
            if target_top and measured_top and target_top != measured_top:
                violations.append(Violation(
                    severity="critical",
                    category="style_drift",
                    evidence=f"主导感官 实测 {measured_top} vs 目标 {target_top}",
                    suggestion=(
                        f"作者画像目标:主导感官 = {target_top};"
                        f"本幕实测主导 = {measured_top}。"
                        f"作者的感官风格已漂移 — 请把本幕描写重心改回 {target_top}(增加 {target_top} 类关键词与场景描写,"
                        f"减少 {measured_top} 类的密度)。"
                    ),
                    subject_name="",
                ))

    # --- 4. 段长 ---
    target_para = target_metrics.get("段落节奏")
    if isinstance(target_para, dict):
        t_val = target_para.get("平均段长字数")
        m_val = measured["段落节奏"]["平均段长字数"]
        if isinstance(t_val, int) and t_val > 0 and m_val > 0:
            rel_diff = abs(m_val - t_val) / t_val
            if rel_diff > paragraph_tolerance_ratio:
                violations.append(Violation(
                    severity="critical",
                    category="style_drift",
                    evidence=f"平均段长 实测 {m_val} 字 vs 目标 {t_val} 字",
                    suggestion=(
                        f"作者画像目标:平均段长 ≈ {t_val} 字;"
                        f"本幕实测 = {m_val} 字(相对偏差 {int(rel_diff * 100)}%,阈值 {int(paragraph_tolerance_ratio * 100)}%)。"
                        f"段落"
                        + ("过长 — 拆分成更多短段" if m_val > t_val else "过短 — 合并相关短段成更厚实的段落")
                        + ",贴合作者节奏。"
                    ),
                    subject_name="",
                ))

    # --- 5. P5.3(2026-05-27)— 身体描写尺度对齐(治"含蓄原作回避得连含蓄都没了")---
    # 治挪威森林第 23 幕"第一次做爱"被直接跳到第二天早餐的问题
    target_body = target_metrics.get("身体描写尺度")
    if isinstance(target_body, dict):
        target_freq = target_body.get("频率")
        if isinstance(target_freq, str) and target_freq in ("none", "rare", "occasional", "frequent"):
            from app.services.style_filter_service import (
                compute_body_register_frequency,
                frequency_drift_distance,
            )
            measured_freq = compute_body_register_frequency(narrative_segment)
            drift = frequency_drift_distance(target_freq, measured_freq)
            # 跨档错位 ≥ 2 → critical(rare ↔ frequent / occasional ↔ none)
            if drift >= 2:
                # 判方向 + 给具体建议
                target_idx = ("none", "rare", "occasional", "frequent").index(target_freq)
                measured_idx = ("none", "rare", "occasional", "frequent").index(measured_freq)
                if target_idx > measured_idx:
                    # 目标高于实测 — 原作有此类内容,续作回避了(灵魂没了)
                    direction = (
                        f"原作身体描写频率为 {target_freq},续作实测为 {measured_freq} — "
                        f"**续作回避了原作风骨中本该有的身体描写**(灵魂没了)。"
                        f"如挪威森林那种 frequent 原作,续作必须保留亲密 / 性描写场景;"
                        f"不许用煎蛋 / 喝粥 / 看风景的日常琐事跳过床戏。"
                    )
                else:
                    # 目标低于实测 — 原作含蓄,续作搞出露骨(灵魂错位)
                    direction = (
                        f"原作身体描写频率为 {target_freq}(含蓄风骨),续作实测为 {measured_freq} — "
                        f"**续作搞出了原作不该有的露骨描写**(灵魂错位)。"
                        f"如雪国那种 rare/sensual-implicit 原作,续作不许出现直白的床戏场景。"
                    )
                violations.append(Violation(
                    severity="critical",
                    category="style_drift",
                    evidence=f"身体描写频率 实测 {measured_freq} vs 目标 {target_freq}(差 {drift} 档)",
                    suggestion=direction,
                    subject_name="",
                ))

    return violations


def _check_imagery_violations(
    conn: sqlite3.Connection,
    project_id: str,
    narrative_segment: str,
) -> list[Violation]:
    """M10.B(2026-05-27)— 意象库:雷区命中 / 推荐意象缺位.

    消费 P3 author_compass:
      - external_profile.雷区:命中 → critical(违反作家"绝对不写"清单)
      - internal_metrics.意象偏好:本幕一个推荐意象都没用 → warning(风格偏离)

    若没有 author_compass → 返空列表(本函数完全跳过)。
    """
    if not narrative_segment or len(narrative_segment.strip()) < 100:
        return []

    try:
        from app.services.author_compass_service import get_compass
        compass = get_compass(conn, project_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"_check_imagery_violations: get_compass failed: {e}")
        return []
    if compass is None:
        return []

    # 读雷区 + 推荐意象(锁定优先)
    external_profile: Optional[dict] = None
    internal_metrics: Optional[dict] = None
    if compass.user_locked and compass.final_compass:
        external_profile = compass.final_compass.get("external_profile")
        internal_metrics = compass.final_compass.get("internal_metrics")
        # 兼容扁平结构
        if external_profile is None and "雷区" in compass.final_compass:
            external_profile = compass.final_compass
        if internal_metrics is None and "意象偏好" in compass.final_compass:
            internal_metrics = compass.final_compass
    else:
        if compass.external_status == "done":
            external_profile = compass.external_profile
        if compass.internal_status == "done":
            internal_metrics = compass.internal_metrics

    violations: list[Violation] = []

    # --- 1. 雷区命中 → critical ---
    if isinstance(external_profile, dict):
        forbidden_items = external_profile.get("雷区")
        if isinstance(forbidden_items, list):
            for item in forbidden_items:
                if not isinstance(item, str) or not item.strip():
                    continue
                # 雷区描述往往是抽象短语(如"现代俚语" / "大段直白心理独白")
                # 直接子串匹配会把"心理"等正常字眼当雷区误伤
                # 改用:命中 ≥ 2 个雷区描述里的 ≥ 2 字关键词 → 提示
                # 但为了实现简单 + 误报率低,这里只做完整短语匹配
                # (作家"雷区"通常是 2-6 字短语,LLM 真写出这些短语就该报)
                fragment = item.strip()
                # 提炼关键词:取雷区描述里 ≥ 2 字的中文子串(去掉 / 和空格)
                # 简化:只用整个雷区短语做包含匹配
                # 如雷区 = "现代俚语 / 流行语" → 检测产物里是否出现"现代俚语"或"流行语"
                # 分号 / 逗号 / 顿号 / 斜杠 拆分
                kw_parts = re.split(r"[、,/;;,]\s*", fragment)
                for kw in kw_parts:
                    kw = kw.strip()
                    if len(kw) < 2:
                        continue
                    if kw in narrative_segment:
                        violations.append(Violation(
                            severity="critical",
                            category="imagery_violation",
                            evidence=f"命中雷区「{kw}」",
                            suggestion=(
                                f"作者画像雷区:「{item}」— 本作家**绝对不写**此类内容。"
                                f"本幕命中关键词「{kw}」,需删除该描写或改写为符合作家风格的表达。"
                            ),
                            subject_name="",
                        ))
                        break   # 同一条雷区项报一次

    # --- 2. 推荐意象缺位 → warning(0 命中视为风格偏离)---
    if isinstance(internal_metrics, dict):
        rec_imagery = internal_metrics.get("意象偏好")
        if isinstance(rec_imagery, list):
            valid_imagery = [i.strip() for i in rec_imagery if isinstance(i, str) and i.strip()]
            if valid_imagery:
                hits = [img for img in valid_imagery if img in narrative_segment]
                if not hits:
                    # 一个推荐意象都没用 — 风格偏离信号
                    sample = "、".join(valid_imagery[:5])
                    violations.append(Violation(
                        severity="warning",
                        category="imagery_violation",
                        evidence=f"推荐意象 0 命中(画像清单:{sample}...)",
                        suggestion=(
                            f"作者画像推荐意象:{sample}{'...' if len(valid_imagery) > 5 else ''} "
                            f"— 本幕这些核心意象**一个都没用**。"
                            f"作家文风的核心是意象;若本幕完全跳过这套意象库,产物会脱离作者风格。"
                            f"请在本幕中至少自然引用 1-2 个推荐意象。"
                        ),
                        subject_name="",
                    ))

    return violations


# =============================================================================
# P5.1 outline 执行率检测(2026-05-27)— 治"剧情空心化"
# =============================================================================
#
# 起源:2026-05-27 实测挪威森林 28 幕续作,覆盖率仅 57%。LLM 接到"村上风格 / 留白"
# 提示词后,把算力全用于氛围渲染(硬币 / 烟雾 / 衬衫),关键剧情(打电话 / 问是否爱直子 /
# 葬礼后崩溃大哭 / 第一次做爱)被日常琐事(吃面 / 喝酒 / 看星星)替换。
#
# 治法:扫产物 vs 当前幕的 key_events,关键词覆盖率 < 50% → critical → retry。
# 配合 P5.4 outline_scene prompt 铁律加强,prompt+事后审计双层。

# 中文虚词 / 停用词(不算关键词)
_STOPWORDS_CN: frozenset[str] = frozenset({
    "的", "了", "是", "在", "和", "与", "及", "或", "等",
    "他", "她", "它", "我", "你", "们", "之", "其", "也",
    "这", "那", "里", "中", "上", "下", "前", "后",
    "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
    "个", "次", "件", "条", "对", "把", "给", "为", "从", "到",
    "有", "无", "已", "未", "可", "能", "会", "要", "想", "说",
    "就", "也", "都", "还", "再", "又", "并", "却", "但",
    "以", "于", "如", "若", "因", "所", "被", "让", "使",
})


def _extract_keywords_from_event(event: str) -> list[str]:
    """从 key_event 描述里提取核心关键词(用于检测 narrative 是否覆盖).

    策略:
      1. 用标点 / 停用词 / 空白作天然分界,切出"非停用词连续段"
      2. 整段长 ≥ 2 字 → 入关键词
      3. 段长 ≥ 3 字 → 再拆所有 2-字 substring(增强匹配灵活度)

    例:
      "渡边在电话亭与绿子通话" 切片 → ["渡边", "电话亭", "绿子通话"]
        → 关键词:["渡边", "电话亭", "电话", "话亭", "绿子通话", "绿子", "子通", "通话"]
      "永泽告诉他通过了外交官考试" 切片 → ["永泽告诉", "通过", "外交官考试"]
        → 关键词含 "永泽", "外交官" 等

    narrative 检测:任一关键词出现就算该 event 命中(宽松匹配,降低误报)
    """
    if not event or not isinstance(event, str):
        return []
    keywords: list[str] = []
    seen: set[str] = set()

    # 按"非停用词连续序列"切段
    segments: list[str] = []
    current = ""
    for ch in event:
        # 标点 / 数字 / 空白 / 停用词 都作分界
        if (
            ch in _STOPWORDS_CN
            or not ch.strip()
            or bool(re.match(r"[\W\d]", ch))
        ):
            if len(current) >= 2:
                segments.append(current)
            current = ""
        else:
            current += ch
    if len(current) >= 2:
        segments.append(current)

    # 每段输出整段 + 2-字 substring
    for seg in segments:
        if seg not in seen:
            keywords.append(seg)
            seen.add(seg)
        if len(seg) >= 3:
            for i in range(len(seg) - 1):
                sub = seg[i:i + 2]
                if sub not in seen:
                    keywords.append(sub)
                    seen.add(sub)
    return keywords


def _check_outline_execution(
    conn: sqlite3.Connection,
    sim_id: str,
    scene_index: int,
    narrative_segment: str,
    *,
    coverage_threshold: float = 0.6,
) -> list[Violation]:
    """P5.1(2026-05-27)— outline key_events 执行率检测.

    流程:
      1. 拉 sim 的 outline_scenes(若无 outline → 返空,本检测跳过)
      2. 取当前 scene_index 对应的 outline_scene
      3. 对每条 key_event,提取核心关键词,扫 narrative 看是否至少 1 个关键词命中
      4. 覆盖率 = 命中条数 / 总条数。覆盖率 < threshold → critical
         单条 0 命中 也单独报(明确告诉用户哪条事件被跳过)

    阈值:0.5 — 至少一半 key_events 要被覆盖。低于此 → 剧情空心化 → retry
    """
    if not narrative_segment or len(narrative_segment.strip()) < 100:
        return []

    # 拉 outline_scenes
    try:
        from app.services.outline_orchestrator import load_outline_scenes_for_sim
        outline_scenes = load_outline_scenes_for_sim(conn, sim_id)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"_check_outline_execution: load_outline_scenes failed: {e}")
        return []
    if not outline_scenes:
        return []   # 该 sim 无 outline(走原 evolution 路径),本检测跳过

    # 找当前 scene
    current_scene = None
    for sc in outline_scenes:
        if sc.scene_index == scene_index:
            current_scene = sc
            break
    if current_scene is None or not current_scene.key_events:
        return []

    # 检查每条 key_event
    # 先提取所有 event 的关键词
    all_event_kws: list[list[str]] = [
        _extract_keywords_from_event(ev) for ev in current_scene.key_events
    ]

    # 对每条 event,算"独特关键词"(只在该 event 出现,其他 event 不含)
    # 避免共享词(如"渡边/绿子"贯穿全篇)误判为命中。
    # 若该 event 无独特词(所有 kw 都跟其他 event 重叠)→ fallback 用全部 kw。
    violations: list[Violation] = []
    missed_events: list[tuple[int, str]] = []
    hit_count = 0

    for idx, event in enumerate(current_scene.key_events):
        keywords = all_event_kws[idx]
        if not keywords:
            hit_count += 1   # 没法提取关键词的 event 默认算覆盖(避免误报)
            continue
        # 算其他 event 的所有关键词
        other_kws: set[str] = set()
        for j, kws in enumerate(all_event_kws):
            if j != idx:
                other_kws.update(kws)
        # 留下独特词
        unique_kws = [k for k in keywords if k not in other_kws]
        if not unique_kws:
            unique_kws = keywords   # fallback
        # 任一独特词出现就算命中
        if any(uk in narrative_segment for uk in unique_kws):
            hit_count += 1
        else:
            missed_events.append((idx, event))

    total = len(current_scene.key_events)
    coverage = hit_count / total if total > 0 else 1.0

    # 单条 0 命中的 event 报 warning(明确指出哪条被跳)
    for idx, ev in missed_events:
        keywords = _extract_keywords_from_event(ev)
        kw_str = " / ".join(keywords[:5])
        violations.append(Violation(
            severity="warning",
            category="outline_execution",
            evidence=f"key_event #{idx + 1} 被跳过:{ev[:50]}",
            suggestion=(
                f"第 {scene_index + 1} 幕的 outline 第 {idx + 1} 条关键事件「{ev[:50]}」"
                f"在 narrative 中无任何关键词命中(检查的关键词:{kw_str})。"
                f"按 P5.4 铁律,必须用**显性对白或明确动作**完成它,"
                f"不许用日常琐事(抽烟/喝酒/看风景)替换。"
            ),
            subject_name="",
        ))

    # 整体覆盖率不达标 → critical 触发 retry
    if coverage < coverage_threshold:
        missed_summary = "; ".join(
            f"#{idx + 1}「{ev[:30]}」" for idx, ev in missed_events[:3]
        )
        violations.append(Violation(
            severity="critical",
            category="outline_execution",
            evidence=(
                f"第 {scene_index + 1} 幕剧情空心化:key_events 覆盖率 "
                f"{int(coverage * 100)}%(命中 {hit_count}/{total},"
                f"阈值 {int(coverage_threshold * 100)}%)"
            ),
            suggestion=(
                f"本幕大纲规定了 {total} 条关键事件,实际只完成了 {hit_count} 条。"
                f"被跳过的事件:{missed_summary}{'...' if len(missed_events) > 3 else ''}"
                f" 请重写本幕,**每条 key_event 用显性对白或明确动作完成**,"
                f"严禁用日常琐事(吃饭 / 抽烟 / 看风景)替换核心冲突。"
            ),
            subject_name="",
        ))

    return violations


def _check_dialogue_phrase_repetition(
    conn: sqlite3.Connection,
    sim_id: str,
    new_segment: str,
    critical_threshold: int = 5,
    min_phrase_chars: int = 4,
) -> list[Violation]:
    """P0V.4(2026-05-24)— 对白主题词反复检测.

    起源:雪国 14345 浜松男人在 7 个对白里**反复说**"跟我回浜松":
      L19/L21/L25/L27/L31/L45 — 6 个完整对白都含"跟我回浜松"子串。
      P0Q.1 对白复读检测看的是完整对白的 containment ≥ 0.7,
      但每条对白后续不同(管你吃穿 / 别在这儿耗着 / 把婚事办了),
      整句 containment 都低于 0.7 → 漏。
      实际**子串"跟我回浜松"出现 6 次**,这是对白"主题词锁定"病。

    工作流:
      1. 收集本 segment + 历史所有 segment 的所有对白(引号内)
      2. 把每条对白当文本,统计 ≥ min_phrase_chars 字 n-gram 累计频次
      3. 任一 phrase 在不同对白里累计 ≥ critical_threshold → 报 critical
         (注意:**同一对白内重复不算**,要跨**不同对白**才计)

    阈值默认:
      - critical_threshold = 5(雪国浜松男人 6 次 ≥ 5 触发)
      - min_phrase_chars = 4(短于 4 字的"我说""你听"是常用搭配)
    """
    from collections import Counter

    # 1. 收集所有对白(本 segment + 历史)
    rows = fetch_all(
        conn,
        """SELECT narrative_segment FROM simulation_scenes
           WHERE simulation_id=? AND narrative_segment != ''""",
        (sim_id,),
    )
    all_dialogues: list[str] = []
    for r in rows:
        all_dialogues.extend(_extract_dialogues_from_narrative(r["narrative_segment"]))
    all_dialogues.extend(_extract_dialogues_from_narrative(new_segment))

    if len(all_dialogues) < 3:
        return []   # 对白太少,统计无意义

    # 2. 统计 n-gram 在不同对白中的"出现的对白数"(set 去重同一对白内重复)
    phrase_dialogue_count: Counter[str] = Counter()
    for d in all_dialogues:
        seen_in_this: set[str] = set()
        for n in (4, 5, 6):   # 4-6 字短语
            for i in range(len(d) - n + 1):
                phrase = d[i:i + n]
                # 过滤常用搭配 + 无意义短语
                if phrase in seen_in_this:
                    continue
                # 跳过含太多 filler 字的短语(纯口头禅"我说啊你"之类)
                # 用 phrase_blacklist 的 _STOP_FILLER_CHARS
                from app.services.phrase_blacklist import _STOP_FILLER_CHARS
                if sum(1 for c in phrase if c in _STOP_FILLER_CHARS) >= len(phrase) * 0.6:
                    continue
                seen_in_this.add(phrase)
        for p in seen_in_this:
            phrase_dialogue_count[p] += 1

    # 3. 找出累计 ≥ critical_threshold 的 phrase
    violations: list[Violation] = []
    reported: set[str] = set()
    for phrase, count in phrase_dialogue_count.most_common(20):
        if count < critical_threshold:
            break
        if phrase in reported:
            continue
        # 子串去重:已报"跟我回浜松"就不再报"我回浜松"
        if any(phrase in r and len(r) > len(phrase) for r in reported):
            continue
        # 反向:如果某更长 phrase 包含本 phrase 且 count 接近,跳过本 phrase
        skip = False
        for longer, longer_count in phrase_dialogue_count.most_common(20):
            if len(longer) > len(phrase) and phrase in longer and longer_count >= count - 1:
                skip = True
                break
        if skip:
            continue
        reported.add(phrase)
        violations.append(Violation(
            severity="critical",
            category="dialogue_phrase_repetition",
            evidence=phrase,
            suggestion=(
                f"对白主题词「{phrase}」在 {count} 条不同对白中出现(阈值 {critical_threshold})。"
                f"角色诉求一致可以,但**字面表达必须有变化** — 不要用一字不差的短语反复说。"
                f"反例:角色 X 要求带走主角,说了 6 次「跟我回浜松」 — 改成「这日子你过够了」/"
                f"「随我走吧」/「跟我离开这破地方」等不同措辞。"
            ),
            subject_name="",
        ))
        if len(violations) >= 3:
            break

    return violations


def _check_phrase_density(
    conn: sqlite3.Connection,
    sim_id: str,
    new_segment: str,
    critical_threshold: int = 12,
) -> list[Violation]:
    """P0V.3(2026-05-24)— 意象主题词刷屏检测.

    起源:雪国 14345 实测发现:
      - "夜雾" 2-gram 出现 27+ 次
      - "焦琴" 2-gram 出现 15+ 次
      - "灯笼光" 3-gram 出现 15+ 次
      LLM 的"主题词锁定"病 — 一旦定下意象就反复刷屏。
      phrase_blacklist 当前只在 narrator prompt 提示"避免使用",但 LLM 仍想用。
      升级为 critical violation 触发 retry,强制 LLM 换意象。

    工作流:
      1. 合并 new_segment + 历史所有 narrative_segment,统计 2/3/4-gram 频次
      2. 任一 phrase 累计 ≥ critical_threshold 次 → 报 critical

    阈值 12:
      - 14345 实测"夜雾" 27 / "焦琴" 15 / "灯笼光" 15 都触发
      - "残火" 11 / "焦木味" 8 / 普通密度词不触发
      - 比 phrase_blacklist 的提示阈值 5 高 2-3 倍,确认是"刷屏级"才报
    """
    from app.services.phrase_blacklist import (
        _normalize_segment, _extract_ngrams, NGRAM_SIZES,
    )
    from collections import Counter

    rows = fetch_all(
        conn,
        """SELECT narrative_segment FROM simulation_scenes
           WHERE simulation_id=? AND narrative_segment != ''""",
        (sim_id,),
    )
    history_text = " ".join(_normalize_segment(r["narrative_segment"]) for r in rows)
    new_text = _normalize_segment(new_segment)
    combined = history_text + " " + new_text

    if len(combined) < 100:
        return []   # 数据不足,不判定

    counter: Counter[str] = Counter()
    for n in NGRAM_SIZES:
        counter.update(_extract_ngrams(combined, n))

    # 找出 ≥ critical_threshold 的 phrase
    violations: list[Violation] = []
    reported: set[str] = set()
    for phrase, freq in counter.most_common(30):
        if freq < critical_threshold:
            break   # most_common 倒序,后面只会更低
        if phrase in reported:
            continue
        # 子串去重:已报"焦琴的断弦"就不再报"焦琴"
        if any(phrase in r and len(r) > len(phrase) for r in reported):
            continue
        reported.add(phrase)
        violations.append(Violation(
            severity="critical",
            category="phrase_density",
            evidence=phrase,
            suggestion=(
                f"意象主题词「{phrase}」累计出现 {freq} 次(阈值 {critical_threshold}),"
                f"已成 LLM 主题词刷屏。必须**完全换一组意象** — 不要再用此词或近义词,"
                f"换不同感官 / 不同动作 / 不同物件来推进场景。"
                f"普适性反例:雪国'雪/银河'用多 → 改'冰/月光';"
                f"科幻'屏幕/数据'用多 → 改'指尖触感/低鸣声'。"
            ),
            subject_name="",
        ))
        if len(violations) >= 5:
            break   # 一次最多报 5 个,避免 retry hint 过长

    return violations


def check_segment(
    conn: sqlite3.Connection,
    sim: Simulation,
    scene_index: int,
    narrative_segment: str,
    agents: list[Character],
) -> tuple[CheckResult, dict]:
    """LLM 判定 narrative_segment 是否违规。返回 (CheckResult, usage)。

    Args:
      sim: 当前 sim(必须 mode='evolution')
      scene_index: 本幕索引
      narrative_segment: narrator 刚合稿的产物
      agents: 本幕在场角色(含 behavior_baseline 或 fallback)

    Returns:
      (CheckResult, llm_usage_dict)
      LLM 失败 → 返空 CheckResult(无违规,主流程继续)

    Sprint 6.A2 FOCUS.2(2026-05-21):函数入口先跑程序级人称漂移检测,
    检测出 critical 漂移会被加入最终 violations,触发 narrator retry。
    人称检测**不调 LLM**(纯字符串扫描),不计 cost。
    """
    if not narrative_segment or len(narrative_segment.strip()) < 50:
        return CheckResult(violations=[]), {"input_tokens": 0, "output_tokens": 0}

    # Sprint 6.A2 FOCUS.2:程序级人称漂移检测(零 LLM 成本,先跑)
    pov_violations: list[Violation] = []
    try:
        pov_row = fetch_one(
            conn,
            "SELECT narrative_pov FROM projects WHERE id=?",
            (sim.project_id,),
        )
        project_pov = None
        if pov_row is not None:
            raw_pov = pov_row["narrative_pov"]
            if isinstance(raw_pov, str) and raw_pov in {"first", "second", "third", "mixed"}:
                project_pov = raw_pov
        pov_violations = _check_narrative_pov_drift(
            narrative_segment, project_pov, agents,
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"narrative_pov drift check failed sim={sim.id}: {e}")
        pov_violations = []

    # P0Q.1(2026-05-24)— 对白复读程序级检测(零 LLM 成本)
    dialogue_repetition_violations: list[Violation] = []
    try:
        dialogue_repetition_violations = _check_dialogue_repetition(
            conn, sim.id, narrative_segment, threshold=0.7,
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"dialogue repetition check failed sim={sim.id}: {e}")

    # P0T.1(2026-05-24)— 叙述句复读程序级检测(零 LLM 成本)
    # 治"L61/L125 内心独白逐字复读" / "L63/L127 嘴唇描写复用"这类 P0Q.1 漏的盲区:
    # 对白检测器只扫引号内文本,叙述层(narrator 的内心独白 / 环境描写)绕过完全。
    narrative_repetition_violations: list[Violation] = []
    try:
        narrative_repetition_violations = _check_narrative_repetition(
            conn, sim.id, narrative_segment, threshold=0.7,
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"narrative repetition check failed sim={sim.id}: {e}")

    # P0V.3(2026-05-24)— 意象主题词刷屏检测(零 LLM 成本)
    # 治雪国 14345 "夜雾 27 次 / 焦琴 15 次 / 灯笼光 15 次"主题词锁死
    phrase_density_violations: list[Violation] = []
    try:
        phrase_density_violations = _check_phrase_density(
            conn, sim.id, narrative_segment, critical_threshold=12,
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"phrase density check failed sim={sim.id}: {e}")

    # P0V.4(2026-05-24)— 对白主题词反复检测(零 LLM 成本)
    # 治雪国 14345 浜松男人"跟我回浜松"在 6 条不同对白中反复说,
    # P0Q.1 完整对白 containment 不到 0.7 漏。子串级 ≥ 5 条对白 → critical
    dialogue_phrase_violations: list[Violation] = []
    try:
        dialogue_phrase_violations = _check_dialogue_phrase_repetition(
            conn, sim.id, narrative_segment, critical_threshold=5,
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"dialogue phrase repetition check failed sim={sim.id}: {e}")

    # P0W.1(2026-05-24)— 受限角色物理 / 语言能力违规事后检测(零 LLM 成本)
    # 治雪国 15070 师傅"半身不遂"仍拄杖步行 / 蹲下 / 站直 / 说 4 完整分句
    # P0V.1 prompt 注入了铁律但 LLM 仍违反,这里事后扫描兜底
    physical_constraint_violations: list[Violation] = []
    try:
        physical_constraint_violations = _check_physical_constraints_violation(
            conn, sim, narrative_segment,
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"physical constraint check failed sim={sim.id}: {e}")

    # M10.A(2026-05-27)— 文风滤镜:实测 vs P3 author_compass 目标偏差
    style_drift_violations: list[Violation] = []
    try:
        style_drift_violations = _check_style_drift(
            conn, sim.project_id, narrative_segment,
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"style drift check failed sim={sim.id}: {e}")

    # M10.B(2026-05-27)— 意象库:雷区命中(critical) / 推荐意象 0 命中(warning)
    imagery_violations: list[Violation] = []
    try:
        imagery_violations = _check_imagery_violations(
            conn, sim.project_id, narrative_segment,
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"imagery violation check failed sim={sim.id}: {e}")

    # P5.1(2026-05-27)— outline 执行率检测(治"剧情空心化")
    # 实测挪威森林 28 幕续作覆盖率仅 57%,LLM 用"看星星/吃面"替换"通话/问直子/崩溃大哭"
    outline_execution_violations: list[Violation] = []
    try:
        outline_execution_violations = _check_outline_execution(
            conn, sim.id, scene_index, narrative_segment,
        )
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning(f"outline execution check failed sim={sim.id}: {e}")

    # 拉本 sim 当前所有生效事实 + 主线
    from app.services.world_state_extractor import list_active_facts
    from app.services.plot_tracker import list_active_threads
    active_facts = list_active_facts(conn, sim.id)
    active_threads = list_active_threads(conn, sim.id)

    # Sprint 6.A2 M5(2026-05-20)— 拉 canonical entities + atomic actions + 上幕情绪
    # 2026-06-02:批次 2 清理 — 加 log 防 silent failure
    try:
        from app.services.entity_registrar import list_canonical_entities
        canonical_entities = list_canonical_entities(conn, sim.id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"check_segment: list_canonical_entities failed sim={sim.id} "
            f"scene={scene_index} → 降级返空: {e}"
        )
        canonical_entities = []
    try:
        from app.services.action_extractor import list_atomic_actions
        atomic_actions = list_atomic_actions(conn, sim.id)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"check_segment: list_atomic_actions failed sim={sim.id} "
            f"scene={scene_index} → 降级返空: {e}"
        )
        atomic_actions = []
    try:
        from app.services.emotional_state_tracker import (
            list_latest_emotional_states_for_scene,
        )
        prev_emotions = list_latest_emotional_states_for_scene(
            conn, sim.id, scene_index, agents,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"check_segment: list_latest_emotional_states_for_scene failed sim={sim.id} "
            f"scene={scene_index} → 降级返空: {e}"
        )
        prev_emotions = {}

    # 序列化角色 baseline(优先用 behavior_baseline,空时 fallback 用 personality + no_go_list)
    agent_baselines = []
    for a in agents:
        if a.behavior_baseline and isinstance(a.behavior_baseline, dict):
            # 显式 baseline:用户精细配置过
            agent_baselines.append({
                "name": a.name,
                "is_protagonist": a.is_protagonist,
                "baseline": a.behavior_baseline,
                "no_go_list": a.no_go_list,
            })
        else:
            # fallback:用 personality + no_go_list(老角色兼容)
            agent_baselines.append({
                "name": a.name,
                "is_protagonist": a.is_protagonist,
                "baseline_fallback": {
                    "personality": a.personality,
                },
                "no_go_list": a.no_go_list,
            })

    # 上幕情绪 → 序列化给 LLM(看连续性)
    prev_emotions_lines = []
    for a in agents:
        st = prev_emotions.get(a.id)
        if st is not None:
            prev_emotions_lines.append(st.to_prompt_line(character_name=a.name))

    user_input = {
        "scene_index": scene_index,
        "narrative_segment": narrative_segment,
        "active_world_facts": [f.to_prompt_line() for f in active_facts],
        "active_plot_threads": [t.to_prompt_line() for t in active_threads],
        "agent_baselines": agent_baselines,
        # M5 新增上下文
        "canonical_entities": [e.to_prompt_line() for e in canonical_entities],
        "atomic_actions_so_far": [a.to_prompt_line() for a in atomic_actions[-15:]],
        "previous_emotional_states": prev_emotions_lines,
    }

    system_prompt = _load_prompt("m4_consistency_checker.md")
    try:
        parsed, usage = call_llm_json(
            system_prompt, user_input,
            max_tokens=1500, temperature=0.2,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            f"consistency_checker LLM failed sim={sim.id} scene={scene_index}: {e}"
        )
        # FOCUS.2 + P0Q.1 + P0T.1 + P0V.3/4 + P0W.1 + M10:LLM 失败也返程序检测结果
        return CheckResult(
            violations=(
                pov_violations
                + dialogue_repetition_violations
                + narrative_repetition_violations
                + phrase_density_violations
                + dialogue_phrase_violations
                + physical_constraint_violations
                + style_drift_violations
                + imagery_violations
                + outline_execution_violations
            ),
        ), {"input_tokens": 0, "output_tokens": 0}

    if not isinstance(parsed, dict):
        return CheckResult(
            violations=(
                pov_violations
                + dialogue_repetition_violations
                + narrative_repetition_violations
                + phrase_density_violations
                + dialogue_phrase_violations
                + physical_constraint_violations
                + style_drift_violations
                + imagery_violations
                + outline_execution_violations
            ),
        ), usage

    raw_violations = parsed.get("violations") or []
    if not isinstance(raw_violations, list):
        return CheckResult(
            violations=(
                pov_violations
                + dialogue_repetition_violations
                + narrative_repetition_violations
                + phrase_density_violations
                + dialogue_phrase_violations
                + physical_constraint_violations
                + style_drift_violations
                + imagery_violations
                + outline_execution_violations
            ),
        ), usage

    violations: list[Violation] = []
    for v in raw_violations:
        if not isinstance(v, dict):
            continue
        severity = str(v.get("severity") or "").lower()
        if severity not in ("critical", "warning", "info"):
            continue
        category = str(v.get("category") or "").lower()
        if category not in (
            "world_fact", "plot_thread", "character_baseline",
            # M5 新增 4 类
            "identity_invented", "action_repeated",
            "temporal_regression", "emotional_discontinuity",
            # FOCUS.2 加(但 LLM 一般不会输出此类,因为人称由程序检测;
            # 兼容性留位,防 LLM 偶发自创)
            "narrative_pov_drift",
            # P0Q.1 / P0T.1 加(同样:程序检测为主,LLM 偶发兼容)
            "dialogue_repetition",
            "narrative_repetition",
            # P0V.3 / P0V.4 加(纯程序检测,LLM 偶发兼容)
            "phrase_density",
            "dialogue_phrase_repetition",
            # P0W.1 加(纯程序检测)
            "physical_constraint_violation",
            # M10.A / M10.B 加(纯程序检测)
            "style_drift",
            "imagery_violation",
            # P5.1 加(纯程序检测)
            "outline_execution",
        ):
            continue
        evidence = str(v.get("evidence") or "").strip()[:300]
        suggestion = str(v.get("suggestion") or "").strip()[:300]
        if not evidence:
            continue
        violations.append(Violation(
            severity=severity,  # type: ignore[arg-type]
            category=category,  # type: ignore[arg-type]
            evidence=evidence,
            suggestion=suggestion,
            subject_name=str(v.get("subject_name") or "").strip()[:50],
        ))

    # Sprint 6.A2 FOCUS.2(2026-05-21):把 pov 漂移违规合并进最终结果
    # pov_violations 在函数入口就跑过了,这里只是合并(不影响 LLM usage)
    violations.extend(pov_violations)
    # P0Q.1(2026-05-24):对白复读违规合并(同样函数入口已跑,不影响 LLM usage)
    violations.extend(dialogue_repetition_violations)
    # P0T.1(2026-05-24):叙述句复读违规合并
    violations.extend(narrative_repetition_violations)
    # P0V.3(2026-05-24):意象主题词刷屏违规合并
    violations.extend(phrase_density_violations)
    # P0V.4(2026-05-24):对白主题词反复违规合并
    violations.extend(dialogue_phrase_violations)
    # P0W.1(2026-05-24):受限角色物理 / 语言能力违规合并
    violations.extend(physical_constraint_violations)
    # M10.A / M10.B(2026-05-27):文风滤镜 + 意象库违规合并
    violations.extend(style_drift_violations)
    violations.extend(imagery_violations)
    # P5.1(2026-05-27):outline 执行率违规合并
    violations.extend(outline_execution_violations)

    return CheckResult(violations=violations), usage
