#!/usr/bin/env bash
# 浑晶 · 后端更新(改了后端 .py 代码后跑这个)
# 用法:sudo bash /opt/huimeng/app/deploy/update.sh
# 做:拉最新代码 → 更新后端依赖 → 重启后端(迁移启动时自动跑)→ reload nginx
#
# ⚠️ 本脚本【不构建前端】—— 服务器只有 1.6G 内存,前端构建会 OOM 卡死。
#    前端改动请走「本地构建 + 上传 dist」,见 OPS_MANUAL.md 第 3 节。
set -euo pipefail

APP=/opt/huimeng/app
VENV=/opt/huimeng/venv

echo "==[1/4] 拉取最新代码 =="
sudo -u huimeng git -C "$APP" pull --ff-only

echo "==[2/4] 更新后端依赖(没变化会很快跳过)=="
sudo -u huimeng "$VENV/bin/pip" install -e "$APP/backend" -q

echo "==[3/4] 重启后端(数据库迁移在启动时自动跑)=="
systemctl restart huimeng-backend

echo "==[4/4] 校验 + reload nginx =="
nginx -t
systemctl reload nginx
sleep 2
systemctl --no-pager --lines=5 status huimeng-backend || true
echo ""
echo "== 后端更新完成。"
echo "   健康检查:curl -s https://api.shuangdayeye.cn/api/health"
echo "   看日志:  sudo journalctl -u huimeng-backend -f"
echo "   ⚠️ 如果这次也改了前端,记得另走本地构建 + 上传 dist(OPS_MANUAL 第 3 节)。"
