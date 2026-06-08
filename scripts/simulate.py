"""
simulate.py — 多 agent 仿真:角色档案 + ch74 图谱 + 反事实锚点 → "如果 X 没发生" 剧情

【架构】Director-Agent-Composer 三层独立 LLM 编排
  Director : 编排每轮场景 / 在场 / 发声 / 契机                (1 个 LLM 调用 / 轮)
  Agent    : 每个角色独立扮演,产出 monologue/action/dialogue (N 个 / 轮)
  Composer : 把 timeline 编织成 ~4000 字小说体叙事             (1 个 LLM 调用 / 推演)

所有 prompt 已外部化到 prompts/ 目录,代码只负责"读模板 → 填变量 → 调 LLM"。

【Phase 模式】
  C: 10 轮 × 5 主 + 6 NPC 完整推演,产出 timeline.json
  D: 把 timeline.json 编织成 narrative.md(默认红楼笔法,--style C 降级现代白话)

【依赖】 openai >= 1.30, .env 中 OPENAI_API_KEY / OPENAI_API_BASE / LLM_MODEL

【使用】
  python scripts/simulate.py --phase C    # 跑完整推演(~¥0.10, 5-7 分钟)
  python scripts/simulate.py --phase D    # 编织叙事(~¥0.013, 30-50 秒)

【相关文档】
  prompts/             — 所有 LLM prompt 的模板文件
  docs/MVP迁移指南.md   — 跨作品复用手册 + 历次教训沉淀
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

# ---------- 路径 ----------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = PROJECT_ROOT / "prompts"
CHARS_DIR = PROJECT_ROOT / "data" / "characters"
NPCS_DIR = CHARS_DIR / "npcs"
SIM_DIR = PROJECT_ROOT / "data" / "simulations"

ALL_MAIN_AGENTS = ["xifeng", "tanchun", "baoyu", "daiyu", "baochai"]
NPC_AGENTS = [
    "wangshanbao", "wangfuren", "xingfuren",
    "zhouruijiade", "siqi", "ruhua",
]

# ---------- 反事实锚点(本次推演的核心变量) ----------

DIVERGENCE = "探春已经举起手要打王善保家的——但巴掌在最后一寸停住,没有打下去。"

SCENE_DESCRIPTION = """场景:大观园抄检之夜,深夜。
凤姐带队,与王善保家的、周瑞家的等陪房,逐一搜检大观园各处。
方才到了探春的院子,探春主动打开了自己的箱子,以维护下人的尊严。
王善保家的不识相,意欲掀探春的衣裳搜身,被探春当众厉声呵斥。

【关键的一刻】
探春已经举起手要打王善保家的——但巴掌在最后一寸停住,没有打下去。
探春缓缓放下手,转身,目光扫过现场所有人。

(原著走向是探春一掌打下去。本次推演,这一掌没有落下。从这一刻起,
 后续走向由你们这群人物的自由意志决定。)
