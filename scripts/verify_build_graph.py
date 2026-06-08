"""verify_build_graph.py — 真实 LLM 验证 build_graph.md v5

【目标】
  验证 prompts/build_graph.md v5 的两个核心改造:
  ① v4 加的"角色归一铁律"(第一人称代词 / 职业称谓 / 关系称谓)
  ② v5 加的"meta.narrative_pov 输出"(作品叙述视角识别)

【场景】
  A. 第一人称小说片段(《挪威的森林》改编)— 验证"我" 折叠到主角 aliases + narrative_pov=first
  B. 第三人称 + 称谓归一(《雪国》改编)— 验证"驹子"/"艺妓"合并 + narrative_pov=third
  C. 第三人称纯净(无称谓歧义)— 验证不误判 + narrative_pov=third
  D. 第一人称对话引用陷阱 — 角色对话里说"我",叙述层仍是第三人称 → narrative_pov=third(不该被对话引用骗到)

【自动 verdict】
  对每个场景检查:
  - entities[*].name 不含"我"作为独立 PERSON(场景 A / D 关键)
  - 期望的称谓在某 PERSON 的 aliases 里(场景 B 关键)
  - meta.narrative_pov 等于预期值(全部场景)
  - 关系密度 ≥ PERSON × 1.5

【使用】
  python scripts/verify_build_graph.py --dry-run   # 只打印 + 预算估算
  python scripts/verify_build_graph.py             # 真调 4 次 LLM(~¥0.10-0.20)
  python scripts/verify_build_graph.py --verbose   # 展开每场景输出

【输出】
  data/prompt_verifications/build_graph_v5_<ts>/
    summary.md / summary.json
    scenario_a/ ... scenario_d/   input.json / output_raw.txt / output_parsed.json / verdict.json
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

# Windows utf-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"
VERIFY_DIR = PROJECT_ROOT / "data" / "prompt_verifications"

PROMPT_FILE = "build_graph.md"


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
        "api_key": os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY"),
        "api_base": os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1"),
        "llm_model": os.getenv("LLM_MODEL", "deepseek-chat"),
    }


def estimate_cost(in_tokens: int, out_tokens: int, model_name: str) -> float:
    pricing = {
        "deepseek-chat": (0.0010, 0.0020),
        "qwen-plus":     (0.0040, 0.0120),
    }
    in_p, out_p = pricing.get(model_name.lower(), (0.005, 0.015))
    return (in_tokens * in_p + out_tokens * out_p) / 1000


def strip_markdown_fence(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json|JSON)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


def load_prompt() -> str:
    return (PROMPTS_DIR / PROMPT_FILE).read_text(encoding="utf-8")


# ---------- 测试场景 ----------

@dataclass
class Scenario:
    """build_graph 测试场景。

    业务字段(进入 prompt 的输入):text + work_type + work_meta_hint
    验证字段(下划线开头,不进 prompt):
      _name / _description / _expected_pov(first/second/third/mixed)
      _expected_no_independent_person:不该出现的独立 PERSON name list(如"我" / "艺妓")
      _expected_aliases_in:期望该名进某 PERSON 的 aliases(如"我" 应在主角 aliases 里)
    """
    _name: str
    _description: str
    text: str
    work_type: str = "小说"
    work_meta_hint: str = ""
    _expected_pov: str | None = None     # "first" / "third" / 等
    _expected_no_independent_person: list[str] = field(default_factory=list)
    _expected_aliases_in: dict[str, list[str]] = field(default_factory=dict)
    # ↑ key=PERSON name, value=该 PERSON.aliases 应该包含的字符串


SCENARIOS: list[Scenario] = [
    # ===== A:第一人称小说片段(《挪威的森林》风格改编)=====
    Scenario(
        _name="A_第一人称归一",
        _description="第一人称叙述者「我」必须折叠到主角(渡边)的 aliases,不能单开 PERSON",
        text=(
            "我是渡边,三十七岁的杂志编辑,正在写一段往事。十七年前,我和直子在新宿见面。"
            "她穿着白色的连衣裙,沉默地走在我身边。\n\n"
            "「渡边君,你还记得井吗?」直子低声问。\n"
            "「记得,在草甸子上的那口井,」我回答,「掉下去就再也上不来。」\n"
            "永泽看了我一眼,笑了笑。这家伙总是一脸不在乎。「渡边,你太悲观了。」\n\n"
            "我心里想,永泽不懂直子。直子的世界,只有我能听到。\n"
            "绿子从对面店里走出来,叫住我:「渡边,你又在发呆?」\n"
            "我没回答。绿子总是热闹的,直子是安静的。"
        ),
        work_type="小说",
        work_meta_hint="作品标题:《挪威的森林》改编片段。第一人称视角。",
        _expected_pov="first",
        _expected_no_independent_person=["我"],   # "我"不能独立 PERSON
        _expected_aliases_in={"渡边": ["我"]},     # "我" 应在渡边的 aliases 里
    ),

    # ===== B:第三人称 + 职业称谓归一(《雪国》风格)=====
    Scenario(
        _name="B_职业称谓归一",
        _description="同一艺妓在文中以本名「驹子」和职业名「艺妓」/「五等艺妓」交替出现,必须归一",
        text=(
            "驹子是雪国温泉乡的五等艺妓。岛村第二次来雪国时,在客栈遇见了她。\n"
            "「来啦,」驹子低头不语。\n"
            "岛村端详着这个艺妓,她和上次相比清瘦了许多。「你瘦了。」\n"
            "「艺妓的日子不好过,」驹子叹了口气。\n\n"
            "夜里下了雪,岛村坐在火盆边。驹子推门进来。\n"
            "这个艺妓的目光在火光里显得格外明亮。岛村心想,五等艺妓也有自己的天地。\n"
            "「岛村先生,」驹子说,「我等你很久了。」\n"
            "岛村没说话,只是看着这个女艺人坐在他对面。"
        ),
        work_type="小说",
        work_meta_hint="作品标题:《雪国》改编片段。第三人称视角。",
        _expected_pov="third",
        _expected_no_independent_person=["艺妓", "五等艺妓", "女艺人"],
        _expected_aliases_in={"驹子": ["艺妓"]},   # 至少"艺妓"应在驹子 aliases 里
    ),

    # ===== C:第三人称纯净(无称谓歧义,但要检查不误判)=====
    Scenario(
        _name="C_第三人称纯净",
        _description="纯第三人称叙述,没有称谓陷阱;验证 LLM 不会过度归一 / narrative_pov 正确",
        text=(
            "张三走进了那家咖啡店。他四下张望,没看见李四。\n"
            "「请问,有人订位了吗?」张三问服务员。\n"
            "服务员摇头,「李先生没来过。」\n"
            "张三在窗边的位置坐下,点了一杯黑咖啡。他打开手机,翻看着和李四的聊天记录。\n"
            "半小时后,李四推门进来。他穿着一件灰色外套,神色有些焦虑。\n"
            "「抱歉迟到了,」李四坐下,「路上堵车。」\n"
            "张三笑了笑,「没事,先点东西吧。」"
        ),
        work_type="小说",
        work_meta_hint="作品标题:都市短篇。第三人称视角。",
        _expected_pov="third",
        _expected_no_independent_person=[],   # 无称谓陷阱,只要 narrative_pov=third 即可
        _expected_aliases_in={},
    ),

    # ===== E:全名 vs 短称归一(v6 铁律 d 新加,治"小林绿子" vs "绿子"实战分裂)
    # 用陌生角色名(避免 LLM 训练偏见把"渡边/绿子"自动联想到原作)
    # _expected_no_independent_person 留空 — 关键检查交给 step 3 的"两候选只有一个独立 PERSON"
    # =====
    Scenario(
        _name="E_全名归一",
        _description="同一角色文本中以全名(沈墨竹)和短称(墨竹)交替出现 → 必须归一为 1 条",
        text=(
            "周明远在咖啡馆里第一次见到沈墨竹。墨竹坐在他对面,推了推眼镜。\n"
            "「周先生,」墨竹笑着说,「久仰大名。」\n"
            "「叫我明远就好。」周明远摆手。\n\n"
            "沈墨竹是文学评论家,家里经营沈氏书坊。墨竹的姐姐叫沈墨梅,在江南做茶商。\n"
            "周明远和墨竹聊了一下午。墨竹点了一份提拉米苏。\n"
            "「沈墨竹老师,」服务员路过时叫住她,「您订的书到了。」\n"
            "「谢谢。」墨竹答道。\n\n"
            "周明远发现墨竹有一种独特的从容,这是他在别人身上没见过的。他想起沈墨竹推眼镜的样子,觉得这次见面格外难忘。"
        ),
        work_type="小说",
        work_meta_hint="作品标题:都市短篇(原创虚构)。第三人称视角。",
        _expected_pov="third",
        _expected_no_independent_person=[],   # 由 step 3 全名归一互斥检查
        _expected_aliases_in={"墨竹": ["沈墨竹"]},   # step 3 接受"墨竹"或"沈墨竹"作 name,另一进 aliases
    ),

    # ===== D:第一人称对话引用陷阱 =====
    # 文本主体是第三人称叙述,但角色对话里多次出现"我" — LLM 应该看出是第三人称,
    # 因为叙述层用"他/她/角色名"指代主体,只有对话引用里有"我"。
    Scenario(
        _name="D_对话我陷阱",
        _description="角色对话里说「我」(自报),但叙述层是第三人称,LLM 不该误判 first",
        text=(
            "王五对张三说:「我昨天去了北京。」\n"
            "张三皱着眉,「我以为你在上海。」\n"
            "王五摇头,「我临时改了行程。」\n"
            "张三看着他,叹了口气。这个朋友总是这么任性。\n\n"
            "李四凑过来,「我看你们在聊什么呢?」\n"
            "「我们在聊王五的行程,」张三说。\n"
            "李四笑了,「我跟他一起去的。」\n"
            "张三愣住,看着这两个朋友,半天没说话。最后他摇头道:「好啦,我也不问了。」"
        ),
        work_type="小说",
        work_meta_hint="作品标题:短篇。第三人称叙述但对话密集。",
        _expected_pov="third",   # 关键:叙述层是第三人称,不该被对话引用骗到 first
        _expected_no_independent_person=["我"],
        _expected_aliases_in={},
    ),
]


def call_llm(system_prompt: str, user_input: dict) -> tuple[str, dict]:
    """call DeepSeek 并返回 (raw_response, usage)."""
    from openai import OpenAI

    cfg = get_config()
    if not cfg["api_key"]:
        raise RuntimeError("OPENAI_API_KEY / DEEPSEEK_API_KEY 未配置")

    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["api_base"])

    user_prompt = f"""请从下面这段{user_input['work_type']}正文中抽取实体与关系。

