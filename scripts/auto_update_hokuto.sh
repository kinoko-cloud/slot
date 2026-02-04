#!/bin/bash
# 北斗高速更新スクリプト（10分間隔、SBJと5分ずらし）
set -e
cd /home/riichi/works/slot

if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi

LOGFILE="logs/auto_update.log"
LOCKFILE="/tmp/slot_hokuto_update.lock"
mkdir -p logs

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [北斗] $1" >> "$LOGFILE"
}

exec 200>"$LOCKFILE"
if ! flock -n 200; then
    exit 0
fi

log "=== 北斗高速更新開始 ==="

# 北斗のみ取得
if timeout 600 python3 scripts/fetch_daidata_availability.py --hokuto-only >> "$LOGFILE" 2>&1; then
    log "北斗データ取得完了"
else
    log "北斗データ取得失敗（exit: $?）"
    exit 1
fi

# 蓄積データ更新
python3 scripts/sync_realtime_to_history.py >> "$LOGFILE" 2>&1 || log "蓄積データ更新失敗（続行）"

# 静的サイト生成
if python3 scripts/generate_static.py >> "$LOGFILE" 2>&1; then
    log "静的サイト生成完了"
else
    log "静的サイト生成失敗"
    exit 1
fi

# デプロイ（変更があれば）
if ! git diff --quiet docs/; then
    git add docs/ data/availability.json data/history/
    git commit -m "auto: 北斗更新 $(date '+%H:%M')" >> "$LOGFILE" 2>&1
    git pull --rebase origin main >> "$LOGFILE" 2>&1 || true
    git push origin main >> "$LOGFILE" 2>&1 && log "デプロイ完了"
fi

log "=== 北斗高速更新完了 ==="
