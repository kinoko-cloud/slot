# SALVAGE.md — 既存実装のサルベージ結果

2026-01-29 06:45 JST 調査完了

## 実装済み・稼働中のモジュール

### analysis/（分析エンジン）
| ファイル | 機能 | 状態 |
|---|---|---|
| `analyzer.py` | AT間計算、連チャン数、初当たり分析、品質チェック | ✅ 稼働中 |
| `recommender.py` | メイン推奨エンジン（スコアリング→ランク判定） | ✅ 稼働中 |
| `store_pattern.py` | 店舗パターン（据え置き率、特定日、設定段階） | ✅ 稼働中 |
| `history_accumulator.py` | 日次蓄積DB（data/history/へ追記） | ✅ 稼働中 |
| `diff_medals_estimator.py` | 差枚推定 | ✅ 稼働中 |
| `feedback.py` | 答え合わせフィードバック→補正係数 | ✅ 稼働中（data/feedback/にデータあり） |
| `pattern_detector.py` | パターン検出（日付・台番号・曜日等） | ✅ 稼働中（data/patterns/に94ファイル） |
| `realtime_predictor.py` | リアルタイム予測（営業中用） | ⚠️ scrapers/realtime_scraper.pyから呼ばれるが、定期実行未確認 |
| `store_analyzer.py` | 店舗別傾向分析（レポート用） | ⚠️ スタンドアロン。他から呼ばれてない |
| `compare_all.py` | 全店舗比較分析 | ⚠️ スタンドアロン。他から呼ばれてない |

### scrapers/（スクレイパー）
| ファイル | 機能 | 状態 |
|---|---|---|
| `daidata_detail_history.py` | 台データオンライン詳細履歴 | ✅ daily_collectから使用 |
| `daidata_ranking.py` | 台データランキング取得 | ✅ daily_collectから使用 |
| `papimo.py` | papimo.jp（アイランド）データ取得 | ✅ fetch_island_historyから使用 |
| `site777.py` | サイトセブンスクレイパー | ✅ daily_collectから使用 |
| `availability_checker.py` | 空き状況チェッカー | ✅ generate_static/web/appから使用 |
| `realtime_scraper.py` | リアルタイムスクレイパー | ✅ web/appから使用 |
| `anaslo.py` | アナスロスクレイパー | ✅ daily_collectから使用 |
| `slorepo.py` | スロレポスクレイパー | ⚠️ orphan（コードはあるが呼び出し元なし） |
| `slorepo_daily.py` | スロレポ日次取得 | ⚠️ orphan |
| `daidata_sbj.py` | SBJ専用スクレイパー | ⚠️ orphan（旧バージョン？） |
| `daidata_scraper*.py` | 各種スクレイパー試作 | ⚠️ orphan（開発時の試行錯誤） |
| `analyze_*.py` | 解析スクリプト | ⚠️ orphan（調査用） |
| `get_sbj_units.py` | 台番号取得 | ⚠️ orphan |

### scripts/（運用スクリプト）
| ファイル | 機能 | 状態 |
|---|---|---|
| `daily_collect.py` | 毎日23時のデータ収集 | ✅ GitHub Actions（daily_collect.yml） |
| `fetch_daidata_availability.py` | リアルタイムデータ取得 | ✅ GitHub Actions（fetch-availability.yml） |
| `generate_static.py` | 静的サイト生成 | ✅ GitHub Actions（deploy-static.yml） |
| `generate_verify.py` | 的中結果生成 | ✅ GitHub Actions（daily-verify.yml） |
| `verify_units.py` | 台番号検証（台移動/減台/増台/撤去検出） | ✅ コードあり。通知連携なし |
| `backtest.py` | バックテスト | ✅ 手動実行 |
| `nightly_verify.py` | 夜間答え合わせ（WhatsApp向けレポート生成） | ⚠️ orphan（呼び出し元なし。generate_verify.pyと重複？） |
| `backfill_papimo.py` | papimo過去データ一括取得 | ⚠️ orphan（手動実行用） |
| `fetch_island_history.py` | アイランド履歴取得 | ⚠️ orphan（ワークフロー未接続） |
| `check_css.py` | CSS整合性チェック | ⚠️ orphan（手動用） |

### docs/（設計文書）
| ファイル | 内容 |
|---|---|
| `DESIGN_evolution.md` | 分析ロジック進化設計書（Phase 1-5の自動進化計画） |
| `UNIT_CHANGE_POLICY.md` | 台変動ポリシー（台移動/減台/増台/撤去） |
| `NEW_MACHINE_WORKFLOW.md` | 新機種追加ワークフロー（チェックリスト付き） |
| `DISPLAY_RULES.md` | モード別表示ルール |

### data/（蓄積データ）
| ディレクトリ | 内容 |
|---|---|
| `data/history/` | 蓄積DB（全店・全台・7-13日分） |
| `data/daily/` | daily JSON（日次スナップショット） |
| `data/site777/` | サイトセブンデータ（7日分） |
| `data/patterns/` | パターン検出結果（94ファイル） |
| `data/feedback/` | フィードバック結果（9ファイル） |
| `data/ranking/` | ランキングデータ |
| `data/availability.json` | リアルタイムデータ |
| `data/raw/` | 生データ・スクリーンショット |
| `data/alerts/` | 台変動アラート（空） |

## 発見：実装済みだが十分活用されてない機能

### 1. DESIGN_evolution.md — Phase別進化計画
- Phase 1-5の段階的分析進化が設計されている
- Phase 1-2は実装済み（history_accumulator + setting_change_cycle + weekday_pattern）
- Phase 3以降（週間パターン、月間トレンド、回帰分析）は**未実装**

### 2. pattern_detector.py — パターンデータ蓄積
- data/patterns/に94ファイルの蓄積データあり
- 日付特徴（3のつく日、ゾロ目等）、台番号特徴を抽出
- **recommender.pyからの参照は未確認** → generate_static.pyからのみ呼ばれてる

### 3. feedback.py — フィードバック補正
- data/feedback/に9ファイル
- 予測誤差の分析、補正係数の算出が実装済み
- recommender.pyから`calculate_correction_factors()`が呼ばれてるが効果は限定的かも

### 4. nightly_verify.py — WhatsApp向けレポート
- WhatsApp向けの答え合わせレポート生成機能が実装済み
- `generate_report()`でテキストレポートを作成
- **しかし呼び出し元がない** → 手動実行が必要

### 5. realtime_predictor.py — リアルタイム予測
- 営業中の台強度計算、当日分析、レポート生成
- realtime_scraper.pyから呼ばれるが、**定期実行の仕組みが不明**

### 6. スロレポ/アナスロスクレイパー
- slorepo.py, slorepo_daily.py — スロレポからの月間・日次データ取得
- anaslo.py — アナスロからのデータ取得
- **daily_collectでanaslo.pyは使われてるが、slorepoは未使用**

### 7. backfill_papimo.py — 過去データ一括取得
- papimoのプルダウンで取れる全日分を取得する機能
- **手動実行用。定期的に実行すれば蓄積データを増やせる**

## 質問事項（RSさんに確認したいこと）

1. **nightly_verify.py**: WhatsApp向けの答え合わせレポートを毎晩自動送信する仕組みにしたいですか？
2. **スロレポ**: daily_collectに組み込むべきですか？追加データソースとして。
3. **realtime_predictor.py**: 営業中に定期実行する仕組みは必要ですか？
4. **DESIGN_evolution.md Phase 3以降**: 週間パターン・月間トレンド・回帰分析の実装優先度は？
