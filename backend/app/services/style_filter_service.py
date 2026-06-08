"""style_filter_service — M10.A 文风滤镜(2026-05-27).

P3 作者指南针 Agent 产出"目标画像"(internal_metrics):
  - 句长分布(短/中/长占比)
  - 对白率(引号字符占比)
  - 感官比例(视/听/嗅/触/味)
  - 段落节奏(平均段长字数)
  - 意象偏好 / 视角 / 基调

P3 当前注入到 prompt 是**软提示**(LLM 看见但不一定执行)。
M10.A 做**事后实测**:扫产物计算实际指标,与画像目标对比,偏差超阈值 → critical violation。

设计原则:
  - **纯字符串统计,0 LLM 成本**
  - **同 P3 同口径**:用 P3 prompt 里的同一套句长定义(短 ≤15 / 中 16-40 / 长 >40)
                    用 P3 prompt 里的同一套感官关键词桶
  - **配 author_compass 数据消费**:输出形式直接和 internal_metrics 字段对齐

普适性:任何作品都适用 — 文风指标与作品类型 / 节奏档位正交。

created 2026-05-27 / M10.A.1
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# 句长定义(同 P3 prompts/author_compass_internal.md)
# ----------------------------------------------------------------------
SHORT_MAX = 15
MID_MAX = 40
# > 40 = 长句

# ----------------------------------------------------------------------
# 感官关键词桶(同 P3 prompts/author_compass_internal.md)
# 注:实际频次估算用关键词出现次数;不细分语境(口语 / 字面 / 比喻)
# 这是粗放估算,与 P3 的 LLM 反推结果误差范围内即合规。
# ----------------------------------------------------------------------
_SENSE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "视觉": (
        "看", "望", "凝视", "瞧", "瞥", "瞄", "盯", "盼", "目光", "眼神",
        "光", "色", "亮", "暗", "白", "黑", "红", "黄", "绿", "蓝", "灰",
        "闪", "映", "照", "投", "幕", "影", "晃",
    ),
    "听觉": (
        "听", "闻声", "声", "响", "鸣", "啼", "唱", "喊", "叫", "吼",
        "嘶", "嘶哑", "嗡", "嗒", "啪", "咔", "嘀", "答", "嘎", "嘶嘶",
        "回荡", "回响", "音", "话", "语", "嗓",
    ),
    "嗅觉": (
        "闻", "嗅", "香", "味儿", "气味", "腥", "膻", "臭",
        "焦糊", "焦味", "酒香", "茶香", "墨香", "花香", "潮气", "霉味",
    ),
    "触觉": (
        "摸", "摸索", "抚", "拍", "捏", "握", "攥", "搂", "抱", "拥",
        "凉", "冷", "暖", "热", "烫", "软", "硬", "滑", "粗", "糙", "毛",
        "刺", "麻", "颤", "震", "抖",
    ),
    "味觉": (
        "尝", "嚼", "吞", "咽", "饮", "啜", "啖",
        "咸", "甜", "苦", "酸", "辣", "涩", "鲜", "腻",
        "酒", "茶", "粥", "饭", "汤",
    ),
}


# ----------------------------------------------------------------------
# 句长统计
# ----------------------------------------------------------------------

def _split_sentences(text: str) -> list[str]:
    """按中文句末标点 + 换行切句,过滤空段。"""
    raw = re.split(r"[。!?\n]+", text)
    return [s.strip() for s in raw if s.strip()]


def compute_sentence_length_distribution(text: str) -> dict[str, float]:
    """实测句长分布.

    Returns:
      {"短句占比": float, "中句占比": float, "长句占比": float}
      数据不足(< 3 句)→ 三个值全 0.0
    """
    sentences = _split_sentences(text)
    if len(sentences) < 3:
        return {"短句占比": 0.0, "中句占比": 0.0, "长句占比": 0.0}
    n_short = sum(1 for s in sentences if len(s) <= SHORT_MAX)
    n_mid = sum(1 for s in sentences if SHORT_MAX < len(s) <= MID_MAX)
    n_long = sum(1 for s in sentences if len(s) > MID_MAX)
    total = len(sentences)
    return {
        "短句占比": round(n_short / total, 3),
        "中句占比": round(n_mid / total, 3),
        "长句占比": round(n_long / total, 3),
    }


# ----------------------------------------------------------------------
# 对白率统计
# ----------------------------------------------------------------------

_DIALOGUE_RE = re.compile(r'[""""「『][^""""」』\n]*[""""」』]')


