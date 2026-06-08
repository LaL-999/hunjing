"""Sprint 6.A2 FOCUS.9(2026-05-22):
  ① _post_merge_alias_dedup 高频角色保护(治"直子失踪"实战 bug)
  ② detect_missed_persons 漏抽人名检测
"""
from __future__ import annotations


def test_high_frequency_person_not_merged_via_alias_overlap():
    """重现"直子失踪":LLM 把"直子"误标为"绿子" alias,但"直子"在多 chunk 独立出现 → 不应合并。"""
    from app.services.llm_extract import _merge_graphs

    graphs = [
        # chunk 1:直子和绿子都作为独立 PERSON 出现
        {
            "entities": [
                {"name": "直子", "type": "PERSON", "aliases": [], "description": "渡边的恋人,精神创伤"},
                {"name": "绿子", "type": "PERSON", "aliases": [], "description": "渡边的同学,活泼"},
                {"name": "渡边", "type": "PERSON", "aliases": [], "description": "主角"},
            ],
            "relations": [],
            "meta": {},
        },
        # chunk 2:直子和绿子又分别独立出现
        {
            "entities": [
                {"name": "直子", "type": "PERSON", "aliases": [], "description": "直子在阿美寮疗养"},
                {"name": "绿子", "type": "PERSON", "aliases": [], "description": "绿子家经营书店"},
            ],
            "relations": [],
            "meta": {},
        },
        # chunk 3:LLM 这次出错,把"直子"错放到"绿子" aliases 里(模拟 LLM 误标)
        {
            "entities": [
                {"name": "绿子", "type": "PERSON", "aliases": ["直子"],  # ← LLM 误标
                 "description": "另一段描述"},
            ],
            "relations": [],
            "meta": {},
        },
    ]
    merged = _merge_graphs(graphs)
    person_names = {
        e["name"] for e in merged["entities"]
        if e.get("type") == "PERSON"
    }
    # FOCUS.9 修复后:直子和绿子都是高频角色(各 ≥ 2 chunks 独立 PERSON),
    # 即使有 alias 误标,也不会被合并 → 两者都保留
    assert "直子" in person_names, "直子应该保留(高频角色保护)"
    assert "绿子" in person_names, "绿子应该保留(高频角色保护)"


def test_low_frequency_person_still_merged_via_alias():
    """P0I.4(2026-05-24)改造:PERSON name 互斥防御 — 即使低频,
    若某 name 被另一 PERSON 的 aliases 列出,也**不允许合并**(防"叶子的弟弟"
    被误塞做"叶子"的 alias 这类 LLM 错误)。

    原期望:探花郎(低频 1 chunk)+ 李寻欢 aliases 含探花郎 → 合并为 1。
    新期望:即使探花郎是低频,仍保留为独立 PERSON,李寻欢的 aliases 中
    被剔除"探花郎"。最终保留 2 个独立 PERSON。
    """
    from app.services.llm_extract import _merge_graphs

    graphs = [
        # chunk 1:出现一次"探花郎"
        {
            "entities": [
                {"name": "探花郎", "type": "PERSON", "aliases": [], "description": "古龙小说"},
            ],
            "relations": [],
            "meta": {},
        },
        # chunk 2:出现"李寻欢"且 aliases 含"探花郎"
        {
            "entities": [
                {"name": "李寻欢", "type": "PERSON", "aliases": ["探花郎"],
                 "description": "古龙小说主角"},
            ],
            "relations": [],
            "meta": {},
        },
    ]
    merged = _merge_graphs(graphs)
    person_names = {e["name"] for e in merged["entities"] if e.get("type") == "PERSON"}
    # P0I.4:即使探花郎低频,仍保留独立 PERSON(防误合并)
    assert len(person_names) == 2, f"P0I.4 后应保留 2 个独立 PERSON,实际 {person_names}"
    assert "探花郎" in person_names
    assert "李寻欢" in person_names


