"""
build_graph.py — 从章节文本一次性抽取实体与关系,产出 JSON 知识图谱

设计选择(为什么没用 LightRAG/GraphRAG):
    LightRAG 的核心价值在跨章节 chunk + embedding + 向量检索。demo 阶段只需要
    单章(ch74,~22k 字)且不需要查询能力,直接给 LLM 整章塞 prompt 一次性
    抽取反而更简洁:依赖只要一个 openai SDK,token 消耗约 LightRAG 的 1/4。
    MVP 阶段如需跨章节图谱聚合或向量查询,再切到 LightRAG。

依赖:
    openai >= 1.30.0(见 requirements.txt)

环境变量(在项目根 .env 里设置,见 .env.example):
    OPENAI_API_KEY      LLM API key
    OPENAI_API_BASE     OpenAI 兼容端点
    LLM_MODEL           模型名(默认 qwen-plus)

使用:
    # 1. 第一次先 dry-run,只花 ~5-10 token 验证连通
    python scripts/build_graph.py --dry-run

    # 2. 正式跑(默认 ch74)
    python scripts/build_graph.py

    # 3. 跑指定章节
    python scripts/build_graph.py --input data/chapters/ch01.txt --output-name ch01

输出:
    data/graphs/{output-name}.json — 含 entities + relations + 元数据(token/cost)
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
GRAPHS_DIR = PROJECT_ROOT / "data" / "graphs"


# ---------- .env 加载(不引入 python-dotenv,自己解析) ----------

def load_env():
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        # 已存在的环境变量优先(允许命令行覆盖 .env)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_config():
    return {
        "api_key": os.getenv("OPENAI_API_KEY"),
        "api_base": os.getenv(
            "OPENAI_API_BASE",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        "llm_model": os.getenv("LLM_MODEL", "qwen-plus"),
    }


# ---------- prompt ----------

SYSTEM_PROMPT = """你是中国古典文学《红楼梦》的实体关系抽取专家。
任务:从给定章节文本中识别实体与实体间关系,严格输出 JSON。

⚠️ 最重要的纪律(违反即整次抽取作废):
**只从下面给定的章节正文里抽取实体。不要根据你训练数据里关于《红楼梦》整部
小说的背景知识补全实体或关系。如果一个角色/地点/物品/事件在给定的本章正文
中没有明确出现,绝对不要列入 JSON,哪怕你知道他/它在《红楼梦》中很重要。**

【实体类型】(只允许下列 4 种):
- PERSON   人物。本章正文中有命名出现且有具体动作或对话的角色,无论戏份多少。
           包括主子、奴仆、下人、亲戚、陪房等。
           排除:泛指代词(如"众人""丫鬟们""婆子们")。
           ⚠️ 不在本章正文出现的角色不列入,即使是红楼梦著名人物。
- LOCATION 地点。可能涵盖府邸(荣国府/宁国府)、园子(大观园)、园中住所、
           屋内具体场所(内厅/书房/抱厦)等多个层级。
           ⚠️ 只抽取本章正文实际提到的地点,不要根据小说背景补全大观园全部住所。
- OBJECT   关键物品。对本章剧情有推动力的器物(香袋、玉、扇子、金项圈、
           抄检搜出的私物等)。一般陈设(花瓶/桌椅)不算。
- EVENT    事件。**把本章贯穿性大事件拆成 4-6 个子事件**,不要压成一个 EVENT 节点。
           子事件应对应原文具体情节段落,典型粒度:引发 / 激化 / 转折 / 反抗 / 收尾。

【关系类型】(选最贴切的一项):
亲属 / 主仆 / 朋友 / 情侣 / 敌对 / 同事 / 师徒 / 参与 / 位于 / 拥有 / 提及

【约束】:
1. 同一人物用最常见的称呼作为 name(如"宝玉"而非"贾宝玉",如"凤姐"而非"王熙凤"),
   其他称呼放进 aliases
2. description 字段简明,不超过 40 字
3. 脂批(评点性文字,如"国昼""页昼""丁辰云"等批者署名后内容)不算正文
4. **作答前最后过一遍 entities 列表,把每个 name 都搜一下章节正文,
    确认确实在文中出现过**;关系的 source/target 也都要在 entities 里
5. 严格输出 JSON,不要 markdown 代码块,不要前后说明

【输出格式】:
{
  "entities": [
    {
      "name": "宝玉",
      "type": "PERSON",
      "aliases": ["贾宝玉"],
      "description": "荣国府二爷"
    }
  ],
  "relations": [
    {
      "source": "凤姐",
      "target": "贾琏",
      "type": "情侣",
      "description": "凤姐是贾琏正妻"
    }
  ]
}
"""

USER_PROMPT_TEMPLATE = """请从下面这段《红楼梦》章节正文中抽取实体与关系。

章节文本:
\"\"\"
{text}
\"\"\"

