"""LLM 抽取核心 — Sprint 2.B 把 scripts/build_graph.py + generate_characters.py 的纯 LLM 逻辑抽出。

设计:
  - 不做 sys.exit / 文件 I/O / argparse,只暴露纯函数
  - 复用 app.services.llm_client.call_llm_json 走统一 LLM 入口
  - 三个主函数:
      extract_graph(text, work_name, work_type)          → entities + relations
      generate_character_profile(char, source_text, ...) → 单角色完整档案
      infer_meta(work_name, sample_text)                  → type + custom_type_name + tags

  - 长文本走 extract_graph_chunked,支持断点续抽:
      持久化每块结果(extract_chunk_results 表)
      重启时跳过已完成块,只跑剩余的
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Callable, Optional

from app.services.llm_client import call_llm_json

# P0I.5(2026-05-24)— module-level logger 定义
# 修 P0I.4 用 `logger.warning(...)` 时遇到 NameError 抽取崩溃的根因
# 之前 L897 / L1255 处用 `import logging; logging.warning(...)` 的方式可用但不一致
# 现在统一用 module-level logger
logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "prompts"


# ============================================================
# Sprint 3.A polish — 关系强度 5 级 enum 白名单
# ============================================================

# build_graph prompt v2 起,每条 relation 必填 strength ∈ 此白名单。
# LLM 输出异常值(拼写错 / 没填 / 多余字段)时,服务层兜底为 "moderate"(中性)。
# 5 级 score 映射在前端 transformBackendGraph 处理,backend 只存 enum 字符串。
VALID_RELATIONSHIP_STRENGTHS = (
    "strong",
    "moderately_strong",
    "moderate",
    "moderately_weak",
    "weak",
)
DEFAULT_RELATIONSHIP_STRENGTH = "moderate"


def _normalize_strength(raw: Any) -> str:
    """把 LLM 输出的 strength 字段归一化到白名单值。

    兜底策略:
      - 非 str / 空 / 不在白名单 → "moderate"(中性,不被默认阈值过滤掉)
      - 大小写敏感(LLM prompt 已明确小写下划线 enum),不做大小写转换
    """
    if not isinstance(raw, str):
        return DEFAULT_RELATIONSHIP_STRENGTH
    if raw not in VALID_RELATIONSHIP_STRENGTHS:
        return DEFAULT_RELATIONSHIP_STRENGTH
    return raw


# Sprint 6.A2 M7.G(2026-05-20)关系类型自由化兜底:
# LLM 抽取允许输出任意 type(不限于 build_graph prompt 列的预设),后端只做长度兜底
# 让 DB CHECK(length BETWEEN 1 AND 20)放行。空 / 过长 / 非 str → "其他" 兜底
# 不再硬性拒绝 LLM 输出的关系名(例如"主仆""位于""参与""提及""暗恋"等)
DEFAULT_RELATIONSHIP_TYPE = "其他"


def _normalize_type(raw: Any) -> str:
    """归一化 LLM 输出的 type 字段。

    兜底策略(M7.G 后):
      - 非 str / 空 / trim 后为空 → "其他"
      - trim 后超过 20 字 → 截断到 20 字(保留 LLM 自创的关系名;丢弃无意义长串)
      - 1-20 字范围内 → 原样保留(包括自创关系如"青梅竹马""忘年交""暗中保护"等)
    """
    if not isinstance(raw, str):
        return DEFAULT_RELATIONSHIP_TYPE
    trimmed = raw.strip()
    if not trimmed:
        return DEFAULT_RELATIONSHIP_TYPE
    # 截断到 20 字(对齐 DB CHECK 上限)
    if len(trimmed) > 20:
        return trimmed[:20]
    return trimmed


# 强度排序(越靠后越强)— 合并多块抽取时若同一关系多块都抽到,取最强
_STRENGTH_RANK = {
    "weak": 0,
    "moderately_weak": 1,
    "moderate": 2,
    "moderately_strong": 3,
    "strong": 4,
}


def _stronger_of(a: str, b: str) -> str:
    """取两个 strength 中更强的(用于合并去重时挑保留值)。"""
    return a if _STRENGTH_RANK.get(a, 2) >= _STRENGTH_RANK.get(b, 2) else b


# ============================================================
# 工具
# ============================================================

def _load_prompt(filename: str) -> str:
    return (PROMPTS_DIR / filename).read_text(encoding="utf-8")


def _safe_format(template: str, **kwargs) -> str:
    """与 simulation_service 同样的 {{}} 转义兜底,避免 LLM JSON 示例里的双大括号
    被 .format 误吃。
    """
    placeholders = list(kwargs.keys())
    out = template
    for k in placeholders:
        out = out.replace("{" + k + "}", str(kwargs[k]))
    out = out.replace("{{", "{").replace("}}", "}")
    return out


def compute_chunk_hash(chunk_text: str) -> str:
    """计算 chunk 文本的短哈希(sha256 前 16 字符)。

    用途:断点续抽前校验文本未变。重启后用相同切片算法重新切,逐块对比 hash:
      - 一致 → 该 chunk 已抽过,跳过
      - 不一致(用户中途换了文件)→ fallback 到全文重抽

    16 字符 = 64 bit,碰撞概率 < 2^-32 对单文档级足够。
    """
    return hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()[:16]


def find_character_excerpts(
    source_text: str,
    name: str,
    aliases: list[str],
    max_chars: int = 8000,
    context_window: int = 400,
) -> str:
    """在原文中搜索 name + aliases,每命中取前后 context_window 字符,
    去重 + 拼接,总长截到 max_chars(自 scripts/generate_characters.py:122)。
    """
    keywords = [name] + (aliases or [])
    hits: list[tuple[int, int]] = []
    for kw in keywords:
        if not kw:
            continue
        start = 0
        while True:
            idx = source_text.find(kw, start)
            if idx < 0:
                break
            seg_start = max(0, idx - context_window)
            seg_end = min(len(source_text), idx + len(kw) + context_window)
            hits.append((seg_start, seg_end))
            start = idx + len(kw)

    if not hits:
        return ""

    hits.sort()
    merged: list[list[int]] = []
    for s, e in hits:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    parts: list[str] = []
    total = 0
    for s, e in merged:
        chunk = source_text[s:e]
        if total + len(chunk) > max_chars:
            chunk = chunk[: max_chars - total]
            parts.append(chunk)
            break
        parts.append(chunk)
        total += len(chunk)
    return "\n\n[…]\n\n".join(parts)


def sample_text_for_meta(
    text: str, head_chars: int = 1500, mid_chars: int = 1500, tail_chars: int = 1500
) -> str:
    """开头 + 中段 + 结尾抽样(infer_meta 用,避免全文喂 LLM)。"""
    n = len(text)
    if n <= head_chars + mid_chars + tail_chars:
        return text
    head = text[:head_chars]
    mid_start = max(head_chars, (n - mid_chars) // 2)
    mid = text[mid_start : mid_start + mid_chars]
    tail = text[-tail_chars:]
    return f"{head}\n\n[…中段…]\n\n{mid}\n\n[…结尾…]\n\n{tail}"


# ============================================================
# 1. 抽 entities + relations(全文单次调用)
# ============================================================

# ## token 预算说明(2.B 修)
#
# DeepSeek V3 上下文 64K token,中文 ~1.5 token/字。
# - prompt(build_graph.md)约 1500 token
# - 25K 字 chunk(DEFAULT_CHUNK_CHARS)≈ 38K input token
# - 留 ~24K token 给输出(≤ 64K total)
#
# 旧 max_tokens=4000 严重不足:25K 字章节抽出 30-80 实体 + 50-150 关系 + 描述,
# 输出 JSON 经常超 4000 token 被截断(line 642 col 17 的 JSON 解析失败痕迹)。
# 截断 → JSON parse fail → retry 3 次 → 单 chunk 耗 3 分钟 → 用户看似"卡死"。
#
# 新口径:
#   max_tokens = 8000(给输出 JSON 留 5300 中文字空间,够 80 实体 + 150 关系)
#   timeout    = 120s(对齐 call_llm_text 长输出场景)

EXTRACT_GRAPH_MAX_TOKENS = 8000
EXTRACT_GRAPH_TIMEOUT = 120.0


def extract_graph(
    text: str, work_name: str = "", work_type: str = "小说"
) -> tuple[dict, dict]:
    """单次 LLM 调用抽 entities + relations。**适合 ≤ 25K 字文本**。

    超长文本(整本小说)请用 extract_graph_chunked,会按字数切块 + 合并去重。

    Args:
      text: 作品文本片段
      work_name: 作品名(给元提示用,可空)
      work_type: 类型词(如"小说" / "漫画分镜" / "剧本")

    Returns:
      (parsed_dict, usage_dict)
        parsed_dict: {"entities": [...], "relations": [...]}
        usage_dict: {"input_tokens": int, "output_tokens": int}
    """
    system = _load_prompt("build_graph.md")
    work_meta_hint = f"作品名:{work_name}" if work_name else ""
    # Sprint 3.A polish 4:关系密度提醒 — build_graph v3 加 6 条密度铁律,但
    # 长 chunk 的 system prompt 容易被 LLM 注意力衰减,user 区再强调一次
    # (LLM 顺序读取,user 区在文本前最后看到 → 抽取时记得更牢)
    density_reminder = (
        "**关键提醒(v3 关系密度铁律)**:抽完所有 entities 后,先识别主角"
        "(第一人称叙述者 / 出场频率最高者),然后强制审查 — 主角与每个 PERSON 是否都有"
        "至少一条关系?共同场景出现的 PERSON 对是否都有关系?**关系数应 ≥ PERSON 数 × 1.5**,"
        "若不达,**回头补**「同事」/「提及」中弱关系,**不要让用户看到孤岛角色**。"
    )
    user_input = (
        f"请从下面这段{work_type}正文中抽取实体与关系。\n\n"
        f"{work_meta_hint}\n\n"
        f"{density_reminder}\n\n"
        f"正文:\n\"\"\"\n{text}\n\"\"\"\n\n只输出 JSON。"
    )
    parsed, usage = call_llm_json(
        system, user_input,
        max_tokens=EXTRACT_GRAPH_MAX_TOKENS,
        timeout=EXTRACT_GRAPH_TIMEOUT,
        retries=2,
    )
    if not isinstance(parsed, dict):
        raise ValueError(f"build_graph LLM 输出顶层不是 dict:{type(parsed).__name__}")
    return parsed, usage


# ============================================================
# 1.5 长文本分块抽取(80W 字红楼梦 case 必走这条)+ 断点续抽
# ============================================================

# DeepSeek V3 上下文 64K token,中文 ~1.5 token/字。
# prompt 约 1500 token + max_tokens 8000(给输出)→ 用户输入控在 50K token ≈ 33K 字。
# 留 buffer 取 25K 字一块。
DEFAULT_CHUNK_CHARS = 25_000


def split_text_into_chunks(
    text: str, chunk_chars: int = DEFAULT_CHUNK_CHARS
) -> list[str]:
    """按字数切块,优先在段落边界(连续两个换行)切,保留上下文连贯性。

    简单 v1:
    - text 长度 ≤ chunk_chars → 整段返(1 块)
    - 否则贪心切:每块 chunk_chars 字,在最近的 \\n\\n 处切;找不到就硬切
    - 不做滑动窗口重叠(v2 优化点)

    **必须是 deterministic 的** — 同样的 text + chunk_chars 必产同样的块序列。
    断点续抽依赖这个性质对齐 db 中已存的 chunk_index。
    """
    n = len(text)
    if n <= chunk_chars:
        return [text]

    chunks: list[str] = []
    cursor = 0
    while cursor < n:
        end_target = min(cursor + chunk_chars, n)
        if end_target >= n:
            chunks.append(text[cursor:n])
            break
        # 在 [cursor + chunk_chars * 0.7, end_target] 之间找最后的 \n\n 切,保段落完整
        search_start = cursor + int(chunk_chars * 0.7)
        cut = text.rfind("\n\n", search_start, end_target)
        if cut == -1:
            # 找不到段落边界 → 退而找最近的 \n,再退而硬切
            cut = text.rfind("\n", search_start, end_target)
            if cut == -1:
                cut = end_target
        chunks.append(text[cursor:cut].strip())
        cursor = cut
    # 过滤空段(段落边界搜索失败时可能产生)
    return [c for c in chunks if c]


def detect_missed_persons(
    merged_entities: dict[str, dict],
    full_text: str,
    min_text_occurrences: int = 3,
    min_description_mentions: int = 2,
) -> list[str]:
    """Sprint 6.A2 FOCUS.9(2026-05-22):漏抽人名检测(治"直子失踪"型 bug)。

    策略:从已确认 PERSON 的 description 里**反向扫描**人名候选 — 如果某个 2-4 字
    序列在多个 description 里被提到 + 在原文出现 ≥ 3 次 + 又不在 PERSON name/aliases
    集合里,就是漏抽嫌疑。

    保守:**只返回 candidate 列表,不自动补抽**(避免假名字污染)。调用方决定是否:
      - log warning(运维 audit)
      - SSE 推给前端做"⚠ 漏抽提示"卡片
      - 触发 LLM 二次补抽

    why 不直接补 entity:中文姓名提取没空格,纯启发式有 ~10-20% 假阳性,
    自动入库可能引入 "故事 / 主人公 / 命运" 等被误识别的"伪人名"。
    """
    # 1. 已确认的 PERSON name + alias 大集合(用于排除)
    known_names: set[str] = set()
    for ent in merged_entities.values():
        if not isinstance(ent, dict):
            continue
        if (ent.get("type") or "PERSON") != "PERSON":
            continue
        name = ent.get("name")
        if name:
            known_names.add(name)
        for a in ent.get("aliases", []) or []:
            if a:
                known_names.add(a)

    if not known_names or not full_text:
        return []

    # 2. 收集所有 PERSON description 文本
    descriptions: list[str] = []
    for ent in merged_entities.values():
        if not isinstance(ent, dict):
            continue
        if (ent.get("type") or "PERSON") != "PERSON":
            continue
        desc = ent.get("description", "")
        if desc:
            descriptions.append(desc)

    if not descriptions:
        return []

    # 3. 候选挖掘:从 description 找 2-4 字的中文人名 candidate
    # 规则:出现在 "<X>的" / "<X>是" / "<X>和" / "<X>与" / "与<X>" / "和<X>" 之类结构里
    # 中文人名:[一-鿿]{2,4}(2-4 字汉字)
    # ⚠ 必须**非贪婪**(`{2,4}?`)+ 前缀边界检查 — 否则贪婪会把"与直子"3 字串当作名字
    # (前面带连接词"与"被吞进去)。
    import re
    candidates: dict[str, int] = {}   # name → 在多少 description 出现

    # 连接词集合 — 出现在前面会被错吞,提取后必须 strip
    LEADING_CONNECTORS = "和与对跟向遇而至从在到为给被让叫使"

    # P0N.3(2026-05-24)— 简化正则:不再堆动词清单,改用"通用结构性后置字"
    # 设计哲学:候选名后接**任何标点 / 助词 / 通用动词字** 都算合法触发
    # 比"列举所有可能动词"更通用,且 false positive 由 BLACKLIST + 频次过滤兜住
    name_pattern = re.compile(
        r"([一-鿿]{2,4}?)"
        r"(?=[,。;:、\s\"'!?]"              # 标点(name 后跟标点 = 强信号)
        r"|的|是|地|得|了|过|着"             # 中文助词 / 时态词
        r"|和|与|对|向|跟|被|让|给"          # 连接词 / 介词
        r"|说|想|看|去|来|到|走|做|要|有|是" # 高频动词(只留 11 个最常见的)
        r")"
    )
    name_pattern2 = re.compile(
        r"(?:和|与|对|跟|向|遇见|爱上|认识|看见|找|跟随|碰到)"
        r"([一-鿿]{2,4}?)"
        r"(?=[,。;:、\s的是了着])"
    )

    def _strip_leading(s: str) -> str:
        """去掉首字是连接词的前缀(防"与直子"被当人名)。"""
        while s and s[0] in LEADING_CONNECTORS:
            s = s[1:]
        return s

    for desc in descriptions:
        seen_in_this_desc: set[str] = set()
        for m in name_pattern.finditer(desc):
            candidate = _strip_leading(m.group(1))
            if len(candidate) >= 2:
                seen_in_this_desc.add(candidate)
        for m in name_pattern2.finditer(desc):
            candidate = _strip_leading(m.group(1))
            if len(candidate) >= 2:
                seen_in_this_desc.add(candidate)
        for nm in seen_in_this_desc:
            candidates[nm] = candidates.get(nm, 0) + 1

    # 4. 筛选:在多 description 出现 + 原文频次足够 + 不在已知列表
    # P0L.2(2026-05-24)— 加"高频文本兜底"路径:即便 desc_mentions < 阈值,
    # 只要 text_count ≥ HIGH_FREQ_THRESHOLD,就推荐(治雪国岛村漏抽 — 岛村只在 1 个
    # description 里被提到 1 次,但原文里出现 30+ 次)
    HIGH_FREQ_TEXT_THRESHOLD = 20  # 原文出现 ≥ 20 次 = 关键角色信号
    missed: list[str] = []
    for nm, desc_mentions in candidates.items():
        if nm in known_names:
            continue
        # 原文频次校验(过滤"故事 / 主人公"等通用词)
        text_count = full_text.count(nm)
        # 主路径:原文 ≥ 3 次 + description ≥ 2 次
        # 兜底:原文 ≥ 20 次(高频信号) + description ≥ 1 次 — catch 焦点人物
        main_path_ok = (
            desc_mentions >= min_description_mentions
            and text_count >= min_text_occurrences
        )
        fallback_high_freq = (
            text_count >= HIGH_FREQ_TEXT_THRESHOLD
            and desc_mentions >= 1
        )
        if not (main_path_ok or fallback_high_freq):
            continue
        # 简单黑名单过滤(常见误识词)
        if nm in _CN_NAME_BLACKLIST:
            continue
        # P0.E(2026-05-24)兜底 — 排除地名后缀,治"阿美寮被推荐为漏抽角色" bug
        # missed 漏抽推断扫 description 找名字模式,但"阿美寮""小林书店"也满足模式
        # 这里用 P0.C 的地名后缀函数过滤掉,防止推荐用户把地点加成角色
        if _is_likely_location_by_suffix(nm):
            continue
        missed.append(nm)

    # 按 description 提及次数排序(高优先)
    missed.sort(key=lambda n: -candidates.get(n, 0))
    return missed[:10]   # 上限 10 个,避免噪声


# 中文姓名误识黑名单 — 常被 LLM 在 description 里当"主语"的非人名
_CN_NAME_BLACKLIST: set[str] = {
    "故事", "主角", "主人公", "命运", "回忆", "青春", "时代", "世界", "生活",
    "感情", "爱情", "友情", "亲情", "学校", "大学", "中学", "高中", "小学",
    "东京", "京都", "北京", "上海", "城市", "国家", "世界", "未来", "过去",
    "现在", "今天", "明天", "昨天", "早上", "晚上", "下午", "中午",
    "情人", "恋人", "朋友", "兄弟", "姐妹", "同事", "同学", "老师", "学生",
    "父亲", "母亲", "孩子", "儿子", "女儿", "夫妻", "丈夫", "妻子",
    "我们", "你们", "他们", "她们", "大家", "众人",
}


# P0.A / P0N.2(2026-05-24)— 关系称谓后缀字典(精简版)
# 设计哲学:**字典只是兜底**,主路径在 _is_invalid_alias 规则 ①"含的"上(覆盖率 80%)
# 字典只补"无的"的常见关系词("绿子父亲" / "玲子妻子" 这种直接拼后缀的)
# 从原 60+ 砍到 25 个核心 — LLM 实际输出主要是直接亲属类
_RELATION_SUFFIXES: tuple[str, ...] = (
    # 直系亲属 — 最高频
    "父亲", "母亲", "爸爸", "妈妈",
    "姐姐", "妹妹", "哥哥", "弟弟",
    "儿子", "女儿",
    "丈夫", "妻子", "老婆", "老公",
    "恋人", "情人", "男友", "女友",
    # 长辈 — 常见
    "爷爷", "奶奶", "叔叔", "阿姨",
    # 称谓 — 常见
    "先生", "太太", "夫人", "小姐",
)


# P0.C / P0N.2(2026-05-24)— 地名后缀字典(精简版)
# 砍冗余:同义后缀去重(寮+院+馆三类机构不必都列;餐厅/客厅/卧室太具体)
# 留下覆盖最广的 25 个核心后缀 — 罕见地名(陵园/化妆室)LLM 自己能判
_LOCATION_SUFFIXES: tuple[str, ...] = (
    # 机构 / 场所(8)
    "院", "馆", "楼", "店", "栈", "厂", "校", "寺",
    # 区域(8)
    "街", "区", "县", "市", "省", "国", "镇", "村",
    # 交通(2)
    "站", "道",
    # 自然(7)
    "山", "河", "湖", "海", "岛", "湾", "港",
)


def _is_likely_location_by_suffix(name: str) -> bool:
    """判定实体名是否疑似地点(基于后缀)。

    严格规则:
    - 实体名长度 ≥ 2 字(单字易误判:"楼"可能是姓)
    - 末尾命中 _LOCATION_SUFFIXES 中的某后缀
    - 排除明显人名模式(后缀前是"先生 / 太太 / 老板 / 主任 / 馆长 / 老师 / 院长"等头衔)
    - P0L.8(2026-05-24):2 字名 + 模糊后缀(村/田/山/川/野/井/岛/林/原/桥)→ 优先视为人名
      原因:这些字在中文里是地名后缀(村庄/山川),但**在日本姓氏里非常常见**
      (岛村/木村/中村/田村/野口/石川/小林/桥本) — 2 字短名 90% 是人名

    例(返 True):
        "阿美寮" / "小林书店" / "戏剧史教室" / "宇都宫" / "京都" / "上野站"
    例(返 False):
        "周馆长"(虽然带"馆",但后缀其实是"馆长"不是"馆")
        "岛村"(P0L.8:2 字 + "村" 结尾,日本姓 — 不当地名)
        "木村"(同上)
        "楼"(单字,跳过)
        "我"(不带后缀)
    """
    if len(name) < 2:
        return False
    # 头衔后缀白名单 — 这些是人不是地
    _PERSON_TITLES = (
        "馆长", "院长", "校长", "主任", "老板", "老师", "教授", "先生", "太太",
        "夫人", "小姐", "公子", "少爷", "大人", "总裁", "经理",
    )
    for title in _PERSON_TITLES:
        if name.endswith(title):
            return False
    # P0L.8(2026-05-24):2 字短名 + 日中通用模糊后缀 → 优先视为人名
    # 这些字在中文是地名(村/山/川/林/原)在日本姓氏里也极常见
    # 3 字以上时才信任(如"小林书店"3 字仍是 LOCATION,但"小林"2 字是人名)
    _AMBIGUOUS_2CHAR_SUFFIXES = {
        "村", "田", "山", "川", "野", "井", "岛", "林", "原", "桥",
    }
    if len(name) == 2 and name[-1] in _AMBIGUOUS_2CHAR_SUFFIXES:
        return False
    # 尾部匹配地名后缀
    for suffix in _LOCATION_SUFFIXES:
        if name.endswith(suffix):
            return True
    return False


def _correct_entity_types_by_suffix(
    entities: dict[str, dict],
) -> dict[str, dict]:
    """P0.C(2026-05-24)— 后处理矫正 LLM 误标的 entity_type.

    LLM 偶尔把"阿美寮"、"小林书店"这种地点误标为 PERSON。本函数扫一遍 entities,
    若 PERSON 实体名以明显地名后缀结尾 → 强制改 type=LOCATION。

    返回:矫正后的 entities dict(原地不修改,返回新 dict)
    """
    corrected: dict[str, dict] = {}
    for name, ent in entities.items():
        ent_type = ent.get("type") or "PERSON"
        if ent_type == "PERSON" and _is_likely_location_by_suffix(name):
            new_ent = dict(ent)
            new_ent["type"] = "LOCATION"
            # 清空 aliases(地点不应该有"父亲/母亲"类伪别名残留)
            new_ent["aliases"] = []
            corrected[name] = new_ent
        else:
            corrected[name] = ent
    return corrected


def _is_relation_suffix_pair(short_name: str, long_name: str) -> bool:
    """判定 long_name 是否 = short_name + [的](可选) + 关系后缀。

    用于子串合并的排除判定(union-find 阶段)。

    例子(都返 True):
        ("绿子", "绿子父亲")           - 直接拼后缀
        ("绿子", "绿子的母亲")         - 中间夹"的"
        ("玲子", "玲子的丈夫")         - 同上
        ("直子", "直子姐姐")           - 直接拼后缀

    例子(都返 False):
        ("绿子", "小林绿子")           - 全名 / 短称归一,**应该合并**
        ("黛玉", "林黛玉")             - 同上
        ("我", "我们")                 - 不是关系词
    """
    if not long_name.startswith(short_name) or long_name == short_name:
        return False
    remainder = long_name[len(short_name):]
    # P0.D(2026-05-24)治本:任何 "<short>的<remainder>" 形式都是不同实体
    # 这覆盖了字典外的描述性短语,如"绿子的小狗 / 玲子的恋人 / 渡边的旧女友"
    if remainder.startswith("的"):
        return True
    # 兜底:不带"的"中介,但 remainder 是常见关系词("绿子父亲""直子姐姐")
    return remainder in _RELATION_SUFFIXES


def _is_invalid_alias(root_name: str, alias: str) -> bool:
    """P0.D(2026-05-24)— alias 合法性判定(模式识别版,覆盖 LLM 创造的描述性短语).

    返回 True 表示 alias 不指代同一人,应剔除。
    用模式识别替代封闭关系词字典,治"渡边的旧女友 / 高三时的女友 / 第一次睡过的女友"
    这类 LLM 描述性短语漏网 bug。

    剔除规则(三选一即剔除):
      ① alias 包含 "的" 字 — 中文别名极少含"的",含"的"必是关系/所属物描述
         例:"渡边的旧女友" / "高三时的女友" / "和玲子要好的太太"
      ② alias 长度 > 2 且**包含**关系词后缀(在 _RELATION_SUFFIXES 字典)
         但 alias 本身 == 关系词时不剔除(那是合法的"母亲"指代李夫人归一)
         例:"<root>父亲" / "<root>母亲" / "<root>姐姐"
      ③ alias 长度 > 6 字 — 普通别名 1-4 字,超 6 字必是描述性短语
         例:"渡边第一次睡过的女友"(已被规则 ① 剔除)/ "学钢琴的女孩"

    合法别名(都不剔除):
      - "我" / "渡边君" / "渡边彻" — 短称/敬语
      - "小林绿子" / "林黛玉" — 全名归一
      - "驹子" / "纳粹"(敢死队 alias)— 简单别名/绰号
      - "母亲" / "父亲"(单独作为某 PERSON 的 alias)— (c) 关系称谓归一合法

    Args:
      root_name: 该 entity 的主 name
      alias: 待判定的 alias 字符串
    """
    if not alias or alias == root_name:
        return False  # 空 / 跟 root 重复(后续会被去重),不算"无效"

    # 规则 ①:含"的"字 → 关系/所属物描述
    if "的" in alias:
        return True

    # 规则 ②(P0L.7,2026-05-24 改 endswith):长度 > 2 且**以关系词后缀结尾**
    # 原版用 `rel in alias`(子串),会把"叶子姑娘" 误判(含"姑")
    # 改用 endswith 精确匹配(治"<X>父亲/母亲/姐姐/丈夫"等真正的关系后缀)
    if len(alias) > 2:
        for rel in _RELATION_SUFFIXES:
            if rel != alias and alias.endswith(rel):
                return True

    # 规则 ③:长度 > 6 → 描述性短语
    if len(alias) > 6:
        return True

    return False


def _post_merge_alias_dedup(
    merged_entities: dict[str, dict],
    graphs: Optional[list[dict]] = None,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Sprint 6.A2 FOCUS.4(2026-05-22)+ FOCUS.9(2026-05-22)修补:跨 chunk 程序级归一。

    返回 (new_merged_entities, name_remap)。
    name_remap:被合并掉的旧 name → 新 root name 的映射;_merge_graphs 处理 relations 时
    用此表把 relation.source/target 里指向旧 name 的 reassign 到 root,防孤儿关系。

    问题:LLM 在单个 chunk 内能识别"小林绿子=绿子"(v6 prompt 铁律 d 起作用),
    但**多 chunk 各自抽取**时,chunk 1 可能输出 `name="绿子"`,chunk 2 可能输出
    `name="小林绿子"`,_merge_graphs 按严格 name 去重 → 保留两条独立 PERSON。

    FOCUS.9 修补(2026-05-22):**高频角色保护**(治《挪威的森林》"直子失踪")。
    用户实测发现某些核心角色(在 ≥ 2 chunks 独立出现)被错误合并并消失。根因:
    LLM 在某个 chunk 里把"直子"误标为"绿子".aliases 的成员 → 条件 ② 触发并掉。
    修复:graphs 非空时,统计 person_chunk_count;若两 entity 都是高频角色
    (各自 chunk_count ≥ 2),禁止通过 ②/③ 软规则合并(条件 ① 子串硬规则保留)。
    保守:宁可漏合(用户可手动 merge),不能漏抽(核心角色消失,体验崩塌)。

    本函数对所有 PERSON 实体做二次合并,合并触发条件(满足任一):
    ① 一个 name 是另一个 name 的真子串(如"绿子"⊂"小林绿子",中文 2 字 ↔ 3-4 字全名)
    ② 一个 name 在另一条的 aliases 里(LLM 在某 chunk 已经标注过归一)
    ③ 两条 aliases 集合有交集(LLM 在不同 chunk 用不同 alias 标注同一人)

    合并策略:
    - 保留出现次数更多的(假设有合理的 description 长度作 proxy)
    - 另一个 name + 其全部 aliases 累积到主条 aliases
    - 上限 15 alias(对齐 character_merger 设计)

    只对 PERSON 类型合并;LOCATION / OBJECT / EVENT 不动(场景重名不常见,且场景合并
    有用户手动操作,见 scene_extractor.merge_scenes)。
    """
    # P0.C(2026-05-24)— 先按后缀矫正误标 type(治"阿美寮"被标 PERSON)
    # 在合并 alias 前矫正,避免误并到角色 aliases 里
    merged_entities = _correct_entity_types_by_suffix(merged_entities)

    # P0I.4(2026-05-24)— PERSON 名互斥前置防御(治"渡边的 aliases 含'永泽''女孩'"严重 bug)
    # 根因:LLM 在某个 chunk 错把"永泽"塞进"渡边"的 aliases → union-find 条件 ②
    # 触发 → 永泽被合并掉 → 永泽的 description 覆盖渡边的 → 数据彻底错乱
    # 防御:扫所有 PERSON 的 aliases,**剔除任何"也是另一条 PERSON name"的项**
    # 两个不同的 PERSON entity 绝不允许通过 aliases 串到一起
    all_person_names_lc = {
        e["name"].lower()
        for e in merged_entities.values()
        if (e.get("type") or "PERSON") == "PERSON"
    }
    for ent in merged_entities.values():
        if (ent.get("type") or "PERSON") != "PERSON":
            continue
        ent_name_lc = ent["name"].lower()
        old_aliases = ent.get("aliases") or []
        filtered = []
        for a in old_aliases:
            if not isinstance(a, str) or not a.strip():
                continue
            a_lc = a.lower().strip()
            # 剔除等于另一条 PERSON name 的 alias(不剔除自己 = 自己的情况)
            if a_lc != ent_name_lc and a_lc in all_person_names_lc:
                logger.warning(
                    f"P0I.4: 剔除 {ent['name']} 的 aliases 中误塞的他人 name: {a}"
                )
                continue
            filtered.append(a)
        ent["aliases"] = filtered

    # 1. 只挑 PERSON,LOCATION/OBJECT/EVENT 直接跳过
    persons: list[dict] = [
        e for e in merged_entities.values()
        if (e.get("type") or "PERSON") == "PERSON"
    ]
    non_persons: list[dict] = [
        e for e in merged_entities.values()
        if (e.get("type") or "PERSON") != "PERSON"
    ]
    if len(persons) < 2:
        return merged_entities, {}   # 不需要合并

    # 2. 构建 "name → person" 索引 + "alias → person name" 索引
    by_name: dict[str, dict] = {p["name"]: p for p in persons}
    alias_to_main: dict[str, str] = {}   # alias / 子串 → 主 name
    for p in persons:
        for a in p.get("aliases", []):
            if a and a not in by_name:   # 别名不与现有 name 冲突
                alias_to_main.setdefault(a, p["name"])

    # 3. 找需要合并的对子 — 用 union-find 防多对传递场景
    # parent[name] = name(初始);合并时 parent[child] = root
    parent: dict[str, str] = {p["name"]: p["name"] for p in persons}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]   # 路径压缩
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        # 选 description 更长 / aliases 更多的那条作为 root(信息量更全)
        pa, pb = by_name[ra], by_name[rb]
        score_a = len(pa.get("description", "")) + len(pa.get("aliases", []))
        score_b = len(pb.get("description", "")) + len(pb.get("aliases", []))
        if score_a >= score_b:
            parent[rb] = ra
        else:
            parent[ra] = rb

    # FOCUS.9(2026-05-22):统计每 name 在多少 chunk 被独立列为 PERSON
    # ≥ 2 chunks 出现 = "高频角色",启用合并保护(防"直子失踪"型 bug)
    person_chunk_count: dict[str, int] = {}
    if graphs:
        for g in graphs:
            if not isinstance(g, dict):
                continue
            for ent in g.get("entities", []) or []:
                if (
                    isinstance(ent, dict)
                    and (ent.get("type") or "PERSON") == "PERSON"
                ):
                    nm = ent.get("name")
                    if nm:
                        person_chunk_count[nm] = person_chunk_count.get(nm, 0) + 1

    def _is_high_freq(name: str) -> bool:
        """高频角色 = 在 ≥ 2 chunks 被独立列为 PERSON entity。"""
        return person_chunk_count.get(name, 0) >= 2

    names = [p["name"] for p in persons]
    for i, name_a in enumerate(names):
        for name_b in names[i + 1:]:
            # 条件 ①:一个 name 是另一个 name 的真子串(且长度差 ≥ 1 字)
            # 限制:短 name 必须 ≥ 2 字(避免单字"我"误合到任意带"我"的全名)
            # 子串规则是硬规则(确定性强,无 LLM 误标风险)→ 即使两者都高频也允许合
            shorter, longer = sorted((name_a, name_b), key=len)
            if len(shorter) >= 2 and shorter != longer and shorter in longer:
                # P0.A(2026-05-24)排除关系后缀 — 治"绿子"合并"绿子父亲"严重 bug
                # "绿子" + "父亲" / "玲子" + "的丈夫" / "直子" + "姐姐" 都是不同人,不能合并
                # 但"绿子" 合 "小林绿子" 仍允许(全名归一,后缀不在 _RELATION_SUFFIXES)
                if _is_relation_suffix_pair(shorter, longer):
                    continue
                union(name_a, name_b)
                continue

            # FOCUS.9 保护:两个都是高频角色 → 跳过软规则 ② / ③ 合并
            # (核心角色被误并比"漏合"代价大得多 — 漏合用户能手动 merge,误并是消失)
            if _is_high_freq(name_a) and _is_high_freq(name_b):
                continue

            # 条件 ②:一个 name 在另一条的 aliases 里
            if name_a in by_name[name_b].get("aliases", []):
                union(name_a, name_b)
                continue
            if name_b in by_name[name_a].get("aliases", []):
                union(name_a, name_b)
                continue
            # 条件 ③:aliases 交集非空(双方都标注过同一 alias)
            aliases_a = set(by_name[name_a].get("aliases", []))
            aliases_b = set(by_name[name_b].get("aliases", []))
            if aliases_a & aliases_b:
                union(name_a, name_b)
                continue

    # 4. 按 root 分组,把非 root 的 aliases / name 累积到 root.aliases
    groups: dict[str, list[str]] = {}
    for n in names:
        root = find(n)
        groups.setdefault(root, []).append(n)

    final_persons: list[dict] = []
    for root, members in groups.items():
        if len(members) == 1:
            # P0L.4(2026-05-24)— 单成员组也必须跑 alias 清洗
            # 历史 bug:LLM 在单 chunk 直接输出 {"name":"叶子","aliases":["叶子的弟弟"]}
            # 不经过 union-find merge,旧代码直接 append 跳过清洗 → 脏 alias 落库
            single_person = dict(by_name[root])
            raw_aliases = single_person.get("aliases") or []
            cleaned = [
                a for a in raw_aliases
                if isinstance(a, str) and a.strip()
                and not _is_invalid_alias(single_person["name"], a)
            ]
            single_person["aliases"] = cleaned[:15]
            final_persons.append(single_person)
            continue
        # 合并 members 到 root
        root_person = dict(by_name[root])   # copy 防原 dict 污染
        merged_aliases: list[str] = list(root_person.get("aliases", []))
        merged_aliases_set = set(merged_aliases)
        for m in members:
            if m == root:
                continue
            # m.name 进 root.aliases(如果不在)
            if m not in merged_aliases_set and m != root:
                merged_aliases.append(m)
                merged_aliases_set.add(m)
            # m.aliases 累积
            for a in by_name[m].get("aliases", []):
                if a and a not in merged_aliases_set and a != root:
                    merged_aliases.append(a)
                    merged_aliases_set.add(a)
            # description 选更长的(信息量更高)
            m_desc = by_name[m].get("description", "")
            if len(m_desc) > len(root_person.get("description", "")):
                root_person["description"] = m_desc
        # P0.A + P0.D(2026-05-24)— alias 清洗升级:用模式识别替代封闭字典
        # 治"渡边的旧女友 / 高三时的女友 / 第一次睡过的女友"漏网 bug
        # 三选一剔除:含"的" / 含关系词且长>2 / 长度>6
        cleaned_aliases = [
            a for a in merged_aliases
            if not _is_invalid_alias(root_person["name"], a)
        ]
        root_person["aliases"] = cleaned_aliases[:15]   # 上限 15
        final_persons.append(root_person)

    # 5. 重建 {name: entity} dict(保留 LOCATION 等非 PERSON 类型)
    new_merged: dict[str, dict] = {p["name"]: p for p in final_persons}
    for np in non_persons:
        new_merged[np["name"]] = np

    # 6. 构建 name 映射:被合并掉的 name → root name(给 relations reassign 用)
    name_remap: dict[str, str] = {}
    for root, members in groups.items():
        for m in members:
            if m != root:
                name_remap[m] = root
    return new_merged, name_remap


