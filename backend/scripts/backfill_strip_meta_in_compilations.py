"""一次性 backfill 脚本(2026-06-02 patch A)— 清除已存合并 sim narrative 中的 "> 语体:..." 残留.

背景:patch A 之前已生成的合并 sim 行(is_final_compilation=1)的 narrative 字段
里残留 N 段 "> 语体:..." 元数据行(每段 N-1 次,因为第 1 段的开头那次 strip 过会保留).

使用:
    python -m scripts.backfill_strip_meta_in_compilations [--dry-run]

策略:
  - 扫描 simulations WHERE is_final_compilation=1
  - 对每条 narrative 调 _strip_narrative_meta_lines + 重写 narrative 字段
  - --dry-run 模式只统计有多少条会变,不真改

为什么用脚本不用 migration:
  - migration 适合 schema 改动;此处只改数据
  - 想给用户 dry-run 预览权 + 失败回滚
"""
import argparse
import sys

from app.db import get_connection, execute, fetch_all
from app.services.compile_final_work import _strip_narrative_meta_lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="只统计,不真改",
    )
    args = parser.parse_args()

    conn = get_connection()
    try:
        rows = fetch_all(
            conn,
            "SELECT id, narrative FROM simulations WHERE is_final_compilation=1",
        )
        if not rows:
            print("No compilation sims found.")
            return 0

        changed = 0
        unchanged = 0
        for row in rows:
            sim_id = row["id"]
            old_narr = row["narrative"] or ""
            new_narr = _strip_narrative_meta_lines(old_narr)
            if new_narr == old_narr:
                unchanged += 1
                continue
            changed += 1
            diff_chars = len(old_narr) - len(new_narr)
            print(f"  {sim_id[:8]}... {len(old_narr)} → {len(new_narr)} 字 (-{diff_chars})")
            if not args.dry_run:
                execute(
                    conn,
                    "UPDATE simulations SET narrative=? WHERE id=?",
                    (new_narr, sim_id),
                )

        if not args.dry_run:
            conn.commit()
        action = "(dry-run, not committed)" if args.dry_run else "✓ committed"
        print(f"\n汇总:{changed} 篇有 meta 行残留 {action} · {unchanged} 篇无需改动")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
