# ARCHITECTURE.md — コードベース全体マップ

**⚠️ セッション開始時に必ず読むこと。RSさんはこの全体像を理解している前提で指示を出す。**

**最終更新: 2026-01-30**

---

## サービス概要

スロット台の予測・おすすめサービス。
- 閲覧者がサイトを開いた瞬間に最新データを取得→予測→表示
- 閉店後は確定データから翌日予測を表示

## 2つの動作モード

### 確定データモード（閉店後〜開店前）
- 蓄積DB（前日までの確定実績）から翌日のおすすめ台を表示
- generate_static.pyでビルドした静的HTML

### リアルタイムモード（営業中）★サービスの核心
- **閲覧者がサイトを開いた瞬間にリアルタイムでデータ取得→予測→表示**
- ローディング画面（プログレスバー、10-30秒）を表示しながらスクレイピング実行
- 取得完了後、最新の当たりデータ・回転数で「今この瞬間の狙い目」を予測

#### リアルタイム取得の仕組み（既存実装）
```
閲覧者がページ開く
  → realtime.js が PythonAnywhere API を呼ぶ
  → /api/scrape/<store_key> でバックグラウンドスクレイピング開始
  → recommend.html でプログレスバー表示（「データ取得中」）
  → /api/scrape_status/<store_key> でポーリング（1秒間隔）
  → 完了 → 最新データでUI更新 or location.reload()
  → 以後 3-5分間隔で自動更新
```

---

## ファイル構成

### データ層
| ファイル | 役割 |
|---------|------|
| `data/history/{store_key}/{unit_id}.json` | **蓄積DB** — 台ごとの全日履歴（最重要データ） |
| `data/daily/{store_key}/YYYY-MM-DD.json` | 日別スナップショット |
| `data/availability/{store_key}.json` | リアルタイム空き状況 |

### スクレイパー層
| ファイル | 役割 |
|---------|------|
| `scrapers/daidata_detail_history.py` | daidata: 当たり履歴取得（Playwright） |
| `scrapers/papimo.py` | papimo: 当たり履歴取得（requests） |
| `scrapers/daidata_direct.py` | daidata: 基本データ取得 |
| `scrapers/availability_checker.py` | リアルタイムデータ取得（availability.json / GAS） |
| `scrapers/realtime_scraper.py` | リアルタイムスクレイピング |

### 分析・予測層
| ファイル | 役割 |
|---------|------|
| `analysis/recommender.py` | **メイン予測エンジン** — スコアリング/ランク/おすすめ文生成 |
| `analysis/history_accumulator.py` | 蓄積DB管理 — 日別データ蓄積/連チャン/差枚計算 |
| `analysis/diff_medals_estimator.py` | 差枚推定（⚠️ 不正確、フォールバックのみ） |
| `analysis/store_analyzer.py` | 店舗分析 |
| `analysis/pattern_detector.py` | パターン検出 |
| `analysis/realtime_predictor.py` | リアルタイム予測 |
| `analysis/verdict.py` | 判定ロジック |
| `analysis/feedback.py` | 的中フィードバック |

### 設定層
| ファイル | 役割 |
|---------|------|
| `config/rankings.py` | **機種別閾値定義（唯一の定義元）** — good_prob, bad_prob等 |
| `config/stores.py` | 店舗定義 |

### Web層
| ファイル | 役割 |
|---------|------|
| `web/app.py` | **Flask APIサーバー** — 動的ルーティング + リアルタイムAPI |
| `web/static/realtime.js` | **クライアント側リアルタイム取得** — API呼び出し/UI更新 |
| `web/static/style.css` | スタイルシート |
| `web/templates/index.html` | トップページ（おすすめ/爆発台/SA候補） |
| `web/templates/recommend.html` | 店舗別推奨ページ（**ローディングUI付き**） |
| `web/templates/ranking.html` | 機種別ランキング |
| `web/templates/unit_history.html` | 台別履歴 |
| `web/templates/verify.html` | 的中率検証 |
| `web/templates/stores.html` | 店舗一覧 |
| `web/templates/_topbar.html` | 共通トップバー |
| `web/templates/_common_js.html` | 共通JS |

### スクリプト層
| ファイル | 役割 |
|---------|------|
| `scripts/generate_static.py` | **静的サイトビルド** → docs/ に出力 |
| `scripts/daily_collect.py` | **日次データ収集** — スクレイピング→蓄積DB更新→ビルド |
| `scripts/data_integrity_check.py` | データ品質チェック |
| `scripts/validate_output.py` | ビルド後HTML検証 |
| `scripts/enrich_rec.py` | データ補完 |
| `scripts/nightly_verify.py` | 夜間的中検証 |

### ドキュメント
| ファイル | 役割 |
|---------|------|
| `docs/SPEC_prediction.md` | **予測ロジック仕様書（マスター）** |
| `docs/DESIGN_evolution.md` | 分析ロジック進化設計書 |
| `CLAUDE.md` | AIアシスタント向けガイド |
| `REVIEW_CHECKLIST.md` | レビューチェックリスト |

### デプロイ
| 項目 | 値 |
|------|-----|
| 静的ホスティング | Cloudflare Pages (`docs/`) |
| APIサーバー | PythonAnywhere (`web/app.py`) |
| データ取得 | GitHub Actions + ローカルcron |

---

## データフロー

### 閉店後（静的ビルド）
```
cron → daily_collect.py
  → daidata_detail_history.py / papimo.py（スクレイピング）
  → history_accumulator.py（蓄積DB更新）
  → data_integrity_check.py（品質チェック）
  → generate_static.py（静的HTML生成）
    → recommender.py（スコアリング/ランク/おすすめ文）
    → validate_output.py（HTML検証）
  → git push → Cloudflare Pages デプロイ
```

### 営業中（リアルタイム）
```
閲覧者がページ開く
  → realtime.js → PythonAnywhere API
  → /api/scrape/{store} → realtime_scraper.py
  → recommend.html ローディングUI表示
  → 完了 → recommender.py で最新データから再予測
  → UI更新
```

---

## 既知の問題・注意点

1. **estimate_diff_medals()は信用できない** — 符号すら逆になることがある。蓄積DBのdiff_medals最優先
2. **config/rankings.pyが閾値の唯一の定義元** — 他でハードコードしない
3. **PythonAnywhereのAPIが現在どの程度稼働しているかは要確認**
4. **静的サイト(docs/)とFlask動的サイト(web/app.py)が共存** — 閉店後は静的、営業中はAPI経由の動的更新

---

## 変更履歴

| 日付 | 内容 |
|------|------|
| 2026-01-30 | 初版作成（RSさん指摘：既存コードを忘れて新規で作る問題への対策） |
