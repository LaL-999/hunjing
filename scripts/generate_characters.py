"""
generate_characters.py — 用 prompts/character_generator.md 自动生成角色档案 JSON

替代手工编写或外部 AI 临时生成的旧方式。任意作品上来,跑一次就有完整、稳定、
含 behavioral_rules 的角色档案。

【设计原则】
- prompt 不在代码里,从 prompts/character_generator.md 读取
- 每个角色独立 LLM 调用(质量 > 速度)
- 文本节选自动检索:scan 原文找含 character_name 的段落,取上下文
- 输出含完整 schema(identity / personality / relationships / key_events /
  voice_fingerprint / no_go_list / behavioral_rules / tone)

【使用】
  # 默认重生成红楼梦 5 主角
  python scripts/generate_characters.py

  # 指定作品 + 角色
  python scripts/generate_characters.py \\
    --work "天龙八部" \\
    --source data/extracted/天龙八部_full.txt \\
    --characters 乔峰 段誉 虚竹 王语嫣 阿朱 \\
    --language-style "古典武侠" \\
    --output-dir data/characters

  # 验证模式(只 dry-run,不调 LLM)
  python scripts/generate_characters.py --dry-run

【输出】
  data/characters/<id>.json (per character)
  + data/characters/_index.json 自动重建

【成本估算】
  per character ≈ 5-8k input tokens + 1-2k output tokens
  DeepSeek: ~¥0.008/character
  5 主角合计: ~¥0.04
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
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_SOURCE = DATA_DIR / "extracted" / "红楼梦_full.txt"
DEFAULT_OUT_DIR = DATA_DIR / "characters"

# 红楼梦 5 主角的默认配置(name → id + aliases 用于检索)
DEFAULT_RED_CHAR_CONFIG = [
    {"id": "baoyu",   "name": "贾宝玉", "aliases": ["宝玉", "怡红公子"]},
    {"id": "daiyu",   "name": "林黛玉", "aliases": ["黛玉", "林妹妹", "潇湘妃子"]},
    {"id": "baochai", "name": "薛宝钗", "aliases": ["宝钗", "宝姐姐"]},
    {"id": "xifeng",  "name": "王熙凤", "aliases": ["凤姐", "凤丫头", "琏二奶奶"]},
    {"id": "tanchun", "name": "贾探春", "aliases": ["探春", "三姑娘", "三妹妹"]},
]

# ---------- env 加载(与 build_graph.py 同款) ----------

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
        "api_base": os.getenv(
            "OPENAI_API_BASE", "https://api.deepseek.com/v1"
        ),
        "llm_model": os.getenv("LLM_MODEL", "deepseek-chat"),
    }


# ---------- 工具函数 ----------

def load_prompt(name: str) -> str:
    """读取 prompts/ 下的模板文件。"""
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def estimate_tokens(text: str) -> int:
    cjk = len(re.findall(r"[一-鿿]", text))
    other = len(text) - cjk
    return int(cjk * 1.5 + other * 0.3)


def estimate_cost(in_tokens: int, out_tokens: int, model: str) -> float:
    pricing = {
        "deepseek-chat":     (0.0010, 0.0020),
        "qwen-plus":         (0.0040, 0.0120),
        "qwen-turbo":        (0.0003, 0.0006),
        "gpt-4o-mini":       (0.0011, 0.0043),
        "claude-sonnet-4-5": (0.0220, 0.1090),
    }
    in_p, out_p = pricing.get(model.lower(), (0.005, 0.015))
    return (in_tokens * in_p + out_tokens * out_p) / 1000


def strip_markdown_fence(s: str) -> str:
    s = s.strip()
    s = re.sub(r"^```(?:json|JSON)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


# ---------- 文本节选检索 ----------

def find_excerpts(
    source_text: str,
    name: str,
    aliases: list[str],
    max_chars: int = 8000,
    context_window: int = 400,
) -> str:
    """
    在原文中搜索 name + aliases,每命中一处取前后 context_window 字符,
    去重 + 拼接,总长度截断到 max_chars。

    简单粗暴但有效:够给 LLM 看到该角色"在原文里大概什么样"。
    """
    keywords = [name] + aliases
    hits: list[tuple[int, int]] = []  # (start, end) ranges
    for kw in keywords:
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

    # 合并重叠区间,保留命中密度高的部分
    hits.sort()
    merged: list[list[int]] = []
    for s, e in hits:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])

    # 拼接,中间用 "..." 分隔
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


# ---------- 单角色生成 ----------

def generate_one_character(
    client,
    model: str,
    template: str,
    work_name: str,
    language_style: str,
    char_config: dict,
    source_text: str,
) -> tuple[dict, dict]:
    """
    调一次 LLM 生成单个角色档案。

    Returns:
      (parsed_json, usage_dict)
    """
    excerpts = find_excerpts(
        source_text, char_config["name"], char_config.get("aliases", [])
    )
    if not excerpts:
        raise RuntimeError(
            f"原文中找不到 {char_config['name']} 的任何提及。"
            "检查 source 文件或 character 配置。"
        )

    user_prompt = template.format(
        work_name=work_name,
        character_name=char_config["name"],
        language_style=language_style,
        source_excerpts=excerpts,
    )

    common_kwargs = dict(
        model=model,
        messages=[{"role": "user", "content": user_prompt}],
        temperature=0.3,
        max_tokens=4000,
    )

    try:
        resp = client.chat.completions.create(
            response_format={"type": "json_object"},
            **common_kwargs,
        )
    except Exception as e:
        if "response_format" in str(e).lower():
            resp = client.chat.completions.create(**common_kwargs)
        else:
            raise

    raw = strip_markdown_fence(resp.choices[0].message.content or "")
    data = json.loads(raw)

    # 强制写入 id(LLM 可能漏写或写错)
    data["id"] = char_config["id"]
    if "source" not in data:
        data["source"] = (
            f"自动生成 from {work_name} + character_generator.md v1"
        )

    usage = {
        "input_tokens": resp.usage.prompt_tokens,
        "output_tokens": resp.usage.completion_tokens,
    }
    return data, usage


# ---------- dry-run ----------

def dry_run(work_name: str, source_path: Path, characters: list[dict]) -> int:
    print("=== dry-run:验证环境 + 检索质量 ===\n")

    print("[1/3] Python 依赖")
    try:
        import openai
        print(f"      ✓ openai {openai.__version__}")
    except ImportError:
        print("      ✗ openai 未安装")
        return 1

    print("[2/3] prompts/character_generator.md 是否存在")
    template_path = PROMPTS_DIR / "character_generator.md"
    if not template_path.exists():
        print(f"      ✗ 找不到 {template_path}")
        return 1
    template = template_path.read_text(encoding="utf-8")
    print(f"      ✓ {template_path.relative_to(PROJECT_ROOT)} ({len(template)} 字)")

    print("[3/3] 源文本 + 各角色检索质量")
    if not source_path.exists():
        print(f"      ✗ 源文本不存在: {source_path}")
        return 1
    source = source_path.read_text(encoding="utf-8")
    print(f"      ✓ {source_path.relative_to(PROJECT_ROOT)} ({len(source):,} 字)")

    cfg = get_config()
    print(f"\n      模型: {cfg['llm_model']}")

    total_est_cost = 0.0
    for ch in characters:
        excerpts = find_excerpts(source, ch["name"], ch.get("aliases", []))
        ex_tokens = estimate_tokens(excerpts) + estimate_tokens(template)
        out_tokens = 1500
        cost = estimate_cost(ex_tokens, out_tokens, cfg["llm_model"])
        total_est_cost += cost
        print(
            f"      - {ch['name']:>5s}: 节选 {len(excerpts):>6,} 字, "
            f"预估 {ex_tokens:>5,} input + {out_tokens} output, ¥{cost:.4f}"
        )

    print(f"\n      合计预估成本: ¥{total_est_cost:.3f}")
    print("\n✅ dry-run 通过。可以执行实跑。")
    return 0


# ---------- 主流程 ----------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--work", default="红楼梦", help="作品名(默认 红楼梦)")
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help="原文 txt 路径(默认 data/extracted/红楼梦_full.txt)",
    )
    parser.add_argument(
        "--characters",
        nargs="+",
        default=None,
        help="角色名列表;省略则用红楼默认 5 主角(贾宝玉 林黛玉 ...)",
    )
    parser.add_argument(
        "--language-style",
        default="古典章回小说",
        help="作品语体(古典章回小说 / 现代网文 / 日系漫画 / 西方推理 等)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUT_DIR,
        help="输出目录(默认 data/characters/)",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="仅验证环境与检索质量,不调 LLM"
    )
    args = parser.parse_args()

    load_env()

    # 解析角色配置:命令行只给 name 时,用合理默认 id
    if args.characters is None:
        char_configs = DEFAULT_RED_CHAR_CONFIG
    else:
        char_configs = []
        for name in args.characters:
            # 默认 id = pinyin 转换;这里粗略,生产中可加 pinyin 库
            cid = re.sub(r"[\s·]", "", name).lower()
            char_configs.append({"id": cid, "name": name, "aliases": [name]})

    if args.dry_run:
        return dry_run(args.work, args.source, char_configs)

    cfg = get_config()
    if not cfg["api_key"]:
        print("✗ OPENAI_API_KEY 未设置,先检查 .env")
        return 1

    if not args.source.exists():
        print(f"✗ 源文本不存在: {args.source}")
        return 1

    template = load_prompt("character_generator.md")
    source = args.source.read_text(encoding="utf-8")
    args.output_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== 角色档案生成器 ===")
    print(f"作品  :{args.work}")
    print(f"语体  :{args.language_style}")
    print(f"模型  :{cfg['llm_model']}")
    print(f"角色  :{[c['name'] for c in char_configs]}")
    print(f"输出  :{args.output_dir.relative_to(PROJECT_ROOT)}")
    print()

    from openai import OpenAI
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["api_base"])

    total_in = 0
    total_out = 0
    index_entries = []
    failed = []

    for i, ch in enumerate(char_configs, 1):
        print(f"[{i}/{len(char_configs)}] {ch['name']} ...", end=" ", flush=True)
        t0 = time.time()
        try:
            data, usage = generate_one_character(
                client, cfg["llm_model"], template,
                args.work, args.language_style, ch, source,
            )
        except Exception as e:
            print(f"✗ {e}")
            failed.append(ch["name"])
            continue

        elapsed = time.time() - t0
        total_in += usage["input_tokens"]
        total_out += usage["output_tokens"]

        out_path = args.output_dir / f"{ch['id']}.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        n_quotes = len(data.get("voice_fingerprint", {}).get("quotes", []))
        n_nogo = len(data.get("no_go_list", []))
        n_rules = len(data.get("behavioral_rules", []))
        print(
            f"✓ {elapsed:.1f}s ({usage['input_tokens']}+{usage['output_tokens']} tk) "
            f"| quotes={n_quotes} no_go={n_nogo} rules={n_rules}"
        )

        index_entries.append({"id": ch["id"], "name": ch["name"], "file": f"{ch['id']}.json"})

    # 重建 _index.json
    index_path = args.output_dir / "_index.json"
    index_path.write_text(
        json.dumps(
            {
                "source": f"自动生成 from {args.work} + character_generator.md v1",
                "characters": index_entries,
                "total": len(index_entries),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # 汇总
    cost = estimate_cost(total_in, total_out, cfg["llm_model"])
    print()
    print(f"=== 完成 ===")
    print(f"成功 :{len(index_entries)}/{len(char_configs)}")
    if failed:
        print(f"失败 :{failed}")
    print(f"token :input {total_in:,} + output {total_out:,}")
    print(f"成本 :¥{cost:.4f}")
    print(f"索引 :→ {index_path.relative_to(PROJECT_ROOT)}")

    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
