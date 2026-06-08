"""全量 Python 语法预检脚本(2026-06-02 经验值 25 防再犯).

起源:中文字符串内嵌套 ASCII 双引号导致 SyntaxError 反复出现 4 次:
  - hard_constraints.py 第 101 行(已修)
  - schemas/simulation.py 含 emoji(已修)
  - simulation_service.py 多处(已修)
  - hard_constraints.py 第 83 / 86 行(2026-06-02 第 4 次)

用法:
    cd backend
    python scripts/check_python_syntax.py
    echo $?   # 0 = OK,非 0 = 有 SyntaxError

退出码:
    0    全部通过
    1    有 SyntaxError(stdout 报告具体文件 + 行)
    2    脚本本身出错

集成方法(任选):
  A. 手动跑:每次大改前后跑一次
  B. pytest 集成:tests/conftest.py 已加 session-start 钩子(每次 pytest 自动跑)
  C. git pre-commit:
       echo 'cd backend && python scripts/check_python_syntax.py' > .git/hooks/pre-commit
       chmod +x .git/hooks/pre-commit
"""
from __future__ import annotations

import compileall
import sys
from pathlib import Path


def main() -> int:
    backend_root = Path(__file__).resolve().parent.parent
    app_dir = backend_root / "app"
    if not app_dir.exists():
        print(f"✗ app/ 目录不存在:{app_dir}", file=sys.stderr)
        return 2

    print(f"扫描 {app_dir}...")
    # quiet=0 显示错误细节;ok=False 时 stderr 已有具体行号
    ok = compileall.compile_dir(
        str(app_dir),
        quiet=0,
        force=True,
        stripdir=str(backend_root),
    )

    if ok:
        print("[OK] 全部通过 — 无 SyntaxError")
        return 0
    else:
        print()
        print("[FAIL] 发现 SyntaxError!")
        print()
        print("常见根因(经验值 25)— 中文字符串内嵌套 ASCII 双引号:")
        print("    错误:'我说\"你听\"完整句'  → Python 把里面 \" 当作字符串结束")
        print("    正确:'我说「你听」完整句'  → 中文方括号")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