"""

# ---------- env 加载 ----------

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


def estimate_cost(in_tokens, out_tokens, model_name):
    pricing = {
        "deepseek-chat": (0.0010, 0.0020),
        "qwen-plus":     (0.0040, 0.0120),
        "qwen-turbo":    (0.0003, 0.0006),
        "gpt-4o-mini":   (0.0011, 0.0043),
    }
    in_p, out_p = pricing.get(model_name.lower(), (0.005, 0.015))
    return (in_tokens * in_p + out_tokens * out_p) / 1000


def strip_markdown_fence(s):
    s = s.strip()
    s = re.sub(r"^```(?:json|JSON|markdown|md)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


# ---------- prompt 模板加载 ----------

def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


# ---------- 资产加载 ----------

def load_character(char_id: str, dir_path: Path = CHARS_DIR) -> dict:
    return json.loads((dir_path / f"{char_id}.json").read_text(encoding="utf-8"))


def load_all_main_chars() -> dict:
    return {cid: load_character(cid) for cid in ALL_MAIN_AGENTS}


def load_all_npcs() -> dict:
    return {nid: load_character(nid, NPCS_DIR) for nid in NPC_AGENTS}


# ============================================================
# Agent prompt 构造
# ============================================================

def _format_list(items, sep="\n  - "):
    """简单 list → 带前缀连字符的多行字符串。"""
    if not items:
        return "  (无)"
    return sep + sep.join(items)


def build_agent_system_prompt(char: dict) -> str:
    """从 prompts/agent_system.md 模板填充该角色的人设。

    v2 关键升级(对应 4 AI 横评教训):注入 behavioral_rules 字段。
    详见 docs/MVP迁移指南.md 第 4 节。
    """
    template = load_prompt("agent_system.md")
    rels_text = _format_list(
        [f"{r['type']}: {r['description']}" for r in char.get("relationships", [])]
    )
    quotes_text = "\n".join(
        f"  {i+1}. “{q}”"
        for i, q in enumerate(char.get("voice_fingerprint", {}).get("quotes", []))
    ) or "  (无)"
    no_go_text = _format_list(char.get("no_go_list", []))
    behavioral_text = _format_list(char.get("behavioral_rules", []))
    high_freq = "、".join(
        char.get("voice_fingerprint", {}).get("high_freq_words", [])
    ) or "(无)"
    style = char.get("voice_fingerprint", {}).get("speech_style_notes", "(无)")

    return template.format(
        name=char["name"],
        identity=char.get("identity", ""),
        personality=char.get("personality", ""),
        relationships=rels_text,
        high_freq_words=high_freq,
        quotes=quotes_text,
        speech_style_notes=style,
        no_go_list=no_go_text,
        behavioral_rules=behavioral_text,
    )


def build_agent_user_prompt(
    char_name, scene, divergence, location, round_seed, narrator_note, flat_history
):
    if flat_history:
        hist_str = "\n".join(
            f"  - [Round {h['round']}] {h['speaker']}({h['action_type']}):{h['content']}"
            for h in flat_history
        )
    else:
        hist_str = "  (这是第 1 轮第一位发声者,你之前还没有任何人开口)"

    return f"""【场景设定】
{scene}

【关键反事实变量】
{divergence}

【当前位置 · 时间】
{location}

【迄今已发生(跨轮)】
{hist_str}

【本轮场面旁白】
{narrator_note}

【本轮契机(导演刚刚布置,你必须直接对此反应)】
{round_seed}

—— 你的回合 ——
作为【{char_name}】,在这一刻产出你的:

1. monologue:此刻内心独白(不超过 30 字,真实想法)
2. action:此刻外在动作(不超过 30 字)
3. dialogue:你说出口的话(不超过 80 字;若此刻不开口,留空字符串)

输出格式(严格 JSON):
{{
  "monologue": "...",
  "action": "...",
  "dialogue": "..."
}}"""


def call_agent(client, model, char, scene, divergence, location, round_seed, narrator_note, flat_history):
    system = build_agent_system_prompt(char)
    user = build_agent_user_prompt(
        char["name"], scene, divergence, location, round_seed, narrator_note, flat_history
    )

    common_kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.7,
        max_tokens=400,
    )
    try:
        resp = client.chat.completions.create(
            response_format={"type": "json_object"}, **common_kwargs
        )
    except Exception as e:
        if "response_format" in str(e).lower():
            resp = client.chat.completions.create(**common_kwargs)
        else:
            raise

    raw = strip_markdown_fence(resp.choices[0].message.content)
    return json.loads(raw), {
        "input_tokens": resp.usage.prompt_tokens,
        "output_tokens": resp.usage.completion_tokens,
    }


# ============================================================
# Director
# ============================================================

def _summarize_char_for_director(char):
    return (
        f"{char['id']}({char['name']}):{char['identity'][:25]} | "
        f"性格 {char['personality'][:35]}"
    )


def build_director_user_prompt(scene, divergence, characters, history, round_num):
    char_lines = "\n".join(
        "  - " + _summarize_char_for_director(c) for c in characters.values()
    )

    if history:
        hist_lines = []
        for r in history:
            hist_lines.append(
                f"\n  Round {r['round']} [{r['location']} · {r['time_advance']}]:"
            )
            hist_lines.append(f"    旁白: {r['narrator_note']}")
            for ev in r["events"]:
                if ev["action"]:
                    hist_lines.append(f"    {ev['speaker']} 动作:{ev['action']}")
                if ev["dialogue"]:
                    hist_lines.append(f"    {ev['speaker']} 对白:{ev['dialogue']}")
        hist_str = "\n".join(hist_lines)
    else:
        hist_str = "  (这是第 1 轮,反事实刚刚发生,还没有任何后续事件)"

    # 注入每个 agent 的 behavioral_rules(对应 director 铁律 8)
    rules_blocks = []
    for char in characters.values():
        rules = char.get("behavioral_rules") or []
        if not rules:
            continue
        block = [f"\n[{char['name']}]"]
        for rule in rules:
            block.append(f"  - {rule}")
        rules_blocks.append("\n".join(block))

    rules_section = ""
    if rules_blocks:
        rules_section = "\n\n【character_behavioral_rules:每个角色受其世界规矩约束】\n"
        rules_section += "**编排 round_seed 时必须自检:不能让任何角色做违反这些规矩的事**(铁律 8)。\n"
        rules_section += "\n".join(rules_blocks)

    return f"""【场景设定】
{scene}

