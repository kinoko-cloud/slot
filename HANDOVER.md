# HANDOVER.md - 引き継ぎ情報

## 最終更新: 2026-02-22 16:00

## 直近の変更 (2026-02-22)

### 1. max_medals取得修正
- DAIDATAから「最大持ち玉」を直接取得するように変更
- `scripts/scrapers_v2/daidata/scraper.py` - fetch_realtimeで取得
- `scripts/update_history_from_availability.py` - DAIDATAの値を優先

### 2. 連チャン判定閾値
- 30G → 65G に変更（config/rankings.pyのrenchain_threshold準拠）

### 3. 朝イチの昨日データ混入防止 ⚠️重要
- `scripts/scrapers_v2/fetch_all.py` を修正
- 日付変更後、詳細ページの日付が今日でない場合:
  - 一覧ページのARTを使わない
  - 全て0でリセット
- **仕様**: 朝イチは全部0で埋めて開店

### 4. GitHub Actionsワークフロー
- `fetch-availability-sub.yml`: コンフリクト対策（reset→再fetch）
- `daily-verify.yml`: コンフリクト時はforce push

### 5. 台番号変更
- 新宿エスパス北斗2: 5台（125, 126, 127, 128, 4349）
- 他店舗は未対応（確認が必要）

## 注意事項
- 変更を加えたら必ずこのファイルを更新すること
- 約束したことは必ず実装すること（朝0埋めの件で問題発生）

## 未対応タスク
- [ ] 西武新宿エスパス北斗2: +1台（3151）
- [ ] 上野エスパスSBJ: 台数確認
- [ ] 高田馬場エスパスSBJ: 台番号入替確認
- [ ] 新小岩エスパスSBJ: 台数確認
- [ ] アイランド秋葉原北斗2: 0814,0824撤去確認