# ======================================================================
# Sprint 6.A2 FOCUS.12(2026-05-22):LLM 实体消解 / 指代统一
# 治 Gemini 实测暴露的痛点 — 程序级 _post_merge_alias_dedup 无法识别零字符重叠的
# 等价称呼("行男" vs "病人" / "客栈掌柜" vs "客栈主人")。
# 在程序级合并之后,再跑一次 LLM 校验,治"指代消解"经典 IE 问题。
# ======================================================================

ENTITY_DEDUP_PROMPT_FILE = "entity_dedup.md"


def _load_entity_dedup_prompt() -> str:
    """加载 prompts/entity_dedup.md(LOCKED v1)。"""
    from pathlib import Path
    p = Path(__file__).resolve().parent.parent.parent.parent / "prompts" / ENTITY_DEDUP_PROMPT_FILE
    return p.read_text(encoding="utf-8")


def dedup_persons_with_llm(
    persons: list[dict],
    full_text: str,
    *,
    max_persons: int = 50,
    context_sample_chars: int = 1500,
) -> tuple[list[dict], dict]:
    """对程序级合并后的 PERSON 列表跑 LLM 实体消解。

    入参:
      persons:_post_merge_alias_dedup 之后的 PERSON list,每条形如
        `{name, description, aliases, type='PERSON'}`(text_occurrences 由本函数计算)
      full_text:作品全文(取开头 context_sample_chars 字给 LLM 作背景)

    返回:
      (merge_groups, usage)
      merge_groups:[{canonical_name, members: [name1, name2], reason}, ...]
      usage:LLM token 用量,用于成本统计

    异常:静默吞掉(不能因消解失败阻塞整次抽取)— 返回 ([], {"input_tokens": 0, ...})。

    成本:DeepSeek V3 处理 21 PERSON ≈ 500 input + 200 output token = ~¥0.001
    """
    if len(persons) < 2:
        return [], {"input_tokens": 0, "output_tokens": 0}
    # 限制规模:超过 50 角色 → 只取前 50(频次或字数排序),防 token 爆炸
    # 实际项目极少超过 50;百万字超长篇可能撞到
    if len(persons) > max_persons:
        persons = persons[:max_persons]

    # 计算 text_occurrences(从原文 count name)— LLM 用此判断 canonical_name
    persons_with_occ = [
        {
            "name": p.get("name", ""),
            "description": p.get("description", ""),
            "aliases": p.get("aliases", []),
            "text_occurrences": full_text.count(p.get("name", "")) if p.get("name") else 0,
        }
        for p in persons
        if p.get("name")
    ]
    # context_sample:取作品开头部分
    context_sample = full_text[:context_sample_chars]

    prompt_input = {
        "persons": persons_with_occ,
        "context_sample": context_sample,
    }
    try:
        system_prompt = _load_entity_dedup_prompt()
        parsed, usage = call_llm_json(system_prompt, prompt_input)
    except Exception as e:  # noqa: BLE001
        import logging
        logging.warning("dedup_persons_with_llm LLM call 失败,跳过消解: %s", e)
        return [], {"input_tokens": 0, "output_tokens": 0}

    if not isinstance(parsed, dict):
        return [], usage
    raw_groups = parsed.get("merge_groups") or []
    if not isinstance(raw_groups, list):
        return [], usage

    # 后端兜底校验 — LLM 可能输出非法 canonical / 编造名字(虽然 prompt 严防)
    valid_names = {p["name"] for p in persons_with_occ if p.get("name")}
    validated: list[dict] = []
    for g in raw_groups:
        if not isinstance(g, dict):
            continue
        canonical = g.get("canonical_name")
        members = g.get("members") or []
        if not canonical or not isinstance(canonical, str):
            continue
        if canonical not in valid_names:
            continue   # LLM 编了新名字,丢
        if not isinstance(members, list):
            continue
        valid_members = [
            m for m in members
            if isinstance(m, str) and m in valid_names and m != canonical
        ]
        if not valid_members:
            continue   # 没有任何合法 member,这条 group 等于空
        validated.append({
            "canonical_name": canonical,
            "members": valid_members,
            "reason": str(g.get("reason") or "")[:80],
        })
    return validated, usage