def test_substring_merge_still_works_for_high_freq():
    """高频角色保护**不阻止子串硬规则**(条件 ①)。
    "绿子" 是 "小林绿子" 真子串,即使两者都是高频,仍合并(子串关系是确定性的)。"""
    from app.services.llm_extract import _merge_graphs

    graphs = [
        {
            "entities": [
                {"name": "绿子", "type": "PERSON", "aliases": [], "description": "戏剧史学生"},
                {"name": "小林绿子", "type": "PERSON", "aliases": [], "description": "经营小林书店"},
            ],
            "relations": [],
            "meta": {},
        },
        {
            "entities": [
                {"name": "绿子", "type": "PERSON", "aliases": [], "description": "另一段"},
                {"name": "小林绿子", "type": "PERSON", "aliases": [], "description": "另一段"},
            ],
            "relations": [],
            "meta": {},
        },
    ]
    merged = _merge_graphs(graphs)
    person_names = {e["name"] for e in merged["entities"] if e.get("type") == "PERSON"}
    # 子串硬规则保留 → 最终只有 1 个绿子(name 是 "绿子" 或 "小林绿子" 都可)
    assert len(person_names) == 1


# ============================================================
# B. detect_missed_persons 漏抽检测
# ============================================================

def test_detect_missed_persons_finds_undetected():
    """从 description 反向扫到的"直子"应该出现在 missed 列表。"""
    from app.services.llm_extract import detect_missed_persons

    merged_entities = {
        "渡边": {
            "name": "渡边", "type": "PERSON", "aliases": [],
            "description": "东京大学学生,与直子和绿子之间有感情纠葛",
        },
        "木月": {
            "name": "木月", "type": "PERSON", "aliases": [],
            "description": "渡边的好友,直子的青梅竹马,自杀身亡",
        },
        "绿子": {
            "name": "绿子", "type": "PERSON", "aliases": [],
            "description": "渡边的同学,与直子是渡边感情的两端",
        },
    }
    full_text = (
        "直子在阿美寮疗养。直子是渡边的恋人。"
        "直子和木月青梅竹马。直子的状况让渡边痛苦。"
    )
    missed = detect_missed_persons(merged_entities, full_text)
    assert "直子" in missed


def test_detect_missed_persons_skips_low_freq():
    """原文出现 < 3 次的 candidate 不算漏抽(噪声过滤)。"""
    from app.services.llm_extract import detect_missed_persons

    merged_entities = {
        "渡边": {
            "name": "渡边", "type": "PERSON", "aliases": [],
            "description": "主角",
        },
    }
    # 直子只出现 1 次 → 不算漏抽
    full_text = "渡边和直子在咖啡馆见面。"
    missed = detect_missed_persons(merged_entities, full_text)
    assert "直子" not in missed


def test_detect_missed_persons_blacklist_filters_common_words():
    """常见伪人名(故事 / 主人公 / 命运 等)在黑名单 → 不算漏抽。"""
    from app.services.llm_extract import detect_missed_persons

    merged_entities = {
        "渡边": {
            "name": "渡边", "type": "PERSON", "aliases": [],
            "description": "故事的主人公,主人公的命运,主人公的回忆",
        },
        "木月": {
            "name": "木月", "type": "PERSON", "aliases": [],
            "description": "故事中的另一主人公",
        },
    }
    full_text = "故事关于主人公的命运。故事中主人公面对回忆。" * 3
    missed = detect_missed_persons(merged_entities, full_text)
    for blocked in ["故事", "主人公", "命运", "回忆"]:
        assert blocked not in missed


def test_detect_missed_persons_empty_when_no_descriptions():
    """空 description 列表 → 返回空(不抛)。"""
    from app.services.llm_extract import detect_missed_persons

    assert detect_missed_persons({}, "随便文本") == []
    assert detect_missed_persons(
        {"a": {"name": "a", "type": "PERSON", "aliases": [], "description": ""}},
        "",
    ) == []
