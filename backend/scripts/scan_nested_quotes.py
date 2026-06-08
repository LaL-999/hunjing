"""扫描 Python 字符串里嵌套 ASCII 双引号(已踩 5+ 次坑,经验值 25/36).

模式:在 "abc中文xxx"yyy"" 之类的字符串里,中间的 "yyy" 用 ASCII " 包裹中文词,
会让 Python parser 误以为字符串在中间结束.

策略:每行扫,找形如 `"[^"\\]*"一-鿿[^"]*"` 的可疑模式.
注意:字符串拼接 `"a" + "b"` 是合法的,要排除.

使用:
    python -m scripts.scan_nested_quotes

输出:可疑文件和行号,人工 review.
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# 启发式正则:
# - 行内含至少 4 个 ASCII "
# - 中间至少有一个中文字符被 " 包裹
# - 但要排除合法 join / split / strip / repr 等用法
SUSPICIOUS_PATTERN = re.compile(
    r'"[^"\n]*[一-鿿]+[^"\n]*"[^",\s+)\]\}]*[一-鿿]+[^"\n]*"'
)

# 简单的 docstring 跳过(三引号开始/结束)
TRIPLE_QUOTE_PATTERN = re.compile(r'"""|\'\'\'')


def scan_file(path: Path) -> list[tuple[int, str]]:
    """扫一个文件,返回 [(行号, 行内容), ...]."""
    suspicious: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return suspicious

    in_triple = False
    for lineno, line in enumerate(text.splitlines(), start=1):
        # 跳过 docstring(简单启发,不完美)
        triple_count = len(TRIPLE_QUOTE_PATTERN.findall(line))
        if triple_count % 2 == 1:
            in_triple = not in_triple
            continue
        if in_triple:
            continue
        # 跳过注释
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        # 数 ASCII " 数量(简单启发:≥ 4 才可能嵌套)
        quote_count = line.count('"')
        if quote_count < 4:
            continue
        # 跳过明显合法用法
        if any(token in line for token in [
            '.join(', '.split(', '.strip(', '.replace(',
            'logger.', 'log.', 'print(',
        ]):
            # 这些是常见函数,字符串多但通常不嵌套
            # 仍然扫一次(可能仍踩雷)
            pass
        # 跑可疑正则
        if SUSPICIOUS_PATTERN.search(line):
            suspicious.append((lineno, line.rstrip()))
    return suspicious


def main() -> int:
    base = Path(__file__).resolve().parent.parent / "app"
    if not base.exists():
        print(f"路径不存在:{base}")
        return 1

    total_files = 0
    total_suspicious = 0
    by_file: dict[str, list[tuple[int, str]]] = {}

    for py_path in base.rglob("*.py"):
        total_files += 1
        suspicious = scan_file(py_path)
        if suspicious:
            rel = py_path.relative_to(base.parent)
            by_file[str(rel)] = suspicious
            total_suspicious += len(suspicious)

    if not by_file:
        print(f"[OK] 扫描 {total_files} 个 .py 文件,未发现可疑嵌套引号.")
        return 0

    print(f"[WARN] 扫描 {total_files} 个 .py 文件,发现 {total_suspicious} 处可疑(在 {len(by_file)} 个文件):\n")
    for filepath, lines in sorted(by_file.items()):
        print(f"\n📁 {filepath}")
        for lineno, content in lines:
            # 截断过长的行
            display = content if len(content) < 120 else content[:117] + "..."
            print(f"  L{lineno}: {display}")
    print("\n注:启发式扫描,需人工 review 判断是否真嵌套(合法的字符串拼接也会被报).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