def _apply_llm_dedup_to_entities(
    entities_by_name: dict[str, dict],
    merge_groups: list[dict],
    relations: list[dict],
) -> tuple[dict[str, dict], list[dict], dict[str, str]]:
    """按 LLM 消解结果合并 entities + reassign relations。

    Mirror `_post_merge_alias_dedup` 的合并语义:
      - canonical 作 root,members 的 name 进 canonical.aliases
      - members 的 description 累积到 root(选最长)
      - relations 里指向 member 的 source/target 改为 canonical
      - self-loop(reassign 后 src==tgt)跳过

    返回:(new_entities_by_name, new_relations, name_remap)
    """
    name_remap: dict[str, str] = {}
    for g in merge_groups:
        canonical = g["canonical_name"]
        if canonical not in entities_by_name:
            continue   # 防御:LLM 给的 canonical 已被前面合并掉
        root = entities_by_name[canonical]
        merged_aliases = list(root.get("aliases", []))
        for m in g["members"]:
            if m == canonical or m not in entities_by_name:
                continue
            member_entity = entities_by_name[m]
            # m.name 进 root.aliases
            if m not in merged_aliases:
                merged_aliases.append(m)
            # m.aliases 累积
            for a in member_entity.get("aliases", []):
                if a and a not in merged_aliases and a != canonical:
                    merged_aliases.append(a)
            # description 选更长的
            m_desc = member_entity.get("description", "")
            if len(m_desc) > len(root.get("description", "")):
                root["description"] = m_desc
            # 从 entities_by_name 删除 member
            del entities_by_name[m]
            name_remap[m] = canonical
        root["aliases"] = merged_aliases[:15]

    # reassign relations(指向被合并 member 的 source/target → canonical)
    new_relations: list[dict] = []
    seen_rel_keys: set[tuple] = set()
    for r in relations:
        if not isinstance(r, dict):
            continue
        src = r.get("source")
        tgt = r.get("target")
        if src in name_remap:
            src = name_remap[src]
        if tgt in name_remap:
            tgt = name_remap[tgt]
        if not src or not tgt or src == tgt:
            continue   # self-loop 跳
        key = (src, tgt, r.get("type"))
        if key in seen_rel_keys:
            continue
        seen_rel_keys.add(key)
        new_r = dict(r)
        new_r["source"] = src
        new_r["target"] = tgt
        new_relations.append(new_r)
    return entities_by_name, new_relations, name_remap


