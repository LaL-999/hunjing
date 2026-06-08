"""一次性脚本 — 批量把 insights-frontend 的所有 .vue 文件 px 改 rem(VD on insights).

设计:
  - root font-size 80% 由 App.vue 内 html { font-size: 80%; } 控制,这里只做单位换算
  - 16px 基准:1rem = 16px(html font-size: 80% 后 1rem = 12.8px = 80% × 16)
  - 跳过 border-* / outline / box-shadow / text-shadow(细线变 rem 容易亚像素丢失)
  - border-radius 保留替换(它是装饰半径,缩 80% 没问题)
  - SVG 的 width / height 属性用 plain number 不带 px 后缀,不会被匹配

跑法:py scripts\\vd_insights_px_to_rem.py
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path("C:/Users/Administrator/Desktop/huimeng/insights-frontend/src")
PX_PATTERN = re.compile(r"(\d+(?:\.\d+)?)px\b")


def px_to_rem(px_str: str) -> str:
    num = float(px_str)
    rem = num / 16
    # 美化输出:整数不带小数点
    if rem == int(rem):
        return f"{int(rem)}rem"
    # 浮点数最多 5 位精度,去尾零
    formatted = f"{rem:.5f}".rstrip("0").rstrip(".")
    return f"{formatted}rem"


def should_skip_line(line: str) -> bool:
    """跳过 border-* / outline / shadow 行,保留 border-radius."""
    stripped = line.strip().lower()
    # border-radius 是装饰,可以缩
    if stripped.startswith("border-radius"):
        return False
    # 其它 border / outline / shadow 跳过(细线不要变 rem)
    SKIP_PREFIXES = (
        "border:",
        "border-top",
        "border-bottom",
        "border-left",
        "border-right",
        "border-width",
        "border-color",
        "border ",
        "outline:",
        "outline ",
        "box-shadow",
        "text-shadow",
    )
    return any(stripped.startswith(p) for p in SKIP_PREFIXES)


def convert_text(text: str) -> str:
    out_lines = []
    for line in text.split("\n"):
        if should_skip_line(line):
            out_lines.append(line)
        else:
            out_lines.append(
                PX_PATTERN.sub(lambda m: px_to_rem(m.group(1)), line)
            )
    return "\n".join(out_lines)


def process_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    new_text = convert_text(text)
    if new_text == text:
        return False
    path.write_text(new_text, encoding="utf-8")
    return True


def main():
    vue_files = sorted(ROOT.rglob("*.vue"))
    changed = []
    for vf in vue_files:
        if process_file(vf):
            changed.append(vf)
    print(f"扫描 {len(vue_files)} 个 .vue,改了 {len(changed)} 个")
    for vf in changed:
        print(f"  {vf.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