【关键反事实变量】
{divergence}

【可用角色(共 {len(characters)} 个 agent)】
{char_lines}
{rules_section}

【迄今已发生】
{hist_str}

—— 你的任务:编排第 {round_num} 轮 ——
基于上面的状态,产出第 {round_num} 轮的导演方案。
**编排前最后默念一遍铁律 8 的 4 个禁区,确认无违反才输出。**
"""


def call_director(client, model, scene, divergence, characters, history, round_num):
    system = load_prompt("director_system.md")
    user = build_director_user_prompt(scene, divergence, characters, history, round_num)

    common_kwargs = dict(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.6,
        max_tokens=600,
    )
    try:
        resp = client.chat.completions.create(
            response_format={"type": "json_object"}, **common_kwargs
        )
    except Exception as e:
        if "response_format" in str(e).lower():
            resp = client.chat.completions.create(**common_kwargs)
        else:
            raise

    raw = strip_markdown_fence(resp.choices[0].message.content)
    return json.loads(raw), {
        "input_tokens": resp.usage.prompt_tokens,
        "output_tokens": resp.usage.completion_tokens,
    }


def validate_director_plan(plan, all_agent_ids):
    warnings = []
    plan = dict(plan)

    present = [a for a in plan.get("present_agents", []) if a in all_agent_ids]
    speaking = [a for a in plan.get("speaking_agents", []) if a in all_agent_ids]

    invalid_present = set(plan.get("present_agents", [])) - set(present)
    invalid_speaking = set(plan.get("speaking_agents", [])) - set(speaking)
    if invalid_present:
        warnings.append(f"present_agents 含未知 id: {invalid_present},已剔除")
    if invalid_speaking:
        warnings.append(f"speaking_agents 含未知 id: {invalid_speaking},已剔除")

    speaking = [a for a in speaking if a in present]
    if not speaking:
        warnings.append("speaking_agents 为空,启用 fallback:取 present 前 2 个")
        speaking = present[:2] if present else []

    plan["present_agents"] = present
    plan["speaking_agents"] = speaking
    for k in ("location", "time_advance", "round_seed", "narrator_note"):
        plan.setdefault(k, "")

    return plan, warnings


# ============================================================
# Phase C:Director + 5 主 + 6 NPC × 10 轮
# ============================================================

PHASE_C_ROUNDS = 10


def run_phase_c():
    cfg = get_config()
    if not cfg["api_key"]:
        print("✗ OPENAI_API_KEY 未设置,先检查 .env")
        return 1

    print(f"=== Phase C:Director + 5 主 + 6 NPC × {PHASE_C_ROUNDS} 轮 ===")
    print(f"模型:{cfg['llm_model']}")

    main_chars = load_all_main_chars()
    npc_chars = load_all_npcs()
    chars = {**main_chars, **npc_chars}
    print(f"已加载 {len(chars)} 个 agent:")
    print(f"  主:{[c['name'] for c in main_chars.values()]}")
    print(f"  NPC:{[c['name'] for c in npc_chars.values()]}")

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    run_dir = SIM_DIR / f"phase_c_{timestamp}"
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "phase": "C",
        "timestamp": timestamp,
        "model": cfg["llm_model"],
        "active_agents": list(chars.keys()),
        "active_agent_names": [c["name"] for c in chars.values()],
        "main_count": len(main_chars),
        "npc_count": len(npc_chars),
        "scene": SCENE_DESCRIPTION,
        "divergence": DIVERGENCE,
        "rounds_planned": PHASE_C_ROUNDS,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"运行目录:{run_dir.relative_to(PROJECT_ROOT)}")

    from openai import OpenAI
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["api_base"])

    timeline_rounds = []
    flat_history = []
    director_history = []
    total_in = total_out = total_calls = 0
    speaker_counter = {cid: 0 for cid in chars}

    for round_num in range(1, PHASE_C_ROUNDS + 1):
        print(f"\n--- 第 {round_num} 轮 ---")
        t_round = time.time()

        # Director
        print("  [Director] 编排中...", end=" ", flush=True)
        t0 = time.time()
        try:
            plan_raw, dir_usage = call_director(
                client, cfg["llm_model"],
                SCENE_DESCRIPTION, DIVERGENCE,
                chars, director_history, round_num,
            )
        except Exception as e:
            print(f"✗ Director 调用失败:{e}")
            return 1
        plan, warnings = validate_director_plan(plan_raw, set(chars.keys()))
        print(f"✓ {time.time()-t0:.1f}s "
              f"({dir_usage['input_tokens']}+{dir_usage['output_tokens']} tk)")
        for w in warnings:
            print(f"    ⚠ {w}")
        total_in += dir_usage["input_tokens"]
        total_out += dir_usage["output_tokens"]
        total_calls += 1

        print(f"  位置:{plan['location']} · {plan['time_advance']}")
        print(f"  发声:{plan['speaking_agents']}")
        print(f"  契机:{plan['round_seed']}")

        # Agents
        round_events = []
        for char_id in plan["speaking_agents"]:
            char = chars[char_id]
            print(f"  [{char['name']}]...", end=" ", flush=True)
            t1 = time.time()
            try:
                output, usage = call_agent(
                    client, cfg["llm_model"], char,
                    SCENE_DESCRIPTION, DIVERGENCE,
                    plan["location"] + " · " + plan["time_advance"],
                    plan["round_seed"], plan["narrator_note"],
                    flat_history,
                )
            except Exception as e:
                print(f"✗ {e}")
                return 1
            total_in += usage["input_tokens"]
            total_out += usage["output_tokens"]
            total_calls += 1
            speaker_counter[char_id] += 1
            print(f"✓ {time.time()-t1:.1f}s "
                  f"({usage['input_tokens']}+{usage['output_tokens']} tk)")

            round_events.append({
                "speaker": char["name"],
                "speaker_id": char_id,
                "monologue": output.get("monologue", ""),
                "action": output.get("action", ""),
                "dialogue": output.get("dialogue", ""),
                "usage": usage,
            })
            if output.get("action"):
                flat_history.append({
                    "round": round_num, "speaker": char["name"],
                    "action_type": "action", "content": output["action"],
                })
            if output.get("dialogue"):
                flat_history.append({
                    "round": round_num, "speaker": char["name"],
                    "action_type": "dialogue", "content": output["dialogue"],
                })

        round_record = {
            "round": round_num,
            "director_plan": plan,
            "events": round_events,
            "elapsed_sec": round(time.time() - t_round, 2),
        }
        timeline_rounds.append(round_record)
        director_history.append({
            "round": round_num,
            "location": plan["location"],
            "time_advance": plan["time_advance"],
            "narrator_note": plan["narrator_note"],
            "events": round_events,
        })

    # 落盘
    (run_dir / "timeline.json").write_text(
        json.dumps({"rounds": timeline_rounds}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    cost = estimate_cost(total_in, total_out, cfg["llm_model"])
    usage_summary = {
        "model": cfg["llm_model"],
        "n_calls": total_calls,
        "total_input_tokens": total_in,
        "total_output_tokens": total_out,
        "total_tokens": total_in + total_out,
        "estimated_cost_rmb": round(cost, 4),
        "speaker_distribution": {
            chars[cid]["name"]: cnt
            for cid, cnt in speaker_counter.items() if cnt > 0
        },
    }
    (run_dir / "usage.json").write_text(
        json.dumps(usage_summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"\n=== 完成 ===")
    print(f"调用次数:{usage_summary['n_calls']}")
    print(f"总 token :{usage_summary['total_tokens']:,}")
    print(f"实际成本 :¥{usage_summary['estimated_cost_rmb']:.4f}")
    print(f"\n--- 戏份分布 ---")
    for name, cnt in sorted(usage_summary["speaker_distribution"].items(),
                             key=lambda x: -x[1]):
        print(f"  {name:8s} {cnt:2d} {'█' * cnt}")
    print(f"\n--- 10 轮地点序列 ---")
    for i, r in enumerate(timeline_rounds, 1):
        print(f"  Round {i:2d}: {r['director_plan']['location']}")
    print(f"\n完整 timeline:{run_dir.relative_to(PROJECT_ROOT)}\\timeline.json")
    return 0


# ============================================================
# Phase D:Composer
# ============================================================

COMPOSER_TARGET_CHARS = 4000


def build_composer_user_prompt(timeline, chars):
    """把 timeline 整理成 Composer 可读的输入,并注入 character_behavioral_rules。"""
    lines = ["【参考材料:推演 timeline,10 轮】\n"]

    for r in timeline["rounds"]:
        plan = r["director_plan"]
        lines.append(
            f"\n=== 第 {r['round']} 轮 [{plan['location']} · {plan['time_advance']}] ==="
        )
        if plan.get("narrator_note"):
            lines.append(f"旁白:{plan['narrator_note']}")
        if plan.get("round_seed"):
            lines.append(f"契机:{plan['round_seed']}")
        for ev in r["events"]:
            lines.append(f"\n  - {ev['speaker']}:")
            if ev.get("monologue"):
                lines.append(f"    内心:{ev['monologue']}")
            if ev.get("action"):
                lines.append(f"    动作:{ev['action']}")
            if ev.get("dialogue"):
                lines.append(f"    对白(必须保留原文):“{ev['dialogue']}”")

    # 注入 behavioral_rules(对应 composer 铁律 11)
    rules_blocks = []
    for char in chars.values():
        rules = char.get("behavioral_rules") or []
        if not rules:
            continue
        block = [f"\n[{char['name']}]"]
        for rule in rules:
            block.append(f"  - {rule}")
        rules_blocks.append("\n".join(block))

    if rules_blocks:
        lines.append("\n\n【character_behavioral_rules:每个角色受其世界规矩约束的行为模式】")
        lines.append("叙述时必须严格遵守这些约束。")
        lines.append("如果上面 timeline 中某个 event 违反了角色的 behavioral_rules,")
        lines.append("不要简单复述,要在叙述中用一句话软化(参照 composer prompt 铁律 11),")
        lines.append("例如:'黛玉本不愿夜深出门,只是事关姊妹清白,只得破例同往。'")
        lines.extend(rules_blocks)

    lines.append("\n\n【任务】")
    lines.append(
        f"把上面 10 轮推演重写为 {COMPOSER_TARGET_CHARS} 字 ± 200 字的小说体连贯叙事。"
        "对白原文必须逐字保留;旁白与契机融化进叙事;"
        "内心独白只挑 3-5 处用'心下暗忖'等手法点出;"
        "**留白率 ≥ 30%(铁律 10)**;"
        "**严格遵守 character_behavioral_rules(铁律 11)**,timeline 与规矩冲突时用叙述软化。"
    )
    return "\n".join(lines)


def call_composer(client, model, timeline, chars, style="A"):
    template_name = "composer_a.md" if style == "A" else "composer_c.md"
    system = load_prompt(template_name).format(target_chars=COMPOSER_TARGET_CHARS)
    user = build_composer_user_prompt(timeline, chars)

    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        temperature=0.65,
        max_tokens=8000,
    )
    text = strip_markdown_fence(resp.choices[0].message.content or "")
    return text, {
        "input_tokens": resp.usage.prompt_tokens,
        "output_tokens": resp.usage.completion_tokens,
    }


def count_chinese_chars(text):
    return len(re.findall(r"[一-鿿]", text))


def find_latest_phase_c_dir():
    candidates = sorted(SIM_DIR.glob("phase_c_*"))
    return candidates[-1] if candidates else None


def run_phase_d(timeline_path=None, style="A"):
    cfg = get_config()
    if not cfg["api_key"]:
        print("✗ OPENAI_API_KEY 未设置")
        return 1

    if timeline_path is None:
        latest = find_latest_phase_c_dir()
        if latest is None:
            print("✗ 找不到 phase_c_* 目录,请先跑 Phase C 或用 --timeline 指定")
            return 1
        timeline_path = latest / "timeline.json"
    else:
        timeline_path = Path(timeline_path)
    if not timeline_path.exists():
        print(f"✗ 找不到 {timeline_path}")
        return 1

    print(f"=== Phase D:Composer({'红楼笔法' if style == 'A' else '现代白话'}) ===")
    print(f"模型      :{cfg['llm_model']}")
    print(f"输入      :{timeline_path.relative_to(PROJECT_ROOT)}")

    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    chars = load_all_main_chars()

    n_rounds = len(timeline.get("rounds", []))
    n_events = sum(len(r["events"]) for r in timeline["rounds"])
    print(f"timeline  :{n_rounds} 轮 × 共 {n_events} events")
    print(f"目标字数  :{COMPOSER_TARGET_CHARS} ± 200")

    from openai import OpenAI
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["api_base"])

    print(f"\n调用 Composer({cfg['llm_model']})...")
    t0 = time.time()
    try:
        narrative, usage = call_composer(client, cfg["llm_model"], timeline, chars, style)
    except Exception as e:
        print(f"✗ Composer 调用失败:{e}")
        return 1
    elapsed = time.time() - t0

    cn_chars = count_chinese_chars(narrative)
    cost = estimate_cost(usage["input_tokens"], usage["output_tokens"], cfg["llm_model"])

    print(f"  耗时    = {elapsed:.1f}s")
    print(f"  token   = input {usage['input_tokens']:,} + output {usage['output_tokens']:,}")
    print(f"  成本    ≈ ¥{cost:.4f}")
    print(f"  中文字  = {cn_chars}")

    target_min = COMPOSER_TARGET_CHARS - 200
    target_max = COMPOSER_TARGET_CHARS + 200
    if cn_chars < target_min:
        print(f"  ⚠ 字数偏少:目标 {target_min}-{target_max},实际 {cn_chars}")
    elif cn_chars > target_max:
        print(f"  ⚠ 字数偏多:目标 {target_min}-{target_max},实际 {cn_chars}")
    else:
        print(f"  ✓ 字数达标")

    # 自动归档:旧 narrative 移到 narrative_v{N}.md
    out_dir = timeline_path.parent
    suffix = "" if style == "A" else "_modern"
    narrative_path = out_dir / f"narrative{suffix}.md"
    if narrative_path.exists():
        n = 1
        while (out_dir / f"narrative{suffix}_v{n}.md").exists():
            n += 1
        archive = out_dir / f"narrative{suffix}_v{n}.md"
        narrative_path.rename(archive)
        old_usage = out_dir / "composer_usage.json"
        if old_usage.exists():
            old_usage.rename(out_dir / f"composer_usage_v{n}.json")
        print(f"  旧版归档 → {archive.name}")
    narrative_path.write_text(narrative, encoding="utf-8")
    print(f"\n✅ → {narrative_path.relative_to(PROJECT_ROOT)}")

    # 对白保留率校验(剥光标点纯比汉字串)
    def strip_to_cjk(s):
        return "".join(re.findall(r"[一-鿿]", s))

    narrative_cjk = strip_to_cjk(narrative)
    preserved = sampled = 0
    for r in timeline["rounds"]:
        for ev in r["events"]:
            d = ev.get("dialogue", "").strip()
            if not d or len(d) < 10:
                continue
            sampled += 1
            key = strip_to_cjk(d)[:15]
            if key and key in narrative_cjk:
                preserved += 1
    rate = preserved / sampled if sampled else None
    if sampled:
        print(f"  对白保留率:{preserved}/{sampled} = {rate:.0%}")
        if rate < 0.7:
            print(f"  ⚠ 对白保留率偏低,Composer 可能改写了过多原对白")

    composer_usage_path = out_dir / "composer_usage.json"
    composer_usage_path.write_text(
        json.dumps({
            "model": cfg["llm_model"],
            "style": style,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "estimated_cost_rmb": round(cost, 4),
            "chinese_char_count": cn_chars,
            "dialogue_preservation_rate": round(rate, 3) if sampled else None,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return 0


# ---------- CLI ----------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--phase", default="C", choices=["C", "D"],
        help="C:5 主 + 6 NPC × 10 轮推演 / D:Composer 编织 4000 字叙事",
    )
    parser.add_argument(
        "--timeline", default=None,
        help="(仅 Phase D)指定 timeline.json 路径;省略则用最新 phase_c_*",
    )
    parser.add_argument(
        "--style", default="A", choices=["A", "C"],
        help="(仅 Phase D)A=红楼笔法(默认) / C=现代白话(降级)",
    )
    args = parser.parse_args()

    load_env()
    if args.phase == "C":
        return run_phase_c()
    if args.phase == "D":
        return run_phase_d(timeline_path=args.timeline, style=args.style)
    return 1


if __name__ == "__main__":
    sys.exit(main())
