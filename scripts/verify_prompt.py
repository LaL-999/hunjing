"""
verify_prompt.py — 真实 LLM 验证:跑 prompts/character_focus.md v1 的 4 个端到端场景

【目标】
  把 docs/MVP阶段1_角色对焦组件设计.md §12 中可由 LLM 验证的 4 个场景(A/B/C/D)
  逐个真实调用 DeepSeek,自动检查输出是否满足 prompt 的 8 条铁律,以及场景特定
  期望(如场景 C/D 必出 consistency_警告)。E/F/G 是异常路径或后端状态机,不在
  prompt 验证范围。

【架构】
  4 个场景 → 各调 1 次 DeepSeek → 自动 verdict + 人工虚构词高亮 → 落盘报告

【依赖】 openai >= 1.30,.env 中 OPENAI_API_KEY / OPENAI_API_BASE / LLM_MODEL

【使用】
  python scripts/verify_prompt.py --dry-run   # 只打印 prompt + 场景 + 预算估算
  python scripts/verify_prompt.py             # 真调 4 次 LLM(预算 ~¥0.05)
  python scripts/verify_prompt.py --verbose   # 跑完后展开每条建议全文

【输出】
  data/prompt_verifications/character_focus_v1_<ts>/
    summary.md / summary.json   人类可读 + 机器可读总报告
    scenario_a/ ... scenario_d/  每场景的 input.json / output_raw.txt /
                                  output_parsed.json / verdict.json

【相关文档】
  prompts/character_focus.md                            被验证的 prompt v1
  docs/MVP阶段1_角色对焦组件设计.md  §12 测试场景        7 个测试场景的设计
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Windows 控制台默认 GBK,中文输出会乱码 → 强制 utf-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---------- 路径(对齐 simulate.py) ----------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"
VERIFY_DIR = PROJECT_ROOT / "data" / "prompt_verifications"

PROMPT_FILE = "character_focus.md"


# ---------- env 加载(对齐 simulate.py) ----------

def load_env():
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_config():
    return {
        "api_key": os.getenv("OPENAI_API_KEY"),
        "api_base": os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1"),
        "llm_model": os.getenv("LLM_MODEL", "deepseek-chat"),
    }


def estimate_cost(in_tokens: int, out_tokens: int, model_name: str) -> float:
    pricing = {
        "deepseek-chat": (0.0010, 0.0020),
        "qwen-plus":     (0.0040, 0.0120),
        "qwen-turbo":    (0.0003, 0.0006),
        "gpt-4o-mini":   (0.0011, 0.0043),
    }
    in_p, out_p = pricing.get(model_name.lower(), (0.005, 0.015))
    return (in_tokens * in_p + out_tokens * out_p) / 1000


def strip_markdown_fence(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json|JSON|markdown|md)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


# ---------- 测试场景(对应设计文档 §12,A/B/C/D 四个 LLM 可验证场景) ----------

@dataclass
class Scenario:
    """一个测试场景的完整定义。

    业务字段(进入 prompt 的输入):project, characters, relationships
    验证字段(以下划线开头,不进 prompt,只供 verdict 用):
      - _name: 场景名(用于报告)
      - _description: 场景目的
      - _expect_warning_for: 期望必出 consistency_警告 的 character_id 列表
      - _suspicious_words: 出现即视为可能的训练数据虚构(铁律 1)
    """
    _name: str
    _description: str
    project: dict
    characters: list[dict]
    relationships: list[dict] = field(default_factory=list)
    _expect_warning_for: list[str] = field(default_factory=list)
    _suspicious_words: list[str] = field(default_factory=list)

    def to_prompt_input(self) -> dict:
        """剥掉验证字段,构造给 LLM 的纯输入。"""
        return {
            "project": self.project,
            "characters": self.characters,
            "relationships": self.relationships,
        }


SCENARIOS: list[Scenario] = [
    # ===== 场景 A:稀疏角色,只填了名字 =====
    # 关键验证:LLM 不能假设"李寻欢"是古龙的小李飞刀。题材是武侠 + 悬疑,可以推演,
    # 但绝不能直接搬古龙原作元素(飞刀、林诗音、阿飞等)。
    Scenario(
        _name="A_稀疏角色",
        _description="用户只填了 3 个角色名,其他全空。验证 LLM 能否克制住基于"
                      "训练数据虚构的冲动,只基于题材标签做合理推演。",
        project={"name": "江湖夜雨", "type": "novel", "tags": ["武侠", "悬疑"]},
        characters=[
            {"id": "c1", "name": "李寻欢", "identity": "", "personality": "",
             "quotes": [], "no_go_list": []},
            {"id": "c2", "name": "孙小红", "identity": "", "personality": "",
             "quotes": [], "no_go_list": []},
            {"id": "c3", "name": "上官金虹", "identity": "", "personality": "",
             "quotes": [], "no_go_list": []},
        ],
        relationships=[],
        _expect_warning_for=[],   # 信息不足,不应出警告
        _suspicious_words=["飞刀", "林诗音", "阿飞", "金钱帮", "百晓生", "兵器谱",
                           "小李飞刀", "古龙"],
    ),

    # ===== 场景 B:饱满角色,字段都填了 =====
    # 关键验证:角色已较完整,建议数应少而精;可能出现 personality 精炼或细节
    # consistency 提示。允许出 1-2 条 warning,但不应"为了凑数"出大量补充建议。
    Scenario(
        _name="B_饱满角色",
        _description="3 个角色都填了完整 identity + personality + ≥2 quotes + ≥2 雷区"
                     "。LLM 应该出极少建议(主要是细节精炼或 consistency)。",
        project={"name": "云端之上", "type": "novel", "tags": ["科幻", "悬疑"]},
        characters=[
            {
                "id": "c1", "name": "陈默",
                "identity": "代号'听者'的密码学家,曾就职于国安局,后退役成为独立调查员",
                "personality": "极度内向但善于倾听,对数字密码有近乎偏执的敏感,"
                                "情感上迟钝但对正义有执念",
                "quotes": [
                    "数字不会说谎,只有人会",
                    "我听过太多人的秘密",
                    "等我把这个序列解出来,你就知道答案",
                ],
                "no_go_list": [
                    "绝不在公共场合使用真实身份",
                    "绝不接受未加密的通讯",
                    "绝不与同行公开合作",
                ],
            },
            {
                "id": "c2", "name": "林夏",
                "identity": "新生代调查记者,擅长社会工程学",
                "personality": "外向健谈,反应敏捷,对一切阴谋论保持警惕但又被它们吸引",
                "quotes": ["真相藏在细节里", "我就不信问不出来"],
                "no_go_list": ["绝不放弃一个故事", "绝不为权贵让步"],
            },
            {
                "id": "c3", "name": "苏教授",
                "identity": "应用数学系退休教授,陈默的导师",
                "personality": "睿智但喜欢用反问引导学生,从不直接给答案",
                "quotes": ["你觉得呢?", "数学告诉你了,你还不信?"],
                "no_go_list": ["绝不直接给答案", "绝不评价学生的私人感情"],
            },
        ],
        relationships=[
            {"source_id": "c1", "target_id": "c3", "type": "师徒",
             "description": "陈默是苏教授最得意的学生,亦师亦友"},
            {"source_id": "c1", "target_id": "c2", "type": "同事",
             "description": "正在合作调查同一桩案件,关系微妙"},
        ],
        _expect_warning_for=[],   # 不强制要警告(角色都很自洽)
        _suspicious_words=[],
    ),

    # ===== 场景 C:角色内部矛盾(personality vs quotes) =====
    # 关键验证:必出 consistency_警告 for c1。
    Scenario(
        _name="C_内部矛盾",
        _description="c1 的 personality 写'内向害羞从不主动开口',但 quotes 里两句"
                     "都是大喊型语气。LLM 必须出 consistency_警告 指出冲突。",
        project={"name": "演说家与影子", "type": "novel", "tags": ["现代", "心理"]},
        characters=[
            {
                "id": "c1", "name": "甲",
                "identity": "城市边缘的小镇青年",
                "personality": "内向害羞,从不主动开口,在人群中总是低着头",
                "quotes": [
                    "我要成为最伟大的演说家!",
                    "听好了,这是我对你的最后通牒",
                ],
                "no_go_list": [],
            },
            {"id": "c2", "name": "乙", "identity": "甲的发小", "personality": "活泼开朗",
             "quotes": [], "no_go_list": []},
            {"id": "c3", "name": "丙", "identity": "心理咨询师", "personality": "沉稳专业",
             "quotes": [], "no_go_list": []},
        ],
        relationships=[],
        _expect_warning_for=["c1"],
        _suspicious_words=[],
    ),

    # ===== 场景 D:关系网矛盾(师父关系 vs 孩子气性格) =====
    # 关键验证:必出 consistency_警告 for c1(玄风)。
    Scenario(
        _name="D_关系网矛盾",
        _description="玄风是周渡的师父,但 personality 写'急躁孩子气一遇事就拍桌子大喊'。"
                     "LLM 必须察觉师父身份与性格的不协调。",
        project={"name": "山门内外", "type": "novel", "tags": ["武侠", "成长"]},
        characters=[
            {
                "id": "c1", "name": "玄风",
                "identity": "青云宗执剑长老",
                "personality": "急躁孩子气,一遇事就拍桌子大喊,做决定全凭直觉",
                "quotes": [], "no_go_list": [],
            },
            {
                "id": "c2", "name": "周渡",
                "identity": "青云宗外门弟子,刚入山三个月",
                "personality": "认真好学,但天赋平庸",
                "quotes": [], "no_go_list": [],
            },
            {"id": "c3", "name": "苏婉", "identity": "周渡的师姐",
             "personality": "心思细密", "quotes": [], "no_go_list": []},
        ],
        relationships=[
            {"source_id": "c1", "target_id": "c2", "type": "师徒",
             "description": "玄风是周渡的师父,亲自带"},
            {"source_id": "c3", "target_id": "c2", "type": "师徒",
             "description": "苏婉作为师姐照顾周渡"},
        ],
        _expect_warning_for=["c1"],
        _suspicious_words=[],
    ),
]


# ---------- LLM 调用 ----------

def call_llm(system_prompt: str, user_input: dict, cfg: dict) -> tuple[str, dict]:
    """调用 DeepSeek,返回 (raw_text, usage_dict)。"""
    from openai import OpenAI

    client = OpenAI(
        api_key=cfg["api_key"],
        base_url=cfg["api_base"],
        timeout=60.0,
    )

    user_content = json.dumps(user_input, ensure_ascii=False, indent=2)

    resp = client.chat.completions.create(
        model=cfg["llm_model"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ],
        max_tokens=4000,
        temperature=0.6,
    )

    raw = resp.choices[0].message.content or ""
    usage = {
        "input_tokens": resp.usage.prompt_tokens if resp.usage else 0,
        "output_tokens": resp.usage.completion_tokens if resp.usage else 0,
    }
    return raw, usage


# ---------- 自动 verdict ----------

VALID_KINDS = {
    "identity_补全",
    "personality_补充",
    "quote_补充",
    "no_go_补充",
    "consistency_警告",
}


def verify_output(scenario: Scenario, raw_output: str) -> dict:
    """对一次 LLM 输出做完整 verdict。返回 {pass, issues, warnings, stats, parsed}。

    - issues = 硬错误(违反铁律 / 格式错 / 期望未满足),pass=False
    - warnings = 软问题(可疑虚构词等),pass 仍然 True
    - parsed = 解析后的 list,失败时为 None
    """
    issues: list[str] = []
    warnings: list[str] = []
    stats: dict[str, Any] = {}

    # ---------- 1. JSON 解析(铁律 8) ----------
    cleaned = strip_markdown_fence(raw_output)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        issues.append(f"[铁律 8] JSON 解析失败:{e}")
        if "```" in raw_output:
            issues.append("[铁律 8] 输出被 ``` 包裹,违反'不要 markdown 包裹'")
        return {"pass": False, "issues": issues, "warnings": warnings,
                "stats": stats, "parsed": None}

    if not isinstance(data, list):
        issues.append(f"[铁律 8] 顶层不是数组,实际类型 {type(data).__name__};"
                      f"输出可能被 {{refinements: [...]}} 包装层包裹")
        return {"pass": False, "issues": issues, "warnings": warnings,
                "stats": stats, "parsed": None}

    stats["total_count"] = len(data)

    # ---------- 2. 数量上限(铁律 4) ----------
    if len(data) > 25:
        issues.append(f"[铁律 4] 总数 {len(data)} > 25")

    # ---------- 3. 每条结构 + 字段验证 ----------
    by_char: dict[str, int] = {}
    by_kind: dict[str, int] = {k: 0 for k in VALID_KINDS}
    char_lookup = {c["id"]: c for c in scenario.characters}

    for i, item in enumerate(data):
        if not isinstance(item, dict):
            issues.append(f"[结构] 第 {i} 条不是 dict,实际 {type(item).__name__}")
            continue

        # 必备字段
        for required in ("character_id", "suggestion_kind",
                         "suggestion_text", "suggestion_payload"):
            if required not in item:
                issues.append(f"[结构] 第 {i} 条缺少字段:{required}")

        # kind 合法性
        kind = item.get("suggestion_kind")
        if kind not in VALID_KINDS:
            issues.append(f"[结构] 第 {i} 条 suggestion_kind 非法:{kind!r}")
        else:
            by_kind[kind] += 1

        # character_id 合法性
        cid = item.get("character_id")
        if cid not in char_lookup:
            issues.append(f"[结构] 第 {i} 条 character_id={cid!r} 不在输入角色列表中")
            continue
        by_char[cid] = by_char.get(cid, 0) + 1

        # suggestion_text ≤ 70 字(v2:从 60 放宽到 70,因为模板天然就接近 60 字)
        text = item.get("suggestion_text", "")
        if len(text) > 70:
            issues.append(f"[长度] 第 {i} 条 suggestion_text {len(text)} 字 > 70:"
                          f"{text[:30]}...")

        # 不重复用户已填(铁律 3)
        payload = item.get("suggestion_payload", {})
        if isinstance(payload, dict):
            field_name = payload.get("field")
            char = char_lookup[cid]
            if "append" in payload and field_name:
                appended = payload["append"]
                existing = char.get(field_name) or ""
                if isinstance(existing, list):
                    existing_str = " | ".join(str(x) for x in existing)
                else:
                    existing_str = str(existing)
                appended_items = appended if isinstance(appended, list) else [appended]
                for a in appended_items:
                    a_str = str(a)
                    if a_str and existing_str and a_str in existing_str:
                        issues.append(f"[铁律 3] 第 {i} 条 ({field_name}) "
                                       f"建议追加'{a_str[:20]}'与已填内容重叠")

            # 铁律 5(完整版,v2 verify 后强化):
            # - 非 identity 字段绝不允许 value(只能用 append)
            # - identity 字段允许 value,但仅当输入 identity 为空字符串时
            #   (v2 verify 暴露:LLM 会截取局部判字数,把"调查记者"4 字误判为不足
            #    而用 value 覆盖了"新生代调查记者,擅长社会工程学"这种已填内容)
            if "value" in payload and field_name:
                if field_name != "identity":
                    issues.append(f"[铁律 5] 第 {i} 条 ({field_name}) 使用 value,"
                                  f"但 field 非 identity(只允许 identity 用 value)")
                else:
                    source_identity = char.get("identity", "")
                    if source_identity and str(source_identity).strip():
                        issues.append(f"[铁律 5] 第 {i} 条 (identity) value 覆盖了用户已填,"
                                      f"输入 identity={str(source_identity)[:20]!r} 非空;"
                                      f"应改用 consistency_警告 或不出此建议")

    # 单角色 ≤ 5(铁律 4)
    for cid, count in by_char.items():
        if count > 5:
            issues.append(f"[铁律 4] 角色 {cid} 有 {count} 条建议 > 5")

    stats["by_character"] = by_char
    stats["by_kind"] = by_kind

    # ---------- 4. 场景特定:必出 consistency_警告 ----------
    actual_warning_chars = {item.get("character_id") for item in data
                             if isinstance(item, dict)
                             and item.get("suggestion_kind") == "consistency_警告"}
    for required in scenario._expect_warning_for:
        if required not in actual_warning_chars:
            issues.append(f"[场景期望] 角色 {required} 应出 consistency_警告,但未出")

    # ---------- 5. 虚构词高亮(铁律 1,人工 review) ----------
    if scenario._suspicious_words:
        full_text = json.dumps(data, ensure_ascii=False)
        for word in scenario._suspicious_words:
            if word in full_text:
                # 这是软警告而非硬 fail,因为 LLM 可能在合理上下文出现该词
                warnings.append(f"[铁律 1 ⚠] 输出含可疑词 '{word}' "
                                 f"(可能基于训练数据虚构,需人工确认)")

    # ---------- 6. suggestion_text 不带 AI 自指(铁律 7) ----------
    self_ref_patterns = ["作为AI", "作为 AI", "作为助手", "作为浑晶", "我建议你",
                          "AI 建议", "AI建议"]
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        text = item.get("suggestion_text", "")
        for pat in self_ref_patterns:
            if pat in text:
                warnings.append(f"[铁律 7] 第 {i} 条含 AI 自指模式 '{pat}':{text[:30]}")

    pass_ = len(issues) == 0
    return {"pass": pass_, "issues": issues, "warnings": warnings,
            "stats": stats, "parsed": data}


# ---------- 报告输出 ----------

def format_console_summary(results: list[dict], cfg: dict) -> str:
    lines = []
    lines.append("=" * 64)
    lines.append(f"Prompt Verification: {PROMPT_FILE}")
    lines.append(f"Model: {cfg['llm_model']}  |  API: {cfg['api_base']}")
    lines.append("=" * 64)

    total_cost = 0.0
    pass_count = 0
    for r in results:
        s = r["scenario"]
        v = r["verdict"]
        u = r["usage"]
        cost = r["cost_yuan"]
        total_cost += cost

        status = "PASS" if v["pass"] else "FAIL"
        if v["pass"]:
            pass_count += 1

        lines.append("")
        lines.append(f"[{status}] {s._name}  ({s._description[:40]}...)")
        lines.append(f"  ▸ Tokens: {u['input_tokens']} in / {u['output_tokens']} out"
                      f"  Cost: ¥{cost:.4f}")

        if v["parsed"] is not None:
            stats = v["stats"]
            kind_str = ", ".join(f"{k}={v_}" for k, v_ in stats["by_kind"].items() if v_ > 0)
            lines.append(f"  ▸ Suggestions: {stats['total_count']}  ({kind_str})")
            lines.append(f"  ▸ By character: {stats['by_character']}")

        if v["issues"]:
            lines.append(f"  ▸ Issues ({len(v['issues'])}):")
            for issue in v["issues"]:
                lines.append(f"      ✕ {issue}")

        if v["warnings"]:
            lines.append(f"  ▸ Warnings ({len(v['warnings'])}):")
            for w in v["warnings"]:
                lines.append(f"      ⚠ {w}")

    lines.append("")
    lines.append("=" * 64)
    lines.append(f"Pass rate:    {pass_count}/{len(results)}")
    lines.append(f"Total cost:   ¥{total_cost:.4f}")
    lines.append("=" * 64)
    return "\n".join(lines)


def write_scenario_report(out_dir: Path, scenario: Scenario, result: dict):
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "input.json").write_text(
        json.dumps(scenario.to_prompt_input(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (out_dir / "output_raw.txt").write_text(
        result["raw_output"], encoding="utf-8",
    )
    if result["verdict"]["parsed"] is not None:
        (out_dir / "output_parsed.json").write_text(
            json.dumps(result["verdict"]["parsed"], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    verdict_dump = {
        "pass": result["verdict"]["pass"],
        "issues": result["verdict"]["issues"],
        "warnings": result["verdict"]["warnings"],
        "stats": result["verdict"]["stats"],
        "usage": result["usage"],
        "cost_yuan": result["cost_yuan"],
        "duration_sec": result["duration_sec"],
    }
    (out_dir / "verdict.json").write_text(
        json.dumps(verdict_dump, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_summary(session_dir: Path, results: list[dict], cfg: dict, console_text: str):
    summary_md = session_dir / "summary.md"
    summary_md.write_text(console_text, encoding="utf-8")

    summary_json = session_dir / "summary.json"
    summary_data = {
        "prompt_file": PROMPT_FILE,
        "model": cfg["llm_model"],
        "api_base": cfg["api_base"],
        "timestamp": datetime.now().isoformat(),
        "scenarios": [
            {
                "name": r["scenario"]._name,
                "pass": r["verdict"]["pass"],
                "issues_count": len(r["verdict"]["issues"]),
                "warnings_count": len(r["verdict"]["warnings"]),
                "issues": r["verdict"]["issues"],
                "warnings": r["verdict"]["warnings"],
                "stats": r["verdict"]["stats"],
                "usage": r["usage"],
                "cost_yuan": r["cost_yuan"],
            }
            for r in results
        ],
        "total_cost_yuan": sum(r["cost_yuan"] for r in results),
        "pass_rate": f"{sum(1 for r in results if r['verdict']['pass'])}/{len(results)}",
    }
    summary_json.write_text(
        json.dumps(summary_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ---------- dry-run + 主流程 ----------

def do_dry_run(prompt: str, cfg: dict):
    print("=" * 64)
    print(f"DRY RUN: {PROMPT_FILE}")
    print("=" * 64)
    print()
    print(f"模型: {cfg['llm_model']}  |  API: {cfg['api_base']}")
    print(f"API key 是否就绪: {'是' if cfg['api_key'] else '否(检查 .env)'}")
    print()
    print(f"Prompt 长度: {len(prompt)} 字符")
    print(f"测试场景数: {len(SCENARIOS)}")
    print()
    for i, s in enumerate(SCENARIOS, 1):
        user_content = json.dumps(s.to_prompt_input(), ensure_ascii=False, indent=2)
        # 粗略估 token: 中文 1 字 ≈ 1.3 token, 英文/标点按字符 ≈ 0.3 token
        # 这里简单按 1.0 字符 = 0.6 token 估,够用
        approx_input = int((len(prompt) + len(user_content)) * 0.6)
        approx_output = 1500  # 平均预估
        approx_cost = estimate_cost(approx_input, approx_output, cfg["llm_model"])
        print(f"  场景 {i} {s._name}:")
        print(f"    描述: {s._description}")
        print(f"    输入 chars: prompt={len(prompt)}, scenario={len(user_content)}")
        print(f"    估 tokens: input ~{approx_input}, output ~{approx_output}")
        print(f"    估成本:   ¥{approx_cost:.4f}")
        print(f"    期望必出 warning: {s._expect_warning_for or '无'}")
        if s._suspicious_words:
            print(f"    虚构词哨兵: {s._suspicious_words}")
        print()

    total_input_est = int(sum(
        (len(prompt) + len(json.dumps(s.to_prompt_input(), ensure_ascii=False)))
        for s in SCENARIOS
    ) * 0.6)
    total_output_est = 1500 * len(SCENARIOS)
    total_cost_est = estimate_cost(total_input_est, total_output_est, cfg["llm_model"])
    print(f"全部 4 场景预算: ~¥{total_cost_est:.4f}")
    print("加 --no-dry-run(或去掉 --dry-run)即可真跑。")


def main():
    parser = argparse.ArgumentParser(
        description="真实 LLM 验证 character_focus.md prompt"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印预算,不真调 LLM")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="跑完后展开每条建议全文")
    parser.add_argument("--scenario", choices=[s._name for s in SCENARIOS] + ["all"],
                        default="all", help="只跑指定场景(默认全部)")
    args = parser.parse_args()

    load_env()
    cfg = get_config()
    prompt = load_prompt(PROMPT_FILE)

    if args.dry_run:
        do_dry_run(prompt, cfg)
        return 0

    if not cfg["api_key"]:
        print("ERROR: OPENAI_API_KEY 未设置,请配 .env", file=sys.stderr)
        return 1

    # ASCII 校验 api_key(避免之前踩过的中文占位符坑)
    try:
        cfg["api_key"].encode("ascii")
    except UnicodeEncodeError:
        print("ERROR: OPENAI_API_KEY 含非 ASCII 字符(可能是占位符),"
              "请检查 .env", file=sys.stderr)
        return 1

    selected = SCENARIOS if args.scenario == "all" \
        else [s for s in SCENARIOS if s._name == args.scenario]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = VERIFY_DIR / f"character_focus_v1_{timestamp}"
    session_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for i, scenario in enumerate(selected, 1):
        print(f"[{i}/{len(selected)}] 跑场景 {scenario._name} ...", flush=True)
        t0 = time.time()
        try:
            raw, usage = call_llm(prompt, scenario.to_prompt_input(), cfg)
        except Exception as e:
            print(f"  调用失败:{type(e).__name__}: {e}", file=sys.stderr)
            results.append({
                "scenario": scenario,
                "raw_output": "",
                "usage": {"input_tokens": 0, "output_tokens": 0},
                "cost_yuan": 0.0,
                "duration_sec": time.time() - t0,
                "verdict": {
                    "pass": False,
                    "issues": [f"LLM 调用异常:{type(e).__name__}: {e}"],
                    "warnings": [],
                    "stats": {},
                    "parsed": None,
                },
            })
            continue

        cost = estimate_cost(usage["input_tokens"], usage["output_tokens"], cfg["llm_model"])
        verdict = verify_output(scenario, raw)
        elapsed = time.time() - t0
        result = {
            "scenario": scenario,
            "raw_output": raw,
            "usage": usage,
            "cost_yuan": cost,
            "duration_sec": elapsed,
            "verdict": verdict,
        }
        results.append(result)

        scenario_dir = session_dir / scenario._name.lower().replace("_", "_")
        write_scenario_report(scenario_dir, scenario, result)

        status = "PASS" if verdict["pass"] else "FAIL"
        print(f"  {status}  耗时 {elapsed:.1f}s  成本 ¥{cost:.4f}  "
              f"issues={len(verdict['issues'])}  warnings={len(verdict['warnings'])}",
              flush=True)

    # 汇总
    console_text = format_console_summary(results, cfg)
    print()
    print(console_text)
    write_summary(session_dir, results, cfg, console_text)
    print()
    print(f"详细报告: {session_dir}")

    if args.verbose:
        print()
        print("=" * 64)
        print("详细 LLM 输出(每条建议)")
        print("=" * 64)
        for r in results:
            print()
            print(f"--- {r['scenario']._name} ---")
            parsed = r["verdict"].get("parsed")
            if parsed is None:
                print("  (输出无法解析为 JSON,见 raw_output)")
                continue
            for i, item in enumerate(parsed, 1):
                print(f"  {i:2d}. [{item.get('suggestion_kind')}] "
                      f"-> {item.get('character_id')}")
                print(f"      {item.get('suggestion_text')}")

    # 退出码:任意场景失败返回 1
    return 0 if all(r["verdict"]["pass"] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
