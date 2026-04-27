#!/bin/bash
# local_daidata_cron.sh - ローカルWSLからdaidataを取得してGitHubにプッシュ
#
# 背景:
#   daidata.goraggio.com は AWS CloudFront WAF で GitHub Actions(AWS IP)をブロック。
#   ローカルの日本IPからのみアクセス可能。
#
# crontab設定例（毎時30分に実行）:
#   30 1-14 * * * bash /home/riichi/works/slot/scripts/local_daidata_cron.sh >> /tmp/slot_local_cron.log 2>&1

REPO=/home/riichi/works/slot
PYTHON=python3
NOW=$(TZ='Asia/Tokyo' date +'%Y-%m-%d %H:%M:%S')
HOUR=$(TZ='Asia/Tokyo' date +'%H')

echo "=== ローカルdaidata取得開始 $NOW ==="
cd "$REPO"

# ロックファイルをクリア（前回の異常終了対策）
rm -f /tmp/slot_fetch.lock

# 最新コードを取得（GitHub Actionsがpapimoデータをプッシュしているかもしれない）
echo "--- git pull ---"
git fetch origin 2>&1
git reset --hard origin/main 2>&1 || {
    echo "⚠️ git reset失敗 - 続行"
}

# --- daidata取得（主要店舗） ---
echo "--- SBJ取得 ---"
timeout 240 $PYTHON "$REPO/scripts/scrapers_v2/fetch_all.py" --priority-only --sbj-only --daidata-only 2>&1 || echo "⚠️ SBJ取得失敗"

echo "--- 真打吉宗取得 ---"
timeout 600 $PYTHON "$REPO/scripts/scrapers_v2/fetch_all.py" --priority-only --yoshitsune-only --daidata-only 2>&1 || echo "⚠️ yoshitsune取得失敗"

echo "--- ToLOVEる取得 ---"
timeout 600 $PYTHON "$REPO/scripts/scrapers_v2/fetch_all.py" --priority-only --toloveru-only --daidata-only 2>&1 || echo "⚠️ toloveru取得失敗"

# --- 静的HTML生成（営業時間中のみ） ---
if [ "$HOUR" -ge 10 ] && [ "$HOUR" -le 23 ]; then
    echo "--- HTML生成 ($HOUR時) ---"
    timeout 120 $PYTHON "$REPO/scripts/generate_static.py" 2>&1 || echo "⚠️ HTML生成失敗"
else
    echo "--- HTML生成スキップ ($HOUR時) ---"
fi

# --- コミット・プッシュ ---
git config user.name "local-cron"
git config user.email "local@localhost"

git add data/availability.json data/.browser_state/daidata_state.json docs/ 2>/dev/null || true

if git diff --staged --quiet; then
    echo "--- 変更なし ---"
else
    TIMESTAMP=$(TZ='Asia/Tokyo' date +'%H:%M')
    git commit -m "auto: ローカルdaidata取得 ${TIMESTAMP}"

    for i in 1 2 3; do
        if git push origin main 2>&1; then
            echo "✅ Push成功"
            break
        fi
        echo "⚠️ Push失敗 (試行$i/3)、リトライ..."
        # リモートの新しいコミットを取り込む
        git fetch origin 2>&1
        # 自分のコミットをリベース
        git rebase origin/main 2>&1 || {
            git rebase --abort 2>&1 || true
            # コンフリクト解決できない場合はforce push
            echo "⚠️ rebase失敗 - force push"
            git push --force origin main 2>&1 && break
        }
    done
fi

echo "=== 完了 $(TZ='Asia/Tokyo' date +'%H:%M:%S') ==="
