"""Sprint 6.A2 FOCUS.4(2026-05-22):_post_merge_alias_dedup 跨 chunk 程序级归一测试。

实战 bug 重现:多 chunk 抽取时,LLM 在 chunk A 输出 name="绿子",chunk B 输出
name="小林绿子",老 _merge_graphs 按严格 name 不去重 → 保留两条独立 PERSON。

新 _post_merge_alias_dedup 在 _merge_graphs 末尾扫描所有 PERSON,根据 3 个条件合并:
  ① 一个 name 是另一个 name 的真子串(中文 2+ 字 ↔ 全名)
  ② 一个 name 在另一条的 aliases 里
  ③ 两条 aliases 集合有交集
"""
from __future__ import annotations


def test_substring_name_merged():
    """场景:chunk A name='绿子',chunk B name='小林绿子'(子串关系)→ 合并。"""
    from app.services.llm_extract import _merge_graphs

    graphs = [
        {
            "entities": [
                {"name": "绿子", "type": "PERSON", "aliases": [], "description": "戏剧史专业学生"},
                {"name": "渡边", "type": "PERSON", "aliases": [], "description": "主角"},
            ],
            "relations": [
                {"source": "渡边", "target": "绿子", "type": "朋友", "strength": "strong", "description": ""},
            ],
            "meta": {"narrative_pov": "first"},
        },
        {
            "entities": [
                {"name": "小林绿子", "type": "PERSON", "aliases": [],
                 "description": "家里经营小林书店,姐姐叫小林桃子"},
                {"name": "渡边", "type": "PERSON", "aliases": [], "description": "主角"},
            ],
            "relations": [
                {"source": "渡边", "target": "小林绿子", "type": "朋友", "strength": "moderately_strong", "description": ""},
            ],
            "meta": {"narrative_pov": "first"},
        },
    ]
    merged = _merge_graphs(graphs)
    person_names = {
        e["name"] for e in merged["entities"]
        if e.get("type") == "PERSON"
    }
    # 只能存在 1 个绿子(合并后),不能既有"绿子"又有"小林绿子"
    assert "绿子" in person_names or "小林绿子" in person_names
    assert not ("绿子" in person_names and "小林绿子" in person_names)
    # 找到合并后的实体,确认另一个 name 进了 aliases
    merged_lvzi = next(
        e for e in merged["entities"]
        if e.get("type") == "PERSON" and e["name"] in {"绿子", "小林绿子"}
    )
    aliases = merged_lvzi.get("aliases", [])
    if merged_lvzi["name"] == "绿子":
        assert "小林绿子" in aliases
    else:
        assert "绿子" in aliases


def test_alias_overlap_merged():
    """场景:chunk A name='驹子' aliases=['艺妓'],chunk B name='女艺人' aliases=['艺妓']
       → aliases 交集"艺妓"触发合并。"""
    from app.services.llm_extract import _merge_graphs

    graphs = [
        {
            "entities": [
                {"name": "驹子", "type": "PERSON", "aliases": ["艺妓"], "description": "本名场景描述"},
                {"name": "岛村", "type": "PERSON", "aliases": [], "description": "主角"},
            ],
            "relations": [],
            "meta": {"narrative_pov": "third"},
        },
        {
            "entities": [
                {"name": "女艺人", "type": "PERSON", "aliases": ["艺妓"], "description": "雪国温泉的女艺人"},
                {"name": "岛村", "type": "PERSON", "aliases": [], "description": "主角"},
            ],
            "relations": [],
            "meta": {"narrative_pov": "third"},
        },
    ]
    merged = _merge_graphs(graphs)
    person_names = {e["name"] for e in merged["entities"] if e.get("type") == "PERSON"}
    # 驹子 和 女艺人 不能同时存在
    assert not ("驹子" in person_names and "女艺人" in person_names)


