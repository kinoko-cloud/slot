#!/bin/bash
###############################################################################
# 夜間更新スクリプト (nightly_update.sh)
#
# 毎日23:00に実行され、以下を行う:
#  1. availability.json から history/ への確定データ移行
#  2. 静的サイト再生成
#  3. コミット & プッシュ
#
# crontab設定例:
#   0 23 * * * /home/riichi/works/slot/scripts/nightly_update.sh
###############################################################################

set -e

# プロジェクトルート
cd "$(dirname "$0")/.."

# ログファイル
LOGFILE="logs/nightly_update.log"
mkdir -p logs

# ログ関数
log() {
    echo "[$(TZ='Asia/Tokyo' date +'%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOGFILE"
}

log "========================================="
log "夜間更新開始"
log "========================================="

# 1. availability.json から history/ への確定データ移行
log "1. 確定データ移行中 (availability → history)..."
if python3 scripts/sync_realtime_to_history.py >> "$LOGFILE" 2>&1; then
    log "  ✓ 確定データ移行完了"
else
    log "  ⚠️ 確定データ移行失敗（続行）"
fi

# 2. 静的サイト再生成
log "2. 静的サイト生成中..."
if python3 scripts/generate_static.py >> "$LOGFILE" 2>&1; then
    log "  ✓ 静的サイト生成完了"
else
    log "  ❌ 静的サイト生成失敗"
    exit 1
fi

# 3. コミット & プッシュ
log "3. 変更をコミット..."
git add data/history/ docs/

if git diff --staged --quiet; then
    log "  変更なし、スキップ"
else
    TIMESTAMP=$(TZ='Asia/Tokyo' date +'%H:%M')
    git commit -m "auto: 北斗更新 ${TIMESTAMP}"

    log "  プッシュ中..."
    if git push origin main; then
        log "  ✓ プッシュ完了"
    else
        log "  ⚠️ プッシュ失敗（要確認）"
        exit 1
    fi
fi

log "========================================="
log "夜間更新完了"
log "========================================="