def compute_dialogue_ratio(text: str) -> float:
    """实测引号包围对白字数 / 总字数.

    Returns: float ∈ [0, 1],总字数 < 50 → 0.0(数据不足)
    """
    if not text:
        return 0.0
    total = len(text)
    if total < 50:
        return 0.0
    dialogue_chars = sum(len(m.group(0)) for m in _DIALOGUE_RE.finditer(text))
    return round(dialogue_chars / total, 3)


# ----------------------------------------------------------------------
# 感官比例统计
# ----------------------------------------------------------------------

def compute_sense_ratio(text: str) -> dict[str, float]:
    """实测 5 大感官关键词频次占比.

    Returns:
      {"视觉": 0.4, "听觉": 0.2, "嗅觉": 0.15, "触觉": 0.2, "味觉": 0.05}
      若 5 类总命中 = 0 → 5 个值全 0.0(数据不足)
    """
    if not text:
        return {k: 0.0 for k in _SENSE_KEYWORDS}
    counts = {k: 0 for k in _SENSE_KEYWORDS}
    for sense, keywords in _SENSE_KEYWORDS.items():
        for kw in keywords:
            counts[sense] += text.count(kw)
    total = sum(counts.values())
    if total == 0:
        return {k: 0.0 for k in _SENSE_KEYWORDS}
    return {k: round(v / total, 3) for k, v in counts.items()}


def top_sense(sense_ratio: dict[str, float]) -> str:
    """从感官比例字典中取占比最高的那个感官名。空 / 全 0 → 空串。"""
    if not sense_ratio:
        return ""
    max_v = max(sense_ratio.values())
    if max_v == 0:
        return ""
    for k, v in sense_ratio.items():
        if v == max_v:
            return k
    return ""


# ----------------------------------------------------------------------
# 段落节奏统计
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# P5.3(2026-05-27)— 身体描写关键词测量(治"含蓄原作回避得连含蓄都没了")
# ----------------------------------------------------------------------
#
# 起源:挪威森林 28 幕续作第 23 幕 "两人第一次真正地做爱" 被直接跳到第二天早餐。
# P4 给的"身体描写尺度"画像是软提示,LLM 不当回事。M10.B 加事后审计:
# 实测产物的身体描写关键词频率 vs 画像目标频率(rare / occasional / frequent),
# 跨档错位 → critical → retry。
#
# 关键词分层:
#   - light:  接触类(碰 / 触 / 牵手 / 拥抱 / 倚 / 靠)— 任何作品都允许
#   - moderate: 亲密类(吻 / 抚 / 怀里 / 肌肤 / 衣 / 床)
#   - explicit: 性描写类(做爱 / 缠绕 / 赤身 / 裸 / 喘息 / 呻吟 / 高潮)
_BODY_KEYWORDS_LIGHT: tuple[str, ...] = (
    "碰", "触", "牵手", "拥抱", "拥住", "倚靠", "依偎",
    "搂", "抱住", "贴着",
)
_BODY_KEYWORDS_MODERATE: tuple[str, ...] = (
    "亲吻", "吻", "亲", "抚摸", "抚", "怀里", "怀中",
    "肌肤", "脖颈", "脖子", "锁骨", "腰", "胸口",
    "衬衫", "裙子", "解开", "脱下", "床上", "床边", "枕头", "被子",
    "心跳", "颤抖", "微颤", "战栗", "贴近",
)
_BODY_KEYWORDS_EXPLICIT: tuple[str, ...] = (
    "做爱", "缠绕", "赤裸", "赤身", "裸体", "光着",
    "喘息", "气息", "呻吟", "低吟", "颤栗", "高潮",
    "纠缠", "交缠", "进入", "深入", "蜷在", "躯体",
    "汗", "湿润", "湿热",
)


def compute_body_register_frequency(text: str) -> str:
    """实测身体描写出现频率档位.

    输出:"none" / "rare" / "occasional" / "frequent"(对齐 P4 author_compass 4 档)

    判定算法(密度而非绝对次数,避免长产物天然偏高):
      - 算 explicit + moderate 关键词的命中次数
      - 按 (命中次数 / 1000 字) 算密度
      - 0 命中 → none
      - density < 0.5 → rare(0-1 处 / 千字)
      - density < 2.0 → occasional(1-4 处 / 千字)
      - density ≥ 2.0 → frequent(每 500 字 ≥ 1 处)
    """
    if not text or len(text) < 200:
        return "none"
    explicit_hits = sum(text.count(kw) for kw in _BODY_KEYWORDS_EXPLICIT)
    moderate_hits = sum(text.count(kw) for kw in _BODY_KEYWORDS_MODERATE)
    total_hits = explicit_hits + moderate_hits
    if total_hits == 0:
        return "none"
    density_per_1k = total_hits / (len(text) / 1000.0)
    if density_per_1k < 0.5:
        return "rare"
    if density_per_1k < 2.0:
        return "occasional"
    return "frequent"