# P0I.2(2026-05-24)— life_status 工具函数
# 严重度顺序:deceased > in_facility > absent > unknown > alive
# 跨 chunk merge 时取最严重值 — 末段 chunk 看到死亡就一定传播到最终结果
_LIFE_STATUS_SEVERITY: dict[str, int] = {
    "deceased": 4,    # 最严重 — 任一 chunk 见死亡,最终必死
    "in_facility": 3, # 隔离机构
    "absent": 2,      # 暂时离开
    "unknown": 1,     # 状态未知
    "alive": 0,       # 默认在世(最低优先级,易被覆盖)
}


def _normalize_life_status(raw: object) -> str:
    """把 LLM 输出的 life_status 字符串归一化到 5 档之一,非法值返 'unknown'."""
    if not isinstance(raw, str):
        return "unknown"
    val = raw.strip().lower()
    if val in _LIFE_STATUS_SEVERITY:
        return val
    return "unknown"


def _life_status_severity(status: str) -> int:
    """返回 life_status 的严重度数值,用于 merge 取最严重."""
    return _LIFE_STATUS_SEVERITY.get(status, 1)


def _merge_graphs(graphs: list[dict]) -> dict:
    """合并多块 graph 输出。

    去重策略:
    - entities:按 name 去重;aliases 取并集;description 选最长那条(信息量最丰富)
    - relations:按 (source, target, type) 三元组去重;description 选最长
    - **跨 chunk 程序级归一(FOCUS.4,2026-05-22)**:多 chunk 抽取时,LLM 在每个 chunk
      内可能把"小林绿子"和"绿子"看作同一人,但不同 chunk 各自挑了不同的 name 出来
      → 合并后看到两条独立 PERSON。本步骤扫描所有 PERSON entity,对"name 是另一 name
      子串 / aliases 含另一 name / aliases 交集"的对子,程序级合并。
    """
    merged_entities: dict[str, dict] = {}   # name → entity dict
    for g in graphs:
        if not isinstance(g, dict):
            continue
        for ent in g.get("entities", []) or []:
            if not isinstance(ent, dict):
                continue
            name = ent.get("name")
            if not name:
                continue
            if name not in merged_entities:
                merged_entities[name] = {
                    "name": name,
                    "type": ent.get("type", "PERSON"),
                    "aliases": list(ent.get("aliases") or []),
                    "description": str(ent.get("description") or ""),
                }
            else:
                cur = merged_entities[name]
                # aliases 并集
                seen = set(cur["aliases"])
                for a in ent.get("aliases") or []:
                    if a and a not in seen:
                        cur["aliases"].append(a)
                        seen.add(a)
                # description 选更长的(信息量更多)
                new_desc = str(ent.get("description") or "")
                if len(new_desc) > len(cur["description"]):
                    cur["description"] = new_desc
                # P0J.5(2026-05-24):life_status 字段已从 build_graph 抽取里拆出
                # 由独立 agent life_status_inferer 在 extract 完工后单独跑;此处不再 merge

    # === FOCUS.4(2026-05-22)跨 chunk 程序级归一 — 治《挪威的森林》"小林绿子"/"绿子"
    #     被不同 chunk 分别选为 name 后,_merge_graphs 按严格 name 不去重的盲点 ===
    # FOCUS.9(2026-05-22)修补:传 graphs 给 dedup,启用"高频角色保护"防"直子失踪"
    merged_entities, name_remap = _post_merge_alias_dedup(merged_entities, graphs)

    merged_relations: dict[tuple, dict] = {}   # (src, tgt, type) → relation dict
    for g in graphs:
        if not isinstance(g, dict):
            continue
        for rel in g.get("relations", []) or []:
            if not isinstance(rel, dict):
                continue
            src = rel.get("source")
            tgt = rel.get("target")
            # FOCUS.4(2026-05-22):被合并掉的 name → root,防孤儿关系
            if src in name_remap:
                src = name_remap[src]
            if tgt in name_remap:
                tgt = name_remap[tgt]
            # M7.G(2026-05-20):type 走 normalize 兜底,允许任意 1-20 字关系名
            # (含 LLM 自创如"暗恋""主仆""位于""参与""提及"等,DB 不再拒)
            rtype = _normalize_type(rel.get("type"))
            if not src or not tgt:
                continue
            # FOCUS.4:reassign 后 src==tgt(self-loop)→ 跳过
            if src == tgt:
                continue
            strength = _normalize_strength(rel.get("strength"))
            key = (src, tgt, rtype)
            if key not in merged_relations:
                merged_relations[key] = {
                    "source": src,
                    "target": tgt,
                    "type": rtype,
                    "description": str(rel.get("description") or ""),
                    "strength": strength,
                }
            else:
                # description 选更长的;strength 取多块结果中更强的
                # (同一关系如在多块都出现,说明涉及面广,应被视为更强;
                # 也避免某一块 LLM 偶然给弱标签把核心关系误降级)
                cur = merged_relations[key]
                new_desc = str(rel.get("description") or "")
                if len(new_desc) > len(cur["description"]):
                    cur["description"] = new_desc
                cur["strength"] = _stronger_of(cur["strength"], strength)

    # Sprint 6.A2 FOCUS.2(2026-05-21):聚合 meta.narrative_pov(各 chunk 投票)
    # 一般小说全文都是同一视角,各 chunk 输出一致 → 多数投票即可
    # 多视角小说(如《罗生门》)各 chunk 不一致 → 投出"mixed"
    pov_votes: dict[str, int] = {}
    for g in graphs:
        if not isinstance(g, dict):
            continue
        meta = g.get("meta") if isinstance(g.get("meta"), dict) else {}
        pov = meta.get("narrative_pov") if isinstance(meta, dict) else None
        if isinstance(pov, str) and pov in {"first", "second", "third", "mixed"}:
            pov_votes[pov] = pov_votes.get(pov, 0) + 1

    final_pov: Optional[str] = None
    if pov_votes:
        # 不同 chunk 视角不一致 + 没有任何 chunk 自报 mixed → 视为 mixed
        # 全 chunk 一致或某视角占据明显多数(≥ 60%)→ 用 majority
        total = sum(pov_votes.values())
        top_pov, top_count = max(pov_votes.items(), key=lambda x: x[1])
        if len(pov_votes) == 1:
            final_pov = top_pov
        elif top_count / total >= 0.6:
            final_pov = top_pov
        else:
            final_pov = "mixed"

    return {
        "entities": list(merged_entities.values()),
        "relations": list(merged_relations.values()),
        "meta": {"narrative_pov": final_pov},
    }


