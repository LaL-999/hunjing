"""每日 credit cron 入口 — Sprint C.5(2026-05-13)。

调度方式:
  - Linux crontab:
      0 0 * * * cd /path/to/huimeng && .venv/bin/python backend/scripts/run_credit_cron.py
  - Windows 任务计划:
      操作 → "启动程序" → 程序 .venv\\Scripts\\python.exe
                       → 参数 backend\\scripts\\run_credit_cron.py
                       → 起始位置 项目根
      触发器 → 每天 0:00(本地时区,内部按 UTC 算)
  - Docker / Kubernetes(部署后):
      kubectl create cronjob credit-daily --schedule="0 0 * * *" --image=...

任务内容(详 services/credit_cron.run_daily_credit_jobs):
  1. month_reset_all_users — 月初为所有用户清旧池 + 发新月度 credit
  2. expire_addon_lots — 加购 lot 到期处理(is_expired=1 + addon_credits 减)
  3. 提醒邮件(月末 3 天 + 加购到期前 30/7/1 天)

退出码:
  0 — 全部成功
  1 — cron 跑了但有部分 error(报告中 errors 数组非空)
  2 — 整体失败(import / DB 连接等致命错误)

输出:JSON 报告 stdout(便于运维 monitoring 抓取)。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# 让 scripts/ 下脚本能 import app
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# Windows 控制台中文输出 utf-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def main() -> int:
    try:
        from app.services.credit_cron import run_daily_credit_jobs
    except Exception as e:
        print(f"FATAL: import credit_cron failed: {e}", file=sys.stderr)
        return 2

    try:
        report = run_daily_credit_jobs()
    except Exception as e:
        print(f"FATAL: run_daily_credit_jobs failed: {e}", file=sys.stderr)
        return 2

    print(json.dumps(report, ensure_ascii=False, indent=2))

    # 退出码:有 error 则 1
    has_errors = (
        bool(report.get("month_reset", {}).get("errors"))
        or bool(report.get("addon_expire", {}).get("errors"))
    )
    return 1 if has_errors else 0


if __name__ == "__main__":
    sys.exit(main())
