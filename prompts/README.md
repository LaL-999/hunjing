# prompts/ — 所有 LLM prompt 集中管理

## 设计原则

**prompt 是产品资产,不是代码碎片**。所有发给 LLM 的 system / user prompt 都放在
这里,以 Markdown 文件形式存在,不要散落在 Python 字符串里。

## 为什么这么做

1. **一处修改,全产品生效** —— 改一个 .md 文件,任意作品(红楼梦/天龙八部/福尔
   摩斯/JK 罗琳)产出立刻获得改进;比"挨个手工修 JSON 数据"高一个数量级
2. **跨语言 / 跨工程师** —— 文学背景的 prompt designer 不需要懂 Python 也能改
3. **版本化** —— 用 git diff 直接看 prompt 演进,不被代码 diff 噪声淹没
4. **A/B 测试便捷** —— 复制一份 .md 改成 _v2,代码里一行切换变量就能对比

## 文件清单

| 文件 | 用途 | 占位符 |
|---|---|---|
| `character_generator.md` | 从原作文本生成单个角色完整档案 JSON(中间态文件抽图谱时复用) | `{work_name}` `{character_name}` `{language_style}` `{source_excerpts}` |
| `character_focus.md` | 角色对焦 v3 LOCKED — 用户对角色档案做审视并出 5 类建议 | `{project_json}` `{characters_json}` `{relationships_json}` |
| `agent_system.md` | 单个 agent 的 system prompt(扮演该角色) | `{name}` `{identity}` `{personality}` `{relationships}` `{high_freq_words}` `{quotes}` `{speech_style_notes}` `{no_go_list}` `{behavioral_rules}` |
| `director_system.md` | Director(导演)的 system prompt | `{anchor_event}` `{divergence}` |
| `composer.md` | Composer v1 LOCKED — 1.Q 起替代旧 composer_a/c/custom,7 类语体自适应 + 用户 hint 优先 | `{target_chars}` `{custom_style_hint}` `{project_meta}` |
| `self_consistency_guardian.md` | 自洽守护者 v1 LOCKED — 1.R 推演产物诊断 8 维度(含 v6.1 字段中文化铁律 8) | `{divergence}` `{characters_snapshot}` `{narrative}` `{timeline}` |

## 使用约定

**Python 代码读模板**:

```python
from pathlib import Path

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"

def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")

system = load_prompt("agent_system.md").format(
    name=char["name"],
    identity=char["identity"],
    # ...
)
```

**占位符规范**:用 Python `str.format` 风格的 `{name}`。如果 prompt 文本中要出现
字面量大括号(如 JSON schema 示例),写成 `{{` 和 `}}`。

## 演进与教训沉淀

每个 prompt 文件顶部用注释标注**当前版本号 + 关键教训出处**。新教训不用反复
追加历史,统一指向 `docs/MVP迁移指南.md` 的相关章节。

例:

```markdown
<!--
版本: v4 (Phase E iteration #3)
教训出处: docs/MVP迁移指南.md 第 5 节(Composer 9 条铁律)
-->
```