def test_name_in_aliases_merged():
    """P0I.4(2026-05-24)改造:PERSON name 互斥防御。
    原行为:chunk A entity 的 aliases 含 chunk B 的 name → 合并为 1 个。
    新行为:两个 PERSON name 同时存在(无论 aliases 关系)→ 不合并,
            保留 2 个独立 PERSON(防 LLM 把另一人 name 错塞进当前人 aliases)。
    """
    from app.services.llm_extract import _merge_graphs

    graphs = [
        {
            "entities": [
                {"name": "李寻欢", "type": "PERSON", "aliases": ["探花郎"], "description": "古龙小说主角"},
            ],
            "relations": [],
            "meta": {},
        },
        {
            "entities": [
                {"name": "探花郎", "type": "PERSON", "aliases": [], "description": "另一 chunk 用别名"},
            ],
            "relations": [],
            "meta": {},
        },
    ]
    merged = _merge_graphs(graphs)
    person_names = {e["name"] for e in merged["entities"] if e.get("type") == "PERSON"}
    # P0I.4:两 PERSON 都保留(name 互斥防御)
    assert len(person_names) == 2
    assert "李寻欢" in person_names
    assert "探花郎" in person_names


def test_relations_reassigned_after_alias_merge():
    """合并"绿子"和"小林绿子"后,指向"小林绿子"的关系应 reassign 到"绿子"(或反之)。"""
    from app.services.llm_extract import _merge_graphs

    graphs = [
        {
            "entities": [
                {"name": "绿子", "type": "PERSON", "aliases": [], "description": "戏剧史专业学生"},
                {"name": "渡边", "type": "PERSON", "aliases": [], "description": ""},
            ],
            "relations": [
                {"source": "渡边", "target": "绿子", "type": "朋友", "strength": "strong", "description": ""},
            ],
            "meta": {},
        },
        {
            "entities": [
                {"name": "小林绿子", "type": "PERSON", "aliases": [],
                 "description": "家里经营小林书店"},
                {"name": "渡边", "type": "PERSON", "aliases": [], "description": ""},
            ],
            "relations": [
                {"source": "渡边", "target": "小林绿子", "type": "情侣", "strength": "moderately_strong", "description": ""},
            ],
            "meta": {},
        },
    ]
    merged = _merge_graphs(graphs)
    # 找出合并后的绿子 name
    person_names = {e["name"] for e in merged["entities"] if e.get("type") == "PERSON"}
    final_lvzi_name = next(
        n for n in person_names if n in {"绿子", "小林绿子"}
    )
    # relations 里不该再出现被合并掉的那个 name
    for r in merged["relations"]:
        assert r["source"] != ("小林绿子" if final_lvzi_name == "绿子" else "绿子")
        assert r["target"] != ("小林绿子" if final_lvzi_name == "绿子" else "绿子")


def test_short_single_char_name_not_merged_to_longer():
    """单字 name "我" 不该被合并到任意带"我"的全名(避免子串误判)。"""
    from app.services.llm_extract import _merge_graphs

    graphs = [
        {
            "entities": [
                {"name": "我", "type": "PERSON", "aliases": [], "description": "第一人称叙述者"},
                {"name": "自我意识", "type": "PERSON", "aliases": [], "description": "(虚构) 一个 NPC 名字含'我'字"},
            ],
            "relations": [],
            "meta": {},
        },
    ]
    merged = _merge_graphs(graphs)
    person_names = {e["name"] for e in merged["entities"] if e.get("type") == "PERSON"}
    # 单字 name "我" 长度 < 2,不会触发子串合并 → 两个仍独立
    assert "我" in person_names
    assert "自我意识" in person_names


def test_non_person_entities_not_merged():
    """LOCATION / OBJECT / EVENT 实体不参与跨 chunk 归一(场景重名应该已被 chunk 内 dedup)。"""
    from app.services.llm_extract import _merge_graphs

    graphs = [
        {
            "entities": [
                {"name": "京都", "type": "LOCATION", "aliases": [], "description": "城市"},
                {"name": "京都大学", "type": "LOCATION", "aliases": [], "description": "学校"},
            ],
            "relations": [],
            "meta": {},
        },
    ]
    merged = _merge_graphs(graphs)
    loc_names = {e["name"] for e in merged["entities"] if e.get("type") == "LOCATION"}
    # "京都"是"京都大学"子串,但是 LOCATION 不归一 → 两个都保留
    assert "京都" in loc_names
    assert "京都大学" in loc_names


def test_no_dedup_when_only_one_person():
    """单角色或 0 角色场景 → 跳过 dedup,直接返回。"""
    from app.services.llm_extract import _merge_graphs

    graphs = [
        {
            "entities": [
                {"name": "渡边", "type": "PERSON", "aliases": [], "description": ""},
            ],
            "relations": [],
            "meta": {},
        },
    ]
    merged = _merge_graphs(graphs)
    assert len(merged["entities"]) == 1
    assert merged["entities"][0]["name"] == "渡边"