def compute_body_register_explicitness(text: str) -> str:
    """实测身体描写直白度档位.

    输出:"absent" / "metaphorical-implicit" / "direct-detailed"
      (简化版,对齐 P4 6 档中的核心 3 档)

    判定:
      - explicit 类关键词 0 命中 → absent / metaphorical-implicit
      - explicit 命中 ≥ 2 → direct-detailed
      - 1 ≤ explicit < 2 + moderate ≥ 3 → direct-detailed
      - 其他 → metaphorical-implicit
    """
    if not text:
        return "absent"
    explicit_hits = sum(text.count(kw) for kw in _BODY_KEYWORDS_EXPLICIT)
    moderate_hits = sum(text.count(kw) for kw in _BODY_KEYWORDS_MODERATE)
    if explicit_hits == 0 and moderate_hits == 0:
        return "absent"
    if explicit_hits >= 2:
        return "direct-detailed"
    if explicit_hits >= 1 and moderate_hits >= 3:
        return "direct-detailed"
    return "metaphorical-implicit"


# 频率档位顺序(用于"跨档错位"判断)
_FREQUENCY_ORDER: tuple[str, ...] = ("none", "rare", "occasional", "frequent")


def frequency_drift_distance(target: str, measured: str) -> int:
    """target 与 measured 频率档位的距离(绝对差).

    例:
      target="frequent" + measured="none" → 距离 3(差 3 档,最严重)
      target="rare" + measured="rare" → 0(对齐)
      target="occasional" + measured="rare" → 1(相邻档,可容忍)
    """
    if target not in _FREQUENCY_ORDER or measured not in _FREQUENCY_ORDER:
        return 0
    return abs(_FREQUENCY_ORDER.index(target) - _FREQUENCY_ORDER.index(measured))


def compute_paragraph_avg_length(text: str) -> int:
    """实测段落平均字数(段落以 \\n\\n 分隔)。

    Returns: int ≥ 0;无段落或全空 → 0
    """
    if not text:
        return 0
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return 0
    total_chars = sum(len(p) for p in paragraphs)
    return round(total_chars / len(paragraphs))


# ----------------------------------------------------------------------
# 主入口 — 完整测量
# ----------------------------------------------------------------------

def measure_style_metrics(text: str) -> dict[str, Any]:
    """实测 narrative 全套文风指标,输出与 P3 internal_metrics 字段对齐.

    Returns:
      {
        "句长": {"短句占比": ..., "中句占比": ..., "长句占比": ...},
        "对白率": {"比例": ...},
        "感官比例": {"视觉": ..., "听觉": ..., ...},
        "段落节奏": {"平均段长字数": ...},
        "_meta": {"total_chars": ..., "sentence_count": ..., "paragraph_count": ...},
      }
    """
    sentences = _split_sentences(text)
    paragraphs = [p.strip() for p in (text or "").split("\n\n") if p.strip()]
    return {
        "句长": compute_sentence_length_distribution(text),
        "对白率": {"比例": compute_dialogue_ratio(text)},
        "感官比例": compute_sense_ratio(text),
        "段落节奏": {"平均段长字数": compute_paragraph_avg_length(text)},
        "_meta": {
            "total_chars": len(text or ""),
            "sentence_count": len(sentences),
            "paragraph_count": len(paragraphs),
        },
    }


# ----------------------------------------------------------------------
# 偏差对比
# ----------------------------------------------------------------------

def compute_drift(measured: float, target: float, tolerance: float = 0.15) -> tuple[bool, float]:
    """绝对差超过 tolerance → drift=True.

    例:target=0.55, measured=0.30 → diff=0.25 > 0.15 → drift=True
        target=0.55, measured=0.45 → diff=0.10 → drift=False

    Returns: (is_drift, absolute_diff)
    """
    diff = abs(measured - target)
    return diff > tolerance, round(diff, 3)


__all__ = [
    "SHORT_MAX",
    "MID_MAX",
    "compute_sentence_length_distribution",
    "compute_dialogue_ratio",
    "compute_sense_ratio",
    "top_sense",
    "compute_paragraph_avg_length",
    "measure_style_metrics",
    "compute_drift",
    # P5.3(2026-05-27)— 身体描写测量
    "compute_body_register_frequency",
    "compute_body_register_explicitness",
    "frequency_drift_distance",
]
