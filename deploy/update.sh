#!/usr/bin/env bash
# 浑晶 · 重新部署(更新代码后跑)
# 用法:sudo bash /opt/huimeng/app/deploy/update.sh
# 做:拉最新代码 → 更新后端依赖 → 重建前端 → 重启后端 + reload nginx
set -euo pipefail

APP=/opt/huimeng/app
VENV=/opt/huimeng/venv
API_BASE=https://api.shuangdayeye.cn

echo "==[1/5] 拉取最新代码 =="
sudo -u huimeng git -C "$APP" pull --ff-only

echo "==[2/5] 后端依赖(editable 安装)=="
sudo -u huimeng "$VENV/bin/pip" install -e "$APP/backend" -q

echo "==[3/5] 构建前端(VITE_API_BASE=$API_BASE)=="
cd "$APP/frontend"
sudo -u huimeng npm ci
sudo -u huimeng env VITE_API_BASE="$API_BASE" NODE_OPTIONS=--max-old-space-size=3072 npm run build

echo "==[4/5] 重启后端(迁移在启动时自动跑)=="
systemctl restart huimeng-backend

echo "==[5/5] 校验 + reload nginx =="
nginx -t
systemctl reload nginx
sleep 2
systemctl --no-pager --lines=5 status huimeng-backend || true
echo "== 完成。后端日志:journalctl -u huimeng-backend -f =="
