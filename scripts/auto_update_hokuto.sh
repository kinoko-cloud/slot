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
if timeout 900 python3 scripts/fetch_daidata_availability.py --hokuto-only >> "$LOGFILE" 2>&1; then
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
if ! git diff --quiet docs/ || ! git diff --quiet data/; then
    # rebase/merge中なら強制abort
    if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
        git rebase --abort >> "$LOGFILE" 2>&1 || true
    fi
    if [ -f .git/MERGE_HEAD ]; then
        git merge --abort >> "$LOGFILE" 2>&1 || true
    fi
    rm -f .git/index.lock 2>/dev/null || true
    
    git add docs/ data/availability.json data/history/
    git commit -m "auto: 北斗更新 $(date '+%H:%M')" >> "$LOGFILE" 2>&1 || true
    
    # fetchしてリモートとの差分を確認
    git fetch origin >> "$LOGFILE" 2>&1 || true
    
    # pull（no-rebase、コンフリクト時はours優先）
    if ! git pull --no-rebase -X ours origin main >> "$LOGFILE" 2>&1; then
        log "pull失敗 - 強制リセット後再試行..."
        git reset --hard origin/main >> "$LOGFILE" 2>&1 || true
    fi
    
    # push（最大3回リトライ）
    for i in 1 2 3; do
        if git push origin main >> "$LOGFILE" 2>&1; then
            log "デプロイ完了"
            break
        fi
        sleep 5
        git pull --no-rebase -X ours origin main >> "$LOGFILE" 2>&1 || true
    done
fi

log "=== 北斗高速更新完了 ==="
