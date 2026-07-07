#!/bin/bash
# local_daidata_cron.sh - ローカルWSLからdaidataを取得してGitHubにプッシュ
#
# 背景:
#   daidata.goraggio.com は AWS CloudFront WAF で GitHub Actions(AWS IP)をブロック。
#   ローカルの日本IPからのみアクセス可能。
#
# crontab設定例（毎時30分に実行・営業時間全体をカバー）:
#   30 1-23 * * * bash /home/riichi/works/slot/scripts/local_daidata_cron.sh >> /tmp/slot_local_cron.log 2>&1
# 2026-07-06: 旧設定(1-14)だと14:30で止まり夕方以降カバー漏れだったため1-23に拡張

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

# --- daidata取得（東京喰種） ---
echo "--- 東京喰種取得 ---"
timeout 600 $PYTHON "$REPO/scripts/scrapers_v2/fetch_all.py" --tokyoghoul-only --daidata-only 2>&1 || echo "⚠️ tokyoghoul取得失敗"

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

git add data/availability.json data/.browser_state/daidata_state.json data/history/ data/patterns/ 2>/dev/null || true

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
        echo "⚠️ Push失敗 (試行$i/3)、スマートマージで再試行..."
        git fetch origin 2>&1

        # JSONスマートマージ：data/とdocs/はリモートより自分を優先してマージ
        python3 << 'PYEOF'
import subprocess, json
from pathlib import Path

result = subprocess.run(['git', 'diff', '--name-only', 'HEAD', 'origin/main'], capture_output=True, text=True)
diff_files = [f for f in result.stdout.strip().split('\n') if f]

# まずrebaseを試みる
rb = subprocess.run(['git', 'rebase', 'origin/main'], capture_output=True, text=True)
if rb.returncode == 0:
    print("rebase成功")
    exit(0)

# rebase失敗 → 手動マージ
subprocess.run(['git', 'rebase', '--abort'], capture_output=True)
print("rebase失敗 → スマートマージ開始")

# 自分の変更をパッチとして保存
my_changes = subprocess.run(['git', 'diff', 'HEAD~1', '--', 'data/', 'docs/'], capture_output=True, text=True).stdout

# リモートの最新に戻す
subprocess.run(['git', 'reset', '--hard', 'origin/main'], capture_output=True)

# パッチを適用
if my_changes:
    result = subprocess.run(['git', 'apply', '--3way', '--whitespace=nowarn', '-'], input=my_changes, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"パッチ適用失敗: {result.stderr[:200]}")
        # 競合ファイルをJSON的にマージ
        conflict_files = subprocess.run(['git', 'diff', '--name-only', '--diff-filter=U'], capture_output=True, text=True).stdout.strip().split('\n')
        for cf in conflict_files:
            if not cf or not cf.endswith('.json'):
                continue
            try:
                ours_out = subprocess.run(['git', 'show', f':2:{cf}'], capture_output=True, text=True)
                theirs_out = subprocess.run(['git', 'show', f':3:{cf}'], capture_output=True, text=True)
                if ours_out.returncode != 0 or theirs_out.returncode != 0:
                    continue
                ours = json.loads(ours_out.stdout)
                theirs = json.loads(theirs_out.stdout)
                if 'days' in ours and 'days' in theirs:
                    their_days = {d['date']: d for d in theirs.get('days', [])}
                    our_days = {d['date']: d for d in ours.get('days', [])}
                    merged = dict(ours)
                    merged['days'] = sorted({**their_days, **our_days}.values(), key=lambda x: x['date'], reverse=True)
                    merged['last_updated'] = max(ours.get('last_updated', ''), theirs.get('last_updated', ''))
                else:
                    ours_t = ours.get('fetched_at', ours.get('last_updated', ''))
                    theirs_t = theirs.get('fetched_at', theirs.get('last_updated', ''))
                    merged = ours if ours_t >= theirs_t else theirs
                Path(cf).write_text(json.dumps(merged, ensure_ascii=False, indent=2) + '\n')
                subprocess.run(['git', 'add', cf])
                print(f"マージ: {cf}")
            except Exception as e:
                print(f"マージ失敗 {cf}: {e}")

# HTMLの競合: 自分のバージョンを採用
for cf in subprocess.run(['git', 'diff', '--name-only', '--diff-filter=U'], capture_output=True, text=True).stdout.strip().split('\n'):
    if cf and cf.endswith('.html'):
        subprocess.run(['git', 'checkout', '--ours', cf])
        subprocess.run(['git', 'add', cf])

subprocess.run(['git', 'add', 'data/', 'docs/'])
PYEOF

        # コミットして再プッシュ
        git diff --staged --quiet || git commit -m "auto: リアルタイムデータ更新 $(TZ='Asia/Tokyo' date +'%H:%M')"
    done
fi

echo "=== 完了 $(TZ='Asia/Tokyo' date +'%H:%M:%S') ==="
