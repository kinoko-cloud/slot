# HANDOVER - 2026-02-13 14:08 作成

## 今日の問題と対応状況

### 問題1: 履歴データが途中で切れていた（ART=0表示）
**原因**: `scrapers/daidata_detail_history.py` の正規表現バグ
- `過去\d+日` で早期終了していた（ページ上部の「過去7日」で切れる）

**修正済み** (コミット: cdf5b268b4):
```python
# 旧
r'大当たり\s+スタート\s+出玉\s+種別\s+時間(.+?)(?:過去\d+日|ページ先頭|台データオンライン|$)'

# 新
r'大当たり\s+スタート\s+出玉\s+種別\s+時間(.+?)(?:グラフ表示|差枚数推移|データを表示中|関連台|機種名|ページ先頭へ|$)'
```

### 問題2: ART=0がサイトに表示されていた
**原因**: `scripts/generate_static.py` がデータ欠損時にART=0を埋めていた

**修正済み** (コミット: 7182eec267):
- `scripts/generate_static.py` 775-779行目: ART=0はスキップ
- `web/templates/index.html` 36行目, 857行目: ART>0のみ表示

### 問題3: availability.json（リアルタイムデータ）が12時間前で止まっている
**原因**: GitHub Actionsのスケジュール実行が今日は動いていない

**状況**: 
- ローカルでfetch_daidata_availability.pyを実行したが、10分以上かかりタイムアウト
- availability.jsonは **まだ更新できていない**

**TODO**:
```bash
# ロックファイル削除
rm -f /tmp/slot_fetch.lock /tmp/slot_*_update.lock

# 実行（10分以上かかる）
cd /home/riichi/works/slot
python3 scripts/fetch_daidata_availability.py

# 完了したら再生成
python3 scripts/generate_static.py
git add -A && git commit -m "fix: availability更新" && git push origin main
```

### 問題4: ヘルスチェックでART=0を検知できていなかった
**修正済み** (コミット: 0d929e6970):
- `scripts/health_check.py` に `check_art_zero_anomaly()` 関数を追加

---

## 未完了タスク

1. **availability.json更新**: ローカルで実行中だったが、タイムアウトで失敗。再実行が必要
2. **island_akihabara**: papimoソースのデータが2/11で止まっている（別問題）
3. **GitHub Actionsスケジュール**: 今日動いていない原因不明。ワークフロー自体はactive

---

## Git差分（今日のコミット）

```
7182eec267 fix: ART=0を表示しないように修正
31bd89329d fix: 2/10-12のART=0異常データを削除＆再生成
4f844b26c5 fix: 履歴データ修正後のサイト再生成
0d929e6970 feat: ART=0異常検知をヘルスチェックに追加
cdf5b268b4 fix: 履歴取得の正規表現修正 - 過去Xで早期終了するバグを修正
```

---

## ファイル変更一覧

| ファイル | 変更内容 |
|---------|---------|
| `scrapers/daidata_detail_history.py` | 正規表現の終了条件修正 |
| `scripts/generate_static.py` | ART=0スキップ処理追加 |
| `scripts/health_check.py` | ART=0異常検知関数追加 |
| `web/templates/index.html` | ART=0非表示条件追加 |
| `data/history/*` | 2/10-12のART<=2データ削除 |
| `docs/*` | 再生成済み |

---

## 確認コマンド

```bash
# 現在のavailability.jsonの鮮度確認
python3 -c "import json; d=json.load(open('data/availability.json')); print(d.get('fetched_at'))"

# ヘルスチェック実行
python3 scripts/health_check.py

# サイト再生成
python3 scripts/generate_static.py
```
