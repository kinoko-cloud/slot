#!/bin/bash
# 営業中の自動更新スクリプト
# cronで15分ごとに実行: */15 10-22 * * * /home/riichi/works/slot/scripts/auto_update.sh
#
# 全9店舗×機種のリアルタイムデータを取得し、静的サイトを再生成してデプロイ
# GitHub Actionsと並行して動作（ローカルはPlaywright直接実行）

set -e
cd /home/riichi/works/slot

# .venvがあればactivate
if [ -f .venv/bin/activate ]; then
    source .venv/bin/activate
fi

LOGFILE="logs/auto_update.log"
LOCKFILE="/tmp/slot_auto_update.lock"
mkdir -p logs

# ログローテーション（1MB超えたら切り詰め）
if [ -f "$LOGFILE" ] && [ "$(stat -c%s "$LOGFILE" 2>/dev/null || echo 0)" -gt 1048576 ]; then
    tail -500 "$LOGFILE" > "${LOGFILE}.tmp" && mv "${LOGFILE}.tmp" "$LOGFILE"
fi

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

# 0. Git状態をクリーンにしてからfetch
# rebase/merge中なら強制的にabort
if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
    git rebase --abort >> "$LOGFILE" 2>&1 || true
    log "rebase abort"
fi
if [ -f .git/MERGE_HEAD ]; then
    git merge --abort >> "$LOGFILE" 2>&1 || true
    log "merge abort"
fi
rm -f .git/index.lock 2>/dev/null || true

# 未コミット変更はdata/とdocs/以外を保持、data/docs/は捨てる
git checkout -- data/ docs/ >> "$LOGFILE" 2>&1 || true

# git fetchしてリモートとの差分を確認
git fetch origin >> "$LOGFILE" 2>&1 || true

# ローカルがリモートより進んでいる場合はそのまま（後でpushする）
# ローカルがリモートより遅れている場合はpull
BEHIND=$(git rev-list --count HEAD..origin/main 2>/dev/null || echo "0")
if [ "$BEHIND" -gt 0 ]; then
    # リモートが先に進んでいる → pull（no-rebase、コンフリクト時はoursを優先）
    if git pull --no-rebase -X ours origin main >> "$LOGFILE" 2>&1; then
        log "git pull完了（$BEHIND コミット取得）"
    else
        # 失敗したらreset
        git reset --hard origin/main >> "$LOGFILE" 2>&1 || true
        log "git reset --hard origin/main（pull失敗のため強制リセット）"
    fi
fi

# 1. リアルタイムデータ取得（全9店舗: daidata 7店 + papimo 2店）
log "データ取得中（全店舗）..."
FETCH_SUCCESS=false
if timeout 2700 python3 scripts/fetch_daidata_availability.py >> "$LOGFILE" 2>&1; then
    log "データ取得完了"
    FETCH_SUCCESS=true
else
    EXIT_CODE=$?
    if [ $EXIT_CODE -eq 124 ]; then
        log "データ取得タイムアウト（10分）"
    else
        log "データ取得失敗（exit code: $EXIT_CODE）"
    fi
    # availability.jsonが古くても静的生成は実行
fi

# availability.jsonの検証
if [ -f data/availability.json ]; then
    STORE_COUNT=$(python3 -c "import json; d=json.load(open('data/availability.json')); print(len(d.get('stores',{})))" 2>/dev/null || echo "0")
    log "availability.json: ${STORE_COUNT}店舗"
    if [ "$STORE_COUNT" -lt 5 ]; then
        log "WARNING: 店舗数が少ない（期待: 9）"
    fi
else
    log "WARNING: availability.json が存在しない"
fi

# 2. 蓄積データ更新（リアルタイム→History同期）
log "蓄積データ更新中..."
if python3 scripts/sync_realtime_to_history.py >> "$LOGFILE" 2>&1; then
    log "蓄積データ更新完了"
else
    log "蓄積データ更新失敗（続行）"
fi

# 3. 静的サイト生成
log "静的サイト生成中..."
if python3 scripts/generate_static.py >> "$LOGFILE" 2>&1; then
    log "静的サイト生成完了"
else
    log "静的サイト生成失敗"
    exit 1
fi

# 3. git push（差分がある場合のみ）
git add data/availability.json data/history/ docs/
if git diff --staged --quiet; then
    log "変更なし、スキップ"
else
    log "変更あり、デプロイ中..."
    git commit -m "auto: リアルタイム更新 $(date '+%H:%M')" --no-verify >> "$LOGFILE" 2>&1
    # リトライ付きpush（最大5回、メイン→セカンダリフォールバック）
    PUSH_SUCCESS=false
    for i in 1 2 3 4 5; do
        # pull --rebase でコンフリクトがあればdocs/はローカル優先
        if ! git pull --rebase origin main >> "$LOGFILE" 2>&1; then
            if [ -d .git/rebase-merge ] || [ -d .git/rebase-apply ]; then
                git checkout --ours docs/ >> "$LOGFILE" 2>&1 || true
                git add docs/ >> "$LOGFILE" 2>&1 || true
                GIT_EDITOR=true git rebase --continue >> "$LOGFILE" 2>&1 || git rebase --skip >> "$LOGFILE" 2>&1 || git rebase --abort >> "$LOGFILE" 2>&1 || true
            fi
        fi
        if git push >> "$LOGFILE" 2>&1; then
            log "デプロイ完了 (attempt $i, origin)"
            PUSH_SUCCESS=true
            break
        fi
        # セカンダリアカウントでリトライ
        if git push secondary main >> "$LOGFILE" 2>&1; then
            log "デプロイ完了 (attempt $i, secondary)"
            PUSH_SUCCESS=true
            break
        fi
        log "Push失敗 (attempt $i/5)、5秒待機..."
        sleep 5
    done
    if [ "$PUSH_SUCCESS" = false ]; then
        log "ERROR: Push失敗（5回試行、origin+secondary両方）"
    fi
fi

log "=== 自動更新完了 ==="