def extract_graph_chunked(
    text: str,
    work_name: str = "",
    work_type: str = "作品",
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    on_chunk_done: Optional[Callable[[int, int, dict, str, dict, str], None]] = None,
    on_chunk_failed: Optional[Callable[[int, int, str], None]] = None,
    on_chunk_skipped: Optional[Callable[[int, int], None]] = None,
    completed_chunks: Optional[dict[int, tuple[str, dict, int, int]]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> tuple[dict, dict]:
    """长文本分块抽取 + 合并去重 + 断点续抽。

    Args:
      text:        作品全文
      work_name:   作品名
      work_type:   类型词
      chunk_chars: 单块字数上限(默认 25K)

      on_chunk_done:   回调 (chunk_idx, total, partial_usage, chunk_hash, chunk_graph_data, chunk_text)
                       extract_service 用它**实时持久化**该块到 extract_chunk_results 表,
                       并推 SSE chunk_done 事件给前端
                       Sprint 6.A2 M3.C 加 chunk_text 参数(给 RAG 用)
      on_chunk_failed: 回调 (chunk_idx, total, err_msg);单块失败时触发
      on_chunk_skipped: 回调 (chunk_idx, total);命中已完成 chunk 时触发(SSE chunk_skipped)

      completed_chunks: 已完成的 {chunk_idx_1based: (hash, graph_data, in_tokens, out_tokens)}
                       — extract_service 从 db 加载;hash 一致则跳过该块
      should_cancel:   每块开始前调用;返回 True → raise InterruptedError 退出循环

    Returns:
      (merged_graph, total_usage)
        merged_graph: 合并去重后的全文图谱(包含已 resume 的块 + 本次新抽的块)
        total_usage:  累加 input_tokens + output_tokens(只算本次新抽,不计 resume 块的旧 token)

    Raises:
      InterruptedError: should_cancel 返回 True
      RuntimeError:     全部块都失败
    """
    chunks = split_text_into_chunks(text, chunk_chars)
    total = len(chunks)
    completed_chunks = completed_chunks or {}
    total_in = 0
    total_out = 0
    per_chunk_graphs: list[dict] = []

    for i, chunk in enumerate(chunks):
        if should_cancel is not None and should_cancel():
            raise InterruptedError("用户取消抽取")

        idx_1based = i + 1
        cur_hash = compute_chunk_hash(chunk)

        # === 断点续抽:hash 一致 → 跳过 ===
        prev = completed_chunks.get(idx_1based)
        if prev is not None:
            prev_hash, prev_graph, prev_in, prev_out = prev
            if prev_hash == cur_hash:
                per_chunk_graphs.append(prev_graph)
                # 注意:resume 块的 token 不再累计到本次 total_*(避免重复计费)
                # extract_service 拿原 job 的 tokens_input/output 加本次 total 即可
                if on_chunk_skipped is not None:
                    try:
                        on_chunk_skipped(idx_1based, total)
                    except Exception as _cb_err:  # noqa: BLE001
                        # 2026-06-02:回调失败不阻塞,但至少 log(否则前端进度推送失败时静默)
                        import logging
                        logging.getLogger(__name__).warning(
                            "on_chunk_skipped 回调失败 chunk=%d/%d: %s",
                            idx_1based, total, _cb_err,
                        )
                continue
            # hash 不一致 → 用户中途换了文件;旧记录作废,本块重抽
            # (extract_service 应在 resume 入口先校验,走到这里说明 hash 比较意外失败,fallback)

        # === 正常抽取 ===
        meta_for_chunk = work_name
        if total > 1:
            meta_for_chunk = (
                f"{work_name}(全文 {total} 段中的第 {idx_1based} 段;"
                f"可能不含全部主角,只抽本段实际出现的)"
            )
        try:
            graph_data, usage = extract_graph(chunk, meta_for_chunk, work_type)
            per_chunk_graphs.append(graph_data)
            in_t = usage.get("input_tokens", 0)
            out_t = usage.get("output_tokens", 0)
            total_in += in_t
            total_out += out_t
            if on_chunk_done is not None:
                on_chunk_done(idx_1based, total, {
                    "input_tokens": total_in,
                    "output_tokens": total_out,
                }, cur_hash, graph_data, chunk)
        except Exception as e:  # noqa: BLE001
            # 单块失败不阻塞整体 — log + 通知上层 + 继续(其它块仍能合出图谱)
            import logging
            err_msg = f"{type(e).__name__}: {e}"[:200]
            logging.warning(
                "chunk %d/%d 抽取失败,跳过: %s", idx_1based, total, err_msg,
            )
            if on_chunk_failed is not None:
                try:
                    on_chunk_failed(idx_1based, total, err_msg)
                except Exception as _cb_err:  # noqa: BLE001
                    # 2026-06-02:回调失败不阻塞,但至少 log
                    import logging
                    logging.getLogger(__name__).warning(
                        "on_chunk_failed 回调失败 chunk=%d/%d: %s",
                        idx_1based, total, _cb_err,
                    )

    if not per_chunk_graphs:
        raise RuntimeError("所有分块都抽取失败,无法合并图谱")

    merged = _merge_graphs(per_chunk_graphs)
    return merged, {"input_tokens": total_in, "output_tokens": total_out}


# ============================================================
# 2. 单角色档案生成
# ============================================================

# 角色档案输出结构:identity(短)+ personality(中)+ quotes(数组,可达 ~600 字)+
# no_go_list(数组,可达 ~400 字)+ voice_fingerprint(嵌套 dict)
# 旧 4000 边缘够用但 quotes/no_go 写多就崩;给 5000 留余量
PROFILE_MAX_TOKENS = 5000
PROFILE_TIMEOUT = 90.0


def generate_character_profile(
    work_name: str,
    language_style: str,
    char_entity: dict,
    source_text: str,
) -> tuple[dict, dict]:
    """对一个抽出来的 PERSON 实体,生成完整角色档案。

    Args:
      work_name: 作品名
      language_style: 语体描述(如"古典武侠" / "现代都市" / "通用")
      char_entity: build_graph 输出的 PERSON 实体(含 name + aliases + description)
      source_text: 原文全文(用于检索 excerpts)

    Returns:
      (profile_dict, usage_dict) — profile 含 identity/personality/quotes/no_go_list 等
    """
    template = _load_prompt("character_generator.md")
    excerpts = find_character_excerpts(
        source_text, char_entity["name"], char_entity.get("aliases", [])
    )
    if not excerpts:
        # 找不到节选不报错 — 用 description 兜底,质量差但不阻塞流程
        excerpts = char_entity.get("description", "(原文中无显著上下文)")

    user_prompt = _safe_format(
        template,
        work_name=work_name,
        character_name=char_entity["name"],
        language_style=language_style,
        source_excerpts=excerpts,
    )
    parsed, usage = call_llm_json(
        "", user_prompt,
        max_tokens=PROFILE_MAX_TOKENS,
        timeout=PROFILE_TIMEOUT,
        retries=2,
    )
    if not isinstance(parsed, dict):
        raise ValueError(
            f"character_generator LLM 输出顶层不是 dict:{type(parsed).__name__}"
        )
    return parsed, usage


# ============================================================
# 2.5 批量轻量档案补全(2.B+ 方案 D)
# ============================================================

# 每批配角数:25 是 DeepSeek 64K context 的安全甜点
# - 25 角色 × 1000 字 excerpts ≈ 25K 字 ≈ 38K input token
# - + prompt 1.5K + 输出 ~3K = 42K,留 22K buffer
MINIMAL_BATCH_SIZE = 25
MINIMAL_EXCERPTS_PER_PERSON = 1000      # 每角色节选字数(1000 字够抽 2-3 句对白)
MINIMAL_MAX_TOKENS = 6000               # 输出 25 角色 × ~150 token + 余量
MINIMAL_TIMEOUT = 90.0


def _build_persons_block(
    persons: list[dict], source_text: str, excerpts_chars: int,
) -> str:
    """把配角列表拼成 markdown 块给 LLM 看。"""
    parts: list[str] = []
    for i, p in enumerate(persons, start=1):
        name = p.get("name", "")
        aliases = list(p.get("aliases") or [])
        desc = str(p.get("description") or "(无描述)")
        excerpts = find_character_excerpts(
            source_text, name, aliases,
            max_chars=excerpts_chars, context_window=200,
        )
        if not excerpts:
            excerpts = "(原文中无显著上下文 — 仅凭描述推断,quotes 给 [])"
        alias_str = ", ".join(aliases) if aliases else "(无)"
        parts.append(
            f"## {i}. {name}\n"
            f"- 别名: {alias_str}\n"
            f"- 描述: {desc}\n"
            f"- 原文节选:\n\"\"\"\n{excerpts}\n\"\"\"\n"
        )
    return "\n".join(parts)


def generate_minimal_profiles_batch(
    work_name: str,
    language_style: str,
    person_entities: list[dict],
    source_text: str,
    excerpts_per_person_chars: int = MINIMAL_EXCERPTS_PER_PERSON,
) -> tuple[list[dict], dict]:
    """单次 LLM 调用为 N 个配角生成简略档案(N 通常 ≤ 25)。

    与 generate_character_profile 的差异:
      - 1 次 LLM 调用搞定 N 个角色 → 成本是单调的 1/N(N=25 时省 25 倍)
      - 输出字段精简:只 personality / quotes / no_go_list,不出 voice_fingerprint
        / behavioral_rules / key_events 等高级字段(留给 top 30 主角的精品档案)
      - 节选短(每角色 1000 字而非 8000),quotes 抽得到就抽,抽不到给 []

    Args:
      person_entities: build_graph 输出的 PERSON 实体列表(name + aliases + description)
      source_text:     原文全文(用于 find_character_excerpts 检索每角色的局部节选)

    Returns:
      (profiles, usage)
        profiles: list of {name, personality, quotes, no_go_list},顺序与输入对齐
                  (LLM 漏输出某角色 → 该位置补 fallback 占位)
        usage:    {input_tokens, output_tokens}

    Raises:
      LlmCallFailed / LlmJsonParseFailed:LLM 调用 / JSON 解析全失败
      ValueError:LLM 输出顶层不是 list
    """
    if not person_entities:
        return [], {"input_tokens": 0, "output_tokens": 0}

    template = _load_prompt("character_minimal_profile.md")
    persons_block = _build_persons_block(
        person_entities, source_text, excerpts_per_person_chars,
    )
    user_prompt = _safe_format(
        template,
        work_name=work_name,
        language_style=language_style,
        persons_block=persons_block,
    )
    parsed, usage = call_llm_json(
        "", user_prompt,
        max_tokens=MINIMAL_MAX_TOKENS,
        timeout=MINIMAL_TIMEOUT,
        retries=2,
    )
    if not isinstance(parsed, list):
        raise ValueError(
            f"character_minimal_profile LLM 输出顶层不是 list:{type(parsed).__name__}"
        )

    # 按 name 索引 LLM 返回的档案
    by_name: dict[str, dict] = {}
    for item in parsed:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if name and isinstance(name, str):
            by_name[name] = item

    # 严格按输入顺序对齐返回(LLM 漏的位置补 fallback)
    aligned: list[dict] = []
    for ent in person_entities:
        name = ent.get("name", "")
        prof = by_name.get(name)
        if prof is None:
            # LLM 漏掉了 → 用 entity description 兜底
            aligned.append({
                "name": name,
                "personality": "",
                "quotes": [],
                "no_go_list": [],
            })
        else:
            # 字段类型清洗(防 LLM 给非 list 的 quotes 等)
            quotes = prof.get("quotes")
            if not isinstance(quotes, list):
                quotes = []
            no_go = prof.get("no_go_list")
            if not isinstance(no_go, list):
                no_go = []
            aligned.append({
                "name": name,
                "personality": str(prof.get("personality") or "")[:200],
                "quotes": [str(q)[:120] for q in quotes][:5],
                "no_go_list": [str(n)[:120] for n in no_go][:3],
            })

    return aligned, usage


# ============================================================
# 3. 作品元信息识别(type + custom_type_name + tags)
# ============================================================

# 2.C+ polish: world_baseline 6 维度白名单(对齐 prompts/infer_meta.md v2)
INFER_META_WORLD_BASELINE_FIELDS = (
    "genre", "setting", "magic_system", "time_axis", "tone", "free_form",
)


def infer_meta(work_name: str, full_text: str) -> tuple[dict, dict]:
    """单次 LLM 调用识别作品类型 + 题材 + 世界观 6 维 baseline(v2)。

    用 sample_text_for_meta 抽样(开头+中段+结尾),不全文喂 LLM 省 token。

    Returns:
      (parsed_dict, usage_dict)
        parsed_dict:
          {
            "type": str,
            "custom_type_name": str|None,
            "tags": list[str],
            "world_baseline": {  # 2.C+ polish 新加,6 字段都必填
              "genre": str, "setting": str, "magic_system": str,
              "time_axis": str, "tone": str, "free_form": str,
            }
          }

    world_baseline 字段在调用方落 projects.world_baseline_json,给反事实工作台
    「世界观 tab」的 6 个"原"字段提供预填值。
    """
    system = _load_prompt("infer_meta.md")
    sample = sample_text_for_meta(full_text)
    user_input = (
        f"请识别下面作品的类型与题材标签 + 世界观 6 维度 baseline。\n\n"
        f"作品名:{work_name or '(未命名)'}\n\n"
        f"文本片段(开头 / 中段 / 结尾抽样):\n"
        f"\"\"\"\n{sample}\n\"\"\"\n\n只输出 JSON,严格按 schema(含 world_baseline 6 字段)。"
    )
    # v2 输出多 6 字段,加大 max_tokens 防截断(每字段 ~50 字 × 6 ≈ 300 字 ≈ 500 token)
    parsed, usage = call_llm_json(system, user_input, max_tokens=2000, retries=2)
    if not isinstance(parsed, dict):
        raise ValueError(f"infer_meta LLM 输出顶层不是 dict:{type(parsed).__name__}")

    # world_baseline 字段清洗:只保留白名单 key + 强转 str + 缺 key 兜底"未识别"
    raw_baseline = parsed.get("world_baseline")
    if not isinstance(raw_baseline, dict):
        raw_baseline = {}
    cleaned_baseline: dict[str, str] = {}
    for f in INFER_META_WORLD_BASELINE_FIELDS:
        v = raw_baseline.get(f)
        if v is None or v == "":
            cleaned_baseline[f] = "未识别"
        else:
            cleaned_baseline[f] = str(v)[:300]   # 防过长
    parsed["world_baseline"] = cleaned_baseline

    return parsed, usage


# ============================================================
# 校验工具
# ============================================================

VALID_RELATION_TYPES = {
    "亲属", "主仆", "朋友", "情侣", "敌对", "同事", "师徒",
    "参与", "位于", "拥有", "提及",
}

# UI 显示的 7 类关系(对齐 RelationshipCreator chips)— extract 时若遇到非这 7 类映射成"其他"
UI_RELATION_TYPES = {"亲属", "敌对", "朋友", "情侣", "师徒", "同事"}


def normalize_relation_type(raw_type: str) -> str:
    """把 LLM 输出的关系类型映射到 UI 7 类之一。
    "参与"/"位于"/"拥有"/"提及" 这些非互动关系 → 不入 relationships 表(留作 events 关联)。
    """
    if raw_type in UI_RELATION_TYPES:
        return raw_type
    return "其他"


def is_interpersonal_relation(raw_type: str) -> bool:
    """关系是否表达"两个 PERSON 之间的人际关系"(用于过滤 build_graph 输出 —
    "参与"/"位于"/"拥有" 这些非人际关系不该进 relationships 表)。
    """
    return raw_type in UI_RELATION_TYPES or raw_type == "其他"


__all__ = [
    "extract_graph",
    "extract_graph_chunked",
    "generate_character_profile",
    "generate_minimal_profiles_batch",
    "infer_meta",
    "find_character_excerpts",
    "sample_text_for_meta",
    "split_text_into_chunks",
    "compute_chunk_hash",
    "normalize_relation_type",
    "is_interpersonal_relation",
    "VALID_RELATION_TYPES",
    "UI_RELATION_TYPES",
    "DEFAULT_CHUNK_CHARS",
    "EXTRACT_GRAPH_MAX_TOKENS",
    "EXTRACT_GRAPH_TIMEOUT",
    "PROFILE_MAX_TOKENS",
    "PROFILE_TIMEOUT",
    "MINIMAL_BATCH_SIZE",
    "MINIMAL_EXCERPTS_PER_PERSON",
    "MINIMAL_MAX_TOKENS",
    "MINIMAL_TIMEOUT",
    "INFER_META_WORLD_BASELINE_FIELDS",
]
