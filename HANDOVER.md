# slot プロジェクト 引き継ぎ

## 今どこ？

**2/12の履歴データ取得中**（バックグラウンドで実行中）

## 直近の修正（2/13 0:20）

### 問題: 0:00過ぎても日付が古いまま
- 的中率が2/11のまま（2/12であるべき）
- 3日分の履歴も古い日付

### 原因
静的サイト（docs/）が23:00に生成されたまま。
日付変更後に再生成されていなかった。

### 対策（実装済み）
deploy-static.ymlに0:05と0:30の実行を追加。
これで毎日0:00過ぎに日付が正しく更新される。

確認コマンド：
```bash
ps aux | grep batch_update | grep -v grep
```

## 直近の課題

### 1. batch_update_history.py の改善が必要
**問題:** 76台を1台ずつ順番処理 → 30分以上かかる、タイムアウトで中断

**RSさんの指示:**
> 長いタイムアウトより、並列数を可変で減らしたり、リトライ回数の方がいいのでは？

**改善案:**
- 並列数を可変（5→3→1と段階的に）
- 短タイムアウト（30秒）+ リトライ3回
- 部分保存（10台ごとに保存）

### 2. 毎朝の前日データ残り問題
**問題:** 営業開始（10:00）に前日データが表示される

**対策（実装済み）:**
- 9:55にプリフェッチ
- 10:05に強制チェック
- フロントエンドに古いデータ警告バナー

**明日10:00に動作確認必要**

## 本日実装したこと

1. **古いデータ警告** - generate_static.py + index.html
2. **朝のプリフェッチ** - auto-recovery.yml
3. **GitHub Actions冗長化** - 毎時4回実行

## よく使うコマンド

```bash
# 履歴データ状況確認
cd /home/riichi/works/slot
python3 -c "
import json
from pathlib import Path
for f in Path('data/history').glob('*'):
    if f.is_dir():
        dates = set()
        for uf in list(f.glob('*.json'))[:3]:
            try:
                d = json.load(open(uf))
                dates.update(day['date'] for day in d.get('days',[]) if day.get('date'))
            except: pass
        print(f'{f.name}: {max(dates) if dates else \"N/A\"}')"

# 手動でデータ取得
python3 scripts/batch_update_history.py --target-date 2026-02-12

# リアルタイムデータ取得
python3 scripts/fetch_daidata_availability.py

# 静的サイト生成
python3 scripts/generate_static.py

# GitHub Actionsの状況
gh run list --limit 5
```

## ファイル構成

```
scripts/
  batch_update_history.py  ← 履歴データ取得（要改善）
  fetch_daidata_availability.py  ← リアルタイムデータ
  generate_static.py  ← サイト生成
  health_check.py  ← ヘルスチェック

.github/workflows/
  auto-recovery.yml  ← 自動復旧（9:55, 10:05, 毎時15分/45分）
  fetch-availability-parallel.yml  ← データ取得（毎時0分/30分）
  deploy-static.yml  ← サイト更新（毎時5分/35分）

data/
  availability.json  ← リアルタイムデータ
  history/  ← 履歴データ（店舗別/台番号.json）
```

## 解決した問題（2/13 01:00）

### 履歴データが途中で切れていた問題 ✅
**根本原因:** `daidata_detail_history.py`の正規表現バグ
- 「過去最大持ち玉」という文字列がページ上部にあり、正規表現が早期終了
- 「本日の大当たり履歴詳細」がない場合のフォールバックがなかった

**修正内容:**
1. 正規表現を`(?:過去\d+日|ページ先頭|台データオンライン|$)`に変更
2. セクションが見つからない場合はテキスト全体から取得
3. 履歴を時間順（古い→新しい）にソート

**データ修復:**
- 390ファイル、4148日分の壊れたデータをクリア
- 再取得バッチ実行中（263台 + papimo 10台）

---

## 過去の問題（アーカイブ）

### 1. 履歴データが途中で切れている（解決済み）
**症状:** 2/11, 2/12の履歴データが10時〜12時台で終わっている（59件）
- shibuya_espass_hokuto2: 10:06〜10:27で終了
- akiba_espass_hokuto2: 11:05〜12:22で終了
- 本来は閉店（22:00-23:00）まであるべき

**原因候補:**
1. batch_update_history.pyが途中で止まった
2. データソース（daidata）の前日データ消失タイミング
3. 取得処理のバグ

**確認コマンド:**
```bash
python3 -c "
import json
from pathlib import Path
for uf in list(Path('data/history/shibuya_espass_hokuto2').glob('*.json'))[:3]:
    d = json.load(open(uf))
    for day in d.get('days',[]):
        if day.get('date') == '2026-02-11':
            hist = day.get('history',[])
            print(f\"{uf.stem}: 最後={hist[-1].get('time') if hist else 'N/A'}\")
"
```

### 2. 「もっと見る」のグラフが直線
**症状:** 営業前モードで「もっと見る」の台のグラフが全部まっすぐの線
**原因:** 上記と同じ。履歴データが取得できていない。

---

## 根本原因

**全ての問題は「履歴データ取得の失敗」が原因**

RSさん指摘:
> 2件未満はほぼないよ、ありえない、ただデータ取得できてないのを疑って
> なんで途切れているのに気づかないで時が過ぎてるの？検知できてないのがおかしい

---

## 実装した対策（2/13 0:35）

### 1. health_check.py に完全性チェック追加
`check_history_completeness()` を追加：
- **閉店後（23時以降）**: 当日データの最終時刻が20:00以降かチェック
- **営業中（12時以降）**: 当日データの最終時刻が現在時刻-2時間以降かチェック
- **開店前（0-10時）**: 前日データの最終時刻が20:00以降かチェック
- 10%以上の台で問題があればエラー

### 2. nightly-update.yml に batch_update 追加
- 23:00のnightly-updateでbatch_update_history.pyを実行
- リトライ3回、タイムアウト10分
- 完了後に完全性検証

### 残りの課題
- auto-recoveryで履歴データ異常時に再取得する仕組み
- 営業中の定期チェック強化

## 的中率

2/12: 44/59 = 74.6%