只输出 JSON。"""


# ---------- 工具函数 ----------

def estimate_tokens(text):
    """中文 token 粗估:每汉字 ~1.5 token,其余 ~0.3 token。"""
    cjk = len(re.findall(r"[一-鿿]", text))
    other = len(text) - cjk
    return int(cjk * 1.5 + other * 0.3)


def estimate_cost(input_tokens, output_tokens, model_name):
    """粗估 RMB 成本。价格按主流国产模型常见档位推算,仅供参考。"""
    # ¥ per 1k tokens
    pricing = {
        "qwen-plus":      (0.0040, 0.0120),
        "qwen-turbo":     (0.0003, 0.0006),
        "qwen-max":       (0.0200, 0.0600),
        "deepseek-chat":  (0.0010, 0.0020),
        "gpt-4o-mini":    (0.0011, 0.0043),
        "gpt-4o":         (0.0180, 0.0710),
        "claude-sonnet-4-5": (0.0220, 0.1090),
    }
    in_price, out_price = pricing.get(model_name.lower(), (0.005, 0.015))
    return (input_tokens * in_price + output_tokens * out_price) / 1000


def strip_markdown_fence(s):
    """有些模型会用 ```json ... ``` 包裹返回。剥掉。"""
    s = s.strip()
    s = re.sub(r"^```(?:json|JSON)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


# ---------- dry-run ----------

def dry_run(input_path):
    print("=== dry-run:验证环境 ===\n")

    # 1. 依赖
    print("[1/3] Python 依赖")
    try:
        import openai
        print(f"      ✓ openai {openai.__version__}")
    except ImportError:
        print("      ✗ openai 未安装")
        print("        修复:uv pip install -r requirements.txt")
        return 1

    # 2. 配置
    print("[2/3] 环境变量")
    cfg = get_config()
    if not cfg["api_key"]:
        print("      ✗ OPENAI_API_KEY 未设置")
        print(f"        修复:复制 .env.example 为 .env 并填入 key")
        return 1
    # API key 必须是纯 ASCII(HTTP header 限制)。
    # 这一关能拦住"复制了 .env.example 但没改占位中文"的常见错误。
    try:
        cfg["api_key"].encode("ascii")
    except UnicodeEncodeError:
        print("      ✗ OPENAI_API_KEY 含非 ASCII 字符(可能仍是 .env.example 的占位文本)")
        print(f"        当前值前 30 字符: {cfg['api_key'][:30]!r}")
        print(f"        修复:把 .env 里的 OPENAI_API_KEY 换成你真实的 key")
        return 1
    masked = cfg["api_key"][:6] + "..." + cfg["api_key"][-4:]
    print(f"      ✓ api_key  = {masked}")
    print(f"      ✓ api_base = {cfg['api_base']}")
    print(f"      ✓ llm_model= {cfg['llm_model']}")

    # 3. 输入文件 + 成本预估
    print("[3/3] 输入文件 + 成本预估")
    if not input_path.exists():
        print(f"      ✗ {input_path} 不存在")
        return 1
    text = input_path.read_text(encoding="utf-8")
    est_in = estimate_tokens(text) + 350  # +system prompt
    est_out = 2500  # 抽取结果约 2-3k token
    est_cost = estimate_cost(est_in, est_out, cfg["llm_model"])
    print(f"      ✓ {input_path.name}: {len(text):,} 字")
    print(f"      预估 input  ≈ {est_in:,} tokens")
    print(f"      预估 output ≈ {est_out:,} tokens")
    print(f"      预估成本    ≈ ¥{est_cost:.3f} ({cfg['llm_model']})")

    # 4. 极小连通测试(~5 token)
    print("\n      尝试 1 次最小 LLM 调用(~5 token)验证连通...")
    try:
        from openai import OpenAI
        client = OpenAI(api_key=cfg["api_key"], base_url=cfg["api_base"])
        resp = client.chat.completions.create(
            model=cfg["llm_model"],
            messages=[{"role": "user", "content": "回一个'好'字"}],
            max_tokens=5,
        )
        reply = (resp.choices[0].message.content or "").strip()
        print(f"      ✓ LLM 回复:{reply!r}")
    except Exception as e:
        print(f"      ✗ LLM 调用失败:{e}")
        return 1

    print("\n✅ dry-run 通过。可以执行:")
    print(f"   python scripts/build_graph.py --input {input_path.relative_to(PROJECT_ROOT)}")
    return 0


# ---------- 实际跑 ----------

def build_graph(input_path, output_name):
    cfg = get_config()
    if not cfg["api_key"]:
        print("✗ OPENAI_API_KEY 未设置。先跑 --dry-run 检查环境。")
        return 1

    text = input_path.read_text(encoding="utf-8").strip()
    if not text:
        print(f"✗ {input_path} 为空")
        return 1

    print(f"=== 构建图谱:{input_path.name} ===")
    print(f"输入:{len(text):,} 字")

    from openai import OpenAI
    client = OpenAI(api_key=cfg["api_key"], base_url=cfg["api_base"])

    print(f"调用 {cfg['llm_model']}...")
    t0 = time.time()
    # max_tokens=8000:留充足余量,即使模型一时啰嗦也不被截断;
    # 实际成本只按真实消耗的 output tokens 计费,设大不会涨钱。
    common_kwargs = dict(
        model=cfg["llm_model"],
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT_TEMPLATE.format(text=text)},
        ],
        temperature=0.2,
        max_tokens=8000,
    )
    try:
        resp = client.chat.completions.create(
            response_format={"type": "json_object"},
            **common_kwargs,
        )
    except Exception as e:
        if "response_format" in str(e).lower():
            print("  (该模型不支持 response_format,降级为普通调用)")
            resp = client.chat.completions.create(**common_kwargs)
        else:
            raise

    elapsed = time.time() - t0
    raw = (resp.choices[0].message.content or "").strip()
    raw = strip_markdown_fence(raw)
    usage = resp.usage
    cost = estimate_cost(usage.prompt_tokens, usage.completion_tokens, cfg["llm_model"])

    print(f"  耗时   = {elapsed:.1f}s")
    print(f"  token  = input {usage.prompt_tokens:,} + output {usage.completion_tokens:,} "
          f"= total {usage.total_tokens:,}")
    print(f"  成本   ≈ ¥{cost:.3f}")

    # 解析 JSON
    try:
        graph = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"✗ JSON 解析失败:{e}")
        debug = GRAPHS_DIR / f"{output_name}_raw.txt"
        debug.parent.mkdir(parents=True, exist_ok=True)
        debug.write_text(raw, encoding="utf-8")
        print(f"  原始响应已存:{debug}")
        return 1

    entities = graph.get("entities", [])
    relations = graph.get("relations", [])
    n_ent = len(entities)
    n_rel = len(relations)
    print(f"  抽取   = {n_ent} 实体, {n_rel} 关系")

    # ----- 校验:幻觉与关系完整性 -----
    # 1) 每个非 EVENT 实体的 name 或 alias 必须在原文中出现
    #    EVENT 名字是描述性标签(如"探春怒打王善保家的"),原文不会逐字出现,
    #    所以 EVENT 跳过命中校验。
    hallucinated = []
    for ent in entities:
        if ent.get("type") == "EVENT":
            continue
        candidates = [ent.get("name", "")] + (ent.get("aliases") or [])
        if not any(c and c in text for c in candidates):
            hallucinated.append(ent.get("name", "<unnamed>"))

    # 2) 每条关系的 source/target 必须在 entities 列表里
    entity_names = {ent.get("name") for ent in entities}
    broken_rels = [
        f"{r.get('source')}→{r.get('target')}"
        for r in relations
        if r.get("source") not in entity_names or r.get("target") not in entity_names
    ]

    if hallucinated:
        print(f"  ⚠️  {len(hallucinated)}/{n_ent} 实体名在原文未命中(可能幻觉):")
        print(f"      {hallucinated}")
    if broken_rels:
        print(f"  ⚠️  {len(broken_rels)}/{n_rel} 关系引用了未登录实体:")
        for br in broken_rels[:5]:
            print(f"      {br}")
    if not hallucinated and not broken_rels:
        print(f"  ✓ 校验通过:全部实体可在原文定位,全部关系引用合法")

    # 输出
    output = {
        "source_file": input_path.name,
        "source_char_count": len(text),
        "extracted_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "model": cfg["llm_model"],
        "usage": {
            "input_tokens": usage.prompt_tokens,
            "output_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "estimated_cost_rmb": round(cost, 4),
        },
        "validation": {
            "hallucinated_entities": hallucinated,
            "broken_relations": broken_rels,
        },
        "n_entities": n_ent,
        "n_relations": n_rel,
        "entities": entities,
        "relations": relations,
    }
    GRAPHS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = GRAPHS_DIR / f"{output_name}.json"
    out_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ → {out_path.relative_to(PROJECT_ROOT)}")
    return 0


# ---------- CLI ----------

def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=PROJECT_ROOT / "data" / "chapters" / "ch74.txt",
        help="输入文本路径(默认 data/chapters/ch74.txt)",
    )
    parser.add_argument(
        "--output-name",
        default=None,
        help="输出文件名(不含 .json)。默认从 input 文件名推断。",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只验证环境与 1 次最小 LLM 调用,不跑全量任务",
    )
    args = parser.parse_args()

    load_env()

    if args.dry_run:
        return dry_run(args.input)

    output_name = args.output_name or args.input.stem
    return build_graph(args.input, output_name)


if __name__ == "__main__":
    sys.exit(main())
