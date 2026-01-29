#!/bin/bash
# 営業中の自動更新スクリプト
# cronで15分ごとに実行: */15 10-22 * * * /home/riichi/works/slot/scripts/auto_update.sh

set -e
cd /home/riichi/works/slot

LOGFILE="logs/auto_update.log"
LOCKFILE="/tmp/slot_auto_update.lock"
mkdir -p logs

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOGFILE"
}

# 排他ロック（既に実行中なら即終了）
exec 200>"$LOCKFILE"
if ! flock -n 200; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 前回の更新がまだ実行中、スキップ" >> "$LOGFILE"
    exit 0
fi

log "=== 自動更新開始 ==="

# 1. リアルタイムデータ取得
log "データ取得中..."
if timeout 180 python3 scripts/fetch_daidata_availability.py >> "$LOGFILE" 2>&1; then
    log "データ取得完了"
else
    log "データ取得失敗（タイムアウトまたはエラー）"
    # availability.jsonが古くても静的生成は実行
fi

# 2. 静的サイト生成
log "静的サイト生成中..."
if python3 scripts/generate_static.py >> "$LOGFILE" 2>&1; then
    log "静的サイト生成完了"
else
    log "静的サイト生成失敗"
    exit 1
fi

# 3. git push（差分がある場合のみ）
git add data/availability.json docs/
if git diff --staged --quiet; then
    log "変更なし、スキップ"
else
    log "変更あり、デプロイ中..."
    git commit -m "auto: リアルタイム更新 $(date '+%H:%M')" --no-verify >> "$LOGFILE" 2>&1
    # リトライ付きpush
    for i in 1 2 3; do
        git pull --rebase origin main >> "$LOGFILE" 2>&1 || true
        if git push >> "$LOGFILE" 2>&1; then
            log "デプロイ完了"
            break
        fi
        log "Push失敗 (attempt $i/3)、リトライ..."
        sleep 3
    done
fi

log "=== 自動更新完了 ==="
