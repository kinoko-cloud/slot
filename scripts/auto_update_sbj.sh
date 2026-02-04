#!/bin/bash
# SBJ高速更新スクリプト（10分間隔）
set -e
cd /home/riichi/works/slot

if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi

LOGFILE="logs/auto_update.log"
LOCKFILE="/tmp/slot_sbj_update.lock"
mkdir -p logs

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [SBJ] $1" >> "$LOGFILE"
}

exec 200>"$LOCKFILE"
if ! flock -n 200; then
    exit 0
fi

log "=== SBJ高速更新開始 ==="

# SBJのみ取得（約5分）
if timeout 600 python3 scripts/fetch_daidata_availability.py --sbj-only >> "$LOGFILE" 2>&1; then
    log "SBJデータ取得完了"
else
    log "SBJデータ取得失敗（exit: $?）"
    exit 1
fi

# 静的サイト生成
if python3 scripts/generate_static.py >> "$LOGFILE" 2>&1; then
    log "静的サイト生成完了"
else
    log "静的サイト生成失敗"
    exit 1
fi

# デプロイ（変更があれば）
if ! git diff --quiet docs/; then
    git add docs/ data/availability.json
    git commit -m "auto: SBJ更新 $(date '+%H:%M')" >> "$LOGFILE" 2>&1
    git pull --rebase origin main >> "$LOGFILE" 2>&1 || true
    git push origin main >> "$LOGFILE" 2>&1 && log "デプロイ完了"
fi

log "=== SBJ高速更新完了 ==="
