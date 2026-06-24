#!/usr/bin/env bash
# 浑晶 · 数据备份(SQLite 在线安全备份 + 有状态文件打包)
# 装成 cron / systemd timer 每 6h 跑一次:见 DEPLOY.md「备份」节。
# 用法:sudo -u huimeng bash /opt/huimeng/app/deploy/backup.sh
set -euo pipefail

APP=/opt/huimeng/app
DATA=$APP/backend/data
DEST=/opt/huimeng/backups
RETAIN_DAYS=14

mkdir -p "$DEST"
TS=$(date +%Y%m%d-%H%M%S)

# SQLite 必须用 .backup(在线一致快照),不能裸 cp 运行中的库
sqlite3 "$DATA/huimeng.db" ".backup '$DEST/huimeng-$TS.db'"

# 有状态文件(上传 / 支付凭证截图 / 收款码),排除 .db(已单独快照)
tar -czf "$DEST/data-$TS.tar.gz" --exclude='*.db' --exclude='*.db-*' -C "$APP/backend" data

# 清理过期备份
find "$DEST" -name 'huimeng-*.db'    -mtime +$RETAIN_DAYS -delete
find "$DEST" -name 'data-*.tar.gz'   -mtime +$RETAIN_DAYS -delete

echo "[backup] $TS -> $DEST (DB + data/, 保留 ${RETAIN_DAYS} 天)"

# ⚠️ 强烈建议加【离机】副本(本机磁盘坏了上面全没用):
# 配好阿里云 OSS 后取消注释(用 ossutil):
#   ossutil cp "$DEST/huimeng-$TS.db" oss://你的bucket/huimeng-backups/
#   ossutil cp "$DEST/data-$TS.tar.gz" oss://你的bucket/huimeng-backups/
