# リアルタイム機能 統合チェックリスト

**目的**: リアルタイム機能が「また放置されてないか」を定期的に確認するためのチェックリスト

---

## 🔴 即座に確認すべき項目（毎週）

### 1. データ取得が動いているか
```bash
# availability.jsonの更新日時を確認（30分以内であること）
ls -la data/availability.json
python3 -c "import json; d=json.load(open('data/availability.json')); print('fetched_at:', d.get('fetched_at'))"

# 全9店舗が含まれるか
python3 -c "
import json
d = json.load(open('data/availability.json'))
stores = list(d.get('stores', {}).keys())
expected = [
    'shibuya_espass_sbj', 'shinjuku_espass_sbj', 'akiba_espass_sbj',
    'seibu_shinjuku_espass_sbj', 'island_akihabara_sbj',
    'shibuya_espass_hokuto', 'shinjuku_espass_hokuto', 'akiba_espass_hokuto',
    'island_akihabara_hokuto',
]
missing = [s for s in expected if s not in stores]
print(f'Stores: {len(stores)}/9')
if missing: print(f'MISSING: {missing}')
else: print('All stores present ✓')
"
```

### 2. GitHub Actionsが動いているか
```bash
# 最新のワークフロー実行を確認
gh run list --workflow="fetch-availability.yml" --limit=5
# → 15分ごとに実行されているか？最後の実行が30分以内か？
```

### 3. ローカルcronが動いているか
```bash
crontab -l | grep auto_update
# → */15 10-22 * * * /home/riichi/works/slot/scripts/auto_update.sh

# 最新のログを確認
tail -20 logs/auto_update.log
```

### 4. PythonAnywhere APIが応答するか
```bash
curl -s "https://autogmail.pythonanywhere.com/version"
# → 最新バージョン文字列

curl -s "https://autogmail.pythonanywhere.com/api/v2/recommend/shibuya_espass_sbj" | python3 -m json.tool | head -20
# → JSONが返ること、updated_atが最近であること
```

---

## 🟡 月次確認項目

### 5. 台番号の変更がないか
```bash
python3 scripts/verify_units.py
# → アラートが出ていないか
```

### 6. Cloudflare Pagesのデプロイ
```bash
# docs/ が最新のビルドか
ls -la docs/index.html
head -5 docs/metadata.json
```

### 7. 静的ビルドの検証
```bash
python3 scripts/validate_output.py
# → 全チェック PASS
```

---

## 🟢 自動チェック（validate_output.py統合用）

以下のチェックを `scripts/validate_output.py` に追加することで、
ビルド時に自動で検証できる：

- [ ] availability.jsonが存在し、24時間以内に更新されている
- [ ] 全9キーがavailability.jsonに含まれる
- [ ] 各店舗のunitsデータが空でない
- [ ] realtime.jsがdocs/static/に含まれる
- [ ] 全recommend/*.htmlにdata-store-key属性がある

---

## トラブルシューティング

### availability.jsonが古い
1. WSLが落ちてないか確認: `wsl --list -v` (PowerShell)
2. cronが動いてるか: `crontab -l`
3. 手動実行: `python3 scripts/fetch_daidata_availability.py`
4. Playwrightが壊れてないか: `python3 -c "from playwright.sync_api import sync_playwright; print('OK')"`

### GitHub Actionsが止まっている
1. `.github/workflows/fetch-availability.yml` のcronが正しいか
2. Actions設定でスケジュール実行が無効化されてないか
3. 手動実行: `gh workflow run "Fetch Availability"`

### PythonAnywhere APIがエラーを返す
1. バージョン確認: `curl https://autogmail.pythonanywhere.com/version`
2. デプロイ: `gh workflow run "Deploy to PythonAnywhere"` または手動deploy
3. PythonAnywhereコンソールでエラーログ確認

### ローカルcronが動かない
1. WSL再起動後はcronデーモン起動が必要: `sudo service cron start`
2. ログ確認: `tail -50 logs/auto_update.log`
3. 手動実行: `bash scripts/auto_update.sh`

---

## データフロー図（健全な状態）

```
[15分ごと] GitHub Actions fetch-availability.yml
    → Playwright実行（daidata 7店 + papimo 2店）
    → data/availability.json 更新
    → git push → Cloudflare Pages デプロイ
    → deploy.yml → PythonAnywhere git pull + reload

[15分ごと] ローカルcron auto_update.sh
    → 同上（ローカルPlaywright実行）
    → git push → 同上

[常時] 閲覧者がサイトを開く
    → realtime.js → PythonAnywhere /api/v2/
    → availability_checker.py → GitHub raw JSON or ローカルJSON
    → recommender.py → 最新データで予測
    → JSON応答 → UI更新

[営業中] recommend.html「最新データ取得」ボタン
    → /api/scrape → run_scraping() → GitHub JSON取得
    → /api/scrape_status ポーリング → 完了でリロード
```
