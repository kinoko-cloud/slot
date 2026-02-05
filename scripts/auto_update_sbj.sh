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
if ! git diff --quiet docs/ || ! git diff --quiet data/; then
    # コンフリクトがあれば自動解消（ローカル優先）
    if git status | grep -q "Unmerged\|both modified"; then
        log "コンフリクト検出 - 自動解消中..."
        git checkout --theirs docs/ data/availability.json 2>/dev/null || true
        git add docs/ data/availability.json
    fi
    
    git add docs/ data/availability.json data/history/
    git commit -m "auto: SBJ更新 $(date '+%H:%M')" >> "$LOGFILE" 2>&1 || true
    
    # pull時のコンフリクトも自動解消
    if ! git pull --rebase origin main >> "$LOGFILE" 2>&1; then
        log "pull失敗 - コンフリクト自動解消..."
        git rebase --abort 2>/dev/null || true
        git checkout --theirs docs/ data/availability.json 2>/dev/null || true
        git add -A
        git commit -m "auto: コンフリクト解消 $(date '+%H:%M')" >> "$LOGFILE" 2>&1 || true
        git pull --rebase origin main >> "$LOGFILE" 2>&1 || git pull origin main >> "$LOGFILE" 2>&1 || true
    fi
    
    git push origin main >> "$LOGFILE" 2>&1 && log "デプロイ完了"
fi

log "=== SBJ高速更新完了 ==="