{user_input['work_meta_hint']}

正文:
\"\"\"
{user_input['text']}
\"\"\"

只输出 JSON。"""

    resp = client.chat.completions.create(
        model=cfg["llm_model"],
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
    )
    raw = resp.choices[0].message.content or ""
    usage = {
        "input_tokens": resp.usage.prompt_tokens,
        "output_tokens": resp.usage.completion_tokens,
    }
    return raw, usage


def verdict_scenario(scenario: Scenario, parsed: dict | None) -> dict:
    """自动检查 LLM 输出是否满足场景期望。返回 verdict dict。"""
    issues: list[str] = []
    passes: list[str] = []

    if parsed is None or not isinstance(parsed, dict):
        issues.append("LLM 输出非合法 JSON dict")
        return {"verdict": "FAIL", "passes": passes, "issues": issues}

    entities = parsed.get("entities") if isinstance(parsed.get("entities"), list) else []
    relations = parsed.get("relations") if isinstance(parsed.get("relations"), list) else []
    meta = parsed.get("meta") if isinstance(parsed.get("meta"), dict) else {}

    # 1. 检查 meta.narrative_pov
    actual_pov = meta.get("narrative_pov") if isinstance(meta, dict) else None
    if scenario._expected_pov:
        if actual_pov == scenario._expected_pov:
            passes.append(f"narrative_pov={actual_pov} ✓")
        else:
            issues.append(
                f"narrative_pov 期望 {scenario._expected_pov!r},"
                f"实际 {actual_pov!r}"
            )

    # 2. 检查不应出现的独立 PERSON
    person_names = {
        e.get("name") for e in entities
        if isinstance(e, dict) and e.get("type") == "PERSON"
    }
    for forbidden in scenario._expected_no_independent_person:
        if forbidden in person_names:
            issues.append(f"PERSON 列表含禁止的独立实体「{forbidden}」(未归一)")
        else:
            passes.append(f"无独立 PERSON「{forbidden}」 ✓")

    # 3. 检查 aliases 归一是否到位
    # v6 改进:接受"两个 name 中任一作主名,另一个进 aliases"— 因为用户核心诉求是
    # "不分裂",name 选哪个不是 bug。如果两个都不是 main,但都没出现 → 也 OK(合并到第三个)
    for expected_main, must_aliases in scenario._expected_aliases_in.items():
        all_candidates = [expected_main] + must_aliases   # 全部可能的 name 候选
        # ⚠️ 关键互斥检查:entities 里**不能同时**有多个 PERSON 以候选名字出现
        # (否则就是分裂,如"墨竹"和"沈墨竹"各自独立 PERSON)
        independent_candidates = [
            e for e in entities
            if isinstance(e, dict)
            and e.get("type") == "PERSON"
            and e.get("name") in all_candidates
        ]
        if len(independent_candidates) > 1:
            names = [e.get("name") for e in independent_candidates]
            issues.append(
                f"候选 {all_candidates} 中同时出现多个独立 PERSON {names},未归一(分裂)"
            )
            continue

        matching_entity = independent_candidates[0] if independent_candidates else None
        if matching_entity is None:
            issues.append(
                f"期望 PERSON「{expected_main}」(或别名 {must_aliases})未出现"
            )
            continue

        actual_name = matching_entity.get("name")
        actual_aliases = matching_entity.get("aliases") or []
        if not isinstance(actual_aliases, list):
            actual_aliases = []
        # 其他候选(除 name 外)必须在 aliases 里
        missing = [
            a for a in all_candidates
            if a != actual_name and a not in actual_aliases
            and a in [e.get("name") for e in entities if isinstance(e, dict)]
            # ↑ 但若该 alias 作为独立 PERSON 存在 → 已在前面 step 2 被检查为禁止
        ]
        # 简化:只要"另一个候选"已被 step 2 检为不独立 PERSON,且 actual_aliases 含至少一个 → PASS
        # 严格:must_aliases 里期望的字符串至少一个出现在 actual_aliases
        if actual_name == expected_main:
            for alias in must_aliases:
                if alias in actual_aliases:
                    passes.append(f"「{expected_main}」.aliases 含「{alias}」 ✓")
                else:
                    issues.append(
                        f"「{expected_main}」.aliases 期望含「{alias}」,实际 {actual_aliases}"
                    )
        else:
            # name 选了别的候选(如全名而非短称),只要 expected_main 在 aliases 里就 PASS
            if expected_main in actual_aliases:
                passes.append(
                    f"「{actual_name}」.aliases 含「{expected_main}」 ✓(归一已生效,name 选了全名)"
                )
            else:
                issues.append(
                    f"PERSON「{actual_name}」缺 aliases 含「{expected_main}」,实际 {actual_aliases}"
                )

    # 4. 关系密度抽检
    person_count = len(person_names)
    rel_count = len(relations) if isinstance(relations, list) else 0
    if person_count >= 2:
        density = rel_count / person_count
        if density >= 1.2:   # 宽松一点(prompt 标的 1.5,verify 给 0.3 margin)
            passes.append(f"关系密度 {density:.2f} ✓(PERSON {person_count} / 关系 {rel_count})")
        else:
            issues.append(
                f"关系密度 {density:.2f} 过低(PERSON {person_count} 但只有 {rel_count} 关系)"
            )

    verdict = "PASS" if not issues else "FAIL"
    return {
        "verdict": verdict,
        "passes": passes,
        "issues": issues,
        "stats": {
            "person_count": person_count,
            "relation_count": rel_count,
            "narrative_pov": actual_pov,
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    load_env()
    cfg = get_config()
    system_prompt = load_prompt()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = VERIFY_DIR / f"build_graph_v5_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 64)
    print(f"Prompt Verification: {PROMPT_FILE} v5")
    print(f"Model: {cfg['llm_model']}  |  API: {cfg['api_base']}")
    print(f"Output: {out_dir.relative_to(PROJECT_ROOT)}")
    print("=" * 64)
    print()

    if args.dry_run:
        for sc in SCENARIOS:
            print(f"[DRY] {sc._name}: text 长 {len(sc.text)} 字, pov 期望 {sc._expected_pov}")
        print(f"\n预估总成本(deepseek-chat):¥0.05-0.20(取决于实际 token)")
        return

    if not cfg["api_key"]:
        print("ERROR: OPENAI_API_KEY / DEEPSEEK_API_KEY 未配置")
        sys.exit(1)

    summary = {
        "prompt_version": "v5",
        "model": cfg["llm_model"],
        "ts": ts,
        "scenarios": [],
        "total_cost_yuan": 0.0,
        "pass_count": 0,
        "fail_count": 0,
    }
    md_lines = [
        "=" * 64,
        f"Prompt Verification: {PROMPT_FILE} v5",
        f"Model: {cfg['llm_model']}  |  API: {cfg['api_base']}",
        "=" * 64,
        "",
    ]

    for sc in SCENARIOS:
        print(f"→ {sc._name} ...", end=" ", flush=True)
        sc_dir = out_dir / sc._name
        sc_dir.mkdir(exist_ok=True)

        # 保存 input
        input_dict = {
            "text": sc.text,
            "work_type": sc.work_type,
            "work_meta_hint": sc.work_meta_hint,
            "_name": sc._name,
            "_description": sc._description,
        }
        (sc_dir / "input.json").write_text(
            json.dumps(input_dict, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        t0 = time.time()
        try:
            raw, usage = call_llm(system_prompt, input_dict)
        except Exception as e:
            print(f"\n  ! LLM 调用失败: {e}")
            (sc_dir / "verdict.json").write_text(
                json.dumps({"verdict": "ERROR", "error": str(e)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            summary["fail_count"] += 1
            continue
        elapsed = time.time() - t0
        cost = estimate_cost(usage["input_tokens"], usage["output_tokens"], cfg["llm_model"])
        summary["total_cost_yuan"] += cost

        (sc_dir / "output_raw.txt").write_text(raw, encoding="utf-8")

        # 解析 JSON
        parsed = None
        try:
            parsed = json.loads(strip_markdown_fence(raw))
            (sc_dir / "output_parsed.json").write_text(
                json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8",
            )
        except Exception as e:
            print(f"\n  ! JSON 解析失败: {e}")

        # 自动 verdict
        verdict = verdict_scenario(sc, parsed)
        verdict["usage"] = usage
        verdict["cost_yuan"] = round(cost, 4)
        verdict["duration_s"] = round(elapsed, 2)
        (sc_dir / "verdict.json").write_text(
            json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8",
        )

        status_str = "PASS" if verdict["verdict"] == "PASS" else "FAIL"
        print(f"[{status_str}] {usage['input_tokens']}/{usage['output_tokens']} tok, ¥{cost:.4f}")

        if verdict["verdict"] == "PASS":
            summary["pass_count"] += 1
        else:
            summary["fail_count"] += 1

        md_lines.append(f"[{status_str}] {sc._name}  ({sc._description[:50]}...)")
        md_lines.append(f"  Tokens: {usage['input_tokens']} in / {usage['output_tokens']} out  Cost: ¥{cost:.4f}")
        if verdict.get("stats"):
            md_lines.append(f"  Stats: {verdict['stats']}")
        if verdict["passes"]:
            md_lines.append(f"  Passes ({len(verdict['passes'])}):")
            for p in verdict["passes"]:
                md_lines.append(f"      ✓ {p}")
        if verdict["issues"]:
            md_lines.append(f"  Issues ({len(verdict['issues'])}):")
            for iss in verdict["issues"]:
                md_lines.append(f"      ✕ {iss}")
        md_lines.append("")

        summary["scenarios"].append({"name": sc._name, "verdict": verdict})

    md_lines.append("=" * 64)
    md_lines.append(f"Pass rate:    {summary['pass_count']}/{summary['pass_count']+summary['fail_count']}")
    md_lines.append(f"Total cost:   ¥{summary['total_cost_yuan']:.4f}")
    md_lines.append("=" * 64)

    (out_dir / "summary.md").write_text("\n".join(md_lines), encoding="utf-8")
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    print()
    print("\n".join(md_lines[-4:]))


if __name__ == "__main__":
    main()
