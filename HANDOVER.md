# slot プロジェクト 引き継ぎ

## 今どこ？

**2/12の履歴データ取得中**（バックグラウンドで実行中）

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

## 的中率

2/12: 44/59 = 74.6%
